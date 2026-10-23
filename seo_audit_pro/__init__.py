# -*- coding: utf-8 -*-
"""SEO Audit Pro — Windows desktop client (PySide6).

The client talks to the Flask API over HTTPS only. It holds no database
credentials, SMTP passwords or server secrets.
"""

import os

APP_NAME = "Rankly"
APP_VERSION = "3.0.0"
APP_TAGLINE = "Website Intelligence Platform"
ORG_NAME = "Mindcob"

# The team server this build connects to. When set, the app skips the first-run
# "This computer / team server" choice and goes straight to sign-in there.
# Empty = ask on first run. SEO_TEAM_SERVER overrides it (testing).
TEAM_SERVER_URL = (os.environ.get("SEO_TEAM_SERVER") or "https://mindcob-seo-audit-pro.onrender.com").strip().rstrip("/")
