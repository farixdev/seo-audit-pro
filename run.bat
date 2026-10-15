@echo off
rem ----------------------------------------------------------------------
rem  SEO Audit Pro — Web UI Launcher (Click & Browser Opens)
rem ----------------------------------------------------------------------
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" "web_app.py"
) else (
    echo Local .venv not found. Falling back to system Python...
    start "" python "web_app.py"
)
