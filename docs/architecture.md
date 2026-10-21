# Architecture

SEO Audit Pro is a Windows desktop application for SEO agencies backed by a
single Flask API server. The desktop client never talks to the database: it
holds no database credentials, so the server checks every request before
reading or writing.

The heavy work runs on each person's own computer (default
`AUDITS_RUN_ON=app`): audits, Word/PDF report files and reading lead files run
in background processes of the desktop app. The server connects the team: it
signs people in, enforces roles, stores and shares the data, relays live events
and chat, and sends email.

```
 Windows desktop (PySide6)               API server (Flask, one process)        Storage
 ─────────────────────────               ───────────────────────────────        ───────
 seo_audit_pro/                          backend/
   ui/      screens               HTTPS    routes/   REST endpoints  ───────▶  PostgreSQL (Neon)
   widgets/ component library    ──────▶  auth.py   sessions + RBAC            or SQLite (dev/test)
   core/    api client, tasks,     WSS     realtime.py  WebSocket hub
            session, realtime     ◀─────   audit_runner.py  queue, progress    server_data/ (a cache:
   core/pc_work.py  runs here ───▶ jobs    reports.py   report rows + files      reports/ uploads/
   pc_jobs.py  engine, report_docx,        lead_import.py  dedupe + store        logs/)
               lead file reader            mailer.py   ──▶ SMTP
```

## Components

| Layer | Location | Responsibility |
|---|---|---|
| Desktop client | `seo_audit_pro/` | UI, and the heavy work on this computer: `core/pc_work.py` (audit runner, report files, lead-file import runner) starts `pc_jobs.py` background processes (`SEOAuditPro.exe --job <folder>`) that talk to it through files. Holds the API URL and, if the user opts in, a refresh token in Windows Credential Manager. |
| API | `backend/routes/*.py` | Validation, authorization, business rules. |
| Auth | `backend/auth.py`, `backend/security.py` | Sessions, tokens, password hashing, role decorators, lead-scope rules, idempotency. |
| Realtime | `backend/realtime.py`, `seo_audit_pro/core/realtime.py` | Chat, typing, presence, notifications, audit and bulk-audit progress over WebSocket (`/api/ws`). Every connection has its own small outbox and sender thread, so a client that stops reading never blocks a publisher. |
| Audit engine | `audit.py`, `crawler.py`, `security_audit.py`, `seo_checks.py`, `scoring.py`, `pagespeed.py`, `authority_traffic.py`, `structured_data.py` | Existing engine (bug fixes and fetch limits, see the implementation record). |
| Audit queue / runner | `backend/audit_runner.py` | The saved audit queue (`audits.status = 'queued'`). Default (`AUDITS_RUN_ON=app`): each person's app claims its own audits (`claim_jobs`, `concurrency()` per computer), sends engine progress messages (turned into percent / step here) and the result; an app that closes or goes silent for 10 minutes gives its audits back to the queue. `AUDITS_RUN_ON=server`: a scheduler here runs `concurrency()` audits at a time with fair turns between people. |
| Results | `backend/results.py` | Full engine results, gzip-compressed in `audit_results` (~80 KB each); older audits are read from the legacy `history_manager` store and copied over on first use. |
| Reports | `backend/reports.py`, `report_docx.py`, `pdf_convert.py`, `templates/client_report.html` | Report rows per audit; files kept in `report_data`. App mode: the auditing app sends both Word reports with the result, and a PDF is built by the app that opens it first (`build_in_app` answer) and uploaded. Server mode: DOCX at completion, PDF on first open. Standalone HTML client report. |
| Email | `backend/mailer.py` | Encrypted SMTP settings, rate-limited outbound queue, delivery status. |

## Request flow (example: start an audit)

```
Desktop  ── POST /api/audits {lead_id}  (Bearer token, Idempotency-Key)
  API    ── session → user (role/team from DB) → lead scope check → address check
         ── INSERT audits status 'queued' (unique index: one queued/running audit per lead)
         ── audit log AUDIT_STARTED → 202, live event "audit_jobs" to that person's apps
Runner   ── (the requester's app) POST /api/audit-jobs/claim (X-Device-Id) → up to N audits,
            single audits first → one background job per audit (engine, below-normal priority)
Job      ── engine progress lines → POST /api/audit-jobs/<id>/progress every 2 s
            (the server turns them into percent / step and sends "audit_progress" to everyone watching)
         ── PageSpeed → POST /api/pagespeed (the Google key stays on the server)
         ── result + both Word reports → POST /api/audit-jobs/<id>/result (one request)
  API    ── results.save (gzip) → report rows, Word reports into report_data
         ── UPDATE audits/leads, audit log AUDIT_COMPLETED, notifications (one per bulk batch)
Desktop  ── receives events (or polls every 5 s while the socket is down) → loads /result
```

With `AUDITS_RUN_ON=server` a scheduler thread on the server claims queued
audits instead (fair turns between people) and runs the engine there.

## Why one server process

The WebSocket registry, live audit logs and the email queue live in memory;
the audit queue itself is in the database, so waiting audits survive a restart.
Audits running in the apps keep running while the server restarts (the apps
retry until it answers again).
Deploy exactly one process with many threads
(`gunicorn web_app:app --workers 1 --threads 32`). Scaling out would require a
shared broker (e.g. Redis) — not needed for an agency-sized deployment.

## Desktop structure

```
seo_audit_pro/
  app.py            entry point and startup sequence (splash → sign-in/restore → workspace)
  theme.py          design tokens + Qt stylesheet
  brand.py, icons.py
  core/             api.py (HTTP + refresh), session.py, tasks.py (thread pool),
                    realtime.py (WebSocket), settings.py, cache.py (short-lived
                    response cache), fmt.py
  widgets/          common.py, table.py, feedback.py, charts.py, skeleton.py
  ui/               splash.py, auth.py, main_window.py, shell.py
  ui/pages/         one module per screen, loaded lazily through ui/pages/__init__.py
```

UI code never calls `requests` directly: every network call goes through
`core/api.py` inside `core/tasks.run(...)`, so the UI thread never blocks.
