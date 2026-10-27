# -*- coding: utf-8 -*-
"""Scrape & Audit: the portal for people working alone, opened from the
sign-in screen without an account. Everything stays on this computer:

  Home      overview: leads, audits, scores, recent work
  My Leads  a personal lead bank (scraped, imported from CSV/Excel, typed in)
  Scrape    Google Maps scraping; every business found is saved to My Leads
  Audit     audit queue (paste links, or audit leads straight from My Leads)
  Reports   finished audits with their client PDF and Word reports

Leads, audits and reports are linked by the website domain (see solo_db)."""

import json
import os
import re
from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QGridLayout, QHeaderView, QMenu, QSpinBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from seo_audit_pro import APP_NAME, quick_audit, solo_db
from seo_audit_pro.core import settings, tasks
from seo_audit_pro.widgets.common import PageHeader, button, hbox, label, logo_label, signature, vbox
from seo_audit_pro.widgets.feedback import (Dialog, Field, SearchBox, combo, confirm, line_edit, show_error,
                                            text_area, toast)

# Palette: Charcoal Grey and Moonlit Silver, with tints in between.
CHARCOAL = "#2A2A2A"
CHARCOAL_2 = "#4A4A4A"
STEEL = "#7A7A7A"
SILVER = "#C0C0C0"
SILVER_SOFT = "#E4E4E4"
SILVER_WASH = "#F3F3F3"
# Quiet score colours (only where a score is shown)
GOOD, FAIR, POOR = "#3F7D58", "#B7833A", "#B5483F"

AUDIT_COLS = ["Website", "Status", "Progress", "Score"]
LEAD_COLS = ["Company", "Website", "Niche", "Area", "Phone", "Rating", "Status", "Score"]
STATUS_TEXT = {"new": "New", "queued": "Queued", "running": "Auditing", "audited": "Audited", "failed": "Failed"}


def _score_color(score):
    if score is None:
        return STEEL
    return GOOD if score >= 80 else (FAIR if score >= 50 else POOR)


def _item(text, color=None, bold=False):
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    return it


def _table(cols, multi=False):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(36)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if multi
                       else QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.setStyleSheet(f"QTableWidget {{ background: #FFFFFF; alternate-background-color: {SILVER_WASH}; "
                    f"border: 1px solid {SILVER_SOFT}; border-radius: 10px; selection-background-color: {SILVER_SOFT}; "
                    f"selection-color: {CHARCOAL}; }}"
                    f"QHeaderView::section {{ background: #FFFFFF; color: {STEEL}; border: none; "
                    f"border-bottom: 1px solid {SILVER_SOFT}; padding: 8px 10px; font-weight: 600; font-size: 11px; }}"
                    "QTableWidget::item { padding: 4px 10px; }")
    return t


class _Body(QWidget):
    def __init__(self, title, subtitle):
        super().__init__()
        self.setObjectName("Canvas")
        self.lay = vbox(self, (28, 24, 28, 24), 14)
        self.head = PageHeader(title, subtitle)
        self.lay.addWidget(self.head)


# ---------------------------------------------------------------------------
# Small painted charts (charcoal / silver)
# ---------------------------------------------------------------------------
class StatCard(QFrame):
    """A big number on a charcoal or silver tile."""

    def __init__(self, title, dark=False):
        super().__init__()
        bg, fg, sub = (CHARCOAL, "#FFFFFF", SILVER) if dark else ("#FFFFFF", CHARCOAL, STEEL)
        self.setStyleSheet(f"StatCard {{ background: {bg}; border: 1px solid {CHARCOAL if dark else SILVER_SOFT}; "
                           f"border-radius: 14px; }} QLabel {{ background: transparent; }}")
        lay = vbox(self, (18, 16, 18, 16), 4)
        self.title = label(title.upper())
        self.title.setStyleSheet(f"color: {sub}; font-size: 10.5px; font-weight: 700; letter-spacing: 1px;")
        self.value = label("—")
        self.value.setStyleSheet(f"color: {fg}; font-size: 30px; font-weight: 800;")
        self.hint = label("")
        self.hint.setStyleSheet(f"color: {sub}; font-size: 11.5px;")
        for w in (self.title, self.value, self.hint):
            lay.addWidget(w)

    def set(self, value, hint=""):
        self.value.setText(str(value))
        self.hint.setText(hint)


def _round_pen(color, width):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class Ring(QWidget):
    """Average score ring."""

    def __init__(self, size=132):
        super().__init__()
        self.setFixedSize(size, size)
        self.score = None

    def set(self, score):
        self.score = score
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(9, 9, self.width() - 18, self.height() - 18)
        p.setPen(_round_pen(SILVER_SOFT, 12))
        p.drawArc(r, 0, 360 * 16)
        if self.score is not None:
            p.setPen(_round_pen(_score_color(self.score), 12))
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0, min(100, self.score)) / 100))
        p.setPen(QColor(CHARCOAL))
        f = QFont(self.font())
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, -8, 0, -8), Qt.AlignmentFlag.AlignCenter,
                   "—" if self.score is None else str(self.score))
        f.setPixelSize(11)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(STEEL))
        p.drawText(self.rect().adjusted(0, 30, 0, 30), Qt.AlignmentFlag.AlignCenter, "avg score")


class BarList(QWidget):
    """Horizontal bars: [(label, value, colour)]."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.setMinimumHeight(40)

    def set(self, items):
        self.items = items
        self.setMinimumHeight(max(40, 30 * len(items)))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.items:
            p.setPen(QColor(STEEL))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing yet")
            return
        top = max(v for _l, v, _c in self.items) or 1
        label_w, num_w = min(150, int(self.width() * 0.38)), 40
        bar_w = max(10, self.width() - label_w - num_w - 12)
        for i, (text, value, color) in enumerate(self.items):
            y = i * 30 + 6
            p.setPen(QColor(CHARCOAL_2))
            p.drawText(QRectF(0, y, label_w - 8, 18), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, label_w - 8))
            path = QPainterPath()
            path.addRoundedRect(QRectF(label_w, y + 4, bar_w, 10), 5, 5)
            p.fillPath(path, QColor(SILVER_SOFT))
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(label_w, y + 4, max(10, bar_w * value / top), 10), 5, 5)
            p.fillPath(fill, QColor(color))
            p.setPen(QColor(CHARCOAL))
            p.drawText(QRectF(label_w + bar_w + 6, y, num_w, 18), Qt.AlignmentFlag.AlignVCenter, str(value))


class Columns(QWidget):
    """Leads added per day over the last 14 days."""

    def __init__(self):
        super().__init__()
        self.days = []
        self.setMinimumHeight(120)

    def set(self, daily):
        counts = {d["day"]: d["n"] for d in daily}
        today = datetime.now().date()
        self.days = [((today - timedelta(days=13 - i)), counts.get(str(today - timedelta(days=13 - i)), 0))
                     for i in range(14)]
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.days:
            return
        top = max(n for _d, n in self.days) or 1
        h = self.height() - 22
        w = self.width() / len(self.days)
        f = QFont(self.font())
        f.setPixelSize(9)
        p.setFont(f)
        for i, (day, n) in enumerate(self.days):
            bh = max(3, (h - 14) * n / top) if n else 3
            x = i * w + w * 0.2
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, h - bh, w * 0.6, bh), 3, 3)
            p.fillPath(path, QColor(CHARCOAL if i == len(self.days) - 1 else (SILVER if n else SILVER_SOFT)))
            if n:
                p.setPen(QColor(CHARCOAL_2))
                p.drawText(QRectF(i * w, h - bh - 14, w, 12), Qt.AlignmentFlag.AlignCenter, str(n))
            if i % 2 == 1 or i == len(self.days) - 1:
                p.setPen(QColor(STEEL))
                p.drawText(QRectF(i * w - 6, h + 4, w + 12, 14), Qt.AlignmentFlag.AlignCenter, day.strftime("%d %b"))


def _panel(title, widget, subtitle=""):
    box = QFrame()
    box.setStyleSheet(f"QFrame#Panel {{ background: #FFFFFF; border: 1px solid {SILVER_SOFT}; border-radius: 14px; }}")
    box.setObjectName("Panel")
    lay = vbox(box, (18, 16, 18, 16), 10)
    t = label(title)
    t.setStyleSheet(f"color: {CHARCOAL}; font-size: 14px; font-weight: 700;")
    lay.addWidget(t)
    if subtitle:
        s = label(subtitle, wrap=True)
        s.setStyleSheet(f"color: {STEEL}; font-size: 11.5px;")
        lay.addWidget(s)
    if isinstance(widget, QWidget):
        lay.addWidget(widget, 1)
    else:
        lay.addLayout(widget, 1)
    return box


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
class HomePage(_Body):
    go = Signal(str)

    def __init__(self):
        super().__init__("Welcome back", "Your leads, audits and reports — all saved on this computer.")
        for text, key, variant in (("Scrape Google Maps", "scrape", None), ("Import leads", "import", None),
                                   ("Audit websites", "audit", "primary")):
            b = button(text, variant)
            b.clicked.connect(lambda _c=False, k=key: self.go.emit(k))
            self.head.add_action(b)
        cards = hbox(spacing=12)
        self.c_leads, self.c_sites = StatCard("Leads", dark=True), StatCard("With a website")
        self.c_audited, self.c_failed = StatCard("Audited"), StatCard("Need attention")
        for c in (self.c_leads, self.c_sites, self.c_audited, self.c_failed):
            cards.addWidget(c, 1)
        self.lay.addLayout(cards)
        grid = QGridLayout()
        grid.setSpacing(12)
        ring_box = hbox(spacing=18)
        self.ring = Ring()
        ring_box.addWidget(self.ring)
        self.spread = BarList()
        ring_box.addWidget(self.spread, 1)
        grid.addWidget(_panel("Website health", ring_box, "Scores of the audited leads"), 0, 0)
        self.daily = Columns()
        grid.addWidget(_panel("Leads added", self.daily, "Last 14 days"), 0, 1)
        self.niches = BarList()
        grid.addWidget(_panel("Top niches", self.niches), 1, 0)
        self.recent = QVBoxLayout()
        self.recent.setSpacing(6)
        grid.addWidget(_panel("Recent audits", self.recent), 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid, 1)

    def refresh(self):
        tasks.run(solo_db.stats, on_done=self._render, on_error=lambda _e: None, owner=self)

    def _render(self, s):
        total = s["total"]
        self.c_leads.set(f"{total:,}", f"{len(s['niches'])} niche{'s' if len(s['niches']) != 1 else ''}")
        self.c_sites.set(f"{s['with_site']:,}", f"{round(100 * s['with_site'] / total)}% of leads" if total else "")
        self.c_audited.set(f"{s['audited']:,}", f"{s['running']} in the queue" if s["running"] else "")
        self.c_failed.set(f"{s['failed']:,}", "audits that could not finish" if s["failed"] else "all good")
        self.ring.set(s["avg_score"])
        self.spread.set([("Good (80+)", s["good"], GOOD), ("Fair (50–79)", s["fair"], FAIR),
                         ("Poor (under 50)", s["poor"], POOR)] if s["audited"] else [])
        self.daily.set(s["daily"])
        self.niches.set([(n["value"], n["n"], CHARCOAL if i == 0 else SILVER) for i, n in enumerate(s["niches"])])
        while self.recent.count():
            it = self.recent.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not s["recent"]:
            empty = label("No audits yet. Audit a lead from My Leads or paste websites in Audit.", wrap=True)
            empty.setStyleSheet(f"color: {STEEL};")
            self.recent.addWidget(empty)
        for r in s["recent"]:
            row = QWidget()
            h = hbox(row, spacing=10)
            name = label(r["company"] or r["domain"])
            name.setStyleSheet(f"color: {CHARCOAL}; font-weight: 600;")
            h.addWidget(name, 1)
            sc = label("—" if r["score"] is None else str(r["score"]))
            sc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sc.setFixedWidth(42)
            sc.setStyleSheet(f"color: #FFFFFF; background: {_score_color(r['score'])}; border-radius: 9px; "
                             "font-weight: 700; padding: 2px 0;")
            h.addWidget(sc)
            self.recent.addWidget(row)
        self.recent.addStretch(1)


# ---------------------------------------------------------------------------
# My Leads
# ---------------------------------------------------------------------------
class ImportDialog(Dialog):
    def __init__(self, parent, path, rows, mapping, niche, area):
        super().__init__(parent, "Import leads", f"{os.path.basename(path)} · {len(rows):,} rows", 520)
        found = [f"{k.replace('_', ' ').title()} ← \"{v}\"" for k, v in mapping.items() if v]
        info = label("Columns found: " + (", ".join(found) or "none"), wrap=True)
        info.setStyleSheet(f"color: {STEEL};")
        self.body.addWidget(info)
        if not mapping.get("website") and not mapping.get("company"):
            self.body.addWidget(label("No website or business-name column was found, so nothing can be imported.",
                                      "FieldError", wrap=True))
        self.niche = line_edit("e.g. Plumbers", niche)
        self.area = line_edit("e.g. Dallas", area)
        self.body.addWidget(Field("Niche", self.niche, "Used to group and filter your leads."))
        self.body.addWidget(Field("Area / city", self.area, "Used when a row has no city of its own."))
        self.add_buttons("Import")
        self.primary.setEnabled(bool(mapping.get("website") or mapping.get("company")))
        self.primary.clicked.connect(self.accept)


class LeadsPage(_Body):
    audit_leads = Signal(list)

    def __init__(self):
        super().__init__("My Leads", "Your own lead bank: scraped businesses, imported files and leads you add. "
                                     "Audit them in one click; results come back here.")
        for text, slot, icon in (("Import CSV / Excel", self.import_file, "upload"), ("Add lead", self._add, "plus"),
                                 ("Export", self._export, "download")):
            b = button(text, icon_name=icon)
            b.clicked.connect(slot)
            self.head.add_action(b)
        bar = hbox(spacing=8)
        self.search = SearchBox("Search company, website, phone or address")
        self.search.search.connect(lambda _t: self.refresh())
        bar.addWidget(self.search, 1)
        self.niche = combo([("All niches", "")])
        self.area = combo([("All areas", "")])
        self.status = combo([("Any status", ""), ("Not audited", "not_audited"), ("Audited", "audited"),
                             ("Failed", "failed"), ("In the queue", "queued"), ("No website", "no_website")])
        for c in (self.niche, self.area, self.status):
            c.setMinimumWidth(150)
            c.currentIndexChanged.connect(lambda _i: self.refresh())
            bar.addWidget(c)
        self.lay.addLayout(bar)
        self.table = _table(LEAD_COLS, multi=True)
        for c, w in ((1, 190), (2, 120), (3, 110), (4, 130), (5, 70), (6, 90), (7, 60)):
            self.table.setColumnWidth(c, w)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(self._open)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.lay.addWidget(self.table, 1)
        foot = hbox(spacing=8)
        self.count = label("")
        self.count.setStyleSheet(f"color: {STEEL};")
        foot.addWidget(self.count, 1)
        self.delete_btn = button("Delete")
        self.delete_btn.clicked.connect(self._delete)
        foot.addWidget(self.delete_btn)
        self.audit_btn = button("Audit selected", "primary", icon_name="audit")
        self.audit_btn.clicked.connect(self._audit_selected)
        foot.addWidget(self.audit_btn)
        self.lay.addLayout(foot)
        self.rows = []
        self._selection()

    # -- data ------------------------------------------------------------------
    def refresh(self):
        args = (self.search.text().strip(), self.niche.currentData() or "", self.area.currentData() or "",
                self.status.currentData() or "")
        tasks.run(lambda: (solo_db.list_leads(*args), solo_db.facets()), on_done=self._render,
                  on_error=lambda e: show_error(self, e, "Could not read your leads"), owner=self)

    def _fill_combo(self, box, items, all_text):
        current = box.currentData() or ""
        box.blockSignals(True)
        box.clear()
        box.addItem(all_text, "")
        for it in items:
            box.addItem(f"{it['value']} ({it['n']:,})", it["value"])
        box.setCurrentIndex(max(0, box.findData(current)))
        box.blockSignals(False)

    def _render(self, payload):
        self.rows, facets = payload
        self._fill_combo(self.niche, facets["niches"], "All niches")
        self._fill_combo(self.area, facets["areas"], "All areas")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for lead in self.rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            status = lead["status"]
            cells = [
                _item(lead["company"], CHARCOAL, bold=True),
                _item(lead["domain"] or "no website", None if lead["domain"] else STEEL),
                _item(lead["niche"]), _item(lead["area"]), _item(lead["phone"]),
                _item(f"★ {lead['rating']:.1f}" if lead["rating"] is not None else ""),
                _item(STATUS_TEXT.get(status, status), {"audited": GOOD, "failed": POOR, "running": CHARCOAL,
                                                        "queued": CHARCOAL_2}.get(status, STEEL), bold=status != "new"),
                _item("" if lead["score"] is None else str(lead["score"]), _score_color(lead["score"]), bold=True),
            ]
            if status == "failed" and lead.get("error"):
                cells[6].setToolTip(lead["error"])
            cells[0].setData(Qt.ItemDataRole.UserRole, lead["id"])
            for c, it in enumerate(cells):
                self.table.setItem(r, c, it)
        self.count.setText(f"{len(self.rows):,} lead{'s' if len(self.rows) != 1 else ''}"
                           + (" (first 5,000 shown)" if len(self.rows) >= 5000 else ""))
        self._selection()

    def _selected(self):
        ids = {self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole) for i in self.table.selectedIndexes()}
        return [lead for lead in self.rows if lead["id"] in ids]

    def _selection(self):
        sel = self._selected()
        n = len([lead for lead in sel if lead["domain"]])
        self.audit_btn.setText(f"Audit {n} selected" if n else "Audit selected")
        self.audit_btn.setEnabled(bool(n))
        self.delete_btn.setEnabled(bool(sel))

    # -- actions ---------------------------------------------------------------
    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import leads", os.path.expanduser("~"),
                                              "Lead files (*.csv *.tsv *.txt *.xlsx)")
        if not path:
            return
        toast(self, "Reading the file…", "info", 2000)

        def read():
            return solo_db.read_file(path)

        def ready(res):
            rows, mapping, niche, area = res
            dlg = ImportDialog(self, path, rows, mapping, niche, area)
            if not dlg.exec():
                return
            n_, a_ = dlg.niche.text().strip(), dlg.area.text().strip()
            tasks.run(solo_db.add_many, rows, "file", n_, a_, on_done=done,
                      on_error=lambda e: show_error(self, e, "Import failed"), owner=self)

        def done(res):
            toast(self, f"{res['added']:,} leads added, {res['updated']:,} already in My Leads"
                        + (f", {res['skipped']:,} rows without a name or website skipped" if res["skipped"] else "")
                        + ".", "success", 6000)
            self.refresh()

        tasks.run(read, on_done=ready, on_error=lambda e: show_error(self, e, "That file could not be read"), owner=self)

    def _add(self):
        dlg = Dialog(self, "Add a lead", "", 460)
        fields = {k: line_edit(ph) for k, ph in (("company", "Business name"), ("website", "website.com"),
                                                ("phone", "Phone"), ("niche", "e.g. Dentists"), ("area", "e.g. Toronto"))}
        for k, w in fields.items():
            dlg.body.addWidget(Field(k.title(), w))
        dlg.add_buttons("Add lead")

        def save():
            row = {k: w.text().strip() for k, w in fields.items()}
            if not row["company"] and not solo_db.domain_of(row["website"]):
                dlg.show_error(ValueError("Enter a business name or a website."))
                return
            res = solo_db.add_many([row], "manual")
            dlg.accept()
            toast(self, "Lead added." if res["added"] else "That website is already in My Leads.", "success")
            self.refresh()

        dlg.primary.clicked.connect(save)
        dlg.exec()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export leads", os.path.join(os.path.expanduser("~"), "My leads.csv"),
                                              "CSV (*.csv)")
        if path:
            try:
                solo_db.export_csv(path, self._selected() or self.rows)
                toast(self, f"Saved {os.path.basename(path)}.", "success")
            except OSError as e:
                show_error(self, e, "Could not save the file")

    def _delete(self):
        sel = self._selected()
        if sel and confirm(self, "Delete leads?", f"Delete {len(sel)} lead{'s' if len(sel) != 1 else ''} from My Leads? "
                                                  "Their saved reports stay in Reports.", "Delete", danger=True):
            solo_db.delete([lead["id"] for lead in sel])
            self.refresh()

    def _audit_selected(self, leads=None):
        leads = [lead for lead in (leads or self._selected()) if lead["domain"]]
        if leads:
            self.audit_leads.emit(leads)

    def _open(self, row, _col=0):
        lead = next((x for x in self.rows if x["id"] == self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)), None)
        if not lead:
            return
        if lead["report"] and os.path.isfile(lead["report"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(lead["report"]))
        elif lead["website"]:
            QDesktopServices.openUrl(QUrl(lead["website"] if "://" in lead["website"] else "https://" + lead["website"]))

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        lead_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        lead = next((x for x in self.rows if x["id"] == lead_id), None)
        if not lead:
            return
        m = QMenu(self)
        if lead["domain"]:
            m.addAction("Audit this website", lambda: self._audit_selected([lead]))
        if lead["report"] and os.path.isfile(lead["report"]):
            m.addAction("Open client report", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(lead["report"])))
        if lead["website"]:
            m.addAction("Open website", lambda: self._open(row))
        if lead["maps_url"]:
            m.addAction("Open on Google Maps", lambda: QDesktopServices.openUrl(QUrl(lead["maps_url"])))
        if lead["error"]:
            m.addAction("Why did the audit fail?", lambda: toast(self, lead["error"], "error", 9000))
        m.addSeparator()
        m.addAction("Delete", self._delete)
        m.exec(self.table.viewport().mapToGlobal(pos))


# ---------------------------------------------------------------------------
# Audit queue
# ---------------------------------------------------------------------------
class AuditPage(_Body):
    def __init__(self, runner, on_saved):
        super().__init__("Audit", "Paste website links, one per line — or audit leads from My Leads. Each audit "
                                  "makes the client PDF, the Word reports and the audit data, saved under Reports.")
        self.runner, self.on_saved = runner, on_saved
        self.rows = {}
        self.links = text_area("mysite.com\nmysite.com, competitor1.com, competitor2.com | Business name", min_height=100)
        self.lay.addWidget(self.links)
        opts = hbox(spacing=10)
        opts.addWidget(label("Pages per site", "FieldLabel"))
        self.pages = QSpinBox()
        self.pages.setRange(1, 500)
        self.pages.setValue(int(settings.get("quick_pages") or 50))
        opts.addWidget(self.pages)
        opts.addSpacing(14)
        opts.addWidget(label("At the same time", "FieldLabel"))
        self.slots = QSpinBox()
        self.slots.setRange(1, 6)
        self.slots.setValue(int(settings.get("quick_slots") or 2))
        opts.addWidget(self.slots)
        opts.addSpacing(14)
        opts.addWidget(label("Google API key (optional)", "FieldLabel"))
        self.key = line_edit("For full Business Profile and speed data", settings.get("quick_google_key") or "")
        self.key.setEchoMode(self.key.EchoMode.Password)
        self.key.setFixedWidth(230)
        self.key.setToolTip("A Google Cloud API key with the Places API (New) and PageSpeed Insights API turned on. "
                            "Without it the Business Profile is read from the public Google Maps page (fewer details).")
        opts.addWidget(self.key)
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
        for c, w in ((1, 120), (2, 380), (3, 90)):
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
        self.runner.api_key = self.key.text().strip() or None
        settings.put("quick_google_key", self.key.text().strip())
        settings.put("quick_pages", self.pages.value())
        settings.put("quick_slots", self.slots.value())
        solo_db.set_status([solo_db.domain_of(j["site"]) for j in jobs], "queued")
        for job in jobs:
            job_id = self._next_id
            self._next_id += 1
            r = self.table.rowCount()
            self.table.insertRow(r)
            shown = (job.get("business") + "  ·  " if job.get("business") else "") + job["site"] + \
                (f"  (+{len(job['competitors'])} competitors)" if job.get("competitors") else "")
            for c, text in enumerate([shown, "Waiting", "In the queue", ""]):
                self.table.setItem(r, c, _item(text))
            self.rows[job_id] = {"row": r, "report": None, "job": dict(job)}
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
        entry = self.rows.get(job_id)
        if entry is None:
            return
        r = entry["row"]
        if not entry.get("started"):
            entry["started"] = True
            solo_db.set_status([solo_db.domain_of(entry["job"]["site"])], "running")
        self.table.setItem(r, 1, _item(f"Running {pct}%", CHARCOAL, bold=True))
        self.table.item(r, 2).setText(step)

    def _finished(self, job_id, ok, info, report):
        entry = self.rows.get(job_id)
        if not entry:
            return
        r = entry["row"]
        stopped = info == "Stopped"
        self.table.setItem(r, 1, _item("Done" if ok else ("Stopped" if stopped else "Failed"),
                                       GOOD if ok else (STEEL if stopped else POOR), bold=True))
        self.table.item(r, 2).setText("Saved — double-click to open the report" if ok else info)
        self.table.item(r, 2).setToolTip("" if ok else info)   # the full reason, also when the column is narrow
        self.table.item(r, 3).setText(info if ok else "")
        site = entry["job"]["site"]
        summary = {}
        if ok and report:
            entry["report"] = report
            try:
                with open(os.path.join(os.path.dirname(report), quick_audit.SUMMARY), encoding="utf-8") as fh:
                    summary = json.load(fh)
            except (OSError, ValueError):
                pass
        try:
            if stopped:
                solo_db.set_status([solo_db.domain_of(site)], "new")
            else:
                solo_db.record_audit(site, ok, summary.get("score"), summary.get("grade") or "", report or "",
                                     "" if ok else info.split("  [details:")[0], entry["job"].get("business") or "")
        except Exception:  # noqa: BLE001 - the audit files are saved either way
            pass
        self.on_saved()

    def _open_row(self, row, _col):
        for entry in self.rows.values():
            if entry["row"] == row and entry["report"]:
                QDesktopServices.openUrl(QUrl.fromLocalFile(entry["report"]))

    def _tick(self):
        running, waiting = self.runner.busy()
        self.status.setText(f"{running} running · {waiting} waiting" if running or waiting else "Ready")
        self.start_btn.setText("Add to queue" if running or waiting else "Start audits")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class ReportsPage(_Body):
    COLS = ["Website", "Score", "Pages", "Finished"]

    def __init__(self):
        super().__init__("Reports", "Audits made on this computer. Double-click to open the client PDF.")
        folder = button("Open reports folder", icon_name="external")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(quick_audit.reports_dir())))
        self.head.add_action(folder)
        self.table = _table(self.COLS)
        for c, w in ((1, 90), (2, 80), (3, 170)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._open)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.lay.addWidget(self.table, 1)
        self.empty = label("No audits yet. Run one from Audit, or audit leads from My Leads.", "Meta")
        self.lay.addWidget(self.empty)
        self.items = []

    def refresh(self):
        tasks.run(quick_audit.saved_audits, on_done=self._render, on_error=lambda _e: None, owner=self)

    def _render(self, items):
        self.items = items
        self.table.setRowCount(0)
        for info in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = info.get("domain") or ""
            if info.get("business"):
                name += f"  ·  {info['business']}"
            try:
                when = datetime.fromisoformat(info.get("finished_at") or "").strftime("%b %d, %Y  %H:%M")
            except ValueError:
                when = ""
            score = info.get("score")
            self.table.setItem(r, 0, _item(name, CHARCOAL, bold=True))
            self.table.setItem(r, 1, _item("—" if score is None else f"{score}/100", _score_color(score), bold=True))
            self.table.setItem(r, 2, _item(str(info.get("pages") or "")))
            self.table.setItem(r, 3, _item(when, STEEL))
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
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction("Open client report", lambda: self._open(row))
        m.addAction("Open folder (Word reports, data)",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.items[row]["folder"])))
        m.exec(self.table.viewport().mapToGlobal(pos))


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------
class ScrapePage(QWidget):
    audit_requested = Signal(list)
    saved = Signal(dict)

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
        self.results_screen.leads_scraped.connect(self._save)

    def _save(self, rows, niche, area):
        """Every scraped business goes to My Leads (the search words are the niche)."""
        def done(res):
            self.saved.emit(res)
            toast(self, f"{res['added']:,} new businesses saved to My Leads"
                        + (f" ({res['updated']:,} were already there)" if res["updated"] else "") + ".", "success", 5000)

        tasks.run(solo_db.add_many, [dict(r, name=r.get("name")) for r in rows], "scrape",
                  niche.strip().title(), area.strip().title(), on_done=done,
                  on_error=lambda e: show_error(self, e, "Could not save the scraped leads"), owner=self)

    def _start(self, domains, areas, fields, headless=False, max_results=50, export_dir="", filters=None):
        # Website, phone and the rest are needed for My Leads and audits.
        fields = list(dict.fromkeys(list(fields) + ["name", "website", "phone", "address", "category", "rating",
                                                    "review_count"]))
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


# ---------------------------------------------------------------------------
class QuickPortal(QWidget):
    """Sidebar with Home, My Leads, Scrape, Audit and Reports; "Sign in" goes
    back to the sign-in screen."""
    leave = Signal()

    def __init__(self):
        super().__init__()
        from seo_audit_pro.ui.shell import NavItem
        self.runner = quick_audit.Runner()
        row = hbox(self)
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(224)
        sl = vbox(side, (10, 14, 10, 12), 2)
        brand = hbox(spacing=10)
        brand.setContentsMargins(4, 0, 0, 0)
        brand.addWidget(logo_label(30))
        name = label(APP_NAME)
        name.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {CHARCOAL};")
        brand.addWidget(name, 1)
        sl.addLayout(brand)
        tag = label("SCRAPE & AUDIT")
        tag.setStyleSheet(f"color: {STEEL}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; padding-left: 44px;")
        sl.addWidget(tag)
        sl.addSpacing(14)
        self.nav = {}
        for key, text, icon_name in (("home", "Home", "dashboard"), ("leads", "My Leads", "leads"),
                                     ("scrape", "Scrape", "map_pin"), ("audit", "Audit", "audit"),
                                     ("reports", "Reports", "report")):
            item = NavItem(key, text, icon_name)
            item.clicked.connect(lambda _c=False, k=key: self.go(k))
            sl.addWidget(item)
            self.nav[key] = item
        sl.addStretch(1)
        mark = signature(compact=True)
        mark.setContentsMargins(8, 0, 0, 8)
        sl.addWidget(mark)
        back = button("Sign in to a workspace", "link")
        back.clicked.connect(self._leave)
        sl.addWidget(back)
        row.addWidget(side)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Canvas")
        self.home = HomePage()
        self.leads = LeadsPage()
        self.reports = ReportsPage()
        self.audit = AuditPage(self.runner, self._after_audit)
        self.scrape = ScrapePage()
        self.home.go.connect(self._home_action)
        self.leads.audit_leads.connect(self._audit_leads)
        self.scrape.audit_requested.connect(self._audit_scraped)
        self.scrape.saved.connect(lambda _r: self.leads.refresh())
        self.pages = {"home": self.home, "leads": self.leads, "scrape": self.scrape, "audit": self.audit,
                      "reports": self.reports}
        for w in self.pages.values():
            self.stack.addWidget(w)
        row.addWidget(self.stack, 1)

    def start(self):
        self.go("home")

    def go(self, key):
        for k, item in self.nav.items():
            item.set_active(k == key)
        page = self.pages[key]
        if hasattr(page, "refresh"):
            page.refresh()
        self.stack.setCurrentWidget(page)

    def _home_action(self, key):
        if key == "import":
            self.go("leads")
            self.leads.import_file()
        else:
            self.go(key)

    def _after_audit(self):
        current = self.stack.currentWidget()
        if current in (self.leads, self.reports, self.home):
            current.refresh()

    def _audit_leads(self, leads):
        n = self.audit.queue([{"site": lead["website"] or lead["domain"], "competitors": [],
                               "business": lead["company"]} for lead in leads])
        self.leads.refresh()
        self.go("audit")
        toast(self.audit, f"{n} website{'s' if n != 1 else ''} added to the audit queue.", "success")

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
