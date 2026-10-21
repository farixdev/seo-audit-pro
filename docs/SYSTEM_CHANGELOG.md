# SEO Audit Pro — System Changelog

## 3.0.0 — secure client/server rebuild

* New API server (`backend/`): sessions with rotating refresh tokens, RBAC and
  lead-scoped IDOR protection, versioned migrations with constraints,
  realtime WebSocket hub, rate-limited email queue, report pipeline.
* New desktop client (`seo_audit_pro/`): splash startup, sign-in and password
  flows, role-based workspace, all screens wired to the API.
* Engine fixes: broken-link and structured-data stages were never running;
  bounded result cache.
* CV bank reinstated as a Manager-only feature (the current product brief
  requires it, superseding the earlier removal noted below).
* Details: [IMPLEMENTATION_RECORD.md](IMPLEMENTATION_RECORD.md).

---

## Historical entries (2.x prototype)


## Overview

This changelog records the recent system architecture upgrades, bug fixes, module reorganizations, and user interface enhancements implemented across the **SEO Audit Pro** enterprise platform.

---

## 1. Agent & Team Lead CRUD Implementation

### Root Cause Analysis
- In earlier versions of `desktop/views/users_view.py`, permission checks (`if session.is_manager():` and `if session.is_team_lead():`) were evaluated strictly inside widget `__init__`.
- At application startup, widgets are instantiated before auto-login completes and session credentials are populated. As a result, creation buttons were permanently omitted from layouts and table action columns were never allocated.

### Changes Applied
- **Unconditional Control Instantiation:** `self.create_btn` and all table columns (7 columns total, including the `ACTIONS` column) are now allocated unconditionally during `__init__`.
- **Dynamic Permission Hook (`update_permissions()`):** Dynamically updates button text (*"Provision New User"* for Managers, *"Add Team Member"* for Team Leads) and column visibility whenever data loads or sessions switch.
- **Provisioning Modal:** Managers can select between **`AGENT`** and **`TEAM_LEAD`** roles and assign them to specific team pods.
- **Edit Modal (`_open_edit_dialog`):** Allows updating full name, work email, role, and team assignment.
- **Activation Toggle (`_toggle_status`):** Instant active/inactive account toggling.
- **Deletion (`_delete_user`):** Confirmation dialog safely removes user accounts while retaining and unlinking assigned leads.

**Modified Files:**
- `desktop/views/users_view.py`
- `agency_routes.py`
- `desktop/core/api_client.py`

---

## 2. Permanent Eradication of Candidate Bank (`cv_bank`)

### Context
- The candidate bank feature was deemed unnecessary and requested to be completely removed from the product.

### Changes Applied
- Completely removed `cv_bank` from sidebar navigation (`desktop/widgets/sidebar.py`).
- Removed `cv_bank` routing from `desktop/main_window.py`.
- Deleted obsolete view file `desktop/views/cv_bank_view.py`.
- Removed `cv_bank` imports from PyInstaller build specifications (`SEOAuditPro_Enterprise.spec`).

---

## 3. Lead Bank with Niche, Area, Ingestion & Deduplication

### Architectural Additions
- **Niche & Area Columns:** Added `niche TEXT DEFAULT 'General'` and `area TEXT DEFAULT 'Global'` to `agency_leads` with automated migrations in `agency_db.py`.
- **API Endpoints:**
  - `GET /api/leads`: Added query filters for `niche`, `area`, `assigned_status` ('assigned'/'unassigned'), and `outreach_status` ('contacted'/'pending').
  - `POST /api/leads/import`: Ingests bulk scraped records, handles default fallbacks, and executes domain deduplication.
- **Smart Column Detection (`desktop/core/file_importer.py`):**
  - Auto-identifies `niche` (`niche`, `category`, `industry`, `type`, `sector`) and `area` (`area`, `city`, `location`, `region`, `address`, `state`).
  - Added modal fallback inputs (`Default Niche`, `Default Area`).
- **High-Density 11-Column Table (`desktop/views/leads_view.py`):**
  - Columns: `SELECT` | `COMPANY` | `DOMAIN / WEBSITE` | `NICHE` | `AREA` | `CONTACT` | `STATUS` | `ASSIGNED TO` | `OUTREACH` | `AUDIT SCORE` | `ACTIONS`.
  - Visual badges for outreach state (`Contacted` vs `Pending`).
  - Real-time top filtering by Search, Niche, Area, Status, Assignment, and Outreach.

**Modified Files:**
- `agency_db.py`
- `agency_routes.py`
- `desktop/core/file_importer.py`
- `desktop/core/api_client.py`
- `desktop/views/leads_view.py`

---

## 4. Agency Team Structure CRUD

### Changes Applied
- Fixed permission evaluation bug in `desktop/views/teams_view.py`: `self.new_team_btn` and 6 table columns (including `ACTIONS`) are allocated unconditionally.
- Added full Team lifecycle controls:
  - **Create New Team:** Provision custom team pods and assign Team Leads.
  - **Edit Team:** Rename pod and reassign Team Lead.
  - **Delete Team:** Safely dissolve team pods without losing member leads.

**Modified Files:**
- `desktop/views/teams_view.py`
- `agency_routes.py`

---

## 5. Floating Quick-Chat Bubble & Live Online Presence

### Architectural Additions
- Created new widget module `desktop/widgets/chat_bubble.py`:
  - **Floating Bubble (`ChatFloatingBubble`):** Pinned 54x54 circular action button at bottom-right corner with blue glow hover effect.
  - **Quick Messenger Card (`FloatingChatOverlay`):** 380x520 popup card positioned directly above the floating bubble.
  - Displays all agency contacts with **live online status** (Green dot for users active within 25 minutes, Gray dot for offline).
  - Searchable contact list with 1-on-1 direct messaging, message history, auto-scroll, and auto-polling.
  - Quick action button to navigate directly into the full internal Chat Studio.
- Integrated with `desktop/main_window.py` with responsive repositioning on window resize events.

**Modified Files:**
- `desktop/widgets/chat_bubble.py` (New file)
- `desktop/main_window.py`
- `agency_routes.py`
- `desktop/core/api_client.py`

---

## 6. Mindcob Branding & UI Consistency

### Changes Applied
- **Top-Left Header:** Embedded `assets/icon.png` (24x24) and `assets/logo.png` (scaled) in `desktop/widgets/topbar.py` with a vertical separator before the section title.
- **Sidebar Header:** Loaded `assets/icon.png` (32x32) alongside "Mindcob SEO Audit Pro Suite" branding in `desktop/widgets/sidebar.py`.
- **Badge Styling Standardization:** Replaced ad-hoc label backgrounds in table cells with unified `status_badge` components in `desktop/widgets/badge.py`.
- **Vector SVG Icons:** Verified complete Lucide SVG vector icon library in `desktop/core/icons.py`. Ran automated validation confirming **0 missing icons** and **0 emojis** across the desktop application.

**Modified Files:**
- `desktop/widgets/topbar.py`
- `desktop/widgets/sidebar.py`
- `desktop/widgets/badge.py`
- `desktop/views/leads_view.py`
- `desktop/core/icons.py`

---

## 7. Packaging & Distribution Artifacts

### Executables Generated
- Verified and built via `build_exe.bat` using PyInstaller:
  - **Single-File Executable:** `dist\SEO Audit Pro (Standalone).exe` (104.9 MB)
  - **Shareable Standalone ZIP:** `dist\SEO Audit Pro-Standalone.zip` (104.1 MB)
  - **Portable Distribution ZIP:** `dist\SEO Audit Pro-Portable.zip` (107.7 MB)
- Application can be launched locally via `run_desktop.bat` or by running `.venv\Scripts\python.exe desktop/app.py`.
