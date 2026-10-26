# -*- coding: utf-8 -*-
"""Audits for the Scrape & Audit portal (no sign-in, no server).

Everything runs on this computer: the engine, the screenshots and Google Maps
check (alongside the crawl), the client PDF and the Word reports. Each audit
saves into its own folder under ``reports_dir()`` with a small ``audit.json``
summary the Reports list reads.
"""

import json
import logging
import os
import re
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, Signal

STAGES = [("fetching homepage", 5), ("robots", 10), ("crawling the site", 12), ("analyzing pages", 55),
          ("internal links", 60), ("pagespeed", 66), ("structured data", 76), ("ssl", 80), ("security audit", 82),
          ("authority", 88), ("scoring", 92), ("competitor", 94), ("executive summary", 96)]
CRAWL_RE = re.compile(r"crawled (\d+)/(\d+) pages", re.I)
SUMMARY = "audit.json"
log = logging.getLogger("seo_audit_pro.quick")


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".write-test-{os.getpid()}")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def reports_dir():
    """Documents\\SEO Audit Pro\\Quick audits, or — when Windows blocks writing
    there (Controlled folder access, OneDrive, a full or read-only disk) — the
    app's own folder, which is always writable."""
    from seo_audit_pro.core import settings
    try:
        path = os.path.join(settings.downloads_dir(), "Quick audits")
    except OSError:
        path = ""
    if path and _writable(path):
        return path
    from seo_audit_pro.local_server import app_dir
    fallback = os.path.join(app_dir(), "Quick audits")
    if path:
        log.warning("Cannot write to %s; saving quick audits to %s", path, fallback)
    os.makedirs(fallback, exist_ok=True)
    return fallback


def domain_of(url):
    d = re.sub(r"^https?://", "", (url or "").strip(), flags=re.I).split("/")[0].lower()
    return d[4:] if d.startswith("www.") else d


def safe(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "site"


def saved_audits():
    """Finished audits, newest first: dicts from each folder's audit.json."""
    root = reports_dir()
    out = []
    for name in os.listdir(root):
        path = os.path.join(root, name, SUMMARY)
        try:
            with open(path, encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            continue
        info["folder"] = os.path.join(root, name)
        out.append(info)
    return sorted(out, key=lambda i: i.get("finished_at") or "", reverse=True)


def _save_failure(job, message, code):
    """Write the full error (with traceback) to the app log and to
    work/failed/<time>-<site>.log. Returns that file's path."""
    detail = traceback.format_exc()
    log.error("Quick audit of %s failed (%s): %s\n%s", job.get("site"), code, message, detail)
    try:
        from seo_audit_pro.local_server import app_dir
        folder = os.path.join(app_dir(), "work", "failed")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{datetime.now():%Y%m%d-%H%M%S}-{safe(domain_of(job.get('site')))}.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{job.get('site')}\n{code}: {message}\n\n{detail}")
        return path
    except OSError:
        return ""


class Signals(QObject):
    progress = Signal(int, int, str)        # job id, percent, step
    finished = Signal(int, bool, str, str)  # job id, ok, score or error, report path


class Runner:
    """Runs queued audits, ``slots`` at a time, each in its own thread."""

    def __init__(self):
        self.signals = Signals()
        self.queue = []          # (job id, job)
        self.active = 0
        self.slots = 2
        self.pages = 50
        self.api_key = None   # optional Google API key: PageSpeed + Places (Business Profile)
        self.lock = threading.Lock()
        self.cancel = threading.Event()

    def busy(self):
        with self.lock:
            return self.active, len(self.queue)

    def add(self, job_id, job):
        with self.lock:
            self.queue.append((job_id, job))
        self.pump()

    def pump(self):
        with self.lock:
            while self.active < self.slots and self.queue and not self.cancel.is_set():
                job_id, job = self.queue.pop(0)
                self.active += 1
                threading.Thread(target=self._run, args=(job_id, job), daemon=True).start()

    def stop(self):
        self.cancel.set()
        with self.lock:
            dropped, self.queue = self.queue, []
        for job_id, _job in dropped:
            self.signals.finished.emit(job_id, False, "Stopped", "")

    def _run(self, job_id, job):
        try:
            self._audit(job_id, job)
        except BaseException as e:  # noqa: BLE001 - every failure gets a reason on screen and a log
            folder = job.get("_folder")
            if folder and not os.path.isfile(os.path.join(folder, SUMMARY)):
                import shutil
                shutil.rmtree(folder, ignore_errors=True)   # nothing finished: no empty folder in Reports
            if self.cancel.is_set():
                self.signals.finished.emit(job_id, False, "Stopped", "")
            else:
                from seo_audit_pro.errors import explain
                msg, code = explain(e)
                path = _save_failure(job, msg, code)
                self.signals.finished.emit(job_id, False, msg + (f"  [details: {path}]" if path else ""), "")
        finally:
            with self.lock:
                self.active -= 1
            self.pump()

    def _audit(self, job_id, job):
        import audit as engine
        import client_pdf
        import report_docx
        state = {"pct": 2}

        def progress(msg):
            msg = str(msg)
            m = CRAWL_RE.search(msg)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                state["pct"] = max(state["pct"], min(54, 12 + int(done / max(1, total) * 42)))
            else:
                low = msg.lower()
                for needle, pct in STAGES:
                    if needle in low and pct > state["pct"]:
                        state["pct"] = pct
                        break
            self.signals.progress.emit(job_id, state["pct"], msg.rstrip("…. ")[:120])

        url = job["site"].strip()
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        domain = domain_of(url)
        folder = os.path.join(reports_dir(), f"{safe(domain)} - {datetime.now():%Y-%m-%d %H%M%S}")
        os.makedirs(folder, exist_ok=True)
        job["_folder"] = folder
        extras = client_pdf.start(url, os.path.join(folder, "screenshots"))   # runs during the crawl
        result = engine.run_audit({"url": url, "max_pages": self.pages, "psi_strategy": "both", "psi_key": self.api_key,
                                   "cancel": self.cancel, "competitors": job.get("competitors") or [],
                                   "business": job.get("business") or ""},
                                  progress=progress)
        domain = domain_of(result.get("final_url") or result.get("url") or url) or domain
        base = os.path.join(folder, safe(domain))
        with open(f"{base} - audit data.json", "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, default=str)
        self.signals.progress.emit(job_id, 97, "Writing the Word reports")
        report_docx.generate_client_docx_report(result, f"{base} - Client SEO Report.docx")
        report_docx.generate_docx_report(result, f"{base} - Technical SEO Audit.docx")
        self.signals.progress.emit(job_id, 99, "Making the client PDF")
        pdf = client_pdf.write(result, folder, f"{base} - {client_pdf.REPORT_NAME}.pdf", extras)
        report = pdf or f"{base} - {client_pdf.REPORT_NAME}.html"
        sc = result.get("score") or {}
        with open(os.path.join(folder, SUMMARY), "w", encoding="utf-8") as fh:
            json.dump({"domain": domain, "business": job.get("business") or "", "url": result.get("final_url") or url,
                       "score": sc.get("score"), "grade": sc.get("grade"), "report": os.path.basename(report),
                       "pages": result.get("pages_crawled") or len(result.get("pages") or []),
                       "finished_at": datetime.now().isoformat(timespec="seconds")}, fh)
        shown = f"{sc.get('score')}/100" if sc.get("score") is not None else "Done"
        self.signals.finished.emit(job_id, True, shown, report)
