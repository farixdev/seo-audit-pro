# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_all

BASE_DIR = SPECPATH
if not os.path.exists(os.path.join(BASE_DIR, "app.py")):
    BASE_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "..", "SEO Audit Pro"))

assets_dir = os.path.join(BASE_DIR, "assets")
icon_path = os.path.join(assets_dir, "icon.ico")
app_script = os.path.join(BASE_DIR, "app.py")

datas = [(assets_dir, "assets")]
binaries = []
hiddenimports = [
    "requests",
    "bs4",
    "lxml",
    "docx",
    "matplotlib",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_agg",
]

# Collect docx data files
datas += collect_data_files("docx")

# Matplotlib data files (fonts, styles, etc.)
datas += collect_data_files("matplotlib")

# Dependencies with data / submodules
for pkg in ["trafilatura", "courlan", "htmldate", "dateparser", "justext", "babel", "tld"]:
    try:
        ret = collect_all(pkg)
        datas += ret[0]
        binaries += ret[1]
        hiddenimports += ret[2]
    except Exception as e:
        print(f"Warning collecting {pkg}: {e}")

# Bundling MSVC runtime DLLs to prevent 'MSVCP140.dll was not found' or 'VCRUNTIME140.dll was not found'
# on target computers that don't have Microsoft Visual C++ 2015-2022 Redistributable installed.
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
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
        'PySide6.QtQml', 'PySide6.QtQmlModels',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DExtras', 'PySide6.Qt3DInput',
        'PySide6.Qt3DAnimation', 'PySide6.Qt3DLogic',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtUiTools',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtSensors',
        'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtBluetooth',
        'PySide6.QtNfc', 'PySide6.QtSerialPort', 'PySide6.QtWebSockets',
        'PySide6.QtWebChannel', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
        'PySide6.QtTextToSpeech', 'PySide6.QtSpatialAudio'
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 1) Folder-based build (onedir mode: SEO Audit Pro.exe + _internal folder)
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

# 2) Standalone Single-File Executable (onefile mode: self-contained single EXE with embedded DLLs)
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
