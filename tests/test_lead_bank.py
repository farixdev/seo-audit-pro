# -*- coding: utf-8 -*-
"""
Lead bank tests: scraped-file import (preview, niche/area guess, no-website
removal, duplicate detection against the file and the bank), rejected-row
reports, niche/area facets, filter summaries, select-all bulk assignment and
even split, export, permissions, and import throughput.
"""

import csv
import io
import time
import unittest
import uuid

from tests import helpers as h
from backend import db, lead_import


def _csv(rows, headers=("title", "website", "phone", "street", "categoryName", "totalScore", "reviewsCount", "url")):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _site():
    return f"site-{uuid.uuid4().hex[:10]}.example"


class LeadBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = h.client()
        cls.team_a = h.make_team()
        cls.team_b = h.make_team()
        cls.manager = h.make_user("MANAGER")
        cls.lead_a = h.make_user("TEAM_LEAD", cls.team_a)
        cls.agent_a1 = h.make_user("AGENT", cls.team_a)
        cls.agent_a2 = h.make_user("AGENT", cls.team_a)
        cls.agent_b1 = h.make_user("AGENT", cls.team_b)
        cls.H = {n: h.login(cls.c, getattr(cls, n))[0] for n in ("manager", "lead_a", "agent_a1", "agent_b1")}

    def upload(self, content, name="Dentists in Chicago IL.csv", who="manager"):
        r = self.c.post("/api/lead-imports", data={"file": (io.BytesIO(content), name)}, headers=self.H[who],
                        content_type="multipart/form-data")
        return r

    def run_import(self, preview, who="manager", **extra):
        batch = preview["batch"]
        payload = {"niche": batch["niche"] or "Dentists", "area": batch["area"] or "Chicago IL", **extra}
        r = self.c.post(f"/api/lead-batches/{batch['id']}/start", json=payload, headers=self.H[who])
        self.assertEqual(r.status_code, 202, r.get_json())
        for _ in range(600):
            b = self.c.get(f"/api/lead-batches/{batch['id']}", headers=self.H[who]).get_json()["batch"]
            if b["status"] in ("completed", "failed"):
                return b
            time.sleep(0.05)
        self.fail("import did not finish")

    def test_preview_detects_gmb_columns_and_guesses_niche_area(self):
        r = self.upload(_csv([["Smile Dental", _site(), "555 0100", "1 Main St", "Dentist", "4.8", "120", "https://maps.google.com/?cid=1"]]))
        self.assertEqual(r.status_code, 201, r.get_json())
        p = r.get_json()
        self.assertEqual(p["mapping"]["website"], "website")
        self.assertEqual(p["mapping"]["company"], "title")
        self.assertEqual(p["mapping"]["rating"], "totalScore")
        self.assertEqual(p["mapping"]["review_count"], "reviewsCount")
        self.assertEqual(p["mapping"]["maps_url"], "url")
        self.assertEqual((p["batch"]["niche"], p["batch"]["area"]), ("Dentists", "Chicago IL"))
        self.assertEqual(p["batch"]["status"], "pending")
        # Nothing is imported until the batch is started.
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE batch_id = %s", (p["batch"]["id"],)), 0)

    def test_import_removes_rows_without_website_and_duplicates(self):
        existing = _site()
        existing_phone_site = _site()
        self.c.post("/api/leads", json={"company": "Already Here", "website": existing}, headers=self.H["manager"])
        self.c.post("/api/leads", json={"company": "Phone Owner", "website": existing_phone_site, "phone": "+1 (312) 555-0199"},
                    headers=self.H["manager"])
        fresh1, fresh2 = _site(), _site()
        rows = [
            ["Fresh One", f"https://www.{fresh1}/", "312-555-1111", "", "Dentist", "4.5", "10", ""],
            ["No Site Dental", "", "312-555-2222", "", "Dentist", "", "", ""],
            ["Repeat of One", f"http://{fresh1}", "", "", "Dentist", "", "", ""],
            ["Existing", f"https://{existing}", "", "", "Dentist", "", "", ""],
            ["Same Phone", f"https://{_site()}", "(312) 555-0199", "", "Dentist", "", "", ""],
            ["Maps Only", "https://www.google.com/maps/place/x", "", "", "Dentist", "", "", ""],
            ["Fresh Two", fresh2, "", "", "Dentist", "4,9", "1,204", ""],
        ]
        p = self.upload(_csv(rows), name="dentists_in_austin_tx.csv").get_json()
        done = self.run_import(p, team_id=self.team_a)
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(done["processed_rows"], 7)
        self.assertEqual(done["imported"], 2)
        self.assertEqual(done["no_website"], 1)
        self.assertEqual(done["invalid"], 1)
        self.assertEqual(done["duplicates"], 3)
        rej = self.c.get(f"/api/lead-batches/{done['id']}/rejects", headers=self.H["manager"]).get_json()["items"]
        reasons = {r["company"]: r["reason"] for r in rej}
        self.assertEqual(reasons["No Site Dental"], "no_website")
        self.assertEqual(reasons["Repeat of One"], "duplicate_in_file")
        self.assertEqual(reasons["Existing"], "duplicate_existing")
        self.assertEqual(reasons["Same Phone"], "duplicate_existing")
        self.assertEqual(reasons["Maps Only"], "invalid_website")
        csv_text = self.c.get(f"/api/lead-batches/{done['id']}/rejects.csv", headers=self.H["manager"]).data.decode("utf-8-sig")
        self.assertIn("Already in lead bank", csv_text)
        lead = db.query_one("SELECT * FROM leads WHERE normalized_domain = %s", (fresh2,))
        self.assertEqual(lead["niche"], "Dentists")
        self.assertEqual(lead["area"], "Austin TX")
        self.assertEqual(lead["rating"], 4.9)
        self.assertEqual(lead["review_count"], 1204)
        self.assertEqual(lead["assigned_team_id"], self.team_a)
        # Re-importing the same file finds only duplicates.
        again = self.run_import(self.upload(_csv(rows), name="dentists_in_austin_tx.csv").get_json(), team_id=self.team_a)
        self.assertEqual(again["imported"], 0)

    def test_facets_summary_and_filters(self):
        niche = f"Roofers {uuid.uuid4().hex[:4]}"
        rows = [[f"Roof {i}", _site(), "", "", "", "", "", ""] for i in range(5)]
        p = self.upload(_csv(rows), name=f"{niche} - Dallas TX.csv").get_json()
        self.run_import(p, team_id=self.team_a)
        facets = self.c.get("/api/leads/facets", headers=self.H["manager"]).get_json()
        # Match on the key: the label is title-cased from the file name ("ab12" → "Ab12").
        node = next(n for n in facets["niches"] if n["key"] == niche.lower())
        self.assertEqual(node["count"], 5)
        self.assertEqual(node["areas"][0]["label"], "Dallas TX")
        flt = {"niche": niche.lower(), "area": "dallas tx"}
        listed = self.c.get("/api/leads", query_string=flt, headers=self.H["manager"]).get_json()
        self.assertEqual(listed["total"], 5)
        summary = self.c.get("/api/leads/summary", query_string=flt, headers=self.H["manager"]).get_json()
        self.assertEqual((summary["total"], summary["unassigned"], summary["audited"]), (5, 5, 0))
        # Team B's agent cannot see them anywhere.
        other = self.c.get("/api/leads/facets", headers=self.H["agent_b1"]).get_json()
        self.assertFalse(any(n["label"] == niche for n in other["niches"]))

    def test_select_all_matching_split_and_scope(self):
        niche = f"Plumbers {uuid.uuid4().hex[:4]}"
        rows = [[f"Pipe {i}", _site(), "", "", "", "", "", ""] for i in range(10)]
        self.run_import(self.upload(_csv(rows), name=f"{niche} in Leeds.csv").get_json(), team_id=self.team_a)
        flt = {"niche": niche.lower()}
        r = self.c.post("/api/leads/bulk-assign", json={"filter": flt, "expected_count": 9, "mode": "split",
                                                        "team_id": self.team_a}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["count"], 10)
        r = self.c.post("/api/leads/bulk-assign", json={"filter": flt, "expected_count": 10, "mode": "split",
                                                        "team_id": self.team_a, "reason": "Even split"},
                        headers=self.H["lead_a"])
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["updated"], 10)
        self.assertEqual(sorted(r.get_json()["per_agent"].values()), [5, 5])
        hist = db.scalar("SELECT COUNT(*) FROM lead_assignments WHERE reason = 'Even split'")
        self.assertEqual(hist, 10)
        mine = self.c.get("/api/leads", query_string=flt, headers=self.H["agent_a1"]).get_json()
        self.assertEqual(mine["total"], 5)
        # Team Lead cannot pull another team's leads in through a filter.
        r = self.c.post("/api/leads/bulk-assign", json={"filter": flt, "expected_count": 10, "team_id": self.team_b,
                                                        "agent_id": self.agent_b1["id"]}, headers=self.H["lead_a"])
        self.assertEqual(r.status_code, 403)

    def test_export_and_permissions(self):
        niche = f"Bakers {uuid.uuid4().hex[:4]}"
        self.run_import(self.upload(_csv([["Bread Co", _site(), "", "", "", "", "", ""]]), name=f"{niche} in York.csv").get_json())
        out = self.c.get("/api/leads/export", query_string={"niche": niche.lower()}, headers=self.H["manager"])
        self.assertEqual(out.status_code, 200)
        text = out.data.decode("utf-8-sig")
        self.assertIn("Bread Co", text)
        self.assertEqual(len(text.strip().splitlines()), 2)
        self.assertEqual(self.upload(_csv([["X", _site(), "", "", "", "", "", ""]]), who="agent_a1").status_code, 403)
        self.assertEqual(self.c.get("/api/leads/export", headers=self.H["agent_a1"]).status_code, 403)
        self.assertEqual(self.c.get("/api/lead-batches", headers=self.H["agent_a1"]).status_code, 403)
        bad = self.upload(b"\x89PNG not a csv", name="leads.png")
        self.assertEqual(bad.status_code, 422)
        no_rows = self.upload(b"title,website\n", name="empty in Leeds.csv")
        self.assertEqual(no_rows.status_code, 422)

    def test_large_import_throughput(self):
        n = 20000
        rows = [[f"Shop {i}", f"shop-{i}-{uuid.uuid4().hex[:6]}.example", f"555{i:07d}", "", "", "", "", ""]
                for i in range(n)]
        p = self.upload(_csv(rows), name="Florists in Paris.csv").get_json()
        t0 = time.time()
        done = self.run_import(p)
        took = time.time() - t0
        self.assertEqual(done["imported"], n)
        self.assertLess(took, 90, f"20k rows took {took:.1f}s")
        page = self.c.get("/api/leads", query_string={"niche": "florists", "page": 50, "page_size": 100},
                          headers=self.H["manager"]).get_json()
        self.assertEqual(page["total"], n)
        self.assertEqual(len(page["items"]), 100)
        print(f"\n  20,000-row import: {took:.1f}s")


class GuessTests(unittest.TestCase):
    def test_file_name_guesses(self):
        m = {}
        self.assertEqual(lead_import.guess_niche_area("Dentists in Chicago IL.csv", [], m), ("Dentists", "Chicago IL"))
        self.assertEqual(lead_import.guess_niche_area("gmb_scrape_plumbers_-_london.csv", [], m), ("Plumbers", "London"))
        self.assertEqual(lead_import.guess_niche_area("roofers-dallas-tx.xlsx", [], m), ("Roofers", "Dallas TX"))


if __name__ == "__main__":
    unittest.main()
