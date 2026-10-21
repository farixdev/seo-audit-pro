# -*- coding: utf-8 -*-
"""Report deliverables: list, view, open, download, export.

The file helpers at the top are shared with the audit detail page.
"""

import os
import shutil
import tempfile

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QMenu

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, settings, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import StatefulArea, hbox, label, vbox
from seo_audit_pro.widgets.feedback import SearchBox, combo, show_error, toast
from seo_audit_pro.widgets.table import Column, DataTable

PDF_UNAVAILABLE = ("PDF rendering is not available on this server (it needs Microsoft Word or LibreOffice). "
                   "DOCX reports are available for every audit.")
PDF_UNAVAILABLE_HERE = ("PDF reports are made on this computer and need Microsoft Word or LibreOffice, which is not "
                        "installed here. Word (DOCX) reports are available for every audit.")


def pdf_notice(data):
    """(PDF available, notice text) for a reports listing. Report files are made
    either on this computer (app mode) or on the server."""
    if data.get("made_in_app"):
        from seo_audit_pro.core import pc_work
        return pc_work.pdf_supported(), PDF_UNAVAILABLE_HERE
    return data.get("pdf_supported", True), PDF_UNAVAILABLE


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------
def report_label(row):
    return f"{(row.get('kind') or '').title()} · {(row.get('format') or '').upper()}"


def pending_file(row):
    """Not made yet: the file is created the first time it is opened (on this
    computer, or for a PDF on a server that makes reports)."""
    return not row.get("ready", 1)


def report_size(row):
    return "Made when first opened" if pending_file(row) else fmt.size(row.get("size_bytes"))


def suggested_name(row):
    kind = "SEO-Audit" if row.get("kind") == "client" else "SEO-Technical-Audit"
    return f"{kind}-{row.get('normalized_domain') or 'site'}-{(row.get('created_at') or '')[:10]}.{row.get('format')}"


def open_remote(page, path, subdir=None, done_text=None):
    """Download ``path`` into the local documents folder and open it."""
    dest = settings.downloads_dir() if not subdir else os.path.join(settings.downloads_dir(), subdir)

    def done(local):
        QDesktopServices.openUrl(QUrl.fromLocalFile(local))
        if done_text:
            toast(page, done_text, "success")

    toast(page, "Downloading…", "info", 1600)
    tasks.run(api.download, path, dest, on_done=done, on_error=lambda e: show_error(page, e, "Could not open the file"),
              owner=page)


def save_remote(page, path, suggested, file_filter):
    """Ask where to save, then download ``path`` there."""
    start = os.path.join(settings.downloads_dir(), suggested)
    target, _ = QFileDialog.getSaveFileName(page, "Save as", start, file_filter)
    if not target:
        return

    def fetch():
        tmp = tempfile.mkdtemp(prefix="seoap_")
        try:
            local = api.download(path, tmp)
            if os.path.exists(target):
                os.remove(target)
            shutil.move(local, target)
            return target
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    tasks.run(fetch, on_done=lambda p: toast(page, f"Saved to {p}", "success"),
              on_error=lambda e: show_error(page, e, "Download failed"), owner=page)


def view_client_report(page, audit_id):
    open_remote(page, f"/api/audits/{audit_id}/client-html", "Client reports")


def export_json(page, audit_id, domain):
    save_remote(page, f"/api/audits/{audit_id}/export", f"SEO-Audit-{domain or 'site'}.json", "JSON (*.json)")


def report_menu(page, row, pos, include_audit=True):
    rid = row["id"]
    if pending_file(row):
        note = lambda: toast(page, "Creating the report — the first open takes a few seconds.", "info", 5000)
    else:
        note = lambda: None
    fmt_filter = "PDF document (*.pdf)" if row.get("format") == "pdf" else "Word document (*.docx)"
    m = QMenu(page)
    m.addAction(icons.icon("eye", C["text_2"]), "View client report", lambda: view_client_report(page, row["audit_id"]))
    m.addAction(icons.icon("external", C["text_2"]), f"Open {report_label(row)}",
                lambda: (note(), open_remote(page, f"/api/reports/{rid}/download")))
    m.addAction(icons.icon("download", C["text_2"]), "Download…",
                lambda: (note(), save_remote(page, f"/api/reports/{rid}/download", suggested_name(row), fmt_filter)))
    m.addAction(icons.icon("file", C["text_2"]), "Export audit data (JSON)…",
                lambda: export_json(page, row["audit_id"], row.get("normalized_domain")))
    if include_audit:
        m.addSeparator()
        m.addAction(icons.icon("audit", C["text_2"]), "Open audit", lambda: page.ws.navigate("audit", audit_id=row["audit_id"]))
    m.exec(pos)


def notice_frame():
    """Neutral information strip (hidden until text is set)."""
    box = QFrame()
    box.setStyleSheet(f"QFrame {{ background: {C['info_bg']}; border: 1px solid #B2DDFF; border-radius: 8px; }}"
                      f"QLabel {{ color: {C['info']}; border: none; background: transparent; }}")
    row = hbox(box, (12, 9, 12, 9), 8)
    ic = QLabel()
    ic.setPixmap(icons.pixmap("info", C["info"], 16))
    row.addWidget(ic)
    box.text = label("", wrap=True)
    row.addWidget(box.text, 1)
    box.setVisible(False)
    return box


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class ReportsPage(Page):
    title = "Reports"
    nav_key = "reports"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.params = {"page": 1, "page_size": 25}
        self.header("Reports", "Client and technical deliverables generated from completed audits.")
        bar = hbox(spacing=8)
        self.search = SearchBox("Search company or domain")
        self.search.setMinimumWidth(280)
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search, 2)
        self.kind = combo([("All report types", ""), ("Client report", "client"), ("Technical report", "technical")])
        self.kind.currentIndexChanged.connect(lambda _i: self._set(kind=self.kind.currentData()))
        bar.addWidget(self.kind)
        self.fmt = combo([("All formats", ""), ("PDF", "pdf"), ("DOCX", "docx")])
        self.fmt.currentIndexChanged.connect(lambda _i: self._set(format=self.fmt.currentData()))
        bar.addWidget(self.fmt)
        bar.addStretch(1)
        self.body.addLayout(bar)
        self.notice = notice_frame()
        self.body.addWidget(self.notice)

        self.table = DataTable([
            Column("company", "Website", "strong", 260, secondary=lambda r: r.get("normalized_domain")),
            Column("score", "Score", "score", 130),
            Column("agent_name", "Agent", "text", 160),
            Column("team_name", "Team", "text", 150),
            Column("created_at", "Date", "date", 120),
            Column("format", "Format", "text", 140, render=report_label, secondary=report_size, stretch=True),
            Column("_actions", "", "actions", 44),
        ])
        self.table.page_changed.connect(lambda p: self._set(keep_page=True, page=p))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.activated.connect(lambda r: self.ws.navigate("audit", audit_id=r["audit_id"]))
        self.table.context_requested.connect(lambda row, pos: report_menu(self, row, pos))
        surface = QFrame()
        surface.setObjectName("Surface")
        vbox(surface).addWidget(self.table)
        self.area = StatefulArea(surface)
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)

    def activate(self, **_):
        self.load()

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def load(self):
        if not self.table.model.rows:
            self.area.loading("Loading reports…")
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        self.fetch("list", api.get, "/api/reports", params=params, on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        self.table.set_page(data)
        pdf_ok, notice = pdf_notice(data)
        self.notice.text.setText(notice)
        self.notice.setVisible(not pdf_ok)
        if not data["items"]:
            if any(self.params.get(k) for k in ("search", "kind", "format")):
                self.area.empty("No reports match these filters", "Clear the search or change the filters.")
            else:
                self.area.empty("No reports yet", "Reports are listed when an audit completes; each file is "
                                "created the first time it is opened.",
                                icon_name="report")
            return
        self.area.ready()

    def on_realtime(self, ev):
        if ev.get("type") == "audit_progress" and ev.get("status") == "completed" and self.isVisible():
            self.load()
