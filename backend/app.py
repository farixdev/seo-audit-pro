# -*- coding: utf-8 -*-
"""Flask application factory for the SEO Audit Pro API server."""

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask

from backend import config


def _configure_logging():
    log_dir = os.path.join(config.DATA_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(log_dir, "server.log"), maxBytes=5 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("seo_audit_pro")
    if not root.handlers:
        root.addHandler(handler)
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)


def bootstrap_manager():
    """Create the first Manager from BOOTSTRAP_MANAGER_* when the user table is empty."""
    from backend import db, security
    from backend.util import now_iso
    if (db.scalar("SELECT COUNT(*) FROM users") or 0) > 0:
        return
    email, password = config.BOOTSTRAP_MANAGER_EMAIL, config.BOOTSTRAP_MANAGER_PASSWORD
    if not email or not password:
        logging.getLogger("seo_audit_pro").warning(
            "No users exist. Create the first Manager with: python -m backend.manage create-manager")
        return
    now = now_iso()
    db.execute("""INSERT INTO users (id, name, email, password_hash, role, is_active, must_change_password, created_at, updated_at)
                  VALUES (%s, %s, %s, %s, 'MANAGER', 1, 1, %s, %s)""",
               (security.new_id("usr_"), config.BOOTSTRAP_MANAGER_NAME, email, security.hash_password(password), now, now))


def create_app(start_workers=True):
    _configure_logging()
    from backend import audit_runner, errors, mailer, migrations, realtime
    from backend.routes import (admin_routes, audit_routes, auth_routes, chat_routes, lead_routes, org_routes,
                                outreach_routes, workspace_routes)

    migrations.migrate()
    bootstrap_manager()

    # The engine's history store keeps a local JSON copy of each result; keep it
    # inside the server data directory rather than next to the source code.
    import history_manager
    hist_dir = os.path.join(config.DATA_DIR, "history")
    history_manager.REPORTS_DIR = hist_dir
    history_manager.AUDITS_DIR = os.path.join(hist_dir, "audits")
    history_manager.HISTORY_FILE = os.path.join(hist_dir, "history.json")
    history_manager.TARGETS_FILE = os.path.join(hist_dir, "targets.json")

    root = config.ROOT_DIR
    app = Flask("seo_audit_pro", template_folder=os.path.join(root, "templates"),
                static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.json.ensure_ascii = False

    for mod in (auth_routes, org_routes, lead_routes, audit_routes, outreach_routes, chat_routes,
                workspace_routes, admin_routes):
        app.register_blueprint(mod.bp)
    errors.register(app)
    realtime.register_routes(app)

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    if start_workers:
        from backend import lead_import
        audit_runner.recover_interrupted()
        lead_import.resume_interrupted()
        mailer.start_worker()
    return app
