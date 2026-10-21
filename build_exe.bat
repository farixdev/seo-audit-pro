@echo off
rem ======================================================================
rem  Build the SEO Audit Pro desktop client (Windows).
rem  Output: dist\SEOAuditPro\SEOAuditPro.exe and, if Inno Setup 6 is
rem  installed, dist\SEOAuditPro-Setup-3.0.0.exe
rem ======================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv...
    python -m venv .venv || goto :err
)
call .venv\Scripts\python.exe -m pip install -q -r requirements-desktop.txt || goto :err

echo [1/3] Regenerating brand assets...
call .venv\Scripts\python.exe scripts\build_assets.py || goto :err

echo [2/3] Building the desktop client with PyInstaller...
call .venv\Scripts\pyinstaller.exe --noconfirm --clean SEOAuditPro.spec || goto :err

echo [3/3] Building the installer...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
    call %ISCC% installer\SEOAuditPro.iss || goto :err
) else (
    echo Inno Setup 6 not found - skipping installer. The app is in dist\SEOAuditPro\
)
echo.
echo Build successful.
exit /b 0

:err
echo.
echo Build failed. See the messages above.
exit /b 1
