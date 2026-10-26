# -*- coding: utf-8 -*-
"""
SEO Audit Tool — a standalone audit program (no server, no sign-in).

Paste one or more website links, press Start: the audits run as a queue (two at
a time by default) and each one writes its documents into its own folder:
the client report and the technical audit as Word files (and PDF when
Microsoft Word or LibreOffice is installed), plus the full audit data (JSON).
"""

import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

APP = "SEO Audit Tool"
STAGES = [("fetching homepage", 5), ("robots", 10), ("crawling the site", 12), ("analyzing pages", 55),
          ("internal links", 60), ("pagespeed", 66), ("structured data", 76), ("ssl", 80), ("security audit", 82),
          ("authority", 88), ("scoring", 92), ("competitor", 94), ("executive summary", 96)]
CRAWL_RE = re.compile(r"crawled (\d+)/(\d+) pages", re.I)
COLS = ["Website", "Status", "Progress", "Score", "Folder"]

STYLE = """
QWidget { font-size: 13px; color: #18181B; }
QMainWindow, #Root { background: #F6F7FB; }
QPlainTextEdit, QLineEdit, QSpinBox { background: #FFFFFF; border: 1px solid #D4D4D8; border-radius: 8px; padding: 6px; }
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus { border-color: #2F5BFF; }
QPushButton { background: #FFFFFF; border: 1px solid #D4D4D8; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: #F1F2F6; }
QPushButton#Primary { background: #2F5BFF; color: #FFFFFF; border: none; }
QPushButton#Primary:hover { background: #2448D8; }
QPushButton:disabled { color: #A1A1AA; }
QTableWidget { background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 10px; gridline-color: #F1F1F4; }
QHeaderView::section { background: #FAFAFB; border: none; border-bottom: 1px solid #E4E4E7; padding: 8px;
                       font-weight: 600; color: #52525B; }
#Title { font-size: 20px; font-weight: 700; }
#Muted { color: #71717A; }
"""


def _domain(url):
    d = re.sub(r"^https?://", "", url.strip(), flags=re.I).split("/")[0].lower()
    return d[4:] if d.startswith("www.") else d


def _safe(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "site"


def default_folder():
    return os.path.join(os.path.expanduser("~"), "Documents", "SEO Audit Tool")


def _resource(*parts):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), *parts)


def render_html(result, folder, docx_files):
    """The interactive client report as one HTML page, with buttons to download the
    Word and PDF versions saved next to it."""
    import jinja2
    from backend.report_context import prepare_client_report_context
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(_resource("templates")), autoescape=True)
    try:
        with open(_resource("static", "libs", "lucide.min.js"), encoding="utf-8") as fh:
            lucide = fh.read().replace("</script", r"<\/script")
    except OSError:
        lucide = ""
    downloads = []
    for path in docx_files:
        kind = "Client report" if "Client" in os.path.basename(path) else "Technical audit"
        for ext, fmt_name in ((".docx", "Word"), (".pdf", "PDF")):
            f = path[:-5] + ext
            if os.path.exists(f):
                downloads.append({"href": QUrl.toPercentEncoding(os.path.basename(f)).data().decode(),
                                  "label": f"{kind} ({fmt_name})"})
    return env.get_template("client_report.html").render(
        audit_id=result.get("id") or "", result=result, client_data=prepare_client_report_context(result),
        lucide_js=lucide, downloads=downloads)


class Signals(QObject):
    progress = Signal(int, int, str)       # row, percent, step
    finished = Signal(int, bool, str, str)  # row, ok, score or error, folder


class Runner:
    """Runs queued audits, ``slots`` at a time, each in its own thread."""

    def __init__(self, signals):
        self.signals = signals
        self.queue = []          # (row, url)
        self.active = 0
        self.slots = 2
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.options = {}

    def add(self, row, url):
        with self.lock:
            self.queue.append((row, url))
        self.pump()

    def pump(self):
        with self.lock:
            while self.active < self.slots and self.queue and not self.cancel.is_set():
                row, url = self.queue.pop(0)
                self.active += 1
                threading.Thread(target=self._run, args=(row, url), daemon=True).start()

    def stop(self):
        self.cancel.set()
        with self.lock:
            dropped, self.queue = self.queue, []
        for row, _url in dropped:
            self.signals.finished.emit(row, False, "Stopped", "")

    def _run(self, row, url):
        try:
            self._audit(row, url)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            msg = str(e) if isinstance(e, ValueError) else "The audit could not be completed."
            self.signals.finished.emit(row, False, msg[:300], "")
        finally:
            with self.lock:
                self.active -= 1
            self.pump()

    def _audit(self, row, url):
        import audit as engine
        import report_docx
        state = {"pct": 2}

        def progress(msg):
            msg = str(msg)
            m = CRAWL_RE.search(msg)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                state["pct"] = max(state["pct"], min(54, 12 + int(done / max(1, total) * 42)))
            else:
                low = msg.lower()
                for needle, pct in STAGES:
                    if needle in low and pct > state["pct"]:
                        state["pct"] = pct
                        break
            self.signals.progress.emit(row, state["pct"], msg.rstrip("…. ")[:120])

        url, competitors, business = url["site"], url["competitors"], url["business"]
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        opts = self.options
        result = engine.run_audit({"url": url, "max_pages": opts["pages"], "psi_strategy": "both",
                                   "psi_key": opts.get("psi_key") or None, "cancel": self.cancel,
                                   "competitors": competitors, "business": business}, progress=progress)
        self.signals.progress.emit(row, 97, "Writing the documents")
        domain = _domain(result.get("url") or url)
        folder = os.path.join(opts["folder"], f"{_safe(domain)} - {datetime.now():%Y-%m-%d %H%M}")
        os.makedirs(folder, exist_ok=True)
        client = os.path.join(folder, f"{_safe(domain)} - Client SEO Report.docx")
        technical = os.path.join(folder, f"{_safe(domain)} - Technical SEO Audit.docx")
        report_docx.generate_client_docx_report(result, client)
        report_docx.generate_docx_report(result, technical)
        with open(os.path.join(folder, f"{_safe(domain)} - audit data.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1, default=str)
        if opts.get("pdf") and __import__("pdf_convert").pdf_supported():
            import pdf_convert
            for docx in (client, technical):
                self.signals.progress.emit(row, 99, "Making the PDF files")
                pdf_convert.docx_to_pdf(docx, docx[:-5] + ".pdf")
        import full_report
        self.signals.progress.emit(row, 99, "Checking the Google Business Profile")
        title = ((result.get("home_facts") or {}).get("title") or "").split("|")[0].split(" - ")[0].strip()
        place, note = result.get("gbp"), result.get("gbp_note") or "not_found"   # found during the audit
        base = os.path.join(folder, f"{_safe(domain)} - Full SEO Audit")
        downloads = [(label, os.path.basename(f)) for label, f in (
            ("Full audit (PDF)", base + ".pdf"), ("Client report (Word)", client), ("Technical audit (Word)", technical),
            ("Client report (PDF)", client[:-5] + ".pdf"), ("Technical audit (PDF)", technical[:-5] + ".pdf"))
            if (f.endswith("Full SEO Audit.pdf") and opts.get("pdf")) or os.path.exists(f)]
        full_html = base + ".html"
        with open(full_html, "w", encoding="utf-8") as fh:
            fh.write(full_report.build(result, downloads, place, note))
        import client_report
        import mindcob_report
        self.signals.progress.emit(row, 99, "Taking screenshots of the website")
        shots = client_report.screenshots(result.get("final_url") or url, folder)
        cbase = os.path.join(folder, f"{_safe(domain)} - Mindcob Website SEO & Growth Report")
        client_downloads = [("Full SEO audit", os.path.basename(full_html))] + [
            d for d in downloads if d[0] == "Full audit (PDF)" and opts.get("pdf")]
        if opts.get("pdf"):
            client_downloads.insert(0, ("This report (PDF)", os.path.basename(cbase + ".pdf")))
        html = cbase + ".html"
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(mindcob_report.build(result, {"name": "Mindcob"}, business, client_downloads, place, note, shots))
        if opts.get("pdf"):
            self.signals.progress.emit(row, 99, "Making the PDF reports")
            full_report.html_to_pdf(full_html, base + ".pdf")
            full_report.html_to_pdf(html, cbase + ".pdf")
        sc = result.get("score") or {}
        self.signals.finished.emit(row, True, f"{sc.get('score', '—')}/100 ({sc.get('grade', '')})", html)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1040, 720)
        self.store = QSettings("Mindcob", APP)
        self.signals = Signals()
        self.signals.progress.connect(self._progress)
        self.signals.finished.connect(self._finished)
        self.runner = Runner(self.signals)
        self.folders = {}

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        title = QLabel(APP)
        title.setObjectName("Title")
        lay.addWidget(title)
        sub = QLabel("Paste website links (one per line), then Start. Each audit saves its client report, "
                     "technical audit and data into its own folder.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("One website per line. Optional: competitors after it, and the business name after |\n"
                                      "mysite.com\nmysite.com, competitor1.com, competitor2.com | My Business Name, Toronto")
        self.links.setFixedHeight(120)
        lay.addWidget(self.links)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Pages per site"))
        self.pages = QSpinBox()
        self.pages.setRange(1, 500)
        self.pages.setValue(int(self.store.value("pages", 50)))
        opts.addWidget(self.pages)
        opts.addSpacing(12)
        opts.addWidget(QLabel("At the same time"))
        self.slots = QSpinBox()
        self.slots.setRange(1, 4)
        self.slots.setValue(int(self.store.value("slots", 2)))
        opts.addWidget(self.slots)
        opts.addSpacing(12)
        import pdf_convert
        self.pdf = QCheckBox("Also make PDF files")
        import full_report
        can_pdf = pdf_convert.pdf_supported() or bool(full_report._browser())
        self.pdf.setEnabled(can_pdf)
        self.pdf.setChecked(can_pdf and self.store.value("pdf", "true") == "true")
        self.pdf.setToolTip("" if can_pdf else "PDF needs Microsoft Edge, Chrome or Word on this computer.")
        opts.addWidget(self.pdf)
        opts.addSpacing(12)
        self.browser = QCheckBox("Open each report in the browser")
        self.browser.setChecked(self.store.value("browser", "true") == "true")
        opts.addWidget(self.browser)
        opts.addStretch(1)
        lay.addLayout(opts)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Save to"))
        self.folder = QLineEdit(self.store.value("folder", default_folder()))
        row2.addWidget(self.folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row2.addWidget(browse)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Google API key (optional)"))
        self.psi = QLineEdit(self.store.value("psi", ""))
        self.psi.setEchoMode(QLineEdit.EchoMode.Password)
        self.psi.setFixedWidth(220)
        self.psi.setToolTip("A Google Cloud API key with PageSpeed Insights API and Places API (New) turned on: "
                            "reliable speed data and the Google Business Profile check.")
        row2.addWidget(self.psi)
        lay.addLayout(row2)

        buttons = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        buttons.addWidget(self.status, 1)
        open_root = QPushButton("Open reports folder")
        open_root.clicked.connect(lambda: self._open(self.folder.text()))
        buttons.addWidget(open_root)
        self.stop_btn = QPushButton("Stop queue")
        self.stop_btn.clicked.connect(self._stop)
        buttons.addWidget(self.stop_btn)
        self.start_btn = QPushButton("Start audits")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._start)
        buttons.addWidget(self.start_btn)
        lay.addLayout(buttons)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 110), (2, 330), (3, 110), (4, 110)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._open_row)
        lay.addWidget(self.table, 1)
        try:
            from seo_audit_pro.widgets.common import signature
            mark = QHBoxLayout()
            mark.addStretch(1)
            mark.addWidget(signature())
            lay.addLayout(mark)
        except Exception:  # noqa: BLE001 - the maker's mark is optional
            pass
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_status)
        self._tick.start()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Save reports to", self.folder.text())
        if path:
            self.folder.setText(path)

    def _open(self, path):
        if path:
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_row(self, row, col):
        html = self.folders.get(row)
        if not html:
            return
        if col == 4:  # "Open folder"
            self._open(os.path.dirname(html))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(html))

    def _start(self):
        urls = []  # one job per line: "site, competitor, competitor | Business name, City"
        for line in self.links.toPlainText().splitlines():
            line, _, business = line.partition("|")
            parts = [p for p in re.split(r"[\s,;]+", line.strip()) if p and "." in p]
            if parts and parts[0] not in [u["site"] for u in urls]:
                urls.append({"site": parts[0], "competitors": parts[1:4], "business": business.strip()})
        if not urls:
            QMessageBox.information(self, APP, "Paste at least one website link.")
            return
        folder = self.folder.text().strip() or default_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            QMessageBox.warning(self, APP, "That folder cannot be used. Choose another one.")
            return
        for key, value in (("pages", self.pages.value()), ("slots", self.slots.value()), ("folder", folder),
                           ("pdf", "true" if self.pdf.isChecked() else "false"), ("psi", self.psi.text().strip()),
                           ("browser", "true" if self.browser.isChecked() else "false")):
            self.store.setValue(key, value)
        self.runner.cancel.clear()
        self.runner.slots = self.slots.value()
        self.runner.options = {"pages": self.pages.value(), "folder": folder, "pdf": self.pdf.isChecked(),
                               "psi_key": self.psi.text().strip()}
        for job in urls:
            r = self.table.rowCount()
            self.table.insertRow(r)
            shown = job["site"] + (f"  (+{len(job['competitors'])} competitors)" if job["competitors"] else "")
            for c, text in enumerate([shown, "Waiting", "In the queue", "", ""]):
                self.table.setItem(r, c, QTableWidgetItem(text))
            self.runner.add(r, job)
        self.links.clear()

    def _stop(self):
        self.runner.stop()

    def _progress(self, row, pct, step):
        self.table.item(row, 1).setText(f"Running {pct}%")
        self.table.item(row, 2).setText(step)

    def _finished(self, row, ok, info, folder):
        item = self.table.item(row, 1)
        item.setText("Done" if ok else ("Stopped" if info == "Stopped" else "Failed"))
        item.setForeground(QColor("#067647" if ok else "#B42318"))
        self.table.item(row, 2).setText("Saved — double-click to open the report" if ok else info)
        self.table.item(row, 3).setText(info if ok else "")
        self.table.item(row, 4).setText("Open folder" if ok else "")
        if ok:
            self.folders[row] = folder  # the HTML report; its folder holds the other files
            if self.browser.isChecked():
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _update_status(self):
        with self.runner.lock:
            waiting, running = len(self.runner.queue), self.runner.active
        self.status.setText(f"{running} running · {waiting} waiting" if running or waiting else "Ready")
        self.start_btn.setText("Add to queue" if running or waiting else "Start audits")

    def closeEvent(self, e):
        with self.runner.lock:
            busy = self.runner.active or self.runner.queue
        if busy and QMessageBox.question(self, APP, "Audits are still running. Close and stop them?") != \
                QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        self.runner.stop()
        e.accept()
        os._exit(0)  # do not wait for audit threads


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setStyleSheet(STYLE)
    f = QFont("Segoe UI")
    f.setPixelSize(13)
    app.setFont(f)
    try:
        from seo_audit_pro import brand
        app.setWindowIcon(brand.app_icon())
    except Exception:  # noqa: BLE001
        pass
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
