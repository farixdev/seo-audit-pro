# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SEO Audit Pro (Windows, all-in-one).

One program, two roles:
  SEOAuditPro.exe           the desktop app
  SEOAuditPro.exe --serve   the built-in API server + audit engine, started by
                            the app in "This computer" mode (no window)

No secrets are bundled: .env is never included; a local install creates its own
keys and database on first start. Build with build_exe.bat.
Output: dist/SEOAuditPro/SEOAuditPro.exe (one-folder build for fast startup).
"""

import os

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

BASE = SPECPATH
ASSETS = os.path.join(BASE, "seo_audit_pro", "assets")

datas = [
    (ASSETS, os.path.join("seo_audit_pro", "assets")),
    (os.path.join(BASE, "templates"), "templates"),                      # HTML client report
    (os.path.join(BASE, "static", "libs"), os.path.join("static", "libs")),
]
# Engine libraries without PyInstaller hooks that read data files at runtime.
for pkg in ("trafilatura", "justext", "tld"):
    datas += collect_data_files(pkg, excludes=["**/tests/**"])
# Packages that look up their own version through importlib.metadata.
for pkg in ("flask", "werkzeug", "jinja2", "trafilatura", "htmldate", "courlan", "justext", "requests",
            "python-docx", "openpyxl", "cryptography", "keyring", "dateparser"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001 - not every package ships metadata the same way
        pass

a = Analysis(
    [os.path.join(BASE, "SEOAuditPro.pyw")],
    pathex=[BASE],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Pages are imported lazily through the registry.
        "seo_audit_pro.ui.pages.dashboard", "seo_audit_pro.ui.pages.leads", "seo_audit_pro.ui.pages.lead_detail",
        "seo_audit_pro.ui.pages.lead_import_dialog", "seo_audit_pro.ui.pages.audit_dialogs",
        "seo_audit_pro.ui.pages.audits", "seo_audit_pro.ui.pages.audit_detail", "seo_audit_pro.ui.pages.reports",
        "seo_audit_pro.ui.pages.teams", "seo_audit_pro.ui.pages.users",
        "seo_audit_pro.ui.pages.outreach", "seo_audit_pro.ui.pages.chat", "seo_audit_pro.ui.pages.notifications",
        "seo_audit_pro.ui.pages.audit_logs", "seo_audit_pro.ui.pages.settings_page", "seo_audit_pro.ui.pages.profile",
        "keyring.backends.Windows",
        # Built-in server: imported only when started with --serve, and the engine
        # modules it loads inside functions.
        "web_app", "backend.app", "backend.routes.admin_routes", "backend.routes.audit_routes",
        "backend.routes.auth_routes", "backend.routes.chat_routes", "backend.routes.lead_routes",
        "backend.routes.org_routes", "backend.routes.outreach_routes", "backend.routes.workspace_routes",
        "audit", "crawler", "security_audit", "seo_checks", "scoring", "pagespeed", "authority_traffic",
        "structured_data", "history_manager", "report_docx", "report_html", "charts", "llm", "prefs",
        "flask_sock", "simple_websocket", "openpyxl", "matplotlib.backends.backend_agg",
    ],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": "Agg"}},  # charts render to PNG only
    runtime_hooks=[],
    excludes=[
        "psycopg2",  # a local install always uses its own SQLite database
        "tkinter", "agency_db", "agency_routes", "IPython", "pytest",
        # Unused Qt modules.
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtGraphs", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSerialPort",
    ],
    noarchive=False,
)
# Qt plugins drag in Quick/QML/PDF/virtual-keyboard libraries and the software
# OpenGL renderer; a Qt Widgets app needs none of them.
_UNUSED = ("qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qtvirtualkeyboardplugin", "qpdf.dll",
           "opengl32sw.dll", "qt6opengl.dll")
a.binaries = [b for b in a.binaries if not any(u in b[0].lower().replace("\\", "/").split("/")[-1] for u in _UNUSED)]
# Never ship local secrets or databases even if they sit in the source folder.
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").split("/")[-1].startswith(".env")
           and not d[0].lower().endswith((".db", ".sqlite"))]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SEOAuditPro",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ASSETS, "app.ico"),
    version=os.path.join(BASE, "installer", "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SEOAuditPro")
