# -*- coding: utf-8 -*-
"""
Chat permission rules (enforced server-side for every chat operation).

  Manager   -> everyone
  Team Lead -> managers, own team
  Agent     -> managers, own team lead, own team
Team channels: members of the team plus managers.
"""

from backend import db, realtime
from backend.auth import MANAGER


def can_message(sender, target):
    if not target or not target.get("is_active") or sender["id"] == target["id"]:
        return False
    if sender["role"] == MANAGER or target["role"] == MANAGER:
        return True
    return bool(sender.get("team_id")) and sender["team_id"] == target.get("team_id")


def contacts_for(user):
    if user["role"] == MANAGER:
        rows = db.query("""SELECT u.id, u.name, u.email, u.role, u.team_id, u.last_seen_at, u.avatar_upload_id, t.name AS team_name
                           FROM users u LEFT JOIN teams t ON t.id = u.team_id
                           WHERE u.is_active = 1 AND u.id <> %s ORDER BY u.name""", (user["id"],))
    else:
        rows = db.query("""SELECT u.id, u.name, u.email, u.role, u.team_id, u.last_seen_at, u.avatar_upload_id, t.name AS team_name
                           FROM users u LEFT JOIN teams t ON t.id = u.team_id
                           WHERE u.is_active = 1 AND u.id <> %s AND (u.role = 'MANAGER' OR u.team_id = %s)
                           ORDER BY u.name""", (user["id"], user["team_id"]))
    online = realtime.online_user_ids()
    for r in rows:
        r["online"] = r["id"] in online
        r["has_avatar"] = bool(r.pop("avatar_upload_id", None))
    return rows


def can_access_conversation(user, conv):
    if conv["type"] == "team":
        return user["role"] == MANAGER or user.get("team_id") == conv["team_id"]
    return user["id"] in (conv["user_a"], conv["user_b"])


def member_ids(conv):
    if conv["type"] == "direct":
        return [conv["user_a"], conv["user_b"]]
    rows = db.query("SELECT id FROM users WHERE is_active = 1 AND (team_id = %s OR role = 'MANAGER')", (conv["team_id"],))
    return [r["id"] for r in rows]


def presence_audience(user):
    try:
        return [c["id"] for c in contacts_for(user)]
    except Exception:  # noqa: BLE001
        return []


def relay_typing(user, conversation_id):
    if not conversation_id:
        return
    conv = db.query_one("SELECT * FROM conversations WHERE id = %s", (conversation_id,))
    if not conv or not can_access_conversation(user, conv):
        return
    targets = [u for u in member_ids(conv) if u != user["id"]]
    realtime.publish(targets, {"type": "typing", "conversation_id": conversation_id,
                               "user_id": user["id"], "name": user["name"]})
