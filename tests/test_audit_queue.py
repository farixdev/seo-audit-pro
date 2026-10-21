# -*- coding: utf-8 -*-
"""
Audit queue tests: bulk audit preview/start (caps, skipped leads, scope,
confirmation), the saved queue (concurrency limit, single audits first, fair
turns between people, restart recovery, cancelling a batch, one summary
notification per batch), compressed result storage and on-demand PDFs.
"""

import json
import os
import threading
import time
import unittest
import uuid

from tests import helpers as h
from backend import audit_runner, db, reports, results
from backend.routes import audit_routes
from backend.util import now_iso

import audit as engine


class FakeEngine:
    """Stands in for audit.run_audit: each URL waits for its gate; records order
    and how many audits ran at the same time."""

    def __init__(self, result):
        self.result = result
        self.gates = {}
        self.order = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def gate(self, url):
        with self.lock:
            return self.gates.setdefault(url, threading.Event())

    def release(self, url):
        self.gate(url).set()

    def __call__(self, params, progress=None):
        url = params["url"]
        with self.lock:
            self.order.append(url)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            progress("Fetching homepage…")
            gate = self.gate(url)
            while not gate.wait(0.05):
                if params["cancel"].is_set():
                    raise RuntimeError("Audit cancelled.")
            res = json.loads(json.dumps(self.result))
            res["url"] = url
            return res
        finally:
            with self.lock:
                self.active -= 1


def _small_result():
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "real_audit_cancomco.json"), encoding="utf-8") as fh:
        return json.load(fh)


class AuditQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = h.client()
        cls.team = h.make_team()
        cls.other_team = h.make_team()
        cls.manager = h.make_user("MANAGER")
        cls.lead = h.make_user("TEAM_LEAD", cls.team)
        cls.a1 = h.make_user("AGENT", cls.team)
        cls.a2 = h.make_user("AGENT", cls.team)
        cls.b1 = h.make_user("AGENT", cls.other_team)
        cls.H = {n: h.login(cls.c, getattr(cls, n))[0] for n in ("manager", "lead", "a1", "a2", "b1")}
        cls.engine = FakeEngine(_small_result())
        cls._orig = (engine.run_audit, audit_runner.validate_public_target, reports.generate_all)
        engine.run_audit = cls.engine
        audit_runner.validate_public_target = lambda url: None
        reports.generate_all = lambda audit_id, result: ([], [])

    @classmethod
    def tearDownClass(cls):
        for ev in list(cls.engine.gates.values()):
            ev.set()
        engine.run_audit, audit_runner.validate_public_target, reports.generate_all = cls._orig
        cls._set_concurrency(2)

    @classmethod
    def _set_concurrency(cls, n):
        r = cls.c.put("/api/settings", json={"audit_defaults": {"max_pages": 50, "psi_strategy": "both", "concurrent": n}},
                      headers=cls.H["manager"])
        assert r.status_code == 200, r.get_json()

    def setUp(self):
        self._wait_idle()

    def _wait_idle(self):
        """Let audits from an earlier test finish so each test starts with an empty queue."""
        for url in list(self.engine.gates):
            self.engine.release(url)
        for _ in range(400 * h.WAIT_SCALE):
            if not db.scalar("SELECT COUNT(*) FROM audits WHERE status IN ('queued', 'running')"):
                return
            time.sleep(0.05)
        self.fail("queue did not drain")

    def leads(self, n, agent, team=None):
        ids = []
        for _ in range(n):
            site = f"https://q-{uuid.uuid4().hex[:10]}.example/"
            r = self.c.post("/api/leads", json={"company": f"Queue {uuid.uuid4().hex[:5]}", "website": site,
                                                "agent_id": agent["id"]}, headers=self.H["manager"])
            self.assertEqual(r.status_code, 201, r.get_json())
            ids.append(r.get_json()["lead"])
        return ids

    def bulk(self, who, lead_ids, **extra):
        pv = self.c.post("/api/audits/bulk/preview", json=dict(lead_ids=lead_ids, **extra), headers=self.H[who])
        self.assertEqual(pv.status_code, 200, pv.get_json())
        pv = pv.get_json()
        r = self.c.post("/api/audits/bulk", json=dict(lead_ids=lead_ids, confirm_count=pv["will_queue"], **extra),
                        headers=dict(self.H[who], **{"Idempotency-Key": uuid.uuid4().hex}))
        return pv, r

    def wait_for(self, cond, what):
        for _ in range(400 * h.WAIT_SCALE):
            if cond():
                return
            time.sleep(0.025)
        self.fail(f"timed out waiting for {what}")

    def status_of(self, lead):
        return db.scalar("SELECT status FROM audits WHERE lead_id = %s ORDER BY created_at DESC LIMIT 1", (lead["id"],))

    # ------------------------------------------------------------------ preview / start
    def test_caps_are_100_per_batch_and_500_for_managers(self):
        self.assertEqual(audit_routes.BATCH_CAP, {"MANAGER": 500, "TEAM_LEAD": 100, "AGENT": 100})

    def test_preview_skips_and_caps(self):
        leads = self.leads(6, self.a1)
        archived, recent, running = leads[0], leads[1], leads[2]
        db.execute("UPDATE leads SET status = 'archived' WHERE id = %s", (archived["id"],))
        db.execute("UPDATE leads SET last_audit_at = %s WHERE id = %s", (now_iso(), recent["id"]))
        db.execute("INSERT INTO audits (id, lead_id, requested_by, url, status, created_at, priority) "
                   "VALUES (%s, %s, %s, %s, 'running', %s, 1)",
                   (f"aud_test{uuid.uuid4().hex[:8]}", running["id"], self.a1["id"], running["website"], now_iso()))
        ids = [x["id"] for x in leads]
        orig = dict(audit_routes.BATCH_CAP)
        audit_routes.BATCH_CAP["AGENT"] = 2
        try:
            pv = self.c.post("/api/audits/bulk/preview", json={"lead_ids": ids}, headers=self.H["a1"]).get_json()
        finally:
            audit_routes.BATCH_CAP.update(orig)
            db.execute("UPDATE audits SET status = 'failed' WHERE lead_id = %s AND status = 'running'", (running["id"],))
        self.assertEqual(pv["selected"], 6)
        self.assertEqual(pv["skipped"], {"archived": 1, "in_progress": 1, "recently_audited": 1})
        self.assertEqual(pv["eligible"], 3)
        self.assertEqual(pv["will_queue"], 2)
        self.assertEqual(pv["not_now"], 1)
        pv2 = self.c.post("/api/audits/bulk/preview", json={"lead_ids": ids, "include_recent": True},
                          headers=self.H["a1"]).get_json()
        self.assertEqual(pv2["skipped"]["recently_audited"], 0)
        self.assertEqual(pv2["eligible"], 5)

    def test_scope_and_confirmation(self):
        mine = self.leads(2, self.a1)
        theirs = self.leads(2, self.b1)
        # Another team's leads are simply not part of an agent's selection.
        pv = self.c.post("/api/audits/bulk/preview", json={"lead_ids": [x["id"] for x in theirs]},
                         headers=self.H["a1"]).get_json()
        self.assertEqual((pv["selected"], pv["will_queue"]), (0, 0))
        r = self.c.post("/api/audits/bulk", json={"lead_ids": [x["id"] for x in theirs], "confirm_count": 0},
                        headers=self.H["a1"])
        self.assertEqual(r.status_code, 422)
        # A stale confirmation is refused with the fresh numbers.
        r = self.c.post("/api/audits/bulk", json={"lead_ids": [x["id"] for x in mine], "confirm_count": 5},
                        headers=self.H["a1"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["preview"]["will_queue"], 2)
        # Team Lead filter selection stays inside the team.
        pv = self.c.post("/api/audits/bulk/preview", json={"filter": {}}, headers=self.H["lead"]).get_json()
        team_total = db.scalar("SELECT COUNT(*) FROM leads WHERE assigned_team_id = %s AND status <> 'archived'",
                               (self.team,))
        self.assertEqual(pv["selected"], team_total)
        # Batches are private to their creator's scope.
        self.engine.release(mine[0]["website"])
        self.engine.release(mine[1]["website"])
        _pv, r = self.bulk("a1", [x["id"] for x in mine], label="Mine")
        self.assertEqual(r.status_code, 202, r.get_json())
        bid = r.get_json()["batch"]["id"]
        self.assertEqual(self.c.get(f"/api/audit-batches/{bid}", headers=self.H["b1"]).status_code, 404)
        self.assertEqual(self.c.get(f"/api/audit-batches/{bid}", headers=self.H["lead"]).status_code, 200)
        self.assertEqual(self.c.post(f"/api/audit-batches/{bid}/cancel", headers=self.H["a2"]).status_code, 404)

    # ------------------------------------------------------------------ scheduling
    def test_concurrency_priority_and_fair_turns(self):
        self._set_concurrency(2)
        self.engine.max_active = 0
        a1_leads = self.leads(4, self.a1)
        a2_leads = self.leads(1, self.a2)
        single = self.leads(1, self.a1)[0]
        start = len(self.engine.order)
        _pv, r = self.bulk("a1", [x["id"] for x in a1_leads], label="A1 batch")
        self.assertEqual(r.status_code, 202, r.get_json())
        self.wait_for(lambda: len(self.engine.order) - start == 2, "two audits to start")
        _pv, r = self.bulk("a2", [x["id"] for x in a2_leads], label="A2 batch")
        self.assertEqual(r.status_code, 202, r.get_json())
        r = self.c.post("/api/audits", json={"lead_id": single["id"]}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 202, r.get_json())
        queued = r.get_json()["audit"]
        info = self.c.get(f"/api/audits/{queued['id']}", headers=self.H["manager"]).get_json()["audit"]
        self.assertEqual(info["status"], "queued")
        self.assertEqual(info["queue_position"], 0)  # single audits go ahead of bulk ones
        self.assertIn("Next in the queue", info["step"])
        time.sleep(0.3)
        self.assertEqual(len(self.engine.order) - start, 2)  # still only two at a time
        first_two = self.engine.order[start:start + 2]
        self.engine.release(first_two[0])
        self.wait_for(lambda: len(self.engine.order) - start == 3, "third audit")
        self.assertEqual(self.engine.order[start + 2], single["website"])  # the single audit jumped the queue
        self.engine.release(first_two[1])
        self.wait_for(lambda: len(self.engine.order) - start == 4, "fourth audit")
        # a2 has nothing running and was served least recently: a2 goes before a1's remaining leads.
        self.assertEqual(self.engine.order[start + 3], a2_leads[0]["website"])
        for x in a1_leads + a2_leads + [single]:
            self.engine.release(x["website"])
        self._wait_idle()
        self.assertEqual(self.engine.max_active, 2)

    def test_batch_finishes_with_one_notification(self):
        leads = self.leads(3, self.a1)
        for x in leads:
            self.engine.release(x["website"])
        _pv, r = self.bulk("a1", [x["id"] for x in leads], label="Plumbers › London")
        bid = r.get_json()["batch"]["id"]
        self.wait_for(lambda: db.scalar("SELECT status FROM audit_batches WHERE id = %s", (bid,)) == "completed",
                      "batch completion")
        batch = self.c.get(f"/api/audit-batches/{bid}", headers=self.H["a1"]).get_json()["batch"]
        self.assertEqual(batch["counts"]["completed"], 3)
        self.assertEqual(batch["done"], 3)
        notes = db.query("SELECT type, body, entity_id FROM notifications WHERE user_id = %s", (self.a1["id"],))
        self.assertEqual([n for n in notes if n["type"] == "audit_batch_completed" and n["entity_id"] == bid][0]["body"],
                         "Plumbers › London: 3 completed.")
        audit_ids = {r["id"] for r in db.query("SELECT id FROM audits WHERE batch_id = %s", (bid,))}
        self.assertFalse([n for n in notes if n["entity_id"] in audit_ids])  # no per-lead notifications
        for x in leads:
            lead = db.query_one("SELECT status, last_audit_score FROM leads WHERE id = %s", (x["id"],))
            self.assertEqual(lead["status"], "audited")
            self.assertIsNotNone(lead["last_audit_score"])

    def test_cancel_batch(self):
        self._set_concurrency(1)
        leads = self.leads(3, self.a1)
        _pv, r = self.bulk("a1", [x["id"] for x in leads])
        bid = r.get_json()["batch"]["id"]
        self.wait_for(lambda: db.scalar("SELECT COUNT(*) FROM audits WHERE batch_id = %s AND status = 'running'",
                                        (bid,)) == 1, "first audit running")
        r = self.c.post(f"/api/audit-batches/{bid}/cancel", headers=self.H["lead"])
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["cancelled"], 2)
        self.wait_for(lambda: db.scalar("SELECT COUNT(*) FROM audits WHERE batch_id = %s AND status = 'cancelled'",
                                        (bid,)) == 3, "running audit to stop")
        self.assertEqual(db.scalar("SELECT status FROM audit_batches WHERE id = %s", (bid,)), "cancelled")
        self.assertEqual(self.c.post(f"/api/audit-batches/{bid}/cancel", headers=self.H["lead"]).status_code, 409)
        self._set_concurrency(2)

    def test_restart_puts_running_audits_back_in_the_queue(self):
        lead1, lead2, lead3 = self.leads(3, self.a1)
        rows = {}
        for lead, status, attempts in ((lead1, "running", 1), (lead2, "running", 2), (lead3, "queued", 0)):
            aid = f"aud_rst{uuid.uuid4().hex[:8]}"
            db.execute("INSERT INTO audits (id, lead_id, requested_by, url, status, created_at, priority, attempts) "
                       "VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                       (aid, lead["id"], self.a1["id"], lead["website"], status, now_iso(), attempts))
            rows[lead["id"]] = aid
        orig = audit_runner.wake
        audit_runner.wake = lambda: None  # inspect the recovered state before the scheduler picks it up
        try:
            audit_runner.recover_interrupted()
        finally:
            audit_runner.wake = orig
        st = {lid: db.scalar("SELECT status FROM audits WHERE id = %s", (aid,)) for lid, aid in rows.items()}
        self.assertEqual(st[lead1["id"]], "queued")
        self.assertEqual(st[lead2["id"]], "failed")
        self.assertEqual(st[lead3["id"]], "queued")
        for x in (lead1, lead3):
            self.engine.release(x["website"])
        audit_runner.wake()
        self._wait_idle()

    # ------------------------------------------------------------------ storage
    def test_results_are_stored_compressed(self):
        lead = self.leads(1, self.a1)[0]
        self.engine.release(lead["website"])
        r = self.c.post("/api/audits", json={"lead_id": lead["id"]}, headers=self.H["a1"])
        aid = r.get_json()["audit"]["id"]
        self.wait_for(lambda: self.status_of(lead) == "completed", "audit completion")
        row = db.query_one("SELECT raw_size, stored_size FROM audit_results WHERE audit_id = %s", (aid,))
        self.assertLess(row["stored_size"] * 5, row["raw_size"])
        res = self.c.get(f"/api/audits/{aid}/result", headers=self.H["a1"]).get_json()
        self.assertEqual(len(res["checks"]), len(self.engine.result["checks"]))

    def test_legacy_results_are_read_and_backfilled(self):
        import history_manager
        lead = self.leads(1, self.a1)[0]
        aid = f"aud_legacy{uuid.uuid4().hex[:8]}"
        db.execute("INSERT INTO audits (id, lead_id, requested_by, url, status, created_at, priority) "
                   "VALUES (%s, %s, %s, %s, 'completed', %s, 1)", (aid, lead["id"], self.a1["id"], lead["website"], now_iso()))
        tmp = os.path.join(os.environ["SEO_DATA_DIR"], "legacy_history")
        history_manager.REPORTS_DIR, history_manager.AUDITS_DIR = tmp, os.path.join(tmp, "audits")
        history_manager.HISTORY_FILE = os.path.join(tmp, "history.json")
        history_manager.save_audit_result(aid, json.loads(json.dumps(self.engine.result)))
        history_manager._CACHE["audits"].clear()
        self.assertIsNone(db.query_one("SELECT 1 AS x FROM audit_results WHERE audit_id = %s", (aid,)))
        self.assertEqual(results.load(aid)["score"], self.engine.result["score"])
        self.assertIsNotNone(db.query_one("SELECT 1 AS x FROM audit_results WHERE audit_id = %s", (aid,)))

    def test_pdfs_are_made_on_first_use_one_at_a_time(self):
        lead = self.leads(1, self.a1)[0]
        aid = f"aud_pdf{uuid.uuid4().hex[:8]}"
        db.execute("INSERT INTO audits (id, lead_id, requested_by, url, status, created_at, priority) "
                   "VALUES (%s, %s, %s, %s, 'completed', %s, 1)", (aid, lead["id"], self.a1["id"], lead["website"], now_iso()))
        calls, live, peak = [], [0], [0]
        lock = threading.Lock()

        def fake_convert(docx, pdf):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.15)
            with open(pdf, "wb") as fh:
                fh.write(b"%PDF-1.4 test")
            calls.append(docx)
            with lock:
                live[0] -= 1
            return True

        orig = (reports.docx_to_pdf, reports.pdf_supported)
        reports.docx_to_pdf, reports.pdf_supported = fake_convert, lambda: True
        try:
            made, _errors = self._orig[2](aid, json.loads(json.dumps(self.engine.result)))
            self.assertEqual(sorted(made), ["client:docx", "technical:docx"])
            rows = db.query("SELECT * FROM reports WHERE audit_id = %s ORDER BY kind, format", (aid,))
            pdfs = [r for r in rows if r["format"] == "pdf"]
            self.assertEqual(len(pdfs), 2)
            self.assertTrue(all(not r["ready"] for r in pdfs))
            self.assertEqual(calls, [])  # nothing converted when the audit finished
            listed = self.c.get(f"/api/reports?audit_id={aid}", headers=self.H["a1"]).get_json()["items"]
            self.assertEqual({(r["format"], r["ready"]) for r in listed}, {("docx", 1), ("pdf", 0)})
            threads = [threading.Thread(target=lambda rid=r["id"]: self.c.get(f"/api/reports/{rid}/download",
                                                                               headers=self.H["a1"])) for r in pdfs]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(calls), 2)
            self.assertEqual(peak[0], 1)  # one conversion at a time
            dl = self.c.get(f"/api/reports/{pdfs[0]['id']}/download", headers=self.H["a1"])
            self.assertEqual(dl.status_code, 200)
            self.assertTrue(dl.data.startswith(b"%PDF"))
            dl.close()
            self.assertEqual(len(calls), 2)  # second download served the stored file
            self.assertEqual(self.c.get(f"/api/reports/{pdfs[0]['id']}/download", headers=self.H["b1"]).status_code, 404)
        finally:
            reports.docx_to_pdf, reports.pdf_supported = orig

    def test_queue_status_and_settings_validation(self):
        snap = self.c.get("/api/audit-queue", headers=self.H["a1"]).get_json()
        self.assertEqual((snap["batch_cap"], snap["queue_cap"]), (100, 300))
        self.assertIn("workers", snap)
        r = self.c.put("/api/settings", json={"audit_defaults": {"concurrent": 9}}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.put("/api/settings", json={"audit_defaults": {"concurrent": 2}},
                                    headers=self.H["a1"]).status_code, 403)


if __name__ == "__main__":
    unittest.main()
