# -*- coding: utf-8 -*-
"""
Sign-in experience: sign in, forgot password (emailed code), reset password,
mandatory first-login password change, and server address configuration.
"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QStackedWidget, QWidget

from seo_audit_pro import icons
from seo_audit_pro.core import api, settings, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C
from seo_audit_pro.widgets.common import BusyButton, button, hbox, label, logo_label, vbox
from seo_audit_pro.widgets.feedback import Field, PasswordInput, line_edit

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _auth_input(placeholder=""):
    w = line_edit(placeholder)
    w.setObjectName("AuthInput")
    return w


class PolicyChecklist(QWidget):
    """Live password-policy feedback mirroring the server's rules."""
    RULES = [("len", "At least 10 characters"), ("case", "Upper- and lower-case letters"), ("digit", "At least one number")]

    def __init__(self):
        super().__init__()
        lay = vbox(self, spacing=4)
        self.rows = {}
        for key, text in self.RULES:
            row = hbox(spacing=6)
            ic = QLabel()
            lbl = label(text, "Meta")
            row.addWidget(ic)
            row.addWidget(lbl, 1)
            lay.addLayout(row)
            self.rows[key] = (ic, lbl)
        self.update_for("")

    def update_for(self, pw):
        state = {"len": len(pw) >= 10, "case": bool(re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw)),
                 "digit": bool(re.search(r"\d", pw))}
        for key, (ic, lbl) in self.rows.items():
            ok = state[key]
            ic.setPixmap(icons.pixmap("check_circle" if ok else "info", C["success"] if ok else C["muted"], 14))
            lbl.setStyleSheet(f"color: {C['success'] if ok else C['text_3']};")
        return all(state.values())


class _Panel(QWidget):
    """Centered column with brand header."""

    def __init__(self, title, subtitle):
        super().__init__()
        outer = vbox(self)
        outer.addStretch(1)
        row = hbox()
        row.addStretch(1)
        self.col = QWidget()
        self.col.setFixedWidth(380)
        self.lay = vbox(self.col, spacing=0)
        head = vbox(spacing=0)
        head.addWidget(logo_label(44), 0, Qt.AlignmentFlag.AlignHCenter)
        head.addSpacing(18)
        name = label("SEO AUDIT PRO")
        name.setStyleSheet("font-size: 13px; font-weight: 700; letter-spacing: 2px; color: #52525B;")
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        head.addWidget(name)
        head.addSpacing(10)
        t = label(title)
        t.setStyleSheet("font-size: 22px; font-weight: 600;")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        head.addWidget(t)
        if subtitle:
            head.addSpacing(6)
            s = label(subtitle, "Muted", wrap=True)
            s.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            head.addWidget(s)
        self.lay.addLayout(head)
        self.lay.addSpacing(28)
        self.banner = QFrame()
        self.banner.setVisible(False)
        bl = hbox(self.banner, (12, 10, 12, 10), 8)
        self.banner_icon = QLabel()
        self.banner_text = label("", wrap=True)
        bl.addWidget(self.banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        bl.addWidget(self.banner_text, 1)
        self.lay.addWidget(self.banner)
        self.lay.addSpacing(14)
        self.form = vbox(spacing=14)
        self.lay.addLayout(self.form)
        row.addWidget(self.col)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def notice(self, text, kind="error"):
        if not text:
            self.banner.setVisible(False)
            return
        fg, bg, border, ic = {
            "error": (C["danger"], C["danger_bg"], "#FECDCA", "alert_circle"),
            "info": (C["info"], C["info_bg"], "#B2DDFF", "info"),
            "success": (C["success"], C["success_bg"], "#ABEFC6", "check_circle"),
        }[kind]
        self.banner.setStyleSheet(f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
                                  f"QLabel {{ color: {fg}; border: none; background: transparent; }}")
        self.banner_icon.setPixmap(icons.pixmap(ic, fg, 16))
        self.banner_text.setText(text)
        self.banner.setVisible(True)


class AuthView(QWidget):
    signed_in = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Canvas")
        lay = vbox(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        footer = hbox(spacing=6)
        footer.setContentsMargins(0, 0, 0, 20)
        footer.addStretch(1)
        lock = QLabel()
        lock.setPixmap(icons.pixmap("lock", C["text_3"], 13))
        footer.addWidget(lock)
        footer.addWidget(label("Secure workspace access", "Meta"))
        footer.addWidget(label("·", "Meta"))
        self.server_label = label("", "Meta")
        footer.addWidget(self.server_label)
        change = button("Change server", "link")
        change.clicked.connect(lambda: self.show_page("server"))
        footer.addWidget(change)
        footer.addStretch(1)
        lay.addLayout(footer)
        self.pages = {}
        self._build_signin()
        self._build_forgot()
        self._build_reset()
        self._build_change()
        self._build_server()
        self.refresh_server_label()

    def refresh_server_label(self):
        self.server_label.setText(settings.api_url().split("://", 1)[-1])

    def show_page(self, key, notice=None, kind="error"):
        page = self.pages[key]
        page.notice(notice, kind) if notice else page.notice(None)
        self.stack.setCurrentWidget(page)
        focus = {"signin": lambda: (self.email if not self.email.text() else self.password).setFocus(),
                 "forgot": lambda: self.f_email.setFocus(), "reset": lambda: self.r_code.setFocus(),
                 "change": lambda: self.c_current.setFocus(), "server": lambda: self.s_url.setFocus()}
        focus[key]()

    # -- Sign in -------------------------------------------------------------
    def _build_signin(self):
        p = _Panel("Sign in to your workspace", "")
        self.email = _auth_input("name@company.com")
        self.email.setText(settings.last_email())
        self.password = PasswordInput("Password", "AuthInput")
        self.email_field = Field("Email", self.email)
        self.pw_field = Field("Password", self.password)
        p.form.addWidget(self.email_field)
        p.form.addWidget(self.pw_field)
        row = hbox()
        self.remember = QCheckBox("Keep me signed in")
        self.remember.setChecked(settings.get("remember", "true") in (True, "true"))
        row.addWidget(self.remember)
        row.addStretch(1)
        forgot = button("Forgot password?", "link")
        forgot.clicked.connect(lambda: (self.f_email.setText(self.email.text()), self.show_page("forgot")))
        row.addWidget(forgot)
        p.form.addLayout(row)
        p.form.addSpacing(6)
        self.signin_btn = BusyButton("Sign in", "primary", size="lg")
        self.signin_btn.clicked.connect(self._sign_in)
        self.email.returnPressed.connect(lambda: self.password.setFocus())
        self.password.returnPressed.connect(self._sign_in)
        p.form.addWidget(self.signin_btn)
        self.pages["signin"] = p
        self.stack.addWidget(p)

    def _sign_in(self):
        email = self.email.text().strip().lower()
        pw = self.password.text()
        self.email_field.set_error("" if EMAIL_RE.match(email) else "Enter a valid email address.")
        self.pw_field.set_error("" if pw else "Enter your password.")
        if not EMAIL_RE.match(email) or not pw:
            return
        self.signin_btn.busy(True, "Signing in…")
        self.pages["signin"].notice(None)

        def done(payload):
            self.signin_btn.busy(False)
            settings.set_last_email(email)
            settings.put("remember", "true" if self.remember.isChecked() else "false")
            session.start(payload, remember=self.remember.isChecked())
            self.password.clear()
            if payload["user"].get("must_change_password"):
                self.c_current.edit.setText(pw)
                self.show_page("change", "For security, choose a new password before continuing.", "info")
            else:
                self.signed_in.emit()

        def fail(err):
            self.signin_btn.busy(False)
            self.password.clear()
            self.pages["signin"].notice(err.message)
            self.password.setFocus()

        tasks.run(api.post, "/api/auth/login", {"email": email, "password": pw}, auth=False,
                  on_done=done, on_error=fail, owner=self)

    # -- Forgot / reset --------------------------------------------------------
    def _build_forgot(self):
        p = _Panel("Reset your password", "Enter your work email. If an account exists, we'll email a reset code.")
        self.f_email = _auth_input("name@company.com")
        self.f_field = Field("Email", self.f_email)
        p.form.addWidget(self.f_field)
        self.f_btn = BusyButton("Send reset code", "primary", size="lg")
        self.f_btn.clicked.connect(self._send_code)
        self.f_email.returnPressed.connect(self._send_code)
        p.form.addWidget(self.f_btn)
        have = button("I already have a code", "link")
        have.clicked.connect(lambda: (self.r_email.setText(self.f_email.text()), self.show_page("reset")))
        back = button("Back to sign in", "link")
        back.clicked.connect(lambda: self.show_page("signin"))
        links = hbox()
        links.addWidget(back)
        links.addStretch(1)
        links.addWidget(have)
        p.form.addLayout(links)
        self.pages["forgot"] = p
        self.stack.addWidget(p)

    def _send_code(self):
        email = self.f_email.text().strip().lower()
        if not EMAIL_RE.match(email):
            self.f_field.set_error("Enter a valid email address.")
            return
        self.f_field.set_error("")
        self.f_btn.busy(True, "Sending…")

        def done(res):
            self.f_btn.busy(False)
            self.r_email.setText(email)
            self.show_page("reset", res.get("message", "") + " If no email arrives, ask your manager to reset your "
                                                              "password.", "info")

        def fail(err):
            self.f_btn.busy(False)
            self.pages["forgot"].notice(err.message)

        tasks.run(api.post, "/api/auth/forgot-password", {"email": email}, auth=False, on_done=done, on_error=fail,
                  owner=self)

    def _build_reset(self):
        p = _Panel("Enter your reset code", "The code expires 30 minutes after it was sent.")
        self.r_email = _auth_input("name@company.com")
        self.r_code = _auth_input("8-digit code")
        self.r_new = PasswordInput("New password", "AuthInput")
        self.r_confirm = PasswordInput("Repeat new password", "AuthInput")
        self.r_policy = PolicyChecklist()
        self.r_new.edit.textChanged.connect(self.r_policy.update_for)
        self.r_fields = {"email": Field("Email", self.r_email), "code": Field("Reset code", self.r_code),
                         "new_password": Field("New password", self.r_new), "confirm": Field("Confirm password", self.r_confirm)}
        for f in self.r_fields.values():
            p.form.addWidget(f)
        p.form.addWidget(self.r_policy)
        self.r_btn = BusyButton("Set new password", "primary", size="lg")
        self.r_btn.clicked.connect(self._reset)
        self.r_confirm.returnPressed.connect(self._reset)
        p.form.addWidget(self.r_btn)
        back = button("Back to sign in", "link")
        back.clicked.connect(lambda: self.show_page("signin"))
        p.form.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages["reset"] = p
        self.stack.addWidget(p)

    def _reset(self):
        email = self.r_email.text().strip().lower()
        code = self.r_code.text().strip()
        new, confirm = self.r_new.text(), self.r_confirm.text()
        errs = {"email": "" if EMAIL_RE.match(email) else "Enter a valid email address.",
                "code": "" if code else "Enter the code from the email.",
                "new_password": "" if self.r_policy.update_for(new) else "The password does not meet the requirements.",
                "confirm": "" if new == confirm else "The passwords do not match."}
        for k, v in errs.items():
            self.r_fields[k].set_error(v)
        if any(errs.values()):
            return
        self.r_btn.busy(True, "Saving…")

        def done(_r):
            self.r_btn.busy(False)
            for w in (self.r_code, self.r_new, self.r_confirm):
                w.clear()
            self.email.setText(email)
            self.show_page("signin", "Your password was reset. Sign in with your new password.", "success")

        def fail(err):
            self.r_btn.busy(False)
            field = err.data.get("field")
            if field in self.r_fields:
                self.r_fields[field].set_error(err.message)
            else:
                self.pages["reset"].notice(err.message)

        tasks.run(api.post, "/api/auth/reset-password", {"email": email, "code": code, "new_password": new}, auth=False,
                  on_done=done, on_error=fail, owner=self)

    # -- Mandatory change (first login / temporary password) -------------------
    def _build_change(self):
        p = _Panel("Choose a new password", "Your account uses a temporary password. Set your own to continue.")
        self.c_current = PasswordInput("Temporary password", "AuthInput")
        self.c_new = PasswordInput("New password", "AuthInput")
        self.c_confirm = PasswordInput("Repeat new password", "AuthInput")
        self.c_policy = PolicyChecklist()
        self.c_new.edit.textChanged.connect(self.c_policy.update_for)
        self.c_fields = {"current_password": Field("Current password", self.c_current),
                         "new_password": Field("New password", self.c_new),
                         "confirm": Field("Confirm password", self.c_confirm)}
        for f in self.c_fields.values():
            p.form.addWidget(f)
        p.form.addWidget(self.c_policy)
        self.c_btn = BusyButton("Update password and continue", "primary", size="lg")
        self.c_btn.clicked.connect(self._change)
        self.c_confirm.returnPressed.connect(self._change)
        p.form.addWidget(self.c_btn)
        out = button("Sign out", "link")
        out.clicked.connect(self._abandon_change)
        p.form.addWidget(out, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages["change"] = p
        self.stack.addWidget(p)

    def require_change(self):
        self.show_page("change", "For security, choose a new password before continuing.", "info")

    def _abandon_change(self):
        tasks.run(api.post, "/api/auth/logout", owner=self)
        session.end()
        self.show_page("signin")

    def _change(self):
        cur, new, confirm = self.c_current.text(), self.c_new.text(), self.c_confirm.text()
        errs = {"current_password": "" if cur else "Enter your current password.",
                "new_password": "" if self.c_policy.update_for(new) else "The password does not meet the requirements.",
                "confirm": "" if new == confirm else "The passwords do not match."}
        for k, v in errs.items():
            self.c_fields[k].set_error(v)
        if any(errs.values()):
            return
        self.c_btn.busy(True, "Updating…")

        def done(res):
            self.c_btn.busy(False)
            for w in (self.c_current, self.c_new, self.c_confirm):
                w.clear()
            session.set_user(res["user"])
            self.signed_in.emit()

        def fail(err):
            self.c_btn.busy(False)
            field = err.data.get("field")
            if field in self.c_fields:
                self.c_fields[field].set_error(err.message)
            else:
                self.pages["change"].notice(err.message)

        tasks.run(api.post, "/api/auth/change-password", {"current_password": cur, "new_password": new},
                  on_done=done, on_error=fail, owner=self)

    # -- Server address ----------------------------------------------------------
    def _build_server(self):
        p = _Panel("Server address", "The address of your agency's SEO Audit Pro server. Use https:// in production.")
        self.s_url = _auth_input("https://seo.youragency.com")
        self.s_field = Field("Server URL", self.s_url)
        p.form.addWidget(self.s_field)
        self.s_btn = BusyButton("Test and save", "primary", size="lg")
        self.s_btn.clicked.connect(self._save_server)
        self.s_url.returnPressed.connect(self._save_server)
        p.form.addWidget(self.s_btn)
        back = button("Back to sign in", "link")
        back.clicked.connect(lambda: self.show_page("signin"))
        p.form.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages["server"] = p
        self.stack.addWidget(p)
        self.s_url.setText(settings.api_url())

    def _save_server(self):
        url = self.s_url.text().strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(:\d+)?(/.*)?$", url):
            self.s_field.set_error("Enter a full address starting with https:// (or http:// for a local server).")
            return
        self.s_field.set_error("")
        previous = settings.api_url()
        settings.set_api_url(url)
        self.s_btn.busy(True, "Testing…")

        def done(_r):
            self.s_btn.busy(False)
            self.refresh_server_label()
            warn = " Note: this connection is not encrypted." if url.startswith("http://") and \
                not re.match(r"^http://(127\.0\.0\.1|localhost)(:|/|$)", url) else ""
            self.show_page("signin", "Connected to the server." + warn, "success" if not warn else "info")

        def fail(err):
            self.s_btn.busy(False)
            settings.set_api_url(previous)
            self.pages["server"].notice(err.message)

        tasks.run(api.health, on_done=done, on_error=fail, owner=self)

    def set_offline_notice(self, text):
        self.show_page("signin", text, "error")
