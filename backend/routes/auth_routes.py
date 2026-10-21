# -*- coding: utf-8 -*-
"""/api/auth/* — sign-in, token refresh, sign-out, password lifecycle, profile."""

import threading

from flask import Blueprint, g, jsonify, request, send_file

from backend import config, db, events, mailer, realtime, security, uploads
from backend.auth import public_user, require
from backend.errors import ApiError, not_found
from backend.util import body, client_ip, email_value, iso_ago, iso_in, now_iso, rate_limit, text

bp = Blueprint("auth", __name__)

_GENERIC_LOGIN_ERROR = "The email or password is incorrect."


def _session_payload(user, session_id, refresh_token):
    return {
        "access_token": security.issue_access_token(user["id"], session_id),
        "refresh_token": refresh_token,
        "expires_in": config.ACCESS_TOKEN_TTL,
        "user": _me(user["id"]),
    }


def _me(user_id):
    row = db.query_one("""SELECT u.*, t.name AS team_name FROM users u LEFT JOIN teams t ON t.id = u.team_id
                          WHERE u.id = %s""", (user_id,))
    data = public_user(row)
    data["unread_notifications"] = db.scalar(
        "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = 0", (user_id,)) or 0
    return data


@bp.post("/api/auth/login")
def login():
    rate_limit(f"login-ip:{client_ip()}", config.LOGIN_ATTEMPTS_PER_IP_5MIN, 300)
    data = body()
    email = text(data, "email", max_len=254).lower()
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if not email or not password:
        raise ApiError(422, "Enter your email and password.")

    failures = db.scalar("SELECT COUNT(*) FROM login_attempts WHERE email = %s AND success = 0 AND created_at > %s",
                         (email, iso_ago(config.LOGIN_LOCK_WINDOW))) or 0
    if failures >= config.LOGIN_MAX_FAILURES:
        events.audit(None, "LOGIN_LOCKED", "user", None, {"email": email}, email=email)
        raise ApiError(429, "Too many failed sign-in attempts. Try again in 15 minutes or reset your password.",
                       code="locked")

    user = db.query_one("SELECT * FROM users WHERE email = %s", (email,))
    ok = bool(user) and security.verify_password(user["password_hash"], password)
    if not user:
        security.burn_password_check(password)
    db.execute("INSERT INTO login_attempts (id, email, ip, success, created_at) VALUES (%s, %s, %s, %s, %s)",
               (security.new_id("la_"), email, client_ip(), 1 if ok else 0, now_iso()))
    if not ok:
        events.audit(user, "LOGIN_FAILED", "user", user["id"] if user else None, {"email": email}, email=email)
        raise ApiError(401, _GENERIC_LOGIN_ERROR, code="invalid_credentials")
    if not user["is_active"]:
        events.audit(user, "LOGIN_FAILED", "user", user["id"], {"reason": "inactive"})
        raise ApiError(403, "This account has been deactivated. Contact your manager.", code="inactive")

    session_id = security.new_id("ses_")
    refresh = security.new_refresh_token()
    now = now_iso()
    with db.transaction() as tx:
        if security.needs_rehash(user["password_hash"]):
            tx.execute("UPDATE users SET password_hash = %s WHERE id = %s", (security.hash_password(password), user["id"]))
        tx.execute("INSERT INTO sessions (id, user_id, refresh_hash, created_at, expires_at, last_used_at, user_agent, ip) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                   (session_id, user["id"], security.sha256_hex(refresh), now, iso_in(config.REFRESH_TOKEN_TTL), now,
                    (request.headers.get("User-Agent") or "")[:200], client_ip()))
        tx.execute("UPDATE users SET last_login_at = %s, last_seen_at = %s WHERE id = %s", (now, now, user["id"]))
        events.audit(user, "LOGIN", "user", user["id"], tx=tx)
    return jsonify(_session_payload(user, session_id, refresh))


@bp.post("/api/auth/refresh")
def refresh_session():
    rate_limit(f"refresh-ip:{client_ip()}", 60, 60)
    data = body()
    token = data.get("refresh_token") if isinstance(data.get("refresh_token"), str) else ""
    if not token:
        raise ApiError(401, "Your session has expired. Please sign in again.", code="unauthorized")
    old_hash = security.sha256_hex(token)
    sess = db.query_one("""SELECT s.*, u.is_active FROM sessions s JOIN users u ON u.id = s.user_id
                           WHERE s.refresh_hash = %s""", (old_hash,))
    if not sess or sess["revoked_at"] or sess["expires_at"] <= now_iso() or not sess["is_active"]:
        raise ApiError(401, "Your session has expired. Please sign in again.", code="unauthorized")
    new_refresh = security.new_refresh_token()
    with db.transaction() as tx:
        tx.execute("UPDATE sessions SET refresh_hash = %s, last_used_at = %s WHERE id = %s AND refresh_hash = %s",
                   (security.sha256_hex(new_refresh), now_iso(), sess["id"], old_hash))
        if tx.rowcount != 1:
            raise ApiError(401, "Your session has expired. Please sign in again.", code="unauthorized")
    user = db.query_one("SELECT * FROM users WHERE id = %s", (sess["user_id"],))
    return jsonify(_session_payload(user, sess["id"], new_refresh))


@bp.post("/api/auth/logout")
@require(allow_pending_password=True)
def logout():
    db.execute("UPDATE sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL", (now_iso(), g.user["sid"]))
    events.audit(g.user, "LOGOUT", "user", g.user["id"])
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
@require(allow_pending_password=True)
def me():
    return jsonify({"user": _me(g.user["id"])})


@bp.patch("/api/auth/me")
@require()
def update_me():
    data = body()
    name = text(data, "name", required=True, max_len=120, label="Name")
    db.execute("UPDATE users SET name = %s, updated_at = %s WHERE id = %s", (name, now_iso(), g.user["id"]))
    events.audit(g.user, "PROFILE_UPDATED", "user", g.user["id"], {"name": name})
    return jsonify({"user": _me(g.user["id"])})


@bp.post("/api/auth/me/avatar")
@require()
def upload_avatar():
    up = uploads.save_upload(request.files.get("file"), g.user["id"], "avatar")
    db.execute("UPDATE users SET avatar_upload_id = %s, updated_at = %s WHERE id = %s", (up["id"], now_iso(), g.user["id"]))
    events.audit(g.user, "PROFILE_PICTURE_UPDATED", "user", g.user["id"])
    return jsonify({"user": _me(g.user["id"])})


@bp.get("/api/users/<user_id>/avatar")
@require(allow_pending_password=True)
def get_avatar(user_id):
    row = db.query_one("""SELECT up.* FROM users u JOIN uploads up ON up.id = u.avatar_upload_id
                          WHERE u.id = %s""", (user_id,))
    if not row:
        raise not_found("Avatar")
    return send_file(uploads.upload_path(row), mimetype=row["mime"], max_age=300)


@bp.post("/api/auth/change-password")
@require(allow_pending_password=True)
def change_password():
    rate_limit(f"chpw:{g.user['id']}", 10, 600)
    data = body()
    current = data.get("current_password") if isinstance(data.get("current_password"), str) else ""
    new = data.get("new_password") if isinstance(data.get("new_password"), str) else ""
    row = db.query_one("SELECT password_hash FROM users WHERE id = %s", (g.user["id"],))
    if not security.verify_password(row["password_hash"], current):
        raise ApiError(422, "Your current password is incorrect.", field="current_password")
    problems = security.password_policy_errors(new, g.user["email"])
    if problems:
        raise ApiError(422, " ".join(problems), field="new_password")
    if security.verify_password(row["password_hash"], new):
        raise ApiError(422, "Choose a password different from your current one.", field="new_password")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("UPDATE users SET password_hash = %s, must_change_password = 0, password_changed_at = %s, updated_at = %s "
                   "WHERE id = %s", (security.hash_password(new), now, now, g.user["id"]))
        # Sign out every other device.
        tx.execute("UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND id <> %s AND revoked_at IS NULL",
                   (now, g.user["id"], g.user["sid"]))
        events.audit(g.user, "PASSWORD_CHANGED", "user", g.user["id"], tx=tx)
    events.notify([g.user["id"]], "password_changed", "Password changed",
                  "Your password was changed. Other devices were signed out.", "user", g.user["id"])
    return jsonify({"ok": True, "user": _me(g.user["id"])})


@bp.post("/api/auth/forgot-password")
def forgot_password():
    rate_limit(f"forgot-ip:{client_ip()}", 5, 900)
    data = body()
    email = email_value(data)
    rate_limit(f"forgot-email:{email}", 3, 900)
    user = db.query_one("SELECT * FROM users WHERE email = %s AND is_active = 1", (email,))
    if user:
        code = security.new_reset_code()
        with db.transaction() as tx:
            tx.execute("UPDATE password_resets SET used_at = %s WHERE user_id = %s AND used_at IS NULL", (now_iso(), user["id"]))
            tx.execute("INSERT INTO password_resets (id, user_id, code_hash, created_at, expires_at) VALUES (%s, %s, %s, %s, %s)",
                       (security.new_id("pr_"), user["id"], security.sha256_hex(f"{user['id']}:{code}"), now_iso(),
                        iso_in(config.PASSWORD_RESET_TTL)))
            events.audit(user, "PASSWORD_RESET_REQUESTED", "user", user["id"], tx=tx)
        threading.Thread(target=mailer.send_password_reset, args=(user, code), daemon=True).start()
    # Identical response whether or not the account exists (no enumeration).
    return jsonify({"ok": True, "message": "If an active account exists for that email, a reset code has been sent."})


@bp.post("/api/auth/reset-password")
def reset_password():
    rate_limit(f"reset-ip:{client_ip()}", 10, 900)
    data = body()
    email = email_value(data)
    code = text(data, "code", required=True, max_len=20, label="Code").replace(" ", "")
    new = data.get("new_password") if isinstance(data.get("new_password"), str) else ""
    invalid = ApiError(422, "The reset code is invalid or has expired.", field="code")
    user = db.query_one("SELECT * FROM users WHERE email = %s AND is_active = 1", (email,))
    if not user:
        raise invalid
    reset = db.query_one("""SELECT * FROM password_resets WHERE user_id = %s AND used_at IS NULL AND expires_at > %s
                            ORDER BY created_at DESC LIMIT 1""", (user["id"], now_iso()))
    if not reset or reset["attempts"] >= 5:
        raise invalid
    if security.sha256_hex(f"{user['id']}:{code}") != reset["code_hash"]:
        db.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = %s", (reset["id"],))
        raise invalid
    problems = security.password_policy_errors(new, email)
    if problems:
        raise ApiError(422, " ".join(problems), field="new_password")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("UPDATE password_resets SET used_at = %s WHERE id = %s AND used_at IS NULL", (now, reset["id"]))
        if tx.rowcount != 1:
            raise invalid
        tx.execute("UPDATE users SET password_hash = %s, must_change_password = 0, password_changed_at = %s, updated_at = %s "
                   "WHERE id = %s", (security.hash_password(new), now, now, user["id"]))
        tx.execute("UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (now, user["id"]))
        events.audit(user, "PASSWORD_RESET", "user", user["id"], tx=tx)
    realtime.disconnect_user(user["id"])
    events.notify([user["id"]], "password_changed", "Password reset",
                  "Your password was reset with an emailed code. All devices were signed out.", "user", user["id"])
    return jsonify({"ok": True})


@bp.get("/api/health")
def health():
    try:
        db.scalar("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    status = 200 if db_ok else 503
    return jsonify({"status": "ok" if db_ok else "degraded", "version": config.APP_VERSION,
                    "app": "SEO Audit Pro"}), status
