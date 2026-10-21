# -*- coding: utf-8 -*-
"""
Make the portable zip you can hand to anyone:

  dist/SEO-Audit-Pro-Portable-<version>.zip
    SEO Audit Pro/
      SEOAuditPro.exe            <- double-click to start
      _internal/                 <- the app's files (keep next to the exe)
      READ ME FIRST.txt
      User Manual.pdf            (when docs/manuals/ has it)
      Technical Reference.pdf    (when docs/manuals/ has it)

Run after PyInstaller (build_exe.bat does both). Refuses to package secrets or
databases if any ended up in the build folder.
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from seo_audit_pro import APP_VERSION  # noqa: E402

BUILD = os.path.join(ROOT, "dist", "SEOAuditPro")
MANUALS = os.path.join(ROOT, "docs", "manuals")
OUT = os.path.join(ROOT, "dist", f"SEO-Audit-Pro-Portable-{APP_VERSION}.zip")
TOP = "SEO Audit Pro"

README = f"""SEO Audit Pro {APP_VERSION}
=====================

START
  1. Unzip this folder somewhere you keep (for example Documents\\SEO Audit Pro).
     Do not run the app from inside the zip.
  2. Double-click SEOAuditPro.exe.
  3. If Windows says "Windows protected your PC", click "More info" and then
     "Run anyway". Windows shows this for new apps that are not from the Store.
  4. Choose "On this computer" and create your Manager account. That's it.

KEEP TOGETHER
  SEOAuditPro.exe needs the _internal folder next to it. Move or copy the whole
  "SEO Audit Pro" folder, never the exe alone.

YOUR DATA
  Saved in %LOCALAPPDATA%\\SEO Audit Pro\\data (the database, reports and logs).
  To back up: close the app and copy that folder.

NEEDS
  Windows 10 or 11 (64-bit), 4 GB of memory, an internet connection for audits.
  PDF reports need Microsoft Word or LibreOffice; Word (.docx) reports always work.

HELP
  See "User Manual.pdf" in this folder.
"""

_FORBIDDEN = (".env", ".secret_key", ".data_encryption_key")


def main():
    exe = os.path.join(BUILD, "SEOAuditPro.exe")
    if not os.path.isfile(exe):
        sys.exit("dist/SEOAuditPro/SEOAuditPro.exe not found - run PyInstaller first (build_exe.bat).")
    bad = []
    for base, _dirs, files in os.walk(BUILD):
        for f in files:
            if f in _FORBIDDEN or f.endswith((".db", ".sqlite", ".db-wal", ".db-shm")):
                bad.append(os.path.join(base, f))
    if bad:
        sys.exit("Refusing to package secrets or databases:\n  " + "\n  ".join(bad))
    if os.path.exists(OUT):
        os.remove(OUT)
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(BUILD):
            for f in files:
                full = os.path.join(base, f)
                z.write(full, os.path.join(TOP, os.path.relpath(full, BUILD)))
                count += 1
        z.writestr(f"{TOP}/READ ME FIRST.txt", README.replace("\n", "\r\n"))
        for name in ("User Manual.pdf", "Technical Reference.pdf"):
            src = os.path.join(MANUALS, f"SEO Audit Pro - {name}")
            if os.path.isfile(src):
                z.write(src, f"{TOP}/{name}")
                count += 1
            else:
                print(f"note: {src} not found - packaged without it")
    print(f"Portable zip: {OUT} ({count + 1} files, {os.path.getsize(OUT) / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
