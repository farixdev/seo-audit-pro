# -*- coding: utf-8 -*-
"""Dashboard: 3-Campaign Overview, Audit 1 (Technical SEO) & Audit 2 (Website Services & AI Pitch), Pipeline, and Activity."""

from datetime import datetime, timedelta, timezone
import json

from PySide6.QtCore import Qt

from seo_audit_pro.core import api, fmt
from seo_audit_pro.theme import C, STATUS_LABEL
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.charts import BarChart, PipelineBars
from seo_audit_pro.widgets.common import Card, ContentArea, MetricStrip, button, hbox, label, score_badge, status_badge, vbox
from seo_audit_pro.widgets.table import DataTable

_ACTION_TEXT = {
    "LEAD_CREATED": "New lead added to Lead Bank",
    "LEAD_IMPORTED": "Leads imported to Lead Bank",
    "LEAD_ASSIGNED": "Lead updated",
    "LEAD_BULK_ASSIGNED": "Leads updated in bulk",
    "LEAD_STATUS_CHANGED": "Lead pipeline status updated",
    "LEAD_OUTREACH": "Outreach logged (email / call)",
    "AUDIT_QUEUED": "Audit 1 (Technical SEO) queued",
    "AUDIT_COMPLETED": "Audit 1 (Technical SEO) completed",
    "AUDIT_FAILED": "Audit 1 (Technical SEO) failed",
    "AUDIT_BULK": "Bulk Technical SEO Audits started",
    "SERVICE_AUDIT_COMPLETED": "Audit 2 (Website Services & AI Pitch) completed",
    "EMAIL_QUEUED": "Outreach email queued",
    "EMAIL_SENT": "Outreach email delivered",
    "EMAIL_SENT_DIRECT": "Direct outreach email delivered",
    "EMAIL_FAILED": "Outreach email failed",
    "BULK_EMAIL": "Bulk outreach campaign queued",
}


class DashboardPage(Page):
    title = "Dashboard"
    nav_key = "dashboard"

    def __init__(self, ws):
        super().__init__(ws, scroll=True)
        head = self.header(
            "Dashboard",
            "Live overview across Campaign 1 (SEO Audits), Campaign 2 (No-Website Call & Pitch), and Campaign 3 (Website Service Audits & AI Pitch).",
        )
        b1 = button("Map Harvest", "secondary", icon_name="map_pin")
        b1.clicked.connect(lambda: self.ws.navigate("map_harvest"))
        b2 = button("Lead Bank", "secondary", icon_name="leads")
        b2.clicked.connect(lambda: self.ws.navigate("leads"))
        b3 = button("Run Audits (C1 & C3)", "secondary", icon_name="audit")
        b3.clicked.connect(lambda: self.ws.navigate("audits"))
        b4 = button("Email Outreach", "primary", icon_name="mail")
        b4.clicked.connect(lambda: self.ws.navigate("outreach"))
        head.add_action(b1)
        head.add_action(b2)
        head.add_action(b3)
        head.add_action(b4)

        self.area = ContentArea()
        self.body.addWidget(self.area, 1)
        content = self.area.body

        # Row 1: 4 Campaign KPIs (never overflows horizontally)
        self.metrics_top = MetricStrip()
        self.m_leads = self.metrics_top.add("Total Leads in Bank")
        self.m_seo = self.metrics_top.add("Audit 1: SEO Audited (C1)")
        self.m_srv = self.metrics_top.add("Audit 2: Service Audited (C3)")
        self.m_nosite = self.metrics_top.add("Campaign 2: No-Website Leads")
        content.addWidget(self.metrics_top)

        # Row 2: 4 Execution & Outreach KPIs
        self.metrics_bottom = MetricStrip()
        self.m_audits = self.metrics_bottom.add("SEO Audits (Last 7d)")
        self.m_running = self.metrics_bottom.add("Audits in Queue / Running")
        self.m_score = self.metrics_bottom.add("Avg SEO Score (30d)")
        self.m_outreach = self.metrics_bottom.add("Outreach Sent (30d)")
        content.addWidget(self.metrics_bottom)

        # Charts row: Pipeline + 14-day Audit Activity
        mid = hbox(spacing=16)
        pipe_card = Card()
        pipe_card.body.addWidget(label("Lead Pipeline Status", "SectionTitle"))
        pipe_card.body.addWidget(label("Distribution of active leads across your outreach funnel.", "Meta"))
        self.pipeline = PipelineBars()
        pipe_card.body.addWidget(self.pipeline)
        mid.addWidget(pipe_card, 1)

        act_card = Card()
        act_card.body.addWidget(label("Audit Activity (Last 14 Days)", "SectionTitle"))
        act_card.body.addWidget(label("Completed technical audits per day.", "Meta"))
        self.chart = BarChart()
        act_card.body.addWidget(self.chart)
        mid.addWidget(act_card, 1)
        content.addLayout(mid)

        # Two-column Audit Tables: Audit 1 (Technical SEO) & Audit 2 (Website Services & AI Pitch)
        tables_row = hbox(spacing=16)

        rec = Card(padding=0, spacing=0)
        rhead = hbox((20, 16, 20, 12))
        rcol = vbox(spacing=2)
        rcol.addWidget(label("Audit 1: Recent Technical SEO Audits (C1)", "SectionTitle"))
        rcol.addWidget(label("Technical health, Core Web Vitals & PDF report status.", "Meta"))
        rhead.addLayout(rcol)
        rhead.addStretch(1)
        all_btn = button("Open SEO Audits", "ghost", size="sm")
        all_btn.clicked.connect(lambda: self.ws.navigate("audits"))
        rhead.addWidget(all_btn)
        rec.body.addLayout(rhead)
        self.audits_table = DataTable([
            ("company", "Company", 1.3),
            ("normalized_domain", "Website", 1.2),
            ("status", "Status", 110, lambda r: status_badge(r["status"], r.get("percent"))),
            ("score", "SEO Score", 90, lambda r: score_badge(r.get("score"), r.get("grade"))),
            ("created_at", "Date", 100, lambda r: fmt.relative(r["created_at"])),
        ], empty_title="No SEO audits yet", empty_text="Queue a Technical SEO Audit from Lead Bank or Audits.")
        self.audits_table.row_activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["id"]))
        rec.body.addWidget(self.audits_table)
        tables_row.addWidget(rec, 1)

        srv_card = Card(padding=0, spacing=0)
        shead = hbox((20, 16, 20, 12))
        scol = vbox(spacing=2)
        scol.addWidget(label("Audit 2: Website Service Audits & AI Pitch (C3)", "SectionTitle"))
        scol.addWidget(label("Services detected on lead websites & what we can provide them.", "Meta"))
        shead.addLayout(scol)
        shead.addStretch(1)
        srv_btn = button("Run Service Audit", "ghost", size="sm")
        srv_btn.clicked.connect(lambda: self.ws.navigate("audits"))
        shead.addWidget(srv_btn)
        srv_card.body.addLayout(shead)
        self.service_table = DataTable([
            ("company", "Company", 1.1),
            ("services_summary", "Their Services", 1.3),
            ("what_we_provide", "What We Can Provide (AI Sniffer)", 1.6),
            ("service_match_score", "Opportunity", 95, lambda r: score_badge(r.get("service_match_score"))),
        ], empty_title="No Website Service Audits yet",
           empty_text="Open Audits → Tab 2 (Website Service Auditor) to sniff out services and generate AI pitches.")
        self.service_table.row_activated.connect(lambda r: self.ws.navigate("lead", lead_id=r["id"]))
        srv_card.body.addWidget(self.service_table)
        tables_row.addWidget(srv_card, 1)

        content.addLayout(tables_row)

        # Bottom row: Recent workspace activity
        act = Card(padding=20, spacing=10)
        act.body.addWidget(label("Recent Workspace Activity", "SectionTitle"))
        self.feed = vbox(spacing=8)
        act.body.addLayout(self.feed)
        content.addWidget(act)
        content.addStretch(1)

    def activate(self, **_):
        self.refresh()

    def on_realtime(self, ev):
        if ev.get("type") in ("audit_progress", "notification"):
            if ev.get("type") == "audit_progress" and ev.get("status") not in ("completed", "failed"):
                return
            self.refresh()

    def refresh(self):
        if not getattr(self, "_loaded", False):
            self.area.loading("Loading dashboard…")
        self.fetch("dash", api.get, "/api/dashboard", on_done=self._render, on_error=self.area.error)

    def _render(self, d):
        self._loaded = True
        self.area.ready()
        ov = d["overview"]
        self.m_leads.set(fmt.number(ov["leads"]), "Active leads in bank")
        self.m_seo.set(fmt.number(ov.get("seo_audited_total", 0)), "Completed SEO report")
        self.m_srv.set(fmt.number(ov.get("service_audited_total", 0)), "Services & pitch ready")
        self.m_nosite.set(
            fmt.number(ov.get("no_website_total", 0)),
            f"{fmt.number(ov.get('called_total', 0))} called so far",
        )
        self.m_audits.set(fmt.number(ov["audits_7d"]), "Completed in last 7d")
        self.m_running.set(fmt.number(ov["audits_running"]), "Active in background")
        self.m_score.set("—" if ov["avg_score_30d"] is None else f"{ov['avg_score_30d']}/100", "30-day average")
        calls = ov.get("calls_30d") or 0
        self.m_outreach.set(
            fmt.number(ov["outreach_30d"]),
            f"{calls} call{'s' if calls != 1 else ''} · last 30 days" if calls else "Emails & calls · 30d",
        )

        p = d.get("pipeline") or {}
        order = [("new", "#52525B"), ("audited", "#2E90FA"), ("contacted", "#06AED4"),
                 ("replied", "#F79009"), ("won", "#12B76A"), ("lost", "#F04438")]
        self.pipeline.set_rows([(STATUS_LABEL[k], p.get(k, 0), col) for k, col in order])

        act = d.get("audit_activity") or {}
        today = datetime.now(timezone.utc).date()
        points = []
        for i in range(13, -1, -1):
            day = today - timedelta(days=i)
            key = day.isoformat()
            points.append((day.strftime("%d %b") if i % 3 == 0 else "", act.get(key, 0)))
        self.chart.set_points(points)

        self.audits_table.set_rows(d.get("recent_audits") or [])

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
            row["services_summary"] = ", ".join(s_list[:4]) if s_list else "General local services"
            if we_provide:
                row["what_we_provide"] = ", ".join(we_provide[:3])
            else:
                notes = (row.get("service_pitch_notes") or "").strip()
                row["what_we_provide"] = notes[:90] + ("…" if len(notes) > 90 else "") if notes else "Ready for AI Pitch"
            srv_rows.append(row)
        self.service_table.set_rows(srv_rows)

        while self.feed.count():
            it = self.feed.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    sub = it.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        items = d.get("recent_activity") or []
        if not items:
            self.feed.addWidget(label("No recent activity yet.", "Muted"))
        for a in items[:8]:
            row = hbox(spacing=10)
            text = _ACTION_TEXT.get(a["action"], a["action"].replace("_", " ").title())
            row.addWidget(label(text), 1)
            row.addWidget(label(fmt.relative(a["created_at"]), "Meta"))
            self.feed.addLayout(row)
