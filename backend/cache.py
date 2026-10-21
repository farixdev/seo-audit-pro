# -*- coding: utf-8 -*-
"""
Small in-process cache for expensive lead-bank aggregates (niche/area facets,
filter summaries, page counts on large banks).

Entries are keyed by a global data version: every lead mutation calls
``bump()``, so cached values are never served after the data they describe
has changed. A TTL bounds memory and covers changes made outside this process.
"""

import threading
import time

_LOCK = threading.Lock()
_STORE = {}
_VERSION = 0
_MAX_ENTRIES = 2000


def bump():
    """Invalidate everything (call after any change to leads)."""
    global _VERSION
    with _LOCK:
        _VERSION += 1
        _STORE.clear()


def get_or_set(key, compute, ttl=60):
    with _LOCK:
        version = _VERSION
        hit = _STORE.get(key)
        if hit and hit[0] == version and hit[1] > time.monotonic():
            return hit[2]
    value = compute()
    with _LOCK:
        if version == _VERSION:  # data did not change while computing
            if len(_STORE) >= _MAX_ENTRIES:
                _STORE.clear()
            _STORE[key] = (version, time.monotonic() + ttl, value)
    return value
