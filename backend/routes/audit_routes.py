# -*- coding: utf-8 -*-
"""/api/audits/* and /api/reports/* — engine integration, results, deliverables.

The documented legacy endpoints (POST /api/audit, /api/audit/<id>/status,
/result, /client-data, /client-docx, /client-pdf, /client-report/<id>,
/api/history) are preserved as authenticated aliases of the new handlers.
"""

import json
import math
import os
import re
import zlib

from flask import Blueprint, Response, g, jsonify, request, send_file

from backend import audit_runner, db, events, reports, results
from backend.auth import AGENT, MANAGER, TEAM_LEAD, can_edit_lead, idempotent, lead_scope, load_audit, load_lead, require
from backend.errors import ApiError, conflict, forbidden, not_found
from backend.report_context import prepare_client_report_context
from backend.util import body, normalize_company, normalize_domain, now_iso, page_args, paged, rate_limit, text

bp = Blueprint("audits", __name__)

_AUDIT_SELECT = """SELECT a.*, l.company, l.normalized_domain, l.assigned_team_id, l.assigned_agent_id,
                          u.name AS requested_by_name, t.name AS team_name
                   FROM audits a JOIN leads l ON l.id = a.lead_id
                   LEFT JOIN users u ON u.id = a.requested_by
                   LEFT JOIN teams t ON t.id = a.team_id"""


def audit_out(row):
    keys = ("id", "lead_id", "company", "normalized_domain", "url", "status", "percent", "step", "pages_discovered",
            "pages_processed", "issues_found", "score", "grade", "critical_count", "high_count", "medium_count",
            "low_count", "error", "created_at", "started_at", "completed_at", "requested_by", "requested_by_name",
            "team_name", "batch_id", "priority")
    return {k: row.get(k) for k in keys}


def _load_result(audit_row):
    if audit_row["status"] != "completed":
        raise ApiError(409, "Results are available once the audit has completed.", code="not_ready")
    res = results.load(audit_row["id"])
    if not res:
        raise ApiError(410, "The stored result for this audit is no longer available.", code="gone")
    return res


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------
@bp.get("/api/audits")
@require()
def list_audits():
    me = g.user
    page, size, offset = page_args(25)
    scope, params = lead_scope(me)
    where, params = [scope], list(params)
    status = request.args.get("status")
    if status in ("queued", "running", "completed", "failed", "cancelled"):
        where.append("a.status = %s")
        params.append(status)
    elif status == "active":
        where.append("a.status IN ('queued', 'running')")
    for key in ("lead_id", "batch_id"):
        if request.args.get(key):
            where.append(f"a.{key} = %s")
            params.append(request.args[key])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE})")
        params += [term, term]
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM audits a JOIN leads l ON l.id = a.lead_id WHERE {clause}", params)
    if status == "active":  # running first, then the waiting line in the order it will (roughly) be served
        order = "CASE WHEN a.status = 'running' THEN 0 ELSE 1 END, a.priority DESC, a.created_at, a.id"
    else:
        order = "a.created_at DESC"
    rows = db.query(f"{_AUDIT_SELECT} WHERE {clause} ORDER BY {order} LIMIT %s OFFSET %s", params + [size, offset])
    items = [audit_out(r) for r in rows]
    for item, row in zip(items, rows):
        if row["status"] == "queued":
            item["queue_position"] = audit_runner.queue_position(row)
    return jsonify(paged(items, total, page, size))


@bp.post("/api/audits")
@bp.post("/api/audit")
@require()
@idempotent
def start_audit():
    me = g.user
    rate_limit(f"audit-start:{me['id']}", 20, 600)
    data = body()
    lead_id = text(data, "lead_id", required=False, max_len=64, label="Lead")
    url_input = text(data, "url", required=False, max_len=2000, label="URL") or text(data, "website", required=False, max_len=2000, label="Website")

    if not lead_id and not url_input:
        raise ApiError(422, "Either choose a lead or enter a website URL to audit.", field="lead_id")

    if lead_id:
        lead = load_lead(me, lead_id)
        if lead["status"] == "archived":
            raise ApiError(422, "Restore this lead before auditing it.")
    else:
        domain = normalize_domain(url_input)
        if not domain:
            raise ApiError(422, "Enter a valid website address, for example example.com.", field="url")
        from backend import security
        scope, params = lead_scope(me)
        lead = db.query_one(f"SELECT l.* FROM leads l WHERE l.normalized_domain = %s AND {scope} LIMIT 1",
                            [domain] + list(params))
        if not lead:
            full_url = url_input if url_input.lower().startswith(("http://", "https://")) else f"https://{domain}"
            company_name = domain.split(".")[0].replace("-", " ").replace("_", " ").title()
            lead_id = security.new_id("led_")
            team_id = me.get("team_id")
            agent_id = me["id"] if me.get("role") == AGENT else None
            now = now_iso()
            company_key = normalize_company(company_name)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO leads (id, company, company_key, website, normalized_domain, status, assigned_team_id, "
                    "assigned_agent_id, source, created_by, created_at, updated_at, version) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                    (lead_id, company_name, company_key, full_url, domain, "new", team_id, agent_id, "Direct audit",
                     me["id"], now, now)
                )
            lead = db.query_one("SELECT * FROM leads WHERE id = %s", (lead_id,))
        elif lead.get("status") == "archived":
            if me["role"] not in (MANAGER, TEAM_LEAD) or not can_edit_lead(me, lead):
                raise ApiError(422, "Restore this lead first.")
            db.execute("UPDATE leads SET status = 'new', updated_at = %s WHERE id = %s", (now_iso(), lead["id"]))
            lead = db.query_one("SELECT * FROM leads WHERE id = %s", (lead["id"],))

    max_pages = data.get("max_pages")
    if max_pages is not None:
        try:
            max_pages = int(max_pages)
            if not 1 <= max_pages <= 500:
                raise ApiError(422, "Pages to crawl must be between 1 and 500.", field="max_pages")
        except (ValueError, TypeError):
            raise ApiError(422, "Max pages must be a whole number.", field="max_pages")

    audit_id = audit_runner.start(me, lead, max_pages)
    return jsonify({"audit": audit_out(db.query_one(_AUDIT_SELECT + " WHERE a.id = %s", (audit_id,)))}), 202


@bp.get("/api/audits/<audit_id>")
@bp.get("/api/audit/<audit_id>/status")
@require()
def get_audit(audit_id):
    load_audit(g.user, audit_id)
    row = db.query_one(_AUDIT_SELECT + " WHERE a.id = %s", (audit_id,))
    data = audit_out(row)
    data["logs"] = audit_runner.live_logs(audit_id, row)[-120:]
    if row["status"] == "queued":
        ahead = audit_runner.queue_position(row)
        snap = audit_runner.snapshot()
        data["queue_position"] = ahead
        data["eta_seconds"] = audit_runner.eta_seconds(ahead, snap["workers"], snap["running"])
        waiting = f"Waiting in the queue · {ahead:,} ahead" if ahead else "Next in the queue"
        if audit_runner.runs_in_app():
            who = row["requested_by_name"] or "the requester"
            data["step"] = f"{waiting} · runs on {who}'s computer, {snap['workers']} at a time"
        else:
            data["step"] = f"{waiting} · {snap['workers']} audits run at a time"
    return jsonify({"audit": data})


@bp.post("/api/audits/<audit_id>/cancel")
@require()
def cancel_audit(audit_id):
    me = g.user
    row = load_audit(me, audit_id)
    if row["status"] not in ("queued", "running"):
        raise conflict("This audit is no longer running.")
    if (me["role"] != MANAGER and row["requested_by"] != me["id"]
            and not (me["role"] == TEAM_LEAD and can_edit_lead(me, row))):
        raise ApiError(403, "Only the person who started this audit, a Team Lead or a Manager can cancel it.")
    audit_runner.cancel(audit_id)
    events.audit(me, "AUDIT_CANCEL_REQUESTED", "audit", audit_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Bulk audits
# ---------------------------------------------------------------------------
BATCH_CAP = {MANAGER: 500, TEAM_LEAD: 100, AGENT: 100}      # leads per bulk run
QUEUE_CAP = {MANAGER: 2000, TEAM_LEAD: 300, AGENT: 300}     # audits one person may have waiting
RECENT_DAYS = 30
_ACTIVE = "EXISTS (SELECT 1 FROM audits q WHERE q.lead_id = l.id AND q.status IN ('queued', 'running'))"


def _bulk_selection(me, data):
    """WHERE clause + params for the leads a bulk audit targets (always within scope)."""
    if isinstance(data.get("filter"), dict):
        from backend.routes.lead_routes import lead_filter
        return lead_filter(me, data["filter"])
    ids = data.get("lead_ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError(422, "Select at least one lead to audit.")
    ids = list(dict.fromkeys(str(i) for i in ids))[:5000]
    scope, scope_params = lead_scope(me)
    return f"{scope} AND l.id IN ({', '.join(['%s'] * len(ids))})", list(scope_params) + ids


def _bulk_preview(me, data):
    from datetime import datetime, timedelta, timezone
    clause, params = _bulk_selection(me, data)
    include_recent = bool(data.get("include_recent"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    recent = "l.last_audit_at IS NOT NULL AND l.last_audit_at >= %s"
    row = db.query_one(f"""SELECT COUNT(*) AS selected,
            SUM(CASE WHEN l.status = 'archived' THEN 1 ELSE 0 END) AS archived,
            SUM(CASE WHEN l.status <> 'archived' AND {_ACTIVE} THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN l.status <> 'archived' AND NOT {_ACTIVE} AND {recent} THEN 1 ELSE 0 END) AS recent
        FROM leads l WHERE {clause}""", [cutoff] + params)
    selected = row["selected"] or 0
    skipped = {"archived": row["archived"] or 0, "in_progress": row["in_progress"] or 0,
               "recently_audited": 0 if include_recent else (row["recent"] or 0)}
    eligible = selected - sum(skipped.values())
    cap = BATCH_CAP[me["role"]]
    waiting_mine = db.scalar("SELECT COUNT(*) FROM audits WHERE requested_by = %s AND status = 'queued'", (me["id"],)) or 0
    room = max(0, QUEUE_CAP[me["role"]] - waiting_mine)
    will_queue = max(0, min(eligible, cap, room))
    where = f"{clause} AND l.status <> 'archived' AND NOT {_ACTIVE}"
    where_params = list(params)
    if not include_recent:
        where += " AND NOT (" + recent + ")"
        where_params.append(cutoff)
    snap = audit_runner.snapshot()
    sample = db.query(f"SELECT l.company, l.normalized_domain FROM leads l WHERE {where} "
                      f"ORDER BY l.created_at DESC, l.id LIMIT 5", where_params) if will_queue else []
    return {
        "selected": selected, "eligible": eligible, "will_queue": will_queue, "cap": cap,
        "not_now": max(0, eligible - will_queue), "queue_room": room, "skipped": skipped,
        "recent_days": RECENT_DAYS, "include_recent": include_recent, "recent_available": row["recent"] or 0,
        "queue": snap, "eta_seconds": audit_runner.eta_seconds(snap["queued"] + will_queue, snap["workers"],
                                                               snap["running"]),
        "sample": sample,
    }, where, where_params


@bp.post("/api/audits/bulk/preview")
@require()
def bulk_preview():
    preview, _w, _p = _bulk_preview(g.user, body())
    return jsonify(preview)


@bp.post("/api/audits/bulk")
@require()
@idempotent
def bulk_start():
    me = g.user
    rate_limit(f"bulk-audit:{me['id']}", 10, 600)
    data = body()
    preview, where, params = _bulk_preview(me, data)
    if data.get("confirm_count") != preview["will_queue"]:
        raise ApiError(409, "The selection changed since you reviewed it. Check the new numbers and confirm again.",
                       code="count_changed", preview=preview)
    if not preview["will_queue"]:
        if preview["eligible"] and not preview["queue_room"]:
            raise ApiError(422, f"You already have {QUEUE_CAP[me['role']]:,} audits waiting. Wait for some to finish "
                                "or cancel a batch first.")
        raise ApiError(422, "None of the selected leads need an audit right now.")
    leads = db.query(f"SELECT l.id, l.website, l.assigned_team_id FROM leads l WHERE {where} "
                     f"ORDER BY l.created_at DESC, l.id LIMIT %s", params + [preview["will_queue"]])
    label = text(data, "label", max_len=200) or f"{len(leads):,} selected leads"
    skipped = dict(preview["skipped"], not_now=preview["not_now"])
    max_pages = data.get("max_pages")
    if max_pages is not None:
        try:
            max_pages = int(max_pages)
        except (ValueError, TypeError):
            max_pages = None
    batch_id = audit_runner.enqueue_bulk(me, leads, label, skipped, max_pages=max_pages)
    return jsonify({"batch": _batch_out(_load_batch(me, batch_id))}), 202


def _batch_scope(me):
    if me["role"] == MANAGER:
        return "1=1", []
    if me["role"] == TEAM_LEAD:
        return "(b.team_id = %s OR b.created_by = %s)", [me["team_id"], me["id"]]
    return "b.created_by = %s", [me["id"]]


_BATCH_SELECT = "SELECT b.*, u.name AS created_by_name FROM audit_batches b LEFT JOIN users u ON u.id = b.created_by"


def _load_batch(me, batch_id):
    scope, params = _batch_scope(me)
    row = db.query_one(f"{_BATCH_SELECT} WHERE b.id = %s AND {scope}", [batch_id] + params)
    if not row:
        raise not_found("Bulk audit")
    return row


def _batch_out(row, counts=None, snap=None):
    counts = counts or audit_runner.batch_counts([row["id"]])[row["id"]]
    snap = snap or audit_runner.snapshot()
    try:
        skipped = json.loads(row.get("skipped") or "{}")
    except ValueError:
        skipped = {}
    out = {k: row.get(k) for k in ("id", "label", "status", "total", "created_by", "created_by_name", "created_at",
                                    "completed_at")}
    out.update(counts=counts, skipped=skipped, done=counts["completed"] + counts["failed"] + counts["cancelled"])
    if row["status"] == "active" and (counts["queued"] or counts["running"]):
        # Bulk audits take turns between the people who queued them, so a batch
        # gets roughly workers / people of the capacity.
        people = max(1, db.scalar("SELECT COUNT(DISTINCT requested_by) FROM audits WHERE status = 'queued'") or 1)
        share = max(snap["workers"] / people, 0.5)
        out["eta_seconds"] = int(math.ceil(counts["queued"] / share) * snap["avg_seconds"] + snap["avg_seconds"])
    return out


@bp.get("/api/audit-batches")
@require()
def list_batches():
    me = g.user
    page, size, offset = page_args(20, 100)
    scope, params = _batch_scope(me)
    where = [scope]
    if request.args.get("status") in ("active", "completed", "cancelled"):
        where.append("b.status = %s")
        params.append(request.args["status"])
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM audit_batches b WHERE {clause}", params)
    rows = db.query(f"{_BATCH_SELECT} WHERE {clause} ORDER BY CASE WHEN b.status = 'active' THEN 0 ELSE 1 END, "
                    f"b.created_at DESC LIMIT %s OFFSET %s", params + [size, offset])
    counts = audit_runner.batch_counts([r["id"] for r in rows])
    snap = audit_runner.snapshot()
    return jsonify(paged([_batch_out(r, counts[r["id"]], snap) for r in rows], total, page, size) | {"queue": snap})


@bp.get("/api/audit-batches/<batch_id>")
@require()
def get_batch(batch_id):
    return jsonify({"batch": _batch_out(_load_batch(g.user, batch_id))})


@bp.post("/api/audit-batches/<batch_id>/cancel")
@require()
def cancel_batch(batch_id):
    me = g.user
    row = _load_batch(me, batch_id)
    if row["status"] != "active":
        raise conflict("This bulk audit has already finished.")
    dropped = audit_runner.cancel_batch(batch_id)
    events.audit(me, "BULK_AUDIT_CANCELLED", "audit_batch", batch_id, {"cancelled": dropped})
    return jsonify({"batch": _batch_out(_load_batch(me, batch_id)), "cancelled": dropped})


@bp.get("/api/audit-queue")
@require()
def queue_status():
    me = g.user
    snap = audit_runner.snapshot()
    snap["mine_queued"] = db.scalar("SELECT COUNT(*) FROM audits WHERE requested_by = %s AND status = 'queued'",
                                    (me["id"],)) or 0
    snap["queue_cap"] = QUEUE_CAP[me["role"]]
    snap["batch_cap"] = BATCH_CAP[me["role"]]
    return jsonify(snap)


def _shape_check(c):
    return {k: c.get(k) for k in ("id", "category", "label", "status", "severity", "effort", "detail", "fix", "why",
                                  "where", "scope")}


@bp.get("/api/audits/<audit_id>/result")
@bp.get("/api/audit/<audit_id>/result")
@require()
def audit_result(audit_id):
    row = load_audit(g.user, audit_id)
    res = _load_result(row)
    sec = res.get("security_audit") or {}
    psi = res.get("psi") or {}

    def psi_summary(strategy):
        p = psi.get(strategy) or {}
        if not p.get("ok") and not p.get("scores"):
            return None
        field = p.get("field") or {}
        return {"scores": p.get("scores") or {}, "lab": p.get("lab") or {},
                "field_available": bool(field.get("available") or field.get("metrics"))}

    pages = []
    for f in res.get("all_facts") or []:
        pages.append({"url": f.get("final_url") or f.get("url"), "status": f.get("status"), "title": f.get("title"),
                      "word_count": f.get("word_count"), "h1_count": f.get("h1_count"),
                      "meta_desc_len": f.get("meta_desc_len"), "noindex": bool(f.get("noindex")),
                      "canonical_ok": f.get("canonical_ok"), "page_authority": f.get("page_authority")})
    states = {r["check_id"]: r["status"] for r in db.query(
        "SELECT check_id, status FROM audit_issue_states WHERE audit_id = %s", (audit_id,))}
    authority = res.get("authority") or {}
    traffic = res.get("traffic") or {}
    return jsonify({
        "audit_id": audit_id, "domain": res.get("domain"), "final_url": res.get("final_url"),
        "generated_at": res.get("generated_at"), "pages_crawled": res.get("pages_crawled"),
        "score": res.get("score") or {}, "priority_counts": res.get("priority_counts") or {},
        "checks": [_shape_check(c) for c in res.get("checks") or []],
        "action_items": [dict(_shape_check(a), priority=a.get("priority"), tier=a.get("tier"),
                              tier_title=a.get("tier_title"), gain=a.get("gain"), difficulty=a.get("difficulty"),
                              category_label=a.get("category_label"))
                         for a in res.get("action_items") or []],
        "issue_states": states,
        "security": {
            "tls": sec.get("tls"), "ssl": sec.get("ssl"), "hsts": sec.get("hsts"), "waf": sec.get("waf"),
            "tech": sec.get("tech"), "email_trust": sec.get("email_trust"), "cve_findings": sec.get("cve_findings"),
            # Only the fact of exposure is surfaced — never file contents.
            "sensitive_files": [{"path": s.get("path"), "name": s.get("name"), "status": "Publicly accessible"}
                                for s in sec.get("sensitive_files") or []],
            "available": bool(sec),
        },
        "pages": pages,
        "psi": {"mobile": psi_summary("mobile"), "desktop": psi_summary("desktop")},
        "authority": {"domain_authority": authority.get("domain_authority"),
                      "page_authority": authority.get("page_authority"),
                      "is_estimated": False, "source": authority.get("source")},
        "traffic": {"monthly_visits": traffic.get("monthly_visits")} if traffic else None,
        "exec_summary": res.get("exec_summary"), "exec_summary_source": res.get("exec_summary_source"),
        "data_gaps": res.get("errors") or [],
    })


@bp.put("/api/audits/<audit_id>/issues/<check_id>")
@require()
def set_issue_state(audit_id, check_id):
    load_audit(g.user, audit_id)
    status = text(body(), "status", required=True, max_len=10)
    if status not in ("open", "resolved"):
        raise ApiError(422, "Status must be open or resolved.")
    if not re.match(r"^[A-Za-z0-9_.:-]{1,120}$", check_id):
        raise ApiError(422, "Invalid issue id.")
    with db.transaction() as tx:
        tx.execute("DELETE FROM audit_issue_states WHERE audit_id = %s AND check_id = %s", (audit_id, check_id))
        tx.execute("INSERT INTO audit_issue_states (audit_id, check_id, status, updated_by, updated_at) "
                   "VALUES (%s, %s, %s, %s, %s)", (audit_id, check_id, status, g.user["id"], now_iso()))
        events.audit(g.user, "ISSUE_STATUS_CHANGED", "audit", audit_id, {"check_id": check_id, "status": status}, tx=tx)
    return jsonify({"ok": True})


@bp.get("/api/audits/<audit_id>/client-data")
@bp.get("/api/audit/<audit_id>/client-data")
@require()
def client_data(audit_id):
    row = load_audit(g.user, audit_id)
    return jsonify(prepare_client_report_context(_load_result(row)))


@bp.get("/api/audits/<audit_id>/client-html")
@bp.get("/client-report/<audit_id>")
@require()
def client_html(audit_id):
    row = load_audit(g.user, audit_id)
    html = reports.render_client_html(audit_id, _load_result(row))
    return Response(html, mimetype="text/html", headers={
        "Content-Disposition": f'attachment; filename="SEO-Audit-{row["normalized_domain"]}.html"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
                                   "font-src https://fonts.gstatic.com; script-src 'unsafe-inline'; img-src data:"})


@bp.get("/api/audits/<audit_id>/export")
@require()
def export_json(audit_id):
    row = load_audit(g.user, audit_id)
    res = _load_result(row)
    events.audit(g.user, "AUDIT_EXPORTED", "audit", audit_id)
    return Response(json.dumps(res, indent=2, ensure_ascii=False, default=str), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="SEO-Audit-{row["normalized_domain"]}.json"'})


@bp.post("/api/audits/<audit_id>/reports")
@require()
def regenerate_reports(audit_id):
    rate_limit(f"regen:{g.user['id']}", 5, 600)
    row = load_audit(g.user, audit_id)
    res = _load_result(row)
    if reports.made_in_app():  # the apps build the files again the next time they are opened
        reports.forget_files(audit_id)
        events.audit(g.user, "REPORT_GENERATED", "audit", audit_id, {"formats": [], "made_in_app": True})
        return jsonify({"generated": [], "warnings": [], "made_in_app": True})
    made, errors = reports.generate_all(audit_id, res)
    if not made:
        raise ApiError(500, "Reports could not be generated. " + "; ".join(errors))
    events.audit(g.user, "REPORT_GENERATED", "audit", audit_id, {"formats": made})
    return jsonify({"generated": made, "warnings": errors})


def _legacy_download(audit_id, kind, fmt):
    load_audit(g.user, audit_id)
    rep = db.query_one("SELECT * FROM reports WHERE audit_id = %s AND kind = %s AND format = %s", (audit_id, kind, fmt))
    if not rep:
        if fmt == "pdf" and not reports.made_in_app() and not reports.pdf_supported():
            raise ApiError(409, "PDF rendering is not available on this server (Word or LibreOffice is required). "
                                "Download the DOCX report instead.", code="pdf_unavailable")
        raise not_found("Report")
    return _send_report(_ready_file(rep))


@bp.get("/api/audit/<audit_id>/client-docx")
@require()
def legacy_client_docx(audit_id):
    return _legacy_download(audit_id, "client", "docx")


@bp.get("/api/audit/<audit_id>/client-pdf")
@require()
def legacy_client_pdf(audit_id):
    return _legacy_download(audit_id, "client", "pdf")


@bp.get("/api/history")
@require()
def legacy_history():
    return list_audits()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@bp.get("/api/reports")
@require()
def list_reports():
    me = g.user
    page, size, offset = page_args(25)
    scope, params = lead_scope(me)
    where, params = [scope, "a.status = 'completed'"], list(params)
    if request.args.get("format") in ("docx", "pdf"):
        where.append("r.format = %s")
        params.append(request.args["format"])
    if request.args.get("kind") in ("client", "technical"):
        where.append("r.kind = %s")
        params.append(request.args["kind"])
    if request.args.get("audit_id"):
        where.append("r.audit_id = %s")
        params.append(request.args["audit_id"])
    if request.args.get("lead_id"):
        where.append("a.lead_id = %s")
        params.append(request.args["lead_id"])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE})")
        params += [term, term]
    clause = " AND ".join(where)
    base = """FROM reports r JOIN audits a ON a.id = r.audit_id JOIN leads l ON l.id = a.lead_id
              LEFT JOIN users u ON u.id = a.requested_by LEFT JOIN teams t ON t.id = a.team_id"""
    total = db.scalar(f"SELECT COUNT(*) {base} WHERE {clause}", params)
    rows = db.query(f"""SELECT r.id, r.audit_id, r.kind, r.format, r.size_bytes, r.ready, r.created_at, a.score, a.grade,
                               a.lead_id, l.company, l.normalized_domain, u.name AS agent_name, t.name AS team_name
                        {base} WHERE {clause} ORDER BY r.created_at DESC LIMIT %s OFFSET %s""", params + [size, offset])
    return jsonify(paged(rows, total, page, size) | {"pdf_supported": reports.pdf_supported(),
                                                    "made_in_app": reports.made_in_app()})


def _download_name(rep):
    domain = db.scalar("SELECT l.normalized_domain FROM audits a JOIN leads l ON l.id = a.lead_id WHERE a.id = %s",
                       (rep["audit_id"],)) or "site"
    label = "SEO-Audit" if rep["kind"] == "client" else "SEO-Technical-Audit"
    return f"{label}-{domain}-{rep['created_at'][:10]}.{rep['format']}"


def _ready_file(rep):
    """The report row once its file is on disk, or a ``build_in_app`` answer
    telling the desktop app to build it on its own computer."""
    try:
        return reports.ensure_file(rep)
    except ApiError as e:
        if e.code == "build_in_app":
            e.extra["download_name"] = _download_name(rep)
        raise


def _send_report(rep):
    path = reports.report_path(rep)
    if not path:
        raise ApiError(410, "This report file is no longer available. Regenerate the reports from the audit.", code="gone")
    events.audit(g.user, "REPORT_DOWNLOADED", "report", rep["id"], {"audit_id": rep["audit_id"]})
    mime = "application/pdf" if rep["format"] == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return send_file(path, mimetype=mime, as_attachment=True, download_name=_download_name(rep))


@bp.get("/api/reports/<report_id>/download")
@require()
def download_report(report_id):
    rep = db.query_one("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not rep:
        raise not_found("Report")
    load_audit(g.user, rep["audit_id"])  # IDOR guard via the lead
    if not os.path.basename(rep["stored_name"]) == rep["stored_name"]:
        raise not_found("Report")
    return _send_report(_ready_file(rep))


@bp.put("/api/reports/<report_id>/file")
@require()
def upload_report_file(report_id):
    """A report file an app built, kept so the server can email and share it."""
    rep = db.query_one("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not rep:
        raise not_found("Report")
    audit = load_audit(g.user, rep["audit_id"])  # IDOR guard via the lead
    # Once a report is ready only the person who ran the audit (or a Manager)
    # may replace its file; anyone who can see it may supply a missing one.
    if rep["ready"] and g.user["role"] != MANAGER and audit.get("requested_by") != g.user["id"]:
        raise forbidden("Only the person who ran this audit or a Manager can replace its report file.")
    rate_limit(f"report-file:{g.user['id']}", 60, 600)
    rep = reports.store_file(rep, request.get_data(cache=False))
    return jsonify({"report": {"id": rep["id"], "ready": rep["ready"], "size_bytes": rep["size_bytes"]}})


@bp.post("/api/reports/<report_id>/opened")
@require()
def report_opened(report_id):
    """Audit trail for a report file an app built on its own computer."""
    rep = db.query_one("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not rep:
        raise not_found("Report")
    load_audit(g.user, rep["audit_id"])
    events.audit(g.user, "REPORT_DOWNLOADED", "report", rep["id"], {"audit_id": rep["audit_id"], "made_in_app": True})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Audits run by the desktop apps (AUDITS_RUN_ON = "app")
# ---------------------------------------------------------------------------
MAX_RESULT_BYTES = 60 * 1024 * 1024   # an unpacked engine result is 1–2 MB


def _apps_only():
    if not audit_runner.runs_in_app():
        raise ApiError(409, "Audits run on the server here.", code="server_runs_audits")


def _device():
    device = (request.headers.get("X-Device-Id") or "").strip()
    if not re.match(r"^[A-Za-z0-9_-]{8,64}$", device):
        raise ApiError(422, "This app did not identify itself. Update the app.")
    return device


def _gunzip(raw):
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(raw, MAX_RESULT_BYTES)
    except zlib.error:
        raise ApiError(422, "The audit result could not be read.")
    if d.unconsumed_tail:
        raise ApiError(413, "The audit result is too large.")
    if not d.eof:
        raise ApiError(422, "The audit result could not be read.")
    return out


@bp.post("/api/audit-jobs/claim")
@require()
def claim_audit_jobs():
    _apps_only()
    return jsonify(audit_runner.claim_jobs(g.user, _device(), restart=bool(body().get("restart"))))


@bp.post("/api/audit-jobs/<audit_id>/progress")
@require()
def audit_job_progress(audit_id):
    _apps_only()
    messages = body().get("messages") or []
    if not isinstance(messages, list):
        raise ApiError(422, "Messages must be a list.")
    return jsonify(audit_runner.job_progress(g.user, audit_id, _device(), messages))


@bp.post("/api/audit-jobs/<audit_id>/result")
@require()
def audit_job_result(audit_id):
    """The finished engine result (gzip-compressed JSON) from the app that ran it,
    either as the whole body or as the ``result`` part of a form that also holds
    the Word reports the app wrote (``client_docx``, ``technical_docx``) and the
    client PDF (``client_pdf``)."""
    _apps_only()
    docx_files = {}
    if request.files:
        part = request.files.get("result")
        raw = part.read() if part else b""
        for kind in ("client", "technical"):
            f = request.files.get(f"{kind}_docx")
            if f:
                docx_files[kind] = f.read(reports.MAX_FILE_BYTES + 1)
        f = request.files.get("client_pdf")  # the finished client PDF (HTML report printed on that computer)
        if f:
            docx_files["client_pdf"] = f.read(reports.MAX_FILE_BYTES + 1)
    else:
        raw = request.get_data(cache=False)
    try:
        result = json.loads(_gunzip(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(422, "The audit result could not be read.")
    if not isinstance(result, dict) or not isinstance(result.get("score"), dict):
        raise ApiError(422, "The audit result is incomplete.")
    rows = audit_runner.job_result(g.user, audit_id, _device(), result, docx_files)
    return jsonify({"ok": True, "reports": rows})


@bp.post("/api/audit-jobs/<audit_id>/failed")
@require()
def audit_job_failed(audit_id):
    _apps_only()
    audit_runner.job_failed(g.user, audit_id, _device(), text(body(), "error", max_len=2000))
    return jsonify({"ok": True})


@bp.post("/api/pagespeed")
@require()
def pagespeed_for_app():
    """Google PageSpeed for an audit running in an app (the API key stays here)."""
    _apps_only()
    rate_limit(f"psi:{g.user['id']}", 120, 600)
    data = body()
    url = text(data, "url", required=True, max_len=2000, label="URL")
    if not re.match(r"^https?://", url, re.I):
        raise ApiError(422, "Only web addresses can be checked.")
    return jsonify(audit_runner.pagespeed_for_app(url, data.get("strategy")))


@bp.post("/api/places-search")
@require()
def places_search_for_app():
    """Google Places text search for an audit running in an app (Google Business
    Profile check). Uses the server's Google API key, which never leaves it."""
    _apps_only()
    rate_limit(f"places:{g.user['id']}", 120, 600)
    query = text(body(), "query", required=True, max_len=200, label="Search")
    key = audit_runner.pagespeed_key()
    if not key:
        return jsonify({"places": [], "error": "no_key"})
    import google_maps_scraper
    places, err = google_maps_scraper.places_search(query, key)
    return jsonify({"places": places, "error": err})


@bp.get("/api/audits/<audit_id>/result-data")
@require()
def result_data(audit_id):
    """The stored result exactly as kept (gzip-compressed JSON), for an app to
    build report files from. The server only reads it out."""
    row = load_audit(g.user, audit_id)
    _load_result(row)  # completed, and copies a legacy result into the store
    stored = db.query_one("SELECT data FROM audit_results WHERE audit_id = %s", (audit_id,))
    if not stored:
        raise ApiError(410, "The stored result for this audit is no longer available.", code="gone")
    return Response(bytes(stored["data"]), mimetype="application/gzip")
