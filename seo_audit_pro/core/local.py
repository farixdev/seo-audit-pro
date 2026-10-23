# -*- coding: utf-8 -*-
"""Client helpers for "This computer" mode (see seo_audit_pro/local_server.py)."""

from seo_audit_pro import local_server
from seo_audit_pro.core import api, settings, tasks
from seo_audit_pro.core.api import ApiError


def sharing():
    return settings.get("share_network") not in (False, "false")


def start(on_ready, on_error, owner=None, share=None):
    """Start (or restart) the server on this computer in the background, then
    call ``on_ready({"needs_setup": bool, ...})`` or ``on_error(ApiError)``."""
    share = sharing() if share is None else share
    preferred = int(settings.get("local_port") or 0) or None

    def work():
        srv = local_server.instance()
        try:
            url = srv.start(share=share, preferred_port=preferred)
        except local_server.StartError as e:
            raise ApiError(-1, str(e), "server_start")
        settings.set_local_url(url)
        settings.put("local_port", srv.port)
        return api.get("/api/setup/status", auth=False)

    tasks.run(work, on_done=on_ready, on_error=on_error, owner=owner)


def share_address():
    srv = local_server.instance()
    return f"http://{local_server.lan_address()}:{srv.port}" if srv.port else ""
