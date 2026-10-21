# Testing

## Automated API tests

```
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

The suite (`tests/test_api.py`, `tests/test_lead_bank.py`, `tests/test_audit_queue.py`,
`tests/test_engine_fetch.py`, `tests/test_client_core.py`) runs against a throw-away SQLite database and
data directory; it never touches a configured PostgreSQL database
(`SEO_DB=sqlite` is forced by `tests/helpers.py`). It covers:

| Area | What is verified |
|---|---|
| Authentication | login, generic errors, lockout after 5 failures, inactive accounts, refresh-token rotation and single use, logout revocation, tampered tokens, first-login forced password change and policy, forgot/reset password (no account enumeration, single-use code) |
| RBAC | Manager-only endpoints return 403 for Team Lead/Agent and are logged as `PERMISSION_DENIED`; client-sent role/team/agent fields are ignored |
| IDOR | other agents' / other teams' leads, audits, results, reports, emails, chat conversations and attachments return 404 |
| Data integrity | domain normalisation (http/https/www/trailing slash/case), duplicate-domain leak protection across teams, similar-lead confirmation, optimistic-lock conflicts, concurrent assignment (single winner), idempotent create, import de-duplication, team deletion guard, one Team Lead per team, deactivation returns leads to the pool, last-Manager protection, append-only audit log |
| Audit workflow | start → real progress values (pages processed/discovered from engine messages) → completion with the engine's real scored result → lead updated → reports generated and downloadable (DOCX, and PDF when Word/LibreOffice exist) → HTML report → issue resolution; duplicate start blocked by the database; cancellation; SSRF targets rejected |
| Outreach | draft generated only from real findings, SMTP password never returned/stored in clear/logged, header-injection rejected, real SMTP conversation with a local test server including the report attachment, idempotent send, lead status → contacted, bulk preview skips recently-contacted leads and requires confirmation |
| Chat | DM permission matrix, team channel isolation, edit/delete ownership, reactions, replies, search scoping, upload type sniffing and filename sanitising |
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
