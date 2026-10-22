# -*- coding: utf-8 -*-
"""
Build the cPanel upload package of the team server:

  dist/cpanel/seo-audit-pro-server.zip     upload and extract this in cPanel
  dist/cpanel/CPANEL SETUP GUIDE.txt       step-by-step instructions (also inside the zip)

Only the light server is included (no audit engine, Word or charts: those run
in the desktop apps). The .env inside gets fresh random secrets; keep the zip private.
"""

import os
import secrets
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dist", "cpanel")
FILES = ["web_app.py", "passenger_wsgi.py", "config.py", "history_manager.py", "scoring.py", "seo_checks.py",
         "security_audit.py", "pagespeed.py", "pdf_convert.py", "requirements-cpanel.txt"]
DIRS = ["backend", "templates"]
EXTRA = ["static/libs/lucide.min.js"]

ENV = """# SEO Audit Pro team server settings (cPanel). Edit the lines marked EDIT.
# Keep this file private: it holds the keys that protect sign-ins and the email password.

SEO_ENV=production
SEO_HOSTING=cpanel
AUDITS_RUN_ON=app

# EDIT: your cPanel PostgreSQL database (cPanel > PostgreSQL Databases).
# Format: postgresql://DATABASE_USER:PASSWORD@localhost:5432/DATABASE_NAME
DATABASE_URL=postgresql://CPANELUSER_seo:PASSWORD@localhost:5432/CPANELUSER_seoaudit

# EDIT: the first Manager account, created the first time the server starts.
# The password is temporary: the app asks to change it at the first sign-in.
BOOTSTRAP_MANAGER_EMAIL=you@yourdomain.com
BOOTSTRAP_MANAGER_PASSWORD=Change-Me-Now-2026
BOOTSTRAP_MANAGER_NAME=Agency Manager

# Created for you. Do not change them later: changing DATA_ENCRYPTION_KEY makes the
# saved email password unreadable, changing SECRET_KEY signs everybody out.
SECRET_KEY={secret}
DATA_ENCRYPTION_KEY={data_key}

# Audits at a time on each person's computer (Managers can change it in Settings).
MAX_CONCURRENT_AUDITS=2

# Optional: Google PageSpeed API key (or save it later in the app's Settings).
PAGESPEED_API_KEY=
"""

REQUIREMENTS = """# SEO Audit Pro team server on cPanel: only what the light server needs.
flask>=3.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
psycopg2-binary>=2.9.9
cryptography>=42.0.0
"""


def main():
    with open(os.path.join(ROOT, "requirements-cpanel.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(REQUIREMENTS)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    guide = os.path.join(ROOT, "deploy", "cpanel", "CPANEL SETUP GUIDE.txt")
    zpath = os.path.join(OUT, "seo-audit-pro-server.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES + EXTRA:
            z.write(os.path.join(ROOT, f), f)
        for d in DIRS:
            for base, dirs, files in os.walk(os.path.join(ROOT, d)):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        continue
                    full = os.path.join(base, f)
                    z.write(full, os.path.relpath(full, ROOT))
        z.writestr(".env", ENV.format(secret=secrets.token_urlsafe(48), data_key=secrets.token_urlsafe(48)))
        z.writestr("server_data/.keep", "")
        z.write(guide, "CPANEL SETUP GUIDE.txt")
    shutil.copy(guide, os.path.join(OUT, "CPANEL SETUP GUIDE.txt"))
    size = os.path.getsize(zpath) / 1024
    print(f"cPanel package: {zpath} ({size:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())
