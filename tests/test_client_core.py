# -*- coding: utf-8 -*-
"""
Desktop client core tests: the API client's session handling against a real
in-process API server.
"""

import socket
import tempfile
import threading
import unittest

from tests import helpers as h  # sets up the isolated server environment first

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from werkzeug.serving import make_server

from seo_audit_pro.core import api, settings
from seo_audit_pro.core.session import session

# A QApplication (not QCoreApplication): other test modules build widgets in the same process.
_APP = QCoreApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ApiClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = make_server("127.0.0.1", cls.port, h.APP, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._orig_url = settings.api_url
        settings.api_url = lambda: f"http://127.0.0.1:{cls.port}"
        team = h.make_team()
        cls.user = h.make_user("AGENT", team)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        settings.api_url = cls._orig_url

    def setUp(self):
        payload = api.post("/api/auth/login", {"email": self.user["email"], "password": h.PASSWORD}, auth=False)
        session.start(payload, remember=False)

    def tearDown(self):
        session.end()

    def test_transparent_refresh_when_access_token_is_invalid(self):
        session.access_token = "expired.token"
        me = api.get("/api/auth/me")
        self.assertEqual(me["user"]["email"], self.user["email"])
        self.assertNotEqual(session.access_token, "expired.token")

    def test_session_expired_when_refresh_fails(self):
        reasons = []
        session.expired.connect(reasons.append)
        session.access_token = "expired.token"
        session.refresh_token = "revoked"
        with self.assertRaises(api.ApiError) as ctx:
            api.get("/api/auth/me")
        self.assertEqual(ctx.exception.status, 401)
        self.assertTrue(reasons)
        self.assertIsNone(session.access_token)

    def test_forbidden_and_not_found_errors_are_friendly(self):
        with self.assertRaises(api.ApiError) as ctx:
            api.get("/api/audit-logs")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(api.ApiError) as ctx:
            api.get("/api/leads/lead_does_not_exist")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("not found", ctx.exception.message.lower())

    def test_idempotent_replay(self):
        key = api.new_idempotency_key()
        a = api.post("/api/leads", {"company": "Replay Co", "website": "replay-client.example"}, idempotency_key=key)
        b = api.post("/api/leads", {"company": "Replay Co", "website": "replay-client.example"}, idempotency_key=key)
        self.assertEqual(a["lead"]["id"], b["lead"]["id"])

    def test_offline_server(self):
        settings.api_url = lambda: f"http://127.0.0.1:{_free_port()}"
        try:
            with self.assertRaises(api.ApiError) as ctx:
                api.get("/api/auth/me")
            self.assertTrue(ctx.exception.offline)
            self.assertEqual(session.connection, "offline")
        finally:
            settings.api_url = lambda: f"http://127.0.0.1:{self.port}"

    def test_download_streams_to_disk(self):
        target = tempfile.mkdtemp()
        lead = api.post("/api/leads", {"company": "Dl Co", "website": "download-client.example"})["lead"]
        self.assertTrue(lead["id"])
        # The avatar endpoint returns 404 JSON for users without a picture.
        with self.assertRaises(api.ApiError) as ctx:
            api.download(f"/api/users/{self.user['id']}/avatar", target)
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
