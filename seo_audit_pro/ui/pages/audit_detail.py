# -*- coding: utf-8 -*-
"""
Audit detail: live progress while the engine runs, then the full result —
overview, issue explorer, passive security assessment, recommendations,
crawled pages and deliverables. Every number shown comes from the stored
engine result; nothing is estimated client-side.
"""

import time
from collections import OrderedDict
from datetime import datetime, timezone

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QPlainTextEdit, QProgressBar, QScrollArea,
                               QStackedWidget, QTabWidget, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.realtime import get_hub
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, MONO_FAMILY, SEVERITY, STATUS, score_color
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.lead_dialogs import start_audit
from seo_audit_pro.ui.pages.reports import (PDF_UNAVAILABLE, export_json, notice_frame, open_remote, report_label,
                                            report_menu, report_size, view_client_report)
from seo_audit_pro.widgets.charts import ScoreRing
from seo_audit_pro.widgets.common import (Badge, BusyButton, Card, MetricStrip, StatefulArea, StateView, button, hbox,
                                          vbox)
from seo_audit_pro.widgets.common import label as _label
from seo_audit_pro.widgets.feedback import Drawer, SearchBox, clear_layout, combo, confirm, show_error, toast
from seo_audit_pro.widgets.table import Column, DataTable

PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
ISSUE_FILTERS = [("All issues", ""), ("Critical", "Critical"), ("High", "High"), ("Medium", "Medium"),
                 ("Low", "Low"), ("Unresolved", "open"), ("Resolved", "resolved")]

# Passive security areas, matched on the engine's check ids.
SECURITY_AREAS = [
    ("transport", "Transport security", "HTTPS, TLS protocols, certificate, HSTS and mixed content",
     ("sec_https", "sec_http_redirect", "sec_ssl", "sec_tls", "sec_hsts", "sec_mixed", "https", "ssl", "hsts")),
    ("headers", "Security headers", "Browser-enforced protections sent by the server",
     ("sec_csp", "sec_x_", "sec_referrer", "sec_permissions", "sec_headers")),
    ("cookies", "Cookies", "Secure, HttpOnly and SameSite flags", ("sec_cookie", "cookie")),
    ("disclosure", "Information disclosure", "Version banners, error pages, directory listings, public files",
     ("sec_info_",)),
    ("components", "Vulnerable components", "Client-side libraries matched against known CVEs", ("sec_cve_", "sec_lib_")),
    ("email", "Email domain trust", "SPF, DMARC and DKIM records", ("sec_dns_",)),
    ("auth", "Authentication surface", "Public login paths and brute-force protection", ("sec_auth_",)),
    ("waf", "WAF and bot protection", "Edge firewall and rate limiting signals", ("sec_waf",)),
    ("tech", "Technology fingerprint", "Platform details visible to anyone", ("sec_tech",)),
]


def label(text="", role=None, wrap=False, selectable=False):
    """Engine text can contain markup-like snippets (e.g. '<link rel=canonical>'): always render as plain text."""
    w = _label(text, role, wrap, selectable)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


def _friendly_gap(text):
    t = str(text)
    if "429" in t or "quota" in t.lower():
        which = "desktop" if "desktop" in t.lower() else ("mobile" if "mobile" in t.lower() else "")
        return f"PageSpeed Insights {which}: the Google API request quota was exceeded (HTTP 429)".replace("  ", " ")
    return t if len(t) <= 160 else t[:157] + "…"


def _now_utc():
    return datetime.now(timezone.utc)


def _section(title, text, mono=False):
    w = QWidget()
    lay = vbox(w, spacing=4)
    lay.addWidget(label(title, "FieldLabel"))
    body = label(text or "—", wrap=True, selectable=True)
    if mono:
        body.setObjectName("Mono")
    lay.addWidget(body)
    return w


def _scroll(inner):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    holder = QWidget()
    holder.setObjectName("Canvas")
    lay = vbox(holder, (0, 16, 4, 16), 16)
    lay.addWidget(inner)
    lay.addStretch(1)
    area.setWidget(holder)
    return area


def _banner(kind, text):
    fg, bg, border, icon_name = {
        "warning": (C["warning"], C["warning_bg"], "#FEDF89", "alert_triangle"),
        "info": (C["info"], C["info_bg"], "#B2DDFF", "info"),
        "danger": (C["danger"], C["danger_bg"], "#FECDCA", "shield_alert"),
    }[kind]
    box = QFrame()
    box.setStyleSheet(f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
                      f"QLabel {{ color: {fg}; border: none; background: transparent; }}")
    row = hbox(box, (12, 10, 12, 10), 10)
    ic = QLabel()
    ic.setPixmap(icons.pixmap(icon_name, fg, 16))
    row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
    row.addWidget(label(text, wrap=True, selectable=True), 1)
    return box


class CategoryScores(QWidget):
    """Category score bars with weights; unmeasured categories are labelled, never zero-filled."""
    ROW = 32

    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, dims):
        self.rows = dims
        self.setMinimumHeight(max(1, len(dims)) * self.ROW)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(13)
        fm = QFontMetrics(f)
        small = QFont(f)
        small.setPixelSize(12)
        name_w, val_w = 210, 100
        for i, d in enumerate(self.rows):
            y = i * self.ROW
            mid = y + self.ROW / 2
            score = d.get("score")
            p.setFont(f)
            p.setPen(QColor(C["text"]))
            name = d.get("label") or d.get("key", "")
            p.drawText(QRectF(0, y, name_w, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, name_w - 56))
            p.setFont(small)
            p.setPen(QColor(C["text_3"]))
            weight = f"{round((d.get('weight') or 0) * 100)}%"
            p.drawText(QRectF(min(fm.horizontalAdvance(name) + 8, name_w - 48), y, 44, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter, weight)
            bx, bw = name_w, max(20, self.width() - name_w - val_w - 12)
            track = QPainterPath()
            track.addRoundedRect(QRectF(bx, mid - 3, bw, 6), 3, 3)
            p.fillPath(track, QColor("#EDEDF0"))
            if score is not None:
                bar = QPainterPath()
                bar.addRoundedRect(QRectF(bx, mid - 3, max(6, bw * max(0, min(100, score)) / 100), 6), 3, 3)
                p.fillPath(bar, QColor(score_color(score)))
                fb = QFont(f)
                fb.setWeight(QFont.Weight.DemiBold)
                p.setFont(fb)
                p.setPen(QColor(score_color(score)))
                p.drawText(QRectF(bx + bw + 12, y, val_w, self.ROW), Qt.AlignmentFlag.AlignVCenter, str(score))
            else:
                p.setFont(small)
                p.setPen(QColor(C["text_3"]))
                p.drawText(QRectF(bx + bw + 12, y, val_w, self.ROW), Qt.AlignmentFlag.AlignVCenter, "Not measured")
        p.end()


class AuditDetailPage(Page):
    title = "Audit"
    nav_key = "audits"
    breadcrumb = "Audits"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.audit_id = None
        self.audit = None
        self.result = None
        self.items = []
        self.states = {}
        self._last_detail_fetch = 0.0

        # -- header ----------------------------------------------------------
        head = QWidget()
        hl = hbox(head, spacing=12)
        col = vbox(spacing=4)
        trow = hbox(spacing=10)
        self.h_title = label("", "PageTitle")
        self.h_status = Badge.status("queued")
        trow.addWidget(self.h_title)
        trow.addWidget(self.h_status, 0, Qt.AlignmentFlag.AlignVCenter)
        trow.addStretch(1)
        col.addLayout(trow)
        self.h_meta = label("", "PageSubtitle")
        col.addWidget(self.h_meta)
        hl.addLayout(col, 1)
        self.btn_lead = button("Open lead", icon_name="leads")
        self.btn_lead.clicked.connect(lambda: self.ws.navigate("lead", lead_id=self.audit["lead_id"]))
        self.btn_email = button("Compose email", icon_name="mail")
        self.btn_email.clicked.connect(lambda: self.ws.navigate("outreach", lead_id=self.audit["lead_id"]))
        self.btn_rerun = button("Run again", icon_name="refresh")
        self.btn_rerun.clicked.connect(self.run_again)
        self.btn_cancel = BusyButton("Cancel audit", "danger", icon_name="stop")
        self.btn_cancel.clicked.connect(self.cancel)
        for b in (self.btn_lead, self.btn_email, self.btn_rerun, self.btn_cancel):
            hl.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(head)

        # -- states ----------------------------------------------------------
        self.stack = QStackedWidget()
        self.progress_view = self._build_progress()
        self.ended_view = StateView()
        self.ended_view.action.connect(self.run_again)
        self.results_view = self._build_results()
        for w in (self.progress_view, self.ended_view, self.results_view):
            self.stack.addWidget(w)
        self.area = StatefulArea(self.stack, skeleton="detail")
        self.area.retry.connect(self.load_audit)
        self.body.addWidget(self.area, 1)

        self.drawer = Drawer(self, 560)
        self.drawer.title.setTextFormat(Qt.TextFormat.PlainText)
        self.ended_view.text.setTextFormat(Qt.TextFormat.PlainText)
        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(self._poll_tick)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick_clock)

    # ------------------------------------------------------------------ build
    def _build_progress(self):
        w = QWidget()
        lay = vbox(w, spacing=16)
        card = Card(padding=24, spacing=12)
        top = hbox(spacing=12)
        self.p_percent = label("0%", "MetricValue")
        top.addWidget(self.p_percent)
        top.addStretch(1)
        self.p_state = label("", "Muted")
        top.addWidget(self.p_state, 0, Qt.AlignmentFlag.AlignBottom)
        card.body.addLayout(top)
        self.p_bar = QProgressBar()
        self.p_bar.setRange(0, 100)
        self.p_bar.setTextVisible(False)
        card.body.addWidget(self.p_bar)
        self.p_step = label("", wrap=True)
        self.p_step.setStyleSheet("font-weight: 600;")
        card.body.addWidget(self.p_step)
        card.body.addWidget(label("Progress follows the engine's real pipeline: crawl, page analysis, links, PageSpeed, "
                                  "structured data, security, scoring and report generation.", "Meta", wrap=True))
        lay.addWidget(card)
        self.p_metrics = MetricStrip()
        self.p_metrics.add("processed", "Pages processed")
        self.p_metrics.add("discovered", "URLs discovered")
        self.p_metrics.add("elapsed", "Elapsed")
        self.p_metrics.add("started", "Started at")
        lay.addWidget(self.p_metrics)
        log_card = Card(padding=0, spacing=0)
        lh = hbox()
        lh.setContentsMargins(20, 16, 20, 10)
        lh.addWidget(label("Engine log", "SectionTitle"), 1)
        lh.addWidget(label("Live", "Meta"))
        log_card.body.addLayout(lh)
        self.p_log = QPlainTextEdit()
        self.p_log.setReadOnly(True)
        self.p_log.setMinimumHeight(220)
        self.p_log.setStyleSheet(f"QPlainTextEdit {{ border: none; border-top: 1px solid {C['divider']}; border-radius: 0; "
                                 f"background: {C['surface_alt']}; font-family: '{MONO_FAMILY}', Consolas; font-size: 12px; "
                                 f"color: {C['text_2']}; padding: 10px 16px; }}")
        log_card.body.addWidget(self.p_log)
        lay.addWidget(log_card, 1)
        return w

    def _build_results(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # Overview
        self.ov_host = QWidget()
        self.ov_layout = vbox(self.ov_host, spacing=16)
        self.tabs.addTab(_scroll(self.ov_host), "Overview")
        # Issues
        issues = QWidget()
        il = vbox(issues, (0, 16, 0, 0), 12)
        bar = hbox(spacing=8)
        self.i_search = SearchBox("Search issues")
        self.i_search.setMinimumWidth(240)
        self.i_search.search.connect(lambda _t: self._filter_issues())
        bar.addWidget(self.i_search, 2)
        self.i_filter = combo(ISSUE_FILTERS)
        self.i_filter.currentIndexChanged.connect(lambda _i: self._filter_issues())
        bar.addWidget(self.i_filter)
        self.i_category = combo([("All categories", "")])
        self.i_category.currentIndexChanged.connect(lambda _i: self._filter_issues())
        bar.addWidget(self.i_category)
        bar.addStretch(1)
        self.i_count = label("", "Meta")
        bar.addWidget(self.i_count)
        il.addLayout(bar)
        self.i_table = DataTable([
            Column("label", "Issue", "strong", 360, stretch=True, secondary=lambda r: (r.get("detail") or "")[:140]),
            Column("priority", "Severity", "severity", 110),
            Column("category_label", "Category", "text", 160),
            Column("_urls", "Affected URLs", "text", 120, align="right",
                   render=lambda r: str(len(r.get("where") or [])) if r.get("where") else "Site-wide"),
            Column("_state", "Status", "status", 110, render=lambda r: r.get("_state", "open")),
        ], id_key="id", paginated=False)
        self.i_table.activated.connect(self.open_issue)
        surface = QFrame()
        surface.setObjectName("Surface")
        vbox(surface).addWidget(self.i_table)
        self.i_area = StatefulArea(surface)
        il.addWidget(self.i_area, 1)
        self.tabs.addTab(issues, "Issues")
        # Security
        self.sec_host = QWidget()
        self.sec_layout = vbox(self.sec_host, spacing=16)
        self.tabs.addTab(_scroll(self.sec_host), "Security")
        # Recommendations
        self.rec_host = QWidget()
        self.rec_layout = vbox(self.rec_host, spacing=16)
        self.tabs.addTab(_scroll(self.rec_host), "Recommendations")
        # Pages
        pages = QWidget()
        pl = vbox(pages, (0, 16, 0, 0), 12)
        self.pg_count = label("", "Meta")
        pl.addWidget(self.pg_count)
        self.pg_table = DataTable([
            Column("url", "URL", "text", 380, stretch=True),
            Column("status", "HTTP", "number", 70, align="right"),
            Column("title", "Title", "text", 280),
            Column("word_count", "Words", "number", 80, align="right"),
            Column("h1_count", "H1", "number", 60, align="right"),
            Column("_index", "Indexing", "text", 110, render=lambda r: "noindex" if r.get("noindex") else "Indexable"),
        ], id_key="url", paginated=False)
        ps = QFrame()
        ps.setObjectName("Surface")
        vbox(ps).addWidget(self.pg_table)
        pl.addWidget(ps, 1)
        self.tabs.addTab(pages, "Pages")
        # Reports
        reps = QWidget()
        rl = vbox(reps, (0, 16, 0, 0), 12)
        rbar = hbox(spacing=8)
        view = button("View client report", "primary", icon_name="eye")
        view.clicked.connect(lambda: view_client_report(self, self.audit_id))
        rbar.addWidget(view)
        exp = button("Export JSON", icon_name="download")
        exp.clicked.connect(lambda: export_json(self, self.audit_id, (self.audit or {}).get("normalized_domain")))
        rbar.addWidget(exp)
        self.btn_regen = BusyButton("Regenerate reports", None, icon_name="refresh")
        self.btn_regen.clicked.connect(self.regenerate)
        rbar.addWidget(self.btn_regen)
        rbar.addStretch(1)
        rl.addLayout(rbar)
        self.r_notice = notice_frame()
        rl.addWidget(self.r_notice)
        self.r_table = DataTable([
            Column("_label", "Report", "strong", 260, render=report_label,
                   secondary=lambda r: "Client-facing deliverable" if r.get("kind") == "client" else "Full technical breakdown"),
            Column("size_bytes", "Size", "text", 170, render=report_size),
            Column("created_at", "Generated", "datetime", 180, stretch=True),
            Column("_actions", "", "actions", 44),
        ], paginated=False)
        self.r_table.activated.connect(lambda r: open_remote(self, f"/api/reports/{r['id']}/download"))
        self.r_table.context_requested.connect(lambda row, pos: report_menu(self, row, pos, include_audit=False))
        rs = QFrame()
        rs.setObjectName("Surface")
        vbox(rs).addWidget(self.r_table)
        self.r_area = StatefulArea(rs)
        rl.addWidget(self.r_area, 1)
        self.tabs.addTab(reps, "Reports")
        return self.tabs

    # ---------------------------------------------------------------- loading
    def activate(self, audit_id=None, **_):
        if audit_id and audit_id != self.audit_id:
            self.audit_id = audit_id
            self.audit = None
            self.result = None
            self.drawer.hide()
            self.tabs.setCurrentIndex(0)
            self.area.loading("Loading audit…")
        if self.audit_id:
            self.load_audit()

    def load_audit(self):
        if not self.audit_id:
            return
        self._last_detail_fetch = time.monotonic()
        aid = self.audit_id
        self.fetch("audit", api.get, f"/api/audits/{aid}",
                   on_done=lambda d: self._apply_audit(d["audit"]) if aid == self.audit_id else None,
                   on_error=self._load_failed)

    def _load_failed(self, err):
        if self.audit is None:
            self.area.error(err)
        else:
            show_error(self, err)

    def _apply_audit(self, a):
        self.audit = a
        self._update_header()
        status = a["status"]
        if status in ("queued", "running"):
            self._show_progress(a)
            self.stack.setCurrentWidget(self.progress_view)
            self.area.ready()
            self._start_timers()
        elif status == "completed":
            self._stop_timers()
            if self.result is None:
                self.load_result()
            else:
                self.stack.setCurrentWidget(self.results_view)
                self.area.ready()
        else:
            self._stop_timers()
            cancelled = status == "cancelled"
            self.ended_view.show_state("error" if not cancelled else "empty",
                                       "Audit cancelled" if cancelled else "Audit failed",
                                       a.get("error") or ("The audit was stopped before it finished." if cancelled else
                                                          "The audit could not be completed."),
                                       "Run again", icon_name="stop" if cancelled else "alert_circle")
            self.stack.setCurrentWidget(self.ended_view)
            self.area.ready()

    def load_result(self):
        aid = self.audit_id
        self.area.loading("Loading results…")
        self.fetch("result", api.get, f"/api/audits/{aid}/result",
                   on_done=lambda r: self._render_result(r) if aid == self.audit_id else None,
                   on_error=self.area.error)

    def _update_header(self):
        a = self.audit
        self.h_title.setText(a.get("company") or a.get("normalized_domain") or "Audit")
        fg, bg, text = STATUS.get(a["status"], (C["text_2"], C["neutral_bg"], a["status"].title()))
        self.h_status.setText(text)
        self.h_status.set_colors(fg, bg)
        meta = [a.get("normalized_domain") or a.get("url") or ""]
        if a.get("requested_by_name"):
            meta.append(f"started by {a['requested_by_name']}")
        meta.append(fmt.datetime_(a.get("started_at") or a.get("created_at")))
        if a["status"] == "completed":
            meta.append(f"took {fmt.duration(a.get('started_at'), a.get('completed_at'))}")
        self.h_meta.setText("  ·  ".join(m for m in meta if m))
        self.ws.set_page_title(self, a.get("company") or "Audit")
        running = a["status"] in ("queued", "running")
        may_cancel = running and (a.get("requested_by") == session.user_id or not session.is_agent())
        self.btn_cancel.setVisible(may_cancel)
        self.btn_cancel.busy(False)
        self.btn_rerun.setVisible(not running)
        self.btn_email.setVisible(a["status"] == "completed")

    # ---------------------------------------------------------------- progress
    def _show_progress(self, a):
        pct = int(a.get("percent") or 0)
        self.p_percent.setText(f"{pct}%")
        self.p_bar.setValue(pct)
        queued = a["status"] == "queued"
        self.p_state.setText("Queued — waiting for an available audit worker" if queued else "Running")
        self.p_step.setText(a.get("step") or "")
        m = self.p_metrics.metrics
        m["processed"].set(fmt.number(a.get("pages_processed") or 0))
        m["discovered"].set(fmt.number(a.get("pages_discovered") or 0) if a.get("pages_discovered") else "—")
        m["started"].set(fmt.time_(a.get("started_at")) or "Waiting")
        if "logs" in a:
            lines = a.get("logs") or []
            text = "\n".join(lines) if lines else "Waiting for the first engine message…"
            if text != self.p_log.toPlainText():
                self.p_log.setPlainText(text)
                sb = self.p_log.verticalScrollBar()
                sb.setValue(sb.maximum())
        self._tick_clock()

    def _tick_clock(self):
        a = self.audit or {}
        start = fmt.parse(a.get("started_at"))
        if not start:
            self.p_metrics.metrics["elapsed"].set("—")
            return
        secs = max(0, int((_now_utc() - start).total_seconds()))
        self.p_metrics.metrics["elapsed"].set(f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s")

    def _start_timers(self):
        if self.isVisible():
            self.clock.start()
            self.poll.setInterval(5000 if not get_hub().connected else 15000)
            if not self.poll.isActive():
                self.poll.start()

    def _stop_timers(self):
        self.poll.stop()
        self.clock.stop()

    def _poll_tick(self):
        if not self.isVisible() or not self.audit or self.audit["status"] not in ("queued", "running"):
            self._stop_timers()
            return
        self.poll.setInterval(5000 if not get_hub().connected else 15000)
        self.load_audit()

    def showEvent(self, e):
        super().showEvent(e)
        if self.audit and self.audit["status"] in ("queued", "running"):
            self._start_timers()
            self.load_audit()

    def hideEvent(self, e):
        self._stop_timers()
        super().hideEvent(e)

    def on_realtime(self, ev):
        if ev.get("type") != "audit_progress" or ev.get("audit_id") != self.audit_id or not self.audit:
            return
        status = ev.get("status")
        if status in ("completed", "failed", "cancelled"):
            self.load_audit()
            return
        for k in ("status", "percent", "step", "pages_processed", "pages_discovered"):
            if k in ev:
                self.audit[k] = ev[k]
        if self.audit["status"] != self.h_status.text().lower():
            self._update_header()
        self._show_progress({k: v for k, v in self.audit.items() if k != "logs"})
        # Logs are only returned by the detail endpoint: refresh them at a calm pace.
        if self.isVisible() and time.monotonic() - self._last_detail_fetch > 4:
            self.load_audit()

    # ---------------------------------------------------------------- actions
    def run_again(self):
        if self.audit:
            start_audit(self, {"id": self.audit["lead_id"], "company": self.audit.get("company") or "this lead"})

    def cancel(self):
        if not confirm(self, "Cancel this audit?", "The engine stops at its next checkpoint. Partial results are not "
                                                   "saved.", "Cancel audit", danger=True):
            return
        self.btn_cancel.busy(True, "Cancelling…")

        def done(_r):
            toast(self, "Cancellation requested.", "info")
            self.load_audit()

        def fail(err):
            self.btn_cancel.busy(False)
            show_error(self, err)

        tasks.run(api.post, f"/api/audits/{self.audit_id}/cancel", on_done=done, on_error=fail, owner=self)

    def regenerate(self):
        self.btn_regen.busy(True, "Generating…")

        def done(res):
            self.btn_regen.busy(False)
            warn = res.get("warnings") or []
            toast(self, "Reports regenerated." + (f" Note: {'; '.join(warn)}." if warn else ""),
                  "success" if not warn else "warning")
            self.load_reports()

        def fail(err):
            self.btn_regen.busy(False)
            show_error(self, err, "Reports not generated")

        tasks.run(api.post, f"/api/audits/{self.audit_id}/reports", timeout=300, on_done=done, on_error=fail, owner=self)

    # ---------------------------------------------------------------- results
    def _render_result(self, r):
        self.result = r
        self.states = dict(r.get("issue_states") or {})
        self._render_overview(r)
        self._prepare_issues(r)
        self._render_security(r)
        self._render_recommendations(r)
        pages = r.get("pages") or []
        self.pg_table.set_rows(pages)
        self.pg_count.setText(f"{fmt.plural(len(pages), 'page')} crawled. Pages beyond the crawl limit were not analysed.")
        self.tabs.setTabText(1, f"Issues ({len(self.items)})")
        self.tabs.setTabText(4, f"Pages ({len(pages)})")
        sec_open = sum(1 for c in r.get("checks") or [] if c.get("category") == "security" and c.get("status") in ("fail", "warn"))
        self.tabs.setTabText(2, f"Security ({sec_open})" if sec_open else "Security")
        self.stack.setCurrentWidget(self.results_view)
        self.area.ready()
        self.load_reports()

    def _render_overview(self, r):
        clear_layout(self.ov_layout)
        sc = r.get("score") or {}
        pc = r.get("priority_counts") or {}

        top = hbox(spacing=16)
        score_card = Card(padding=20, spacing=0)
        srow = hbox(spacing=20)
        ring = ScoreRing(124)
        ring.set_score(sc.get("score"), sc.get("grade"))
        srow.addWidget(ring)
        info = vbox(spacing=6)
        headline = label(sc.get("label") or "Overall SEO health", wrap=True)
        headline.setStyleSheet("font-size: 16px; font-weight: 600;")
        info.addWidget(headline)
        conf = sc.get("confidence")
        comp = sc.get("completeness_pct")
        bits = []
        if conf:
            bits.append(f"Confidence: {conf}")
        if comp is not None:
            bits.append(f"{comp}% of weighted categories measured")
        if bits:
            info.addWidget(label(" · ".join(bits), "Muted", wrap=True))
        penalties = sc.get("penalties") or []
        if penalties:
            info.addWidget(label("Penalties applied: " + "; ".join(penalties), "Meta", wrap=True))
        info.addWidget(label(f"{fmt.plural(r.get('pages_crawled') or 0, 'page')} crawled · generated "
                             f"{r.get('generated_at') or '—'}", "Meta"))
        info.addWidget(label("Scoring: pass = 1.0, warning = 0.3, fail = 0. Checks that could not run are excluded "
                             "and the remaining weights renormalised.", "Meta", wrap=True))
        info.addStretch(1)
        srow.addLayout(info, 1)
        score_card.body.addLayout(srow)
        top.addWidget(score_card, 3)

        est = Card(padding=20, spacing=8)
        est.body.addWidget(label("Estimated reach", "SectionTitle"))
        est.body.addWidget(label("Algorithmic estimates from crawl signals — not third-party ranking data.", "Meta", wrap=True))
        auth = r.get("authority") or {}
        traffic = r.get("traffic") or {}
        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        rows = [("Domain authority (estimated)", auth.get("domain_authority")),
                ("Page authority (estimated)", auth.get("page_authority")),
                ("Monthly visits (estimated)", fmt.number(traffic.get("monthly_visits")) if traffic else None)]
        for i, (k, v) in enumerate(rows):
            grid.addWidget(label(k, "Muted"), i, 0)
            val = label("Not available" if v in (None, "") else str(v))
            val.setStyleSheet("font-weight: 600;" if v not in (None, "") else f"color: {C['text_3']};")
            grid.addWidget(val, i, 1, Qt.AlignmentFlag.AlignRight)
        est.body.addLayout(grid)
        est.body.addStretch(1)
        top.addWidget(est, 2)
        self.ov_layout.addLayout(top)

        strip = MetricStrip()
        for key in ("Critical", "High", "Medium", "Low"):
            m = strip.add(key, f"{key} issues")
            n = pc.get(key, 0)
            m.set(fmt.number(n), color=SEVERITY[key.lower()][0] if n else None)
        total = strip.add("total", "Total findings")
        total.set(fmt.number(sum(pc.values())))
        self.ov_layout.addWidget(strip)

        gaps = list(OrderedDict.fromkeys(_friendly_gap(g) for g in r.get("data_gaps") or []))
        if gaps:
            shown = "; ".join(gaps[:6]) + (f"; and {len(gaps) - 6} more" if len(gaps) > 6 else "")
            self.ov_layout.addWidget(_banner("warning", "Some checks could not run, so they are excluded from the score "
                                                        f"rather than counted as passed: {shown}."))

        mid = hbox(spacing=16)
        cats = Card(padding=20, spacing=10)
        cats.body.addWidget(label("Category scores", "SectionTitle"))
        cats.body.addWidget(label("Weight shows each category's share of the overall score.", "Meta"))
        bars = CategoryScores()
        bars.set_rows(sc.get("dimensions") or [])
        cats.body.addWidget(bars)
        cats.body.addStretch(1)
        mid.addWidget(cats, 3)
        psi_card = Card(padding=20, spacing=8)
        psi_card.body.addWidget(label("PageSpeed Insights", "SectionTitle"))
        psi = r.get("psi") or {}
        if not psi.get("mobile") and not psi.get("desktop"):
            psi_card.body.addWidget(label("PageSpeed data was not available for this audit.", "Muted", wrap=True))
        else:
            g = QGridLayout()
            g.setHorizontalSpacing(16)
            g.setVerticalSpacing(8)
            g.addWidget(label("", "Meta"), 0, 0)
            g.addWidget(label("Mobile", "FieldLabel"), 0, 1, Qt.AlignmentFlag.AlignRight)
            g.addWidget(label("Desktop", "FieldLabel"), 0, 2, Qt.AlignmentFlag.AlignRight)
            for i, (key, name) in enumerate((("performance", "Performance"), ("seo", "SEO"),
                                              ("accessibility", "Accessibility"), ("best_practices", "Best practices")), 1):
                g.addWidget(label(name, "Muted"), i, 0)
                for j, strat in enumerate(("mobile", "desktop"), 1):
                    v = ((psi.get(strat) or {}).get("scores") or {}).get(key)
                    cell = label("—" if v is None else str(v))
                    cell.setStyleSheet(f"font-weight: 600; color: {score_color(v)};" if v is not None else
                                       f"color: {C['text_3']};")
                    g.addWidget(cell, i, j, Qt.AlignmentFlag.AlignRight)
            psi_card.body.addLayout(g)
            field = any((psi.get(s) or {}).get("field_available") for s in ("mobile", "desktop"))
            psi_card.body.addWidget(label("Includes real-user (CrUX) field data." if field else
                                          "Lab data only — no real-user field data was available.", "Meta", wrap=True))
        psi_card.body.addStretch(1)
        mid.addWidget(psi_card, 2)
        self.ov_layout.addLayout(mid)

        summary = (r.get("exec_summary") or "").strip()
        if summary:
            sc_card = Card(padding=20, spacing=8)
            sc_card.body.addWidget(label("Executive summary", "SectionTitle"))
            src = r.get("exec_summary_source")
            sc_card.body.addWidget(label("AI-written from this audit's findings" if src == "ai" else
                                         "Written from this audit's findings", "Meta"))
            sc_card.body.addWidget(label(summary, wrap=True, selectable=True))
            self.ov_layout.addWidget(sc_card)

    # ---------------------------------------------------------------- issues
    def _prepare_issues(self, r):
        names = {d.get("key"): d.get("label") for d in (r.get("score") or {}).get("dimensions") or []}
        items = sorted(r.get("action_items") or [], key=lambda a: PRIORITY_ORDER.get(a.get("priority"), 9))
        for a in items:
            a["_state"] = self.states.get(a["id"], "open")
            a["category_label"] = a.get("category_label") or names.get(a.get("category")) or (a.get("category") or "").title()
        self.items = items
        cats = sorted({(a["category_label"], a.get("category")) for a in items})
        self.i_category.blockSignals(True)
        self.i_category.clear()
        self.i_category.addItem("All categories", "")
        for text, key in cats:
            self.i_category.addItem(text, key)
        self.i_category.blockSignals(False)
        self._filter_issues()

    def _filter_issues(self):
        f = self.i_filter.currentData()
        cat = self.i_category.currentData()
        q = self.i_search.text().strip().lower()
        rows = []
        for a in self.items:
            if f in ("open", "resolved") and a["_state"] != f:
                continue
            if f and f not in ("open", "resolved") and a.get("priority") != f:
                continue
            if cat and a.get("category") != cat:
                continue
            if q and q not in (a.get("label") or "").lower() and q not in (a.get("detail") or "").lower():
                continue
            rows.append(a)
        self.i_table.set_rows(rows)
        resolved = sum(1 for a in self.items if a["_state"] == "resolved")
        self.i_count.setText(f"{len(rows)} shown · {resolved} of {len(self.items)} resolved")
        if not self.items:
            self.i_area.empty("No issues found", "Every scored check passed in this audit.", icon_name="check_circle")
        elif not rows:
            self.i_area.empty("No issues match these filters", "Change the severity, status or category filter.")
        else:
            self.i_area.ready()

    def open_issue(self, item):
        self.drawer.clear()
        lay = self.drawer.content_layout
        top = hbox(spacing=8)
        top.addWidget(Badge.severity(item.get("priority")))
        top.addWidget(Badge.status(item["_state"]))
        top.addWidget(label(item.get("category_label") or "", "Meta"))
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(_section("Finding", item.get("label")))
        lay.addWidget(_section("Evidence", item.get("detail")))
        urls = item.get("where") or []
        if urls:
            shown = "\n".join(urls[:60]) + (f"\n… and {len(urls) - 60} more" if len(urls) > 60 else "")
            lay.addWidget(_section(f"Affected URLs ({len(urls)})", shown, mono=True))
        else:
            lay.addWidget(_section("Affected URLs", "Site-wide finding (not tied to specific pages)."))
        lay.addWidget(_section("Why it matters", item.get("why") or "Not provided by the engine for this check."))
        lay.addWidget(_section("Recommended remediation", item.get("fix") or "No specific remediation recorded for this check."))
        meta = []
        if item.get("difficulty"):
            meta.append(f"Effort: {item['difficulty']}")
        if item.get("gain"):
            meta.append(f"Expected gain: {item['gain']}")
        if item.get("tier_title"):
            meta.append(f"Priority group: {item['tier_title']}")
        if meta:
            lay.addWidget(label(" · ".join(meta), "Meta", wrap=True))
        resolved = item["_state"] == "resolved"
        btn = BusyButton("Mark as unresolved" if resolved else "Mark as resolved", None if resolved else "primary",
                         icon_name="restore" if resolved else "check")
        btn.clicked.connect(lambda: self._toggle_state(item, btn))
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        self.drawer.open_drawer(item.get("label") or "Issue")

    def _toggle_state(self, item, btn):
        new = "open" if item["_state"] == "resolved" else "resolved"
        btn.busy(True, "Saving…")

        def done(_r):
            item["_state"] = new
            self.states[item["id"]] = new
            self._filter_issues()
            toast(self, "Marked as resolved." if new == "resolved" else "Marked as unresolved.", "success")
            self.open_issue(item)

        def fail(err):
            btn.busy(False)
            show_error(self, err)

        tasks.run(api.put, f"/api/audits/{self.audit_id}/issues/{item['id']}", {"status": new}, on_done=done,
                  on_error=fail, owner=self)

    # ---------------------------------------------------------------- security
    def _render_security(self, r):
        clear_layout(self.sec_layout)
        sec = r.get("security") or {}
        checks = [c for c in r.get("checks") or [] if c.get("category") == "security"]
        self.sec_layout.addWidget(_banner("info", "Passive assessment: only publicly observable signals were checked — "
                                                  "response headers, TLS and certificate details, DNS records and whether "
                                                  "well-known files are publicly reachable. No intrusive, credentialed or "
                                                  "destructive tests were run."))
        if not sec.get("available"):
            self.sec_layout.addWidget(_banner("warning", "The security module did not run for this audit, so only the "
                                                         "basic HTTPS and header checks below are available. Run the "
                                                         "audit again to include the full passive assessment."))
        if not checks:
            self.sec_layout.addWidget(label("No security checks were recorded for this audit.", "Muted"))
            return
        open_checks = [c for c in checks if c.get("status") in ("fail", "warn")]
        strip = MetricStrip()
        for key in ("critical", "high", "medium", "low"):
            n = sum(1 for c in open_checks if (c.get("severity") or "").lower() == key)
            strip.add(key, f"{SEVERITY[key][2]} findings").set(n, color=SEVERITY[key][0] if n else None)
        strip.add("passed", "Checks passed").set(sum(1 for c in checks if c.get("status") == "pass"))
        self.sec_layout.addWidget(strip)

        exposed = sec.get("sensitive_files") or []
        if exposed:
            paths = "\n".join(f"{f.get('path')} — {f.get('status', 'Publicly accessible')}" for f in exposed)
            self.sec_layout.addWidget(_banner("danger", "Sensitive files are publicly accessible (contents were not "
                                                        f"downloaded or inspected):\n{paths}"))
        facts = self._security_facts(sec)
        if facts:
            card = Card(padding=20, spacing=8)
            card.body.addWidget(label("Observed configuration", "SectionTitle"))
            g = QGridLayout()
            g.setHorizontalSpacing(24)
            g.setVerticalSpacing(6)
            for i, (k, v) in enumerate(facts):
                g.addWidget(label(k, "Muted"), i // 2, (i % 2) * 2)
                g.addWidget(label(v, wrap=True, selectable=True), i // 2, (i % 2) * 2 + 1)
            g.setColumnStretch(1, 1)
            g.setColumnStretch(3, 1)
            card.body.addLayout(g)
            self.sec_layout.addWidget(card)

        grouped = OrderedDict((key, []) for key, *_ in SECURITY_AREAS)
        grouped["other"] = []
        for c in checks:
            cid = (c.get("id") or "").lower()
            for key, _t, _d, prefixes in SECURITY_AREAS:
                if any(cid.startswith(p) for p in prefixes):
                    grouped[key].append(c)
                    break
            else:
                grouped["other"].append(c)
        meta = {key: (title, desc) for key, title, desc, _p in SECURITY_AREAS}
        meta["other"] = ("Other security checks", "")
        for key, items in grouped.items():
            if items:
                self.sec_layout.addWidget(self._security_area(*meta[key], items))

    @staticmethod
    def _security_facts(sec):
        facts = []
        tls = sec.get("tls") or {}
        if tls.get("negotiated"):
            facts.append(("TLS negotiated", str(tls["negotiated"])))
        if tls:
            facts.append(("Deprecated TLS 1.0/1.1", "Accepted" if tls.get("deprecated_protocols_accepted") else "Not accepted"))
        ssl = sec.get("ssl") or {}
        if ssl.get("issuer"):
            facts.append(("Certificate issuer", ssl["issuer"]))
        if ssl.get("not_after"):
            left = ssl.get("days_left")
            facts.append(("Certificate expires", f"{ssl['not_after']}" + (f" ({left} days)" if left is not None else "")))
        hsts = sec.get("hsts") or {}
        if hsts:
            facts.append(("HSTS", f"max-age {hsts.get('max_age')}" if hsts.get("present") else "Not sent"))
        waf = sec.get("waf") or {}
        if waf:
            facts.append(("WAF / edge protection", waf.get("waf_provider") or "None detected"))
        tech = sec.get("tech") or {}
        if tech.get("cms"):
            facts.append(("CMS", str(tech["cms"])))
        if tech.get("frameworks"):
            facts.append(("Frameworks", ", ".join(map(str, tech["frameworks"]))))
        if tech.get("hosting_cdn"):
            hc = tech["hosting_cdn"]
            facts.append(("Hosting / CDN", ", ".join(map(str, hc)) if isinstance(hc, (list, tuple)) else str(hc)))
        mail = sec.get("email_trust") or {}
        for rec in ("spf", "dmarc", "dkim"):
            if isinstance(mail.get(rec), dict):
                facts.append((rec.upper(), "Present" if mail[rec].get("present") else "Not found"))
        return facts

    def _security_area(self, title, desc, items):
        card = Card(padding=20, spacing=10)
        head = hbox(spacing=8)
        head.addWidget(label(title, "SectionTitle"))
        bad = [c for c in items if c.get("status") in ("fail", "warn")]
        good = [c for c in items if c.get("status") == "pass"]
        other = [c for c in items if c.get("status") not in ("fail", "warn", "pass")]
        head.addStretch(1)
        head.addWidget(label(f"{len(bad)} to fix · {len(good)} passed" if bad else f"All {len(good)} passed" if good
                             else "Informational", "Meta"))
        card.body.addLayout(head)
        if desc:
            card.body.addWidget(label(desc, "Meta"))
        bad.sort(key=lambda c: ["critical", "high", "medium", "low"].index((c.get("severity") or "low").lower())
                 if (c.get("severity") or "low").lower() in ("critical", "high", "medium", "low") else 9)
        for c in bad:
            card.body.addWidget(self._finding(c))
        for c in good + other:
            row = hbox(spacing=8)
            ic = QLabel()
            ok = c.get("status") == "pass"
            ic.setPixmap(icons.pixmap("check_circle" if ok else "info", C["success"] if ok else C["text_3"], 15))
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            t = label(f"{c.get('label')}", wrap=True)
            row.addWidget(t, 2)
            row.addWidget(label(c.get("detail") or ("Passed" if ok else "Not applicable"), "Meta", wrap=True), 3)
            card.body.addLayout(row)
        return card

    @staticmethod
    def _finding(c):
        sev = (c.get("severity") or "low").lower()
        fg = SEVERITY.get(sev, SEVERITY["info"])[0]
        box = QFrame()
        box.setStyleSheet(f"QFrame#Finding {{ background: {C['surface']}; border: 1px solid {C['border']}; "
                          f"border-left: 3px solid {fg}; border-radius: 6px; }}")
        box.setObjectName("Finding")
        lay = vbox(box, (14, 12, 14, 12), 8)
        head = hbox(spacing=8)
        head.addWidget(Badge.severity(sev))
        head.addWidget(Badge.status(c.get("status")))
        t = label(c.get("label") or "", wrap=True)
        t.setStyleSheet("font-weight: 600;")
        head.addWidget(t, 1)
        lay.addLayout(head)
        where = c.get("where") or []
        if where:
            lay.addWidget(_section("Affected", "\n".join(where[:10]) + (f"\n… and {len(where) - 10} more"
                                                                        if len(where) > 10 else ""), mono=True))
        lay.addWidget(_section("Evidence", c.get("detail")))
        if c.get("why"):
            lay.addWidget(_section("Why it matters", c["why"]))
        lay.addWidget(_section("Remediation", c.get("fix") or "No specific remediation recorded for this check."))
        return box

    # ---------------------------------------------------------------- recommendations
    def _render_recommendations(self, r):
        clear_layout(self.rec_layout)
        items = r.get("action_items") or []
        if not items:
            self.rec_layout.addWidget(label("No recommendations: every scored check passed.", "Muted"))
            return
        self.rec_layout.addWidget(label("Recommended order of work. Fix visibility and indexing first, then technical "
                                        "issues, then on-page content, authority and experience.", "Muted", wrap=True))
        tiers = OrderedDict()
        for a in sorted(items, key=lambda a: (a.get("tier") or 9, PRIORITY_ORDER.get(a.get("priority"), 9))):
            tiers.setdefault((a.get("tier") or 9, a.get("tier_title") or "Other improvements"), []).append(a)
        for (num, title), group in tiers.items():
            card = Card(padding=20, spacing=12)
            head = hbox(spacing=8)
            head.addWidget(label(f"{num}. {title}" if num != 9 else title, "SectionTitle"), 1)
            head.addWidget(label(fmt.plural(len(group), "item"), "Meta"))
            card.body.addLayout(head)
            for a in group:
                row = QFrame()
                row.setStyleSheet(f"QFrame {{ border-top: 1px solid {C['divider']}; }} QLabel {{ border: none; }}")
                rl = vbox(row, (0, 10, 0, 0), 4)
                top = hbox(spacing=8)
                top.addWidget(Badge.severity(a.get("priority")))
                name = label(a.get("label") or "", wrap=True)
                name.setStyleSheet("font-weight: 600; border: none;")
                top.addWidget(name, 1)
                n = len(a.get("where") or [])
                top.addWidget(label(f"{fmt.plural(n, 'URL')}" if n else "Site-wide", "Meta"))
                rl.addLayout(top)
                rl.addWidget(label(a.get("fix") or a.get("detail") or "", "Muted", wrap=True, selectable=True))
                card.body.addWidget(row)
            self.rec_layout.addWidget(card)

    # ---------------------------------------------------------------- reports
    def load_reports(self):
        aid = self.audit_id
        self.r_area.loading("Loading reports…")

        def done(data):
            if aid != self.audit_id:
                return
            rows = data.get("items") or []
            for row in rows:
                row.setdefault("normalized_domain", (self.audit or {}).get("normalized_domain"))
            self.r_table.set_rows(rows)
            has_pdf = any(r.get("format") == "pdf" for r in rows)
            self.r_notice.text.setText(PDF_UNAVAILABLE)
            self.r_notice.setVisible(not has_pdf and not data.get("pdf_supported", True))
            if rows:
                self.r_area.ready()
            else:
                self.r_area.empty("No report files", "Use Regenerate reports to create the client and technical "
                                                     "deliverables for this audit.", icon_name="report")

        self.fetch("reports", api.get, "/api/reports", params={"audit_id": aid, "page_size": 20},
                   on_done=done, on_error=self.r_area.error)
