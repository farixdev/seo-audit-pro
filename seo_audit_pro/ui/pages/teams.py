# -*- coding: utf-8 -*-
"""Teams (Manager) and My Team (Team Lead)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QMenu

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, ROLE_LABEL
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.ui.pages.users import people_table, person_summary, role_badge, surface
from seo_audit_pro.widgets.common import Avatar, Badge, MetricStrip, StatefulArea, button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Drawer, Field, confirm, kv_row, line_edit, show_error, toast
from seo_audit_pro.widgets.table import Column, DataTable


class TeamNameDialog(Dialog):
    def __init__(self, parent, team=None):
        super().__init__(parent, f"Rename {team['name']}" if team else "New team",
                         "" if team else "Add members and a Team Lead from Users after creating the team.", 420)
        self.team = team
        self.result_team = None
        self.name = line_edit("e.g. Local SEO", team["name"] if team else "")
        self.field = Field("Team name", self.name)
        self.body.addWidget(self.field)
        self.add_buttons("Save" if team else "Create team")
        self.primary.clicked.connect(self.submit)
        self.name.returnPressed.connect(self.submit)
        self._idem = api.new_idempotency_key()

    def submit(self):
        name = self.name.text().strip()
        if not name:
            self.field.set_error("Team name is required.")
            return
        self.field.set_error("")
        self.primary.busy(True, "Saving…")

        def done(res):
            self.primary.busy(False)
            self.result_team = res["team"]
            self.accept()

        def fail(err):
            self.primary.busy(False)
            self._idem = api.new_idempotency_key()
            if err.data.get("field") == "name":
                self.field.set_error(err.message)
            else:
                self.show_error(err)

        if self.team:
            tasks.run(api.patch, f"/api/teams/{self.team['id']}", {"name": name}, on_done=done, on_error=fail, owner=self)
        else:
            tasks.run(api.post, "/api/teams", {"name": name}, idempotency_key=self._idem, on_done=done, on_error=fail,
                      owner=self)


def _member_row(m):
    """Compact member line for team drawers."""
    w = QFrame()
    w.setStyleSheet(f"QFrame#Member {{ border-bottom: 1px solid {C['divider']}; }}")
    w.setObjectName("Member")
    row = hbox(w, (0, 10, 0, 10), 12)
    av = Avatar(m["name"], 32)
    av.set_online(bool(m.get("online")))
    row.addWidget(av)
    col = vbox(spacing=2)
    name = label(m["name"])
    name.setStyleSheet("font-weight: 600;")
    col.addWidget(name)
    col.addWidget(label(f"{fmt.plural(m.get('lead_count', 0), 'lead')} · {fmt.plural(m.get('audit_count', 0), 'audit')}",
                        "Meta"))
    row.addLayout(col, 1)
    row.addWidget(role_badge(m["role"]), 0, Qt.AlignmentFlag.AlignVCenter)
    if not m.get("is_active", True):
        row.addWidget(Badge.status("inactive"), 0, Qt.AlignmentFlag.AlignVCenter)
    return w


class TeamsPage(Page):
    nav_key = "teams"
    title = "Teams"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        h = self.header("Teams", "Teams scope which leads, audits and chats people can access.")
        add = h.add_action(button("New team", "primary", icon_name="plus"))
        add.clicked.connect(self.create)
        self.table = DataTable([
            Column("name", "Team", "strong", 240),
            Column("lead_name", "Team Lead", "text", 200, render=lambda r: r.get("lead_name") or "No Team Lead"),
            Column("member_count", "Members", "number", 100, align="right"),
            Column("lead_count", "Leads", "number", 100, align="right"),
            Column("audit_count", "Completed audits", "number", 140, align="right"),
            Column("created_at", "Created", "date", 120, stretch=True),
            Column("_actions", "", "actions", 44),
        ], paginated=False)
        self.table.activated.connect(lambda r: self.open_team(r["id"]))
        self.table.context_requested.connect(self.row_menu)
        self.area = StatefulArea(surface(self.table))
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self.drawer = Drawer(self, 480)
        self.rows = []

    def activate(self, team_id=None, **_):
        self.load()
        if team_id:
            self.open_team(team_id)

    def load(self):
        if not self.rows:
            self.area.loading("Loading teams…")
        self.fetch("list", api.get, "/api/teams", on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        self.rows = data.get("items") or []
        self.table.set_rows(self.rows)
        if not self.rows:
            self.area.empty("No teams yet", "Create a team, then add a Team Lead and Agents from Users.",
                            icon_name="teams")
        else:
            self.area.ready()

    def create(self):
        dlg = TeamNameDialog(self)
        if dlg.exec():
            toast(self, f"Team {dlg.result_team['name']} created.", "success")
            self.load()

    def rename(self, team):
        dlg = TeamNameDialog(self, team)
        if dlg.exec():
            toast(self, "Team renamed.", "success")
            self.load()

    def delete(self, team):
        if not confirm(self, f"Delete {team['name']}?", "A team can only be deleted when it has no members, leads "
                                                         "or audit history.", "Delete team", danger=True):
            return
        tasks.run(api.delete, f"/api/teams/{team['id']}",
                  on_done=lambda _r: (toast(self, f"{team['name']} deleted.", "success"), self.drawer.close_drawer(),
                                      self.load()),
                  on_error=lambda e: show_error(self, e), owner=self)

    def row_menu(self, row, pos):
        m = QMenu(self)
        m.addAction(icons.icon("teams", C["text_2"]), "View members", lambda: self.open_team(row["id"]))
        m.addAction(icons.icon("edit", C["text_2"]), "Rename", lambda: self.rename(row))
        m.addSeparator()
        m.addAction(icons.icon("trash", C["danger"]), "Delete", lambda: self.delete(row))
        m.exec(pos)

    def open_team(self, team_id):
        self.drawer.clear()
        self.drawer.open_drawer("Team")
        self.drawer.content_layout.addWidget(label("Loading…", "Muted"))

        def fail(err):
            self.drawer.clear()
            self.drawer.content_layout.addWidget(label(err.message, "Muted", wrap=True))

        self.fetch("team", api.get, f"/api/teams/{team_id}", on_done=self._fill_drawer, on_error=fail)

    def _fill_drawer(self, data):
        team, members = data["team"], data["members"]
        d = self.drawer
        d.clear()
        d.title.setText(team["name"])
        lay = d.content_layout
        for k, v in (("Team Lead", team.get("lead_name") or "No active Team Lead"),
                     ("Members", fmt.number(team["member_count"])), ("Active leads", fmt.number(team["lead_count"])),
                     ("Completed audits", fmt.number(team["audit_count"])), ("Created", fmt.date(team["created_at"]))):
            lay.addWidget(kv_row(k, v))
        lay.addWidget(label("A person becomes Team Lead when their role is set to Team Lead in Users. "
                            "Each team has one active Team Lead.", "Meta", wrap=True))
        lay.addSpacing(4)
        lay.addWidget(label("Members", "SectionTitle"))
        if not members:
            lay.addWidget(label("No members yet. Add people to this team from Users.", "Muted", wrap=True))
        holder = vbox(spacing=0)
        for m in members:
            holder.addWidget(_member_row(m))
        lay.addLayout(holder)
        actions = hbox(spacing=8)
        rename = button("Rename", icon_name="edit")
        rename.clicked.connect(lambda: self.rename(team))
        users = button("Manage people", icon_name="user")
        users.clicked.connect(lambda: (self.drawer.close_drawer(), self.ws.navigate("users")))
        delete = button("Delete", "danger", icon_name="trash")
        delete.clicked.connect(lambda: self.delete(team))
        for b in (rename, users):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(delete)
        lay.addSpacing(8)
        lay.addLayout(actions)
        lay.addStretch(1)


class MyTeamPage(Page):
    """Read-only view of the Team Lead's own team."""
    nav_key = "team"
    title = "Team"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.head = self.header("Team", "Members of your team and their current workload")
        self.metrics = MetricStrip()
        self.metrics.add("members", "Members")
        self.metrics.add("online", "Online now")
        self.metrics.add("leads", "Active leads")
        self.metrics.add("audits", "Completed audits")
        self.table = people_table([
            Column("name", "Name", "strong", 240, stretch=True, secondary=lambda r: r.get("email")),
            Column("role", "Role", "text", 120, render=lambda r: ROLE_LABEL.get(r["role"], r["role"])),
            Column("is_active", "Status", "status", 110, render=lambda r: "active" if r["is_active"] else "inactive"),
            Column("last_seen_at", "Last active", "presence", 160),
            Column("lead_count", "Leads", "number", 90, align="right"),
            Column("audit_count", "Audits", "number", 90, align="right"),
        ])
        self.table.activated.connect(self.open_member)
        content = vbox(None, spacing=16)
        holder = QFrame()
        holder.setLayout(content)
        content.addWidget(self.metrics)
        content.addWidget(surface(self.table), 1)
        self.area = StatefulArea(holder)
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)
        self.drawer = Drawer(self, 460)
        self.members = []
        self._pending_user = None

    def activate(self, user_id=None, team_id=None, **_):
        self._pending_user = user_id
        self.load()

    def load(self):
        team_id = (session.user or {}).get("team_id")
        if not team_id:
            self.area.empty("You are not assigned to a team", "Ask a Manager to add you to a team.", icon_name="teams")
            return
        if not self.members:
            self.area.loading("Loading team…")
        self.fetch("team", api.get, f"/api/teams/{team_id}", on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        team, self.members = data["team"], data["members"]
        self.head.title.setText(team["name"])
        self.head.subtitle.setText(f"Led by {team.get('lead_name') or 'no active Team Lead'} · "
                                   f"{fmt.plural(team['member_count'], 'active member')}")
        self.ws.set_page_title(self, team["name"])
        m = self.metrics.metrics
        m["members"].set(fmt.number(team["member_count"]))
        m["online"].set(fmt.number(sum(1 for x in self.members if x.get("online"))))
        m["leads"].set(fmt.number(team["lead_count"]))
        m["audits"].set(fmt.number(team["audit_count"]))
        self.table.set_rows(self.members)
        self.area.ready()
        if self._pending_user:
            match = next((x for x in self.members if x["id"] == self._pending_user), None)
            self._pending_user = None
            if match:
                self.open_member(match)

    def open_member(self, member):
        d = self.drawer
        d.clear()
        d.open_drawer(member["name"])
        lay = d.content_layout
        lay.addWidget(person_summary(member))
        for k, v in (("Leads assigned", fmt.number(member.get("lead_count"))),
                     ("Audits started", fmt.number(member.get("audit_count"))),
                     ("Last sign-in", fmt.datetime_(member.get("last_login_at"), "Never")),
                     ("Last active", "Online now" if member.get("online") else fmt.relative(member.get("last_seen_at"), "Never")),
                     ("Joined", fmt.date(member.get("created_at")))):
            lay.addWidget(kv_row(k, v))
        if member["id"] != session.user_id and member.get("is_active"):
            msg = button("Send message", icon_name="chat")
            msg.clicked.connect(lambda: self._message(member))
            lay.addWidget(msg, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

    def _message(self, member):
        def done(res):
            self.drawer.close_drawer()
            self.ws.navigate("chat", conversation_id=res["conversation"]["id"])
        tasks.run(api.post, "/api/chat/conversations/direct", {"user_id": member["id"]}, on_done=done,
                  on_error=lambda e: show_error(self, e), owner=self)
