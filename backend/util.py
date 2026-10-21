# -*- coding: utf-8 -*-
"""Small shared helpers: time, request parsing, validation, pagination."""

import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import request

from backend.errors import ApiError, bad_request

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")


def _fmt(dt):
    # Millisecond precision keeps ordering stable for events in the same second;
    # fixed width keeps lexicographic order == chronological order.
    return dt.strftime(ISO_FMT)[:-4] + "Z"


def now_iso():
    return _fmt(datetime.now(timezone.utc))


def iso_in(seconds):
    return _fmt(datetime.now(timezone.utc) + timedelta(seconds=seconds))


def iso_ago(seconds):
    return _fmt(datetime.now(timezone.utc) - timedelta(seconds=seconds))


def body():
    """JSON object body or 400."""
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise bad_request("Request body must be a JSON object.")
    return data


def text(data, key, required=False, max_len=500, label=None):
    val = data.get(key)
    if val is None:
        val = ""
    if not isinstance(val, (str, int, float)):
        raise bad_request(f"{label or key} must be text.", field=key)
    val = str(val).strip()
    if required and not val:
        raise ApiError(422, f"{label or key.replace('_', ' ').capitalize()} is required.", field=key)
    if len(val) > max_len:
        raise ApiError(422, f"{label or key} is too long (max {max_len} characters).", field=key)
    return val


def email_value(data, key="email", required=True):
    val = text(data, key, required=required, max_len=254, label="Email").lower()
    if val and not EMAIL_RE.match(val):
        raise ApiError(422, "Enter a valid email address.", field=key)
    return val


def client_ip():
    return request.remote_addr or ""


def page_args(default_size=25, max_size=200):
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(max_size, max(1, int(request.args.get("page_size", default_size))))
    except ValueError:
        raise bad_request("Invalid pagination parameters.")
    return page, size, (page - 1) * size


def paged(items, total, page, size):
    return {"items": items, "total": int(total or 0), "page": page, "page_size": size,
            "pages": max(1, -(-int(total or 0) // size))}


def normalize_domain(value):
    """Canonical registrable host used for duplicate detection.

    http://example.com, https://www.example.com/, example.com/ -> example.com
    """
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:
        return ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", host):
        return ""
    return host


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 7 else ""


def normalize_company(value):
    v = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())
    v = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|company|the)\b", " ", v)
    return re.sub(r"\s+", " ", v).strip()


# ---------------------------------------------------------------------------
# In-process sliding-window rate limiter (per key). Sufficient for the single
# API process this application is deployed as.
# ---------------------------------------------------------------------------
_RATE = defaultdict(deque)
_RATE_LOCK = threading.Lock()


def rate_limit(key, limit, window_seconds):
    now = time.monotonic()
    with _RATE_LOCK:
        q = _RATE[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            raise ApiError(429, "Too many requests. Please slow down and try again shortly.")
        q.append(now)
