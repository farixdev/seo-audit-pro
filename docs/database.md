# Database

PostgreSQL (Neon) in production, SQLite for local development and tests. The
schema is created and evolved only by versioned migrations in
`backend/migrations.py` (recorded in `schema_migrations`); they run on server
start or with `python -m backend.manage migrate`. Never change the production
schema by hand — add a new migration.

Timestamps are UTC ISO-8601 strings with millisecond precision
(`2026-10-21T09:15:02.123Z`), so lexical order equals chronological order on
both engines.

## Tables

| Table | Purpose | Key constraints |
|---|---|---|
| `teams` | Agency teams | `name_key` unique (case-insensitive name) |
| `users` | Accounts | `email` unique; `role` check; Team Lead/Agent require `team_id`, Manager must not have one; partial unique index: one active Team Lead per team |
| `sessions` | Refresh-token sessions | `refresh_hash` unique; FK user |
| `password_resets` | Emailed reset codes (hashed) | FK user |
| `login_attempts` | Lockout accounting | index (email, created_at) |
| `uploads` | Stored files (chat/email attachments, avatars) | `stored_name` unique; purpose check |
| `leads` | Lead bank: company, website, `niche`/`area` (+ lower-case `niche_key`/`area_key`), address, category, Google `rating`/`review_count`, `maps_url`, `extra` (unmapped columns as JSON), `batch_id` (source file) | `normalized_domain` unique; status check; agent requires team; `version` for optimistic locking; indexes on agent, team, status, email_key, phone_key, company_key, created_at, (niche_key, area_key), area_key, batch_id, (team, status), last_contact_at |
| `lead_batches` | One imported file: status (`pending → queued → processing → completed/failed/cancelled`), column mapping, counters (`total_rows`, `processed_rows`, `imported`, `duplicates`, `no_website`, `invalid`), target team/agent | status check |
| `lead_batch_rejects` | Every row removed from an import, with its row number, raw values and reason (`no_website`, `invalid_website`, `duplicate_in_file`, `duplicate_existing`) | reason check; FK batch |
| `lead_assignments` | Assignment history (who/when/from/to/reason) | FK lead, changed_by |
| `audits` | Audit runs + live progress + summary; `status = 'queued'` rows are the audit queue (`priority` 1 = single audit, 0 = bulk; `params` = crawl settings; `attempts` counts starts; `batch_id`) | status check; partial unique index: one queued/running audit per lead; indexes on lead, status, created_at, requested_by, (status, priority, created_at), (batch_id, status) |
| `audit_batches` | One bulk audit: label, creator, team, `total`, skipped counts (JSON), status `active → completed/cancelled` | status check |
| `audit_results` | Full engine result per audit, gzip-compressed JSON (`raw_size`, `stored_size`) | PK audit_id |
| `audit_issue_states` | Resolved/unresolved per finding | PK (audit_id, check_id) |
| `reports` | Generated report files; `ready = 0` marks a PDF created on first open | unique (audit_id, kind, format) |
| `smtp_settings` | Single row; encrypted password | `id = 'default'` check; port range check |
| `app_settings` | JSON settings (audit defaults) | PK key |
| `email_batches`, `emails`, `email_attachments` | Outreach | status check; attachment is exactly one of report/upload |
| `conversations`, `conversation_reads`, `messages`, `message_attachments`, `message_reactions` | Chat | one team channel per team; one direct conversation per user pair (`user_a < user_b`); reaction check |
| `notifications` | Per-user notifications | index (user_id, is_read, created_at) |
| `audit_logs` | Immutable audit trail | UPDATE/DELETE rejected by trigger; indexes on user, created_at, action, entity |
| `idempotency_keys` | Replay protection | PK (user_id, key) |

Full audit results (the engine output, ~1–2 MB of JSON each) are stored
gzip-compressed in `audit_results` (~80 KB each). Audits from before migration 3
were stored by the legacy `history_manager` in `seo_audits` (PostgreSQL JSONB)
and `server_data/history/`; they are still readable and are copied into
`audit_results` the first time they are opened. New audits no longer write to
`seo_audits`.

## Migrations

| Version | What it does |
|---|---|
| 1 | Initial schema |
| 2 | Lead bank: renames `leads.industry` → `niche` and `location` → `area`, adds the GMB columns and indexes above, creates `lead_batches` and `lead_batch_rejects`, drops the CV bank (`candidates`). On PostgreSQL it also tries to enable `pg_trgm` and add trigram indexes for fast company/domain search; if the extension is not allowed the server logs it and search still works (just slower on very large banks) |
| 3 | Audit queue: `audit_batches`, `audit_results`, `audits.batch_id / priority / params / attempts`, `reports.ready` |

## Integrity rules worth knowing

* Users are never deleted, only deactivated, so foreign keys from leads,
  audits, emails and logs never dangle. Deactivating a user returns their
  leads to the team pool and records it in `lead_assignments`.
* A team can be deleted only when it has no members, leads, audits or chat
  history.
* Concurrent edits/assignments of one lead: the update includes
  `WHERE version = <expected>`; the loser receives 409 and reloads.
* A website (normalized domain) exists at most once in the bank. Imports also
  treat a matching phone number as a duplicate, so the same business scraped
  into two files is kept once.
* Bulk actions on "all matching leads" send the count the user confirmed; if
  the filter now matches a different number, nothing changes (409).

## Legacy tables

The previous prototype used `agency_*` tables with seeded demo accounts whose
passwords were published in old documentation. They are not read by the new
server. `python -m backend.manage import-legacy` copies teams, users and leads
once, issues new temporary passwords to every imported account, and does not
import legacy audit scores (no stored audit backs them).
