# -*- coding: utf-8 -*-
"""
PageSpeed Insights v5 — the free performance & Core Web Vitals data source.

Runs Google's public PSI endpoint, which works with NO API key (an optional key
only raises quota). One call returns:
  * Lighthouse lab scores (Performance / SEO / Accessibility / Best-Practices)
  * lab Core Web Vitals (LCP, TBT, CLS, FCP, Speed Index, TTI)
  * real-user CrUX FIELD data inside `loadingExperience` /
    `originLoadingExperience` (p75 LCP / INP / CLS / FCP / TTFB + pass/fail)
  * top optimisation opportunities (render-blocking, page weight, images, text
    compression)

Nothing here needs authentication. If a site has no field data, the field block
is simply marked unavailable and downstream checks report `na`.
"""

import requests

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Core Web Vitals thresholds: (good_max, poor_min). Between the two = "needs
# improvement". Units: ms except CLS (unitless).
CWV_THRESHOLDS = {
    "lcp": (2500, 4000),
    "inp": (200, 500),
    "cls": (0.1, 0.25),
    "fcp": (1800, 3000),
    "ttfb": (800, 1800),
}

# CrUX field-metric id -> our short key.
_FIELD_MAP = {
    "LARGEST_CONTENTFUL_PAINT_MS": "lcp",
    "INTERACTION_TO_NEXT_PAINT": "inp",
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": "cls",
    "FIRST_CONTENTFUL_PAINT_MS": "fcp",
    "EXPERIENCE_TIME_TO_FIRST_BYTE": "ttfb",
}


def rate_metric(key, value):
    """good / needs-improvement / poor for a CWV value (value already in ms, or
    the raw CLS score)."""
    if value is None:
        return "na"
    good, poor = CWV_THRESHOLDS[key]
    if value <= good:
        return "good"
    if value > poor:
        return "poor"
    return "needs-improvement"


def _pct(score01):
    return int(round(score01 * 100)) if isinstance(score01, (int, float)) else None


def _audit_num(audits, aid):
    a = audits.get(aid) or {}
    v = a.get("numericValue")
    return v if isinstance(v, (int, float)) else None


def _audit_savings(audits, aid):
    """(overallSavingsMs, overallSavingsBytes, score) for an opportunity audit."""
    a = audits.get(aid) or {}
    det = a.get("details") or {}
    return (det.get("overallSavingsMs"), det.get("overallSavingsBytes"),
            a.get("score"))


def _parse_field(block, source):
    """Normalize a loadingExperience / originLoadingExperience block."""
    if not isinstance(block, dict):
        return None
    metrics = block.get("metrics") or {}
    if not metrics:
        return None
    out = {"available": True, "source": source,
           "overall": block.get("overall_category", ""), "metrics": {}}
    for gid, short in _FIELD_MAP.items():
        m = metrics.get(gid)
        if not isinstance(m, dict):
            continue
        p = m.get("percentile")
        val = None
        if isinstance(p, (int, float)):
            val = p / 100.0 if short == "cls" else float(p)
        out["metrics"][short] = {
            "p75": val,
            "rating": rate_metric(short, val) if val is not None else "na",
            "google_category": m.get("category", ""),
        }
    return out if out["metrics"] else None


def run_pagespeed(url, strategy="mobile", api_key=None, timeout=90):
    """Run PSI for one URL+strategy. Returns a normalized dict; never raises."""
    result = {
        "url": url, "strategy": strategy, "ok": False, "error": "",
        "scores": {}, "lab": {}, "field": {"available": False},
        "opportunities": {}, "top_opportunities": [],
    }
    params = [
        ("url", url), ("strategy", strategy),
        ("category", "performance"), ("category", "seo"),
        ("category", "accessibility"), ("category", "best-practices"),
    ]
    if api_key:
        params.append(("key", api_key))
    try:
        r = requests.get(PSI_ENDPOINT, params=params, timeout=timeout)
    except requests.RequestException as e:
        result["error"] = "network error: %s" % e
        return result
    if not r.ok:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        result["error"] = "PSI HTTP %d: %s" % (r.status_code, msg)
        return result
    try:
        data = r.json()
    except Exception as e:
        result["error"] = "PSI returned non-JSON: %s" % e
        return result

    lh = data.get("lighthouseResult") or {}
    cats = lh.get("categories") or {}
    audits = lh.get("audits") or {}

    result["scores"] = {
        "performance": _pct((cats.get("performance") or {}).get("score")),
        "seo": _pct((cats.get("seo") or {}).get("score")),
        "accessibility": _pct((cats.get("accessibility") or {}).get("score")),
        "best_practices": _pct((cats.get("best-practices") or {}).get("score")),
    }
    result["lab"] = {
        "lcp_ms": _audit_num(audits, "largest-contentful-paint"),
        "tbt_ms": _audit_num(audits, "total-blocking-time"),
        "cls": _audit_num(audits, "cumulative-layout-shift"),
        "fcp_ms": _audit_num(audits, "first-contentful-paint"),
        "speed_index_ms": _audit_num(audits, "speed-index"),
        "tti_ms": _audit_num(audits, "interactive"),
    }

    # Field data: prefer page-level, fall back to origin-level.
    field = (_parse_field(data.get("loadingExperience"), "page")
             or _parse_field(data.get("originLoadingExperience"), "origin"))
    if field:
        result["field"] = field

    rb_ms, _b, rb_score = _audit_savings(audits, "render-blocking-resources")
    _im_ms, im_bytes, im_score = _audit_savings(audits, "uses-optimized-images")
    _mf_ms, mf_bytes, mf_score = _audit_savings(audits, "modern-image-formats")
    _tc_ms, tc_bytes, tc_score = _audit_savings(audits, "uses-text-compression")
    total_bytes = _audit_num(audits, "total-byte-weight")
    result["opportunities"] = {
        "render_blocking_ms": rb_ms,
        "render_blocking_score": rb_score,
        "total_bytes": total_bytes,
        "image_savings_bytes": (im_bytes or 0) + (mf_bytes or 0) or None,
        "image_score": _min_score(im_score, mf_score),
        "text_compression_bytes": tc_bytes,
        "text_compression_score": tc_score,
    }

    # Top opportunities by estimated ms saved (for the report appendix).
    opps = []
    for aid, a in audits.items():
        details = a.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        ms = details.get("overallSavingsMs") or 0
        if ms and a.get("score") is not None and a.get("score") < 0.9:
            opps.append({"id": aid, "title": a.get("title", aid),
                         "savings_ms": int(ms)})
    opps.sort(key=lambda o: -o["savings_ms"])
    result["top_opportunities"] = opps[:8]

    # Lighthouse ACCESSIBILITY audits (real a11y data) + curated named audits
    # that power the Mobile / Accessibility / UX categories.
    result["accessibility_audits"] = _collect_category_audits(
        cats, audits, "accessibility")
    result["accessibility_score"] = _pct(
        (cats.get("accessibility") or {}).get("score"))
    named = {}
    for aid in ("viewport", "font-size", "tap-targets", "image-alt", "label",
                "color-contrast", "link-name", "document-title",
                "html-has-lang", "meta-description", "crawlable-anchors",
                "is-crawlable", "robots-txt", "structured-data",
                "uses-responsive-images", "efficient-animated-content",
                "unminified-css", "unminified-javascript", "unused-css-rules",
                "unused-javascript", "uses-rel-preconnect", "font-display",
                "server-response-time", "redirects", "uses-long-cache-ttl"):
        a = audits.get(aid)
        if a is not None:
            named[aid] = {"score": a.get("score"),
                          "title": a.get("title", aid),
                          "display": a.get("displayValue", ""),
                          "mode": a.get("scoreDisplayMode", "")}
    result["named_audits"] = named

    result["ok"] = True
    return result


def _collect_category_audits(cats, audits, key):
    """Return scored (non-informative) audits referenced by a Lighthouse
    category — used to surface real accessibility findings."""
    refs = (cats.get(key) or {}).get("auditRefs") or []
    out = []
    for ref in refs:
        aid = ref.get("id")
        a = audits.get(aid) or {}
        if a.get("scoreDisplayMode") in ("informative", "notApplicable",
                                         "manual"):
            continue
        if a.get("score") is None:
            continue
        out.append({"id": aid, "title": a.get("title", aid),
                    "score": a.get("score"),
                    "display": a.get("displayValue", ""),
                    "weight": ref.get("weight", 0)})
    return out


def _min_score(*scores):
    vals = [s for s in scores if isinstance(s, (int, float))]
    return min(vals) if vals else None
