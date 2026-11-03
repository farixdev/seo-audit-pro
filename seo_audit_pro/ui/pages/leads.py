# -*- coding: utf-8 -*-
"""
Lead bank.

Leads tab
  Left: niche → area navigator with live counts (the "bank" structure built
  from imported files). Right: filters, quick views (unassigned, audited,
  contacted…), a summary for the current selection, and the lead table with
  server-side sorting and pagination. Selection works per page or for every
  lead matching the filter ("select all"), for assigning, splitting across a
  team, emailing or archiving.

Imports tab (Manager / Team Lead)
  Every uploaded file with live progress and what happened to its rows
  (imported, duplicates removed, rows without a website removed), with a
  per-file rejected-rows report.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QMenu, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, settings, tasks
from seo_audit_pro.core.realtime import get_hub
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.lead_dialogs import AssignDialog, LeadFormDialog, delete_leads, load_assignees, set_archived, start_audit
from seo_audit_pro.widgets.common import Badge, MetricStrip, Segmented, StatefulArea, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Drawer, Field, SearchBox, combo, line_edit, show_error, toast
from seo_audit_pro.widgets.table import Column, DataTable

class QuickEditDialog(Dialog):
    """In-place modal to edit domain or area with Enter-key and Esc support."""
    def __init__(self, parent, title, label_text, current_val, placeholder=""):
        super().__init__(parent, title, "", 460)
        self.edit = line_edit(placeholder, current_val or "")
        self.body.addWidget(Field(label_text, self.edit))
        self.add_button("Cancel")
        save_btn = self.add_button("Save Changes", "primary")
        save_btn.clicked.connect(self.accept)
        self.edit.returnPressed.connect(self.accept)

    def value(self):
        return self.edit.text().strip()

STATUS_FILTER = [("Any status", ""), ("New", "new"), ("Assigned", "assigned"), ("Audited", "audited"),
                 ("Contacted", "contacted"), ("Qualified", "qualified"), ("Won", "won"), ("Lost", "lost"),
                 ("Archived", "archived")]
QUICK_VIEWS = {"": {}, "everyone": {}, "unassigned": {"assigned": "unassigned"}, "not_audited": {"audited": "no"},
               "audited": {"audited": "yes"}, "no_website": {"has_website": "no"}, "services_audited": {"service_audited": "yes"}, "needs_service_audit": {"service_audited": "no", "has_website": "yes"},
               "contacted": {"outreach": "yes"}, "not_contacted": {"outreach": "no"}}
REASONS = {"no_website": "No website", "invalid_website": "Website not usable",
           "duplicate_in_file": "Duplicate within file", "duplicate_existing": "Already in lead bank"}
FILTER_KEYS = ("search", "status", "team_id", "agent_id", "assigned", "audited", "outreach", "niche", "area", "batch_id", "has_website", "call_status", "service_audited")


def _surface(widget):
    frame = QFrame()
    frame.setObjectName("Surface")
    vbox(frame).addWidget(widget)
    return frame


# ---------------------------------------------------------------------------
# Niche → area navigator
# ---------------------------------------------------------------------------
class LeadNavigator(QFrame):
    """Tree of niches and their areas with counts; calls on_select(niche_key, area_key)."""

    def __init__(self, on_select):
        super().__init__()
        self.setObjectName("Surface")
        self.on_select = on_select
        self.setMinimumWidth(230)
        lay = vbox(self, (12, 14, 12, 8), 10)
        head = hbox(spacing=6)
        head.addWidget(label("Niches & areas", "SectionTitle"), 1)
        lay.addLayout(head)
        self.total = label("", "Meta")
        lay.addWidget(self.total)
        self.search = SearchBox("Find a niche or area", delay=150)
        self.search.search.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setStyleSheet(
            "QTreeWidget { border: none; background: transparent; outline: none; }"
            "QTreeWidget::item { height: 30px; border-radius: 6px; }"
            "QTreeWidget::item:hover { background: #F4F4F6; }"
            f"QTreeWidget::item:selected {{ background: {C['selection']}; color: {C['text']}; }}")
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, hdr.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, hdr.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._clicked)
        lay.addWidget(self.tree, 1)
        self._selected = ("", "")

    def set_data(self, facets):
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        all_item = QTreeWidgetItem(["All leads", fmt.number(facets.get("total", 0))])
        all_item.setData(0, Qt.ItemDataRole.UserRole, ("", ""))
        all_item.setFont(0, bold)
        all_item.setIcon(0, icons.icon("layers", C["text_2"], 15))
        self.tree.addTopLevelItem(all_item)
        selected_item = all_item
        for n in facets.get("niches", []):
            item = QTreeWidgetItem([n["label"], fmt.number(n["count"])])
            item.setData(0, Qt.ItemDataRole.UserRole, (n["key"], ""))
            item.setFont(0, bold)
            item.setToolTip(0, f"{n['label']}: {n['count']:,} leads, {n['unassigned']:,} unassigned, "
                               f"{len(n['areas'])} area(s)")
            item.setForeground(1, QColor(C["text_3"]))
            for a in n["areas"]:
                child = QTreeWidgetItem([a["label"], fmt.number(a["count"])])
                child.setData(0, Qt.ItemDataRole.UserRole, (n["key"], a["key"]))
                child.setIcon(0, icons.icon("map_pin", C["text_3"], 13))
                child.setToolTip(0, f"{n['label']} in {a['label']}: {a['count']:,} leads, "
                                    f"{a['unassigned']:,} unassigned")
                child.setForeground(1, QColor(C["text_3"]))
                item.addChild(child)
                if (n["key"], a["key"]) == self._selected:
                    selected_item = child
            self.tree.addTopLevelItem(item)
            if (n["key"], "") == self._selected:
                selected_item = item
        self.total.setText(f"{len(facets.get('niches', []))} niches · {len(facets.get('areas', []))} areas")
        self.tree.setCurrentItem(selected_item)
        if selected_item.parent():
            selected_item.parent().setExpanded(True)
        self.tree.blockSignals(False)
        self._filter(self.search.text())

    def _find(self, niche, area):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole) == (niche, area):
                return top
            for j in range(top.childCount()):
                if top.child(j).data(0, Qt.ItemDataRole.UserRole) == (niche, area):
                    return top.child(j)
        return None

    def select(self, niche, area):
        self._selected = (niche or "", area or "")
        item = self._find(*self._selected)
        self.tree.blockSignals(True)
        if item:
            self.tree.setCurrentItem(item)
            if item.parent():
                item.parent().setExpanded(True)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)

    def label_for(self, niche, area):
        item = self._find(niche or "", area or "")
        if item:
            return item.text(0) if not item.parent() else f"{item.parent().text(0)} › {item.text(0)}"
        return " › ".join(x for x in (niche, area) if x).title()

    def _clicked(self, item, _col):
        niche, area = item.data(0, Qt.ItemDataRole.UserRole)
        self._selected = (niche, area)
        if niche and not area:
            item.setExpanded(True)
        self.on_select(niche, area)

    def _filter(self, text):
        q = (text or "").strip().lower()
        for i in range(1, self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            niche_hit = q in top.text(0).lower()
            child_hit = False
            for j in range(top.childCount()):
                child = top.child(j)
                hit = q in child.text(0).lower()
                child.setHidden(bool(q) and not (niche_hit or hit))
                child_hit = child_hit or (bool(q) and hit)
            top.setHidden(bool(q) and not niche_hit and not child_hit)
            if q and child_hit:
                top.setExpanded(True)


# ---------------------------------------------------------------------------
# Imports tab
# ---------------------------------------------------------------------------
class ImportsView(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = vbox(self, spacing=14)
        self.metrics = MetricStrip()
        for key, text in (("files", "Files imported"), ("rows_read", "Rows read"), ("imported", "Leads added"),
                          ("duplicates", "Duplicates removed"), ("no_website", "No website removed")):
            self.metrics.add(key, text)
        lay.addWidget(self.metrics)
        bar = hbox(spacing=8)
        self.search = SearchBox("Search file, niche or area")
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search, 1)
        self.status = combo([("Any status", ""), ("Importing", "processing"), ("Queued", "queued"),
                             ("Completed", "completed"), ("Failed", "failed")])
        self.status.currentIndexChanged.connect(lambda _i: self._set(status=self.status.currentData()))
        bar.addWidget(self.status)
        lay.addLayout(bar)

        def status_text(r):
            if r["status"] == "processing" and r["total_rows"]:
                return f"Importing {r['processed_rows'] * 100 // max(1, r['total_rows'])}%"
            return {"queued": "Waiting", "processing": "Importing", "completed": "Completed",
                    "failed": "Failed", "cancelled": "Cancelled"}.get(r["status"], r["status"])

        self.table = DataTable([
            Column("file_name", "File", "strong", 260, stretch=True,
                   secondary=lambda r: " · ".join(x for x in (r.get("niche"), r.get("area")) if x)),
            Column("status", "Status", "text", 130, render=status_text,
                   secondary=lambda r: (r.get("error") or "")[:60]),
            Column("total_rows", "Rows", "number", 90, align="right"),
            Column("imported", "Imported", "number", 90, align="right"),
            Column("duplicates", "Duplicates", "number", 100, align="right"),
            Column("no_website", "No website", "number", 100, align="right"),
            Column("agent_name", "Assigned to", "text", 160, optional=True,
                   render=lambda r: r.get("agent_name") or r.get("team_name") or "Unassigned"),
            Column("created_by_name", "Uploaded by", "text", 140, optional=True),
            Column("created_at", "Uploaded", "relative", 110, optional=True),
            Column("_actions", "", "actions", 44),
        ])
        self.table.page_changed.connect(lambda p: self._set(page=p, keep_page=True))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.activated.connect(self.page.show_batch_leads)
        self.table.context_requested.connect(self._menu)
        self.area = StatefulArea(_surface(self.table))
        self.area.retry.connect(self.load)
        lay.addWidget(self.area, 1)
        self.params = {"page": 1, "page_size": 25}
        self.drawer = Drawer(page, 640)
        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self.load)

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def load(self):
        if not self.table.model.rows:
            self.area.loading("Loading imports…")
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        self.page.fetch("batches", api.get, "/api/lead-batches", params=params, on_done=self._render,
                        on_error=self.area.error)

    def _render(self, data):
        t = data.get("totals") or {}
        for key, m in self.metrics.metrics.items():
            m.set(fmt.number(t.get(key, 0)))
        self.table.set_page(data)
        active = any(r["status"] in ("queued", "processing") for r in data["items"])
        if active and not get_hub().connected:
            self._poll.start()
        else:
            self._poll.stop()
        if not data["items"]:
            filtered = bool(self.params.get("search") or self.params.get("status"))
            if filtered:
                self.area.empty("No imports match", "Change the search or status filter.", icon_name="filter")
            else:
                self.area.empty("No imports yet", "Import scraped CSV or Excel files to fill the lead bank. Each file "
                                "is one niche in one area.", "Import files", icon_name="upload", action_icon="upload",
                                on_action=self.page.import_files)
            return
        self.area.ready()

    def on_progress(self, batch):
        rows = self.table.model.rows
        for i, r in enumerate(rows):
            if r["id"] == batch["id"]:
                rows[i] = dict(r, **batch)
                top = self.table.model.index(i, 0)
                bottom = self.table.model.index(i, len(self.table.columns) - 1)
                self.table.model.dataChanged.emit(top, bottom)
                if batch["status"] in ("completed", "failed"):
                    QTimer.singleShot(400, self.load)  # refresh totals
                return
        self.load()

    def _menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("leads", C["text_2"]), "View leads from this file", lambda: self.page.show_batch_leads(row))
        if row.get("rejected"):
            m.addAction(icons.icon("list", C["text_2"]), "Rejected rows…", lambda: self.show_rejects(row))
            m.addAction(icons.icon("download", C["text_2"]), "Download rejected rows (CSV)", lambda: self.download(row))
        m.addSeparator()
        m.addAction(icons.icon("trash", C["danger"]), "Delete lead…",
                    lambda: delete_leads(self, self.load, leads=[row]))
        m.exec(pos)

    def download(self, row):
        default = os.path.join(settings.downloads_dir(), f"{os.path.splitext(row['file_name'])[0]} - rejected rows.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Save rejected rows", default, "CSV (*.csv)")
        if not path:
            return
        tasks.run(api.save_as, f"/api/lead-batches/{row['id']}/rejects.csv", path, on_done=lambda p: toast(self, f"Saved {os.path.basename(p)}", "success"),
                  on_error=lambda e: show_error(self, e), owner=self)

    def show_rejects(self, row):
        d = self.drawer
        d.clear()
        d.content_layout.addWidget(label(" · ".join(x for x in (row.get("niche"), row.get("area")) if x), "Meta"))
        stats = hbox(spacing=8)
        for key, text in (("duplicates", "Duplicates"), ("no_website", "No website"), ("invalid", "Website not usable")):
            stats.addWidget(Badge(f"{text}: {row.get(key, 0):,}", C["text_2"], C["neutral_bg"]))
        stats.addStretch(1)
        dl = button("Download CSV", icon_name="download", size="sm")
        dl.clicked.connect(lambda: self.download(row))
        stats.addWidget(dl)
        d.content_layout.addLayout(stats)
        seg = Segmented([("All", "")] + [(v, k) for k, v in REASONS.items()])
        d.content_layout.addWidget(seg)
        table = DataTable([
            Column("row_number", "Row", "number", 60),
            Column("company", "Company", "strong", 180, stretch=True,
                   secondary=lambda r: r.get("website") or "No website"),
            Column("reason", "Reason", "text", 190, render=lambda r: REASONS.get(r["reason"], r["reason"]),
                   secondary=lambda r: (f"Matches {r['existing_company']} ({r['existing_domain']})"
                                        if r.get("existing_company") else "")),
        ], page_sizes=(50, 100))
        table.view.setMinimumHeight(420)
        d.content_layout.addWidget(table, 1)
        state = {"page": 1, "page_size": 50, "reason": ""}

        def load():
            params = {k: v for k, v in state.items() if v}
            tasks.run(api.get, f"/api/lead-batches/{row['id']}/rejects", params=params, on_done=table.set_page,
                      on_error=lambda e: show_error(self, e), owner=table)
        seg.changed.connect(lambda v: (state.update(reason=v, page=1), load()))
        table.page_changed.connect(lambda p: (state.update(page=p), load()))
        table.page_size_changed.connect(lambda s: (state.update(page_size=s, page=1), load()))
        d.open_drawer(f"Rejected rows · {row['file_name']}")
        load()


def can_manage_lead(row):
    """Display hint (the server enforces it): may this user assign, archive or
    restore ``row``? Managers: any lead. Team Leads: only their team's leads.
    Leads a Manager added are visible to everyone but not manageable by all."""
    if session.is_manager():
        return True
    if session.is_team_lead():
        team = (session.user or {}).get("team_id")
        return bool(team) and (row or {}).get("assigned_team_id") == team
    return False


def can_edit_lead(row):
    """Display hint: may this user edit ``row`` (manager, its team's lead, or
    the agent it is assigned to)?"""
    if session.is_agent():
        return bool(session.user_id) and (row or {}).get("assigned_agent_id") == session.user_id
    return can_manage_lead(row)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class LeadsPage(Page):
    nav_key = "leads"

    def __init__(self, ws):
        super().__init__(ws, spacing=14)
        self.manage = not session.is_agent()
        self.title = "Lead Bank" if self.manage else "My Leads"
        self.params = {"page": 1, "page_size": 50, "sort": "created", "order": "desc"}
        if not self.manage:
            self.params["assigned"] = "mine"  # "My Leads": the leads assigned to this agent
        self.options = None
        self.all_matching = False
        self.total = 0
        self.batch_label = ""

        self.head = self.header(self.title, "")
        if self.manage:
            exp = self.head.add_action(button("Export", icon_name="download"))
            exp.clicked.connect(self.export)
            imp = self.head.add_action(button("Import files", "primary", icon_name="upload"))
            imp.clicked.connect(self.import_files)
        dedup_btn = self.head.add_action(button("Clean Duplicates", icon_name="refresh"))
        dedup_btn.clicked.connect(self.clean_duplicates)
        add = self.head.add_action(button("Add lead", icon_name="plus"))
        add.clicked.connect(self.create)

        self.tabs = Segmented([("Leads", "leads"), ("Imports", "imports")])
        self.tabs.changed.connect(self._switch_tab)
        if not self.manage:
            self.tabs.hide()
        self.body.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.views = QStackedWidget()
        self.body.addWidget(self.views, 1)

        leads_view = QWidget()
        lv = hbox(leads_view)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(14)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.nav = LeadNavigator(self._navigate_tree)
        split.addWidget(self.nav)
        main = QWidget()
        ml = vbox(main, spacing=12)
        bar = hbox(spacing=8)
        self.search = SearchBox("Search company, website or contact")
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search, 2)
        self.status = combo(STATUS_FILTER)
        self.status.currentIndexChanged.connect(lambda _i: self._set(status=self.status.currentData()))
        bar.addWidget(self.status)
        self.area_filter = combo([("All areas", "")])
        self.area_filter.currentIndexChanged.connect(lambda _i: self._set(area=self.area_filter.currentData()))
        bar.addWidget(self.area_filter)
        self.agent = combo([("Anyone", "")])
        if self.manage:
            self.agent.currentIndexChanged.connect(lambda _i: self._set(agent_id=self.agent.currentData()))
            bar.addWidget(self.agent)
        ml.addLayout(bar)
        views = [("All Leads", ""), ("Audit 1: SEO Audited (C1)", "audited"), ("Needs SEO Audit (C1)", "not_audited"), ("No Website / Phone (C2)", "no_website"),
                 ("Audit 2: Service Audited (C3)", "services_audited"), ("Needs Service Audit (C3)", "needs_service_audit"), ("Contacted", "contacted")]
        if not self.manage:
            # "Mine" is the agent's own leads; "All visible" adds the leads a Manager shared with everyone.
            views = [("Mine", ""), ("All visible", "everyone")] + [v for v in views[2:]]
        self.quick = Segmented(views)
        self.quick.changed.connect(self._quick)
        ml.addWidget(self.quick, 0, Qt.AlignmentFlag.AlignLeft)

        self.summary = QFrame()
        self.summary.setObjectName("Surface")
        sl = hbox(self.summary, (16, 10, 12, 10), 22)
        self.sum_labels = {}
        for key, text in (("total", "total leads"), ("seo_audited", "Audit 1: SEO audited"), ("service_audited", "Audit 2: Service audited"), ("no_website", "C2: no website"), ("called", "C2: called"),
                          ("outreached", "contacted")):
            if key == "unassigned" and not self.manage:
                continue  # an agent's leads are always assigned to them
            box = hbox(spacing=6)
            val = label("—")
            val.setStyleSheet("font-weight: 600; font-size: 14px;")
            box.addWidget(val)
            box.addWidget(label(text, "Muted"))
            sl.addLayout(box)
            self.sum_labels[key] = val
        sl.addStretch(1)
        self.where = label("", "Meta")
        sl.addWidget(self.where)
        self.clear_scope = button("Clear filters", "ghost", size="sm")
        self.clear_scope.clicked.connect(self.reset_filters)
        sl.addWidget(self.clear_scope)
        guide = QFrame()
        guide.setStyleSheet(f"QFrame {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 8px; }}")
        gl = hbox(guide, (14, 8, 14, 8), 16)
        gl.addWidget(Badge("Audit 1: SEO Technical Audit (C1)", C["info"], C["info_bg"]))
        gl.addWidget(label("Crawls technical SEO, speed & security → PDF report + score", "Meta"))
        gl.addWidget(Badge("Campaign 2: No-Website Phone Pitch (C2)", C["warning"], C["warning_bg"]))
        gl.addWidget(label("Phone call log & script for businesses with no website", "Meta"))
        gl.addWidget(Badge("Audit 2: Website Service Audit (C3)", C["success"], C["success_bg"]))
        gl.addWidget(label("Extracts site services → LLM compares with your agency → Match score & pitch", "Meta"), 1)
        ml.addWidget(guide)
        ml.addWidget(self.summary)

        self.bulk = QFrame()
        self.bulk.setObjectName("BulkBar")
        self.bulk.setStyleSheet(f"QFrame#BulkBar {{ background: {C['ink']}; border-radius: 8px; }}")
        bl = hbox(self.bulk, (14, 6, 8, 6), 8)
        self.bulk_label = label("")
        self.bulk_label.setStyleSheet("color: #FFFFFF; font-weight: 600;")
        bl.addWidget(self.bulk_label)
        self.select_all_btn = button("", "ghost", size="sm")
        self.select_all_btn.setStyleSheet("QPushButton { color: #9DB8FF; } QPushButton:hover { background: #3F3F46; }")
        self.select_all_btn.clicked.connect(self._toggle_all_matching)
        bl.addWidget(self.select_all_btn)
        bl.addStretch(1)
        self.bulk_buttons = {}
        for key, text in (("audit", "Audit 1: Technical SEO Audit (C1)"), ("service_audit", "Audit 2: Website Service Audit (C3)"),
                          ("pitch", "C3: Compare & Score Pitches"), ("archive", "Archive"), ("restore", "Restore"), ("delete", "Delete")):
            b = button(text, "ghost", size="sm")
            b.setStyleSheet("QPushButton { color: #FFFFFF; } QPushButton:hover { background: #3F3F46; color: #FFFFFF; }"
                            "QPushButton:disabled { color: #71717A; }")
            b.clicked.connect(getattr(self, f"bulk_{key}"))
            self.bulk_buttons[key] = b
            bl.addWidget(b)
        clear = button("Clear", "ghost", size="sm")
        clear.setStyleSheet("QPushButton { color: #A1A1AA; } QPushButton:hover { background: #3F3F46; color: #FFFFFF; }")
        clear.clicked.connect(self.clear_selection)
        bl.addWidget(clear)
        self.bulk.setVisible(False)
        ml.addWidget(self.bulk)

        cols = [Column("_check", "", "check", 40)]
        cols += [
            Column("company", "Company", "strong", 180, sort="company"),
            Column("website", "Domain / Website (Double-click to Edit)", "text", 210, sort="website",
                   render=lambda r: r.get("normalized_domain") or r.get("website") or "—",
                   secondary=lambda r: "Double-click to edit domain" if (r.get("normalized_domain") or r.get("website")) else "Double-click to add domain"),
            Column("niche", "Niche", "text", 130, sort="niche", render=lambda r: r.get("niche") or "—"),
            Column("area", "Area / City (Double-click to Edit)", "text", 150, sort="area", render=lambda r: r.get("area") or "—",
                   secondary=lambda r: "Double-click to edit area" if r.get("area") else ""),
            Column("rating", "Google", "text", 95, sort="rating", optional=True,
                   render=lambda r: f"{r['rating']:.1f} rating" if r.get("rating") is not None else "—",
                   secondary=lambda r: f"{r['review_count']:,} reviews" if r.get("review_count") is not None else ""),
            Column("status", "Status", "status", 100, sort="status"),
            Column("call_status", "Campaign 2: Phone Call", "text", 145,
                   render=lambda r: (r.get("call_status") or "not_called").replace("_", " ").title() + (f" ({r['call_count']}x)" if r.get("call_count") else ""),
                   secondary=lambda r: r.get("phone") or "No phone"),
            Column("last_audit_score", "Audit 1: SEO Audit (C1)", "text", 155, sort="score", render=lambda r: "No website (C2)" if not r.get("has_website", bool(r.get("website"))) else (f"SEO Audited · {r['last_audit_score']}/100" if r.get("last_audit_score") is not None else ("SEO Audited" if r.get("last_audit_id") else "Not SEO Audited")), secondary=lambda r: f"PDF Ready · {fmt.date(r['last_audit_at'])}" if r.get("last_audit_at") else ("Technical SEO & Speed" if r.get("has_website", bool(r.get("website"))) else "")),
            Column("service_match_score", "Audit 2: Service Audit (C3)", "text", 175, sort="service_score",
                   render=lambda r: "No website (C2)" if not r.get("has_website", bool(r.get("website"))) else (f"Service Audited · {r['service_match_score']}/100" if r.get("service_match_score") is not None else ("Services Extracted" if r.get("services_audit_date") else "Not Service Audited")), secondary=lambda r: (", ".join((r.get("services_list") or [])[:3]) if r.get("services_list") else ("Offerings & LLM Match" if r.get("has_website", bool(r.get("website"))) else ""))),
            Column("last_contact_at", "Outreach", "text", 120, sort="last_contact", optional=True,
                   render=lambda r: "Contacted" if r.get("last_contact_at") else "Not contacted",
                   secondary=lambda r: fmt.relative(r["last_contact_at"]) if r.get("last_contact_at") else ""),
            Column("created_at", "Added", "date", 100, sort="created", optional=True),
            Column("_actions", "", "actions", 44),
        ]
        self.table = DataTable(cols, page_sizes=(50, 100, 200, 25))
        self.table.set_sort_indicator("created", "desc")
        self.table.sort_changed.connect(lambda k, o: self._set(sort=k, order=o))
        self.table.page_changed.connect(lambda p: self._set(page=p, keep_page=True))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.cell_double_clicked.connect(self._on_cell_double_clicked)
        self.table.context_requested.connect(self.row_menu)
        self.table.selection_changed.connect(self._selection)
        self.area = StatefulArea(_surface(self.table))
        self.area.retry.connect(self.load)
        ml.addWidget(self.area, 1)
        split.addWidget(main)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 1000])
        lv.addWidget(split)
        self.views.addWidget(leads_view)
        self.imports = ImportsView(self) if self.manage else None
        if self.imports:
            self.views.addWidget(self.imports)

    # -- lifecycle -----------------------------------------------------------
    def activate(self, create=False, niche=None, area=None, batch_id=None, tab=None, **_):
        if self.options is None and self.manage:
            load_assignees(self, self._options_loaded)
        if niche is not None or area is not None:
            self.params.update(niche=niche or "", area=area or "", page=1)
            self.nav.select(niche, area)
            self.area_filter.blockSignals(True)
            idx = self.area_filter.findData(area or "")
            self.area_filter.setCurrentIndex(max(0, idx))
            self.area_filter.blockSignals(False)
        if batch_id:
            self.params.update(batch_id=batch_id, page=1)
        if tab:
            self.tabs.set_value(tab)
            self._switch_tab(tab)
        elif self.views.currentIndex() == 1 and self.imports:
            self.imports.load()
        else:
            self.load()
        if create:
            self.create()

    def _switch_tab(self, tab):
        self.views.setCurrentIndex(1 if tab == "imports" and self.imports else 0)
        if tab == "imports" and self.imports:
            self.imports.load()
        else:
            self.load()

    def _options_loaded(self, opts):
        self.options = opts
        self.agent.blockSignals(True)
        self.agent.clear()
        self.agent.addItem("Anyone", "")
        for p in opts.get("people", []):
            self.agent.addItem(p["name"], p["id"])
        self.agent.blockSignals(False)

    # -- filters -------------------------------------------------------------
    def _filter_params(self):
        return {k: self.params[k] for k in FILTER_KEYS if self.params.get(k) not in ("", None)}

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
            self.clear_selection()
        self.load()

    def _quick(self, value):
        for k in ("assigned", "audited", "outreach", "has_website", "call_status", "service_audited"):
            self.params[k] = ""
        kv = dict(QUICK_VIEWS.get(value, {}))
        if not self.manage and value != "everyone":
            kv.setdefault("assigned", "mine")
        self._set(**kv)

    def _navigate_tree(self, niche, area):
        self.area_filter.blockSignals(True)
        idx = self.area_filter.findData(area or "")
        self.area_filter.setCurrentIndex(max(0, idx))
        self.area_filter.blockSignals(False)
        self._set(niche=niche, area=area)

    def show_batch_leads(self, batch):
        self.batch_label = batch.get("file_name", "")
        self.tabs.set_value("leads")
        self.views.setCurrentIndex(0)
        self.params.update(niche="", area="", status="")
        self.nav.select("", "")
        self.area_filter.blockSignals(True)
        self.area_filter.setCurrentIndex(0)
        self.area_filter.blockSignals(False)
        self._set(batch_id=batch["id"])

    def reset_filters(self):
        for k in FILTER_KEYS:
            self.params[k] = ""
        if not self.manage:
            self.params["assigned"] = "mine"
        self.batch_label = ""
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        for box in (self.status, self.area_filter, self.agent):
            box.blockSignals(True)
            box.setCurrentIndex(0)
            box.blockSignals(False)
        self.quick.set_value("")
        self.nav.select("", "")
        self._set()

    # -- loading -------------------------------------------------------------
    def load(self):
        flt = self._filter_params()
        params = dict(flt, page=self.params["page"], page_size=self.params["page_size"], sort=self.params["sort"],
                      order=self.params["order"])
        if not self.table.model.rows:
            self.area.loading("Loading leads…")
        self.fetch("list", api.get, "/api/leads", params=params, on_done=self._render, on_error=self.area.error)
        self.fetch("summary", api.get, "/api/leads/summary", params=flt, on_done=self._render_summary,
                   on_error=lambda _e: None)
        team = self.params.get("team_id")
        facet_params = {"team_id": team} if team and team != "none" else {}
        self.fetch("facets", api.get, "/api/leads/facets", params=facet_params, on_done=self._render_facets,
                   on_error=lambda _e: None)
        self._describe_scope()

    def _render(self, data):
        self.total = data.get("total", 0)
        self.table.set_page(data)
        self._selection(self.table.selected_ids())
        if not data["items"]:
            if self._filter_params():
                self.area.empty("No leads match", "Change the filters or clear them to see the whole lead bank.",
                                "Clear filters", icon_name="filter", action_icon="", on_action=self.reset_filters)
            elif self.manage:
                self.area.empty("Your lead bank is empty", "Import scraped CSV or Excel files (one niche in one area "
                                "per file) or add a lead manually.", "Import files", icon_name="upload",
                                action_icon="upload", on_action=self.import_files)
            else:
                self.area.empty("No leads assigned to you yet", "Leads appear here when a Manager or your Team Lead "
                                "assigns them to you.", icon_name="leads")
            return
        self.area.ready()

    def _render_summary(self, s):
        for key, lbl in self.sum_labels.items():
            lbl.setText(fmt.number(s.get(key, 0)))

    def _render_facets(self, facets):
        self.nav.set_data(facets)
        self._describe_scope()
        current_area = self.params.get("area") or ""
        self.area_filter.blockSignals(True)
        self.area_filter.clear()
        self.area_filter.addItem("All areas", "")
        for a in facets.get("areas", []):
            label_text = f"{a['label']} ({a['count']:,})"
            self.area_filter.addItem(label_text, a["key"])
        idx = self.area_filter.findData(current_area)
        self.area_filter.setCurrentIndex(max(0, idx))
        self.area_filter.blockSignals(False)
        if self.manage:
            self.head.subtitle.setText(f"{facets.get('total', 0):,} active leads · {len(facets.get('niches', []))} "
                                       f"niches · {len(facets.get('areas', []))} areas · "
                                       f"{facets.get('unassigned', 0):,} unassigned")
            self.head.subtitle.setVisible(True)

    def _describe_scope(self):
        parts = []
        if self.params.get("batch_id"):
            parts.append(f"File: {self.batch_label or 'selected import'}")
        if self.params.get("niche") or self.params.get("area"):
            parts.append(self.nav.label_for(self.params.get("niche") or "", self.params.get("area") or ""))
        self.where.setText(" · ".join(parts) if parts else ("Whole lead bank" if self.manage else "All your leads"))
        self.clear_scope.setVisible(bool(self._filter_params()))

    # -- selection -----------------------------------------------------------
    def _selection(self, ids):
        page_rows = len(self.table.model.rows)
        if not ids:
            self.all_matching = False
        self.bulk.setVisible(bool(ids))
        if self.all_matching:
            self.bulk_label.setText(f"All {self.total:,} matching leads selected")
            self.select_all_btn.setText("Select only this page")
        else:
            self.bulk_label.setText(f"{len(ids)} selected on this page")
            self.select_all_btn.setText(f"Select all {self.total:,} matching")
        self.select_all_btn.setVisible(bool(ids) and (self.all_matching or (len(ids) == page_rows and self.total > page_rows)))
        archived_view = self.params.get("status") == "archived"
        # A Team Lead can see a Manager's leads from other teams but cannot assign or archive them.
        manageable = self.manage and (self.all_matching or session.is_manager()
                                      or all(can_manage_lead(r) for r in self.table.selected_rows()))
        self.bulk_buttons["audit"].setVisible(not archived_view)
        self.bulk_buttons["archive"].setVisible(manageable and not archived_view)
        self.bulk_buttons["restore"].setVisible(manageable and archived_view)
        if "assign" in self.bulk_buttons: self.bulk_buttons["assign"].setVisible(manageable and not archived_view)

    def _toggle_all_matching(self):
        self.all_matching = not self.all_matching
        self._selection(self.table.selected_ids())

    def clear_selection(self):
        self.all_matching = False
        self.table.clear_selection()

    def _selection_arg(self):
        if self.all_matching:
            return {"filter": self._filter_params(), "count": self.total, "label": self.where.text()}
        return None

    # -- actions -------------------------------------------------------------
    def _with_options(self, fn):
        if self.options is None:
            load_assignees(self, lambda o: (self._options_loaded(o), fn(o)))
        else:
            fn(self.options)

    def create(self):
        def open_dialog(opts=None):
            dlg = LeadFormDialog(self, options=opts)
            if dlg.exec():
                toast(self, f"{dlg.created['company']} added.", "success")
                self.load()
            elif dlg.existing_lead_id:
                self.ws.navigate("lead", lead_id=dlg.existing_lead_id)
        if session.is_agent():
            open_dialog()
        else:
            self._with_options(open_dialog)

    def import_files(self):
        from seo_audit_pro.ui.pages.lead_import_dialog import open_import

        def started(batches):
            toast(self, f"Importing {len(batches)} file(s). Progress is shown under Imports.", "success")
            self.tabs.set_value("imports")
            self._switch_tab("imports")
        self._with_options(lambda opts: open_import(self, opts, started))

    def export(self):
        default = os.path.join(settings.downloads_dir(), "lead-bank-export.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export leads", default, "CSV (*.csv)")
        if not path:
            return
        toast(self, "Exporting the current selection…", "info")
        tasks.run(api.save_as, "/api/leads/export", path, params=self._filter_params(),
                  on_done=lambda p: toast(self, f"Exported to {os.path.basename(p)}", "success"),
                  on_error=lambda e: show_error(self, e), owner=self)

    def _assign(self, leads=None, selection=None):
        def go(opts):
            dlg = AssignDialog(self, opts, leads=leads, selection=selection)
            if dlg.exec():
                res = dlg.result or {}
                n = res.get("updated")
                toast(self, f"Assigned {n:,} lead(s)." if n is not None else "Assignment updated.", "success")
                self.clear_selection()
                self.load()
        self._with_options(go)

    def bulk_assign(self):
        sel = self._selection_arg()
        self._assign(leads=None if sel else self.table.selected_rows(), selection=sel)

    def bulk_audit(self):
        from seo_audit_pro.ui.pages.audit_dialogs import BulkAuditDialog
        if self.all_matching:
            sel = {"filter": self._filter_params(), "label": self.where.text()}
        else:
            ids = self.table.selected_ids()
            if not ids:
                return
            rows = self.table.selected_rows()
            sel = {"lead_ids": ids,
                   "label": rows[0]["company"] if len(rows) == 1 else f"{len(ids)} selected leads · {self.where.text()}"}
        dlg = BulkAuditDialog(self, sel)
        if dlg.exec() and dlg.result:
            b = dlg.result
            toast(self, f"{b['total']:,} audit(s) queued. Follow them under Audits › Queue.", "success")
            self.clear_selection()
            self.ws.navigate("audits", view="queue")

    def _mark_outreach(self, row):
        from seo_audit_pro.ui.pages.outreach import mark_outreach
        tasks.run(api.get, f"/api/leads/{row['id']}",
                  on_done=lambda r: mark_outreach(self, r["lead"], on_saved=lambda _l: self.load()),
                  on_error=lambda e: show_error(self, e, "Lead"), owner=self)

    def bulk_archive(self):
        self._archive(True)

    def bulk_restore(self):
        self._archive(False)

    def bulk_delete(self):
        sel = self._selection_arg()
        delete_leads(self, lambda: (self.clear_selection(), self.load()),
                     leads=None if sel else self.table.selected_rows(), selection=sel)

    def bulk_delete(self):
        sel = self._selection_arg()
        delete_leads(self, lambda: (self.clear_selection(), self.load()),
                     leads=None if sel else self.table.selected_rows(), selection=sel)
        self._archive(False)

    def _archive(self, archived):
        sel = self._selection_arg()
        set_archived(self, archived, lambda: (self.clear_selection(), self.load()),
                     leads=None if sel else self.table.selected_rows(), selection=sel)

    def _on_cell_double_clicked(self, row, col_key):
        if col_key in ("website", "normalized_domain"):
            self._quick_edit_field(row, "website", "Edit Domain / Website", "Website or Domain URL:", "example.com")
        elif col_key in ("area", "location"):
            self._quick_edit_field(row, "area", "Edit Area / City", "Area, City or Region:", "e.g. Chicago IL")
        else:
            self.ws.navigate("lead", lead_id=row["id"])

    def _quick_edit_field(self, row, field, title, label_text, placeholder=""):
        cur_val = row.get(field) or ""
        dlg = QuickEditDialog(self, title, label_text, cur_val, placeholder)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_val = dlg.value()
        if new_val == cur_val:
            return
        payload = {field: new_val}
        if "version" in row:
            payload["version"] = row["version"]

        def _on_done(res):
            lead_data = res.get("lead", {})
            row.update(lead_data)
            row[field] = new_val
            if field == "website":
                row["normalized_domain"] = lead_data.get("normalized_domain") or new_val
                row["has_website"] = bool(new_val)
            self.table.model.layoutChanged.emit()
            toast(self, f"Updated {title.lower()} successfully!", "success")

        tasks.run(api.patch, f"/api/leads/{row['id']}", payload,
                  on_done=_on_done, on_error=lambda e: show_error(self, e), owner=self)

    def row_menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("external", C["text_2"]), "Open", lambda: self.ws.navigate("lead", lead_id=row["id"]))
        m.addAction(icons.icon("globe", C["text_2"]), "Edit Domain / Website",
                    lambda: self._quick_edit_field(row, "website", "Edit Domain / Website", "Website or Domain URL:", "example.com"))
        m.addAction(icons.icon("map_pin", C["text_2"]), "Edit Area / City",
                    lambda: self._quick_edit_field(row, "area", "Edit Area / City", "Area, City or Region:", "e.g. Chicago IL"))
        if row["status"] != "archived":
            if row.get("last_audit_id"):
                m.addAction(icons.icon("report", C["text_2"]), "Audit 1: View latest SEO audit (C1)",
                            lambda: self.ws.navigate("audit", audit_id=row["last_audit_id"]))
            if row.get("website"):
                m.addAction(icons.icon("audit", C["text_2"]), "Audit 1: Re-run SEO Technical Audit (C1)" if row.get("last_audit_id") else "Audit 1: Start SEO Technical Audit (C1)",
                        lambda: start_audit(self, row, lambda _a: self.load()))
            m.addAction(icons.icon("mail", C["text_2"]), "Write email",
                        lambda: self.ws.navigate("outreach", lead_id=row["id"]))
            m.addAction(icons.icon("check", C["text_2"]), "Campaign 2: Log Phone Call & Website Pitch",
                        lambda: self._log_call(row))
            if row.get("website"):
                m.addAction(icons.icon("zap", C["text_2"]), "Audit 2: Run Website Service Audit & Pitch (C3)",
                            lambda: self._audit_services(row))
            m.addAction(icons.icon("check", C["text_2"]),
                        "Update outreach…" if row.get("last_contact_at") else "Mark outreached…",
                        lambda: self._mark_outreach(row))
        if self.manage and can_manage_lead(row):
            m.addSeparator()
            if row["status"] != "archived":
                m.addAction(icons.icon("user_check", C["text_2"]), "Assign…", lambda: self._assign(leads=[row]))
                m.addAction(icons.icon("archive", C["text_2"]), "Archive",
                            lambda: set_archived(self, True, self.load, leads=[row]))
            else:
                m.addAction(icons.icon("restore", C["text_2"]), "Restore",
                            lambda: set_archived(self, False, self.load, leads=[row]))
        m.addSeparator()
        m.addAction(icons.icon("trash", C["danger"]), "Delete lead…",
                    lambda: delete_leads(self, self.load, leads=[row]))
        m.exec(pos)

    def on_realtime(self, ev):
        kind = ev.get("type")
        if kind == "import_progress" and self.imports:
            self.imports.on_progress(ev["batch"])
            if ev["batch"]["status"] == "completed" and self.isVisible():
                self.load()
        elif kind == "audit_progress" and ev.get("status") == "completed" and self.isVisible():
            self.load()


    def _log_call(self, row):
        from seo_audit_pro.ui.pages.lead_dialogs import CallLogDialog
        dlg = CallLogDialog(self, row)
        if dlg.exec():
            self.load()
            toast(self, "Call log and pitch saved.", "success")

    def _audit_services(self, row):
        if not row.get("website"):
            toast(self, "This lead does not have a website to audit.", "warning")
            return
        toast(self, "Auditing website services & detecting opportunities…", "info", 4000)
        def on_done(res):
            toast(self, "Services audited! Opening outreach pitch…", "success")
            self.ws.navigate("outreach", lead_id=row["id"], campaign="service_pitch")
        tasks.run(api.post, f"/api/leads/{row['id']}/service-audit", {}, timeout=90, on_done=on_done, on_error=lambda err: show_error(self, err), owner=self)


    def clean_duplicates(self):
        def _done(res):
            n = int(res.get("deleted") or 0)
            toast(self, f"Removed {n:,} duplicate lead(s)." if n else "No duplicate leads found.", "success")
            self.load()
        tasks.run(api.post, "/api/leads/deduplicate", {}, on_done=_done, on_error=lambda e: show_error(self, e), owner=self)

    def bulk_service_audit(self):
        ids = self.table.selected_ids()
        if not ids:
            return
        toast(self, f"Auditing services for {len(ids)} selected lead(s)…", "info", 5000)
        def on_done(res):
            succeeded = res.get("succeeded", sum(1 for r in res.get("results", []) if r.get("ok")))
            total = res.get("total", len(ids))
            toast(self, f"Service audits completed: {succeeded}/{total} website(s) analyzed!", "success")
            self.clear_selection()
            self.load()
        payload = {"filter": self._filter_params()} if self.all_matching else {"lead_ids": ids}
        tasks.run(api.post, "/api/leads/mass-service-audit", payload, timeout=180, on_done=on_done, on_error=lambda err: show_error(self, err), owner=self)

    def bulk_pitch(self):
        ids = self.table.selected_ids()
        if not ids:
            return
        toast(self, f"Comparing services & generating pitches for {len(ids)} lead(s)…", "info", 5000)
        def on_done(res):
            succeeded = res.get("succeeded", sum(1 for r in res.get("results", []) if r.get("ok")))
            total = res.get("total", len(ids))
            toast(self, f"Service pitches generated: {succeeded}/{total} lead(s)!", "success")
            self.clear_selection()
            self.load()
        payload = {"filter": self._filter_params()} if self.all_matching else {"lead_ids": ids}
        tasks.run(api.post, "/api/leads/mass-service-pitch", payload, timeout=180, on_done=on_done, on_error=lambda err: show_error(self, err), owner=self)
