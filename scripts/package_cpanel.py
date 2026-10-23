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
         "security_audit.py", "pagespeed.py", "pdf_convert.py", "mindcob_report.py", "google_maps_scraper.py",
         "requirements-cpanel.txt"]
DIRS = ["backend", "templates", "assets"]
EXTRA = ["static/libs/lucide.min.js"]

ENV = """# SEO Audit Pro / Rankly team server settings (cPanel).
# Pre-configured with your active Neon cloud PostgreSQL database.
SEO_ENV=production
SEO_HOSTING=cpanel
AUDITS_RUN_ON=app

# Active Neon PostgreSQL database (pre-configured)
DATABASE_URL=postgresql://neondb_owner:npg_YcKQEXg53Zzf@ep-winter-dawn-b4l5t22n-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require

# First Manager account (change password upon first login)
BOOTSTRAP_MANAGER_EMAIL=admin@mindcob.com
BOOTSTRAP_MANAGER_PASSWORD=Change-Me-Now-2026
BOOTSTRAP_MANAGER_NAME=Mindcob Manager

# Security keys (auto-generated)
SECRET_KEY={secret}
DATA_ENCRYPTION_KEY={data_key}

MAX_CONCURRENT_AUDITS=2

PAGESPEED_API_KEY=AIzaSyDKAEwTDdDdyRWHX4mr7cr5yb2PR4_S8l4
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
