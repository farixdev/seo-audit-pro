# -*- coding: utf-8 -*-
"""Find the audited business's Google Business Profile and read it.

1. Hints from the website itself: business names (schema, og:site_name, title),
   phone numbers (tel: links, schema) and the city/address (schema).
2. Google's official Places API (New) when a Google API key is available —
   either directly (``api_key``) or through the team server (``remote``, so the
   key stays on the server). This gives the full profile: rating, review count,
   recent reviews, photos, hours, status, summary.
3. Otherwise (and to add what the API does not expose: the "Claim this
   business" link and booking/order buttons) the public Google Maps page, read
   with a plain headless browser. Google shows automated browsers a limited
   view, so anything not shown there is simply absent (= "not checked").

A profile is only accepted when it links to the audited website or lists one
of the website's phone numbers — another business's listing is never reported.
Returns (place, note); ``place`` uses the Places API field names, plus:
  _source      "places_api" | "maps_scrape"
  _matched_by  "website" | "phone"
  claimed      False when Google shows "Claim this business" (absent = unknown)
  actions      booking / order / appointment buttons shown on the listing
"""

import json
import logging
import re
import time
import urllib.parse
from urllib.parse import urlparse

log = logging.getLogger("seo_audit_pro.maps")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "formattedAddress", "rating", "userRatingCount", "websiteUri", "nationalPhoneNumber",
    "internationalPhoneNumber", "regularOpeningHours", "photos", "primaryTypeDisplayName", "types",
    "businessStatus", "googleMapsUri", "editorialSummary", "reviews"))


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------
def _clean_domain(url_or_domain):
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    netloc = urlparse(raw).netloc if "://" in raw else raw.split("/")[0]
    return netloc.split(":")[0].removeprefix("www.")


def _digits(phone):
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else d


def same_site(website, domain):
    """The listing's website is the audited site (or a subdomain of it)."""
    site = _clean_domain(website)
    domain = _clean_domain(domain)
    return bool(site and domain) and (site == domain or site.endswith("." + domain) or domain.endswith("." + site))


def same_phone(phone, site_phones):
    d = _digits(phone)
    return len(d) >= 7 and any(d == _digits(p) for p in site_phones or [])


def name_similarity(a, b):
    """Share of the shorter name's words that appear in the other (0–1)."""
    stop = {"the", "and", "inc", "ltd", "llc", "co", "corp", "company", "limited", "of", "&"}
    wa = {w for w in re.findall(r"[a-z0-9]+", (a or "").lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z0-9]+", (b or "").lower()) if w not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


# ---------------------------------------------------------------------------
# What the website says about the business (search queries + NAP comparison)
# ---------------------------------------------------------------------------
def hints_from_html(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    names, phones, address, city = [], [], "", ""

    def add_name(n):
        n = re.sub(r"\s+", " ", (n or "")).strip(" |-–—")
        if 2 < len(n) <= 80 and n.lower() not in [x.lower() for x in names]:
            names.append(n)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            # Organization / LocalBusiness and its many subtypes (Plumber, Dentist, ...):
            # any named node with a phone or postal address that is not a page or person.
            not_business = {"Person", "WebSite", "WebPage", "Article", "BlogPosting", "NewsArticle", "Product",
                            "Offer", "Event", "ImageObject", "BreadcrumbList", "ListItem", "PostalAddress",
                            "ContactPoint", "SearchAction", "Review", "Rating", "AggregateRating"}
            named = isinstance(node.get("name"), str)
            org_type = any(isinstance(k, str) and (k in ("Organization", "LocalBusiness", "Corporation")
                                                   or "Business" in k) for k in kinds)
            local = named and (node.get("telephone") or isinstance(node.get("address"), dict)) \
                and not any(k in not_business for k in kinds if isinstance(k, str))
            if org_type or local:
                add_name(node.get("name") if isinstance(node.get("name"), str) else "")
                if isinstance(node.get("telephone"), str):
                    phones.append(node["telephone"])
                addr = node.get("address")
                if isinstance(addr, dict):
                    address = address or ", ".join(str(addr.get(k)) for k in (
                        "streetAddress", "addressLocality", "addressRegion", "postalCode") if addr.get(k))
                    city = city or str(addr.get("addressLocality") or "")
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        add_name(og["content"])
    head_title = soup.head.find("title") if soup.head else None
    if head_title:
        parts = [p.strip() for p in re.split(r"\s[|–—:-]\s|\||\s:\s?", head_title.get_text()) if p.strip()]
        parts = [p for p in parts if p.lower() not in ("home", "homepage", "welcome")]
        for p in sorted(parts, key=len)[:2]:   # the brand is usually the short part
            add_name(p)
    for a in soup.select("a[href^='tel:']"):
        phones.append(urllib.parse.unquote(a["href"][4:]))
    seen, uniq = set(), []
    for p in phones:
        d = _digits(p)
        if len(d) >= 7 and d not in seen:
            seen.add(d)
            uniq.append(p.strip())
    return {"names": names[:4], "phones": uniq[:4], "address": address, "city": city,
            "domain": _clean_domain(url)}


def _queries(hints, domain):
    q = []
    for p in hints.get("phones") or []:
        q.append(p)
    city = hints.get("city") or ""
    for n in (hints.get("names") or [])[:2]:
        q.append(n if city.lower() in n.lower() else f"{n} {city}".strip())
    q.append(domain)
    out = []
    for x in q:
        if x and x not in out:
            out.append(x)
    return out[:5]


def _verify(place, domain, hints):
    if same_site(place.get("websiteUri"), domain):
        return "website"
    if same_phone(place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"), hints.get("phones")):
        return "phone"
    return None


# ---------------------------------------------------------------------------
# Places API (New)
# ---------------------------------------------------------------------------
def places_search(query, api_key, timeout=20):
    """Raw Places API text search. Returns (places list, error or None)."""
    import requests
    try:
        r = requests.post(PLACES_URL, timeout=timeout, json={"textQuery": query, "maxResultCount": 5},
                          headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": PLACES_FIELDS})
    except requests.RequestException as e:
        return [], f"unreachable: {e}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        return [], f"api_error: {msg[:200]}"
    return r.json().get("places") or [], None


def _from_api(queries, search, domain, hints):
    errors = []
    for q in queries:
        places, err = search(q)
        if err:
            errors.append(err)
            if err.startswith(("api_error", "no_key")):
                break   # a bad key or a disabled API will not get better with another query
            continue
        for p in places:
            how = _verify(p, domain, hints)
            if how:
                p = dict(p, _source="places_api", _matched_by=how)
                return p, "matched_" + how
    return None, (errors[0] if errors else "not_found")


# ---------------------------------------------------------------------------
# Public Google Maps page (plain headless browser, limited view)
# ---------------------------------------------------------------------------
def _get_driver():
    try:
        from selenium import webdriver
    except ImportError:
        return None
    for make in ("chrome", "edge"):
        try:
            if make == "chrome":
                from selenium.webdriver.chrome.options import Options
            else:
                from selenium.webdriver.edge.options import Options
            opts = Options()
            for arg in ("--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1280,900", "--lang=en-US"):
                opts.add_argument(arg)
            opts.page_load_strategy = "eager"
            return webdriver.Chrome(options=opts) if make == "chrome" else webdriver.Edge(options=opts)
        except Exception as e:  # noqa: BLE001 - try the next browser
            log.info("Maps browser %s unavailable: %s", make, e)
    return None


def _dismiss_consent(driver):
    from selenium.webdriver.common.by import By
    for xp in ("//button[.//span[contains(text(),'Reject all')]]", "//button[contains(., 'Reject all')]",
               "//button[contains(., 'Accept all')]"):
        try:
            for btn in driver.find_elements(By.XPATH, xp):
                if btn.is_displayed():
                    btn.click()
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


_READ_PAGE = r"""
const q = (s) => [...document.querySelectorAll(s)];
const txt = (e) => (e && (e.innerText || e.textContent) || '').replace(/[-]/g, '').trim();
const out = {};
const h1 = q('h1').map(txt).filter(Boolean);
out.name = h1[0] || '';
const item = (id) => { const e = document.querySelector(`[data-item-id="${id}"]`); return e ? txt(e) : ''; };
out.address = item('address');
const ph = q('[data-item-id^="phone"]')[0];
out.phone = ph ? txt(ph) : '';
const web = document.querySelector('a[data-item-id="authority"]');
out.website = web ? (web.getAttribute('href') || txt(web)) : '';
const cat = q('button[jsaction*="category"]').map(txt).filter(t => t && t.length < 80);
out.category = cat[0] || '';
const f7 = q('div.F7nice').map(txt).join(' ');
const rm = f7.match(/^\s*([0-9][.,][0-9])/);
out.rating = rm ? parseFloat(rm[1].replace(',', '.')) : null;
let count = null;
for (const e of q('[aria-label]')) {
  const m = (e.getAttribute('aria-label') || '').match(/^([0-9][0-9,.]*)\s+reviews?$/i);
  if (m) { count = parseInt(m[1].replace(/[,.]/g, '')); break; }
}
if (count === null) { const m = f7.match(/\(([0-9][0-9,.]*)\)/); if (m) count = parseInt(m[1].replace(/[,.]/g, '')); }
out.reviews = count;
const days = new Set();
for (const e of q('[aria-label*="Copy open hours"], table tr')) {
  const m = (e.getAttribute('aria-label') || txt(e)).match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)/);
  if (m) days.add(m[1]);
}
const hoursBtn = document.querySelector('[data-item-id="oh"], [aria-label^="Hours"], [aria-label*="open hours" i]');
const body = document.body.innerText;
out.hours_days = [...days];
out.hours_present = days.size > 0 || !!hoursBtn || /Open 24 hours|Closes [0-9]|Opens [0-9]/.test(body);
out.closed = (body.match(/(Permanently closed|Temporarily closed)/) || [''])[0];
out.claim = /Claim this business/i.test(body) || q('a[aria-label*="Claim this business" i]').length > 0;
out.actions = q('[data-item-id^="action:"]').map(e => (e.getAttribute('aria-label') || txt(e)).trim()).filter(Boolean);
out.plus_code = item('oloc');
out.url = location.href;
return out;
"""


def _read_place_page(driver):
    data = driver.execute_script(_READ_PAGE) or {}
    if not data.get("name"):
        return None
    place = {"_source": "maps_scrape", "displayName": {"text": data["name"]}, "googleMapsUri": data.get("url") or ""}
    if data.get("address"):
        place["formattedAddress"] = data["address"]
    if data.get("phone"):
        place["nationalPhoneNumber"] = data["phone"]
    if data.get("website"):
        site = data["website"]
        if "/url?" in site:   # Google redirect link: the real address is in q=
            site = urllib.parse.parse_qs(urlparse(site).query).get("q", [site])[0]
        place["websiteUri"] = site
    if data.get("category"):
        place["primaryTypeDisplayName"] = {"text": data["category"]}
    if data.get("rating") is not None:
        place["rating"] = float(data["rating"])
    if data.get("reviews") is not None:
        place["userRatingCount"] = int(data["reviews"])
    if data.get("hours_present"):
        place["regularOpeningHours"] = {"weekdayDays": data.get("hours_days") or []}
    place["businessStatus"] = ("CLOSED_PERMANENTLY" if "Permanently" in (data.get("closed") or "")
                               else "CLOSED_TEMPORARILY" if data.get("closed") else "OPERATIONAL")
    place["claimed"] = not data.get("claim")
    # "Find a table\nopentable.ca" -> "Find a table (opentable.ca)"
    place["actions"] = [re.sub(r"\s*\n\s*(.+)", r" (\1)", a).strip() for a in data.get("actions") or []]
    if data.get("plus_code"):
        place["plusCode"] = data["plus_code"]
    return place


def _wait_results(driver, timeout):
    from selenium.webdriver.common.by import By
    t0 = time.time()
    while time.time() - t0 < timeout:
        _dismiss_consent(driver)
        if "/maps/place/" in (driver.current_url or ""):
            time.sleep(1.5)
            return "place"
        if driver.find_elements(By.CSS_SELECTOR, "div[role='feed'] a[href*='/maps/place/']"):
            return "feed"
        body = driver.find_element(By.TAG_NAME, "body").text if driver.find_elements(By.TAG_NAME, "body") else ""
        if "can't find" in body or "No results found" in body:
            return "none"
        time.sleep(0.4)
    return "none"


def _open_place(driver, url, timeout):
    from selenium.webdriver.common.by import By
    driver.get(url)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if driver.find_elements(By.TAG_NAME, "h1") and any(h.text.strip() for h in driver.find_elements(By.TAG_NAME, "h1")):
            time.sleep(1.2)
            return True
        time.sleep(0.3)
    return False


def _from_maps(queries, domain, hints, timeout, driver=None, only_url=None):
    from selenium.webdriver.common.by import By
    own = driver is None
    driver = driver or _get_driver()
    if not driver:
        return None, "no_browser"
    try:
        driver.set_page_load_timeout(max(timeout, 15))
        if only_url:   # add what the page shows to a profile the API already found
            return (_read_place_page(driver) if _open_place(driver, only_url, timeout) else None), "found"
        for q in queries:
            driver.get(f"https://www.google.com/maps/search/{urllib.parse.quote(q)}?hl=en")
            kind = _wait_results(driver, timeout)
            candidates = []
            if kind == "place":
                place = _read_place_page(driver)
                if place:
                    how = _verify(place, domain, hints)
                    if how:
                        return dict(place, _matched_by=how), "matched_" + how
            elif kind == "feed":
                candidates = [a.get_attribute("href") for a in
                              driver.find_elements(By.CSS_SELECTOR, "div[role='feed'] a[href*='/maps/place/']")][:4]
            for href in candidates:
                if href and _open_place(driver, href, timeout):
                    place = _read_place_page(driver)
                    how = place and _verify(place, domain, hints)
                    if how:
                        return dict(place, _matched_by=how), "matched_" + how
        return None, "not_found"
    except Exception as e:  # noqa: BLE001
        log.warning("Google Maps lookup failed for %s: %s", domain, e)
        return None, f"error: {e}"
    finally:
        if own:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
def lookup(url, hints=None, api_key=None, remote=None, timeout=12):
    """Find and read the business's profile. ``remote(query)`` → (places, error)
    searches through the team server's key. Returns (place or None, note)."""
    domain = _clean_domain(url)
    if not domain:
        return None, "empty_query"
    hints = dict(hints or {}, domain=domain)
    queries = _queries(hints, domain)
    search = (lambda q: places_search(q, api_key)) if api_key else remote
    if search:
        place, note = _from_api(queries, search, domain, hints)
        if place:
            # The API does not say whether the profile is claimed or which booking
            # buttons it shows: read those from the public page when possible.
            try:
                page, _n = _from_maps([], domain, hints, timeout, only_url=place.get("googleMapsUri"))
            except Exception:  # noqa: BLE001
                page = None
            if page:
                place["claimed"] = page.get("claimed")
                place["actions"] = page.get("actions") or []
            return place, note
        if not note.startswith(("api_error", "no_key")) and note != "not_found":
            log.info("Places lookup for %s: %s", domain, note)
    return _from_maps(queries, domain, hints, timeout)


def scrape_google_maps(url_or_domain, timeout=12, hints=None, api_key=None, remote=None):
    """Kept for older callers: same as ``lookup``."""
    return lookup(url_or_domain, hints, api_key, remote, timeout)
