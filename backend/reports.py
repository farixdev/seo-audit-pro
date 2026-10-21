# -*- coding: utf-8 -*-
"""
Report files for completed audits, made with the existing deliverable pipeline
(report_docx + templates/client_report.html).

Where the files are made depends on ``config.AUDITS_RUN_ON``:

* ``"app"`` (default): on each person's own computer. The server only lists the
  reports; a desktop app builds a file from the audit's stored result when
  someone opens it. A file the app uploads (to attach it to an email) is kept in
  the database (``report_data``) so the server can send and share it.
* ``"server"``: the server writes the DOCX files when an audit completes and
  renders each PDF the first time someone downloads or emails it, one at a time
  (each Word/LibreOffice process needs 150–250 MB). PDF needs Microsoft Word or
  LibreOffice on the server; it is never faked.

The reports folder on the server is only a cache. A server without a persistent
disk (Render's free plan) loses it on every restart; files come back from the
database copy or, in "server" mode, are rebuilt from the stored result.
"""

import logging
import os
import re
import threading

from flask import render_template

from backend import config, db, security
from backend.errors import ApiError
from backend.report_context import prepare_client_report_context
from backend.util import now_iso
from pdf_convert import docx_to_pdf, pdf_supported  # noqa: F401 - re-exported for routes and tests

log = logging.getLogger("seo_audit_pro.reports")
_LUCIDE_JS = None
_BUILD_LOCK = threading.Lock()
BUILD_WAIT_SECONDS = 240
MAX_FILE_BYTES = 20 * 1024 * 1024


def made_in_app():
    return config.AUDITS_RUN_ON == "app"


def _record(tx, audit_id, kind, fmt, stored, ready=True):
    size = os.path.getsize(os.path.join(config.REPORTS_DIR, stored)) if ready else 0
    # Keep the id of an existing row: emails that attached this report point at it.
    rid = tx.scalar("SELECT id FROM reports WHERE audit_id = %s AND kind = %s AND format = %s", (audit_id, kind, fmt))
    if rid:
        tx.execute("UPDATE reports SET stored_name = %s, size_bytes = %s, created_at = %s, ready = %s WHERE id = %s",
                   (stored, size, now_iso(), 1 if ready else 0, rid))
        tx.execute("DELETE FROM report_data WHERE report_id = %s", (rid,))
        return rid
    rid = security.new_id("rep_")
    tx.execute("INSERT INTO reports (id, audit_id, kind, format, stored_name, size_bytes, created_at, ready) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (rid, audit_id, kind, fmt, stored, size, now_iso(),
                                                           1 if ready else 0))
    return rid


def _docx_writer(kind):
    import report_docx
    return report_docx.generate_client_docx_report if kind == "client" else report_docx.generate_docx_report


def _stored_base(audit_id, kind, result):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", result.get("domain") or "site")[:80]
    return f"{audit_id}-{kind}-{safe}"


def _remove_file(stored):
    try:
        os.remove(os.path.join(config.REPORTS_DIR, os.path.basename(stored)))
    except OSError:
        pass


def register_for_app(audit_id, result, docx_files=None):
    """List the client and technical reports (DOCX and PDF) of a completed audit.
    The server makes no file: ``docx_files`` ({kind: bytes}) are the Word reports
    the app that ran the audit wrote, kept for the team; the others are built by
    a desktop app when first opened."""
    docx_files = docx_files or {}
    kept = []
    with db.transaction() as tx:
        for kind in ("client", "technical"):
            base = _stored_base(audit_id, kind, result)
            for fmt in ("docx", "pdf"):
                _remove_file(f"{base}.{fmt}")
                rid = _record(tx, audit_id, kind, fmt, f"{base}.{fmt}", ready=False)
                data = docx_files.get(kind) if fmt == "docx" else None
                if data and data.startswith(b"PK") and len(data) <= MAX_FILE_BYTES:
                    tx.execute("INSERT INTO report_data (report_id, data) VALUES (%s, %s)", (rid, data))
                    tx.execute("UPDATE reports SET ready = 1, size_bytes = %s WHERE id = %s", (len(data), rid))
                    kept.append(({"id": rid, "stored_name": f"{base}.docx"}, data))
    for rep, data in kept:
        _write_cache(rep, data)
    return ["client:docx", "client:pdf", "technical:docx", "technical:pdf"]


def generate_all(audit_id, result):
    """Server mode: generate the client + technical DOCX now and register their
    PDFs to be rendered on first use (when PDF conversion is available).
    Returns (generated_formats, errors)."""
    made, errors = [], []
    want_pdf = pdf_supported()
    for kind in ("client", "technical"):
        stored = _stored_base(audit_id, kind, result) + ".docx"
        path = os.path.join(config.REPORTS_DIR, stored)
        try:
            _docx_writer(kind)(result, path)
            with db.transaction() as tx:
                _record(tx, audit_id, kind, "docx", stored)
            made.append(f"{kind}:docx")
        except Exception as e:  # noqa: BLE001
            log.error("Report %s DOCX failed for %s: %s", kind, audit_id, e)
            errors.append(f"{kind} DOCX could not be generated")
            continue
        if want_pdf:
            pdf_stored = stored[:-5] + ".pdf"
            _remove_file(pdf_stored)  # an older PDF of this audit would no longer match the new DOCX
            with db.transaction() as tx:
                _record(tx, audit_id, kind, "pdf", pdf_stored, ready=False)
    return made, errors


def forget_files(audit_id):
    """App mode: drop the stored copies of an audit's report files so the apps
    build them again."""
    with db.transaction() as tx:
        for r in tx.query("SELECT id, stored_name FROM reports WHERE audit_id = %s", (audit_id,)):
            tx.execute("DELETE FROM report_data WHERE report_id = %s", (r["id"],))
            tx.execute("UPDATE reports SET ready = 0, size_bytes = 0 WHERE id = %s", (r["id"],))
            _remove_file(r["stored_name"])


def store_file(rep, data):
    """Keep a report file an app built (in the database and the disk cache)."""
    head = data[:5]
    if rep["format"] == "pdf" and not head.startswith(b"%PDF-") or rep["format"] == "docx" and not head.startswith(b"PK"):
        raise ApiError(422, "This is not a valid report file.")
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(413, "The report file is too large.")
    with db.transaction() as tx:
        tx.execute("DELETE FROM report_data WHERE report_id = %s", (rep["id"],))
        tx.execute("INSERT INTO report_data (report_id, data) VALUES (%s, %s)", (rep["id"], data))
        tx.execute("UPDATE reports SET ready = 1, size_bytes = %s WHERE id = %s", (len(data), rep["id"]))
    _write_cache(rep, data)
    return db.query_one("SELECT * FROM reports WHERE id = %s", (rep["id"],))


def _write_cache(rep, data):
    path = os.path.join(config.REPORTS_DIR, os.path.basename(rep["stored_name"]))
    part = f"{path}.{threading.get_ident()}.part"
    with open(part, "wb") as fh:
        fh.write(data)
    os.replace(part, path)


def _from_database(rep):
    copy = db.query_one("SELECT data FROM report_data WHERE report_id = %s", (rep["id"],))
    if not copy:
        return False
    _write_cache(rep, bytes(copy["data"]))
    return True


def ensure_file(rep):
    """Make sure a report row's file is on disk and return the current row.

    A file kept in the database is written back to disk. Otherwise, in app mode
    the caller is told to build it in the desktop app (409 ``build_in_app``); in
    server mode a missing DOCX is written again from the audit's stored result
    and a PDF is rendered from its DOCX on first use. Files are built one at a
    time; callers wait up to BUILD_WAIT_SECONDS."""
    if rep.get("ready", 1) and report_path(rep):
        return rep
    if _from_database(rep):
        return rep
    if made_in_app():
        raise ApiError(409, "This report is made by the desktop app on your computer.", code="build_in_app",
                       report_id=rep["id"], audit_id=rep["audit_id"], kind=rep["kind"], format=rep["format"])
    if rep["format"] == "pdf" and not pdf_supported():
        raise ApiError(409, "PDF rendering is not available on this server (Word or LibreOffice is required). "
                            "Download the DOCX report instead.", code="pdf_unavailable")
    if not _BUILD_LOCK.acquire(timeout=BUILD_WAIT_SECONDS):
        raise ApiError(503, "Other reports are being created right now. Try again in a minute.", code="busy")
    try:
        rep = db.query_one("SELECT * FROM reports WHERE id = %s", (rep["id"],)) or rep
        if rep.get("ready", 1) and report_path(rep):
            return rep  # made by another request while we waited
        stored = os.path.basename(rep["stored_name"])
        if rep["format"] == "docx":
            _rebuild_docx(rep)
        else:
            docx = db.query_one("SELECT * FROM reports WHERE audit_id = %s AND kind = %s AND format = 'docx'",
                                (rep["audit_id"], rep["kind"]))
            if not docx:
                raise ApiError(410, "The Word report this PDF is made from is missing. Regenerate the reports from the audit.",
                               code="gone")
            if not report_path(docx):
                _rebuild_docx(docx)
            if not docx_to_pdf(report_path(docx), os.path.join(config.REPORTS_DIR, stored)):
                raise ApiError(500, "The PDF could not be created. Download the DOCX report instead.", code="pdf_failed")
        size = os.path.getsize(os.path.join(config.REPORTS_DIR, stored))
        db.execute("UPDATE reports SET ready = 1, size_bytes = %s WHERE id = %s", (size, rep["id"]))
        return db.query_one("SELECT * FROM reports WHERE id = %s", (rep["id"],))
    finally:
        _BUILD_LOCK.release()


def _rebuild_docx(rep):
    """Write a DOCX report again from the audit's stored result."""
    from backend import results

    result = results.load(rep["audit_id"])
    if result is None:
        raise ApiError(410, "This report can no longer be made because the audit's results are missing.", code="gone")
    try:
        _docx_writer(rep["kind"])(result, os.path.join(config.REPORTS_DIR, os.path.basename(rep["stored_name"])))
    except Exception as e:  # noqa: BLE001
        log.error("Report %s DOCX rebuild failed for %s: %s", rep["kind"], rep["audit_id"], e)
        raise ApiError(500, "The report could not be created. Try again in a minute.", code="report_failed") from e


def report_path(row):
    if not row.get("stored_name"):
        return None
    path = os.path.realpath(os.path.join(config.REPORTS_DIR, row["stored_name"]))
    if not path.startswith(os.path.realpath(config.REPORTS_DIR) + os.sep) or not os.path.isfile(path):
        return None
    return path


def render_client_html(audit_id, result):
    """Self-contained HTML client report (no server round-trips needed)."""
    global _LUCIDE_JS
    if _LUCIDE_JS is None:
        p = os.path.join(config.RESOURCE_DIR, "static", "libs", "lucide.min.js")
        try:
            with open(p, encoding="utf-8") as fh:
                _LUCIDE_JS = fh.read().replace("</script", "<\\/script")
        except OSError:
            _LUCIDE_JS = ""
    return render_template("client_report.html", audit_id=audit_id, result=result,
                           client_data=prepare_client_report_context(result), lucide_js=_LUCIDE_JS)
