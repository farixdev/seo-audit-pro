# Desktop client

`SEOAuditPro.exe` (source: `seo_audit_pro/`, launcher `SEOAuditPro.pyw`) is a
PySide6 application. It talks only to the API server.

## Startup

1. **Splash** — brand mark, product name, and a small startup log in the
   bottom-left: *Initializing application → Loading configuration → Loading
   interface → Checking local resources → Initializing secure session →
   Connecting to &lt;server&gt; → (Restoring your session → Loading workspace) →
   Ready*. Each line corresponds to real work; the progress line follows it.
   Heavy modules load while the splash is visible. No secrets or stack traces
   are ever shown.
2. **Sign in** — or straight into the workspace when "Keep me signed in" was
   used and the stored session is still valid. If the server cannot be
   reached, the sign-in screen says so and offers "Change server".
3. The splash fades into the main window; screens cross-fade on sign-in and
   sign-out. The window never shows an unstyled frame.

## Sign-in flows

| Screen | Purpose |
|---|---|
| Sign in | Email, password (show/hide), keep me signed in, forgot password |
| Reset your password | Requests an emailed 8-digit code (works when SMTP is configured) |
| Enter your reset code | Code + new password with live policy checklist |
| Choose a new password | Mandatory after a temporary password (first sign-in or manager reset); cannot be skipped |
| Server address | Test and save the API URL |

## Workspace

Collapsible sidebar (Ctrl+B), breadcrumb top bar, global search (Ctrl+K:
leads, audits, people, teams within your permissions), connection indicator
(Connected / Connecting / Offline), notifications popover with unread badge,
and the profile menu.

Navigation by role:

* **Manager** — Dashboard · Workspace (Lead Bank, Audits, Reports) ·
  Organization (Teams, Users) · Outreach (Emails, Activity) · Communication
  (Chat) · System (Notifications, Audit Logs, Settings)
* **Team Lead** — Dashboard, Lead Bank (own team), Audits, Reports, Team,
  Outreach, Chat, Notifications, Profile
* **Agent** — Dashboard, My Leads, Audits, Reports, Outreach, Chat,
  Notifications, Profile

**Back navigation.** The arrow left of the breadcrumb, Alt+Left and the mouse's
back button return to the previous screen with its filters, niche/area and page
intact.

Every screen has loading, empty, error, offline and permission-denied states.
First loads show skeleton placeholders; revisiting a screen shows the last
result instantly and refreshes it in the background (responses younger than
8 s are not re-fetched, and any change you make or that arrives over the
WebSocket clears the cache). Network work runs on a background thread pool; the
UI never blocks.

## Lead Bank

Built for Google Maps scrapes: one file per niche per area, hundreds of files,
millions of rows.

* **Niches & areas navigator** (left): every niche with its areas and lead
  counts; type to find a niche or city. Clicking narrows the table and the
  summary strip (leads, unassigned, audited, contacted).
* **Filters**: search, status, team, person, plus quick views — Unassigned,
  Not audited, Audited, Contacted, Not contacted. "Clear filters" resets all.
* **Table**: company with website, niche · area, Google rating and reviews,
  status, who it is assigned to (person and team), SEO score, outreach state
  and date added; server-side sorting and paging (25–200 rows, jump to page).
  Columns that do not fit the window are hidden instead of squeezed.
* **Selection**: tick rows or the header to select the page, then "Select all
  N matching" to act on every lead that matches the filters across all pages.
  Assign (to a person, to a team pool, or split evenly across the team's
  Agents), archive or restore; bulk email works on up to 200 ticked leads.
  Agents can tick their own leads too (Audit and Email only).
* **Audit**: queues audits for the ticked leads or for all matching leads. A
  review first shows how many will be audited, which are skipped (audited in
  the last 30 days — with an option to re-audit them —, already waiting or
  running, archived) and what is over the limit (100 per run, 500 for
  Managers), plus how long the queue will take. Then the app opens
  Audits › Queue; you get one notification when the batch finishes.
* **Import files**: drop or pick many CSV/XLSX files at once. Each file's niche
  and area are read from its name (editable) and its website column is detected.
  Imports run on the server in the background with live progress; rows without
  a website and leads already in the bank are removed.
* **Imports tab**: every file with rows read, leads added, duplicates and
  no-website counts; open a file's leads, see each removed row and why, or
  download the removed rows as CSV.
* **Export** downloads the current filter as CSV.
* Opening a lead shows its details (Google rating, Maps link, source file),
  audits, reports, outreach and activity, with Start audit and Compose email.

## Audits and the queue

Audits run on the server, never on the laptop. The **Queue** tab of Audits
shows how many run at a time, how many are waiting (and how many are yours),
the average audit time and when the queue will be empty; every bulk audit with
its progress and time left (cancel it from its menu); and every audit that is
running or waiting, with its place in line. A single **Start audit** goes ahead
of bulk audits. Managers set how many audits run at a time in Settings (2 suits
a 4 GB server).

Word reports are ready when an audit completes. PDFs are created on the server
the first time someone opens or emails them (a few seconds); lists show "Made
when first opened" until then. Actions
that create or send are disabled while running and carry idempotency keys, so
double-clicks cannot duplicate work.

## Realtime

A WebSocket delivers chat messages, typing, presence, notifications and audit
progress. It reconnects with backoff; while disconnected, the audit screen
polls every 5 s and the open chat every 15 s.

## Local data

* Preferences (server address, window geometry, sidebar state):
  `HKCU\Software\Mindcob\SEO Audit Pro`.
* Refresh token (only with "Keep me signed in"): Windows Credential Manager,
  entry "SEO Audit Pro".
* Downloads: `Documents\SEO Audit Pro`.
* Client log (no secrets): `%LOCALAPPDATA%\SEO Audit Pro\logs\client.log`.

## Design system

Tokens and the stylesheet live in `seo_audit_pro/theme.py`: graphite / off-white
neutrals, one accent for focus and links, colour reserved for status and
severity, a 4-px spacing scale (4–48), Segoe UI Variable typography. Icons are
stroke vectors (`seo_audit_pro/icons.py`); the brand mark and the multi-size
`.ico` are generated from vector source by `scripts/build_assets.py`.
