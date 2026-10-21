# -*- coding: utf-8 -*-
"""
DOCX to PDF conversion with Microsoft Word (Windows) or LibreOffice, whichever
is installed. Used by the API server and by the desktop app, which builds
reports on the user's own computer. If neither program exists, PDF is reported
as unavailable; it is never faked.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger("seo_audit_pro.pdf")


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
                       capture_output=True, timeout=180, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        produced = os.path.join(outdir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
        if os.path.isfile(produced):
            shutil.move(produced, pdf_path)
            return True
        return False
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def docx_to_pdf(docx_path, pdf_path):
    # Convert a copy in a short temporary folder: Word opens files there reliably,
    # whatever the folder names, length or permissions of the real location.
    work = tempfile.mkdtemp(prefix="seo-pdf-")
    try:
        src, out = os.path.join(work, "report.docx"), os.path.join(work, "report.pdf")
        shutil.copyfile(docx_path, src)
        if not (_soffice() and _docx_to_pdf_soffice(src, out)) and not (_word_available() and _docx_to_pdf_word(src, out)):
            return False
        shutil.move(out, pdf_path)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("PDF conversion failed: %s", e)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
