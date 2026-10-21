# -*- coding: utf-8 -*-
"""
History and Progress Tracker for SEO Audit Pro.
Stores completed audits, computes SEO progress over time, manages targets,
and simulates score gains from remediating detected issues.
Powered by Neon PostgreSQL with thread-safe in-memory caching and local fallback.
"""

import copy
import json
import os
import re
import threading
import time
from datetime import datetime

import scoring
import config
config.load_env()

import sys

if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_DIR = EXE_DIR
REPORTS_DIR = os.path.join(EXE_DIR, "reports")
AUDITS_DIR = os.path.join(REPORTS_DIR, "audits")
HISTORY_FILE = os.path.join(REPORTS_DIR, "history.json")
TARGETS_FILE = os.path.join(REPORTS_DIR, "targets.json")

# Neon PostgreSQL Connection URL - Loaded strictly from environment variable
NEON_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _clean_db_url(raw_url):
    """Strip channel_binding or unsupported params that older libpq/Linux versions reject."""
    if not raw_url:
        return ""
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(raw_url)
        if parsed.query:
            qs = urllib.parse.parse_qs(parsed.query)
            qs.pop("channel_binding", None)
            new_query = urllib.parse.urlencode(qs, doseq=True)
            return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        pass
    cleaned = re.sub(r"[\?\&]channel_binding=[^&]+", "", raw_url)
    if "?" not in cleaned and "&" in cleaned:
        cleaned = cleaned.replace("&", "?", 1)
    return cleaned


# In-Memory Cache for ultra-fast web performance
# TTL in seconds
CACHE_TTL_HISTORY = 30
CACHE_TTL_TIMELINE = 30
CACHE_TTL_TARGETS = 60

_CACHE_LOCK = threading.RLock()
_CACHE = {
    "history": None,       # (timestamp, list_of_summaries)
    "timelines": {},       # domain -> (timestamp, list_of_summaries)
    "targets": {},         # domain -> (timestamp, dict)
    "audits": {},          # audit_id -> full_result_dict (immutable)
}


_AUDIT_CACHE_MAX = 12


def _trim_audit_cache():
    """Full results are large (MBs); keep only the most recently stored ones."""
    audits = _CACHE["audits"]
    while len(audits) > _AUDIT_CACHE_MAX:
        audits.pop(next(iter(audits)))


def _ensure_dirs():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(AUDITS_DIR, exist_ok=True)


def _get_db():
    """Returns a new psycopg2 connection to Neon PostgreSQL, or None if unavailable."""
    try:
        import psycopg2
        target_url = _clean_db_url(os.environ.get("DATABASE_URL") or NEON_DATABASE_URL)
        if not target_url or not target_url.strip():
            return None
        conn = psycopg2.connect(target_url, connect_timeout=6)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"[history_manager] DB connection error: {e}")
        return None


def _init_db():
    """Ensure database schema tables exist in Neon PostgreSQL."""
    conn = _get_db()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS seo_audits (
                    id VARCHAR(128) PRIMARY KEY,
                    domain VARCHAR(255) NOT NULL,
                    url TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    score INTEGER NOT NULL,
                    grade VARCHAR(10) NOT NULL,
                    summary JSONB NOT NULL,
                    result JSONB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_seo_audits_domain ON seo_audits (lower(domain));
                CREATE INDEX IF NOT EXISTS idx_seo_audits_created ON seo_audits (created_at DESC);

                CREATE TABLE IF NOT EXISTS seo_targets (
                    domain VARCHAR(255) PRIMARY KEY,
                    targets JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                """
            )
    except Exception as e:
        print(f"[history_manager] DB init schema error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Initialize DB tables on startup
try:
    _init_db()
except Exception:
    pass


# ---------------------------------------------------------------------------
# History Index & Audit Store
# ---------------------------------------------------------------------------

def load_history():
    """
    Returns the list of historical audit summaries, sorted newest first.
    Uses in-memory cache, falls back to Neon PostgreSQL, then local history.json.
    """
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get("history")
        if cached and (now - cached[0]) < CACHE_TTL_HISTORY:
            return copy.deepcopy(cached[1])

    # Try Neon PostgreSQL
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary FROM seo_audits ORDER BY created_at DESC LIMIT 200"
                )
                rows = cur.fetchall()
                if rows is not None:
                    summaries = [r[0] for r in rows if r and r[0]]
                    with _CACHE_LOCK:
                        _CACHE["history"] = (now, summaries)
                    return copy.deepcopy(summaries)
        except Exception as e:
            print(f"[history_manager] Error querying history from DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback to local file
    _ensure_dirs()
    if not os.path.isfile(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                with _CACHE_LOCK:
                    _CACHE["history"] = (now, data)
                return copy.deepcopy(data)
            return []
    except Exception:
        return []


def save_history(history_list):
    """Saves the audit summaries list to local disk as backup."""
    _ensure_dirs()
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_list, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def save_audit_result(audit_id, result):
    """
    Saves full audit result JSON and adds an entry to Neon PostgreSQL and local disk.
    Invalidates history caches to ensure instant consistency.
    """
    _ensure_dirs()
    if isinstance(result, dict):
        result["id"] = audit_id

    # Build summary record for index & timeline
    sc = result.get("score") or {}
    dims = {d.get("key"): d.get("score") for d in sc.get("dimensions", [])}
    psi = result.get("psi") or {}
    mobile_psi = (psi.get("mobile") or {}).get("scores", {})
    desktop_psi = (psi.get("desktop") or {}).get("scores", {})
    mobile_crux = ((psi.get("mobile") or {}).get("field") or {}).get("metrics") or {}

    # Extract CWV metrics if available
    lcp_val = None
    cls_val = None
    inp_val = None
    if mobile_crux:
        lcp_val = (mobile_crux.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile")
        cls_val = (mobile_crux.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")
        inp_val = (mobile_crux.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")

    # Count issues
    items = result.get("action_items") or []
    counts = result.get("priority_counts") or {}

    domain = result.get("domain", "")
    url = result.get("url") or result.get("final_url", "")
    date_str = result.get("date") or datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat()
    score_val = sc.get("score", 0)
    grade_val = sc.get("grade", "?")

    summary = {
        "id": audit_id,
        "url": url,
        "domain": domain,
        "timestamp": now_iso,
        "date": date_str,
        "time": datetime.now().strftime("%H:%M"),
        "score": score_val,
        "grade": grade_val,
        "label": sc.get("label", ""),
        "pages_crawled": result.get("pages_crawled", 1),
        "critical_issues": counts.get("Critical", 0),
        "high_issues": counts.get("High", 0),
        "medium_issues": counts.get("Medium", 0),
        "low_issues": counts.get("Low", 0),
        "total_issues": len(items),
        "scores": {
            "technical": dims.get("technical"),
            "onpage": dims.get("onpage"),
            "performance": dims.get("performance"),
            "content": dims.get("content"),
            "mobile": dims.get("mobile"),
            "accessibility": dims.get("accessibility"),
            "security": dims.get("security"),
            "ux": dims.get("ux"),
            "psi_mobile": mobile_psi.get("performance"),
            "psi_desktop": desktop_psi.get("performance"),
        },
        "cwv": {
            "lcp_ms": lcp_val,
            "cls": cls_val,
            "inp_ms": inp_val,
        },
    }

    # 1. Save to Neon PostgreSQL
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seo_audits (id, domain, url, created_at, score, grade, summary, result)
                    VALUES (%s, %s, %s, NOW(), %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        domain = EXCLUDED.domain,
                        url = EXCLUDED.url,
                        score = EXCLUDED.score,
                        grade = EXCLUDED.grade,
                        summary = EXCLUDED.summary,
                        result = EXCLUDED.result
                    """,
                    (
                        audit_id,
                        domain,
                        url,
                        score_val,
                        grade_val,
                        json.dumps(summary, ensure_ascii=False),
                        json.dumps(result, ensure_ascii=False),
                    ),
                )
        except Exception as e:
            print(f"[history_manager] Error inserting audit into Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 2. Local disk backup
    audit_file = os.path.join(AUDITS_DIR, f"{audit_id}.json")
    try:
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[history_manager] Error saving audit local file {audit_id}: {e}")

    # 3. Update local history index
    try:
        local_history = []
        if os.path.isfile(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                local_history = json.load(f)
        if not isinstance(local_history, list):
            local_history = []
        local_history = [h for h in local_history if h.get("id") != audit_id]
        local_history.insert(0, summary)
        local_history = local_history[:200]
        save_history(local_history)
    except Exception:
        pass

    # 4. Invalidate & update in-memory caches
    with _CACHE_LOCK:
        _CACHE["history"] = None
        _CACHE["timelines"].clear()
        _CACHE["audits"][audit_id] = copy.deepcopy(result)
        _trim_audit_cache()

    return summary


def get_audit(audit_id):
    """Loads full audit result JSON by id from cache, Neon DB, or local file."""
    if not audit_id:
        return None

    # 1. Check in-memory cache
    with _CACHE_LOCK:
        if audit_id in _CACHE["audits"]:
            res = copy.deepcopy(_CACHE["audits"][audit_id])
            if isinstance(res, dict):
                res["id"] = audit_id
            return res

    # 2. Query Neon DB
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT result FROM seo_audits WHERE id = %s",
                    (audit_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    res = row[0]
                    if isinstance(res, dict):
                        res["id"] = audit_id
                    with _CACHE_LOCK:
                        _CACHE["audits"][audit_id] = copy.deepcopy(res)
                        _trim_audit_cache()
                    return res
        except Exception as e:
            print(f"[history_manager] Error fetching audit {audit_id} from Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 3. Local disk fallback
    _ensure_dirs()
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", audit_id)
    path = os.path.join(AUDITS_DIR, f"{safe_id}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                res = json.load(f)
                if isinstance(res, dict):
                    res["id"] = audit_id
                with _CACHE_LOCK:
                    _CACHE["audits"][audit_id] = copy.deepcopy(res)
                    _trim_audit_cache()
                return res
        except Exception:
            return None
    return None


def delete_audit(audit_id):
    """Deletes audit from Neon DB, cache, and disk."""
    if not audit_id:
        return False

    # 1. Delete from Neon DB
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM seo_audits WHERE id = %s", (audit_id,))
        except Exception as e:
            print(f"[history_manager] Error deleting audit {audit_id} from Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 2. Invalidate cache
    with _CACHE_LOCK:
        _CACHE["history"] = None
        _CACHE["timelines"].clear()
        if audit_id in _CACHE["audits"]:
            del _CACHE["audits"][audit_id]

    # 3. Delete from disk
    _ensure_dirs()
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", audit_id)
    path = os.path.join(AUDITS_DIR, f"{safe_id}.json")
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass

    try:
        if os.path.isfile(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                data = [h for h in data if h.get("id") != audit_id]
                save_history(data)
    except Exception:
        pass

    return True


def get_domain_progress(domain):
    """
    Returns the historical progression timeline for a specific domain,
    sorted chronologically (oldest to newest).
    """
    if not domain:
        return []
    norm_dom = domain.lower().replace("www.", "").strip()

    # Check cache
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE["timelines"].get(norm_dom)
        if cached and (now - cached[0]) < CACHE_TTL_TIMELINE:
            return copy.deepcopy(cached[1])

    # Try Neon PostgreSQL
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT summary FROM seo_audits
                    WHERE lower(replace(domain, 'www.', '')) = %s
                    ORDER BY created_at ASC
                    """,
                    (norm_dom,),
                )
                rows = cur.fetchall()
                if rows:
                    timeline = [r[0] for r in rows if r and r[0]]
                    with _CACHE_LOCK:
                        _CACHE["timelines"][norm_dom] = (now, timeline)
                    return copy.deepcopy(timeline)
        except Exception as e:
            print(f"[history_manager] Error querying domain timeline from Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback to in-memory/file history scan
    history = load_history()
    matches = []
    for h in history:
        h_dom = (h.get("domain") or "").lower().replace("www.", "").strip()
        if h_dom == norm_dom:
            matches.append(h)
    matches.sort(key=lambda x: x.get("timestamp", x.get("date", "")))

    with _CACHE_LOCK:
        _CACHE["timelines"][norm_dom] = (now, matches)

    return copy.deepcopy(matches)


# ---------------------------------------------------------------------------
# Site Targets & Goals (Neon DB + Cache + Local Fallback)
# ---------------------------------------------------------------------------

DEFAULT_TARGETS = {
    "target_score": 90,
    "target_lcp": 2.5,
    "target_mobile_psi": 85,
    "target_critical": 0,
}


def get_targets(domain):
    """Get target goals for a specific domain."""
    if not domain:
        return copy.deepcopy(DEFAULT_TARGETS)
    norm = domain.lower().replace("www.", "").strip()

    # Check cache
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE["targets"].get(norm)
        if cached and (now - cached[0]) < CACHE_TTL_TARGETS:
            return copy.deepcopy(cached[1])

    # Try Neon DB
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT targets FROM seo_targets WHERE lower(replace(domain, 'www.', '')) = %s",
                    (norm,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    t_data = row[0]
                    with _CACHE_LOCK:
                        _CACHE["targets"][norm] = (now, t_data)
                    return copy.deepcopy(t_data)
        except Exception as e:
            print(f"[history_manager] Error querying targets from Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback to local file
    _ensure_dirs()
    if os.path.isfile(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and norm in data:
                    res = data[norm]
                    with _CACHE_LOCK:
                        _CACHE["targets"][norm] = (now, res)
                    return copy.deepcopy(res)
        except Exception:
            pass

    # Defaults
    return copy.deepcopy(DEFAULT_TARGETS)


def save_targets(domain, targets_dict):
    """Save target goals for a specific domain."""
    if not domain:
        return copy.deepcopy(DEFAULT_TARGETS)
    norm = domain.lower().replace("www.", "").strip()
    clean_target = {
        "target_score": int(targets_dict.get("target_score", 90)),
        "target_lcp": float(targets_dict.get("target_lcp", 2.5)),
        "target_mobile_psi": int(targets_dict.get("target_mobile_psi", 85)),
        "target_critical": int(targets_dict.get("target_critical", 0)),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
    }

    # 1. Save to Neon DB
    conn = _get_db()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seo_targets (domain, targets, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (domain) DO UPDATE SET
                        targets = EXCLUDED.targets,
                        updated_at = NOW()
                    """,
                    (norm, json.dumps(clean_target)),
                )
        except Exception as e:
            print(f"[history_manager] Error saving targets to Neon DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 2. Save to local targets file as backup
    _ensure_dirs()
    data = {}
    if os.path.isfile(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
        except Exception:
            data = {}
    data[norm] = clean_target
    try:
        with open(TARGETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

    # 3. Update cache
    now = time.time()
    with _CACHE_LOCK:
        _CACHE["targets"][norm] = (now, clean_target)

    return clean_target


# ---------------------------------------------------------------------------
# "What-If" Fix Simulator
# ---------------------------------------------------------------------------

def simulate_fixes(audit_id, fixed_check_ids):
    """
    Simulates what happens if specified check IDs are remediated (marked pass).
    Re-evaluates the scoring engine and returns the projected new score, grade,
    and points gained!
    """
    res = get_audit(audit_id)
    if not res:
        return {"error": "Audit not found"}

    orig_score_obj = res.get("score") or {}
    orig_score = orig_score_obj.get("score", 0)
    orig_grade = orig_score_obj.get("grade", "?")

    checks = copy.deepcopy(res.get("checks", []))
    fixed_set = set(fixed_check_ids or [])

    for c in checks:
        if c.get("id") in fixed_set:
            c["status"] = "pass"

    sim_score_obj = scoring.compute(checks) or {}
    sim_score = sim_score_obj.get("score", orig_score)
    sim_grade = sim_score_obj.get("grade", orig_grade)

    return {
        "original_score": orig_score,
        "original_grade": orig_grade,
        "projected_score": sim_score,
        "projected_grade": sim_grade,
        "score_gain": max(0, sim_score - orig_score),
        "dimensions": sim_score_obj.get("dimensions", []),
        "penalties": sim_score_obj.get("penalties", []),
    }


# ---------------------------------------------------------------------------
# Audit Comparison (Full Delta Engine)
# ---------------------------------------------------------------------------

def compare_audits(audit_id_1, audit_id_2):
    """
    Compares two audits (audit1: baseline/older, audit2: newer)
    and computes the exact progress/delta across all parameters.
    """
    a1 = get_audit(audit_id_1)
    a2 = get_audit(audit_id_2)
    if not a1 or not a2:
        return {"error": "One or both audits could not be loaded"}

    sc1 = a1.get("score") or {}
    sc2 = a2.get("score") or {}
    score1 = sc1.get("score", 0)
    score2 = sc2.get("score", 0)
    score_diff = score2 - score1

    # Dimensions comparison
    dims1 = {d["key"]: d["score"] for d in sc1.get("dimensions", [])}
    dims2 = {d["key"]: d["score"] for d in sc2.get("dimensions", [])}
    dim_diffs = {}
    for k in set(dims1.keys()).union(dims2.keys()):
        v1 = dims1.get(k)
        v2 = dims2.get(k)
        diff = (v2 - v1) if (v1 is not None and v2 is not None) else None
        dim_diffs[k] = {"before": v1, "after": v2, "delta": diff}

    checks1 = {c["id"]: c for c in a1.get("checks", [])}
    checks2 = {c["id"]: c for c in a2.get("checks", [])}

    resolved_issues = []
    new_issues = []
    persistent_issues = []

    for cid, c1 in checks1.items():
        if c1.get("status") in ("fail", "warn"):
            c2 = checks2.get(cid)
            if c2 and c2.get("status") == "pass":
                resolved_issues.append({
                    "id": cid,
                    "label": c1.get("label"),
                    "category": c1.get("category"),
                    "severity": c1.get("severity"),
                    "previous_status": c1.get("status"),
                    "current_status": "pass",
                })
            elif c2 and c2.get("status") in ("fail", "warn"):
                persistent_issues.append({
                    "id": cid,
                    "label": c1.get("label"),
                    "category": c1.get("category"),
                    "status": c2.get("status"),
                    "severity": c2.get("severity"),
                })

    for cid, c2 in checks2.items():
        if c2.get("status") in ("fail", "warn"):
            c1 = checks1.get(cid)
            if not c1 or c1.get("status") == "pass":
                new_issues.append({
                    "id": cid,
                    "label": c2.get("label"),
                    "category": c2.get("category"),
                    "current_status": c2.get("status"),
                    "severity": c2.get("severity"),
                    "fix": c2.get("fix"),
                })

    # PageSpeed / CWV comparison
    psi1 = a1.get("psi") or {}
    psi2 = a2.get("psi") or {}
    mob1 = (psi1.get("mobile") or {}).get("scores", {})
    mob2 = (psi2.get("mobile") or {}).get("scores", {})

    return {
        "domain": a2.get("domain", a1.get("domain", "")),
        "baseline": {
            "id": audit_id_1,
            "date": a1.get("date"),
            "score": score1,
            "grade": sc1.get("grade", "?"),
            "pages": a1.get("pages_crawled", 1),
        },
        "latest": {
            "id": audit_id_2,
            "date": a2.get("date"),
            "score": score2,
            "grade": sc2.get("grade", "?"),
            "pages": a2.get("pages_crawled", 1),
        },
        "score_delta": score_diff,
        "grade_improved": score_diff > 0,
        "dimensions": dim_diffs,
        "resolved_issues_count": len(resolved_issues),
        "new_issues_count": len(new_issues),
        "persistent_issues_count": len(persistent_issues),
        "resolved_issues": resolved_issues,
        "new_issues": new_issues,
        "persistent_issues": persistent_issues,
        "speed_comparison": {
            "mobile_perf_before": mob1.get("performance"),
            "mobile_perf_after": mob2.get("performance"),
            "mobile_perf_delta": (mob2.get("performance") - mob1.get("performance"))
            if (mob1.get("performance") is not None and mob2.get("performance") is not None)
            else None,
        },
    }
