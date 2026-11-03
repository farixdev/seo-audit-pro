# -*- coding: utf-8 -*-
"""Scrape Planner & Matrix (Kanban Board for Countries -> Areas -> Niches)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget, QDialog
)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Badge, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Field, SearchBox, combo, confirm, line_edit, show_error, text_area, toast


# ── Dialogs ───────────────────────────────────────────────────────────────────

class CountryDialog(Dialog):
    def __init__(self, parent, country=None):
        super().__init__(parent, "Edit Country" if country else "Add Country", "", 420)
        self.country = country or {}
        self.name_in = line_edit("e.g. United States", self.country.get("name", ""))
        self.code_in = line_edit("e.g. US, UK, CA", self.country.get("code", ""))
        self.body.addWidget(Field("Country Name", self.name_in))
        self.body.addWidget(Field("Country Code (optional)", self.code_in))
        self.add_button("Cancel")
        self.save_btn = self.add_button("Save Country", "primary")
        self.save_btn.clicked.connect(self.accept)
        self.name_in.returnPressed.connect(self.accept)

    def values(self):
        return {
            "name": self.name_in.text().strip(),
            "code": self.code_in.text().strip().upper(),
        }


class AreaDialog(Dialog):
    def __init__(self, parent, countries, area=None, default_country_id=None):
        super().__init__(parent, "Edit Area / City" if area else "Add Area / City", "", 440)
        self.area = area or {}
        c_items = [(c["name"], c["id"]) for c in countries]
        sel_cid = self.area.get("country_id") or default_country_id or (c_items[0][1] if c_items else None)
        self.country_combo = combo(c_items, sel_cid)
        self.name_in = line_edit("e.g. Austin, TX or London", self.area.get("name", ""))
        self.body.addWidget(Field("Country", self.country_combo))
        self.body.addWidget(Field("Area / City / Region Name", self.name_in))
        self.add_button("Cancel")
        self.save_btn = self.add_button("Save Area", "primary")
        self.save_btn.clicked.connect(self.accept)
        self.name_in.returnPressed.connect(self.accept)

    def values(self):
        return {
            "country_id": self.country_combo.currentData(),
            "name": self.name_in.text().strip(),
        }


class TargetDialog(Dialog):
    def __init__(self, parent, countries, areas, target=None, default_country_id=None, default_area_id=None, default_status="planned"):
        super().__init__(parent, "Edit Scraping Target" if target else "Add Niche Scraping Target", "", 480)
        self.target = target or {}
        self.countries = countries
        self.areas = areas

        # Country & Area selector
        c_items = [(c["name"], c["id"]) for c in countries]
        sel_cid = self.target.get("country_id") or default_country_id or (c_items[0][1] if c_items else "")
        self.c_combo = combo(c_items, sel_cid)

        self.a_combo = combo([])
        self._populate_areas(sel_cid, self.target.get("area_id") or default_area_id)
        self.c_combo.currentIndexChanged.connect(lambda _i: self._populate_areas(self.c_combo.currentData()))

        self.niche_in = line_edit("e.g. Dentists, Roofing Contractors, Plumbers", self.target.get("niche", ""))
        status_items = [("To Scrape (Planned)", "planned"), ("Currently Scraping", "scraping"), ("Harvested (Completed)", "completed")]
        self.status_combo = combo(status_items, self.target.get("status") or default_status)
        self.notes_in = text_area("Special instructions, target keywords, or notes", self.target.get("notes", ""), 70)

        self.body.addWidget(Field("Country", self.c_combo))
        self.body.addWidget(Field("Area / City", self.a_combo))
        self.body.addWidget(Field("Niche / Category to Harvest", self.niche_in))
        self.body.addWidget(Field("Status", self.status_combo))
        self.body.addWidget(Field("Notes (optional)", self.notes_in))

        self.add_button("Cancel")
        self.save_btn = self.add_button("Save Target", "primary")
        self.save_btn.clicked.connect(self.accept)
        self.niche_in.returnPressed.connect(self.accept)

    def _populate_areas(self, cid, select_aid=None):
        self.a_combo.clear()
        matching = [a for a in self.areas if not cid or a["country_id"] == cid]
        for a in matching:
            self.a_combo.addItem(a["name"], a["id"])
        if select_aid:
            idx = self.a_combo.findData(select_aid)
            if idx >= 0:
                self.a_combo.setCurrentIndex(idx)

    def values(self):
        return {
            "area_id": self.a_combo.currentData(),
            "niche": self.niche_in.text().strip(),
            "status": self.status_combo.currentData(),
            "notes": self.notes_in.toPlainText().strip(),
        }


# ── Kanban Card Widget ────────────────────────────────────────────────────────

class KanbanCard(QFrame):
    scrape_requested = Signal(dict)
    status_changed = Signal(dict, str)
    edit_requested = Signal(dict)
    delete_requested = Signal(dict)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target
        self.setObjectName("KanbanCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            #KanbanCard {{
                background: {C["surface"]};
                border: 1px solid {C["border"]};
                border-radius: 8px;
            }}
            #KanbanCard:hover {{
                border-color: {C["border_strong"]};
                background: #27272A;
            }}
        """)

        lay = vbox(self, (12, 12, 12, 12), 8)

        # Header: Niche Name + Action dots
        top = hbox(spacing=6)
        niche_lbl = QLabel(target["niche"])
        niche_lbl.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {C['text']};")
        niche_lbl.setWordWrap(True)
        top.addWidget(niche_lbl, 1)

        menu_btn = button("", "ghost", icon_name="more", size="sm")
        menu_btn.setFixedSize(26, 26)
        menu_btn.clicked.connect(self._open_menu)
        top.addWidget(menu_btn)
        lay.addLayout(top)

        # Location row: Area · Country
        loc_row = hbox(spacing=6)
        pin_icon = QLabel()
        pin_icon.setPixmap(icons.pixmap("map_pin", C["text_3"], 14))
        loc_row.addWidget(pin_icon)
        loc_text = f"{target.get('area_name', 'Unknown Area')}, {target.get('country_name', 'Unknown Country')}"
        loc_lbl = QLabel(loc_text)
        loc_lbl.setStyleSheet(f"font-size: 12px; color: {C['text_2']}; font-weight: 500;")
        loc_lbl.setWordWrap(True)
        loc_row.addWidget(loc_lbl, 1)
        lay.addLayout(loc_row)

        # Stats & Notes snippet
        if target.get("notes"):
            note_lbl = QLabel(target["notes"])
            note_lbl.setStyleSheet(f"font-size: 11px; color: {C['text_3']}; font-style: italic;")
            note_lbl.setWordWrap(True)
            lay.addWidget(note_lbl)

        # Bottom row: Leads count + Launch Scrape button
        bot = hbox(spacing=8)
        harvested = target.get("leads_harvested", 0)
        leads_chip = QLabel(f"👥 {harvested} leads" if harvested else "0 leads")
        leads_chip.setStyleSheet(f"font-size: 11px; color: {C['text_3']}; padding: 2px 6px; background: {C['bg']}; border-radius: 4px;")
        bot.addWidget(leads_chip)
        bot.addStretch(1)

        # Quick action: Launch into Map Harvest
        scrape_btn = button("Scrape in Maps", "primary", icon_name="map_pin", size="sm")
        scrape_btn.setFixedHeight(28)
        scrape_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C["primary"]};
                color: #FFFFFF;
                font-weight: 600;
                font-size: 11px;
                padding: 4px 10px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: #2563EB;
            }}
        """)
        scrape_btn.clicked.connect(lambda: self.scrape_requested.emit(self.target))
        bot.addWidget(scrape_btn)
        lay.addLayout(bot)

    def _open_menu(self):
        m = QMenu(self)
        status = self.target.get("status", "planned")

        m.addAction(icons.icon("map_pin", C["primary"]), "Launch Scrape in Map Harvest",
                    lambda: self.scrape_requested.emit(self.target))
        m.addSeparator()

        if status != "planned":
            m.addAction(icons.icon("clock", C["text_2"]), "Move to To Scrape (Planned)",
                        lambda: self.status_changed.emit(self.target, "planned"))
        if status != "scraping":
            m.addAction(icons.icon("play", "#F59E0B"), "Move to Currently Scraping",
                        lambda: self.status_changed.emit(self.target, "scraping"))
        if status != "completed":
            m.addAction(icons.icon("check", "#10B981"), "Move to Harvested (Completed)",
                        lambda: self.status_changed.emit(self.target, "completed"))

        m.addSeparator()
        m.addAction(icons.icon("edit", C["text_2"]), "Edit Target…", lambda: self.edit_requested.emit(self.target))
        m.addAction(icons.icon("trash", C["danger"]), "Delete Target", lambda: self.delete_requested.emit(self.target))
        m.exec(self.mapToGlobal(self.rect().bottomRight()))


# ── Kanban Column Widget ──────────────────────────────────────────────────────

class KanbanColumn(QFrame):
    target_add_requested = Signal(str)

    def __init__(self, key, title, color_accent, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("KanbanColumn")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            #KanbanColumn {{
                background: {C["neutral_bg"]};
                border: 1px solid {C["border"]};
                border-radius: 10px;
            }}
        """)

        col_lay = vbox(self, (12, 12, 12, 12), 10)

        # Column Header
        head = hbox(spacing=8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color_accent}; font-size: 14px;")
        head.addWidget(dot)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {C['text']}; letter-spacing: 0.5px;")
        head.addWidget(title_lbl)

        self.count_badge = QLabel("0")
        self.count_badge.setStyleSheet(f"""
            background: {C["surface"]};
            color: {C["text_2"]};
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 10px;
            border: 1px solid {C["border"]};
        """)
        head.addWidget(self.count_badge)
        head.addStretch(1)

        add_b = button("", "ghost", icon_name="plus", size="sm")
        add_b.setFixedSize(26, 26)
        add_b.setToolTip(f"Add niche to {title}")
        add_b.clicked.connect(lambda: self.target_add_requested.emit(self.key))
        head.addWidget(add_b)
        col_lay.addLayout(head)

        # Scrollable Cards Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent; border: none;")

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_lay = vbox(self.cards_container, (0, 4, 0, 4), 8)
        self.cards_lay.addStretch(1)
        self.scroll.setWidget(self.cards_container)
        col_lay.addWidget(self.scroll, 1)

    def set_cards(self, cards):
        # Clear existing
        while self.cards_lay.count() > 1:
            item = self.cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.count_badge.setText(str(len(cards)))
        for c in cards:
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, c)


# ── Main Scrape Planner Page ──────────────────────────────────────────────────

class ScrapePlannerPage(Page):
    title = "Scrape Planner"
    nav_key = "scrape_planner"

    def __init__(self, workspace):
        super().__init__(workspace, scroll=False, spacing=14)

        # Header
        h = self.header(
            "Scrape Planner & Matrix",
            "Plan and organize Google Maps harvesting by Country, Area, and Niche. One-click launch into Map Harvest."
        )
        self.add_target_btn = h.add_action(button("Add Niche Target", "primary", icon_name="plus"))
        self.add_target_btn.clicked.connect(self.create_target_modal)

        self.add_area_btn = h.add_action(button("Add Area", "ghost", icon_name="plus"))
        self.add_area_btn.clicked.connect(self.create_area_modal)

        self.add_country_btn = h.add_action(button("Add Country", "ghost", icon_name="plus"))
        self.add_country_btn.clicked.connect(self.create_country_modal)

        self.refresh_btn = h.add_action(button("", "ghost", icon_name="refresh", tooltip="Refresh board"))
        self.refresh_btn.clicked.connect(self.load)

        # Filter & stats bar
        bar = hbox(spacing=10)
        self.search = SearchBox("Search niches or cities…")
        self.search.setFixedWidth(240)
        self.search.search.connect(lambda _t: self.load())
        bar.addWidget(self.search)

        self.country_filter = combo([("All Countries", "")])
        self.country_filter.setFixedWidth(160)
        self.country_filter.currentIndexChanged.connect(self._on_country_filter_changed)
        bar.addWidget(self.country_filter)

        self.area_filter = combo([("All Areas", "")])
        self.area_filter.setFixedWidth(170)
        self.area_filter.currentIndexChanged.connect(lambda _i: self.load())
        bar.addWidget(self.area_filter)

        bar.addStretch(1)

        # Stat badges
        self.stat_total = Badge("Total: 0", C["text_2"], C["neutral_bg"])
        self.stat_planned = Badge("Planned: 0", "#3B82F6", "#1E293B")
        self.stat_scraping = Badge("Scraping: 0", "#F59E0B", "#2A2415")
        self.stat_completed = Badge("Completed: 0", "#10B981", "#132D21")
        for st in (self.stat_total, self.stat_planned, self.stat_scraping, self.stat_completed):
            bar.addWidget(st)

        self.body.addLayout(bar)

        # Kanban columns container
        board_widget = QWidget()
        board_lay = hbox(board_widget, (0, 0, 0, 0), 14)

        self.col_planned = KanbanColumn("planned", "TO SCRAPE", "#3B82F6")
        self.col_scraping = KanbanColumn("scraping", "CURRENTLY SCRAPING", "#F59E0B")
        self.col_completed = KanbanColumn("completed", "HARVESTED", "#10B981")

        for col in (self.col_planned, self.col_scraping, self.col_completed):
            col.target_add_requested.connect(lambda st: self.create_target_modal(default_status=st))
            board_lay.addWidget(col, 1)

        self.body.addWidget(board_widget, 1)

        self.countries = []
        self.areas = []
        self.targets = []

    def activate(self, **params):
        self.load()

    def load(self):
        cid = self.country_filter.currentData() if hasattr(self, "country_filter") else ""
        aid = self.area_filter.currentData() if hasattr(self, "area_filter") else ""
        q = self.search.text().strip() if hasattr(self, "search") else ""

        params = {}
        if cid:
            params["country_id"] = cid
        if aid:
            params["area_id"] = aid
        if q:
            params["search"] = q

        tasks.run(api.get, "/api/scrape-planner", params=params, on_done=self._render,
                  on_error=lambda e: show_error(self, e), owner=self)

    def _render(self, data):
        self.countries = data.get("countries", [])
        self.areas = data.get("areas", [])
        self.targets = data.get("targets", [])
        stats = data.get("stats", {})

        # Update stats badges
        self.stat_total.setText(f"Total: {stats.get('total', 0)}")
        self.stat_planned.setText(f"Planned: {stats.get('planned', 0)}")
        self.stat_scraping.setText(f"Scraping: {stats.get('scraping', 0)}")
        self.stat_completed.setText(f"Completed: {stats.get('completed', 0)}")

        # Update filter dropdowns if count changed
        cur_cid = self.country_filter.currentData()
        self.country_filter.blockSignals(True)
        self.country_filter.clear()
        self.country_filter.addItem("All Countries", "")
        for c in self.countries:
            self.country_filter.addItem(f"{c['name']}" + (f" ({c['code']})" if c.get("code") else ""), c["id"])
        idx = self.country_filter.findData(cur_cid or "")
        self.country_filter.setCurrentIndex(max(0, idx))
        self.country_filter.blockSignals(False)

        self._refresh_area_filter(cur_cid)

        # Distribute targets into Kanban columns
        cards_by_status = {"planned": [], "scraping": [], "completed": []}
        for t in self.targets:
            card = KanbanCard(t)
            card.scrape_requested.connect(self._launch_in_map_harvest)
            card.status_changed.connect(self._update_target_status)
            card.edit_requested.connect(self.edit_target_modal)
            card.delete_requested.connect(self._delete_target)
            st = t.get("status", "planned")
            if st in cards_by_status:
                cards_by_status[st].append(card)

        self.col_planned.set_cards(cards_by_status["planned"])
        self.col_scraping.set_cards(cards_by_status["scraping"])
        self.col_completed.set_cards(cards_by_status["completed"])

    def _on_country_filter_changed(self):
        cid = self.country_filter.currentData()
        self._refresh_area_filter(cid)
        self.load()

    def _refresh_area_filter(self, cid):
        cur_aid = self.area_filter.currentData()
        self.area_filter.blockSignals(True)
        self.area_filter.clear()
        self.area_filter.addItem("All Areas", "")
        matching = [a for a in self.areas if not cid or a["country_id"] == cid]
        for a in matching:
            self.area_filter.addItem(a["name"], a["id"])
        idx = self.area_filter.findData(cur_aid or "")
        self.area_filter.setCurrentIndex(max(0, idx))
        self.area_filter.blockSignals(False)

    # ── Map Harvest Integration ───────────────────────────────────────────────

    def _launch_in_map_harvest(self, target):
        niche = target["niche"]
        area = target.get("area_name", "")
        country = target.get("country_name", "")

        # Automatically mark target as scraping
        if target.get("status") == "planned":
            tasks.run(api.patch, f"/api/scrape-planner/targets/{target['id']}", {"status": "scraping"},
                      owner=self)

        toast(self, f"Pre-filling Map Harvest for {niche} in {area}, {country}…", "info", 3000)
        # Navigate to Map Harvest with pre-filled inputs
        self.ws.navigate(
            "map_harvest",
            niche=niche,
            area=area,
            country=country,
            target_id=target["id"]
        )

    def _update_target_status(self, target, new_status):
        tasks.run(api.patch, f"/api/scrape-planner/targets/{target['id']}", {"status": new_status},
                  on_done=lambda _r: (toast(self, f"Target moved to {new_status.title()}.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    # ── CRUD Modals ───────────────────────────────────────────────────────────

    def create_target_modal(self, default_status="planned"):
        if not self.countries:
            toast(self, "Add a country first before creating niche targets.", "warning")
            return
        cid = self.country_filter.currentData() or (self.countries[0]["id"] if self.countries else "")
        aid = self.area_filter.currentData() or ""
        dlg = TargetDialog(self, self.countries, self.areas, default_country_id=cid, default_area_id=aid, default_status=default_status)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("niche"):
                return
            tasks.run(api.post, "/api/scrape-planner/targets", vals,
                      on_done=lambda _r: (toast(self, "Target added to planner!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_target_modal(self, target):
        dlg = TargetDialog(self, self.countries, self.areas, target=target)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            tasks.run(api.patch, f"/api/scrape-planner/targets/{target['id']}", vals,
                      on_done=lambda _r: (toast(self, "Target updated successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def _delete_target(self, target):
        if not confirm(self, f"Delete target '{target['niche']}'?", "Are you sure you want to remove this niche target?", "Delete"):
            return
        tasks.run(api.delete, f"/api/scrape-planner/targets/{target['id']}",
                  on_done=lambda _r: (toast(self, "Target deleted.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def create_country_modal(self):
        dlg = CountryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("name"):
                return
            tasks.run(api.post, "/api/scrape-planner/countries", vals,
                      on_done=lambda _r: (toast(self, "Country added successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def create_area_modal(self):
        if not self.countries:
            toast(self, "Add at least one country before creating areas.", "warning")
            return
        cid = self.country_filter.currentData() or self.countries[0]["id"]
        dlg = AreaDialog(self, self.countries, default_country_id=cid)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("name"):
                return
            tasks.run(api.post, "/api/scrape-planner/areas", vals,
                      on_done=lambda _r: (toast(self, "Area added successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)
