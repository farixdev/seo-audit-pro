# -*- coding: utf-8 -*-
"""
Audits and report files made on each person's own computer (AUDITS_RUN_ON =
"app"): the app claims the audits its user queued, sends the engine's progress
and the result, builds report files itself and uploads one only to email it.
The server stores and shares; it never runs the engine or builds a file.
"""

import gzip
import io
import json
import os
import unittest
from unittest import mock

from tests import helpers as h
from backend import audit_runner, config, db, mailer, reports
from tests.test_api import Base, _stored_real_result

PC1 = {"X-Device-Id": "pc-one-0000000001"}
PC2 = {"X-Device-Id": "pc-two-0000000002"}


class AppRunnerTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._mode = config.AUDITS_RUN_ON
        config.AUDITS_RUN_ON = "app"
        cls._validate = audit_runner.validate_public_target
        audit_runner.validate_public_target = lambda url: None

    @classmethod
    def tearDownClass(cls):
        # Audits left waiting or "running in an app" would never finish under the server's runner.
        db.execute("UPDATE audits SET status = 'cancelled', step = 'Cancelled', completed_at = %s "
                   "WHERE status IN ('queued', 'running')", ("2000-01-01T00:00:00.000Z",))
        config.AUDITS_RUN_ON = cls._mode
        audit_runner.validate_public_target = cls._validate

    def h(self, who, device=PC1):
        return dict(self.H[who], **device)

    def start(self, who="agent_a1"):
        lead = self.create_lead(who)
        r = self.c.post("/api/audits", json={"lead_id": lead["id"]}, headers=self.H[who])
        self.assertEqual(r.status_code, 202, r.get_json())
        return lead, r.get_json()["audit"]["id"]

    def claim(self, who="agent_a1", device=PC1, restart=False):
        r = self.c.post("/api/audit-jobs/claim", json={"restart": restart}, headers=self.h(who, device))
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def audit(self, audit_id, who="agent_a1"):
        return self.c.get(f"/api/audits/{audit_id}", headers=self.H[who]).get_json()["audit"]

    def send_result(self, audit_id, result, who="agent_a1", device=PC1):
        packed = gzip.compress(json.dumps(result).encode("utf-8"))
        return self.c.post(f"/api/audit-jobs/{audit_id}/result", data=packed, headers=self.h(who, device),
                           content_type="application/gzip")

    def test_audit_runs_in_the_app_and_reports_are_made_there(self):
        lead, audit_id = self.start()
        self.assertEqual(self.audit(audit_id)["status"], "queued")
        self.assertIn("computer", self.audit(audit_id)["step"])
        # Only the person who queued it gets the job, on the computer that asks.
        self.assertEqual(self.claim("agent_a2")["jobs"], [])
        job = self.claim()["jobs"]
        self.assertEqual([j["audit_id"] for j in job], [audit_id])
        self.assertTrue(job[0]["url"].startswith("https://"))
        self.assertNotIn("psi_key", job[0])
        self.assertEqual(self.claim()["jobs"], [])  # already running

        # Progress: the server turns engine messages into percent / step.
        r = self.c.post(f"/api/audit-jobs/{audit_id}/progress", headers=self.h("agent_a1"),
                        json={"messages": ["Fetching homepage…", "Crawled 5/10 pages (50%), 12 URLs discovered"]})
        self.assertEqual(r.get_json(), {"stop": False})
        a = self.audit(audit_id)
        self.assertEqual((a["status"], a["pages_processed"], a["pages_discovered"]), ("running", 5, 12))
        self.assertGreaterEqual(a["percent"], 12)
        self.assertTrue(any("Crawled 5/10" in line for line in a["logs"]))
        # Another computer of the same person may not report for it.
        r = self.c.post(f"/api/audit-jobs/{audit_id}/progress", headers=self.h("agent_a1", PC2), json={"messages": []})
        self.assertEqual(r.get_json()["stop"], True)
        self.assertEqual(self.send_result(audit_id, _stored_real_result(), device=PC2).status_code, 409)
        # Nobody else can report for it.
        r = self.c.post(f"/api/audit-jobs/{audit_id}/progress", headers=self.h("agent_a2"), json={"messages": []})
        self.assertEqual(r.status_code, 404)

        # The result.
        real = _stored_real_result()
        r = self.send_result(audit_id, real)
        self.assertEqual(r.status_code, 200, r.get_json())
        a = self.audit(audit_id)
        self.assertEqual((a["status"], a["score"]), ("completed", real["score"]["score"]))
        lead_now = self.c.get(f"/api/leads/{lead['id']}", headers=self.H["agent_a1"]).get_json()["lead"]
        self.assertEqual((lead_now["status"], lead_now["last_audit_score"]), ("audited", real["score"]["score"]))
        self.assertEqual(self.send_result(audit_id, real).status_code, 409)  # only once

        # Reports are listed, but no file was made on the server.
        listing = self.c.get(f"/api/reports?audit_id={audit_id}", headers=self.H["agent_a1"]).get_json()
        self.assertTrue(listing["made_in_app"])
        reps = {(r["kind"], r["format"]): r for r in listing["items"]}
        self.assertEqual(set(reps), {("client", "docx"), ("client", "pdf"), ("technical", "docx"), ("technical", "pdf")})
        self.assertFalse(any(r["ready"] for r in reps.values()))
        self.assertEqual([n for n in os.listdir(config.REPORTS_DIR) if n.startswith(audit_id)], [])
        docx = reps[("client", "docx")]
        r = self.c.get(f"/api/reports/{docx['id']}/download", headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 409)
        info = r.get_json()
        self.assertEqual((info["code"], info["audit_id"], info["kind"], info["format"]),
                         ("build_in_app", audit_id, "client", "docx"))
        self.assertTrue(info["download_name"].endswith(".docx"))
        self.assertEqual(self.c.get(f"/api/reports/{docx['id']}/download", headers=self.H["agent_b1"]).status_code, 404)

        # The app reads the stored result to build the file.
        r = self.c.get(f"/api/audits/{audit_id}/result-data", headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(gzip.decompress(r.data))["score"], real["score"])
        self.assertEqual(self.c.get(f"/api/audits/{audit_id}/result-data", headers=self.H["agent_b1"]).status_code, 404)

        # An emailed report needs the app's copy first.
        smtp = h.FakeSMTP()
        try:
            self.c.put("/api/outreach/smtp", json={"host": "127.0.0.1", "port": smtp.port, "encryption": "none",
                                                   "username": "", "password": "x", "from_email": "team@agency.test",
                                                   "from_name": "Agency"}, headers=self.H["manager"])
            em = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
                "lead_id": lead["id"], "to_email": "owner@client-site.com", "subject": "Your audit", "body": "Hi",
                "audit_id": audit_id, "report_ids": [docx["id"]]})
            self.assertEqual(em.status_code, 201, em.get_json())
            eid = em.get_json()["email"]["id"]
            r = self.c.post(f"/api/outreach/emails/{eid}/send", headers=self.H["agent_a1"])
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.get_json()["code"], "report_not_uploaded")
            self.assertEqual([x["id"] for x in r.get_json()["reports"]], [docx["id"]])

            self.assertEqual(self.c.put(f"/api/reports/{docx['id']}/file", data=b"not a report",
                                        headers=self.H["agent_a1"]).status_code, 422)
            fake_docx = b"PK\x03\x04" + b"word/" * 50
            r = self.c.put(f"/api/reports/{docx['id']}/file", data=fake_docx, headers=self.H["agent_a1"])
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()["report"]["ready"], 1)
            self.assertEqual(self.c.put(f"/api/reports/{docx['id']}/file", data=fake_docx,
                                        headers=self.H["agent_b1"]).status_code, 404)
            # Once ready, only the person who ran the audit (or a Manager) may replace the file.
            self.assertEqual(self.c.put(f"/api/reports/{docx['id']}/file", data=fake_docx,
                                        headers=self.H["lead_a"]).status_code, 403)
            r = self.c.post(f"/api/outreach/emails/{eid}/send", headers=self.H["agent_a1"])
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(mailer._process_one(), "sent")
            self.assertIn("SEO-Audit-Report.docx", smtp.messages[-1]["data"])
        finally:
            smtp.close()

        # Shared with teammates: the server serves the copy, also after its disk was wiped.
        os.remove(os.path.join(config.REPORTS_DIR, docx["stored_name"] if "stored_name" in docx else
                               db.scalar("SELECT stored_name FROM reports WHERE id = %s", (docx["id"],))))
        r = self.c.get(f"/api/reports/{docx['id']}/download", headers=self.H["lead_a"])
        self.assertEqual((r.status_code, r.data), (200, fake_docx))
        r.close()

        # Regenerating drops the copies; the apps build the files again.
        r = self.c.post(f"/api/audits/{audit_id}/reports", headers=self.H["agent_a1"])
        self.assertEqual(r.get_json()["made_in_app"], True)
        self.assertEqual(self.c.get(f"/api/reports/{docx['id']}/download", headers=self.H["agent_a1"]).status_code, 409)

    def test_chat_shares_leads_and_reports(self):
        lead, audit_id = self.start()
        self.claim()
        self.assertEqual(self.send_result(audit_id, _stored_real_result()).status_code, 200)
        reps = {(r["kind"], r["format"]): r for r in
                self.c.get(f"/api/reports?audit_id={audit_id}", headers=self.H["agent_a1"]).get_json()["items"]}
        team = next(c for c in self.c.get("/api/chat/conversations", headers=self.H["agent_a1"]).get_json()["items"]
                    if c["type"] == "team")
        url = f"/api/chat/conversations/{team['id']}/messages"
        pdf, docx = reps[("client", "pdf")], reps[("client", "docx")]

        # A report the app has not uploaded yet: the app is told to upload it first.
        r = self.c.post(url, json={"body": "", "shares": [{"kind": "report", "id": pdf["id"]}]}, headers=self.H["agent_a1"])
        self.assertEqual((r.status_code, r.get_json()["code"]), (409, "report_not_uploaded"))
        self.assertEqual([x["id"] for x in r.get_json()["reports"]], [pdf["id"]])
        self.c.put(f"/api/reports/{pdf['id']}/file", data=b"%PDF-1.7 shared", headers=self.H["agent_a1"])

        r = self.c.post(url, json={"body": "Have a look", "shares": [{"kind": "lead", "id": lead["id"]},
                                                                      {"kind": "report", "id": pdf["id"]}]},
                        headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 201, r.get_json())
        msg = r.get_json()["message"]
        kinds = {s["kind"]: s for s in msg["shares"]}
        self.assertEqual(kinds["lead"]["company"], lead["company"])
        self.assertEqual((kinds["report"]["report_kind"], kinds["report"]["format"]), ("client", "pdf"))

        # A teammate sees the shares and can open the shared report, without access to the lead itself.
        items = self.c.get(f"/api/chat/conversations/{team['id']}/messages", headers=self.H["agent_a2"]).get_json()["items"]
        self.assertEqual(len(next(m for m in items if m["id"] == msg["id"])["shares"]), 2)
        self.assertEqual(self.c.get(f"/api/leads/{lead['id']}", headers=self.H["agent_a2"]).status_code, 404)
        r = self.c.get(f"/api/chat/messages/{msg['id']}/reports/{pdf['id']}", headers=self.H["agent_a2"])
        self.assertEqual((r.status_code, r.data), (200, b"%PDF-1.7 shared"))
        r.close()
        # Outside the conversation, or a report that was not shared in that message: no.
        self.assertEqual(self.c.get(f"/api/chat/messages/{msg['id']}/reports/{pdf['id']}",
                                    headers=self.H["agent_b1"]).status_code, 404)
        self.assertEqual(self.c.get(f"/api/chat/messages/{msg['id']}/reports/{docx['id']}",
                                    headers=self.H["agent_a2"]).status_code, 404)
        # Only what the sender can open may be shared.
        r = self.c.post(url, json={"shares": [{"kind": "lead", "id": lead["id"]}]}, headers=self.H["agent_a2"])
        self.assertEqual(r.status_code, 404)
        r = self.c.post(url, json={"shares": [{"kind": "file", "id": "x"}]}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 422)

    def test_result_and_word_reports_arrive_together(self):
        _lead, audit_id = self.start("agent_a2")
        self.claim("agent_a2")
        word = b"PK\x03\x04 client report written by the app"
        packed = gzip.compress(json.dumps(_stored_real_result()).encode("utf-8"))
        r = self.c.post(f"/api/audit-jobs/{audit_id}/result", headers=self.h("agent_a2"),
                        data={"result": (io.BytesIO(packed), "result.json.gz"),
                              "client_docx": (io.BytesIO(word), "client.docx")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        reps = {(x["kind"], x["format"]): x for x in
                self.c.get(f"/api/reports?audit_id={audit_id}", headers=self.H["agent_a2"]).get_json()["items"]}
        self.assertEqual((reps[("client", "docx")]["ready"], reps[("technical", "docx")]["ready"]), (1, 0))
        r = self.c.get(f"/api/reports/{reps[('client', 'docx')]['id']}/download", headers=self.H["lead_a"])
        self.assertEqual((r.status_code, r.data), (200, word))
        r.close()

    def test_failed_and_cancelled_audits(self):
        _lead, audit_id = self.start()
        self.claim()
        r = self.c.post(f"/api/audit-jobs/{audit_id}/failed", headers=self.h("agent_a1"),
                        json={"error": "The domain example.invalid could not be resolved."})
        self.assertEqual(r.status_code, 200)
        a = self.audit(audit_id)
        self.assertEqual((a["status"], a["error"]), ("failed", "The domain example.invalid could not be resolved."))

        _lead, audit_id = self.start()
        self.claim()
        self.assertEqual(self.c.post(f"/api/audits/{audit_id}/cancel", headers=self.H["lead_a"]).status_code, 200)
        self.assertEqual(self.audit(audit_id)["status"], "cancelled")
        r = self.c.post(f"/api/audit-jobs/{audit_id}/progress", headers=self.h("agent_a1"), json={"messages": ["x"]})
        self.assertEqual(r.get_json(), {"stop": True, "status": "cancelled"})
        self.assertEqual(self.send_result(audit_id, _stored_real_result()).status_code, 409)

    def test_closed_or_silent_app_gives_its_audits_back(self):
        _lead, audit_id = self.start("agent_b1")
        self.assertEqual(len(self.claim("agent_b1")["jobs"]), 1)
        # The app starts again: what it was running goes back to the queue and is handed out again.
        jobs = self.claim("agent_b1", restart=True)["jobs"]
        self.assertEqual([j["audit_id"] for j in jobs], [audit_id])
        # Second interruption: the audit fails instead of looping forever.
        self.claim("agent_b1", restart=True)
        a = self.audit(audit_id, "agent_b1")
        self.assertEqual(a["status"], "failed")
        self.assertIn("interrupted twice", a["error"])

        # An app that stopped reporting loses its audit to the next claim (any computer).
        _lead, audit_id = self.start("agent_b1")
        self.claim("agent_b1")
        db.execute("UPDATE audits SET heartbeat_at = %s WHERE id = %s", ("2000-01-01T00:00:00.000Z", audit_id))
        jobs = self.claim("agent_b1", PC2)["jobs"]
        self.assertEqual([j["audit_id"] for j in jobs], [audit_id])

    def test_slots_and_order(self):
        who = "lead_b"
        first = [self.start(who)[1] for _ in range(3)]
        got = self.claim(who)
        self.assertEqual(got["slots"], audit_runner.concurrency())
        self.assertEqual([j["audit_id"] for j in got["jobs"]], first[:got["slots"]])
        self.assertEqual(self.claim(who)["jobs"], [])  # all slots busy on this computer

    def test_pagespeed_goes_through_the_server_key(self):
        calls = []

        def fake(url, strategy="mobile", api_key=None, timeout=90):
            calls.append((url, strategy, api_key))
            return {"url": url, "strategy": strategy, "ok": True, "scores": {"performance": 91}}

        with mock.patch.dict(os.environ, {"PAGESPEED_API_KEY": "server-only-key"}), \
                mock.patch("pagespeed.run_pagespeed", fake):
            r = self.c.post("/api/pagespeed", json={"url": "https://example.com/", "strategy": "desktop"},
                            headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["scores"]["performance"], 91)
        self.assertNotIn("server-only-key", r.get_data(as_text=True))
        self.assertEqual(calls, [("https://example.com/", "desktop", "server-only-key")])
        self.assertEqual(self.c.post("/api/pagespeed", json={"url": "file:///etc/passwd"},
                                     headers=self.H["agent_a1"]).status_code, 422)

    def test_bad_results_are_refused(self):
        _lead, audit_id = self.start("agent_a2")
        self.claim("agent_a2")
        bomb = gzip.compress(b"{" + b" " * (70 * 1024 * 1024) + b"}")
        r = self.c.post(f"/api/audit-jobs/{audit_id}/result", data=bomb, headers=self.h("agent_a2"))
        self.assertEqual(r.status_code, 413)
        r = self.c.post(f"/api/audit-jobs/{audit_id}/result", data=b"not gzip", headers=self.h("agent_a2"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.send_result(audit_id, {"no": "score"}, "agent_a2").status_code, 422)
        self.assertEqual(self.audit(audit_id, "agent_a2")["status"], "running")
        r = self.c.post("/api/audit-jobs/claim", json={}, headers=self.H["agent_a2"])  # no device id
        self.assertEqual(r.status_code, 422)

    def test_server_mode_refuses_app_jobs_and_ping_skips_the_database(self):
        config.AUDITS_RUN_ON = "server"
        try:
            r = self.c.post("/api/audit-jobs/claim", json={}, headers=self.h("agent_a1"))
            self.assertEqual((r.status_code, r.get_json()["code"]), (409, "server_runs_audits"))
        finally:
            config.AUDITS_RUN_ON = "app"
        self.assertEqual(self.c.get("/api/health").get_json()["audits_run_on"], "app")
        with mock.patch.object(db, "scalar", side_effect=AssertionError("database used")), \
                mock.patch.object(db, "query_one", side_effect=AssertionError("database used")):
            r = self.c.get("/api/ping")
        self.assertEqual(r.get_json(), {"ok": True, "app": "SEO Audit Pro"})


class ReportJobTests(unittest.TestCase):
    """The app's background job writes a real Word report from a stored result."""

    def test_report_job_writes_docx(self):
        import subprocess
        import sys
        import tempfile
        folder = tempfile.mkdtemp(prefix="seo_job_")
        result_path = os.path.join(folder, "result.json.gz")
        with open(result_path, "wb") as fh:
            fh.write(gzip.compress(json.dumps(_stored_real_result()).encode("utf-8")))
        docx = os.path.join(folder, "client.docx")
        with open(os.path.join(folder, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({"kind": "report", "audit_id": "aud_test", "report_kind": "client",
                       "result_path": result_path, "docx_path": docx}, fh)
        proc = subprocess.run([sys.executable, "-m", "seo_audit_pro.pc_jobs", "--job", folder], cwd=h.ROOT,
                              timeout=300, capture_output=True)
        self.assertEqual(proc.returncode, 0, open(os.path.join(folder, "worker.log"), encoding="utf-8").read())
        with open(docx, "rb") as fh:
            self.assertEqual(fh.read(2), b"PK")
        with open(os.path.join(folder, "progress.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.read().splitlines()[-1]), {"t": "done"})


if __name__ == "__main__":
    unittest.main()
