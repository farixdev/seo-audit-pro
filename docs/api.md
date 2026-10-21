# API reference

Base URL: the server address (e.g. `https://seo.youragency.com`). All bodies
are JSON unless noted. Authenticated endpoints need
`Authorization: Bearer <access_token>`. State-changing endpoints marked **(I)**
accept an `Idempotency-Key` header: a repeated key replays the original
response instead of repeating the operation.

Errors: `{"error": "<human message>", "code": "<machine code>", ...}` with the
HTTP status. Common codes: `unauthorized` (401), `password_change_required`
(403), `forbidden` (403), `not_found` (404), `conflict`/`possible_duplicate`/
`in_progress` (409), `invalid` (422), `rate_limited`/`locked` (429).

List endpoints accept `page` and `page_size` and return
`{items, total, page, page_size, pages}`.

## Auth — `/api/auth`
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/login` | `{email, password}` → `{access_token, refresh_token, expires_in, user}` |
| POST | `/api/auth/refresh` | `{refresh_token}` → new token pair (rotation) |
| POST | `/api/auth/logout` | revokes the session |
| GET / PATCH | `/api/auth/me` | profile; PATCH `{name}` |
| POST | `/api/auth/me/avatar` | multipart `file` (PNG/JPG ≤ 2 MB) |
| POST | `/api/auth/change-password` | `{current_password, new_password}`; signs out other devices |
| POST | `/api/auth/forgot-password` | `{email}`; always 200 |
| POST | `/api/auth/reset-password` | `{email, code, new_password}` |
| GET | `/api/health` | public; `{status, version, app, audits_run_on}` (checks the database) |
| GET | `/api/ping` | public; `{ok, app}` without touching the database — for uptime monitors |

## Users & teams
| Method | Path | Role |
|---|---|---|
| GET | `/api/users` (`search, role, team_id, status`) | Manager; Team Lead (own team) |
| GET | `/api/users/<id>` | Manager; Team Lead (own team) |
| POST **(I)** | `/api/users` `{name, email, role, team_id}` → `{user, temporary_password}` | Manager |
| PATCH | `/api/users/<id>` | Manager |
| POST | `/api/users/<id>/deactivate`, `/reactivate`, `/reset-password` | Manager |
| GET | `/api/users/<id>/avatar` | any signed-in user |
| GET | `/api/teams` | Manager: all; others: own |
| GET | `/api/teams/<id>` (with members) | Manager; Team Lead (own) |
| POST **(I)** / PATCH / DELETE | `/api/teams`, `/api/teams/<id>` | Manager (delete only when empty) |

## Lead bank
Every lead query is limited to the caller's scope (Manager: everything; Team
Lead: their team; Agent: leads assigned to them). A lead outside the scope
answers 404.

**Filter parameters** (shared by list, summary, export, bulk assign and bulk
archive): `search` (company, domain, contact), `status` (one status, `all`
includes archived; default hides archived), `team_id` (Manager; `none` = no
team), `agent_id`, `assigned` (`assigned`/`unassigned`), `audited` (`yes`/`no`),
`outreach` (`yes`/`no` — contacted or not), `niche`, `area` (lower-case keys;
`__none__` = missing), `batch_id` (one imported file), `has_email`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/leads` | filters + `sort` (`company, website, niche, area, status, team, agent, score, rating, reviews, created, last_audit, last_contact`), `order`, `page_size` ≤ 200 |
| GET | `/api/leads/summary` | filters → `{total, unassigned, audited, outreached}` |
| GET | `/api/leads/facets` | niche → area tree with lead and unassigned counts (cached; `team_id` for Managers) |
| GET | `/api/leads/<id>` | includes niche, area, address, category, Google rating/reviews, Maps URL, source file |
| POST **(I)** | `/api/leads` | duplicate domain → 409; same email/phone/company → 409 `possible_duplicate` unless `allow_similar: true` |
| PATCH | `/api/leads/<id>` | requires current `version` (optimistic lock) |
| POST **(I)** | `/api/leads/<id>/assign` | `{team_id, agent_id, reason, version}` — Manager / Team Lead |
| POST **(I)** | `/api/leads/bulk-assign` | `{lead_ids}` **or** `{filter, expected_count}` (select-all across pages, ≤ 200,000); `{team_id, agent_id, reason, mode}` where `mode: "split"` spreads the leads evenly over the team's active Agents. A changed match count → 409 `count_changed` |
| POST | `/api/leads/archive`, `/api/leads/restore` | `{lead_ids}` or `{filter, expected_count}` |
| GET | `/api/leads/export` | filters → streamed CSV (Manager / Team Lead; 10 per 10 min) |
| GET | `/api/leads/<id>/activity` | assignment history + audit trail for the lead |
| GET | `/api/assignees` | teams and people the caller may assign to |

### Importing scraped files (Manager / Team Lead)
Each file is one niche in one area (e.g. a Google Maps scrape of "Dentists in
Chicago IL"). CSV, TSV, TXT and XLSX up to 200 MB.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/lead-imports/local` | the desktop app's way: the app read the file on its computer and sends `{file_name, file_size, headers, total_rows, sample}` (≤ 200 rows) → same answer as below; the batch has no stored file |
| POST | `/api/lead-imports` | multipart `file` (+ optional `niche`, `area`) → `{batch, headers, mapping, sample, sample_with_website, sample_size}`; stores a *pending* batch with detected columns and a niche/area guessed from the file name. Nothing is imported yet |
| POST **(I)** | `/api/lead-batches/<id>/start` | `{niche, area, mapping?, team_id?, agent_id?}` → queues the import; a website column is required. A stored file is processed on the server; a file read by the app waits for its rows |
| POST | `/api/lead-batches/<id>/rows?first=N` | creator's app only: gzip JSON list of row dicts (≤ 2,000) starting at file row `N`; checked and stored like a server import. A chunk already stored is accepted again; a gap → 409 `out_of_order` with `processed_rows` |
| POST | `/api/lead-batches/<id>/finish` | all rows sent: completes the import (audit log, notifications) |
| POST | `/api/lead-batches/<id>/failed` | `{error}`; rows already stored are kept |
| DELETE | `/api/lead-batches/<id>` | discard a pending preview |
| GET | `/api/lead-batches` | `search, status` → batches with counters + `totals` |
| GET | `/api/lead-batches/<id>` | status, `processed_rows`, `imported`, `duplicates`, `no_website`, `invalid` |
| GET | `/api/lead-batches/<id>/rejects` | `reason` (`no_website`, `invalid_website`, `duplicate_in_file`, `duplicate_existing`) — every removed row with its reason |
| GET | `/api/lead-batches/<id>/rejects.csv` | the same as a CSV download |

Imports run in the background in 1,000-row chunks and resume after a server
restart. Rows without a usable website are removed; a row whose domain or phone
number is already in the bank (or earlier in the same file) is removed as a
duplicate. Progress is pushed over the WebSocket as `import_progress`.

## Audits & reports
| Method | Path | Notes |
|---|---|---|
| GET | `/api/audits` | `status (queued/running/completed/failed/cancelled/active), lead_id, batch_id, search`; queued rows include `queue_position` (audits ahead); `active` lists running first, then the waiting line |
| POST **(I)** | `/api/audits` `{lead_id}` | 202 (queued ahead of bulk audits); 409 with `audit_id` if one is already waiting or running |
| GET | `/api/audits/<id>` | status, percent, step, pages processed/discovered, live log; while queued also `queue_position` and `eta_seconds` |
| POST | `/api/audits/<id>/cancel` | requester, Team Lead or Manager |
| GET | `/api/audits/<id>/result` | scored result (409 until completed) |
| PUT | `/api/audits/<id>/issues/<check_id>` | `{status: open|resolved}` |
| GET | `/api/audits/<id>/client-data` | client report view model |
| GET | `/api/audits/<id>/client-html` | standalone HTML client report (download) |
| GET | `/api/audits/<id>/export` | full result JSON (download) |
| POST | `/api/audits/<id>/reports` | regenerate: app mode drops the stored files (`made_in_app: true`; apps build them again when opened); server mode writes the DOCX files again |
| GET | `/api/audits/<id>/result-data` | the stored result as kept (gzip JSON), for an app to build report files from |
| GET | `/api/reports` | `audit_id, lead_id, kind, format, search`; includes `pdf_supported` (the server's) and `made_in_app`; `ready: 0` marks a file that has not been made yet |
| GET | `/api/reports/<id>/download` | the file. App mode, file not made yet: 409 `build_in_app` with `{report_id, audit_id, kind, format, download_name}` (the app builds it here, then uploads it). Server mode: a PDF that is not `ready` is created first (503 `busy` if others take longer than 4 minutes) |
| PUT | `/api/reports/<id>/file` | raw DOCX/PDF bytes an app built; kept in `report_data` for the team |
| POST | `/api/reports/<id>/opened` | audit trail for a file an app built for itself |

### Audits run by the desktop apps (`AUDITS_RUN_ON=app`, default)
Every call sends `X-Device-Id` (a random id per installation).

| Method | Path | Notes |
|---|---|---|
| POST | `/api/audit-jobs/claim` | `{restart?}` → `{jobs: [{audit_id, url, max_pages, psi_strategy}], slots}`: the caller's own queued audits, up to `slots` running on this computer. `restart`: audits this computer was running go back to the queue first (twice interrupted → failed). Audits whose app went silent for 10 minutes are given back too. 409 `server_runs_audits` in server mode |
| POST | `/api/audit-jobs/<id>/progress` | `{messages: [engine progress lines]}` → `{stop}` (true when cancelled or taken over) |
| POST | `/api/audit-jobs/<id>/result` | gzip JSON result as the body, or a form with `result` (gzip JSON) and optional `client_docx`, `technical_docx` → `{reports}`; 409 when the audit is no longer running on this computer |
| POST | `/api/audit-jobs/<id>/failed` | `{error}` |
| POST | `/api/pagespeed` | `{url, strategy}` → Google PageSpeed result fetched with the server's key |

### Bulk audits and the queue
Audits run `workers` at a time (Settings → audit defaults → `concurrent`, 1–4,
default 2): on each computer in app mode, on the server in server mode. Single
audits go ahead of bulk audits; in server mode bulk audits take turns between
the people who queued them.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/audits/bulk/preview` | `{lead_ids}` or `{filter}` (lead-bank filter), `include_recent?` → `{selected, eligible, will_queue, cap, not_now, skipped: {archived, in_progress, recently_audited}, recent_days, queue, eta_seconds, sample}` |
| POST **(I)** | `/api/audits/bulk` | same body + `confirm_count` (= the preview's `will_queue`) + `label` → 202 `{batch}`; 409 `count_changed` with a fresh `preview` if the numbers moved. At most 100 leads per batch (Manager 500) and 300 waiting audits per person (Manager 2,000); leads audited in the last 30 days are skipped unless `include_recent` |
| GET | `/api/audit-batches` | `status (active/completed/cancelled)` → batches with `counts`, `done`, `eta_seconds`, plus `queue` |
| GET | `/api/audit-batches/<id>` | one batch (creator, their Team Lead, Managers) |
| POST | `/api/audit-batches/<id>/cancel` | removes its waiting audits and stops running ones; finished results are kept |
| GET | `/api/audit-queue` | `{workers, running, queued, avg_seconds, eta_seconds, mine_queued, batch_cap, queue_cap}` |

A finished batch sends one `audit_batch_completed` notification to its creator
instead of one notification per lead.

Legacy paths kept as authenticated aliases: `POST /api/audit`,
`GET /api/audit/<id>/status`, `/result`, `/client-data`, `/client-docx`,
`/client-pdf`, `GET /client-report/<id>`, `GET /api/history`.

## Outreach
| Method | Path | Notes |
|---|---|---|
| GET / PUT | `/api/outreach/smtp` | Manager; password is write-only |
| POST | `/api/outreach/smtp/test` | Manager; tests stored or submitted settings |
| GET | `/api/outreach/status` | SMTP configured flag and sending limits |
| POST | `/api/outreach/generate` `{lead_id, audit_id?}` | draft built only from the stored audit result |
| GET | `/api/outreach/emails` | `status, lead_id, batch_id, search`; includes `counts` |
| GET / PATCH / DELETE | `/api/outreach/emails/<id>` | edit/delete drafts only |
| POST **(I)** | `/api/outreach/emails` | `{lead_id, to_email, subject, body, audit_id?, report_ids?, upload_ids?, send?}` |
| POST **(I)** | `/api/outreach/emails/<id>/send` | queue a draft or retry a failed email; app mode: 409 `report_not_uploaded` with `reports` when an attached report file is not on the server yet (the app uploads it and sends again) |
| POST | `/api/outreach/uploads` | multipart attachment (also kept in `upload_data`) |
| POST | `/api/outreach/bulk/preview` | `{lead_ids}` → eligible count, skipped reasons, sample |
| POST **(I)** | `/api/outreach/bulk` | `{lead_ids, confirm_count}` |

Email statuses: `draft`, `queued`, `sending`, `sent`, `failed`. Opens, clicks
and replies are not tracked and are never displayed.

## Chat
`GET /api/chat/contacts`, `GET /api/chat/conversations`,
`POST /api/chat/conversations/direct {user_id}`,
`GET /api/chat/conversations/<id>/messages?before=&limit=`,
`POST (I) /api/chat/conversations/<id>/messages {body, reply_to_id?, shares?: [{kind: lead|report, id}]}`
(at most 5 shares, only what the sender can open; 409 `report_not_uploaded` with `reports` when a
shared report file is not on the server yet; `upload_ids` → 422),
`POST /api/chat/conversations/<id>/read`, `PATCH|DELETE /api/chat/messages/<id>`,
`POST /api/chat/messages/<id>/reactions {reaction: ack|like|seen|question}`,
`GET /api/chat/messages/<id>/reports/<report_id>` (a report shared in that message: anyone in the
conversation may open it), `GET /api/chat/search?q=`. Files cannot be uploaded to chat
(`POST /api/chat/uploads` → 410 `chat_files_off`); `GET /api/chat/attachments/<upload_id>` still
serves attachments of older messages.

## Notifications, dashboard, search
`GET /api/notifications?unread=1`, `POST /api/notifications/read {ids | all}`,
`GET /api/dashboard`, `GET /api/search?q=`.

## Administration (Manager)
`GET|PUT /api/settings` (`audit_defaults: {max_pages, psi_strategy, concurrent}`),
`GET /api/audit-logs` (`action, user_id, entity_type, entity_id, since, until, search`).

## WebSocket — `/api/ws`
First frame: `{"type": "auth", "token": "<access token>"}` → `{"type": "ready"}`.
Client may send `{"type": "ping"}` (every 25 s) and
`{"type": "typing", "conversation_id": "..."}`. Server events: `notification`,
`message`, `message_updated`, `typing`, `presence`, `audit_progress`,
`audit_batch` (`{batch_id, status, counts, total}`), `import_progress`.
