# -*- coding: utf-8 -*-
"""Test harness: isolated SQLite database + data directory per test run."""

import os
import socketserver
import sys
import tempfile
import threading
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="seo_audit_pro_test_")
os.environ["SEO_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = ""          # never touch a real database from tests
os.environ["SEO_DB"] = "sqlite"
os.environ["SEO_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-" + uuid.uuid4().hex
os.environ["DATA_ENCRYPTION_KEY"] = "test-data-key-" + uuid.uuid4().hex
os.environ["EMAIL_SEND_INTERVAL_SECONDS"] = "0"
os.environ["LOGIN_ATTEMPTS_PER_IP_5MIN"] = "100000"  # every test client shares 127.0.0.1
for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    os.environ[key] = ""

from backend import db, security  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.util import now_iso  # noqa: E402

APP = create_app(start_workers=False)
PASSWORD = "Correct-Horse-42"


def client():
    return APP.test_client()


def make_team(name=None):
    tid = security.new_id("team_")
    name = name or f"Team {uuid.uuid4().hex[:6]}"
    db.execute("INSERT INTO teams (id, name, name_key, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
               (tid, name, name.lower(), now_iso(), now_iso()))
    return tid


def make_user(role, team_id=None, password=PASSWORD, must_change=False, active=True):
    uid = security.new_id("usr_")
    email = f"{role.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    db.execute("""INSERT INTO users (id, name, email, password_hash, role, team_id, is_active, must_change_password,
                  created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (uid, f"{role.title()} {uid[-4:]}", email, security.hash_password(password), role,
                None if role == "MANAGER" else team_id, 1 if active else 0, 1 if must_change else 0, now_iso(), now_iso()))
    return {"id": uid, "email": email, "role": role, "team_id": team_id}


def login(c, user, password=PASSWORD):
    r = c.post("/api/auth/login", json={"email": user["email"], "password": password})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data


class FakeSMTP:
    """Minimal plaintext SMTP server that records delivered messages."""

    def __init__(self):
        self.messages = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 fake ESMTP\r\n")
                data_mode, buf, rcpt = False, [], []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    if data_mode:
                        if line in (b".\r\n", b".\n"):
                            data_mode = False
                            outer.messages.append({"rcpt": rcpt[:], "data": b"".join(buf).decode("utf-8", "replace")})
                            buf, rcpt = [], []
                            self.wfile.write(b"250 queued\r\n")
                        else:
                            buf.append(line)
                        continue
                    cmd = line.strip().upper()
                    if cmd.startswith(b"EHLO") or cmd.startswith(b"HELO"):
                        self.wfile.write(b"250-fake\r\n250 OK\r\n")
                    elif cmd.startswith(b"MAIL") or cmd.startswith(b"RSET") or cmd.startswith(b"NOOP"):
                        self.wfile.write(b"250 OK\r\n")
                    elif cmd.startswith(b"RCPT"):
                        rcpt.append(line.decode().split(":", 1)[1].strip())
                        self.wfile.write(b"250 OK\r\n")
                    elif cmd == b"DATA":
                        data_mode = True
                        self.wfile.write(b"354 go\r\n")
                    elif cmd == b"QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"502 not implemented\r\n")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
