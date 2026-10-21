# -*- coding: utf-8 -*-
"""Lead detail: profile, actions, and Overview / Audits / Reports / Outreach / Activity tabs."""

import json
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QFrame, QMenu, QTabWidget, QWidget

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, settings, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, STATUS, score_color
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.reports import pending_pdf, report_size
from seo_audit_pro.ui.pages.lead_dialogs import (AssignDialog, LeadFormDialog, load_assignees, set_archived,
                                                 start_audit)
from seo_audit_pro.widgets.charts import ScoreRing
from seo_audit_pro.widgets.common import Badge, Card, StatefulArea, button, hbox, icon_button, label, vbox
from seo_audit_pro.widgets.feedback import clear_layout, kv_row, show_error, toast
from seo_audit_pro.widgets.table import Column, DataTable

REPORT_LABEL = {"client": "Client report", "technical": "Technical report"}


# ---------------------------------------------------------------------------
# Report file helpers (download with a save dialog, or open in the default app)
# ---------------------------------------------------------------------------
def report_filename(row):
    prefix = "SEO-Audit" if row.get("kind") == "client" else "SEO-Technical-Audit"
    return f"{prefix}-{row.get('normalized_domain') or 'site'}-{(row.get('created_at') or '')[:10]}.{row.get('format')}"


def download_report(page, row, open_after=False):
    if open_after:
        dest_dir, name = settings.downloads_dir(), report_filename(row)
    else:
        target, _ = QFileDialog.getSaveFileName(page, "Save report", os.path.join(settings.downloads_dir(),
                                                                                  report_filename(row)))
        if not target:
            return
        dest_dir, name = os.path.dirname(target), os.path.basename(target)
        if os.path.exists(target):
            try:
                os.remove(target)  # the user confirmed overwriting in the save dialog
            except OSError:
                pass
    if pending_pdf(row):
        toast(page, "Creating the PDF on the server — the first open takes a few seconds.", "info", 5000)
    else:
        toast(page, "Downloading report…", "info", 1800)

    def done(path):
        if open_after:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            toast(page, f"Saved to {path}", "success")

    tasks.run(api.download, f"/api/reports/{row['id']}/download", dest_dir, name, on_done=done,
              on_error=lambda e: show_error(page, e, "Download failed"), owner=page)


def _section(title, subtitle=""):
    w = QWidget()
    lay = vbox(w, spacing=2)
    lay.addWidget(label(title, "SectionTitle"))
    if subtitle:
        lay.addWidget(label(subtitle, "Meta"))
    return w


def _surface_table(table):
    frame = QFrame()
    frame.setObjectName("Surface")
    vbox(frame).addWidget(table)
    return frame


class LeadDetailPage(Page):
    nav_key = "leads"

    def __init__(self, ws):
        super().__init__(ws, scroll=True, spacing=20)
        self.breadcrumb = "My Leads" if session.is_agent() else "Lead Bank"
        self.title = "Lead"
        self.lead = None
        self.lead_id = None
        self.options = None
        self._loaded_tabs = set()

        content = QWidget()
        cl = vbox(content, spacing=20)

        # Header -------------------------------------------------------------
        head = hbox(spacing=16)
        left = vbox(spacing=6)
        title_row = hbox(spacing=10)
        self.name_label = label("", "PageTitle")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_row.addWidget(self.name_label)
        self.status_badge = Badge()
        title_row.addWidget(self.status_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        left.addLayout(title_row)
        sub = hbox(spacing=8)
        self.domain_btn = button("", "link", icon_name="external")
        self.domain_btn.setToolTip("Open the website in your browser")
        self.domain_btn.clicked.connect(self._open_website)
        sub.addWidget(self.domain_btn)
        self.sub_meta = label("", "Meta")
        sub.addWidget(self.sub_meta)
        sub.addStretch(1)
        left.addLayout(sub)
        head.addLayout(left, 1)
        self.audit_btn = button("Start audit", "primary", icon_name="audit")
        self.audit_btn.clicked.connect(self._audit_action)
        self.email_btn = button("Compose email", icon_name="mail")
        self.email_btn.clicked.connect(lambda: self.ws.navigate("outreach", lead_id=self.lead_id))
        self.edit_btn = button("Edit", icon_name="edit")
        self.edit_btn.clicked.connect(self._edit)
        self.more_btn = icon_button("more", "More actions", 18)
        self.more_btn.clicked.connect(self._more_menu)
        for b in (self.edit_btn, self.email_btn, self.audit_btn):
            head.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.more_btn.setVisible(not session.is_agent())
        cl.addLayout(head)

        # Body: tabs + details ----------------------------------------------
        body = hbox(spacing=20)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs, 1)

        self.details = Card(padding=20, spacing=10)
        self.details.setFixedWidth(320)
        self.details.body.addWidget(label("Details", "SectionTitle"))
        self.details_rows = vbox(spacing=8)
        self.details.body.addLayout(self.details_rows)
        self.details.body.addStretch(1)
        body.addWidget(self.details, 0, Qt.AlignmentFlag.AlignTop)
        cl.addLayout(body, 1)

        self._build_overview()
        self._build_audits()
        self._build_reports()
        self._build_outreach()
        self._build_activity()

        self.area = StatefulArea(content, skeleton="detail")
        self.area.retry.connect(self.reload)
        self.body.addWidget(self.area, 1)

    # -- tabs ----------------------------------------------------------------
    def _build_overview(self):
        w = QWidget()
        lay = vbox(w, (0, 16, 0, 0), 16)
        self.latest_card = Card(padding=20, spacing=12)
        lay.addWidget(self.latest_card)
        notes = Card(padding=20, spacing=8)
        notes.body.addWidget(label("Notes", "SectionTitle"))
        self.notes_label = label("", wrap=True, selectable=True)
        notes.body.addWidget(self.notes_label)
        lay.addWidget(notes)
        lay.addStretch(1)
        self.tabs.addTab(w, "Overview")

    def _tab_table(self, title, columns, empty_text):
        w = QWidget()
        lay = vbox(w, (0, 16, 0, 0), 0)
        table = DataTable(columns, paginated=False)
        table.view.setMinimumHeight(320)
        area = StatefulArea(_surface_table(table))
        lay.addWidget(area, 1)
        self.tabs.addTab(w, title)
        area.empty_text = empty_text
        return table, area

    def _build_audits(self):
        self.audits_table, self.audits_area = self._tab_table("Audits", [
            Column("status", "Status", "status", 110),
            Column("score", "Score", "score", 120),
            Column("issues_found", "Issues", "number", 70, align="right",
                   render=lambda r: fmt.number(r["issues_found"]) if r["status"] == "completed" else "—"),
            Column("requested_by_name", "Started by", "text", 140),
            Column("created_at", "Started", "datetime", 150),
            Column("completed_at", "Duration", "text", 100, stretch=True,
                   render=lambda r: fmt.duration(r.get("started_at"), r.get("completed_at"))
                   if r.get("completed_at") else ("In progress" if r["status"] in ("queued", "running") else "—")),
        ], "No audits have been run for this lead yet.")
        self.audits_table.activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["id"]))
        self.audits_area.retry.connect(lambda: self._load_tab(1, force=True))

    def _build_reports(self):
        self.reports_table, self.reports_area = self._tab_table("Reports", [
            Column("kind", "Report", "strong", 180, render=lambda r: REPORT_LABEL.get(r["kind"], r["kind"]),
                   secondary=lambda r: f"Audit score {r['score']}" if r.get("score") is not None else ""),
            Column("format", "Format", "text", 80, render=lambda r: (r.get("format") or "").upper()),
            Column("created_at", "Created", "datetime", 160),
            Column("size_bytes", "Size", "text", 170, render=report_size),
            Column("agent_name", "Audited by", "text", 140, stretch=True),
            Column("_actions", "", "actions", 44),
        ], "Reports appear here after an audit completes.")
        self.reports_table.activated.connect(lambda r: download_report(self, r, open_after=True))
        self.reports_table.context_requested.connect(self._report_menu)
        self.reports_area.retry.connect(lambda: self._load_tab(2, force=True))

    def _build_outreach(self):
        self.emails_table, self.emails_area = self._tab_table("Outreach", [
            Column("subject", "Subject", "strong", 260, secondary=lambda r: f"To {r['to_email']}"),
            Column("status", "Status", "status", 110),
            Column("sender_name", "Sender", "text", 150),
            Column("updated_at", "Updated", "relative", 120, stretch=True,
                   render=lambda r: fmt.relative(r.get("sent_at") or r.get("updated_at"))),
        ], "No emails have been drafted or sent to this lead.")
        self.emails_table.activated.connect(self._open_email)
        self.emails_area.retry.connect(lambda: self._load_tab(3, force=True))

    def _build_activity(self):
        w = QWidget()
        lay = vbox(w, (0, 16, 0, 0), 0)
        self.activity_card = Card(padding=20, spacing=0)
        self.activity_list = vbox(spacing=0)
        self.activity_card.body.addLayout(self.activity_list)
        self.activity_area = StatefulArea(self.activity_card, skeleton="detail")
        self.activity_area.retry.connect(lambda: self._load_tab(4, force=True))
        lay.addWidget(self.activity_area)
        lay.addStretch(1)
        self.tabs.addTab(w, "Activity")

    # -- lifecycle -----------------------------------------------------------
    def activate(self, lead_id=None, **_):
        if lead_id and lead_id != self.lead_id:
            self.lead_id = lead_id
            self.lead = None
            self._loaded_tabs.clear()
            self.tabs.setCurrentIndex(0)
        self.reload()

    def reload(self):
        if not self.lead_id:
            self.area.empty("No lead selected", "Open a lead from the lead list.")
            return
        if self.lead is None:
            self.area.loading("Loading lead…")
        self._loaded_tabs.clear()
        self.fetch("lead", api.get, f"/api/leads/{self.lead_id}", on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        lead = data["lead"]
        self.lead = lead
        archived = lead["status"] == "archived"
        self.ws.set_page_title(self, lead["company"])
        self.name_label.setText(lead["company"])
        fg, bg, text = STATUS.get(lead["status"], (C["text_2"], C["neutral_bg"], lead["status"].title()))
        self.status_badge.setText(text)
        self.status_badge.set_colors(fg, bg)
        self.domain_btn.setText(lead["normalized_domain"])
        self.sub_meta.setText(" · ".join(x for x in (lead.get("niche"), lead.get("area"), lead.get("country")) if x))
        running = lead.get("running_audit_id")
        self.audit_btn.setText("View running audit" if running else "Start audit")
        self.audit_btn.setEnabled(not archived or bool(running))
        self.audit_btn.setToolTip("Restore this lead to audit it" if archived and not running else "")
        self.email_btn.setEnabled(not archived)
        self._fill_details(lead)
        self.notes_label.setText(lead.get("notes") or "No notes yet. Use Edit to add context for your team.")
        self.notes_label.setStyleSheet("" if lead.get("notes") else f"color: {C['text_3']};")
        self.area.ready()
        self._load_tab(self.tabs.currentIndex())
        if self.tabs.currentIndex() != 0:
            self._load_tab(0)

    def _fill_details(self, lead):
        clear_layout(self.details_rows)
        score = lead.get("last_audit_score")
        if score is None:
            audit_val = label("Not audited", "Muted")
        else:
            audit_val = label(f"{score}/100 · {fmt.relative(lead.get('last_audit_at'))}")
            audit_val.setStyleSheet(f"color: {score_color(score)}; font-weight: 600;")
        email = lead.get("email")
        rating = lead.get("rating")
        reviews = lead.get("review_count")
        google = None
        if rating is not None or reviews is not None:
            google = " · ".join(x for x in (f"{rating:.1f} rating" if rating is not None else "",
                                            f"{reviews:,} reviews" if reviews is not None else "") if x)
        rows = [("Company", lead["company"]), ("Domain", lead["normalized_domain"]),
                ("Niche", lead.get("niche")), ("Area", lead.get("area")), ("Category", lead.get("category")),
                ("Address", lead.get("address")), ("Google", google),
                ("Contact", lead.get("contact_name")), ("Email", email), ("Phone", lead.get("phone")),
                ("Country", lead.get("country")),
                ("Assigned team", lead.get("team_name") or "Unassigned"),
                ("Assigned agent", lead.get("agent_name") or "Unassigned"),
                ("Source", lead.get("batch_name") or lead.get("source")), ("Created", fmt.date(lead.get("created_at"))),
                ("Last audit", audit_val), ("Last contact", fmt.relative(lead.get("last_contact_at"), "Never contacted"))]
        rows = [(k, v) for k, v in rows if v not in (None, "") or k in ("Contact", "Email", "Phone", "Niche", "Area")]
        if lead.get("maps_url"):
            link = button("Open in Google Maps", "link")
            link.setStyleSheet("QPushButton { padding: 0; text-align: left; }")
            link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(lead["maps_url"])))
            rows.insert(6, ("Maps", link))
        for k, v in rows:
            row = kv_row(k, v)
            row.layout().itemAt(0).widget().setFixedWidth(100)
            self.details_rows.addWidget(row)

    def _tab_changed(self, index):
        if self.lead is not None:
            self._load_tab(index)

    def _load_tab(self, index, force=False):
        if index in self._loaded_tabs and not force:
            return
        self._loaded_tabs.add(index)
        lid = self.lead_id
        if index == 0:
            self.fetch("overview", api.get, "/api/audits", params={"lead_id": lid, "page_size": 10},
                       on_done=self._render_overview, on_error=lambda e: self._overview_error(e))
        elif index == 1:
            self._load_table("audits", "/api/audits", self.audits_table, self.audits_area, {"lead_id": lid, "page_size": 100})
        elif index == 2:
            self._load_table("reports", "/api/reports", self.reports_table, self.reports_area,
                             {"lead_id": lid, "page_size": 100})
        elif index == 3:
            self._load_table("emails", "/api/outreach/emails", self.emails_table, self.emails_area,
                             {"lead_id": lid, "page_size": 100})
        elif index == 4:
            self.activity_area.loading("Loading activity…")
            self.fetch("activity", api.get, f"/api/leads/{lid}/activity", on_done=self._render_activity,
                       on_error=self.activity_area.error)

    def _load_table(self, channel, path, table, area, params):
        if not table.model.rows:
            area.loading()

        def done(data):
            table.set_rows(data.get("items") or [])
            if data.get("items"):
                area.ready()
            else:
                area.empty(area.empty_text)

        self.fetch(channel, api.get, path, params=params, on_done=done, on_error=area.error)

    # -- overview ------------------------------------------------------------
    def _overview_error(self, err):
        clear_layout(self.latest_card.body)
        self.latest_card.body.addWidget(label("Latest audit", "SectionTitle"))
        self.latest_card.body.addWidget(label(err.message, "Muted", wrap=True))

    def _render_overview(self, data):
        items = data.get("items") or []
        body = self.latest_card.body
        clear_layout(body)
        head = hbox(spacing=8)
        head.addWidget(label("Latest audit", "SectionTitle"), 1)
        body.addLayout(head)
        running = next((a for a in items if a["status"] in ("queued", "running")), None)
        latest = next((a for a in items if a["status"] == "completed"), None)
        if running:
            line = hbox(spacing=8)
            line.addWidget(Badge.status(running["status"]))
            line.addWidget(label(f"{running.get('percent') or 0}% · {running.get('step') or 'Waiting'}", "Muted"), 1)
            view = button("View progress", size="sm")
            view.clicked.connect(lambda: self.ws.navigate("audit", audit_id=running["id"]))
            line.addWidget(view)
            body.addLayout(line)
        if not latest:
            if not running:
                body.addWidget(label("No completed audit yet. Start an audit to measure this website.", "Muted",
                                     wrap=True))
            return
        row = hbox(spacing=24)
        ring = ScoreRing(104)
        ring.set_score(latest.get("score"), latest.get("grade"))
        row.addWidget(ring)
        col = vbox(spacing=8)
        col.addWidget(label(f"Completed {fmt.datetime_(latest.get('completed_at'))} by "
                            f"{latest.get('requested_by_name') or 'a team member'}", "Meta"))
        counts = hbox(spacing=6)
        for key, field in (("critical", "critical_count"), ("high", "high_count"), ("medium", "medium_count"),
                           ("low", "low_count")):
            b = Badge.severity(key)
            b.setText(f"{b.text()} {latest.get(field) or 0}")
            counts.addWidget(b)
        counts.addStretch(1)
        col.addLayout(counts)
        col.addWidget(label(f"{fmt.plural(latest.get('issues_found') or 0, 'issue')} across "
                            f"{fmt.plural(latest.get('pages_processed') or 0, 'page')} checked", "Muted"))
        actions = hbox(spacing=8)
        open_btn = button("Open audit", icon_name="external", size="sm")
        open_btn.clicked.connect(lambda: self.ws.navigate("audit", audit_id=latest["id"]))
        actions.addWidget(open_btn)
        actions.addStretch(1)
        col.addLayout(actions)
        row.addLayout(col, 1)
        body.addLayout(row)

    # -- activity ------------------------------------------------------------
    def _render_activity(self, data):
        clear_layout(self.activity_list)
        entries = []
        for a in data.get("assignments") or []:
            src = " / ".join(x for x in (a.get("from_team_name"), a.get("from_agent_name")) if x) or "Unassigned"
            dst = " / ".join(x for x in (a.get("to_team_name"), a.get("to_agent_name")) if x) or "Unassigned"
            title = "Assigned" if not (a.get("from_team_id") or a.get("from_agent_id")) else "Reassigned"
            detail = f"{src}  →  {dst}"
            if a.get("reason"):
                detail += f"  ·  {a['reason']}"
            entries.append((a["created_at"], "user_check", title, a.get("changed_by_name"), detail))
        for log in data.get("activity") or []:
            if log["action"] in ("LEAD_ASSIGNED", "LEAD_REASSIGNED"):
                continue  # shown with richer detail from the assignment history
            entries.append((log["created_at"], _ACTION_ICON.get(log["action"], "activity"),
                            fmt.action_label(log["action"]), log.get("user_name") or log.get("user_email"),
                            _log_detail(log)))
        entries.sort(key=lambda e: e[0] or "", reverse=True)
        if not entries:
            self.activity_area.empty("No activity yet", "Changes, audits and emails for this lead will appear here.")
            return
        for created, icon_name, title, who, detail in entries:
            self.activity_list.addWidget(_timeline_row(created, icon_name, title, who, detail))
        self.activity_area.ready()

    # -- actions -------------------------------------------------------------
    def _open_website(self):
        if self.lead and self.lead["website"].lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(self.lead["website"]))

    def _audit_action(self):
        if not self.lead:
            return
        running = self.lead.get("running_audit_id")
        if running:
            self.ws.navigate("audit", audit_id=running)
        else:
            start_audit(self, self.lead)

    def _edit(self):
        if not self.lead:
            return
        dlg = LeadFormDialog(self, lead=self.lead)
        if dlg.exec():
            toast(self, "Lead updated.", "success")
        # Reload either way: a stale-version conflict means someone else changed it.
        self.reload()

    def _assign(self):
        def go(opts):
            self.options = opts
            dlg = AssignDialog(self, opts, leads=[self.lead])
            if dlg.exec():
                toast(self, "Assignment updated.", "success")
            self.reload()
        if self.options is None:
            load_assignees(self, go)
        else:
            go(self.options)

    def _more_menu(self):
        if not self.lead:
            return
        m = QMenu(self)
        archived = self.lead["status"] == "archived"
        if not archived:
            m.addAction(icons.icon("user_check", C["text_2"]), "Assign…", self._assign)
            m.addSeparator()
            m.addAction(icons.icon("archive", C["text_2"]), "Archive",
                        lambda: set_archived(self, True, self.reload, leads=[self.lead]))
        else:
            m.addAction(icons.icon("restore", C["text_2"]), "Restore",
                        lambda: set_archived(self, False, self.reload, leads=[self.lead]))
        m.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def _report_menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("external", C["text_2"]), "Open", lambda: download_report(self, row, open_after=True))
        m.addAction(icons.icon("download", C["text_2"]), "Download…", lambda: download_report(self, row))
        m.addAction(icons.icon("audit", C["text_2"]), "Open audit",
                    lambda: self.ws.navigate("audit", audit_id=row["audit_id"]))
        m.exec(pos)

    def _open_email(self, row):
        key = "outreach_activity" if session.is_manager() else "outreach"
        self.ws.navigate(key, email_id=row["id"])

    def on_realtime(self, ev):
        if ev.get("type") == "audit_progress" and ev.get("lead_id") == self.lead_id and \
                ev.get("status") in ("completed", "failed", "cancelled") and self.isVisible():
            self.reload()


_ACTION_ICON = {"LEAD_CREATED": "plus", "LEAD_UPDATED": "edit", "LEAD_ARCHIVED": "archive", "LEAD_RESTORED": "restore",
                "AUDIT_STARTED": "audit", "AUDIT_COMPLETED": "check_circle", "AUDIT_FAILED": "alert_circle",
                "AUDIT_CANCELLED": "stop", "AUDIT_CANCEL_REQUESTED": "stop", "REPORT_GENERATED": "report",
                "EMAIL_SENT": "send", "EMAIL_QUEUED": "mail", "EMAIL_FAILED": "alert_triangle",
                "EMAIL_DRAFT_SAVED": "mail", "EMAIL_DRAFT_DELETED": "trash", "ISSUE_STATUS_CHANGED": "check",
                "AUDIT_EXPORTED": "download"}


def _log_detail(log):
    try:
        meta = json.loads(log["metadata"]) if isinstance(log.get("metadata"), str) and log["metadata"] else (log.get("metadata") or {})
    except ValueError:
        return ""
    action = log["action"]
    if action == "AUDIT_COMPLETED" and meta.get("score") is not None:
        return f"Score {meta['score']}/100"
    if action in ("AUDIT_FAILED", "AUDIT_CANCELLED") and meta.get("error"):
        return meta["error"]
    if action in ("EMAIL_SENT", "EMAIL_QUEUED") and meta.get("to"):
        return f"To {meta['to']}"
    if action == "EMAIL_FAILED" and meta.get("reason"):
        return meta["reason"]
    if action == "REPORT_GENERATED" and meta.get("formats"):
        return ", ".join(str(f).replace(":", " ").upper() for f in meta["formats"])
    if action == "ISSUE_STATUS_CHANGED" and meta.get("check_id"):
        return f"{meta['check_id']} marked {meta.get('status')}"
    if action == "LEAD_UPDATED":
        changed = [k.replace("_", " ") for k in meta if k not in ("version",)]
        return ("Changed " + ", ".join(changed)) if changed else ""
    return ""


def _timeline_row(created, icon_name, title, who, detail):
    w = QFrame()
    w.setObjectName("TimelineRow")
    w.setStyleSheet(f"QFrame#TimelineRow {{ border-bottom: 1px solid {C['divider']}; }}")
    row = hbox(w, (0, 12, 0, 12), 12)
    ic = label()
    ic.setPixmap(icons.pixmap(icon_name, C["text_2"], 16))
    ic.setFixedWidth(20)
    row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
    col = vbox(spacing=2)
    t = label(title)
    t.setStyleSheet("font-weight: 600;")
    col.addWidget(t)
    if detail:
        col.addWidget(label(detail, "Muted", wrap=True, selectable=True))
    col.addWidget(label(f"{who or 'System'} · {fmt.datetime_(created)}", "Meta"))
    row.addLayout(col, 1)
    return w
