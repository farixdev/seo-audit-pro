# -*- coding: utf-8 -*-
"""Local client preferences (QSettings / Windows registry). Contains only
non-secret values such as the API address and UI state."""

import os
import sys
import uuid

from PySide6.QtCore import QSettings

from seo_audit_pro import APP_NAME, ORG_NAME, TEAM_SERVER_URL

DEFAULT_API_URL = os.environ.get("SEO_API_URL") or TEAM_SERVER_URL or "http://127.0.0.1:5055"
PROFILE = (os.environ.get("SEO_AUDIT_PRO_PROFILE") or "").strip()  # isolated settings, used for testing
_RUNTIME = {"local_url": None}

LOCAL, REMOTE = "local", "remote"


def _store():
    return QSettings(ORG_NAME, APP_NAME + (f" ({PROFILE})" if PROFILE else ""))


def mode():
    """"local" (this computer runs the server), "remote" (a team server), or
    None before the first-run choice. Running from source defaults to remote."""
    value = _store().value("mode")
    if value == REMOTE:
        saved_url = _store().value("api_url", "")
        if "onrender.com" in saved_url and not TEAM_SERVER_URL:
            _store().remove("mode")
            _store().remove("api_url")
            return LOCAL
        return REMOTE
    if value == LOCAL:
        return LOCAL
    if TEAM_SERVER_URL:  # this build connects to the team server
        return REMOTE
    return None  # First run on any computer: show the choice!


def set_mode(value):
    _store().setValue("mode", value)


def set_local_url(url):
    _RUNTIME["local_url"] = url


def api_url():
    if mode() == LOCAL:
        return (_RUNTIME["local_url"] or "http://127.0.0.1:5057").rstrip("/")
    url = _store().value("api_url", DEFAULT_API_URL) or DEFAULT_API_URL
    if "onrender.com" in url and not TEAM_SERVER_URL:
        return "http://127.0.0.1:5057"
    return url.rstrip("/")


def set_api_url(url):
    _store().setValue("api_url", url.strip().rstrip("/"))


def last_email():
    return _store().value("last_email", "") or ""


def set_last_email(email):
    _store().setValue("last_email", email)


def device_id():
    """A random id for this installation, so the server knows which computer runs an audit."""
    store = _store()
    value = store.value("device_id") or ""
    if len(value) < 16:
        value = uuid.uuid4().hex
        store.setValue("device_id", value)
    return value


def get(key, default=None):
    return _store().value(key, default)


def put(key, value):
    _store().setValue(key, value)


def asset_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "seo_audit_pro", "assets")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def downloads_dir():
    """Documents\\SEO Audit Pro; when Windows blocks writing there (Controlled
    folder access, an OneDrive folder that is offline, a read-only drive) the
    app's own folder in AppData is used instead, so saving never fails."""
    path = os.path.join(os.path.expanduser("~"), "Documents", "SEO Audit Pro")
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".write-test-{os.getpid()}")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return path
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        fallback = os.path.join(base, "SEO Audit Pro", "Documents")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "SEO Audit Pro", "logs")
    os.makedirs(path, exist_ok=True)
    return path
