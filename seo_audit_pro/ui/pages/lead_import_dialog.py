# -*- coding: utf-8 -*-
"""
Import scraped lead files (Google Maps / GMB exports) into the lead bank.

Many files can be added at once (picker or drag and drop). Each file is
uploaded for a server-side preview, which detects the columns and guesses the
niche and area from the file name; both stay editable. Nothing is imported
until "Import" — then each file becomes an import batch processed on the
server, which removes rows without a website and duplicates and reports them.
"""

import mimetypes
import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QLineEdit, QTableWidget,
                               QToolButton, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.lead_dialogs import AssigneePicker
from seo_audit_pro.widgets.common import button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog

_ACCEPT = (".csv", ".tsv", ".txt", ".xlsx")
_MAX_PARALLEL = 2
COL_FILE, COL_STATUS, COL_NICHE, COL_AREA, COL_SITE, COL_REMOVE = range(6)


def _upload(path):
    name = os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        return api.request("POST", "/api/lead-imports", files={"file": (name, fh, mime)}, data={}, timeout=600)


class _FileRow:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.state = "waiting"      # waiting | reading | ready | error | importing | started
        self.preview = None
        self.error = ""
        self.niche = QLineEdit()
        self.area = QLineEdit()
        self.site = QComboBox()
        for w in (self.niche, self.area):
            w.setPlaceholderText("Required")
            w.setMinimumHeight(30)
        self.site.setMinimumHeight(30)


class ImportFilesDialog(Dialog):
    imported = Signal(list)   # started batches

    def __init__(self, parent, options):
        super().__init__(parent, "Import lead files",
                         "Add one or many scraped files (CSV or Excel), one niche in one area per file. Rows without a "
                         "website and leads already in the bank are removed automatically and listed per file.", 980)
        self.setAcceptDrops(True)
        self.rows = []
        self.started = []
        self._inflight = 0
        top = hbox(spacing=8)
        add = button("Add files…", icon_name="upload")
        add.clicked.connect(self.choose)
        top.addWidget(add)
        self.hint = label("or drop files here", "Meta")
        top.addWidget(self.hint)
        top.addStretch(1)
        self.counter = label("", "Meta")
        top.addWidget(self.counter)
        self.body.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Niche", "Area", "Website column", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(300)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        for col, w in ((COL_STATUS, 170), (COL_NICHE, 170), (COL_AREA, 170), (COL_SITE, 150), (COL_REMOVE, 40)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, w)
        self.table.setStyleSheet(f"QTableWidget {{ border: 1px solid {C['border']}; border-radius: 8px; }}")
        self.body.addWidget(self.table, 1)

        assign = QWidget()
        al = vbox(assign, spacing=6)
        al.addWidget(label("Assign the imported leads (optional)", "FieldLabel"))
        self.picker = AssigneePicker(options, session.user.get("team_id") if session.is_team_lead() else None)
        al.addWidget(self.picker)
        self.body.addWidget(assign)
        self.add_buttons("Import")
        self.primary.clicked.connect(self.submit)
        self._refresh()

    # -- adding files -------------------------------------------------------
    def choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose lead files", os.path.expanduser("~"),
                                                "Lead files (*.csv *.tsv *.txt *.xlsx)")
        self.add_paths(paths)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])

    def add_paths(self, paths):
        known = {r.path for r in self.rows}
        skipped = 0
        for path in paths:
            if not path or path in known or not path.lower().endswith(_ACCEPT):
                skipped += bool(path) and path not in known
                continue
            row = _FileRow(path)
            self.rows.append(row)
            self._add_table_row(row)
        if skipped:
            self.show_error(api.ApiError(422, f"{skipped} file(s) skipped: only CSV, TSV and XLSX files can be imported."))
        self._pump()
        self._refresh()

    def _add_table_row(self, row):
        r = self.table.rowCount()
        self.table.insertRow(r)
        name = label(row.name)
        name.setToolTip(row.path)
        cell = QWidget()
        cl = vbox(cell, (8, 4, 8, 4), 0)
        cl.addWidget(name)
        row.meta = label("Waiting…", "Meta")
        cl.addWidget(row.meta)
        self.table.setCellWidget(r, COL_FILE, cell)
        row.status = label("Queued", "Meta")
        row.status.setContentsMargins(8, 0, 8, 0)
        row.status.setWordWrap(True)
        self.table.setCellWidget(r, COL_STATUS, row.status)
        for col, w in ((COL_NICHE, row.niche), (COL_AREA, row.area), (COL_SITE, row.site)):
            holder = QWidget()
            hl = hbox(holder, (4, 6, 4, 6))
            hl.addWidget(w)
            self.table.setCellWidget(r, col, holder)
            w.setEnabled(False)
        rm = QToolButton()
        rm.setIcon(icons.icon("x", C["text_3"], 14))
        rm.setToolTip("Remove this file")
        rm.clicked.connect(lambda _c=False, fr=row: self.remove(fr))
        self.table.setCellWidget(r, COL_REMOVE, rm)
        row.niche.textChanged.connect(self._refresh)
        row.area.textChanged.connect(self._refresh)
        row.site.currentIndexChanged.connect(lambda _i, fr=row: (self._set_ready_text(fr), self._refresh()))

    def remove(self, row):
        if row.state in ("importing", "started"):
            return
        idx = self.rows.index(row)
        self.rows.pop(idx)
        self.table.removeRow(idx)
        self._discard(row)
        self._refresh()

    # -- previews -----------------------------------------------------------
    def _pump(self):
        for row in self.rows:
            if self._inflight >= _MAX_PARALLEL:
                break
            if row.state == "waiting":
                self._start_preview(row)

    def _start_preview(self, row):
        row.state = "reading"
        self._inflight += 1
        row.status.setText("Reading file…")
        row.meta.setText(fmt.size(os.path.getsize(row.path)) if os.path.exists(row.path) else "")

        def done(res):
            self._inflight -= 1
            if row not in self.rows:  # removed while uploading
                self._discard_batch(res["batch"]["id"])
            else:
                row.preview = res
                row.state = "ready"
                b = res["batch"]
                row.niche.setText(b.get("niche") or "")
                row.area.setText(b.get("area") or "")
                row.site.clear()
                row.site.addItem("Choose column…", None)
                for h in res["headers"]:
                    row.site.addItem(h, h)
                if res["mapping"].get("website"):
                    row.site.setCurrentIndex(row.site.findData(res["mapping"]["website"]))
                for w in (row.niche, row.area, row.site):
                    w.setEnabled(True)
                detected = [k.replace("_", " ") for k, v in res["mapping"].items() if v and k != "website"]
                row.meta.setText(f"{b['total_rows']:,} rows · {fmt.size(b['file_size'])}")
                row.meta.setToolTip("Also detected: " + ", ".join(detected) if detected else "")
                self._set_ready_text(row)
            self._pump()
            self._refresh()

        def fail(err):
            self._inflight -= 1
            row.state = "error"
            row.error = err.message
            row.status.setText("Can't import")
            row.status.setStyleSheet(f"color: {C['danger']};")
            row.meta.setText(err.message)
            row.meta.setToolTip(err.message)
            self._pump()
            self._refresh()

        tasks.run(_upload, row.path, on_done=done, on_error=fail, owner=self)

    def _set_ready_text(self, row):
        if row.state != "ready":
            return
        if row.site.currentData():
            sample = row.preview.get("sample_size") or 0
            with_site = row.preview.get("sample_with_website") or 0
            row.status.setText("Ready" if not sample else f"Ready\n{with_site * 100 // max(1, sample)}% have a website")
            row.status.setStyleSheet(f"color: {C['success']};")
        else:
            row.status.setText("Choose the website column")
            row.status.setStyleSheet(f"color: {C['warning']};")

    def _refresh(self):
        ready = [r for r in self.rows if r.state == "ready"]
        valid = [r for r in ready if r.site.currentData() and r.niche.text().strip() and r.area.text().strip()]
        busy = any(r.state in ("waiting", "reading") for r in self.rows)
        total_rows = sum((r.preview or {}).get("batch", {}).get("total_rows", 0) for r in ready)
        self.counter.setText(f"{len(self.rows)} file(s) · {total_rows:,} rows" if self.rows else "")
        self.hint.setVisible(not self.rows)
        ok = bool(valid) and len(valid) == len(ready) and not busy
        self.primary.setEnabled(ok)
        n = len(valid)
        self.primary.setText(f"Import {n} file{'s' if n != 1 else ''}" if n else "Import")
        self.primary._idle_text = self.primary.text()

    # -- import -----------------------------------------------------------------
    def submit(self):
        team_id, agent_id = self.picker.values()
        todo = [r for r in self.rows if r.state == "ready"]
        if not todo:
            return
        self.clear_error()
        self.primary.busy(True, "Starting imports…")
        self._pending = len(todo)
        self._failures = []
        for row in todo:
            row.state = "importing"
            row.status.setText("Starting…")
            mapping = dict(row.preview["mapping"], website=row.site.currentData())
            payload = {"niche": row.niche.text().strip(), "area": row.area.text().strip(), "mapping": mapping,
                       "team_id": team_id, "agent_id": agent_id}
            if not hasattr(row, "idem"):
                row.idem = api.new_idempotency_key()

            def done(res, row=row):
                row.state = "started"
                self.started.append(res["batch"])
                row.status.setText("Importing in the background")
                self._finish_one()

            def fail(err, row=row):
                row.state = "ready"
                row.status.setText("Not started")
                row.status.setStyleSheet(f"color: {C['danger']};")
                self._failures.append(f"{row.name}: {err.message}")
                self._finish_one()

            tasks.run(api.post, f"/api/lead-batches/{row.preview['batch']['id']}/start", payload,
                      idempotency_key=row.idem, on_done=done, on_error=fail, owner=self)

    def _finish_one(self):
        self._pending -= 1
        if self._pending:
            return
        self.primary.busy(False)
        if self._failures:
            self.show_error(api.ApiError(422, " ".join(self._failures)))
            self._refresh()
        if self.started:
            self.imported.emit(self.started)
        if not self._failures:
            self.accept()

    # -- cleanup ------------------------------------------------------------------
    def _discard(self, row):
        if row.preview and row.state == "ready":
            self._discard_batch(row.preview["batch"]["id"])

    @staticmethod
    def _discard_batch(batch_id):
        tasks.run(api.delete, f"/api/lead-batches/{batch_id}")

    def reject(self):
        for row in self.rows:
            self._discard(row)
        super().reject()

    def done(self, result):
        # Files previewed but not imported are discarded on the server.
        if result:
            for row in self.rows:
                self._discard(row)
        super().done(result)


def open_import(parent, options, on_started):
    dlg = ImportFilesDialog(parent, options)
    dlg.imported.connect(on_started)
    dlg.exec()

