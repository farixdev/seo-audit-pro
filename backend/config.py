# -*- coding: utf-8 -*-
"""
Server configuration, read once from the environment (.env is loaded by the
project-level ``config`` module).

Secrets live ONLY on the server. The desktop client never receives any of
these values.
"""

import os
import secrets
import sys

import config as _env_loader

_env_loader.load_env()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):
    ROOT_DIR = os.path.dirname(sys.executable)

DATA_DIR = os.environ.get("SEO_DATA_DIR") or os.path.join(ROOT_DIR, "server_data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
for _d in (DATA_DIR, UPLOAD_DIR, REPORTS_DIR):
    os.makedirs(_d, exist_ok=True)

# Runtime environment: "production" refuses to start without real secrets.
ENVIRONMENT = (os.environ.get("SEO_ENV") or "development").lower()

# Database selection. PostgreSQL is used only in production or when explicitly
# requested with SEO_DB=postgres; otherwise a DATABASE_URL found in a local
# .env file is ignored so development runs can never touch a production
# database by accident. Clearing the variable also covers the engine's history
# store, which reads the environment directly.
_SEO_DB = (os.environ.get("SEO_DB") or "").lower()
if _SEO_DB == "sqlite" or (ENVIRONMENT != "production" and _SEO_DB != "postgres"):
    if os.environ.get("DATABASE_URL") and _SEO_DB != "sqlite":
        print("[SEO Audit Pro] Development mode: ignoring DATABASE_URL and using local SQLite. "
              "Set SEO_DB=postgres to use PostgreSQL outside production.")
    os.environ["DATABASE_URL"] = ""
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
SQLITE_PATH = os.environ.get("SEO_SQLITE_PATH") or os.path.join(DATA_DIR, "seo_audit_pro.db")


def _load_or_create_dev_secret(name):
    """Development only: persist a random secret under DATA_DIR so tokens and
    encrypted SMTP passwords survive restarts. Never used in production."""
    path = os.path.join(DATA_DIR, f".{name.lower()}")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    val = secrets.token_urlsafe(48)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(val)
    return val


def _secret(name):
    val = (os.environ.get(name) or "").strip()
    if val:
        return val
    if ENVIRONMENT == "production":
        raise RuntimeError(f"{name} must be set in production.")
    return _load_or_create_dev_secret(name)


SECRET_KEY = _secret("SECRET_KEY")
# Separate key for encrypting stored credentials (SMTP password). Rotating
# SECRET_KEY must not make stored SMTP credentials unreadable.
DATA_ENCRYPTION_KEY = _secret("DATA_ENCRYPTION_KEY")

ACCESS_TOKEN_TTL = int(os.environ.get("ACCESS_TOKEN_TTL", 30 * 60))          # 30 min
REFRESH_TOKEN_TTL = int(os.environ.get("REFRESH_TOKEN_TTL", 14 * 24 * 3600))  # 14 days
PASSWORD_RESET_TTL = 30 * 60
LOGIN_MAX_FAILURES = 5
LOGIN_ATTEMPTS_PER_IP_5MIN = int(os.environ.get("LOGIN_ATTEMPTS_PER_IP_5MIN", 30))
LOGIN_LOCK_WINDOW = 15 * 60

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_CONCURRENT_AUDITS = int(os.environ.get("MAX_CONCURRENT_AUDITS", 2))  # default; Managers can change it in Settings

# Bulk outreach safety controls
BULK_MAX_RECIPIENTS = int(os.environ.get("BULK_MAX_RECIPIENTS", 200))
EMAIL_SEND_INTERVAL_SECONDS = float(os.environ.get("EMAIL_SEND_INTERVAL_SECONDS", 4))
DAILY_EMAIL_LIMIT = int(os.environ.get("DAILY_EMAIL_LIMIT", 500))

# Optional bootstrap of the very first Manager account (empty database only).
BOOTSTRAP_MANAGER_EMAIL = (os.environ.get("BOOTSTRAP_MANAGER_EMAIL") or "").strip().lower()
BOOTSTRAP_MANAGER_PASSWORD = os.environ.get("BOOTSTRAP_MANAGER_PASSWORD") or ""
BOOTSTRAP_MANAGER_NAME = os.environ.get("BOOTSTRAP_MANAGER_NAME") or "Agency Manager"

APP_VERSION = "3.0.0"
