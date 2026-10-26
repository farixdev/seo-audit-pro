# -*- coding: utf-8 -*-
"""Scrape & Audit: a minimal portal opened from the sign-in screen, without an
account. Three pages: Scrape (Map Harvest), Audit (paste links, queue) and
Reports (audits saved on this computer). Nothing goes to the team server."""

import os
import re
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from seo_audit_pro import APP_NAME, quick_audit
from seo_audit_pro.widgets.common import PageHeader, button, hbox, label, logo_label, vbox
from seo_audit_pro.widgets.feedback import confirm, text_area, toast

AUDIT_COLS = ["Website", "Status", "Progress", "Score"]


def _item(text, color=None):
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(color))
    return it


def _table(cols):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return t


class _Body(QWidget):
    def __init__(self, title, subtitle):
        super().__init__()
        self.setObjectName("Canvas")
        self.lay = vbox(self, (28, 24, 28, 24), 14)
        self.head = PageHeader(title, subtitle)
        self.lay.addWidget(self.head)


class AuditPage(_Body):
    def __init__(self, runner, on_saved):
        super().__init__("Audit", "Paste website links, one per line. Each audit makes the client PDF, the Word "
                                  "reports and the audit data, saved under Reports.")
        self.runner, self.on_saved = runner, on_saved
        self.rows = {}
        self.links = text_area("mysite.com\nmysite.com, competitor1.com, competitor2.com | Business name", min_height=110)
        self.lay.addWidget(self.links)
        opts = hbox(spacing=10)
        opts.addWidget(label("Pages per site", "FieldLabel"))
        self.pages = QSpinBox()
        self.pages.setRange(1, 500)
        self.pages.setValue(50)
        opts.addWidget(self.pages)
        opts.addSpacing(14)
        opts.addWidget(label("At the same time", "FieldLabel"))
        self.slots = QSpinBox()
        self.slots.setRange(1, 6)
        self.slots.setValue(2)
        opts.addWidget(self.slots)
        opts.addStretch(1)
        self.status = label("Ready", "Meta")
        opts.addWidget(self.status)
        self.stop_btn = button("Stop queue")
        self.stop_btn.clicked.connect(self._stop)
        opts.addWidget(self.stop_btn)
        self.start_btn = button("Start audits", "primary")
        self.start_btn.clicked.connect(self._start)
        opts.addWidget(self.start_btn)
        self.lay.addLayout(opts)
        self.table = _table(AUDIT_COLS)
        for c, w in ((1, 120), (2, 340), (3, 90)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.lay.addWidget(self.table, 1)
        runner.signals.progress.connect(self._progress)
        runner.signals.finished.connect(self._finished)
        self._next_id = 0
        timer = QTimer(self)
        timer.setInterval(1000)
        timer.timeout.connect(self._tick)
        timer.start()

    @staticmethod
    def parse(text):
        """"site, competitor, competitor | Business name" per line."""
        jobs, seen = [], set()
        for line in text.splitlines():
            line, _, business = line.partition("|")
            parts = [p for p in re.split(r"[\s,;]+", line.strip()) if p and "." in p]
            if parts and quick_audit.domain_of(parts[0]) not in seen:
                seen.add(quick_audit.domain_of(parts[0]))
                jobs.append({"site": parts[0], "competitors": parts[1:4], "business": business.strip()})
        return jobs

    def queue(self, jobs):
        if not jobs:
            return 0
        self.runner.cancel.clear()
        self.runner.slots = self.slots.value()
        self.runner.pages = self.pages.value()
        for job in jobs:
            job_id = self._next_id
            self._next_id += 1
            r = self.table.rowCount()
            self.table.insertRow(r)
            shown = job["site"] + (f"  (+{len(job['competitors'])} competitors)" if job.get("competitors") else "")
            for c, text in enumerate([shown, "Waiting", "In the queue", ""]):
                self.table.setItem(r, c, _item(text))
            self.rows[job_id] = {"row": r, "report": None}
            self.runner.add(job_id, job)
        return len(jobs)

    def _start(self):
        n = self.queue(self.parse(self.links.toPlainText()))
        if not n:
            toast(self, "Paste at least one website link.", "info")
            return
        self.links.clear()
        toast(self, f"{n} audit{'s' if n != 1 else ''} added to the queue.", "success")

    def _stop(self):
        self.runner.stop()

    def _progress(self, job_id, pct, step):
        r = self.rows.get(job_id, {}).get("row")
        if r is not None:
            self.table.item(r, 1).setText(f"Running {pct}%")
            self.table.item(r, 2).setText(step)

    def _finished(self, job_id, ok, info, report):
        entry = self.rows.get(job_id)
        if not entry:
            return
        r = entry["row"]
        self.table.setItem(r, 1, _item("Done" if ok else ("Stopped" if info == "Stopped" else "Failed"),
                                       "#067647" if ok else "#B42318"))
        self.table.item(r, 2).setText("Saved — double-click to open the report" if ok else info)
        self.table.item(r, 3).setText(info if ok else "")
        if ok:
            entry["report"] = report
            self.on_saved()

    def _open_row(self, row, _col):
        for entry in self.rows.values():
            if entry["row"] == row and entry["report"]:
                QDesktopServices.openUrl(QUrl.fromLocalFile(entry["report"]))

    def _tick(self):
        running, waiting = self.runner.busy()
        self.status.setText(f"{running} running · {waiting} waiting" if running or waiting else "Ready")
        self.start_btn.setText("Add to queue" if running or waiting else "Start audits")


class ReportsPage(_Body):
    COLS = ["Website", "Score", "Pages", "Finished"]

    def __init__(self):
        super().__init__("Reports", "Audits made on this computer. Double-click to open the client PDF.")
        folder = button("Open reports folder", icon_name="external")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(quick_audit.reports_dir())))
        self.head.add_action(folder)
        self.table = _table(self.COLS)
        for c, w in ((1, 90), (2, 80), (3, 160)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._open)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.lay.addWidget(self.table, 1)
        self.empty = label("No audits yet. Run one from Audit, or scrape businesses and audit their websites.", "Meta")
        self.lay.addWidget(self.empty)
        self.items = []

    def refresh(self):
        self.items = quick_audit.saved_audits()
        self.table.setRowCount(0)
        for info in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = info.get("domain") or ""
            if info.get("business"):
                name += f"  ·  {info['business']}"
            try:
                when = datetime.fromisoformat(info.get("finished_at") or "").strftime("%b %d, %Y %H:%M")
            except ValueError:
                when = ""
            score = info.get("score")
            for c, text in enumerate([name, "—" if score is None else f"{score}/100", str(info.get("pages") or ""), when]):
                self.table.setItem(r, c, _item(text))
        self.empty.setVisible(not self.items)

    def _report(self, row):
        info = self.items[row] if 0 <= row < len(self.items) else None
        return os.path.join(info["folder"], info.get("report") or "") if info else None

    def _open(self, row, _col=0):
        path = self._report(row)
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction("Open client report", lambda: self._open(row))
        m.addAction("Open folder (Word reports, data)",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.items[row]["folder"])))
        m.exec(self.table.viewport().mapToGlobal(pos))


class ScrapePage(QWidget):
    audit_requested = Signal(list)

    def __init__(self):
        super().__init__()
        from seo_audit_pro.map_harvest.ui.screen_input import InputScreen
        from seo_audit_pro.map_harvest.ui.screen_results import ResultsScreen
        from seo_audit_pro.map_harvest.ui.theme import QSS
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        box = QWidget()
        box.setObjectName("MapHarvestContainer")
        box.setStyleSheet(QSS)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.input_screen = InputScreen()
        self.results_screen = ResultsScreen()
        self.results_screen.quick_mode = True
        self.results_screen.lead_bank_btn.hide()
        self.stack.addWidget(self.input_screen)
        self.stack.addWidget(self.results_screen)
        inner.addWidget(self.stack)
        lay.addWidget(box)
        self.input_screen.start_signal.connect(self._start)
        self.results_screen.stop_signal.connect(self.results_screen.stop_worker)
        self.results_screen.home_signal.connect(lambda: self.stack.setCurrentIndex(0))
        self.results_screen.audit_signal.connect(self.audit_requested.emit)

    def _start(self, domains, areas, fields, headless=False, max_results=50, export_dir="", filters=None):
        self.results_screen.setup(domains, areas, fields, headless, max_results, export_dir, filters or {})
        self.stack.setCurrentIndex(1)
        self.results_screen.start_worker()

    def busy(self):
        w = getattr(self.results_screen, "worker", None)
        return bool(w and w.isRunning())

    def shutdown(self):
        w = getattr(self.results_screen, "worker", None)
        if w is None or not w.isRunning():
            return
        self.results_screen._stopped_by_user = True
        w.stop()
        if not w.wait(3000):
            w.abort()
            w.wait(3000)


class QuickPortal(QWidget):
    """Sidebar with Scrape, Audit and Reports; "Sign in" goes back to the sign-in screen."""
    leave = Signal()

    def __init__(self):
        super().__init__()
        from seo_audit_pro.ui.shell import NavItem
        self.runner = quick_audit.Runner()
        row = hbox(self)
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        sl = vbox(side, (10, 14, 10, 12), 2)
        brand = hbox(spacing=10)
        brand.setContentsMargins(4, 0, 0, 0)
        brand.addWidget(logo_label(30))
        name = label(APP_NAME)
        name.setStyleSheet("font-size: 16px; font-weight: 700; color: #18181B;")
        brand.addWidget(name, 1)
        sl.addLayout(brand)
        sl.addWidget(label("Scrape & Audit", "SidebarSection"))
        sl.addSpacing(10)
        self.nav = {}
        for key, text, icon_name in (("scrape", "Scrape", "map_pin"), ("audit", "Audit", "audit"),
                                     ("reports", "Reports", "report")):
            item = NavItem(key, text, icon_name)
            item.clicked.connect(lambda _c=False, k=key: self.go(k))
            sl.addWidget(item)
            self.nav[key] = item
        sl.addStretch(1)
        back = button("Sign in to a workspace", "link")
        back.clicked.connect(self._leave)
        sl.addWidget(back)
        row.addWidget(side)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Canvas")
        self.reports = ReportsPage()
        self.audit = AuditPage(self.runner, self.reports.refresh)
        self.scrape = ScrapePage()
        self.scrape.audit_requested.connect(self._audit_scraped)
        self.pages = {"scrape": self.scrape, "audit": self.audit, "reports": self.reports}
        for w in self.pages.values():
            self.stack.addWidget(w)
        row.addWidget(self.stack, 1)

    def start(self):
        self.go("scrape")

    def go(self, key):
        for k, item in self.nav.items():
            item.set_active(k == key)
        if key == "reports":
            self.reports.refresh()
        self.stack.setCurrentWidget(self.pages[key])

    def _audit_scraped(self, targets):
        n = self.audit.queue([{"site": t["site"], "competitors": [], "business": t.get("business") or ""}
                              for t in targets])
        self.go("audit")
        toast(self.audit, f"{n} website{'s' if n != 1 else ''} added to the audit queue.", "success")

    def busy(self):
        running, waiting = self.runner.busy()
        return bool(running or waiting) or self.scrape.busy()

    def stop(self):
        self.runner.stop()
        self.scrape.shutdown()

    def _leave(self):
        if self.busy() and not confirm(self, "Leave Scrape & Audit?",
                                       "Scraping or audits are still running. Stop them and go to sign in?",
                                       "Stop and leave", danger=True):
            return
        self.stop()
        self.leave.emit()
