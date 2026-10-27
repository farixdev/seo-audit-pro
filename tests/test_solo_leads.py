# -*- coding: utf-8 -*-
"""My Leads (Scrape & Audit without an account): the local lead store."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SoloLeadsTests(unittest.TestCase):
    def setUp(self):
        from seo_audit_pro import solo_db
        self.db = solo_db
        self.tmp = tempfile.mkdtemp()
        self._orig = solo_db.db_path
        solo_db.db_path = lambda: os.path.join(self.tmp, "leads.db")

    def tearDown(self):
        self.db.db_path = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dedupe_fill_and_skip(self):
        r = self.db.add_many([
            {"name": "Joe Plumbing", "website": "https://www.joes.com/", "phone": "1", "rating": "4.6", "review_count": "1,204"},
            {"company": "Joe again", "website": "joes.com", "address": "12 Main St"},
            {"company": "Social only", "website": "https://facebook.com/x"},
            {"company": "", "website": ""},
        ], "scrape", "Plumbers", "Dallas")
        self.assertEqual(r, {"added": 2, "updated": 1, "skipped": 1})
        joe = next(x for x in self.db.list_leads() if x["domain"] == "joes.com")
        self.assertEqual(joe["company"], "Joe Plumbing")          # existing value kept
        self.assertEqual(joe["address"], "12 Main St")            # empty value filled
        self.assertEqual((joe["rating"], joe["reviews"]), (4.6, 1204))
        social = next(x for x in self.db.list_leads() if x["company"] == "Social only")
        self.assertEqual(social["domain"], "")                    # a Facebook page is not a website to audit

    def test_audits_update_leads_and_stats(self):
        self.db.add_many([{"company": "A", "website": "a.com", "niche": "Dentists"},
                          {"company": "B", "website": "b.com", "niche": "Dentists"}])
        self.db.set_status(["a.com", "b.com"], "queued")
        self.assertEqual(len(self.db.list_leads(status="queued")), 2)
        self.db.record_audit("https://www.a.com", True, 85, "A", "/r.pdf")
        self.db.record_audit("b.com", False, error="blocked")
        self.db.record_audit("https://pasted.com", True, 40, "F", "/p.pdf", business="Pasted")
        rows = {x["domain"]: x for x in self.db.list_leads()}
        self.assertEqual((rows["a.com"]["status"], rows["a.com"]["score"]), ("audited", 85))
        self.assertEqual((rows["b.com"]["status"], rows["b.com"]["error"]), ("failed", "blocked"))
        self.assertEqual(rows["pasted.com"]["company"], "Pasted")    # a pasted website becomes a lead
        s = self.db.stats()
        self.assertEqual((s["total"], s["audited"], s["failed"], s["good"], s["poor"]), (3, 2, 1, 1, 1))
        self.assertEqual(s["avg_score"], 62)
        self.assertEqual(len(self.db.list_leads(status="not_audited")), 1)
        self.assertEqual(self.db.facets()["niches"][0], {"value": "Dentists", "n": 2})

    def test_csv_import_and_export(self):
        path = os.path.join(self.tmp, "roofers_in_austin_tx.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Business Name,Website,Phone,Rating\nTop Roof,toproof.com,555,4.9\nNo Site Roof,,556,4\n")
        rows, mapping, niche, area = self.db.read_file(path)
        self.assertEqual((mapping["company"], mapping["website"]), ("Business Name", "Website"))
        self.assertEqual((niche, area), ("Roofers", "Austin TX"))
        self.assertEqual(self.db.add_many(rows, "file", niche, area)["added"], 2)
        out = self.db.export_csv(os.path.join(self.tmp, "out.csv"), self.db.list_leads())
        with open(out, encoding="utf-8-sig") as fh:
            text = fh.read()
        self.assertIn("Top Roof", text)
        self.assertIn("No Site Roof", text)


if __name__ == "__main__":
    unittest.main()
