# -*- coding: utf-8 -*-
"""Manager-only administration: application settings and audit logs."""

import json
import re

from flask import Blueprint, g, jsonify, request

from backend import audit_runner, db, events, reports, security
from backend.auth import MANAGER, require
from backend.errors import ApiError
from backend.util import body, now_iso, page_args, paged

bp = Blueprint("admin", __name__)

# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------
@bp.get("/api/settings")
@require(MANAGER)
def get_settings():
    return jsonify({"audit_defaults": audit_runner.audit_settings(), "pdf_supported": reports.pdf_supported(),
                    "made_in_app": reports.made_in_app(),
                    "pagespeed_key_set": bool(audit_runner.pagespeed_key())})


def _save_pagespeed_key(raw):
    """Store (encrypted) or clear the Google PageSpeed Insights API key."""
    key = (raw or "").strip() if isinstance(raw, str) else ""
    if key and not re.match(r"^[A-Za-z0-9_\-]{20,80}$", key):
        raise ApiError(422, "That does not look like a Google API key. Copy it again from Google Cloud.",
                       field="pagespeed_api_key")
    with db.transaction() as tx:
        tx.execute("DELETE FROM app_settings WHERE key = 'pagespeed_key'")
        if key:
            tx.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES ('pagespeed_key', %s, %s, %s)",
                       (security.encrypt_secret(key), g.user["id"], now_iso()))
        events.audit(g.user, "SETTINGS_CHANGED", "settings", "pagespeed_key", {"set": bool(key)}, tx=tx)


@bp.put("/api/settings")
@require(MANAGER)
def save_settings():
    payload = body()
    if "pagespeed_api_key" in payload:
        _save_pagespeed_key(payload["pagespeed_api_key"])
        if "audit_defaults" not in payload:
            return get_settings()
    data = payload.get("audit_defaults") or {}
    try:
        max_pages = int(data.get("max_pages", 50))
    except (TypeError, ValueError):
        raise ApiError(422, "Max pages must be a number.", field="max_pages")
    if not 1 <= max_pages <= 500:
        raise ApiError(422, "Max pages must be between 1 and 500.", field="max_pages")
    psi = data.get("psi_strategy", "both")
    if psi not in ("both", "mobile"):
        raise ApiError(422, "PageSpeed strategy must be both or mobile.", field="psi_strategy")
    try:
        concurrent = int(data.get("concurrent", audit_runner.concurrency()))
    except (TypeError, ValueError):
        raise ApiError(422, "Audits at the same time must be a number.", field="concurrent")
    if not 1 <= concurrent <= audit_runner.MAX_CONCURRENT:
        raise ApiError(422, f"Audits at the same time must be between 1 and {audit_runner.MAX_CONCURRENT}.",
                       field="concurrent")
    value = json.dumps({"max_pages": max_pages, "psi_strategy": psi, "concurrent": concurrent})
    with db.transaction() as tx:
        tx.execute("DELETE FROM app_settings WHERE key = 'audit_defaults'")
        tx.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES ('audit_defaults', %s, %s, %s)",
                   (value, g.user["id"], now_iso()))
        events.audit(g.user, "SETTINGS_CHANGED", "settings", "audit_defaults", json.loads(value), tx=tx)
    audit_runner.wake()  # a higher limit can start waiting audits right away
    return get_settings()


# ---------------------------------------------------------------------------
# Audit logs (read-only; the table rejects UPDATE/DELETE at the database level)
# ---------------------------------------------------------------------------
@bp.get("/api/audit-logs")
@require(MANAGER)
def list_audit_logs():
    page, size, offset = page_args(50, 200)
    where, params = ["1=1"], []
    for arg, col in (("action", "al.action"), ("user_id", "al.user_id"), ("entity_type", "al.entity_type"),
                     ("entity_id", "al.entity_id")):
        if request.args.get(arg):
            where.append(f"{col} = %s")
            params.append(request.args[arg])
    if request.args.get("since"):
        where.append("al.created_at >= %s")
        params.append(request.args["since"])
    if request.args.get("until"):
        where.append("al.created_at <= %s")
        params.append(request.args["until"])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(COALESCE(al.user_email, '')) {db.LIKE} OR LOWER(al.action) {db.LIKE} "
                     f"OR LOWER(COALESCE(al.metadata, '')) {db.LIKE})")
        params += [term] * 3
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM audit_logs al WHERE {clause}", params)
    rows = db.query(f"""SELECT al.*, u.name AS user_name FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
                        WHERE {clause} ORDER BY al.created_at DESC LIMIT %s OFFSET %s""", params + [size, offset])
    for r in rows:
        try:
            r["metadata"] = json.loads(r["metadata"]) if r["metadata"] else None
        except ValueError:
            pass
    actions = [r["action"] for r in db.query("SELECT DISTINCT action FROM audit_logs ORDER BY action")]
    return jsonify(paged(rows, total, page, size) | {"actions": actions})
