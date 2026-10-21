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
| `SEO_DATA_DIR` | yes | working directory for report files, upload cache, logs (may be temporary, see below) |
| `BOOTSTRAP_MANAGER_EMAIL` / `BOOTSTRAP_MANAGER_PASSWORD` / `BOOTSTRAP_MANAGER_NAME` | first start only | creates the first Manager when the user table is empty (must change password at first sign-in) |
| `PAGESPEED_API_KEY`, `GROQ_API_KEY` … | optional | engine quota / AI executive summary |
| `AUDITS_RUN_ON` | optional | `app` (default): audits, Word/PDF reports and lead-file reading run on each person's computer in the desktop app; `server`: the server runs audits and makes report files itself |
| `MAX_CONCURRENT_AUDITS` (2 — audits at a time on each computer until a Manager changes it in Settings), `BULK_MAX_RECIPIENTS` (200), `EMAIL_SEND_INTERVAL_SECONDS` (4), `DAILY_EMAIL_LIMIT` (500), `ACCESS_TOKEN_TTL`, `REFRESH_TOKEN_TTL` | optional | limits |

Start command:

```
gunicorn web_app:app --bind 0.0.0.0:$PORT --workers 1 --threads 32 --timeout 180
```

### Where the work happens

With `AUDITS_RUN_ON=app` the server only connects the team: sign-in and
permissions, storing and sharing data in PostgreSQL, live events and chat, and
sending email (the agency mailbox password stays on the server). Everything
heavy runs in each person's desktop app, in background processes at
below-normal priority:

* **Audits.** The app asks for the audits its user queued (`concurrency()` at a
  time on that computer), runs the engine, forwards its progress messages and
  sends the result together with both Word reports. PageSpeed requests go
  through `/api/pagespeed`, so the Google API key stays on the server.
* **Reports.** Word reports arrive with each result and are stored in
  PostgreSQL for the whole team. A PDF (or a Word report made again after
  "Regenerate") is built by the app of whoever opens it first, with Microsoft
  Word or LibreOffice on that computer, and stored for everyone else.
* **Lead files.** The app reads the file and sends the column names, row count
  and a sample for the preview, then the rows in gzip chunks of 1,000. The
  server checks them against the lead bank and stores them; the file itself
  never leaves the computer. An import interrupted by closing the app continues
  from the last stored row when the app starts again.

### Render (free plan)

`render.yaml` (a Render Blueprint) and `Procfile` are configured accordingly.
The Blueprint uses Render's **free plan** in the `ohio` region, next to the
Neon database:

* No persistent disk: `SEO_DATA_DIR` is `/tmp/seo-audit-pro` and is wiped on
  every restart, redeploy and sleep. Nothing is lost: uploaded files (email
  attachments, photos) and report files are stored in PostgreSQL
  (`upload_data`, `report_data`) and written back to disk when next opened.
* The service sleeps after 15 minutes without incoming requests and takes
  about a minute to wake; the desktop app waits up to 90 seconds at start. To
  keep it awake, point an uptime monitor (for example UptimeRobot, every 5
  minutes) at `/api/ping`. It answers without touching the database, and the
  idle server leaves the database alone, so Neon can still suspend when nobody
  works. One service awake all month uses about 744 of Render's 750 free hours.
* About a tenth of a CPU and 512 MB of memory is enough because the server does
  no heavy work.

With `AUDITS_RUN_ON=server`, PDF reports need Microsoft Word (Windows host) or
LibreOffice (`soffice` on PATH) on the server; otherwise the app offers DOCX and
the HTML report. PDFs are created the first time someone opens or emails them,
one at a time.

### Sizing

Measured on this codebase (50-page audits, real sites):

| | Memory |
|---|---|
| API server idle | ~60 MB |
| Desktop app | ~120 MB at start, ~165 MB after every screen |
| One audit while it runs | ~100 MB (measured when audits ran inside the server) |
| One PDF conversion (Word) | ~110 MB, ~4–7 s |

A computer with 4 GB of memory runs 2 audits at a time comfortably; use 3–4
only with 8 GB or more. In the database, results take ~80 KB per audit and the
two Word reports ~100 KB; each PDF that someone opens adds ~0.5–0.8 MB.

First Manager without bootstrap variables:

```
python -m backend.manage create-manager --email you@agency.com --name "Your Name"
```

## Desktop client

Build on Windows:

```
build_exe.bat
```

produces `dist\SEOAuditPro\SEOAuditPro.exe` (one-folder build that includes the
API server and the audit engine), `dist\SEO-Audit-Pro-Portable-3.0.0.zip` (the
app folder, *READ ME FIRST.txt* and the PDF manuals from `docs\manuals\`) and,
when Inno Setup 6 is installed, `dist\SEOAuditPro-Setup-3.0.0.exe`.

When `TEAM_SERVER_URL` in `seo_audit_pro/__init__.py` holds the team server's
address before building, the app opens straight on sign-in against that server
(`SEO_TEAM_SERVER` sets it when running from source, for testing). Otherwise, on first launch users choose **On this
computer** (the app runs its built-in server with its own SQLite database; no
setup needed) or **Connect to a team server** (enter the server address). A local workspace can be shared on the
office network from Settings › This computer; others connect to the address
shown there. Running from source, the app connects to `http://127.0.0.1:5055`
by default (`SEO_API_URL` overrides it).

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
