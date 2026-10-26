# -*- coding: utf-8 -*-
"""
Scoring — one transparent SEO health score, plus a prioritized action plan.

Adapted from Content Quality Auditor's scoring.py:
  * TRANSPARENT — every category's sub-score, weight and contribution is
    returned, so the overall number is fully explainable.
  * ROBUST TO MISSING DATA — categories with no applicable checks (e.g. no
    hreflang, or PSI unavailable) are dropped and the remaining weights are
    renormalised, with a completeness/confidence indicator.
  * HONEST PENALTIES — a noindexed homepage, missing HTTPS, or critical failures
    visibly dock points rather than being hidden.

Check value: pass→1.0, warn→0.5, fail→0.0; info/na excluded from denominators.
Per-category score = severity-weighted pass rate. Overall = weighted blend.
"""

from seo_checks import CATEGORIES, SEV_WEIGHT, CATEGORY_LABEL

# STRICT audit — a warning only earns 30% credit and a fail earns none, so a
# site has to genuinely follow best practice to score well. This is deliberate:
# the audit is a lead-qualification tool and holds sites to an agency standard.
_VALUE = {"pass": 1.0, "warn": 0.3, "fail": 0.0}
_PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

# generic SEO-impact statement per category, used when a check doesn't provide
# its own "impact" copy.
_CATEGORY_IMPACT = {
    "technical": "Affects how search engines crawl, index and trust the site.",
    "onpage": "Affects keyword relevance and click-through from search results.",
    "performance": "Affects Core Web Vitals rankings and user retention.",
    "content": "Affects topical authority, relevance and engagement.",
    "mobile": "Affects mobile-first indexing and the majority of your visitors.",
    "accessibility": "Affects usability for all users and overlaps with SEO.",
    "security": "Affects user trust and browser/search safe-browsing signals.",
    "local": "Affects Google Business Profile visibility, Maps rankings, and local customer acquisition.",
    "offpage": "Affects domain authority, backlink trust signals, and referral growth.",
    "ux": "Affects engagement, bounce rate and page-experience signals.",
}
_GAIN_FROM_SEV = {"critical": "High", "high": "High", "medium": "Medium",
                  "low": "Low"}
_DIFFICULTY = {"low": "Easy", "med": "Moderate", "high": "Involved"}


def _grade(score):
    # Strict grade bands — an A is genuinely hard to earn.
    for cut, g in [(95, "A+"), (90, "A"), (86, "A-"), (82, "B+"), (78, "B"),
                   (74, "B-"), (69, "C+"), (64, "C"), (59, "C-"), (53, "D+"),
                   (46, "D"), (0, "F")]:
        if score >= cut:
            return g
    return "F"


def _label(score):
    if score >= 85:
        return "Strong — ahead of most competitors"
    if score >= 70:
        return "Fair — a solid base with real gaps to close"
    if score >= 55:
        return "Needs work — leaving visibility and revenue on the table"
    if score >= 40:
        return "At risk — major issues are suppressing your rankings"
    return "Critical — urgent problems are costing you traffic"


def _category_score(checks):
    """Severity-weighted pass rate over scored checks. Returns (score, counts)."""
    num = den = 0.0
    counts = {"pass": 0, "warn": 0, "fail": 0, "info": 0, "na": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        if c["status"] in _VALUE:
            w = SEV_WEIGHT.get(c["severity"], 1)
            num += w * _VALUE[c["status"]]
            den += w
    score = round(num / den * 100) if den else None
    return score, counts


def compute(checks):
    """Return the full transparent score breakdown from a flat check list."""
    by_cat = {}
    for c in checks:
        by_cat.setdefault(c["category"], []).append(c)

    present = []
    for key, label, weight in CATEGORIES:
        cat_checks = by_cat.get(key, [])
        score, counts = _category_score(cat_checks)
        if score is None:
            continue  # no scored checks → drop & renormalize
        present.append((key, label, weight, score, counts, len(cat_checks)))

    if not present:
        return None

    total_w = sum(w for _k, _l, w, _s, _c, _n in present)
    dimensions, base = [], 0.0
    for key, label, weight, score, counts, n in present:
        eff = weight / total_w
        base += score * eff
        dimensions.append({
            "key": key, "label": label, "score": score, "weight": weight,
            "weight_effective": round(eff, 3), "contribution": round(score * eff, 1),
            "counts": counts, "checks_total": n,
        })
    base = round(base, 1)

    # ---- honest penalties -------------------------------------------------
    penalties, penalty_total = [], 0.0
    idx = {c["id"]: c for c in checks}
    if idx.get("noindex_pages", {}).get("status") == "fail":
        penalty_total += 10
        penalties.append("Homepage set to noindex (-10)")
    if idx.get("sec_https_active", {}).get("status") == "fail":
        penalty_total += 8
        penalties.append("Site not served over HTTPS (-8)")
    crit_fails = [c for c in checks
                  if c["severity"] == "critical" and c["status"] == "fail"]
    if crit_fails:
        p = min(24, len(crit_fails) * 8)
        penalty_total += p
        penalties.append("%d critical failure(s) (-%d)" % (len(crit_fails), p))
    # High-severity failures also carry a (smaller) surfaced penalty.
    high_fails = [c for c in checks
                  if c["severity"] == "high" and c["status"] == "fail"]
    if high_fails:
        p = min(12, len(high_fails) * 3)
        penalty_total += p
        penalties.append("%d high-impact failure(s) (-%d)" % (len(high_fails),
                                                              p))

    score = int(max(0, min(100, round(base - penalty_total))))

    # ---- completeness / confidence ---------------------------------------
    completeness = round(total_w * 100)
    have_perf = any(d["key"] == "performance" for d in dimensions)
    if completeness >= 85 and have_perf:
        confidence = "High"
    elif completeness >= 60:
        confidence = "Medium"
    else:
        confidence = "Low"

    # ---- drivers ----------------------------------------------------------
    pos, neg = [], []
    for d in sorted(dimensions, key=lambda x: -x["score"]):
        if d["score"] >= 80:
            pos.append("%s is strong (%d)" % (d["label"], d["score"]))
    for d in sorted(dimensions, key=lambda x: x["score"]):
        if d["score"] < 65:
            neg.append("%s needs work (%d)" % (d["label"], d["score"]))
    neg.extend(penalties)

    counts_total = {"pass": 0, "warn": 0, "fail": 0, "info": 0, "na": 0}
    for c in checks:
        counts_total[c["status"]] = counts_total.get(c["status"], 0) + 1

    return {
        "score": score, "grade": _grade(score), "label": _label(score),
        "confidence": confidence, "completeness_pct": completeness,
        "base_before_penalties": base, "penalty_total": round(penalty_total, 1),
        "penalties": penalties, "dimensions": dimensions,
        "drivers_positive": pos[:6], "drivers_negative": neg[:6],
        "counts": counts_total,
    }


# ---------------------------------------------------------------------------
# Action plan (issue prioritization)
# ---------------------------------------------------------------------------
def _category_weight(key):
    for k, _l, w in CATEGORIES:
        if k == key:
            return w
    return 0.1


def _priority(impact, severity, status):
    if severity == "critical" and status == "fail":
        return "Critical"
    if impact >= 0.55:
        return "Critical"
    if impact >= 0.35:
        return "High"
    if impact >= 0.18:
        return "Medium"
    return "Low"


def _determine_tier(category, check_id):
    # Tier 1: Search Visibility & Indexability
    if check_id in ("robots_present", "noindex_pages", "sitemap_present", "sitemap_status",
                     "sitemap_valid", "canonical_self", "redirect_chains", "broken_internal_links"):
        return 1, "Search Visibility & Indexability", "Ensures search engine bots crawl and index priority revenue pages without dead-ends."
    # Tier 2: Technical SEO
    if category == "technical":
        return 2, "Technical SEO", "Eliminates server architecture and technical barriers that suppress domain search rankings."
    # Tier 3: On-Page SEO
    if category in ("onpage", "content"):
        return 3, "On-Page SEO & Content", "Directly improves organic click-through rates, keyword ranking depth, and conversion points."
    # Tier 4: Local SEO
    if category == "local":
        return 4, "Local SEO", "Boosts visibility on Google Maps, local 3-pack rankings, and drives qualified inbound calls/foot-traffic."
    # Tier 5: Off-Page SEO
    if category == "offpage":
        return 5, "Off-Page SEO & Authority", "Elevates domain trust, referral acquisition, and strengthens topical backlink equity."
    # Tier 6: Performance, Mobile UX & Security
    return 6, "Performance, Mobile UX & Security", "Protects mobile visitors, satisfies Core Web Vitals, and secures customer trust."


def action_items(checks):
    """Every non-pass finding becomes a prioritized, actionable item."""
    items = []
    for c in checks:
        if c["status"] not in ("warn", "fail"):
            continue
        sev_w = SEV_WEIGHT.get(c["severity"], 1) / 5.0
        cat_w = _category_weight(c["category"])
        status_w = 1.0 if c["status"] == "fail" else 0.5
        impact = round(sev_w * (0.5 + cat_w) * status_w, 3)
        priority = _priority(impact, c["severity"], c["status"])
        tier_num, tier_title, biz_impact = _determine_tier(c["category"], c["id"])
        items.append({
            "id": c["id"], "priority": priority, "impact": impact,
            "tier": tier_num, "tier_title": tier_title,
            "business_impact": biz_impact,
            "label": c["label"], "category": c["category"],
            "category_label": CATEGORY_LABEL.get(c["category"], c["category"]),
            "status": c["status"], "severity": c["severity"],
            "effort": c["effort"], "detail": c["detail"],
            "fix": c.get("fix", ""), "where": c.get("where", []),
            # enriched, agency-style fields (per-check override, else derived)
            "why": c.get("why") or _CATEGORY_IMPACT.get(c["category"], ""),
            "impact_text": c.get("impact") or _CATEGORY_IMPACT.get(
                c["category"], ""),
            "gain": c.get("gain") or _GAIN_FROM_SEV.get(c["severity"], "Medium"),
            "difficulty": _DIFFICULTY.get(c["effort"], "Moderate"),
        })
    effort_rank = {"low": 0, "med": 1, "high": 2}
    items.sort(key=lambda it: (it["tier"], _PRIORITY_ORDER.get(it["priority"], 9),
                               -it["impact"], effort_rank.get(it["effort"], 1)))
    return items


def priority_counts(items):
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for it in items:
        counts[it["priority"]] = counts.get(it["priority"], 0) + 1
    return counts
