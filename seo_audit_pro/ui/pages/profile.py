# -*- coding: utf-8 -*-
"""Profile: name, picture, account details, password change, sign out."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QWidget

from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import ROLE_LABEL
from seo_audit_pro.ui.auth import PolicyChecklist
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Avatar, BusyButton, Card, StatefulArea, button, hbox, label, scaled_pixmap, vbox
from seo_audit_pro.widgets.feedback import Field, PasswordInput, kv_row, line_edit, show_error, toast

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_MAX_AVATAR = 2 * 1024 * 1024


def _upload_avatar(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        return api.request("POST", "/api/auth/me/avatar", files={"file": (os.path.basename(path), fh, _IMAGE_TYPES[ext])},
                           timeout=60)


class ProfilePage(Page):
    nav_key = "profile"
    title = "Profile"

    def __init__(self, ws):
        super().__init__(ws, scroll=True, spacing=20)
        self.header("Profile", "Your account details and sign-in security.")
        content = QWidget()
        cl = vbox(content, spacing=20)
        content.setMaximumWidth(760)

        # Identity --------------------------------------------------------------
        ident = Card(padding=24, spacing=16)
        top = hbox(spacing=20)
        self.avatar = Avatar("", 72)
        top.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(spacing=6)
        self.name_title = label("")
        self.name_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        col.addWidget(self.name_title)
        self.role_line = label("", "Muted")
        col.addWidget(self.role_line)
        pic = hbox(spacing=8)
        self.pic_btn = BusyButton("Change picture", None, icon_name="camera", size="sm")
        self.pic_btn.clicked.connect(self._choose_picture)
        pic.addWidget(self.pic_btn)
        pic.addWidget(label("PNG or JPG, up to 2 MB", "Meta"))
        pic.addStretch(1)
        col.addLayout(pic)
        top.addLayout(col, 1)
        ident.body.addLayout(top)
        name_row = hbox(spacing=8)
        self.name_input = line_edit("Full name")
        self.name_field = Field("Display name", self.name_input)
        name_row.addWidget(self.name_field, 1)
        self.name_save = BusyButton("Save name", None)
        self.name_save.clicked.connect(self._save_name)
        self.name_input.returnPressed.connect(self._save_name)
        name_row.addWidget(self.name_save, 0, Qt.AlignmentFlag.AlignBottom)
        ident.body.addLayout(name_row)
        self.details = vbox(spacing=8)
        ident.body.addLayout(self.details)
        cl.addWidget(ident)

        # Password -------------------------------------------------------------
        pw = Card(padding=24, spacing=12)
        pw.body.addWidget(label("Change password", "SectionTitle"))
        pw.body.addWidget(label("Changing your password signs you out on every other device.", "Meta"))
        self.cur = PasswordInput("Current password")
        self.new = PasswordInput("New password")
        self.confirm = PasswordInput("Repeat new password")
        self.policy = PolicyChecklist()
        self.new.edit.textChanged.connect(self.policy.update_for)
        self.pw_fields = {"current_password": Field("Current password", self.cur),
                          "new_password": Field("New password", self.new), "confirm": Field("Confirm new password", self.confirm)}
        for f in self.pw_fields.values():
            pw.body.addWidget(f)
        pw.body.addWidget(self.policy)
        row = hbox()
        row.addStretch(1)
        self.pw_btn = BusyButton("Update password", "primary")
        self.pw_btn.clicked.connect(self._change_password)
        self.confirm.returnPressed.connect(self._change_password)
        row.addWidget(self.pw_btn)
        pw.body.addLayout(row)
        cl.addWidget(pw)

        # Session --------------------------------------------------------------
        out = Card(padding=24, spacing=8)
        orow = hbox(spacing=12)
        ocol = vbox(spacing=2)
        ocol.addWidget(label("Sign out", "SectionTitle"))
        ocol.addWidget(label("Ends this session and removes the saved sign-in from this computer.", "Meta", wrap=True))
        orow.addLayout(ocol, 1)
        sign_out = button("Sign out", "danger", icon_name="logout")
        sign_out.clicked.connect(self.ws.logout_requested.emit)
        orow.addWidget(sign_out, 0, Qt.AlignmentFlag.AlignVCenter)
        out.body.addLayout(orow)
        cl.addWidget(out)
        cl.addStretch(1)

        wrapper = QWidget()
        wl = hbox(wrapper)
        wl.addWidget(content, 1)
        wl.addStretch(0)
        self.area = StatefulArea(wrapper, skeleton="detail")
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self._avatar_loaded_for = None

    def activate(self, **_):
        self.load()

    def load(self):
        if not getattr(self, "_loaded", False):
            self.area.loading("Loading profile…")
        self.fetch("me", api.get, "/api/auth/me", on_done=lambda r: self._render(r["user"]), on_error=self.area.error)

    def _render(self, user):
        self._loaded = True
        self.name_title.setText(user["name"])
        self.avatar.set_name(user["name"])
        role = ROLE_LABEL.get(user["role"], user["role"])
        self.role_line.setText(f"{role} · {user['team_name']}" if user.get("team_name") else role)
        if not self.name_input.hasFocus():
            self.name_input.setText(user["name"])
        while self.details.count():
            it = self.details.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for k, v in (("Email", user["email"]), ("Role", role), ("Team", user.get("team_name") or "—"),
                     ("Last sign-in", fmt.datetime_(user.get("last_login_at"), "—")),
                     ("Member since", fmt.date(user.get("created_at")))):
            self.details.addWidget(kv_row(k, v))
        if user.get("has_avatar"):
            self._load_avatar(user["id"])
        else:
            self.avatar.set_pixmap(None)
        self.area.ready()

    def _load_avatar(self, user_id):
        def done(data):
            if isinstance(data, (bytes, bytearray)):
                self.avatar.set_pixmap(scaled_pixmap(bytes(data), 72))
        self.fetch("avatar", api.get, f"/api/users/{user_id}/avatar", on_done=done, on_error=lambda _e: None)

    def _choose_picture(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", os.path.expanduser("~"), "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        if os.path.splitext(path)[1].lower() not in _IMAGE_TYPES:
            toast(self, "Choose a PNG or JPG image.", "error")
            return
        if os.path.getsize(path) > _MAX_AVATAR:
            toast(self, "The picture is larger than 2 MB.", "error")
            return
        self.pic_btn.busy(True, "Uploading…")

        def done(res):
            self.pic_btn.busy(False)
            session.set_user(dict(session.user or {}, **res["user"]))
            self._render(res["user"])
            toast(self, "Profile picture updated.", "success")

        def fail(err):
            self.pic_btn.busy(False)
            show_error(self, err)

        tasks.run(_upload_avatar, path, on_done=done, on_error=fail, owner=self)

    def _save_name(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_field.set_error("Enter your name.")
            return
        self.name_field.set_error("")
        self.name_save.busy(True, "Saving…")

        def done(res):
            self.name_save.busy(False)
            session.set_user(dict(session.user or {}, **res["user"]))
            self._render(res["user"])
            toast(self, "Name updated.", "success")

        def fail(err):
            self.name_save.busy(False)
            if err.data.get("field") == "name":
                self.name_field.set_error(err.message)
            else:
                show_error(self, err)

        tasks.run(api.patch, "/api/auth/me", {"name": name}, on_done=done, on_error=fail, owner=self)

    def _change_password(self):
        cur, new, confirm = self.cur.text(), self.new.text(), self.confirm.text()
        errs = {"current_password": "" if cur else "Enter your current password.",
                "new_password": "" if self.policy.update_for(new) else "The password does not meet the requirements.",
                "confirm": "" if new == confirm else "The passwords do not match."}
        for k, v in errs.items():
            self.pw_fields[k].set_error(v)
        if any(errs.values()):
            return
        self.pw_btn.busy(True, "Updating…")

        def done(res):
            self.pw_btn.busy(False)
            for w in (self.cur, self.new, self.confirm):
                w.clear()
            self.policy.update_for("")
            session.set_user(dict(session.user or {}, **res["user"]))
            toast(self, "Password updated. Other devices were signed out.", "success")

        def fail(err):
            self.pw_btn.busy(False)
            field = err.data.get("field")
            if field in self.pw_fields:
                self.pw_fields[field].set_error(err.message)
            else:
                show_error(self, err)

        tasks.run(api.post, "/api/auth/change-password", {"current_password": cur, "new_password": new},
                  on_done=done, on_error=fail, owner=self)
