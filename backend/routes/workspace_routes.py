# -*- coding: utf-8 -*-
"""/api/notifications/*, /api/dashboard, /api/search."""

from flask import Blueprint, g, jsonify, request

from backend import db
from backend.auth import AGENT, MANAGER, TEAM_LEAD, lead_scope, require
from backend.util import body, iso_ago, page_args, paged

bp = Blueprint("workspace", __name__)


# ---------------------------------------------------------------------------
# Notifications (always scoped to the caller)
# ---------------------------------------------------------------------------
@bp.get("/api/notifications")
@require()
def list_notifications():
    page, size, offset = page_args(30, 100)
    where, params = "user_id = %s", [g.user["id"]]
    if request.args.get("unread") == "1":
        where += " AND is_read = 0"
    total = db.scalar(f"SELECT COUNT(*) FROM notifications WHERE {where}", params)
    rows = db.query(f"SELECT * FROM notifications WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset])
    for r in rows:
        r["is_read"] = bool(r["is_read"])
    unread = db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = 0", (g.user["id"],)) or 0
    return jsonify(paged(rows, total, page, size) | {"unread": unread})


@bp.post("/api/notifications/read")
@require()
def mark_notifications_read():
    data = body()
    ids = data.get("ids")
    if data.get("all"):
        db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0", (g.user["id"],))
    elif isinstance(ids, list) and ids:
        ids = [str(i) for i in ids][:200]
        ph = ",".join(["%s"] * len(ids))
        db.execute(f"UPDATE notifications SET is_read = 1 WHERE user_id = %s AND id IN ({ph})", [g.user["id"]] + ids)
    unread = db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = 0", (g.user["id"],)) or 0
    return jsonify({"unread": unread})


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@bp.get("/api/dashboard")
@require()
def dashboard():
    me = g.user
    scope, sp = lead_scope(me)
    sp = list(sp)
    week = iso_ago(7 * 86400)
    month = iso_ago(30 * 86400)

    pipeline = {r["status"]: r["n"] for r in db.query(
        f"SELECT l.status, COUNT(*) AS n FROM leads l WHERE {scope} GROUP BY l.status", sp)}
    total_leads = sum(v for k, v in pipeline.items() if k != "archived")
    unassigned = db.scalar(f"SELECT COUNT(*) FROM leads l WHERE {scope} AND l.assigned_agent_id IS NULL "
                           "AND l.status <> 'archived'", sp) or 0
    audit_base = f"FROM audits a JOIN leads l ON l.id = a.lead_id WHERE {scope}"
    audits_week = db.scalar(f"SELECT COUNT(*) {audit_base} AND a.status = 'completed' AND a.completed_at > %s",
                            sp + [week]) or 0
    running = db.scalar(f"SELECT COUNT(*) {audit_base} AND a.status IN ('queued', 'running')", sp) or 0
    avg_score = db.scalar(f"SELECT AVG(a.score) {audit_base} AND a.status = 'completed' AND a.completed_at > %s",
                          sp + [month])
    out_base = f"FROM lead_outreach o JOIN leads l ON l.id = o.lead_id WHERE {scope} AND o.outreached = 1 AND o.created_at > %s"
    outreach_month = db.scalar(f"SELECT COUNT(*) {out_base}", sp + [month]) or 0
    calls_month = db.scalar(f"SELECT COUNT(*) {out_base} AND o.method = 'call'", sp + [month]) or 0

    # Audit activity: completed audits per day for the last 14 days.
    rows = db.query(f"SELECT SUBSTR(a.completed_at, 1, 10) AS day, COUNT(*) AS n {audit_base} "
                    "AND a.status = 'completed' AND a.completed_at > %s GROUP BY SUBSTR(a.completed_at, 1, 10)",
                    sp + [iso_ago(14 * 86400)])
    activity = {r["day"]: r["n"] for r in rows}

    recent_audits = db.query(f"""SELECT a.id, a.status, a.score, a.grade, a.created_at, a.completed_at, a.percent,
                                        l.company, l.normalized_domain, u.name AS requested_by_name
                                 {audit_base.replace('WHERE', 'LEFT JOIN users u ON u.id = a.requested_by WHERE')}
                                 ORDER BY a.created_at DESC LIMIT 8""", sp)

    team_perf = []
    if me["role"] in (MANAGER, TEAM_LEAD):
        if me["role"] == MANAGER:
            groups = db.query("SELECT id, name FROM teams ORDER BY name")
            for t in groups:
                tid = t["id"]
                team_perf.append({
                    "id": tid, "name": t["name"],
                    "leads": db.scalar("SELECT COUNT(*) FROM leads WHERE assigned_team_id = %s AND status <> 'archived'", (tid,)) or 0,
                    "audits": db.scalar("SELECT COUNT(*) FROM audits WHERE team_id = %s AND status = 'completed' AND completed_at > %s",
                                        (tid, month)) or 0,
                    "outreach": db.scalar("SELECT COUNT(*) FROM lead_outreach WHERE team_id = %s AND outreached = 1 "
                                          "AND created_at > %s", (tid, month)) or 0,
                    "won": db.scalar("SELECT COUNT(*) FROM leads WHERE assigned_team_id = %s AND status = 'won'", (tid,)) or 0,
                })
        else:
            members = db.query("SELECT id, name, role FROM users WHERE team_id = %s AND is_active = 1 ORDER BY name",
                               (me["team_id"],))
            for m in members:
                uid = m["id"]
                team_perf.append({
                    "id": uid, "name": m["name"], "role": m["role"],
                    "leads": db.scalar("SELECT COUNT(*) FROM leads WHERE assigned_agent_id = %s AND status <> 'archived'", (uid,)) or 0,
                    "audits": db.scalar("SELECT COUNT(*) FROM audits WHERE requested_by = %s AND status = 'completed' AND completed_at > %s",
                                        (uid, month)) or 0,
                    "outreach": db.scalar("SELECT COUNT(*) FROM lead_outreach WHERE user_id = %s AND outreached = 1 "
                                          "AND created_at > %s", (uid, month)) or 0,
                    "won": db.scalar("SELECT COUNT(*) FROM leads WHERE assigned_agent_id = %s AND status = 'won'", (uid,)) or 0,
                })

    if me["role"] == MANAGER:
        recent = db.query("""SELECT al.action, al.entity_type, al.entity_id, al.created_at, u.name AS user_name
                             FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
                             WHERE al.action NOT IN ('LOGIN', 'LOGOUT', 'LOGIN_FAILED', 'SMTP_TESTED', 'REPORT_DOWNLOADED')
                             ORDER BY al.created_at DESC LIMIT 12""")
    else:
        recent = db.query(f"""SELECT al.action, al.entity_type, al.entity_id, al.created_at, u.name AS user_name
                              FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
                              WHERE al.entity_type = 'lead' AND al.entity_id IN (SELECT l.id FROM leads l WHERE {scope})
                              ORDER BY al.created_at DESC LIMIT 12""", sp)

    return jsonify({
        "role": me["role"],
        "overview": {"leads": total_leads, "unassigned": unassigned, "audits_7d": audits_week,
                     "audits_running": running, "avg_score_30d": round(avg_score) if avg_score is not None else None,
                     "outreach_30d": outreach_month, "calls_30d": calls_month},
        "pipeline": pipeline,
        "audit_activity": activity,
        "recent_audits": recent_audits,
        "team_performance": team_perf,
        "recent_activity": recent,
    })


# ---------------------------------------------------------------------------
# Global search (scoped)
# ---------------------------------------------------------------------------
@bp.get("/api/search")
@require()
def search():
    me = g.user
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"leads": [], "audits": [], "users": [], "teams": []})
    term = f"%{db.like_escape(q)}%"
    scope, sp = lead_scope(me)
    leads = db.query(f"""SELECT l.id, l.company, l.normalized_domain, l.status FROM leads l
                         WHERE {scope} AND (LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE}
                                            OR LOWER(COALESCE(l.email, '')) {db.LIKE})
                         ORDER BY l.updated_at DESC LIMIT 8""", list(sp) + [term] * 3)
    audits = db.query(f"""SELECT a.id, a.status, a.score, a.created_at, l.company, l.normalized_domain
                          FROM audits a JOIN leads l ON l.id = a.lead_id
                          WHERE {scope} AND (LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE})
                          ORDER BY a.created_at DESC LIMIT 6""", list(sp) + [term] * 2)
    users, teams = [], []
    if me["role"] != AGENT:
        uscope, up = ("1=1", []) if me["role"] == MANAGER else ("team_id = %s", [me["team_id"]])
        users = db.query(f"""SELECT id, name, email, role FROM users WHERE {uscope}
                             AND (LOWER(name) {db.LIKE} OR LOWER(email) {db.LIKE}) ORDER BY name LIMIT 6""",
                         up + [term, term])
        tscope, tp = ("1=1", []) if me["role"] == MANAGER else ("id = %s", [me["team_id"]])
        teams = db.query(f"SELECT id, name FROM teams WHERE {tscope} AND name_key {db.LIKE} ORDER BY name LIMIT 5",
                         tp + [term])
    return jsonify({"leads": leads, "audits": audits, "users": users, "teams": teams})
