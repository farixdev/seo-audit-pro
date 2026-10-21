# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the SEO Audit Pro desktop CLIENT.

The client contains no server code, no database driver and no secrets: only
the PySide6 UI, the HTTP client and brand assets. Build with build_exe.bat.
Output: dist/SEOAuditPro/SEOAuditPro.exe (one-folder build for fast startup).
"""

import os

BASE = SPECPATH
ASSETS = os.path.join(BASE, "seo_audit_pro", "assets")

a = Analysis(
    [os.path.join(BASE, "SEOAuditPro.pyw")],
    pathex=[BASE],
    binaries=[],
    datas=[(ASSETS, os.path.join("seo_audit_pro", "assets"))],
    hiddenimports=[
        # Pages are imported lazily through the registry.
        "seo_audit_pro.ui.pages.dashboard", "seo_audit_pro.ui.pages.leads", "seo_audit_pro.ui.pages.lead_detail",
        "seo_audit_pro.ui.pages.lead_import_dialog",
        "seo_audit_pro.ui.pages.audit_dialogs",
        "seo_audit_pro.ui.pages.audits", "seo_audit_pro.ui.pages.audit_detail", "seo_audit_pro.ui.pages.reports",
        "seo_audit_pro.ui.pages.teams", "seo_audit_pro.ui.pages.users",
        "seo_audit_pro.ui.pages.outreach", "seo_audit_pro.ui.pages.chat", "seo_audit_pro.ui.pages.notifications",
        "seo_audit_pro.ui.pages.audit_logs", "seo_audit_pro.ui.pages.settings_page", "seo_audit_pro.ui.pages.profile",
        "keyring.backends.Windows",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Server and engine code must never ship in the client.
        "backend", "web_app", "agency_db", "agency_routes", "audit", "crawler", "security_audit", "seo_checks",
        "scoring", "pagespeed", "authority_traffic", "structured_data", "history_manager", "report_docx",
        "report_html", "psycopg2", "flask", "flask_sock", "matplotlib", "docx", "trafilatura", "lxml",
        "tkinter", "numpy", "PIL",
        # Unused Qt modules.
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtGraphs", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSerialPort",
    ],
    noarchive=False,
)
# Qt plugins drag in Quick/QML/PDF/virtual-keyboard libraries and the software
# OpenGL renderer; a Qt Widgets client needs none of them.
_UNUSED = ("qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qtvirtualkeyboardplugin", "qpdf.dll",
           "opengl32sw.dll", "qt6opengl.dll")
a.binaries = [b for b in a.binaries if not any(u in b[0].lower().replace("\\", "/").split("/")[-1] for u in _UNUSED)]

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
