# -*- coding: utf-8 -*-
"""
API test-suite: authentication, sessions, RBAC/IDOR boundaries, data
integrity, concurrency, audits, reports, outreach, chat, notifications,
uploads and the audit trail.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import copy
import glob
import io
import json
import os
import threading
import time
import unittest

from tests import helpers as h
from backend import audit_runner, db, mailer

import audit as engine
import history_manager


def _stored_real_result():
    files = sorted(glob.glob(os.path.join(h.ROOT, "tests", "fixtures", "*.json")))
    with open(files[-1], encoding="utf-8") as fh:
        return json.load(fh)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = h.client()
        cls.team_a = h.make_team()
        cls.team_b = h.make_team()
        cls.manager = h.make_user("MANAGER")
        cls.lead_a = h.make_user("TEAM_LEAD", cls.team_a)
        cls.lead_b = h.make_user("TEAM_LEAD", cls.team_b)
        cls.agent_a1 = h.make_user("AGENT", cls.team_a)
        cls.agent_a2 = h.make_user("AGENT", cls.team_a)
        cls.agent_b1 = h.make_user("AGENT", cls.team_b)
        cls.H = {}
        for name in ("manager", "lead_a", "lead_b", "agent_a1", "agent_a2", "agent_b1"):
            cls.H[name], _ = h.login(cls.c, getattr(cls, name))

    def create_lead(self, who="manager", **fields):
        import uuid
        payload = {"company": f"Acme {uuid.uuid4().hex[:6]}", "website": f"https://www.acme-{uuid.uuid4().hex[:8]}.com/"}
        payload.update(fields)
        r = self.c.post("/api/leads", json=payload, headers=self.H[who])
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["lead"]


class AuthTests(Base):
    def test_login_and_me_never_expose_password(self):
        r = self.c.get("/api/auth/me", headers=self.H["manager"])
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("password_hash", json.dumps(r.get_json()))

    def test_wrong_password_generic_error_and_lockout(self):
        user = h.make_user("AGENT", self.team_a)
        for _ in range(5):
            r = self.c.post("/api/auth/login", json={"email": user["email"], "password": "nope"})
            self.assertEqual(r.status_code, 401)
        r = self.c.post("/api/auth/login", json={"email": user["email"], "password": h.PASSWORD})
        self.assertEqual(r.status_code, 429)
        r = self.c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
        self.assertEqual(r.status_code, 401)

    def test_inactive_user_cannot_login(self):
        user = h.make_user("AGENT", self.team_a, active=False)
        r = self.c.post("/api/auth/login", json={"email": user["email"], "password": h.PASSWORD})
        self.assertEqual(r.status_code, 403)

    def test_refresh_rotation_and_logout(self):
        user = h.make_user("AGENT", self.team_a)
        headers, data = h.login(self.c, user)
        r = self.c.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        self.assertEqual(r.status_code, 200)
        new = r.get_json()
        # Old refresh token is single-use.
        self.assertEqual(self.c.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]}).status_code, 401)
        h2 = {"Authorization": f"Bearer {new['access_token']}"}
        self.assertEqual(self.c.post("/api/auth/logout", headers=h2).status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=h2).status_code, 401)
        self.assertEqual(self.c.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code, 401)

    def test_tampered_token_rejected(self):
        tok = self.H["agent_a1"]["Authorization"].split(" ")[1]
        body, sig = tok.split(".")
        forged = {"Authorization": f"Bearer {body}x.{sig}"}
        self.assertEqual(self.c.get("/api/auth/me", headers=forged).status_code, 401)
        self.assertEqual(self.c.get("/api/leads").status_code, 401)

    def test_first_login_must_change_password(self):
        user = h.make_user("AGENT", self.team_a, must_change=True)
        headers, data = h.login(self.c, user)
        self.assertTrue(data["user"]["must_change_password"])
        r = self.c.get("/api/leads", headers=headers)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "password_change_required")
        weak = self.c.post("/api/auth/change-password", headers=headers,
                           json={"current_password": h.PASSWORD, "new_password": "short"})
        self.assertEqual(weak.status_code, 422)
        same = self.c.post("/api/auth/change-password", headers=headers,
                           json={"current_password": h.PASSWORD, "new_password": h.PASSWORD})
        self.assertEqual(same.status_code, 422)
        ok = self.c.post("/api/auth/change-password", headers=headers,
                         json={"current_password": h.PASSWORD, "new_password": "Brand-New-Pass-77"})
        self.assertEqual(ok.status_code, 200)
        self.assertFalse(ok.get_json()["user"]["must_change_password"])
        self.assertEqual(self.c.get("/api/leads", headers=headers).status_code, 200)

    def test_forgot_and_reset_password(self):
        user = h.make_user("AGENT", self.team_a)
        captured = {}
        original = mailer.send_password_reset
        mailer.send_password_reset = lambda u, code: captured.setdefault("code", code)
        try:
            r = self.c.post("/api/auth/forgot-password", json={"email": user["email"]})
            self.assertEqual(r.status_code, 200)
            r2 = self.c.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
            self.assertEqual(r2.get_json(), r.get_json())  # no account enumeration
            for _ in range(50):
                if "code" in captured:
                    break
                time.sleep(0.05)
        finally:
            mailer.send_password_reset = original
        bad = self.c.post("/api/auth/reset-password", json={"email": user["email"], "code": "00000000",
                                                            "new_password": "Another-Pass-99"})
        self.assertEqual(bad.status_code, 422)
        ok = self.c.post("/api/auth/reset-password", json={"email": user["email"], "code": captured["code"],
                                                           "new_password": "Another-Pass-99"})
        self.assertEqual(ok.status_code, 200)
        reused = self.c.post("/api/auth/reset-password", json={"email": user["email"], "code": captured["code"],
                                                               "new_password": "Third-Pass-123"})
        self.assertEqual(reused.status_code, 422)
        h.login(self.c, user, "Another-Pass-99")


class RbacTests(Base):
    def test_manager_only_endpoints(self):
        for who in ("lead_a", "agent_a1"):
            self.assertEqual(self.c.post("/api/users", json={"name": "X", "email": "x@example.com", "role": "MANAGER"},
                                         headers=self.H[who]).status_code, 403)
            self.assertEqual(self.c.post("/api/teams", json={"name": "Hack"}, headers=self.H[who]).status_code, 403)
            self.assertEqual(self.c.get("/api/outreach/smtp", headers=self.H[who]).status_code, 403)
            self.assertEqual(self.c.get("/api/audit-logs", headers=self.H[who]).status_code, 403)
            self.assertEqual(self.c.put("/api/settings", json={}, headers=self.H[who]).status_code, 403)
        self.assertEqual(self.c.get("/api/users", headers=self.H["agent_a1"]).status_code, 403)
        denied = db.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'PERMISSION_DENIED'")
        self.assertGreater(denied, 0)

    def test_client_cannot_escalate_role_or_team(self):
        # Extra identity fields in the body are ignored; identity comes from the session.
        lead = self.create_lead("agent_a1", role="MANAGER", team_id=self.team_b, agent_id=self.agent_b1["id"])
        self.assertEqual(lead["assigned_agent_id"], self.agent_a1["id"])
        self.assertEqual(lead["assigned_team_id"], self.team_a)
        r = self.c.patch("/api/auth/me", json={"name": "Agent", "role": "MANAGER", "team_id": self.team_b},
                         headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["user"]["role"], "AGENT")
        self.assertEqual(r.get_json()["user"]["team_id"], self.team_a)

    def test_lead_idor(self):
        lead = self.create_lead("manager", agent_id=self.agent_a1["id"])
        lid = lead["id"]
        self.assertEqual(self.c.get(f"/api/leads/{lid}", headers=self.H["agent_a1"]).status_code, 200)
        self.assertEqual(self.c.get(f"/api/leads/{lid}", headers=self.H["lead_a"]).status_code, 200)
        for who in ("agent_a2", "agent_b1", "lead_b"):
            self.assertEqual(self.c.get(f"/api/leads/{lid}", headers=self.H[who]).status_code, 404, who)
            self.assertEqual(self.c.get(f"/api/leads/{lid}/activity", headers=self.H[who]).status_code, 404)
            self.assertEqual(self.c.patch(f"/api/leads/{lid}", json={"company": "Pwned", "version": lead["version"]},
                                          headers=self.H[who]).status_code, 404)
        listed = self.c.get("/api/leads?page_size=200", headers=self.H["lead_b"]).get_json()["items"]
        self.assertNotIn(lid, [l["id"] for l in listed])

    def test_team_lead_cannot_assign_across_teams(self):
        lead = self.create_lead("manager", team_id=self.team_a)
        r = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_b1["id"]}, headers=self.H["lead_a"])
        self.assertEqual(r.status_code, 403)
        r = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a1["id"]}, headers=self.H["lead_b"])
        self.assertEqual(r.status_code, 404)
        r = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a1["id"]}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 403)
        r = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a1["id"]}, headers=self.H["lead_a"])
        self.assertEqual(r.status_code, 200)

    def test_team_lead_sees_only_own_team_users(self):
        items = self.c.get("/api/users?page_size=200", headers=self.H["lead_a"]).get_json()["items"]
        self.assertTrue(items)
        self.assertTrue(all(u["team_id"] == self.team_a for u in items))
        self.assertEqual(self.c.get(f"/api/users/{self.agent_b1['id']}", headers=self.H["lead_a"]).status_code, 404)
        self.assertEqual(self.c.get(f"/api/teams/{self.team_b}", headers=self.H["lead_a"]).status_code, 404)

    def test_search_is_scoped(self):
        lead = self.create_lead("manager", company="Zebra Unique Holdings", team_id=self.team_b)
        r = self.c.get("/api/search?q=zebra unique", headers=self.H["agent_a1"]).get_json()
        self.assertEqual(r["leads"], [])
        r = self.c.get("/api/search?q=zebra unique", headers=self.H["lead_b"]).get_json()
        self.assertEqual([l["id"] for l in r["leads"]], [lead["id"]])


class LeadIntegrityTests(Base):
    def test_domain_normalization_duplicates(self):
        base = self.create_lead("manager", website="http://dup-check-example.com")
        self.assertEqual(base["normalized_domain"], "dup-check-example.com")
        for variant in ("https://dup-check-example.com", "http://www.dup-check-example.com",
                        "https://www.dup-check-example.com/", "dup-check-example.com/", "DUP-CHECK-EXAMPLE.COM"):
            r = self.c.post("/api/leads", json={"company": "Other", "website": variant}, headers=self.H["manager"])
            self.assertEqual(r.status_code, 409, variant)

    def test_duplicate_does_not_leak_other_team_lead_id(self):
        self.create_lead("manager", website="secret-team-b.com", team_id=self.team_b)
        r = self.c.post("/api/leads", json={"company": "X", "website": "secret-team-b.com"}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 409)
        self.assertNotIn("existing_lead_id", r.get_json())

    def test_similar_email_requires_confirmation(self):
        self.create_lead("manager", email="owner@similar-co.com")
        r = self.c.post("/api/leads", json={"company": "Y", "website": "different-site-1.com", "email": "OWNER@similar-co.com"},
                        headers=self.H["manager"])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "possible_duplicate")
        r = self.c.post("/api/leads", json={"company": "Y", "website": "different-site-1.com", "email": "owner@similar-co.com",
                                            "allow_similar": True}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 201)

    def test_stale_version_conflict_and_assignment_history(self):
        lead = self.create_lead("manager", team_id=self.team_a)
        r1 = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a1["id"], "version": lead["version"]},
                         headers=self.H["manager"])
        self.assertEqual(r1.status_code, 200)
        r2 = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a2["id"], "version": lead["version"]},
                         headers=self.H["lead_a"])
        self.assertEqual(r2.status_code, 409)
        cur = r1.get_json()["lead"]
        r3 = self.c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": self.agent_a2["id"], "version": cur["version"],
                                                                  "reason": "Workload"}, headers=self.H["lead_a"])
        self.assertEqual(r3.status_code, 200)
        act = self.c.get(f"/api/leads/{lead['id']}/activity", headers=self.H["manager"]).get_json()
        self.assertEqual(act["assignments"][0]["reason"], "Workload")
        self.assertEqual(act["assignments"][0]["from_agent_id"], self.agent_a1["id"])
        notes = db.query("SELECT type FROM notifications WHERE user_id = %s", (self.agent_a1["id"],))
        self.assertIn("lead_reassigned", [n["type"] for n in notes])

    def test_concurrent_assignment_single_winner(self):
        lead = self.create_lead("manager", team_id=self.team_a)
        results = []

        def go(agent):
            c = h.client()
            r = c.post(f"/api/leads/{lead['id']}/assign", json={"agent_id": agent["id"], "version": lead["version"]},
                       headers=self.H["manager"])
            results.append(r.status_code)

        threads = [threading.Thread(target=go, args=(a,)) for a in (self.agent_a1, self.agent_a2) * 3]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly one assignment change is applied; competing targets get 409 and
        # repeats of the winning target are harmless no-ops.
        self.assertIn(409, results)
        changes = db.scalar("SELECT COUNT(*) FROM lead_assignments WHERE lead_id = %s AND reason IS NULL "
                            "AND from_agent_id IS NULL AND to_agent_id IS NOT NULL", (lead["id"],))
        self.assertEqual(changes, 1, results)

    def test_idempotency_key_prevents_double_create(self):
        headers = dict(self.H["manager"], **{"Idempotency-Key": "create-lead-once-1"})
        payload = {"company": "Twice Co", "website": "idempotent-check.com"}
        r1 = self.c.post("/api/leads", json=payload, headers=headers)
        r2 = self.c.post("/api/leads", json=payload, headers=headers)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r1.get_json()["lead"]["id"], r2.get_json()["lead"]["id"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE normalized_domain = 'idempotent-check.com'"), 1)

    def test_team_delete_blocked_when_in_use(self):
        r = self.c.delete(f"/api/teams/{self.team_a}", headers=self.H["manager"])
        self.assertEqual(r.status_code, 409)
        empty = self.c.post("/api/teams", json={"name": "Empty Team Delete"}, headers=self.H["manager"]).get_json()["team"]
        self.assertEqual(self.c.delete(f"/api/teams/{empty['id']}", headers=self.H["manager"]).status_code, 200)

    def test_one_team_lead_per_team(self):
        r = self.c.post("/api/users", json={"name": "Second Lead", "email": "second-lead@example.com", "role": "TEAM_LEAD",
                                            "team_id": self.team_a}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 409)

    def test_user_creation_and_deactivation(self):
        r = self.c.post("/api/users", json={"name": "New Agent", "email": "new.agent@example.com", "role": "AGENT",
                                            "team_id": self.team_a}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 201)
        temp = r.get_json()["temporary_password"]
        uid = r.get_json()["user"]["id"]
        self.assertEqual(self.c.post("/api/users", json={"name": "Dup", "email": "NEW.AGENT@example.com", "role": "AGENT",
                                                         "team_id": self.team_a}, headers=self.H["manager"]).status_code, 409)
        headers, data = h.login(self.c, {"email": "new.agent@example.com"}, temp)
        self.assertTrue(data["user"]["must_change_password"])
        self.assertEqual(self.c.post(f"/api/users/{uid}/deactivate", headers=self.H["manager"]).status_code, 200)
        self.assertEqual(self.c.get("/api/auth/me", headers=headers).status_code, 401)
        self.assertEqual(self.c.post(f"/api/users/{self.manager['id']}/deactivate", headers=self.H["manager"]).status_code, 422)

    def test_deactivation_returns_leads_to_team_pool(self):
        agent = h.make_user("AGENT", self.team_a)
        lead = self.create_lead("manager", agent_id=agent["id"])
        self.assertEqual(self.c.post(f"/api/users/{agent['id']}/deactivate", headers=self.H["manager"]).status_code, 200)
        now = self.c.get(f"/api/leads/{lead['id']}", headers=self.H["lead_a"]).get_json()["lead"]
        self.assertIsNone(now["assigned_agent_id"])
        self.assertEqual(now["assigned_team_id"], self.team_a)
        self.assertEqual(now["status"], "new")

    def test_last_manager_cannot_be_demoted(self):
        r = self.c.patch(f"/api/users/{self.manager['id']}", json={"role": "AGENT", "team_id": self.team_a},
                         headers=self.H["manager"])
        self.assertEqual(r.status_code, 422)

    def test_audit_log_is_append_only(self):
        with self.assertRaises(Exception):
            db.execute("UPDATE audit_logs SET action = 'X'")
        with self.assertRaises(Exception):
            db.execute("DELETE FROM audit_logs")
        self.assertEqual(self.c.delete("/api/audit-logs", headers=self.H["manager"]).status_code, 405)


class AuditWorkflowTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tmp = os.path.join(os.environ["SEO_DATA_DIR"], "history")
        history_manager.REPORTS_DIR = tmp
        history_manager.AUDITS_DIR = os.path.join(tmp, "audits")
        history_manager.HISTORY_FILE = os.path.join(tmp, "history.json")
        history_manager.TARGETS_FILE = os.path.join(tmp, "targets.json")
        cls._orig_engine = engine.run_audit
        cls._orig_validate = staticmethod(audit_runner.validate_public_target)
        audit_runner.validate_public_target = lambda url: None
        cls.gate = threading.Event()

        def fake_run(params, progress=None):
            # Replays a REAL stored engine result; progress messages use the engine's own wording.
            progress("Fetching homepage…")
            progress("Crawling the site…")
            progress("Crawled 5/10 pages (50%), 12 URLs discovered")
            cls.gate.wait(10)
            if params["cancel"].is_set():
                raise RuntimeError("Audit cancelled.")
            progress("Scoring…")
            res = copy.deepcopy(_stored_real_result())
            res["url"] = params["url"]
            return res

        engine.run_audit = fake_run

    @classmethod
    def tearDownClass(cls):
        engine.run_audit = cls._orig_engine
        audit_runner.validate_public_target = cls._orig_validate

    def _wait(self, audit_id, headers, states=("completed", "failed", "cancelled")):
        for _ in range(400):
            a = self.c.get(f"/api/audits/{audit_id}", headers=headers).get_json()["audit"]
            if a["status"] in states:
                return a
            time.sleep(0.05)
        self.fail("audit did not finish")

    def test_full_audit_report_and_outreach_flow(self):
        lead = self.create_lead("manager", agent_id=self.agent_a1["id"], email="owner@client-site.com",
                                contact_name="Dana Smith")
        self.gate.clear()
        r = self.c.post("/api/audits", json={"lead_id": lead["id"]}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 202, r.get_json())
        audit_id = r.get_json()["audit"]["id"]
        # Double submit while running is rejected by the database constraint.
        r2 = self.c.post("/api/audits", json={"lead_id": lead["id"]}, headers=self.H["agent_a1"])
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.get_json().get("audit_id"), audit_id)
        self._wait(audit_id, self.H["agent_a1"], ("running",))
        for _ in range(60):  # throttled progress writes land within ~1 s
            running = self.c.get(f"/api/audits/{audit_id}", headers=self.H["agent_a1"]).get_json()["audit"]
            if running["pages_processed"]:
                break
            time.sleep(0.05)
        self.assertEqual(running["pages_processed"], 5)
        self.assertTrue(any("Crawled 5/10" in line for line in running["logs"]))
        self.assertEqual(running["pages_discovered"], 12)
        # Results are not available until completion.
        self.assertEqual(self.c.get(f"/api/audits/{audit_id}/result", headers=self.H["agent_a1"]).status_code, 409)
        self.gate.set()
        done = self._wait(audit_id, self.H["agent_a1"])
        self.assertEqual(done["status"], "completed", done)
        real = _stored_real_result()
        self.assertEqual(done["score"], real["score"]["score"])

        # Other agents / other team cannot see it.
        for who in ("agent_a2", "agent_b1", "lead_b"):
            self.assertEqual(self.c.get(f"/api/audits/{audit_id}", headers=self.H[who]).status_code, 404)
            self.assertEqual(self.c.get(f"/api/audits/{audit_id}/result", headers=self.H[who]).status_code, 404)
        res = self.c.get(f"/api/audits/{audit_id}/result", headers=self.H["lead_a"]).get_json()
        self.assertEqual(len(res["checks"]), len(real["checks"]))
        self.assertTrue(res["authority"]["is_estimated"])

        # Lead updated from the real result.
        lead_now = self.c.get(f"/api/leads/{lead['id']}", headers=self.H["agent_a1"]).get_json()["lead"]
        self.assertEqual(lead_now["status"], "audited")
        self.assertEqual(lead_now["last_audit_score"], real["score"]["score"])

        # Reports exist and download; IDOR blocked.
        reps = self.c.get(f"/api/reports?audit_id={audit_id}", headers=self.H["agent_a1"]).get_json()["items"]
        self.assertTrue(any(r["format"] == "docx" and r["kind"] == "client" for r in reps))
        for rep in reps:
            dl = self.c.get(f"/api/reports/{rep['id']}/download", headers=self.H["agent_a1"])
            self.assertEqual(dl.status_code, 200)
            self.assertTrue(dl.data.startswith(b"PK" if rep["format"] == "docx" else b"%PDF"))
        rep_id = reps[0]["id"]
        self.assertEqual(self.c.get(f"/api/reports/{rep_id}/download", headers=self.H["agent_b1"]).status_code, 404)
        html = self.c.get(f"/api/audits/{audit_id}/client-html", headers=self.H["agent_a1"])
        self.assertEqual(html.status_code, 200)
        self.assertIn(b"<html", html.data[:500].lower())

        # Issue state toggling.
        first_check = res["action_items"][0]["id"]
        self.assertEqual(self.c.put(f"/api/audits/{audit_id}/issues/{first_check}", json={"status": "resolved"},
                                    headers=self.H["agent_a1"]).status_code, 200)

        # Outreach draft uses only real findings.
        gen = self.c.post("/api/outreach/generate", json={"lead_id": lead["id"]}, headers=self.H["agent_a1"]).get_json()
        self.assertIn(str(real["score"]["score"]), gen["body"])
        top_labels = [a["label"] for a in real["action_items"]]
        self.assertTrue(any(lbl in gen["body"] for lbl in top_labels))
        self.assertIn("Hi Dana,", gen["body"])

        # Send through a real (local) SMTP conversation.
        smtp = h.FakeSMTP()
        try:
            r = self.c.put("/api/outreach/smtp", json={"host": "127.0.0.1", "port": smtp.port, "encryption": "none",
                                                       "username": "", "password": "app-pass-123", "from_email": "team@agency.test",
                                                       "from_name": "Agency"}, headers=self.H["manager"])
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertNotIn("app-pass-123", json.dumps(r.get_json()))
            stored = db.scalar("SELECT password_enc FROM smtp_settings")
            self.assertNotIn("app-pass-123", stored)
            self.assertNotIn("app-pass-123", json.dumps(db.query("SELECT metadata FROM audit_logs")))
            self.assertEqual(self.c.get("/api/outreach/smtp", headers=self.H["agent_a1"]).status_code, 403)

            report_ids = [r["id"] for r in reps if r["kind"] == "client" and r["format"] == "docx"]
            bad = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
                "lead_id": lead["id"], "to_email": "owner@client-site.com", "subject": "Hi\r\nBcc: victim@example.com",
                "body": "x"})
            self.assertEqual(bad.status_code, 422)
            em = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
                "lead_id": lead["id"], "to_email": "owner@client-site.com", "subject": gen["subject"],
                "body": gen["body"] + "\n\nEdited by agent.", "audit_id": audit_id, "report_ids": report_ids})
            self.assertEqual(em.status_code, 201, em.get_json())
            eid = em.get_json()["email"]["id"]
            key = {"Idempotency-Key": "send-once-xyz"}
            s1 = self.c.post(f"/api/outreach/emails/{eid}/send", headers=dict(self.H["agent_a1"], **key))
            s2 = self.c.post(f"/api/outreach/emails/{eid}/send", headers=dict(self.H["agent_a1"], **key))
            self.assertEqual(s1.status_code, 200, s1.get_json())
            self.assertEqual(s2.status_code, 200)
            self.assertEqual(self.c.post(f"/api/outreach/emails/{eid}/send", headers=self.H["agent_a1"]).status_code, 409)
            self.assertEqual(mailer._process_one(), "sent")
            self.assertEqual(len(smtp.messages), 1)
            self.assertIn("Edited by agent.", smtp.messages[0]["data"])
            self.assertIn("SEO-Audit-Report.docx", smtp.messages[0]["data"])
            st = self.c.get(f"/api/outreach/emails/{eid}", headers=self.H["agent_a1"]).get_json()["email"]
            self.assertEqual(st["status"], "sent")
            self.assertEqual(self.c.get(f"/api/outreach/emails/{eid}", headers=self.H["agent_a2"]).status_code, 404)
            lead_now = self.c.get(f"/api/leads/{lead['id']}", headers=self.H["agent_a1"]).get_json()["lead"]
            self.assertEqual(lead_now["status"], "contacted")

            # Bulk: preview + explicit confirmation; recently contacted lead is skipped.
            prev = self.c.post("/api/outreach/bulk/preview", json={"lead_ids": [lead["id"]]}, headers=self.H["agent_a1"]).get_json()
            self.assertEqual(prev["recipient_count"], 0)
            self.assertEqual(self.c.post("/api/outreach/bulk", json={"lead_ids": [lead["id"]]},
                                         headers=self.H["agent_a1"]).status_code, 422)
        finally:
            smtp.close()

    def test_cancel_running_audit(self):
        lead = self.create_lead("manager", agent_id=self.agent_a2["id"])
        self.gate.clear()
        audit_id = self.c.post("/api/audits", json={"lead_id": lead["id"]}, headers=self.H["agent_a2"]).get_json()["audit"]["id"]
        self._wait(audit_id, self.H["agent_a2"], ("running",))
        self.assertEqual(self.c.post(f"/api/audits/{audit_id}/cancel", headers=self.H["agent_a1"]).status_code, 404)
        self.assertEqual(self.c.post(f"/api/audits/{audit_id}/cancel", headers=self.H["agent_a2"]).status_code, 200)
        self.gate.set()
        done = self._wait(audit_id, self.H["agent_a2"])
        self.assertEqual(done["status"], "cancelled")

    def test_ssrf_targets_rejected(self):
        from backend.errors import ApiError
        for url in ("http://127.0.0.1", "http://localhost:5055", "http://10.0.0.5", "http://169.254.169.254/latest",
                    "ftp://example.com"):
            with self.assertRaises(ApiError, msg=url):
                self._orig_validate(url)


class ChatTests(Base):
    def test_direct_message_permissions(self):
        r = self.c.post("/api/chat/conversations/direct", json={"user_id": self.agent_b1["id"]}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 404)
        r = self.c.post("/api/chat/conversations/direct", json={"user_id": self.lead_b["id"]}, headers=self.H["lead_a"])
        self.assertEqual(r.status_code, 404)
        r = self.c.post("/api/chat/conversations/direct", json={"user_id": self.manager["id"]}, headers=self.H["agent_b1"])
        self.assertEqual(r.status_code, 200)
        conv = self.c.post("/api/chat/conversations/direct", json={"user_id": self.agent_a2["id"]},
                           headers=self.H["agent_a1"]).get_json()["conversation"]
        m = self.c.post(f"/api/chat/conversations/{conv['id']}/messages", json={"body": "Hello teammate"},
                        headers=self.H["agent_a1"])
        self.assertEqual(m.status_code, 201)
        msg = m.get_json()["message"]
        listed = self.c.get(f"/api/chat/conversations/{conv['id']}/messages", headers=self.H["agent_a2"]).get_json()["items"]
        self.assertEqual(listed[-1]["body"], "Hello teammate")
        self.assertEqual(self.c.get(f"/api/chat/conversations/{conv['id']}/messages", headers=self.H["agent_b1"]).status_code, 404)
        self.assertEqual(self.c.patch(f"/api/chat/messages/{msg['id']}", json={"body": "hacked"},
                                      headers=self.H["agent_a2"]).status_code, 403)
        self.assertEqual(self.c.patch(f"/api/chat/messages/{msg['id']}", json={"body": "Hello again"},
                                      headers=self.H["agent_a1"]).status_code, 200)
        self.assertEqual(self.c.post(f"/api/chat/messages/{msg['id']}/reactions", json={"reaction": "like"},
                                     headers=self.H["agent_a2"]).status_code, 200)
        reply = self.c.post(f"/api/chat/conversations/{conv['id']}/messages", json={"body": "Reply", "reply_to_id": msg["id"]},
                            headers=self.H["agent_a2"]).get_json()["message"]
        self.assertEqual(reply["reply_to"]["id"], msg["id"])
        self.assertEqual(self.c.delete(f"/api/chat/messages/{msg['id']}", headers=self.H["agent_a1"]).status_code, 200)
        hits = self.c.get("/api/chat/search?q=hello again", headers=self.H["agent_b1"]).get_json()["items"]
        self.assertEqual(hits, [])

    def test_team_channel_isolation(self):
        convs = self.c.get("/api/chat/conversations", headers=self.H["agent_a1"]).get_json()["items"]
        team = [c for c in convs if c["type"] == "team"]
        self.assertEqual([c["team_id"] for c in team], [self.team_a])
        b_conv = [c for c in self.c.get("/api/chat/conversations", headers=self.H["agent_b1"]).get_json()["items"]
                  if c["type"] == "team"][0]
        r = self.c.post(f"/api/chat/conversations/{b_conv['id']}/messages", json={"body": "intrude"}, headers=self.H["agent_a1"])
        self.assertEqual(r.status_code, 404)
        mgr = self.c.get("/api/chat/conversations", headers=self.H["manager"]).get_json()["items"]
        self.assertGreaterEqual(len([c for c in mgr if c["type"] == "team"]), 2)

    def test_upload_validation(self):
        fake_pdf = (io.BytesIO(b"MZ\x90\x00 this is an exe"), "cv.pdf")
        r = self.c.post("/api/chat/uploads", data={"file": fake_pdf}, headers=self.H["agent_a1"],
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 422)
        exe = (io.BytesIO(b"MZ\x90\x00"), "tool.exe")
        r = self.c.post("/api/chat/uploads", data={"file": exe}, headers=self.H["agent_a1"], content_type="multipart/form-data")
        self.assertEqual(r.status_code, 422)
        good = (io.BytesIO(b"%PDF-1.4\n%test\n"), "../../etc/passwd.pdf")
        r = self.c.post("/api/chat/uploads", data={"file": good}, headers=self.H["agent_a1"], content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["upload"]["name"], "passwd.pdf")
        up_id = r.get_json()["upload"]["id"]
        self.assertEqual(self.c.get(f"/api/chat/attachments/{up_id}", headers=self.H["agent_b1"]).status_code, 404)


class NotificationsAndDashboardTests(Base):
    def test_cv_bank_removed(self):
        self.assertEqual(self.c.get("/api/cv-bank", headers=self.H["manager"]).status_code, 404)
        self.assertNotIn("candidates", [r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")])

    def test_notifications_scoped_and_mark_read(self):
        self.create_lead("manager", agent_id=self.agent_a1["id"])
        n = self.c.get("/api/notifications", headers=self.H["agent_a1"]).get_json()
        self.assertGreaterEqual(n["unread"], 1)
        other_id = n["items"][0]["id"]
        self.c.post("/api/notifications/read", json={"ids": [other_id]}, headers=self.H["agent_b1"])
        still = db.scalar("SELECT is_read FROM notifications WHERE id = %s", (other_id,))
        self.assertEqual(still, 0)
        r = self.c.post("/api/notifications/read", json={"all": True}, headers=self.H["agent_a1"]).get_json()
        self.assertEqual(r["unread"], 0)

    def test_dashboard_per_role(self):
        for who in ("manager", "lead_a", "agent_a1"):
            r = self.c.get("/api/dashboard", headers=self.H[who])
            self.assertEqual(r.status_code, 200)
            self.assertIn("overview", r.get_json())


if __name__ == "__main__":
    unittest.main()
