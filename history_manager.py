# -*- coding: utf-8 -*-
"""
History and Progress Tracker for SEO Audit Pro.
Stores completed audits, computes SEO progress over time, manages targets,
and simulates score gains from remediating detected issues.
"""

import copy
import json
import os
import re
from datetime import datetime

import scoring

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(APP_DIR, "reports")
AUDITS_DIR = os.path.join(REPORTS_DIR, "audits")
HISTORY_FILE = os.path.join(REPORTS_DIR, "history.json")
TARGETS_FILE = os.path.join(REPORTS_DIR, "targets.json")


def _ensure_dirs():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(AUDITS_DIR, exist_ok=True)


def load_history():
    """Returns the list of historical audit summaries, sorted newest first."""
    _ensure_dirs()
    if not os.path.isfile(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_history(history_list):
    """Saves the audit summaries list."""
    _ensure_dirs()
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_list, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def save_audit_result(audit_id, result):
    """Saves full audit result JSON and adds an entry to the history index."""
    _ensure_dirs()
    audit_file = os.path.join(AUDITS_DIR, f"{audit_id}.json")
    try:
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[history] Error saving audit {audit_id}: {e}")

    # Build summary record for index
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

    summary = {
        "id": audit_id,
        "url": result.get("url") or result.get("final_url", ""),
        "domain": result.get("domain", ""),
        "timestamp": datetime.now().isoformat(),
        "date": result.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "score": sc.get("score", 0),
        "grade": sc.get("grade", "?"),
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

    history = load_history()
    history = [h for h in history if h.get("id") != audit_id]
    history.insert(0, summary)
    history = history[:200]
    save_history(history)
    return summary


def get_audit(audit_id):
    """Loads full audit result JSON by id."""
    _ensure_dirs()
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", audit_id)
    path = os.path.join(AUDITS_DIR, f"{safe_id}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def delete_audit(audit_id):
    """Deletes audit from history and disk."""
    _ensure_dirs()
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", audit_id)
    path = os.path.join(AUDITS_DIR, f"{safe_id}.json")
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    history = load_history()
    history = [h for h in history if h.get("id") != audit_id]
    save_history(history)
    return True


def get_domain_progress(domain):
    """
    Returns the historical progression timeline for a specific domain,
    sorted chronologically (oldest to newest).
    """
    norm_dom = domain.lower().replace("www.", "").strip()
    history = load_history()
    matches = []
    for h in history:
        h_dom = (h.get("domain") or "").lower().replace("www.", "").strip()
        if h_dom == norm_dom:
            matches.append(h)
    matches.sort(key=lambda x: x.get("timestamp", x.get("date", "")))
    return matches


# ---- Site Targets & Goals --------------------------------------------------
def get_targets(domain):
    """Get target goals for a specific domain."""
    _ensure_dirs()
    norm = domain.lower().replace("www.", "").strip()
    if os.path.isfile(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and norm in data:
                    return data[norm]
        except Exception:
            pass
    # Defaults
    return {
        "target_score": 90,
        "target_lcp": 2.5,
        "target_mobile_psi": 85,
        "target_critical": 0,
    }


def save_targets(domain, targets_dict):
    """Save target goals for a specific domain."""
    _ensure_dirs()
    norm = domain.lower().replace("www.", "").strip()
    data = {}
    if os.path.isfile(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
        except Exception:
            data = {}
    data[norm] = {
        "target_score": int(targets_dict.get("target_score", 90)),
        "target_lcp": float(targets_dict.get("target_lcp", 2.5)),
        "target_mobile_psi": int(targets_dict.get("target_mobile_psi", 85)),
        "target_critical": int(targets_dict.get("target_critical", 0)),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
    }
    try:
        with open(TARGETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    return data[norm]


# ---- "What-If" Fix Simulator ------------------------------------------------
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


# ---- Audit Comparison -------------------------------------------------------
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
