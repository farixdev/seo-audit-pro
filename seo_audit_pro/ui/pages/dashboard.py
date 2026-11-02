# -*- coding: utf-8 -*-
"""Solo offline dashboard: 3-Campaign overview, pipeline, audit activity, Audit 1 (SEO) & Audit 2 (Services), recent work."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from seo_audit_pro.core import api, fmt
from seo_audit_pro.theme import C, STATUS, score_color
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.charts import BarList, DailyBars
from seo_audit_pro.widgets.common import Card, MetricStrip, StatefulArea, button, hbox, label, vbox
from seo_audit_pro.widgets.table import Column, DataTable

PIPELINE_ORDER = ["new", "assigned", "audited", "contacted", "qualified", "won", "lost"]


class DashboardPage(Page):
    title = "Dashboard"

    def __init__(self, ws):
        super().__init__(ws, scroll=True)
        subtitle = "Local Offline Workspace · 3-Campaign Outreach & Dual Auditing Engine"
        h = self.header("Dashboard", subtitle)
        add = h.add_action(button("Add lead", icon_name="plus"))
        add.clicked.connect(lambda: self.ws.navigate("leads", create=True))
        b_harv = h.add_action(button("Map Harvest", icon_name="map_pin"))
        b_harv.clicked.connect(lambda: self.ws.navigate("map_harvest"))
        b_aud = h.add_action(button("Run Audits", icon_name="audit"))
        b_aud.clicked.connect(lambda: self.ws.navigate("audits"))
        b_out = h.add_action(button("Email Outreach", "primary", icon_name="mail"))
        b_out.clicked.connect(lambda: self.ws.navigate("outreach"))
        refresh = h.add_action(button("", icon_name="refresh", tooltip="Refresh"))
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.load)

        content = QWidget()
        cl = vbox(content, spacing=20)

        # Row 1: 4 Campaign Metrics (never overflows horizontally)
        self.metrics_top = MetricStrip()
        self.metrics_top.add("leads", "Total Leads in Bank")
        self.metrics_top.add("seo_audited", "Audit 1: SEO Audited (C1)")
        self.metrics_top.add("service_audited", "Audit 2: Service Audited (C3)")
        self.metrics_top.add("no_website", "Campaign 2: No-Website Leads")
        cl.addWidget(self.metrics_top)

        # Row 2: 4 Execution & Activity Metrics
        self.metrics_bottom = MetricStrip()
        self.metrics_bottom.add("audits_7d", "SEO Audits (Last 7d)")
        self.metrics_bottom.add("running", "Audits in Queue / Running")
        self.metrics_bottom.add("avg", "Avg SEO Score (30d)")
        self.metrics_bottom.add("emails", "Outreached (30d)")
        cl.addWidget(self.metrics_bottom)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        pipe = Card()
        pipe.body.addWidget(label("Lead Pipeline", "SectionTitle"))
        pipe.body.addWidget(label("Leads by current outreach stage", "Meta"))
        self.pipeline = BarList(28)
        pipe.body.addWidget(self.pipeline)
        pipe.body.addStretch(1)

        act = Card()
        act.body.addWidget(label("Audit Activity", "SectionTitle"))
        act.body.addWidget(label("Completed technical SEO audits per day (UTC), last 14 days", "Meta"))
        self.activity = DailyBars(14)
        act.body.addWidget(self.activity)
        act.body.addStretch(1)

        grid.addWidget(pipe, 0, 0)
        grid.addWidget(act, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)

        # Dual Audits row: Audit 1 (Technical SEO) & Audit 2 (Website Services & Pitch)
        audits_row = hbox(spacing=20)

        # Audit 1 Card
        aud1_card = Card(padding=0, spacing=0)
        ah1 = hbox()
        ah1.setContentsMargins(20, 18, 20, 12)
        ah1.addWidget(label("Audit 1: Recent Technical SEO Audits (C1)", "SectionTitle"), 1)
        see1 = button("View all", "ghost", size="sm")
        see1.clicked.connect(lambda: self.ws.navigate("audits"))
        ah1.addWidget(see1)
        aud1_card.body.addLayout(ah1)
        self.audits_table = DataTable([
            Column("company", "Website", "strong", stretch=True, secondary=lambda r: r.get("normalized_domain")),
            Column("status", "Status", "status", 110),
            Column("score", "SEO Score", "score", 120),
            Column("created_at", "Date", "relative", 110),
        ], paginated=False)
        self.audits_table.view.setMinimumHeight(240)
        self.audits_table.activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["id"]))
        aud1_card.body.addWidget(self.audits_table)
        audits_row.addWidget(aud1_card, 1)

        # Audit 2 Card
        aud2_card = Card(padding=0, spacing=0)
        ah2 = hbox()
        ah2.setContentsMargins(20, 18, 20, 12)
        ah2.addWidget(label("Audit 2: Recent Website Service Audits (C3)", "SectionTitle"), 1)
        see2 = button("Run Service Audits", "ghost", size="sm")
        see2.clicked.connect(lambda: self.ws.navigate("audits"))
        ah2.addWidget(see2)
        aud2_card.body.addLayout(ah2)
        self.service_table = DataTable([
            Column("company", "Company", "strong", 180, secondary=lambda r: r.get("services_summary")),
            Column("what_we_provide", "What We Can Provide (AI Sniffer)", "text", stretch=True),
            Column("service_match_score", "Opportunity", "score", 120),
        ], paginated=False)
        self.service_table.view.setMinimumHeight(240)
        self.service_table.activated.connect(lambda r: self.ws.navigate("outreach", lead_id=r["id"], campaign="service_pitch"))
        aud2_card.body.addWidget(self.service_table)
        audits_row.addWidget(aud2_card, 1)

        cl.addLayout(audits_row)

        # Recent Activity Card
        recent = Card(padding=0, spacing=0)
        rh = hbox()
        rh.setContentsMargins(20, 18, 20, 12)
        rh.addWidget(label("Recent Workspace Activity", "SectionTitle"), 1)
        recent.body.addLayout(rh)
        self.recent_host = QWidget()
        self.recent_layout = vbox(self.recent_host, (20, 0, 20, 16), 0)
        recent.body.addWidget(self.recent_host)
        recent.body.addStretch(1)
        cl.addWidget(recent)

        self.area = StatefulArea(content, skeleton="cards")
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)

    def activate(self, **_):
        self.load()

    def load(self):
        if not hasattr(self, "_loaded"):
            self.area.loading("Loading dashboard…")
        self.fetch("dash", api.get, "/api/dashboard", on_done=self._render, on_error=self.area.error)

    def _render(self, d):
        self._loaded = True
        o = d["overview"]
        m_top = self.metrics_top.metrics
        m_top["leads"].set(fmt.number(o["leads"]))
        m_top["seo_audited"].set(fmt.number(o.get("seo_audited_total", 0)), hint="Technical SEO report ready")
        m_top["service_audited"].set(fmt.number(o.get("service_audited_total", 0)), hint="Services & pitch ready")
        m_top["no_website"].set(fmt.number(o.get("no_website_total", 0)), hint=f"{o.get('called_total', 0)} called so far")

        m_bot = self.metrics_bottom.metrics
        m_bot["audits_7d"].set(fmt.number(o["audits_7d"]))
        m_bot["running"].set(fmt.number(o["audits_running"]))
        avg = o.get("avg_score_30d")
        m_bot["avg"].set("—" if avg is None else avg, hint="" if avg is not None else "No audits completed yet",
                         color=score_color(avg) if avg is not None else None)
        calls = o.get("calls_30d") or 0
        m_bot["emails"].set(fmt.number(o.get("outreach_30d") or 0), hint=f"{calls} by phone call" if calls else "Outreach actions · 30d")

        pipe = d.get("pipeline") or {}
        peak = max([pipe.get(k, 0) for k in PIPELINE_ORDER] + [1])
        self.pipeline.set_rows([(STATUS[k][2], pipe.get(k, 0), peak, STATUS[k][0], "") for k in PIPELINE_ORDER], 110)
        self.activity.set_values(d.get("audit_activity") or {})
        self.audits_table.set_rows(d.get("recent_audits") or [])

        # Process service audits
        srv_rows = []
        for r in (d.get("recent_service_audits") or []):
            row = dict(r)
            s_list = []
            we_provide = []
            if row.get("services_extracted"):
                try:
                    parsed = json.loads(row["services_extracted"])
                    if isinstance(parsed, dict):
                        s_list = parsed.get("services") or []
                        we_provide = parsed.get("what_we_can_provide") or parsed.get("recommended_services") or []
                except Exception:
                    pass
            row["services_summary"] = ", ".join(s_list[:3]) if s_list else "Local services"
            if we_provide:
                row["what_we_provide"] = ", ".join(we_provide[:2])
            else:
                notes = (row.get("service_pitch_notes") or "").strip()
                row["what_we_provide"] = notes[:80] + ("…" if len(notes) > 80 else "") if notes else "Ready for pitch"
            srv_rows.append(row)
        self.service_table.set_rows(srv_rows)

        self._render_recent(d.get("recent_activity") or [])
        self.area.ready()

    def _render_recent(self, rows):
        while self.recent_layout.count():
            it = self.recent_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not rows:
            self.recent_layout.addWidget(label("No activity yet.", "Muted"))
            return
        for r in rows[:10]:
            line = QFrame()
            row = hbox(line, (0, 8, 0, 8), 10)
            dot = QFrame()
            dot.setFixedSize(6, 6)
            dot.setStyleSheet(f"background: {C['border_strong']}; border-radius: 3px;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            action_name = fmt.action_label(r["action"]) if hasattr(fmt, "action_label") else r["action"].replace("_", " ").title()
            text = label(f"<b>{_esc(action_name)}</b>")
            text.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(text, 1)
            row.addWidget(label(fmt.relative(r["created_at"]), "Meta"))
            line.setStyleSheet(f"QFrame {{ border-bottom: 1px solid {C['divider']}; }} QLabel {{ border: none; }}")
            dot.setStyleSheet(f"background: {C['border_strong']}; border-radius: 3px; border: none;")
            self.recent_layout.addWidget(line)

    def on_realtime(self, ev):
        if ev.get("type") == "audit_progress" and ev.get("status") in ("completed", "failed") and self.isVisible():
            self.load()


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
