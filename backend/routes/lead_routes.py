# -*- coding: utf-8 -*-
"""
/api/leads/* and /api/lead-imports, /api/lead-batches — the lead bank.

All list, count, export and bulk operations share one filter builder
(``lead_filter``) that always includes the caller's scope, so a "select all
matching" action can never reach leads outside the caller's permissions.
"""

import csv
import io
import json

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from backend import cache, db, events, lead_import, security
from backend.auth import AGENT, MANAGER, TEAM_LEAD, can_access_lead, idempotent, lead_scope, load_lead, require
from backend.errors import ApiError, conflict, forbidden, not_found
from backend.util import (body, email_value, normalize_company, normalize_domain, normalize_phone, now_iso,
                          page_args, paged, rate_limit, text)

bp = Blueprint("leads", __name__)

STATUSES = ("new", "assigned", "audited", "contacted", "qualified", "won", "lost", "archived")
_SORTS = {"company": "l.company", "status": "l.status", "score": "l.last_audit_score", "created": "l.created_at",
          "last_contact": "l.last_contact_at", "last_audit": "l.last_audit_at", "team": "t.name", "agent": "u.name",
          "website": "l.normalized_domain", "niche": "l.niche_key", "area": "l.area_key", "rating": "l.rating",
          "reviews": "l.review_count"}
_LEAD_SELECT = """SELECT l.*, t.name AS team_name, u.name AS agent_name, b.file_name AS batch_name
                  FROM leads l LEFT JOIN teams t ON t.id = l.assigned_team_id
                  LEFT JOIN users u ON u.id = l.assigned_agent_id
                  LEFT JOIN lead_batches b ON b.id = l.batch_id"""
_FROM = """FROM leads l LEFT JOIN teams t ON t.id = l.assigned_team_id
           LEFT JOIN users u ON u.id = l.assigned_agent_id"""
BULK_LIMIT = 200_000
FILTER_KEYS = ("search", "status", "team_id", "agent_id", "assigned", "audited", "outreach", "niche", "area",
               "batch_id", "has_email")


def lead_out(row):
    keys = ("id", "company", "website", "normalized_domain", "contact_name", "email", "phone", "niche", "area",
            "country", "address", "category", "rating", "review_count", "maps_url", "source", "notes", "status",
            "assigned_team_id", "assigned_agent_id", "team_name", "agent_name", "batch_id", "batch_name",
            "last_audit_id", "last_audit_score", "last_audit_at", "last_contact_at", "version", "created_at",
            "updated_at")
    out = {k: row.get(k) for k in keys}
    try:
        out["extra"] = json.loads(row["extra"]) if row.get("extra") else {}
    except (TypeError, ValueError):
        out["extra"] = {}
    return out


# ---------------------------------------------------------------------------
# Shared filter
# ---------------------------------------------------------------------------
def lead_filter(me, args):
    """WHERE clause + params for a lead filter, always within the caller's scope.
    ``args`` is a mapping (query string or a JSON "filter" object)."""
    def arg(key):
        v = args.get(key)
        return str(v).strip() if v not in (None, "") else ""

    scope, scope_params = lead_scope(me)
    where, params = [scope], list(scope_params)
    status = arg("status")
    if status in STATUSES:
        where.append("l.status = %s")
        params.append(status)
    elif status != "all":
        where.append("l.status <> 'archived'")
    if me["role"] == MANAGER and arg("team_id"):
        if arg("team_id") == "none":
            where.append("l.assigned_team_id IS NULL")
        else:
            where.append("l.assigned_team_id = %s")
            params.append(arg("team_id"))
    if me["role"] in (MANAGER, TEAM_LEAD) and arg("agent_id"):
        where.append("l.assigned_agent_id = %s")
        params.append(arg("agent_id"))
    if arg("assigned") == "unassigned":
        where.append("l.assigned_agent_id IS NULL")
    elif arg("assigned") == "assigned":
        where.append("l.assigned_agent_id IS NOT NULL")
    if arg("audited") == "yes":
        where.append("l.last_audit_id IS NOT NULL")
    elif arg("audited") == "no":
        where.append("l.last_audit_id IS NULL")
    if arg("outreach") == "yes":
        where.append("l.last_contact_at IS NOT NULL")
    elif arg("outreach") == "no":
        where.append("l.last_contact_at IS NULL")
    for key, col in (("niche", "niche_key"), ("area", "area_key")):
        value = arg(key)
        if value == "__none__":
            where.append(f"l.{col} IS NULL")
        elif value:
            where.append(f"l.{col} = %s")
            params.append(value.lower())
    if arg("batch_id"):
        where.append("l.batch_id = %s")
        params.append(arg("batch_id"))
    if arg("has_email") in ("1", "true", "True"):
        where.append("l.email IS NOT NULL AND l.email <> ''")
    q = arg("search").lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE} "
                     f"OR LOWER(COALESCE(l.contact_name, '')) {db.LIKE})")
        params += [term] * 3
    return " AND ".join(where), params


def _filter_key(me, args):
    return json.dumps({"u": me["id"], "r": me["role"], **{k: args.get(k) for k in FILTER_KEYS}}, sort_keys=True,
                      default=str)


def _count(me, args):
    clause, params = lead_filter(me, args)
    return cache.get_or_set(("count", _filter_key(me, args)),
                            lambda: db.scalar(f"SELECT COUNT(*) {_FROM} WHERE {clause}", params) or 0, ttl=120)


# ---------------------------------------------------------------------------
# Lead CRUD
# ---------------------------------------------------------------------------
def _lead_fields(data, partial=False):
    data = dict(data)
    for legacy, new in (("industry", "niche"), ("location", "area")):  # accept older client field names
        if legacy in data and new not in data:
            data[new] = data[legacy]
    out = {}
    if not partial or "company" in data:
        out["company"] = text(data, "company", required=True, max_len=200, label="Company")
    if not partial or "website" in data:
        website = text(data, "website", required=True, max_len=500, label="Website")
        domain = normalize_domain(website)
        if not domain:
            raise ApiError(422, "Enter a valid website address, for example example.com.", field="website")
        out["website"] = website if website.lower().startswith(("http://", "https://")) else f"https://{domain}"
        out["normalized_domain"] = domain
    for key, max_len in (("contact_name", 120), ("phone", 40), ("niche", 80), ("country", 80), ("area", 120),
                         ("address", 300), ("source", 80), ("notes", 4000)):
        if not partial or key in data:
            out[key] = text(data, key, max_len=max_len) or None
    if not partial or "email" in data:
        out["email"] = email_value(data, "email", required=False) or None
    return out


def _derived(fields):
    out = {}
    if "email" in fields:
        out["email_key"] = (fields["email"] or "").lower() or None
    if "phone" in fields:
        out["phone_key"] = normalize_phone(fields["phone"]) or None
    if "company" in fields:
        out["company_key"] = normalize_company(fields["company"])
    if "niche" in fields:
        out["niche_key"] = (fields["niche"] or "").lower() or None
    if "area" in fields:
        out["area_key"] = (fields["area"] or "").lower() or None
    return out


def _resolve_assignment(me, team_id, agent_id, tx):
    """Validate a target assignment for the caller. Returns (team_id, agent_id)."""
    team_id = team_id or None
    agent_id = agent_id or None
    if agent_id:
        agent = tx.query_one("SELECT id, team_id, role, is_active FROM users WHERE id = %s", (agent_id,))
        if not agent or not agent["is_active"] or agent["role"] == MANAGER:
            raise ApiError(422, "Choose an active Agent or Team Lead to assign.", field="agent_id")
        if team_id and team_id != agent["team_id"]:
            raise ApiError(422, "The selected person is not a member of the selected team.", field="agent_id")
        team_id = agent["team_id"]
    if team_id and not tx.query_one("SELECT 1 AS x FROM teams WHERE id = %s", (team_id,)):
        raise ApiError(422, "The selected team does not exist.", field="team_id")
    if me["role"] == TEAM_LEAD and team_id != me["team_id"]:
        raise forbidden("Team Leads can only assign leads within their own team.")
    return team_id, agent_id


def _similar(tx, fields, exclude_id=None):
    """Soft duplicate signals beyond the domain (email / phone / company)."""
    checks = []
    if fields.get("email"):
        checks.append(("email", "email_key", fields["email"].lower()))
    phone = normalize_phone(fields.get("phone"))
    if phone:
        checks.append(("phone", "phone_key", phone))
    company = normalize_company(fields.get("company"))
    if company:
        checks.append(("company", "company_key", company))
    matches = []
    for label, col, val in checks:
        sql = f"SELECT id, company, normalized_domain FROM leads WHERE {col} = %s"
        params = [val]
        if exclude_id:
            sql += " AND id <> %s"
            params.append(exclude_id)
        row = tx.query_one(sql + " LIMIT 1", params)
        if row:
            matches.append({"field": label, "company": row["company"], "domain": row["normalized_domain"]})
    return matches


def _dup_domain_error(tx, domain):
    existing = tx.query_one("SELECT * FROM leads WHERE normalized_domain = %s", (domain,))
    if not existing:
        return None
    extra = {"field": "website"}
    if can_access_lead(g.user, existing):
        extra["existing_lead_id"] = existing["id"]
    return conflict(f"A lead for {domain} already exists.", code_detail="duplicate_domain", **extra)


@bp.get("/api/leads")
@require()
def list_leads():
    me = g.user
    page, size, offset = page_args(25, 200)
    clause, params = lead_filter(me, request.args)
    sort = _SORTS.get(request.args.get("sort"), "l.created_at")
    order = "ASC" if request.args.get("order") == "asc" else "DESC"
    nulls = " NULLS LAST" if db.DIALECT == "postgres" else ""
    rows = db.query(f"""SELECT l.*, t.name AS team_name, u.name AS agent_name, b.file_name AS batch_name
                        {_FROM} LEFT JOIN lead_batches b ON b.id = l.batch_id
                        WHERE {clause} ORDER BY {sort} {order}{nulls}, l.id LIMIT %s OFFSET %s""",
                    params + [size, offset])
    return jsonify(paged([lead_out(r) for r in rows], _count(me, request.args), page, size))


@bp.get("/api/leads/summary")
@require()
def leads_summary():
    """Counts for the current filter: total, unassigned, audited, outreached."""
    me = g.user
    clause, params = lead_filter(me, request.args)

    def compute():
        row = db.query_one(f"""SELECT COUNT(*) AS total,
                                      SUM(CASE WHEN l.assigned_agent_id IS NULL THEN 1 ELSE 0 END) AS unassigned,
                                      SUM(CASE WHEN l.last_audit_id IS NOT NULL THEN 1 ELSE 0 END) AS audited,
                                      SUM(CASE WHEN l.last_contact_at IS NOT NULL THEN 1 ELSE 0 END) AS outreached
                               {_FROM} WHERE {clause}""", params)
        return {k: int(row[k] or 0) for k in ("total", "unassigned", "audited", "outreached")}
    return jsonify(cache.get_or_set(("summary", _filter_key(me, request.args)), compute, ttl=120))


@bp.get("/api/leads/facets")
@require()
def leads_facets():
    """Niche → area tree with counts for the lead-bank navigator (active leads
    within the caller's scope)."""
    me = g.user
    scope, sp = lead_scope(me)
    extra, ep = "", []
    if me["role"] == MANAGER and request.args.get("team_id"):
        extra, ep = " AND l.assigned_team_id = %s", [request.args["team_id"]]

    def compute():
        rows = db.query(f"""SELECT l.niche_key, MIN(l.niche) AS niche, l.area_key, MIN(l.area) AS area, COUNT(*) AS n,
                                   SUM(CASE WHEN l.assigned_agent_id IS NULL THEN 1 ELSE 0 END) AS unassigned
                            FROM leads l WHERE {scope} AND l.status <> 'archived'{extra}
                            GROUP BY l.niche_key, l.area_key""", list(sp) + ep)
        niches, areas = {}, {}
        for r in rows:
            nk = r["niche_key"] or "__none__"
            ak = r["area_key"] or "__none__"
            n = niches.setdefault(nk, {"key": nk, "label": r["niche"] or "No niche", "count": 0, "unassigned": 0,
                                       "areas": []})
            n["count"] += int(r["n"])
            n["unassigned"] += int(r["unassigned"] or 0)
            n["areas"].append({"key": ak, "label": r["area"] or "No area", "count": int(r["n"]),
                               "unassigned": int(r["unassigned"] or 0)})
            a = areas.setdefault(ak, {"key": ak, "label": r["area"] or "No area", "count": 0})
            a["count"] += int(r["n"])
        out = sorted(niches.values(), key=lambda x: (-x["count"], x["label"].lower()))
        for n in out:
            n["areas"].sort(key=lambda x: (-x["count"], x["label"].lower()))
        return {"niches": out, "areas": sorted(areas.values(), key=lambda x: (-x["count"], x["label"].lower())),
                "total": sum(n["count"] for n in out), "unassigned": sum(n["unassigned"] for n in out)}
    key = ("facets", me["id"], me["role"], request.args.get("team_id") or "")
    return jsonify(cache.get_or_set(key, compute, ttl=300))


@bp.get("/api/leads/<lead_id>")
@require()
def get_lead(lead_id):
    load_lead(g.user, lead_id)
    row = db.query_one(_LEAD_SELECT + " WHERE l.id = %s", (lead_id,))
    data = lead_out(row)
    data["audit_count"] = db.scalar("SELECT COUNT(*) FROM audits WHERE lead_id = %s", (lead_id,)) or 0
    data["email_count"] = db.scalar("SELECT COUNT(*) FROM emails WHERE lead_id = %s AND status <> 'draft'", (lead_id,)) or 0
    data["running_audit_id"] = db.scalar(
        "SELECT id FROM audits WHERE lead_id = %s AND status IN ('queued', 'running')", (lead_id,))
    return jsonify({"lead": data})


@bp.post("/api/leads")
@require()
@idempotent
def create_lead():
    me = g.user
    data = body()
    fields = _lead_fields(data)
    fields.update(_derived(fields))
    lid = security.new_id("lead_")
    now = now_iso()
    with db.transaction() as tx:
        dup = _dup_domain_error(tx, fields["normalized_domain"])
        if dup:
            raise dup
        similar = _similar(tx, fields)
        if similar and not data.get("allow_similar"):
            raise ApiError(409, "Possible duplicate: another lead has the same "
                           + ", ".join(m["field"] for m in similar) + ".", code="possible_duplicate", matches=similar)
        if me["role"] == AGENT:
            team_id, agent_id = me["team_id"], me["id"]
        else:
            team_id, agent_id = _resolve_assignment(me, data.get("team_id") or (me["team_id"] if me["role"] == TEAM_LEAD else None),
                                                    data.get("agent_id"), tx)
        fields.update(id=lid, status="assigned" if agent_id else "new", assigned_team_id=team_id,
                      assigned_agent_id=agent_id, created_by=me["id"], created_at=now, updated_at=now,
                      source=fields.get("source") or "Manual")
        cols = list(fields)
        tx.execute(f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                   [fields[c] for c in cols])
        events.audit(me, "LEAD_CREATED", "lead", lid, {"domain": fields["normalized_domain"]}, tx=tx)
        if agent_id or team_id:
            tx.execute("""INSERT INTO lead_assignments (id, lead_id, to_team_id, to_agent_id, changed_by, reason, created_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                       (security.new_id("las_"), lid, team_id, agent_id, me["id"], "Initial assignment", now))
    cache.bump()
    if agent_id and agent_id != me["id"]:
        events.notify([agent_id], "lead_assigned", "New lead assigned", f"{fields['company']} was assigned to you.", "lead", lid)
    lead_mgr_targets = [m for m in events.managers() if m != me["id"]] if me["role"] != MANAGER else []
    events.notify(lead_mgr_targets, "lead_created", "New lead", f"{me['name']} added {fields['company']}.", "lead", lid)
    return jsonify({"lead": lead_out(db.query_one(_LEAD_SELECT + " WHERE l.id = %s", (lid,)))}), 201


@bp.patch("/api/leads/<lead_id>")
@require()
def update_lead(lead_id):
    me = g.user
    data = body()
    lead = load_lead(me, lead_id)
    fields = _lead_fields(data, partial=True)
    if "status" in data:
        status = text(data, "status", max_len=20)
        if status not in STATUSES or status == "archived":
            raise ApiError(422, "Choose a valid status. Use Archive to archive a lead.", field="status")
        fields["status"] = status
    if not fields:
        raise ApiError(422, "Nothing to update.")
    version = data.get("version")
    if not isinstance(version, int):
        raise ApiError(422, "The lead version is required to save changes.", field="version")
    logged = dict(fields)
    fields.update(_derived(fields))
    with db.transaction() as tx:
        if "normalized_domain" in fields and fields["normalized_domain"] != lead["normalized_domain"]:
            dup = _dup_domain_error(tx, fields["normalized_domain"])
            if dup:
                raise dup
        sets = ", ".join(f"{k} = %s" for k in fields)
        tx.execute(f"UPDATE leads SET {sets}, version = version + 1, updated_at = %s WHERE id = %s AND version = %s",
                   list(fields.values()) + [now_iso(), lead_id, version])
        if tx.rowcount != 1:
            raise conflict("This lead was changed by someone else. Reload it and try again.", code_detail="stale")
        events.audit(me, "LEAD_UPDATED", "lead", lead_id, logged, tx=tx)
    cache.bump()
    return jsonify({"lead": lead_out(db.query_one(_LEAD_SELECT + " WHERE l.id = %s", (lead_id,)))})


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def _apply_assignment(tx, me, lead, team_id, agent_id, reason, version=None):
    if lead["status"] == "archived":
        raise ApiError(422, f"{lead['company']} is archived. Restore it before assigning.")
    if lead["assigned_team_id"] == team_id and lead["assigned_agent_id"] == agent_id:
        return False
    if me["role"] == TEAM_LEAD and lead["assigned_team_id"] != me["team_id"]:
        raise forbidden("Team Leads can only assign leads that belong to their team.")
    new_status = lead["status"]
    if agent_id and lead["status"] == "new":
        new_status = "assigned"
    elif not agent_id and lead["status"] == "assigned":
        new_status = "new"
    sql = ("UPDATE leads SET assigned_team_id = %s, assigned_agent_id = %s, status = %s, version = version + 1, "
           "updated_at = %s WHERE id = %s AND version = %s")
    tx.execute(sql, (team_id, agent_id, new_status, now_iso(), lead["id"],
                     version if isinstance(version, int) else lead["version"]))
    if tx.rowcount != 1:
        raise conflict(f"{lead['company']} was changed by someone else. Reload and try again.", code_detail="stale")
    tx.execute("""INSERT INTO lead_assignments (id, lead_id, from_team_id, from_agent_id, to_team_id, to_agent_id,
                  changed_by, reason, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (security.new_id("las_"), lead["id"], lead["assigned_team_id"], lead["assigned_agent_id"], team_id,
                agent_id, me["id"], reason, now_iso()))
    action = "LEAD_REASSIGNED" if lead["assigned_agent_id"] or lead["assigned_team_id"] else "LEAD_ASSIGNED"
    events.audit(me, action, "lead", lead["id"], {
        "from_team_id": lead["assigned_team_id"], "from_agent_id": lead["assigned_agent_id"],
        "to_team_id": team_id, "to_agent_id": agent_id, "reason": reason}, tx=tx)
    return True


def _notify_assignment(me, lead, agent_id):
    if agent_id and agent_id != me["id"]:
        events.notify([agent_id], "lead_assigned", "New lead assigned", f"{lead['company']} was assigned to you.",
                      "lead", lead["id"])
    old = lead["assigned_agent_id"]
    if old and old != agent_id and old != me["id"]:
        events.notify([old], "lead_reassigned", "Lead reassigned", f"{lead['company']} was reassigned to someone else.",
                      "lead", lead["id"])


@bp.post("/api/leads/<lead_id>/assign")
@require(MANAGER, TEAM_LEAD)
@idempotent
def assign_lead(lead_id):
    me = g.user
    data = body()
    reason = text(data, "reason", max_len=300) or None
    with db.transaction() as tx:
        lead = load_lead(me, lead_id, tx)
        team_id, agent_id = _resolve_assignment(me, data.get("team_id"), data.get("agent_id"), tx)
        changed = _apply_assignment(tx, me, lead, team_id, agent_id, reason, data.get("version"))
    if changed:
        cache.bump()
        _notify_assignment(me, lead, agent_id)
    return jsonify({"lead": lead_out(db.query_one(_LEAD_SELECT + " WHERE l.id = %s", (lead_id,)))})


def _target_ids(me, data):
    """Leads targeted by a bulk action: explicit ``lead_ids`` (≤ 500) or every
    lead matching ``filter`` (requires ``expected_count`` to match, so the user
    confirms exactly what they saw)."""
    if isinstance(data.get("filter"), dict):
        flt = data["filter"]
        clause, params = lead_filter(me, flt)
        total = db.scalar(f"SELECT COUNT(*) {_FROM} WHERE {clause}", params) or 0
        if total > BULK_LIMIT:
            raise ApiError(422, f"This selection has {total:,} leads. Narrow the filter to {BULK_LIMIT:,} or fewer.")
        if data.get("expected_count") != total:
            raise ApiError(409, f"The number of matching leads changed to {total:,}. Review and confirm again.",
                           code="count_changed", count=total)
        rows = db.query(f"SELECT l.id {_FROM} WHERE {clause} ORDER BY l.created_at, l.id", params)
        return [r["id"] for r in rows], True
    ids = data.get("lead_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise ApiError(422, "Select between 1 and 500 leads, or use select all matching.")
    ids = list(dict.fromkeys(str(i) for i in ids))
    for lid in ids:
        load_lead(me, lid)  # 404 for anything outside the caller's scope
    return ids, False


def _chunks(seq, n=1000):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


@bp.post("/api/leads/bulk-assign")
@require(MANAGER, TEAM_LEAD)
@idempotent
def bulk_assign():
    """Assign many leads to one team/person, or split them evenly across a
    team's agents (``mode: "split"``)."""
    me = g.user
    data = body()
    reason = text(data, "reason", max_len=300) or None
    mode = data.get("mode") or "single"
    with db.transaction() as tx:
        if mode == "split":
            team_id, _agent = _resolve_assignment(me, data.get("team_id") or (me["team_id"] if me["role"] == TEAM_LEAD else None),
                                                  None, tx)
            if not team_id:
                raise ApiError(422, "Choose the team to split the leads across.", field="team_id")
            members = [r["id"] for r in tx.query("""SELECT id FROM users WHERE team_id = %s AND is_active = 1
                                                    AND role = 'AGENT' ORDER BY name""", (team_id,))]
            if not members:
                raise ApiError(422, "This team has no active agents to split leads across.")
        else:
            team_id, agent_id = _resolve_assignment(me, data.get("team_id"), data.get("agent_id"), tx)
            members = [agent_id]
    ids, by_filter = _target_ids(me, data)
    op = security.new_id("op")[:14]
    now = now_iso()
    per_agent = {m: 0 for m in members}
    updated = 0
    for chunk_no, chunk in enumerate(_chunks(ids, 1000)):
        groups = {}
        for i, lid in enumerate(chunk):
            agent = members[(chunk_no * 1000 + i) % len(members)]
            groups.setdefault(agent, []).append(lid)
        with db.transaction() as tx:
            for agent, lids in groups.items():
                ph = ",".join(["%s"] * len(lids))
                guard = " AND l.assigned_team_id = %s" if me["role"] == TEAM_LEAD else ""
                gp = [me["team_id"]] if me["role"] == TEAM_LEAD else []
                # History first, so it records the previous assignment.
                tx.execute(f"""INSERT INTO lead_assignments (id, lead_id, from_team_id, from_agent_id, to_team_id,
                                   to_agent_id, changed_by, reason, created_at)
                               SELECT 'las_' || %s || '_' || l.id, l.id, l.assigned_team_id, l.assigned_agent_id, %s, %s,
                                      %s, %s, %s
                               FROM leads l WHERE l.id IN ({ph}) AND l.status <> 'archived'{guard}
                                 AND (COALESCE(l.assigned_team_id, '') <> COALESCE(%s, '')
                                      OR COALESCE(l.assigned_agent_id, '') <> COALESCE(%s, ''))""",
                           [op, team_id, agent, me["id"], reason, now] + lids + gp + [team_id, agent])
                tx.execute(f"""UPDATE leads SET assigned_team_id = %s, assigned_agent_id = %s, version = version + 1,
                                   updated_at = %s,
                                   status = CASE WHEN %s IS NOT NULL AND status = 'new' THEN 'assigned'
                                                 WHEN %s IS NULL AND status = 'assigned' THEN 'new' ELSE status END
                               WHERE id IN ({ph}) AND status <> 'archived'
                                 {"AND assigned_team_id = %s" if me["role"] == TEAM_LEAD else ""}
                                 AND (COALESCE(assigned_team_id, '') <> COALESCE(%s, '')
                                      OR COALESCE(assigned_agent_id, '') <> COALESCE(%s, ''))""",
                           [team_id, agent, now, agent, agent] + lids + gp + [team_id, agent])
                updated += tx.rowcount
                if agent:
                    per_agent[agent] = per_agent.get(agent, 0) + tx.rowcount
    events.audit(me, "LEADS_BULK_ASSIGNED", "lead", None, {
        "updated": updated, "selected": len(ids), "team_id": team_id, "mode": mode, "by_filter": by_filter,
        "filter": data.get("filter") if by_filter else None, "reason": reason,
        "per_agent": {k: v for k, v in per_agent.items() if k and v}})
    cache.bump()
    for agent, n in per_agent.items():
        if agent and n and agent != me["id"]:
            events.notify([agent], "lead_assigned", "Leads assigned", f"{n:,} lead(s) were assigned to you.", "lead", None)
    return jsonify({"updated": updated, "selected": len(ids),
                    "per_agent": {k: v for k, v in per_agent.items() if k and v}})


def _set_archived(data, archived):
    me = g.user
    ids, by_filter = _target_ids(me, data)
    now = now_iso()
    done = 0
    for chunk in _chunks(ids, 1000):
        ph = ",".join(["%s"] * len(chunk))
        with db.transaction() as tx:
            if archived:
                tx.execute(f"UPDATE leads SET status = 'archived', version = version + 1, updated_at = %s "
                           f"WHERE id IN ({ph}) AND status <> 'archived'", [now] + chunk)
            else:
                tx.execute(f"""UPDATE leads SET version = version + 1, updated_at = %s,
                                   status = CASE WHEN last_audit_id IS NOT NULL THEN 'audited'
                                                 WHEN assigned_agent_id IS NOT NULL THEN 'assigned' ELSE 'new' END
                               WHERE id IN ({ph}) AND status = 'archived'""", [now] + chunk)
            done += tx.rowcount
    events.audit(me, "LEADS_ARCHIVED" if archived else "LEADS_RESTORED", "lead", ids[0] if len(ids) == 1 else None,
                 {"count": done, "by_filter": by_filter, "filter": data.get("filter") if by_filter else None})
    cache.bump()
    return done


@bp.post("/api/leads/archive")
@require(MANAGER, TEAM_LEAD)
def archive_leads():
    return jsonify({"updated": _set_archived(body(), True)})


@bp.post("/api/leads/restore")
@require(MANAGER, TEAM_LEAD)
def restore_leads():
    return jsonify({"updated": _set_archived(body(), False)})


@bp.get("/api/leads/export")
@require(MANAGER, TEAM_LEAD)
def export_leads():
    """Stream the current filter as CSV (keyset paging keeps memory flat)."""
    me = g.user
    rate_limit(f"export:{me['id']}", 10, 600)
    clause, params = lead_filter(me, request.args)
    total = db.scalar(f"SELECT COUNT(*) {_FROM} WHERE {clause}", params) or 0
    events.audit(me, "LEADS_EXPORTED", "lead", None, {"count": total, "filter": {k: request.args.get(k)
                                                                                   for k in FILTER_KEYS if request.args.get(k)}})
    cols = [("Company", "company"), ("Website", "website"), ("Niche", "niche"), ("Area", "area"), ("Phone", "phone"),
            ("Email", "email"), ("Address", "address"), ("Rating", "rating"), ("Reviews", "review_count"),
            ("Status", "status"), ("Team", "team_name"), ("Agent", "agent_name"), ("SEO score", "last_audit_score"),
            ("Last audit", "last_audit_at"), ("Last contact", "last_contact_at"), ("Created", "created_at")]

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c[0] for c in cols])
        yield "﻿" + buf.getvalue()
        last = None
        while True:
            buf.seek(0)
            buf.truncate()
            cond, extra = "", []
            if last:
                cond = " AND (l.created_at > %s OR (l.created_at = %s AND l.id > %s))"
                extra = [last[0], last[0], last[1]]
            rows = db.query(f"""SELECT l.*, t.name AS team_name, u.name AS agent_name {_FROM}
                                WHERE {clause}{cond} ORDER BY l.created_at, l.id LIMIT 5000""", params + extra)
            if not rows:
                break
            for r in rows:
                w.writerow(["" if r.get(k) is None else r.get(k) for _h, k in cols])
            last = (rows[-1]["created_at"], rows[-1]["id"])
            yield buf.getvalue()
    return Response(stream_with_context(generate()), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="lead-bank-export.csv"'})


@bp.get("/api/leads/<lead_id>/activity")
@require()
def lead_activity(lead_id):
    lead = load_lead(g.user, lead_id)
    logs = db.query("""SELECT al.action, al.user_email, al.metadata, al.created_at, u.name AS user_name
                       FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
                       WHERE (al.entity_type = 'lead' AND al.entity_id = %s)
                          OR (al.entity_type = 'audit' AND al.entity_id IN (SELECT id FROM audits WHERE lead_id = %s))
                          OR (al.entity_type = 'email' AND al.entity_id IN (SELECT id FROM emails WHERE lead_id = %s))
                       ORDER BY al.created_at DESC LIMIT 200""", (lead_id, lead_id, lead_id))
    assignments = db.query("""SELECT la.*, cb.name AS changed_by_name, ft.name AS from_team_name, tt.name AS to_team_name,
                                     fa.name AS from_agent_name, ta.name AS to_agent_name
                              FROM lead_assignments la
                              LEFT JOIN users cb ON cb.id = la.changed_by
                              LEFT JOIN teams ft ON ft.id = la.from_team_id LEFT JOIN teams tt ON tt.id = la.to_team_id
                              LEFT JOIN users fa ON fa.id = la.from_agent_id LEFT JOIN users ta ON ta.id = la.to_agent_id
                              WHERE la.lead_id = %s ORDER BY la.created_at DESC""", (lead_id,))
    batch = None
    if lead.get("batch_id"):
        b = lead_import.get_batch(lead["batch_id"])
        if b:
            batch = {"id": b["id"], "file_name": b["file_name"], "created_by_name": b["created_by_name"],
                     "created_at": b["created_at"]}
    return jsonify({"activity": logs, "assignments": assignments, "imported_from": batch})


@bp.get("/api/leads/<lead_id>/assignees")
@require(MANAGER, TEAM_LEAD)
def assignees(lead_id):
    """People the caller may assign this lead to."""
    me = g.user
    load_lead(me, lead_id)
    return jsonify(_assignee_options(me))


@bp.get("/api/assignees")
@require(MANAGER, TEAM_LEAD)
def assignee_options():
    return jsonify(_assignee_options(g.user))


def _assignee_options(me):
    if me["role"] == MANAGER:
        teams = db.query("SELECT id, name FROM teams ORDER BY name")
        people = db.query("""SELECT id, name, role, team_id FROM users WHERE is_active = 1 AND role <> 'MANAGER'
                             ORDER BY name""")
    else:
        teams = db.query("SELECT id, name FROM teams WHERE id = %s", (me["team_id"],))
        people = db.query("""SELECT id, name, role, team_id FROM users WHERE is_active = 1 AND team_id = %s
                             ORDER BY name""", (me["team_id"],))
    return {"teams": teams, "people": people}


# ---------------------------------------------------------------------------
# Imports (lead batches)
# ---------------------------------------------------------------------------
def _batch_visible(me, batch):
    if not batch:
        return False
    if me["role"] == MANAGER:
        return True
    return me["role"] == TEAM_LEAD and (batch["created_by"] == me["id"] or batch.get("team_id") == me["team_id"])


def _load_batch(batch_id):
    batch = lead_import.get_batch(batch_id)
    if not _batch_visible(g.user, batch):
        raise not_found("Import")
    return batch


@bp.post("/api/lead-imports")
@require(MANAGER, TEAM_LEAD)
def preview_import():
    """Upload one scraped file; returns detected columns and a niche/area guess.
    Nothing is imported until the batch is started."""
    rate_limit(f"import-upload:{g.user['id']}", 300, 600)
    request.max_content_length = lead_import.MAX_FILE_BYTES + 1024 * 1024
    form = request.form
    return jsonify(lead_import.create_preview(g.user, request.files.get("file"), form.get("niche", ""),
                                              form.get("area", ""))), 201


@bp.post("/api/lead-batches/<batch_id>/start")
@require(MANAGER, TEAM_LEAD)
@idempotent
def start_import(batch_id):
    me = g.user
    batch = _load_batch(batch_id)
    if batch["status"] != "pending":
        raise conflict("This file has already been imported or is being imported.")
    data = body()
    headers = json.loads(batch["headers"] or "[]")
    mapping = data.get("mapping") if isinstance(data.get("mapping"), dict) else json.loads(batch["mapping"] or "{}")
    mapping = {f: (mapping.get(f) if mapping.get(f) in headers else None) for f in lead_import.FIELDS}
    if not mapping["website"]:
        raise ApiError(422, f"{batch['file_name']}: choose the column that contains the website.", field="mapping")
    niche = text(data, "niche", required=True, max_len=80, label="Niche")
    area = text(data, "area", required=True, max_len=120, label="Area")
    with db.transaction() as tx:
        team_id, agent_id = _resolve_assignment(
            me, data.get("team_id") or (me["team_id"] if me["role"] == TEAM_LEAD else None), data.get("agent_id"), tx)
        tx.execute("""UPDATE lead_batches SET niche = %s, area = %s, niche_key = %s, area_key = %s, mapping = %s,
                          team_id = %s, agent_id = %s WHERE id = %s AND status = 'pending'""",
                   (niche, area, niche.lower(), area.lower(), json.dumps(mapping), team_id, agent_id, batch_id))
        if tx.rowcount != 1:
            raise conflict("This file has already been imported or is being imported.")
        events.audit(me, "LEAD_IMPORT_STARTED", "lead_batch", batch_id,
                     {"file": batch["file_name"], "rows": batch["total_rows"], "niche": niche, "area": area,
                      "team_id": team_id, "agent_id": agent_id}, tx=tx)
    lead_import.start(batch_id)
    return jsonify({"batch": lead_import.batch_out(lead_import.get_batch(batch_id))}), 202


@bp.delete("/api/lead-batches/<batch_id>")
@require(MANAGER, TEAM_LEAD)
def discard_import(batch_id):
    batch = _load_batch(batch_id)
    if batch["status"] != "pending":
        raise conflict("Only files that have not been imported yet can be discarded.")
    if batch["created_by"] != g.user["id"] and g.user["role"] != MANAGER:
        raise forbidden()
    if batch["stored_name"]:
        import os
        try:
            os.remove(os.path.join(lead_import.IMPORT_DIR, batch["stored_name"]))
        except OSError:
            pass
    db.execute("DELETE FROM lead_batches WHERE id = %s AND status = 'pending'", (batch_id,))
    return jsonify({"ok": True})


@bp.get("/api/lead-batches")
@require(MANAGER, TEAM_LEAD)
def list_batches():
    me = g.user
    page, size, offset = page_args(25, 100)
    where, params = ["b.status <> 'pending'"], []
    if me["role"] == TEAM_LEAD:
        where.append("(b.created_by = %s OR b.team_id = %s)")
        params += [me["id"], me["team_id"]]
    if request.args.get("status") in ("queued", "processing", "completed", "failed"):
        where.append("b.status = %s")
        params.append(request.args["status"])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(b.file_name) {db.LIKE} OR COALESCE(b.niche_key, '') {db.LIKE} "
                     f"OR COALESCE(b.area_key, '') {db.LIKE})")
        params += [term] * 3
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM lead_batches b WHERE {clause}", params)
    rows = db.query(f"""SELECT b.*, u.name AS created_by_name, t.name AS team_name, a.name AS agent_name
                        FROM lead_batches b LEFT JOIN users u ON u.id = b.created_by
                        LEFT JOIN teams t ON t.id = b.team_id LEFT JOIN users a ON a.id = b.agent_id
                        WHERE {clause} ORDER BY b.created_at DESC LIMIT %s OFFSET %s""", params + [size, offset])
    totals = db.query_one(f"""SELECT COUNT(*) AS files, COALESCE(SUM(b.processed_rows), 0) AS rows_read,
                                     COALESCE(SUM(b.imported), 0) AS imported, COALESCE(SUM(b.duplicates), 0) AS duplicates,
                                     COALESCE(SUM(b.no_website), 0) AS no_website
                              FROM lead_batches b WHERE {clause}""", params)
    return jsonify(paged([lead_import.batch_out(r) for r in rows], total, page, size)
                   | {"totals": {k: int(v or 0) for k, v in totals.items()}})


@bp.get("/api/lead-batches/<batch_id>")
@require(MANAGER, TEAM_LEAD)
def get_batch(batch_id):
    return jsonify({"batch": lead_import.batch_out(_load_batch(batch_id))})


@bp.get("/api/lead-batches/<batch_id>/rejects")
@require(MANAGER, TEAM_LEAD)
def batch_rejects(batch_id):
    _load_batch(batch_id)
    page, size, offset = page_args(50, 200)
    where, params = ["r.batch_id = %s"], [batch_id]
    reason = request.args.get("reason")
    if reason in ("no_website", "invalid_website", "duplicate_in_file", "duplicate_existing"):
        where.append("r.reason = %s")
        params.append(reason)
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM lead_batch_rejects r WHERE {clause}", params)
    rows = db.query(f"""SELECT r.row_number, r.company, r.website, r.phone, r.reason, r.existing_lead_id,
                               l.company AS existing_company, l.normalized_domain AS existing_domain
                        FROM lead_batch_rejects r LEFT JOIN leads l ON l.id = r.existing_lead_id
                        WHERE {clause} ORDER BY r.row_number LIMIT %s OFFSET %s""", params + [size, offset])
    me = g.user
    for r in rows:  # only reveal existing leads the caller can see
        if r["existing_lead_id"]:
            lead = db.query_one("SELECT assigned_team_id, assigned_agent_id FROM leads WHERE id = %s", (r["existing_lead_id"],))
            if not lead or not can_access_lead(me, lead):
                r["existing_lead_id"] = None
    return jsonify(paged(rows, total, page, size))


@bp.get("/api/lead-batches/<batch_id>/rejects.csv")
@require(MANAGER, TEAM_LEAD)
def batch_rejects_csv(batch_id):
    batch = _load_batch(batch_id)
    name = batch["file_name"].rsplit(".", 1)[0][:80]
    return Response("﻿" + lead_import.rejects_csv(batch_id), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name} - rejected rows.csv"'})
