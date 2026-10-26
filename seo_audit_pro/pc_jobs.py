# -*- coding: utf-8 -*-
"""Heavy work the desktop app does on this computer, each job in its own
background process (``SEOAuditPro.exe --job <folder>``) so the window stays
responsive: running an SEO audit with the engine, writing a report file, and
reading a lead file (a preview, then its rows in chunks).

The app and the job talk through files in the job folder:

* ``job.json``         what to do (written by the app)
* ``progress.jsonl``   one JSON object per line, written by the job:
  ``{"t": "msg", "text": ...}`` engine progress, ``{"t": "psi", "id": n, "url": ...,
  "strategy": ...}`` a Google PageSpeed request the app forwards to the server
  (the API key stays there; the app writes the answer to ``psi-<n>.json``),
  ``{"t": "done"}`` or ``{"t": "error", "text": ...}`` at the end
* ``result.json``      the finished audit result
* ``worker.log``       anything the job prints
"""

import argparse
import gzip
import itertools
import json
import os
import sys
import threading
import time
import traceback

PSI_WAIT_SECONDS = 180


class _Channel:
    def __init__(self, folder):
        self._fh = open(os.path.join(folder, "progress.jsonl"), "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def send(self, obj):
        with self._lock:
            self._fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()


def _pagespeed_via_app(folder, channel):
    """Stands in for pagespeed.run_pagespeed inside the job."""
    numbers = itertools.count(1)
    lock = threading.Lock()

    def run_pagespeed(url, strategy="mobile", api_key=None, timeout=90):
        with lock:
            n = next(numbers)
        answer = os.path.join(folder, f"psi-{n}.json")
        channel.send({"t": "psi", "id": n, "url": url, "strategy": strategy})
        deadline = time.monotonic() + PSI_WAIT_SECONDS
        while time.monotonic() < deadline:
            if os.path.isfile(answer):
                try:
                    with open(answer, encoding="utf-8") as fh:
                        return json.load(fh)
                except (OSError, ValueError):
                    pass
            time.sleep(0.3)
        return {"url": url, "strategy": strategy, "ok": False, "error": "PageSpeed did not answer in time",
                "scores": {}, "lab": {}, "field": {"available": False}, "opportunities": {}, "top_opportunities": []}

    return run_pagespeed


def _places_via_app(folder, channel):
    """Google Places search through the app and the team server (the key stays
    there): returns (places, error) like google_maps_scraper.places_search."""
    numbers = itertools.count(1000)
    lock = threading.Lock()

    def search(query):
        with lock:
            n = next(numbers)
        answer = os.path.join(folder, f"places-{n}.json")
        channel.send({"t": "places", "id": n, "query": query})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if os.path.isfile(answer):
                try:
                    with open(answer, encoding="utf-8") as fh:
                        data = json.load(fh)
                    return data.get("places") or [], data.get("error")
                except (OSError, ValueError):
                    pass
            time.sleep(0.3)
        return [], "unreachable: no answer from the team server"

    return search


def _write_json(path, data):
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    os.replace(part, path)


def _run_audit(job, folder, channel):
    import pagespeed
    pagespeed.run_pagespeed = _pagespeed_via_app(folder, channel)
    import audit as engine

    params = {"url": job["url"], "max_pages": job["max_pages"], "psi_strategy": job["psi_strategy"],
              "psi_key": None, "cancel": threading.Event(), "places_remote": _places_via_app(folder, channel)}
    # Screenshots and the Google Maps profile run while the site is crawled.
    import client_pdf
    shots_dir = os.path.join(folder, "client")
    extras = client_pdf.start(job["url"], shots_dir)
    result = engine.run_audit(params, progress=lambda m: channel.send({"t": "msg", "text": str(m)}))
    _write_json(os.path.join(folder, "result.json"), result)
    # Both Word reports right away, so the whole team can open them as soon as the audit is in.
    channel.send({"t": "msg", "text": "Writing the reports"})
    result["id"] = job["audit_id"]
    for kind in ("client", "technical"):
        try:
            _write_docx(result, kind, os.path.join(folder, f"{kind}.docx"))
        except Exception:  # noqa: BLE001 - the audit itself is fine; the report is made again when opened
            traceback.print_exc()
    channel.send({"t": "msg", "text": "Making the client PDF"})
    try:
        client_pdf.write(result, shots_dir, os.path.join(folder, "client.pdf"), extras)
    except Exception:  # noqa: BLE001 - made again when first opened
        traceback.print_exc()


def _write_docx(result, kind, path):
    import report_docx
    write = report_docx.generate_client_docx_report if kind == "client" else report_docx.generate_docx_report
    part = path + ".part.docx"
    write(result, part)
    os.replace(part, path)


def _run_report(job):
    with open(job["result_path"], "rb") as fh:
        result = json.loads(gzip.decompress(fh.read()).decode("utf-8"))
    result["id"] = job["audit_id"]
    _write_docx(result, job["report_kind"], job["docx_path"])


LEAD_CHUNK = 1000
LEAD_SAMPLE = 200
CHUNKS_AHEAD = 4   # chunk files waiting for the app at most (keeps disk use small)


def _lead_rows(path):
    from backend import lead_import   # the same reader the server uses
    return lead_import.iter_rows(path)


def _read_leads_preview(job, folder):
    """Column names, row count and the first rows of a lead file."""
    from backend import lead_import
    headers, rows = _lead_rows(job["path"])
    sample, total = [], 0
    for row in rows:
        total += 1
        if len(sample) < LEAD_SAMPLE:
            sample.append(row)
        if total > lead_import.MAX_ROWS:
            raise ValueError(f"{os.path.basename(job['path'])} has more than {lead_import.MAX_ROWS:,} rows. "
                             "Split it into smaller files.")
    _write_json(os.path.join(folder, "preview.json"), {"headers": headers, "total": total, "sample": sample})


def _read_leads_rows(job, folder, channel):
    """The file's rows from ``start_row`` on, in gzip chunks the app sends in order."""
    _headers, rows = _lead_rows(job["path"])
    start, number, chunk, first = int(job.get("start_row") or 1), 0, [], None

    def flush():
        while len([n for n in os.listdir(folder) if n.startswith("chunk-")]) >= CHUNKS_AHEAD:
            time.sleep(0.2)
        name = f"chunk-{first:08d}.json.gz"
        with open(os.path.join(folder, name + ".part"), "wb") as fh:
            fh.write(gzip.compress(json.dumps(chunk, ensure_ascii=False).encode("utf-8"), compresslevel=5))
        os.replace(os.path.join(folder, name + ".part"), os.path.join(folder, name))
        channel.send({"t": "chunk", "first": first, "file": name, "count": len(chunk)})

    for row in rows:
        number += 1
        if number < start:
            continue
        if first is None:
            first = number
        chunk.append(row)
        if len(chunk) >= LEAD_CHUNK:
            flush()
            chunk, first = [], None
    if chunk:
        flush()
    channel.send({"t": "rows", "total": number})


def _message(exc):
    from seo_audit_pro.errors import explain
    return explain(exc)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="SEOAuditPro --job")
    parser.add_argument("--job", required=True)
    parser.add_argument("--parent", type=int, default=0)
    args, _unknown = parser.parse_known_args(argv)
    folder = args.job
    log = open(os.path.join(folder, "worker.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log  # a windowless program has no console
    # The engine never needs a database here; keep its own settings out of the user's folders.
    os.environ.update(DATABASE_URL="", SEO_DB="sqlite", SEO_DATA_DIR=os.path.join(folder, "engine"))
    from seo_audit_pro.local_server import _exit_with_parent
    _exit_with_parent(args.parent)
    channel = _Channel(folder)
    try:
        with open(os.path.join(folder, "job.json"), encoding="utf-8") as fh:
            job = json.load(fh)
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {job['kind']} job")
        if job["kind"] == "audit":
            _run_audit(job, folder, channel)
        elif job["kind"] == "leads-preview":
            _read_leads_preview(job, folder)
        elif job["kind"] == "leads-rows":
            _read_leads_rows(job, folder, channel)
        else:
            _run_report(job)
        channel.send({"t": "done"})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        channel.send({"t": "error", "text": _message(e)})
        log.flush()
        os._exit(1)
    log.flush()
    os._exit(0)  # do not wait for engine threads that may still be winding down


if __name__ == "__main__":
    main(sys.argv[1:])
