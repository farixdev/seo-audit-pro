@echo off
rem ======================================================================
rem  Build SEO Audit Pro for Windows (desktop app + built-in server).
rem  Output:
rem    dist\SEOAuditPro\SEOAuditPro.exe              the app (one folder)
rem    dist\SEO-Audit-Pro-Portable-3.0.0.zip         portable zip to share
rem    dist\SEOAuditPro-Setup-3.0.0.exe              only if Inno Setup 6 is installed
rem ======================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv...
    python -m venv .venv || goto :err
)
echo [1/4] Installing dependencies...
call .venv\Scripts\python.exe -m pip install -q -r requirements.txt -r requirements-desktop.txt || goto :err

echo [2/4] Regenerating brand assets...
call .venv\Scripts\python.exe scripts\build_assets.py || goto :err

echo [3/4] Building the app with PyInstaller...
call .venv\Scripts\pyinstaller.exe --noconfirm --clean SEOAuditPro.spec || goto :err

echo [4/4] Packaging the portable zip...
call .venv\Scripts\python.exe scripts\package_portable.py || goto :err

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
    echo Building the installer...
    call %ISCC% installer\SEOAuditPro.iss || goto :err
)
echo.
echo Build successful.
exit /b 0

:err
echo.
echo Build failed. See the messages above.
exit /b 1
