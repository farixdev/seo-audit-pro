# -*- coding: utf-8 -*-
"""Manual outreach: the app writes the email but does not send it; people record
on the lead whether they reached out, by email or call, and with what."""

import unittest

from backend import config, db
from tests.test_api import Base


class ManualOutreachTests(Base):
    def setUp(self):
        self._sending = config.EMAIL_SENDING
        config.EMAIL_SENDING = False

    def tearDown(self):
        config.EMAIL_SENDING = self._sending

    def mark(self, lead, who="agent_a1", **payload):
        return self.c.post(f"/api/leads/{lead['id']}/outreach", json=payload, headers=self.H[who])

    def test_record_email_and_call_outreach(self):
        import uuid
        phone = f"+1 555 {uuid.uuid4().int % 10**7:07d}"
        lead = self.create_lead("agent_a1", email=f"owner{phone[-7:]}@client.test", phone=phone)
        r = self.mark(lead, outreached=True, method="email", contact=lead["email"], note="Sent the audit summary")
        self.assertEqual(r.status_code, 201, r.get_json())
        got = r.get_json()["lead"]
        self.assertEqual((got["status"], got["outreach_method"], got["outreach_contact"]),
                         ("contacted", "email", lead["email"]))
        self.assertTrue(got["last_contact_at"])

        r = self.mark(lead, outreached=True, method="call", contact=phone)
        self.assertEqual(r.get_json()["lead"]["outreach_method"], "call")
        hist = self.c.get(f"/api/leads/{lead['id']}/outreach", headers=self.H["agent_a1"]).get_json()["items"]
        self.assertEqual([(h["method"], h["contact"]) for h in hist], [("call", phone), ("email", lead["email"])])
        self.assertEqual(hist[1]["note"], "Sent the audit summary")

        # Counted as contacted in the lead filter, and visible to the team lead and Managers.
        ids = [x["id"] for x in self.c.get("/api/leads?outreach=yes&page_size=200", headers=self.H["agent_a1"]).get_json()["items"]]
        self.assertIn(lead["id"], ids)
        log = self.c.get("/api/outreach/log", headers=self.H["lead_a"]).get_json()["items"]
        self.assertTrue(any(x["lead_id"] == lead["id"] for x in log))
        self.assertTrue(any(x["lead_id"] == lead["id"] for x in
                            self.c.get("/api/outreach/log", headers=self.H["manager"]).get_json()["items"]))
        self.assertFalse(any(x["lead_id"] == lead["id"] for x in
                             self.c.get("/api/outreach/log", headers=self.H["agent_b1"]).get_json()["items"]))
        calls = self.c.get("/api/outreach/log?method=call", headers=self.H["manager"]).get_json()["items"]
        self.assertTrue(all(x["method"] == "call" for x in calls))

        # "No": back to not outreached.
        r = self.mark(lead, outreached=False)
        got = r.get_json()["lead"]
        self.assertEqual((got["status"] in ("new", "assigned"), got["last_contact_at"], got["outreach_method"]), (True, None, None))
        self.assertIn("LEAD_OUTREACH", [x["action"] for x in db.query(
            "SELECT action FROM audit_logs WHERE entity_id = %s", (lead["id"],))])

    def test_checks(self):
        lead = self.create_lead("agent_a1")
        self.assertEqual(self.mark(lead, outreached=True, method="fax", contact="x").status_code, 422)
        self.assertEqual(self.mark(lead, outreached=True, method="email", contact="not-an-email").status_code, 422)
        self.assertEqual(self.mark(lead, outreached=True, method="call", contact="12").status_code, 422)
        self.assertEqual(self.mark(lead, "agent_b1", outreached=True, method="email",
                                   contact="a@b.test").status_code, 404)  # not their lead

    def test_the_app_does_not_send_email(self):
        lead = self.create_lead("agent_a1")
        r = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
            "lead_id": lead["id"], "to_email": "owner@client.test", "subject": "Hi", "body": "Hello", "send": True})
        self.assertEqual((r.status_code, r.get_json()["code"]), (410, "sending_off"))
        self.assertEqual(self.c.post("/api/outreach/bulk", json={"lead_ids": [lead["id"]], "confirm_count": 1},
                                     headers=self.H["agent_a1"]).status_code, 410)
        r = self.c.post("/api/auth/forgot-password", json={"email": self.agent_a1["email"]})
        self.assertEqual(r.get_json()["code"], "reset_by_manager")
        dash = self.c.get("/api/dashboard", headers=self.H["manager"]).get_json()["overview"]
        self.assertIn("outreach_30d", dash)


if __name__ == "__main__":
    unittest.main()
