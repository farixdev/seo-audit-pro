"""Website enrichment — pull email + social links from a business website.

Uses the standard library only (urllib), so there is no extra dependency to
install. The extraction core, `extract_contacts(html, base_url)`, is a pure
function and is unit-tested offline in `tests/test_enrich.py`.

Reliability note: this reads third-party business websites, which vary wildly.
Extraction is best-effort — a missing email means the site didn't expose one in
a machine-readable way, not that the scraper failed.
"""

from __future__ import annotations

import gzip
import io
import re
import urllib.error
import urllib.parse
import urllib.request

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Domain substrings that are never a real business contact (tracking, CDNs,
# platform system addresses, placeholders). Matched anywhere in the domain.
_JUNK_DOMAIN_SUBSTR = (
    "sentry", "wixpress", "wix.com", "godaddy", "squarespace", "shopify",
    "cloudflare", "googleapis", "gstatic", "schema.org", "w3.org", "jquery",
    "bootstrapcdn", "fontawesome", "example.", "yourdomain", "domain.com",
    "email.com", "test.com", "sentry.io", "mailerlite", "wordpress.org",
)
# File extensions that mean the "email" was really an asset URL fragment.
_EMAIL_ASSET_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js|ico|woff2?)$", re.I)

_SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|pub)/[A-Za-z0-9._%\-/?=&]+", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9._%\-/?=&]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/[A-Za-z0-9._%\-/?=&@]+", re.I),
}

# Social URLs that are share/intent widgets, not the business's own profile.
# NOTE: do not reject on a bare "=" — legitimate profiles use query strings
# (e.g. facebook.com/profile.php?id=..., youtube.com/channel/x?sub=1).
_SOCIAL_JUNK = re.compile(
    r"(?:/sharer|/share\.php|/intent/|/plugins/|/dialog/|/tr\?"
    r"|facebook\.com/(?:tr|plugins|sharer)|/hashtag/)",
    re.I,
)

ENRICH_KEYS = ("email", "facebook", "instagram", "linkedin", "twitter", "youtube")


def _fetch(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1_500_000)  # cap to ~1.5MB
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
    charset = "utf-8"
    return raw.decode(charset, errors="replace")


def _clean_email(email: str) -> str:
    email = email.strip().strip(".").lower()
    if not _EMAIL_RE.fullmatch(email):
        return ""
    if _EMAIL_ASSET_RE.search(email):
        return ""
    local, _, domain = email.partition("@")
    if any(j in domain for j in _JUNK_DOMAIN_SUBSTR):
        return ""
    # Long hex local parts are Sentry/analytics tracking IDs, not people.
    if re.fullmatch(r"[0-9a-f]{20,}", local):
        return ""
    # A real address has at least one letter in the local part.
    if not re.search(r"[a-z]", local):
        return ""
    return email


def extract_contacts(html: str, base_url: str = "") -> dict:
    """Pure extraction of contacts from a page's HTML. No network."""
    out = {k: "" for k in ENRICH_KEYS}
    if not html:
        return out

    site_host = urllib.parse.urlparse(base_url).netloc.lower()
    if site_host.startswith("www."):
        site_host = site_host[4:]

    # ── emails ──
    found = []
    # Prefer explicit mailto: links.
    for m in re.findall(r'mailto:([^"\'>?\s]+)', html, re.I):
        e = _clean_email(urllib.parse.unquote(m))
        if e and _EMAIL_RE.fullmatch(e):
            found.append(e)
    for m in _EMAIL_RE.findall(html):
        e = _clean_email(m)
        if e:
            found.append(e)

    # De-dupe preserving order; prefer emails on the site's own domain.
    seen, ordered = set(), []
    for e in found:
        if e not in seen:
            seen.add(e)
            ordered.append(e)
    def _same_domain(email: str) -> bool:
        d = email.split("@")[-1]
        return bool(site_host) and (d == site_host or d.endswith("." + site_host))

    if site_host:
        ordered.sort(key=lambda e: 0 if _same_domain(e) else 1)
    if ordered:
        out["email"] = ordered[0]

    # ── socials ──
    for key, pat in _SOCIAL_PATTERNS.items():
        for url in pat.findall(html):
            url = url.rstrip('"\'.,);')
            if _SOCIAL_JUNK.search(url):
                continue
            # Skip bare domain roots like facebook.com/ (no profile).
            path = urllib.parse.urlparse(url).path.strip("/")
            if not path or path.lower() in ("home", "share"):
                continue
            out[key] = url
            break

    return out


def enrich_website(url: str, timeout: float = 8.0, fields: tuple = ENRICH_KEYS) -> dict:
    """Fetch a business website (home + a contact page) and extract contacts.

    Returns only the requested `fields`. Never raises — failures yield "".
    """
    out = {k: "" for k in fields}
    if not url:
        return out
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        html = _fetch(url, timeout=timeout)
    except Exception:
        html = ""

    contacts = extract_contacts(html, url) if html else {k: "" for k in ENRICH_KEYS}

    # If we still lack an email, try a likely contact page.
    if not contacts.get("email") and html:
        contact_url = _guess_contact_url(html, url)
        if contact_url:
            try:
                chtml = _fetch(contact_url, timeout=timeout)
                extra = extract_contacts(chtml, url)
                for k, v in extra.items():
                    if v and not contacts.get(k):
                        contacts[k] = v
            except Exception:
                pass

    return {k: contacts.get(k, "") for k in fields}


def _guess_contact_url(html: str, base_url: str) -> str:
    for m in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        if re.search(r"contact|about|reach|connect", m, re.I) and "mailto:" not in m.lower():
            return urllib.parse.urljoin(base_url, m)
    return ""
