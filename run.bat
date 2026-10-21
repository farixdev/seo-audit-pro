@echo off
rem Starts the SEO Audit Pro API server locally on http://127.0.0.1:5055
rem using the local SQLite development database (server_data\).
rem To run against PostgreSQL instead, start web_app.py with DATABASE_URL set
rem and SEO_DB unset.
cd /d "%~dp0"
set SEO_DB=sqlite
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" web_app.py
) else (
    python web_app.py
)
