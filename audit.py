# -*- coding: utf-8 -*-
"""
Audit orchestrator.

run_audit(params, progress) sequences the whole pipeline — crawl → per-page
facts → site-wide link check → PageSpeed Insights → structured data → content
metrics → scoring → optional AI executive summary — and returns one result dict
the report builders consume.

Modeled on Content Quality Auditor's pipeline.py: a threading.Event cancel token
checked between stages via _ck(), and every stage wrapped so one failure records
an error but never aborts the report.
"""

import re
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests

import authority_traffic
import crawler
import llm
import pagespeed
import scoring
import security_audit
import seo_checks
import structured_data

# text primitives (ported from Web Content Analyzer's analyzer.py) --------------
_STOP = set("""the a an and or but of to in on for with by from as is are be been
being this that these those you your we our they their it its at into over under
up out off can could will would should may might must do does did not no new need
needs more most very much many all any each other some one two""".split())


def _words(text):
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", (text or "").lower())


def _sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', (text or "").strip())
    return [s for s in parts if s.strip()]


def _syllables(word):
    groups = re.findall(r'[aeiouy]+', word.lower())
    n = len(groups)
    if word.lower().endswith('e') and n > 1:
        n -= 1
    return max(1, n)


def _flesch(text):
    sents, wds = _sentences(text), _words(text)
    if not sents or not wds:
        return None
    syl = sum(_syllables(w) for w in wds)
    return round(206.835 - 1.015 * (len(wds) / len(sents))
                 - 84.6 * (syl / len(wds)), 1)


def _top_bigram(text):
    wds = _words(text)
    c = collections.Counter()
    for i in range(len(wds) - 1):
        g = wds[i:i + 2]
        if any(x in _STOP or len(x) < 3 for x in g):
            continue
        c[" ".join(g)] += 1
    return c.most_common(1)[0][0] if c else (wds[0] if wds else "")


def _density(text, phrase):
    wds = _words(text)
    total = len(wds)
    if not total or not phrase:
        return 0.0
    p = phrase.lower().split()
    if len(p) == 1:
        hits = sum(1 for w in wds if w == p[0])
    else:
        hits = sum(1 for i in range(total - len(p) + 1)
                   if wds[i:i + len(p)] == p)
    return round(hits / total * 100, 2)


def _ck(cancel):
    if cancel is not None and cancel.is_set():
        raise RuntimeError("Audit cancelled.")


def _key_findings(checks, score):
    """Strengths / weaknesses / opportunities for the executive section."""
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    fails = sorted([c for c in checks if c["status"] == "fail"],
                   key=lambda c: sev_rank.get(c["severity"], 4))
    warns = sorted([c for c in checks if c["status"] == "warn"],
                   key=lambda c: sev_rank.get(c["severity"], 4))
    strong = [c for c in checks if c["status"] == "pass"
              and c["severity"] in ("critical", "high")]
    dims = (score or {}).get("dimensions", [])
    strengths = ["%s (%d/100)" % (d["label"], d["score"]) for d in
                 sorted(dims, key=lambda x: -x["score"]) if d["score"] >= 80][:5]
    if not strengths:
        strengths = ["%s — %s" % (c["label"], c["detail"]) for c in strong][:5]
    weaknesses = ["%s — %s" % (c["label"], c["detail"]) for c in fails][:6]
    opportunities = ["%s — %s" % (c["label"], c.get("fix") or c["detail"])
                     for c in warns if c["severity"] in ("high", "medium")][:6]
    return {"strengths": strengths, "weaknesses": weaknesses,
            "opportunities": opportunities}


def _analyze_competitor(url, proxy=None, psi_key=None):
    """Lightweight single-page audit of a competitor for side-by-side compare."""
    u = crawler.normalize_url(url)
    page = crawler.fetch_page(u, timeout=25, proxy=proxy)
    if not page.get("is_html") or not page.get("html"):
        return {"url": url, "domain": urlparse(u).netloc, "error": "unreachable"}
    home = seo_checks.page_facts(page)
    final = page.get("final_url") or u
    try:
        robots = crawler.fetch_robots(u, proxy=proxy)
    except Exception:
        robots = {"present": False, "sitemaps": []}
    try:
        sm = crawler.fetch_sitemaps(
            robots.get("sitemaps") or [crawler.origin(u) + "/sitemap.xml"],
            proxy=proxy)
    except Exception:
        sm = {"count": 0}
    psi = {}
    try:
        psi["mobile"] = pagespeed.run_pagespeed(final, "mobile", api_key=psi_key)
    except Exception:
        pass
    structured = {}
    try:
        structured[final] = structured_data.analyze(page["html"])
    except Exception:
        pass
    content = {}
    try:
        mt, _m = crawler.extract_main_text(page["html"], final)
        content[final] = {"word_count": len(_words(mt)), "flesch": _flesch(mt),
                          "primary_keyword": _top_bigram(mt)}
    except Exception:
        pass
    ctx = {"home": home, "all_facts": [home], "robots": robots, "sitemap": sm,
           "http_redirect": None, "broken_links": None, "psi": psi,
           "structured": structured, "content": content,
           "enabled": {k for k, _l, _w in seo_checks.CATEGORIES}}
    checks = seo_checks.build_checks(ctx)
    score = scoring.compute(checks)
    dims = {d["key"]: d["score"] for d in (score or {}).get("dimensions", [])}
    return {
        "url": url, "domain": urlparse(final).netloc,
        "score": (score or {}).get("score"), "grade": (score or {}).get("grade"),
        "perf_mobile": (psi.get("mobile") or {}).get("scores", {}).get(
            "performance") if psi.get("mobile", {}).get("ok") else None,
        "technical": dims.get("technical"), "onpage": dims.get("onpage"),
        "content_score": dims.get("content"),
        "https": final.startswith("https://"),
        "title_len": home.get("title_len", 0),
        "meta_len": home.get("meta_desc_len", 0),
        "word_count": home.get("word_count", 0), "h1": home.get("h1_count", 0),
        "sd": bool((structured.get(final) or {}).get("present")),
    }


# ---------------------------------------------------------------------------
def _check_links(home_facts, pages, cancel, proxy=None, cap=30, workers=6):
    """Verify internal links: reuse crawled statuses, HEAD-probe a bounded
    sample of the rest. Returns {checked, broken:[{url,status}]}."""
    targets = []
    seen = set()
    for u in home_facts.get("internal_links") or []:
        nu = crawler.normalize_url(u)
        if nu and nu not in seen:
            seen.add(nu)
            targets.append(nu)
    crawled = {crawler.normalize_url(u): p for u, p in pages.items()}
    broken, checked = [], 0
    to_probe = []
    for t in targets:
        p = crawled.get(t)
        if p is not None:
            checked += 1
            if p.get("status", 0) >= 400 or (not p.get("ok") and
                                             p.get("status", 0) == 0):
                broken.append({"url": t, "status": p.get("status", 0)})
        else:
            to_probe.append(t)
    to_probe = to_probe[:cap]
    if to_probe:
        sess = requests.Session()

        def probe(u):
            pg = crawler.fetch_page(u, sess, timeout=15, proxy=proxy,
                                    method="HEAD")
            if pg["status"] in (405, 501) or pg["status"] == 0:
                pg = crawler.fetch_page(u, sess, timeout=15, proxy=proxy,
                                        method="GET")
            return u, pg["status"]

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(probe, u) for u in to_probe]
            for fut in as_completed(futs):
                try:
                    u, status = fut.result()
                except Exception:
                    continue
                checked += 1
                if status >= 400 or status == 0:
                    broken.append({"url": u, "status": status})
    return {"checked": checked, "broken": broken}


def _http_redirect_check(home_url, proxy=None):
    """Does the http:// origin redirect to https://?"""
    o = crawler.origin(home_url)
    http_url = "http://" + o.split("://", 1)[-1] + "/"
    pg = crawler.fetch_page(http_url, timeout=15, proxy=proxy, method="GET")
    return {
        "reachable": bool(pg.get("status")),
        "redirects_to_https": (pg.get("final_url", "").startswith("https://")
                               and pg.get("status", 0) < 400),
    }


def _exec_summary_template(result):
    sc = result.get("score") or {}
    items = result.get("action_items") or []
    domain = result.get("domain", "your website")
    crit = [it for it in items if it["priority"] in ("Critical", "High")]
    parts = ["%s scored %d out of 100 (grade %s) in this audit — %s."
             % (domain, sc.get("score", 0), sc.get("grade", "?"),
                sc.get("label", "").lower()),
             "We graded the site strictly against current best-practice "
             "standards — the same bar Google and your competitors set."]
    if crit:
        parts.append("We found %d high-impact issue%s most likely holding back "
                     "your search visibility and the leads it should bring; the "
                     "top priority is “%s”."
                     % (len(crit), "" if len(crit) == 1 else "s",
                        crit[0]["label"]))
    else:
        parts.append("No critical issues surfaced — the focus now is widening "
                     "your lead over competitors.")
    parts.append("Working through the priorities in this report is the fastest "
                 "route to more organic traffic, stronger rankings and a better "
                 "return from your website.")
    return " ".join(parts)


def _exec_summary_ai(result, api_key, model):
    sc = result.get("score") or {}
    dims = sc.get("dimensions") or []
    items = result.get("action_items") or []
    lines = ["Domain: %s" % result.get("domain", ""),
             "Overall score: %d/100 (grade %s, %s)"
             % (sc.get("score", 0), sc.get("grade", ""), sc.get("label", "")),
             "Category scores:"]
    for d in dims:
        lines.append("  - %s: %d/100" % (d["label"], d["score"]))
    lines.append("Top issues:")
    for it in items[:10]:
        lines.append("  - [%s] %s — %s" % (it["priority"], it["label"],
                                           it["detail"]))
    system = ("You are a senior technical SEO consultant writing the executive "
              "summary of an audit for a client. Be specific, professional and "
              "concrete. 4–6 sentences, plain text, no markdown, no lists.")
    user = ("Write the executive summary for this SEO audit. Lead with the "
            "overall verdict, name the biggest wins and the most urgent fixes, "
            "and end with the expected payoff of acting.\n\n" + "\n".join(lines))
    return llm.chat(api_key, model, system, user, json_mode=False,
                    temperature=0.4, max_tokens=600).strip()


# ---------------------------------------------------------------------------
def run_audit(params, progress=None):
    """Main entry. params keys:
      url (str, required), max_pages (int), single_page (bool),
      psi_strategy ('both'|'mobile'), psi_key (str), proxy (str),
      enabled (set of category keys), api_key (str), model (str),
      cancel (threading.Event).
    """
    log = progress if callable(progress) else (lambda m: None)
    cancel = params.get("cancel")
    errors, timings = [], {}

    url = crawler.normalize_url(params.get("url", ""))
    if not url:
        raise ValueError("Please enter a website URL.")
    single_page = bool(params.get("single_page"))
    max_pages = 1 if single_page else int(params.get("max_pages") or 25)
    proxy = params.get("proxy") or None
    enabled = params.get("enabled") or {k for k, _l, _w in seo_checks.CATEGORIES}

    result = {
        "url": params.get("url", ""), "final_url": url,
        "domain": urlparse(url).netloc,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "single_page": single_page, "errors": errors, "timings": timings,
        "model": "", "psi_key_used": bool(params.get("psi_key")),
    }

    # 1) homepage --------------------------------------------------------
    log("Fetching homepage…")
    home_page = crawler.fetch_page(url, timeout=25, proxy=proxy)
    if not home_page.get("is_html") or not home_page.get("html"):
        if home_page.get("error"):
            raise ValueError("Could not fetch the site: %s" % home_page["error"])
        raise ValueError("The URL did not return an HTML page (status %s)."
                         % home_page.get("status"))
    result["final_url"] = home_page.get("final_url") or url
    result["domain"] = urlparse(result["final_url"]).netloc

    # 2) robots + sitemap ------------------------------------------------
    _ck(cancel)
    log("Reading robots.txt and sitemap…")
    robots = sitemap = None
    try:
        robots = crawler.fetch_robots(url, proxy=proxy)
    except Exception as e:
        errors.append("robots.txt: %s" % e)
        robots = {"present": False, "status": 0, "sitemaps": [], "ai_rules": {}}
    try:
        sm_urls = list(robots.get("sitemaps") or [])
        if not sm_urls:
            sm_urls = [crawler.origin(url) + "/sitemap.xml"]
        sitemap = crawler.fetch_sitemaps(sm_urls, proxy=proxy)
    except Exception as e:
        errors.append("sitemap: %s" % e)
        sitemap = {"urls": [], "count": 0, "errors": [str(e)], "had_index": False}
    result["robots"], result["sitemap"] = robots, sitemap

    # 3) crawl -----------------------------------------------------------
    _ck(cancel)
    if single_page:
        pages = {url: home_page}
        order = [url]
    else:
        log("Crawling the site…")
        try:
            pages, order = crawler.crawl(
                url, max_pages=max_pages, seed_urls=sitemap.get("urls"),
                progress=log, cancel=cancel, proxy=proxy)
        except RuntimeError:
            raise
        except Exception as e:
            errors.append("crawl: %s" % e)
            pages, order = {url: home_page}, [url]
        pages.setdefault(url, home_page)
        if url not in order:
            order.insert(0, url)

    # 4) per-page facts --------------------------------------------------
    _ck(cancel)
    log("Analyzing pages…")
    all_facts, home_facts = [], None
    for u in order:
        try:
            f = seo_checks.page_facts(pages[u])
        except Exception as e:
            errors.append("facts %s: %s" % (u, e))
            continue
        all_facts.append(f)
        if u == url:
            home_facts = f
    if home_facts is None:
        home_facts = seo_checks.page_facts(home_page)
        all_facts.insert(0, home_facts)
    result["home_facts"] = home_facts
    result["all_facts"] = all_facts
    result["pages_crawled"] = len(all_facts)
    result["crawl_order"] = order

    # 5) http->https + broken links -------------------------------------
    _ck(cancel)
    try:
        result["http_redirect"] = _http_redirect_check(result["final_url"], proxy)
    except Exception as e:
        errors.append("http redirect: %s" % e)
        result["http_redirect"] = None
    # Broken-link checking feeds the Technical SEO category.
    if not single_page and ("technical" in enabled or "crawl" in enabled):
        log("Checking internal links…")
        try:
            result["broken_links"] = _check_links(home_facts, pages, cancel,
                                                  proxy=proxy)
        except RuntimeError:
            raise
        except Exception as e:
            errors.append("link check: %s" % e)
            result["broken_links"] = None
    else:
        result["broken_links"] = None

    # 6) PageSpeed Insights ---------------------------------------------
    _ck(cancel)
    psi = {}
    if "performance" in enabled:
        strat = params.get("psi_strategy") or "both"
        strategies = ["mobile", "desktop"] if strat == "both" else ["mobile"]
        psi_key = params.get("psi_key") or None
        target = result["final_url"]

        def run_one(s):
            return s, pagespeed.run_pagespeed(target, s, api_key=psi_key)

        log("Running PageSpeed Insights (%s)…" % ", ".join(strategies))
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = [ex.submit(run_one, s) for s in strategies]
                for fut in as_completed(futs):
                    s, res = fut.result()
                    psi[s] = res
                    if not res.get("ok"):
                        errors.append("PSI %s: %s" % (s, res.get("error", "")))
        except Exception as e:
            errors.append("PageSpeed: %s" % e)
    result["psi"] = psi

    # 7) structured data -------------------------------------------------
    _ck(cancel)
    structured = {}
    # Structured-data validation feeds the Technical SEO category.
    if ("technical" in enabled or "structured" in enabled) and home_page.get("html"):
        log("Validating structured data…")
        try:
            structured[result["final_url"]] = structured_data.analyze(
                home_page["html"])
        except Exception as e:
            errors.append("structured data: %s" % e)
    result["structured"] = structured

    # 8) content metrics -------------------------------------------------
    _ck(cancel)
    content = {}
    if "content" in enabled or "onpage" in enabled:
        try:
            main_text, method = crawler.extract_main_text(
                home_page["html"], result["final_url"])
            kw = _top_bigram(main_text)
            content[result["final_url"]] = {
                "extraction_method": method,
                "word_count": len(_words(main_text)),
                "flesch": _flesch(main_text),
                "primary_keyword": kw,
                "keyword_density": _density(main_text, kw),
            }
        except Exception as e:
            errors.append("content metrics: %s" % e)
    result["content"] = content

    # 8b) SSL cert, orphan pages, authority, and traffic estimation -------
    _ck(cancel)
    log("Checking SSL security certificate…")
    ssl_cert = None
    try:
        ssl_cert = crawler.get_ssl_certificate_details(result["final_url"])
    except Exception as e:
        errors.append("ssl cert: %s" % e)
        ssl_cert = {"valid": False, "error": str(e)}
    result["ssl_cert"] = ssl_cert

    # In-degree / orphan pages analysis from crawl graph
    in_degrees = {f["final_url"]: 0 for f in all_facts}
    for f in all_facts:
        for il in f.get("internal_links", []):
            if il in in_degrees and il != f["final_url"]:
                in_degrees[il] += 1
    orphan_pages = [u for u, cnt in in_degrees.items() if cnt <= 1 and u != result["final_url"]]
    result["orphan_pages"] = orphan_pages

    # 8c) Passive Website Security Audit ---------------------------------
    _ck(cancel)
    sec_audit = None
    if "security" in enabled:
        log("Running passive security audit (headers, SSL, cookies, DNS, files)…")
        try:
            sec_audit = security_audit.run_full_security_audit(
                result["final_url"], home_page, all_facts, proxy=proxy, timeout=6
            )
        except Exception as e:
            errors.append("security audit: %s" % e)
            sec_audit = None
    result["security_audit"] = sec_audit
    if sec_audit and sec_audit.get("ssl"):
        ssl_cert = sec_audit["ssl"]
        result["ssl_cert"] = ssl_cert

    log("Evaluating Domain Authority (DA) & Traffic…")
    psi_score = ((psi.get("mobile") or {}).get("scores") or {}).get("performance")
    authority = authority_traffic.calculate_authority_scores(
        result["final_url"], crawl_facts=all_facts, home_facts=home_facts,
        psi_score=psi_score, ssl_info=ssl_cert
    )
    result["authority"] = authority

    da_val = authority.get("domain_authority", 35)
    for f in all_facts:
        is_home = (f.get("final_url") == result["final_url"]) or (f.get("url") == result["final_url"])
        f["page_authority"] = authority_traffic.calculate_page_authority(f, da_score=da_val, is_home=is_home)

    traffic = authority_traffic.estimate_monthly_traffic(
        result["domain"], da_score=da_val,
        pages_crawled=len(all_facts)
    )
    result["traffic"] = traffic

    # 8d) Google Maps & GBP Lookup -------------------------------------
    _ck(cancel)
    log("Checking Google Maps & Business Profile…")
    try:
        import google_maps_scraper
        gbp_place, gbp_note = google_maps_scraper.scrape_google_maps(result["final_url"], timeout=12)
        result["gbp"] = gbp_place
        result["gbp_note"] = gbp_note
    except Exception as e:
        result["gbp"] = None
        result["gbp_note"] = f"error: {e}"

    # 9) build checks + score -------------------------------------------
    _ck(cancel)
    log("Scoring…")
    ctx = {
        "home": home_facts, "all_facts": all_facts, "robots": robots,
        "sitemap": sitemap, "http_redirect": result.get("http_redirect"),
        "broken_links": result.get("broken_links"), "psi": psi,
        "structured": structured, "content": content, "enabled": enabled,
        "ssl_cert": ssl_cert, "orphan_pages": orphan_pages,
        "authority": authority, "traffic": traffic,
        "security_audit": sec_audit,
    }
    try:
        checks = seo_checks.build_checks(ctx)
    except Exception as e:
        errors.append("checks: %s" % e)
        checks = []
    result["checks"] = checks
    result["score"] = scoring.compute(checks)
    result["action_items"] = scoring.action_items(checks)
    result["priority_counts"] = scoring.priority_counts(result["action_items"])
    result["key_findings"] = _key_findings(checks, result["score"])

    # 9b) optional competitor comparison ---------------------------------
    comps = [c for c in (params.get("competitors") or []) if c.strip()]
    result["competitors"] = []
    if comps:
        for cu in comps[:3]:
            _ck(cancel)
            log("Analyzing competitor %s…" % cu)
            try:
                result["competitors"].append(
                    _analyze_competitor(cu, proxy, params.get("psi_key")))
            except Exception as e:
                errors.append("competitor %s: %s" % (cu, e))

    # 10) optional AI executive summary ---------------------------------
    _ck(cancel)
    api_key = (params.get("api_key") or "").strip()
    model = (params.get("model") or "").strip()
    summary, source = None, "template"
    if api_key and result.get("score"):
        log("Writing executive summary (AI)…")
        try:
            summary = _exec_summary_ai(result, api_key,
                                       model or llm.default_model_for_key(api_key))
            source = "ai"
            result["model"] = model or llm.default_model_for_key(api_key)
        except Exception as e:
            errors.append("AI summary: %s" % e)
    if not summary:
        summary = _exec_summary_template(result)
        source = "template"
    result["exec_summary"] = summary
    result["exec_summary_source"] = source

    log("Done.")
    return result
