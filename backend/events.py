# -*- coding: utf-8 -*-
"""Audit trail + notifications (persisted, then pushed over the realtime hub)."""

import json
import logging

from flask import has_request_context

from backend import db, realtime, security
from backend.util import client_ip, now_iso

log = logging.getLogger("seo_audit_pro")

# Keys that must never be written to the audit trail.
_REDACT = {"password", "new_password", "current_password", "token", "refresh_token", "code", "smtp_password"}


def _clean(meta):
    if not meta:
        return None
    return json.dumps({k: ("[redacted]" if k in _REDACT else v) for k, v in meta.items()}, default=str)[:4000]


def audit(user, action, entity_type=None, entity_id=None, metadata=None, tx=None, email=None):
    """Append an entry to the immutable audit trail. Never raises."""
    params = (security.new_id("log_"), (user or {}).get("id"), email or (user or {}).get("email"), action,
              entity_type, None if entity_id is None else str(entity_id), _clean(metadata),
              client_ip() if has_request_context() else None, now_iso())
    sql = ("INSERT INTO audit_logs (id, user_id, user_email, action, entity_type, entity_id, metadata, ip, created_at) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")
    try:
        if tx is not None:
            tx.execute(sql, params)
        else:
            db.execute(sql, params)
    except Exception as e:  # noqa: BLE001
        if tx is not None:
            raise
        log.error("Failed to write audit log %s: %s", action, e)


def notify(user_ids, ntype, title, body, entity_type=None, entity_id=None):
    """Create a notification for each recipient and push it live."""
    user_ids = [u for u in dict.fromkeys(user_ids or []) if u]
    if not user_ids:
        return
    created = now_iso()
    rows = []
    for uid in user_ids:
        nid = security.new_id("ntf_")
        rows.append((nid, uid, ntype, title[:200], body[:1000], entity_type, entity_id, created))
    try:
        with db.transaction() as tx:
            tx.executemany("INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, is_read, created_at) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)", rows)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to create notification %s: %s", ntype, e)
        return
    for nid, uid, _t, t_title, t_body, et, eid, _c in rows:
        realtime.publish([uid], {"type": "notification", "notification": {
            "id": nid, "type": ntype, "title": t_title, "body": t_body, "entity_type": et,
            "entity_id": eid, "is_read": False, "created_at": created}})


def managers():
    return [r["id"] for r in db.query("SELECT id FROM users WHERE role = 'MANAGER' AND is_active = 1")]


def team_lead_of(team_id):
    if not team_id:
        return None
    row = db.query_one("SELECT id FROM users WHERE team_id = %s AND role = 'TEAM_LEAD' AND is_active = 1", (team_id,))
    return row["id"] if row else None
