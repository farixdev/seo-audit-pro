# -*- coding: utf-8 -*-
"""
Client response cache (stale-while-revalidate).

Revisiting a screen shows the last data instantly; a background refresh then
updates it if anything changed. Any successful write (POST/PUT/PATCH/DELETE)
and data-changing realtime events clear the cache, so stale data is never
presented as current after a change made in this app.
"""

import json
import threading
import time

_LOCK = threading.Lock()
_STORE = {}
_MAX = 300


def key(path, params=None):
    return path + "?" + json.dumps(params or {}, sort_keys=True, default=str)


def get(k):
    with _LOCK:
        hit = _STORE.get(k)
    if not hit:
        return None, None
    return hit[1], time.monotonic() - hit[0]


def put(k, data):
    with _LOCK:
        if len(_STORE) >= _MAX:
            oldest = sorted(_STORE.items(), key=lambda kv: kv[1][0])[: _MAX // 4]
            for old_key, _v in oldest:
                _STORE.pop(old_key, None)
        _STORE[k] = (time.monotonic(), data)


def clear(prefix=None):
    with _LOCK:
        if prefix is None:
            _STORE.clear()
        else:
            for k in [k for k in _STORE if k.startswith(prefix)]:
                _STORE.pop(k, None)
