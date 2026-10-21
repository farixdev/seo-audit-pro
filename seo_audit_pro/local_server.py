# -*- coding: utf-8 -*-
"""
"This computer" mode: the desktop app runs its own API server.

The server is a second, windowless process of the same program
(``SEOAuditPro.exe --serve``), so audits never slow down the window. It keeps
everything in one data folder (database, reports, uploads, logs and its own
secret keys, all created on first start) and exits as soon as the app that
started it closes — even if the app crashes.

The app still talks to it over HTTP exactly like a remote server, so the
desktop never touches the database directly.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time

PROFILE = (os.environ.get("SEO_AUDIT_PRO_PROFILE") or "").strip()  # isolated settings/data, used for testing
DEFAULT_PORT = 5057
_SPAWN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def app_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "SEO Audit Pro" + (f" ({PROFILE})" if PROFILE else ""))
    os.makedirs(path, exist_ok=True)
    return path


def data_dir():
    path = os.path.join(app_dir(), "data")
    os.makedirs(os.path.join(path, "logs"), exist_ok=True)
    return path


def lan_address():
    """This computer's address on the local network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _port_free(port, host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(preferred=None, host="127.0.0.1"):
    for port in [p for p in (preferred, DEFAULT_PORT) if p] + list(range(DEFAULT_PORT + 1, DEFAULT_PORT + 40)):
        if _port_free(port, host):
            return port
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class StartError(Exception):
    pass


class LocalServer:
    """Starts, watches and stops the background server process (client side)."""

    def __init__(self):
        self.proc = None
        self.port = None
        self.shared = False
        self._lock = threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def _command(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--serve"]
        exe = sys.executable
        if exe.lower().endswith("python.exe"):  # no console window when started from source
            alt = exe[:-10] + "pythonw.exe"
            exe = alt if os.path.isfile(alt) else exe
        return [exe, "-m", "seo_audit_pro.local_server", "--serve"]

    def start(self, share=False, preferred_port=None, timeout=120):
        """Start (or restart) the server and wait until it answers. Returns its URL."""
        with self._lock:
            self._stop_locked()
            host = "0.0.0.0" if share else "127.0.0.1"
            self.port = pick_port(preferred_port, host)
            self.shared = share
            data = data_dir()
            log_path = os.path.join(data, "logs", "server-start.log")
            log = open(log_path, "ab")
            cmd = self._command() + ["--port", str(self.port), "--host", host, "--data", data,
                                     "--parent", str(os.getpid())]
            try:
                self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                             creationflags=_SPAWN_FLAGS, close_fds=True)
            except OSError as e:
                raise StartError(f"The audit server could not be started: {e}")
            finally:
                log.close()
            self._wait_ready(timeout, log_path)
            return self.url

    def _wait_ready(self, timeout, log_path):
        import requests
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise StartError("The audit server stopped while starting. Details are in "
                                 f"{log_path}:\n{_tail(log_path)}")
            try:
                r = requests.get(self.url + "/api/health", timeout=2)
                if r.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.3)
        self._stop_locked()
        raise StartError(f"The audit server did not start within {timeout} seconds. Details are in {log_path}.")

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _tail(path, lines=12):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 4000))
            return "\n".join(fh.read().decode("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return ""


_SERVER = LocalServer()


def instance():
    return _SERVER


# ---------------------------------------------------------------------------
# Server process side
# ---------------------------------------------------------------------------
def _exit_with_parent(pid):
    """Exit as soon as the desktop app that started us is gone."""
    if not pid or sys.platform != "win32":
        return
    import ctypes
    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        os._exit(0)

    def wait():
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(0)
    threading.Thread(target=wait, name="parent-watch", daemon=True).start()


def serve(argv=None):
    parser = argparse.ArgumentParser(prog="SEOAuditPro --serve")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", default=None)
    parser.add_argument("--parent", type=int, default=0)
    args, _unknown = parser.parse_known_args(argv)
    data = args.data or data_dir()
    os.makedirs(os.path.join(data, "logs"), exist_ok=True)
    # A windowless program has no console: keep server output in a log file.
    out = open(os.path.join(data, "logs", "server-console.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = out
    # Local installs always use their own SQLite database, never a DATABASE_URL
    # that might be lying around in a .env file.
    os.environ.update(SEO_DATA_DIR=data, SEO_DB="sqlite", SEO_ENV="local", DATABASE_URL="", PORT=str(args.port))
    _exit_with_parent(args.parent)
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting on {args.host}:{args.port}, data in {data}")
    import logging
    import web_app
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no line per request in the log
    with open(os.path.join(data, "server.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "port": args.port, "host": args.host}, fh)
    web_app.start_server(port=args.port, host=args.host)


if __name__ == "__main__":
    serve(sys.argv[1:])
