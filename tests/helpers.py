# -*- coding: utf-8 -*-
"""Test harness: isolated database + data directory per test run.

By default every run uses a fresh SQLite file. With ``SEO_TEST_POSTGRES=1`` the
same tests run against a PostgreSQL TEST database (for example a Neon test
branch) whose URL is in ``SEO_TEST_DATABASE_URL`` or in
``server_data/test_database_url.txt``. That database is WIPED at the start of
the run, so it is refused when it points at the same host as the
``DATABASE_URL`` in ``.env`` (the live database).
"""

import os
import socketserver
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _live_database_host():
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("DATABASE_URL="):
                    return urlparse(line.split("=", 1)[1].strip().strip("'\"")).hostname
    except OSError:
        pass
    return None


def _postgres_test_url():
    url = (os.environ.get("SEO_TEST_DATABASE_URL") or "").strip()
    path = os.path.join(ROOT, "server_data", "test_database_url.txt")
    if not url and os.path.isfile(path):
        url = open(path, encoding="utf-8").read().strip()
    if not url:
        sys.exit("SEO_TEST_POSTGRES=1 needs SEO_TEST_DATABASE_URL or server_data/test_database_url.txt")
    def endpoint(h):  # Neon: the pooled and direct hosts of one database differ only by "-pooler"
        return (h or "").split(".")[0].replace("-pooler", "")
    host, live = urlparse(url).hostname, _live_database_host()
    if not host or host == live or (live and endpoint(host) == endpoint(live)):
        sys.exit("Refusing to run tests: the test database URL points at the live database in .env.")
    return url


def _one_postgres_run():
    """Two runs on the same test database would wipe each other's tables."""
    import atexit
    path = os.path.join(ROOT, "server_data", "postgres_tests.lock")
    try:
        with open(path, encoding="utf-8") as fh:
            other = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        other = 0
    if other and other != os.getpid():
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, other) if sys.platform == "win32" else None
        alive = bool(handle) if sys.platform == "win32" else os.path.exists(f"/proc/{other}")
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        if alive:
            sys.exit(f"Another PostgreSQL test run (process {other}) is using the test database.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(path) and os.remove(path))


def _wipe_postgres(url):
    import psycopg2
    from backend.db import _clean_pg_url
    conn = psycopg2.connect(_clean_pg_url(url), connect_timeout=15)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    conn.close()


_TMP = tempfile.mkdtemp(prefix="seo_audit_pro_test_")
os.environ["SEO_DATA_DIR"] = _TMP
POSTGRES = os.environ.get("SEO_TEST_POSTGRES") == "1"
WAIT_SCALE = 10 if POSTGRES else 1  # a remote test database answers far slower than a local file
if POSTGRES:
    _url = _postgres_test_url()
    os.environ["DATABASE_URL"] = _url
    os.environ["SEO_DB"] = "postgres"
else:
    os.environ["DATABASE_URL"] = ""      # never touch a real database from tests
    os.environ["SEO_DB"] = "sqlite"
os.environ["SEO_ENV"] = "test"
os.environ["EMAIL_SENDING"] = "1"  # the sending code stays tested; the app runs with it off
os.environ["AUDITS_RUN_ON"] = "server"  # the queue tests drive the server's runner; test_app_runner switches to "app"
os.environ["SECRET_KEY"] = "test-secret-key-" + uuid.uuid4().hex
os.environ["DATA_ENCRYPTION_KEY"] = "test-data-key-" + uuid.uuid4().hex
os.environ["EMAIL_SEND_INTERVAL_SECONDS"] = "0"
os.environ["LOGIN_ATTEMPTS_PER_IP_5MIN"] = "100000"  # every test client shares 127.0.0.1
for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    os.environ[key] = ""

if POSTGRES:
    _one_postgres_run()
    _wipe_postgres(_url)

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
