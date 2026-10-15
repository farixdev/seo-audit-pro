@echo off
rem ======================================================================
rem  Build SEO Audit Pro into a standalone executable and portable zip.
rem ======================================================================
setlocal
cd /d "%~dp0"

echo [1/3] Checking Python virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv...
    python -m venv .venv || goto :err
)

call .venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller || goto :err

echo [2/3] Building executables with PyInstaller...
call .venv\Scripts\pyinstaller.exe --noconfirm --clean "SEO Audit Pro Web.spec" || goto :err

echo [3/3] Creating distribution zips...
.venv\Scripts\python.exe -c "import zipfile, os, pathlib, shutil; \
standalone_exe = pathlib.Path('dist/SEO Audit Pro (Standalone).exe'); \
standalone_zip = pathlib.Path('dist/SEO Audit Pro-Standalone.zip'); \
if standalone_zip.exists(): standalone_zip.unlink(); \
with zipfile.ZipFile(standalone_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf: \
    zf.write(standalone_exe, 'SEO Audit Pro.exe'); \
portable_zip = pathlib.Path('dist/SEO Audit Pro-Portable.zip'); \
if portable_zip.exists(): portable_zip.unlink(); \
with zipfile.ZipFile(portable_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf: \
    for root, dirs, files in os.walk('dist/SEO Audit Pro'): \
        for f in files: \
            full = os.path.join(root, f); \
            zf.write(full, os.path.relpath(full, 'dist')); \
pkg = pathlib.Path('../packaging/dist/SEO Audit Pro'); \
if pkg.exists(): \
    shutil.copy2(standalone_zip, pkg / 'SEO Audit Pro.zip'); \
    shutil.copy2(standalone_zip, pkg / 'SEO Audit Pro-Standalone.zip'); \
    shutil.copy2(portable_zip, pkg / 'SEO Audit Pro-Portable.zip'); \
    shutil.copy2(standalone_exe, pkg / 'SEO Audit Pro (Standalone).exe'); \
" || goto :err

echo.
echo ======================================================================
echo  Build successful!
echo  Single-file EXE:  dist\SEO Audit Pro (Standalone).exe
echo  Shareable ZIP:    dist\SEO Audit Pro-Standalone.zip
echo  Portable ZIP:     dist\SEO Audit Pro-Portable.zip
echo ======================================================================
exit /b 0

:err
echo.
echo Build failed. Please check errors above.
exit /b 1
