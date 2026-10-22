"""Pure, side-effect-free parsing of Google Maps search-feed HTML.

Everything in this module operates on strings / lxml elements — no Selenium,
no network — so it can be unit-tested offline against a saved page (see
`tests/test_parse.py`, which runs against a real captured feed).

The whole speed/accuracy win of MapHarvest lives here: a single Google Maps
results feed already contains name, rating, category, address, phone, website
and — encoded in each place URL — coordinates and a stable Place ID. Reading
all of that straight from the feed avoids opening a separate browser tab per
business, which is what made the old scraper slow and fragile.
"""

from __future__ import annotations

import re
import urllib.parse

import lxml.html as LH

# ── URL parsing ──────────────────────────────────────────────────────────────
# A Maps place URL looks like:
#   /maps/place/Alpine+Roofing/data=!4m7!3m6
#     !1s0x89d4cc9b150c8a9f:0xb0887b40788685bd   <- hex CID pair
#     !8m2!3d43.656728!4d-79.338035              <- lat / lng
#     !16s%2Fg%2F1tk68nss                        <- feature id (/g/...)
#     !19sChIJn4oMFZvM1IkRvYWGeEB7iLA            <- ChIJ Place ID
_COORD_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_PLACEID_RE = re.compile(r"!19s([^!?&]+)")
_CID_RE = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
_ATLL_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")  # fallback: @lat,lng

_PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s().]{6,}\d)")
_STATUS_RE = re.compile(
    r"\b(open|closed|closes|opens|24 hours|temporarily|permanently|reopens)\b",
    re.I,
)
_SEP_CHARS = "·•・"  # ·  •  ・


def parse_place_url(href: str) -> dict:
    """Pull coordinates + stable identifiers out of a Maps place URL."""
    out = {"maps_link": "", "latitude": "", "longitude": "", "place_id": "", "cid": ""}
    if not href:
        return out

    out["maps_link"] = href.split("?")[0]

    m = _COORD_RE.search(href)
    if not m:
        m = _ATLL_RE.search(href)
    if m:
        out["latitude"], out["longitude"] = m.group(1), m.group(2)

    m = _PLACEID_RE.search(href)
    if m:
        out["place_id"] = urllib.parse.unquote(m.group(1))

    m = _CID_RE.search(href)
    if m:
        out["cid"] = m.group(1)

    return out


def place_key(href: str) -> str:
    """Stable dedup key for a listing, resilient to URL variants."""
    info = parse_place_url(href)
    if info["place_id"]:
        return info["place_id"]
    if info["cid"]:
        return info["cid"]
    m = re.search(r"/place/([^/@?]+)", href or "")
    if m:
        return urllib.parse.unquote(m.group(1))
    return (href or "").split("?")[0]


# ── small helpers ────────────────────────────────────────────────────────────
def _clean(text: str | None) -> str:
    return " ".join(text.split()) if text else ""


def _el_text(el) -> str:
    return _clean("".join(el.itertext())) if el is not None else ""


def _has_class(cls: str):
    # XPath predicate that matches a whitespace-delimited class token.
    return (
        f'contains(concat(" ", normalize-space(@class), " "), " {cls} ")'
    )


def _split_sep(text: str) -> list[str]:
    parts = re.split(f"[{_SEP_CHARS}]", text)
    return [p.strip() for p in parts if p.strip()]


# ── card parsing ─────────────────────────────────────────────────────────────
def parse_card(card) -> dict:
    """Extract every card-available field from one feed card element.

    Returns a dict with the public field keys plus bookkeeping keys prefixed
    with '_' (`_href`, `_sponsored`, `_key`). Missing fields are "".
    """
    data = {
        "name": "",
        "category": "",
        "rating": "",
        "review_count": "",
        "address": "",
        "website": "",
        "phone": "",
        "maps_link": "",
        "latitude": "",
        "longitude": "",
        "place_id": "",
        "claim_this_business": "",
        "_href": "",
        "_sponsored": False,
        "_key": "",
    }

    # Anchor → name + place URL (the aria-label is the business name).
    anchors = card.xpath(f'.//a[{_has_class("hfpxzc")}]')
    if not anchors:
        anchors = card.xpath('.//a[contains(@href, "/maps/place")]')
    if anchors:
        a = anchors[0]
        href = a.get("href") or ""
        data["_href"] = href
        data["name"] = _clean(a.get("aria-label")) or ""
        data.update({k: v for k, v in parse_place_url(href).items() if k != "cid"})
        data["place_id"] = parse_place_url(href)["place_id"]

    if not data["name"]:
        headline = card.xpath(f'.//div[{_has_class("qBF1Pd")}]')
        if headline:
            data["name"] = _el_text(headline[0])

    data["_key"] = place_key(data["_href"]) or data["name"]

    # Sponsored / ad detection.
    if card.xpath('.//*[@aria-label="Sponsored"]') or card.xpath(
        './/span[normalize-space(text())="Sponsored"]'
    ):
        data["_sponsored"] = True

    # Rating.
    rating_el = card.xpath(f'.//span[{_has_class("MW4etd")}]')
    if rating_el:
        m = re.search(r"[\d.]+", _el_text(rating_el[0]))
        if m:
            data["rating"] = m.group(0)
    if not data["rating"]:
        for lbl in card.xpath('.//span[@role="img"]/@aria-label'):
            m = re.search(r"([\d.]+)\s*star", lbl, re.I)
            if m:
                data["rating"] = m.group(1)
                break

    # Review count — not always present in the feed; best effort.
    data["review_count"] = _extract_review_count(card)

    # Phone (span.UsdlK is stable), with a regex fallback.
    phone_el = card.xpath(f'.//span[{_has_class("UsdlK")}]')
    phone = _el_text(phone_el[0]) if phone_el else ""

    # Website — skip ad-redirect (/aclk, /url) hrefs; those aren't real sites.
    for w in card.xpath('.//a[@data-value="Website"]/@href'):
        if w and not w.startswith(("/aclk", "/url", "https://www.google.")):
            data["website"] = w
            break

    # Category + address live in the "leaf" W4Efsd rows.
    category, address = _extract_category_address(card, phone)
    data["category"] = category
    data["address"] = address

    if not phone:  # last resort: dig a phone number out of the info rows
        phone = _phone_from_rows(card)
    data["phone"] = phone

    return data


def _extract_review_count(card) -> str:
    # "(1,234)" style spans.
    for cls in ("UY7F9", "RDApEe"):
        els = card.xpath(f'.//span[{_has_class(cls)}]')
        for el in els:
            m = re.search(r"([\d,]+)", _el_text(el))
            if m:
                return m.group(1).replace(",", "")
    # "4.5 stars 1,234 Reviews" aria-labels.
    for lbl in card.xpath('.//span[@role="img"]/@aria-label'):
        m = re.search(r"([\d,]+)\s*review", lbl, re.I)
        if m:
            return m.group(1).replace(",", "")
    return ""


def _info_rows(card):
    """Innermost W4Efsd rows (the ones that actually hold text)."""
    rows = card.xpath(f'.//div[{_has_class("W4Efsd")}]')
    return [r for r in rows if not r.xpath(f'.//div[{_has_class("W4Efsd")}]')]


def _extract_category_address(card, phone: str) -> tuple[str, str]:
    for row in _info_rows(card):
        text = _el_text(row)
        if not text:
            continue
        if re.fullmatch(r"[\d.]+", text):  # pure rating row
            continue
        if phone and phone in text:  # status · phone row
            continue
        if _STATUS_RE.search(text) and any(c in text for c in _SEP_CHARS) is False:
            continue
        parts = _split_sep(text)
        if not parts:
            continue
        # Drop a leading/trailing status token ("Open 24 hours").
        parts = [p for p in parts if not _STATUS_RE.search(p)]
        if not parts:
            continue
        category = parts[0]
        address = parts[-1] if len(parts) > 1 else ""
        # A row that is only a status/phone line shouldn't win.
        if phone and address and phone in address:
            continue
        return category, address
    return "", ""


def _phone_from_rows(card) -> str:
    for row in _info_rows(card):
        text = _el_text(row)
        m = _PHONE_RE.search(text)
        if m:
            cand = _clean(m.group(0))
            if sum(ch.isdigit() for ch in cand) >= 7:
                return cand
    return ""


def parse_feed_html(html: str) -> list[dict]:
    """Parse every business card in a feed's HTML into a list of dicts."""
    if not html:
        return []
    doc = LH.fromstring(html)
    cards = doc.xpath(f'//div[{_has_class("Nv2PK")}]')
    if not cards:
        # Fallback: any article/link container that holds a place anchor.
        cards = doc.xpath('//div[.//a[contains(@href, "/maps/place")]][@role="article"]')
    out = []
    for c in cards:
        card = parse_card(c)
        if card["_href"] or card["name"]:
            out.append(card)
    return out
