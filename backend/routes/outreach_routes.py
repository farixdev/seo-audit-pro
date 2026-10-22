# -*- coding: utf-8 -*-
"""/api/outreach/* — SMTP settings (Manager), drafts, sending, bulk outreach."""

import re

from flask import Blueprint, g, jsonify, request

from backend import config, db, events, mailer, reports, security, uploads
from backend.auth import AGENT, MANAGER, TEAM_LEAD, can_access_lead, idempotent, lead_scope, load_lead, require
from backend.errors import ApiError, conflict, not_found
from backend.util import EMAIL_RE, body, email_value, iso_ago, now_iso, page_args, paged, text

bp = Blueprint("outreach", __name__)

_PRIORITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


# ---------------------------------------------------------------------------
# SMTP settings — Manager only. The password is write-only.
# ---------------------------------------------------------------------------
def _smtp_public():
    row = db.query_one("SELECT provider, host, port, username, encryption, from_name, from_email, updated_at "
                       "FROM smtp_settings WHERE id = 'default'")
    if not row:
        return {"configured": False}
    return dict(row, configured=True, has_password=True)


@bp.get("/api/outreach/smtp")
@require(MANAGER)
def get_smtp():
    return jsonify(_smtp_public())


def _smtp_from_body(data, require_password):
    if (data.get("provider") or "smtp") == "brevo":  # Brevo's web API: only a key and the sender
        key = data.get("password") if isinstance(data.get("password"), str) else ""
        if require_password and not key.strip():
            raise ApiError(422, "Enter the Brevo API key.", field="password")
        return {"provider": "brevo", "host": "api.brevo.com", "port": 443, "encryption": "ssl", "username": "",
                "password": key.strip(), "from_email": email_value(data, "from_email"),
                "from_name": text(data, "from_name", required=True, max_len=120, label="From name")}
    host = text(data, "host", required=True, max_len=255, label="SMTP host")
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        raise ApiError(422, "Port must be a number.", field="port")
    if not 0 < port < 65536:
        raise ApiError(422, "Port must be between 1 and 65535.", field="port")
    encryption = text(data, "encryption", required=True, max_len=10).lower()
    if encryption not in ("starttls", "ssl", "none"):
        raise ApiError(422, "Encryption must be STARTTLS, SSL/TLS or None.", field="encryption")
    username = text(data, "username", max_len=255)
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if require_password and not password:
        raise ApiError(422, "Enter the SMTP password or app password.", field="password")
    from_email = email_value(data, "from_email")
    from_name = text(data, "from_name", required=True, max_len=120, label="From name")
    return {"provider": "smtp", "host": host, "port": port, "encryption": encryption, "username": username,
            "password": password, "from_email": from_email, "from_name": from_name}


@bp.put("/api/outreach/smtp")
@require(MANAGER)
def save_smtp():
    existing = db.query_one("SELECT password_enc, provider FROM smtp_settings WHERE id = 'default'")
    data = body()
    # Switching between SMTP and Brevo needs the new password / key.
    same_kind = bool(existing) and existing["provider"] == (data.get("provider") or "smtp")
    cfg = _smtp_from_body(data, require_password=not same_kind)
    enc = security.encrypt_secret(cfg["password"]) if cfg["password"] else existing["password_enc"]
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("DELETE FROM smtp_settings WHERE id = 'default'")
        tx.execute("""INSERT INTO smtp_settings (id, provider, host, port, username, password_enc, encryption, from_name,
                      from_email, updated_by, updated_at) VALUES ('default', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (cfg["provider"], cfg["host"], cfg["port"], cfg["username"], enc, cfg["encryption"],
                    cfg["from_name"], cfg["from_email"], g.user["id"], now))
        events.audit(g.user, "SMTP_CHANGED", "settings", "smtp",
                     {"provider": cfg["provider"], "host": cfg["host"], "port": cfg["port"], "username": cfg["username"],
                      "encryption": cfg["encryption"], "password_changed": bool(cfg["password"])}, tx=tx)
    mailer.wake()
    return jsonify(_smtp_public())


@bp.post("/api/outreach/smtp/test")
@require(MANAGER)
def test_smtp():
    data = body()
    if data.get("host") or data.get("provider") == "brevo":
        try:
            stored = mailer.smtp_config() if not data.get("password") else None
        except mailer.MailError:
            stored = None
        if stored and stored.get("provider") != (data.get("provider") or "smtp"):
            stored = None  # a key for the other kind is no use here
        cfg = _smtp_from_body(data, require_password=stored is None)
        if not cfg["password"]:
            cfg["password"] = stored["password"]
    else:
        try:
            cfg = mailer.smtp_config()
        except mailer.MailError as e:
            raise ApiError(422, str(e))
        if not cfg:
            raise ApiError(422, "SMTP is not configured yet.")
    ok, message = mailer.test_connection(cfg)
    events.audit(g.user, "SMTP_TESTED", "settings", "smtp", {"ok": ok})
    payload = {"ok": ok, "message": message}
    if not ok:
        payload.update(error=message, code="smtp_test_failed")
    return jsonify(payload), (200 if ok else 422)


@bp.get("/api/outreach/status")
@require()
def outreach_status():
    return jsonify({"smtp_configured": bool(db.query_one("SELECT 1 AS x FROM smtp_settings WHERE id = 'default'")),
                    "bulk_max_recipients": config.BULK_MAX_RECIPIENTS,
                    "send_interval_seconds": config.EMAIL_SEND_INTERVAL_SECONDS,
                    "daily_limit": config.DAILY_EMAIL_LIMIT})


# ---------------------------------------------------------------------------
# Draft generation — built ONLY from the stored audit result.
# ---------------------------------------------------------------------------
def _latest_completed_audit(lead_id, audit_id=None):
    if audit_id:
        row = db.query_one("SELECT * FROM audits WHERE id = %s AND lead_id = %s AND status = 'completed'",
                           (audit_id, lead_id))
    else:
        row = db.query_one("SELECT * FROM audits WHERE lead_id = %s AND status = 'completed' "
                           "ORDER BY completed_at DESC LIMIT 1", (lead_id,))
    return row


def build_draft(lead, audit_row, sender):
    from backend import results
    res = results.load(audit_row["id"]) if audit_row else None
    if not res:
        raise ApiError(409, "This lead has no completed audit to base an email on. Run an audit first.",
                       code="no_audit")
    sc = res.get("score") or {}
    items = sorted((res.get("action_items") or []), key=lambda a: _PRIORITY_RANK.get(a.get("priority"), 9))
    top = [a for a in items if a.get("priority") in ("Critical", "High")][:4] or items[:3]
    weak = [d for d in sc.get("dimensions") or [] if isinstance(d.get("score"), (int, float)) and d["score"] < 70]
    domain = lead["normalized_domain"]
    greeting = f"Hi {lead['contact_name'].split()[0]}," if lead.get("contact_name") else "Hello,"
    lines = [greeting, "",
             f"I recently ran a technical website audit of {domain} ({res.get('pages_crawled') or 1} pages checked "
             f"on {res.get('date') or audit_row['completed_at'][:10]}). The site scored {sc.get('score')}/100 "
             f"(grade {sc.get('grade')})."]
    if top:
        lines += ["", "The findings with the most impact were:"]
        for i, a in enumerate(top, 1):
            detail = (a.get("detail") or "").strip()
            lines.append(f"{i}. {a.get('label')}" + (f" — {detail}" if detail else ""))
    else:
        lines += ["", "The audit did not find any significant issues, which is a strong result."]
    if weak:
        lines += ["", "Areas scoring below 70/100: " + ", ".join(f"{d['label']} ({d['score']})" for d in weak) + "."]
    lines += ["", "I have the full report with the affected pages and the recommended fix for each item. "
                  "Would you be open to a short call to walk through it?", "", "Best regards,", sender["name"]]
    subject = f"Website audit findings for {domain}"
    return {"subject": subject, "body": "\n".join(lines), "audit_id": audit_row["id"],
            "to_email": lead.get("email") or ""}


@bp.post("/api/outreach/generate")
@require()
def generate():
    data = body()
    lead = load_lead(g.user, text(data, "lead_id", required=True, max_len=64, label="Lead"))
    audit_row = _latest_completed_audit(lead["id"], data.get("audit_id") or None)
    return jsonify(build_draft(lead, audit_row, g.user))


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------
_EMAIL_SELECT = """SELECT e.*, l.company, l.normalized_domain, u.name AS sender_name
                   FROM emails e JOIN leads l ON l.id = e.lead_id LEFT JOIN users u ON u.id = e.sender_id"""


def _email_scope(me):
    if me["role"] == MANAGER:
        return "1=1", []
    if me["role"] == TEAM_LEAD:
        return "(e.team_id = %s OR e.sender_id = %s)", [me["team_id"], me["id"]]
    return "e.sender_id = %s", [me["id"]]


def _email_out(row, with_body=False):
    keys = ["id", "lead_id", "audit_id", "company", "normalized_domain", "to_email", "subject", "status", "error",
            "sender_id", "sender_name", "batch_id", "created_at", "updated_at", "queued_at", "sent_at"]
    out = {k: row.get(k) for k in keys}
    if with_body:
        out["body"] = row["body"]
        out["attachments"] = db.query(
            """SELECT ea.id, ea.report_id, ea.upload_id, r.kind AS report_kind, r.format AS report_format,
                      up.original_name AS upload_name, up.size_bytes AS upload_size
               FROM email_attachments ea LEFT JOIN reports r ON r.id = ea.report_id
               LEFT JOIN uploads up ON up.id = ea.upload_id WHERE ea.email_id = %s""", (row["id"],))
    return out


def _load_email(email_id):
    me = g.user
    scope, params = _email_scope(me)
    row = db.query_one(f"{_EMAIL_SELECT} WHERE e.id = %s AND {scope}", [email_id] + params)
    if not row:
        raise not_found("Email")
    return row


@bp.get("/api/outreach/emails")
@require()
def list_emails():
    me = g.user
    page, size, offset = page_args(25)
    scope, params = _email_scope(me)
    where = [scope]
    status = request.args.get("status")
    if status in ("draft", "queued", "sending", "sent", "failed"):
        where.append("e.status = %s")
        params.append(status)
    for key in ("lead_id", "batch_id"):
        if request.args.get(key):
            where.append(f"e.{key} = %s")
            params.append(request.args[key])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(l.company) {db.LIKE} OR LOWER(e.to_email) {db.LIKE} OR LOWER(e.subject) {db.LIKE})")
        params += [term] * 3
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM emails e JOIN leads l ON l.id = e.lead_id WHERE {clause}", params)
    rows = db.query(f"{_EMAIL_SELECT} WHERE {clause} ORDER BY e.updated_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset])
    counts = {r["status"]: r["n"] for r in db.query(
        f"SELECT e.status, COUNT(*) AS n FROM emails e WHERE {scope} GROUP BY e.status", _email_scope(me)[1])}
    return jsonify(paged([_email_out(r) for r in rows], total, page, size) | {"counts": counts})


@bp.get("/api/outreach/emails/<email_id>")
@require()
def get_email(email_id):
    return jsonify({"email": _email_out(_load_email(email_id), with_body=True)})


def _attachments(tx, email_id, lead_id, data):
    report_ids = data.get("report_ids") or []
    upload_ids = data.get("upload_ids") or []
    if not isinstance(report_ids, list) or not isinstance(upload_ids, list) or len(report_ids) + len(upload_ids) > 5:
        raise ApiError(422, "Attach at most 5 files.")
    tx.execute("DELETE FROM email_attachments WHERE email_id = %s", (email_id,))
    for rid in dict.fromkeys(map(str, report_ids)):
        ok = tx.query_one("SELECT 1 AS x FROM reports r JOIN audits a ON a.id = r.audit_id WHERE r.id = %s AND a.lead_id = %s",
                          (rid, lead_id))
        if not ok:
            raise ApiError(422, "Only reports from this lead's audits can be attached.")
        tx.execute("INSERT INTO email_attachments (id, email_id, report_id) VALUES (%s, %s, %s)",
                   (security.new_id("eat_"), email_id, rid))
    for uid in dict.fromkeys(map(str, upload_ids)):
        ok = tx.query_one("SELECT 1 AS x FROM uploads WHERE id = %s AND owner_id = %s AND purpose = 'email'",
                          (uid, g.user["id"]))
        if not ok:
            raise ApiError(422, "One of the attached files is not available.")
        tx.execute("INSERT INTO email_attachments (id, email_id, upload_id) VALUES (%s, %s, %s)",
                   (security.new_id("eat_"), email_id, uid))


def _email_fields(data):
    to_email = email_value(data, "to_email")
    subject = text(data, "subject", required=True, max_len=200, label="Subject")
    if "\r" in subject or "\n" in subject:
        raise ApiError(422, "The subject must be a single line.", field="subject")
    body_text = text(data, "body", required=True, max_len=20000, label="Message")
    return to_email, subject, body_text


NO_SENDING = ("This app does not send email. Copy the email (or open it in your mail app), send it from your "
              "own mailbox, then mark the lead as outreached.")


def _no_sending():
    if not config.EMAIL_SENDING:
        raise ApiError(410, NO_SENDING, code="sending_off")


def _queue(tx, email_id):
    _no_sending()
    if reports.made_in_app():  # report files come from the sender's app; the server needs its copy to send them
        missing = tx.query(
            """SELECT r.id, r.audit_id, r.kind, r.format FROM email_attachments ea JOIN reports r ON r.id = ea.report_id
               WHERE ea.email_id = %s AND NOT EXISTS (SELECT 1 FROM report_data d WHERE d.report_id = r.id)""",
            (email_id,))
        if missing:
            raise ApiError(409, "The attached reports have not been sent from your app yet. Try again.",
                           code="report_not_uploaded", reports=missing)
    tx.execute("UPDATE emails SET status = 'queued', queued_at = %s, updated_at = %s, error = NULL "
               "WHERE id = %s AND status IN ('draft', 'failed')", (now_iso(), now_iso(), email_id))
    if tx.rowcount != 1:
        raise conflict("This email has already been sent or queued.")


def _require_smtp():
    if not db.query_one("SELECT 1 AS x FROM smtp_settings WHERE id = 'default'"):
        raise ApiError(409, "Email sending is not configured. Ask a Manager to set up SMTP in Settings.",
                       code="smtp_not_configured")


@bp.post("/api/outreach/emails")
@require()
@idempotent
def create_email():
    me = g.user
    data = body()
    lead = load_lead(me, text(data, "lead_id", required=True, max_len=64, label="Lead"))
    to_email, subject, body_text = _email_fields(data)
    send = bool(data.get("send"))
    if send:
        _no_sending()
        _require_smtp()
    audit_id = data.get("audit_id") or None
    if audit_id and not db.query_one("SELECT 1 AS x FROM audits WHERE id = %s AND lead_id = %s", (audit_id, lead["id"])):
        raise ApiError(422, "The referenced audit does not belong to this lead.")
    eid = security.new_id("eml_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("""INSERT INTO emails (id, lead_id, audit_id, sender_id, team_id, to_email, subject, body, status,
                      created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s)""",
                   (eid, lead["id"], audit_id, me["id"], lead["assigned_team_id"], to_email, subject, body_text, now, now))
        _attachments(tx, eid, lead["id"], data)
        if send:
            _queue(tx, eid)
            events.audit(me, "EMAIL_QUEUED", "email", eid, {"lead_id": lead["id"], "to": to_email}, tx=tx)
        else:
            events.audit(me, "EMAIL_DRAFT_SAVED", "email", eid, {"lead_id": lead["id"]}, tx=tx)
    if send:
        mailer.wake()
    return jsonify({"email": _email_out(_load_email(eid), with_body=True)}), 201


@bp.patch("/api/outreach/emails/<email_id>")
@require()
def update_email(email_id):
    row = _load_email(email_id)
    if row["status"] not in ("draft", "failed"):
        raise conflict("Only drafts and failed emails can be edited.")
    if row["sender_id"] != g.user["id"] and g.user["role"] != MANAGER:
        raise ApiError(403, "Only the author can edit this email.")
    data = body()
    to_email, subject, body_text = _email_fields(data)
    with db.transaction() as tx:
        tx.execute("UPDATE emails SET to_email = %s, subject = %s, body = %s, updated_at = %s WHERE id = %s AND status IN ('draft', 'failed')",
                   (to_email, subject, body_text, now_iso(), email_id))
        if tx.rowcount != 1:
            raise conflict("This email has already been sent or queued.")
        if "report_ids" in data or "upload_ids" in data:
            _attachments(tx, email_id, row["lead_id"], data)
    return jsonify({"email": _email_out(_load_email(email_id), with_body=True)})


@bp.delete("/api/outreach/emails/<email_id>")
@require()
def delete_draft(email_id):
    row = _load_email(email_id)
    if row["status"] != "draft":
        raise conflict("Only drafts can be deleted. Sent and failed emails are kept for the activity record.")
    if row["sender_id"] != g.user["id"] and g.user["role"] != MANAGER:
        raise ApiError(403, "Only the author can delete this draft.")
    with db.transaction() as tx:
        tx.execute("DELETE FROM email_attachments WHERE email_id = %s", (email_id,))
        tx.execute("DELETE FROM emails WHERE id = %s AND status = 'draft'", (email_id,))
        events.audit(g.user, "EMAIL_DRAFT_DELETED", "email", email_id, tx=tx)
    return jsonify({"ok": True})


@bp.post("/api/outreach/emails/<email_id>/send")
@require()
@idempotent
def send_email(email_id):
    row = _load_email(email_id)
    if row["sender_id"] != g.user["id"] and g.user["role"] != MANAGER:
        raise ApiError(403, "Only the author can send this email.")
    lead = load_lead(g.user, row["lead_id"])  # access may have changed since drafting
    _require_smtp()
    with db.transaction() as tx:
        _queue(tx, email_id)
        events.audit(g.user, "EMAIL_QUEUED", "email", email_id, {"lead_id": lead["id"], "to": row["to_email"]}, tx=tx)
    mailer.wake()
    return jsonify({"email": _email_out(_load_email(email_id), with_body=True)})


@bp.post("/api/outreach/uploads")
@require()
def upload_attachment():
    _no_sending()
    up = uploads.save_upload(request.files.get("file"), g.user["id"], "email")
    return jsonify({"upload": uploads.public_upload(up)}), 201


# ---------------------------------------------------------------------------
# Bulk outreach
# ---------------------------------------------------------------------------
def _bulk_candidates(me, lead_ids):
    eligible, skipped = [], []
    recent_cutoff = iso_ago(14 * 86400)
    for lid in dict.fromkeys(map(str, lead_ids)):
        lead = db.query_one("SELECT * FROM leads WHERE id = %s", (lid,))
        if not lead or not can_access_lead(me, lead):
            skipped.append({"lead_id": lid, "reason": "Not accessible"})
            continue
        if lead["status"] in ("archived", "won", "lost"):
            skipped.append({"lead_id": lid, "company": lead["company"], "reason": f"Lead is {lead['status']}"})
            continue
        if not lead.get("email") or not EMAIL_RE.match(lead["email"]):
            skipped.append({"lead_id": lid, "company": lead["company"], "reason": "No valid email address"})
            continue
        audit_row = _latest_completed_audit(lid)
        if not audit_row:
            skipped.append({"lead_id": lid, "company": lead["company"], "reason": "No completed audit"})
            continue
        if db.query_one("SELECT 1 AS x FROM emails WHERE lead_id = %s AND status IN ('queued', 'sending', 'sent') "
                        "AND COALESCE(sent_at, queued_at) > %s", (lid, recent_cutoff)):
            skipped.append({"lead_id": lid, "company": lead["company"], "reason": "Emailed in the last 14 days"})
            continue
        eligible.append((lead, audit_row))
    return eligible, skipped


@bp.post("/api/outreach/bulk/preview")
@require()
def bulk_preview():
    me = g.user
    ids = body().get("lead_ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError(422, "Select at least one lead.")
    if len(ids) > config.BULK_MAX_RECIPIENTS:
        raise ApiError(422, f"Bulk outreach is limited to {config.BULK_MAX_RECIPIENTS} recipients at a time.")
    eligible, skipped = _bulk_candidates(me, ids)
    sample = build_draft(eligible[0][0], eligible[0][1], me) if eligible else None
    if sample:
        sample["company"] = eligible[0][0]["company"]
    return jsonify({"recipient_count": len(eligible), "skipped": skipped, "sample": sample,
                    "recipients": [{"lead_id": l["id"], "company": l["company"], "email": l["email"]} for l, _ in eligible],
                    "send_interval_seconds": config.EMAIL_SEND_INTERVAL_SECONDS})


@bp.post("/api/outreach/bulk")
@require()
@idempotent
def bulk_send():
    _no_sending()
    me = g.user
    data = body()
    ids = data.get("lead_ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError(422, "Select at least one lead.")
    if len(ids) > config.BULK_MAX_RECIPIENTS:
        raise ApiError(422, f"Bulk outreach is limited to {config.BULK_MAX_RECIPIENTS} recipients at a time.")
    if data.get("confirm_count") != len(ids):
        raise ApiError(422, "Confirm the recipient count before sending.")
    _require_smtp()
    if me["role"] == AGENT:
        sent_today = db.scalar("SELECT COUNT(*) FROM emails WHERE sender_id = %s AND queued_at > %s",
                               (me["id"], iso_ago(86400))) or 0
        if sent_today + len(ids) > 150:
            raise ApiError(429, "Agents can queue at most 150 outreach emails per 24 hours.")
    eligible, skipped = _bulk_candidates(me, ids)
    if not eligible:
        raise ApiError(422, "None of the selected leads can be emailed.", skipped=skipped)
    batch_id = security.new_id("bat_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("INSERT INTO email_batches (id, created_by, total, created_at) VALUES (%s, %s, %s, %s)",
                   (batch_id, me["id"], len(eligible), now))
        for lead, audit_row in eligible:
            draft = build_draft(lead, audit_row, me)
            tx.execute("""INSERT INTO emails (id, lead_id, audit_id, sender_id, team_id, batch_id, to_email, subject, body,
                          status, created_at, updated_at, queued_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s)""",
                       (security.new_id("eml_"), lead["id"], audit_row["id"], me["id"], lead["assigned_team_id"],
                        batch_id, lead["email"], draft["subject"], draft["body"], now, now, now))
        events.audit(me, "BULK_EMAIL", "email_batch", batch_id, {"queued": len(eligible), "skipped": len(skipped)}, tx=tx)
    mailer.wake()
    return jsonify({"batch_id": batch_id, "queued": len(eligible), "skipped": skipped}), 202


# ---------------------------------------------------------------------------
# Manual outreach: people email or call from their own mailbox / phone and record it
# ---------------------------------------------------------------------------
_METHODS = ("email", "call")


def _outreach_out(r):
    keys = ("id", "lead_id", "user_id", "user_name", "outreached", "method", "contact", "note", "created_at",
            "company", "normalized_domain", "team_name")
    out = {k: r.get(k) for k in keys}
    out["outreached"] = bool(out["outreached"])
    return out


_OUTREACH_SELECT = """SELECT o.*, u.name AS user_name, l.company, l.normalized_domain, t.name AS team_name
                      FROM lead_outreach o JOIN leads l ON l.id = o.lead_id
                      LEFT JOIN users u ON u.id = o.user_id LEFT JOIN teams t ON t.id = o.team_id"""


@bp.post("/api/leads/<lead_id>/outreach")
@require()
@idempotent
def mark_outreach(lead_id):
    """Record that this lead was (or was not) contacted, by email or call, and with which address / number."""
    me = g.user
    lead = load_lead(me, lead_id)
    data = body()
    outreached = bool(data.get("outreached"))
    method = contact = None
    if outreached:
        method = text(data, "method", required=True, max_len=10, label="Method").lower()
        if method not in _METHODS:
            raise ApiError(422, "Choose Email or Call.", field="method")
        if method == "email":
            contact = email_value(data, "contact")
        else:
            contact = text(data, "contact", required=True, max_len=40, label="Phone number")
            if len(re.sub(r"\D", "", contact)) < 5:
                raise ApiError(422, "Enter the phone number you called.", field="contact")
    note = text(data, "note", max_len=1000, label="Note") or None
    now = now_iso()
    oid = security.new_id("out_")
    with db.transaction() as tx:
        tx.execute("""INSERT INTO lead_outreach (id, lead_id, user_id, team_id, outreached, method, contact, note, created_at)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (oid, lead["id"], me["id"], lead["assigned_team_id"], 1 if outreached else 0, method, contact, note, now))
        if outreached:
            tx.execute("""UPDATE leads SET last_contact_at = %s, outreach_method = %s, outreach_contact = %s,
                              updated_at = %s, version = version + 1,
                              status = CASE WHEN status IN ('new', 'assigned', 'audited') THEN 'contacted' ELSE status END
                          WHERE id = %s""", (now, method, contact, now, lead["id"]))
        else:
            tx.execute("""UPDATE leads SET last_contact_at = NULL, outreach_method = NULL, outreach_contact = NULL,
                              updated_at = %s, version = version + 1,
                              status = CASE WHEN status <> 'contacted' THEN status
                                            WHEN last_audit_id IS NOT NULL THEN 'audited'
                                            WHEN assigned_agent_id IS NOT NULL THEN 'assigned' ELSE 'new' END
                          WHERE id = %s""", (now, lead["id"]))
        events.audit(me, "LEAD_OUTREACH", "lead", lead["id"],
                     {"outreached": outreached, "method": method, "contact": contact}, tx=tx)
    from backend import cache
    cache.bump()
    from backend.routes.lead_routes import lead_out
    row = db.query_one("""SELECT l.*, t.name AS team_name, a.name AS agent_name FROM leads l
                          LEFT JOIN teams t ON t.id = l.assigned_team_id LEFT JOIN users a ON a.id = l.assigned_agent_id
                          WHERE l.id = %s""", (lead["id"],))
    entry = db.query_one(_OUTREACH_SELECT + " WHERE o.id = %s", (oid,))
    return jsonify({"lead": lead_out(row), "entry": _outreach_out(entry)}), 201


@bp.get("/api/leads/<lead_id>/outreach")
@require()
def lead_outreach_history(lead_id):
    load_lead(g.user, lead_id)
    rows = db.query(_OUTREACH_SELECT + " WHERE o.lead_id = %s ORDER BY o.created_at DESC LIMIT 100", (lead_id,))
    return jsonify({"items": [_outreach_out(r) for r in rows]})


@bp.get("/api/outreach/log")
@require()
def outreach_log():
    """Everyone's recorded outreach within the caller's leads (Managers: all; Team
    Leads: their team; Agents: their own leads)."""
    me = g.user
    page, size, offset = page_args(25)
    scope, params = lead_scope(me)
    where, params = [scope], list(params)
    if request.args.get("method") in _METHODS:
        where.append("o.method = %s")
        params.append(request.args["method"])
    if request.args.get("outreached") in ("yes", "no"):
        where.append("o.outreached = %s")
        params.append(1 if request.args["outreached"] == "yes" else 0)
    if request.args.get("user_id"):
        where.append("o.user_id = %s")
        params.append(request.args["user_id"])
    q = (request.args.get("search") or "").strip().lower()
    if q:
        term = f"%{db.like_escape(q)}%"
        where.append(f"(LOWER(l.company) {db.LIKE} OR l.normalized_domain {db.LIKE} OR LOWER(COALESCE(o.contact, '')) {db.LIKE})")
        params += [term, term, term]
    clause = " AND ".join(where)
    base = """FROM lead_outreach o JOIN leads l ON l.id = o.lead_id LEFT JOIN users u ON u.id = o.user_id
              LEFT JOIN teams t ON t.id = o.team_id"""
    total = db.scalar(f"SELECT COUNT(*) {base} WHERE {clause}", params)
    rows = db.query(f"""SELECT o.*, u.name AS user_name, l.company, l.normalized_domain, t.name AS team_name
                        {base} WHERE {clause} ORDER BY o.created_at DESC LIMIT %s OFFSET %s""", params + [size, offset])
    return jsonify(paged([_outreach_out(r) for r in rows], total, page, size))
