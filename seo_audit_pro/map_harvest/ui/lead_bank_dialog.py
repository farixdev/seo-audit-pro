# -*- coding: utf-8 -*-
"""Dialog to directly import Map Harvest scraped leads into the Lead Bank."""

import gzip
import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QWidget

from seo_audit_pro.core import api, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.ui.pages.lead_dialogs import AssigneePicker
from seo_audit_pro.widgets.common import button, hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog


class AddToLeadBankDialog(Dialog):
    leads_imported = Signal(dict)

    def __init__(self, parent, results, default_domain="", default_area="", options=None):
        self.results = list(results or [])
        count = len(self.results)
        super().__init__(
            parent,
            "Add Scraped Leads to Lead Bank",
            f"Import {count:,} scraped business{'es' if count != 1 else ''} directly into the lead bank. "
            "Rows without a website and duplicates already in the bank will be handled automatically.",
            540,
        )
        opts = dict(options or {})
        opts.setdefault("teams", [])
        opts.setdefault("people", [])
        self.options = opts
        
        # Form
        fl = vbox(spacing=12)

        # Niche
        fl.addWidget(label("Niche / Category", "FieldLabel"))
        self.niche_input = QLineEdit()
        self.niche_input.setPlaceholderText("e.g. Roofing, Dentists, Car Dealers")
        self.niche_input.setFixedHeight(34)
        self.niche_input.setText(str(default_domain or "").strip())
        fl.addWidget(self.niche_input)

        # Area
        fl.addWidget(label("Area / City", "FieldLabel"))
        self.area_input = QLineEdit()
        self.area_input.setPlaceholderText("e.g. Dallas, Toronto, Chicago")
        self.area_input.setFixedHeight(34)
        self.area_input.setText(str(default_area or "").strip())
        fl.addWidget(self.area_input)

        # Optional Assignment (Manager can assign anyone; Team Lead assigns within team; Agent assigns to self)
        assign_box = QWidget()
        al = vbox(assign_box, spacing=6)
        if session.is_agent():
            al.addWidget(label("Assigned to", "FieldLabel"))
            agent_name = (session.user or {}).get("name") or "You"
            al.addWidget(label(f"{agent_name} (My Leads)", "Meta"))
            self.picker = None
        else:
            al.addWidget(label("Assign leads (optional)", "FieldLabel"))
            user_team = (session.user or {}).get("team_id") if session.is_team_lead() else None
            self.picker = AssigneePicker(self.options, user_team)
            al.addWidget(self.picker)
        fl.addWidget(assign_box)

        self.body.addLayout(fl)
        self.add_buttons(f"Add {count:,} Leads")
        self.primary.clicked.connect(self.submit)

    def submit(self):
        niche = self.niche_input.text().strip()
        area = self.area_input.text().strip()
        if not niche:
            self.show_error("Please enter a niche or business category.")
            self.niche_input.setFocus()
            return
        if not area:
            self.show_error("Please enter an area or city.")
            self.area_input.setFocus()
            return
        if not self.results:
            self.show_error("No scraped results to import.")
            return

        if self.picker:
            team_id, agent_id = self.picker.values()
        else:
            team_id = (session.user or {}).get("team_id")
            agent_id = (session.user or {}).get("id")
        self.clear_error()
        self.primary.busy(True, "Importing to Bank…")

        def run_import():
            # Format rows matching Lead Bank column structure
            headers = ["company", "website", "phone", "email", "address", "category",
                       "rating", "review_count", "maps_url", "city"]
            formatted_rows = []
            for r in self.results:
                formatted_rows.append({
                    "company": str(r.get("name") or "").strip(),
                    "website": str(r.get("website") or "").strip(),
                    "phone": str(r.get("phone") or "").strip(),
                    "email": str(r.get("email") or "").strip(),
                    "address": str(r.get("address") or "").strip(),
                    "category": str(r.get("category") or niche).strip(),
                    "rating": str(r.get("rating") or "").strip(),
                    "review_count": str(r.get("review_count") or "").strip(),
                    "maps_url": str(r.get("maps_link") or "").strip(),
                    "city": str(r.get("area") or area).strip(),
                })

            # 1. Create pending batch
            preview = api.post("/api/lead-imports/local", {
                "file_name": f"MapHarvest - {niche} in {area}.csv",
                "headers": headers,
                "total_rows": len(formatted_rows),
                "file_size": max(1024, len(formatted_rows) * 120),
                "sample": formatted_rows[:200],
                "niche": niche,
                "area": area,
            })
            batch_id = preview["batch"]["id"]
            mapping = preview.get("mapping") or {}

            # 2. Start batch with mapping and assignment
            api.post(f"/api/lead-batches/{batch_id}/start", {
                "niche": niche,
                "area": area,
                "mapping": mapping,
                "team_id": team_id,
                "agent_id": agent_id,
            }, idempotency_key=api.new_idempotency_key())

            # 3. Send the rows (gzip, at most 2,000 per request: the server's chunk limit),
            # then finish. A failure marks the batch failed so it is never left queued.
            try:
                for first in range(0, len(formatted_rows), 2000):
                    chunk = formatted_rows[first:first + 2000]
                    packed = gzip.compress(json.dumps(chunk, ensure_ascii=False).encode("utf-8"), compresslevel=5)
                    api.request("POST", f"/api/lead-batches/{batch_id}/rows", params={"first": first + 1},
                                data=packed, timeout=120)
                res = api.post(f"/api/lead-batches/{batch_id}/finish")
            except Exception as e:  # noqa: BLE001 - report it on the batch, then show it here
                try:
                    api.post(f"/api/lead-batches/{batch_id}/failed",
                             {"error": getattr(e, "message", str(e))[:500] or "The import stopped."})
                except Exception:  # noqa: BLE001
                    pass
                raise
            return res.get("batch") or preview["batch"]

        def on_done(batch):
            self.primary.busy(False)
            self.leads_imported.emit(batch)
            self.accept()

        def on_fail(err):
            self.primary.busy(False)
            self.show_error(err.message if hasattr(err, "message") else str(err))

        tasks.run(run_import, on_done=on_done, on_error=on_fail, owner=self)
