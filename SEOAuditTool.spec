# -*- mode: python ; coding: utf-8 -*-
"""Standalone SEO Audit Tool: one exe (no server, no sign-in).
Build: .venv\\Scripts\\pyinstaller.exe --noconfirm SEOAuditTool.spec  ->  dist\\SEO Audit Tool.exe"""

import os

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

BASE = SPECPATH
ASSETS = os.path.join(BASE, "seo_audit_pro", "assets")
datas = [(ASSETS, os.path.join("seo_audit_pro", "assets")), (os.path.join(BASE, "templates"), "templates"),
         (os.path.join(BASE, "static", "libs"), os.path.join("static", "libs"))]
for pkg in ("trafilatura", "justext", "tld"):
    datas += collect_data_files(pkg, excludes=["**/tests/**"])
for pkg in ("trafilatura", "htmldate", "courlan", "justext", "requests", "python-docx", "dateparser"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001
        pass

a = Analysis(
    [os.path.join(BASE, "audit_tool.pyw")],
    pathex=[BASE],
    datas=datas,
    hiddenimports=["audit", "crawler", "security_audit", "seo_checks", "scoring", "pagespeed", "authority_traffic",
                   "structured_data", "report_docx", "charts", "llm", "prefs", "pdf_convert",
                   "matplotlib.backends.backend_agg", "seo_audit_pro.brand",
                   "full_report", "client_report"],
    hooksconfig={"matplotlib": {"backends": "Agg"}},
    excludes=["psycopg2", "flask", "tkinter", "IPython", "pytest", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
              "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtDesigner",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtPositioning", "PySide6.QtLocation"],
)
_UNUSED = ("qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qtvirtualkeyboardplugin", "qpdf.dll",
           "opengl32sw.dll", "qt6opengl.dll")
a.binaries = [b for b in a.binaries if not any(u in b[0].lower().replace("\\", "/").split("/")[-1] for u in _UNUSED)]
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").split("/")[-1].startswith(".env")]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="SEO Audit Tool", debug=False, strip=False, upx=False,
          console=False, icon=os.path.join(ASSETS, "app.ico"), runtime_tmpdir=None)
