"""Result filtering — pure, offline-testable record predicates.

A filter spec is a plain dict (easy to pass across Qt signals and persist). The
spec is split into two predicates by cost:

* `cheap`  — decidable from a feed card alone (rating, website presence, name /
  category text). Applied during collection to skip work early.
* `full`   — needs fully-populated fields (review count, phone, email) that may
  require a detail-page visit or website enrichment. Applied just before emit.

`needs_reviews` / `needs_phone` / `needs_email` tell the scraper which extra work
a spec forces, so it only pays for what the filter actually needs.
"""

from __future__ import annotations


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(v) -> int:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _terms(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()
    return [t.strip().lower() for t in value if t and t.strip()]


def normalize_spec(spec: dict | None) -> dict:
    spec = dict(spec or {})
    return {
        "min_rating": _to_float(spec.get("min_rating", 0)),
        "min_reviews": _to_int(spec.get("min_reviews", 0)),
        "max_reviews": _to_int(spec.get("max_reviews", 0)),  # 0 = no cap
        "require_phone": bool(spec.get("require_phone", False)),
        "require_website": bool(spec.get("require_website", False)),
        "require_no_website": bool(spec.get("require_no_website", False)),
        "require_email": bool(spec.get("require_email", False)),
        "name_include": _terms(spec.get("name_include")),
        "name_exclude": _terms(spec.get("name_exclude")),
        "category_include": _terms(spec.get("category_include")),
        "category_exclude": _terms(spec.get("category_exclude")),
        "claim_status": str(spec.get("claim_status") or "any").strip().lower(),
    }


def is_active(spec: dict | None) -> bool:
    s = normalize_spec(spec)
    return (
        s["min_rating"] > 0 or s["min_reviews"] > 0 or s["max_reviews"] > 0
        or s["require_phone"] or s["require_website"] or s["require_no_website"]
        or s["require_email"] or s["name_include"] or s["name_exclude"]
        or s["category_include"] or s["category_exclude"]
        or s["claim_status"] in ("unclaimed", "claimed")
    )


def needs_reviews(spec: dict | None) -> bool:
    s = normalize_spec(spec)
    return s["min_reviews"] > 0 or s["max_reviews"] > 0


def needs_phone(spec: dict | None) -> bool:
    return normalize_spec(spec)["require_phone"]


def needs_email(spec: dict | None) -> bool:
    return normalize_spec(spec)["require_email"]


def needs_claim(spec: dict | None) -> bool:
    return normalize_spec(spec)["claim_status"] in ("unclaimed", "claimed")


def cheap_pass(record: dict, spec: dict | None) -> bool:
    """Filters decidable from card-level data. Applied during collection."""
    s = normalize_spec(spec)

    if s["min_rating"] > 0 and _to_float(record.get("rating")) < s["min_rating"]:
        return False

    website = (record.get("website") or "").strip()
    if s["require_website"] and not website:
        return False
    if s["require_no_website"] and website:
        return False

    name = (record.get("name") or "").lower()
    if s["name_include"] and not any(t in name for t in s["name_include"]):
        return False
    if s["name_exclude"] and any(t in name for t in s["name_exclude"]):
        return False

    category = (record.get("category") or "").lower()
    if s["category_include"] and not any(t in category for t in s["category_include"]):
        return False
    if s["category_exclude"] and any(t in category for t in s["category_exclude"]):
        return False

    return True


def full_pass(record: dict, spec: dict | None) -> bool:
    """Filters needing fully-populated fields. Applied just before emit."""
    s = normalize_spec(spec)

    if not cheap_pass(record, spec):
        return False

    if s["require_phone"] and not (record.get("phone") or "").strip():
        return False
    if s["require_email"] and not (record.get("email") or "").strip():
        return False
    if s["claim_status"] == "unclaimed" and record.get("claim_this_business") != "Yes":
        return False
    if s["claim_status"] == "claimed" and record.get("claim_this_business") != "No":
        return False

    reviews = _to_int(record.get("review_count"))
    if s["min_reviews"] > 0 and reviews < s["min_reviews"]:
        return False
    if s["max_reviews"] > 0 and reviews > s["max_reviews"]:
        return False

    return True
