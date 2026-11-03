"""Website contact enrichment (email + social profiles).

Given a business website URL from a Google Maps card, fetch the home page (and,
if no email is found there, a likely `/contact`, `/contact-us`, `/about`, or
footer-linked page) over plain HTTP — no browser needed — and pull out the best
email address (including from `mailto:`, `<footer>`, JSON-LD, Cloudflare
`data-cfemail`, and obfuscated `[at]` formats) plus social profile URLs.

Pure extraction lives in `extract_contacts(html, base_url)` so it can be unit
tested offline; `enrich_website(url)` wraps it with `urllib.request`.
"""

from __future__ import annotations

import html as html_lib
import re
import ssl
import urllib.parse
import urllib.request

ENRICH_KEYS = ("email", "facebook", "instagram", "linkedin", "twitter", "youtube")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}"
)

# Obfuscated email pattern: e.g. info [at] domain [dot] com or info(at)domain.com
_OBFUSCATED_EMAIL_RE = re.compile(
    r"\b([a-zA-Z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|&#64;)\s*([a-zA-Z0-9.\-]+)\s*(?:\[dot\]|\(dot\)|\.)\s*([a-zA-Z]{2,24})\b",
    re.I,
)

# Cloudflare email protection hex strings
_CF_EMAIL_RE = re.compile(
    r'(?:data-cfemail=["\']([0-9a-fA-F]{6,})["\']|/cdn-cgi/l/email-protection#([0-9a-fA-F]{6,}))'
)

# File extensions / junk domains that regex-match as "emails" in HTML/CSS/JS.
_BAD_EMAIL_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".map", ".mp4", ".pdf",
)
_BAD_EMAIL_DOMAINS = {
    "sentry.io", "sentry-next.wixpress.com", "sentry.wixpress.com",
    "example.com", "domain.com", "email.com", "yoursite.com", "yourdomain.com",
    "company.com", "wix.com", "wordpress.org", "w3.org", "schema.org",
    "googleapis.com", "googleusercontent.com", "gstatic.com", "cloudflare.com",
    "godaddy.com", "squarespace.com", "shopify.com", "latofonts.com",
}
_BAD_EMAIL_LOCAL = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "abuse", "webmaster@example", "user@", "yourname", "name@",
    "email@", "firstname", "lastname",
)

_PREFERRED_PREFIXES = (
    "info@", "contact@", "hello@", "office@", "sales@", "support@",
    "enquiries@", "inquiries@", "booking@", "appointments@", "admin@", "team@",
)

_SOCIAL_PATTERNS = {
    "facebook": re.compile(
        r"https?://(?:www\.|m\.)?facebook\.com/(?!tr\b|sharer|share\.php|dialog|plugins|flx|login|pages/category)"
        r"[A-Za-z0-9._\-/]+",
        re.I,
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|explore/|accounts/)[A-Za-z0-9._]+/?",
        re.I,
    ),
    "linkedin": re.compile(
        r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9._\-]+/?",
        re.I,
    ),
    "twitter": re.compile(
        r"https?://(?:www\.)?(?:twitter|x)\.com/(?!intent/|share\b|home\b|search\b|i/)[A-Za-z0-9_]+/?",
        re.I,
    ),
    "youtube": re.compile(
        r"https?://(?:www\.)?youtube\.com/(?:@[A-Za-z0-9._\-]+|(?:c|channel|user)/[A-Za-z0-9._\-]+)/?",
        re.I,
    ),
}

_SOCIAL_JUNK = re.compile(r"(wix\.com|squarespace|shopify|wordpress|godaddy|example)", re.I)

_FOOTER_BLOCK_RE = re.compile(
    r"<footer\b[^>]*>.*?</footer>|<div[^>]+(?:id|class)=[\"'][^\"']*footer[^\"']*[\"'][^>]*>.*?</div>",
    re.I | re.S,
)


def _decode_cfemail(hex_str: str) -> str:
    """Decode Cloudflare's XOR-obfuscated data-cfemail hex string."""
    try:
        key = int(hex_str[:2], 16)
        return "".join(chr(int(hex_str[i:i + 2], 16) ^ key) for i in range(2, len(hex_str), 2))
    except Exception:
        return ""


def _valid_email(addr: str) -> bool:
    addr = addr.strip().strip(".,;:()[]<>\"'").lower()
    if not addr or len(addr) > 120 or "@" not in addr:
        return False
    if any(addr.endswith(ext) for ext in _BAD_EMAIL_SUFFIXES):
        return False
    local, _, domain = addr.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if domain in _BAD_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in _BAD_EMAIL_DOMAINS):
        return False
    if any(local.startswith(p) for p in _BAD_EMAIL_LOCAL):
        return False
    # Reject hashed tracking IDs like 605a7baede844d278b89dc95ae0a9123@...
    if len(local) >= 24 and re.fullmatch(r"[0-9a-f]+", local):
        return False
    # Reject version strings like react@18.2.0
    if re.fullmatch(r"\d+(\.\d+)+", domain):
        return False
    return True


def _clean_candidate(raw: str) -> str:
    addr = urllib.parse.unquote(html_lib.unescape(raw or "")).strip()
    addr = re.sub(r"^mailto:\s*", "", addr, flags=re.I)
    addr = addr.split("?")[0].split("#")[0].strip().strip(".,;:()[]<>\"'/\\")
    return addr.lower()


def extract_contacts(html: str, base_url: str = "") -> dict:
    """Extract `{email, facebook, instagram, linkedin, twitter, youtube}` from HTML."""
    out = {k: "" for k in ENRICH_KEYS}
    if not html:
        return out

    site_host = urllib.parse.urlparse(base_url).netloc.lower().removeprefix("www.")
    unescaped = html_lib.unescape(html)

    # ── emails ──
    # Priority buckets:
    # 1. mailto: links (highest confidence)
    # 2. Cloudflare decoded emails & JSON-LD "email" fields
    # 3. Footer section emails (<footer> or class/id*="footer")
    # 4. General page body emails + de-obfuscated [at] emails
    high_conf: list[str] = []
    footer_conf: list[str] = []
    body_conf: list[str] = []

    for m in re.findall(r'mailto:([^"\'\s?>#]+)', unescaped, re.I):
        c = _clean_candidate(m)
        if _valid_email(c):
            high_conf.append(c)

    for g1, g2 in _CF_EMAIL_RE.findall(html):
        decoded = _clean_candidate(_decode_cfemail(g1 or g2))
        if _valid_email(decoded):
            high_conf.append(decoded)

    for m in re.findall(r'"email"\s*:\s*"([^"]+)"', unescaped, re.I):
        c = _clean_candidate(m)
        if _valid_email(c):
            high_conf.append(c)

    # Inspect <footer> and footer containers specifically
    for fblock in _FOOTER_BLOCK_RE.findall(unescaped):
        for m in re.findall(r'mailto:([^"\'\s?>#]+)', fblock, re.I):
            c = _clean_candidate(m)
            if _valid_email(c):
                high_conf.append(c)
        for m in _EMAIL_RE.findall(fblock):
            c = _clean_candidate(m)
            if _valid_email(c):
                footer_conf.append(c)
        for loc, dom, tld in _OBFUSCATED_EMAIL_RE.findall(fblock):
            c = _clean_candidate(f"{loc}@{dom}.{tld}")
            if _valid_email(c):
                footer_conf.append(c)

    # Full page body regex + obfuscated [at]
    for m in _EMAIL_RE.findall(unescaped):
        c = _clean_candidate(m)
        if _valid_email(c):
            body_conf.append(c)

    for loc, dom, tld in _OBFUSCATED_EMAIL_RE.findall(unescaped):
        c = _clean_candidate(f"{loc}@{dom}.{tld}")
        if _valid_email(c):
            body_conf.append(c)

    seen = set()
    ordered: list[str] = []
    high_set = set(high_conf)
    footer_set = set(footer_conf)

    for e in (*high_conf, *footer_conf, *body_conf):
        if e not in seen:
            seen.add(e)
            ordered.append(e)

    def _same_domain(email: str) -> bool:
        d = email.split("@")[-1]
        return bool(site_host) and (d == site_host or d.endswith("." + site_host))

    def _rank(email: str) -> tuple:
        # Prefer: (same domain OR mailto/cf/jsonld, in footer/high_conf, standard contact prefix)
        same = 0 if _same_domain(email) else 1
        src = 0 if email in high_set else (1 if email in footer_set else 2)
        pref = 0 if email.startswith(_PREFERRED_PREFIXES) else 1
        return (same, src, pref)

    if ordered:
        if site_host:
            ordered.sort(key=_rank)
        out["email"] = ordered[0]

    # ── socials ──
    for key, pat in _SOCIAL_PATTERNS.items():
        for url in pat.findall(unescaped):
            url = url.rstrip('"\'.,);')
            if _SOCIAL_JUNK.search(url):
                continue
            path = urllib.parse.urlparse(url).path.strip("/")
            if not path or path.lower() in ("home", "share"):
                continue
            out[key] = url
            break

    return out


def _fetch(url: str, timeout: float = 8.0) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and not any(t in ctype for t in ("text/html", "application/xhtml", "text/plain")):
            return ""
        raw = resp.read(450_000)
    return raw.decode("utf-8", errors="ignore")


def _guess_contact_urls(html: str, base_url: str) -> list[str]:
    """Return ranked internal URLs likely to contain Contact Us / Footer / About contact details."""
    if not base_url:
        return []
    parsed_base = urllib.parse.urlparse(base_url)
    base_host = parsed_base.netloc.lower().removeprefix("www.")
    base_norm = base_url.rstrip("/")

    scored: list[tuple[int, str]] = []
    seen_urls = {base_norm}

    def _consider(href: str, anchor_text: str = "") -> None:
        href = html_lib.unescape((href or "").strip())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:", "whatsapp:")):
            return
        full = urllib.parse.urljoin(base_url, href).split("#")[0].rstrip("/")
        if not full or full in seen_urls:
            return
        p = urllib.parse.urlparse(full)
        if p.scheme not in ("http", "https"):
            return
        host = p.netloc.lower().removeprefix("www.")
        if base_host and host != base_host and not host.endswith("." + base_host):
            return
        low_path = (p.path or "").lower()
        if any(low_path.endswith(ext) for ext in _BAD_EMAIL_SUFFIXES):
            return
        low_text = (anchor_text or "").lower()
        hay = f"{low_path} {low_text}"
        score = 0
        if re.search(r"contact-us|contactus|/contact\b|get-in-touch|reach-us", low_path):
            score = 100
        elif re.search(r"\bcontact\b|get in touch|reach us|email us", low_text):
            score = 90
        elif re.search(r"contact|reach|connect|inquir|enquir|support|help|impressum", hay):
            score = 75
        elif re.search(r"about-us|aboutus|/about\b|our-team|team|company|location", hay):
            score = 50
        if score > 0:
            seen_urls.add(full)
            scored.append((score, full))

    if html:
        # Match full <a ... href="...">anchor text</a> first
        for href, inner in re.findall(
            r'<a\b[^>]*?\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S
        ):
            clean_txt = re.sub(r"<[^>]+>", " ", inner)
            _consider(href, clean_txt)
        # Also catch any bare href="..." attributes
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            _consider(href, "")

    scored.sort(key=lambda item: -item[0])
    results = [u for _s, u in scored]

    # Fallback standard slugs if the page used JS menus or didn't expose links
    if not results and parsed_base.scheme and parsed_base.netloc:
        root = f"{parsed_base.scheme}://{parsed_base.netloc}"
        for slug in ("/contact", "/contact-us", "/about"):
            cand = root + slug
            if cand not in seen_urls:
                results.append(cand)

    return results[:3]


def _guess_contact_url(html: str, base_url: str) -> str:
    urls = _guess_contact_urls(html, base_url)
    return urls[0] if urls else ""


def enrich_website(url: str, timeout: float = 4.5, fields: tuple = ENRICH_KEYS) -> dict:
    """Fetch a business website (home + Contact Us / About pages) and extract contacts.

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

    # If we still lack an email, try up to 2 likely Contact Us / About / Footer pages.
    if not contacts.get("email"):
        for contact_url in _guess_contact_urls(html, url)[:2]:
            try:
                chtml = _fetch(contact_url, timeout=min(timeout, 3.2))
                if not chtml:
                    continue
                extra = extract_contacts(chtml, url)
                for k, v in extra.items():
                    if v and not contacts.get(k):
                        contacts[k] = v
                if contacts.get("email"):
                    break
            except Exception:
                continue

    return {k: contacts.get(k, "") for k in fields}
