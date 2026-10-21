# -*- coding: utf-8 -*-
"""Role-scoped dashboard: overview, pipeline, audit activity, team performance, recent work."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from seo_audit_pro.core import api, fmt
from seo_audit_pro.core.session import session
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
        role = session.role
        subtitle = {"MANAGER": "Agency-wide overview", "TEAM_LEAD": f"Team {session.user.get('team_name') or ''}".strip(),
                    "AGENT": "Your leads, audits and outreach"}.get(role, "")
        h = self.header("Overview", subtitle)
        add = h.add_action(button("Add lead", icon_name="plus"))
        add.clicked.connect(lambda: self.ws.navigate("leads", create=True))
        refresh = h.add_action(button("", icon_name="refresh", tooltip="Refresh"))
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.load)

        content = QWidget()
        cl = vbox(content, spacing=20)
        self.metrics = MetricStrip()
        self.metrics.add("leads", "Active leads")
        if role != "AGENT":
            self.metrics.add("unassigned", "Unassigned")
        self.metrics.add("audits_7d", "Audits completed · 7 days")
        self.metrics.add("running", "Audits running")
        self.metrics.add("avg", "Average score · 30 days")
        self.metrics.add("emails", "Emails sent · 30 days")
        cl.addWidget(self.metrics)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        pipe = Card()
        pipe.body.addWidget(label("Lead pipeline", "SectionTitle"))
        pipe.body.addWidget(label("Leads by current status", "Meta"))
        self.pipeline = BarList(28)
        pipe.body.addWidget(self.pipeline)
        pipe.body.addStretch(1)
        act = Card()
        act.body.addWidget(label("Audit activity", "SectionTitle"))
        act.body.addWidget(label("Completed audits per day (UTC), last 14 days", "Meta"))
        self.activity = DailyBars(14)
        act.body.addWidget(self.activity)
        act.body.addStretch(1)
        grid.addWidget(pipe, 0, 0)
        grid.addWidget(act, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        cl.addLayout(grid)

        lower = hbox(spacing=20)
        if role in ("MANAGER", "TEAM_LEAD"):
            perf = Card(padding=0, spacing=0)
            head = hbox()
            head.setContentsMargins(20, 18, 20, 12)
            head.addWidget(label("Team performance" if role == "MANAGER" else "Team members", "SectionTitle"), 1)
            head.addWidget(label("Last 30 days", "Meta"))
            perf.body.addLayout(head)
            self.perf = DataTable([
                Column("name", "Team" if role == "MANAGER" else "Member", "strong", stretch=True,
                       secondary=(lambda r: {"TEAM_LEAD": "Team Lead", "AGENT": "Agent"}.get(r.get("role"), "")) if role != "MANAGER" else None),
                Column("leads", "Leads", "number", 80, align="right"),
                Column("audits", "Audits", "number", 80, align="right"),
                Column("emails", "Emails", "number", 80, align="right"),
                Column("won", "Won", "number", 70, align="right"),
            ], paginated=False)
            self.perf.view.setMinimumHeight(220)
            perf.body.addWidget(self.perf)
            lower.addWidget(perf, 3)
        else:
            self.perf = None
        recent = Card(padding=0, spacing=0)
        rh = hbox()
        rh.setContentsMargins(20, 18, 20, 12)
        rh.addWidget(label("Recent activity", "SectionTitle"), 1)
        recent.body.addLayout(rh)
        self.recent_host = QWidget()
        self.recent_layout = vbox(self.recent_host, (20, 0, 20, 16), 0)
        recent.body.addWidget(self.recent_host)
        recent.body.addStretch(1)
        lower.addWidget(recent, 2)
        cl.addLayout(lower)

        audits = Card(padding=0, spacing=0)
        ah = hbox()
        ah.setContentsMargins(20, 18, 20, 12)
        ah.addWidget(label("Recent audits", "SectionTitle"), 1)
        see = button("View all", "ghost", size="sm")
        see.clicked.connect(lambda: self.ws.navigate("audits"))
        ah.addWidget(see)
        audits.body.addLayout(ah)
        self.audits_table = DataTable([
            Column("company", "Website", "strong", stretch=True, secondary=lambda r: r.get("normalized_domain")),
            Column("status", "Status", "status", 120),
            Column("score", "Score", "score", 130),
            Column("requested_by_name", "Started by", "text", 160),
            Column("created_at", "Started", "relative", 120),
        ], paginated=False)
        self.audits_table.view.setMinimumHeight(260)
        self.audits_table.activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["id"]))
        audits.body.addWidget(self.audits_table)
        cl.addWidget(audits)

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
        m = self.metrics.metrics
        m["leads"].set(fmt.number(o["leads"]))
        if "unassigned" in m:
            m["unassigned"].set(fmt.number(o["unassigned"]), color=C["warning"] if o["unassigned"] else None)
        m["audits_7d"].set(fmt.number(o["audits_7d"]))
        m["running"].set(fmt.number(o["audits_running"]))
        avg = o.get("avg_score_30d")
        m["avg"].set("—" if avg is None else avg, hint="" if avg is not None else "No audits completed yet",
                     color=score_color(avg) if avg is not None else None)
        failed = o.get("emails_failed_30d") or 0
        m["emails"].set(fmt.number(o["emails_sent_30d"]), hint=f"{failed} failed" if failed else "")

        pipe = d.get("pipeline") or {}
        peak = max([pipe.get(k, 0) for k in PIPELINE_ORDER] + [1])
        self.pipeline.set_rows([(STATUS[k][2], pipe.get(k, 0), peak, STATUS[k][0], "") for k in PIPELINE_ORDER], 110)
        self.activity.set_values(d.get("audit_activity") or {})
        if self.perf is not None:
            self.perf.set_rows(d.get("team_performance") or [])
        self.audits_table.set_rows(d.get("recent_audits") or [])
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
            who = r.get("user_name") or "System"
            text = label(f"<b>{_esc(who)}</b> · {_esc(fmt.action_label(r['action']))}")
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
