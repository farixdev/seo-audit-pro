# SEO Audit Pro

Website intelligence for SEO agencies: a Windows desktop application for
Managers, Team Leads and Agents to manage leads, run real technical SEO and
passive security audits, deliver client reports, run audit-grounded email
outreach and collaborate — backed by a secure API server.

```
Windows desktop (PySide6)  ──HTTPS / WSS──▶  Flask API (one process)  ──▶  PostgreSQL (Neon)
                                             │
                                             └─ existing audit engine, report generator, SMTP queue
```

The desktop client never connects to the database and contains no server
secrets. See [docs/architecture.md](docs/architecture.md).

## Quick start (local development)

```bat
:: 1. API server on the local SQLite database
set SEO_DB=sqlite
.venv\Scripts\python.exe scripts\dev_seed.py     :: once; demo accounts -> server_data\dev_credentials.txt
run.bat                                           :: http://127.0.0.1:5055

:: 2. Desktop client
run_desktop.bat
```

Build the Windows executable (and installer, if Inno Setup 6 is installed):

```bat
build_exe.bat
```

Production deployment, environment variables and first-Manager setup:
[docs/deployment.md](docs/deployment.md).

## What it does

| Area | Highlights |
|---|---|
| Access | Sign-in with lockout, forgot/reset password by emailed code, mandatory change of temporary passwords, refresh-token sessions stored in Windows Credential Manager, sign-out everywhere on password change |
| Roles | Manager, Team Lead, Agent — enforced by the API (see [docs/security.md](docs/security.md)) |
| Lead Bank | Built for Google Maps scrapes (one niche + area per file, many files, millions of rows): multi-file CSV/XLSX import running in the background with live progress; rows without a website and duplicates (by domain or phone, against the bank and within the file) removed and listed per file; niche → area navigator with counts; filters for unassigned / audited / contacted; select all matching across pages; assign to a person or team, or split evenly across a team; assignment history and conflict protection; CSV export; archive |
| Audits | One click from a lead, or bulk: select leads (or all matching) and queue up to 100 per run (500 for Managers); a saved server-side queue runs 2 at a time by default (sized for a 4 GB server), single audits first and fair turns between people, survives restarts; Queue view with progress, time left and cancel; one notification per finished batch. Real progress from the engine; scored results, issue explorer with resolved/unresolved tracking, passive security findings, recommendations, crawled pages |
| Reports | Client and technical DOCX when an audit completes; PDF created on first open (Word/LibreOffice on the server, one at a time); standalone HTML client report; JSON export. Results are stored compressed (~80 KB per audit) |
| Outreach | Email drafts generated only from real audit findings and always editable; attachments; encrypted SMTP (Gmail or company domain); rate-limited queue; bulk outreach with preview and confirmation; delivery activity |
| Collaboration | Direct messages and team channels with presence, typing, replies, edits, deletes, reactions, attachments and search; notifications |
| Administration | Teams, users, immutable audit log, audit defaults |

## The audit engine

Give it a website; it crawls the site and runs ~70 checks across ten weighted
categories, plus PageSpeed Insights (lab + real-user CrUX data) and a passive
security assessment.

| Category | Weight | Examples |
|---|---|---|
| Technical SEO | 16% | robots.txt, sitemap, canonicals, indexability, redirects, broken links, structured data, hreflang |
| On-Page SEO | 14% | titles, meta descriptions, headings, thin content, image alt, internal links |
| Performance | 14% | PageSpeed mobile/desktop, Core Web Vitals, render-blocking, compression, caching |
| Content | 10% | readability, depth, E-E-A-T signals, NAP |
| Mobile | 10% | viewport, responsive signals, tap targets, mobile speed |
| Security | 8% | HTTPS/TLS, certificates, HSTS, security headers, cookies, information disclosure, sensitive file exposure, known JS library CVEs, SPF/DMARC/DKIM, authentication surface, WAF, technology fingerprint |
| Local SEO | 8% | local business signals |
| Off-Page / Authority | 8% | estimated authority model (labelled as an estimate) |
| Accessibility | 8% | language, labels, link text, landmarks, Lighthouse a11y |
| UX | 4% | layout stability, interactivity, font loading |

Each check is pass (1.0) / warning (0.3) / fail (0.0); `info` and
not-applicable checks are excluded, categories without data are dropped and
weights renormalised, and honest penalties are applied. The security
assessment is passive: exposed sensitive files are reported only as "Publicly
accessible", never downloaded or quoted. Scoring and rules are documented in
[docs/AUDIT_SCORING.TXT](docs/AUDIT_SCORING.TXT) and
[docs/SCORING_RULES.TXT](docs/SCORING_RULES.TXT).

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, request flow, structure |
| [docs/desktop.md](docs/desktop.md) | Startup, sign-in flows, navigation, local data, design system |
| [docs/api.md](docs/api.md) | REST and WebSocket reference |
| [docs/database.md](docs/database.md) | Schema, constraints, migrations |
| [docs/security.md](docs/security.md) | Authentication, RBAC matrix, data protection |
| [docs/deployment.md](docs/deployment.md) | Server and client deployment |
| [docs/testing.md](docs/testing.md) | Automated and manual tests |
| [docs/IMPLEMENTATION_RECORD.md](docs/IMPLEMENTATION_RECORD.md) | What was found, changed, fixed, and what remains |
| `docs/*.TXT` | Engine methodology notes (scoring, security headers, indexation, …) |
