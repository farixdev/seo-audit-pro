# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller specification for SEO Audit Pro Desktop Web Edition.
Builds a self-contained single-file executable that starts the local web server
and automatically launches the default browser, storing all audit deliverables
locally with no database requirement.
"""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_all

BASE_DIR = SPECPATH

assets_dir = os.path.join(BASE_DIR, "assets")
templates_dir = os.path.join(BASE_DIR, "templates")
static_dir = os.path.join(BASE_DIR, "static")
icon_path = os.path.join(assets_dir, "icon.ico")
app_script = os.path.join(BASE_DIR, "web_app.py")

datas = [
    (assets_dir, "assets"),
    (templates_dir, "templates"),
    (static_dir, "static"),
]
binaries = []
hiddenimports = [
    "jinja2",
    "jinja2.ext",
    "werkzeug",
    "flask",
    "requests",
    "certifi",
    "bs4",
    "lxml",
    "lxml.etree",
    "docx",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "trafilatura",
    "courlan",
    "htmldate",
    "dateparser",
    "justext",
    "babel",
    "tld",
]

# Collect docx, matplotlib and certifi data
datas += collect_data_files("docx")
datas += collect_data_files("matplotlib")
datas += collect_data_files("certifi")

# Dependencies with data / submodules
for pkg in ["trafilatura", "courlan", "htmldate", "dateparser", "justext", "babel", "tld"]:
    try:
        ret = collect_all(pkg)
        datas += ret[0]
        binaries += ret[1]
        hiddenimports += ret[2]
    except Exception as e:
        print(f"Warning collecting {pkg}: {e}")

# Bundling MSVC runtime DLLs
sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
vc_dlls = [
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "ucrtbase.dll",
]
for dll in vc_dlls:
    p = os.path.join(sys32, dll)
    if os.path.exists(p):
        binaries.append((p, "."))

a = Analysis(
    [app_script],
    pathex=[BASE_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PyQt5', 'PyQt6', 'tkinter'
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 1) Portable Folder Distribution Executable (fastest startup, no temp extraction)
exe_dir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SEO Audit Pro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[icon_path],
)
coll = COLLECT(
    exe_dir,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SEO Audit Pro',
)

# 2) Standalone Single-File Executable (single self-contained .exe)
exe_standalone = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SEO Audit Pro (Standalone)',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[icon_path],
)
