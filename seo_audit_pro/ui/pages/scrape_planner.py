# -*- coding: utf-8 -*-
"""Scrape Planner & Matrix (Hierarchical Tree: Country -> Areas -> Niches with Scraped/Not Scraped filters)."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QDialog,
    QHeaderView
)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Badge, Segmented, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Field, SearchBox, combo, confirm, line_edit, show_error, text_area, toast


# ── CRUD Dialogs ─────────────────────────────────────────────────────────────

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
    def __init__(self, parent, countries, areas, target=None, default_country_id=None, default_area_id=None):
        super().__init__(parent, "Edit Scraping Target" if target else "Add Niche Scraping Target", "", 480)
        self.target = target or {}
        self.countries = countries
        self.areas = areas

        c_items = [(c["name"], c["id"]) for c in countries]
        sel_cid = self.target.get("country_id") or default_country_id or (c_items[0][1] if c_items else "")
        self.c_combo = combo(c_items, sel_cid)

        self.a_combo = combo([])
        self._populate_areas(sel_cid, self.target.get("area_id") or default_area_id)
        self.c_combo.currentIndexChanged.connect(lambda _i: self._populate_areas(self.c_combo.currentData()))

        self.niche_in = line_edit("e.g. Dentists, Roofing Contractors, Plumbers", self.target.get("niche", ""))
        is_scraped_initial = "yes" if bool(self.target.get("status") == "completed" or self.target.get("leads_harvested", 0) > 0) else "no"
        self.scraped_combo = combo([("Not Scraped (Pending)", "no"), ("Scraped (Harvested)", "yes")], is_scraped_initial)
        self.notes_in = text_area("Special keywords, notes or search modifiers", self.target.get("notes", ""), 70)

        self.body.addWidget(Field("Country", self.c_combo))
        self.body.addWidget(Field("Area / City", self.a_combo))
        self.body.addWidget(Field("Niche / Category to Scrape", self.niche_in))
        self.body.addWidget(Field("Scrape Status", self.scraped_combo))
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
        is_scraped = (self.scraped_combo.currentData() == "yes")
        return {
            "area_id": self.a_combo.currentData(),
            "niche": self.niche_in.text().strip(),
            "status": "completed" if is_scraped else "planned",
            "is_scraped": is_scraped,
            "notes": self.notes_in.toPlainText().strip(),
        }


# ── Tree View Page ────────────────────────────────────────────────────────────

class ScrapePlannerPage(Page):
    title = "Scrape Planner"
    nav_key = "scrape_planner"

    def __init__(self, workspace):
        super().__init__(workspace, scroll=False, spacing=14)

        # Header
        h = self.header(
            "Scrape Planner & Matrix",
            "Hierarchical matrix: Country ➔ Areas / Cities ➔ Niches to harvest. Track scraped status and launch into Map Harvest."
        )
        self.add_target_btn = h.add_action(button("Add Niche Target", "primary", icon_name="plus"))
        self.add_target_btn.clicked.connect(lambda: self.create_target_modal())

        self.add_area_btn = h.add_action(button("Add Area", "ghost", icon_name="plus"))
        self.add_area_btn.clicked.connect(lambda: self.create_area_modal())

        self.add_country_btn = h.add_action(button("Add Country", "ghost", icon_name="plus"))
        self.add_country_btn.clicked.connect(self.create_country_modal)

        self.expand_btn = h.add_action(button("Expand All", "ghost"))
        self.expand_btn.clicked.connect(lambda: self.tree.expandAll())

        self.collapse_btn = h.add_action(button("Collapse All", "ghost"))
        self.collapse_btn.clicked.connect(lambda: self.tree.collapseAll())

        self.refresh_btn = h.add_action(button("", "ghost", icon_name="refresh", tooltip="Refresh tree"))
        self.refresh_btn.clicked.connect(self.load)

        # Filter bar
        bar = hbox(spacing=12)
        self.search = SearchBox("Search country, city or niche…")
        self.search.setFixedWidth(280)
        self.search.search.connect(lambda _t: self.load())
        bar.addWidget(self.search)

        self.filter_segmented = Segmented([
            ("All Targets", ""),
            ("⏳ Not Scraped", "no"),
            ("✔ Scraped", "yes")
        ])
        self.filter_segmented.changed.connect(lambda _v: self.load())
        bar.addWidget(self.filter_segmented)

        bar.addStretch(1)

        self.badge_total = Badge("Total: 0", C["text_2"], C["neutral_bg"])
        self.badge_not_scraped = Badge("Not Scraped: 0", "#F59E0B", "#2A2415")
        self.badge_scraped = Badge("Scraped: 0", "#10B981", "#132D21")
        for b in (self.badge_total, self.badge_not_scraped, self.badge_scraped):
            bar.addWidget(b)

        self.body.addLayout(bar)

        # Tree Widget inside styled surface frame
        surface = QFrame()
        surface.setObjectName("Surface")
        surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surface_lay = vbox(surface, (0, 0, 0, 0), 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Country ➔ Area ➔ Niche", "Status", "Leads Harvested", "Quick Scrape", "Actions"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(24)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {C['surface']};
                border: none;
                color: {C['text']};
                font-size: 13px;
            }}
            QTreeWidget::item {{
                padding: 6px 4px;
                border-bottom: 1px solid {C['border']};
            }}
            QTreeWidget::item:hover {{
                background: #27272A;
            }}
            QTreeWidget::item:selected {{
                background: #3F3F46;
                color: #FFFFFF;
            }}
            QHeaderView::section {{
                background: {C['neutral_bg']};
                color: {C['text_2']};
                font-weight: 600;
                font-size: 12px;
                padding: 8px 10px;
                border: none;
                border-bottom: 1px solid {C['border']};
            }}
        """)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 140)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 160)
        self.tree.setColumnWidth(4, 140)

        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        surface_lay.addWidget(self.tree)
        self.body.addWidget(surface, 1)

        self.countries = []
        self.areas = []
        self.targets = []

    def activate(self, **params):
        self.load()

    def load(self):
        scraped = self.filter_segmented.value() if hasattr(self, "filter_segmented") else ""
        q = self.search.text().strip() if hasattr(self, "search") else ""

        params = {}
        if scraped:
            params["scraped"] = scraped
        if q:
            params["search"] = q

        tasks.run(api.get, "/api/scrape-planner", params=params, on_done=self._render,
                  on_error=lambda e: show_error(self, e), owner=self)

    def _render(self, data):
        self.tree.clear()
        self.countries = data.get("countries", [])
        self.areas = data.get("areas", [])
        self.targets = data.get("targets", [])
        stats = data.get("stats", {})

        # Update stats badges
        self.badge_total.setText(f"Total: {stats.get('total', 0)}")
        self.badge_scraped.setText(f"Scraped: {stats.get('scraped', 0)}")
        self.badge_not_scraped.setText(f"Not Scraped: {stats.get('not_scraped', 0)}")

        # Build Country -> Area -> Targets hierarchy
        areas_by_country = {}
        for a in self.areas:
            areas_by_country.setdefault(a["country_id"], []).append(a)

        targets_by_area = {}
        for t in self.targets:
            targets_by_area.setdefault(t["area_id"], []).append(t)

        for country in self.countries:
            cid = country["id"]
            country_areas = areas_by_country.get(cid, [])

            # Count total targets in this country
            c_targets = [t for a in country_areas for t in targets_by_area.get(a["id"], [])]
            if self.search.text().strip() and not country_areas and not c_targets:
                if self.search.text().lower() not in country["name"].lower():
                    continue

            # Level 0: Country Item
            c_item = QTreeWidgetItem(self.tree)
            c_text = f"🌍  {country['name']}" + (f" ({country['code']})" if country.get("code") else "")
            c_item.setText(0, c_text)
            c_font = QFont()
            c_font.setPointSize(11)
            c_font.setBold(True)
            c_item.setFont(0, c_font)
            c_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "country", "data": country})

            # Country Actions widget
            c_act_wrap = QWidget()
            c_act_lay = hbox(c_act_wrap, (0, 0, 0, 0), 4)
            add_a_btn = button("+ Area", "ghost", size="sm")
            add_a_btn.setFixedHeight(24)
            add_a_btn.clicked.connect(lambda _c=False, _cid=cid: self.create_area_modal(default_country_id=_cid))
            c_act_lay.addWidget(add_a_btn)

            edit_c_btn = button("", "ghost", icon_name="edit", size="sm")
            edit_c_btn.setFixedSize(24, 24)
            edit_c_btn.clicked.connect(lambda _c=False, _ct=country: self.edit_country_modal(_ct))
            c_act_lay.addWidget(edit_c_btn)

            del_c_btn = button("", "ghost", icon_name="trash", size="sm")
            del_c_btn.setFixedSize(24, 24)
            del_c_btn.clicked.connect(lambda _c=False, _ct=country: self.delete_country(_ct))
            c_act_lay.addWidget(del_c_btn)
            c_act_lay.addStretch(1)
            self.tree.setItemWidget(c_item, 4, c_act_wrap)

            # Level 1: Area Items
            for area in country_areas:
                aid = area["id"]
                a_targets = targets_by_area.get(aid, [])
                if self.search.text().strip() and not a_targets:
                    if self.search.text().lower() not in area["name"].lower():
                        continue

                a_item = QTreeWidgetItem(c_item)
                a_item.setText(0, f"📍  {area['name']}")
                a_font = QFont()
                a_font.setPointSize(10)
                a_font.setDemiBold(True)
                a_item.setFont(0, a_font)
                a_item.setForeground(0, QColor(C["text"]))
                a_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "area", "data": area, "country": country})

                # Area actions widget
                a_act_wrap = QWidget()
                a_act_lay = hbox(a_act_wrap, (0, 0, 0, 0), 4)
                add_n_btn = button("+ Niche", "ghost", size="sm")
                add_n_btn.setFixedHeight(24)
                add_n_btn.clicked.connect(lambda _c=False, _aid=aid, _cid=cid: self.create_target_modal(default_area_id=_aid, default_country_id=_cid))
                a_act_lay.addWidget(add_n_btn)

                edit_a_btn = button("", "ghost", icon_name="edit", size="sm")
                edit_a_btn.setFixedSize(24, 24)
                edit_a_btn.clicked.connect(lambda _c=False, _ar=area: self.edit_area_modal(_ar))
                a_act_lay.addWidget(edit_a_btn)

                del_a_btn = button("", "ghost", icon_name="trash", size="sm")
                del_a_btn.setFixedSize(24, 24)
                del_a_btn.clicked.connect(lambda _c=False, _ar=area: self.delete_area(_ar))
                a_act_lay.addWidget(del_a_btn)
                a_act_lay.addStretch(1)
                self.tree.setItemWidget(a_item, 4, a_act_wrap)

                # Level 2: Niche Targets
                for target in a_targets:
                    t_item = QTreeWidgetItem(a_item)
                    t_item.setText(0, f"🎯  {target['niche']}")
                    if target.get("notes"):
                        t_item.setToolTip(0, f"Notes: {target['notes']}")
                    t_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "target", "data": target, "area": area, "country": country})

                    # Column 1: Status Pill
                    is_scraped = target.get("is_scraped", False)
                    status_widget = QWidget()
                    s_lay = hbox(status_widget, (0, 0, 0, 0), 0)
                    if is_scraped:
                        pill = Badge("✔ Scraped", "#10B981", "#132D21")
                    else:
                        pill = Badge("⏳ Not Scraped", "#F59E0B", "#2A2415")
                    s_lay.addWidget(pill)
                    s_lay.addStretch(1)
                    self.tree.setItemWidget(t_item, 1, status_widget)

                    # Column 2: Leads Harvested
                    leads_count = target.get("leads_harvested", 0)
                    leads_lbl = QLabel(f"👥 {leads_count:,} leads" if leads_count else "—")
                    leads_lbl.setStyleSheet(f"color: {C['text_2'] if leads_count else C['muted']}; font-size: 12px; font-weight: {'600' if leads_count else 'normal'};")
                    self.tree.setItemWidget(t_item, 2, leads_lbl)

                    # Column 3: Quick Action Launch in Map Harvest
                    scrape_b = button("Scrape in Maps", "primary", icon_name="map_pin", size="sm")
                    scrape_b.setFixedHeight(26)
                    scrape_b.setStyleSheet(f"""
                        QPushButton {{
                            background: {C['primary']};
                            color: #FFFFFF;
                            font-size: 11px;
                            font-weight: 600;
                            border-radius: 4px;
                            padding: 2px 10px;
                        }}
                        QPushButton:hover {{
                            background: #2563EB;
                        }}
                    """)
                    scrape_b.clicked.connect(lambda _c=False, _t=target: self._launch_in_map_harvest(_t))
                    self.tree.setItemWidget(t_item, 3, scrape_b)

                    # Column 4: Actions (Toggle Scraped, Edit, Delete)
                    t_act_wrap = QWidget()
                    t_act_lay = hbox(t_act_wrap, (0, 0, 0, 0), 4)

                    toggle_btn = button("", "ghost", icon_name="check" if not is_scraped else "refresh", size="sm")
                    toggle_btn.setFixedSize(24, 24)
                    toggle_btn.setToolTip("Mark as Scraped" if not is_scraped else "Mark as Not Scraped")
                    toggle_btn.clicked.connect(lambda _c=False, _t=target, _val=not is_scraped: self._toggle_target_scraped(_t, _val))
                    t_act_lay.addWidget(toggle_btn)

                    edit_t_btn = button("", "ghost", icon_name="edit", size="sm")
                    edit_t_btn.setFixedSize(24, 24)
                    edit_t_btn.setToolTip("Edit Niche Target")
                    edit_t_btn.clicked.connect(lambda _c=False, _t=target: self.edit_target_modal(_t))
                    t_act_lay.addWidget(edit_t_btn)

                    del_t_btn = button("", "ghost", icon_name="trash", size="sm")
                    del_t_btn.setFixedSize(24, 24)
                    del_t_btn.setToolTip("Delete Target")
                    del_t_btn.clicked.connect(lambda _c=False, _t=target: self.delete_target(_t))
                    t_act_lay.addWidget(del_t_btn)
                    t_act_lay.addStretch(1)
                    self.tree.setItemWidget(t_item, 4, t_act_wrap)

        # Expand all nodes by default so the tree is immediately accessible
        self.tree.expandAll()

    # ── Map Harvest Integration ───────────────────────────────────────────────

    def _launch_in_map_harvest(self, target):
        niche = target["niche"]
        area = target.get("area_name", "")
        country = target.get("country_name", "")

        toast(self, f"Opening Map Harvest for {niche} in {area}, {country}…", "info", 3000)
        self.ws.navigate(
            "map_harvest",
            niche=niche,
            area=area,
            country=country,
            target_id=target["id"]
        )

    def _toggle_target_scraped(self, target, is_scraped):
        tasks.run(api.patch, f"/api/scrape-planner/targets/{target['id']}", {"is_scraped": is_scraped},
                  on_done=lambda _r: (toast(self, "Marked as Scraped." if is_scraped else "Marked as Not Scraped.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def _on_item_double_clicked(self, item, col):
        node = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = node.get("type")
        if kind == "target":
            self._launch_in_map_harvest(node["data"])
        elif kind == "area":
            self.create_target_modal(default_area_id=node["data"]["id"], default_country_id=node["country"]["id"])
        elif kind == "country":
            self.create_area_modal(default_country_id=node["data"]["id"])

    # ── CRUD Modals ───────────────────────────────────────────────────────────

    def create_country_modal(self):
        dlg = CountryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("name"):
                return
            tasks.run(api.post, "/api/scrape-planner/countries", vals,
                      on_done=lambda _r: (toast(self, "Country added successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_country_modal(self, country):
        dlg = CountryDialog(self, country=country)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            tasks.run(api.patch, f"/api/scrape-planner/countries/{country['id']}", vals,
                      on_done=lambda _r: (toast(self, "Country updated.", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def delete_country(self, country):
        if not confirm(self, f"Delete country '{country['name']}'?", "This will also remove all cities and niche targets within this country.", "Delete"):
            return
        tasks.run(api.delete, f"/api/scrape-planner/countries/{country['id']}",
                  on_done=lambda _r: (toast(self, "Country deleted.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def create_area_modal(self, default_country_id=None):
        if not self.countries:
            toast(self, "Add at least one country before creating areas.", "warning")
            return
        dlg = AreaDialog(self, self.countries, default_country_id=default_country_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("name"):
                return
            tasks.run(api.post, "/api/scrape-planner/areas", vals,
                      on_done=lambda _r: (toast(self, "Area added successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_area_modal(self, area):
        dlg = AreaDialog(self, self.countries, area=area)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            tasks.run(api.patch, f"/api/scrape-planner/areas/{area['id']}", vals,
                      on_done=lambda _r: (toast(self, "Area updated.", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def delete_area(self, area):
        if not confirm(self, f"Delete area '{area['name']}'?", "This will also remove all niche targets within this area.", "Delete"):
            return
        tasks.run(api.delete, f"/api/scrape-planner/areas/{area['id']}",
                  on_done=lambda _r: (toast(self, "Area deleted.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def create_target_modal(self, default_area_id=None, default_country_id=None):
        if not self.countries:
            toast(self, "Add a country and area first.", "warning")
            return
        if not self.areas:
            toast(self, "Add an area first before creating niche targets.", "warning")
            return
        dlg = TargetDialog(self, self.countries, self.areas, default_country_id=default_country_id, default_area_id=default_area_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("niche"):
                return
            tasks.run(api.post, "/api/scrape-planner/targets", vals,
                      on_done=lambda _r: (toast(self, "Niche target added to tree!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_target_modal(self, target):
        dlg = TargetDialog(self, self.countries, self.areas, target=target)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            tasks.run(api.patch, f"/api/scrape-planner/targets/{target['id']}", vals,
                      on_done=lambda _r: (toast(self, "Target updated.", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def delete_target(self, target):
        if not confirm(self, f"Delete target '{target['niche']}'?", "Are you sure you want to remove this niche target?", "Delete"):
            return
        tasks.run(api.delete, f"/api/scrape-planner/targets/{target['id']}",
                  on_done=lambda _r: (toast(self, "Target deleted.", "success"), self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)
