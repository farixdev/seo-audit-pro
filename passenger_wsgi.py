# -*- coding: utf-8 -*-
"""cPanel "Setup Python App" (Passenger) entry point for the SEO Audit Pro team server.

Settings come from the .env file next to this file (see deploy/cpanel/README).
Shared-hosting mode: no background threads or WebSocket; a cron job sends email.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ.setdefault("SEO_HOSTING", "cpanel")

from web_app import app as application  # noqa: E402,F401
