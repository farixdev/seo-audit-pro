# -*- coding: utf-8 -*-
"""Settings (Manager): SMTP delivery, audit defaults, PageSpeed key, and this
computer (data folder, sharing the workspace on the local network)."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QSpinBox, QWidget

from seo_audit_pro import APP_VERSION, icons, local_server
from seo_audit_pro.core import api, local, settings, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Badge, BusyButton, Card, StatefulArea, Toggle, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import (Field, PasswordInput, combo, confirm, kv_row, line_edit, show_error,
                                            toast)

ENCRYPTION = [("STARTTLS (port 587)", "starttls"), ("SSL/TLS (port 465)", "ssl"), ("None (not recommended)", "none")]
PROVIDERS = [("SMTP mail server (Gmail, domain email)", "smtp"), ("Brevo (API key)", "brevo")]


class SettingsPage(Page):
    nav_key = "settings"
    title = "Settings"

    def __init__(self, ws):
        super().__init__(ws, scroll=True, spacing=20)
        self.header("Settings", "Workspace-wide configuration. Changes are recorded in the audit log.")
        content = QWidget()
        cl = vbox(content, spacing=20)
        self.has_password = False
        cl.addWidget(self._audit_card())
        cl.addWidget(self._pagespeed_card())
        cl.addWidget(self._client_card())
        cl.addStretch(1)
        self.area = StatefulArea(content, skeleton="detail")
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self.has_password = False

    # -- SMTP ----------------------------------------------------------------
    def _smtp_card(self):
        card = Card(padding=24, spacing=16)
        head = hbox(spacing=10)
        col = vbox(spacing=2)
        col.addWidget(label("Email delivery", "SectionTitle"))
        col.addWidget(label("Used for outreach emails and password reset codes. The password is encrypted on the "
                            "server and is never shown again after saving.", "Meta", wrap=True))
        head.addLayout(col, 1)
        self.smtp_state = Badge()
        head.addWidget(self.smtp_state, 0, Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(head)

        self.provider = combo(PROVIDERS, "smtp")
        self.provider_field = Field("Send with", self.provider)
        card.body.addWidget(self.provider_field)
        self.brevo_hint = label("Brevo sends over HTTPS, so it works on any hosting. In Brevo, open SMTP & API > "
                                "API Keys and create a key (it starts with xkeysib-). The From email must be a "
                                "verified sender, or on a domain you authenticated in Brevo (Senders, Domains & "
                                "Dedicated IPs).", "Meta", wrap=True)
        card.body.addWidget(self.brevo_hint)
        self.presets = QWidget()
        presets = vbox(self.presets, spacing=10)
        gmail = hbox(spacing=10)
        gm = button("Use Gmail settings", icon_name="mail", size="sm")
        gm.clicked.connect(self._gmail)
        gmail.addWidget(gm)
        gmail.addWidget(label("Gmail requires 2-Step Verification and an App Password "
                              "(Google Account → Security → App passwords). Your normal Gmail password will not work.",
                              "Meta", wrap=True), 1)
        presets.addLayout(gmail)
        domain = hbox(spacing=10)
        dm = button("Use domain email settings", icon_name="globe", size="sm")
        dm.clicked.connect(self._domain_mail)
        domain.addWidget(dm)
        domain.addWidget(label("For a mailbox on your own domain (for example from cPanel): enter the full email "
                               "address as the username first. Uses mail.yourdomain.com, port 465, SSL; if the test "
                               "says the certificate does not match, use the server name your host shows for email.",
                               "Meta", wrap=True), 1)
        presets.addLayout(domain)
        card.body.addWidget(self.presets)

        self.host = line_edit("smtp.yourdomain.com")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(587)
        self.encryption = combo(ENCRYPTION, "starttls")
        self.username = line_edit("Usually the full email address")
        self.password = PasswordInput("App password")
        self.from_name = line_edit("e.g. Mindcob SEO Team")
        self.from_email = line_edit("outreach@yourdomain.com")
        self.smtp_fields = {
            "host": Field("SMTP host", self.host), "port": Field("Port", self.port),
            "encryption": Field("Encryption", self.encryption), "username": Field("Username", self.username),
            "password": Field("Password / App password", self.password),
            "from_name": Field("From name", self.from_name), "from_email": Field("From email", self.from_email),
        }
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        layout = [("host", 0, 0, 1, 2), ("port", 0, 2, 1, 1), ("encryption", 0, 3, 1, 1), ("username", 1, 0, 1, 2),
                  ("password", 1, 2, 1, 2), ("from_name", 2, 0, 1, 2), ("from_email", 2, 2, 1, 2)]
        for key, r, c, rs, cs in layout:
            grid.addWidget(self.smtp_fields[key], r, c, rs, cs)
        for i in range(4):
            grid.setColumnStretch(i, 1)
        card.body.addLayout(grid)
        self.encryption.currentIndexChanged.connect(self._encryption_changed)
        self.saved_provider = None
        self.provider.currentIndexChanged.connect(lambda _i: self._provider_changed())
        self._provider_changed()

        actions = hbox(spacing=8)
        self.test_result = label("", wrap=True)
        actions.addWidget(self.test_result, 1)
        self.test_btn = BusyButton("Test connection", None, icon_name="zap")
        self.test_btn.clicked.connect(self._test)
        self.save_btn = BusyButton("Save email settings", "primary")
        self.save_btn.clicked.connect(self._save_smtp)
        actions.addWidget(self.test_btn)
        actions.addWidget(self.save_btn)
        card.body.addLayout(actions)
        return card

    def _gmail(self):
        self.host.setText("smtp.gmail.com")
        self.port.setValue(587)
        self.encryption.setCurrentIndex(self.encryption.findData("starttls"))
        if not self.from_email.text().strip() and "@" in self.username.text():
            self.from_email.setText(self.username.text().strip())
        self.username.setFocus()

    def _is_brevo(self):
        return self.provider.currentData() == "brevo"

    def _provider_changed(self):
        brevo = self._is_brevo()
        for key in ("host", "port", "encryption", "username"):
            self.smtp_fields[key].setVisible(not brevo)
        self.presets.setVisible(not brevo)
        self.brevo_hint.setVisible(brevo)
        self.smtp_fields["password"].title.setText("Brevo API key" if brevo else "Password / App password")
        keep = self.has_password and self.saved_provider == self.provider.currentData()
        self.password.edit.setPlaceholderText("Saved — leave blank to keep" if keep else
                                              ("xkeysib-…" if brevo else "App password"))

    def _domain_mail(self):
        address = (self.username.text().strip() or self.from_email.text().strip())
        if "@" in address:
            self.host.setText("mail." + address.split("@", 1)[1].lower())
            if not self.from_email.text().strip():
                self.from_email.setText(address)
            if not self.username.text().strip():
                self.username.setText(address)
        self.port.setValue(465)
        self.encryption.setCurrentIndex(self.encryption.findData("ssl"))
        (self.username if "@" not in address else self.password).setFocus()

    def _encryption_changed(self, _i):
        suggested = {"starttls": 587, "ssl": 465}.get(self.encryption.currentData())
        if suggested and self.port.value() in (25, 465, 587):
            self.port.setValue(suggested)

    def _smtp_payload(self):
        data = {"provider": self.provider.currentData(), "host": self.host.text().strip(), "port": self.port.value(),
                "encryption": self.encryption.currentData(), "username": self.username.text().strip(),
                "from_name": self.from_name.text().strip(), "from_email": self.from_email.text().strip()}
        if self.password.text():
            data["password"] = self.password.text()
        return data

    def _validate_smtp(self):
        for f in self.smtp_fields.values():
            f.set_error("")
        data = self._smtp_payload()
        brevo = self._is_brevo()
        keep = self.has_password and self.saved_provider == data["provider"]
        errs = {"host": "" if (brevo or data["host"]) else "Enter the SMTP host.",
                "from_name": "" if data["from_name"] else "Enter the sender name.",
                "from_email": "" if "@" in data["from_email"] else "Enter the sender email address.",
                "password": "" if (keep or data.get("password")) else
                ("Enter the Brevo API key." if brevo else "Enter the SMTP password.")}
        for k, v in errs.items():
            self.smtp_fields[k].set_error(v)
        return None if any(errs.values()) else data

    def _smtp_error(self, err):
        field = err.data.get("field")
        if field in self.smtp_fields:
            self.smtp_fields[field].set_error(err.message)
        else:
            show_error(self, err)

    def _set_test_result(self, ok, message):
        color = C["success"] if ok else C["danger"]
        self.test_result.setText(message)
        self.test_result.setStyleSheet(f"color: {color};")

    def _test(self):
        data = self._validate_smtp()
        if data is None:
            return
        self.test_btn.busy(True, "Testing…")
        self.test_result.setText("")

        def done(res):
            self.test_btn.busy(False)
            self._set_test_result(bool(res.get("ok")), res.get("message") or "Connected.")

        def fail(err):
            self.test_btn.busy(False)
            # A failed handshake is reported as {"ok": false, "message": ...}.
            if "message" in err.data and "ok" in err.data:
                self._set_test_result(False, err.data["message"])
            else:
                self._smtp_error(err)

        tasks.run(api.post, "/api/outreach/smtp/test", data, timeout=40, on_done=done, on_error=fail, owner=self)

    def _save_smtp(self):
        data = self._validate_smtp()
        if data is None:
            return
        self.save_btn.busy(True, "Saving…")

        def done(res):
            self.save_btn.busy(False)
            self._fill_smtp(res)
            toast(self, "Email settings saved.", "success")

        def fail(err):
            self.save_btn.busy(False)
            self._smtp_error(err)

        tasks.run(api.put, "/api/outreach/smtp", data, on_done=done, on_error=fail, owner=self)

    def _fill_smtp(self, s):
        configured = bool(s.get("configured"))
        self.has_password = bool(s.get("has_password"))
        if configured:
            self.smtp_state.setText("Configured" + (" · Brevo" if s.get("provider") == "brevo" else ""))
            self.smtp_state.set_colors(C["success"], C["success_bg"])
            self.saved_provider = s.get("provider") or "smtp"
            self.provider.setCurrentIndex(max(0, self.provider.findData(self.saved_provider)))
            self.host.setText(s.get("host") if s.get("provider") != "brevo" else "")
            self.port.setValue(int(s.get("port") or 587))
            self.encryption.setCurrentIndex(max(0, self.encryption.findData(s.get("encryption"))))
            self.username.setText(s.get("username") or "")
            self.from_name.setText(s.get("from_name") or "")
            self.from_email.setText(s.get("from_email") or "")
        else:
            self.smtp_state.setText("Not configured")
            self.smtp_state.set_colors(C["warning"], C["warning_bg"])
        self.password.clear()
        self._provider_changed()

    # -- Audit defaults --------------------------------------------------------
    def _audit_card(self):
        card = Card(padding=24, spacing=16)
        card.body.addWidget(label("Audit defaults", "SectionTitle"))
        card.body.addWidget(label("Applied to every new audit. Larger crawls take longer but cover more of the site.",
                                  "Meta", wrap=True))
        row = hbox(spacing=16)
        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 500)
        self.max_pages.setValue(50)
        self.psi = combo([("Mobile and desktop", "both"), ("Mobile only", "mobile")], "both")
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 8)
        self.concurrent.setValue(2)
        row.addWidget(Field("Pages to crawl", self.max_pages, "Between 1 and 500 pages per audit."), 1)
        row.addWidget(Field("PageSpeed Insights", self.psi, "Mobile only halves PageSpeed time."), 1)
        row.addWidget(Field("Audits at the same time", self.concurrent, "On each computer. 2–3 for 4 GB of memory; 4–8 for 8–16 GB."), 1)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(row)
        pdf = hbox(spacing=8)
        self.pdf_icon = label()
        self.pdf_text = label("", "Muted", wrap=True)
        pdf.addWidget(self.pdf_icon, 0, Qt.AlignmentFlag.AlignTop)
        pdf.addWidget(self.pdf_text, 1)
        card.body.addLayout(pdf)
        actions = hbox()
        actions.addStretch(1)
        self.audit_save = BusyButton("Save audit defaults", "primary")
        self.audit_save.clicked.connect(self._save_audit)
        actions.addWidget(self.audit_save)
        card.body.addLayout(actions)
        return card

    def _fill_audit(self, data):
        d = data.get("audit_defaults") or {}
        self.max_pages.setValue(int(d.get("max_pages") or 50))
        self.psi.setCurrentIndex(max(0, self.psi.findData(d.get("psi_strategy") or "both")))
        self.concurrent.setValue(int(d.get("concurrent") or 2))
        if data.get("made_in_app"):
            self.pdf_icon.setPixmap(icons.pixmap("info", C["text_3"], 16))
            self.pdf_text.setText("Audits and report files are made on each person's own computer; the server only "
                                  "stores and shares them. PDF reports need Microsoft Word or LibreOffice on that "
                                  "computer, otherwise reports are delivered as DOCX.")
        elif data.get("pdf_supported"):
            self.pdf_icon.setPixmap(icons.pixmap("check_circle", C["success"], 16))
            self.pdf_text.setText("PDF reports are available: the server has Microsoft Word or LibreOffice installed. "
                                  "Each PDF is created the first time someone opens or emails it.")
        else:
            self.pdf_icon.setPixmap(icons.pixmap("info", C["text_3"], 16))
            self.pdf_text.setText("PDF reports are not available on this server, so reports are delivered as DOCX. "
                                  "Install Microsoft Word or LibreOffice on the server to enable PDF.")

    def _save_audit(self):
        self.audit_save.busy(True, "Saving…")
        payload = {"audit_defaults": {"max_pages": self.max_pages.value(), "psi_strategy": self.psi.currentData(),
                                      "concurrent": self.concurrent.value()}}

        def done(res):
            self.audit_save.busy(False)
            self._fill_audit(res)
            toast(self, "Audit defaults saved.", "success")

        def fail(err):
            self.audit_save.busy(False)
            show_error(self, err)

        tasks.run(api.put, "/api/settings", payload, on_done=done, on_error=fail, owner=self)

    # -- PageSpeed ---------------------------------------------------------------
    def _pagespeed_card(self):
        card = Card(padding=24, spacing=14)
        head = hbox(spacing=10)
        head.addWidget(label("Google PageSpeed Insights", "SectionTitle"), 1)
        self.psi_badge = Badge("Not set", C["warning"], C["warning_bg"])
        head.addWidget(self.psi_badge)
        card.body.addLayout(head)
        card.body.addWidget(label(
            "Speed scores come from Google. Without your own free API key Google often says it is busy, and the "
            "speed part of the audit is skipped. To get a key: open console.cloud.google.com, create a project, "
            "turn on \"PageSpeed Insights API\", then go to Credentials and create an API key.", "Meta", wrap=True))
        row = hbox(spacing=10)
        self.psi_key = PasswordInput("Paste the API key")
        row.addWidget(self.psi_key, 1)
        self.psi_save = BusyButton("Save key", "primary")
        self.psi_save.clicked.connect(lambda: self._save_psi(self.psi_key.text().strip()))
        row.addWidget(self.psi_save)
        self.psi_clear = button("Remove", "ghost")
        self.psi_clear.clicked.connect(lambda: self._save_psi(""))
        row.addWidget(self.psi_clear)
        card.body.addLayout(row)
        return card

    def _fill_psi(self, data):
        on = bool(data.get("pagespeed_key_set"))
        self.psi_badge.setText("Saved" if on else "Not set")
        self.psi_badge.set_colors(*((C["success"], C["success_bg"]) if on else (C["warning"], C["warning_bg"])))
        self.psi_clear.setVisible(on)
        self.psi_key.edit.setPlaceholderText("Saved — paste a new key to replace it" if on else "Paste the API key")

    def _save_psi(self, key):
        if not key and not self.psi_clear.isVisible():
            toast(self, "Paste the API key first.", "warning")
            return
        self.psi_save.busy(True, "Saving…")

        def done(res):
            self.psi_save.busy(False)
            self.psi_key.clear()
            self._fill_psi(res)
            toast(self, "PageSpeed key saved. New audits will use it." if key else "PageSpeed key removed.", "success")

        def fail(err):
            self.psi_save.busy(False)
            show_error(self, err)

        tasks.run(api.put, "/api/settings", {"pagespeed_api_key": key}, on_done=done, on_error=fail, owner=self)

    # -- This computer --------------------------------------------------------------
    def _client_card(self):
        card = Card(padding=24, spacing=12)
        card.body.addWidget(label("This computer", "SectionTitle"))
        is_local = settings.mode() == settings.LOCAL
        self.server_row = kv_row("Workspace", "Runs on this computer" if is_local else settings.api_url())
        card.body.addWidget(self.server_row)
        card.body.addWidget(kv_row("App version", APP_VERSION))
        if not is_local:
            card.body.addWidget(label("To use a different server, sign out and choose Change on the sign-in screen.",
                                      "Meta", wrap=True))
            return card
        folder = local_server.data_dir()
        row = hbox(spacing=10)
        row.addWidget(label(folder, wrap=True, selectable=True), 1)
        open_btn = button("Open folder", "ghost", icon_name="folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        row.addWidget(open_btn)
        holder = QWidget()
        holder.setLayout(row)
        card.body.addWidget(kv_row("Data folder", holder))
        card.body.addWidget(label("Everything is saved here: the database, reports and logs. To back it up, close the "
                                  "app and copy this folder.", "Meta", wrap=True))
        share = hbox(spacing=10)
        self.share_toggle = Toggle(local.sharing())
        self.share_toggle.toggled.connect(self._toggle_share)
        share.addWidget(self.share_toggle)
        share.addWidget(label("Let other computers on this network use this workspace"), 1)
        card.body.addLayout(share)
        self.share_info = label("", "Meta", wrap=True, selectable=True)
        card.body.addWidget(self.share_info)
        self._show_share()
        return card

    def _show_share(self):
        if not hasattr(self, "share_info"):
            return
        if local.sharing():
            self.share_info.setText(
                f"Others on the same network open SEO Audit Pro, choose \"Connect to a team server\" and enter "
                f"{local.share_address()} — this computer must stay on with the app open. If Windows asks, "
                "allow access on private networks.")
        else:
            self.share_info.setText("Off: only this computer can use the workspace.")

    def _toggle_share(self, on):
        if not confirm(self, "Turn on network sharing?" if on else "Turn off network sharing?",
                       "The audit server restarts (a few seconds). Audits that are running start again."
                       + (" People you add can then sign in from other computers on this network." if on else ""),
                       "Restart server"):
            self.share_toggle.setChecked(not on)
            return
        settings.put("share_network", "true" if on else "false")
        self.share_info.setText("Restarting the audit server…")

        def ready(_status):
            self._show_share()
            toast(self, "Network sharing is on." if on else "Network sharing is off.", "success")

        def fail(err):
            settings.put("share_network", "false")
            self.share_toggle.setChecked(False)
            self._show_share()
            show_error(self, err)

        local.start(ready, fail, owner=self, share=on)

    # -- lifecycle -------------------------------------------------------------
    def activate(self, **_):
        self.load()

    def load(self):
        if not getattr(self, "_loaded", False):
            self.area.loading("Loading settings…")
        state = {"smtp": {}, "settings": None}  # the app does not send email: no email settings

        def maybe_ready():
            if state["settings"] is not None:
                self._loaded = True
                self._fill_audit(state["settings"])
                self._fill_psi(state["settings"])
                self.area.ready()

        def got_smtp(res):
            state["smtp"] = res
            maybe_ready()

        def got_settings(res):
            state["settings"] = res
            maybe_ready()

        self.fetch("settings", api.get, "/api/settings", on_done=got_settings, on_error=self.area.error)
