# Architecture

SEO Audit Pro is a Windows desktop application for SEO agencies backed by a
single Flask API server. The desktop client never talks to the database.

```
 Windows desktop (PySide6)          API server (Flask, one process)            Storage
 ─────────────────────────          ───────────────────────────────            ───────
 seo_audit_pro/                     backend/
   ui/      screens            HTTPS   routes/   REST endpoints  ──────────▶  PostgreSQL (Neon)
   widgets/ component library ─────▶  auth.py   sessions + RBAC                or SQLite (dev/test)
   core/    api client, tasks,  WSS    realtime.py  WebSocket hub
            session, realtime  ◀────   audit_runner.py ──▶ existing engine     server_data/
                                       reports.py      ──▶ report_docx            reports/ uploads/
                                       mailer.py       ──▶ SMTP                   history/ logs/
```

## Components

| Layer | Location | Responsibility |
|---|---|---|
| Desktop client | `seo_audit_pro/` | UI only. Holds the API URL and, if the user opts in, a refresh token in Windows Credential Manager. |
| API | `backend/routes/*.py` | Validation, authorization, business rules. |
| Auth | `backend/auth.py`, `backend/security.py` | Sessions, tokens, password hashing, role decorators, lead-scope rules, idempotency. |
| Realtime | `backend/realtime.py`, `seo_audit_pro/core/realtime.py` | Chat, typing, presence, notifications, audit and bulk-audit progress over WebSocket (`/api/ws`). Every connection has its own small outbox and sender thread, so a client that stops reading never blocks a publisher. |
| Audit engine | `audit.py`, `crawler.py`, `security_audit.py`, `seo_checks.py`, `scoring.py`, `pagespeed.py`, `authority_traffic.py`, `structured_data.py` | Existing engine (bug fixes and fetch limits, see the implementation record). |
| Audit queue / runner | `backend/audit_runner.py` | The saved audit queue (`audits.status = 'queued'`), a scheduler that runs `concurrency()` audits at a time (2 by default), single audits first and fair turns between people for bulk audits; persists real progress and finishes each audit. |
| Results | `backend/results.py` | Full engine results, gzip-compressed in `audit_results` (~80 KB each); older audits are read from the legacy `history_manager` store and copied over on first use. |
| Reports | `backend/reports.py`, `report_docx.py`, `templates/client_report.html` | Client and technical DOCX when an audit completes; PDF on first open (Word or LibreOffice, one conversion at a time); standalone HTML client report. |
| Email | `backend/mailer.py` | Encrypted SMTP settings, rate-limited outbound queue, delivery status. |

## Request flow (example: start an audit)

```
Desktop  ── POST /api/audits {lead_id}  (Bearer token, Idempotency-Key)
  API    ── session → user (role/team from DB) → lead scope check → SSRF check
         ── INSERT audits status 'queued' (unique index: one queued/running audit per lead)
         ── audit log AUDIT_STARTED → 202, wake the scheduler
 Sched.  ── fewer than N running? claim the next queued audit (single audits first,
            then fair turns between people) → worker thread
 Worker  ── SSRF check again → audit.run_audit(...) progress → UPDATE audits + WebSocket "audit_progress"
         ── results.save (gzip) → reports.generate_all (DOCX now, PDF on first open)
         ── UPDATE audits/leads, audit log AUDIT_COMPLETED, notifications (one per bulk batch)
Desktop  ── receives events (or polls every 5 s while the socket is down) → loads /result
```

## Why one server process

The WebSocket registry, the audit scheduler's running set and the email queue
live in memory; the audit queue itself is in the database, so waiting audits
survive a restart and interrupted ones are queued again once.
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
