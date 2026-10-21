# -*- coding: utf-8 -*-
"""/api/users/* and /api/teams/* — organization management."""

from flask import Blueprint, g, jsonify, request

from backend import cache, db, events, realtime, security
from backend.auth import MANAGER, ROLES, TEAM_LEAD, idempotent, public_user, require
from backend.errors import ApiError, conflict, not_found
from backend.util import body, email_value, now_iso, page_args, paged, text

bp = Blueprint("org", __name__)

_USER_SELECT = """SELECT u.*, t.name AS team_name,
       (SELECT COUNT(*) FROM leads l WHERE l.assigned_agent_id = u.id AND l.status <> 'archived') AS lead_count,
       (SELECT COUNT(*) FROM audits a WHERE a.requested_by = u.id) AS audit_count
   FROM users u LEFT JOIN teams t ON t.id = u.team_id"""


def _user_out(row):
    data = public_user(row)
    data["lead_count"] = int(row.get("lead_count") or 0)
    data["audit_count"] = int(row.get("audit_count") or 0)
    data["online"] = realtime.is_online(row["id"])
    return data


def _get_team(team_id, tx=None):
    q = tx.query_one if tx else db.query_one
    team = q("SELECT * FROM teams WHERE id = %s", (team_id,))
    if not team:
        raise ApiError(422, "The selected team does not exist.", field="team_id")
    return team


def _visible_user(user_id):
    """Load a user the caller may view (Manager: all; Team Lead: own team)."""
    row = db.query_one(_USER_SELECT + " WHERE u.id = %s", (user_id,))
    if not row:
        raise not_found("User")
    me = g.user
    if me["role"] == MANAGER or row["id"] == me["id"]:
        return row
    if me["role"] == TEAM_LEAD and row["team_id"] == me["team_id"]:
        return row
    raise not_found("User")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@bp.get("/api/users")
@require(MANAGER, TEAM_LEAD)
def list_users():
    me = g.user
    page, size, offset = page_args(50)
    where, params = ["1=1"], []
    if me["role"] == TEAM_LEAD:
        where.append("u.team_id = %s")
        params.append(me["team_id"])
    role = request.args.get("role")
    if role in ROLES:
        where.append("u.role = %s")
        params.append(role)
    team_id = request.args.get("team_id")
    if team_id and me["role"] == MANAGER:
        where.append("u.team_id = %s")
        params.append(team_id)
    status = request.args.get("status")
    if status == "active":
        where.append("u.is_active = 1")
    elif status == "inactive":
        where.append("u.is_active = 0")
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(u.name) {db.LIKE} OR LOWER(u.email) {db.LIKE})")
        params += [term, term]
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM users u WHERE {clause}", params)
    rows = db.query(f"{_USER_SELECT} WHERE {clause} ORDER BY u.is_active DESC, u.name LIMIT %s OFFSET %s",
                    params + [size, offset])
    return jsonify(paged([_user_out(r) for r in rows], total, page, size))


@bp.get("/api/users/<user_id>")
@require(MANAGER, TEAM_LEAD)
def get_user(user_id):
    return jsonify({"user": _user_out(_visible_user(user_id))})


@bp.post("/api/users")
@require(MANAGER)
@idempotent
def create_user():
    data = body()
    name = text(data, "name", required=True, max_len=120, label="Name")
    email = email_value(data)
    role = text(data, "role", required=True, max_len=20, label="Role").upper()
    if role not in ROLES:
        raise ApiError(422, "Choose a valid role.", field="role")
    team_id = text(data, "team_id", max_len=64) or None
    if role == MANAGER:
        team_id = None
    elif not team_id:
        raise ApiError(422, "Team Leads and Agents must belong to a team.", field="team_id")
    temp_password = security.generate_temporary_password()
    uid = security.new_id("usr_")
    now = now_iso()
    with db.transaction() as tx:
        if team_id:
            _get_team(team_id, tx)
        if tx.query_one("SELECT 1 AS x FROM users WHERE email = %s", (email,)):
            raise conflict("A user with this email already exists.", field="email")
        if role == TEAM_LEAD and tx.query_one(
                "SELECT 1 AS x FROM users WHERE team_id = %s AND role = 'TEAM_LEAD' AND is_active = 1", (team_id,)):
            raise conflict("This team already has an active Team Lead.", field="team_id")
        tx.execute("""INSERT INTO users (id, name, email, password_hash, role, team_id, is_active, must_change_password,
                                         created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s, %s)""",
                   (uid, name, email, security.hash_password(temp_password), role, team_id, now, now))
        events.audit(g.user, "USER_CREATED", "user", uid, {"email": email, "role": role, "team_id": team_id}, tx=tx)
    if team_id:
        lead_id = events.team_lead_of(team_id)
        events.notify([lead_id] if lead_id and lead_id != uid else [], "team_change", "New team member",
                      f"{name} joined your team as {role.replace('_', ' ').title()}.", "user", uid)
    row = db.query_one(_USER_SELECT + " WHERE u.id = %s", (uid,))
    # The temporary password is shown exactly once to the creating manager.
    return jsonify({"user": _user_out(row), "temporary_password": temp_password}), 201


@bp.patch("/api/users/<user_id>")
@require(MANAGER)
def update_user(user_id):
    data = body()
    target = _visible_user(user_id)
    name = text(data, "name", max_len=120) or target["name"]
    email = email_value(data, required=False) or target["email"]
    role = (text(data, "role", max_len=20) or target["role"]).upper()
    if role not in ROLES:
        raise ApiError(422, "Choose a valid role.", field="role")
    team_id = (text(data, "team_id", max_len=64) or None) if "team_id" in data else target["team_id"]
    if role == MANAGER:
        team_id = None
    elif not team_id:
        raise ApiError(422, "Team Leads and Agents must belong to a team.", field="team_id")
    if target["id"] == g.user["id"] and role != MANAGER:
        raise ApiError(422, "You cannot remove your own Manager role.")
    if target["role"] == MANAGER and role != MANAGER and target["is_active"] and             (db.scalar("SELECT COUNT(*) FROM users WHERE role = 'MANAGER' AND is_active = 1") or 0) <= 1:
        raise ApiError(422, "At least one active Manager is required.")
    now = now_iso()
    with db.transaction() as tx:
        if team_id:
            _get_team(team_id, tx)
        if email != target["email"] and tx.query_one("SELECT 1 AS x FROM users WHERE email = %s AND id <> %s",
                                                     (email, user_id)):
            raise conflict("A user with this email already exists.", field="email")
        if role == TEAM_LEAD and target["is_active"] and tx.query_one(
                "SELECT 1 AS x FROM users WHERE team_id = %s AND role = 'TEAM_LEAD' AND is_active = 1 AND id <> %s",
                (team_id, user_id)):
            raise conflict("That team already has an active Team Lead.", field="team_id")
        tx.execute("UPDATE users SET name = %s, email = %s, role = %s, team_id = %s, updated_at = %s WHERE id = %s",
                   (name, email, role, team_id, now, user_id))
        moved_team = team_id != target["team_id"]
        if moved_team:
            # Leads cannot stay with an agent in a different team: return them to the old team's pool.
            moved = tx.query("SELECT id, assigned_team_id FROM leads WHERE assigned_agent_id = %s", (user_id,))
            for lead in moved:
                tx.execute("UPDATE leads SET assigned_agent_id = NULL, status = CASE WHEN status = 'assigned' THEN 'new' ELSE status END, "
                           "version = version + 1, updated_at = %s WHERE id = %s", (now, lead["id"]))
                tx.execute("""INSERT INTO lead_assignments (id, lead_id, from_team_id, from_agent_id, to_team_id, to_agent_id,
                              changed_by, reason, created_at) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s)""",
                           (security.new_id("las_"), lead["id"], lead["assigned_team_id"], user_id,
                            lead["assigned_team_id"], g.user["id"], "Agent moved to another team", now))
        events.audit(g.user, "USER_UPDATED", "user", user_id,
                     {"name": name, "email": email, "role": role, "team_id": team_id}, tx=tx)
    cache.bump()
    if moved_team:
        events.notify([user_id], "team_change", "Team changed", "You were moved to a different team.", "user", user_id)
    if role != target["role"] or moved_team:
        # Role/team affects permissions; force fresh sign-in on other devices.
        realtime.disconnect_user(user_id)
    return jsonify({"user": _user_out(db.query_one(_USER_SELECT + " WHERE u.id = %s", (user_id,)))})


@bp.post("/api/users/<user_id>/deactivate")
@require(MANAGER)
def deactivate_user(user_id):
    target = _visible_user(user_id)
    if target["id"] == g.user["id"]:
        raise ApiError(422, "You cannot deactivate your own account.")
    if target["role"] == MANAGER and (db.scalar("SELECT COUNT(*) FROM users WHERE role = 'MANAGER' AND is_active = 1") or 0) <= 1:
        raise ApiError(422, "At least one active Manager is required.")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("UPDATE users SET is_active = 0, updated_at = %s WHERE id = %s", (now, user_id))
        tx.execute("UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (now, user_id))
        # Work must not stay with someone who can no longer sign in: return it to the team pool.
        stranded = tx.query("SELECT id, assigned_team_id FROM leads WHERE assigned_agent_id = %s", (user_id,))
        for lead in stranded:
            tx.execute("UPDATE leads SET assigned_agent_id = NULL, version = version + 1, updated_at = %s, "
                       "status = CASE WHEN status = 'assigned' THEN 'new' ELSE status END WHERE id = %s", (now, lead["id"]))
            tx.execute("""INSERT INTO lead_assignments (id, lead_id, from_team_id, from_agent_id, to_team_id, to_agent_id,
                          changed_by, reason, created_at) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s)""",
                       (security.new_id("las_"), lead["id"], lead["assigned_team_id"], user_id, lead["assigned_team_id"],
                        g.user["id"], "Assignee deactivated", now))
        events.audit(g.user, "USER_DEACTIVATED", "user", user_id, {"leads_returned_to_pool": len(stranded)}, tx=tx)
    cache.bump()
    lead_of_team = events.team_lead_of(target["team_id"])
    if stranded and lead_of_team:
        events.notify([lead_of_team], "team_change", "Leads need reassignment",
                      f"{len(stranded)} lead(s) from {target['name']} were returned to the team pool.", "user", user_id)
    realtime.disconnect_user(user_id)
    return jsonify({"user": _user_out(db.query_one(_USER_SELECT + " WHERE u.id = %s", (user_id,)))})


@bp.post("/api/users/<user_id>/reactivate")
@require(MANAGER)
def reactivate_user(user_id):
    target = _visible_user(user_id)
    with db.transaction() as tx:
        if target["role"] == TEAM_LEAD and tx.query_one(
                "SELECT 1 AS x FROM users WHERE team_id = %s AND role = 'TEAM_LEAD' AND is_active = 1 AND id <> %s",
                (target["team_id"], user_id)):
            raise conflict("This team already has another active Team Lead. Change this user's role or team first.")
        tx.execute("UPDATE users SET is_active = 1, updated_at = %s WHERE id = %s", (now_iso(), user_id))
        events.audit(g.user, "USER_REACTIVATED", "user", user_id, tx=tx)
    return jsonify({"user": _user_out(db.query_one(_USER_SELECT + " WHERE u.id = %s", (user_id,)))})


@bp.post("/api/users/<user_id>/reset-password")
@require(MANAGER)
def admin_reset_password(user_id):
    target = _visible_user(user_id)
    if target["id"] == g.user["id"]:
        raise ApiError(422, "Use Profile → Change password for your own account.")
    temp_password = security.generate_temporary_password()
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("UPDATE users SET password_hash = %s, must_change_password = 1, updated_at = %s WHERE id = %s",
                   (security.hash_password(temp_password), now, user_id))
        tx.execute("UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (now, user_id))
        events.audit(g.user, "PASSWORD_RESET_BY_MANAGER", "user", user_id, tx=tx)
    realtime.disconnect_user(user_id)
    return jsonify({"temporary_password": temp_password})


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
_TEAM_SELECT = """SELECT t.*,
       (SELECT u.id FROM users u WHERE u.team_id = t.id AND u.role = 'TEAM_LEAD' AND u.is_active = 1) AS lead_id,
       (SELECT u.name FROM users u WHERE u.team_id = t.id AND u.role = 'TEAM_LEAD' AND u.is_active = 1) AS lead_name,
       (SELECT COUNT(*) FROM users u WHERE u.team_id = t.id AND u.is_active = 1) AS member_count,
       (SELECT COUNT(*) FROM leads l WHERE l.assigned_team_id = t.id AND l.status <> 'archived') AS lead_count,
       (SELECT COUNT(*) FROM audits a WHERE a.team_id = t.id AND a.status = 'completed') AS audit_count
   FROM teams t"""


def _team_out(row):
    return {"id": row["id"], "name": row["name"], "lead_id": row.get("lead_id"), "lead_name": row.get("lead_name"),
            "member_count": int(row.get("member_count") or 0), "lead_count": int(row.get("lead_count") or 0),
            "audit_count": int(row.get("audit_count") or 0), "created_at": row["created_at"]}


@bp.get("/api/teams")
@require()
def list_teams():
    me = g.user
    if me["role"] == MANAGER:
        rows = db.query(_TEAM_SELECT + " ORDER BY t.name")
    else:
        rows = db.query(_TEAM_SELECT + " WHERE t.id = %s", (me["team_id"],))
    return jsonify({"items": [_team_out(r) for r in rows]})


@bp.get("/api/teams/<team_id>")
@require(MANAGER, TEAM_LEAD)
def get_team(team_id):
    me = g.user
    if me["role"] != MANAGER and me["team_id"] != team_id:
        raise not_found("Team")
    row = db.query_one(_TEAM_SELECT + " WHERE t.id = %s", (team_id,))
    if not row:
        raise not_found("Team")
    members = db.query(_USER_SELECT + " WHERE u.team_id = %s ORDER BY u.role DESC, u.name", (team_id,))
    return jsonify({"team": _team_out(row), "members": [_user_out(m) for m in members]})


@bp.post("/api/teams")
@require(MANAGER)
@idempotent
def create_team():
    data = body()
    name = text(data, "name", required=True, max_len=80, label="Team name")
    tid = security.new_id("team_")
    now = now_iso()
    with db.transaction() as tx:
        if tx.query_one("SELECT 1 AS x FROM teams WHERE name_key = %s", (name.lower(),)):
            raise conflict("A team with this name already exists.", field="name")
        tx.execute("INSERT INTO teams (id, name, name_key, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                   (tid, name, name.lower(), now, now))
        events.audit(g.user, "TEAM_CREATED", "team", tid, {"name": name}, tx=tx)
    return jsonify({"team": _team_out(db.query_one(_TEAM_SELECT + " WHERE t.id = %s", (tid,)))}), 201


@bp.patch("/api/teams/<team_id>")
@require(MANAGER)
def rename_team(team_id):
    data = body()
    name = text(data, "name", required=True, max_len=80, label="Team name")
    with db.transaction() as tx:
        if not tx.query_one("SELECT 1 AS x FROM teams WHERE id = %s", (team_id,)):
            raise not_found("Team")
        if tx.query_one("SELECT 1 AS x FROM teams WHERE name_key = %s AND id <> %s", (name.lower(), team_id)):
            raise conflict("A team with this name already exists.", field="name")
        tx.execute("UPDATE teams SET name = %s, name_key = %s, updated_at = %s WHERE id = %s",
                   (name, name.lower(), now_iso(), team_id))
        events.audit(g.user, "TEAM_UPDATED", "team", team_id, {"name": name}, tx=tx)
    return jsonify({"team": _team_out(db.query_one(_TEAM_SELECT + " WHERE t.id = %s", (team_id,)))})


@bp.delete("/api/teams/<team_id>")
@require(MANAGER)
def delete_team(team_id):
    with db.transaction() as tx:
        if not tx.query_one("SELECT 1 AS x FROM teams WHERE id = %s", (team_id,)):
            raise not_found("Team")
        members = tx.scalar("SELECT COUNT(*) FROM users WHERE team_id = %s", (team_id,)) or 0
        leads = tx.scalar("SELECT COUNT(*) FROM leads WHERE assigned_team_id = %s", (team_id,)) or 0
        history = tx.scalar("SELECT COUNT(*) FROM audits WHERE team_id = %s", (team_id,)) or 0
        if members or leads or history:
            raise conflict(f"This team still has {members} member(s), {leads} lead(s) and {history} audit(s). "
                           "Move them to another team before deleting it.")
        tx.execute("DELETE FROM conversation_reads WHERE conversation_id IN (SELECT id FROM conversations WHERE team_id = %s)", (team_id,))
        tx.execute("DELETE FROM conversations WHERE team_id = %s AND NOT EXISTS "
                   "(SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)", (team_id,))
        if tx.query_one("SELECT 1 AS x FROM conversations WHERE team_id = %s", (team_id,)):
            raise conflict("This team has chat history and cannot be deleted. Rename it instead.")
        tx.execute("DELETE FROM teams WHERE id = %s", (team_id,))
        events.audit(g.user, "TEAM_DELETED", "team", team_id, tx=tx)
    return jsonify({"ok": True})

