# -*- coding: utf-8 -*-
"""Audit Logs (Manager): read-only, append-only security trail."""

import json
from datetime import datetime, time, timedelta, timezone

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDateEdit

from seo_audit_pro.core import api, fmt
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.users import surface
from seo_audit_pro.widgets.common import StatefulArea, hbox, label
from seo_audit_pro.widgets.feedback import Drawer, SearchBox, combo, kv_row
from seo_audit_pro.widgets.table import Column, DataTable

PERIODS = [("All time", ""), ("Last 24 hours", "1"), ("Last 7 days", "7"), ("Last 30 days", "30"),
           ("Custom range", "custom")]


def _utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class AuditLogsPage(Page):
    nav_key = "audit_logs"
    title = "Audit Logs"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.params = {"page": 1, "page_size": 50}
        self.header("Audit Logs", "Security-relevant activity across the workspace. Entries cannot be edited or "
                                  "deleted.")
        bar = hbox(spacing=8)
        self.search = SearchBox("Search user, action or details")
        self.search.search.connect(lambda t: self._set("search", t))
        bar.addWidget(self.search, 2)
        self.action = combo([("All actions", "")])
        self.action.currentIndexChanged.connect(lambda _i: self._set("action", self.action.currentData()))
        self.action.setMinimumWidth(200)
        bar.addWidget(self.action)
        self.period = combo(PERIODS)
        self.period.currentIndexChanged.connect(self._period_changed)
        bar.addWidget(self.period)
        self.since = QDateEdit(QDate.currentDate().addDays(-7))
        self.until = QDateEdit(QDate.currentDate())
        for d in (self.since, self.until):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setMaximumDate(QDate.currentDate())
            d.dateChanged.connect(lambda _d: self._apply_range())
            d.setVisible(False)
        self.to_label = label("to", "Meta")
        self.to_label.setVisible(False)
        bar.addWidget(self.since)
        bar.addWidget(self.to_label)
        bar.addWidget(self.until)
        bar.addStretch(1)
        self.body.addLayout(bar)

        self.table = DataTable([
            Column("created_at", "Time", "datetime", 170),
            Column("user_name", "User", "strong", 220, render=lambda r: r.get("user_name") or r.get("user_email") or "System",
                   secondary=lambda r: r.get("user_email") if r.get("user_name") else ""),
            Column("action", "Action", "text", 230, render=lambda r: fmt.action_label(r["action"])),
            Column("entity_type", "Entity", "text", 220, render=_entity),
            Column("ip", "IP address", "text", 140, stretch=True),
        ], page_sizes=(50, 100, 200))
        self.table.page_changed.connect(lambda p: self._set("page", p, keep_page=True))
        self.table.page_size_changed.connect(lambda s: self._set("page_size", s))
        self.table.activated.connect(self.open_entry)
        self.area = StatefulArea(surface(self.table))
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self.drawer = Drawer(self, 520)

    def activate(self, **_):
        self.load()

    def _set(self, key, value, keep_page=False):
        self.params[key] = value
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def _period_changed(self, _i):
        value = self.period.currentData()
        custom = value == "custom"
        for w in (self.since, self.until, self.to_label):
            w.setVisible(custom)
        if custom:
            self._apply_range()
            return
        self.params.pop("until", None)
        if value:
            self.params["since"] = _utc_iso(datetime.now(timezone.utc) - timedelta(days=int(value)))
        else:
            self.params.pop("since", None)
        self._set("page", 1)

    def _apply_range(self):
        if self.period.currentData() != "custom":
            return
        start, end = self.since.date().toPython(), self.until.date().toPython()
        if end < start:
            start, end = end, start
        local = datetime.now().astimezone().tzinfo
        self.params["since"] = _utc_iso(datetime.combine(start, time.min, local))
        self.params["until"] = _utc_iso(datetime.combine(end, time.max, local))
        self._set("page", 1)

    def load(self):
        if not self.table.model.rows:
            self.area.loading("Loading audit logs…")
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        self.fetch("list", api.get, "/api/audit-logs", params=params, on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        self._fill_actions(data.get("actions") or [])
        self.table.set_page(data)
        if data["items"]:
            self.area.ready()
        else:
            self.area.empty("No log entries match these filters", "Change the search, action or date range.")

    def _fill_actions(self, actions):
        current = self.action.currentData()
        existing = [self.action.itemData(i) for i in range(1, self.action.count())]
        if existing == actions:
            return
        self.action.blockSignals(True)
        self.action.clear()
        self.action.addItem("All actions", "")
        for a in actions:
            self.action.addItem(fmt.action_label(a), a)
        self.action.setCurrentIndex(max(0, self.action.findData(current)))
        self.action.blockSignals(False)

    def open_entry(self, row):
        d = self.drawer
        d.clear()
        d.open_drawer(fmt.action_label(row["action"]))
        lay = d.content_layout
        for k, v in (("Time", fmt.datetime_(row["created_at"])), ("Action", row["action"]),
                     ("User", row.get("user_name") or "—"), ("User email", row.get("user_email") or "—"),
                     ("User ID", row.get("user_id") or "—"), ("Entity type", row.get("entity_type") or "—"),
                     ("Entity ID", row.get("entity_id") or "—"), ("IP address", row.get("ip") or "—"),
                     ("Entry ID", row["id"])):
            lay.addWidget(kv_row(k, v))
        lay.addSpacing(4)
        lay.addWidget(label("Details", "SectionTitle"))
        meta = row.get("metadata")
        if meta in (None, "", {}):
            lay.addWidget(label("No additional details were recorded.", "Muted"))
        else:
            text = json.dumps(meta, indent=2, ensure_ascii=False, default=str) if not isinstance(meta, str) else meta
            block = label(text, "Mono", wrap=True, selectable=True)
            block.setStyleSheet("background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 6px; padding: 10px;")
            lay.addWidget(block)
        lay.addStretch(1)


def _entity(row):
    et, eid = row.get("entity_type"), row.get("entity_id")
    if not et:
        return "—"
    return f"{et.replace('_', ' ').capitalize()} · {eid[:14]}" if eid else et.replace("_", " ").capitalize()
