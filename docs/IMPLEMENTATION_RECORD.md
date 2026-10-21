# Implementation record — v3.0 rebuild

This records what was found in the repository, what was changed and why, and
what remains. It supersedes the claims in the older `SYSTEM_CHANGELOG.md`,
`DESKTOP_SYSTEM_GUIDE.md`, `DATABASE_SCHEMA.md` and `OPERATIONAL_WORKFLOW.md`
where they conflict.

## 1. What was discovered

| Area | Finding |
|---|---|
| Audit engine (`audit.py`, `crawler.py`, `security_audit.py`, `seo_checks.py`, `scoring.py`, …) | Solid and valuable. Passive security checks stop at "publicly accessible" for sensitive files. Kept. |
| Engine bug | Broken-link checking and structured-data validation were gated on category keys `"crawl"` / `"structured"` that never exist in `CATEGORIES`, so both stages were silently skipped in every audit. |
| Engine bug | `history_manager` cached every full audit result (MBs each) in memory forever. |
| Docs vs code | `scoring.py` docstring says warn = 0.5; the code (authoritative, matching the brief) uses 0.3. Code kept. |
| Desktop client (`desktop/`) | Auto-signed-in as the Manager with hard-coded credentials on every launch and, if the server was down, fabricated a local "manager" session. |
| Web server (`web_app.py`) | Every audit, report, history, settings and file endpoint was unauthenticated. `POST /api/settings` let anyone overwrite API keys in `.env`. CORS `*`. `check-google-index` crashed (`urllib` not imported). Report downloads matched files by the last 8 characters of an id and could serve another audit's file. |
| Agency API (`agency_routes.py`) | Hard-coded fallback `SECRET_KEY`; 7-day tokens carrying role/team; SMTP passwords "encrypted" with XOR and a hard-coded key; outreach generator invented issues and a default score of 58 when no audit existed; Team Leads could re-assign leads to agents of other teams; no endpoints for notifications, audit logs, email sending, password reset — although the client called them. |
| Database | No foreign keys, no unique constraint on domains (only an index), seeded fake leads with fabricated scores, demo passwords published in `docs/DESKTOP_SYSTEM_GUIDE.md`. |
| Product decision conflict | `SYSTEM_CHANGELOG.md` records that the CV bank was removed on request; the first brief asked for a Manager-only CV bank, so it was built — and then removed again when the owner confirmed they need a lead bank, not a CV bank (see "Iteration 2"). |

## 2. Decisions

* **Reuse** the audit engine, `report_docx`, the client HTML report template
  and `history_manager` for result storage.
* **Rebuild** the agency layer as `backend/` (sessions, RBAC, migrations,
  realtime, queue) and the desktop client as `seo_audit_pro/`.
* **Desktop never embeds the server**: the old client started `web_app.py`
  in-process, which put database credentials on every desktop. The new
  executable excludes all server modules.
* **One running audit per lead** and **one active Team Lead per team** are
  database constraints, not UI rules.
* **Users are deactivated, never deleted** (keeps the audit trail and foreign
  keys intact).
* **PDF honesty**: PDF is produced only where Word or LibreOffice exists on the
  server; otherwise the app says so.
* **Email tracking**: opens/clicks/replies are not tracked, so they are not
  shown.

## 3. Security issues found and fixed

1. Hard-coded Manager auto-login and fake offline session in the client — removed; real sign-in with lockout.
2. Unauthenticated audit/report/history/settings endpoints — replaced by authenticated, lead-scoped endpoints; legacy paths kept as authenticated aliases.
3. Unauthenticated `.env` overwrite (`POST /api/settings`) — removed; settings are Manager-only and contain no secrets.
4. Hard-coded secret keys — removed; production refuses to start without secrets.
5. XOR "encryption" of SMTP passwords — Fernet with an HKDF-derived key; password write-only.
6. Long-lived tokens trusting embedded role/team — 30-minute access tokens bound to revocable sessions; role/team from the database.
7. Cross-team lead assignment by Team Leads — enforced team boundaries.
8. Report file confusion via suffix matching — reports tracked by id in the database with path confinement.
9. IDOR across leads/audits/reports/emails/chat — lead-scope checks with 404 responses.
10. Fabricated outreach content — drafts use only stored audit findings; no audit → explicit error.
11. SSRF via audits of internal addresses — resolved-IP check (see limitation below).
12. Email header injection via subjects — rejected.
13. Upload type spoofing and path traversal — content sniffing, random names, confinement.
14. Published demo credentials — legacy accounts are not used; the import command issues new temporary passwords.

## 4. Bugs found and fixed (selection)

* Engine stages skipped (see above) — gate fixed in `audit.py`.
* Unbounded result cache — bounded to 12 results.
* Audit progress could be lost when throttled — trailing flush.
* Same-second events had unstable ordering — millisecond timestamps.
* Interrupted audits stayed "running" forever after a restart — marked failed on start.
* Interrupted emails could be re-sent after a restart — marked failed for review.
* Deactivated agents kept leads nobody could work — leads return to the team pool with history.
* Old desktop: navigation to "Notifications" opened the chat view; tables of mock data; buttons calling missing endpoints.
* Old importer misaligned Excel columns when a header cell was empty — fixed in the new importer.

## 5. Verification performed

* Automated: 43 tests (`tests/test_api.py` 35, `tests/test_client_core.py` 8) — all passing.
* Real engine runs through the API and the client against python.org and
  example.com: live progress (pages processed/discovered), scored results,
  four report files (client/technical × DOCX/PDF), cancellation.
* Every screen rendered for Manager, Team Lead and Agent at 1440×900 and
  1366×768 against the running server, including chat live updates,
  outreach drafts/bulk preview, user creation with one-time temporary
  password, CV upload, audit-log drawer and SMTP test failure.
* Failure states: server unreachable after sign-in (Offline indicator,
  "Server unavailable" with Retry), expired access token (transparent
  refresh), revoked refresh token (back to sign-in), idempotent replays.
* Packaged build: `dist\SEOAuditPro\SEOAuditPro.exe` (95 MB one-folder build)
  launches without Python to the sign-in screen; the bundle contains no
  server, engine or database-driver modules.

UI defects found during verification and fixed: engine text rendered as markup
(labels now plain text by default), table footer borders cascading into child
widgets, missing combo/spinbox arrows, leads table overflowing at 1366/1440 px
(optional columns now hide responsively), sidebar item staying highlighted
after a failed navigation, chart days keyed in local time vs UTC server
grouping, redundant offline toasts.

### Incident: schema created in the production database (2026-10-21)

During post-cleanup verification, an import check of `web_app` was run without
the local-database flag and the server connected to the Neon database from
`.env`. Migration 1 ran there: the 25 new tables, `schema_migrations` and the
audit-log trigger function were created. No rows were written (all new tables
empty) and existing tables (`agency_*`, `seo_audits`, `seo_targets`) were not
modified. At the owner's request the 26 tables and the trigger function were
then dropped in a single transaction (no CASCADE, after verifying every table
was empty); the database is back to its original 11 tables, and migration 1
will run again on the first real production start. Prevention: outside
`SEO_ENV=production`, `DATABASE_URL` is now ignored unless `SEO_DB=postgres`
is set explicitly.

### Iteration 2: Lead Bank (2026-10-21)

Owner request: leads come from Google Maps scrapes, one niche in one area per
CSV, 100+ files and potentially millions of rows; remove rows without a website
and anything already uploaded (and say so); let the Manager see and assign
everything (person or team), see who owns each lead and whether it was audited
or contacted, with proper pagination, niche + area filtering, select-all across
pages, caching, skeleton loading and a working Back. Remove the CV bank.

What changed:

* **Schema (migration 2)**: `industry`/`location` → `niche`/`area` with
  lower-case keys, GMB columns (address, category, rating, reviews, Maps URL,
  extra), `lead_batches` + `lead_batch_rejects`, indexes for niche/area, batch,
  team/status and last contact, optional trigram search indexes on PostgreSQL;
  `candidates` dropped.
* **Import pipeline** (`backend/lead_import.py`): upload → preview (columns
  detected, niche/area guessed from the file name) → start → background
  processing in 1,000-row chunks with bulk inserts, resumable after a restart,
  live `import_progress` events, notifications and an audit-log entry. Every
  removed row is stored with its reason. 20,000 rows ≈ 3 s on SQLite.
* **Lead API**: shared filter builder for list/summary/export/bulk; niche → area
  facets (cached); select-all-matching bulk assign/archive with
  `expected_count` confirmation; split assignment across a team's Agents;
  streamed CSV export; server-side cache invalidated by a version bump on every
  write.
* **Desktop**: Lead Bank page (navigator, quick views, summary strip, bulk bar,
  Imports tab, rejected-rows drawer), multi-file import dialog with drag and
  drop, Back navigation (button, Alt+Left, mouse back button) that restores
  filters, stale-while-revalidate response cache, skeleton loaders, responsive
  table columns with jump-to-page. Notification popover now opens below the
  bell and stays on screen when maximised.
* **Removed**: CV bank page, API, upload purpose and tests; the old one-shot
  `/api/leads/import` and client-side importer.

Verified: 47 automated tests passing (`test_api` 34, `test_lead_bank` 7,
`test_client_core` 6); three synthetic GMB files (CSV, CSV, XLSX — 3,000 rows,
`.example` domains) imported through the API with the expected removals
(1,500 → 1,369 added, 45 duplicates, 86 without website, etc.); split
assignment of 824 leads across three Agents (274/275/275); Back restores the
niche/area filter; screens checked for Manager, Team Lead and Agent at
1440×900 and 1366×768.

Defects found and fixed during this iteration: decimal-comma ratings parsed as
×10; two-letter state codes title-cased ("Tx"); navigator highlight not
following Back; lead table hiding optional columns while the page was hidden;
header subtitle never appearing when set after load; import status text
clipped; worker threads raising "Signal source has been deleted" when the app
closed mid-request.

### Iteration 3: portal QA and bulk auditing (2026-10-21)

**Team Lead and Agent portals.** A live permission sweep against the running
server (76 checks: scoped lists, 404 for anything outside scope, Manager-only
endpoints, imports into another team, DMs across teams, search, duplicate
warnings without leaking ids) passed, and every screen of both portals was
opened in the client with error states, error toasts, crashes and Qt warnings
recorded — none. One fix: *Generate from audit* is disabled until the lead has
an audit.

**Owner decisions.** Bulk audits of selected or all matching leads, 100 per run
(Manager 500), 2 at a time, a saved queue with fair turns, compressed results,
PDFs on demand — sized for a 4 GB machine.

What changed:

* **Migration 3**: `audit_batches`, `audit_results`; `audits.batch_id`,
  `priority`, `params`, `attempts`; `reports.ready`.
* **Queue** (`backend/audit_runner.py`): queued rows are the queue; one
  scheduler thread starts audits while fewer than the configured number run
  (Settings, 1–4, default 2); single audits first, then bulk audits by fair
  turns (fewest running, then served longest ago). Restart re-queues running
  audits once; queued audits stay queued. Bulk audits notify once per batch.
* **Bulk API**: preview/start with skip rules (archived, waiting or running,
  audited in the last 30 days unless re-audit is chosen), caps per run and per
  person, confirmation count (409 with fresh numbers), batches list/detail/
  cancel, queue status, queue position and time estimates.
* **Storage**: results gzip-compressed in `audit_results` (measured
  0.5–0.9 MB → 53–73 KB); legacy results read once and copied. PDFs rendered
  on first download or email, one conversion at a time (measured 7.3 s first
  open, 0.1 s after; Word ~110 MB).
* **Desktop**: Audit action in the Lead Bank bulk bar (agents can now select
  their own leads), review dialog, Audits › Queue tab (capacity, waiting, average,
  time to empty, batches with progress/time left/cancel, running and waiting
  audits with place in line), concurrency setting, "Made when first opened"
  for pending PDFs.

Defects found while measuring, and fixed:

* **Engine fetch** (`crawler.fetch_page`): every URL was downloaded in full
  and decoded with encoding detection, and very large HTML pages were parsed
  entirely. On iana.org (multi-MB registry pages) one 50-page audit took
  303 s and 501 MB and starved the other audit thread; 6 audits took 535 s
  with a 523 MB server peak. Now responses are streamed, binary/data files are
  not downloaded, non-HTML types are sniffed from 4 KB, HTML is capped at
  3 MB and decoded with the declared charset (detection only on a 64 KB
  sample). Same iana.org audit: 57 s, 174 MB; the 6-audit batch: 125 s,
  241 MB server peak.
* **Realtime**: publishing wrote to each socket synchronously, so a client that
  stopped reading (a sleeping laptop) could block an audit worker. Each
  connection now has an outbox and its own sender thread; a stalled client is
  disconnected.
* **Lead import**: the "completed" status could become visible a moment before
  the lead-count cache was cleared; the cache is now cleared first.
* **Flaky test**: the facets test looked a niche up by its label, but niche
  labels are title-cased from the file name, so a random suffix starting with a
  letter ("ab12" → "Ab12") failed about a third of runs; it now matches the key.

Verified: 64 automated tests passing (`test_api` 34, `test_lead_bank` 7,
`test_audit_queue` 11, `test_engine_fetch` 6, `test_client_core` 6); real
bulk audits through the client as an Agent (preview → re-audit option →
queue → completion notification → scores in the lead list) and as a Manager
(select all 824 matching → 500 queued, 324 left for a later run).

### Iteration 4: portable all-in-one app, first-run setup, manuals (2026-10-21)

**Owner request.** A final exe with the full icon, portable to give to friends:
on first run it initialises the database and everything else, sets up roles,
stores its data locally, and works without DLL errors. Plus a simple user
manual for every role (with error handling and limits) and a technical
reference for developers.

What changed:

* **One exe, two roles.** `SEOAuditPro.pyw` opens the app, or with `--serve`
  runs the API server and engine. The spec now bundles the server, the engine,
  `templates/`, `static/libs/` and the data files of trafilatura, justext and
  tld; `psycopg2`, unused Qt modules and `.env`/databases are kept out.
* **On this computer mode** (`seo_audit_pro/local_server.py`, `core/local.py`):
  the app starts its own windowless server on 127.0.0.1 with data in
  `%LOCALAPPDATA%\SEO Audit Pro\data`; the server exits when the app closes
  or is killed (parent-process watch) and the app restarts it if it stops.
  Single app instance per user. Optional sharing on the local network.
* **First run**: Welcome screen (this computer / team server) and a *Create the
  Manager account* screen backed by `GET /api/setup/status` and
  `POST /api/setup/manager` (only while no account exists, only from
  loopback, password policy enforced).
* **PageSpeed key** in Settings (stored encrypted; falls back to
  `PAGESPEED_API_KEY`) — before this, audits sent no key at all.
* Templates and static files are read from the bundle (`config.RESOURCE_DIR`).
* `build_exe.bat` installs server + desktop requirements and runs
  `scripts/package_portable.py`, which makes the portable zip with
  *READ ME FIRST.txt* and the PDF manuals, and refuses to package secrets or
  databases.
* Manuals written as shared docs and exported to `docs/manuals/*.pdf`.

Verified: 69 automated tests (`test_local_mode` adds 5: first-run setup on a
fresh database, setup closed once accounts exist, encrypted PageSpeed key,
built-in server start and exit-with-parent, port selection). The packaged
server passed 21 end-to-end checks against a fresh data folder (start 2.8 s;
real single and bulk audits; Word reports; both PDFs rendered on first open;
HTML report; CSV and Excel imports; export; chat; no errors, missing modules
or DLL problems in the logs). The packaged app showed the Welcome screen on a
fresh profile, started its own server in local mode and showed the Manager
setup; closing the app normally or killing it stopped the server; a second
launch exited and focused the first window.

## 6. Known limitations

* SSRF: the existing crawler follows redirects; a public site redirecting to an
  internal address is not blocked mid-crawl. Mitigated by the initial check.
* Single API process by design (in-memory realtime hub, audit pool, email queue).
* PDF requires Word or LibreOffice on the server.
* The Windows installer requires Inno Setup 6 at build time (script provided).
* Engine authority and traffic figures are model estimates; the UI labels them
  "Estimated".
* Removed as superseded (with the owner's approval): the previous `desktop/`
  package, `agency_db.py`, `agency_routes.py`, the legacy browser UI
  (`templates/index.html`, `static/js/app.js`, `static/css/style.css`,
  `static/libs/chart.umd.min.js`, `static/libs/html2canvas.min.js`), the
  standalone `app.py` GUI, the old `*.spec` files, `scratch/` and the old
  screenshots in `reports/`. `package_dist.py` (zips the old build names) is
  no longer used by `build_exe.bat`.
