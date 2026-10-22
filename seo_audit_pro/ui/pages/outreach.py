# -*- coding: utf-8 -*-
"""
Outreach. The app does not send email: it writes the email from the lead's
latest audit findings, the person edits it, copies it (or opens it in their own
mail app) and sends it from their own mailbox. Every contact, by email or by
phone, is then recorded on the lead (outreached yes / no, how, and with which
address or number), so the team and Managers see who was contacted.
"""

from urllib.parse import quote

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QPlainTextEdit, QTabWidget, QWidget

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import BusyButton, Segmented, StatefulArea, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Field, SearchBox, confirm, line_edit, show_error, text_area, toast
from seo_audit_pro.widgets.table import Column, DataTable

METHOD_LABEL = {"email": "Email", "call": "Call"}


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


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def outreach_text(lead):
    """One line about a lead's recorded outreach."""
    if not lead or not lead.get("last_contact_at"):
        return "Not outreached yet"
    how = METHOD_LABEL.get(lead.get("outreach_method"), "Contacted")
    contact = lead.get("outreach_contact")
    return f"Outreached · {how}{f' · {contact}' if contact else ''} · {fmt.relative(lead['last_contact_at'])}"


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


class MarkOutreachDialog(Dialog):
    """Record that a lead was (or was not) contacted, by email or call, and with what."""

    def __init__(self, parent, lead, method="email", contact=None):
        super().__init__(parent, "Record outreach", lead.get("company") or "", 480)
        self.lead = lead
        self.result_lead = None
        self._key = api.new_idempotency_key()
        self.yes = Segmented([("Yes, contacted", "yes"), ("No, not contacted", "no")],
                             "no" if lead.get("last_contact_at") and method is None else "yes")
        self.yes.changed.connect(lambda _v: self._update())
        self.body.addWidget(Field("Outreached", self.yes))
        self.method = Segmented([("Email", "email"), ("Call", "call")], method or lead.get("outreach_method") or "email")
        self.method.changed.connect(lambda _v: self._method_changed())
        self.method_field = Field("How", self.method)
        self.body.addWidget(self.method_field)
        self.contact = line_edit("", contact or "")
        self.contact_field = Field("Email address used", self.contact)
        self.body.addWidget(self.contact_field)
        self.note = text_area("Optional: what was said, when to follow up…", "", 80)
        self.body.addWidget(Field("Note", self.note))
        self.add_buttons("Save")
        self.primary.clicked.connect(self.save)
        self._typed = bool(contact)
        self.contact.textEdited.connect(lambda _t: setattr(self, "_typed", True))
        self._method_changed()

    def _method_changed(self):
        email = self.method.value() == "email"
        self.contact_field.title.setText("Email address used" if email else "Phone number called")
        self.contact.setPlaceholderText("name@company.com" if email else "+1 555 0100")
        if not self._typed:
            self.contact.setText((self.lead.get("email") if email else self.lead.get("phone")) or "")
        self._update()

    def _update(self):
        on = self.yes.value() == "yes"
        self.method_field.setVisible(on)
        self.contact_field.setVisible(on)

    def save(self):
        on = self.yes.value() == "yes"
        payload = {"outreached": on, "note": self.note.toPlainText().strip()}
        if on:
            payload.update(method=self.method.value(), contact=self.contact.text().strip())
            if not payload["contact"]:
                self.contact_field.set_error("Enter the email address used." if payload["method"] == "email"
                                             else "Enter the phone number called.")
                return
        self.contact_field.set_error("")
        self.primary.busy(True, "Saving…")

        def done(res):
            self.result_lead = res["lead"]
            self.accept()

        def fail(err):
            self.primary.busy(False)
            if err.data.get("field") == "contact":
                self.contact_field.set_error(err.message)
            else:
                self.show_error(err)

        tasks.run(api.post, f"/api/leads/{self.lead['id']}/outreach", payload, idempotency_key=self._key,
                  on_done=done, on_error=fail, owner=self)


def mark_outreach(page, lead, method=None, contact=None, on_saved=None):
    """Open the outreach dialog for a lead; ``on_saved(lead)`` gets the updated lead."""
    dlg = MarkOutreachDialog(page, lead, method, contact)
    if dlg.exec() and dlg.result_lead:
        toast(page, outreach_text(dlg.result_lead) if dlg.result_lead.get("last_contact_at") else
              "Marked as not outreached.", "success")
        if on_saved:
            on_saved(dlg.result_lead)
        return dlg.result_lead
    return None


# ---------------------------------------------------------------------------
# Write email
# ---------------------------------------------------------------------------
class ComposePanel(QWidget):
    """Write the email (generated from the audit, always editable), then copy it or
    open it in the mail app, and record the outreach."""
    recorded = Signal(dict)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.lead = None
        outer = vbox(self, (0, 16, 0, 0), 12)
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
        self.status_label = label("", "Meta")
        cl.addWidget(_form_row("", self.status_label))

        self.to = QLineEdit()
        self.to.setPlaceholderText("recipient@company.com")
        cl.addWidget(_form_row("To", self.to))
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Subject")
        self.subject.setMaxLength(200)
        cl.addWidget(_form_row("Subject", self.subject))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Generate the email from the lead's latest audit, then edit it.")
        self.body_edit.setMinimumHeight(300)
        cl.addWidget(_form_row("Message", self.body_edit, align_top=True), 1)

        actions = hbox(spacing=8)
        actions.addWidget(label("Send it from your own mailbox, then record it.", "Meta"), 1)
        self.generate_btn = BusyButton("Generate from audit", None, icon_name="zap")
        self.generate_btn.clicked.connect(self.generate)
        actions.addWidget(self.generate_btn)
        self.copy_subject_btn = button("Copy subject", icon_name="copy")
        self.copy_subject_btn.clicked.connect(lambda: self._copy(self.subject.text(), "Subject copied."))
        actions.addWidget(self.copy_subject_btn)
        self.copy_body_btn = button("Copy message", icon_name="copy")
        self.copy_body_btn.clicked.connect(lambda: self._copy(self.body_edit.toPlainText(), "Message copied."))
        actions.addWidget(self.copy_body_btn)
        self.mail_btn = button("Open in mail app", icon_name="external")
        self.mail_btn.clicked.connect(self.open_mail_app)
        actions.addWidget(self.mail_btn)
        self.mark_btn = button("Mark outreached…", "primary", icon_name="check")
        self.mark_btn.clicked.connect(self.mark)
        actions.addWidget(self.mark_btn)
        cl.addLayout(actions)
        outer.addWidget(card, 1)
        for w in (self.subject, self.to):
            w.textChanged.connect(lambda _t: self._update_state())
        self.body_edit.textChanged.connect(self._update_state)
        self._update_state()

    def pick_lead(self):
        dlg = LeadPickerDialog(self)
        if dlg.exec() and dlg.chosen:
            self.load_lead(dlg.chosen["id"])

    def set_lead(self, lead):
        changed = not self.lead or self.lead["id"] != lead["id"]
        self.lead = lead
        self.lead_label.setText(f"<b>{_esc(lead.get('company'))}</b>"
                                f"<span style='color:{C['text_3']};'>&nbsp;&nbsp;{_esc(lead.get('normalized_domain'))}</span>")
        self.open_lead_btn.setVisible(True)
        self.pick_btn.setText("Change…")
        self.status_label.setText(outreach_text(lead))
        if changed:
            self.to.setText(lead.get("email") or "")
            self.subject.clear()
            self.body_edit.clear()
            self.notice.clear()
        self._update_state()

    def load_lead(self, lead_id):
        tasks.run(api.get, f"/api/leads/{lead_id}", on_done=lambda r: self.set_lead(r["lead"]),
                  on_error=lambda e: show_error(self, e, "Lead"), owner=self)

    def has_text(self):
        return bool(self.body_edit.toPlainText().strip())

    def _update_state(self):
        has_lead = self.lead is not None
        has_text = bool(self.body_edit.toPlainText().strip())
        self.generate_btn.setEnabled(has_lead and bool(self.lead.get("last_audit_id")))
        self.generate_btn.setToolTip("" if not has_lead or self.lead.get("last_audit_id")
                                     else "Audit this lead first: the email is written from its findings.")
        self.copy_subject_btn.setEnabled(bool(self.subject.text().strip()))
        self.copy_body_btn.setEnabled(has_text)
        self.mail_btn.setEnabled(has_text)
        self.mark_btn.setEnabled(has_lead)

    def generate(self):
        if not self.lead:
            return
        if self.has_text() and not confirm(
                self, "Replace the current message?", "Generating replaces the subject and message with text built "
                                                      "from the lead's latest audit findings.", "Replace"):
            return
        self.generate_btn.busy(True, "Generating…")
        self.notice.clear()

        def done(d):
            self.generate_btn.busy(False)
            self.subject.setText(d["subject"])
            self.body_edit.setPlainText(d["body"])
            if not self.to.text().strip() and d.get("to_email"):
                self.to.setText(d["to_email"])
            self._update_state()
            self.body_edit.setFocus()

        def fail(err):
            self.generate_btn.busy(False)
            if err.code == "no_audit":
                self.notice.show_message(err.message, "info", "Open lead",
                                         lambda: self.page.ws.navigate("lead", lead_id=self.lead["id"]))
            else:
                show_error(self, err, "Email not written")

        tasks.run(api.post, "/api/outreach/generate", {"lead_id": self.lead["id"]}, on_done=done, on_error=fail, owner=self)

    def _copy(self, text, message):
        QGuiApplication.clipboard().setText(text)
        toast(self, message, "success", 1800)

    def open_mail_app(self):
        """The person's own mail program with the email filled in (they press Send there)."""
        url = (f"mailto:{quote(self.to.text().strip())}?subject={quote(self.subject.text())}"
               f"&body={quote(self.body_edit.toPlainText())}")
        if len(url) > 1900:  # mail programs cut long links: put the message on the clipboard instead
            QGuiApplication.clipboard().setText(self.body_edit.toPlainText())
            url = f"mailto:{quote(self.to.text().strip())}?subject={quote(self.subject.text())}"
            toast(self, "The message is long: it was copied, paste it into the email.", "info", 4000)
        if not QDesktopServices.openUrl(QUrl(url)):
            toast(self, "No mail app is set up on this computer. Use Copy subject and Copy message.", "warning", 4000)

    def mark(self):
        if not self.lead:
            return
        lead = mark_outreach(self, self.lead, "email", self.to.text().strip() or None)
        if lead:
            self.set_lead(lead)
            self.recorded.emit(lead)


# ---------------------------------------------------------------------------
# Outreach log
# ---------------------------------------------------------------------------
class LogPanel(QWidget):
    """Every recorded outreach within the person's leads (Managers: everyone's)."""

    def __init__(self, page, top_margin=16):
        super().__init__()
        self.page = page
        self.params = {"page": 1, "page_size": 25}
        lay = vbox(self, (0, top_margin, 0, 0), 12)
        bar = hbox(spacing=8)
        self.seg = Segmented([("All", ""), ("Email", "email"), ("Call", "call")])
        self.seg.changed.connect(lambda v: self._set(method=v))
        bar.addWidget(self.seg)
        bar.addStretch(1)
        self.search = SearchBox("Search company, website, email or phone")
        self.search.setFixedWidth(320)
        self.search.search.connect(lambda t: self._set(search=t))
        bar.addWidget(self.search)
        refresh = button("", icon_name="refresh", tooltip="Refresh")
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.load)
        bar.addWidget(refresh)
        lay.addLayout(bar)
        cols = [
            Column("company", "Lead", "strong", 220, secondary=lambda r: r.get("normalized_domain")),
            Column("outreached", "Outreached", "text", 110, render=lambda r: "Yes" if r.get("outreached") else "No"),
            Column("method", "How", "text", 90, render=lambda r: METHOD_LABEL.get(r.get("method"), "—")),
            Column("contact", "Email / phone", "text", 220, render=lambda r: r.get("contact") or "—"),
            Column("note", "Note", "text", 200, stretch=True, render=lambda r: (r.get("note") or "").replace("\n", " ")),
        ]
        if not session.is_agent():
            cols.append(Column("user_name", "By", "text", 150, secondary=lambda r: r.get("team_name")))
        cols.append(Column("created_at", "When", "relative", 120))
        self.table = DataTable(cols)
        self.table.page_changed.connect(lambda p: self._set(keep_page=True, page=p))
        self.table.page_size_changed.connect(lambda s: self._set(page_size=s))
        self.table.activated.connect(lambda r: self.page.ws.navigate("lead", lead_id=r["lead_id"]))
        surface = QFrame()
        surface.setObjectName("Surface")
        vbox(surface).addWidget(self.table)
        self.area = StatefulArea(surface)
        self.area.retry.connect(self.load)
        lay.addWidget(self.area, 1)

    def _set(self, keep_page=False, **kv):
        self.params.update(kv)
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def load(self):
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        if not self.table.model.rows:
            self.area.loading("Loading outreach…")
        self.page.fetch("log", api.get, "/api/outreach/log", params=params, on_done=self._render,
                        on_error=self.area.error)

    def _render(self, data):
        self.table.set_page(data)
        if not data.get("items"):
            if self.params.get("method") or self.params.get("search"):
                self.area.empty("Nothing matches", "Change the filter or search.")
            else:
                self.area.empty("No outreach recorded yet", "Mark a lead as outreached after you email or call it.",
                                icon_name="mail")
            return
        self.area.ready()


class OutreachPage(Page):
    nav_key = "outreach"

    def __init__(self, ws):
        super().__init__(ws, spacing=4)
        self.title = "Write email" if session.is_manager() else "Outreach"
        self.header(self.title, "The app writes the email from the audit findings. Send it from your own mailbox, "
                                "then record the outreach (email or call) on the lead.")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.compose = ComposePanel(self)
        self.log = LogPanel(self)
        self.tabs.addTab(self.compose, "Write email")
        self.tabs.addTab(self.log, "Outreach log")
        self.body.addWidget(self.tabs, 1)
        self.compose.recorded.connect(lambda _l: self.log.load())
        self.tabs.currentChanged.connect(lambda _i: self.tabs.currentWidget() is self.log and self.log.load())

    def activate(self, lead_id=None, **_):
        if lead_id:
            self.tabs.setCurrentWidget(self.compose)
            if self.compose.lead is None or self.compose.lead["id"] != lead_id:
                if self.compose.has_text() and not confirm(
                        self, "Write a new email?", "The email you were writing will be cleared.", "Write new email",
                        danger=True):
                    return
                self.compose.load_lead(lead_id)
        elif self.tabs.currentWidget() is self.log:
            self.log.load()


class OutreachActivityPage(Page):
    title = "Outreach log"
    nav_key = "outreach_activity"

    def __init__(self, ws):
        super().__init__(ws, spacing=4)
        h = self.header("Outreach log", "Every lead your teams emailed or called, with the address or number used.")
        write = h.add_action(button("Write email", "primary", icon_name="edit"))
        write.clicked.connect(lambda: self.ws.navigate("outreach"))
        self.log = LogPanel(self, top_margin=12)
        self.body.addWidget(self.log, 1)

    def activate(self, **_):
        self.log.load()
