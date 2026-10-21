# -*- coding: utf-8 -*-
"""
Authentication context, role decorators, resource-scope helpers and
idempotency for state-changing endpoints.

Identity (user id, role, team, active flag) is ALWAYS derived from the
server-side session and database — never from client-supplied fields.
"""

import json
from functools import wraps

from flask import g, jsonify, request

from backend import db, security
from backend.errors import ApiError, forbidden, not_found
from backend.util import now_iso

MANAGER, TEAM_LEAD, AGENT = "MANAGER", "TEAM_LEAD", "AGENT"
ROLES = (MANAGER, TEAM_LEAD, AGENT)

_USER_COLUMNS = ("u.id, u.name, u.email, u.role, u.team_id, u.is_active, u.must_change_password, "
                 "u.avatar_upload_id, u.created_at, u.last_login_at, u.last_seen_at")


def user_from_token(token):
    """Validates an access token against a live session. Returns the user dict
    (with ``sid``) or None."""
    payload = security.read_access_token(token)
    if not payload:
        return None
    row = db.query_one(
        f"""SELECT {_USER_COLUMNS}, s.id AS sid
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.id = %s AND u.id = %s AND s.revoked_at IS NULL AND s.expires_at > %s""",
        (payload.get("sid"), payload.get("uid"), now_iso()))
    if not row or not row["is_active"]:
        return None
    return row


def session_valid(sid):
    return bool(db.query_one(
        "SELECT 1 AS ok FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id = %s AND s.revoked_at IS NULL AND u.is_active = 1", (sid,)))


def touch_last_seen(user_id):
    try:
        db.execute("UPDATE users SET last_seen_at = %s WHERE id = %s", (now_iso(), user_id))
    except Exception:  # noqa: BLE001 - presence is best effort
        pass


def current_user():
    return g.user


def require(*roles, allow_pending_password=False):
    """Authenticate the request and optionally restrict to roles.

    Users who must change their password can only reach endpoints explicitly
    marked ``allow_pending_password``.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header[7:].strip() if header.startswith("Bearer ") else None
            user = user_from_token(token)
            if not user:
                raise ApiError(401, "Your session has expired. Please sign in again.", code="unauthorized")
            if user["must_change_password"] and not allow_pending_password:
                raise ApiError(403, "You must change your password before continuing.",
                               code="password_change_required")
            if roles and user["role"] not in roles:
                from backend import events
                events.audit(user, "PERMISSION_DENIED", "endpoint", request.path,
                             {"method": request.method})
                raise forbidden()
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Lead scope — the single source of truth for who may see which lead.
# Audits, reports and outreach emails inherit access from their lead.
# ---------------------------------------------------------------------------
def lead_scope(user, alias="l"):
    """SQL fragment + params restricting leads to what ``user`` may access."""
    if user["role"] == MANAGER:
        return "1=1", ()
    if user["role"] == TEAM_LEAD:
        return f"{alias}.assigned_team_id = %s", (user["team_id"],)
    return f"{alias}.assigned_agent_id = %s", (user["id"],)


def can_access_lead(user, lead):
    if user["role"] == MANAGER:
        return True
    if user["role"] == TEAM_LEAD:
        return bool(lead.get("assigned_team_id")) and lead["assigned_team_id"] == user["team_id"]
    return bool(lead.get("assigned_agent_id")) and lead["assigned_agent_id"] == user["id"]


def load_lead(user, lead_id, tx=None):
    q = tx.query_one if tx else db.query_one
    lead = q("SELECT * FROM leads WHERE id = %s", (lead_id,))
    if not lead or not can_access_lead(user, lead):
        raise not_found("Lead")
    return lead


def load_audit(user, audit_id):
    row = db.query_one(
        """SELECT a.*, l.company, l.normalized_domain, l.assigned_team_id, l.assigned_agent_id
           FROM audits a JOIN leads l ON l.id = a.lead_id WHERE a.id = %s""", (audit_id,))
    if not row or not can_access_lead(user, row):
        raise not_found("Audit")
    return row


# ---------------------------------------------------------------------------
# Idempotency: replay the stored response for a repeated Idempotency-Key.
# ---------------------------------------------------------------------------
def idempotent(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = (request.headers.get("Idempotency-Key") or "").strip()[:100]
        if not key:
            return fn(*args, **kwargs)
        user = g.user
        # Claim the key first (status 0 = in progress) so two concurrent
        # requests with the same key cannot both execute.
        try:
            db.execute("INSERT INTO idempotency_keys (user_id, key, endpoint, status_code, response, created_at) "
                       "VALUES (%s, %s, %s, 0, '{}', %s)", (user["id"], key, request.path, now_iso()))
        except db.IntegrityError:
            prior = db.query_one("SELECT status_code, response, endpoint FROM idempotency_keys "
                                 "WHERE user_id = %s AND key = %s", (user["id"], key))
            if not prior or prior["endpoint"] != request.path:
                raise ApiError(422, "Idempotency key reused for a different operation.")
            if prior["status_code"] == 0:
                raise ApiError(409, "This request is already being processed.", code="in_progress")
            resp = jsonify(json.loads(prior["response"]))
            resp.status_code = prior["status_code"]
            resp.headers["Idempotent-Replay"] = "true"
            return resp
        try:
            result = fn(*args, **kwargs)
        except Exception:
            db.execute("DELETE FROM idempotency_keys WHERE user_id = %s AND key = %s", (user["id"], key))
            raise
        if isinstance(result, tuple):
            resp = result[0] if hasattr(result[0], "status_code") else jsonify(result[0])
            resp.status_code = result[1]
        else:
            resp = result if hasattr(result, "status_code") else jsonify(result)
        if 200 <= resp.status_code < 300 and resp.is_json:
            db.execute("UPDATE idempotency_keys SET status_code = %s, response = %s WHERE user_id = %s AND key = %s",
                       (resp.status_code, json.dumps(resp.get_json()), user["id"], key))
        else:
            db.execute("DELETE FROM idempotency_keys WHERE user_id = %s AND key = %s", (user["id"], key))
        return resp
    return wrapper


def public_user(row):
    """Shape a user row for API responses. Never includes secrets."""
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
        "team_id": row.get("team_id"), "team_name": row.get("team_name"),
        "is_active": bool(row.get("is_active", 1)),
        "must_change_password": bool(row.get("must_change_password", 0)),
        "has_avatar": bool(row.get("avatar_upload_id")),
        "created_at": row.get("created_at"), "last_login_at": row.get("last_login_at"),
        "last_seen_at": row.get("last_seen_at"),
    }
