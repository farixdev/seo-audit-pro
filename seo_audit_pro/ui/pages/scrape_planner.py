# -*- coding: utf-8 -*-
"""Scrape Planner & Matrix (Hierarchical Tree: Country -> Area -> Niche with instant filtering)."""

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QDialog,
    QHeaderView
)

from seo_audit_pro import icons
from seo_audit_pro.core import api, tasks
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
        self.save_btn = self.add_buttons("Save Country", "Cancel")
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
        self.save_btn = self.add_buttons("Save Area", "Cancel")
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

        self.save_btn = self.add_buttons("Save Target", "Cancel")
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
        super().__init__(workspace, scroll=False, spacing=12)

        # Clean header with NO "+" buttons
        self.header(
            "Scrape Planner",
            "Plan and organize Google Maps harvesting hierarchically by Country, Area, and Niche."
        )

        # Top row: Search on left, Actions on right
        row_top = hbox(spacing=10)

        self.search = SearchBox("Search country, area or niche…")
        self.search.setFixedWidth(300)
        self.search.search.connect(lambda _t: self._render_tree())
        row_top.addWidget(self.search)

        row_top.addStretch(1)

        # Action Buttons in the toolbar (NOT in the header)
        self.add_country_btn = button("Add Country", "ghost", icon_name="plus", size="sm")
        self.add_country_btn.clicked.connect(self.create_country_modal)
        row_top.addWidget(self.add_country_btn)

        self.add_area_btn = button("Add Area", "ghost", icon_name="plus", size="sm")
        self.add_area_btn.clicked.connect(lambda: self.create_area_modal())
        row_top.addWidget(self.add_area_btn)

        self.add_target_btn = button("Add Niche Target", "primary", icon_name="plus", size="sm")
        self.add_target_btn.clicked.connect(lambda: self.create_target_modal())
        row_top.addWidget(self.add_target_btn)

        self.expand_btn = button("Expand All", "ghost", size="sm")
        self.expand_btn.clicked.connect(lambda: self.tree.expandAll())
        row_top.addWidget(self.expand_btn)

        self.collapse_btn = button("Collapse All", "ghost", size="sm")
        self.collapse_btn.clicked.connect(lambda: self.tree.collapseAll())
        row_top.addWidget(self.collapse_btn)

        self.refresh_btn = button("", "ghost", icon_name="refresh", size="sm", tooltip="Refresh tree")
        self.refresh_btn.clicked.connect(self.load)
        row_top.addWidget(self.refresh_btn)

        self.body.addLayout(row_top)

        # Filter row directly under search: Segmented filter + Badges
        row_filter = hbox(spacing=10)

        self.filter_segmented = Segmented([
            ("All Targets", ""),
            ("Not Scraped", "no"),
            ("Scraped", "yes")
        ])
        self.filter_segmented.changed.connect(lambda _v: self._render_tree())
        row_filter.addWidget(self.filter_segmented)

        row_filter.addSpacing(4)

        # Stat badges using system theme tokens
        self.badge_total = Badge("Total: 0", C["text_2"], C["neutral_bg"])
        self.badge_not_scraped = Badge("Not Scraped: 0", C["warning"], C["warning_bg"])
        self.badge_scraped = Badge("Scraped: 0", C["success"], C["success_bg"])
        row_filter.addWidget(self.badge_total)
        row_filter.addWidget(self.badge_not_scraped)
        row_filter.addWidget(self.badge_scraped)

        row_filter.addStretch(1)

        self.body.addLayout(row_filter)

        # Tree container frame
        surface = QFrame()
        surface.setObjectName("Surface")
        surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surface_lay = vbox(surface, (0, 0, 0, 0), 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Country / Area / Niche", "Status", "Leads Harvested", "Quick Scrape", "Actions"])
        self.tree.setAlternatingRowColors(False)
        self.tree.setIndentation(22)
        # Disable animation to eliminate choppy jumping/sliding widgets
        self.tree.setAnimated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        # Style sheet strictly following system theme tokens: light background, high contrast, solid hover
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {C['surface']};
                border: none;
                color: {C['text']};
                font-size: 13px;
                outline: none;
            }}
            QTreeWidget::item {{
                height: 36px;
                padding: 0 4px;
                border-bottom: 1px solid {C['divider']};
                color: {C['text']};
            }}
            QTreeWidget::item:hover {{
                background: #F4F4F6;
                color: {C['text']};
            }}
            QTreeWidget::item:selected {{
                background: {C['selection']};
                color: {C['text']};
            }}
            QHeaderView::section {{
                background: {C['surface_alt']};
                color: {C['text_3']};
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
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 150)
        self.tree.setColumnWidth(4, 130)

        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        surface_lay.addWidget(self.tree)
        self.body.addWidget(surface, 1)

        self._all_countries = []
        self._all_areas = []
        self._all_targets = []
        self._stats = {}

    def activate(self, **params):
        self.load()

    def load(self):
        tasks.run(api.get, "/api/scrape-planner", on_done=self._on_data_loaded,
                  on_error=lambda e: show_error(self, e), owner=self)

    def _on_data_loaded(self, data):
        self._all_countries = data.get("countries", [])
        self._all_areas = data.get("areas", [])
        self._all_targets = data.get("targets", [])
        self._stats = data.get("stats", {})

        # Update stats badges
        self.badge_total.setText(f"Total: {self._stats.get('total', len(self._all_targets))}")
        self.badge_scraped.setText(f"Scraped: {self._stats.get('scraped', 0)}")
        self.badge_not_scraped.setText(f"Not Scraped: {self._stats.get('not_scraped', 0)}")

        self._render_tree()

    def _render_tree(self):
        self.tree.clear()

        filter_mode = self.filter_segmented.value() if hasattr(self, "filter_segmented") else ""
        search_kw = self.search.text().strip().lower() if hasattr(self, "search") else ""
        is_filtered = bool(filter_mode or search_kw)

        # Filter targets
        def _match_target(t):
            is_scraped = bool(t.get("is_scraped") or t.get("status") == "completed" or (t.get("leads_harvested", 0) > 0))
            if filter_mode == "yes" and not is_scraped:
                return False
            if filter_mode == "no" and is_scraped:
                return False
            if search_kw:
                niche_match = search_kw in (t.get("niche") or "").lower()
                notes_match = search_kw in (t.get("notes") or "").lower()
                area_match = search_kw in (t.get("area_name") or "").lower()
                country_match = search_kw in (t.get("country_name") or "").lower()
                if not (niche_match or notes_match or area_match or country_match):
                    return False
            return True

        targets_by_area = {}
        for t in self._all_targets:
            if _match_target(t):
                targets_by_area.setdefault(t["area_id"], []).append(t)

        areas_by_country = {}
        for a in self._all_areas:
            areas_by_country.setdefault(a["country_id"], []).append(a)

        rendered_countries = 0
        rendered_targets = 0

        for country in self._all_countries:
            cid = country["id"]
            country_areas = areas_by_country.get(cid, [])
            c_name_lower = country["name"].lower()

            # Pre-filter areas that have matching targets or match search
            matched_areas_for_country = []
            for area in country_areas:
                aid = area["id"]
                a_targets = targets_by_area.get(aid, [])
                a_name_lower = area["name"].lower()
                if is_filtered:
                    if not a_targets and (not search_kw or search_kw not in a_name_lower):
                        continue
                matched_areas_for_country.append((area, a_targets))

            # When filtering, hide country if it has no matching areas/targets
            if is_filtered and not matched_areas_for_country:
                if not search_kw or search_kw not in c_name_lower:
                    continue

            rendered_countries += 1

            # Level 0: Country Item
            c_item = QTreeWidgetItem(self.tree)
            c_text = country["name"] + (f" ({country['code']})" if country.get("code") else "")
            c_item.setText(0, c_text)
            c_item.setIcon(0, icons.icon("globe", C["text_2"], 15))
            c_font = QFont()
            c_font.setPointSize(11)
            c_font.setWeight(QFont.Weight.DemiBold)
            c_item.setFont(0, c_font)
            c_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "country", "data": country})

            # Country Actions widget
            c_act_wrap = QWidget()
            c_act_wrap.setFixedHeight(32)
            c_act_wrap.setStyleSheet("background: transparent;")
            c_act_lay = hbox(c_act_wrap, (0, 0, 0, 0), 4)

            add_a_btn = button("+ Area", "ghost", size="sm")
            add_a_btn.setFixedHeight(24)
            add_a_btn.clicked.connect(lambda _c=False, _cid=cid: self.create_area_modal(default_country_id=_cid))
            c_act_lay.addWidget(add_a_btn)

            edit_c_btn = button("", "ghost", icon_name="edit", size="sm", tooltip="Edit Country")
            edit_c_btn.setFixedSize(24, 24)
            edit_c_btn.clicked.connect(lambda _c=False, _ct=country: self.edit_country_modal(_ct))
            c_act_lay.addWidget(edit_c_btn)

            del_c_btn = button("", "ghost", icon_name="trash", size="sm", tooltip="Delete Country")
            del_c_btn.setFixedSize(24, 24)
            del_c_btn.clicked.connect(lambda _c=False, _ct=country: self.delete_country(_ct))
            c_act_lay.addWidget(del_c_btn)
            c_act_lay.addStretch(1)
            self.tree.setItemWidget(c_item, 4, c_act_wrap)

            # Level 1: Area Items
            areas_to_render = matched_areas_for_country if is_filtered else [(a, targets_by_area.get(a["id"], [])) for a in country_areas]

            for area, a_targets in areas_to_render:
                aid = area["id"]
                a_item = QTreeWidgetItem(c_item)
                a_item.setText(0, area["name"])
                a_item.setIcon(0, icons.icon("map_pin", C["text_3"], 14))
                a_font = QFont()
                a_font.setPointSize(10)
                a_font.setWeight(QFont.Weight.DemiBold)
                a_item.setFont(0, a_font)
                a_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "area", "data": area, "country": country})

                # Area actions widget
                a_act_wrap = QWidget()
                a_act_wrap.setFixedHeight(32)
                a_act_wrap.setStyleSheet("background: transparent;")
                a_act_lay = hbox(a_act_wrap, (0, 0, 0, 0), 4)

                add_n_btn = button("+ Niche", "ghost", size="sm")
                add_n_btn.setFixedHeight(24)
                add_n_btn.clicked.connect(lambda _c=False, _aid=aid, _cid=cid: self.create_target_modal(default_area_id=_aid, default_country_id=_cid))
                a_act_lay.addWidget(add_n_btn)

                edit_a_btn = button("", "ghost", icon_name="edit", size="sm", tooltip="Edit Area")
                edit_a_btn.setFixedSize(24, 24)
                edit_a_btn.clicked.connect(lambda _c=False, _ar=area: self.edit_area_modal(_ar))
                a_act_lay.addWidget(edit_a_btn)

                del_a_btn = button("", "ghost", icon_name="trash", size="sm", tooltip="Delete Area")
                del_a_btn.setFixedSize(24, 24)
                del_a_btn.clicked.connect(lambda _c=False, _ar=area: self.delete_area(_ar))
                a_act_lay.addWidget(del_a_btn)
                a_act_lay.addStretch(1)
                self.tree.setItemWidget(a_item, 4, a_act_wrap)

                # Level 2: Niche Targets
                for target in a_targets:
                    rendered_targets += 1
                    t_item = QTreeWidgetItem(a_item)
                    t_item.setText(0, target["niche"])
                    t_item.setIcon(0, icons.icon("target", C["accent"], 14))
                    if target.get("notes"):
                        t_item.setToolTip(0, f"Notes: {target['notes']}")
                    t_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "target", "data": target, "area": area, "country": country})

                    # Column 1: Status Pill
                    is_scraped = bool(target.get("is_scraped") or target.get("status") == "completed" or (target.get("leads_harvested", 0) > 0))
                    status_widget = QWidget()
                    status_widget.setFixedHeight(32)
                    status_widget.setStyleSheet("background: transparent;")
                    s_lay = hbox(status_widget, (0, 0, 0, 0), 0)
                    if is_scraped:
                        pill = Badge("Scraped", C["success"], C["success_bg"])
                    else:
                        pill = Badge("Not Scraped", C["warning"], C["warning_bg"])
                    s_lay.addWidget(pill)
                    s_lay.addStretch(1)
                    self.tree.setItemWidget(t_item, 1, status_widget)

                    # Column 2: Leads Harvested
                    leads_count = target.get("leads_harvested", 0)
                    leads_wrap = QWidget()
                    leads_wrap.setFixedHeight(32)
                    leads_wrap.setStyleSheet("background: transparent;")
                    leads_lay = hbox(leads_wrap, (0, 0, 0, 0), 0)
                    leads_lbl = QLabel(f"{leads_count:,} leads" if leads_count else "—")
                    leads_lbl.setStyleSheet(f"color: {C['text_2'] if leads_count else C['muted']}; font-size: 12px; font-weight: {'600' if leads_count else 'normal'}; background: transparent;")
                    leads_lay.addWidget(leads_lbl)
                    leads_lay.addStretch(1)
                    self.tree.setItemWidget(t_item, 2, leads_wrap)

                    # Column 3: Quick Action Launch in Map Harvest
                    scrape_b = button("Scrape in Maps", "primary", icon_name="map_pin", size="sm")
                    scrape_b.setFixedHeight(26)
                    scrape_b.setStyleSheet(f"""
                        QPushButton {{
                            background: {C['ink']};
                            color: #FFFFFF;
                            font-size: 11px;
                            font-weight: 600;
                            border-radius: 4px;
                            padding: 2px 10px;
                            border: none;
                        }}
                        QPushButton:hover {{
                            background: {C['ink_hover']};
                        }}
                    """)
                    scrape_b.clicked.connect(lambda _c=False, _t=target: self._launch_in_map_harvest(_t))
                    self.tree.setItemWidget(t_item, 3, scrape_b)

                    # Column 4: Actions (Toggle Scraped, Edit, Delete)
                    t_act_wrap = QWidget()
                    t_act_wrap.setFixedHeight(32)
                    t_act_wrap.setStyleSheet("background: transparent;")
                    t_act_lay = hbox(t_act_wrap, (0, 0, 0, 0), 4)

                    toggle_btn = button("", "ghost", icon_name="check" if not is_scraped else "refresh", size="sm")
                    toggle_btn.setFixedSize(24, 24)
                    toggle_btn.setToolTip("Mark as Scraped" if not is_scraped else "Mark as Not Scraped")
                    toggle_btn.clicked.connect(lambda _c=False, _t=target, _val=not is_scraped: self._toggle_target_scraped(_t, _val))
                    t_act_lay.addWidget(toggle_btn)

                    edit_t_btn = button("", "ghost", icon_name="edit", size="sm", tooltip="Edit Niche Target")
                    edit_t_btn.setFixedSize(24, 24)
                    edit_t_btn.clicked.connect(lambda _c=False, _t=target: self.edit_target_modal(_t))
                    t_act_lay.addWidget(edit_t_btn)

                    del_t_btn = button("", "ghost", icon_name="trash", size="sm", tooltip="Delete Target")
                    del_t_btn.setFixedSize(24, 24)
                    del_t_btn.clicked.connect(lambda _c=False, _t=target: self.delete_target(_t))
                    t_act_lay.addWidget(del_t_btn)
                    t_act_lay.addStretch(1)
                    self.tree.setItemWidget(t_item, 4, t_act_wrap)

        # Empty state when filter or search matches nothing
        if rendered_countries == 0:
            empty_item = QTreeWidgetItem(self.tree)
            msg = "No targets match the current filter." if is_filtered else "No countries added yet. Click 'Add Country' above to start planning."
            empty_item.setText(0, msg)
            empty_item.setForeground(0, QColor(C["text_3"]))
            f = QFont()
            f.setItalic(True)
            empty_item.setFont(0, f)

        # Expand all nodes cleanly without animation jitter
        self.tree.expandAll()

    # ── Context Menu ──────────────────────────────────────────────────────────

    def _show_context_menu(self, pos: QPoint):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if not item:
            menu.addAction("Add Country...", self.create_country_modal)
            menu.addSeparator()
            menu.addAction("Expand All", self.tree.expandAll)
            menu.addAction("Collapse All", self.tree.collapseAll)
            menu.addAction("Refresh", self.load)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return

        node = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = node.get("type")
        data = node.get("data", {})

        if kind == "country":
            cid = data.get("id")
            menu.addAction("Add Area / City in " + data.get("name", "Country") + "...", lambda: self.create_area_modal(default_country_id=cid))
            menu.addSeparator()
            menu.addAction("Edit Country...", lambda: self.edit_country_modal(data))
            menu.addAction("Delete Country...", lambda: self.delete_country(data))
        elif kind == "area":
            aid = data.get("id")
            cid = data.get("country_id")
            menu.addAction("Add Niche Target in " + data.get("name", "Area") + "...", lambda: self.create_target_modal(default_area_id=aid, default_country_id=cid))
            menu.addSeparator()
            menu.addAction("Edit Area...", lambda: self.edit_area_modal(data))
            menu.addAction("Delete Area...", lambda: self.delete_area(data))
        elif kind == "target":
            is_scraped = bool(data.get("is_scraped") or data.get("status") == "completed" or (data.get("leads_harvested", 0) > 0))
            menu.addAction("Scrape in Map Harvest", lambda: self._launch_in_map_harvest(data))
            menu.addAction("Mark as Not Scraped" if is_scraped else "Mark as Scraped", lambda: self._toggle_target_scraped(data, not is_scraped))
            menu.addSeparator()
            menu.addAction("Edit Niche Target...", lambda: self.edit_target_modal(data))
            menu.addAction("Delete Target...", lambda: self.delete_target(data))

        menu.addSeparator()
        menu.addAction("Expand All", self.tree.expandAll)
        menu.addAction("Collapse All", self.tree.collapseAll)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

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
            item.setExpanded(not item.isExpanded())
        elif kind == "country":
            item.setExpanded(not item.isExpanded())

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
        if not self._all_countries:
            toast(self, "Add at least one country before creating areas.", "warning")
            return
        dlg = AreaDialog(self, self._all_countries, default_country_id=default_country_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("name"):
                return
            tasks.run(api.post, "/api/scrape-planner/areas", vals,
                      on_done=lambda _r: (toast(self, "Area added successfully!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_area_modal(self, area):
        dlg = AreaDialog(self, self._all_countries, area=area)
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
        if not self._all_countries:
            toast(self, "Add a country and area first.", "warning")
            return
        if not self._all_areas:
            toast(self, "Add an area first before creating niche targets.", "warning")
            return
        dlg = TargetDialog(self, self._all_countries, self._all_areas, default_country_id=default_country_id, default_area_id=default_area_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals.get("niche"):
                return
            tasks.run(api.post, "/api/scrape-planner/targets", vals,
                      on_done=lambda _r: (toast(self, "Niche target added to tree!", "success"), self.load()),
                      on_error=lambda e: show_error(self, e), owner=self)

    def edit_target_modal(self, target):
        dlg = TargetDialog(self, self._all_countries, self._all_areas, target=target)
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
