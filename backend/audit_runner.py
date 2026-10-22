# -*- coding: utf-8 -*-
"""
Runs the existing SEO audit engine (audit.run_audit) for leads and records
real progress. Percent/step values come only from the engine's own stage
messages; nothing is simulated.

Queue
-----
Every audit is a row in ``audits``; ``status = 'queued'`` *is* the queue, so it
survives a restart. Single audits started from a lead (``priority = 1``) go
before bulk audits (``priority = 0``), oldest first.

Where audits run (``config.AUDITS_RUN_ON``):

* ``"app"`` (default): on the computer of the person who queued them. Their
  desktop app asks for its next audits (``claim_jobs``), runs the engine on that
  computer, sends the engine's progress messages and finally the result. The
  server only stores and shares: it turns the messages into percent / step,
  tells everyone watching, and saves the result. ``concurrency()`` audits run at
  a time on each computer.
* ``"server"``: one scheduler thread here starts queued audits while fewer than
  ``concurrency()`` are running; bulk audits take turns between the people who
  queued them (fewest running, then served longest ago), so one agent's batch
  never blocks a teammate.
"""

import ipaddress
import itertools
import json
import logging
import math
import os
import re
import socket
import threading
import time
from collections import Counter, deque
from datetime import datetime
from urllib.parse import urlparse

from backend import cache, config, db, events, realtime, reports, results, security
from backend.errors import ApiError, conflict
from backend.util import iso_ago, now_iso

log = logging.getLogger("seo_audit_pro.audits")

_LIVE = {}            # audit_id -> {"cancel": Event, "logs": deque} while running (kept 10 min after)
_RUNNING = {}         # audit_id -> requester id, for audits holding a worker slot
_SERVED = {}          # requester id -> turn number of the last audit started for them
_TURN = itertools.count(1)
_LIVE_LOCK = threading.Lock()
_WAKE = threading.Event()
_SCHED = {"thread": None}
_SCHED_LOCK = threading.Lock()
_AVG = {"at": 0.0, "seconds": 75}

DEFAULT_SETTINGS = {"max_pages": 50, "psi_strategy": "both", "concurrent": config.MAX_CONCURRENT_AUDITS}
MAX_CONCURRENT = 4
WAITING = "Waiting in the queue"


def audit_settings():
    row = db.query_one("SELECT value FROM app_settings WHERE key = 'audit_defaults'")
    data = dict(DEFAULT_SETTINGS)
    if row:
        try:
            data.update(json.loads(row["value"]))
        except ValueError:
            pass
    return data


def pagespeed_key():
    """Google PageSpeed Insights API key: the one saved in Settings, else the
    PAGESPEED_API_KEY environment variable. Without a key Google allows only a
    small shared quota, so PageSpeed data is often missing."""
    row = db.query_one("SELECT value FROM app_settings WHERE key = 'pagespeed_key'")
    if row and row["value"]:
        try:
            return security.decrypt_secret(row["value"])
        except Exception:  # noqa: BLE001 - key encrypted with another DATA_ENCRYPTION_KEY
            log.warning("The saved PageSpeed key could not be decrypted; save it again in Settings.")
    return (os.environ.get("PAGESPEED_API_KEY") or "").strip() or None


def concurrency():
    try:
        n = int(audit_settings().get("concurrent") or config.MAX_CONCURRENT_AUDITS)
    except (TypeError, ValueError):
        n = config.MAX_CONCURRENT_AUDITS
    return max(1, min(MAX_CONCURRENT, n))


def validate_public_target(url):
    """SSRF guard: only audit public http(s) hosts."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(422, "The lead's website is not a valid http(s) URL.")
    if parsed.port not in (None, 80, 443, 8080, 8443):
        raise ApiError(422, "Only standard web ports can be audited.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ApiError(422, f"The domain {parsed.hostname} could not be resolved. Check the website address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ApiError(422, "This address points to a private or internal network and cannot be audited.")


def audience(lead, requested_by):
    ids = {requested_by, lead.get("assigned_agent_id"), events.team_lead_of(lead.get("assigned_team_id"))}
    ids.update(events.managers())
    return [i for i in ids if i]


def _params(max_pages=None):
    settings = audit_settings()
    pages = max(1, min(int(max_pages or settings["max_pages"]), 500))
    return json.dumps({"max_pages": pages, "psi_strategy": settings.get("psi_strategy", "both")})


_INSERT_COLS = ("id", "lead_id", "requested_by", "team_id", "url", "status", "percent", "step", "created_at",
                "priority", "params", "batch_id")


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------
def start(user, lead, max_pages=None):
    """Queue a single audit for a lead (ahead of bulk audits)."""
    url = lead["website"]
    validate_public_target(url)
    audit_id = security.new_id("aud_")
    try:
        with db.transaction() as tx:
            tx.execute(f"INSERT INTO audits ({', '.join(_INSERT_COLS)}) VALUES ({', '.join(['%s'] * len(_INSERT_COLS))})",
                       (audit_id, lead["id"], user["id"], lead["assigned_team_id"], url, "queued", 0, WAITING,
                        now_iso(), 1, _params(max_pages), None))
            events.audit(user, "AUDIT_STARTED", "audit", audit_id, {"lead_id": lead["id"], "url": url}, tx=tx)
    except db.IntegrityError:
        running = db.query_one("SELECT id FROM audits WHERE lead_id = %s AND status IN ('queued', 'running')", (lead["id"],))
        raise conflict("An audit is already running for this lead.", audit_id=running["id"] if running else None)
    _queued(user["id"])
    return audit_id


def enqueue_bulk(user, leads, label, skipped):
    """Queue one audit per lead as a batch. ``leads`` were already checked for
    scope, archive state and running audits. Returns the batch id."""
    batch_id = security.new_id("abt_")
    now = now_iso()
    params = _params()
    team_id = user["team_id"] if user["role"] != "MANAGER" else None
    rows = [(security.new_id("aud_"), l["id"], user["id"], l["assigned_team_id"], l["website"], "queued", 0, WAITING,
             now, 0, params, batch_id) for l in leads]
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO audit_batches (id, label, created_by, team_id, status, total, skipped, created_at) "
                       "VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)",
                       (batch_id, (label or "")[:200] or None, user["id"], team_id, len(rows), json.dumps(skipped), now))
            tx.bulk_insert("audits", _INSERT_COLS, rows)
            events.audit(user, "BULK_AUDIT_STARTED", "audit_batch", batch_id,
                         {"count": len(rows), "label": label, "skipped": skipped}, tx=tx)
    except db.IntegrityError:
        raise conflict("Some of these leads started auditing a moment ago. Review the selection again.",
                       code_detail="in_progress")
    _queued(user["id"])
    return batch_id


def _queued(user_id):
    """New audits are waiting: start them here, or tell that person's app."""
    if runs_in_app():
        realtime.publish([user_id], {"type": "audit_jobs"})
    else:
        wake()


def _cancel_in_app(audit_ids):
    """App mode: cancel queued or running audits; the app running one stops it
    when it hears about it (live event, or the answer to its next progress)."""
    stopped = []
    for audit_id in audit_ids:
        with db.transaction() as tx:
            tx.execute("UPDATE audits SET status = 'cancelled', step = 'Cancelled', error = %s, completed_at = %s "
                       "WHERE id = %s AND status IN ('queued', 'running')",
                       ("The audit was cancelled.", now_iso(), audit_id))
            changed = tx.rowcount == 1
        if changed:
            ctx = _context(audit_id)
            realtime.publish(ctx["targets"], {"type": "audit_progress", "audit_id": audit_id,
                                              "lead_id": ctx["row"]["lead_id"], "status": "cancelled",
                                              "error": "The audit was cancelled."})
            stopped.append(audit_id)
    return stopped


def cancel(audit_id):
    """Cancel a queued audit, or ask a running one to stop."""
    if runs_in_app():
        done = bool(_cancel_in_app([audit_id]))
        if done:
            _after_finish(audit_id)
        return done
    with _LIVE_LOCK:
        live = _LIVE.get(audit_id) if audit_id in _RUNNING else None
    if live:
        live["cancel"].set()
    with db.transaction() as tx:
        tx.execute("UPDATE audits SET status = 'cancelled', step = 'Cancelled', completed_at = %s "
                   "WHERE id = %s AND status = 'queued'", (now_iso(), audit_id))
        was_queued = tx.rowcount == 1
    if was_queued:
        _after_finish(audit_id)
    return bool(live) or was_queued


def cancel_batch(batch_id):
    """Cancel every queued audit of a batch and stop its running ones.
    Returns how many queued audits were cancelled."""
    now = now_iso()
    if runs_in_app():
        _cancel_in_app([r["id"] for r in db.query("SELECT id FROM audits WHERE batch_id = %s AND status = 'running'",
                                                  (batch_id,))])
    with db.transaction() as tx:
        tx.execute("UPDATE audits SET status = 'cancelled', step = 'Cancelled', completed_at = %s "
                   "WHERE batch_id = %s AND status = 'queued'", (now, batch_id))
        dropped = tx.rowcount
        tx.execute("UPDATE audit_batches SET status = 'cancelled', completed_at = %s WHERE id = %s AND status = 'active'",
                   (now, batch_id))
    running = [r["id"] for r in db.query("SELECT id FROM audits WHERE batch_id = %s AND status = 'running'", (batch_id,))]
    with _LIVE_LOCK:
        for aid in running:
            if aid in _LIVE:
                _LIVE[aid]["cancel"].set()
    _publish_batch(batch_id)
    cache.bump()
    return dropped


def live_logs(audit_id, row=None):
    if row and row.get("log_tail"):  # an audit run by an app: kept in the database
        try:
            return json.loads(row["log_tail"])
        except ValueError:
            pass
    with _LIVE_LOCK:
        live = _LIVE.get(audit_id)
        return list(live["logs"]) if live else []


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def wake():
    if runs_in_app():  # no scheduler: the apps ask for their audits
        return
    _ensure_scheduler()
    _WAKE.set()


def _ensure_scheduler():
    with _SCHED_LOCK:
        t = _SCHED["thread"]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_loop, name="audit-scheduler", daemon=True)
        _SCHED["thread"] = t
        t.start()


def _loop():
    while True:
        _WAKE.wait(5)  # woken on enqueue/finish; the timeout also picks up settings changes
        _WAKE.clear()
        try:
            _dispatch()
        except Exception:  # noqa: BLE001 - the scheduler must keep running
            log.exception("Audit scheduler error")


def _dispatch():
    if runs_in_app():  # the apps run the audits; never take one here
        return
    limit = concurrency()
    while True:
        with _LIVE_LOCK:
            if len(_RUNNING) >= limit:
                return
            running_by = Counter(_RUNNING.values())
        nxt = _pick_next(running_by)
        if not nxt:
            return
        _claim(nxt)


def _pick_next(running_by):
    row = db.query_one("SELECT id, requested_by FROM audits WHERE status = 'queued' AND priority > 0 "
                       "ORDER BY priority DESC, created_at, id LIMIT 1")
    if row:
        return row
    people = db.query("SELECT requested_by, MIN(created_at) AS first_at FROM audits WHERE status = 'queued' "
                      "GROUP BY requested_by")
    if not people:
        return None
    best = min(people, key=lambda p: (running_by.get(p["requested_by"], 0), _SERVED.get(p["requested_by"], 0),
                                      p["first_at"]))
    return db.query_one("SELECT id, requested_by FROM audits WHERE status = 'queued' AND requested_by = %s "
                        "ORDER BY created_at, id LIMIT 1", (best["requested_by"],))


def _claim(row):
    with db.transaction() as tx:
        tx.execute("UPDATE audits SET status = 'running', started_at = %s, percent = 2, step = 'Starting audit', "
                   "attempts = attempts + 1 WHERE id = %s AND status = 'queued'", (now_iso(), row["id"]))
        if tx.rowcount != 1:
            return False
    with _LIVE_LOCK:
        _RUNNING[row["id"]] = row["requested_by"]
        _LIVE[row["id"]] = {"cancel": threading.Event(), "logs": deque(maxlen=300)}
        _SERVED[row["requested_by"]] = next(_TURN)
    threading.Thread(target=_worker, args=(row["id"],), name=f"audit-{row['id'][-6:]}", daemon=True).start()
    return True


def _worker(audit_id):
    try:
        _run_safe(audit_id)
    finally:
        with _LIVE_LOCK:
            _RUNNING.pop(audit_id, None)
        t = threading.Timer(600, _drop_live, args=(audit_id,))
        t.daemon = True
        t.start()
        try:
            _after_finish(audit_id)
        finally:
            _WAKE.set()


def _drop_live(audit_id):
    with _LIVE_LOCK:
        if audit_id not in _RUNNING:
            _LIVE.pop(audit_id, None)


# ---------------------------------------------------------------------------
# Queue information
# ---------------------------------------------------------------------------
def average_seconds():
    """Average duration of recent completed audits (cached for a minute)."""
    if time.monotonic() - _AVG["at"] < 60:
        return _AVG["seconds"]
    rows = db.query("SELECT started_at, completed_at FROM audits WHERE status = 'completed' AND started_at IS NOT NULL "
                    "ORDER BY completed_at DESC LIMIT 20")
    spans = []
    for r in rows:
        try:
            a = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(r["completed_at"].replace("Z", "+00:00"))
            spans.append(max(1.0, (b - a).total_seconds()))
        except (AttributeError, ValueError):
            continue
    _AVG.update(at=time.monotonic(), seconds=int(sum(spans) / len(spans)) if spans else 75)
    return _AVG["seconds"]


def eta_seconds(waiting_ahead, workers=None, running=0):
    """Rough time until ``waiting_ahead`` queued audits have been started and finished."""
    workers = workers or concurrency()
    avg = average_seconds()
    if not waiting_ahead and not running:
        return 0
    return int(math.ceil(waiting_ahead / workers) * avg + avg)


def snapshot():
    counts = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM audits WHERE status IN ('queued', 'running') GROUP BY status")}
    workers = concurrency()
    queued, running = counts.get("queued", 0), counts.get("running", 0)
    return {"workers": workers, "running": running, "queued": queued, "avg_seconds": average_seconds(),
            "eta_seconds": eta_seconds(queued, workers, running)}


def queue_position(row):
    """How many queued audits are ahead of this one (first come, first served
    within a priority; bulk turns between people make it an estimate). In app
    mode only the same person's audits count: they run on that person's computer."""
    mine = " AND requested_by = %s" if runs_in_app() else ""
    params = (row["id"], row["priority"], row["priority"], row["created_at"], row["created_at"], row["id"])
    return db.scalar(f"""SELECT COUNT(*) FROM audits WHERE status = 'queued' AND id <> %s AND (priority > %s OR
                         (priority = %s AND (created_at < %s OR (created_at = %s AND id < %s)))){mine}""",
                     params + ((row["requested_by"],) if mine else ())) or 0


def batch_counts(batch_ids):
    out = {b: {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0} for b in batch_ids}
    if not batch_ids:
        return out
    marks = ", ".join(["%s"] * len(batch_ids))
    for r in db.query(f"SELECT batch_id, status, COUNT(*) AS n FROM audits WHERE batch_id IN ({marks}) "
                      f"GROUP BY batch_id, status", list(batch_ids)):
        out[r["batch_id"]][r["status"]] = r["n"]
    return out


def _batch_audience(batch):
    ids = {batch["created_by"], events.team_lead_of(batch.get("team_id"))}
    ids.update(events.managers())
    return [i for i in ids if i]


def _publish_batch(batch_id):
    batch = db.query_one("SELECT * FROM audit_batches WHERE id = %s", (batch_id,))
    if not batch:
        return None, None
    counts = batch_counts([batch_id])[batch_id]
    realtime.publish(_batch_audience(batch), {"type": "audit_batch", "batch_id": batch_id, "status": batch["status"],
                                              "counts": counts, "total": batch["total"]})
    return batch, counts


def _after_finish(audit_id):
    row = db.query_one("SELECT batch_id FROM audits WHERE id = %s", (audit_id,))
    if not row or not row["batch_id"]:
        return
    batch, counts = _publish_batch(row["batch_id"])
    if not batch or batch["status"] != "active" or counts["queued"] or counts["running"]:
        return
    with db.transaction() as tx:
        tx.execute("UPDATE audit_batches SET status = 'completed', completed_at = %s WHERE id = %s AND status = 'active'",
                   (now_iso(), batch["id"]))
        closed = tx.rowcount == 1
    if closed:
        parts = [f"{counts['completed']:,} completed"]
        if counts["failed"]:
            parts.append(f"{counts['failed']:,} failed")
        if counts["cancelled"]:
            parts.append(f"{counts['cancelled']:,} cancelled")
        events.notify([batch["created_by"]], "audit_batch_completed", "Bulk audit finished",
                      f"{batch['label'] or 'Bulk audit'}: " + ", ".join(parts) + ".", "audit_batch", batch["id"])
        _publish_batch(batch["id"])


# ---------------------------------------------------------------------------
# Running one audit
# ---------------------------------------------------------------------------
_STAGES = [  # (substring in engine message, minimum percent)
    ("fetching homepage", 5), ("robots", 10), ("crawling the site", 12), ("analyzing pages", 55),
    ("internal links", 60), ("pagespeed", 66), ("structured data", 76), ("ssl", 80),
    ("security audit", 82), ("authority", 88), ("scoring", 92), ("competitor", 94),
    ("executive summary", 96), ("done", 97),
]
_CRAWL_RE = re.compile(r"crawled (\d+)/(\d+) pages(?:.*?(\d+) urls discovered)?", re.I)


def _run_safe(audit_id):
    try:
        _run(audit_id)
    except Exception:  # noqa: BLE001 - last-resort guard so no audit stays "running"
        log.exception("Audit worker crashed for %s", audit_id)
        db.execute("UPDATE audits SET status = 'failed', error = %s, step = 'Failed', completed_at = %s "
                   "WHERE id = %s AND status IN ('queued', 'running')",
                   ("The audit could not be completed because of an unexpected error.", now_iso(), audit_id))


def _new_state(row=None):
    row = row or {}
    return {"percent": row.get("percent") or 2, "step": row.get("step") or "Starting audit",
            "pages_processed": row.get("pages_processed") or 0, "pages_discovered": row.get("pages_discovered") or 0}


def _apply_message(state, msg):
    """Update percent / step / page counts from one engine progress message.
    Returns True when a new stage began."""
    low = msg.lower()
    stage_changed = False
    m = _CRAWL_RE.search(msg)
    if m:
        done, total = int(m.group(1)), int(m.group(2))
        state["pages_processed"] = done
        if m.group(3):
            state["pages_discovered"] = int(m.group(3))
        state["percent"] = max(state["percent"], min(54, 12 + int(done / max(1, total) * 42)))
    else:
        for needle, pct in _STAGES:
            if needle in low and pct > state["percent"]:
                state["percent"] = pct
                stage_changed = True
                break
    state["step"] = msg.rstrip("…. ")
    return stage_changed


def _context(audit_id):
    row = db.query_one("SELECT * FROM audits WHERE id = %s", (audit_id,))
    lead = db.query_one("SELECT * FROM leads WHERE id = %s", (row["lead_id"],))
    requester = db.query_one("SELECT * FROM users WHERE id = %s", (row["requested_by"],))
    return {"row": row, "lead": lead, "requester": requester, "targets": audience(lead, row["requested_by"])}


def _write_progress(ctx, state, logs=None):
    audit_id, lead_id = ctx["row"]["id"], ctx["row"]["lead_id"]
    if logs is not None:
        db.execute("UPDATE audits SET percent = %s, step = %s, pages_processed = %s, pages_discovered = %s, "
                   "heartbeat_at = %s, log_tail = %s WHERE id = %s AND status = 'running'",
                   (state["percent"], state["step"][:300], state["pages_processed"], state["pages_discovered"],
                    now_iso(), json.dumps(logs), audit_id))
    else:
        db.execute("UPDATE audits SET percent = %s, step = %s, pages_processed = %s, pages_discovered = %s, "
                   "heartbeat_at = %s WHERE id = %s AND status = 'running'",
                   (state["percent"], state["step"][:300], state["pages_processed"], state["pages_discovered"],
                    now_iso(), audit_id))
    realtime.publish(ctx["targets"], {"type": "audit_progress", "audit_id": audit_id, "lead_id": lead_id,
                                      "status": "running", "percent": state["percent"], "step": state["step"],
                                      "pages_processed": state["pages_processed"],
                                      "pages_discovered": state["pages_discovered"]})


def _complete(ctx, result, docx_files=None):
    """Store a finished engine result and update the audit, the lead and everyone watching."""
    row, lead, requester, targets = ctx["row"], ctx["lead"], ctx["requester"], ctx["targets"]
    audit_id, lead_id = row["id"], row["lead_id"]
    results.save(audit_id, result)
    if reports.made_in_app():
        made, report_errors = reports.register_for_app(audit_id, result, docx_files), []
    else:
        _write_progress(ctx, dict(_new_state(), percent=98, step="Generating reports"))
        made, report_errors = reports.generate_all(audit_id, result)
    sc = result.get("score") or {}
    pc = result.get("priority_counts") or {}
    now = now_iso()
    with db.transaction() as tx:
        tx.execute("""UPDATE audits SET status = 'completed', percent = 100, step = 'Completed', completed_at = %s,
                          score = %s, grade = %s, pages_processed = %s, issues_found = %s,
                          critical_count = %s, high_count = %s, medium_count = %s, low_count = %s
                      WHERE id = %s""",
                   (now, sc.get("score"), sc.get("grade"), result.get("pages_crawled") or 0,
                    len(result.get("action_items") or []), pc.get("Critical", 0), pc.get("High", 0),
                    pc.get("Medium", 0), pc.get("Low", 0), audit_id))
        tx.execute("""UPDATE leads SET last_audit_id = %s, last_audit_score = %s, last_audit_at = %s, updated_at = %s,
                          version = version + 1,
                          status = CASE WHEN status IN ('new', 'assigned') THEN 'audited' ELSE status END
                      WHERE id = %s""", (audit_id, sc.get("score"), now, now, lead_id))
        events.audit(requester, "AUDIT_COMPLETED", "audit", audit_id,
                     {"lead_id": lead_id, "score": sc.get("score"), "batch_id": row["batch_id"]}, tx=tx)
        if made:
            events.audit(requester, "REPORT_GENERATED", "audit", audit_id, {"formats": made}, tx=tx)
    cache.bump()  # after commit: lead status / score changed
    realtime.publish(targets, {"type": "audit_progress", "audit_id": audit_id, "lead_id": lead_id,
                               "status": "completed", "percent": 100, "step": "Completed"})
    if not row["batch_id"]:  # a batch sends one summary notification instead of one per lead
        events.notify(targets, "audit_completed", "Audit completed",
                      f"{lead['company']} scored {sc.get('score')}/100 ({sc.get('grade')}).", "audit", audit_id)
        if made:
            events.notify([row["requested_by"]], "report_ready", "Report ready",
                          f"Reports for {lead['company']} are ready to view and download.", "audit", audit_id)
    if report_errors:
        log.warning("Audit %s report issues: %s", audit_id, report_errors)


def _fail(ctx, message, cancelled=False):
    row, lead = ctx["row"], ctx["lead"]
    status = "cancelled" if cancelled else "failed"
    db.execute("UPDATE audits SET status = %s, error = %s, step = %s, completed_at = %s WHERE id = %s",
               (status, message, "Cancelled" if cancelled else "Failed", now_iso(), row["id"]))
    events.audit(ctx["requester"], "AUDIT_CANCELLED" if cancelled else "AUDIT_FAILED", "audit", row["id"],
                 {"lead_id": row["lead_id"], "error": message, "batch_id": row["batch_id"]})
    realtime.publish(ctx["targets"], {"type": "audit_progress", "audit_id": row["id"], "lead_id": row["lead_id"],
                                      "status": status, "error": message})
    if not cancelled and not row["batch_id"]:
        events.notify([row["requested_by"]], "audit_failed", "Audit failed", f"{lead['company']}: {message}",
                      "audit", row["id"])


def _run(audit_id):
    """Server mode: run one claimed audit in this process."""
    import audit as engine

    with _LIVE_LOCK:
        live = _LIVE[audit_id]
    ctx = _context(audit_id)
    row = ctx["row"]
    try:
        params = json.loads(row.get("params") or "{}")
    except ValueError:
        params = {}
    defaults = audit_settings()
    params = {"url": row["url"], "max_pages": params.get("max_pages") or defaults["max_pages"],
              "psi_strategy": params.get("psi_strategy") or defaults.get("psi_strategy", "both"),
              "psi_key": pagespeed_key()}
    state = dict(_new_state(), last_write=0.0)
    flush_lock = threading.Lock()

    def push(force=False):
        with flush_lock:
            now = time.monotonic()
            if not force and now - state["last_write"] < 1.0:
                # Throttled: make sure the latest values still land shortly.
                if not state.get("pending"):
                    state["pending"] = True
                    t = threading.Timer(1.0, push, kwargs={"force": True})
                    t.daemon = True
                    t.start()
                return
            state["pending"] = False
            state["last_write"] = now
        _write_progress(ctx, state)

    def progress(msg):
        msg = str(msg)
        live["logs"].append(f"{time.strftime('%H:%M:%S')}  {msg}")
        push(force=_apply_message(state, msg))

    try:
        push(force=True)
        validate_public_target(row["url"])  # again at run time: queued audits may wait a while
        result = engine.run_audit(dict(params, cancel=live["cancel"]), progress=progress)
        state.update(percent=97, step="Saving results")
        push(force=True)
        _complete(ctx, result)
    except Exception as e:  # noqa: BLE001
        cancelled = live["cancel"].is_set() or (isinstance(e, RuntimeError) and "cancel" in str(e).lower())
        if cancelled:
            message = "The audit was cancelled."
        elif isinstance(e, ApiError):
            message = e.message
        elif isinstance(e, ValueError):
            message = str(e)[:300]  # engine raises ValueError with user-facing text
        else:
            log.exception("Audit %s failed", audit_id)
            message = "The audit could not be completed because of an unexpected error."
        _fail(ctx, message, cancelled)


# ---------------------------------------------------------------------------
# App mode: each person's desktop app runs the audits they queued
# ---------------------------------------------------------------------------
APP_SILENT_SECONDS = 600   # an app that has not reported for this long has lost its audit


def runs_in_app():
    return config.AUDITS_RUN_ON == "app"


def _requeue_or_fail(audit_id, attempts, reason):
    if attempts >= 2:
        ctx = _context(audit_id)
        if ctx["row"]["status"] == "running":
            _fail(ctx, f"The audit was interrupted twice ({reason}). Start it again.")
            _after_finish(audit_id)
        return
    db.execute("UPDATE audits SET status = 'queued', percent = 0, pages_processed = 0, pages_discovered = 0, "
               "runner = NULL, step = %s WHERE id = %s AND status = 'running'",
               (f"{WAITING} (restarted: {reason})", audit_id))


def claim_jobs(user, device, restart=False):
    """Give this person's app the next audits they queued, up to the number
    that run at a time on one computer. ``restart``: the app has just started,
    so audits it was running before are put back in the queue first."""
    for r in db.query("SELECT id, attempts, runner, heartbeat_at FROM audits WHERE requested_by = %s AND status = 'running'",
                      (user["id"],)):
        if restart and r["runner"] == device:
            _requeue_or_fail(r["id"], r["attempts"], "the app was closed")
        elif not r["heartbeat_at"] or r["heartbeat_at"] < iso_ago(APP_SILENT_SECONDS):
            _requeue_or_fail(r["id"], r["attempts"], "the app stopped responding")
    slots = concurrency()
    running = db.scalar("SELECT COUNT(*) FROM audits WHERE requested_by = %s AND status = 'running' AND runner = %s",
                        (user["id"], device)) or 0
    jobs = []
    if running < slots:
        defaults = audit_settings()
        for row in db.query("SELECT * FROM audits WHERE requested_by = %s AND status = 'queued' "
                            "ORDER BY priority DESC, created_at, id LIMIT %s", (user["id"], slots - running)):
            now = now_iso()
            with db.transaction() as tx:
                tx.execute("UPDATE audits SET status = 'running', started_at = %s, heartbeat_at = %s, runner = %s, "
                           "percent = 2, step = 'Starting audit', attempts = attempts + 1 "
                           "WHERE id = %s AND status = 'queued'", (now, now, device, row["id"]))
                if tx.rowcount != 1:
                    continue
            try:
                params = json.loads(row.get("params") or "{}")
            except ValueError:
                params = {}
            jobs.append({"audit_id": row["id"], "url": row["url"],
                         "max_pages": params.get("max_pages") or defaults["max_pages"],
                         "psi_strategy": params.get("psi_strategy") or defaults.get("psi_strategy", "both")})
            ctx = _context(row["id"])
            with _LIVE_LOCK:
                _LIVE[row["id"]] = {"cancel": threading.Event(), "logs": deque(maxlen=300), "state": _new_state(),
                                    "ctx": ctx}
            _write_progress(ctx, _new_state())
    return {"jobs": jobs, "slots": slots}


def _app_job(user, audit_id, device):
    row = db.query_one("SELECT * FROM audits WHERE id = %s AND requested_by = %s", (audit_id, user["id"]))
    if not row:
        raise ApiError(404, "Audit not found.")
    return row, row["status"] == "running" and row["runner"] == device


def _app_live(row):
    with _LIVE_LOCK:
        live = _LIVE.get(row["id"])
        if live is None:  # the server restarted while the app kept running the audit
            live = _LIVE[row["id"]] = {"cancel": threading.Event(), "logs": deque(maxlen=300),
                                       "state": _new_state(row), "ctx": None}
    if live["ctx"] is None:
        live["ctx"] = _context(row["id"])
    return live


def job_progress(user, audit_id, device, messages):
    """Progress from the app running an audit. Returns whether it should stop."""
    row, mine = _app_job(user, audit_id, device)
    if not mine:
        return {"stop": True, "status": row["status"]}
    live = _app_live(row)
    # The database row is the truth: another server process may have taken the previous messages.
    state = _new_state(row)
    try:
        logs = json.loads(row.get("log_tail") or "[]")
    except ValueError:
        logs = []
    for msg in messages[:300]:
        msg = str(msg)[:500]
        logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        _apply_message(state, msg)
    live["state"] = state
    _write_progress(live["ctx"], state, logs[-150:] if messages else None)
    return {"stop": False}


def job_result(user, audit_id, device, result, docx_files=None):
    row, mine = _app_job(user, audit_id, device)
    if not mine:
        raise conflict("This audit is no longer running on this computer.", code="not_running")
    ctx = _context(audit_id)
    _write_progress(ctx, dict(_app_live(row)["state"], percent=97, step="Saving results"))
    _complete(ctx, result, docx_files)
    _job_done(audit_id)
    # The app uploads the report files it already wrote to these rows.
    return db.query("SELECT id, kind, format FROM reports WHERE audit_id = %s ORDER BY kind, format", (audit_id,))


def job_failed(user, audit_id, device, message):
    row, mine = _app_job(user, audit_id, device)
    if mine:
        _fail(_context(audit_id), (message or "").strip()[:300] or
              "The audit could not be completed because of an unexpected error.")
        _job_done(audit_id)


def _job_done(audit_id):
    t = threading.Timer(600, _drop_live, args=(audit_id,))
    t.daemon = True
    t.start()
    _after_finish(audit_id)


def pagespeed_for_app(url, strategy):
    """Google PageSpeed for an audit running in an app, with the key that stays
    on the server. The server only waits on Google; it does no processing."""
    import pagespeed
    return pagespeed.run_pagespeed(url, strategy if strategy in ("mobile", "desktop") else "mobile",
                                   api_key=pagespeed_key())


def recover_interrupted():
    """After a restart. Server mode: audits that were running go back to the
    queue (once) and the scheduler starts. App mode: nothing to recover, the
    audits keep running in the apps (an app that went silent loses its audit
    when that person's app next asks for work)."""
    now = now_iso()
    if not runs_in_app():
        db.execute("UPDATE audits SET status = 'failed', error = %s, step = 'Failed', completed_at = %s "
                   "WHERE status = 'running' AND attempts >= 2",
                   ("The audit was interrupted twice by a server restart. Start it again.", now))
        db.execute("UPDATE audits SET status = 'queued', percent = 0, pages_processed = 0, pages_discovered = 0, "
                   "step = %s WHERE status = 'running'", (WAITING + " (restarted after a server restart)",))
    for b in db.query("SELECT id FROM audit_batches WHERE status = 'active'"):
        c = batch_counts([b["id"]])[b["id"]]
        if not c["queued"] and not c["running"]:
            db.execute("UPDATE audit_batches SET status = 'completed', completed_at = %s WHERE id = %s", (now, b["id"]))
    if not runs_in_app():
        wake()
