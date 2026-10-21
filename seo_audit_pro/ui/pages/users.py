# -*- coding: utf-8 -*-
"""User management (Manager): provisioning, editing, temporary passwords, activation."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QMenu, QStyle, QStyleOptionViewItem

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, ROLE_LABEL
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Avatar, Badge, StatefulArea, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import (Dialog, Drawer, Field, SearchBox, SecretRevealDialog, combo, confirm,
                                            kv_row, line_edit, show_error, toast)
from seo_audit_pro.widgets.table import ROW_ROLE, CellDelegate, Column, DataTable

ROLE_CHOICES = [("Agent", "AGENT"), ("Team Lead", "TEAM_LEAD"), ("Manager", "MANAGER")]


# ---------------------------------------------------------------------------
# Shared people helpers (also used by the Teams pages)
# ---------------------------------------------------------------------------
class PresenceDelegate(CellDelegate):
    """Adds a ``presence`` column kind: green dot + "Online", else last-seen time."""

    def paint(self, painter, option, index):
        col = self.model.columns[index.column()]
        if col.kind != "presence":
            super().paint(painter, option, index)
            return
        row = index.data(ROW_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        if opt.widget:
            opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        r = option.rect.adjusted(12, 0, -12, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(option.font)
        painter.setFont(f)
        x = r.x()
        if row.get("online"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#12B76A"))
            painter.drawEllipse(QRectF(x, r.center().y() - 3.5, 7, 7))
            x += 14
            text, color = "Online", C["text"]
        else:
            seen = row.get("last_seen_at")
            text, color = (fmt.relative(seen) if seen else "Never signed in"), (C["text_2"] if seen else C["muted"])
        painter.setPen(QColor(color))
        fm = QFontMetrics(f)
        painter.drawText(QRectF(x, r.y(), r.right() - x, r.height()), int(Qt.AlignmentFlag.AlignVCenter),
                         fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.right() - x)))
        painter.restore()


def people_table(columns, paginated=False, page_sizes=(25, 50, 100)):
    table = DataTable(columns, paginated=paginated, page_sizes=page_sizes)
    table.view.setItemDelegate(PresenceDelegate(table.model, table.view))
    return table


def role_badge(role):
    fg, bg = {"MANAGER": ("#18181B", "#E4E4E7"), "TEAM_LEAD": (C["info"], C["info_bg"])}.get(
        role, (C["neutral"], C["neutral_bg"]))
    return Badge(ROLE_LABEL.get(role, role or ""), fg, bg)


def surface(widget):
    frame = QFrame()
    frame.setObjectName("Surface")
    vbox(frame).addWidget(widget)
    return frame


def person_summary(user):
    """Avatar + name + email block used at the top of person drawers."""
    w = QFrame()
    row = hbox(w, spacing=12)
    av = Avatar(user.get("name", ""), 44)
    av.set_online(bool(user.get("online")))
    row.addWidget(av)
    col = vbox(spacing=2)
    name = label(user.get("name", ""))
    name.setStyleSheet("font-size: 15px; font-weight: 600;")
    col.addWidget(name)
    col.addWidget(label(user.get("email", ""), "Muted", selectable=True))
    row.addLayout(col, 1)
    badges = hbox(spacing=6)
    badges.addWidget(role_badge(user.get("role")))
    badges.addWidget(Badge.status("active" if user.get("is_active", True) else "inactive"))
    row.addLayout(badges)
    return w


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class UserDialog(Dialog):
    """Create (temporary password issued by the server) or edit a user."""

    def __init__(self, parent, teams, user=None):
        super().__init__(parent, "Edit user" if user else "Add user",
                         "" if user else "A temporary password is generated. The user must choose their own "
                                         "password at first sign-in.", 480)
        self.user = user
        self.result_user = None
        self.temporary_password = None
        user = user or {}
        self.name = line_edit("Full name", user.get("name", ""))
        self.email = line_edit("name@company.com", user.get("email", ""))
        self.role = combo(ROLE_CHOICES, user.get("role", "AGENT"))
        self.team = combo([("Select a team", None)] + [(t["name"], t["id"]) for t in teams], user.get("team_id"))
        self.fields = {"name": Field("Name", self.name), "email": Field("Work email", self.email),
                       "role": Field("Role", self.role),
                       "team_id": Field("Team", self.team, "Team Leads and Agents must belong to a team. "
                                                           "Each team has one active Team Lead.")}
        for f in self.fields.values():
            self.body.addWidget(f)
        self.role.currentIndexChanged.connect(self._role_changed)
        self._role_changed()
        if user and user.get("id") == session.user_id:
            self.role.setEnabled(False)
            self.fields["role"].hint.setText("You cannot change your own role.")
            self.fields["role"].hint.setVisible(True)
        self.add_buttons("Save changes" if self.user else "Add user")
        self.primary.clicked.connect(self.submit)
        self._idem = api.new_idempotency_key()

    def _role_changed(self):
        manager = self.role.currentData() == "MANAGER"
        self.fields["team_id"].setVisible(not manager)

    def submit(self):
        for f in self.fields.values():
            f.set_error("")
        self.clear_error()
        name, email = self.name.text().strip(), self.email.text().strip().lower()
        role, team_id = self.role.currentData(), self.team.currentData()
        errs = {"name": "" if name else "Name is required.",
                "email": "" if "@" in email and "." in email.split("@")[-1] else "Enter a valid email address.",
                "team_id": "" if role == "MANAGER" or team_id else "Choose a team."}
        for k, v in errs.items():
            self.fields[k].set_error(v)
        if any(errs.values()):
            return
        payload = {"name": name, "email": email, "role": role, "team_id": None if role == "MANAGER" else team_id}
        self.primary.busy(True, "Saving…")

        def done(res):
            self.primary.busy(False)
            self.result_user = res.get("user")
            self.temporary_password = res.get("temporary_password")
            self.accept()

        def fail(err):
            self.primary.busy(False)
            self._idem = api.new_idempotency_key()
            field = err.data.get("field")
            if field in self.fields:
                self.fields[field].set_error(err.message)
            else:
                self.show_error(err)

        if self.user:
            tasks.run(api.patch, f"/api/users/{self.user['id']}", payload, on_done=done, on_error=fail, owner=self)
        else:
            tasks.run(api.post, "/api/users", payload, idempotency_key=self._idem, on_done=done, on_error=fail,
                      owner=self)


def reset_password(page, user, on_done=None):
    if not confirm(page, f"Issue a new temporary password for {user['name']}?",
                   "Their current password stops working immediately and they are signed out on every device. "
                   "They must choose a new password at their next sign-in.", "Issue temporary password"):
        return

    def done(res):
        SecretRevealDialog(page, "Temporary password", f"Share this with {user['name']} ({user['email']}).",
                           res["temporary_password"]).exec()
        if on_done:
            on_done()

    tasks.run(api.post, f"/api/users/{user['id']}/reset-password", on_done=done,
              on_error=lambda e: show_error(page, e), owner=page)


def set_active(page, user, active, on_done):
    if active:
        title, text, verb = (f"Reactivate {user['name']}?", "They will be able to sign in again with their existing "
                             "password.", "Reactivate")
    else:
        title, text, verb = (f"Deactivate {user['name']}?", "They are signed out immediately and cannot sign in. "
                             "Their leads stay assigned until you reassign them.", "Deactivate")
    if not confirm(page, title, text, verb, danger=not active):
        return
    path = f"/api/users/{user['id']}/{'reactivate' if active else 'deactivate'}"
    tasks.run(api.post, path, on_done=lambda _r: (toast(page, f"{user['name']} {verb.lower()}d.", "success"), on_done()),
              on_error=lambda e: show_error(page, e), owner=page)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class UsersPage(Page):
    nav_key = "users"
    title = "Users"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.params = {"page": 1, "page_size": 50}
        self.teams = []
        h = self.header("Users", "Everyone with access to the workspace. Roles decide what each person can see.")
        add = h.add_action(button("Add user", "primary", icon_name="user_plus"))
        add.clicked.connect(self.create)

        bar = hbox(spacing=8)
        self.search = SearchBox("Search name or email")
        self.search.search.connect(lambda t: self._set("search", t))
        bar.addWidget(self.search, 2)
        self.role = combo([("All roles", "")] + ROLE_CHOICES)
        self.role.currentIndexChanged.connect(lambda _i: self._set("role", self.role.currentData()))
        bar.addWidget(self.role)
        self.team = combo([("All teams", "")])
        self.team.currentIndexChanged.connect(lambda _i: self._set("team_id", self.team.currentData()))
        bar.addWidget(self.team)
        self.status = combo([("Any status", ""), ("Active", "active"), ("Inactive", "inactive")])
        self.status.currentIndexChanged.connect(lambda _i: self._set("status", self.status.currentData()))
        bar.addWidget(self.status)
        bar.addStretch(1)
        self.body.addLayout(bar)

        self.table = people_table([
            Column("name", "Name", "strong", 240, secondary=lambda r: r.get("email")),
            Column("role", "Role", "text", 110, render=lambda r: ROLE_LABEL.get(r["role"], r["role"])),
            Column("team_name", "Team", "text", 160, render=lambda r: r.get("team_name") or "—"),
            Column("is_active", "Status", "status", 110, render=lambda r: "active" if r["is_active"] else "inactive"),
            Column("last_seen_at", "Last active", "presence", 150),
            Column("lead_count", "Leads", "number", 80, align="right"),
            Column("audit_count", "Audits", "number", 80, align="right"),
            Column("created_at", "Created", "date", 110, stretch=True),
            Column("_actions", "", "actions", 44),
        ], paginated=True, page_sizes=(50, 100, 200))
        self.table.page_changed.connect(lambda p: self._set("page", p, keep_page=True))
        self.table.page_size_changed.connect(lambda s: self._set("page_size", s))
        self.table.activated.connect(lambda r: self.open_user(r["id"]))
        self.table.context_requested.connect(self.row_menu)
        self.area = StatefulArea(surface(self.table))
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self.drawer = Drawer(self, 480)

    def activate(self, user_id=None, **_):
        self._load_teams()
        self.load()
        if user_id:
            self.open_user(user_id)

    def _load_teams(self):
        def done(res):
            self.teams = res.get("items") or []
            current = self.team.currentData()
            self.team.blockSignals(True)
            self.team.clear()
            self.team.addItem("All teams", "")
            for t in self.teams:
                self.team.addItem(t["name"], t["id"])
            idx = self.team.findData(current)
            self.team.setCurrentIndex(max(0, idx))
            self.team.blockSignals(False)
        self.fetch("teams", api.get, "/api/teams", on_done=done, on_error=lambda e: show_error(self, e))

    def _set(self, key, value, keep_page=False):
        self.params[key] = value
        if not keep_page:
            self.params["page"] = 1
        self.load()

    def load(self):
        if not self.table.model.rows:
            self.area.loading("Loading users…")
        params = {k: v for k, v in self.params.items() if v not in ("", None)}
        self.fetch("list", api.get, "/api/users", params=params, on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        self.table.set_page(data)
        if not data["items"]:
            self.area.empty("No users match these filters", "Change the search or filters.")
        else:
            self.area.ready()

    # -- actions -------------------------------------------------------------
    def create(self):
        dlg = UserDialog(self, self.teams)
        if dlg.exec():
            self.load()
            SecretRevealDialog(self, "User created", f"{dlg.result_user['name']} can now sign in with this "
                                                     "temporary password.", dlg.temporary_password).exec()

    def edit(self, user):
        dlg = UserDialog(self, self.teams, user)
        if dlg.exec():
            toast(self, "User updated.", "success")
            self.load()
            if self.drawer.isVisible():
                self.open_user(user["id"])

    def row_menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("eye", C["text_2"]), "View", lambda: self.open_user(row["id"]))
        m.addAction(icons.icon("edit", C["text_2"]), "Edit", lambda: self.edit(row))
        if row["id"] != session.user_id:
            m.addAction(icons.icon("key", C["text_2"]), "Change password",
                        lambda: reset_password(self, row, self.load))
            m.addSeparator()
            if row["is_active"]:
                m.addAction(icons.icon("user_x", C["danger"]), "Deactivate",
                            lambda: set_active(self, row, False, self._after_change))
            else:
                m.addAction(icons.icon("user_check", C["text_2"]), "Reactivate",
                            lambda: set_active(self, row, True, self._after_change))
        m.exec(pos)

    def _after_change(self):
        self.load()
        self.drawer.close_drawer()

    def open_user(self, user_id):
        self.drawer.clear()
        self.drawer.open_drawer("User")
        self.drawer.content_layout.addWidget(label("Loading…", "Muted"))

        def done(res):
            self._fill_drawer(res["user"])

        def fail(err):
            self.drawer.clear()
            self.drawer.content_layout.addWidget(label(err.message, "Muted", wrap=True))

        self.fetch("user", api.get, f"/api/users/{user_id}", on_done=done, on_error=fail)

    def _fill_drawer(self, u):
        d = self.drawer
        d.clear()
        d.title.setText(u["name"])
        lay = d.content_layout
        lay.addWidget(person_summary(u))
        if u.get("must_change_password"):
            note = label("Has not yet replaced the temporary password.", "Meta")
            note.setStyleSheet(f"color: {C['warning']};")
            lay.addWidget(note)
        for k, v in (("Team", u.get("team_name") or "—"), ("Leads assigned", fmt.number(u.get("lead_count"))),
                     ("Audits started", fmt.number(u.get("audit_count"))),
                     ("Last sign-in", fmt.datetime_(u.get("last_login_at"), "Never")),
                     ("Last active", "Online now" if u.get("online") else fmt.relative(u.get("last_seen_at"), "Never")),
                     ("Created", fmt.date(u.get("created_at")))):
            lay.addWidget(kv_row(k, v))
        actions = hbox(spacing=8)
        edit = button("Edit", icon_name="edit")
        edit.clicked.connect(lambda: self.edit(u))
        actions.addWidget(edit)
        if u["id"] != session.user_id:
            pw = button("Change password", icon_name="key")
            pw.clicked.connect(lambda: reset_password(self, u, self.load))
            actions.addWidget(pw)
            if u["is_active"]:
                toggle = button("Deactivate", "danger", icon_name="user_x")
                toggle.clicked.connect(lambda: set_active(self, u, False, self._after_change))
            else:
                toggle = button("Reactivate", icon_name="user_check")
                toggle.clicked.connect(lambda: set_active(self, u, True, self._after_change))
            actions.addWidget(toggle)
        actions.addStretch(1)
        lay.addSpacing(4)
        lay.addLayout(actions)
        lay.addStretch(1)
