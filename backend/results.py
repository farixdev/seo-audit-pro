# -*- coding: utf-8 -*-
"""
Storage for full audit results (the engine output, ~1–2 MB of JSON each).

Results are stored gzip-compressed in ``audit_results`` (~80 KB each), so a
bank of thousands of audits stays small. Audits saved by earlier versions live
in the legacy ``history_manager`` store; they are read from there once and
copied into ``audit_results`` on first use.
"""

import gzip
import json
import threading
from collections import OrderedDict

from backend import db
from backend.util import now_iso

_CACHE = OrderedDict()   # audit_id -> compressed bytes; every read parses a fresh copy
_CACHE_MAX = 32
_LOCK = threading.Lock()


def _remember(audit_id, packed):
    with _LOCK:
        _CACHE[audit_id] = packed
        _CACHE.move_to_end(audit_id)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def save(audit_id, result):
    """Store a result. Returns (raw_bytes, stored_bytes)."""
    raw = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
    packed = gzip.compress(raw, compresslevel=6)
    with db.transaction() as tx:
        tx.execute("DELETE FROM audit_results WHERE audit_id = %s", (audit_id,))
        tx.execute("INSERT INTO audit_results (audit_id, encoding, data, raw_size, stored_size, created_at) "
                   "VALUES (%s, 'json+gzip', %s, %s, %s, %s)", (audit_id, packed, len(raw), len(packed), now_iso()))
    _remember(audit_id, packed)
    return len(raw), len(packed)


def load(audit_id):
    """The stored result for an audit (a new dict each call), or None."""
    with _LOCK:
        packed = _CACHE.get(audit_id)
    if packed is None:
        row = db.query_one("SELECT data FROM audit_results WHERE audit_id = %s", (audit_id,))
        if row:
            packed = bytes(row["data"])
            _remember(audit_id, packed)
    if packed is not None:
        res = json.loads(gzip.decompress(packed).decode("utf-8"))
    else:
        res = _load_legacy(audit_id)
        if res is None:
            return None
    if isinstance(res, dict):
        res["id"] = audit_id
    return res


def _load_legacy(audit_id):
    import history_manager
    res = history_manager.get_audit(audit_id)
    if res:
        try:
            save(audit_id, res)
        except Exception:  # noqa: BLE001 - the legacy copy still serves reads
            pass
    return res
