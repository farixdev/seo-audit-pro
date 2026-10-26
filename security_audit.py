# -*- coding: utf-8 -*-
"""
Passive Website Security Auditor for SEO Audit Pro.

Executes 100% passive, safe, non-intrusive security checks:
  A. Transport Security (HTTPS enforcement, SSL/TLS cert validity, domain match, TLS versions, mixed content, HSTS)
  B. Security Headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, HSTS)
  C. Cookie Security (Secure, HttpOnly, SameSite attributes)
  D. Information Disclosure (Server/tech banners, CMS versions, verbose errors, directory listings, sensitive files, JS source maps)
  E. Outdated / Vulnerable Components (JS library versions, CVE cross-referencing, CMS extensions)
  F. DNS & Email Domain Trust (SPF, DMARC, DKIM records via DoH)
  G. Authentication Surface Observation (login path, CAPTCHA, 2FA mentions, xmlrpc reachability)
  H. Bot / WAF Detection (Cloudflare, Sucuri, Akamai, AWS CloudFront, Imperva, rate-limit headers)
  I. Technology Fingerprinting (CMS, frontend frameworks, hosting provider)

Explicitly excluded per security audit constraints:
  - NO tenant isolation testing (cannot be verified via public observation)
  - NO domain age scoring (domain age does not equate to security)
  - NO certification badges (badges do not prove security)
  - NEVER open, extract, or quote credentials from discovered files (stop at exposure confirmation)
  - ZERO login attempts / interaction attempts (pure passive observation)
"""

import collections
import datetime
import html
import re
import socket
import ssl
from urllib.parse import urlparse, urljoin
import warnings

import requests
from bs4 import BeautifulSoup

# Suppress deprecation warnings when probing legacy TLS versions
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Standard user agent for passive security requests
SEC_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 SEOAuditPro/2.5"

# Safe sensitive files list (GET requests only; strict signature verification)
SENSITIVE_FILES_MAP = {
    ".git/config": {"sig": ["[core]", "repositoryformatversion"], "name": "Git Configuration (.git/config)"},
    ".env": {"sig": ["APP_", "DB_", "SECRET", "KEY=", "API_KEY", "AWS_"], "name": "Environment Variables (.env)"},
    "wp-config.php.bak": {"sig": ["DB_NAME", "table_prefix", "<?php"], "name": "WordPress Config Backup (wp-config.php.bak)"},
    "backup.zip": {"mime": ["application/zip", "application/x-zip-compressed"], "magic": b"PK\x03\x04", "name": "Database/Site Backup (backup.zip)"},
    "phpinfo.php": {"sig": ["PHP Version", "phpinfo()", "Configuration File"], "name": "PHP Info Diagnostic (phpinfo.php)"},
    ".DS_Store": {"magic": b"\x00\x00\x00\x01Bud1", "name": "macOS Metadata File (.DS_Store)"},
}

# Known vulnerable JS component database with confirmed CVEs
KNOWN_VULNERABLE_LIBRARIES = {
    "jquery": [
        {"max_version": "1.12.4", "cve": "CVE-2015-9251 / CVE-2019-11358", "sev": "high", "desc": "jQuery < 1.12.4 contains known Cross-Site Scripting (XSS) and Prototype Pollution vulnerabilities."},
        {"min_version": "1.12.4", "max_version": "3.4.99", "cve": "CVE-2020-11022 / CVE-2020-11023", "sev": "high", "desc": "jQuery < 3.5.0 contains Cross-Site Scripting (XSS) flaw in jQuery.htmlPrefilter."},
    ],
    "bootstrap": [
        {"max_version": "3.4.0", "cve": "CVE-2019-8331 / CVE-2018-14041", "sev": "high", "desc": "Bootstrap < 3.4.1 has XSS vulnerabilities in tooltip and popover plugins."},
        {"min_version": "4.0.0", "max_version": "4.3.0", "cve": "CVE-2019-8331", "sev": "medium", "desc": "Bootstrap < 4.3.1 allows XSS via data-template attributes in tooltip/popover."},
    ],
    "moment": [
        {"max_version": "2.29.3", "cve": "CVE-2022-31129", "sev": "high", "desc": "Moment.js < 2.29.4 is vulnerable to Regular Expression Denial of Service (ReDoS)."},
    ],
    "lodash": [
        {"max_version": "4.17.20", "cve": "CVE-2020-8203 / CVE-2021-23337", "sev": "high", "desc": "Lodash < 4.17.21 contains Prototype Pollution and Command Injection vulnerabilities."},
    ],
    "angularjs": [
        {"max_version": "1.8.3", "cve": "EOL / Multiple CVEs", "sev": "high", "desc": "AngularJS (1.x) reached official End-of-Life and contains multiple unpatched XSS vectors."},
    ],
}


def _norm_headers(headers):
    """Normalize headers dict to lowercase keys."""
    if not headers:
        return {}
    return {k.lower(): str(v) for k, v in headers.items()}


def _parse_version(v_str):
    """Extract numeric tuple for version comparison (e.g. '3.5.1' -> (3, 5, 1))."""
    if not v_str:
        return (0, 0, 0)
    m = re.findall(r"\d+", v_str)
    return tuple(int(x) for x in m[:3]) if m else (0, 0, 0)


# ===========================================================================
# A. Transport Security
# ===========================================================================
def check_tls_support(hostname, timeout=4):
    """Safely probes which TLS protocol versions the server accepts."""
    results = {
        "negotiated": None,
        "supports_tls10": False,
        "supports_tls11": False,
        "supports_tls12": False,
        "supports_tls13": False,
        "deprecated_protocols_accepted": False,
    }
    if not hostname:
        return results

    # Probe negotiated default protocol first
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                results["negotiated"] = ssock.version()
                if results["negotiated"] == "TLSv1.3":
                    results["supports_tls13"] = True
                elif results["negotiated"] == "TLSv1.2":
                    results["supports_tls12"] = True
    except Exception:
        pass

    # Probe TLS 1.0 / 1.1 (deprecated / insecure). Each result is
    #   True  = the server completed a legacy handshake,
    #   False = the server refused it during the handshake,
    #   None  = the probe could not run (local OpenSSL lacks the protocol,
    #           network error, timeout) — the outcome is unknown.
    def _probe_legacy(version):
        try:
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            # Legacy protocols need legacy ciphers; modern OpenSSL hides them
            # behind security level 0.
            c.set_ciphers("DEFAULT:@SECLEVEL=0")
            c.minimum_version = version
            c.maximum_version = version
        except Exception:
            return None
        try:
            sock = socket.create_connection((hostname, 443), timeout=timeout)
        except Exception:
            return None
        try:
            with sock:
                with c.wrap_socket(sock, server_hostname=hostname) as ssock:
                    v = ssock.version() or ""
                    return v in ("TLSv1", "TLSv1.1")
        except ssl.SSLError as e:
            msg = str(e).upper()
            # Client-side configuration failures: the probe never reached
            # the server's decision.
            if any(k in msg for k in ("NO_PROTOCOLS_AVAILABLE", "NO_CIPHERS_AVAILABLE",
                                      "NO CIPHERS AVAILABLE", "NO PROTOCOLS AVAILABLE")):
                return None
            return False
        except (ConnectionResetError, ConnectionAbortedError):
            return False  # server dropped the legacy hello = refusal
        except Exception:
            return None

    results["supports_tls10"] = _probe_legacy(ssl.TLSVersion.TLSv1)
    results["supports_tls11"] = _probe_legacy(ssl.TLSVersion.TLSv1_1)
    results["deprecated_protocols_accepted"] = bool(
        results["supports_tls10"] or results["supports_tls11"])
    # Only a clean refusal of both legacy versions proves they are disabled.
    results["legacy_probe_complete"] = (results["supports_tls10"] is not None
                                        and results["supports_tls11"] is not None)

    # Probe TLS 1.2
    if not results["supports_tls12"]:
        try:
            ctx12 = ssl.create_default_context()
            ctx12.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx12.maximum_version = ssl.TLSVersion.TLSv1_2
            with socket.create_connection((hostname, 443), timeout=timeout) as sock:
                with ctx12.wrap_socket(sock, server_hostname=hostname) as ssock:
                    results["supports_tls12"] = True
        except Exception:
            pass

    # Probe TLS 1.3
    if not results["supports_tls13"]:
        try:
            ctx13 = ssl.create_default_context()
            ctx13.minimum_version = ssl.TLSVersion.TLSv1_3
            ctx13.maximum_version = ssl.TLSVersion.TLSv1_3
            with socket.create_connection((hostname, 443), timeout=timeout) as sock:
                with ctx13.wrap_socket(sock, server_hostname=hostname) as ssock:
                    results["supports_tls13"] = True
        except Exception:
            pass

    return results


def check_ssl_certificate(hostname, timeout=6):
    """Inspects SSL certificate validity, expiration, and domain match."""
    res = {
        "valid": False,
        "issuer": "",
        "subject": "",
        "not_after": "",
        "days_left": None,
        "is_expired": False,
        "domain_match": False,
        "san_list": [],
        "error": None,
    }
    if not hostname:
        res["error"] = "No hostname"
        return res

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                issuer_dict = dict(x[0] for x in cert.get("issuer", ()))
                issuer = (issuer_dict.get("organizationName") or
                          issuer_dict.get("commonName") or "Trusted CA")
                subject_dict = dict(x[0] for x in cert.get("subject", ()))
                cn = subject_dict.get("commonName") or ""
                sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]

                not_after_str = cert.get("notAfter")
                days_left = None
                exp_date_str = ""
                if not_after_str:
                    exp_date = datetime.datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z")
                    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    days_left = (exp_date - now).days
                    exp_date_str = exp_date.strftime("%Y-%m-%d")

                # Verify domain match
                h_clean = hostname.lower()
                domain_match = False
                all_names = sans if sans else ([cn] if cn else [])
                for name in all_names:
                    n_clean = name.lower()
                    if n_clean == h_clean:
                        domain_match = True
                        break
                    if n_clean.startswith("*."):
                        parent = n_clean[2:]
                        if h_clean == parent or h_clean.endswith("." + parent):
                            domain_match = True
                            break

                res.update({
                    "valid": True,
                    "issuer": issuer,
                    "subject": cn,
                    "not_after": exp_date_str,
                    "days_left": days_left,
                    "is_expired": (days_left is not None and days_left <= 0),
                    "domain_match": domain_match,
                    "san_list": sans[:10],
                })
    except ssl.SSLCertVerificationError as e:
        res["valid"] = False
        res["error"] = f"Certificate verification failed: {e}"
        if "Hostname mismatch" in str(e):
            res["domain_match"] = False
    except Exception as e:
        res["valid"] = False
        res["error"] = str(e)
    return res


def check_hsts_header(headers):
    """Analyzes HSTS (Strict-Transport-Security) header attributes."""
    h_val = _norm_headers(headers).get("strict-transport-security", "")
    if not h_val:
        return {"present": False, "max_age": None, "subdomains": False, "preload": False, "raw": ""}
    
    max_age = None
    m = re.search(r"max-age\s*=\s*(\d+)", h_val, re.IGNORECASE)
    if m:
        try:
            max_age = int(m.group(1))
        except ValueError:
            pass
    subdomains = "includesubdomains" in h_val.lower()
    preload = "preload" in h_val.lower()
    return {
        "present": True,
        "max_age": max_age,
        "subdomains": subdomains,
        "preload": preload,
        "raw": h_val,
        "is_optimal": bool(max_age and max_age >= 31536000 and subdomains),
    }


def check_mixed_content_across_pages(all_facts):
    """Scans crawled HTML pages for HTTP resources loaded on HTTPS pages."""
    insecure_items = []
    for f in all_facts:
        f_url = f.get("final_url") or f.get("url") or ""
        if not f_url.startswith("https://"):
            continue
        html_src = f.get("html") or ""
        if not html_src:
            continue
        soup = BeautifulSoup(html_src, "lxml")
        for tag, attr in [("img", "src"), ("script", "src"), ("link", "href"),
                          ("iframe", "src"), ("video", "src"), ("audio", "src")]:
            for el in soup.find_all(tag):
                if tag == "link":
                    # Only links that load a subresource; canonical/alternate
                    # hrefs are just URLs, not mixed content.
                    rel = " ".join(el.get("rel") or []).lower()
                    if not re.search(r"stylesheet|icon|preload|prefetch|manifest", rel):
                        continue
                src = (el.get(attr) or "").strip()
                if src.lower().startswith("http://"):
                    insecure_items.append({"page": f_url, "resource": src, "type": tag})
                    if len(insecure_items) >= 20:
                        break
            if len(insecure_items) >= 20:
                break
        if len(insecure_items) >= 20:
            break
    return insecure_items


# ===========================================================================
# B. Security Headers
# ===========================================================================
def check_security_headers(headers):
    """Inspects essential defensive HTTP response headers."""
    norm = _norm_headers(headers)
    out = {}

    # Content-Security-Policy
    csp = norm.get("content-security-policy", "")
    out["csp"] = {
        "present": bool(csp),
        "raw": csp,
        "has_unsafe_inline": "unsafe-inline" in csp.lower(),
        "has_unsafe_eval": "unsafe-eval" in csp.lower(),
    }

    # X-Frame-Options
    xfo = norm.get("x-frame-options", "").upper().strip()
    out["x_frame_options"] = {
        "present": bool(xfo),
        "value": xfo,
        "is_secure": xfo in ("DENY", "SAMEORIGIN"),
    }

    # X-Content-Type-Options
    xcto = norm.get("x-content-type-options", "").lower().strip()
    out["x_content_type"] = {
        "present": bool(xcto),
        "value": xcto,
        "is_secure": xcto == "nosniff",
    }

    # Referrer-Policy
    rp = norm.get("referrer-policy", "").lower().strip()
    out["referrer_policy"] = {
        "present": bool(rp),
        "value": rp,
        "is_secure": rp in ("no-referrer", "strict-origin", "strict-origin-when-cross-origin", "same-origin"),
    }

    # Permissions-Policy
    pp = norm.get("permissions-policy") or norm.get("feature-policy") or ""
    out["permissions_policy"] = {
        "present": bool(pp),
        "value": pp,
    }

    return out


# ===========================================================================
# C. Cookies Security
# ===========================================================================
def check_cookies_security(headers_list):
    """Checks Secure, HttpOnly, and SameSite attributes on all set cookies."""
    cookies = []
    seen = set()

    for h in headers_list:
        if not h:
            continue
        # Set-Cookie headers can be passed as single string or comma/newline separated
        raw_cookies = []
        for k, v in h.items():
            if k.lower() == "set-cookie":
                raw_cookies.append(str(v))

        for raw in raw_cookies:
            # Handle multiple cookies in header
            parts = re.split(r",\s*(?=[a-zA-Z0-9_\-]+=)", raw)
            for part in parts:
                tokens = [t.strip() for t in part.split(";") if t.strip()]
                if not tokens:
                    continue
                name_val = tokens[0].split("=", 1)
                name = name_val[0].strip()
                if not name or name in seen:
                    continue
                seen.add(name)

                flags_lower = [t.lower() for t in tokens[1:]]
                is_secure = any(t == "secure" for t in flags_lower)
                is_httponly = any(t == "httponly" for t in flags_lower)
                samesite = None
                for t in tokens[1:]:
                    if t.lower().startswith("samesite"):
                        sp = t.split("=", 1)
                        if len(sp) > 1:
                            samesite = sp[1].strip()

                issues = []
                if not is_secure:
                    issues.append("Missing Secure flag (transmitted over unencrypted HTTP)")
                if not is_httponly:
                    issues.append("Missing HttpOnly flag (accessible to JavaScript / XSS theft)")
                if not samesite:
                    issues.append("Missing SameSite attribute (CSRF risk)")
                elif samesite.lower() == "none" and not is_secure:
                    issues.append("SameSite=None without Secure flag")

                cookies.append({
                    "name": name,
                    "secure": is_secure,
                    "httponly": is_httponly,
                    "samesite": samesite,
                    "issues": issues,
                })

    return cookies


# ===========================================================================
# D. Information Disclosure
# ===========================================================================
def check_version_leaks_in_headers(headers):
    """Detects server, runtime, and framework version banners in headers."""
    norm = _norm_headers(headers)
    leaks = []
    for hdr in ["server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version",
                "x-generator", "x-version", "x-runtime"]:
        val = norm.get(hdr)
        if val and re.search(r"\d+\.\d+", val):
            leaks.append({"header": hdr, "value": val})
    return leaks


def check_cms_version_in_html(html_str):
    """Detects CMS version disclosure in meta generator tags or asset scripts."""
    if not html_str:
        return []
    disclosures = []
    soup = BeautifulSoup(html_str, "lxml")
    
    # Meta generator tag
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^generator$", re.I)}):
        content = meta.get("content", "").strip()
        if content and re.search(r"\d+\.\d+", content):
            disclosures.append({"source": "<meta name='generator'>", "detail": content})

    # Script/Style query parameter version strings for WordPress / CMS core
    for tag, attr in [("script", "src"), ("link", "href")]:
        for el in soup.find_all(tag):
            src = el.get(attr, "")
            if "wp-includes" in src or "wp-admin" in src or "modules/system" in src:
                m = re.search(r"[?&]ver=([\d\.]+)", src)
                if m and len(m.group(1).split(".")) >= 2:
                    disclosures.append({"source": f"Asset query string ({tag})", "detail": f"Version {m.group(1)} in {src[:60]}..."})
                    break
        if disclosures:
            break

    return disclosures


def check_verbose_errors(crawled_pages):
    """Checks naturally encountered pages during crawl for verbose debug traces."""
    verbose_errors = []
    debug_signatures = [
        ("Laravel Whoops", r"Whoops! There was an error"),
        ("Django Debug", r"Django Version:.*Traceback \(most recent call last\)"),
        ("PHP Error Trace", r"Fatal error:.*in\s+/.*\s+on line\s+\d+"),
        ("ASP.NET Stack Trace", r"Server Error in '.*' Application\..*Stack Trace:"),
        ("SQL Syntax Leak", r"You have an error in your SQL syntax;"),
        ("Spring Boot Trace", r"Whitelabel Error Page.*timestamp.*status.*error"),
    ]
    for p in crawled_pages:
        html_content = p.get("html") or ""
        status = p.get("status", 0)
        # Only check naturally occurring 4xx, 5xx or unhandled pages
        if status >= 400 or "traceback" in html_content.lower() or "stack trace" in html_content.lower():
            for name, pattern in debug_signatures:
                if re.search(pattern, html_content, re.IGNORECASE):
                    verbose_errors.append({
                        "url": p.get("url") or p.get("final_url"),
                        "signature": name,
                        "status": status
                    })
                    break
    return verbose_errors


def check_directory_listing(origin, sample_dirs=None, timeout=5, proxy=None):
    """Checks discoverable asset folders for directory listing indexing."""
    dirs_to_check = sample_dirs or ["/wp-content/uploads/", "/uploads/", "/images/", "/assets/"]
    exposed = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": SEC_UA})
    for d in dirs_to_check:
        target = urljoin(origin, d)
        try:
            r = sess.get(target, timeout=timeout, proxies={"http": proxy, "https": proxy} if proxy else None, allow_redirects=False)
            if r.status_code == 200 and ("Index of /" in r.text or "Directory listing for" in r.text or "<title>Index of" in r.text):
                exposed.append({"path": d, "url": target})
        except Exception:
            continue
    return exposed


def check_sensitive_files(origin, timeout=5, proxy=None):
    """
    PASSIVE GET ONLY: Checks if standard sensitive config files are publicly exposed.
    RULES:
      - 200 OK with signature content = confirmed exposure.
      - 403 / 404 = not exposed.
      - NEVER read, output, or quote credentials found! Stop at 'file is publicly accessible'.
    """
    exposed = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": SEC_UA})

    for path, meta in SENSITIVE_FILES_MAP.items():
        target = urljoin(origin, "/" + path.lstrip("/"))
        try:
            r = sess.get(target, timeout=timeout, proxies={"http": proxy, "https": proxy} if proxy else None, allow_redirects=False)
            if r.status_code == 200:
                body_bytes = r.content[:2048]
                body_text = r.text[:2048]

                is_exposed = False
                # If magic bytes required
                if "magic" in meta and body_bytes.startswith(meta["magic"]):
                    is_exposed = True
                # If signatures required
                elif "sig" in meta:
                    # Must not be an HTML redirect or custom error page
                    if not (body_text.lower().startswith("<!doctype") or "<html" in body_text.lower()[:200]):
                        if any(sig in body_text for sig in meta["sig"]):
                            is_exposed = True

                if is_exposed:
                    exposed.append({
                        "path": "/" + path.lstrip("/"),
                        "url": target,
                        "name": meta["name"],
                        "status": 200,
                    })
        except Exception:
            continue
    return exposed


def check_exposed_sourcemaps(origin, crawled_pages, timeout=4, proxy=None):
    """Checks if production JavaScript source maps (.js.map) are publicly exposed."""
    sourcemap_urls = []
    seen = set()
    for p in crawled_pages[:5]:
        html_src = p.get("html") or ""
        soup = BeautifulSoup(html_src, "lxml")
        for sc in soup.find_all("script", src=True):
            src = sc["src"]
            full_src = urljoin(p.get("final_url") or origin, src)
            p_obj = urlparse(full_src)
            if p_obj.netloc == urlparse(origin).netloc and full_src.endswith(".js"):
                map_url = full_src + ".map"
                if map_url not in seen:
                    seen.add(map_url)
                    sourcemap_urls.append((full_src, map_url))
            if len(sourcemap_urls) >= 3:
                break

    exposed = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": SEC_UA})
    for js_url, map_url in sourcemap_urls:
        try:
            r = sess.get(map_url, timeout=timeout, proxies={"http": proxy, "https": proxy} if proxy else None, allow_redirects=False)
            if r.status_code == 200 and ('"version":' in r.text[:200] or '"sources":' in r.text[:200]):
                exposed.append({"js_url": js_url, "map_url": map_url})
        except Exception:
            continue
    return exposed


# ===========================================================================
# E. Outdated / Vulnerable Components
# ===========================================================================
def detect_js_libraries(html_str):
    """Detects JavaScript libraries and versions from script tags."""
    if not html_str:
        return []
    soup = BeautifulSoup(html_str, "lxml")
    libs = []
    seen = set()

    for sc in soup.find_all("script"):
        src = sc.get("src", "")
        text = sc.string or ""

        # jQuery
        if "jquery" in src.lower() or "jquery" in text.lower()[:300]:
            m = re.search(r"jquery[.-]([0-9]+\.[0-9]+(?:\.[0-9]+)?)", src.lower())
            if not m:
                m = re.search(r"jQuery\s*v([0-9]+\.[0-9]+(?:\.[0-9]+)?)", text[:500])
            if m:
                ver = m.group(1)
                k = ("jquery", ver)
                if k not in seen:
                    seen.add(k)
                    libs.append({"name": "jQuery", "version": ver, "key": "jquery"})

        # Bootstrap
        if "bootstrap" in src.lower():
            m = re.search(r"bootstrap[.-]([0-9]+\.[0-9]+(?:\.[0-9]+)?)", src.lower())
            if m:
                ver = m.group(1)
                k = ("bootstrap", ver)
                if k not in seen:
                    seen.add(k)
                    libs.append({"name": "Bootstrap", "version": ver, "key": "bootstrap"})

        # Moment.js
        if "moment" in src.lower():
            m = re.search(r"moment[.-]([0-9]+\.[0-9]+(?:\.[0-9]+)?)", src.lower())
            if m:
                ver = m.group(1)
                k = ("moment", ver)
                if k not in seen:
                    seen.add(k)
                    libs.append({"name": "Moment.js", "version": ver, "key": "moment"})

        # Lodash
        if "lodash" in src.lower():
            m = re.search(r"lodash[.-]([0-9]+\.[0-9]+(?:\.[0-9]+)?)", src.lower())
            if m:
                ver = m.group(1)
                k = ("lodash", ver)
                if k not in seen:
                    seen.add(k)
                    libs.append({"name": "Lodash", "version": ver, "key": "lodash"})

        # AngularJS (1.x)
        if "angular" in src.lower() and not ("angular2" in src.lower() or "@angular" in src.lower()):
            m = re.search(r"angular[.-]([0-9]+\.[0-9]+(?:\.[0-9]+)?)", src.lower())
            if m:
                ver = m.group(1)
                k = ("angularjs", ver)
                if k not in seen:
                    seen.add(k)
                    libs.append({"name": "AngularJS", "version": ver, "key": "angularjs"})

    return libs


def cross_reference_cves(libraries):
    """
    Cross-references detected libraries ONLY against genuine, named CVEs.
    If not proven vulnerable to a specific CVE, marks as hygiene/maintenance.
    """
    results = []
    for lib in libraries:
        key = lib.get("key")
        ver_str = lib.get("version", "")
        v_num = _parse_version(ver_str)
        rules = KNOWN_VULNERABLE_LIBRARIES.get(key, [])
        matched_cve = None

        for rule in rules:
            min_v = _parse_version(rule.get("min_version", "0.0.0"))
            max_v = _parse_version(rule.get("max_version", "99.99.99"))
            if min_v <= v_num <= max_v:
                matched_cve = rule
                break

        if matched_cve:
            results.append({
                "library": lib.get("name"),
                "version": ver_str,
                "is_vulnerable": True,
                "cve": matched_cve["cve"],
                "severity": matched_cve["sev"],
                "description": matched_cve["desc"],
            })
        else:
            # Outdated hygiene notice only (never invent CVEs)
            results.append({
                "library": lib.get("name"),
                "version": ver_str,
                "is_vulnerable": False,
                "cve": None,
                "severity": "low",
                "description": f"{lib.get('name')} {ver_str} detected. Confirm dependency patch status against vendor release notes.",
            })
    return results


# ===========================================================================
# F. DNS & Email Domain Trust (via DoH)
# ===========================================================================
def _query_doh_txt(name, timeout=4):
    """Performs DNS TXT lookup using Cloudflare / Google DNS-over-HTTPS.

    Returns a list of TXT strings ([] = the name has no TXT record), or None
    when the lookup itself failed (no resolver answered / SERVFAIL), so a
    failed lookup is never mistaken for a missing record."""
    endpoints = [
        f"https://cloudflare-dns.com/dns-query?name={name}&type=TXT",
        f"https://dns.google/resolve?name={name}&type=TXT",
    ]
    for url in endpoints:
        try:
            r = requests.get(url, headers={"Accept": "application/dns-json"}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                # DNS RCODE: 0 NOERROR, 3 NXDOMAIN are real answers; others
                # (e.g. 2 SERVFAIL) mean the lookup failed.
                if data.get("Status") not in (0, 3):
                    continue
                records = []
                for ans in data.get("Answer", []):
                    if ans.get("type") == 16:  # TXT
                        txt = ans.get("data", "").strip('"').replace('" "', '')
                        records.append(txt)
                return records
        except Exception:
            continue
    return None


def check_email_security_records(domain, timeout=4):
    """Checks SPF, DMARC, and DKIM DNS records. Strictly NO domain age check."""
    # "checked" is False when the DNS lookup failed — the record's presence is
    # then unknown and the matching check is reported as 'na'.
    res = {
        "spf": {"present": False, "record": None, "is_secure": False, "checked": False},
        "dmarc": {"present": False, "record": None, "policy": None, "is_secure": False, "checked": False},
        "dkim": {"present": False, "selector": None, "record": None, "checked": False},
    }
    clean_domain = (domain or "").lower()
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]
    if not clean_domain:
        return res

    # 1. SPF Record
    txts = _query_doh_txt(clean_domain, timeout=timeout)
    res["spf"]["checked"] = txts is not None
    for t in txts or []:
        if t.startswith("v=spf1"):
            res["spf"]["present"] = True
            res["spf"]["record"] = t
            # Check if strict (-all) or soft (~all)
            res["spf"]["is_secure"] = "-all" in t or "~all" in t
            break

    # 2. DMARC Record
    dmarc_txts = _query_doh_txt(f"_dmarc.{clean_domain}", timeout=timeout)
    res["dmarc"]["checked"] = dmarc_txts is not None
    for t in dmarc_txts or []:
        if t.startswith("v=DMARC1"):
            res["dmarc"]["present"] = True
            res["dmarc"]["record"] = t
            m = re.search(r"p=(reject|quarantine|none)", t, re.IGNORECASE)
            if m:
                policy = m.group(1).lower()
                res["dmarc"]["policy"] = policy
                res["dmarc"]["is_secure"] = policy in ("reject", "quarantine")
            break

    # 3. DKIM Record (check common standard selectors)
    for selector in ["google", "default", "k1", "mail", "smtp"]:
        dkim_name = f"{selector}._domainkey.{clean_domain}"
        dkim_txts = _query_doh_txt(dkim_name, timeout=timeout)
        if dkim_txts is not None:
            res["dkim"]["checked"] = True
        for t in dkim_txts or []:
            # An empty "p=" means the key was revoked — that is not a DKIM key.
            if re.search(r"(^|;)\s*p=[A-Za-z0-9+/]", t):
                res["dkim"]["present"] = True
                res["dkim"]["selector"] = selector
                res["dkim"]["record"] = t[:60] + "..."
                break
        if res["dkim"]["present"]:
            break

    return res


# ===========================================================================
# G. Authentication Surface (Passive Observation Only)
# ===========================================================================
def check_authentication_surface(origin, timeout=5, proxy=None):
    """
    Observational check of login endpoints.
    ZERO interaction or credential brute-force attempts.
    """
    res = {
        "login_found": False,
        "login_path": None,
        "is_default_path": False,
        "captcha_present": False,
        "mfa_mentioned": False,
        "xmlrpc_reachable": False,
    }
    sess = requests.Session()
    sess.headers.update({"User-Agent": SEC_UA})
    proxies = {"http": proxy, "https": proxy} if proxy else None

    # Check common login URLs
    login_candidates = ["/wp-login.php", "/wp-admin/", "/login", "/admin", "/signin"]
    for path in login_candidates:
        target = urljoin(origin, path)
        try:
            r = sess.get(target, timeout=timeout, proxies=proxies, allow_redirects=True)
            if r.status_code == 200:
                body_lower = r.text.lower()
                # Confirm it's genuinely a login interface
                if ('type="password"' in body_lower or "name=\"pwd\"" in body_lower or
                    "login" in r.url.lower() or "log in" in body_lower[:1000]):
                    res["login_found"] = True
                    res["login_path"] = path
                    res["is_default_path"] = path in ("/wp-login.php", "/wp-admin/", "/admin")

                    # CAPTCHA detection
                    if any(c in body_lower for c in ["recaptcha", "hcaptcha", "turnstile", "g-recaptcha", "h-captcha", "cf-turnstile"]):
                        res["captcha_present"] = True

                    # 2FA / MFA mention
                    if any(m in body_lower for m in ["2fa", "two-factor", "multi-factor", "authenticator", "verification code", "mfa"]):
                        res["mfa_mentioned"] = True
                    break
        except Exception:
            continue

    # xmlrpc.php check (WordPress) — reachability probe ONLY; NEVER invoke RPC methods
    xmlrpc_url = urljoin(origin, "/xmlrpc.php")
    try:
        rx = sess.get(xmlrpc_url, timeout=timeout, proxies=proxies)
        if rx.status_code in (200, 405) and ("XML-RPC server accepts POST requests only" in rx.text or "xmlrpc" in rx.text.lower()):
            res["xmlrpc_reachable"] = True
    except Exception:
        pass

    return res


# ===========================================================================
# H. Bot / WAF Detection
# ===========================================================================
def check_waf_and_rate_limits(headers, cookies=None):
    """Identifies WAF / CDN signatures and rate-limit response headers."""
    norm = _norm_headers(headers)
    cookie_str = str(cookies or "").lower()

    waf_provider = None
    if any(k in norm for k in ["cf-ray", "cf-cache-status"]) or "cloudflare" in norm.get("server", "").lower() or "__cfduid" in cookie_str:
        waf_provider = "Cloudflare"
    elif "x-sucuri-id" in norm or "x-sucuri-cache" in norm or "sucuri" in norm.get("server", "").lower():
        waf_provider = "Sucuri"
    elif "x-akamai-transformed" in norm or "akamai" in norm.get("server", "").lower():
        waf_provider = "Akamai"
    elif "x-amz-cf-id" in norm or "cloudfront" in norm.get("via", "").lower():
        waf_provider = "AWS CloudFront"
    elif "x-iinfo" in norm or "incap_ses" in cookie_str or "visid_incap" in cookie_str:
        waf_provider = "Imperva / Incapsula"
    elif "x-fastly-request-id" in norm:
        waf_provider = "Fastly"

    # Rate limiting headers
    rate_headers = []
    for k, v in norm.items():
        if "ratelimit" in k or k == "retry-after":
            rate_headers.append(f"{k}: {v}")

    return {
        "waf_detected": bool(waf_provider),
        "waf_provider": waf_provider,
        "rate_limiting_active": bool(rate_headers),
        "rate_headers": rate_headers,
    }


# ===========================================================================
# I. Technology Fingerprinting
# ===========================================================================
def fingerprint_technology(headers, html_str):
    """Passive fingerprinting of CMS, frameworks, and web server."""
    norm = _norm_headers(headers)
    html_lower = (html_str or "").lower()

    # CMS Detection
    cms = None
    cms_indicator = ""
    if "wp-content" in html_lower or "wp-includes" in html_lower:
        cms = "WordPress"
        cms_indicator = "wp-content/wp-includes structure in HTML"
    elif "cdn.shopify.com" in html_lower or "shopify.theme" in html_lower:
        cms = "Shopify"
        cms_indicator = "Shopify CDN and theme scripts"
    elif "drupal.settings" in html_lower or "sites/default/files" in html_lower:
        cms = "Drupal"
        cms_indicator = "Drupal settings and site paths"
    elif "components/com_" in html_lower or "joomla!" in html_lower:
        cms = "Joomla"
        cms_indicator = "Joomla component structure in HTML"
    elif "static.wixstatic.com" in html_lower or "wix.com" in html_lower:
        cms = "Wix"
        cms_indicator = "Wix static media CDN assets"
    elif "squarespace.com" in html_lower or "static1.squarespace.com" in html_lower:
        cms = "Squarespace"
        cms_indicator = "Squarespace CDN assets"
    elif "webflow.com" in html_lower or "data-wf-page" in html_lower:
        cms = "Webflow"
        cms_indicator = "Webflow designer attributes"

    # Frontend Frameworks
    frameworks = []
    if "data-reactroot" in html_lower or "react" in html_lower[:2000]:
        frameworks.append("React")
    if "__next_data__" in html_lower or "/_next/" in html_lower:
        frameworks.append("Next.js")
    if "data-v-" in html_lower or "__nuxt" in html_lower:
        frameworks.append("Vue / Nuxt")
    if "ng-version" in html_lower or "ng-app" in html_lower:
        frameworks.append("Angular")
    if "tailwind" in html_lower:
        frameworks.append("Tailwind CSS")
    if "bootstrap" in html_lower:
        frameworks.append("Bootstrap")

    # Hosting / CDN from headers
    server_hdr = norm.get("server", "")
    via_hdr = norm.get("via", "")
    hosting_cdn = server_hdr or via_hdr or "Standard Web Hosting"

    return {
        "cms": cms,
        "cms_indicator": cms_indicator,
        "frameworks": frameworks,
        "hosting_cdn": hosting_cdn,
    }


# ===========================================================================
# Master Security Runner & Check Catalog Generator
# ===========================================================================
def run_full_security_audit(url, home_page, all_facts, proxy=None, timeout=6):
    """
    Executes the complete passive security audit pipeline across all categories (A–I).
    Returns an aggregated dictionary consumed by seo_checks.py.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or parsed.netloc
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = home_page.get("headers") or {}
    html_str = home_page.get("html") or ""

    # A. Transport Security
    tls_info = check_tls_support(hostname, timeout=timeout)
    ssl_info = check_ssl_certificate(hostname, timeout=timeout)
    hsts_info = check_hsts_header(headers)
    # all_facts must be raw page objects (with "html"); parsed facts carry no
    # HTML, and then the HTML-based checks below are reported as 'na'.
    html_pages_scanned = sum(1 for f in all_facts if f.get("html"))
    mixed_content = check_mixed_content_across_pages(all_facts)

    # B. Security Headers
    sec_headers = check_security_headers(headers)

    # C. Cookies
    all_headers = [f.get("headers") for f in all_facts if f.get("headers")]
    cookies_info = check_cookies_security(all_headers)

    # D. Information Disclosure
    version_leaks = check_version_leaks_in_headers(headers)
    cms_version_leaks = check_cms_version_in_html(html_str)
    verbose_errors = check_verbose_errors(all_facts)
    dir_listings = check_directory_listing(origin, timeout=timeout, proxy=proxy)
    sensitive_files = check_sensitive_files(origin, timeout=timeout, proxy=proxy)
    sourcemaps = check_exposed_sourcemaps(origin, all_facts, timeout=timeout, proxy=proxy)

    # E. Outdated / Vulnerable Components
    js_libs = detect_js_libraries(html_str)
    cve_findings = cross_reference_cves(js_libs)

    # F. DNS & Email Domain Trust
    email_trust = check_email_security_records(hostname, timeout=timeout)

    # G. Authentication Surface
    auth_surface = check_authentication_surface(origin, timeout=timeout, proxy=proxy)

    # H. Bot / WAF Detection
    waf_info = check_waf_and_rate_limits(headers, cookies=home_page.get("cookies"))

    # I. Technology Fingerprint
    tech_info = fingerprint_technology(headers, html_str)

    return {
        "tls": tls_info,
        "ssl": ssl_info,
        "hsts": hsts_info,
        "mixed_content": mixed_content,
        "html_pages_scanned": html_pages_scanned,
        "sec_headers": sec_headers,
        "cookies": cookies_info,
        "version_leaks": version_leaks,
        "cms_version_leaks": cms_version_leaks,
        "verbose_errors": verbose_errors,
        "dir_listings": dir_listings,
        "sensitive_files": sensitive_files,
        "sourcemaps": sourcemaps,
        "cve_findings": cve_findings,
        "email_trust": email_trust,
        "auth_surface": auth_surface,
        "waf": waf_info,
        "tech": tech_info,
    }


def generate_security_checks_catalog(sec_data, ctx, home):
    """
    Generates normalized check dictionaries for seo_checks.py.
    Category: 'security'. Statuses: 'pass', 'warn', 'fail', 'info' (unscored), 'na' (not checked, unscored).
    """
    out = []
    final_url = home.get("final_url") or home.get("url") or ""
    is_https = final_url.startswith("https://")

    # Helper factory
    def _c(cid, label, status, severity, effort, detail, fix="", where=None, scope="home", why=""):
        return {
            "id": cid, "category": "security", "label": label, "status": status,
            "severity": severity, "effort": effort, "detail": detail, "fix": fix,
            "why": why or "Protects customer data, prevents browser security blocks, and maintains search engine trust.",
            "impact": "Security flaws damage domain trust, trigger browser warnings, and directly impair organic visibility.",
            "gain": "High" if severity in ("critical", "high") else "Medium",
            "where": where or [final_url], "scope": scope,
        }

    # =========================================================================
    # A. Transport Security Checks
    # =========================================================================
    # 1. HTTPS Active
    out.append(_c(
        "sec_https_active", "HTTPS Protocol Enforcement",
        "pass" if is_https else "fail",
        "critical", "med",
        "Site is served securely over HTTPS." if is_https else "Site is served over insecure HTTP.",
        "" if is_https else "Install an SSL/TLS certificate and force all web traffic over HTTPS.",
        why="HTTPS is a fundamental trust baseline and confirmed Google ranking signal."
    ))

    # 2. HTTP -> HTTPS Redirect
    http_redir = ctx.get("http_redirect")
    if is_https:
        if http_redir is None:
            out.append(_c(
                "sec_http_redirect", "HTTP to HTTPS Automatic Redirect",
                "na", "critical", "low",
                "The http:// version could not be tested, so the redirect was not checked.",
                ""
            ))
        elif not http_redir.get("reachable"):
            out.append(_c(
                "sec_http_redirect", "HTTP to HTTPS Automatic Redirect",
                "pass", "low", "low",
                "HTTP port closed (only HTTPS served).",
                ""
            ))
        else:
            redir_ok = bool(http_redir.get("redirects_to_https"))
            out.append(_c(
                "sec_http_redirect", "HTTP to HTTPS Automatic Redirect",
                "pass" if redir_ok else "fail",
                "critical", "low",
                "Insecure HTTP requests automatically redirect to HTTPS." if redir_ok else "HTTP origin does not redirect to HTTPS, allowing unencrypted access.",
                "" if redir_ok else "Configure a 301 permanent redirect from HTTP to HTTPS in server configuration.",
                why="Unredirected HTTP endpoints split PageRank equity and expose user traffic to eavesdropping."
            ))

    # 3. SSL/TLS Certificate Expiry & Validity
    ssl_info = sec_data.get("ssl") or {}
    if ssl_info.get("valid"):
        days = ssl_info.get("days_left")
        issuer = ssl_info.get("issuer") or "Trusted CA"
        if days is not None and days <= 0:
            st, sev, det, fix = "fail", "critical", f"SSL certificate EXPIRED on {ssl_info.get('not_after')} ({issuer}).", "Renew SSL certificate immediately to clear browser warning blocks."
        elif days is not None and days < 30:
            st, sev, det, fix = "warn", "high", f"SSL certificate expires in {days} days on {ssl_info.get('not_after')} ({issuer}).", "Renew SSL certificate before expiration."
        else:
            st, sev, det, fix = "pass", "low", f"SSL certificate valid for {days} days until {ssl_info.get('not_after')} (Issued by {issuer}).", ""
        out.append(_c("sec_ssl_cert_expiry", "SSL Certificate Expiration & Validity", st, sev, "low", det, fix, scope="site"))

        # 4. Domain Name Match (SAN / CN)
        domain_match = ssl_info.get("domain_match", True)
        out.append(_c(
            "sec_ssl_domain_match", "SSL Certificate Domain Name Match",
            "pass" if domain_match else "fail",
            "critical", "med",
            "Certificate Common Name and SAN match the target domain." if domain_match else "Certificate Subject Alternative Name does NOT match target domain name.",
            "" if domain_match else "Re-issue SSL certificate to include all domain variants and subdomains.",
            why="Domain mismatch triggers alarming full-screen 'Your connection is not private' security blocks in Chrome/Safari."
        ))
    elif is_https:
        out.append(_c(
            "sec_ssl_cert_expiry", "SSL Certificate Validation",
            "fail", "critical", "med",
            f"SSL certificate validation failed: {ssl_info.get('error', 'Invalid certificate')}",
            "Install a valid SSL certificate signed by a trusted Certificate Authority.",
            scope="site"
        ))

    # 5. TLS Protocols (Check deprecated TLS 1.0 / 1.1)
    tls_info = sec_data.get("tls") or {}
    if tls_info.get("deprecated_protocols_accepted"):
        dep_list = []
        if tls_info.get("supports_tls10"):
            dep_list.append("TLS 1.0")
        if tls_info.get("supports_tls11"):
            dep_list.append("TLS 1.1")
        out.append(_c(
            "sec_tls_deprecated_protocols", "Deprecated TLS Protocols (TLS 1.0/1.1)",
            "fail", "high", "low",
            f"Server accepts insecure deprecated protocols: {', '.join(dep_list)}.",
            "Disable TLS 1.0 and TLS 1.1 in web server/load balancer. Enforce TLS 1.2 and TLS 1.3 only.",
            why="TLS 1.0 and 1.1 have known cryptographic flaws (POODLE, BEAST) and are deprecated by all major browsers."
        ))
    elif tls_info.get("negotiated") and tls_info.get("legacy_probe_complete"):
        out.append(_c(
            "sec_tls_deprecated_protocols", "Modern TLS Protocol Enforcement",
            "pass", "low", "low",
            f"Legacy TLS 1.0/1.1 refused by the server; negotiated {tls_info.get('negotiated')}.",
            ""
        ))
    else:
        out.append(_c(
            "sec_tls_deprecated_protocols", "Deprecated TLS Protocols (TLS 1.0/1.1)",
            "na", "high", "low",
            "Legacy TLS 1.0/1.1 support could not be tested from this computer, so it was not checked.",
            ""
        ))

    # 6. Mixed Content
    mixed = sec_data.get("mixed_content") or []
    if is_https:
        if mixed:
            sample_res = [m["resource"] for m in mixed[:3]]
            out.append(_c(
                "sec_mixed_content", "Mixed Content (Insecure HTTP Assets on HTTPS)",
                "fail", "high", "med",
                f"Detected {len(mixed)} insecure http:// subresource(s) on HTTPS pages.",
                "Update resource links in templates and database to use relative or https:// URLs.",
                where=[m["page"] for m in mixed[:2]],
                why="Modern browsers block mixed-content scripts and show broken lock icons for insecure images."
            ))
        elif not sec_data.get("html_pages_scanned"):
            out.append(_c(
                "sec_mixed_content", "Mixed Content Protection",
                "na", "high", "low",
                "Page HTML was not available to scan, so mixed content was not checked.",
                ""
            ))
        else:
            out.append(_c(
                "sec_mixed_content", "Mixed Content Protection",
                "pass", "low", "low",
                "No insecure http:// subresources detected across %d scanned page(s)."
                % sec_data.get("html_pages_scanned"),
                ""
            ))

    # 7. Strict-Transport-Security (HSTS)
    hsts = sec_data.get("hsts") or {}
    if not hsts.get("present"):
        out.append(_c(
            "sec_hsts", "HTTP Strict Transport Security (HSTS)",
            "warn", "medium", "low",
            "HSTS header missing from server response.",
            "Add header 'Strict-Transport-Security: max-age=31536000; includeSubDomains; preload'.",
            why="HSTS forces browsers to connect over HTTPS only, preventing SSL-stripping man-in-the-middle attacks."
        ))
    elif not hsts.get("is_optimal"):
        out.append(_c(
            "sec_hsts", "HTTP Strict Transport Security (HSTS) Configuration",
            "warn", "low", "low",
            f"HSTS present ({hsts.get('raw')}) but max-age is short or missing includeSubDomains.",
            "Upgrade HSTS max-age to 31536000 (1 year) and includeSubDomains directive.",
            why="A 1-year max-age is required for Chrome/Edge HSTS Preload list eligibility."
        ))
    else:
        out.append(_c(
            "sec_hsts", "HTTP Strict Transport Security (HSTS)",
            "pass", "low", "low",
            f"HSTS enforced with optimal 1-year max-age ({hsts.get('raw')}).",
            ""
        ))

    # =========================================================================
    # B. Security Headers Checks
    # =========================================================================
    headers_info = sec_data.get("sec_headers") or {}

    # CSP
    csp = headers_info.get("csp") or {}
    if not csp.get("present"):
        out.append(_c(
            "sec_csp", "Content-Security-Policy (CSP)",
            "warn", "medium", "med",
            "Content-Security-Policy header is missing.",
            "Deploy a Content-Security-Policy header to restrict unauthorized script execution and data exfiltration.",
            why="CSP is the primary defense against Cross-Site Scripting (XSS) and code injection attacks."
        ))
    elif csp.get("has_unsafe_inline") or csp.get("has_unsafe_eval"):
        out.append(_c(
            "sec_csp", "Content-Security-Policy (CSP) Policy Strength",
            "warn", "low", "med",
            "CSP header contains 'unsafe-inline' or 'unsafe-eval' directives.",
            "Refactor inline scripts to external files and use nonces or hashes to eliminate 'unsafe-inline'.",
            why="Permitting 'unsafe-inline' significantly weakens CSP protection against XSS."
        ))
    else:
        out.append(_c("sec_csp", "Content-Security-Policy (CSP)", "pass", "low", "low", "Content-Security-Policy header active.", ""))

    # X-Frame-Options
    xfo = headers_info.get("x_frame_options") or {}
    if not xfo.get("present"):
        out.append(_c(
            "sec_x_frame_options", "X-Frame-Options (Clickjacking Protection)",
            "warn", "medium", "low",
            "X-Frame-Options header missing (or missing CSP frame-ancestors).",
            "Set 'X-Frame-Options: SAMEORIGIN' or configure CSP 'frame-ancestors 'self''.",
            why="Protects visitors from clickjacking attacks where your website is embedded inside malicious invisible iframes."
        ))
    else:
        out.append(_c("sec_x_frame_options", "X-Frame-Options (Clickjacking Protection)", "pass", "low", "low", f"X-Frame-Options configured: {xfo.get('value')}.", ""))

    # X-Content-Type-Options
    xcto = headers_info.get("x_content_type") or {}
    if not xcto.get("is_secure"):
        out.append(_c(
            "sec_x_content_type", "X-Content-Type-Options (MIME Sniffing)",
            "warn", "low", "low",
            "X-Content-Type-Options: nosniff header is missing.",
            "Add header 'X-Content-Type-Options: nosniff'.",
            why="Prevents browsers from MIME-sniffing a response away from the declared content-type, blocking drive-by attacks."
        ))
    else:
        out.append(_c("sec_x_content_type", "X-Content-Type-Options", "pass", "low", "low", "X-Content-Type-Options: nosniff active.", ""))

    # Referrer-Policy
    rp = headers_info.get("referrer_policy") or {}
    if not rp.get("present"):
        out.append(_c(
            "sec_referrer_policy", "Referrer-Policy Header",
            "warn", "low", "low",
            "Referrer-Policy header is missing.",
            "Add header 'Referrer-Policy: strict-origin-when-cross-origin'.",
            why="Controls how much referrer information is sent with outbound requests, preventing sensitive URL parameter leakage."
        ))
    else:
        out.append(_c("sec_referrer_policy", "Referrer-Policy Header", "pass", "low", "low", f"Referrer-Policy configured: {rp.get('value')}.", ""))

    # Permissions-Policy
    pp = headers_info.get("permissions_policy") or {}
    if not pp.get("present"):
        out.append(_c(
            "sec_permissions_policy", "Permissions-Policy Header",
            "warn", "low", "low",
            "Permissions-Policy header is missing.",
            "Add Permissions-Policy to restrict browser features (camera, microphone, geolocation) to authorized origins.",
            why="Restricts access to hardware and browser APIs from embedded third-party scripts."
        ))
    else:
        out.append(_c("sec_permissions_policy", "Permissions-Policy Header", "pass", "low", "low", "Permissions-Policy active.", ""))

    # =========================================================================
    # C. Cookies Security Checks
    # =========================================================================
    cookies = sec_data.get("cookies") or []
    if cookies:
        flawed_cookies = [c for c in cookies if c.get("issues")]
        if flawed_cookies:
            sample_names = ", ".join(c["name"] for c in flawed_cookies[:3])
            issue_summary = "; ".join(flawed_cookies[0]["issues"])
            out.append(_c(
                "sec_cookie_flags", "Cookie Security Flags (Secure, HttpOnly, SameSite)",
                "warn", "medium", "low",
                f"{len(flawed_cookies)} cookie(s) ({sample_names}) have insecure flags: {issue_summary}.",
                "Set Secure, HttpOnly, and SameSite=Lax (or Strict) on all session and application cookies.",
                why="Missing Secure allows cookie interception over HTTP; missing HttpOnly allows XSS session hijacking; missing SameSite allows CSRF."
            ))
        else:
            out.append(_c(
                "sec_cookie_flags", "Cookie Security Flags",
                "pass", "low", "low",
                f"All {len(cookies)} detected cookie(s) have Secure, HttpOnly, and SameSite attributes configured.",
                ""
            ))

    # =========================================================================
    # D. Information Disclosure Checks
    # =========================================================================
    # 1. Server / Tech Version Leaks
    v_leaks = sec_data.get("version_leaks") or []
    if v_leaks:
        leaks_str = ", ".join(f"{l['header']}: {l['value']}" for l in v_leaks)
        out.append(_c(
            "sec_info_server_version", "Server / Software Version Disclosure",
            "warn", "medium", "low",
            f"Exact software versions disclosed in HTTP headers: {leaks_str}.",
            "Configure server to suppress version banners (e.g. 'ServerTokens Prod' in Apache, 'server_tokens off;' in Nginx, remove X-Powered-By).",
            why="Revealing exact software versions assists attackers in targeting known CVE exploits."
        ))
    else:
        out.append(_c("sec_info_server_version", "Server Version Suppression", "pass", "low", "low", "Server and technology version banners are masked.", ""))

    # 2. CMS Version in HTML
    cms_leaks = sec_data.get("cms_version_leaks") or []
    if cms_leaks:
        detail_msg = cms_leaks[0]["detail"]
        out.append(_c(
            "sec_info_cms_version", "CMS Core Version Disclosure",
            "warn", "low", "low",
            f"CMS version disclosed in HTML: {detail_msg}.",
            "Remove meta generator tags and strip version query strings (?ver=) from asset URLs.",
            why="Revealing exact CMS core versions simplifies automated vulnerability scanning by bots."
        ))

    # 3. Verbose Error Pages
    verbose = sec_data.get("verbose_errors") or []
    if verbose:
        out.append(_c(
            "sec_info_verbose_errors", "Verbose Error Pages / Stack Trace Exposure",
            "fail", "high", "med",
            f"Detected debug trace or verbose stack trace: {verbose[0]['signature']} at {verbose[0]['url']}.",
            "Disable debug mode (e.g. APP_DEBUG=false) and implement custom generic 404/500 error templates.",
            where=[v["url"] for v in verbose[:2]],
            why="Stack traces expose database structure, internal filesystem paths, and application secrets."
        ))

    # 4. Directory Listing
    dir_list = sec_data.get("dir_listings") or []
    if dir_list:
        exposed_paths = ", ".join(d["path"] for d in dir_list)
        out.append(_c(
            "sec_info_directory_listing", "Public Directory Listing Enabled",
            "fail", "high", "low",
            f"Directory listing indexing enabled on: {exposed_paths}.",
            "Disable directory indexing on web server (e.g. 'Options -Indexes' in Apache, 'autoindex off;' in Nginx).",
            where=[d["url"] for d in dir_list],
            why="Directory listing exposes private uploads, backups, and staging files to unauthenticated crawling."
        ))

    # 5. Publicly Accessible Sensitive Files
    sens_files = sec_data.get("sensitive_files") or []
    if sens_files:
        exposed_names = ", ".join(f["name"] for f in sens_files)
        out.append(_c(
            "sec_info_sensitive_files", "Publicly Accessible Sensitive Files",
            "fail", "critical", "low",
            f"CRITICAL EXPOSURE: Sensitive configuration/backup file is publicly accessible: {exposed_names}.",
            "Immediately block access to hidden files and backups via server rules and delete exposed backup files.",
            where=[f["url"] for f in sens_files],
            why="Exposed .git, .env, or backup files allow complete compromise of source code, database credentials, and API keys."
        ))
    else:
        out.append(_c("sec_info_sensitive_files", "Sensitive File Access Protection", "pass", "low", "low", "No exposed configuration or backup files detected (.git, .env, backup.zip, phpinfo.php).", ""))

    # 6. Exposed JS Source Maps
    sourcemaps = sec_data.get("sourcemaps") or []
    if sourcemaps:
        out.append(_c(
            "sec_info_sourcemaps", "Exposed JavaScript Source Maps (.js.map)",
            "warn", "low", "low",
            f"Production JavaScript source map (.map) is publicly downloadable: {sourcemaps[0]['map_url']}.",
            "Exclude .map files from production deployments or restrict source map access to authenticated developers.",
            where=[s["map_url"] for s in sourcemaps[:2]],
            why="Source maps reconstruct complete unminified frontend source code, comments, and internal logic."
        ))

    # =========================================================================
    # E. Outdated / Vulnerable Components
    # =========================================================================
    cve_findings = sec_data.get("cve_findings") or []
    for finding in cve_findings:
        lib_name = finding["library"]
        ver = finding["version"]
        if finding["is_vulnerable"]:
            out.append(_c(
                f"sec_cve_{lib_name.lower()}", f"Vulnerable Component: {lib_name} {ver}",
                "fail", finding["severity"], "med",
                f"Known vulnerability {finding['cve']}: {finding['description']}",
                f"Update {lib_name} to the latest stable patched release immediately.",
                why=f"Publicly known CVE ({finding['cve']}) allows automated exploitation by attackers."
            ))
        else:
            out.append(_c(
                f"sec_lib_{lib_name.lower()}", f"Component Hygiene: {lib_name} {ver}",
                "info", "low", "low",
                f"{lib_name} version {ver} detected with no confirmed critical CVEs.",
                ""
            ))

    # =========================================================================
    # F. DNS & Email Domain Trust Checks
    # =========================================================================
    email = sec_data.get("email_trust") or {}

    # SPF
    spf = email.get("spf") or {}
    if not spf.get("present") and not spf.get("checked", True):
        out.append(_c("sec_dns_spf", "SPF Email Authentication Record", "na", "high", "low",
                      "DNS lookup failed, so the SPF record was not checked.", scope="site"))
    elif not spf.get("present"):
        out.append(_c(
            "sec_dns_spf", "SPF Email Authentication Record",
            "fail", "high", "low",
            "No SPF (Sender Policy Framework) TXT record found on domain.",
            "Publish an SPF TXT record on your root domain (e.g. 'v=spf1 include:_spf.google.com ~all').",
            scope="site",
            why="Without an SPF record, phishers and spammers can freely spoof emails from your domain name."
        ))
    else:
        out.append(_c("sec_dns_spf", "SPF Email Authentication Record", "pass", "low", "low", f"SPF record active ({spf.get('record')}).", scope="site"))

    # DMARC
    dmarc = email.get("dmarc") or {}
    if not dmarc.get("present") and not dmarc.get("checked", True):
        out.append(_c("sec_dns_dmarc", "DMARC Phishing Protection Record", "na", "high", "low",
                      "DNS lookup failed, so the DMARC record was not checked.", scope="site"))
    elif not dmarc.get("present"):
        out.append(_c(
            "sec_dns_dmarc", "DMARC Phishing Protection Record",
            "fail", "high", "low",
            "No DMARC record found at _dmarc TXT record.",
            "Create a DMARC record at '_dmarc.yourdomain.com' with policy 'v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com'.",
            scope="site",
            why="DMARC prevents domain spoofing, protects brand reputation, and improves marketing email deliverability."
        ))
    elif dmarc.get("policy") == "none":
        out.append(_c(
            "sec_dns_dmarc", "DMARC Enforcement Policy",
            "warn", "medium", "low",
            f"DMARC is in monitoring mode only (p=none): {dmarc.get('record')}.",
            "Upgrade DMARC policy from 'p=none' to 'p=quarantine' or 'p=reject' to actively block spoofed emails.",
            scope="site",
            why="p=none does not prevent unauthorized senders from delivering fraudulent emails using your domain."
        ))
    else:
        out.append(_c("sec_dns_dmarc", "DMARC Phishing Protection Record", "pass", "low", "low", f"DMARC enforced with policy p={dmarc.get('policy')}.", scope="site"))

    # DKIM
    dkim = email.get("dkim") or {}
    if dkim.get("present"):
        out.append(_c("sec_dns_dkim", "DKIM Email Signature Record", "pass", "low", "low", f"DKIM record discovered on selector '{dkim.get('selector')}'.", scope="site"))
    elif not dkim.get("checked", True):
        out.append(_c("sec_dns_dkim", "DKIM Email Signature Record", "na", "low", "low",
                      "DNS lookup failed, so DKIM was not checked.", scope="site"))

    # =========================================================================
    # G. Authentication Surface Checks
    # =========================================================================
    auth = sec_data.get("auth_surface") or {}
    if auth.get("login_found"):
        path = auth.get("login_path")
        if auth.get("is_default_path"):
            out.append(_c(
                "sec_auth_login_path", "Default Login Path Exposure",
                "warn", "low", "low",
                f"Standard default administrative login path is publicly discoverable at '{path}'.",
                "Move administrative login to a custom obscured URL or restrict access by IP whitelist / VPN.",
                where=[urljoin(final_url, path)],
                why="Default login URLs (/wp-login.php, /admin) attract automated dictionary and brute-force bot attacks."
            ))
        if not auth.get("captcha_present"):
            out.append(_c(
                "sec_auth_captcha", "Login Form Brute-Force Protection (CAPTCHA)",
                "warn", "medium", "low",
                f"No CAPTCHA or bot verification script detected on login page ({path}).",
                "Install Google reCAPTCHA v3, Cloudflare Turnstile, or hCaptcha on login forms.",
                where=[urljoin(final_url, path)],
                why="Absence of CAPTCHA exposes user accounts to automated credential stuffing attacks."
            ))
        else:
            out.append(_c("sec_auth_captcha", "Login Form Bot Protection", "pass", "low", "low", f"CAPTCHA bot verification active on login page ({path}).", where=[urljoin(final_url, path)]))

    # WordPress XML-RPC
    if auth.get("xmlrpc_reachable"):
        out.append(_c(
            "sec_auth_xmlrpc", "WordPress XML-RPC Endpoint Reachability",
            "warn", "medium", "low",
            "xmlrpc.php is publicly reachable on server.",
            "Disable XML-RPC via web server rules or security plugin if mobile app publishing is not required.",
            where=[urljoin(final_url, "/xmlrpc.php")],
            why="xmlrpc.php is a frequent target for brute-force amplification and Layer 7 DDoS attacks."
        ))

    # =========================================================================
    # H. Bot / WAF Detection Checks
    # =========================================================================
    waf = sec_data.get("waf") or {}
    if waf.get("waf_detected"):
        out.append(_c(
            "sec_waf_protection", "Web Application Firewall (WAF) & DDoS Shield",
            "pass", "low", "low",
            f"Active WAF/CDN protection detected ({waf.get('waf_provider')}).",
            ""
        ))
    else:
        out.append(_c(
            "sec_waf_protection", "Web Application Firewall (WAF) Protection",
            "warn", "medium", "low",
            "No edge WAF or Cloud DDoS mitigation proxy detected (Cloudflare, Sucuri, Akamai, AWS CloudFront).",
            "Deploy a cloud WAF (such as Cloudflare or AWS WAF) in front of your origin server.",
            why="A WAF filters malicious bot traffic, blocks SQLi/XSS payloads, and protects against volumetric DDoS attacks."
        ))

    # =========================================================================
    # I. Technology Fingerprint Checks (Informational / Pass)
    # =========================================================================
    tech = sec_data.get("tech") or {}
    if tech.get("cms"):
        out.append(_c(
            "sec_tech_cms", f"CMS Detection: {tech.get('cms')}",
            "info", "low", "low",
            f"Detected {tech.get('cms')} based on {tech.get('cms_indicator')}.",
            ""
        ))

    return out
