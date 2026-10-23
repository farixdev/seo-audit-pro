# -*- coding: utf-8 -*-
"""
Work done on this computer instead of the server.

* ``AuditRunner`` runs the audits the signed-in person queued. It asks the
  server for its next audits, runs each one with the engine in a background
  process (``pc_jobs``) at below-normal priority, forwards the engine's progress
  and Google PageSpeed requests, and sends the finished result. The server only
  stores and shares the data.
* ``save_report`` / ``upload_report`` build report files (DOCX, and PDF with
  Microsoft Word or LibreOffice) from an audit's stored result.
"""

import gzip
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

from seo_audit_pro.core import api
from seo_audit_pro.core.api import ApiError

log = logging.getLogger("seo_audit_pro.work")

POLL_SECONDS = 1.0
POST_EVERY = 2.0          # progress messages are sent in small groups
HEARTBEAT_EVERY = 60.0    # the server gives up on an audit after 10 silent minutes
CLAIM_EVERY = 60.0        # look for queued audits even without a live event
RETRY_EVERY = 15.0
_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def work_dir(*parts):
    from seo_audit_pro.local_server import app_dir
    path = os.path.join(app_dir(), "work", *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _job_command(folder):
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):  # no console window when started from source
            alt = exe[:-10] + "pythonw.exe"
            exe = alt if os.path.isfile(alt) else exe
        cmd = [exe, "-m", "seo_audit_pro.pc_jobs"]
    return cmd + ["--job", folder, "--parent", str(os.getpid())]


def _start_job(folder, job):
    with open(os.path.join(folder, "job.json"), "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return subprocess.Popen(_job_command(folder), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=_FLAGS, close_fds=True,
                            cwd=None if getattr(sys, "frozen", False) else root)


def _write_json(path, data):
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(part, path)


class _Job:
    def __init__(self, info, folder, proc):
        self.id = info["audit_id"]
        self.info = info
        self.folder = folder
        self.proc = proc
        self.offset = 0
        self.messages = []
        self.last_post = 0.0
        self.outcome = None       # ("done", None) or ("error", text) once the job says so
        self.stopped = False      # cancelled elsewhere: stop quietly
        self.next_upload = 0.0

    def read(self):
        """New lines from the job's progress file."""
        path = os.path.join(self.folder, "progress.jsonl")
        try:
            with open(path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
        except OSError:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        out = []
        for line in chunk[:end].splitlines():
            try:
                out.append(json.loads(line.decode("utf-8")))
            except ValueError:
                continue
        return out

    def kill(self):
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass


class AuditRunner:
    """Runs this person's queued audits on this computer (one background thread)."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._claim_now = True
        self._restart = True
        self._last_claim = 0.0
        self.slots = 2
        self.enabled = True

    # -- control (UI thread) -------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.enabled, self._claim_now, self._restart = True, True, True
        self._thread = threading.Thread(target=self._loop, name="audit-runner", daemon=True)
        self._thread.start()

    def stop(self):
        """Sign-out or exit: stop running audits here. The server puts them back
        in the queue the next time this person's app starts."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            jobs, self._jobs = list(self._jobs.values()), {}
        for job in jobs:
            job.kill()

    def on_event(self, ev):
        kind = ev.get("type")
        if kind == "audit_jobs":
            self._claim_now = True
            self._wake.set()
        elif kind == "audit_progress" and ev.get("status") in ("cancelled", "failed"):
            with self._lock:
                job = self._jobs.get(ev.get("audit_id"))
            if job and job.outcome is None:
                job.stopped = True
                job.kill()
                self._wake.set()

    def running(self):
        with self._lock:
            return len(self._jobs)

    # -- background thread ---------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._step()
            except Exception:  # noqa: BLE001 - keep running
                log.exception("Audit runner error")
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def _step(self):
        now = time.monotonic()
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            self._service(job, now)
        every = CLAIM_EVERY if api.live_updates() else 15  # without live events, look for new audits more often
        if self.enabled and len(self._jobs) < self.slots and (self._claim_now or now - self._last_claim > every):
            self._claim()

    def _claim(self):
        self._last_claim = time.monotonic()
        try:
            res = api.post("/api/audit-jobs/claim", {"restart": self._restart}, timeout=30)
        except ApiError as e:
            if e.code == "server_runs_audits":
                self.enabled = False  # this server runs audits itself
            elif e.status == 401:
                self._stop.set()
            return
        self._claim_now = self._restart = False
        self.slots = max(1, int(res.get("slots") or 2))
        for info in res.get("jobs") or []:
            self._launch(info)

    def _launch(self, info):
        folder = work_dir("audits", info["audit_id"])
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        try:
            proc = _start_job(folder, dict(info, kind="audit"))
        except OSError as e:
            self._post_failed(info["audit_id"], f"The audit could not be started on this computer: {e}")
            return
        with self._lock:
            self._jobs[info["audit_id"]] = _Job(info, folder, proc)

    def _service(self, job, now):
        exited = job.proc.poll() is not None  # checked first: an ended job's last lines are all on disk
        for line in job.read():
            t = line.get("t")
            if t == "msg":
                job.messages.append(line.get("text") or "")
            elif t == "psi":
                threading.Thread(target=self._pagespeed, args=(job, line), daemon=True).start()
            elif t == "done":
                job.outcome = ("done", None)
            elif t == "error":
                job.outcome = ("error", line.get("text"))
        if job.stopped:
            self._forget(job)
            return
        if not exited:
            due = job.messages and now - job.last_post >= POST_EVERY
            if due or now - job.last_post >= HEARTBEAT_EVERY:
                self._post_progress(job, now)
            return
        if job.outcome is None:
            job.outcome = ("error", "The audit stopped unexpectedly on this computer.")
        if job.messages:
            self._post_progress(job, now)
        if job.outcome[0] == "done":
            if now >= job.next_upload:
                self._upload(job, now)
        else:
            self._post_failed(job.id, job.outcome[1])
            self._forget(job)

    def _post_progress(self, job, now):
        batch, job.messages = job.messages[:300], job.messages[300:]
        job.last_post = now
        try:
            res = api.post(f"/api/audit-jobs/{job.id}/progress", {"messages": batch})
        except ApiError as e:
            if e.status in (404, 409):
                job.stopped = True
                job.kill()
            return
        if res.get("stop"):  # cancelled, or taken over elsewhere
            job.stopped = True
            job.kill()

    def _pagespeed(self, job, line):
        try:
            answer = api.post("/api/pagespeed", {"url": line.get("url"), "strategy": line.get("strategy")}, timeout=150)
        except ApiError as e:
            answer = {"url": line.get("url"), "strategy": line.get("strategy"), "ok": False, "error": e.message,
                      "scores": {}, "lab": {}, "field": {"available": False}, "opportunities": {},
                      "top_opportunities": []}
        try:
            _write_json(os.path.join(job.folder, f"psi-{int(line.get('id') or 0)}.json"), answer)
        except OSError:
            pass

    def _upload(self, job, now):
        try:
            with open(os.path.join(job.folder, "result.json"), "rb") as fh:
                packed = gzip.compress(fh.read(), compresslevel=6)
        except OSError:
            self._post_failed(job.id, "The audit finished but its result could not be read on this computer.")
            self._forget(job)
            return
        # One request: the result and the Word reports the job wrote, so the team can
        # open the reports the moment the audit shows as completed.
        files = {"result": ("result.json.gz", packed, "application/gzip")}
        for kind in ("client", "technical"):
            made = os.path.join(job.folder, f"{kind}.docx")
            if os.path.isfile(made):
                with open(made, "rb") as fh:
                    files[f"{kind}_docx"] = (f"{kind}.docx", fh.read(), "application/octet-stream")
        try:
            api.request("POST", f"/api/audit-jobs/{job.id}/result", files=files, timeout=180)
        except ApiError as e:
            if e.offline or e.status >= 500 or e.status == 429:
                job.next_upload = now + RETRY_EVERY  # keep the result and try again
                return
            log.warning("Audit %s result refused: %s", job.id, e.message)
        self._keep_here(job, packed)
        self._forget(job)
        self._claim_now = True

    def _keep_here(self, job, packed):
        """This computer keeps the result and the Word reports, so opening them here is instant."""
        cache = work_dir("reports")
        try:
            with open(os.path.join(cache, f"{job.id}.json.gz"), "wb") as fh:
                fh.write(packed)
            for kind in ("client", "technical"):
                made = os.path.join(job.folder, f"{kind}.docx")
                if os.path.isfile(made):
                    shutil.copyfile(made, os.path.join(cache, f"{job.id}-{kind}.docx"))
        except OSError:
            pass

    def _post_failed(self, audit_id, text):
        try:
            api.post(f"/api/audit-jobs/{audit_id}/failed", {"error": (text or "")[:2000]})
        except ApiError:
            pass
        self._claim_now = True

    def _forget(self, job):
        job.kill()
        with self._lock:
            self._jobs.pop(job.id, None)
        shutil.rmtree(job.folder, ignore_errors=True)
        self._claim_now = True


_RUNNER = AuditRunner()


def runner():
    return _RUNNER


# ---------------------------------------------------------------------------
# Report files built on this computer
# ---------------------------------------------------------------------------
_REPORT_LOCK = threading.Lock()   # one report at a time: Word and the builder are heavy
REPORT_TIMEOUT = 300


def pdf_supported():
    from pdf_convert import pdf_supported as supported
    return supported()


def report_file(audit_id, kind, fmt):
    """Path of a report file for an audit, built here if needed (blocking)."""
    cache = work_dir("reports")
    path = os.path.join(cache, f"{audit_id}-{kind}.{fmt}")
    if os.path.isfile(path):
        return path
    if kind == "client" and fmt == "pdf":
        import full_report
        if full_report._browser():
            with _REPORT_LOCK:
                if os.path.isfile(path):
                    return path
                _build_client_html_pdf(audit_id, path, cache)
                return path
    if fmt == "pdf" and not pdf_supported():
        raise ApiError(409, "PDF reports need Microsoft Word or LibreOffice on this computer. "
                            "Download the Word (DOCX) report instead.", "pdf_unavailable")
    with _REPORT_LOCK:
        if os.path.isfile(path):
            return path
        docx = os.path.join(cache, f"{audit_id}-{kind}.docx")
        if not os.path.isfile(docx):
            _build_docx(audit_id, kind, docx, cache)
        if fmt == "pdf":
            from pdf_convert import docx_to_pdf
            part = path + ".part.pdf"
            if not docx_to_pdf(docx, part):
                raise ApiError(500, "The PDF could not be created on this computer. Download the Word (DOCX) "
                                    "report instead.", "pdf_failed")
            os.replace(part, path)
    return path


def _build_client_html_pdf(audit_id, pdf_path, cache):
    import gzip
    import client_report
    import full_report
    import mindcob_report
    from seo_audit_pro.core import settings

    # Check if already generated in user's Client reports folder
    client_dir = os.path.join(settings.downloads_dir(), "Client reports")
    if os.path.isdir(client_dir):
        suffix = f"- {str(audit_id)[:8]}"
        for name in os.listdir(client_dir):
            if name.endswith(suffix):
                cand = os.path.join(client_dir, name)
                if os.path.isdir(cand):
                    for f in os.listdir(cand):
                        if f.endswith(".pdf") and os.path.getsize(os.path.join(cand, f)) > 1000:
                            shutil.copyfile(os.path.join(cand, f), pdf_path)
                            return

    result_path = os.path.join(cache, f"{audit_id}.json.gz")
    if not os.path.isfile(result_path):
        data = api.get(f"/api/audits/{audit_id}/result-data", timeout=120)
        with open(result_path + ".part", "wb") as fh:
            fh.write(data)
        os.replace(result_path + ".part", result_path)
    with gzip.open(result_path, "rt", encoding="utf-8") as fh:
        result = json.load(fh)
    domain = result.get("domain") or "site"
    folder = os.path.join(cache, f"client_{audit_id}")
    os.makedirs(folder, exist_ok=True)
    shots = client_report.screenshots(result.get("final_url") or f"https://{domain}", folder)
    html_path = os.path.join(folder, f"{domain} - Mindcob Website SEO & Growth Report.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(mindcob_report.build(result, {"name": "Mindcob"}, shots=shots))
    part_pdf = pdf_path + ".part.pdf"
    if not full_report.html_to_pdf(html_path, part_pdf):
        raise ApiError(500, "The client PDF could not be created on this computer. Download the Word (DOCX) report instead.", "pdf_failed")
    os.replace(part_pdf, pdf_path)


def _build_docx(audit_id, kind, docx, cache):
    result_path = os.path.join(cache, f"{audit_id}.json.gz")
    if not os.path.isfile(result_path):
        data = api.get(f"/api/audits/{audit_id}/result-data", timeout=120)
        with open(result_path + ".part", "wb") as fh:
            fh.write(data)
        os.replace(result_path + ".part", result_path)
    folder = work_dir("jobs", uuid.uuid4().hex)
    try:
        proc = _start_job(folder, {"kind": "report", "audit_id": audit_id, "report_kind": kind,
                                   "result_path": result_path, "docx_path": docx})
        try:
            proc.wait(REPORT_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not os.path.isfile(docx):
            raise ApiError(500, "The report could not be created on this computer. Try again.", "report_failed")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def forget_reports(audit_id):
    """Drop this computer's report files for an audit (they are built again)."""
    cache = work_dir("reports")
    for name in os.listdir(cache):
        if name.startswith(audit_id + "-") or name == f"{audit_id}.json.gz" or name == f"client_{audit_id}":
            p = os.path.join(cache, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except OSError:
                pass
    try:
        from seo_audit_pro.core import settings
        client_dir = os.path.join(settings.downloads_dir(), "Client reports")
        if os.path.isdir(client_dir):
            suffix = f"- {str(audit_id)[:8]}"
            for name in os.listdir(client_dir):
                if name.endswith(suffix):
                    shutil.rmtree(os.path.join(client_dir, name), ignore_errors=True)
    except Exception:
        pass


def save_report(info, dest_dir=None, filename=None):
    """Build the report the server asked this app to make (``info`` is the
    server's ``build_in_app`` answer) and save a copy. Returns the saved path."""
    path = report_file(info["audit_id"], info["kind"], info["format"])
    target = api.unique_target(filename or info.get("download_name") or os.path.basename(path), dest_dir)
    shutil.copyfile(path, target)
    try:  # the team gets this copy too, so nobody has to build it again
        with open(path, "rb") as fh:
            api.request("PUT", f"/api/reports/{info['report_id']}/file", data=fh.read(), timeout=180)
        api.post(f"/api/reports/{info['report_id']}/opened")
    except (OSError, ApiError):
        pass
    return target


def upload_report(row):
    """Give the server a copy of a report built here (needed to email it)."""
    path = report_file(row["audit_id"], row["kind"], row["format"])
    with open(path, "rb") as fh:
        data = fh.read()
    return api.request("PUT", f"/api/reports/{row['id']}/file", data=data, timeout=180)


# ---------------------------------------------------------------------------
# Lead files read on this computer
# ---------------------------------------------------------------------------
PREVIEW_TIMEOUT = 900


def _run_job_to_end(kind, job, timeout):
    """Run a job and wait for it. Returns (folder, error text or None)."""
    folder = work_dir("jobs", uuid.uuid4().hex)
    proc = _start_job(folder, dict(job, kind=kind))
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return folder, "Reading the file took too long."
    error = None
    for line in _Job({"audit_id": ""}, folder, proc).read():
        if line.get("t") == "error":
            error = line.get("text")
    if proc.returncode and not error:
        error = "The file could not be read."
    return folder, error


def import_preview(path):
    """Read a lead file here and register it with the server as a pending import
    (only its column names, row count and a sample are sent). Blocking."""
    folder, error = _run_job_to_end("leads-preview", {"path": path}, PREVIEW_TIMEOUT)
    try:
        if error:
            raise ApiError(422, error if error.startswith(os.path.basename(path)) else
                           f"{os.path.basename(path)}: {error}", "unreadable")
        with open(os.path.join(folder, "preview.json"), encoding="utf-8") as fh:
            info = json.load(fh)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    res = api.post("/api/lead-imports/local", {"file_name": os.path.basename(path), "file_size": os.path.getsize(path),
                                               "headers": info["headers"], "total_rows": info["total"],
                                               "sample": info["sample"]}, timeout=60)
    _write_json(os.path.join(work_dir("imports"), res["batch"]["id"] + ".json"),
                {"batch_id": res["batch"]["id"], "path": os.path.abspath(path)})
    return res


class _Import:
    def __init__(self, batch_id, path):
        self.batch_id, self.path = batch_id, path
        self.job = None
        self.queue = []        # chunk lines waiting to be sent, in file order
        self.read_all = False
        self.next_try = 0.0


class ImportRunner:
    """Sends the rows of lead files read on this computer, chunk by chunk and in
    order. An import interrupted by closing the app continues from the last row
    the server stored when the app starts again (if the file is still there)."""

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._resume = True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._resume = True
        self._thread = threading.Thread(target=self._loop, name="lead-imports", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            items, self._items = list(self._items.values()), {}
        for item in items:
            if item.job:
                item.job.kill()

    def add(self, batch_id):
        """The import was started: send its rows."""
        try:
            with open(os.path.join(work_dir("imports"), batch_id + ".json"), encoding="utf-8") as fh:
                path = json.load(fh)["path"]
        except (OSError, ValueError, KeyError):
            return
        with self._lock:
            self._items.setdefault(batch_id, _Import(batch_id, path))
        self._wake.set()

    def busy(self):
        with self._lock:
            return bool(self._items)

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self._resume:
                    self._resume = False
                    self._resume_saved()
                with self._lock:
                    items = list(self._items.values())
                for item in items:
                    self._service(item)
            except Exception:  # noqa: BLE001 - keep running
                log.exception("Lead import error")
            self._wake.wait(0.5)
            self._wake.clear()

    def _resume_saved(self):
        folder = work_dir("imports")
        for name in os.listdir(folder):
            if not name.endswith(".json"):
                continue
            batch_id = name[:-5]
            try:
                batch = api.get(f"/api/lead-batches/{batch_id}", timeout=30)["batch"]
            except ApiError as e:
                if e.status in (403, 404):
                    self._drop_record(batch_id)
                continue
            if batch["status"] in ("queued", "processing"):
                self.add(batch_id)
            elif batch["status"] != "pending" or time.time() - os.path.getmtime(os.path.join(folder, name)) > 86400:
                self._drop_record(batch_id)

    def _drop_record(self, batch_id):
        try:
            os.remove(os.path.join(work_dir("imports"), batch_id + ".json"))
        except OSError:
            pass

    def _launch(self, item):
        if not os.path.isfile(item.path):
            self._fail(item, "The file is no longer on this computer. Already imported rows were kept; "
                             "import the file again to add the rest (duplicates are skipped).")
            return
        try:
            batch = api.get(f"/api/lead-batches/{item.batch_id}", timeout=30)["batch"]
        except ApiError:
            item.next_try = time.monotonic() + RETRY_EVERY
            return
        if batch["status"] not in ("queued", "processing"):
            self._forget(item)
            return
        folder = work_dir("imports", item.batch_id)
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        start_row = int(batch.get("processed_rows") or 0) + 1
        item.queue, item.read_all = [], False
        item.job = _Job({"audit_id": item.batch_id}, folder,
                        _start_job(folder, {"kind": "leads-rows", "path": item.path, "start_row": start_row}))

    def _service(self, item):
        now = time.monotonic()
        if now < item.next_try:
            return
        if item.job is None:
            self._launch(item)
            return
        job = item.job
        exited = job.proc.poll() is not None  # checked first: an ended job's last lines are all on disk
        for line in job.read():
            t = line.get("t")
            if t == "chunk":
                item.queue.append(line)
            elif t == "rows":
                item.read_all = True
            elif t == "error":
                job.outcome = ("error", line.get("text"))
        while item.queue:
            line = item.queue[0]
            path = os.path.join(job.folder, line["file"])
            try:
                with open(path, "rb") as fh:
                    packed = fh.read()
                api.request("POST", f"/api/lead-batches/{item.batch_id}/rows", params={"first": line["first"]},
                            data=packed, timeout=120)
            except ApiError as e:
                if e.code == "out_of_order":   # start again from the last row the server stored
                    job.kill()
                    item.job = None
                elif e.code == "not_running" or e.status in (403, 404):
                    self._forget(item)
                else:
                    item.next_try = now + RETRY_EVERY
                return
            except OSError:
                return
            item.queue.pop(0)
            try:
                os.remove(path)
            except OSError:
                pass
        if not exited:
            return
        if job.outcome and job.outcome[0] == "error":
            self._fail(item, job.outcome[1])
        elif not item.read_all:
            self._fail(item, "Reading the file stopped unexpectedly. Already imported rows were kept.")
        else:
            try:
                api.post(f"/api/lead-batches/{item.batch_id}/finish", timeout=60)
            except ApiError as e:
                if e.offline or e.status >= 500:
                    item.next_try = now + RETRY_EVERY
                    return
            self._forget(item)

    def _fail(self, item, text):
        try:
            api.post(f"/api/lead-batches/{item.batch_id}/failed", {"error": text})
        except ApiError:
            pass
        self._forget(item)

    def _forget(self, item):
        if item.job:
            item.job.kill()
            shutil.rmtree(item.job.folder, ignore_errors=True)
        with self._lock:
            self._items.pop(item.batch_id, None)
        self._drop_record(item.batch_id)


_IMPORTS = ImportRunner()


def imports():
    return _IMPORTS
