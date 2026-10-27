# -*- coding: utf-8 -*-
"""
Central HTTP client. Every screen goes through this module (via background
tasks); UI code never calls ``requests`` directly.

Handles: base URL, bearer auth, transparent single-flight token refresh,
idempotency keys, timeouts, uniform errors and connection state.
"""

import logging
import os
import threading
import uuid

import requests

from seo_audit_pro import APP_VERSION
from seo_audit_pro.core import cache, settings
from seo_audit_pro.core.session import session

log = logging.getLogger("seo_audit_pro.client")


class ApiError(Exception):
    def __init__(self, status, message, code="error", data=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.data = data or {}

    @property
    def offline(self):
        return self.code in ("offline", "timeout")

    def field(self):
        return self.data.get("field")


_local = threading.local()
SERVER = {}   # what /api/health said about the server (e.g. realtime: false on shared hosting)


def live_updates():
    """False when the server cannot keep a live connection (shared hosting): screens refresh on a timer."""
    return SERVER.get("realtime", True) is not False
_refresh_lock = threading.Lock()


def _http():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = f"SEOAuditPro-Desktop/{APP_VERSION}"
        s.headers["X-Device-Id"] = settings.device_id()  # which computer runs an audit
        _local.s = s
    return s


def new_idempotency_key():
    return uuid.uuid4().hex


def _url(path):
    return settings.api_url() + path


def _raise_for(resp):
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    message = data.get("error") or {
        400: "The request was not valid.", 401: "Your session has expired. Please sign in again.",
        403: "You do not have permission to do that.", 404: "That item could not be found.",
        409: "This conflicts with the current state. Reload and try again.",
        413: "The file is too large.", 429: "Too many requests. Please wait a moment.",
    }.get(resp.status_code, "The server could not complete the request. Please try again.")
    raise ApiError(resp.status_code, message, data.get("code") or "error", data)


def refresh_session():
    """Exchange the refresh token for new tokens. Returns True on success."""
    token_before = session.access_token
    with _refresh_lock:
        if session.access_token and session.access_token != token_before:
            return True  # another thread already refreshed
        refresh = session.refresh_token
        if not refresh:
            return False
        try:
            resp = _http().post(_url("/api/auth/refresh"), json={"refresh_token": refresh}, timeout=15)
        except requests.RequestException:
            session.set_connection("offline")
            raise ApiError(0, "Can't reach the server. Check your connection.", "offline")
        if resp.status_code != 200:
            return False
        session.update_tokens(resp.json())
        session.set_connection("online")
        return True


def request(method, path, *, json=None, params=None, files=None, data=None, idempotency_key=None,
            timeout=20, auth=True, stream=False, _retried=False):
    headers = {}
    if auth and session.access_token:
        headers["Authorization"] = f"Bearer {session.access_token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        resp = _http().request(method, _url(path), json=json, params=params, files=files, data=data,
                               headers=headers, timeout=timeout, stream=stream)
    except requests.Timeout:
        session.set_connection("offline")
        raise ApiError(0, "The server took too long to respond. Please try again.", "timeout")
    except requests.RequestException:
        session.set_connection("offline")
        raise ApiError(0, "Can't reach the server. Check your connection and try again.", "offline")
    session.set_connection("online")

    if resp.status_code == 401 and auth and not _retried:
        if refresh_session():
            return request(method, path, json=json, params=params, files=files, data=data,
                           idempotency_key=idempotency_key, timeout=timeout, auth=auth, stream=stream, _retried=True)
        session.end("Your session has expired. Please sign in again.")
        raise ApiError(401, "Your session has expired. Please sign in again.", "unauthorized")
    if resp.status_code == 403:
        try:
            code = resp.json().get("code")
        except ValueError:
            code = None
        if code == "password_change_required":
            session.password_change_required.emit()
    if stream:
        if resp.status_code >= 400:
            _raise_for(resp)
        return resp
    if resp.status_code >= 400:
        _raise_for(resp)
    if method != "GET" and path != "/api/auth/refresh":
        cache.clear()  # any successful change may affect cached lists and counts
    if not resp.content:
        return {}
    ctype = resp.headers.get("Content-Type", "")
    if "application/json" in ctype:
        return resp.json()
    return resp.content


def get(path, **kw):
    return request("GET", path, **kw)


def get_cached(path, params=None, **kw):
    """GET that also stores the JSON response in the client cache."""
    data = request("GET", path, params=params, **kw)
    if isinstance(data, dict):
        cache.put(cache.key(path, params), data)
    return data


def post(path, json=None, **kw):
    return request("POST", path, json=json if json is not None else {}, **kw)


def put(path, json=None, **kw):
    return request("PUT", path, json=json if json is not None else {}, **kw)


def patch(path, json=None, **kw):
    return request("PATCH", path, json=json if json is not None else {}, **kw)


def delete(path, **kw):
    return request("DELETE", path, **kw)


def unique_target(name, dest_dir=None):
    """A path in ``dest_dir`` (default: the downloads folder) for ``name`` that
    does not overwrite an existing file."""
    name = os.path.basename(name or "download")
    name = "".join(ch for ch in name if ch not in '<>:"/\\|?*') or "download"
    dest_dir = dest_dir or settings.downloads_dir()
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name)
    base, ext = os.path.splitext(target)
    n = 1
    while os.path.exists(target):
        target = f"{base} ({n}){ext}"
        n += 1
    return target


def download(path, dest_dir=None, filename=None, params=None):
    """Stream a file to disk. Returns the saved path. A report the server asks
    this app to make (``build_in_app``) is built on this computer instead."""
    try:
        resp = request("GET", path, params=params, stream=True, timeout=300)  # a server may render a PDF first
    except ApiError as e:
        if e.code != "build_in_app":
            raise
        from seo_audit_pro.core import pc_work
        return pc_work.save_report(e.data, dest_dir, filename)
    name = filename
    if not name:
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            name = cd.split("filename=", 1)[1].strip().strip('"').split(";")[0]
    target = unique_target(name, dest_dir)
    tmp = target + ".part"
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(64 * 1024):
            if chunk:
                fh.write(chunk)
    os.replace(tmp, target)
    return target


def save_as(path, target, params=None):
    """Download ``path`` to exactly ``target`` (the file the person chose in a
    Save dialog, replacing it if they confirmed that). Returns ``target``."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="seoap_")
    try:
        local = download(path, tmp, os.path.basename(target), params=params)
        os.replace(local, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return target


def health(timeout=5):
    try:
        resp = _http().get(_url("/api/health"), timeout=timeout)
    except requests.RequestException:
        session.set_connection("offline")
        raise ApiError(0, "Can't reach the server.", "offline")
    if resp.status_code not in (200, 503):
        raise ApiError(resp.status_code, "The server responded unexpectedly.", "bad_server")
    try:
        data = resp.json()
    except ValueError:
        raise ApiError(resp.status_code, "This address does not look like an SEO Audit Pro server.", "bad_server")
    if data.get("app") not in ("SEO Audit Pro", "Rankly"):
        raise ApiError(resp.status_code, "This address does not look like an SEO Audit Pro server.", "bad_server")
    session.set_connection("online" if data.get("status") == "ok" else "offline")
    SERVER.update(data)
    return data
