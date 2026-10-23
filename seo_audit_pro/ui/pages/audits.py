# -*- coding: utf-8 -*-
"""Audits: every audit the user can see, plus the shared audit queue (bulk
audits, what is running and what is waiting) with live progress."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QMenu, QSpinBox, QStackedWidget, QWidget

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.lead_dialogs import start_audit, start_direct_audit
from seo_audit_pro.widgets.common import (MetricStrip, Segmented, Spinner, StatefulArea, button, hbox, label,
                                          vbox)
from seo_audit_pro.widgets.feedback import Dialog, SearchBox, combo, confirm, show_error, toast
from seo_audit_pro.widgets.table import ROW_ROLE, CellDelegate, Column, DataTable

STATUS_FILTER = [("All audits", ""), ("Active", "active"), ("Completed", "completed"), ("Failed", "failed"),
                 ("Cancelled", "cancelled")]


def progress_text(row):
    if row.get("status") == "queued":
        ahead = row.get("queue_position")
        if ahead is None:
            return "Waiting in the queue"
        return "Next in the queue" if not ahead else f"Waiting · {ahead:,} ahead"
    if row.get("status") == "running":
        return f"{row.get('percent') or 0}% · running"
    return fmt.duration(row.get("started_at"), row.get("completed_at"))


def _surface(widget):
    frame = QFrame()
    frame.setObjectName("Surface")
    vbox(frame).addWidget(widget)
    return frame


class _AuditCellDelegate(CellDelegate):
    """Score cells of unfinished audits show a dash rather than "Not audited"."""

    def paint(self, painter, option, index):
        col = self.model.columns[index.column()]
        row = index.data(ROW_ROLE)
        if col.kind == "score" and row.get("status") != "completed":
            saved = col.kind
            col.kind = "text"
            try:
                super().paint(painter, option, index)
            finally:
                col.kind = saved
            return
        super().paint(painter, option, index)


class LeadPickerDialog(Dialog):
    """Search the caller's (non-archived) leads and pick one to audit."""

    def __init__(self, page):
        super().__init__(page, "Start audit", "Enter a website URL to audit directly, or choose one or more leads "
                                              "from your Lead Bank.", 540)
        self.page = page
        self.lead = None
        self.leads = []
        self.direct_url = ""

        # Direct Website URL input
        u_box = vbox(spacing=4)
        u_box.addWidget(label("Website URL to audit:", "FieldLabel"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("e.g. https://example.com")
        self.url_edit.setStyleSheet("QLineEdit { border: 1px solid #D0D5DD; border-radius: 6px; padding: 7px 10px; font-size: 13px; }")
        self.url_edit.textChanged.connect(self._url_changed)
        self.url_edit.returnPressed.connect(self.choose)
        u_box.addWidget(self.url_edit)
        self.body.addLayout(u_box)

        # Divider / label
        self.body.addWidget(label("— Or choose from Lead Bank —", "Meta"))

        self.search = SearchBox("Search company or domain")
        self.search.search.connect(self.load)
        self.body.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(240)
        self.list.setStyleSheet("QListWidget { border: 1px solid #E4E4E7; border-radius: 6px; padding: 4px; }")
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda _i: self.choose())
        self.body.addWidget(self.list)

        # Pages to crawl / scrape
        prow = hbox(spacing=10)
        prow.addWidget(label("Pages to crawl / scrape:", "FieldLabel"))
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 500)
        self.pages_spin.setValue(50)
        self.pages_spin.setToolTip("Maximum pages to crawl for the audit (1-500)")
        prow.addWidget(self.pages_spin)
        prow.addStretch(1)
        self.body.addLayout(prow)

        self.status = label("", "Meta")
        self.body.addWidget(self.status)
        self.add_buttons("Start audit")
        self.primary.setEnabled(False)
        self.primary.clicked.connect(self.choose)
        self._gen = 0
        self.load("")

    def _url_changed(self, text):
        clean = text.strip()
        self.direct_url = clean
        if clean:
            self.list.clearSelection()
            self.leads = []
            self.lead = None
            self.primary.setEnabled(True)
            self.primary.setText("Start audit")
        else:
            self._selection_changed()

    def _selection_changed(self):
        selected = [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems() if it.data(Qt.ItemDataRole.UserRole)]
        self.leads = selected
        self.lead = selected[0] if len(selected) == 1 else None
        n = len(selected)
        if n > 0:
            self.direct_url = ""
            if self.url_edit.text():
                self.url_edit.blockSignals(True)
                self.url_edit.clear()
                self.url_edit.blockSignals(False)
            self.primary.setEnabled(True)
            if n > 1:
                self.primary.setText(f"Queue {n} audits")
            else:
                self.primary.setText("Start audit")
        elif not self.direct_url:
            self.primary.setEnabled(False)

    def load(self, text):
        self._gen += 1
        gen = self._gen
        self.status.setText("Searching…")
        params = {"page_size": 20, "sort": "company", "order": "asc"}
        if text:
            params["search"] = text

        def done(data):
            if gen != self._gen:
                return
            self.list.clear()
            items = data.get("items") or []
            for lead in items:
                audited = f"last score {lead['last_audit_score']}" if lead.get("last_audit_score") is not None else "not audited"
                it = QListWidgetItem(f"{lead['company']}\n{lead['normalized_domain']} · {audited}")
                it.setData(Qt.ItemDataRole.UserRole, lead)
                it.setSizeHint(QSize(0, 48))
                self.list.addItem(it)
            total = data.get("total", 0)
            self.status.setText("No matching leads." if not items else
                                (f"Showing {len(items)} of {total} leads — refine the search to narrow it down."
                                 if total > len(items) else f"{total} lead(s)"))

        def fail(err):
            if gen == self._gen:
                self.status.setText("")
                self.show_error(err)

        tasks.run(api.get, "/api/leads", params=params, on_done=done, on_error=fail, owner=self)

    def choose(self):
        url_text = self.url_edit.text().strip()
        if url_text:
            self.direct_url = url_text
            self.leads = []
            self.lead = None
            self.accept()
            return
        selected = [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems() if it.data(Qt.ItemDataRole.UserRole)]
        if not selected:
            cur = self.list.currentItem()
            if cur and cur.data(Qt.ItemDataRole.UserRole):
                selected = [cur.data(Qt.ItemDataRole.UserRole)]
        if selected:
            self.leads = selected
            self.lead = selected[0]
            self.accept()


# ---------------------------------------------------------------------------
# Queue tab
# ---------------------------------------------------------------------------
def _batch_state(row):
    if row["status"] != "active":
        return row["status"]
    return "running" if row["counts"]["running"] else "queued"


def _batch_progress(row):
    c = row["counts"]
    parts = [f"{row['done']:,} of {row['total']:,} done"]
    if c["failed"]:
        parts.append(f"{c['failed']:,} failed")
    if c["cancelled"]:
        parts.append(f"{c['cancelled']:,} cancelled")
    return " · ".join(parts)


class QueueView(QWidget):
    """Shared audit queue: capacity, bulk audits and what is running/waiting."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = vbox(self, spacing=14)
        self.strip = MetricStrip()
        self.m_running = self.strip.add("running", "Running now")
        self.m_waiting = self.strip.add("waiting", "Waiting")
        self.m_avg = self.strip.add("avg", "Average audit")
        self.m_eta = self.strip.add("eta", "Queue clears in")
        lay.addWidget(self.strip)

        head = hbox(spacing=8)
        head.addWidget(label("Bulk audits", "SectionTitle"), 1)
        self.batch_note = label("", "Meta")
        head.addWidget(self.batch_note)
        lay.addLayout(head)
        self.batches = DataTable([
            Column("label", "Selection", "strong", 260, stretch=True,
                   secondary=lambda r: f"by {r.get('created_by_name') or 'someone'} · {fmt.relative(r.get('created_at'))}"),
            Column("_state", "Status", "status", 120, render=_batch_state),
            Column("_progress", "Progress", "text", 220, render=_batch_progress),
            Column("_eta", "Time left", "text", 140,
                   render=lambda r: fmt.eta(r["eta_seconds"]) if r.get("eta_seconds") is not None else "—"),
            Column("_actions", "", "actions", 44),
        ], page_sizes=(10, 20, 50))
        self.batches.activated.connect(self.page.show_batch)
        self.batches.context_requested.connect(self._batch_menu)
        self.batches.page_changed.connect(lambda p: self._load_batches(p))
        self.b_area = StatefulArea(_surface(self.batches))
        self.b_area.retry.connect(self.load)
        self.b_area.setMinimumHeight(220)
        lay.addWidget(self.b_area, 2)

        lay.addWidget(label("Running and waiting", "SectionTitle"))
        self.active = DataTable([
            Column("company", "Website", "strong", 260, secondary=lambda r: r.get("normalized_domain"), stretch=True),
            Column("status", "Status", "status", 110),
            Column("_progress", "Progress", "text", 200, render=progress_text),
            Column("requested_by_name", "Started by", "text", 160),
            Column("created_at", "Queued", "relative", 120, optional=True),
        ], page_sizes=(25, 50, 100))
        self.active.activated.connect(lambda r: self.page.ws.navigate("audit", audit_id=r["id"]))
        self.active.page_changed.connect(lambda p: self._load_active(p))
        self.a_area = StatefulArea(_surface(self.active))
        self.a_area.retry.connect(self.load)
        lay.addWidget(self.a_area, 3)

        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.timeout.connect(self.load)
        self._poll = QTimer(self)
        self._poll.setInterval(15000 if api.live_updates() else 6000)  # fallback when live events are not arriving
        self._poll.timeout.connect(self.load)

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()

    def hideEvent(self, e):
        self._poll.stop()
        super().hideEvent(e)

    def load(self):
        tasks.run(api.get, "/api/audit-queue", on_done=self._render_queue, on_error=lambda _e: None, owner=self)
        self._load_batches(self.batches.page)
        self._load_active(self.active.page)

    def _render_queue(self, q):
        self.m_running.set(f"{q['running']} of {q['workers']}", "audits run at a time on the server")
        self.m_waiting.set(fmt.number(q["queued"]),
                           f"{q['mine_queued']:,} of them yours" if q.get("mine_queued")
                           else "none of them yours" if q["queued"] else "nothing waiting")
        avg = int(q["avg_seconds"])
        self.m_avg.set(f"{avg // 60}m {avg % 60:02d}s" if avg >= 60 else f"{avg}s", "from recent audits")
        idle = not q["queued"] and not q["running"]
        self.m_eta.set("Empty" if idle else fmt.eta(q["eta_seconds"]).capitalize(),
                       "ready for new audits" if idle else "at the current pace")
        self.batch_note.setText(f"Up to {q['batch_cap']:,} leads per bulk audit · up to {q['queue_cap']:,} waiting "
                                "per person")

    def _load_batches(self, page=1):
        if not self.batches.model.rows:
            self.b_area.loading("Loading bulk audits…")

        def done(data):
            self.batches.set_page(data)
            if data["items"]:
                self.b_area.ready()
            else:
                self.b_area.empty("No bulk audits yet", "Select leads in the Lead Bank and choose Audit to queue "
                                  "many audits at once.", icon_name="audit")
        self.page.fetch("batches", api.get, "/api/audit-batches", params={"page": page, "page_size": 10},
                        on_done=done, on_error=self.b_area.error)

    def _load_active(self, page=1):
        if not self.active.model.rows:
            self.a_area.loading("Loading the queue…")

        def done(data):
            self.active.set_page(data)
            if data["items"]:
                self.a_area.ready()
            else:
                self.a_area.empty("Nothing is running", "Audits you start appear here while they wait and run.",
                                  icon_name="audit")
        self.page.fetch("active", api.get, "/api/audits", params={"status": "active", "page": page, "page_size": 25},
                        on_done=done, on_error=self.a_area.error)

    def _batch_menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("list", C["text_2"]), "View its audits", lambda: self.page.show_batch(row))
        if row["status"] == "active":
            m.addAction(icons.icon("x", C["danger"]), "Cancel bulk audit…", lambda: self.cancel_batch(row))
        m.exec(pos)

    def cancel_batch(self, row):
        left = row["counts"]["queued"]
        if not confirm(self, "Cancel this bulk audit?",
                       f"{left:,} waiting audit(s) will be removed from the queue and the running ones stopped. "
                       "Finished audits and their reports are kept.", "Cancel bulk audit", danger=True):
            return
        tasks.run(api.post, f"/api/audit-batches/{row['id']}/cancel", {},
                  on_done=lambda r: (toast(self, f"Cancelled {r['cancelled']:,} waiting audit(s).", "success"),
                                     self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def on_realtime(self, ev):
        kind = ev.get("type")
        if kind == "audit_batch":
            for i, row in enumerate(self.batches.model.rows):
                if row["id"] == ev.get("batch_id"):
                    row["counts"] = ev["counts"]
                    row["status"] = ev.get("status", row["status"])
                    c = ev["counts"]
                    row["done"] = c["completed"] + c["failed"] + c["cancelled"]
                    m = self.batches.model
                    m.dataChanged.emit(m.index(i, 0), m.index(i, m.columnCount() - 1))
                    break
            self._reload.start(1500)  # counts, positions and time left settle after a moment
        elif kind == "audit_progress":
            model = self.active.model
            for i, row in enumerate(model.rows):
                if row.get("id") == ev.get("audit_id") and ev.get("status") == "running":
                    for k in ("status", "percent", "step"):
                        if k in ev:
                            row[k] = ev[k]
                    model.dataChanged.emit(model.index(i, 0), model.index(i, model.columnCount() - 1))
                    return
            self._reload.start(1500)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class AuditsPage(Page):
    title = "Audits"
    nav_key = "audits"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.params = {"page": 1, "page_size": 25}
        self.batch = None
        h = self.header("Audits", "Each audit is a real crawl by the audit engine. Progress and results update live.")
        start = h.add_action(button("Start audit", "primary", icon_name="play"))
        start.clicked.connect(self.start)
        self.tabs = Segmented([("All audits", "all"), ("Queue", "queue")])
        self.tabs.changed.connect(self._switch)
        self.body.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.views = QStackedWidget()
        self.body.addWidget(self.views, 1)

        all_view = QWidget()
        av = vbox(all_view, spacing=12)
        bar = hbox(spacing=8)
        self.search = SearchBox("Search company or domain")
        self.search.setMinimumWidth(280)
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search, 2)
        self.status = combo(STATUS_FILTER)
        self.status.currentIndexChanged.connect(lambda _i: self._set(status=self.status.currentData()))
        bar.addWidget(self.status)
        self.batch_chip = button("", "ghost", size="sm", icon_name="x", tooltip="Show all audits again")
        self.batch_chip.clicked.connect(lambda: self.show_batch(None))
        self.batch_chip.setVisible(False)
        bar.addWidget(self.batch_chip)
        bar.addStretch(1)
        self.live = hbox(spacing=6)
        self.live_spin = Spinner(14)
        self.live_label = label("", "Meta")
        self.live.addWidget(self.live_spin)
        self.live.addWidget(self.live_label)
        bar.addLayout(self.live)
        av.addLayout(bar)

        self.table = DataTable([
            Column("company", "Website", "strong", 260, secondary=lambda r: r.get("normalized_domain")),
            Column("status", "Status", "status", 120),
            Column("score", "Score", "score", 130,
                   render=lambda r: str(r["score"]) if r.get("status") == "completed" and r.get("score") is not None else "—"),
            Column("issues_found", "Issues", "number", 80, align="right",
                   render=lambda r: fmt.number(r["issues_found"]) if r.get("status") == "completed" else "—"),
            Column("requested_by_name", "Started by", "text", 160, optional=True),
            Column("created_at", "Started", "relative", 120, optional=True),
            Column("_progress", "Duration", "text", 150, render=progress_text, stretch=True),
        ])
        self.table.view.setItemDelegate(_AuditCellDelegate(self.table.model, self.table.view))
        self.table.page_changed.connect(lambda p: self._set(keep_page=True, page=p))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["id"]))
        self.area = StatefulArea(_surface(self.table))
        self.area.retry.connect(self.load)
        av.addWidget(self.area, 1)
        self.views.addWidget(all_view)
        self.queue = QueueView(self)
        self.views.addWidget(self.queue)
        self._announced = set()
        self._update_live()

    def activate(self, view=None, batch_id=None, **_):
        if batch_id:
            self.show_batch({"id": batch_id, "label": "Bulk audit"})
            tasks.run(api.get, f"/api/audit-batches/{batch_id}",
                      on_done=lambda r: self.batch_chip.setText(f"Bulk audit: {r['batch'].get('label') or 'selected'}"),
                      on_error=lambda _e: None, owner=self)
            return
        if view:
            self.tabs.set_value(view)
        self._switch(self.tabs.value())

    def _switch(self, view):
        self.views.setCurrentIndex(1 if view == "queue" else 0)
        if view == "queue":
            self.queue.load()
        else:
            self.load()

    def show_batch(self, row):
        """Show the audits of one bulk audit (``row`` None clears the filter)."""
        self.batch = row
        self.params.update(batch_id=row["id"] if row else "", page=1)
        self.batch_chip.setText(f"Bulk audit: {row.get('label') or 'selected'}" if row else "")
        self.batch_chip.setVisible(bool(row))
        self.tabs.set_value("all")
        self._switch("all")

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def load(self):
        if not self.table.model.rows:
            self.area.loading("Loading audits…")
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        self.fetch("list", api.get, "/api/audits", params=params, on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        self.table.set_page(data)
        self._update_live()
        if not data["items"]:
            if self.params.get("search") or self.params.get("status") or self.params.get("batch_id"):
                self.area.empty("No audits match these filters", "Clear the search or change the status filter.")
            else:
                self.area.empty("No audits yet", "Start an audit from a lead, or select many leads in the Lead Bank "
                                "and choose Audit.", icon_name="audit")
            return
        self.area.ready()

    def _update_live(self):
        running = sum(1 for r in self.table.model.rows if r.get("status") == "running")
        waiting = sum(1 for r in self.table.model.rows if r.get("status") == "queued")
        busy = bool(running or waiting)
        self.live_spin.setVisible(busy)
        self.live_label.setVisible(busy)
        parts = [f"{running} running"] if running else []
        if waiting:
            parts.append(f"{waiting} waiting")
        self.live_label.setText(" · ".join(parts))

    def start(self):
        dlg = LeadPickerDialog(self)
        if dlg.exec():
            max_pages = dlg.pages_spin.value()
            if getattr(dlg, "direct_url", ""):
                start_direct_audit(self, dlg.direct_url, max_pages=max_pages)
            elif dlg.leads:
                if len(dlg.leads) == 1:
                    start_audit(self, dlg.leads[0], max_pages=max_pages)
                else:
                    from seo_audit_pro.ui.pages.audit_dialogs import BulkAuditDialog
                    ids = [l["id"] for l in dlg.leads]
                    label_text = f"{len(ids)} selected leads"
                    dlg_bulk = BulkAuditDialog(self, {"lead_ids": ids, "label": label_text})
                    dlg_bulk.pages_spin.setValue(max_pages)
                    if dlg_bulk.exec() and dlg_bulk.result:
                        toast(self, f"{len(ids)} audits queued. Follow them in the Queue tab.", "success")
                        self.tabs.set_value("queue")
                        self._switch("queue")

    def on_realtime(self, ev):
        if self.views.currentIndex() == 1:
            self.queue.on_realtime(ev)
            return
        if ev.get("type") != "audit_progress":
            return
        model = self.table.model
        for i, row in enumerate(model.rows):
            if row.get("id") == ev.get("audit_id"):
                if ev.get("status") in ("completed", "failed", "cancelled"):
                    if self.isVisible():
                        self.load()
                    return
                for k in ("status", "percent", "step", "pages_processed", "pages_discovered"):
                    if k in ev:
                        row[k] = ev[k]
                model.dataChanged.emit(model.index(i, 0), model.index(i, model.columnCount() - 1))
                self._update_live()
                return
        # An audit started elsewhere: refresh once so it appears.
        aid = ev.get("audit_id")
        if (self.isVisible() and ev.get("status") == "running" and self.params.get("page", 1) == 1
                and aid not in self._announced):
            self._announced.add(aid)
            self.load()
