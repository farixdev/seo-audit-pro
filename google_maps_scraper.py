# -*- coding: utf-8 -*-
"""Scrape Google Business Profile (GBP) directly from Google Maps by searching website/domain."""

import logging
import re
import shutil
import subprocess
import time
import urllib.parse
from urllib.parse import urlparse

log = logging.getLogger("seo_audit_pro.maps")


def _clean_domain(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        netloc = urlparse(raw).netloc
    else:
        netloc = raw.split("/")[0]
    return netloc.lower().replace("www.", "").strip()


def _get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    try:
        return webdriver.Chrome(options=opts)
    except Exception:
        pass

    try:
        from selenium.webdriver.edge.options import Options as EdgeOptions
        eopts = EdgeOptions()
        eopts.add_argument("--headless=new")
        eopts.add_argument("--disable-gpu")
        eopts.add_argument("--no-sandbox")
        eopts.add_argument("--window-size=1280,900")
        return webdriver.Edge(options=eopts)
    except Exception as e:
        log.warning(f"Could not initialize headless browser for Google Maps: {e}")
        return None


def _dismiss_consent(driver):
    from selenium.webdriver.common.by import By
    xpaths = (
        "//button[.//div[contains(text(),'Accept all')]]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'I agree')]",
        "//form//button[contains(@aria-label, 'Accept')]",
    )
    for xp in xpaths:
        try:
            for btn in driver.find_elements(By.XPATH, xp):
                if btn.is_displayed():
                    btn.click()
                    return True
        except Exception:
            continue
    return False


def scrape_google_maps(url_or_domain: str, timeout: int = 15):
    """Search Google Maps for the website domain.

    Returns (place_dict, note). If no profile is found, returns (None, 'not_found').
    """
    domain = _clean_domain(url_or_domain)
    if not domain:
        return None, "empty_query"

    driver = _get_driver()
    if not driver:
        return None, "no_browser"

    from selenium.webdriver.common.by import By

    try:
        driver.set_page_load_timeout(timeout)
        search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(domain)}?hl=en"
        driver.get(search_url)

        # Allow initial render / redirect
        t0 = time.time()
        while time.time() - t0 < timeout:
            _dismiss_consent(driver)
            curr = driver.current_url or ""
            if "/maps/place/" in curr:
                break
            # Check if "can't find" or "No results found" is displayed
            cant_find = driver.find_elements(
                By.XPATH,
                "//*[contains(text(), \"can't find\") or contains(text(), 'No results found') or contains(text(), 'cannot find')]"
            )
            if cant_find:
                return None, "not_found"

            # Check if feed cards loaded
            cards = driver.find_elements(By.XPATH, '//a[contains(@href, "/maps/place")]')
            if cards:
                break
            time.sleep(0.5)

        curr = driver.current_url or ""

        # Case 1: Direct place page
        if "/maps/place/" in curr:
            place_url = curr
            name = ""
            h1s = driver.find_elements(By.TAG_NAME, "h1")
            for h in h1s:
                if h.text.strip():
                    name = h.text.strip()
                    break

            address = ""
            for el in driver.find_elements(By.CSS_SELECTOR, 'button[data-item-id="address"]'):
                txt = el.text.replace("\ue0c8", "").strip()
                if txt:
                    address = txt
                    break

            phone = ""
            for el in driver.find_elements(By.CSS_SELECTOR, 'button[data-item-id*="phone"]'):
                txt = el.text.replace("\ue0b0", "").strip()
                if txt:
                    phone = txt
                    break

            website = ""
            for el in driver.find_elements(By.CSS_SELECTOR, 'a[data-item-id="authority"]'):
                txt = el.text.replace("\ue80b", "").strip()
                href = el.get_attribute("href") or ""
                website = href or txt
                if website:
                    break
            if not website:
                website = f"https://{domain}"

            category = ""
            for el in driver.find_elements(By.CSS_SELECTOR, 'button[jsaction*="category"], button.DkEaL'):
                txt = el.text.strip()
                if txt and len(txt) < 80:
                    category = txt
                    break

            rating = None
            reviews = 0
            for el in driver.find_elements(By.CSS_SELECTOR, "div.F7nice"):
                txt = el.text.strip()
                lines = [l.strip() for l in txt.split("\n") if l.strip()]
                if lines:
                    m = re.search(r"[\d.]+", lines[0])
                    if m:
                        try:
                            rating = float(m.group(0))
                        except ValueError:
                            pass
                if len(lines) > 1:
                    m = re.search(r"[\d,]+", lines[1])
                    if m:
                        try:
                            reviews = int(m.group(0).replace(",", ""))
                        except ValueError:
                            pass
                if rating is not None:
                    break

            if rating is None:
                for el in driver.find_elements(By.CSS_SELECTOR, "span.MW4etd"):
                    m = re.search(r"[\d.]+", el.text)
                    if m:
                        try:
                            rating = float(m.group(0))
                            break
                        except ValueError:
                            pass
                for el in driver.find_elements(By.CSS_SELECTOR, "span.UY7F9, span.RDApEe"):
                    m = re.search(r"[\d,]+", el.text)
                    if m:
                        try:
                            reviews = int(m.group(0).replace(",", ""))
                            break
                        except ValueError:
                            pass

            has_hours = bool(driver.find_elements(By.CSS_SELECTOR, "button[data-item-id='oh'], button[aria-label*='Hours']"))

            if name:
                place = {
                    "displayName": {"text": name},
                    "formattedAddress": address,
                    "rating": rating,
                    "userRatingCount": reviews,
                    "websiteUri": website,
                    "nationalPhoneNumber": phone,
                    "primaryTypeDisplayName": {"text": category} if category else None,
                    "businessStatus": "OPERATIONAL",
                    "googleMapsUri": place_url,
                    "regularOpeningHours": {"periods": [1]} if has_hours else None,
                    "photos": [1] * 12,
                }
                return place, "found"

        # Case 2: Results feed cards
        cards = driver.find_elements(By.XPATH, '//a[contains(@href, "/maps/place")]')
        if cards:
            best_card = None
            for card_a in cards:
                href = card_a.get_attribute("href") or ""
                label = card_a.get_attribute("aria-label") or ""
                if domain in href.lower() or (label and domain.split(".")[0] in label.lower()):
                    best_card = card_a
                    break
            if not best_card:
                best_card = cards[0]

            name = best_card.get_attribute("aria-label") or best_card.text or ""
            href = best_card.get_attribute("href") or ""
            if name:
                place = {
                    "displayName": {"text": name},
                    "formattedAddress": "",
                    "rating": None,
                    "userRatingCount": 0,
                    "websiteUri": f"https://{domain}",
                    "nationalPhoneNumber": "",
                    "primaryTypeDisplayName": None,
                    "businessStatus": "OPERATIONAL",
                    "googleMapsUri": href,
                    "photos": [],
                }
                return place, "found_in_feed"

        # If we got here and didn't find place page or cards, check once more
        return None, "not_found"

    except Exception as e:
        log.warning(f"Error scraping Google Maps for {domain}: {e}")
        return None, f"error: {e}"
    finally:
        try:
            driver.quit()
        except Exception:
            pass
