# -*- coding: utf-8 -*-
"""
Crawler — the free data layer for SEO Audit Pro.

Fetches pages over plain HTTP(S) (no paid API, no headless browser), reads
robots.txt and the XML sitemap(s), and performs a bounded breadth-first crawl of
same-host internal links. Every page object carries what the check engine needs:
final URL, status, redirect chain, response headers, and raw HTML.

Design notes:
  * fetch_page() never raises for HTTP errors — it returns a page dict with
    ok=False and the status/error so a single bad URL can't abort the audit.
  * The crawl is same-host only (www and non-www are treated as one host) and
    capped at max_pages so a large site stays fast and free.
  * HTML fetch/extract patterns follow Web Content Analyzer's analyzer.py.
"""

import datetime
import re
import socket
import ssl
import time
# From the submodules: in Python 3.15 "concurrent.futures.ThreadPoolExecutor" can still be an
# unresolved lazy import when this module loads (it then fails with "lazy_import is not callable").
from concurrent.futures._base import as_completed
from concurrent.futures.thread import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin, urldefrag, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (compatible; MindcobSEOAuditPro/1.0; +https://mindcob.tools; "
      "local SEO audit tool)")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Non-page endpoints we never want to enqueue during a crawl.
_SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp",
    ".pdf", ".zip", ".rar", ".gz", ".tar", ".dmg", ".exe", ".mp4", ".webm",
    ".mp3", ".wav", ".avi", ".mov", ".css", ".js", ".json", ".xml", ".rss",
    ".woff", ".woff2", ".ttf", ".eot", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv",
)

# Known AI / LLM crawler user-agents worth reporting on in robots.txt.
AI_BOTS = ["GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "Claude-Web",
           "anthropic-ai", "Google-Extended", "PerplexityBot", "CCBot",
           "Bytespider", "Amazonbot", "Applebot-Extended", "meta-externalagent"]


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def normalize_url(url):
    """Add a scheme if missing, drop the fragment, and tidy a trailing slash on
    the bare-host form so 'example.com' and 'https://example.com/' unify."""
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url, _ = urldefrag(url)
    parts = urlsplit(url)
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _root_host(netloc):
    """Registrable-ish host with a leading 'www.' stripped, lower-cased."""
    host = (netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def same_host(a, b):
    return _root_host(urlparse(a).netloc) == _root_host(urlparse(b).netloc)


def registrable(url):
    return _root_host(urlparse(url).netloc)


def origin(url):
    p = urlparse(url)
    return "%s://%s" % (p.scheme, p.netloc)


def _looks_like_page(url):
    path = urlparse(url).path.lower()
    return not path.endswith(_SKIP_EXT)


# ---------------------------------------------------------------------------
# Fetch a single page
# ---------------------------------------------------------------------------
def fetch_page(url, session=None, timeout=25, proxy=None, method="GET"):
    """Fetch one URL. Returns a page dict — never raises for HTTP/network errors.

    Keys: url, final_url, status, ok, is_html, content_type, headers (dict),
          redirects (list of {status,url}), html, text_len, elapsed_ms, error.
    """
    sess = session or requests
    page = {
        "url": url, "final_url": url, "status": 0, "ok": False,
        "is_html": False, "content_type": "", "headers": {}, "redirects": [],
        "html": "", "text_len": 0, "elapsed_ms": 0, "error": "",
    }
    proxies = _proxies(proxy)
    t0 = time.time()
    try:
        # Streamed: the body is read only as far as needed (see _read_html).
        resp = sess.request(method, url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True, proxies=proxies,
                            stream=(method == "GET"))
    except requests.RequestException as e:
        page["error"] = str(e)
        page["elapsed_ms"] = int((time.time() - t0) * 1000)
        return page

    try:
        page["status"] = resp.status_code
        page["final_url"] = resp.url
        page["ok"] = resp.ok
        page["headers"] = {k: v for k, v in resp.headers.items()}
        page["content_type"] = resp.headers.get("Content-Type", "")
        page["redirects"] = [{"status": r.status_code, "url": r.url}
                             for r in resp.history]
        ctype = page["content_type"].lower()
        is_html = "html" in ctype
        if method == "GET":
            try:
                html = _read_html(resp, ctype, is_html)
            except (requests.RequestException, OSError) as e:
                html, page["error"] = None, str(e)
            if html is not None:
                page["is_html"] = True
                page["html"] = html
                page["text_len"] = len(html)
        else:
            page["is_html"] = is_html
    finally:
        resp.close()
        page["elapsed_ms"] = int((time.time() - t0) * 1000)
    return page


# Pages larger than this are truncated: every on-page check looks at the head
# and the main content, and huge documents (registries, data dumps) would cost
# seconds of CPU and hundreds of MB per page.
MAX_HTML_BYTES = 3 * 1024 * 1024
_BINARY_TYPES = ("image/", "video/", "audio/", "font/", "application/pdf", "application/zip",
                 "application/gzip", "application/x-", "application/octet-stream", "application/vnd.",
                 "application/msword", "application/wasm")


def _read_html(resp, ctype, is_html):
    """The page's HTML, or None when the response is not an HTML page.

    Binary files are never downloaded. Other non-HTML types are sniffed from
    their first 4 KB (some servers send HTML as text/plain) and dropped if they
    are not HTML."""
    if not is_html and any(t in ctype for t in _BINARY_TYPES):
        return None
    chunks, size, sniffed = [], 0, is_html
    for chunk in resp.iter_content(65536):
        chunks.append(chunk)
        size += len(chunk)
        if not sniffed and size >= 4096:
            start = b"".join(chunks)[:4096].lower()
            if b"<html" not in start and b"<!doctype html" not in start:
                return None
            sniffed = True
        if size >= MAX_HTML_BYTES:
            break
    raw = b"".join(chunks)[:MAX_HTML_BYTES]
    if not sniffed:
        start = raw[:4096].lower()
        if b"<html" not in start and b"<!doctype html" not in start:
            return None
    return _decode(raw, ctype)


_CHARSET_RE = re.compile(r"charset=[\"']?([\w.:-]+)", re.I)
_META_CHARSET_RE = re.compile(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", re.I)


def _decode(raw, ctype):
    """Decode with the declared charset (header, then <meta>), else UTF-8;
    detection runs only on a 64 KB sample (it is very slow on large bodies)."""
    declared = []
    m = _CHARSET_RE.search(ctype or "")
    if m:
        declared.append(m.group(1))
    m = _META_CHARSET_RE.search(raw[:4096])
    if m:
        declared.append(m.group(1).decode("ascii", "ignore"))
    for enc in declared + ["utf-8"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw[:65536]).best()
        if best and best.encoding:
            return raw.decode(best.encoding, errors="replace")
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


def _proxies(proxy):
    if not proxy:
        return None
    if isinstance(proxy, dict):
        return proxy
    proxy = str(proxy).strip()
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    return {"http": proxy, "https": proxy}


def extract_main_text(html, url=None):
    """(main_text, method) — trafilatura when installed, else a bs4 heuristic.
    Ported from Web Content Analyzer's analyzer.py."""
    try:
        import trafilatura
        txt = trafilatura.extract(html, url=url, include_comments=False,
                                  include_tables=True, favor_precision=True)
        if txt and len(txt.split()) > 50:
            return txt.strip(), "trafilatura"
    except Exception:
        pass
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        t.decompose()
    container = soup.find("main") or soup.find("article") or soup.body or soup
    for t in container.find_all(["nav", "header", "footer", "aside", "form"]):
        t.decompose()
    blocks = []
    for el in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        s = el.get_text(" ", strip=True)
        if s:
            blocks.append(s)
    text = "\n".join(blocks) if blocks else container.get_text(" ", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip(), "heuristic (install trafilatura for better extraction)"


def extract_links(html, base_url):
    """Return (internal_urls, external_count) discovered on a page.

    internal_urls are normalized, same-host, page-like, de-duplicated. The
    external count is a raw tally for on-page link stats.
    """
    soup = BeautifulSoup(html, "html.parser")
    internal, external, seen = [], 0, set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:",
                                        "data:")):
            continue
        absu = normalize_url(urljoin(base_url, href))
        if not absu.startswith(("http://", "https://")):
            continue
        if same_host(absu, base_url):
            if _looks_like_page(absu) and absu not in seen:
                seen.add(absu)
                internal.append(absu)
        else:
            external += 1
    return internal, external


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
def fetch_robots(base_url, session=None, timeout=15, proxy=None):
    """Fetch and parse robots.txt at the site origin."""
    robots_url = origin(base_url) + "/robots.txt"
    page = fetch_page(robots_url, session=session, timeout=timeout, proxy=proxy,
                      method="GET")
    text = page.get("html") or ""
    # robots.txt is plain text; fetch_page only fills html for html content, so
    # re-fetch raw if needed.
    if page["status"] == 200 and not text:
        try:
            r = (session or requests).get(robots_url, headers=HEADERS,
                                          timeout=timeout, proxies=_proxies(proxy))
            text = r.text if r.ok else ""
        except requests.RequestException:
            text = ""
    parsed = parse_robots(text)
    parsed.update({
        "url": robots_url,
        "status": page["status"],
        "present": page["status"] == 200 and bool(text.strip()),
        "error": page.get("error", ""),
        "raw": text[:20000],
    })
    return parsed


def parse_robots(text):
    """Minimal robots.txt parser: per-agent Disallow/Allow groups, Sitemap
    lines, a blanket-block flag for '*', and AI-bot rule detection."""
    agents_rules = {}      # agent -> {"disallow":[], "allow":[]}
    sitemaps = []
    current = []
    # Consecutive User-agent lines share one group: a new group only starts
    # when a User-agent line follows at least one rule line.
    in_rules = True
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field in ("disallow", "allow"):
            in_rules = True
        if field == "user-agent":
            ua = value.lower()
            if in_rules:
                current = []
                in_rules = False
            if ua not in current:
                current.append(ua)
            agents_rules.setdefault(ua, {"disallow": [], "allow": []})
        elif field == "disallow" and current:
            for ua in current:
                agents_rules.setdefault(ua, {"disallow": [], "allow": []})
                agents_rules[ua]["disallow"].append(value)
        elif field == "allow" and current:
            for ua in current:
                agents_rules.setdefault(ua, {"disallow": [], "allow": []})
                agents_rules[ua]["allow"].append(value)
        elif field == "sitemap":
            sitemaps.append(value)

    star = agents_rules.get("*", {"disallow": [], "allow": []})
    blocks_all = any(d.strip() == "/" for d in star["disallow"]) and \
        not any(a.strip() == "/" for a in star["allow"])

    ai_rules = {}
    for bot in AI_BOTS:
        rules = agents_rules.get(bot.lower())
        if rules is not None:
            blocked = any(d.strip() == "/" for d in rules["disallow"])
            ai_rules[bot] = "blocked" if blocked else "allowed"
    return {
        "agents": agents_rules,
        "sitemaps": sitemaps,
        "blocks_all": blocks_all,
        "ai_rules": ai_rules,
        "star_disallow": star["disallow"],
    }


# ---------------------------------------------------------------------------
# XML sitemaps
# ---------------------------------------------------------------------------
def fetch_sitemaps(sitemap_urls, session=None, timeout=20, proxy=None,
                   max_urls=2000, max_maps=25):
    """Fetch one or more sitemaps (recursing sitemap-index files) and return
    {urls:[...], count, maps_fetched, errors:[...], had_index}."""
    sess = session or requests
    seen_maps, out_urls, errors = set(), [], []
    queue = list(sitemap_urls or [])
    had_index = False
    while queue and len(seen_maps) < max_maps and len(out_urls) < max_urls:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            r = sess.get(sm, headers=HEADERS, timeout=timeout,
                         proxies=_proxies(proxy))
            if not r.ok:
                errors.append("%s → HTTP %d" % (sm, r.status_code))
                continue
            body = r.content
        except requests.RequestException as e:
            errors.append("%s → %s" % (sm, e))
            continue
        if body[:2] == b"\x1f\x8b":  # .xml.gz sitemap (not auto-decoded)
            try:
                import gzip
                body = gzip.decompress(body)
            except Exception as e:
                errors.append("%s → gzip: %s" % (sm, e))
                continue
        locs, is_index = _parse_sitemap_xml(body)
        if is_index:
            had_index = True
            for loc in locs:
                if loc not in seen_maps:
                    queue.append(loc)
        else:
            for loc in locs:
                if len(out_urls) >= max_urls:
                    break
                out_urls.append(loc)
    return {
        "urls": out_urls,
        "count": len(out_urls),
        # True when the max_urls cap was hit, so count is a lower bound.
        "capped": len(out_urls) >= max_urls,
        "maps_fetched": len(seen_maps),
        "errors": errors,
        "had_index": had_index,
    }


def _parse_sitemap_xml(body):
    """Return (locs, is_index). Uses lxml when available, else a regex fallback."""
    locs = []
    is_index = False
    try:
        from bs4 import BeautifulSoup as BS
        soup = BS(body, "xml")
        if soup.find("sitemapindex"):
            is_index = True
            locs = [t.get_text(strip=True) for t in soup.find_all("loc")]
        elif soup.find("urlset"):
            locs = [t.get_text(strip=True) for t in soup.find_all("loc")]
        else:
            locs = [t.get_text(strip=True) for t in soup.find_all("loc")]
            is_index = b"<sitemapindex" in body[:500].lower()
        if locs:
            return [u for u in locs if u], is_index
    except Exception:
        pass
    # Regex fallback
    try:
        text = body.decode("utf-8", "ignore")
    except Exception:
        text = str(body)
    is_index = "<sitemapindex" in text[:1000].lower()
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.I | re.S)
    return [u.strip() for u in locs if u.strip()], is_index


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# SSL / TLS Certificate Inspector
# ---------------------------------------------------------------------------
def get_ssl_certificate_details(host_or_url, timeout=8):
    """Inspects SSL/TLS certificate directly via socket handshake.
    Returns: {
        'valid': bool,
        'issuer': str,
        'subject': str,
        'not_after': str,
        'days_left': int or None,
        'is_expired': bool,
        'protocol': str,
        'error': str or None
    }
    """
    p = urlparse(host_or_url if "://" in host_or_url else "https://" + host_or_url)
    hostname = p.netloc or p.path
    hostname = hostname.split(":")[0]
    res = {
        "valid": False,
        "issuer": "",
        "subject": "",
        "not_after": "",
        "days_left": None,
        "is_expired": False,
        "protocol": "",
        "error": None,
    }
    if not hostname:
        res["error"] = "No hostname provided"
        return res

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                res["protocol"] = ssock.version() or "TLS"
                issuer_dict = dict(x[0] for x in cert.get("issuer", ()))
                issuer = (issuer_dict.get("organizationName") or
                          issuer_dict.get("commonName") or "Trusted CA")
                not_after_str = cert.get("notAfter")
                if not_after_str:
                    expire_date = datetime.datetime.strptime(
                        not_after_str, "%b %d %H:%M:%S %Y %Z")
                    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    days_left = (expire_date - now).days
                    res["valid"] = True
                    res["issuer"] = issuer
                    res["not_after"] = expire_date.strftime("%Y-%m-%d")
                    res["days_left"] = days_left
                    res["is_expired"] = days_left <= 0
    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# Bounded breadth-first crawl (supporting up to 1,000+ pages)
# ---------------------------------------------------------------------------
def crawl(start_url, max_pages=25, seed_urls=None, progress=None, cancel=None,
          timeout=25, proxy=None, workers=None):
    """BFS crawl of same-host internal pages up to max_pages (supporting 200 - 1,000+ pages).

    Returns (pages, order) where pages is {url: page_dict} for every fetched URL
    and order is the list of URLs in the sequence they were fetched.
    """
    log = progress if callable(progress) else (lambda m: None)
    start = normalize_url(start_url)
    session = requests.Session()

    if workers is None:
        workers = max(8, min(24, max_pages // 20))

    pages, order = {}, []
    queued = {start}
    frontier = [start]
    # Same-host seed URLs (from sitemap) get appended, de-duplicated.
    for s in (seed_urls or []):
        s = normalize_url(s)
        if s and same_host(s, start) and _looks_like_page(s) and s not in queued:
            queued.add(s)
            frontier.append(s)

    report_interval = max(5, min(50, max_pages // 20))

    while frontier and len(order) < max_pages:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Audit cancelled.")
        batch = frontier[:workers]
        frontier = frontier[workers:]
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batch)))) as ex:
            futs = {ex.submit(fetch_page, u, session, timeout, proxy): u
                    for u in batch}
            for fut in as_completed(futs):
                u = futs[fut]
                try:
                    results[u] = fut.result()
                except Exception as e:
                    results[u] = {"url": u, "final_url": u, "status": 0,
                                  "ok": False, "is_html": False, "html": "",
                                  "headers": {}, "redirects": [], "error": str(e),
                                  "content_type": "", "text_len": 0,
                                  "elapsed_ms": 0}
        # Preserve batch order for determinism.
        for u in batch:
            page = results.get(u)
            if page is None:
                continue
            pages[u] = page
            order.append(u)
            if len(order) % report_interval == 0 or len(order) == max_pages or len(order) <= 5:
                log("Crawled %d/%d pages (%.0f%%), %d URLs discovered"
                    % (len(order), max_pages, len(order) / max_pages * 100, len(queued)))
            if len(order) >= max_pages:
                break
            # Enqueue new internal links found on this page.
            if page.get("is_html") and page.get("html"):
                try:
                    links, _ = extract_links(page["html"],
                                             page.get("final_url") or u)
                except Exception:
                    links = []
                for link in links:
                    if link not in queued:
                        queued.add(link)
                        frontier.append(link)
    return pages, order
