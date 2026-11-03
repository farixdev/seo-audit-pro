# -*- coding: utf-8 -*-
"""Dialog to directly import Map Harvest scraped leads into the Lead Bank."""

import gzip
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
)

from seo_audit_pro.core import api, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C
from seo_audit_pro.widgets.common import label, vbox
from seo_audit_pro.widgets.feedback import Dialog


def _clean_str(val):
    return str(val or "").strip()


class AddToLeadBankDialog(Dialog):
    leads_imported = Signal(dict)

    def __init__(self, parent, results, default_domain="", default_area="", default_country="", options=None):
        self.results = list(results or [])
        count = len(self.results)
        super().__init__(
            parent,
            "Add Scraped Leads to Lead Bank",
            f"Import {count:,} scraped business{'es' if count != 1 else ''} directly into the lead bank. "
            "Choose or confirm the Country, Area, and Niche to store them in your Lead Bank and Tracker.",
            540,
        )

        # 1. Fetch Scrape Planner / Tracker data and Lead Bank facets to inspect existing hierarchy
        self.tree = {}
        try:
            p_data = api.get("/api/scrape-planner")
            for c in p_data.get("countries", []):
                cname = str(c.get("name", "")).strip()
                if cname:
                    self.tree[cname.lower()] = {"id": c.get("id"), "name": cname, "areas": {}}

            for a in p_data.get("areas", []):
                aname = str(a.get("name", "")).strip()
                cid = a.get("country_id")
                for c_obj in self.tree.values():
                    if c_obj.get("id") == cid:
                        c_obj["areas"][aname.lower()] = {"id": a.get("id"), "name": aname, "targets": {}}

            for t in p_data.get("targets", []):
                aid = t.get("area_id")
                tniche = str(t.get("niche", "")).strip()
                for c_obj in self.tree.values():
                    for a_obj in c_obj["areas"].values():
                        if a_obj.get("id") == aid:
                            a_obj["targets"][tniche.lower()] = {
                                "id": t.get("id"),
                                "niche": tniche,
                                "leads_harvested": t.get("leads_harvested", 0),
                            }
        except Exception:
            pass

        # Also merge facets from lead bank
        try:
            f_data = api.get("/api/leads/facets")
            for c_entry in f_data.get("hierarchy", []):
                clabel = str(c_entry.get("label", "")).strip()
                if not clabel or clabel.lower() == "other / unspecified":
                    continue
                c_obj = self.tree.setdefault(clabel.lower(), {"id": None, "name": clabel, "areas": {}})
                for a_entry in c_entry.get("areas", []):
                    alabel = str(a_entry.get("label", "")).strip()
                    if not alabel:
                        continue
                    a_obj = c_obj["areas"].setdefault(alabel.lower(), {"id": None, "name": alabel, "targets": {}})
                    for n_entry in a_entry.get("niches", []):
                        nlabel = str(n_entry.get("label", "")).strip()
                        if not nlabel:
                            continue
                        if nlabel.lower() not in a_obj["targets"]:
                            a_obj["targets"][nlabel.lower()] = {
                                "id": None,
                                "niche": nlabel,
                                "leads_harvested": n_entry.get("count", 0),
                            }
        except Exception:
            pass

        # 2. Resolve initial country, area, and niche values
        initial_domain = _clean_str(default_domain)
        initial_area = _clean_str(default_area)
        initial_country = _clean_str(default_country)

        # If area contains comma (e.g. "Albany, Australia" or "Dallas, TX, United States")
        if "," in initial_area:
            parts = [p.strip() for p in initial_area.split(",") if p.strip()]
            if len(parts) >= 2:
                initial_area = parts[0]
                if not initial_country:
                    initial_country = parts[-1]

        # If country still not determined, try to find country by matching area in tree
        if not initial_country and initial_area:
            for c_low, c_obj in self.tree.items():
                if initial_area.lower() in c_obj.get("areas", {}):
                    initial_country = c_obj["name"]
                    break

        if not initial_country:
            initial_country = "Australia" if "australia" in self.tree else (
                next((c["name"] for c in self.tree.values()), "United States")
            )

        # 3. Build UI Form
        fl = vbox(spacing=12)

        # Country combo
        fl.addWidget(label("Country", "FieldLabel"))
        self.country_combo = QComboBox()
        self.country_combo.setEditable(True)
        self.country_combo.setFixedHeight(34)
        self.country_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.country_combo.lineEdit():
            self.country_combo.lineEdit().setPlaceholderText("Select or enter country (e.g. Australia)")
        fl.addWidget(self.country_combo)

        # Area combo
        fl.addWidget(label("Area / City", "FieldLabel"))
        self.area_combo = QComboBox()
        self.area_combo.setEditable(True)
        self.area_combo.setFixedHeight(34)
        self.area_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.area_combo.lineEdit():
            self.area_combo.lineEdit().setPlaceholderText("Select or enter area / city (e.g. Albany)")
        fl.addWidget(self.area_combo)

        # Niche combo
        fl.addWidget(label("Niche / Category", "FieldLabel"))
        self.niche_combo = QComboBox()
        self.niche_combo.setEditable(True)
        self.niche_combo.setFixedHeight(34)
        self.niche_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.niche_combo.lineEdit():
            self.niche_combo.lineEdit().setPlaceholderText("Select or enter niche (e.g. Dentists)")
        fl.addWidget(self.niche_combo)

        # 4. Target Match / Existence Status Card
        self.status_card = QFrame()
        self.status_card.setObjectName("MatchStatusCard")
        sc_lay = vbox(self.status_card, (10, 10, 10, 10), 4)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"font-size: 12px; color: {C['text_2']}; line-height: 1.4;")
        sc_lay.addWidget(self.status_label)
        fl.addWidget(self.status_card)

        self.body.addLayout(fl)
        self.add_buttons(f"Add {count:,} Leads")
        self.primary.clicked.connect(self.submit)

        # Populate countries
        country_names = [c["name"] for c in self.tree.values() if c.get("name")]
        defaults = ["Australia", "United States", "United Kingdom", "Canada"]
        for d in defaults:
            if d not in country_names:
                country_names.append(d)
        self.country_combo.addItems(country_names)

        # Set initial country
        if initial_country:
            idx = self.country_combo.findText(initial_country, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.country_combo.setCurrentIndex(idx)
            else:
                self.country_combo.setEditText(initial_country)

        self._populate_areas(initial_area)
        self._populate_niches(initial_domain)

        # Connect change signals
        self.country_combo.currentTextChanged.connect(self._on_country_changed)
        self.area_combo.currentTextChanged.connect(self._on_area_changed)
        self.niche_combo.currentTextChanged.connect(self._on_niche_changed)

        self._update_match_status()

    def _populate_areas(self, preserve_text=""):
        cur_country = self.country_combo.currentText().strip().lower()
        c_obj = self.tree.get(cur_country)
        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        if c_obj:
            areas = [a.get("name", "").strip() for a in c_obj.get("areas", {}).values() if a.get("name")]
            self.area_combo.addItems(areas)
        if preserve_text:
            idx = self.area_combo.findText(preserve_text, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.area_combo.setCurrentIndex(idx)
            else:
                self.area_combo.setEditText(preserve_text)
        elif self.area_combo.count() > 0:
            self.area_combo.setCurrentIndex(0)
        self.area_combo.blockSignals(False)

    def _populate_niches(self, preserve_text=""):
        cur_country = self.country_combo.currentText().strip().lower()
        cur_area = self.area_combo.currentText().strip().lower()
        c_obj = self.tree.get(cur_country)
        niches = []
        if c_obj:
            a_obj = c_obj.get("areas", {}).get(cur_area)
            if a_obj:
                niches = [t.get("niche", "").strip() for t in a_obj.get("targets", {}).values() if t.get("niche")]
        self.niche_combo.blockSignals(True)
        self.niche_combo.clear()
        if niches:
            self.niche_combo.addItems(niches)
        if preserve_text:
            idx = self.niche_combo.findText(preserve_text, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.niche_combo.setCurrentIndex(idx)
            else:
                self.niche_combo.setEditText(preserve_text)
        elif self.niche_combo.count() > 0:
            self.niche_combo.setCurrentIndex(0)
        self.niche_combo.blockSignals(False)

    def _on_country_changed(self, _text):
        cur_area = self.area_combo.currentText()
        self._populate_areas(cur_area)
        self._populate_niches(self.niche_combo.currentText())
        self._update_match_status()

    def _on_area_changed(self, _text):
        self._populate_niches(self.niche_combo.currentText())
        self._update_match_status()

    def _on_niche_changed(self, _text):
        self._update_match_status()

    def _update_match_status(self):
        c_name = self.country_combo.currentText().strip()
        a_name = self.area_combo.currentText().strip()
        n_name = self.niche_combo.currentText().strip()

        if not c_name:
            self.status_label.setText("Select or enter a country to continue.")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        c_obj = self.tree.get(c_name.lower())
        if not c_obj:
            self.status_label.setText(f"New country: <b>{c_name}</b><br><span style='color: {C['text_3']};'>Will create this country branch in your Tracker and Lead Bank.</span>")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        if not a_name:
            self.status_label.setText(f"Country exists: <b>{c_obj['name']}</b><br><span style='color: {C['text_3']};'>Select or enter an area/city under this country.</span>")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        a_obj = c_obj.get("areas", {}).get(a_name.lower())
        if not a_obj:
            self.status_label.setText(f"Country exists: <b>{c_obj['name']}</b> &bull; New area: <b>{a_name}</b><br><span style='color: {C['text_3']};'>Will create this area under {c_obj['name']} in Tracker and Lead Bank.</span>")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        if not n_name:
            self.status_label.setText(f"Area exists: <b>{c_obj['name']} › {a_obj['name']}</b><br><span style='color: {C['text_3']};'>Select or enter a niche category.</span>")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        t_obj = a_obj.get("targets", {}).get(n_name.lower())
        if not t_obj:
            self.status_label.setText(f"Area exists: <b>{c_obj['name']} › {a_obj['name']}</b> &bull; New niche: <b>{n_name}</b><br><span style='color: {C['text_3']};'>Will add this new niche node to {a_obj['name']} in Tracker and Lead Bank.</span>")
            self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}")
            return

        # Target exists!
        prev_leads = t_obj.get("leads_harvested", 0)
        self.status_label.setText(f"Target exists: <b>{c_obj['name']} › {a_obj['name']} › {t_obj['niche']}</b> ({prev_leads:,} existing leads)<br><span style='color: {C['success']};'>Scraped leads will be merged directly into this node in your Tracker and Lead Bank.</span>")
        self.status_card.setStyleSheet(f"QFrame#MatchStatusCard {{ background: {C['success_bg']}; border: 1px solid {C['success']}; border-radius: 6px; }}")

    def submit(self):
        country = self.country_combo.currentText().strip()
        area = self.area_combo.currentText().strip()
        niche = self.niche_combo.currentText().strip()

        if not country:
            self.show_error("Please enter or select a country.")
            self.country_combo.setFocus()
            return
        if not area:
            self.show_error("Please enter or select an area or city.")
            self.area_combo.setFocus()
            return
        if not niche:
            self.show_error("Please enter or select a niche or business category.")
            self.niche_combo.setFocus()
            return
        if not self.results:
            self.show_error("No scraped results to import.")
            return

        # Team selection check removed
        team_id = None
        agent_id = (session.user or {}).get("id") if session.is_agent() else None

        self.clear_error()
        self.primary.busy(True, "Importing to Bank…")

        def run_import():
            headers = ["company", "website", "phone", "email", "address", "category",
                       "rating", "review_count", "maps_url", "city", "country"]
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
                    "country": str(r.get("country") or country).strip(),
                })

            # 1. Create pending batch
            preview = api.post("/api/lead-imports/local", {
                "file_name": f"MapHarvest - {country} - {area} - {niche}.csv",
                "headers": headers,
                "total_rows": len(formatted_rows),
                "file_size": max(1024, len(formatted_rows) * 120),
                "sample": formatted_rows[:200],
                "niche": niche,
                "area": area,
            })
            batch_id = preview["batch"]["id"]
            mapping = preview.get("mapping") or {}

            # 2. Start batch with mapping
            map_copy = dict(mapping or {})
            map_copy["__allow_no_website__"] = True
            api.post(f"/api/lead-batches/{batch_id}/start", {
                "niche": niche,
                "area": area,
                "mapping": map_copy,
                "team_id": team_id,
                "agent_id": agent_id,
            }, idempotency_key=api.new_idempotency_key())

            # 3. Send rows
            try:
                for first in range(0, len(formatted_rows), 2000):
                    chunk = formatted_rows[first:first + 2000]
                    packed = gzip.compress(json.dumps(chunk, ensure_ascii=False).encode("utf-8"), compresslevel=5)
                    api.request("POST", f"/api/lead-batches/{batch_id}/rows", params={"first": first + 1},
                                data=packed, timeout=120)
                res = api.post(f"/api/lead-batches/{batch_id}/finish")
            except Exception as e:
                try:
                    api.post(f"/api/lead-batches/{batch_id}/failed",
                             {"error": getattr(e, "message", str(e))[:500] or "The import stopped."})
                except Exception:
                    pass
                raise

            batch_data = res.get("batch") or preview["batch"]
            imported_count = batch_data.get("imported", len(formatted_rows))

            # 4. Sync / update with Scrape Tracker (Country -> Area -> Niche)
            try:
                from backend.util import now_iso
                p_data = api.get("/api/scrape-planner")
                all_c = p_data.get("countries", [])
                c_match = next((c for c in all_c if c.get("name", "").strip().lower() == country.lower()), None)
                if not c_match:
                    c_res = api.post("/api/scrape-planner/countries", {"name": country})
                    cid = c_res.get("country", {}).get("id")
                    c_match = {"id": cid, "name": country, "areas": []}
                else:
                    cid = c_match.get("id")

                # Re-fetch areas to ensure clean lookup
                all_areas = p_data.get("areas", [])
                a_match = next((a for a in all_areas if a.get("country_id") == cid and a.get("name", "").strip().lower() == area.lower()), None)
                if not a_match:
                    a_res = api.post("/api/scrape-planner/areas", {"country_id": cid, "name": area})
                    aid = a_res.get("area", {}).get("id")
                else:
                    aid = a_match.get("id")

                all_targets = p_data.get("targets", [])
                t_match = next((t for t in all_targets if t.get("area_id") == aid and t.get("niche", "").strip().lower() == niche.lower()), None)
                if not t_match:
                    t_res = api.post("/api/scrape-planner/targets", {
                        "area_id": aid,
                        "niche": niche,
                        "status": "completed",
                    })
                    tid = t_res.get("target", {}).get("id")
                else:
                    tid = t_match.get("id")

                if tid:
                    api.patch(f"/api/scrape-planner/targets/{tid}", {
                        "status": "completed",
                        "is_scraped": True,
                        "leads_harvested": imported_count,
                        "last_scraped_at": now_iso(),
                    })
            except Exception:
                pass

            return batch_data

        def on_done(batch):
            self.primary.busy(False)
            self.leads_imported.emit(batch)
            self.accept()

        def on_fail(err):
            self.primary.busy(False)
            self.show_error(err.message if hasattr(err, "message") else str(err))

        tasks.run(run_import, on_done=on_done, on_error=on_fail, owner=self)
