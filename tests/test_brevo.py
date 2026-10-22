# -*- coding: utf-8 -*-
"""Email through Brevo's web API (HTTPS): settings, key kept secret, connection
test with sender check, sending with Reply-To and attachments. Brevo is simulated."""

import base64
import json
import unittest
from unittest import mock

from tests import helpers as h
from backend import db, mailer
from tests.test_api import Base


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeBrevo:
    def __init__(self, key="xkeysib-good", senders=("team@agency.test",)):
        self.key, self.senders, self.sent = key, senders, []

    def __call__(self, method, url, json=None, timeout=None, headers=None):
        if headers.get("api-key") != self.key:
            return FakeResponse(401, {"code": "unauthorized", "message": "Key not found"})
        path = url.split("/v3", 1)[1]
        if path == "/account":
            return FakeResponse(200, {"email": "owner@agency.test"})
        if path == "/senders":
            return FakeResponse(200, {"senders": [{"email": s} for s in self.senders]})
        if path == "/senders/domains":
            return FakeResponse(200, {"domains": []})
        if path == "/smtp/email":
            if json["sender"]["email"] not in self.senders:
                return FakeResponse(400, {"code": "invalid_parameter", "message": "Sender is not valid"})
            self.sent.append(json)
            return FakeResponse(201, {"messageId": "<1@brevo>"})
        return FakeResponse(404, {})


class BrevoTests(Base):
    def settings(self, key="xkeysib-good", sender="team@agency.test"):
        return {"provider": "brevo", "password": key, "from_name": "Agency", "from_email": sender}

    def test_settings_test_and_send(self):
        fake = FakeBrevo()
        with mock.patch("requests.request", fake):
            r = self.c.post("/api/outreach/smtp/test", json=self.settings("xkeysib-wrong"), headers=self.H["manager"])
            self.assertEqual(r.status_code, 422)
            self.assertIn("API key", r.get_json()["message"])
            r = self.c.post("/api/outreach/smtp/test", json=self.settings(sender="nobody@other.test"),
                            headers=self.H["manager"])
            self.assertIn("not a verified sender", r.get_json()["message"])
            r = self.c.post("/api/outreach/smtp/test", json=self.settings(), headers=self.H["manager"])
            self.assertEqual(r.status_code, 200, r.get_json())

            r = self.c.put("/api/outreach/smtp", json=self.settings(), headers=self.H["manager"])
            self.assertEqual(r.status_code, 200, r.get_json())
            saved = r.get_json()
            self.assertEqual((saved["provider"], saved["configured"]), ("brevo", True))
            self.assertNotIn("xkeysib-good", json.dumps(saved))
            self.assertNotIn("xkeysib-good", db.scalar("SELECT password_enc FROM smtp_settings"))
            self.assertEqual(self.c.put("/api/outreach/smtp", json=self.settings(), headers=self.H["agent_a1"]).status_code,
                             403)
            # Saving again without the key keeps it; switching back to SMTP needs a password.
            keep = dict(self.settings(), password="")
            self.assertEqual(self.c.put("/api/outreach/smtp", json=keep, headers=self.H["manager"]).status_code, 200)
            smtp = {"provider": "smtp", "host": "smtp.gmail.com", "port": 587, "encryption": "starttls",
                    "username": "a@gmail.com", "from_name": "A", "from_email": "a@gmail.com"}
            self.assertEqual(self.c.put("/api/outreach/smtp", json=smtp, headers=self.H["manager"]).status_code, 422)

            # An agent's email goes out through Brevo with Reply-To and the attachment.
            lead = self.create_lead("agent_a1")
            up = self.c.post("/api/outreach/uploads", headers=self.H["agent_a1"], content_type="multipart/form-data",
                             data={"file": (__import__("io").BytesIO(b"%PDF-1.4 brochure"), "brochure.pdf")})
            em = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
                "lead_id": lead["id"], "to_email": "owner@client.test", "subject": "Your audit", "body": "Hello",
                "upload_ids": [up.get_json()["upload"]["id"]], "send": True})
            self.assertEqual(em.status_code, 201, em.get_json())
            for _ in range(5):
                if mailer._process_one() == "idle":
                    break
            msg = fake.sent[-1]
            self.assertEqual(msg["to"], [{"email": "owner@client.test"}])
            self.assertEqual(msg["sender"]["email"], "team@agency.test")
            self.assertEqual(msg["replyTo"], {"email": self.agent_a1["email"]})
            self.assertEqual(msg["attachment"][0]["name"], "brochure.pdf")
            self.assertEqual(base64.b64decode(msg["attachment"][0]["content"]), b"%PDF-1.4 brochure")
            st = self.c.get(f"/api/outreach/emails/{em.get_json()['email']['id']}",
                            headers=self.H["agent_a1"]).get_json()["email"]
            self.assertEqual(st["status"], "sent")

            # A rejected sender is reported on the email.
            fake.senders = ()
            em = self.c.post("/api/outreach/emails", headers=self.H["agent_a1"], json={
                "lead_id": lead["id"], "to_email": "second@client.test", "subject": "Hi", "body": "Hello", "send": True})
            for _ in range(5):
                if mailer._process_one() == "idle":
                    break
            st = self.c.get(f"/api/outreach/emails/{em.get_json()['email']['id']}",
                            headers=self.H["agent_a1"]).get_json()["email"]
            self.assertEqual(st["status"], "failed")
            self.assertIn("Senders", st["error"])
        db.execute("DELETE FROM smtp_settings")


if __name__ == "__main__":
    unittest.main()
