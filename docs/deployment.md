# Deployment

## API server

Requirements: Python 3.12+, PostgreSQL (Neon), HTTPS termination (the hosting
platform or a reverse proxy). Run exactly one process.

Environment variables:

| Variable | Required | Purpose |
|---|---|---|
| `SEO_ENV` | yes (`production`) | enables PostgreSQL via `DATABASE_URL` and refuses to start without real secrets. Outside production, `DATABASE_URL` is ignored unless `SEO_DB=postgres` is set |
| `DATABASE_URL` | yes | PostgreSQL connection string |
| `SECRET_KEY` | yes | signs access tokens (≥32 random bytes) |
| `DATA_ENCRYPTION_KEY` | yes | encrypts the stored SMTP password |
| `SEO_DATA_DIR` | yes | persistent directory for reports, uploads, logs |
| `BOOTSTRAP_MANAGER_EMAIL` / `BOOTSTRAP_MANAGER_PASSWORD` / `BOOTSTRAP_MANAGER_NAME` | first start only | creates the first Manager when the user table is empty (must change password at first sign-in) |
| `PAGESPEED_API_KEY`, `GROQ_API_KEY` … | optional | engine quota / AI executive summary |
| `MAX_CONCURRENT_AUDITS` (2 — the default until a Manager changes it in Settings), `BULK_MAX_RECIPIENTS` (200), `EMAIL_SEND_INTERVAL_SECONDS` (4), `DAILY_EMAIL_LIMIT` (500), `ACCESS_TOKEN_TTL`, `REFRESH_TOKEN_TTL` | optional | limits |

Start command:

```
gunicorn web_app:app --bind 0.0.0.0:$PORT --workers 1 --threads 32 --timeout 180
```

`render.yaml` and `Procfile` are configured accordingly, including a persistent
disk for `SEO_DATA_DIR`. Without a persistent disk, uploaded files and report
files are lost on redeploy (audit results remain in PostgreSQL and reports can
be regenerated from the audit screen).

PDF reports need Microsoft Word (Windows host) or LibreOffice (`soffice` on
PATH) on the server; otherwise the app offers DOCX and the HTML report and
states that PDF is unavailable. PDFs are created the first time someone opens
or emails them, one at a time.

### Sizing

Measured on this codebase (50-page audits, real sites):

| | Memory |
|---|---|
| API server idle | ~60 MB |
| Server while 2 audits run (default) | ~250 MB peak |
| One PDF conversion (Word) | ~110 MB, ~7 s, one at a time |
| Desktop app | ~120 MB at start, ~165 MB after every screen |

A 4 GB machine runs the server comfortably at 2 audits at a time (roughly
60–100 fifty-page audits an hour, depending on the sites). Use 3–4 only with
8 GB or more. Stored results take ~80 KB per audit in the database; report
files take ~100 KB per audit until PDFs are opened (~0.5–0.8 MB each).

First Manager without bootstrap variables:

```
python -m backend.manage create-manager --email you@agency.com --name "Your Name"
```

## Desktop client

Build on Windows:

```
build_exe.bat
```

produces `dist\SEOAuditPro\SEOAuditPro.exe` (one-folder build) and, when Inno
Setup 6 is installed, `dist\SEOAuditPro-Setup-3.0.0.exe` (installer with Start
menu entry, optional desktop shortcut and uninstaller). On first launch, users
set the server address from the sign-in screen ("Change server"). The default
is `http://127.0.0.1:5055` for local development and can be preset with the
`SEO_API_URL` environment variable.

## Local development

```
set SEO_DB=sqlite
.venv\Scripts\python.exe scripts\dev_seed.py        (once; writes server_data\dev_credentials.txt)
run.bat                                              (API on http://127.0.0.1:5055, SQLite)
run_desktop.bat                                      (client)
```

Outside `SEO_ENV=production` the server always uses the local SQLite database
in `server_data/`, even when a `.env` file contains a production
`DATABASE_URL` (a notice is printed). To develop against a PostgreSQL test
database, set `SEO_DB=postgres` and point `DATABASE_URL` at a non-production
database — never at production.
