# -*- coding: utf-8 -*-
"""
WebSocket hub for real-time events (chat, typing, presence, notifications,
audit progress).

Protocol (JSON text frames):
  client -> {"type": "auth", "token": "<access token>"}    (must be first, within 10 s)
  server -> {"type": "ready", "user_id": "..."}
  client -> {"type": "typing", "conversation_id": "..."}
  client -> {"type": "ping"}                                -> {"type": "pong"}
  server -> {"type": "<event>", ...}                        (pushed events)

Authorization for every pushed event is decided by the publisher, which only
targets users that are allowed to see it.
"""

import json
import logging
import queue
import threading

log = logging.getLogger("seo_audit_pro.realtime")

_LOCK = threading.RLock()
_CONNECTIONS = {}  # user_id -> {conn_id: _Conn}
_OUTBOX = 500      # queued frames per connection before it is considered stalled


class _Conn:
    """One client socket. Frames go through a small outbox drained by the
    connection's own thread, so a client that stops reading (a laptop that went
    to sleep) can never block an audit worker or a request that publishes."""

    def __init__(self, ws):
        self.ws = ws
        self.outbox = queue.Queue(maxsize=_OUTBOX)
        self.closed = threading.Event()
        threading.Thread(target=self._drain, name="ws-send", daemon=True).start()

    def send(self, payload):
        if self.closed.is_set():
            return
        try:
            self.outbox.put_nowait(payload)
        except queue.Full:
            log.warning("Realtime client is not reading; closing its connection")
            self.close()

    def _drain(self):
        while not self.closed.is_set():
            try:
                payload = self.outbox.get(timeout=30)
            except queue.Empty:
                continue
            if payload is None:
                break
            try:
                self.ws.send(payload)
            except Exception:  # noqa: BLE001 - dead socket; the reader loop unregisters it
                break
        self.closed.set()

    def close(self):
        self.closed.set()
        try:
            self.outbox.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _register(user_id, conn_id, conn):
    with _LOCK:
        first = not _CONNECTIONS.get(user_id)
        _CONNECTIONS.setdefault(user_id, {})[conn_id] = conn
    return first


def _unregister(user_id, conn_id):
    with _LOCK:
        conns = _CONNECTIONS.get(user_id) or {}
        conns.pop(conn_id, None)
        if not conns:
            _CONNECTIONS.pop(user_id, None)
            return True
    return False


def online_user_ids():
    with _LOCK:
        return set(_CONNECTIONS.keys())


def is_online(user_id):
    with _LOCK:
        return bool(_CONNECTIONS.get(user_id))


def publish(user_ids, event):
    """Send an event to every open connection of the given users."""
    payload = json.dumps(event, default=str)
    with _LOCK:
        targets = [c for uid in set(user_ids or []) for c in (_CONNECTIONS.get(uid) or {}).values()]
    for conn in targets:
        try:
            conn.send(payload)
        except Exception:  # noqa: BLE001 - dead socket; cleaned up by its reader loop
            pass


def disconnect_user(user_id):
    """Close all sockets of a user (logout everywhere / deactivation)."""
    with _LOCK:
        conns = list((_CONNECTIONS.get(user_id) or {}).values())
    for c in conns:
        c.close()


def register_routes(app):
    from flask_sock import Sock

    from backend import auth, chat_access

    sock = Sock(app)

    @sock.route("/api/ws")
    def ws_endpoint(ws):
        import secrets
        try:
            first = ws.receive(timeout=10)
            msg = json.loads(first or "{}")
        except Exception:  # noqa: BLE001
            return
        user = auth.user_from_token(msg.get("token") if msg.get("type") == "auth" else None)
        if not user:
            try:
                ws.send(json.dumps({"type": "error", "code": "unauthorized"}))
            except Exception:  # noqa: BLE001
                pass
            return
        conn_id = secrets.token_hex(8)
        conn = _Conn(ws)
        became_online = _register(user["id"], conn_id, conn)
        try:
            conn.send(json.dumps({"type": "ready", "user_id": user["id"]}))
            if became_online:
                publish(chat_access.presence_audience(user), {"type": "presence", "user_id": user["id"], "online": True})
            while True:
                raw = ws.receive(timeout=75)
                if raw is None:
                    # Idle timeout without even a ping: assume the client is gone.
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    if not auth.session_valid(user["sid"]):
                        break  # signed out / deactivated / password reset
                    conn.send('{"type":"pong"}')
                    auth.touch_last_seen(user["id"])
                elif kind == "typing":
                    chat_access.relay_typing(user, str(msg.get("conversation_id") or ""))
        except Exception:  # noqa: BLE001 - socket closed
            pass
        finally:
            conn.close()  # stops the connection's sender thread
            if _unregister(user["id"], conn_id):
                auth.touch_last_seen(user["id"])
                publish(chat_access.presence_audience(user), {"type": "presence", "user_id": user["id"], "online": False})
