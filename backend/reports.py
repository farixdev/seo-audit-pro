# -*- coding: utf-8 -*-
"""
Report generation for completed audits, reusing the existing deliverable
pipeline (report_docx + templates/client_report.html).

PDF conversion uses Microsoft Word (Windows) or LibreOffice if either is
installed on the server. If neither exists, PDF is reported as unavailable —
it is never faked.

PDFs are made on demand: when an audit completes only the DOCX files are
written, plus a PDF row with ``ready = 0``. The PDF is rendered the first time
someone downloads or emails it, one conversion at a time (each Word/LibreOffice
process needs 150–250 MB, so parallel conversions would overload a small server).
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from flask import render_template

from backend import config, db, security
from backend.errors import ApiError
from backend.report_context import prepare_client_report_context
from backend.util import now_iso

log = logging.getLogger("seo_audit_pro.reports")
_LUCIDE_JS = None
_PDF_LOCK = threading.Lock()
PDF_WAIT_SECONDS = 240


def _soffice():
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.isfile(p):
            return p
    return None


def _word_available():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application"))
        return True
    except OSError:
        return False


def pdf_supported():
    return bool(_soffice()) or _word_available()


def _docx_to_pdf_word(docx_path, pdf_path):
    script = (
        "param($in,$out)\n"
        "$w = New-Object -ComObject Word.Application\n"
        "$w.Visible = $false\n"
        "try { $d = $w.Documents.Open($in); try { $d.Fields.Update() | Out-Null } catch {}\n"
        "  try { foreach($t in $d.TablesOfContents){ $t.Update() } } catch {}\n"
        "  $d.SaveAs([ref]$out,[ref]17); $d.Close($false) } finally { $w.Quit() }\n"
    )
    fh = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8")
    try:
        fh.write(script)
        fh.close()
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", fh.name,
                        os.path.abspath(docx_path), os.path.abspath(pdf_path)],
                       capture_output=True, timeout=180, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        try:
            os.remove(fh.name)
        except OSError:
            pass
    return os.path.isfile(pdf_path)


def _docx_to_pdf_soffice(docx_path, pdf_path):
    exe = _soffice()
    outdir = tempfile.mkdtemp()
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", outdir, os.path.abspath(docx_path)],
                       capture_output=True, timeout=180, check=False)
        produced = os.path.join(outdir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
        if os.path.isfile(produced):
            shutil.move(produced, pdf_path)
            return True
        return False
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def docx_to_pdf(docx_path, pdf_path):
    try:
        if _soffice() and _docx_to_pdf_soffice(docx_path, pdf_path):
            return True
        if _word_available() and _docx_to_pdf_word(docx_path, pdf_path):
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("PDF conversion failed: %s", e)
    return False


def _record(tx, audit_id, kind, fmt, stored, ready=True):
    size = os.path.getsize(os.path.join(config.REPORTS_DIR, stored)) if ready else 0
    tx.execute("DELETE FROM reports WHERE audit_id = %s AND kind = %s AND format = %s", (audit_id, kind, fmt))
    rid = security.new_id("rep_")
    tx.execute("INSERT INTO reports (id, audit_id, kind, format, stored_name, size_bytes, created_at, ready) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (rid, audit_id, kind, fmt, stored, size, now_iso(),
                                                           1 if ready else 0))
    return rid


def _remove_file(stored):
    try:
        os.remove(os.path.join(config.REPORTS_DIR, os.path.basename(stored)))
    except OSError:
        pass


def generate_all(audit_id, result):
    """Generate the client + technical DOCX now and register their PDFs to be
    rendered on first use (when PDF conversion is available).
    Returns (generated_formats, errors)."""
    import report_docx

    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", result.get("domain") or "site")[:80]
    made, errors = [], []
    jobs = [("client", report_docx.generate_client_docx_report), ("technical", report_docx.generate_docx_report)]
    want_pdf = pdf_supported()
    for kind, fn in jobs:
        stored = f"{audit_id}-{kind}-{safe}.docx"
        path = os.path.join(config.REPORTS_DIR, stored)
        try:
            fn(result, path)
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


def ensure_pdf(rep):
    """Render an on-demand PDF report row if needed. Returns the current row.
    Conversions run one at a time; callers wait up to PDF_WAIT_SECONDS."""
    if rep["format"] != "pdf" or (rep.get("ready", 1) and report_path(rep)):
        return rep
    if not pdf_supported():
        raise ApiError(409, "PDF rendering is not available on this server (Word or LibreOffice is required). "
                            "Download the DOCX report instead.", code="pdf_unavailable")
    if not _PDF_LOCK.acquire(timeout=PDF_WAIT_SECONDS):
        raise ApiError(503, "Other PDFs are being created right now. Try again in a minute.", code="busy")
    try:
        rep = db.query_one("SELECT * FROM reports WHERE id = %s", (rep["id"],)) or rep
        if rep.get("ready") and report_path(rep):
            return rep  # made by another request while we waited
        docx = db.query_one("SELECT * FROM reports WHERE audit_id = %s AND kind = %s AND format = 'docx'",
                            (rep["audit_id"], rep["kind"]))
        docx_path = report_path(docx) if docx else None
        if not docx_path:
            raise ApiError(410, "The Word report this PDF is made from is missing. Regenerate the reports from the audit.",
                           code="gone")
        stored = os.path.basename(rep["stored_name"])
        if not docx_to_pdf(docx_path, os.path.join(config.REPORTS_DIR, stored)):
            raise ApiError(500, "The PDF could not be created. Download the DOCX report instead.", code="pdf_failed")
        size = os.path.getsize(os.path.join(config.REPORTS_DIR, stored))
        db.execute("UPDATE reports SET ready = 1, size_bytes = %s WHERE id = %s", (size, rep["id"]))
        return db.query_one("SELECT * FROM reports WHERE id = %s", (rep["id"],))
    finally:
        _PDF_LOCK.release()


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
