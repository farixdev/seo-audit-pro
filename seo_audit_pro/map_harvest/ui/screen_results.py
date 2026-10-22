import webbrowser

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QMenu, QApplication,
    QProgressBar, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QVBoxLayout,
    QHeaderView, QAbstractItemView,
    QSizePolicy,
)

from core.exporter import export_csv, FIELD_LABELS
from core.scraper import ScrapeWorker


class SortableItem(QTableWidgetItem):
    """Table cell that sorts numerically when both values look like numbers."""

    def __lt__(self, other):
        t1 = (self.text() or "").strip()
        t2 = (other.text() or "").strip()
        if t1 == "—" and t2 != "—":
            return False
        if t2 == "—" and t1 != "—":
            return True
        a, b = self._num(t1), self._num(t2)
        if a is not None and b is not None:
            return a < b
        return t1.lower() < t2.lower()

    @staticmethod
    def _num(text):
        t = (text or "").replace(",", "").strip()
        try:
            return float(t)
        except ValueError:
            return None


class ResultsScreen(QWidget):
    stop_signal = pyqtSignal()
    home_signal = pyqtSignal()

    TOAST_MS = 6000

    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.fields = []
        self._domains = []
        self._areas = []
        self._headless = False
        self._max_results = 50
        self._export_dir = ""
        self._filters = {}
        self._multi = False
        self._is_running = False
        self._is_paused = False
        self._stopped_by_user = False
        self._toast_timer = None
        self._search_text = ""
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("results_header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        name_lbl = QLabel("MapHarvest")
        name_lbl.setObjectName("app_name")

        self.lead_bank_btn = QPushButton("Add to Lead Bank")
        self.lead_bank_btn.setObjectName("lead_bank_btn")
        self.lead_bank_btn.setFixedHeight(30)
        self.lead_bank_btn.setToolTip("Import scraped leads directly into the agency Lead Bank")
        self.lead_bank_btn.clicked.connect(self._on_add_to_lead_bank)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("outlined")
        self.pause_btn.setFixedHeight(30)
        self.pause_btn.clicked.connect(self._on_pause_clicked)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setObjectName("outlined")
        self.export_btn.setFixedHeight(30)
        self.export_btn.setToolTip("Save the current results to a CSV file")
        self.export_btn.clicked.connect(self._on_export_clicked)

        self.action_btn = QPushButton("Stop")
        self.action_btn.setObjectName("danger")
        self.action_btn.setFixedHeight(30)
        self.action_btn.clicked.connect(self._on_action_clicked)

        top_row.addWidget(name_lbl)
        top_row.addStretch()
        top_row.addWidget(self.lead_bank_btn)
        top_row.addWidget(self.export_btn)
        top_row.addWidget(self.pause_btn)
        top_row.addWidget(self.action_btn)
        header_layout.addLayout(top_row)

        self.export_path_label = QLabel("")
        self.export_path_label.setObjectName("muted")
        self.export_path_label.setWordWrap(True)
        header_layout.addWidget(self.export_path_label)

        root.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 8, 20, 16)
        body_layout.setSpacing(12)

        status_block = QVBoxLayout()
        status_block.setSpacing(6)
        status_block.setContentsMargins(0, 0, 0, 0)

        top_line = QHBoxLayout()
        top_line.setSpacing(16)
        count_wrap = QHBoxLayout()
        count_wrap.setSpacing(6)
        count_wrap.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.count_label = QLabel("0")
        self.count_label.setObjectName("count_label")
        self.count_sub = QLabel("collected")
        self.count_sub.setObjectName("count_sub")
        count_wrap.addWidget(self.count_label)
        count_wrap.addWidget(self.count_sub)
        top_line.addLayout(count_wrap)
        top_line.addStretch()

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("status_text")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top_line.addWidget(self.status_label)
        status_block.addLayout(top_line)

        self.area_label = QLabel("")
        self.area_label.setObjectName("muted")
        self.area_label.setAlignment(Qt.AlignLeft)
        status_block.addWidget(self.area_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        status_block.addWidget(self.progress_bar)
        body_layout.addLayout(status_block)

        table_header = QHBoxLayout()
        table_header.setContentsMargins(0, 4, 0, 0)
        table_header.setSpacing(12)
        table_title = QLabel("Results")
        table_title.setObjectName("section_label")
        table_header.addWidget(table_title)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("search_box")
        self.search_input.setPlaceholderText("Filter results…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(28)
        self.search_input.setMaximumWidth(260)
        self.search_input.textChanged.connect(self._apply_search)
        table_header.addWidget(self.search_input)

        table_header.addStretch()
        self.row_count_label = QLabel("")
        self.row_count_label.setObjectName("muted")
        table_header.addWidget(self.row_count_label)
        body_layout.addLayout(table_header)

        self.table = QTableWidget(0, 0)
        self.table.setObjectName("results_table")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().setDefaultSectionSize(130)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        body_layout.addWidget(self.table, stretch=1)

        hint = QLabel("Double-click a row to open it in Google Maps · right-click for more · click a header to sort")
        hint.setObjectName("hint")
        body_layout.addWidget(hint)

        self.toast_label = QLabel("")
        self.toast_label.setObjectName("toast")
        self.toast_label.setWordWrap(True)
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.hide()
        body_layout.addWidget(self.toast_label)

        root.addWidget(body)

    # ── mode helpers ─────────────────────────────────────────────────────────
    def _restyle(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _set_running_mode(self):
        self._is_running = True
        self._is_paused = False
        self.lead_bank_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(True)
        self.pause_btn.show()
        self.action_btn.setText("Stop")
        self.action_btn.setObjectName("danger")
        self._restyle(self.action_btn)
        self.action_btn.setEnabled(True)
        self.table.setSortingEnabled(False)

    def _set_idle_mode(self):
        self._is_running = False
        self._is_paused = False
        self.pause_btn.hide()
        self.action_btn.setText("Scrape Another")
        self.action_btn.setObjectName("outlined")
        self._restyle(self.action_btn)
        self.action_btn.setEnabled(True)
        self.table.setSortingEnabled(True)  # allow header-click sorting once done
        try:
            from seo_audit_pro.core.session import session
            can_import = session.is_manager() or session.is_team_lead()
            self.lead_bank_btn.setVisible(can_import)
            self.lead_bank_btn.setEnabled(can_import and len(self.results) > 0)
        except Exception:
            self.lead_bank_btn.setEnabled(len(self.results) > 0)

    # ── setup / worker ───────────────────────────────────────────────────────
    def setup(self, domains, areas, fields, headless=False, max_results=50,
              export_dir="", filters=None):
        self._stopped_by_user = False
        self.results = []
        self._domains = list(domains)
        self._areas = list(areas) if isinstance(areas, list) else [areas]
        self._headless = headless
        self._max_results = max_results
        self._export_dir = export_dir or ""
        self._filters = filters or {}
        self._multi = len(self._domains) * len(self._areas) > 1

        self.fields = list(fields)
        if len(self._areas) > 1 and "area" not in self.fields:
            self.fields = ["area"] + self.fields
        if len(self._domains) > 1 and "domain" not in self.fields:
            self.fields = ["domain"] + self.fields

        labels = [FIELD_LABELS.get(f, f) for f in self.fields]
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(labels))
        self.table.setHorizontalHeaderLabels(labels)
        if labels:
            self.table.setColumnWidth(0, 180)

        self.search_input.clear()
        self._search_text = ""
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.count_label.setText("0")
        self.count_sub.setText(f"of {self._max_results}")
        self.row_count_label.setText("")
        self.status_label.setText("Starting…")
        self.area_label.setText(
            f"{', '.join(self._domains)} · {', '.join(self._areas)}"
        )
        self.export_path_label.setText(
            f"Saving exports to {self._export_dir}" if self._export_dir else ""
        )
        self._hide_toast()
        self._set_running_mode()

    def start_worker(self):
        # Never let two scrapes overlap — a previous worker that is still
        # shutting down would leave a second Chrome running alongside the new one.
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(5000):
                self.worker.abort()
                self.worker.wait(5000)

        if self.worker is not None:
            for signal, slot in (
                (self.worker.log_signal, self._on_log),
                (self.worker.result_signal, self.add_table_row),
                (self.worker.progress_signal, self.update_progress),
                (self.worker.done_signal, self.on_done),
                (self.worker.error_signal, self.on_error),
                (self.worker.paused_signal, self._on_paused),
                (self.worker.domain_finished_signal, self._on_domain_finished),
            ):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass

        self.worker = ScrapeWorker(
            self._domains, self._areas, self.fields,
            headless=self._headless, max_results=self._max_results,
            filters=self._filters,
        )
        self.worker.log_signal.connect(self._on_log)
        self.worker.result_signal.connect(self.add_table_row)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.done_signal.connect(self.on_done)
        self.worker.error_signal.connect(self.on_error)
        self.worker.paused_signal.connect(self._on_paused)
        if self._multi:
            self.worker.domain_finished_signal.connect(self._on_domain_finished)
        self.worker.start()

    def stop_worker(self):
        self._stopped_by_user = True
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.pause_btn.setEnabled(False)
        self.action_btn.setEnabled(False)
        self.status_label.setText("Stopping…")

    def _on_pause_clicked(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self._is_paused:
            self.worker.resume()
        else:
            self.worker.pause()

    def _on_paused(self, paused: bool):
        self._is_paused = paused
        if paused:
            self.pause_btn.setText("Resume")
            self.status_label.setText("Paused")
        else:
            self.pause_btn.setText("Pause")
            self.status_label.setText("Resuming…")

    def _on_log(self, message: str, status: str):
        if status == "active":
            self.status_label.setText(message)
        elif status == "done" and message.startswith("#"):
            short = message.lstrip("#").strip()
            if len(short) > 48:
                short = short[:45] + "…"
            self.status_label.setText(short)

    def update_progress(self, collected: int):
        self.count_label.setText(str(collected))
        self.progress_bar.setMaximum(max(self._max_results, 1))
        self.progress_bar.setValue(min(collected, self._max_results))

    # ── table ────────────────────────────────────────────────────────────────
    def add_table_row(self, data: dict):
        self.results.append(data)
        row = self.table.rowCount()
        self.table.insertRow(row)

        for col, field in enumerate(self.fields):
            if field == "domain":
                raw = data.get("domain", data.get("_domain", ""))
            elif field == "area":
                raw = data.get("area", data.get("_area", ""))
            else:
                raw = data.get(field, "")
            text = str(raw).strip() if raw else "—"
            display = text[:77] + "…" if len(text) > 80 else text

            item = SortableItem(display)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            item.setData(Qt.UserRole + 1, text)  # full (untruncated) value for search
            if len(text) > 80:
                item.setToolTip(text)
            if col == 0:
                item.setData(Qt.UserRole, data)  # attach record for row actions
            self.table.setItem(row, col, item)

        if self._search_text and not self._row_matches(row, self._search_text):
            self.table.setRowHidden(row, True)
        else:
            self.table.scrollToBottom()
        self._update_row_count()

    def _update_row_count(self):
        total = len(self.results)
        visible = sum(0 if self.table.isRowHidden(r) else 1 for r in range(self.table.rowCount()))
        if visible != total:
            self.row_count_label.setText(f"{visible} of {total} rows")
        else:
            self.row_count_label.setText(f"{total} row{'s' if total != 1 else ''}")

    def _row_matches(self, row: int, text: str) -> bool:
        for col in range(self.table.columnCount()):
            it = self.table.item(row, col)
            if it is None:
                continue
            full = it.data(Qt.UserRole + 1)
            hay = full if isinstance(full, str) else it.text()
            if text in hay.lower():
                return True
        return False

    def _apply_search(self, text: str):
        self._search_text = (text or "").strip().lower()
        for row in range(self.table.rowCount()):
            hide = bool(self._search_text) and not self._row_matches(row, self._search_text)
            self.table.setRowHidden(row, hide)
        self._update_row_count()

    def _record_for_row(self, row: int) -> dict:
        it = self.table.item(row, 0)
        if it is None:
            return {}
        data = it.data(Qt.UserRole)
        return data if isinstance(data, dict) else {}

    def _on_cell_double_clicked(self, row: int, _col: int):
        rec = self._record_for_row(row)
        url = rec.get("maps_link") or rec.get("_href")
        if url:
            webbrowser.open(url)

    def _show_row_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        rec = self._record_for_row(index.row())
        if not rec:
            return
        menu = QMenu(self)
        maps_url = rec.get("maps_link") or rec.get("_href")
        website = rec.get("website")
        phone = rec.get("phone")
        email = rec.get("email")

        if maps_url:
            menu.addAction("Open in Google Maps", lambda: webbrowser.open(maps_url))
        if website:
            menu.addAction("Open website", lambda: webbrowser.open(website))
        if maps_url or website:
            menu.addSeparator()
        if phone:
            menu.addAction("Copy phone", lambda: self._copy(phone))
        if email:
            menu.addAction("Copy email", lambda: self._copy(email))
        if rec.get("name"):
            menu.addAction("Copy name", lambda: self._copy(rec.get("name")))
        menu.addAction("Copy row (tab-separated)", lambda: self._copy_row(rec))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _copy(self, text: str):
        QApplication.clipboard().setText(str(text))
        self.status_label.setText("Copied to clipboard")

    def _copy_row(self, rec: dict):
        vals = []
        for f in self.fields:
            if f == "domain":
                vals.append(str(rec.get("domain", rec.get("_domain", ""))))
            elif f == "area":
                vals.append(str(rec.get("area", rec.get("_area", ""))))
            else:
                vals.append(str(rec.get(f, "")))
        self._copy("\t".join(vals))

    # ── export / summaries ───────────────────────────────────────────────────
    def _current_task(self):
        if self.results:
            last = self.results[-1]
            return last.get("_domain", ""), last.get("_area", "")
        d = self._domains[0] if self._domains else ""
        a = self._areas[0] if self._areas else ""
        return d, a

    def _export_summary(self, domain, area, count, hit_limit) -> str:
        label = f"{domain} in {area}"
        if count <= 0:
            return f'"{label}" — no results found. Skipping CSV.'
        if hit_limit:
            summary = f"{count} of {self._max_results} collected"
        elif self._max_results > 0 and count < self._max_results:
            summary = f"{count} of {self._max_results} requested — Google Maps had fewer"
        else:
            summary = f"{count} collected"
        return f'"{label}" — {summary}'

    def _export_rows(self, rows):
        """Ensure the pseudo-columns (domain/area) are populated for CSV."""
        out = []
        for r in rows:
            r2 = dict(r)
            if "domain" in self.fields and not r2.get("domain"):
                r2["domain"] = r2.get("_domain", "")
            if "area" in self.fields and not r2.get("area"):
                r2["area"] = r2.get("_area", "")
            out.append(r2)
        return out

    def _notify_task_export(self, domain, area, count, hit_limit, rows=None):
        message = self._export_summary(domain, area, count, hit_limit)
        filepath = None
        rows = self.results if rows is None else rows
        if count > 0 and rows:
            try:
                filepath = export_csv(
                    self._export_rows(rows), domain, area, self.fields, self._export_dir,
                )
            except Exception as e:
                message += f"  (CSV save failed: {e})"
        self._show_toast(message, filepath)

    def _show_toast(self, message: str, filepath=None):
        text = f"{message}\nCSV saved to:\n{filepath}" if filepath else message
        self.toast_label.setText(text)
        self.toast_label.show()
        if self._toast_timer is not None:
            self._toast_timer.stop()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        self._toast_timer.start(self.TOAST_MS)

    def _hide_toast(self):
        self.toast_label.hide()
        self.toast_label.clear()

    def _on_domain_finished(self, domain, area, count, max_results, hit_limit):
        if not self.worker:
            return
        self._notify_task_export(domain, area, count, hit_limit, rows=list(self.results))
        self.results = []
        self.table.setRowCount(0)
        self.count_label.setText("0")
        self.progress_bar.setValue(0)
        self.row_count_label.setText("")
        self.status_label.setText("Continuing to next search…")
        self.worker.continue_next_domain()

    def on_done(self):
        n = len(self.results)
        if self._stopped_by_user:
            if n > 0:
                d, a = self._current_task()
                self._notify_task_export(d, a, n, False)
                self.status_label.setText("Stopped — partial results saved")
            else:
                self.status_label.setText("Stopped — no results collected")
        else:
            if not self._multi:
                d = self._domains[0] if self._domains else ""
                a = self._areas[0] if self._areas else ""
                hit_limit = self._max_results > 0 and n >= self._max_results
                self._notify_task_export(d, a, n, hit_limit)
                self.status_label.setText(f"Done — {n} businesses" if n else "Done — no results")
            else:
                self.status_label.setText("Done — all searches scraped")

        self.count_sub.setText(f"of {self._max_results}")
        self._update_row_count()
        self._set_idle_mode()
        self._stopped_by_user = False

    def on_error(self, message: str):
        self.status_label.setText(f"Error: {message[:60]}")
        self._show_toast(f"Error: {message}")
        self._set_idle_mode()

    def _on_export_clicked(self):
        if not self.results:
            self._show_toast("Nothing to export yet.")
            return
        d, a = self._current_task()
        self._notify_task_export(d, a, len(self.results), False)

    def _on_add_to_lead_bank(self):
        if not self.results:
            self._show_toast("No scraped leads to add to the Lead Bank.")
            return
        d, a = self._current_task()
        try:
            from seo_audit_pro.core import api, tasks
            from seo_audit_pro.map_harvest.ui.lead_bank_dialog import AddToLeadBankDialog

            def open_dialog(options):
                dlg = AddToLeadBankDialog(self, self.results, default_domain=d, default_area=a, options=options)
                dlg.leads_imported.connect(self._on_leads_imported)
                dlg.exec()

            tasks.run(api.get, "/api/workspace/options", on_done=open_dialog, on_error=lambda _e: open_dialog({}))
        except Exception as e:
            self._show_toast(f"Could not open Lead Bank import: {e}")

    def _on_leads_imported(self, batch):
        imported = batch.get("imported", 0)
        duplicates = batch.get("duplicates", 0)
        no_site = batch.get("no_website", 0)
        msg = f"Successfully added {imported:,} lead{'s' if imported != 1 else ''} to the Lead Bank!"
        if duplicates or no_site:
            msg += f" ({duplicates:,} duplicates, {no_site:,} without website skipped)"
        self._show_toast(msg)

    def _on_action_clicked(self):
        if self._is_running:
            self.stop_signal.emit()
        else:
            self.home_signal.emit()
