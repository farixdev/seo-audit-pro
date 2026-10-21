# -*- coding: utf-8 -*-
"""
"This computer" mode: first-run Manager setup (fresh database, local only,
once), the PageSpeed key setting, and the built-in server process (starts with
its own data folder, answers, and exits when the app that started it is gone).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

import requests

from tests import helpers as h
from backend import audit_runner, db
from seo_audit_pro import local_server

ROOT = h.ROOT

_FRESH_SETUP = r"""
import json, os, sys
sys.path.insert(0, os.environ["ROOT"])
from backend.app import create_app
app = create_app(start_workers=False)
c = app.test_client()
out = {}
out["status"] = c.get("/api/setup/status").get_json()
out["remote"] = c.post("/api/setup/manager", json={"name": "A", "email": "a@x.co", "password": "Strong-Pass-2026"},
                       environ_base={"REMOTE_ADDR": "10.1.2.3"}).status_code
out["weak"] = c.post("/api/setup/manager", json={"name": "A", "email": "a@x.co", "password": "weak"}).status_code
r = c.post("/api/setup/manager", json={"name": "Alex", "email": "alex@x.co", "password": "Strong-Pass-2026"})
out["create"] = r.status_code
out["user"] = r.get_json()["user"]
out["has_tokens"] = bool(r.get_json().get("access_token") and r.get_json().get("refresh_token"))
me = c.get("/api/auth/me", headers={"Authorization": "Bearer " + r.get_json()["access_token"]})
out["me"] = me.status_code
out["again"] = c.post("/api/setup/manager", json={"name": "B", "email": "b@x.co", "password": "Strong-Pass-2026"}).get_json()
out["status_after"] = c.get("/api/setup/status").get_json()
out["login"] = c.post("/api/auth/login", json={"email": "alex@x.co", "password": "Strong-Pass-2026"}).status_code
print("RESULT" + json.dumps(out))
"""


def _env(data_dir):
    env = dict(os.environ, ROOT=ROOT, SEO_DATA_DIR=data_dir, SEO_DB="sqlite", SEO_ENV="test", DATABASE_URL="")
    env.pop("SECRET_KEY", None)
    env.pop("DATA_ENCRYPTION_KEY", None)
    return env


class FirstRunSetupTests(unittest.TestCase):
    def test_first_manager_is_created_once_and_only_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-c", _FRESH_SETUP], env=_env(tmp), capture_output=True, text=True,
                               timeout=120)
            line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")), None)
            self.assertIsNotNone(line, r.stderr[-2000:])
            out = json.loads(line[6:])
            self.assertEqual(out["status"], {"needs_setup": True, "this_computer": True})
            self.assertEqual(out["remote"], 403)          # not from another computer
            self.assertEqual(out["weak"], 422)            # password policy applies
            self.assertEqual(out["create"], 201)
            self.assertTrue(out["has_tokens"])
            self.assertEqual(out["user"]["role"], "MANAGER")
            self.assertFalse(out["user"]["must_change_password"])
            self.assertEqual(out["me"], 200)
            self.assertEqual(out["again"]["code"], "already_set_up")
            self.assertFalse(out["status_after"]["needs_setup"])
            self.assertEqual(out["login"], 200)
            # The fresh install created its own keys and database in the data folder.
            names = os.listdir(tmp)
            self.assertIn("seo_audit_pro.db", names)
            self.assertIn(".secret_key", names)

    def test_setup_is_closed_once_accounts_exist(self):
        h.make_user("MANAGER")
        c = h.client()
        self.assertFalse(c.get("/api/setup/status").get_json()["needs_setup"])
        r = c.post("/api/setup/manager", json={"name": "X", "email": f"x{uuid.uuid4().hex[:6]}@x.co",
                                               "password": "Strong-Pass-2026"})
        self.assertEqual(r.status_code, 409)


class PageSpeedKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = h.client()
        cls.manager = h.make_user("MANAGER")
        cls.agent = h.make_user("AGENT", h.make_team())
        cls.H = {n: h.login(cls.c, getattr(cls, n))[0] for n in ("manager", "agent")}

    def test_key_is_saved_encrypted_and_used(self):
        saved_env = os.environ.pop("PAGESPEED_API_KEY", None)  # a developer's .env may define one
        self.addCleanup(lambda: saved_env and os.environ.__setitem__("PAGESPEED_API_KEY", saved_env))
        key = "AIzaSy" + "A" * 33
        r = self.c.put("/api/settings", json={"pagespeed_api_key": key}, headers=self.H["manager"])
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["pagespeed_key_set"])
        stored = db.scalar("SELECT value FROM app_settings WHERE key = 'pagespeed_key'")
        self.assertNotIn(key, stored)
        self.assertEqual(audit_runner.pagespeed_key(), key)
        self.assertEqual(self.c.put("/api/settings", json={"pagespeed_api_key": "not a key!"},
                                    headers=self.H["manager"]).status_code, 422)
        self.assertEqual(self.c.put("/api/settings", json={"pagespeed_api_key": key},
                                    headers=self.H["agent"]).status_code, 403)
        r = self.c.put("/api/settings", json={"pagespeed_api_key": ""}, headers=self.H["manager"])
        self.assertFalse(r.get_json()["pagespeed_key_set"])
        self.assertIsNone(audit_runner.pagespeed_key())


class BuiltInServerTests(unittest.TestCase):
    def test_server_starts_with_its_own_data_and_exits_with_its_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            port = local_server.pick_port(None)
            env = _env(tmp)
            env.pop("SEO_DATA_DIR")
            server = subprocess.Popen([sys.executable, "-m", "seo_audit_pro.local_server", "--serve", "--port",
                                       str(port), "--data", tmp, "--parent", str(parent.pid)], env=env, cwd=ROOT,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                status = None
                for _ in range(200):
                    try:
                        status = requests.get(f"http://127.0.0.1:{port}/api/setup/status", timeout=1).json()
                        break
                    except requests.RequestException:
                        time.sleep(0.2)
                self.assertEqual(status, {"needs_setup": True, "this_computer": True})
                self.assertTrue(os.path.isfile(os.path.join(tmp, "seo_audit_pro.db")))
                self.assertEqual(json.load(open(os.path.join(tmp, "server.json")))["port"], port)
                parent.kill()  # the app went away (closed or crashed)
                server.wait(timeout=15)
                self.assertIsNotNone(server.returncode)
            finally:
                for p in (server, parent):
                    if p.poll() is None:
                        p.kill()
                server.wait(timeout=10)

    def test_pick_port_skips_busy_ports(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            busy = s.getsockname()[1]
            self.assertNotEqual(local_server.pick_port(busy), busy)


if __name__ == "__main__":
    unittest.main()
