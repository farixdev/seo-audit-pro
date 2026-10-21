# -*- coding: utf-8 -*-
"""Lead dialogs and actions shared by the lead list and lead detail pages."""

from PySide6.QtWidgets import QCheckBox, QGridLayout, QWidget

from seo_audit_pro.core import api, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.widgets.common import label, vbox
from seo_audit_pro.widgets.feedback import Dialog, Field, combo, confirm, line_edit, show_error, text_area, toast

STATUS_CHOICES = [("New", "new"), ("Assigned", "assigned"), ("Audited", "audited"), ("Contacted", "contacted"),
                  ("Qualified", "qualified"), ("Won", "won"), ("Lost", "lost")]


def load_assignees(owner, on_done):
    if session.is_agent():
        on_done({"teams": [], "people": []})
        return
    # Offline is already shown by the page state and connection indicator.
    tasks.run(api.get, "/api/assignees", on_done=on_done,
              on_error=lambda e: None if e.offline else show_error(owner, e), owner=owner)


class AssigneePicker(QWidget):
    """Team + person selectors honouring the caller's scope."""

    def __init__(self, options, team_id=None, agent_id=None, allow_unassigned=True):
        super().__init__()
        self.options = options
        lay = vbox(self, spacing=12)
        self.team = combo([("No team", None)] + [(t["name"], t["id"]) for t in options["teams"]], team_id)
        self.person = combo([])
        self.allow_unassigned = allow_unassigned
        if session.is_manager():
            lay.addWidget(Field("Team", self.team))
        else:
            self.team.setCurrentIndex(self.team.findData(session.user.get("team_id")))
        lay.addWidget(Field("Assigned to", self.person))
        self.team.currentIndexChanged.connect(lambda _i: self._fill_people(None))
        self._fill_people(agent_id)

    def _fill_people(self, selected):
        tid = self.team.currentData()
        self.person.clear()
        self.person.addItem("Unassigned (team pool)" if tid else "Unassigned", None)
        for p in self.options["people"]:
            if not tid or p["team_id"] == tid:
                role = " · Team Lead" if p["role"] == "TEAM_LEAD" else ""
                self.person.addItem(f"{p['name']}{role}", p["id"])
        if selected:
            idx = self.person.findData(selected)
            if idx >= 0:
                self.person.setCurrentIndex(idx)

    def values(self):
        return self.team.currentData(), self.person.currentData()


class LeadFormDialog(Dialog):
    """Create or edit a lead. Handles domain duplicates and similar-lead warnings."""

    def __init__(self, parent, lead=None, options=None):
        super().__init__(parent, "Edit lead" if lead else "Add lead",
                         "" if lead else "Duplicate websites are detected automatically "
                                         "(http/https, www and trailing slashes are ignored).", 560)
        self.lead = lead
        self.created = None
        self.existing_lead_id = None
        lead = lead or {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.inputs = {
            "company": line_edit("Company name", lead.get("company", "")),
            "website": line_edit("example.com", lead.get("website", "")),
            "contact_name": line_edit("Full name", lead.get("contact_name") or ""),
            "email": line_edit("name@company.com", lead.get("email") or ""),
            "phone": line_edit("+1 555 000 0000", lead.get("phone") or ""),
            "niche": line_edit("e.g. Dentists", lead.get("niche") or ""),
            "area": line_edit("e.g. Chicago IL", lead.get("area") or ""),
            "address": line_edit("Street address", lead.get("address") or ""),
            "country": line_edit("e.g. Canada", lead.get("country") or ""),
        }
        titles = {"company": "Company", "website": "Website", "contact_name": "Contact", "email": "Email",
                  "phone": "Phone", "niche": "Niche", "area": "Area", "address": "Address", "country": "Country"}
        self.fields = {k: Field(titles[k], w) for k, w in self.inputs.items()}
        order = ["company", "website", "niche", "area", "contact_name", "email", "phone", "country", "address"]
        for i, k in enumerate(order):
            grid.addWidget(self.fields[k], i // 2, i % 2)
        self.body.addLayout(grid)
        self.notes = text_area("Notes visible to everyone working this lead", lead.get("notes") or "", 70)
        self.body.addWidget(Field("Notes", self.notes))
        self.status = None
        if self.lead:
            self.status = combo(STATUS_CHOICES, lead.get("status"))
            if lead.get("status") != "archived":
                self.body.addWidget(Field("Status", self.status))
        self.picker = None
        if not self.lead and options and not session.is_agent():
            self.picker = AssigneePicker(options, session.user.get("team_id") if session.is_team_lead() else None)
            self.body.addWidget(self.picker)
        self.add_buttons("Save changes" if self.lead else "Add lead")
        self.primary.clicked.connect(lambda: self.submit(False))

    def payload(self):
        data = {k: w.text().strip() for k, w in self.inputs.items()}
        data["notes"] = self.notes.toPlainText().strip()
        if self.status is not None and self.lead.get("status") != "archived":
            data["status"] = self.status.currentData()
        if self.picker:
            data["team_id"], data["agent_id"] = self.picker.values()
        return data

    def submit(self, allow_similar):
        for f in self.fields.values():
            f.set_error("")
        self.clear_error()
        data = self.payload()
        if not data["company"]:
            self.fields["company"].set_error("Company is required.")
        if not data["website"]:
            self.fields["website"].set_error("Website is required.")
        if not data["company"] or not data["website"]:
            return
        self.primary.busy(True, "Saving…")
        if self.lead:
            data["version"] = self.lead["version"]
            call = (api.patch, f"/api/leads/{self.lead['id']}", data)
            kw = {}
        else:
            data["allow_similar"] = allow_similar
            if not hasattr(self, "_idem"):
                self._idem = api.new_idempotency_key()
            call = (api.post, "/api/leads", data)
            kw = {"idempotency_key": self._idem}

        def done(res):
            self.primary.busy(False)
            self.created = res["lead"]
            self.accept()

        def fail(err):
            self.primary.busy(False)
            if err.code == "possible_duplicate" and not allow_similar:
                matches = "\n".join(f"• {m['company']} ({m['domain']}) — same {m['field']}" for m in err.data.get("matches", []))
                if confirm(self, "Possible duplicate", f"{err.message}\n\n{matches}\n\nCreate this lead anyway?",
                           "Create anyway"):
                    self._idem = api.new_idempotency_key()
                    self.submit(True)
                return
            field = err.data.get("field")
            self.existing_lead_id = err.data.get("existing_lead_id")
            if field in self.fields:
                self.fields[field].set_error(err.message)
            else:
                self.show_error(err)
            if err.status == 409:
                self._idem = api.new_idempotency_key()

        tasks.run(call[0], call[1], call[2], on_done=done, on_error=fail, owner=self, **kw)


class AssignDialog(Dialog):
    """Assign one lead, a set of selected leads, or every lead matching a
    filter (``selection={"filter": {...}, "count": n, "label": str}``). Leads can
    go to one person, a team pool, or be split evenly across a team's agents."""

    def __init__(self, parent, options, leads=None, selection=None):
        self.leads = leads or []
        self.selection = selection
        count = selection["count"] if selection else len(self.leads)
        title = (f"Assign {self.leads[0]['company']}" if count == 1 and self.leads
                 else f"Assign {count:,} leads")
        super().__init__(parent, title, "Every change is recorded in each lead's assignment history.", 480)
        if selection and selection.get("label"):
            self.body.addWidget(label(f"Selection: {selection['label']}", "Meta", wrap=True))
        single = len(self.leads) == 1 and not selection
        first = self.leads[0] if single else {}
        self.picker = AssigneePicker(options, first.get("assigned_team_id"), first.get("assigned_agent_id"))
        self.body.addWidget(self.picker)
        self.split = QCheckBox("Split evenly across the team's agents")
        self.split.setVisible(not single)
        self.body.addWidget(self.split)
        self.split_hint = label("", "Meta", wrap=True)
        self.split_hint.setVisible(False)
        self.body.addWidget(self.split_hint)
        self.reason = line_edit("Optional — e.g. new territory, workload balancing")
        self.body.addWidget(Field("Reason", self.reason))
        self.add_buttons("Assign")
        self.primary.clicked.connect(self.submit)
        self.split.toggled.connect(self._sync)
        self.picker.team.currentIndexChanged.connect(lambda _i: self._sync())
        self._sync()
        self._idem = api.new_idempotency_key()
        self.result = None

    def _count(self):
        return self.selection["count"] if self.selection else len(self.leads)

    def _sync(self):
        team_id = self.picker.team.currentData()
        agents = [p for p in self.picker.options["people"] if p["team_id"] == team_id and p["role"] == "AGENT"]
        self.split.setEnabled(bool(team_id and agents))
        on = self.split.isChecked() and self.split.isEnabled()
        self.picker.person.setEnabled(not on)
        self.split_hint.setVisible(on)
        if on:
            per = self._count() / max(1, len(agents))
            self.split_hint.setText(f"{self._count():,} leads across {len(agents)} agents, about {per:,.0f} each: "
                                    + ", ".join(a["name"] for a in agents))

    def submit(self):
        team_id, agent_id = self.picker.values()
        reason = self.reason.text().strip()
        split = self.split.isChecked() and self.split.isEnabled()
        if len(self.leads) == 1 and not self.selection:
            lead = self.leads[0]
            path, payload = f"/api/leads/{lead['id']}/assign", {"team_id": team_id, "agent_id": agent_id,
                                                                "reason": reason, "version": lead["version"]}
        else:
            payload = {"team_id": team_id, "agent_id": None if split else agent_id, "reason": reason,
                       "mode": "split" if split else "single"}
            if self.selection:
                payload.update(filter=self.selection["filter"], expected_count=self.selection["count"])
            else:
                payload["lead_ids"] = [lead["id"] for lead in self.leads]
            path = "/api/leads/bulk-assign"
        self.primary.busy(True, "Assigning…")

        def done(res):
            self.primary.busy(False)
            self.result = res
            self.accept()

        def fail(err):
            self.primary.busy(False)
            self._idem = api.new_idempotency_key()
            if err.code == "count_changed" and self.selection:
                self.selection["count"] = err.data.get("count", self.selection["count"])
                self.setWindowTitle(f"Assign {self.selection['count']:,} leads")
                self._sync()
            self.show_error(err)

        tasks.run(api.post, path, payload, idempotency_key=self._idem, timeout=180, on_done=done, on_error=fail,
                  owner=self)


def start_audit(page, lead, on_started=None):
    """Start an audit for ``lead``; navigates to the live audit view."""
    key = api.new_idempotency_key()

    def done(res):
        toast(page, f"Audit started for {lead['company']}.", "success")
        if on_started:
            on_started(res["audit"])
        page.ws.navigate("audit", audit_id=res["audit"]["id"])

    def fail(err):
        if err.status == 409 and err.data.get("audit_id"):
            toast(page, "An audit is already running for this lead. Opening it.", "info")
            page.ws.navigate("audit", audit_id=err.data["audit_id"])
        else:
            show_error(page, err, "Audit not started")

    tasks.run(api.post, "/api/audits", {"lead_id": lead["id"]}, idempotency_key=key, on_done=done, on_error=fail,
              owner=page)


def set_archived(page, archived, on_done, leads=None, selection=None):
    """Archive or restore selected leads, or every lead matching a filter."""
    verb = "Archive" if archived else "Restore"
    count = selection["count"] if selection else len(leads)
    names = leads[0]["company"] if leads and count == 1 else f"{count:,} leads"
    if archived and not confirm(page, f"Archive {names}?", "Archived leads are hidden from the lead bank and cannot be "
                                                            "audited or emailed until restored.", "Archive"):
        return
    payload = ({"filter": selection["filter"], "expected_count": selection["count"]} if selection
               else {"lead_ids": [lead["id"] for lead in leads]})
    tasks.run(api.post, "/api/leads/archive" if archived else "/api/leads/restore", payload, timeout=180,
              on_done=lambda r: (toast(page, f"{verb}d {r['updated']:,} lead(s).", "success"), on_done()),
              on_error=lambda e: show_error(page, e), owner=page)
