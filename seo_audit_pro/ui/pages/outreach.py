# -*- coding: utf-8 -*-
"""
Outreach: compose (audit-grounded drafts the author always edits before
sending), drafts & sent activity, and bulk outreach with preview and explicit
confirmation. Only delivery states the server actually tracks are shown.
"""

import mimetypes
import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QLabel, QLineEdit, QPlainTextEdit, QTabWidget,
                               QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, pc_work, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, STATUS
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import (Badge, Segmented, BusyButton, StatefulArea, button, hbox, icon_button, label, vbox)
from seo_audit_pro.widgets.feedback import (Dialog, Drawer, SearchBox, confirm, kv_row, show_error, toast)
from seo_audit_pro.widgets.table import Column, DataTable

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")
MAX_ATTACHMENTS = 5
MAX_UPLOAD = 10 * 1024 * 1024
_UPLOAD_TYPES = "Documents and images (*.pdf *.docx *.png *.jpg *.jpeg)"


# ---------------------------------------------------------------------------
# Small local building blocks
# ---------------------------------------------------------------------------
class Banner(QFrame):
    """Inline notice with optional action button."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Banner")
        row = hbox(self, (12, 10, 10, 10), 10)
        self.icon = QLabel()
        self.text = label("", wrap=True)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.text, 1)
        self.action = button("", size="sm")
        self.action.setVisible(False)
        row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self._handler = None
        self.action.clicked.connect(lambda: self._handler and self._handler())
        self.setVisible(False)

    def show_message(self, text, kind="info", action_text=None, handler=None):
        fg, bg, border, ic = {
            "error": (C["danger"], C["danger_bg"], "#FECDCA", "alert_circle"),
            "warning": (C["warning"], C["warning_bg"], "#FEDF89", "alert_triangle"),
            "info": (C["info"], C["info_bg"], "#B2DDFF", "info"),
            "success": (C["success"], C["success_bg"], "#ABEFC6", "check_circle"),
        }[kind]
        self.setStyleSheet(f"#Banner {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
                           f"#Banner QLabel {{ color: {fg}; background: transparent; border: none; }}")
        self.icon.setPixmap(icons.pixmap(ic, fg, 16))
        self.text.setText(text)
        self._handler = handler
        self.action.setVisible(bool(action_text))
        if action_text:
            self.action.setText(action_text)
        self.setVisible(True)

    def clear(self):
        self.setVisible(False)


def _form_row(title, widget, align_top=False):
    w = QWidget()
    row = hbox(w, (0, 0, 0, 0), 12)
    lbl = label(title, "FieldLabel")
    lbl.setFixedWidth(84)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | (Qt.AlignmentFlag.AlignTop if align_top else Qt.AlignmentFlag.AlignVCenter))
    if align_top:
        lbl.setContentsMargins(0, 8, 0, 0)
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    return w


def _upload_file(path, endpoint):
    """Multipart upload in a background task; returns the server's upload record."""
    name = os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        res = api.request("POST", endpoint, files={"file": (name, fh, mime)}, timeout=120)
    return res["upload"]


class LeadPickerDialog(Dialog):
    """Search the caller's leads (server-scoped) and pick one."""

    def __init__(self, parent):
        super().__init__(parent, "Choose a lead", "Only leads you have access to are listed.", 640)
        self.chosen = None
        self.search = SearchBox("Search company, domain, contact")
        self.search.search.connect(self.load)
        self.body.addWidget(self.search)
        self.table = DataTable([
            Column("company", "Company", "strong", stretch=True, secondary=lambda r: r.get("normalized_domain")),
            Column("email", "Email", "text", 200),
            Column("last_audit_score", "Score", "score", 110),
        ], paginated=False)
        self.table.setMinimumHeight(320)
        self.table.activated.connect(self._pick)
        frame = QFrame()
        frame.setObjectName("Surface")
        vbox(frame).addWidget(self.table)
        self.body.addWidget(frame, 1)
        self.status = label("", "Meta")
        self.body.addWidget(self.status)
        self.add_buttons("Select")
        self.primary.clicked.connect(lambda: self._pick(self.table.current_row()))
        self._gen = 0
        self.load("")
        self.search.setFocus()

    def load(self, text):
        self._gen += 1
        gen = self._gen
        self.status.setText("Searching…")
        params = {"page_size": 25, "sort": "company", "order": "asc"}
        if text:
            params["search"] = text

        def done(data):
            if gen != self._gen:
                return
            self.table.set_rows(data.get("items") or [])
            total = data.get("total", 0)
            self.status.setText("No leads match this search." if not total else
                                f"Showing {min(total, 25)} of {fmt.number(total)}. Double-click to select.")

        tasks.run(api.get, "/api/leads", params=params, on_done=done,
                  on_error=lambda e: self.status.setText(e.message), owner=self)

    def _pick(self, row):
        if not row:
            self.show_error(api.ApiError(0, "Select a lead from the list."))
            return
        self.chosen = row
        self.accept()


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
class ComposePanel(QWidget):
    """Email composer. Drafts are saved server-side; Send queues for delivery."""
    sent = Signal(dict)
    saved = Signal(dict)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.lead = None
        self.email_id = None
        self.email_status = None
        self.audit_id = None
        self.uploads = []          # [{id, name, size_bytes}]
        self.report_boxes = {}     # report_id -> QCheckBox
        self.smtp_ok = True
        self._create_key = None
        self._send_key = None

        outer = vbox(self, (0, 16, 0, 0), 12)
        self.smtp_banner = Banner()
        outer.addWidget(self.smtp_banner)
        self.notice = Banner()
        outer.addWidget(self.notice)

        card = QFrame()
        card.setObjectName("Surface")
        cl = vbox(card, (20, 18, 20, 18), 12)

        lead_box = QWidget()
        lb = hbox(lead_box, spacing=10)
        self.lead_label = label("No lead selected", "Muted")
        self.lead_label.setTextFormat(Qt.TextFormat.RichText)
        lb.addWidget(self.lead_label, 1)
        self.open_lead_btn = button("Open lead", "ghost", size="sm")
        self.open_lead_btn.clicked.connect(lambda: self.lead and self.page.ws.navigate("lead", lead_id=self.lead["id"]))
        self.open_lead_btn.setVisible(False)
        lb.addWidget(self.open_lead_btn)
        self.pick_btn = button("Choose lead…", icon_name="leads", size="sm")
        self.pick_btn.clicked.connect(self.pick_lead)
        lb.addWidget(self.pick_btn)
        cl.addWidget(_form_row("Lead", lead_box))

        self.to = QLineEdit()
        self.to.setPlaceholderText("recipient@company.com")
        cl.addWidget(_form_row("To", self.to))
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Subject")
        self.subject.setMaxLength(200)
        cl.addWidget(_form_row("Subject", self.subject))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Write your message, or generate a draft from the lead's latest audit and edit it.")
        self.body_edit.setMinimumHeight(260)
        cl.addWidget(_form_row("Message", self.body_edit, align_top=True), 1)

        att = QWidget()
        al = vbox(att, spacing=8)
        self.reports_host = QWidget()
        self.reports_layout = vbox(self.reports_host, spacing=6)
        al.addWidget(self.reports_host)
        self.uploads_host = QWidget()
        self.uploads_layout = vbox(self.uploads_host, spacing=6)
        al.addWidget(self.uploads_host)
        arow = hbox(spacing=10)
        self.attach_btn = button("Attach file…", icon_name="paperclip", size="sm")
        self.attach_btn.clicked.connect(self.attach_file)
        arow.addWidget(self.attach_btn)
        arow.addWidget(label(f"Up to {MAX_ATTACHMENTS} files · PDF, DOCX, PNG or JPG · 10 MB each", "Meta"))
        arow.addStretch(1)
        al.addLayout(arow)
        cl.addWidget(_form_row("Attachments", att, align_top=True))

        actions = hbox(spacing=8)
        self.state_label = label("", "Meta")
        actions.addWidget(self.state_label, 1)
        self.discard_btn = button("New email", "ghost", icon_name="plus")
        self.discard_btn.clicked.connect(self._new_email)
        actions.addWidget(self.discard_btn)
        self.generate_btn = BusyButton("Generate from audit", None, icon_name="zap")
        self.generate_btn.clicked.connect(self.generate)
        actions.addWidget(self.generate_btn)
        self.save_btn = BusyButton("Save draft", None, icon_name="file")
        self.save_btn.clicked.connect(self.save_draft)
        actions.addWidget(self.save_btn)
        self.send_btn = BusyButton("Send", "primary", icon_name="send")
        self.send_btn.clicked.connect(self.send)
        actions.addWidget(self.send_btn)
        cl.addLayout(actions)
        outer.addWidget(card, 1)
        self._update_state()

    # -- SMTP status --------------------------------------------------------
    def load_status(self):
        def done(st):
            self.smtp_ok = bool(st.get("smtp_configured"))
            if self.smtp_ok:
                self.smtp_banner.clear()
            elif session.is_manager():
                self.smtp_banner.show_message("Email sending is not configured yet. Drafts can be saved; set up SMTP "
                                              "to send.", "warning", "Open settings",
                                              lambda: self.page.ws.navigate("settings"))
            else:
                self.smtp_banner.show_message("Email sending is not configured yet. You can save drafts now; ask a "
                                              "manager to set up SMTP to send.", "warning")
            self._update_state()

        tasks.run(api.get, "/api/outreach/status", on_done=done, on_error=lambda _e: None, owner=self)

    # -- lead & attachments ---------------------------------------------------
    def pick_lead(self):
        dlg = LeadPickerDialog(self)
        if dlg.exec() and dlg.chosen:
            self.set_lead(dlg.chosen)

    def set_lead(self, lead, selected_reports=()):
        changed = not self.lead or self.lead["id"] != lead["id"]
        self.lead = lead
        domain = lead.get("normalized_domain") or ""
        self.lead_label.setText(f"<b>{_esc(lead.get('company'))}</b>"
                                f"<span style='color:{C['text_3']};'>&nbsp;&nbsp;{_esc(domain)}</span>")
        self.lead_label.setObjectName("")
        self.lead_label.setStyleSheet("")
        self.open_lead_btn.setVisible(True)
        self.pick_btn.setText("Change…")
        if changed and not self.to.text().strip() and lead.get("email"):
            self.to.setText(lead["email"])
        if changed:
            self.audit_id = None
        self.load_reports(selected_reports)
        self._update_state()

    def load_lead(self, lead_id):
        tasks.run(api.get, f"/api/leads/{lead_id}", on_done=lambda r: self.set_lead(r["lead"]),
                  on_error=lambda e: show_error(self, e, "Lead"), owner=self)

    def load_reports(self, selected=()):
        _clear(self.reports_layout)
        self.report_boxes = {}
        if not self.lead:
            return
        selected = set(selected)
        lead_id = self.lead["id"]

        def done(data):
            if not self.lead or self.lead["id"] != lead_id:
                return
            _clear(self.reports_layout)
            self.report_boxes = {}
            items = data.get("items") or []
            self.report_rows = {r["id"]: r for r in items}
            self.reports_made_in_app = bool(data.get("made_in_app"))
            if not items:
                self.reports_layout.addWidget(label("No audit reports for this lead yet. Run an audit to attach one.", "Meta"))
                return
            for r in items[:8]:
                kind = "Client report" if r["kind"] == "client" else "Technical report"
                size = "created when sent" if not r.get("ready", 1) else fmt.size(r["size_bytes"])
                cb = QCheckBox(f"{kind} · {r['format'].upper()} · {size} · {fmt.date(r['created_at'])}")
                cb.setChecked(r["id"] in selected)
                cb.toggled.connect(lambda _c: self._update_state())
                self.report_boxes[r["id"]] = cb
                self.reports_layout.addWidget(cb)
            self._update_state()

        tasks.run(api.get, "/api/reports", params={"lead_id": lead_id, "page_size": 20}, on_done=done,
                  on_error=lambda e: self.reports_layout.addWidget(label(e.message, "Meta")), owner=self)

    def _attachment_count(self):
        return sum(1 for cb in self.report_boxes.values() if cb.isChecked()) + len(self.uploads)

    def attach_file(self):
        if self._attachment_count() >= MAX_ATTACHMENTS:
            toast(self, f"You can attach at most {MAX_ATTACHMENTS} files.", "warning")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Attach file", os.path.expanduser("~"), _UPLOAD_TYPES)
        if not path:
            return
        if os.path.getsize(path) > MAX_UPLOAD:
            toast(self, "The file is larger than 10 MB.", "error")
            return
        self.attach_btn.setEnabled(False)
        self.attach_btn.setText("Uploading…")

        def done(up):
            self._reset_attach_btn()
            self.uploads.append(up)
            self._render_uploads()

        def fail(err):
            self._reset_attach_btn()
            show_error(self, err, "Attachment not added")

        tasks.run(_upload_file, path, "/api/outreach/uploads", on_done=done, on_error=fail, owner=self)

    def _reset_attach_btn(self):
        self.attach_btn.setEnabled(True)
        self.attach_btn.setText("Attach file…")

    def _render_uploads(self):
        _clear(self.uploads_layout)
        for up in self.uploads:
            chip = QFrame()
            chip.setStyleSheet(f"QFrame {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}"
                               "QLabel { border: none; background: transparent; }")
            row = hbox(chip, (10, 4, 4, 4), 8)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("file", C["text_2"], 14))
            row.addWidget(ic)
            row.addWidget(label(up.get("name", "file")))
            row.addWidget(label(fmt.size(up.get("size_bytes")), "Meta"))
            rm = icon_button("x", "Remove attachment", 14)
            rm.clicked.connect(lambda _c=False, uid=up["id"]: self._remove_upload(uid))
            row.addWidget(rm)
            wrap = hbox()
            wrap.addWidget(chip)
            wrap.addStretch(1)
            holder = QWidget()
            holder.setLayout(wrap)
            self.uploads_layout.addWidget(holder)
        self._update_state()

    def _remove_upload(self, upload_id):
        self.uploads = [u for u in self.uploads if u["id"] != upload_id]
        self._render_uploads()

    # -- state ----------------------------------------------------------------
    def _update_state(self):
        parts = []
        if self.email_id:
            parts.append("Draft saved" if self.email_status == "draft" else
                         ("Editing a failed email — sending retries delivery" if self.email_status == "failed" else ""))
        if self.audit_id:
            parts.append("Generated from the latest audit — review before sending")
        n = self._attachment_count()
        if n:
            parts.append(fmt.plural(n, "attachment"))
        self.state_label.setText(" · ".join(p for p in parts if p))
        self.send_btn.setEnabled(self.smtp_ok)
        self.send_btn.setToolTip("" if self.smtp_ok else "SMTP is not configured")
        audited = bool(self.lead and self.lead.get("last_audit_id"))
        self.generate_btn.setEnabled(audited)
        self.generate_btn.setToolTip("" if audited else "Run an audit on this lead first" if self.lead
                                     else "Choose a lead first")

    def has_unsaved_text(self):
        return bool(not self.email_id and (self.body_edit.toPlainText().strip() or self.subject.text().strip()))

    def _new_email(self):
        if self.has_unsaved_text() and not confirm(self, "Discard this email?",
                                                             "The unsaved message will be cleared.", "Discard", danger=True):
            return
        self.reset()

    def reset(self):
        self.lead = None
        self.email_id = None
        self.email_status = None
        self.audit_id = None
        self.uploads = []
        self._create_key = None
        self._send_key = None
        self.to.clear()
        self.subject.clear()
        self.body_edit.clear()
        self.lead_label.setText("No lead selected")
        self.open_lead_btn.setVisible(False)
        self.pick_btn.setText("Choose lead…")
        _clear(self.reports_layout)
        self.report_boxes = {}
        self._render_uploads()
        self.notice.clear()
        self._update_state()

    def load_email(self, email_id):
        """Load an existing draft (or failed email) for editing."""
        def done(res):
            e = res["email"]
            if e["status"] not in ("draft", "failed"):
                toast(self, "Only drafts and failed emails can be edited.", "info")
                return
            self.reset()
            self.email_id = e["id"]
            self.email_status = e["status"]
            self.audit_id = e.get("audit_id")
            self.to.setText(e["to_email"])
            self.subject.setText(e["subject"])
            self.body_edit.setPlainText(e.get("body") or "")
            atts = e.get("attachments") or []
            self.uploads = [{"id": a["upload_id"], "name": a.get("upload_name") or "file",
                             "size_bytes": a.get("upload_size")} for a in atts if a.get("upload_id")]
            self._render_uploads()
            if e.get("status") == "failed" and e.get("error"):
                self.notice.show_message(f"Last delivery attempt failed: {e['error']}", "error")
            tasks.run(api.get, f"/api/leads/{e['lead_id']}",
                      on_done=lambda r: self.set_lead(r["lead"], [a["report_id"] for a in atts if a.get("report_id")]),
                      on_error=lambda err: show_error(self, err, "Lead"), owner=self)

        tasks.run(api.get, f"/api/outreach/emails/{email_id}", on_done=done, on_error=lambda e: show_error(self, e),
                  owner=self)

    # -- actions --------------------------------------------------------------
    def generate(self):
        if not self.lead:
            return
        if self.body_edit.toPlainText().strip() and not confirm(
                self, "Replace the current message?", "Generating a draft replaces the subject and message with text "
                                                      "built from the lead's latest audit findings.", "Replace"):
            return
        self.generate_btn.busy(True, "Generating…")
        self.notice.clear()

        def done(d):
            self.generate_btn.busy(False)
            self.subject.setText(d["subject"])
            self.body_edit.setPlainText(d["body"])
            if not self.to.text().strip() and d.get("to_email"):
                self.to.setText(d["to_email"])
            self.audit_id = d.get("audit_id")
            self._update_state()
            self.body_edit.setFocus()

        def fail(err):
            self.generate_btn.busy(False)
            if err.code == "no_audit":
                self.notice.show_message(err.message, "info", "Open lead",
                                         lambda: self.page.ws.navigate("lead", lead_id=self.lead["id"]))
            else:
                show_error(self, err, "Draft not generated")

        tasks.run(api.post, "/api/outreach/generate", {"lead_id": self.lead["id"]}, on_done=done, on_error=fail, owner=self)

    def _validate(self):
        problems = []
        if not self.lead:
            problems.append("Choose the lead this email is about.")
        if not EMAIL_RE.match(self.to.text().strip()):
            problems.append("Enter a valid recipient email address.")
        if not self.subject.text().strip():
            problems.append("Add a subject.")
        if not self.body_edit.toPlainText().strip():
            problems.append("Write a message.")
        if self._attachment_count() > MAX_ATTACHMENTS:
            problems.append(f"Attach at most {MAX_ATTACHMENTS} files.")
        if problems:
            self.notice.show_message(" ".join(problems), "error")
            return False
        self.notice.clear()
        return True

    def _payload(self):
        return {"lead_id": self.lead["id"], "to_email": self.to.text().strip(), "subject": self.subject.text().strip(),
                "body": self.body_edit.toPlainText().strip(), "audit_id": self.audit_id,
                "report_ids": [rid for rid, cb in self.report_boxes.items() if cb.isChecked()],
                "upload_ids": [u["id"] for u in self.uploads]}

    def _busy(self, on):
        for b, text in ((self.save_btn, "Saving…"), (self.send_btn, "Sending…")):
            b.busy(on, text)
        if not on:
            self._update_state()
        self.generate_btn.setEnabled(not on and bool(self.lead and self.lead.get("last_audit_id")))
        self.discard_btn.setEnabled(not on)

    def save_draft(self):
        if not self._validate():
            return
        payload = self._payload()
        self._busy(True)

        def done(res):
            self._busy(False)
            e = res["email"]
            self.email_id, self.email_status = e["id"], e["status"]
            self._create_key = None
            self._update_state()
            toast(self, "Draft saved.", "success")
            self.saved.emit(e)

        def fail(err):
            self._busy(False)
            if err.status and err.status < 500:
                self._create_key = None
            self.notice.show_message(err.message, "error")

        if self.email_id:
            tasks.run(api.patch, f"/api/outreach/emails/{self.email_id}", payload, on_done=done, on_error=fail, owner=self)
        else:
            self._create_key = self._create_key or api.new_idempotency_key()
            tasks.run(api.post, "/api/outreach/emails", payload, idempotency_key=self._create_key,
                      on_done=done, on_error=fail, owner=self)

    def send(self):
        if not self._validate():
            return
        payload = self._payload()
        n = self._attachment_count()
        text = (f"To: {payload['to_email']}\nSubject: {payload['subject']}\n"
                f"Attachments: {n if n else 'none'}\n\nThe email is queued and delivered through the agency mail "
                "server. It cannot be recalled once sent.")
        if not confirm(self, "Send this email?", text, "Send email"):
            return
        self._busy(True)
        self._send_key = self._send_key or api.new_idempotency_key()
        key = self._send_key
        email_id = self.email_id
        rows = getattr(self, "report_rows", {})
        to_upload = [rows[rid] for rid in payload["report_ids"]
                     if getattr(self, "reports_made_in_app", False) and rid in rows and not rows[rid].get("ready", 1)]

        def work():
            for row in to_upload:  # report files are made on this computer; the server needs a copy to send them
                pc_work.upload_report(row)
            if email_id:
                api.patch(f"/api/outreach/emails/{email_id}", payload)
                return send_email(email_id, key)
            return api.post("/api/outreach/emails", dict(payload, send=True), idempotency_key=key)

        def done(res):
            self._busy(False)
            e = res["email"]
            toast(self, f"Email to {e['to_email']} queued for delivery.", "success")
            self.reset()
            self.sent.emit(e)

        def fail(err):
            self._busy(False)
            if err.status and err.status < 500:
                self._send_key = None
            self.notice.show_message(err.message, "error")

        tasks.run(work, on_done=done, on_error=fail, owner=self)


def send_email(email_id, key):
    """Queue an email for delivery (blocking). When its reports were made on this
    computer and the server has no copy yet, upload them and try once more."""
    try:
        return api.post(f"/api/outreach/emails/{email_id}/send", idempotency_key=key)
    except api.ApiError as e:
        if e.code != "report_not_uploaded":
            raise
        for row in e.data.get("reports") or []:
            pc_work.upload_report(row)
        return api.post(f"/api/outreach/emails/{email_id}/send", idempotency_key=api.new_idempotency_key())


# ---------------------------------------------------------------------------
# Activity (drafts & sent)
# ---------------------------------------------------------------------------
_ACTIVITY_FILTERS = [("All", ""), ("Drafts", "draft"), ("Queued", "queued"), ("Sending", "sending"),
                     ("Sent", "sent"), ("Failed", "failed")]


class ActivityPanel(QWidget):
    """Email activity table with a detail drawer."""
    edit_requested = Signal(str)

    def __init__(self, page, top_margin=16):
        super().__init__()
        self.page = page
        self.params = {"page": 1, "page_size": 25}
        self._dirty = False
        lay = vbox(self, (0, top_margin, 0, 0), 12)
        bar = hbox(spacing=8)
        self.seg = Segmented(_ACTIVITY_FILTERS)
        self.seg.changed.connect(lambda v: self._set(status=v))
        self.seg.buttons["sending"].setVisible(False)
        bar.addWidget(self.seg)
        self.batch_chip = QFrame()
        self.batch_chip.setStyleSheet(f"QFrame {{ background: {C['accent_soft']}; border-radius: 6px; }}"
                                      f"QLabel {{ color: {C['accent']}; background: transparent; font-weight: 600; }}")
        bc = hbox(self.batch_chip, (10, 2, 2, 2), 4)
        self.batch_label = label("")
        bc.addWidget(self.batch_label)
        clear_batch = icon_button("x", "Show all emails", 13, C["accent"])
        clear_batch.clicked.connect(lambda: self.set_batch(None))
        bc.addWidget(clear_batch)
        self.batch_chip.setVisible(False)
        bar.addWidget(self.batch_chip)
        bar.addStretch(1)
        self.search = SearchBox("Search recipient, company, subject")
        self.search.setFixedWidth(300)
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search)
        refresh = button("", icon_name="refresh", tooltip="Refresh")
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.load)
        bar.addWidget(refresh)
        lay.addLayout(bar)

        cols = [
            Column("to_email", "To", "strong", 220),
            Column("company", "Company", "text", 180, secondary=lambda r: r.get("normalized_domain")),
            Column("subject", "Subject", "text", 260, stretch=True),
            Column("status", "Status", "status", 110),
        ]
        if not session.is_agent():
            cols.append(Column("sender_name", "Sender", "text", 150))
        cols += [Column("updated_at", "Updated", "relative", 110), Column("sent_at", "Sent", "datetime", 150)]
        self.table = DataTable(cols)
        self.table.page_changed.connect(lambda p: self._set(keep_page=True, page=p))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.activated.connect(lambda r: self.open_email(r["id"]))
        surface = QFrame()
        surface.setObjectName("Surface")
        vbox(surface).addWidget(self.table)
        self.area = StatefulArea(surface)
        self.area.retry.connect(self.load)
        lay.addWidget(self.area, 1)
        self.drawer = Drawer(page, 560)

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def set_batch(self, batch_id, count=None):
        self.params["batch_id"] = batch_id
        self.params["page"] = 1
        self.batch_chip.setVisible(bool(batch_id))
        if batch_id:
            self.batch_label.setText(f"Bulk send · {fmt.plural(count, 'email')}" if count else "Bulk send")
        self.load()

    def load(self):
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        if not self.table.model.rows:
            self.area.loading("Loading emails…")
        self.page.fetch("activity", api.get, "/api/outreach/emails", params=params, on_done=self._render,
                        on_error=self.area.error)

    def _render(self, data):
        counts = data.get("counts") or {}
        total_all = sum(counts.values())
        for text, value in _ACTIVITY_FILTERS:
            n = total_all if not value else counts.get(value, 0)
            self.seg.set_text(value, f"{text} · {n}" if n else text)
        self.seg.buttons["sending"].setVisible(bool(counts.get("sending")) or self.params.get("status") == "sending")
        self.table.set_page(data)
        if not data.get("items"):
            if self.params.get("status") or self.params.get("search") or self.params.get("batch_id"):
                self.area.empty("No emails match", "Change the filter or search.")
            else:
                self.area.empty("No emails yet", "Drafts and sent emails appear here once you compose one.",
                                icon_name="mail")
            return
        self.area.ready()

    def mark_dirty(self):
        if self.isVisible():
            self.load()
        else:
            self._dirty = True

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self._dirty = False
            self.load()

    # -- detail drawer ---------------------------------------------------------
    def open_email(self, email_id):
        self.drawer.clear()
        self.drawer.content_layout.addWidget(label("Loading…", "Muted"))
        self.drawer.open_drawer("Email")
        tasks.run(api.get, f"/api/outreach/emails/{email_id}", on_done=lambda r: self._fill_drawer(r["email"]),
                  on_error=lambda e: (self.drawer.close_drawer(), show_error(self, e)), owner=self)

    def _fill_drawer(self, e):
        d = self.drawer
        d.clear()
        d.title.setText(e["subject"])
        lay = d.content_layout
        fg, bg, text = STATUS.get(e["status"], (C["text_2"], C["neutral_bg"], e["status"].title()))
        badge_row = hbox(spacing=8)
        badge_row.addWidget(Badge(text, fg, bg))
        badge_row.addStretch(1)
        lay.addLayout(badge_row)
        if e["status"] == "failed" and e.get("error"):
            err = Banner()
            err.show_message(e["error"], "error")
            lay.addWidget(err)
        link = button(f"{e.get('company') or 'Lead'}", "link")
        link.clicked.connect(lambda: (d.close_drawer(), self.page.ws.navigate("lead", lead_id=e["lead_id"])))
        lead_link = QWidget()
        ll = hbox(lead_link)
        ll.addWidget(link)
        ll.addStretch(1)
        rows = [("To", e["to_email"]), ("Lead", lead_link), ("Sender", e.get("sender_name")),
                ("Created", fmt.datetime_(e.get("created_at"))), ("Queued", fmt.datetime_(e.get("queued_at"))),
                ("Sent", fmt.datetime_(e.get("sent_at")))]
        for k, v in rows:
            lay.addWidget(kv_row(k, v))
        atts = e.get("attachments") or []
        if atts:
            lay.addWidget(label("Attachments", "FieldLabel"))
            for a in atts:
                if a.get("report_id"):
                    name = f"{'Client' if a.get('report_kind') == 'client' else 'Technical'} report · " \
                           f"{(a.get('report_format') or '').upper()}"
                else:
                    name = f"{a.get('upload_name') or 'file'} · {fmt.size(a.get('upload_size'))}"
                row = hbox(spacing=8)
                ic = QLabel()
                ic.setPixmap(icons.pixmap("paperclip", C["text_2"], 14))
                row.addWidget(ic)
                row.addWidget(label(name), 1)
                lay.addLayout(row)
        lay.addWidget(label("Message", "FieldLabel"))
        body = label(e.get("body") or "", wrap=True, selectable=True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 8px; }}"
                            "QLabel { border: none; background: transparent; }")
        vbox(frame, (14, 12, 14, 12)).addWidget(body)
        lay.addWidget(frame)
        actions = hbox(spacing=8)
        mine = e.get("sender_id") == session.user_id or session.is_manager()
        if e["status"] in ("draft", "failed") and mine:
            edit = button("Edit" if e["status"] == "draft" else "Edit and resend", icon_name="edit")
            edit.clicked.connect(lambda: (d.close_drawer(), self.edit_requested.emit(e["id"])))
            actions.addWidget(edit)
        if e["status"] == "failed" and mine:
            retry = BusyButton("Retry delivery", "primary", icon_name="refresh")
            retry.clicked.connect(lambda: self._retry(e, retry))
            actions.addWidget(retry)
        if e["status"] == "draft" and mine:
            delete = button("Delete draft", "danger", icon_name="trash")
            delete.clicked.connect(lambda: self._delete(e))
            actions.addWidget(delete)
        actions.addStretch(1)
        lay.addLayout(actions)
        lay.addStretch(1)

    def _retry(self, e, btn):
        if not confirm(self, "Retry delivery?", f"The email to {e['to_email']} will be queued again.", "Retry"):
            return
        btn.busy(True, "Queuing…")
        key = api.new_idempotency_key()

        def done(_r):
            toast(self, "Email queued again.", "success")
            self.drawer.close_drawer()
            self.load()

        def fail(err):
            btn.busy(False)
            show_error(self, err)

        tasks.run(send_email, e["id"], key, on_done=done, on_error=fail, owner=self)

    def _delete(self, e):
        if not confirm(self, "Delete this draft?", "The draft and its attachment list are removed. Sent and failed "
                                                    "emails are always kept.", "Delete draft", danger=True):
            return
        tasks.run(api.delete, f"/api/outreach/emails/{e['id']}",
                  on_done=lambda _r: (toast(self, "Draft deleted.", "success"), self.drawer.close_drawer(), self.load()),
                  on_error=lambda err: show_error(self, err), owner=self)


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------
class BulkPanel(QWidget):
    queued = Signal(str, int)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.lead_ids = []
        self.preview = None
        self._key = None
        lay = vbox(self, (0, 16, 0, 0), 12)
        content = QWidget()
        cl = vbox(content, spacing=16)
        top = QFrame()
        top.setObjectName("Surface")
        tl = hbox(top, (20, 16, 20, 16), 24)
        self.count_value = label("—", "MetricValue")
        col = vbox(spacing=2)
        col.addWidget(label("Recipients", "MetricLabel"))
        col.addWidget(self.count_value)
        tl.addLayout(col)
        self.summary = label("", "Muted", wrap=True)
        tl.addWidget(self.summary, 1)
        self.queue_btn = BusyButton("Queue emails", "primary", icon_name="send")
        self.queue_btn.clicked.connect(self.queue)
        tl.addWidget(self.queue_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cl.addWidget(top)
        self.smtp_banner = Banner()
        cl.addWidget(self.smtp_banner)
        row = hbox(spacing=16)
        sample = QFrame()
        sample.setObjectName("Surface")
        sl = vbox(sample, (20, 18, 20, 18), 10)
        sl.addWidget(label("Sample email", "SectionTitle"))
        self.sample_meta = label("", "Meta", wrap=True)
        sl.addWidget(self.sample_meta)
        self.sample_subject = label("", wrap=True)
        self.sample_subject.setStyleSheet("font-weight: 600;")
        sl.addWidget(self.sample_subject)
        self.sample_body = label("", wrap=True, selectable=True)
        self.sample_body.setTextFormat(Qt.TextFormat.PlainText)
        sl.addWidget(self.sample_body)
        sl.addStretch(1)
        row.addWidget(sample, 3)
        skipped = QFrame()
        skipped.setObjectName("Surface")
        kl = vbox(skipped, (0, 0, 0, 0), 0)
        sh = hbox()
        sh.setContentsMargins(20, 18, 20, 10)
        self.skipped_title = label("Skipped", "SectionTitle")
        sh.addWidget(self.skipped_title, 1)
        kl.addLayout(sh)
        self.skipped_table = DataTable([
            Column("company", "Lead", "strong", 200, render=lambda r: r.get("company") or "Not accessible"),
            Column("reason", "Reason", "text", stretch=True),
        ], paginated=False)
        kl.addWidget(self.skipped_table, 1)
        row.addWidget(skipped, 2)
        cl.addLayout(row, 1)
        cl.addWidget(label("Each email is generated from that lead's latest completed audit and contains only its real "
                           "findings. Leads without an email address or completed audit, archived, won or lost leads, "
                           "and leads emailed in the last 14 days are skipped automatically.", "Meta", wrap=True))
        self.area = StatefulArea(content, skeleton=None)
        lay.addWidget(self.area, 1)
        self._retry_action = self.load_preview
        self.area.retry.connect(lambda: self._retry_action())
        self._show_empty()

    def _show_empty(self):
        self.area.empty("No leads selected", "Select leads in the Leads list, then choose Email to prepare a bulk send.",
                        "Go to leads", icon_name="leads")
        self.area.state.btn.setIcon(icons.icon("leads", C["text_2"], 16))
        self._retry_action = lambda: self.page.ws.navigate("leads")

    def set_leads(self, ids):
        self.lead_ids = list(ids or [])
        self._key = None
        self._retry_action = self.load_preview
        self.area.state.btn.setIcon(icons.icon("refresh", C["text_2"], 16))
        if not self.lead_ids:
            self._show_empty()
            return
        self.load_preview()

    def load_preview(self):
        self.area.loading("Preparing preview…")
        self.page.fetch("bulk", api.post, "/api/outreach/bulk/preview", {"lead_ids": self.lead_ids},
                        on_done=self._render, on_error=self.area.error)
        tasks.run(api.get, "/api/outreach/status", on_done=self._status, on_error=lambda _e: None, owner=self)

    def _status(self, st):
        if st.get("smtp_configured"):
            self.smtp_banner.clear()
        else:
            self.smtp_banner.show_message("Email sending is not configured, so bulk emails cannot be queued yet.",
                                          "warning", "Open settings" if session.is_manager() else None,
                                          (lambda: self.page.ws.navigate("settings")) if session.is_manager() else None)
            self.queue_btn.setEnabled(False)

    def _render(self, p):
        self.preview = p
        n = p.get("recipient_count", 0)
        skipped = p.get("skipped") or []
        self.count_value.setText(fmt.number(n))
        interval = p.get("send_interval_seconds") or 0
        self.summary.setText(f"{fmt.plural(len(self.lead_ids), 'lead')} selected · {fmt.plural(n, 'email')} will be "
                             f"queued · {len(skipped)} skipped. Emails are sent one every {interval:g} s.")
        self.queue_btn.setText(f"Queue {fmt.plural(n, 'email')}" if n else "Nothing to send")
        self.queue_btn._idle_text = self.queue_btn.text()
        self.queue_btn.setEnabled(n > 0)
        s = p.get("sample")
        if s:
            self.sample_meta.setText(f"For {s.get('company', '')} · {s.get('to_email', '')}. Every email is "
                                     "personalised from its own lead's audit.")
            self.sample_subject.setText(s["subject"])
            self.sample_body.setText(s["body"])
        else:
            self.sample_meta.setText("None of the selected leads can be emailed.")
            self.sample_subject.setText("")
            self.sample_body.setText("")
        self.skipped_title.setText(f"Skipped · {len(skipped)}" if skipped else "Skipped")
        self.skipped_table.set_rows([dict(r, id=r.get("lead_id")) for r in skipped])
        self.area.ready()

    def queue(self):
        p = self.preview or {}
        n = p.get("recipient_count", 0)
        if not n:
            return
        interval = p.get("send_interval_seconds") or 0
        if not confirm(self, f"Queue {fmt.plural(n, 'email')}?",
                       f"{fmt.plural(n, 'email')} will be queued and sent one every {interval:g} s through the agency "
                       f"mail server. {len(p.get('skipped') or [])} selected lead(s) are skipped. This cannot be undone.",
                       f"Queue {n}"):
            return
        self.queue_btn.busy(True, "Queuing…")
        self._key = self._key or api.new_idempotency_key()

        def done(res):
            self.queue_btn.busy(False)
            toast(self, f"{fmt.plural(res['queued'], 'email')} queued for delivery.", "success")
            batch, count = res["batch_id"], res["queued"]
            self.lead_ids = []
            self.preview = None
            self._key = None
            self._show_empty()
            self.queued.emit(batch, count)

        def fail(err):
            self.queue_btn.busy(False)
            if err.status and err.status < 500:
                self._key = None
            show_error(self, err, "Bulk send not queued")

        tasks.run(api.post, "/api/outreach/bulk", {"lead_ids": self.lead_ids, "confirm_count": len(self.lead_ids)},
                  idempotency_key=self._key, timeout=120, on_done=done, on_error=fail, owner=self)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
class OutreachPage(Page):
    nav_key = "outreach"

    def __init__(self, ws):
        super().__init__(ws, spacing=4)
        self.title = "Emails" if session.is_manager() else "Outreach"
        self.header(self.title, "Audit-grounded outreach. Drafts are built only from real audit findings and are always "
                                "yours to edit before sending.")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.compose = ComposePanel(self)
        self.activity = ActivityPanel(self)
        self.bulk = BulkPanel(self)
        self.tabs.addTab(self.compose, "Compose")
        self.tabs.addTab(self.activity, "Drafts && sent")
        self.tabs.addTab(self.bulk, "Bulk outreach")
        self.body.addWidget(self.tabs, 1)
        self.compose.sent.connect(lambda _e: (self.activity.mark_dirty(), self.tabs.setCurrentWidget(self.activity)))
        self.compose.saved.connect(lambda _e: self.activity.mark_dirty())
        self.activity.edit_requested.connect(self._edit)
        self.bulk.queued.connect(self._bulk_queued)
        self.tabs.currentChanged.connect(self._tab_changed)

    def activate(self, lead_id=None, email_id=None, bulk_lead_ids=None, edit_email_id=None, **_):
        self.compose.load_status()
        if bulk_lead_ids:
            self.tabs.setCurrentWidget(self.bulk)
            self.bulk.set_leads(bulk_lead_ids)
        elif edit_email_id:
            self._edit(edit_email_id)
        elif lead_id:
            self.tabs.setCurrentWidget(self.compose)
            if self.compose.lead is None or self.compose.lead["id"] != lead_id:
                if self.compose.has_unsaved_text() and not confirm(
                        self, "Start a new email?", "The email you were writing has not been saved and will be "
                                                    "cleared.", "Start new email", danger=True):
                    return
                self.compose.reset()
                self.compose.load_lead(lead_id)
        elif email_id:
            self.tabs.setCurrentWidget(self.activity)
            self.activity.load()
            self.activity.open_email(email_id)
        elif self.tabs.currentWidget() is self.activity:
            self.activity.load()

    def _tab_changed(self, _i):
        if self.tabs.currentWidget() is self.activity:
            self.activity.load()

    def _edit(self, email_id):
        self.tabs.setCurrentWidget(self.compose)
        self.compose.load_email(email_id)

    def _bulk_queued(self, batch_id, count):
        self.tabs.setCurrentWidget(self.activity)
        self.activity.set_batch(batch_id, count)

    def on_realtime(self, ev):
        if ev.get("type") == "notification" and (ev.get("notification") or {}).get("type") in ("email_sent", "email_failed"):
            self.activity.mark_dirty()


class OutreachActivityPage(Page):
    title = "Email activity"
    nav_key = "outreach_activity"

    def __init__(self, ws):
        super().__init__(ws, spacing=4)
        h = self.header("Email activity", "Every draft, queued, sent and failed email across the agency.")
        compose = h.add_action(button("Compose", "primary", icon_name="edit"))
        compose.clicked.connect(lambda: self.ws.navigate("outreach"))
        self.activity = ActivityPanel(self, top_margin=12)
        self.activity.edit_requested.connect(lambda eid: self.ws.navigate("outreach", edit_email_id=eid))
        self.body.addWidget(self.activity, 1)

    def activate(self, email_id=None, **_):
        self.activity.load()
        if email_id:
            self.activity.open_email(email_id)

    def on_realtime(self, ev):
        if ev.get("type") == "notification" and (ev.get("notification") or {}).get("type") in ("email_sent", "email_failed"):
            self.activity.mark_dirty()


def _clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            _clear(it.layout())


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
