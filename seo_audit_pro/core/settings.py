# -*- coding: utf-8 -*-
"""Local client preferences (QSettings / Windows registry). Contains only
non-secret values such as the API address and UI state."""

import os
import sys

from PySide6.QtCore import QSettings

from seo_audit_pro import APP_NAME, ORG_NAME

DEFAULT_API_URL = os.environ.get("SEO_API_URL", "http://127.0.0.1:5055")


def _store():
    return QSettings(ORG_NAME, APP_NAME)


def api_url():
    return (_store().value("api_url", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")


def set_api_url(url):
    _store().setValue("api_url", url.strip().rstrip("/"))


def last_email():
    return _store().value("last_email", "") or ""


def set_last_email(email):
    _store().setValue("last_email", email)


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
    path = os.path.join(os.path.expanduser("~"), "Documents", "SEO Audit Pro")
    os.makedirs(path, exist_ok=True)
    return path


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "SEO Audit Pro", "logs")
    os.makedirs(path, exist_ok=True)
    return path
