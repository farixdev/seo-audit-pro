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
.venv\Scripts\python.exe package_dist.py || goto :err

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
