# -*- coding: utf-8 -*-
"""
Lead files read on the person's own computer: the app reads the file, sends
the column names, row count and a sample for the preview, then the rows in
gzip chunks in file order. The server only checks them against the lead bank
and stores them; the file itself never reaches the server.
"""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

from tests import helpers as h
from backend import db

HEADERS = ["title", "website", "phone", "street", "categoryName"]


def _site():
    return f"site-{uuid.uuid4().hex[:10]}.example"


class AppImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = h.client()
        cls.team = h.make_team()
        cls.manager = h.make_user("MANAGER")
        cls.lead = h.make_user("TEAM_LEAD", cls.team)
        cls.agent = h.make_user("AGENT", cls.team)
        cls.H = {n: h.login(cls.c, getattr(cls, n))[0] for n in ("manager", "lead", "agent")}

    def preview(self, rows, name="Roofers in Dallas TX.csv", who="manager"):
        return self.c.post("/api/lead-imports/local", headers=self.H[who], json={
            "file_name": name, "file_size": 1234, "headers": HEADERS, "total_rows": len(rows),
            "sample": [dict(zip(HEADERS, r)) for r in rows[:200]]})

    def send(self, batch_id, first, rows, who="manager"):
        packed = gzip.compress(json.dumps([dict(zip(HEADERS, r)) for r in rows]).encode("utf-8"))
        return self.c.post(f"/api/lead-batches/{batch_id}/rows?first={first}", data=packed, headers=self.H[who])

    def test_rows_read_on_the_pc_are_checked_and_stored(self):
        dup_site = _site()
        rows = [["Ace Roofing", dup_site, "555 0101", "1 Main St", "Roofer"],
                ["Ace Roofing again", dup_site, "", "", "Roofer"],           # repeated in this file
                ["No Site Roofing", "", "555 0103", "", "Roofer"],            # no website
                ["Best Roofs", _site(), "555 0104", "", "Roofer"]]
        r = self.preview(rows)
        self.assertEqual(r.status_code, 201, r.get_json())
        p = r.get_json()
        batch_id = p["batch"]["id"]
        self.assertEqual((p["batch"]["niche"], p["batch"]["area"]), ("Roofers", "Dallas TX"))
        self.assertEqual(p["mapping"]["website"], "website")
        self.assertEqual(p["batch"]["total_rows"], 4)
        self.assertIsNone(db.scalar("SELECT stored_name FROM lead_batches WHERE id = %s", (batch_id,)))

        r = self.c.post(f"/api/lead-batches/{batch_id}/start", headers=self.H["manager"],
                        json={"niche": "Roofers", "area": "Dallas TX", "team_id": self.team})
        self.assertEqual(r.status_code, 202, r.get_json())
        self.assertEqual(r.get_json()["batch"]["status"], "queued")  # the server waits for the app's rows

        self.assertEqual(self.send(batch_id, 3, rows[2:]).status_code, 409)  # out of order
        b = self.send(batch_id, 1, rows[:2]).get_json()["batch"]
        self.assertEqual((b["status"], b["processed_rows"], b["imported"], b["duplicates"]), ("processing", 2, 1, 1))
        b = self.send(batch_id, 1, rows[:2]).get_json()["batch"]  # sent again after a lost answer
        self.assertEqual((b["processed_rows"], b["imported"]), (2, 1))
        self.assertEqual(self.send(batch_id, 3, rows[2:], who="lead").status_code, 404)  # only the app that started it
        b = self.send(batch_id, 3, rows[2:]).get_json()["batch"]
        self.assertEqual((b["processed_rows"], b["imported"], b["no_website"]), (4, 2, 1))

        b = self.c.post(f"/api/lead-batches/{batch_id}/finish", headers=self.H["manager"]).get_json()["batch"]
        self.assertEqual(b["status"], "completed")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE batch_id = %s", (batch_id,)), 2)
        rejects = self.c.get(f"/api/lead-batches/{batch_id}/rejects", headers=self.H["manager"]).get_json()["items"]
        self.assertEqual(sorted(r["reason"] for r in rejects), ["duplicate_in_file", "no_website"])
        self.assertEqual(self.send(batch_id, 5, rows[:1]).status_code, 409)  # finished

        # Already in the lead bank: a second file with the same website is rejected.
        r = self.preview([["Ace Again", dup_site, "", "", "Roofer"]], name="Roofers in Austin TX.csv")
        again = r.get_json()["batch"]["id"]
        self.c.post(f"/api/lead-batches/{again}/start", headers=self.H["manager"],
                    json={"niche": "Roofers", "area": "Austin TX", "team_id": self.team})
        b = self.send(again, 1, [["Ace Again", dup_site, "", "", "Roofer"]]).get_json()["batch"]
        self.assertEqual((b["imported"], b["duplicates"]), (0, 1))
        b = self.c.post(f"/api/lead-batches/{again}/failed", json={"error": "The file is no longer on this computer."},
                        headers=self.H["manager"]).get_json()["batch"]
        self.assertEqual((b["status"], b["error"]), ("failed", "The file is no longer on this computer."))

    def test_server_never_reads_files_in_app_mode(self):
        import io
        from backend import config
        mode, config.AUDITS_RUN_ON = config.AUDITS_RUN_ON, "app"
        try:
            r = self.c.post("/api/lead-imports", data={"file": (io.BytesIO(b"title,website\nA,a.example\n"), "a.csv")},
                            headers=self.H["manager"], content_type="multipart/form-data")
            self.assertEqual((r.status_code, r.get_json()["code"]), (409, "read_in_app"))
        finally:
            config.AUDITS_RUN_ON = mode

    def test_preview_checks(self):
        self.assertEqual(self.c.post("/api/lead-imports/local", json={"file_name": "x.csv"}).status_code, 401)
        self.assertEqual(self.preview([["A", _site(), "", "", ""]], who="agent").status_code, 201)
        self.assertEqual(self.preview([["A", _site(), "", "", ""]], name="leads.exe").status_code, 422)
        r = self.c.post("/api/lead-imports/local", headers=self.H["manager"],
                        json={"file_name": "x.csv", "headers": HEADERS, "total_rows": 2_000_000, "sample": []})
        self.assertEqual(r.status_code, 422)
        bomb = gzip.compress(b"[" + b" " * (70 * 1024 * 1024) + b"]")
        batch_id = self.preview([["A", _site(), "", "", ""]]).get_json()["batch"]["id"]
        self.c.post(f"/api/lead-batches/{batch_id}/start", headers=self.H["manager"],
                    json={"niche": "N", "area": "A", "team_id": self.team})
        r = self.c.post(f"/api/lead-batches/{batch_id}/rows?first=1", data=bomb, headers=self.H["manager"])
        self.assertEqual(r.status_code, 413)


class LeadFileJobTests(unittest.TestCase):
    """The app's background job reads a lead file: a preview, then gzip chunks."""

    def run_job(self, job):
        folder = tempfile.mkdtemp(prefix="seo_job_")
        with open(os.path.join(folder, "job.json"), "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        proc = subprocess.run([sys.executable, "-m", "seo_audit_pro.pc_jobs", "--job", folder], cwd=h.ROOT,
                              timeout=300, capture_output=True)
        lines = [json.loads(x) for x in open(os.path.join(folder, "progress.jsonl"), encoding="utf-8")]
        return folder, proc.returncode, lines

    def test_preview_and_chunks(self):
        src = os.path.join(tempfile.mkdtemp(prefix="seo_leads_"), "Plumbers in Leeds.csv")
        with open(src, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(HEADERS) + "\n")
            for i in range(2500):
                fh.write(f"Company {i},site{i}.example,555 {i:04d},,Plumber\n")
        folder, code, lines = self.run_job({"kind": "leads-preview", "path": src})
        self.assertEqual(code, 0, lines)
        with open(os.path.join(folder, "preview.json"), encoding="utf-8") as fh:
            info = json.load(fh)
        self.assertEqual((info["headers"], info["total"], len(info["sample"])), (HEADERS, 2500, 200))

        folder, code, lines = self.run_job({"kind": "leads-rows", "path": src, "start_row": 1001})
        self.assertEqual(code, 0, lines)
        chunks = [x for x in lines if x["t"] == "chunk"]
        self.assertEqual([(c["first"], c["count"]) for c in chunks], [(1001, 1000), (2001, 500)])
        with open(os.path.join(folder, chunks[0]["file"]), "rb") as fh:
            first = json.loads(gzip.decompress(fh.read()))[0]
        self.assertEqual(first["title"], "Company 1000")
        self.assertEqual([x["t"] for x in lines[-2:]], ["rows", "done"])

        bad = src[:-4] + ".xls"
        with open(bad, "wb") as fh:
            fh.write(b"old excel")
        _folder, code, lines = self.run_job({"kind": "leads-preview", "path": bad})
        self.assertEqual(code, 1)
        self.assertIn(".xlsx", lines[-1]["text"])


if __name__ == "__main__":
    unittest.main()
