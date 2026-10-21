# Testing

## Automated API tests

```
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

The suite (`tests/test_api.py`, `tests/test_lead_bank.py`, `tests/test_audit_queue.py`,
`tests/test_app_runner.py`, `tests/test_app_imports.py`, `tests/test_engine_fetch.py`,
`tests/test_client_core.py`, `tests/test_local_mode.py`) runs against a throw-away SQLite database and
data directory; it never touches a configured PostgreSQL database
(`SEO_DB=sqlite` is forced by `tests/helpers.py`).

The same suite runs on PostgreSQL with `SEO_TEST_POSTGRES=1` against a **test**
database (for example a Neon test branch) whose URL is in
`SEO_TEST_DATABASE_URL` or `server_data/test_database_url.txt`. That database
is wiped at the start of the run, so the run refuses a URL that points at the
database in `.env` (same host, or the same Neon endpoint), and only one run may
use it at a time (`server_data/postgres_tests.lock`). It covers:

| Area | What is verified |
|---|---|
| Authentication | login, generic errors, lockout after 5 failures, inactive accounts, refresh-token rotation and single use, logout revocation, tampered tokens, first-login forced password change and policy, forgot/reset password (no account enumeration, single-use code) |
| RBAC | Manager-only endpoints return 403 for Team Lead/Agent and are logged as `PERMISSION_DENIED`; client-sent role/team/agent fields are ignored |
| IDOR | other agents' / other teams' leads, audits, results, reports, emails, chat conversations and attachments return 404 |
| Data integrity | domain normalisation (http/https/www/trailing slash/case), duplicate-domain leak protection across teams, similar-lead confirmation, optimistic-lock conflicts, concurrent assignment (single winner), idempotent create, import de-duplication, team deletion guard, one Team Lead per team, deactivation returns leads to the pool, last-Manager protection, append-only audit log |
| Audit workflow | start → real progress values (pages processed/discovered from engine messages) → completion with the engine's real scored result → lead updated → reports generated and downloadable (DOCX, and PDF when Word/LibreOffice exist) → HTML report → issue resolution; duplicate start blocked by the database; cancellation; SSRF targets rejected |
| Outreach | draft generated only from real findings, SMTP password never returned/stored in clear/logged, header-injection rejected, real SMTP conversation with a local test server including the report attachment, idempotent send, lead status → contacted, bulk preview skips recently-contacted leads and requires confirmation |
| Chat | DM permission matrix, team channel isolation, edit/delete ownership, reactions, replies, search scoping; no files in chat (upload → 410, `upload_ids` → 422); sharing a lead and a report (only what the sender can open; a report must be on the server first; teammates in the conversation can open the shared report, others cannot) |
| Uploads | email attachments: type sniffing, allow-list, filename sanitising; a file wiped from the server's disk comes back from the database copy |
| Work on each computer | an audit claimed only by its requester's app on the computer that asked; progress messages → percent / pages / logs; another computer or person cannot report for it; result stored once; reports listed but no file made on the server; `build_in_app` answer; result-data; report upload checks; email and chat need the app's copy first; teammates download the stored copy after a wiped disk; regenerate drops copies; failed and cancelled audits; restart gives running audits back (twice → failed); a silent app loses its audit; slots per computer; PageSpeed through the server key; oversized / broken / incomplete results refused; `/api/ping` never touches the database; the report job writes a real Word report |
| Lead files read on the computer | preview from headers + count + sample, niche/area guess, no stored file; rows in order (gap → 409, repeat accepted), duplicates in the file and in the bank, no-website rows; only the creator's app; finish and failed; size and type checks; the background job reads a preview and gzip chunks from a given row |
| Lead bank imports | column detection and niche/area guessed from file names (`Dentists in Chicago IL.csv`, `roofers-dallas-tx.xlsx`); rows without a website removed; duplicates removed by domain and by phone, both against the bank and within the file; Maps-only "websites" rejected; per-row reject list and CSV; European decimal ratings; Excel files; bad file types; a 20,000-row import timed (≈3 s on SQLite) |
| Lead bank management | niche → area facets and summary counts scoped per role; select-all-matching bulk assign with even split across a team's Agents; 409 when the matching count changed; Team Lead limited to own team; CSV export permissions |
| Audit queue | bulk preview skips (archived, waiting/running, audited in the last 30 days) and caps (100 / 500); stale confirmation → 409 with fresh numbers; scope (agents only their leads, Team Leads their team, batches private to their scope); never more than the configured audits at once; a single audit goes ahead of bulk ones; fair turns between people; restart puts running audits back in the queue once; cancelling a batch; one notification per batch and none per lead; results stored gzip-compressed and legacy results read and copied; PDFs made on first open, one conversion at a time, then served from disk; settings validation |
| Engine fetch and realtime | huge pages truncated at 3 MB, binary/data files not downloaded, HTML served as text/plain detected, declared charsets honoured; a WebSocket client that stops reading never blocks a publisher and is disconnected |
| Other | CV bank endpoints are gone, notification scoping and mark-read, dashboard per role, client cache and token refresh |

The audit workflow test replays a real stored engine result
(`tests/fixtures/real_audit_cancomco.json`) instead of crawling the internet,
so it is deterministic and fast. Live end-to-end audits are exercised manually
(below).

## Manual end-to-end checks

1. `set SEO_DB=sqlite`, `scripts\dev_seed.py`, `run.bat`, `run_desktop.bat`.
2. Manager: sign in → dashboard → create team → add Team Lead and Agent (note the
   one-time temporary passwords) → add/import leads → assign → configure SMTP →
   view activity, audit logs, chat.
3. Agent (first sign-in with the temporary password): forced password change →
   open assigned lead → start audit → watch live progress → results, issue
   explorer, security section → open/download reports → generate email from the
   audit, edit, send → activity → chat with Team Lead.
4. Team Lead: team view, team leads only, assign within the team, chat.
5. Failure paths: stop the server while signed in (connection indicator →
   Offline, screens show "Server unavailable" with Retry), restart and retry;
   let the access token expire (transparent refresh); deactivate a signed-in
   user (their next request returns to sign-in).

## Visual QA

`scripts/ui_snapshot.py` renders screens against a running local server and
writes PNGs, e.g.:

```
.venv\Scripts\python.exe scripts\ui_snapshot.py shots manager@dev.local <password> signin dashboard leads audits
```

Use `SNAP_W=1366 SNAP_H=768` to check the minimum supported resolution.
