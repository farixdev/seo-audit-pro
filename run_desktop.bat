@echo off
rem Starts the SEO Audit Pro desktop client from source (no console window).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" SEOAuditPro.pyw
) else (
    start "" pythonw SEOAuditPro.pyw
)
