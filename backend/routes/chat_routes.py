# -*- coding: utf-8 -*-
"""/api/chat/* — internal collaboration (direct messages and team channels).

Chat carries only what is already in the workspace: messages can share a lead
or an audit report (everyone in the conversation can open a shared report).
Files from outside cannot be uploaded to chat.
"""

import json

from flask import Blueprint, g, jsonify, request, send_file

from backend import chat_access, db, events, realtime, reports, security, uploads
from backend.auth import MANAGER, idempotent, load_audit, load_lead, require
from backend.errors import ApiError, conflict, not_found
from backend.util import body, now_iso, rate_limit, text

bp = Blueprint("chat", __name__)
REACTIONS = ("ack", "like", "seen", "question")
NO_FILES = "Files can't be uploaded to chat. Share a lead or an audit report instead."
MAX_SHARES = 5


def _shares(me, items):
    """Validate shared leads / reports (the sender must be able to open them)
    and keep a snapshot of what was shared. Returns [(kind, id, snapshot)]."""
    if not isinstance(items, list) or len(items) > MAX_SHARES:
        raise ApiError(422, f"Share at most {MAX_SHARES} items in one message.")
    out, missing = [], []
    for item in items:
        item = item if isinstance(item, dict) else {}
        kind, ref_id = item.get("kind"), str(item.get("id") or "")
        if any(k == kind and i == ref_id for k, i, _s in out):
            continue
        if kind == "lead":
            lead = load_lead(me, ref_id)
            out.append(("lead", ref_id, {"company": lead["company"], "domain": lead["normalized_domain"],
                                         "status": lead["status"], "score": lead["last_audit_score"],
                                         "niche": lead.get("niche"), "area": lead.get("area")}))
        elif kind == "report":
            rep = db.query_one("SELECT * FROM reports WHERE id = %s", (ref_id,))
            if not rep:
                raise not_found("Report")
            audit = load_audit(me, rep["audit_id"])
            try:
                reports.ensure_file(rep)  # the file must be on the server for the others to open it
            except ApiError as e:
                if e.code != "build_in_app":
                    raise
                missing.append({"id": rep["id"], "audit_id": rep["audit_id"], "kind": rep["kind"],
                                "format": rep["format"]})
            out.append(("report", ref_id, {"report_kind": rep["kind"], "format": rep["format"],
                                           "company": audit["company"], "domain": audit["normalized_domain"],
                                           "score": audit["score"], "grade": audit["grade"],
                                           "audit_id": rep["audit_id"], "lead_id": audit["lead_id"],
                                           "audited_at": audit["completed_at"]}))
        else:
            raise ApiError(422, "Only leads and audit reports can be shared.")
    if missing:
        raise ApiError(409, "The report has not been sent from your app yet. Try again.", code="report_not_uploaded",
                       reports=missing)
    return out


def _share_summary(shares):
    names = [s["company"] for _k, _i, s in shares]
    return "Shared " + ", ".join(names) if names else "Shared an item"


def _ensure_team_conversation(team_id):
    conv = db.query_one("SELECT * FROM conversations WHERE type = 'team' AND team_id = %s", (team_id,))
    if conv:
        return conv
    try:
        db.execute("INSERT INTO conversations (id, type, team_id, created_at) VALUES (%s, 'team', %s, %s)",
                   (security.new_id("cnv_"), team_id, now_iso()))
    except db.IntegrityError:
        pass  # created concurrently
    return db.query_one("SELECT * FROM conversations WHERE type = 'team' AND team_id = %s", (team_id,))


def _load_conversation(conv_id):
    conv = db.query_one("SELECT * FROM conversations WHERE id = %s", (conv_id,))
    if not conv or not chat_access.can_access_conversation(g.user, conv):
        raise not_found("Conversation")
    if conv["type"] == "direct":
        other_id = conv["user_b"] if conv["user_a"] == g.user["id"] else conv["user_a"]
        other = db.query_one("SELECT * FROM users WHERE id = %s", (other_id,))
        conv["_other"] = other
    return conv


def _unread(conv_id, user_id):
    return db.scalar("""SELECT COUNT(*) FROM messages m
                        WHERE m.conversation_id = %s AND m.sender_id <> %s AND m.deleted_at IS NULL
                          AND m.created_at > COALESCE((SELECT last_read_at FROM conversation_reads
                                                       WHERE conversation_id = %s AND user_id = %s), '')""",
                     (conv_id, user_id, conv_id, user_id)) or 0


def _conv_out(conv, me):
    last = db.query_one("""SELECT m.body, m.created_at, m.deleted_at, m.sender_id, u.name AS sender_name FROM messages m
                           LEFT JOIN users u ON u.id = m.sender_id WHERE m.conversation_id = %s
                           ORDER BY m.created_at DESC LIMIT 1""", (conv["id"],))
    out = {"id": conv["id"], "type": conv["type"], "last_message_at": conv["last_message_at"],
           "unread": _unread(conv["id"], me["id"]),
           "last_message": None if not last else {
               "preview": "Message deleted" if last["deleted_at"] else (last["body"] or "")[:120],
               "sender_id": last["sender_id"], "sender_name": last["sender_name"],
               "created_at": last["created_at"]}}
    if conv["type"] == "team":
        team = db.query_one("SELECT name FROM teams WHERE id = %s", (conv["team_id"],))
        out.update(title=team["name"] if team else "Team", team_id=conv["team_id"])
    else:
        other_id = conv["user_b"] if conv["user_a"] == me["id"] else conv["user_a"]
        other = db.query_one("SELECT id, name, role, is_active, avatar_upload_id FROM users WHERE id = %s", (other_id,))
        out.update(title=other["name"], other_user={"id": other["id"], "name": other["name"], "role": other["role"],
                                                    "online": realtime.is_online(other["id"]),
                                                    "is_active": bool(other["is_active"]),
                                                    "has_avatar": bool(other["avatar_upload_id"])})
    return out


@bp.get("/api/chat/contacts")
@require()
def contacts():
    return jsonify({"items": [dict(c, has_avatar=c.get("has_avatar", False)) for c in chat_access.contacts_for(g.user)]})


@bp.get("/api/chat/conversations")
@require()
def list_conversations():
    me = g.user
    convs = []
    team_ids = [r["id"] for r in db.query("SELECT id FROM teams ORDER BY name")] if me["role"] == MANAGER else \
        ([me["team_id"]] if me["team_id"] else [])
    for tid in team_ids:
        convs.append(_ensure_team_conversation(tid))
    convs += db.query("SELECT * FROM conversations WHERE type = 'direct' AND (user_a = %s OR user_b = %s)",
                      (me["id"], me["id"]))
    items = [_conv_out(c, me) for c in convs]
    teams =[c for c in items if c["type"] == "team"]
    directs = sorted([c for c in items if c["type"] == "direct"], key=lambda c: c["last_message_at"] or "", reverse=True)
    return jsonify({"items": teams + directs, "unread_total": sum(c["unread"] for c in items)})


@bp.post("/api/chat/conversations/direct")
@require()
def open_direct():
    me = g.user
    target_id = text(body(), "user_id", required=True, max_len=64, label="User")
    target = db.query_one("SELECT * FROM users WHERE id = %s", (target_id,))
    if not target or not chat_access.can_message(me, target):
        raise not_found("User")
    a, b = sorted([me["id"], target["id"]])
    conv = db.query_one("SELECT * FROM conversations WHERE type = 'direct' AND user_a = %s AND user_b = %s", (a, b))
    if not conv:
        try:
            db.execute("INSERT INTO conversations (id, type, user_a, user_b, created_at) VALUES (%s, 'direct', %s, %s, %s)",
                       (security.new_id("cnv_"), a, b, now_iso()))
        except db.IntegrityError:
            pass
        conv = db.query_one("SELECT * FROM conversations WHERE type = 'direct' AND user_a = %s AND user_b = %s", (a, b))
    return jsonify({"conversation": _conv_out(conv, me)})


def _messages_out(rows, me_id):
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join(["%s"] * len(ids))
    reactions = db.query(f"SELECT message_id, user_id, reaction FROM message_reactions WHERE message_id IN ({ph})", ids)
    atts = db.query(f"""SELECT ma.message_id, up.id, up.original_name, up.mime, up.size_bytes FROM message_attachments ma
                        JOIN uploads up ON up.id = ma.upload_id WHERE ma.message_id IN ({ph})""", ids)
    shares = db.query(f"SELECT message_id, kind, ref_id, snapshot FROM message_shares WHERE message_id IN ({ph}) "
                      f"ORDER BY kind, ref_id", ids)
    out = []
    for r in rows:
        deleted = bool(r["deleted_at"])
        rx = {}
        for x in reactions:
            if x["message_id"] == r["id"]:
                entry = rx.setdefault(x["reaction"], {"count": 0, "mine": False})
                entry["count"] += 1
                entry["mine"] = entry["mine"] or x["user_id"] == me_id
        out.append({
            "id": r["id"], "conversation_id": r["conversation_id"], "sender_id": r["sender_id"],
            "sender_name": r.get("sender_name"), "body": "" if deleted else r["body"], "deleted": deleted,
            "created_at": r["created_at"], "edited_at": r["edited_at"],
            "reply_to": None if not r.get("reply_to_id") else {
                "id": r["reply_to_id"], "sender_name": r.get("reply_sender_name"),
                "preview": "Message deleted" if r.get("reply_deleted_at") else (r.get("reply_body") or "")[:140]},
            "reactions": rx if not deleted else {},
            "attachments": [] if deleted else [{"id": a["id"], "name": a["original_name"], "mime": a["mime"],
                                                "size_bytes": a["size_bytes"]} for a in atts if a["message_id"] == r["id"]],
            "shares": [] if deleted else [dict(json.loads(x["snapshot"]), kind=x["kind"], id=x["ref_id"])
                                          for x in shares if x["message_id"] == r["id"]],
        })
    return out


_MSG_SELECT = """SELECT m.*, u.name AS sender_name, rm.body AS reply_body, rm.deleted_at AS reply_deleted_at,
                        ru.name AS reply_sender_name
                 FROM messages m LEFT JOIN users u ON u.id = m.sender_id
                 LEFT JOIN messages rm ON rm.id = m.reply_to_id LEFT JOIN users ru ON ru.id = rm.sender_id"""


def _mark_read(conv_id, user_id):
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("DELETE FROM conversation_reads WHERE conversation_id = %s AND user_id = %s", (conv_id, user_id))
        tx.execute("INSERT INTO conversation_reads (conversation_id, user_id, last_read_at) VALUES (%s, %s, %s)",
                   (conv_id, user_id, now))


@bp.get("/api/chat/conversations/<conv_id>/messages")
@require()
def list_messages(conv_id):
    _load_conversation(conv_id)
    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    before = request.args.get("before")
    params = [conv_id]
    sql = _MSG_SELECT + " WHERE m.conversation_id = %s"
    if before:
        sql += " AND m.created_at < %s"
        params.append(before)
    rows = db.query(sql + " ORDER BY m.created_at DESC, m.id DESC LIMIT %s", params + [limit])
    rows.reverse()
    if not before:
        _mark_read(conv_id, g.user["id"])
    return jsonify({"items": _messages_out(rows, g.user["id"]), "has_more": len(rows) == limit})


@bp.post("/api/chat/conversations/<conv_id>/read")
@require()
def mark_read(conv_id):
    _load_conversation(conv_id)
    _mark_read(conv_id, g.user["id"])
    return jsonify({"ok": True})


def _publish_message(conv, msg_id, event_type):
    row = db.query_one(_MSG_SELECT + " WHERE m.id = %s", (msg_id,))
    for uid in chat_access.member_ids(conv):
        realtime.publish([uid], {"type": event_type, "conversation_id": conv["id"],
                                 "message": _messages_out([row], uid)[0]})


@bp.post("/api/chat/conversations/<conv_id>/messages")
@require()
@idempotent
def send_message(conv_id):
    me = g.user
    rate_limit(f"chat:{me['id']}", 30, 10)
    conv = _load_conversation(conv_id)
    if conv["type"] == "direct" and not chat_access.can_message(me, conv["_other"]):
        raise ApiError(403, "You can no longer message this person.")
    data = body()
    text_body = text(data, "body", max_len=5000, label="Message")
    if data.get("upload_ids"):
        raise ApiError(422, NO_FILES)
    shares = _shares(me, data.get("shares") or [])
    if not text_body and not shares:
        raise ApiError(422, "Type a message or share a lead or report.")
    reply_to = data.get("reply_to_id") or None
    if reply_to and not db.query_one("SELECT 1 AS x FROM messages WHERE id = %s AND conversation_id = %s", (reply_to, conv_id)):
        raise ApiError(422, "The message you are replying to is not in this conversation.")
    mid = security.new_id("msg_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("""INSERT INTO messages (id, conversation_id, sender_id, body, reply_to_id, created_at)
                      VALUES (%s, %s, %s, %s, %s, %s)""", (mid, conv_id, me["id"], text_body, reply_to, now))
        for kind, ref_id, snapshot in shares:
            tx.execute("INSERT INTO message_shares (message_id, kind, ref_id, snapshot) VALUES (%s, %s, %s, %s)",
                       (mid, kind, ref_id, json.dumps(snapshot)))
        tx.execute("UPDATE conversations SET last_message_at = %s WHERE id = %s", (now, conv_id))
    _mark_read(conv_id, me["id"])
    _publish_message(conv, mid, "message")
    if conv["type"] == "direct":
        other = conv["_other"]
        if not realtime.is_online(other["id"]):
            events.notify([other["id"]], "new_message", f"New message from {me['name']}",
                          (text_body or _share_summary(shares))[:140], "conversation", conv_id)
    row = db.query_one(_MSG_SELECT + " WHERE m.id = %s", (mid,))
    return jsonify({"message": _messages_out([row], me["id"])[0]}), 201


def _own_message(msg_id, allow_manager=False):
    msg = db.query_one("SELECT * FROM messages WHERE id = %s", (msg_id,))
    if not msg:
        raise not_found("Message")
    conv = _load_conversation(msg["conversation_id"])
    if msg["sender_id"] != g.user["id"] and not (allow_manager and g.user["role"] == MANAGER):
        raise ApiError(403, "You can only change your own messages.")
    if msg["deleted_at"]:
        raise conflict("This message was deleted.")
    return msg, conv


@bp.patch("/api/chat/messages/<msg_id>")
@require()
def edit_message(msg_id):
    msg, conv = _own_message(msg_id)
    new_body = text(body(), "body", required=True, max_len=5000, label="Message")
    db.execute("UPDATE messages SET body = %s, edited_at = %s WHERE id = %s", (new_body, now_iso(), msg_id))
    _publish_message(conv, msg_id, "message_updated")
    return jsonify({"ok": True})


@bp.delete("/api/chat/messages/<msg_id>")
@require()
def delete_message(msg_id):
    msg, conv = _own_message(msg_id, allow_manager=True)
    db.execute("UPDATE messages SET deleted_at = %s WHERE id = %s", (now_iso(), msg_id))
    if msg["sender_id"] != g.user["id"]:
        events.audit(g.user, "CHAT_MESSAGE_REMOVED", "message", msg_id, {"conversation_id": conv["id"]})
    _publish_message(conv, msg_id, "message_updated")
    return jsonify({"ok": True})


@bp.post("/api/chat/messages/<msg_id>/reactions")
@require()
def toggle_reaction(msg_id):
    reaction = text(body(), "reaction", required=True, max_len=20)
    if reaction not in REACTIONS:
        raise ApiError(422, "Unknown reaction.")
    msg = db.query_one("SELECT * FROM messages WHERE id = %s", (msg_id,))
    if not msg:
        raise not_found("Message")
    conv = _load_conversation(msg["conversation_id"])
    with db.transaction() as tx:
        tx.execute("DELETE FROM message_reactions WHERE message_id = %s AND user_id = %s AND reaction = %s",
                   (msg_id, g.user["id"], reaction))
        if tx.rowcount == 0:
            tx.execute("INSERT INTO message_reactions (message_id, user_id, reaction, created_at) VALUES (%s, %s, %s, %s)",
                       (msg_id, g.user["id"], reaction, now_iso()))
    _publish_message(conv, msg_id, "message_updated")
    return jsonify({"ok": True})


@bp.post("/api/chat/uploads")
@require()
def chat_upload():
    raise ApiError(410, NO_FILES, code="chat_files_off")


@bp.get("/api/chat/messages/<msg_id>/reports/<report_id>")
@require()
def shared_report(msg_id, report_id):
    """A report shared in a conversation: everyone in it may open the file."""
    msg = db.query_one("SELECT * FROM messages WHERE id = %s AND deleted_at IS NULL", (msg_id,))
    if not msg or not db.query_one("SELECT 1 AS x FROM message_shares WHERE message_id = %s AND kind = 'report' "
                                   "AND ref_id = %s", (msg_id, report_id)):
        raise not_found("Report")
    _load_conversation(msg["conversation_id"])
    rep = db.query_one("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not rep:
        raise not_found("Report")
    try:
        rep = reports.ensure_file(rep)
    except ApiError as e:
        if e.code != "build_in_app":
            raise
        raise ApiError(410, "This shared report is no longer available. Ask for it to be shared again.", code="gone")
    from backend.routes.audit_routes import _download_name
    events.audit(g.user, "REPORT_DOWNLOADED", "report", rep["id"], {"audit_id": rep["audit_id"], "message_id": msg_id})
    mime = "application/pdf" if rep["format"] == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return send_file(reports.report_path(rep), mimetype=mime, as_attachment=True, download_name=_download_name(rep))


@bp.get("/api/chat/attachments/<upload_id>")
@require()
def chat_attachment(upload_id):
    rows = db.query("""SELECT m.conversation_id FROM message_attachments ma JOIN messages m ON m.id = ma.message_id
                       WHERE ma.upload_id = %s AND m.deleted_at IS NULL""", (upload_id,))
    allowed = False
    for r in rows:
        conv = db.query_one("SELECT * FROM conversations WHERE id = %s", (r["conversation_id"],))
        if conv and chat_access.can_access_conversation(g.user, conv):
            allowed = True
            break
    up = db.query_one("SELECT * FROM uploads WHERE id = %s AND purpose = 'chat'", (upload_id,))
    if not up or not (allowed or up["owner_id"] == g.user["id"]):
        raise not_found("File")
    return send_file(uploads.upload_path(up), mimetype=up["mime"], as_attachment=True, download_name=up["original_name"])


@bp.get("/api/chat/search")
@require()
def search_messages():
    me = g.user
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"items": []})
    term = f"%{db.like_escape(q)}%"
    if me["role"] == MANAGER:
        access = "(c.type = 'team' OR c.user_a = %s OR c.user_b = %s)"
        params = [me["id"], me["id"]]
    else:
        access = "((c.type = 'team' AND c.team_id = %s) OR c.user_a = %s OR c.user_b = %s)"
        params = [me["team_id"], me["id"], me["id"]]
    rows = db.query(f"""SELECT m.id, m.conversation_id, m.body, m.created_at, u.name AS sender_name, c.type, c.team_id
                        FROM messages m JOIN conversations c ON c.id = m.conversation_id
                        LEFT JOIN users u ON u.id = m.sender_id
                        WHERE {access} AND m.deleted_at IS NULL AND LOWER(m.body) {db.LIKE}
                        ORDER BY m.created_at DESC LIMIT 50""", params + [term])
    return jsonify({"items": rows})
