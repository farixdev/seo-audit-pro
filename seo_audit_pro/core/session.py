# -*- coding: utf-8 -*-
"""
Client session state.

* The access token lives only in memory.
* The refresh token is kept in Windows Credential Manager (via ``keyring``)
  only when the user chooses "Keep me signed in"; it is never written to
  disk in plaintext.
* Role and team shown in the UI come from the server's /api/auth/me — they
  are display hints only; every permission is enforced by the server.
"""

import json
import logging
import threading

from PySide6.QtCore import QObject, Signal

from seo_audit_pro.core import settings

log = logging.getLogger("seo_audit_pro.client")
_KEYRING_SERVICE = "SEO Audit Pro" + (f" ({settings.PROFILE})" if settings.PROFILE else "")


class Session(QObject):
    user_changed = Signal()
    expired = Signal(str)            # reason shown on the sign-in screen
    password_change_required = Signal()
    connection_changed = Signal(str)  # "online" | "connecting" | "offline"

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self.user = None
        self.access_token = None
        self.refresh_token = None
        self.remember = False
        self.connection = "connecting"
        self._ended = False

    # -- identity ---------------------------------------------------------
    @property
    def role(self):
        return (self.user or {}).get("role")

    def is_manager(self):
        return self.role == "MANAGER"

    def is_team_lead(self):
        return self.role == "TEAM_LEAD"

    def is_agent(self):
        return self.role == "AGENT"

    @property
    def user_id(self):
        return (self.user or {}).get("id")

    def authenticated(self):
        return bool(self.access_token and self.user)

    # -- tokens -----------------------------------------------------------
    def start(self, payload, remember):
        with self._lock:
            self._ended = False
            self.access_token = payload["access_token"]
            self.refresh_token = payload["refresh_token"]
            self.user = payload["user"]
            self.remember = remember
        self._persist()
        self.user_changed.emit()

    def update_tokens(self, payload):
        with self._lock:
            self.access_token = payload["access_token"]
            self.refresh_token = payload["refresh_token"]
            if payload.get("user"):
                self.user = payload["user"]
        self._persist()

    def set_user(self, user):
        self.user = user
        self.user_changed.emit()

    def end(self, reason=""):
        """Forget all credentials. Emits ``expired`` once if a reason is given."""
        with self._lock:
            was_active = not self._ended and bool(self.access_token)
            self._ended = True
            self.access_token = None
            self.refresh_token = None
            self.user = None
        self.forget_stored()
        if reason and was_active:
            self.expired.emit(reason)

    def set_connection(self, state):
        if state != self.connection:
            self.connection = state
            self.connection_changed.emit(state)

    # -- secure storage -----------------------------------------------------
    def _key(self):
        if settings.mode() == settings.LOCAL:  # the local port can change between runs
            return "this-computer"
        return settings.api_url()

    def _persist(self):
        if not self.remember:
            return
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, self._key(), json.dumps(
                {"refresh_token": self.refresh_token, "email": (self.user or {}).get("email")}))
        except Exception as e:  # noqa: BLE001
            log.warning("Could not store credentials securely: %s", type(e).__name__)

    def stored_refresh(self):
        try:
            import keyring
            raw = keyring.get_password(_KEYRING_SERVICE, self._key())
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def forget_stored(self):
        try:
            import keyring
            keyring.delete_password(_KEYRING_SERVICE, self._key())
        except Exception:  # noqa: BLE001 - nothing stored
            pass


session = Session()
