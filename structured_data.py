# -*- coding: utf-8 -*-
"""
Structured-data detection & validation (free, offline).

Parses JSON-LD (the format Google recommends), plus a light tally of Microdata
and RDFa, from a page's raw HTML. Classifies the schema.org @types present and
validates the required properties Google needs for the common rich-result types
(Article, Product, Organization/WebSite, LocalBusiness, FAQPage,
BreadcrumbList), then reports rich-result eligibility.

No network, no keys — pure parsing.
"""

import json

from bs4 import BeautifulSoup

# type -> required properties for Google rich-result eligibility (simplified but
# faithful to the mandatory fields).
REQUIRED = {
    "Article": ["headline"],
    "NewsArticle": ["headline"],
    "BlogPosting": ["headline"],
    "Product": ["name"],
    "Organization": ["name"],
    "WebSite": ["name", "url"],
    "LocalBusiness": ["name", "address"],
    "FAQPage": ["mainEntity"],
    "BreadcrumbList": ["itemListElement"],
    "Recipe": ["name", "image"],
    "Event": ["name", "startDate", "location"],
    "VideoObject": ["name", "thumbnailUrl", "uploadDate"],
}
# Recommended (not required) — surfaced as warnings, never as failures.
RECOMMENDED = {
    "Article": ["author", "datePublished", "image"],
    "NewsArticle": ["author", "datePublished", "image"],
    "BlogPosting": ["author", "datePublished", "image"],
    "Product": ["offers", "image", "aggregateRating", "review", "brand"],
    "Organization": ["url", "logo", "sameAs"],
    "LocalBusiness": ["telephone", "openingHours", "geo", "priceRange"],
    "Event": ["endDate", "offers", "performer"],
}


def _types_of(node):
    """Return a list of schema.org @type strings on a node (handles arrays)."""
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x).split("/")[-1] for x in t if x]
    if isinstance(t, str):
        return [t.split("/")[-1]]
    return []


def _walk(node, collected):
    """Recurse a JSON-LD node, collecting every typed object (incl. @graph and
    nested arrays)."""
    if isinstance(node, list):
        for n in node:
            _walk(n, collected)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node and isinstance(node["@graph"], list):
        for n in node["@graph"]:
            _walk(n, collected)
    if _types_of(node):
        collected.append(node)
    # Recurse into nested typed objects (e.g. Product -> offers -> Offer).
    for v in node.values():
        if isinstance(v, (list, dict)):
            _walk(v, collected)


def extract(html):
    """Return {jsonld_objects, jsonld_blocks, parse_errors, microdata,
    rdfa}."""
    soup = BeautifulSoup(html, "html.parser")
    objects, blocks, errors = [], 0, []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        blocks += 1
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception as e:
            errors.append(str(e).split("(")[0].strip())
            continue
        _walk(data, objects)
    microdata = len(soup.find_all(attrs={"itemscope": True}))
    rdfa = len(soup.find_all(attrs={"typeof": True}))
    return {"jsonld_objects": objects, "jsonld_blocks": blocks,
            "parse_errors": errors, "microdata": microdata, "rdfa": rdfa}


def _validate_object(obj):
    """(type, missing_required, missing_recommended) for one typed object."""
    findings = []
    for t in _types_of(obj):
        req = REQUIRED.get(t)
        if req is None:
            continue
        missing_req = [p for p in req if not obj.get(p)]
        missing_rec = [p for p in RECOMMENDED.get(t, []) if not obj.get(p)]
        findings.append((t, missing_req, missing_rec))
    return findings


def analyze(html):
    """Main entry: full structured-data assessment for one page."""
    ex = extract(html)
    objects = ex["jsonld_objects"]
    types, validations = [], []
    for obj in objects:
        types.extend(_types_of(obj))
        validations.extend(_validate_object(obj))

    types_unique = sorted(set(types))
    invalid = [(t, mr) for (t, mr, _rec) in validations if mr]
    warn_recommended = [(t, rec) for (t, _mr, rec) in validations if rec]

    known_validated = [t for (t, _mr, _rec) in validations]
    rich_eligible = bool(known_validated) and not invalid

    return {
        "present": bool(objects) or ex["microdata"] > 0 or ex["rdfa"] > 0,
        "jsonld_blocks": ex["jsonld_blocks"],
        "jsonld_object_count": len(objects),
        "parse_errors": ex["parse_errors"],
        "microdata_items": ex["microdata"],
        "rdfa_items": ex["rdfa"],
        "types": types_unique,
        "rich_types": sorted(set(known_validated)),
        "missing_required": invalid,          # [(type, [missing...])]
        "missing_recommended": warn_recommended,
        "rich_eligible": rich_eligible,
        "has_organization": any(t in types_unique
                                for t in ("Organization", "WebSite")),
        "has_breadcrumb": "BreadcrumbList" in types_unique,
    }
