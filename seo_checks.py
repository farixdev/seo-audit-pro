# -*- coding: utf-8 -*-
"""
Check-catalog engine — the SEO knowledge of a world-class specialist, encoded.

Eight scored categories (Technical, On-Page, Performance, Content, Mobile,
Accessibility, Security, UX) roll up into an overall score. Every check produces
a normalized record:

    {
      "id", "category", "label",
      "status":   pass | warn | fail | info | na,
      "severity": critical | high | medium | low,
      "effort":   low | med | high,
      "detail":   what was found,
      "fix":      remediation guidance,
      "why":      why it matters (optional; else derived),
      "impact":   SEO impact (optional; else derived),
      "gain":     estimated SEO gain High/Medium/Low (optional; else derived),
      "where":    [urls] the finding applies to,
      "scope":    page | home | site,
    }

On-page detail is evaluated on a representative homepage; site-wide aggregates
(duplicate/missing titles & metas, thin pages, broken links, non-200 pages,
noindex pages) span the whole crawl. Performance / Core Web Vitals / real
Accessibility audits come from PageSpeed Insights (Lighthouse); the rest is
computed from the crawl with no paid APIs.
"""

import re
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

import security_audit

# category key -> (label, default weight). Weights sum to 1.0.
CATEGORIES = [
    ("technical", "Technical SEO", 0.16),
    ("onpage", "On-Page SEO", 0.14),
    ("performance", "Performance", 0.14),
    ("content", "Content Quality", 0.10),
    ("mobile", "Mobile", 0.10),
    ("accessibility", "Accessibility", 0.08),
    ("security", "Security", 0.08),
    ("local", "Local SEO", 0.08),
    ("offpage", "Off-Page SEO", 0.08),
    ("ux", "User Experience", 0.04),
]
CATEGORY_LABEL = {k: lbl for k, lbl, _w in CATEGORIES}

SEV_WEIGHT = {"critical": 5, "high": 3, "medium": 2, "low": 1}


def _chk(cid, category, label, status, severity, effort, detail, fix="",
         where=None, scope="home", why="", impact="", gain=""):
    return {
        "id": cid, "category": category, "label": label, "status": status,
        "severity": severity, "effort": effort, "detail": detail, "fix": fix,
        "why": why, "impact": impact, "gain": gain,
        "where": where or [], "scope": scope,
    }


def _hget(headers, name):
    if not headers:
        return ""
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v or ""
    return ""


def _norm_for_compare(u):
    p = urlparse(u or "")
    path = (p.path or "/").rstrip("/") or "/"
    return "%s://%s%s" % (p.scheme, p.netloc.lower().replace("www.", ""), path)


GENERIC_ANCHORS = {"click here", "read more", "here", "more", "link",
                   "learn more", "this", "click", "details", "go"}


# ---------------------------------------------------------------------------
# Per-page fact extraction
# ---------------------------------------------------------------------------
def page_facts(page):
    """Parse one crawled page into the facts the checks consume."""
    html = page.get("html") or ""
    final_url = page.get("final_url") or page.get("url") or ""
    headers = page.get("headers") or {}
    f = {
        "url": page.get("url", ""), "final_url": final_url,
        "status": page.get("status", 0), "ok": page.get("ok", False),
        "redirects": len(page.get("redirects") or []),
        "is_html": page.get("is_html", False), "headers": headers,
    }
    if not html:
        f.update({"title": "", "title_len": 0, "meta_desc": "",
                  "meta_desc_len": 0, "h1_count": 0, "word_count": 0,
                  "noindex": False, "canonical": "", "canonical_ok": None,
                  "hreflang": [], "internal_links": []})
        return f

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    f["title"] = title
    f["title_len"] = len(title)

    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    meta_desc = (md.get("content") or "").strip() if md else ""
    f["meta_desc"] = meta_desc
    f["meta_desc_len"] = len(meta_desc)

    heads = []
    for lvl in range(1, 7):
        for h in soup.find_all("h%d" % lvl):
            if h.get_text(strip=True):
                heads.append((lvl, h.get_text(" ", strip=True)))
    f["headings"] = heads
    f["h1_texts"] = [t for (l, t) in heads if l == 1]
    f["h1_count"] = len(f["h1_texts"])

    can = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = (can.get("href") or "").strip() if can else ""
    if canonical:
        canonical = urljoin(final_url, canonical)
    f["canonical"] = canonical
    f["canonical_ok"] = (_norm_for_compare(canonical) == _norm_for_compare(final_url)
                         if canonical else None)

    rob = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    robots_meta = (rob.get("content") or "").lower() if rob else ""
    x_robots = _hget(headers, "X-Robots-Tag").lower()
    f["robots_meta"] = robots_meta
    f["x_robots"] = x_robots
    f["noindex"] = ("noindex" in robots_meta) or ("noindex" in x_robots)
    f["nofollow"] = ("nofollow" in robots_meta) or ("nofollow" in x_robots)

    f["lang"] = (soup.html.get("lang") if soup.html else "") or ""
    vp = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    f["viewport"] = bool(vp)
    f["viewport_content"] = (vp.get("content") or "").lower() if vp else ""
    f["charset"] = bool(soup.find("meta", charset=True) or
                        soup.find("meta", attrs={"http-equiv": re.compile(
                            "content-type", re.I)}))
    f["favicon"] = bool(soup.find("link",
                                  attrs={"rel": re.compile("icon", re.I)}))

    og_data = {}
    for p in ("title", "description", "image", "type", "site_name", "url"):
        tag = (soup.find("meta", attrs={"property": re.compile(r"^og:" + p + r"$", re.I)}) or
               soup.find("meta", attrs={"name": re.compile(r"^og:" + p + r"$", re.I)}))
        if tag and tag.get("content"):
            val = tag.get("content").strip()
            if p == "image" and val:
                val = urljoin(final_url, val)
            og_data[p] = val

    tw_data = {}
    for p in ("card", "title", "description", "image", "site", "creator"):
        tag = (soup.find("meta", attrs={"name": re.compile(r"^twitter:" + p + r"$", re.I)}) or
               soup.find("meta", attrs={"property": re.compile(r"^twitter:" + p + r"$", re.I)}))
        if tag and tag.get("content"):
            val = tag.get("content").strip()
            if p == "image" and val:
                val = urljoin(final_url, val)
            tw_data[p] = val

    f["og"] = og_data
    f["twitter"] = tw_data
    f["pagination"] = bool(soup.find("link",
                                     attrs={"rel": re.compile(r"^(next|prev)$",
                                                              re.I)}))

    # Images (extended)
    imgs = soup.find_all("img")
    f["images_total"] = len(imgs)
    f["images_with_alt"] = sum(1 for i in imgs if (i.get("alt") or "").strip())
    f["img_lazy"] = sum(1 for i in imgs
                        if (i.get("loading") or "").lower() == "lazy")
    f["img_srcset"] = sum(1 for i in imgs if i.get("srcset"))
    f["img_dims"] = sum(1 for i in imgs if i.get("width") and i.get("height"))
    f["img_modern"] = sum(1 for i in imgs
                          if re.search(r"\.(webp|avif)(\?|$)",
                                       i.get("src") or "", re.I))
    f["picture_count"] = len(soup.find_all("picture"))

    # Links + anchor quality
    base = urlparse(final_url).netloc.lower().replace("www.", "")
    internal, external, generic_links, empty_links = [], 0, 0, 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:",
                                        "data:")):
            continue
        txt = a.get_text(" ", strip=True).lower()
        if not txt and not (a.get("aria-label") or a.find("img")):
            empty_links += 1
        elif txt in GENERIC_ANCHORS:
            generic_links += 1
        netloc = urlparse(urljoin(final_url, href)).netloc.lower().replace(
            "www.", "")
        if netloc == "" or netloc == base:
            internal.append(urljoin(final_url, href))
        else:
            external += 1
    f["internal_links"] = internal
    f["internal_count"] = len(internal)
    f["external_count"] = external
    f["generic_link_text"] = generic_links
    f["empty_links"] = empty_links

    # Forms & labels (accessibility)
    label_for = {lb.get("for") for lb in soup.find_all("label") if lb.get("for")}
    labelable = [i for i in soup.find_all(["input", "select", "textarea"])
                 if (i.get("type") or "").lower() not in
                 ("hidden", "submit", "button", "image", "reset")]
    labeled = 0
    for i in labelable:
        iid = i.get("id")
        if (i.get("aria-label") or i.get("aria-labelledby") or i.get("title")
                or (iid and iid in label_for)):
            labeled += 1
    f["form_fields"] = len(labelable)
    f["form_fields_labeled"] = labeled

    # ARIA / landmarks
    f["aria_count"] = len(soup.find_all(
        lambda t: bool(t.get("role")) or any(
            str(k).startswith("aria-") for k in t.attrs)))
    f["has_main_landmark"] = bool(soup.find("main") or
                                  soup.find(attrs={"role": "main"}))
    f["has_nav_landmark"] = bool(soup.find("nav") or
                                 soup.find(attrs={"role": "navigation"}))
    f["skip_link"] = bool(soup.find("a", href=re.compile(r"#(main|content)",
                                                         re.I)))

    # Render-blocking / fonts (UX + performance heuristics)
    head = soup.head
    f["head_css"] = len(head.find_all("link", attrs={"rel": "stylesheet"})) \
        if head else 0
    f["head_sync_js"] = len([s for s in (head.find_all("script", src=True)
                                         if head else [])
                             if not (s.get("async") or s.get("defer"))])
    f["font_preload"] = bool(soup.find("link", attrs={
        "rel": re.compile("preconnect|preload", re.I)}))
    f["has_media_queries"] = bool(f["img_srcset"] or f["picture_count"] or
                                  any(l.get("media") for l in soup.find_all(
                                      "link", attrs={"rel": "stylesheet"})))

    # CTAs (Calls to Action)
    ctas = []
    for el in soup.find_all(["a", "button"]):
        txt = el.get_text(" ", strip=True)
        href = (el.get("href") or "").strip()
        cls = " ".join(el.get("class", [])) if el.get("class") else ""
        is_cta = False
        if href.startswith(("tel:", "mailto:")) or any(k in cls.lower() for k in ["btn", "cta", "button", "action"]):
            is_cta = True
        elif any(k in txt.lower() for k in ["contact", "book", "demo", "call", "quote", "get started", "sign up", "schedule", "hire"]):
            is_cta = True
        if is_cta and (txt or href):
            ctas.append({"text": txt or "[Action]", "href": href, "type": "tel" if href.startswith("tel:") else ("mailto" if href.startswith("mailto:") else "action")})
    f["ctas"] = ctas
    f["ctas_count"] = len(ctas)

    # Local SEO & Google Business Profile Signals
    has_map = bool(soup.find("iframe", src=re.compile(r"google\.com/maps|maps\.google", re.I)) or
                   soup.find("a", href=re.compile(r"maps\.app\.goo\.gl|google\.com/maps", re.I)))
    phone_links = [a.get("href") for a in soup.find_all("a", href=True) if (a.get("href") or "").startswith("tel:")]
    has_phone = bool(phone_links or re.search(r"\b(?:\+?1[-.]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", html))
    has_address = bool(re.search(r"\b(suite|ste|avenue|ave|street|st|road|rd|blvd|boulevard|highway|hwy|way|drive|dr)\b", html, re.I))
    citations = []
    for c_domain in ["yelp.com", "yellowpages", "bing.com/maps", "facebook.com", "tripadvisor", "bbb.org"]:
        if c_domain in html.lower():
            citations.append(c_domain)
    f["local_signals"] = {
        "has_map": has_map,
        "has_phone": has_phone,
        "phone_count": len(phone_links),
        "has_address": has_address,
        "citations": citations,
        "has_reviews": bool(re.search(r"(review|rating|testimonial|trustpilot|google-review)", html, re.I)),
    }

    # External / Outbound link classification (Off-page signals)
    ext_links = []
    base_domain = urlparse(final_url).netloc.lower().replace("www.", "")
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if not h.startswith(("http://", "https://")):
            continue
        link_net = urlparse(h).netloc.lower().replace("www.", "")
        if link_net and link_net != base_domain:
            rel = (a.get("rel") or [])
            rel_str = " ".join(rel) if isinstance(rel, list) else str(rel)
            ext_links.append({
                "url": h,
                "domain": link_net,
                "anchor": a.get_text(" ", strip=True)[:40],
                "nofollow": "nofollow" in rel_str.lower(),
                "sponsored": ("sponsored" in rel_str.lower() or "ugc" in rel_str.lower()),
            })
    f["external_links_details"] = ext_links[:30]
    f["external_dofollow_count"] = sum(1 for x in ext_links if not x["nofollow"])
    f["external_nofollow_count"] = sum(1 for x in ext_links if x["nofollow"])

    # Word count (cheap visible-text tally)
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    body = soup.body or soup
    f["word_count"] = len(re.findall(r"[A-Za-z][A-Za-z'\-]*",
                                     body.get_text(" ", strip=True)))

    hreflang = []
    for link in soup.find_all("link", attrs={"rel": re.compile("alternate",
                                                               re.I)}):
        if link.get("hreflang"):
            hreflang.append({"lang": link.get("hreflang").strip(),
                             "href": (link.get("href") or "").strip()})
    f["hreflang"] = hreflang

    f["mixed_content"] = 0
    if final_url.startswith("https://"):
        n = 0
        soup2 = BeautifulSoup(html, "html.parser")
        for tag, attr in (("img", "src"), ("script", "src"), ("link", "href"),
                          ("iframe", "src"), ("source", "src")):
            for el in soup2.find_all(tag):
                if (el.get(attr) or "").strip().startswith("http://"):
                    n += 1
        f["mixed_content"] = n

    text_low = html.lower()
    f["has_author"] = bool(
        soup.find(attrs={"rel": re.compile("author", re.I)}) or
        soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)}) or
        re.search(r'"author"\s*:', text_low))
    f["has_date"] = bool(
        soup.find("meta", attrs={"property": re.compile(
            "article:published_time|article:modified_time", re.I)}) or
        soup.find("time") or re.search(r'"date(published|modified)"\s*:',
                                       text_low))
    return f


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _psi_src(ctx):
    psi = ctx.get("psi") or {}
    if (psi.get("mobile") or {}).get("ok"):
        return psi["mobile"]
    if (psi.get("desktop") or {}).get("ok"):
        return psi["desktop"]
    return {}


# ---------------------------------------------------------------------------
# TECHNICAL
# ---------------------------------------------------------------------------
def _technical_checks(ctx, home, all_facts):
    out = []
    robots = ctx.get("robots") or {}
    sitemap = ctx.get("sitemap") or {}

    out.append(_chk("robots_present", "technical", "robots.txt",
                    "pass" if robots.get("present") else "fail", "high", "low",
                    "robots.txt found and readable." if robots.get("present")
                    else "No readable robots.txt (HTTP %s)." %
                    robots.get("status", "?"),
                    "" if robots.get("present") else
                    "Add a robots.txt at the site root and reference your "
                    "sitemap.",
                    why="robots.txt tells crawlers what they may access.",
                    scope="site"))
    if robots.get("blocks_all"):
        out.append(_chk("robots_blocks_site", "technical", "Crawl block",
                        "fail", "critical", "low",
                        "robots.txt disallows all crawlers (Disallow: /).",
                        "Remove the blanket 'Disallow: /'.",
                        why="A blanket block prevents the whole site from being "
                        "crawled or ranked.", scope="site"))
    elif robots.get("present"):
        out.append(_chk("robots_blocks_site", "technical", "Crawl block",
                        "pass", "critical", "low", "No blanket crawl block.",
                        scope="site"))

    if sitemap.get("count"):
        out.append(_chk("sitemap_present", "technical", "XML sitemap", "pass",
                        "high", "low", "Sitemap found with %d URL(s)%s." % (
                            sitemap["count"], " (sitemap index)" if
                            sitemap.get("had_index") else ""), scope="site"))
        out.append(_chk("sitemap_in_robots", "technical", "Sitemap in robots.txt",
                        "pass" if robots.get("sitemaps") else "warn", "low",
                        "low", "Referenced in robots.txt." if
                        robots.get("sitemaps") else
                        "Sitemap exists but isn't referenced in robots.txt.",
                        "" if robots.get("sitemaps") else
                        "Add a 'Sitemap:' line to robots.txt.", scope="site"))
    else:
        out.append(_chk("sitemap_present", "technical", "XML sitemap", "fail",
                        "high", "med",
                        "No XML sitemap found (checked robots.txt and "
                        "/sitemap.xml).",
                        "Publish an XML sitemap and reference it in robots.txt.",
                        why="Sitemaps help search engines discover every page.",
                        scope="site"))

    ai = robots.get("ai_rules") or {}
    out.append(_chk("ai_crawler_rules", "technical", "AI crawler rules", "info",
                    "low", "low",
                    ("robots.txt names AI crawlers — %s." %
                     ", ".join("%s: %s" % kv for kv in ai.items())) if ai else
                    "No explicit rules for AI crawlers (GPTBot, ClaudeBot, "
                    "Google-Extended). They use the default policy.",
                    scope="site"))

    hr = ctx.get("http_redirect")
    if hr is not None and hr.get("reachable"):
        ok = hr.get("redirects_to_https")
        out.append(_chk("https_redirect", "technical", "HTTP→HTTPS redirect",
                        "pass" if ok else "fail", "high", "med",
                        "http:// redirects to https://." if ok else
                        "The http:// version does not redirect to https://.",
                        "" if ok else
                        "Add a 301 redirect from http to https for every URL.",
                        scope="home"))

    if home.get("canonical"):
        ok = home.get("canonical_ok")
        out.append(_chk("canonical_home", "technical", "Canonical tag",
                        "pass" if ok else "warn", "high", "low",
                        "Homepage has a self-referencing canonical." if ok else
                        "Homepage canonical points elsewhere: %s" %
                        home["canonical"], "" if ok else
                        "Confirm the canonical target is intended.",
                        scope="home"))
    else:
        out.append(_chk("canonical_home", "technical", "Canonical tag", "warn",
                        "high", "low", "No canonical tag on the homepage.",
                        "Add <link rel=\"canonical\"> to avoid duplicate-URL "
                        "dilution.", scope="home"))

    non200 = [f["final_url"] for f in all_facts
              if f["status"] and f["status"] >= 400]
    if all_facts:
        out.append(_chk("non_200_pages", "technical", "HTTP status codes",
                        "pass" if not non200 else ("warn" if len(non200) <= 2
                                                   else "fail"), "high", "med",
                        "All %d crawled pages returned OK." % len(all_facts)
                        if not non200 else
                        "%d crawled page(s) returned 4xx/5xx." % len(non200),
                        "" if not non200 else "Fix or redirect the broken URLs.",
                        where=non200[:10], scope="site"))
        redir = [f["final_url"] for f in all_facts if f.get("redirects", 0) >= 2]
        out.append(_chk("redirect_chains", "technical", "Redirect chains",
                        "pass" if not redir else "warn", "medium", "med",
                        "No multi-hop redirects." if not redir else
                        "%d page(s) go through 2+ redirect hops." % len(redir),
                        "" if not redir else
                        "Point links straight at the final URL.",
                        where=redir[:10], scope="site"))
        noindex = [f["final_url"] for f in all_facts if f.get("noindex")]
        home_noindex = home.get("noindex")
        out.append(_chk("noindex_pages", "technical", "Indexability",
                        "fail" if home_noindex else ("warn" if noindex else
                                                     "pass"),
                        "critical" if home_noindex else "high", "low",
                        "Homepage is set to noindex — it will be dropped from "
                        "search." if home_noindex else
                        ("%d crawled page(s) use noindex." % len(noindex)
                         if noindex else "No unexpected noindex directives."),
                        "Remove noindex from pages that should rank." if noindex
                        else "", where=noindex[:10], scope="site"))

    bl = ctx.get("broken_links")
    if bl is not None:
        broken = bl.get("broken") or []
        out.append(_chk("broken_internal_links", "technical",
                        "Broken internal links",
                        "pass" if not broken else ("warn" if len(broken) <= 2
                                                   else "fail"), "high", "med",
                        "Checked %d internal link(s); none broken." %
                        bl.get("checked", 0) if not broken else
                        "%d internal link(s) are broken (4xx/5xx)." %
                        len(broken), "" if not broken else
                        "Fix or remove the dead internal links.",
                        where=[b["url"] for b in broken][:10], scope="site"))

    # URL structure (site)
    htmls = [f for f in all_facts if f.get("is_html")]
    if htmls:
        bad_urls = []
        for f in htmls:
            u = f["final_url"]
            p = urlparse(u)
            if len(u) > 115 or p.query.count("=") >= 3 or "_" in p.path:
                bad_urls.append(u)
        out.append(_chk("url_structure", "technical", "URL structure",
                        "pass" if not bad_urls else "warn", "low", "high",
                        "URLs are clean and readable." if not bad_urls else
                        "%d URL(s) are long, parameter-heavy, or use "
                        "underscores." % len(bad_urls), "" if not bad_urls else
                        "Prefer short, hyphenated, lowercase URLs.",
                        where=bad_urls[:10], scope="site"))

    out.append(_chk("charset", "technical", "Charset declaration",
                    "pass" if home.get("charset") else "warn", "low", "low",
                    "Charset declared." if home.get("charset") else
                    "No charset meta.", "" if home.get("charset") else
                    "Declare <meta charset=\"utf-8\"> early in <head>.",
                    scope="home"))
    og_dict = home.get("og") or {}
    if isinstance(og_dict, dict):
        og_valid_count = sum(1 for p in ("title", "description", "image") if og_dict.get(p))
    else:
        og_valid_count = 0
    out.append(_chk("open_graph", "technical", "Open Graph tags",
                    "pass" if og_valid_count == 3 else "warn", "low", "low",
                    "og:title/description/image present." if og_valid_count == 3 else
                    "Missing some Open Graph tags.",
                    "Add og:title, og:description and og:image." if og_valid_count != 3 else "",
                    scope="home"))
    tw_val = home.get("twitter")
    tw_present = bool(tw_val) if isinstance(tw_val, (bool, dict)) and tw_val else False
    out.append(_chk("twitter_card", "technical", "Twitter Card",
                    "pass" if tw_present else "info", "low", "low",
                    "Twitter Card tags present." if tw_present else
                    "No Twitter Card tags.", "" if tw_present else
                    "Add twitter:card for richer previews.", scope="home"))
    out.append(_chk("pagination", "technical", "Pagination markup", "info",
                    "low", "low",
                    "rel=next/prev present." if home.get("pagination") else
                    "No rel=next/prev pagination markup (fine for most sites).",
                    scope="home"))

    out += _structured_checks(ctx, home["final_url"])
    out += _international_checks(home)
    return out


def _structured_checks(ctx, home_url):
    out = []
    sd = (ctx.get("structured") or {}).get(home_url)
    if sd is None:
        sd = next(iter((ctx.get("structured") or {}).values()), None)
    if not sd:
        return out
    if sd.get("present"):
        out.append(_chk("sd_present", "technical", "Structured data", "pass",
                        "medium", "med", "%d JSON-LD object(s)%s detected." % (
                            sd.get("jsonld_object_count", 0),
                            (" + %d microdata item(s)" % sd["microdata_items"])
                            if sd.get("microdata_items") else ""), scope="home"))
    else:
        out.append(_chk("sd_present", "technical", "Structured data", "fail",
                        "medium", "med",
                        "No structured data (JSON-LD / Microdata) found.",
                        "Add schema.org JSON-LD (Organization/WebSite at "
                        "minimum) for rich-result eligibility.",
                        why="Structured data unlocks rich results in search.",
                        scope="home"))
        return out
    out.append(_chk("sd_valid", "technical", "Schema validity",
                    "fail" if sd.get("parse_errors") else "pass", "medium",
                    "low", "%d JSON-LD block(s) failed to parse." %
                    len(sd["parse_errors"]) if sd.get("parse_errors") else
                    "All JSON-LD blocks parse cleanly.",
                    "Fix the malformed JSON-LD." if sd.get("parse_errors") else
                    "", scope="home"))
    if sd.get("types"):
        out.append(_chk("sd_types", "technical", "Schema types", "info", "low",
                        "low", "Detected: %s." % ", ".join(sd["types"][:12]),
                        scope="home"))
    missing = sd.get("missing_required") or []
    if missing:
        out.append(_chk("sd_required_fields", "technical",
                        "Required schema fields", "fail", "medium", "low",
                        "Required properties missing — %s." % "; ".join(
                            "%s: %s" % (t, ", ".join(mr))
                            for t, mr in missing[:5]),
                        "Add the required properties for valid rich results.",
                        scope="home"))
    elif sd.get("rich_types"):
        out.append(_chk("sd_required_fields", "technical",
                        "Required schema fields", "pass", "medium", "low",
                        "Recognized types have their required properties.",
                        scope="home"))
    out.append(_chk("sd_organization", "technical", "Organization / WebSite",
                    "pass" if sd.get("has_organization") else "warn", "low",
                    "low", "Organization/WebSite markup present." if
                    sd.get("has_organization") else
                    "No Organization/WebSite markup.",
                    "" if sd.get("has_organization") else
                    "Add Organization + WebSite JSON-LD.", scope="home"))
    if sd.get("rich_types"):
        out.append(_chk("sd_rich_eligible", "technical",
                        "Rich-result eligibility",
                        "pass" if sd.get("rich_eligible") else "warn", "medium",
                        "low", "Markup for %s meets Google's required fields." %
                        ", ".join(sd["rich_types"][:6]) if sd.get("rich_eligible")
                        else "Some markup is missing required fields.",
                        "" if sd.get("rich_eligible") else
                        "Complete the required properties.", scope="home"))
    return out


def _international_checks(home):
    out = []
    hreflang = home.get("hreflang") or []
    if not hreflang:
        out.append(_chk("hreflang_present", "technical", "hreflang", "na",
                        "medium", "med",
                        "No hreflang tags — treated as a single-locale site.",
                        scope="home"))
        return out
    out.append(_chk("hreflang_present", "technical", "hreflang present", "pass",
                    "medium", "med", "%d hreflang alternate(s) declared." %
                    len(hreflang), scope="home"))
    bad = [h["lang"] for h in hreflang if h["lang"].lower() != "x-default"
           and not re.match(r"^[a-z]{2}(-[A-Za-z]{2})?$", h["lang"])]
    out.append(_chk("hreflang_valid", "technical", "hreflang codes valid",
                    "pass" if not bad else "warn", "medium", "low",
                    "All hreflang codes look valid." if not bad else
                    "Invalid hreflang code(s): %s." % ", ".join(bad[:6]),
                    "" if not bad else "Use valid ISO language(-region) codes.",
                    scope="home"))
    has_xd = any(h["lang"].lower() == "x-default" for h in hreflang)
    out.append(_chk("hreflang_xdefault", "technical", "hreflang x-default",
                    "pass" if has_xd else "warn", "low", "low",
                    "x-default is declared." if has_xd else "No x-default.",
                    "" if has_xd else "Add an x-default hreflang.", scope="home"))
    return out


# ---------------------------------------------------------------------------
# ON-PAGE
# ---------------------------------------------------------------------------
def _onpage_checks(ctx, home, all_facts):
    out = []
    tl = home["title_len"]
    if not home["title"]:
        out.append(_chk("title_present", "onpage", "Title tag", "fail",
                        "critical", "low", "Homepage has no <title>.",
                        "Add a unique, descriptive <title> (50–60 chars).",
                        why="The title is the strongest on-page ranking and "
                        "click-through signal.", scope="home"))
    else:
        if 50 <= tl <= 60:
            st, fix, det = "pass", "", "Title is %d chars — ideal." % tl
        elif 30 <= tl <= 65:
            st, fix, det = "warn", "Aim for 50–60 characters.", \
                "Title is %d chars." % tl
        else:
            st, fix = "fail", "Rewrite to 50–60 characters."
            det = "Title is %d chars (%s)." % (tl, "too short" if tl < 30
                                               else "too long")
        out.append(_chk("title_length", "onpage", "Title length", st, "high",
                        "low", det, fix, scope="home"))

    ml = home["meta_desc_len"]
    if not home["meta_desc"]:
        out.append(_chk("metadesc_present", "onpage", "Meta description", "fail",
                        "high", "low", "Homepage has no meta description.",
                        "Add a 120–160 char meta description.", scope="home"))
    else:
        if 120 <= ml <= 160:
            st, fix, det = "pass", "", "Meta description is %d chars — ideal." % ml
        elif 70 <= ml <= 165:
            st, fix, det = "warn", "Aim for 120–160 characters.", \
                "Meta description is %d chars." % ml
        else:
            st, fix = "warn", "Rewrite to 120–160 characters."
            det = "Meta description is %d chars." % ml
        out.append(_chk("metadesc_length", "onpage", "Meta description length",
                        st, "medium", "low", det, fix, scope="home"))

    hc = home["h1_count"]
    out.append(_chk("h1_count", "onpage", "H1 heading",
                    "pass" if hc == 1 else "fail" if hc == 0 else "warn", "high",
                    "low", "Exactly one H1." if hc == 1 else
                    ("No H1 on the homepage." if hc == 0 else
                     "%d H1 tags (one is best)." % hc),
                    "" if hc == 1 else "Use a single, descriptive H1.",
                    scope="home"))

    levels = [l for (l, _t) in home.get("headings", [])]
    skipped, prev = False, 0
    for l in levels:
        if prev and l > prev + 1:
            skipped = True
            break
        prev = l
    if levels:
        out.append(_chk("heading_hierarchy", "onpage", "Heading hierarchy",
                        "pass" if not skipped else "warn", "medium", "low",
                        "Headings nest without skipping levels." if not skipped
                        else "Heading levels skip (e.g. H2→H4).",
                        "" if not skipped else "Use sequential heading levels.",
                        scope="home"))

    it, ia = home.get("images_total", 0), home.get("images_with_alt", 0)
    if it:
        pct = round(ia / it * 100)
        st = "pass" if pct >= 90 else ("warn" if pct >= 50 else "fail")
        out.append(_chk("image_alt", "onpage", "Image alt text", st, "medium",
                        "low", "%d/%d images have alt text (%d%%)." % (ia, it,
                                                                       pct),
                        "" if st == "pass" else
                        "Add descriptive alt text to the remaining images.",
                        scope="home"))
    ic = home.get("internal_count", 0)
    out.append(_chk("internal_links", "onpage", "Internal links",
                    "pass" if ic >= 3 else ("warn" if ic >= 1 else "fail"),
                    "low", "low", "%d internal link(s), %d external." % (
                        ic, home.get("external_count", 0)),
                    "" if ic >= 3 else "Add internal links to aid crawling.",
                    scope="home"))
    out.append(_chk("favicon", "onpage", "Favicon",
                    "pass" if home.get("favicon") else "warn", "low", "low",
                    "Favicon declared." if home.get("favicon") else
                    "No favicon link.", "" if home.get("favicon") else
                    "Add a favicon.", scope="home"))

    htmls = [f for f in all_facts if f.get("is_html")]
    if len(htmls) >= 2:
        seen = {}
        for f in htmls:
            t = (f.get("title") or "").strip().lower()
            if t:
                seen.setdefault(t, []).append(f["final_url"])
        dups = {t: us for t, us in seen.items() if len(us) > 1}
        dup_urls = [u for us in dups.values() for u in us]
        out.append(_chk("title_unique", "onpage", "Unique titles",
                        "pass" if not dups else ("warn" if len(dups) == 1 else
                                                 "fail"), "high", "med",
                        "All crawled pages have unique titles." if not dups else
                        "%d group(s) of pages share the same title." % len(dups),
                        "" if not dups else
                        "Give every page a distinct title.", where=dup_urls[:10],
                        scope="site"))
        missing_m = [f["final_url"] for f in htmls if not f.get("meta_desc")]
        out.append(_chk("metadesc_missing_site", "onpage",
                        "Meta descriptions (site)",
                        "pass" if not missing_m else ("warn" if len(missing_m)
                                                      <= len(htmls) / 2 else
                                                      "fail"), "medium", "med",
                        "Every crawled page has a meta description." if not
                        missing_m else "%d of %d crawled page(s) lack a meta "
                        "description." % (len(missing_m), len(htmls)),
                        "" if not missing_m else
                        "Write unique meta descriptions for the missing pages.",
                        where=missing_m[:10], scope="site"))

    # Calls to Action (CTAs)
    ctas_cnt = home.get("ctas_count", 0)
    if ctas_cnt >= 2:
        out.append(_chk("onpage_ctas", "onpage", "Calls to Action (CTAs)",
                        "pass", "medium", "low",
                        f"Found {ctas_cnt} actionable Call to Action (CTA) triggers / buttons on homepage.",
                        "", scope="home"))
    elif ctas_cnt == 1:
        out.append(_chk("onpage_ctas", "onpage", "Calls to Action (CTAs)",
                        "warn", "medium", "low",
                        "Only 1 Call to Action found on the homepage — add clear primary and secondary conversion points.",
                        "Add prominent CTA buttons (e.g. 'Get Started', 'Schedule Demo', 'Contact Us') above the fold.",
                        scope="home"))
    else:
        out.append(_chk("onpage_ctas", "onpage", "Calls to Action (CTAs)",
                        "fail", "high", "low",
                        "No clear Call to Action (CTA) buttons, booking forms, or phone links found on the homepage.",
                        "Place prominent CTA buttons in the hero section and at the bottom of the page.",
                        why="Without prominent CTAs, organic search visitors bounce without converting.",
                        scope="home"))

    # Orphan pages in crawl graph
    orphan_pages = ctx.get("orphan_pages") or []
    if len(all_facts) > 3:
        out.append(_chk("onpage_orphan_pages", "onpage", "Internal Linking & Orphan Risk",
                        "pass" if not orphan_pages else "warn", "medium", "med",
                        "All crawled pages have healthy internal link connectivity." if not orphan_pages else
                        f"{len(orphan_pages)} crawled page(s) have 1 or fewer incoming internal links (orphan risk).",
                        "" if not orphan_pages else "Add contextual internal links to these pages from higher-authority parent pages.",
                        where=orphan_pages[:10], scope="site"))

    return out


# ---------------------------------------------------------------------------
# PERFORMANCE
# ---------------------------------------------------------------------------
def _performance_checks(ctx, home):
    out = []
    psi = ctx.get("psi") or {}
    mob, desk = psi.get("mobile") or {}, psi.get("desktop") or {}
    if not mob.get("ok") and not desk.get("ok"):
        out.append(_chk("psi_unavailable", "performance", "PageSpeed Insights",
                        "na", "high", "high",
                        "PageSpeed Insights data was unavailable (%s)." %
                        (mob.get("error") or desk.get("error") or "no data"),
                        "Add a free PAGESPEED_API_KEY, or retry when the shared "
                        "keyless quota resets.", scope="home"))
        return out

    def sc(cid, label, val, sev):
        if val is None:
            return _chk(cid, "performance", label, "na", sev, "high",
                        "No score returned.", scope="home")
        st = "pass" if val >= 90 else ("warn" if val >= 50 else "fail")
        return _chk(cid, "performance", label, st, sev, "high", "%d / 100." % val,
                    "" if st == "pass" else
                    "Address the flagged opportunities to raise this score.",
                    scope="home")

    if mob.get("ok"):
        out.append(sc("psi_performance_mobile", "Performance (mobile)",
                      (mob.get("scores") or {}).get("performance"), "critical"))
    if desk.get("ok"):
        out.append(sc("psi_performance_desktop", "Performance (desktop)",
                      (desk.get("scores") or {}).get("performance"), "high"))

    src = mob if mob.get("ok") else desk
    ss = src.get("scores") or {}
    seo_v = ss.get("seo")
    if seo_v is not None:
        st = "pass" if seo_v >= 90 else ("warn" if seo_v >= 80 else "fail")
        out.append(_chk("psi_seo_score", "performance", "Lighthouse SEO score",
                        st, "high", "med", "%d / 100." % seo_v,
                        "" if st == "pass" else "Fix the Lighthouse SEO items.",
                        scope="home"))
    v = ss.get("best_practices")
    if v is not None:
        st = "pass" if v >= 90 else ("warn" if v >= 80 else "fail")
        out.append(_chk("psi_best_practices", "performance", "Best Practices",
                        st, "medium", "med", "%d / 100." % v,
                        "" if st == "pass" else "Resolve best-practice issues.",
                        scope="home"))

    lab = src.get("lab") or {}
    for cid, label, val, good, poor, unit, sev in [
        ("lab_lcp", "LCP (lab)", lab.get("lcp_ms"), 2500, 4000, "ms", "high"),
        ("lab_tbt", "Total Blocking Time", lab.get("tbt_ms"), 200, 600, "ms",
         "medium"),
        ("lab_cls", "CLS (lab)", lab.get("cls"), 0.1, 0.25, "", "high"),
        ("lab_fcp", "First Contentful Paint", lab.get("fcp_ms"), 1800, 3000,
         "ms", "medium"),
    ]:
        if val is None:
            continue
        st = "pass" if val <= good else ("warn" if val <= poor else "fail")
        shown = ("%.2f" % val) if unit == "" else ("%.0f ms" % val)
        out.append(_chk(cid, "performance", label, st, sev, "high",
                        "%s (lab)." % shown, "" if st == "pass" else
                        "Optimise to improve this metric.", scope="home"))

    field = src.get("field") or {}
    if field.get("available"):
        fm = field.get("metrics") or {}
        lm = {"lcp": ("LCP (field)", "high"), "inp": ("INP (field)", "high"),
              "cls": ("CLS (field)", "high"), "fcp": ("FCP (field)", "medium"),
              "ttfb": ("TTFB (field)", "medium")}
        for key, (label, sev) in lm.items():
            m = fm.get(key)
            if not m or m.get("p75") is None:
                continue
            st = {"good": "pass", "needs-improvement": "warn",
                  "poor": "fail"}.get(m["rating"], "na")
            shown = ("%.2f" % m["p75"]) if key == "cls" else ("%.0f ms" %
                                                              m["p75"])
            out.append(_chk("crux_" + key, "performance", label, st, sev, "high",
                            "Real-user p75: %s (%s)." % (shown, m["rating"]),
                            "" if st == "pass" else
                            "Improve %s for real users." % key.upper(),
                            scope="home"))

    opp = src.get("opportunities") or {}
    rb = opp.get("render_blocking_ms")
    if rb is not None:
        st = "pass" if rb < 300 else ("warn" if rb < 1000 else "fail")
        out.append(_chk("render_blocking", "performance",
                        "Render-blocking resources", st, "medium", "med",
                        "Est. %.0f ms blocked by render-blocking resources." % rb,
                        "" if st == "pass" else
                        "Defer non-critical CSS/JS; inline critical CSS.",
                        scope="home"))
    tb = opp.get("total_bytes")
    if tb is not None:
        mb = tb / 1048576.0
        st = "pass" if mb <= 1.5 else ("warn" if mb <= 3 else "fail")
        out.append(_chk("page_weight", "performance", "Page weight", st, "low",
                        "med", "Total page weight ≈ %.1f MB." % mb,
                        "" if st == "pass" else
                        "Compress images, split code, remove unused assets.",
                        scope="home"))
    tcs = opp.get("text_compression_score")
    if tcs is not None:
        out.append(_chk("text_compression", "performance", "Text compression",
                        "pass" if tcs >= 0.9 else "fail", "medium", "low",
                        "Text compression enabled." if tcs >= 0.9 else
                        "Text assets aren't gzip/brotli compressed.",
                        "" if tcs >= 0.9 else
                        "Enable gzip or Brotli on the server.", scope="home"))
    # Extra named audits (image formats, minification, cache)
    named = src.get("named_audits") or {}
    for aid, cid, label, sev in [
        ("uses-long-cache-ttl", "cache_ttl", "Cache headers", "low"),
        ("unused-css-rules", "unused_css", "Unused CSS", "low"),
        ("unused-javascript", "unused_js", "Unused JavaScript", "medium"),
        ("server-response-time", "ttfb_lab", "Server response (TTFB)",
         "medium"),
    ]:
        a = named.get(aid)
        if not a or a.get("score") is None:
            continue
        s = a["score"]
        st = "pass" if s >= 0.9 else ("warn" if s >= 0.5 else "fail")
        out.append(_chk(cid, "performance", label, st, sev, "med",
                        "%s%s" % (a.get("title", label),
                                  (" — %s" % a["display"]) if a.get("display")
                                  else "."), "" if st == "pass" else
                        "See Lighthouse guidance for %s." % label.lower(),
                        scope="home"))
    return out


# ---------------------------------------------------------------------------
# CONTENT
# ---------------------------------------------------------------------------
def _content_checks(ctx, home, all_facts):
    out = []
    content = (ctx.get("content") or {}).get(home["final_url"]) or {}
    fl = content.get("flesch")
    wc = content.get("word_count", home.get("word_count", 0))

    if fl is not None:
        if 50 <= fl <= 70:
            st, fix, det = "pass", "", \
                "Flesch reading ease %.0f — good for a general audience." % fl
        elif 30 <= fl < 50 or 70 < fl <= 80:
            st, fix = "warn", "Aim for a Flesch score of 50–70."
            det = "Flesch reading ease %.0f." % fl
        else:
            st, fix = "warn", "Simplify sentences and vocabulary toward 50–70."
            det = "Flesch reading ease %.0f (%s)." % (
                fl, "very hard to read" if fl < 30 else "unusually simple")
        out.append(_chk("readability", "content", "Readability", st, "low",
                        "med", det, fix, scope="home"))
    if wc:
        st = "pass" if wc >= 500 else ("warn" if wc >= 250 else "fail")
        out.append(_chk("content_depth", "content", "Content depth", st,
                        "medium", "high", "Homepage main content ≈ %d words." %
                        wc, "" if st == "pass" else
                        "Add substantive, original copy (aim well above 300 "
                        "words on key pages).", scope="home"))

    kd = content.get("keyword_density")
    kw = content.get("primary_keyword")
    if kd is not None and kw:
        st = "pass" if kd <= 3.5 else ("warn" if kd <= 5.5 else "fail")
        out.append(_chk("keyword_density", "content", "Keyword density", st,
                        "low", "low", "Top term \"%s\" density %.1f%%." % (kw,
                                                                           kd),
                        "" if st == "pass" else
                        "Reduce repetition — keyword stuffing risks penalties.",
                        scope="home"))
        in_title = kw.lower() in (home.get("title") or "").lower()
        in_h1 = any(kw.lower() in h.lower() for h in home.get("h1_texts", []))
        out.append(_chk("title_keyword_align", "content",
                        "Primary keyword in title/H1",
                        "pass" if (in_title or in_h1) else "warn", "low", "low",
                        "Primary topic \"%s\" appears in the title/H1." % kw if
                        (in_title or in_h1) else
                        "Primary topic \"%s\" isn't in the title or H1." % kw,
                        "" if (in_title or in_h1) else
                        "Work the primary topic into the title and H1.",
                        scope="home"))

    out.append(_chk("author_byline", "content", "Author / E-E-A-T",
                    "pass" if home.get("has_author") else "warn", "medium",
                    "med", "Author/byline signal detected." if
                    home.get("has_author") else "No clear author/byline signal.",
                    "" if home.get("has_author") else
                    "Add visible authorship and author schema.", scope="home"))
    out.append(_chk("date_signals", "content", "Freshness / dates",
                    "pass" if home.get("has_date") else "info", "low", "low",
                    "Published/updated date signals present." if
                    home.get("has_date") else "No date signals found.",
                    "" if home.get("has_date") else
                    "Show published/updated dates where relevant.",
                    scope="home"))

    all_internal = []
    for f in all_facts:
        all_internal.extend(f.get("internal_links") or [])
    joined = " ".join(u.lower() for u in all_internal)
    out.append(_chk("about_page", "content", "About page",
                    "pass" if "about" in joined else "warn", "low", "low",
                    "An About page is linked." if "about" in joined else
                    "No About page found in internal links.",
                    "" if "about" in joined else
                    "Publish an About page — a core trust/E-E-A-T signal.",
                    scope="site"))
    out.append(_chk("contact_page", "content", "Contact page",
                    "pass" if "contact" in joined else "warn", "medium", "low",
                    "A Contact page is linked." if "contact" in joined else
                    "No Contact page found in internal links.",
                    "" if "contact" in joined else
                    "Add a Contact page with name, address and phone (NAP).",
                    scope="site"))

    # thin content across the crawl
    htmls = [f for f in all_facts if f.get("is_html")]
    if len(htmls) >= 2:
        thin = [f["final_url"] for f in htmls
                if 0 < f.get("word_count", 0) < 300]
        out.append(_chk("thin_content_site", "content", "Thin content",
                        "pass" if not thin else "warn", "medium", "high",
                        "No obviously thin pages." if not thin else
                        "%d crawled page(s) have under 300 words." % len(thin),
                        "" if not thin else
                        "Expand thin pages with useful, original content.",
                        where=thin[:10], scope="site"))
    return out


# ---------------------------------------------------------------------------
# MOBILE
# ---------------------------------------------------------------------------
def _mobile_checks(ctx, home):
    out = []
    named = _psi_src(ctx).get("named_audits") or {}

    out.append(_chk("mobile_viewport", "mobile", "Viewport meta",
                    "pass" if home.get("viewport") else "fail", "high", "low",
                    "Viewport meta present." if home.get("viewport") else
                    "No viewport meta — the page won't scale on mobile.",
                    "" if home.get("viewport") else
                    "Add <meta name=\"viewport\" content=\"width=device-width, "
                    "initial-scale=1\">.",
                    why="Mobile-first indexing means Google ranks the mobile "
                    "version; a viewport is mandatory.", scope="home"))
    vc = home.get("viewport_content", "")
    if home.get("viewport"):
        ok = "width=device-width" in vc
        out.append(_chk("mobile_viewport_config", "mobile",
                        "Viewport configuration", "pass" if ok else "warn",
                        "medium", "low",
                        "Viewport uses width=device-width." if ok else
                        "Viewport is present but not set to width=device-width.",
                        "" if ok else "Use content=\"width=device-width, "
                        "initial-scale=1\".", scope="home"))

    responsive = home.get("has_media_queries") or home.get("img_srcset") or \
        home.get("picture_count")
    out.append(_chk("mobile_responsive", "mobile", "Responsive design",
                    "pass" if responsive else "warn", "medium", "high",
                    "Responsive signals found (srcset/picture/media)." if
                    responsive else
                    "No responsive-image or media-query signals detected.",
                    "" if responsive else
                    "Use responsive images (srcset/picture) and CSS media "
                    "queries.", scope="home"))

    # Lighthouse-backed mobile audits
    for aid, cid, label, sev, fix in [
        ("font-size", "mobile_font_size", "Legible font sizes", "medium",
         "Use font sizes ≥ 12px so text is readable without zoom."),
        ("tap-targets", "mobile_tap_targets", "Tap target sizing", "medium",
         "Size tap targets ≥ 48px and space them apart."),
    ]:
        a = named.get(aid)
        if a and a.get("score") is not None:
            s = a["score"]
            st = "pass" if s >= 0.9 else ("warn" if s >= 0.5 else "fail")
            out.append(_chk(cid, "mobile", label, st, sev, "med",
                            "%s%s" % (a.get("title", label),
                                      (" — %s" % a["display"]) if a.get("display")
                                      else "."), "" if st == "pass" else fix,
                            scope="home"))

    mob = (ctx.get("psi") or {}).get("mobile") or {}
    if mob.get("ok"):
        perf = (mob.get("scores") or {}).get("performance")
        if perf is not None:
            st = "pass" if perf >= 90 else ("warn" if perf >= 50 else "fail")
            out.append(_chk("mobile_performance", "mobile",
                            "Mobile page speed", st, "high", "high",
                            "Mobile Performance score %d/100." % perf,
                            "" if st == "pass" else
                            "Improve mobile load speed (images, JS, server).",
                            scope="home"))
    if not out or all(c["status"] == "na" for c in out):
        out.append(_chk("mobile_unavailable", "mobile", "Mobile analysis", "na",
                        "medium", "med", "Not enough data to assess mobile.",
                        scope="home"))
    return out


# ---------------------------------------------------------------------------
# ACCESSIBILITY
# ---------------------------------------------------------------------------
_A11Y_SKIP = {"image-alt", "label", "html-has-lang", "document-title",
              "link-name"}  # covered by static checks below


def _accessibility_checks(ctx, home):
    out = []
    src = _psi_src(ctx)
    a11y_score = src.get("accessibility_score")
    if a11y_score is not None:
        st = "pass" if a11y_score >= 90 else ("warn" if a11y_score >= 80 else
                                              "fail")
        out.append(_chk("a11y_score", "accessibility",
                        "Lighthouse accessibility score", st, "high", "med",
                        "%d / 100." % a11y_score, "" if st == "pass" else
                        "Fix the accessibility issues below.", scope="home"))

    out.append(_chk("a11y_lang", "accessibility", "Document language",
                    "pass" if home.get("lang") else "fail", "medium", "low",
                    "lang=\"%s\"." % home["lang"] if home.get("lang") else
                    "No lang attribute on <html>.", "" if home.get("lang") else
                    "Set <html lang> so screen readers use the right voice.",
                    scope="home"))
    it, ia = home.get("images_total", 0), home.get("images_with_alt", 0)
    if it:
        pct = round(ia / it * 100)
        st = "pass" if pct >= 95 else ("warn" if pct >= 60 else "fail")
        out.append(_chk("a11y_image_alt", "accessibility",
                        "Images have alt text", st, "medium", "low",
                        "%d/%d images have alt (%d%%)." % (ia, it, pct),
                        "" if st == "pass" else
                        "Add alt text (empty alt=\"\" for decorative images).",
                        scope="home"))
    ff, fl = home.get("form_fields", 0), home.get("form_fields_labeled", 0)
    if ff:
        st = "pass" if fl == ff else ("warn" if fl >= ff / 2 else "fail")
        out.append(_chk("a11y_form_labels", "accessibility", "Form labels", st,
                        "medium", "med", "%d/%d form fields are labeled." %
                        (fl, ff), "" if st == "pass" else
                        "Associate a <label> (or aria-label) with every field.",
                        scope="home"))
    gl = home.get("generic_link_text", 0)
    out.append(_chk("a11y_link_text", "accessibility", "Descriptive link text",
                    "pass" if gl == 0 else ("warn" if gl <= 3 else "fail"),
                    "low", "med", "No generic link text." if gl == 0 else
                    "%d link(s) use generic text like 'click here'." % gl,
                    "" if gl == 0 else
                    "Use descriptive link text that makes sense out of context.",
                    scope="home"))
    out.append(_chk("a11y_landmarks", "accessibility", "Landmark regions",
                    "pass" if home.get("has_main_landmark") else "warn", "low",
                    "low", "A <main> landmark is present." if
                    home.get("has_main_landmark") else
                    "No <main> landmark for assistive tech.",
                    "" if home.get("has_main_landmark") else
                    "Wrap primary content in <main> and use nav/header/footer.",
                    scope="home"))

    # Real Lighthouse a11y audits (color-contrast, aria-*, etc.)
    audits = src.get("accessibility_audits") or []
    failing = [a for a in audits if a.get("score") is not None
               and a["score"] < 0.9 and a["id"] not in _A11Y_SKIP]
    failing.sort(key=lambda a: (a["score"], -a.get("weight", 0)))
    for a in failing[:8]:
        s = a["score"]
        st = "fail" if s < 0.5 else "warn"
        sev = "high" if s < 0.5 else "medium"
        out.append(_chk("psi_a11y_" + a["id"], "accessibility", a["title"], st,
                        sev, "med", a.get("display") or
                        "Lighthouse flagged this accessibility issue.",
                        "See the Lighthouse accessibility guidance for '%s'." %
                        a["id"], scope="home"))
    return out


# ---------------------------------------------------------------------------
# SECURITY
# ---------------------------------------------------------------------------
def _security_checks(ctx, home):
    sec_data = ctx.get("security_audit")
    if sec_data:
        return security_audit.generate_security_checks_catalog(sec_data, ctx, home)

    final_url = home.get("final_url") or home.get("url") or ""
    all_facts = ctx.get("all_facts") or [home]
    try:
        sec_data = security_audit.run_full_security_audit(final_url, home, all_facts, timeout=4)
        return security_audit.generate_security_checks_catalog(sec_data, ctx, home)
    except Exception:
        pass

    # Basic fallback if network fails
    out = []
    headers = home.get("headers") or {}
    https = final_url.startswith("https://")
    out.append(_chk("sec_https_active", "security", "HTTPS Protocol Enforcement", "pass" if https else "fail",
                    "critical", "med", "Served over HTTPS." if https else "Homepage is not HTTPS.",
                    "" if https else "Install TLS and serve the whole site over HTTPS.",
                    why="HTTPS is a confirmed ranking signal and trust requirement.", scope="home"))
    for cid, hdr, label, sev, fix in [
        ("sec_hsts", "Strict-Transport-Security", "HSTS", "medium", "Add Strict-Transport-Security to force HTTPS."),
        ("sec_csp", "Content-Security-Policy", "Content Security Policy", "medium", "Add a Content-Security-Policy to mitigate XSS/injection."),
        ("sec_x_frame_options", "X-Frame-Options", "X-Frame-Options", "low", "Add X-Frame-Options (or CSP frame-ancestors)."),
        ("sec_x_content_type", "X-Content-Type-Options", "X-Content-Type-Options", "low", "Add X-Content-Type-Options: nosniff."),
        ("sec_referrer_policy", "Referrer-Policy", "Referrer-Policy", "low", "Add a Referrer-Policy header."),
        ("sec_permissions_policy", "Permissions-Policy", "Permissions-Policy", "low", "Add a Permissions-Policy to restrict powerful features."),
    ]:
        present = bool(_hget(headers, hdr))
        out.append(_chk(cid, "security", label, "pass" if present else "warn",
                        sev, "low", "%s present." % label if present else "%s missing." % label,
                        "" if present else fix, scope="home"))
    return out


# ---------------------------------------------------------------------------
# USER EXPERIENCE
# ---------------------------------------------------------------------------
def _ux_checks(ctx, home):
    out = []
    src = _psi_src(ctx)
    field = src.get("field") or {}
    lab = src.get("lab") or {}
    fm = field.get("metrics") or {}

    # layout stability (CLS)
    cls = None
    rating = None
    if fm.get("cls") and fm["cls"].get("p75") is not None:
        cls, rating = fm["cls"]["p75"], fm["cls"]["rating"]
    elif lab.get("cls") is not None:
        cls = lab["cls"]
        rating = "good" if cls <= 0.1 else ("needs-improvement" if cls <= 0.25
                                            else "poor")
    if cls is not None:
        st = {"good": "pass", "needs-improvement": "warn", "poor": "fail"}.get(
            rating, "na")
        out.append(_chk("ux_layout_stability", "ux", "Layout stability (CLS)",
                        st, "high", "med", "CLS %.2f (%s)." % (cls, rating),
                        "" if st == "pass" else
                        "Reserve space for images/ads and avoid layout shifts.",
                        scope="home"))
    # interactivity (INP field, else TBT proxy)
    if fm.get("inp") and fm["inp"].get("p75") is not None:
        m = fm["inp"]
        st = {"good": "pass", "needs-improvement": "warn", "poor": "fail"}.get(
            m["rating"], "na")
        out.append(_chk("ux_interactivity", "ux", "Interactivity (INP)", st,
                        "high", "high", "INP p75 %.0f ms (%s)." % (m["p75"],
                                                                   m["rating"]),
                        "" if st == "pass" else
                        "Reduce main-thread work and break up long tasks.",
                        scope="home"))
    elif lab.get("tbt_ms") is not None:
        tbt = lab["tbt_ms"]
        st = "pass" if tbt <= 200 else ("warn" if tbt <= 600 else "fail")
        out.append(_chk("ux_interactivity", "ux",
                        "Interactivity (lab TBT proxy)", st, "medium", "high",
                        "Total Blocking Time %.0f ms." % tbt,
                        "" if st == "pass" else
                        "Reduce JavaScript execution to improve responsiveness.",
                        scope="home"))

    # render-blocking / perceived speed
    opp = src.get("opportunities") or {}
    rb = opp.get("render_blocking_ms")
    if rb is not None:
        st = "pass" if rb < 300 else ("warn" if rb < 1000 else "fail")
        out.append(_chk("ux_render_blocking", "ux", "Perceived load speed", st,
                        "medium", "med", "%.0f ms blocked before render." % rb,
                        "" if st == "pass" else
                        "Eliminate render-blocking resources for a faster first "
                        "paint.", scope="home"))
    elif home.get("head_sync_js") is not None:
        n = home.get("head_sync_js", 0)
        st = "pass" if n == 0 else ("warn" if n <= 2 else "fail")
        out.append(_chk("ux_render_blocking", "ux", "Render-blocking scripts",
                        st, "medium", "med",
                        "%d synchronous script(s) in <head>." % n,
                        "" if st == "pass" else
                        "Add async/defer to head scripts.", scope="home"))

    # font optimization
    out.append(_chk("ux_font_optimization", "ux", "Font loading",
                    "pass" if home.get("font_preload") else "warn", "low", "low",
                    "Fonts use preconnect/preload." if home.get("font_preload")
                    else "No font preconnect/preload — text may flash or block.",
                    "" if home.get("font_preload") else
                    "Preconnect to font hosts and use font-display: swap.",
                    scope="home"))
    if not out:
        out.append(_chk("ux_unavailable", "ux", "User-experience signals", "na",
                        "medium", "med",
                        "Not enough data (needs PageSpeed / field data).",
                        scope="home"))
    return out


# ---------------------------------------------------------------------------
# LOCAL SEO
# ---------------------------------------------------------------------------
def _local_checks(ctx, home, all_facts):
    out = []
    ls = home.get("local_signals") or {}
    sd = (ctx.get("structured") or {}).get(home.get("final_url")) or {}
    types = sd.get("types") or []
    is_local_schema = any(t in ("LocalBusiness", "Store", "Restaurant", "Dentist", "MedicalBusiness", "ProfessionalService", "AutomotiveBusiness") for t in types)

    # 1. Google Business Profile & Maps Presence
    has_map = ls.get("has_map", False)
    out.append(_chk("local_gbp_maps", "local", "Google Maps & GBP Visibility",
                    "pass" if has_map else "warn", "medium", "low",
                    "Google Maps integration / location link detected." if has_map
                    else "No embedded Google Map or direct Google Business link found on site.",
                    "" if has_map else "Embed a Google Maps iframe or link directly to your verified Google Business Profile.",
                    why="Google Maps presence strongly signals localized trust and supports Local Pack rankings.",
                    scope="home"))

    # 2. LocalBusiness Schema Markup
    out.append(_chk("local_schema_markup", "local", "LocalBusiness Schema",
                    "pass" if is_local_schema else ("warn" if sd.get("present") else "fail"),
                    "high", "low",
                    f"Structured LocalBusiness schema detected ({', '.join([t for t in types if 'Business' in t or 'Store' in t or 'Restaurant' in t][:3]) or 'Valid'})."
                    if is_local_schema else "No LocalBusiness schema markup detected on the site.",
                    "" if is_local_schema else "Add schema.org/LocalBusiness JSON-LD with address, telephone, openingHours, and geo coordinates.",
                    why="LocalBusiness schema directly qualifies your site for rich local results and Google Maps knowledge cards.",
                    scope="home"))

    # 3. NAP - Telephone Consistency
    has_phone = ls.get("has_phone", False)
    phone_cnt = ls.get("phone_count", 0)
    out.append(_chk("local_nap_phone", "local", "Phone Number (Click-to-Call)",
                    "pass" if phone_cnt > 0 else ("warn" if has_phone else "fail"),
                    "high", "low",
                    f"Click-to-call 'tel:' links present ({phone_cnt} instance(s))." if phone_cnt > 0 else
                    ("Phone number found in text, but lacks a click-to-call 'tel:' link." if has_phone else "No telephone number detected on the homepage."),
                    "" if phone_cnt > 0 else "Wrap your primary phone number in a <a href='tel:...'> link for mobile callers and local search engines.",
                    scope="home"))

    # 4. NAP - Physical Address
    has_addr = ls.get("has_address", False)
    out.append(_chk("local_nap_address", "local", "Physical Address (NAP)",
                    "pass" if has_addr else "warn", "medium", "low",
                    "Physical address indicators detected on site." if has_addr else
                    "No physical street address detected on the homepage or footer.",
                    "" if has_addr else "Publish your consistent business Name, physical Address, and Phone (NAP) in the site footer and contact page.",
                    scope="home"))

    # 5. Local Citations & Directories
    cits = ls.get("citations") or []
    out.append(_chk("local_citations", "local", "Local Directory Citations",
                    "pass" if len(cits) >= 2 else ("info" if len(cits) == 1 else "warn"),
                    "low", "med",
                    f"Connected to local directory citations ({', '.join(cits)})." if cits else
                    "No links to major local directories (Yelp, Bing Places, YellowPages, BBB, Facebook).",
                    "" if cits else "Claim and link to consistent business profiles on Google Business, Yelp, Bing Places, and local chambers.",
                    scope="home"))

    # 6. Customer Reviews & Social Proof
    has_rev = ls.get("has_reviews", False)
    out.append(_chk("local_customer_reviews", "local", "Reviews & Social Proof",
                    "pass" if has_rev else "warn", "medium", "low",
                    "Customer reviews, ratings, or testimonial signals detected." if has_rev else
                    "No customer review widgets, testimonial sections, or review schema found.",
                    "" if has_rev else "Showcase verified customer reviews or add AggregateRating schema to boost click-through rates.",
                    scope="home"))
    return out


# ---------------------------------------------------------------------------
# OFF-PAGE & BACKLINK INTELLIGENCE
# ---------------------------------------------------------------------------
def _offpage_checks(ctx, home, all_facts):
    out = []
    auth = ctx.get("authority") or {}
    da = auth.get("domain_authority", 35)
    pa = auth.get("page_authority", 32)
    src_label = auth.get("source", "Authority Model")

    # 1. Domain Authority (DA) Score
    da_status = "pass" if da >= 40 else ("warn" if da >= 20 else "fail")
    out.append(_chk("offpage_domain_authority", "offpage", "Domain Authority (DA)",
                    da_status, "high", "high",
                    f"Domain Authority: {da}/100 ({src_label})." if da_status == "pass" else
                    f"Domain Authority: {da}/100 — opportunity to build high-quality industry backlinks.",
                    "" if da_status == "pass" else "Implement a digital PR and authoritative backlink building campaign to elevate domain equity.",
                    why="Higher domain authority correlates strongly with competitive keyword ranking capability.",
                    scope="site"))

    # 2. Page Authority (PA) Score
    pa_status = "pass" if pa >= 35 else ("warn" if pa >= 18 else "fail")
    out.append(_chk("offpage_page_authority", "offpage", "Page Authority (PA)",
                    pa_status, "medium", "high",
                    f"Homepage Authority: {pa}/100 ({src_label}).",
                    "" if pa_status == "pass" else "Acquire targeted external backlinks pointing directly to key landing pages.",
                    scope="home"))

    # 3. Outbound Link Ratio & Hygiene
    ext_dofollow = home.get("external_dofollow_count", 0)
    ext_nofollow = home.get("external_nofollow_count", 0)
    ext_total = ext_dofollow + ext_nofollow
    if ext_total > 50:
        st, fix, det = "warn", "Limit outbound links to prevent leaking PageRank equity.", f"{ext_total} outbound links on homepage ({ext_dofollow} dofollow)."
    else:
        st, fix, det = "pass", "", f"Healthy outbound link profile ({ext_dofollow} dofollow, {ext_nofollow} nofollow)."
    out.append(_chk("offpage_outbound_links", "offpage", "Outbound Link Ratio",
                    st, "low", "low", det, fix, scope="home"))

    # 4. Toxic Link Risk Heuristics
    toxic_flags = 0
    for f in all_facts:
        for link in f.get("external_links_details", []):
            domain = link.get("domain", "")
            tld = domain.split(".")[-1] if "." in domain else ""
            if tld in ("xyz", "top", "click", "win", "loan", "buzz"):
                toxic_flags += 1
    if toxic_flags > 0:
        out.append(_chk("offpage_toxic_risk", "offpage", "Potentially Risky Outbound Links",
                        "warn", "medium", "low",
                        f"Detected {toxic_flags} outbound link(s) to spam-prone TLDs (.xyz, .top, .click).",
                        "Audit outgoing external links and apply rel='nofollow' or rel='ugc' to unverified user content.",
                        scope="site"))
    else:
        out.append(_chk("offpage_toxic_risk", "offpage", "Outbound Link Safety",
                        "pass", "low", "low",
                        "No suspicious or spammy external link destinations detected.",
                        "", scope="site"))
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def build_checks(ctx):
    home = ctx["home"]
    all_facts = ctx.get("all_facts") or [home]
    enabled = ctx.get("enabled") or {k for k, _l, _w in CATEGORIES}
    checks = []
    if "technical" in enabled:
        checks += _technical_checks(ctx, home, all_facts)
    if "onpage" in enabled:
        checks += _onpage_checks(ctx, home, all_facts)
    if "performance" in enabled:
        checks += _performance_checks(ctx, home)
    if "content" in enabled:
        checks += _content_checks(ctx, home, all_facts)
    if "mobile" in enabled:
        checks += _mobile_checks(ctx, home)
    if "accessibility" in enabled:
        checks += _accessibility_checks(ctx, home)
    if "security" in enabled:
        checks += _security_checks(ctx, home)
    if "local" in enabled:
        checks += _local_checks(ctx, home, all_facts)
    if "offpage" in enabled:
        checks += _offpage_checks(ctx, home, all_facts)
    if "ux" in enabled:
        checks += _ux_checks(ctx, home)
    return checks
