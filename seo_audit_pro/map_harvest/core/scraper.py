"""Google Maps scraping engine.

Design: **card-first, detail-on-demand.**

The Google Maps results feed already carries name, rating, category, address,
phone, website and (encoded in each place URL) coordinates + a stable Place ID.
We parse all of that straight from the feed HTML in a single pass per scroll
— no per-business browser tab, no page load per row. That is the whole speed
and reliability win over the old "open every listing in a new tab" approach.

We only open individual place pages when the user explicitly asks for a field
that the feed cannot provide (weekly Hours, or Review snippets). That detail
pass also upgrades address/category/rating to their full-detail values.
"""

import os
import re
import sys
import threading
import time
import urllib.parse

from core.distutils_compat import *  # noqa: F401,F403 — must load before uc

import undetected_chromedriver as uc
from PyQt5.QtCore import QThread, pyqtSignal
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.parse import parse_feed_html, parse_place_url, place_key
from core.enrich import enrich_website, ENRICH_KEYS
from core import filters as recfilters

# ── Timing (seconds) ─────────────────────────────────────────────────────────
T_SEARCH_LOAD = 2.0
T_DETAIL_LOAD = 0.5
T_AFTER_SCROLL = 0.5

# Fields that cannot be read from a feed card — they force a detail-page visit.
DETAIL_ONLY_FIELDS = ("hours", "review_1", "review_2", "review_3", "claim_this_business")
# Card-derived fields that a detail visit can improve when one happens anyway.
UPGRADEABLE_FIELDS = ("address", "category", "rating", "review_count")
# Fields that require fetching the business website (HTTP, not the browser).
ENRICH_FIELDS = tuple(ENRICH_KEYS)


# ── Chrome / driver ──────────────────────────────────────────────────────────
def _find_chrome_binary() -> str:
    """Return path to Chrome binary if installed in standard macOS / Linux locations, else ''."""
    if sys.platform == "darwin":
        for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ):
            if os.path.isfile(p):
                return p
    elif sys.platform.startswith("linux"):
        import shutil
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                return found
    return ""


def _chrome_major_version() -> int:
    version_re = re.compile(r"^(\d+)\.\d+\.\d+\.\d+$")
    if sys.platform.startswith("win"):
        for app_dir in (
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application"),
        ):
            if not os.path.isdir(app_dir):
                continue
            for name in os.listdir(app_dir):
                m = version_re.match(name)
                if m:
                    return int(m.group(1))
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
            version, _ = winreg.QueryValueEx(key, "version")
            m = re.match(r"(\d+)\.", str(version))
            if m:
                return int(m.group(1))
        except Exception:
            pass
    elif sys.platform == "darwin":
        # Check Info.plist in /Applications/Google Chrome.app
        for plist_path in (
            "/Applications/Google Chrome.app/Contents/Info.plist",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/Info.plist"),
        ):
            if os.path.isfile(plist_path):
                try:
                    import plistlib
                    with open(plist_path, "rb") as fp:
                        plist = plistlib.load(fp)
                    ver = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion") or ""
                    m = re.match(r"(\d+)\.", str(ver))
                    if m:
                        return int(m.group(1))
                except Exception:
                    pass
        # Fallback: invoke binary with --version
        bin_path = _find_chrome_binary()
        if bin_path:
            try:
                import subprocess
                out = subprocess.check_output([bin_path, "--version"], text=True, timeout=2)
                m = re.search(r"(\d+)\.", out)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
    return 0


def get_driver(headless: bool = False):
    options = uc.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-popup-blocking")
    # Force English UI so our selectors and status strings stay predictable.
    options.add_experimental_option("prefs", {"intl.accept_languages": "en-US,en"})
    options.page_load_strategy = "eager"

    binary = _find_chrome_binary()
    if binary:
        options.binary_location = binary

    major = _chrome_major_version()
    kwargs = {"options": options, "use_subprocess": True}
    if major:
        kwargs["version_main"] = major

    try:
        driver = uc.Chrome(**kwargs)
    except Exception as e:
        err_str = str(e)
        if sys.platform == "darwin":
            if "Permission denied" in err_str or "chromedriver" in err_str.lower() or isinstance(e, OSError):
                raise RuntimeError(
                    "macOS Gatekeeper blocked undetected-chromedriver. "
                    "Run this command in Terminal to unblock it:\n"
                    "xattr -cr ~/Library/Application\\ Support/undetected_chromedriver"
                ) from e
            if not _find_chrome_binary():
                raise RuntimeError(
                    "Google Chrome was not found on your Mac. "
                    "Please install Google Chrome from https://www.google.com/chrome "
                    "and place it in /Applications."
                ) from e
        raise

    driver.set_window_size(1280, 900)
    driver.set_page_load_timeout(30)
    return driver


def _search_url(domain: str, area: str) -> str:
    query = f"{domain} in {area}"
    # hl=en keeps the UI/end-of-list strings in English.
    return f"https://www.google.com/maps/search/{urllib.parse.quote(query)}?hl=en"


# ── Consent / blocking ───────────────────────────────────────────────────────
def _dismiss_consent_screen(driver) -> bool:
    """Click through Google's cookie/consent interstitial if it appears.

    On non-US IPs Google shows a consent page *before* the Maps feed, so the
    feed selectors otherwise find nothing because the feed isn't there yet.
    """
    clicked = False
    xpaths = (
        "//button[.//div[contains(text(),'Accept all')]]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'Reject all')]",
        "//button[contains(., 'I agree')]",
        "//form//button[contains(@aria-label, 'Accept')]",
    )
    for xp in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                time.sleep(0.8)
                break
        except Exception:
            continue
    return clicked


def _page_blocked_reason(driver) -> str:
    """Human-readable reason if Google is blocking/challenging us, else ''."""
    try:
        url = driver.current_url or ""
        src = (driver.page_source or "")[:20000]
    except Exception:
        return ""

    if "consent.google.com" in url:
        return "Stuck on Google's cookie-consent page — the consent click did not go through."
    if "sorry/index" in url or "unusual traffic" in src.lower():
        return "Google is showing an 'unusual traffic' / CAPTCHA page — the automated browser got flagged."
    if "recaptcha" in src.lower() and "maps" not in url:
        return "Google is showing a reCAPTCHA challenge instead of Maps."
    return ""


# ── Feed detection / scrolling ───────────────────────────────────────────────
def _get_feed(driver):
    selectors = (
        'div[role="feed"]',
        'div[aria-label*="Results for"]',
        'div[aria-label*="Search results"]',
    )
    for sel in selectors:
        try:
            for feed in driver.find_elements(By.CSS_SELECTOR, sel):
                if feed.is_displayed():
                    return feed
        except Exception:
            continue
    try:
        return WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//div[@role='feed' or contains(@aria-label, 'Results for') "
                "or contains(@aria-label, 'Search results')]",
            ))
        )
    except Exception:
        return None


def _is_loading(driver) -> bool:
    """True if a *visible* loading/progress indicator is on the page."""
    try:
        for sel in ('div[role="progressbar"]', 'div.section-loading', 'div.loading'):
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    style = el.get_attribute("style") or ""
                    if "opacity: 0" in style or "opacity:0" in style:
                        continue
                    size = el.size
                    if size and (size.get("width", 0) == 0 or size.get("height", 0) == 0):
                        continue
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _is_end_of_list(driver) -> bool:
    try:
        if _is_loading(driver):
            return False
        return bool(driver.find_elements(
            By.XPATH,
            "//*[contains(text(), \"You've reached the end\") "
            "or contains(text(), 'reached the end') "
            "or contains(text(), 'end of the list')]",
        ))
    except Exception:
        return False


def _feed_html(driver) -> str:
    feed = _get_feed(driver)
    if feed is None:
        return ""
    try:
        return feed.get_attribute("outerHTML") or ""
    except StaleElementReferenceException:
        feed = _get_feed(driver)
        return (feed.get_attribute("outerHTML") or "") if feed else ""
    except Exception:
        return ""


# Resolves the scrollable results container in-page: the role="feed" div, with
# the same aria-label fallbacks _get_feed() uses.
_JS_FEED = (
    "function feedEl(){return document.querySelector('div[role=\"feed\"]')"
    "||document.querySelector('div[aria-label^=\"Results for\"]')"
    "||document.querySelector('div[aria-label*=\"Search results\"]');}"
)


def _feed_card_count(driver) -> int:
    """Cheap count of place links currently in the feed (for growth detection)."""
    try:
        return int(driver.execute_script(
            _JS_FEED + "const f=feedEl();"
            "return f?f.querySelectorAll('a[href*=\"/maps/place\"]').length:0;"
        ) or 0)
    except Exception:
        return 0


def _nudge_feed(driver) -> None:
    """Fire Maps' lazy-load on the results feed.

    The dispatched WheelEvent is what actually triggers loading — a bare
    ``scrollTop`` change is ignored by Maps, and ``scrollIntoView`` fights the
    scroll position and stalls pagination. Target the feed container directly;
    the "div with the most links" heuristic latches onto an inner wrapper once
    results load and freezes.
    """
    try:
        driver.execute_script(
            _JS_FEED +
            """
            const f = feedEl();
            if (f) {
                f.scrollTop = f.scrollHeight;
                f.dispatchEvent(new WheelEvent('wheel',
                    {deltaY: 1500, bubbles: true, cancelable: true}));
            }
            """
        )
    except Exception:
        pass


def _scroll_for_more(driver, timeout: float = 5.0) -> bool:
    """Scroll the feed and wait for new cards to load; True if the feed grew.

    Maps often needs repeated wheel events, so we keep nudging while waiting.
    """
    before = _feed_card_count(driver)
    _nudge_feed(driver)
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(0.4)
        if _feed_card_count(driver) > before:
            return True
        if _is_end_of_list(driver):
            return False
        _nudge_feed(driver)
    return _feed_card_count(driver) > before


# ── Detail-page extraction (only used when Hours/Reviews are requested) ───────
def _first_text(scope, selectors: str) -> str:
    for sel in selectors.split(", "):
        try:
            el = scope.find_element(By.CSS_SELECTOR, sel.strip())
            text = (el.text or el.get_attribute("textContent") or "").strip()
            if text:
                return " ".join(text.split())
        except Exception:
            continue
    return ""


def _first_attr(scope, selectors: str, attr: str) -> str:
    for sel in selectors.split(", "):
        try:
            el = scope.find_element(By.CSS_SELECTOR, sel.strip())
            val = (el.get_attribute(attr) or "").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _xpath_text(driver, xpaths: list) -> str:
    for xp in xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            text = (el.text or el.get_attribute("textContent") or "").strip()
            if text:
                return " ".join(text.split())
        except Exception:
            continue
    return ""


def _element_text(el) -> str:
    text = (el.text or el.get_attribute("textContent") or "").strip()
    return " ".join(text.split()) if text else ""


def _extract_category(driver) -> str:
    for sel in ("button[jsaction*='category']", "button.DkEaL[jsaction*='category']"):
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                text = _element_text(btn)
                if text and len(text) < 80:
                    return text
        except Exception:
            continue
    return _xpath_text(driver, ["//button[contains(@jsaction,'category')]"])


def _find_hours_button(driver):
    for sel in (
        "button[data-item-id='oh']",
        "button[aria-label*='Hours']",
        "button[data-tooltip*='hours']",
        "button[data-tooltip*='Hours']",
    ):
        try:
            return driver.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            continue
    return None


def _hours_from_label(label: str) -> str:
    if not label:
        return ""
    label = label.strip()
    for prefix in ("Hours:", "Opening hours:", "Hours "):
        if label.lower().startswith(prefix.lower()):
            return label[len(prefix):].strip()
    return label


def _extract_hours(driver) -> str:
    btn = _find_hours_button(driver)
    if btn is None:
        return _first_text(driver, "button[data-item-id='oh'] div.Io6YTe, div[aria-label*='Hours']")

    aria = (btn.get_attribute("aria-label") or "").strip()
    hours = _element_text(btn) or _hours_from_label(aria)
    try:
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.25)
        rows = []
        for row in driver.find_elements(By.CSS_SELECTOR, "table.eK4R0e tr, tr.y0skZc"):
            cells = [_element_text(c) for c in row.find_elements(By.CSS_SELECTOR, "td, th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(": ".join(cells) if len(cells) > 1 else cells[0])
        if rows:
            hours = "; ".join(rows)
    except Exception:
        pass
    return hours


def _extract_claim_this_business(driver) -> str:
    """Check if 'Claim this business' is present on the place detail page.

    Returns 'Yes' if the business has the 'Claim this business' option (unclaimed),
    or 'No' if it is claimed / does not have the option.
    """
    # 1. Quick check for common selectors and attributes
    for sel in (
        "[data-item-id='merchant']",
        "a[href*='business.google.com/create']",
        "a[href*='business.google.com']",
        "button[aria-label*='Claim this business' i]",
        "a[aria-label*='Claim this business' i]",
    ):
        try:
            if driver.find_elements(By.CSS_SELECTOR, sel):
                return "Yes"
        except Exception:
            continue

    # 2. Check for div.Io6YTe containing 'Claim this business'
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "div.Io6YTe"):
            txt = (el.text or el.get_attribute("textContent") or "").strip().lower()
            if "claim this business" in txt:
                return "Yes"
    except Exception:
        pass

    # 3. Fallback: XPath check for text or aria-label containing 'claim this business'
    try:
        els = driver.find_elements(
            By.XPATH,
            "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim this business') "
            "or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim this business')]"
        )
        if els:
            return "Yes"
    except Exception:
        pass

    return "No"


def _open_reviews_tab(driver) -> bool:
    for sel in ('button[aria-label*="Reviews"]', 'button[data-tab-index="1"]'):
        try:
            for tab in driver.find_elements(By.CSS_SELECTOR, sel):
                label = (tab.get_attribute("aria-label") or tab.text or "").lower()
                if "review" in label:
                    driver.execute_script("arguments[0].click();", tab)
                    time.sleep(0.35)
                    return True
        except Exception:
            continue
    return False


def _extract_reviews(driver, count: int = 3) -> list:
    reviews = []
    if not _open_reviews_tab(driver):
        return reviews
    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.jftiE, div[data-review-id]"))
        )
    except Exception:
        return reviews
    for block in driver.find_elements(By.CSS_SELECTOR, "div.jftiE, div[data-review-id]")[:count]:
        try:
            author = _first_text(block, "div.d4r55, span.dwiWPf")
            rating = _first_attr(block, "span.kvMYJc, span[role='img']", "aria-label")
            date = _first_text(block, "span.rsqaWe, span.dehysf")
            body = _first_text(block, "span.wiI7pd, div.MyEned")
            parts = [p for p in (author, rating, date, body) if p]
            if parts:
                reviews.append(" | ".join(parts))
        except Exception:
            continue
    return reviews


def _extract_rating_and_count(driver) -> tuple:
    rating, count = "", ""
    for el in driver.find_elements(By.CSS_SELECTOR, "div.F7nice, div.fontBodyMedium"):
        aria = el.get_attribute("aria-label") or ""
        if "star" in aria.lower() or "review" in aria.lower():
            rm = re.search(r"([\d.]+)\s*star", aria, re.I)
            if rm and not rating:
                rating = rm.group(1)
            cm = re.search(r"([\d,]+)\s*review", aria, re.I)
            if cm and not count:
                count = cm.group(1).replace(",", "")
    if not count:
        for el in driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='review'], span[aria-label*='review']"):
            aria = el.get_attribute("aria-label") or ""
            cm = re.search(r"([\d,]+)\s*review", aria, re.I)
            if cm:
                count = cm.group(1).replace(",", "")
                break
    return rating, count


def _extract_detail(driver, href: str, fields: list) -> dict:
    """Open a place page and pull the detail-only + upgradeable fields."""
    out = {}
    try:
        driver.get(href)
        WebDriverWait(driver, 8).until(EC.presence_of_element_located((
            By.CSS_SELECTOR, "h1.DUwDvf, h1.fontHeadlineLarge, h1[class*='fontHeadline']",
        )))
        time.sleep(T_DETAIL_LOAD)

        if "address" in fields:
            out["address"] = _first_text(
                driver,
                "button[data-item-id='address'] div.Io6YTe, "
                "button[data-tooltip='Copy address'] .Io6YTe",
            )
        if "phone" in fields:
            phone = _first_text(
                driver,
                "button[data-item-id^='phone'] .Io6YTe, "
                "button[data-tooltip='Copy phone number'] .Io6YTe",
            )
            if not phone:
                tel = _first_attr(driver, "a[href^='tel:']", "href")
                phone = tel.replace("tel:", "").strip() if tel else ""
            out["phone"] = phone
        if "website" in fields:
            out["website"] = _first_attr(
                driver, "a[data-item-id='authority'], a[aria-label*='website']", "href",
            )
        if "category" in fields:
            out["category"] = _extract_category(driver)
        if "rating" in fields or "review_count" in fields:
            rating, count = _extract_rating_and_count(driver)
            if rating:
                out["rating"] = rating
            if count:
                out["review_count"] = count
        if "hours" in fields:
            out["hours"] = _extract_hours(driver)
        if "claim_this_business" in fields:
            out["claim_this_business"] = _extract_claim_this_business(driver)
        if any(f in fields for f in ("review_1", "review_2", "review_3")):
            reviews = _extract_reviews(driver, count=3)
            for i, key in enumerate(("review_1", "review_2", "review_3")):
                if key in fields:
                    out[key] = reviews[i] if i < len(reviews) else ""
    except Exception as e:
        out["_error"] = str(e)
    return {k: v for k, v in out.items() if v}


# ── Record assembly ──────────────────────────────────────────────────────────
def _card_to_record(card: dict, fields: list, domain: str, area: str) -> dict:
    record = {f: card.get(f, "") for f in fields}
    record["_domain"] = domain
    record["_area"] = area
    record["_href"] = card.get("_href", "")
    if "domain" in fields:
        record["domain"] = domain
    if "area" in fields:
        record["area"] = area
    return record


def _debug_dump(driver, tag: str) -> None:
    try:
        os.makedirs("debug", exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", tag)[:40]
        driver.save_screenshot(f"debug/{safe}.png")
        with open(f"debug/{safe}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("debug dump failed:", e)


# ── Main per-(domain, area) scrape ───────────────────────────────────────────
def _detail_wants(card: dict, fields: list, spec: dict) -> list:
    """Which detail-page fields to fetch for this card (empty = no detail visit)."""
    want = [f for f in fields if f in DETAIL_ONLY_FIELDS]
    if ("phone" in fields or recfilters.needs_phone(spec)) and not card.get("phone"):
        want.append("phone")
    # We need the website URL to enrich (email/socials), even if the user didn't
    # ask for the Website column itself.
    enrich_needed = any(f in fields for f in ENRICH_FIELDS) or recfilters.needs_email(spec)
    if ("website" in fields or enrich_needed) and not card.get("website"):
        want.append("website")
    if ("review_count" in fields or recfilters.needs_reviews(spec)) and not card.get("review_count"):
        want.append("review_count")
    if "rating" in fields and not card.get("rating"):
        want.append("rating")
    if recfilters.needs_claim(spec) and "claim_this_business" not in want:
        want.append("claim_this_business")
    # Opportunistically upgrade address/category if a visit is already happening.
    if want:
        for f in ("address", "category"):
            if f in fields:
                want.append(f)
    return list(dict.fromkeys(want))


def _enrich_wants(card: dict, fields: list, spec: dict) -> list:
    want = [f for f in fields if f in ENRICH_FIELDS]
    if recfilters.needs_email(spec) and "email" not in want:
        want.append("email")
    return want if card.get("website") else []


def scrape_domain_progressive(
    driver, domain: str, area: str, fields: list, worker,
    max_results: int = 0, filters: dict = None,
) -> tuple:
    """Scrape one (domain, area). Returns (matched_count, completed_naturally)."""
    spec = recfilters.normalize_spec(filters)
    search_url = _search_url(domain, area)
    driver.get(search_url)
    time.sleep(1.0)

    if _dismiss_consent_screen(driver):
        worker.log_signal.emit("Dismissed cookie-consent screen...", "active")
        time.sleep(1.0)
        if "consent.google.com" in driver.current_url:
            driver.get(search_url)
            time.sleep(1.0)
            _dismiss_consent_screen(driver)
            time.sleep(1.0)

    reason = _page_blocked_reason(driver)
    if reason:
        _debug_dump(driver, f"blocked_{domain}_{area}")
        worker.log_signal.emit(f'"{domain} in {area}" — {reason} See debug/ folder.', "done")
        return 0, True

    try:
        WebDriverWait(driver, 12).until(lambda d: _get_feed(d) is not None)
    except Exception:
        reason = _page_blocked_reason(driver)
        _debug_dump(driver, f"nofeed_{domain}_{area}")
        msg = reason or (
            "no results feed appeared. Saved debug/nofeed_*.png and .html — "
            "check what Chrome actually loaded."
        )
        worker.log_signal.emit(f'"{domain} in {area}" — {msg}', "done")
        return 0, True

    target = max_results if max_results > 0 else 10 ** 9
    # Filters that can only be decided after a detail visit / enrichment mean we
    # may reject some candidates, so over-collect to still reach the target.
    strict = (recfilters.needs_phone(spec) or recfilters.needs_email(spec)
              or recfilters.needs_reviews(spec) or recfilters.needs_claim(spec))
    collect_cap = target if not strict else min(target * 4, target + 300)

    seen = set()
    pending = []        # (card, href) that still need a Maps detail visit
    matched = 0
    emitted = 0
    enrich_active = bool([f for f in fields if f in ENRICH_FIELDS]) or recfilters.needs_email(spec)

    def emit(card: dict) -> None:
        nonlocal emitted
        emitted += 1
        record = _card_to_record(card, fields, domain, area)
        worker.result_signal.emit(record)
        worker.progress_signal.emit(worker._progress_base + emitted)
        name = (card.get("name") or "Unknown")[:50]
        worker.log_signal.emit(f"#{worker._progress_base + emitted}  {name}", "done")

    def do_enrich(card: dict) -> None:
        want = _enrich_wants(card, fields, spec)
        if not want:
            return
        contacts = enrich_website(card.get("website", ""), fields=tuple(want))
        for k, v in contacts.items():
            if v:
                card[k] = v

    worker.log_signal.emit(f'Scanning "{domain} in {area}"...', "active")

    stall = 0
    max_stall = 6
    last_growth = time.time()
    no_growth_timeout = 120
    ended = False

    while worker._running and matched < target and (matched + len(pending)) < collect_cap:
        worker._wait_if_paused()
        if not worker._running:
            return matched, False

        cards = parse_feed_html(_feed_html(driver))
        new_this_round = 0
        for card in cards:
            if card.get("_sponsored"):
                continue
            key = card.get("_key")
            if not key or key in seen:
                continue
            seen.add(key)
            new_this_round += 1

            if not recfilters.cheap_pass(card, spec):
                continue  # rejected on card-level data; no further work

            if _detail_wants(card, fields, spec):
                pending.append((card, card.get("_href", "")))
            else:
                # No Maps detail needed — enrich (HTTP, safe) then decide now.
                # Enrichment can take seconds per card, so honour Pause/Stop here
                # rather than making the user wait out the whole batch.
                worker._wait_if_paused()
                if not worker._running:
                    break
                if enrich_active:
                    do_enrich(card)
                if recfilters.full_pass(card, spec):
                    emit(card)
                    matched += 1
            if matched >= target or (matched + len(pending)) >= collect_cap:
                break

        if not worker._running:
            return matched, False
        if matched >= target or (matched + len(pending)) >= collect_cap:
            break

        if new_this_round:
            last_growth = time.time()
            stall = 0
        else:
            stall += 1
            if seen:
                worker.log_signal.emit(
                    f'Scanned {len(seen)}, matched {matched} so far...', "active")

        if _is_end_of_list(driver) and new_this_round == 0:
            ended = True
            break
        if stall >= max_stall or time.time() - last_growth > no_growth_timeout:
            ended = True
            break

        if _get_feed(driver) is None:
            driver.get(search_url)
            time.sleep(T_SEARCH_LOAD)
            continue
        _scroll_for_more(driver)

    if not seen:
        _debug_dump(driver, f"noresults_{domain}_{area}")
        worker.log_signal.emit(
            f'"{domain} in {area}" — no listings found in the results feed. '
            f'See debug/ folder.', "done",
        )
        return 0, True

    if not worker._running:
        return matched, False

    # Detail pass — for candidates that needed a Maps place-page visit.
    total = len(pending)
    for idx, (card, href) in enumerate(pending, 1):
        if not worker._running or matched >= target:
            break
        worker._wait_if_paused()
        worker.log_signal.emit(
            f'Fetching details {idx}/{total}: {card.get("name", "")[:40]}', "active")
        want = _detail_wants(card, fields, spec)
        detail = _extract_detail(driver, href, want)
        for f in want:
            if detail.get(f) and (
                f in DETAIL_ONLY_FIELDS or f in UPGRADEABLE_FIELDS or not card.get(f)
            ):
                card[f] = detail[f]
        if enrich_active:
            do_enrich(card)
        if recfilters.full_pass(card, spec):
            emit(card)
            matched += 1

    if matched == 0 and recfilters.is_active(spec):
        worker.log_signal.emit(
            f'"{domain} in {area}" — scanned {len(seen)} but none matched your filters.',
            "done",
        )

    completed = ended or (max_results > 0 and matched >= max_results)
    return matched, completed


# ── Qt worker thread (interface unchanged) ───────────────────────────────────
class ScrapeWorker(QThread):
    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    result_signal = pyqtSignal(dict)
    # domain, area, count, max_results, hit_limit
    domain_finished_signal = pyqtSignal(str, str, int, int, bool)
    done_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    paused_signal = pyqtSignal(bool)

    def __init__(
        self,
        domains: list,
        areas,
        fields: list,
        headless: bool = False,
        max_results: int = 0,
        filters: dict = None,
        use_tabs: bool = True,  # kept for backward compatibility (unused)
    ):
        super().__init__()
        self.domains = domains
        self.areas = areas if isinstance(areas, list) else [areas]
        self.fields = fields
        self.headless = headless
        self.max_results = max(0, max_results)
        self.filters = filters or {}
        self.use_tabs = use_tabs
        self._driver = None   # set in run(); used by abort() on app close
        self._running = True
        self._paused = False
        self._pause_lock = threading.Event()
        self._pause_lock.set()
        self._continue_event = threading.Event()
        self._continue_event.set()
        self._total_collected = 0
        self._progress_base = 0

    def continue_next_domain(self):
        self._continue_event.set()

    def stop(self):
        self._running = False
        self._pause_lock.set()
        self._continue_event.set()

    def abort(self):
        """Stop and force the browser closed (used when the app is closing).

        Selenium calls block the worker thread, so a co-operative stop can't
        interrupt an in-flight page load. Quitting the driver from the outside
        makes that call fail fast, letting run() unwind and the thread exit
        instead of hanging with an orphaned chrome.exe.
        """
        self.stop()
        driver = self._driver
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def pause(self):
        self._paused = True
        self._pause_lock.clear()
        self.paused_signal.emit(True)

    def resume(self):
        self._paused = False
        self._pause_lock.set()
        self.paused_signal.emit(False)

    def _wait_if_paused(self):
        while self._running and not self._pause_lock.is_set():
            time.sleep(0.15)

    def run(self):
        driver = None
        try:
            driver = get_driver(headless=self.headless)
            self._driver = driver
            self._total_collected = 0
            tasks = [(d, a) for d in self.domains for a in self.areas]
            multi = len(tasks) > 1

            for domain, area in tasks:
                if not self._running:
                    break

                if multi:
                    limit = self.max_results
                elif self.max_results > 0:
                    limit = self.max_results - self._total_collected
                    if limit <= 0:
                        break
                else:
                    limit = 0

                self._progress_base = 0 if multi else self._total_collected

                count, completed = scrape_domain_progressive(
                    driver, domain, area, self.fields, self,
                    max_results=limit, filters=self.filters,
                )
                self._total_collected += count

                if not self._running:
                    break

                if not multi:
                    if self.max_results > 0 and self._total_collected >= self.max_results:
                        break
                    continue

                label = f"{domain} in {area}"
                if not completed and count:
                    self.log_signal.emit(f'"{label}" stopped early — {count} collected', "done")

                if count == 0:
                    self.log_signal.emit(f'No results for "{label}"', "done")
                elif self.max_results > 0 and count >= self.max_results:
                    self.log_signal.emit(f'"{label}" done — {count} of {self.max_results}', "done")
                else:
                    self.log_signal.emit(f'"{label}" done — {count} (no more results)', "done")

                self._continue_event.clear()
                hit_limit = self.max_results > 0 and count >= self.max_results
                self.domain_finished_signal.emit(domain, area, count, self.max_results, hit_limit)
                while self._running and not self._continue_event.wait(timeout=0.2):
                    pass

        except Exception as e:
            self.error_signal.emit(str(e))

        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self._driver = None
            self.done_signal.emit()
