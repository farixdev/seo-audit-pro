# -*- coding: utf-8 -*-
"""
Realtime connection (WebSocket) for chat, presence, typing, notifications and
audit progress. Reconnects automatically with backoff; consumers listen to
``hub.event`` and should fall back to a slow poll while ``hub.connected`` is
False.
"""

import json
import logging

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from seo_audit_pro.core import api, settings, tasks
from seo_audit_pro.core.session import session

log = logging.getLogger("seo_audit_pro.client")
_BACKOFF = [1, 2, 4, 8, 15, 30]


class RealtimeHub(QObject):
    event = Signal(dict)
    state_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.connected = False
        self._ws = None
        self._attempt = 0
        self._wanted = False
        self._ping = QTimer(self)
        self._ping.setInterval(25000)
        self._ping.timeout.connect(self._send_ping)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._open)

    def start(self):
        self._wanted = True
        self._attempt = 0
        self._open()

    def stop(self):
        self._wanted = False
        self._retry.stop()
        self._ping.stop()
        self._drop_socket()
        self._set_connected(False)

    def _drop_socket(self):
        if self._ws is None:
            return
        ws, self._ws = self._ws, None
        for sig in (ws.connected, ws.disconnected, ws.textMessageReceived):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        ws.abort()
        ws.deleteLater()

    def send(self, payload):
        if self._ws is not None and self.connected:
            self._ws.sendTextMessage(json.dumps(payload))

    # -- internals ----------------------------------------------------------
    def _url(self):
        base = settings.api_url()
        scheme = "wss" if base.startswith("https") else "ws"
        return QUrl(f"{scheme}://{base.split('://', 1)[1]}/api/ws")

    def _open(self):
        if not self._wanted or not session.access_token:
            return
        self._drop_socket()
        ws = QWebSocket()
        ws.connected.connect(self._on_connected)
        ws.disconnected.connect(self._on_disconnected)
        ws.textMessageReceived.connect(self._on_message)
        ws.errorOccurred.connect(lambda _e: None)
        self._ws = ws
        ws.open(self._url())

    def _on_connected(self):
        self._ws.sendTextMessage(json.dumps({"type": "auth", "token": session.access_token}))

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "ready":
            self._attempt = 0
            self._ping.start()
            self._set_connected(True)
            return
        if kind == "error" and msg.get("code") == "unauthorized":
            # Access token expired: refresh in the background, then reconnect.
            self._ping.stop()
            tasks.run(api.refresh_session, on_done=lambda ok: self._schedule(0.2 if ok else 5),
                      on_error=lambda _e: self._schedule(5), owner=self)
            return
        if kind == "pong":
            return
        self.event.emit(msg)

    def _on_disconnected(self):
        self._ping.stop()
        self._set_connected(False)
        if self._wanted:
            delay = _BACKOFF[min(self._attempt, len(_BACKOFF) - 1)]
            self._attempt += 1
            self._schedule(delay)

    def _schedule(self, seconds):
        if self._wanted:
            self._retry.start(int(seconds * 1000))

    def _send_ping(self):
        if self._ws is not None and self._ws.state() == QAbstractSocket.SocketState.ConnectedState:
            self._ws.sendTextMessage('{"type":"ping"}')

    def _set_connected(self, value):
        if value != self.connected:
            self.connected = value
            self.state_changed.emit(value)


hub = None


def get_hub():
    global hub
    if hub is None:
        hub = RealtimeHub()
    return hub
