# Security

## Trust boundaries

* The desktop client is untrusted. It contains only the API URL. It never
  holds `DATABASE_URL`, `SECRET_KEY`, `DATA_ENCRYPTION_KEY` or SMTP passwords,
  and the PyInstaller spec explicitly excludes all server and engine modules.
* Identity (user id, role, team, active flag) is loaded from the database on
  every request from the session referenced by the access token. Role or
  team values sent by the client are ignored.

## Authentication

| Item | Implementation |
|---|---|
| Password hashing | scrypt (N=2^15, r=8, p=1, 16-byte salt). Legacy PBKDF2 hashes verify and are upgraded on next sign-in. |
| Password policy | ≥10 characters, upper + lower case, a digit, not a common password, not containing the email name. |
| Access token | HMAC-SHA256 signed, 30 minutes, carries only user id + session id. Revoked sessions are rejected immediately. |
| Refresh token | 48 random bytes, stored only as SHA-256, rotated on every use, 14-day expiry. |
| Client storage | Refresh token in Windows Credential Manager (`keyring`) only when "Keep me signed in" is ticked. Access token in memory only. |
| Brute force | 5 failed attempts per email in 15 minutes locks sign-in; per-IP limit on login, refresh and reset endpoints. Unknown emails take the same time as wrong passwords. |
| Temporary passwords | Generated server-side for new users and manager resets; shown once; `must_change_password` blocks every endpoint except profile/password/logout until changed. |
| Forgot password | 8-digit code emailed via the agency SMTP; stored hashed; 30-minute expiry; 5 attempts; identical response whether or not the email exists. |
| Sign-out / password change / deactivation | Revokes sessions; open WebSockets are closed or dropped at the next ping. |

## Authorization (RBAC + IDOR)

All rules are enforced in the API. Hidden buttons are a convenience only.

| Action | Manager | Team Lead | Agent |
|---|---|---|---|
| Create / edit / deactivate users, reset passwords | Yes | No | No |
| Create / rename / delete teams | Yes | No | No |
| View all leads | Yes | No | No |
| View team leads | Yes | Own team | No |
| View own leads | Yes | Yes | Yes (assigned only) |
| Assign / reassign leads | Any | Within own team | No |
| Import lead files, see import history and rejected rows | All files | Files they uploaded or that target their team | No |
| Bulk assign / archive "all matching" | Whole bank | Own team | No |
| Export leads (CSV) | Whole bank | Own team | No |
| Start audits, view results/reports | Any lead | Team leads | Assigned leads |
| Send email | Yes | Yes (team leads) | Yes (own leads) |
| SMTP settings, app settings | Yes | No | No |
| Audit logs | Yes | No | No |
| Chat | Everyone | Managers + own team | Managers + own team (incl. Team Lead) |

Resource access for audits, reports, issue states and emails is derived from
the lead (`backend/auth.py: lead_scope / can_access_lead`). Resources the
caller may not see return **404**, identical to a missing resource, so IDs
cannot be probed. Permission denials on role-restricted endpoints are written
to the audit log (`PERMISSION_DENIED`).

## Data protection

* SQL: every query is parameterised; dynamic column names come only from
  server-side allow-lists. `LIKE` input is escaped.
* SMTP password: Fernet (AES-128-CBC + HMAC) with a key derived via HKDF from
  `DATA_ENCRYPTION_KEY`; write-only through the API; never logged.
* Audit trail: `audit_logs` is append-only, enforced by database triggers on
  both SQLite and PostgreSQL. Sensitive keys are redacted from metadata.
* Uploads: extension allow-list per purpose, magic-byte sniffing (client MIME
  ignored), size limits (10 MB, 2 MB avatars), random stored names, served
  only by id after an access check. Original names are sanitised for display.
* Path traversal: report and upload paths are resolved and confined to their
  directories.
* Error disclosure: unexpected errors return a generic message; details go to
  `server_data/logs/server.log`.
* HTTP headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cache-Control: no-store` for JSON, and a strict CSP on the HTML report.
* CORS: removed (the desktop client does not need it).
* CSRF: not applicable — the API authenticates with bearer tokens, not cookies.

## Audit engine safety (preserved)

* The security audit is passive: GET requests only, no payloads.
* Exposed sensitive files (`.env`, `.git/config`, backups) are reported only as
  "Publicly accessible"; contents are never read into results, displayed or
  stored. The API strips everything except the path and name.
* SSRF guard: audits are refused for hosts resolving to private, loopback,
  link-local, multicast or reserved addresses, and for non-web ports.
  Known limitation: the existing crawler follows HTTP redirects, so a public
  site that redirects to an internal address is not blocked mid-crawl.

## Operational safety

* Idempotency keys on create/send/assign/import/start-audit operations, plus
  database constraints (unique domain, one running audit per lead, one active
  Team Lead per team) and optimistic locking (`leads.version`) for concurrent
  edits and assignments.
* Bulk email: explicit recipient-count confirmation, ≤200 recipients per batch,
  one email every 4 s, 500/day server cap, 150/day per agent, leads emailed in
  the last 14 days are skipped. Emails interrupted by a restart are marked
  failed instead of being re-sent.

## Secrets checklist for production

`SEO_ENV=production` makes the server refuse to start without `SECRET_KEY`
and `DATA_ENCRYPTION_KEY`. In development, random values are generated into
`server_data/` (git-ignored). Rotate any secret that was ever committed or
shared; the repository's `.env` is git-ignored and must never be shipped with
the desktop client.
