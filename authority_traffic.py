# -*- coding: utf-8 -*-
"""
Authority & Traffic module — honest values only.

Domain Authority (DA), Page Authority (PA), organic traffic, ranking keywords
and country split are third-party / first-party measurements. This tool does
not have them unless a real data provider answers, so it NEVER estimates or
invents them:

  * DA / PA come from the Moz Links API when MOZ_ACCESS_ID + MOZ_SECRET_KEY are
    configured and the API returns a value. Otherwise they are None with
    source "not_measured".
  * Traffic, keywords and country split have no free provider here, so they are
    always None with source "not_measured".

Report builders show None as "Not measured" (or hide the row/section).
"""

import os
from urllib.parse import urlparse

import requests

NOT_MEASURED = "not_measured"


def not_measured_authority():
    return {
        "domain_authority": None,
        "page_authority": None,
        "source": NOT_MEASURED,
        "is_estimated": False,
    }


def get_moz_metrics(url, access_id=None, secret_key=None, timeout=8):
    """Moz Links API v2 url_metrics (HTTP Basic auth). Returns a dict with real
    DA/PA, or None when no key is configured or the API gives no value."""
    access_id = access_id or os.environ.get("MOZ_ACCESS_ID") or os.environ.get("MOZ_API_KEY")
    secret_key = secret_key or os.environ.get("MOZ_SECRET_KEY")
    if not access_id or not secret_key:
        return None
    try:
        p = urlparse(url if "://" in url else "https://" + url)
        target = p.netloc or p.path
        r = requests.post("https://lsapi.seomoz.com/v2/url_metrics",
                          auth=(access_id, secret_key),
                          json={"targets": [target]},
                          headers={"User-Agent": "MindcobSEOAuditPro/1.0"},
                          timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") if isinstance(data, dict) else data
        if not results:
            return None
        item = results[0] or {}
        da = item.get("domain_authority")
        pa = item.get("page_authority")
        if da is None:
            return None
        return {
            "domain_authority": int(round(float(da))),
            "page_authority": int(round(float(pa))) if pa is not None else None,
            "source": "Moz API",
            "is_estimated": False,
        }
    except Exception:
        return None


def calculate_authority_scores(url, *args, **kwargs):
    """Real DA/PA from Moz when configured, else None values (not measured).
    Extra arguments are accepted for backward compatibility and ignored."""
    return get_moz_metrics(url) or not_measured_authority()


def calculate_page_authority(fact=None, *args, **kwargs):
    """Per-page authority is not measured (no per-URL provider call is made)."""
    return None


def estimate_monthly_traffic(domain=None, *args, **kwargs):
    """Organic traffic / keywords / country split are not measured — there is
    no real data source for them in this tool, so nothing is made up."""
    return {
        "monthly_visits": None,
        "monthly_impressions": None,
        "growth_pct": None,
        "trajectory": [],
        "geo_distribution": [],
        "ranking_keywords": None,
        "top_3_pct": None,
        "search_visibility_index": None,
        "source": NOT_MEASURED,
        "is_estimated": False,
        "note": "Organic traffic and ranking keywords were not measured "
                "(they require Google Search Console / Analytics or a paid "
                "SEO data provider).",
    }
