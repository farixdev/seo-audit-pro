# -*- coding: utf-8 -*-
"""
Full SEO Audit report (HTML, printable to PDF) for the standalone SEO Audit Tool.

Sections: Summary, Website Speed, All SEO, Website Design, Local Ranking,
Google Business Profile, Competitors, Security, Action plan. Every problem is
shown with what was found, why it matters and possible solutions. No prices.
Only real audit data is used; estimates are labelled as estimates.
"""

import html
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

E = html.escape

SECTIONS = [
    ("speed", "Website Speed", ("performance",)),
    ("seo", "All SEO", ("technical", "onpage", "content", "offpage")),
    ("design", "Website Design", ("ux", "mobile", "accessibility")),
    ("local", "Local Ranking", ("local",)),
    ("security", "Security", ("security",)),
]
CATEGORY_LABEL = {"performance": "Speed", "technical": "Technical SEO", "onpage": "On-page SEO",
                  "content": "Content", "offpage": "Off-page & authority", "ux": "User experience",
                  "mobile": "Mobile", "accessibility": "Accessibility", "local": "Local SEO", "security": "Security"}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEV_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

# Extra, concrete solutions for common problems (the engine's own fix comes first).
SOLUTIONS = [
    (("unused_css", "unused css", "unused-css"), ["Remove CSS from plugins/themes that the page does not use (e.g. with Asset CleanUp or PurgeCSS).",
                                     "Load page-specific CSS only on the pages that need it."]),
    (("unused_js", "unused javascript", "unused-javascript"), ["Remove unused scripts and plugins; load the rest with defer.",
                                     "Delay third-party scripts (chat, analytics, pixels) until the visitor interacts."]),
    (("lcp",), ["Compress and resize the main (hero) image; serve it as WebP/AVIF.",
                "Preload the hero image and the main font; remove render-blocking CSS/JS above the fold.",
                "Use good hosting or a CDN so the server answers fast (aim for under 600 ms)."]),
    (("cls",), ["Give every image, video and ad slot a fixed width and height.",
                "Load web fonts with font-display: swap and avoid inserting banners above existing content."]),
    (("tbt", "tti", "main_thread", "javascript", "js_"), [
        "Remove unused plugins and scripts; defer or delay non-essential JavaScript (chat widgets, trackers).",
        "Split large bundles and load them only on the pages that need them."]),
    (("fcp", "speed_index", "render_block"), [
        "Inline the critical CSS and defer the rest; load fonts with preconnect.",
        "Enable caching and compression (Gzip/Brotli) on the server."]),
    (("image", "img_"), ["Compress images (WebP), use responsive sizes (srcset) and lazy-load images below the fold."]),
    (("compress", "gzip", "brotli"), ["Turn on Gzip or Brotli compression in the hosting panel or CDN."]),
    (("cache",), ["Set long cache headers for images, CSS and JS (e.g. 1 year with versioned file names)."]),
    (("title",), ["Write a unique title of 50–60 characters per page: main keyword first, brand last."]),
    (("meta", "description"), ["Write a unique meta description of 140–160 characters with a clear benefit and call to action."]),
    (("h1",), ["Use exactly one H1 per page that states the page topic with the main keyword."]),
    (("heading",), ["Structure content with H2/H3 headings in order; one idea per section."]),
    (("alt",), ["Add short, descriptive alt text to every meaningful image (decorative images: empty alt)."]),
    (("canonical",), ["Add a self-referencing canonical tag on every page; point duplicates to the main version."]),
    (("sitemap",), ["Generate an XML sitemap (Yoast/RankMath or your CMS), list it in robots.txt and submit it in Search Console."]),
    (("robots",), ["Keep robots.txt simple: do not block CSS/JS or important pages; add the sitemap line."]),
    (("schema", "structured"), ["Add JSON-LD structured data: Organization/LocalBusiness on the home page, plus Service, FAQ, "
                                "Article or Product where relevant; test it with Google's Rich Results Test."]),
    (("broken", "404"), ["Fix or redirect (301) every broken link; update internal links to the final URLs."]),
    (("orphan", "internal_link"), ["Link to these pages from related pages and menus with descriptive anchor text."]),
    (("word", "thin", "content"), ["Expand thin pages with useful, original content that answers the visitor's questions (aim 600+ words on key pages)."]),
    (("ssl", "https", "certificate"), ["Install a valid SSL certificate (free with Let's Encrypt/AutoSSL), redirect all HTTP to HTTPS and renew before expiry."]),
    (("hsts",), ["Add the Strict-Transport-Security header (max-age=31536000; includeSubDomains)."]),
    (("csp", "content_security"), ["Add a Content-Security-Policy header, starting in report-only mode, then enforce it."]),
    (("frame", "clickjack"), ["Add X-Frame-Options: SAMEORIGIN (or CSP frame-ancestors 'self')."]),
    (("x_content_type", "nosniff"), ["Add the X-Content-Type-Options: nosniff header."]),
    (("referrer",), ["Add Referrer-Policy: strict-origin-when-cross-origin."]),
    (("cookie",), ["Set Secure, HttpOnly and SameSite on cookies."]),
    (("viewport", "mobile", "tap", "font_size"), ["Use a responsive layout with the viewport meta tag; buttons at least 48 px apart, text at least 16 px."]),
    (("gbp", "maps"), ["Claim and verify the Google Business Profile, link it to the website and embed the Google map on the contact page."]),
    (("nap", "phone", "address"), ["Show the same business Name, Address and Phone on every page (footer) and on every directory listing."]),
    (("citation",), ["List the business on the main directories (Bing Places, Apple Business Connect, Yelp, Facebook, industry sites) with the same NAP."]),
    (("review",), ["Ask every happy customer for a Google review (send a short review link) and reply to every review."]),
    (("contrast",), ["Increase text/background contrast to at least 4.5:1."]),
    (("label", "aria", "form"), ["Give every form field a visible label and every button a clear name."]),
    (("cta", "call_to_action", "contact"), ["Put one clear call to action (call / book / quote) above the fold on every key page."]),
    (("backlink", "authority", "offpage"), ["Earn links from local news, partners, suppliers and industry directories; publish useful guides people want to cite."]),
]


def _solutions(check):
    out = []
    fix = (check.get("fix") or "").strip()
    if fix and not fix.lower().startswith("optimise to improve") and "lighthouse" not in fix.lower():
        out.append(fix)
    key = f"{check.get('id', '')} {check.get('label', '')}".lower().replace("-", "_")
    for needles, extra in SOLUTIONS:
        if any(n in key for n in needles):
            out += [x for x in extra if x not in out]
            break
    if not out:
        out.append("Review this item with your web developer and fix it on every affected page.")
    return out


def _where(check):
    w = check.get("where") or []
    if isinstance(w, str):
        w = re.findall(r"https?://[^'\",\]\s]+", w)
    return [u for u in w if isinstance(u, str)][:6]


def _score_color(score):
    if score is None:
        return "#71717A"
    return "#12B76A" if score >= 90 else "#F79009" if score >= 50 else "#F04438"


def _gauge(score, label, sub=""):
    s = "—" if score is None else int(round(score))
    pct = 0 if score is None else max(0, min(100, score))
    color = _score_color(score)
    return (f'<div class="gauge"><div class="ring" style="--p:{pct};--c:{color}"><span>{s}</span></div>'
            f'<div class="g-label">{E(label)}</div><div class="g-sub">{E(sub)}</div></div>')


def _ms(v):
    if v is None:
        return "—"
    return f"{v / 1000:.1f} s" if v >= 1000 else f"{int(v)} ms"


def _issue_card(c):
    sev = c.get("severity") or "low"
    status = c.get("status")
    tag = "Needs fixing" if status == "fail" else "Can be improved"
    sols = "".join(f"<li>{E(s)}</li>" for s in _solutions(c))
    where = _where(c)
    where_html = ("<div class='where'><b>Where:</b> " + ", ".join(f"<span>{E(u)}</span>" for u in where) + "</div>") if where else ""
    why = c.get("why") or c.get("impact") or ""
    return (f'<div class="issue sev-{E(sev)}"><div class="issue-head"><span class="badge b-{E(sev)}">{SEV_LABEL.get(sev, sev)}</span>'
            f'<span class="tag">{tag}</span><h4>{E(c.get("label") or "")}</h4></div>'
            f'<p class="found"><b>What we found:</b> {E(c.get("detail") or "")}</p>'
            + (f'<p class="why"><b>Why it matters:</b> {E(why)}</p>' if why else "")
            + f'<div class="sol"><b>Possible solutions</b><ul>{sols}</ul></div>{where_html}</div>')


def _passed_list(checks):
    good = [c for c in checks if c.get("status") == "pass"]
    if not good:
        return ""
    return ("<details class='passed'><summary>" + f"{len(good)} checks passed" + "</summary><ul>"
            + "".join(f"<li>✓ {E(c.get('label') or '')}</li>" for c in good) + "</ul></details>")


def _section_checks(result, cats):
    checks = [c for c in result.get("checks") or [] if c.get("category") in cats]
    problems = sorted([c for c in checks if c.get("status") in ("fail", "warn")],
                      key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
    return checks, problems


def _dimension(result, key):
    for d in (result.get("score") or {}).get("dimensions") or []:
        if d.get("key") == key:
            return d.get("score")
    return None


# ---------------------------------------------------------------------------
# Google Business Profile
# ---------------------------------------------------------------------------
def gbp_lookup(query, domain, key, timeout=20, hints=None):
    """Find the business's Google Business Profile (see google_maps_scraper).
    ``query`` is the business name the user typed (optional). Returns (place, note)."""
    import google_maps_scraper
    hints = dict(hints or {})
    if query:
        hints["names"] = [query] + [n for n in hints.get("names") or [] if n != query]
    return google_maps_scraper.lookup(domain, hints, api_key=key or None, timeout=min(timeout, 15))


def _gbp_hours_text(place):
    return "; ".join((place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [])


def gbp_checks(place, site_domain, hints=None):
    """Checklist for a Google Business Profile, each with a solution.

    Only what was really read is judged. Places API data is complete (a field it
    leaves out is really missing on the profile); data read from the public Maps
    page (limited view) omits some fields, and those checks are 'na' -- never a
    pass or a fail."""
    import google_maps_scraper as gms
    hints = hints or {}
    out = []
    api = place.get("_source") == "places_api"

    def add(key, label, status, detail, fix, sev="medium"):
        out.append({"id": "gbp_" + key, "label": label, "status": status, "severity": sev, "detail": detail,
                    "fix": fix, "category": "local"})

    def na(key, label, fix, sev="medium", why="Not shown to automated checks on Google Maps (needs the Google Places API key)."):
        add(key, label, "na", why, fix, sev)

    # 1. Open for business
    status = place.get("businessStatus") or ("OPERATIONAL" if api else None)
    fix = "Correct the business status in Google Business Profile (a closed listing gets no calls)."
    if status:
        add("open", "Business shown as open", "pass" if status == "OPERATIONAL" else "fail",
            "Google shows the business as " + status.replace("_", " ").lower() + ".", fix, "critical")
    else:
        na("open", "Business shown as open", fix, "critical")

    # 2. Claimed by the owner
    fix = "Claim the profile at business.google.com and verify it, so only you control the listing."
    if place.get("claimed") is True:
        add("claimed", "Profile claimed by the owner", "pass",
            "No \"Claim this business\" link is shown, so the profile is managed.", fix, "critical")
    elif place.get("claimed") is False:
        add("claimed", "Profile claimed by the owner", "fail",
            "Google shows \"Claim this business\": nobody manages this listing and anyone can suggest changes.",
            fix, "critical")
    else:
        na("claimed", "Profile claimed by the owner", fix, "critical", "Could not be read from Google Maps.")

    # 3. Website
    site = place.get("websiteUri") or ""
    fix = "Add the website address to the profile (the exact domain of this site)."
    if site and gms.same_site(site, site_domain):
        add("website", "Website linked", "pass", f"The profile links to {gms._clean_domain(site)}.", fix, "high")
    elif site:
        add("website", "Website linked", "fail", f"The profile links to {gms._clean_domain(site)}, not {site_domain}.",
            fix, "high")
    else:
        add("website", "Website linked", "fail", "The profile has no website link.", fix, "high")

    # 4. Phone, and does it match the website (NAP)
    phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or ""
    site_phones = hints.get("phones") or []
    fix = "Add the main phone number, the same one shown on the website."
    add("phone", "Phone number listed", "pass" if phone else "fail",
        f"Phone on the profile: {phone}." if phone else "No phone number on the profile.", fix, "high")
    fix = "Use one main phone number everywhere: website header/footer, Google profile and directories."
    if phone and site_phones:
        ok = gms.same_phone(phone, site_phones)
        add("phone_match", "Phone matches the website", "pass" if ok else "warn",
            f"Profile {phone} / website {', '.join(site_phones[:2])}.", fix, "medium")
    else:
        na("phone_match", "Phone matches the website", fix, "medium",
           "No phone number found on the website to compare." if phone else "No phone on the profile.")

    # 5. Name consistency
    gname = (place.get("displayName") or {}).get("text", "")
    names = hints.get("names") or []
    fix = "Use the same business name on the website and on Google (no extra keywords in the Google name)."
    if gname and names:
        best = max(gms.name_similarity(gname, n) for n in names)
        add("name_match", "Business name matches the website", "pass" if best >= 0.5 else "warn",
            f"Google: \"{gname}\" / website: \"{names[0]}\".", fix, "low")
    else:
        na("name_match", "Business name matches the website", fix, "low", "No business name found on the website to compare.")

    # 6. Address
    addr = place.get("formattedAddress") or ""
    fix = "Add the full address (or a service area if you visit customers) so you show up in local searches."
    add("address", "Address or service area", "pass" if addr else "warn",
        f"Address: {addr}." if addr else "No address is shown (a service-area business may hide it on purpose).",
        fix, "medium")

    # 7. Opening hours
    fix = "Add opening hours for every day, plus holiday hours, so Google can show you as \"Open now\"."
    if place.get("regularOpeningHours"):
        text = _gbp_hours_text(place)
        add("hours", "Opening hours listed", "pass", f"Hours: {text}." if text else "Opening hours are listed.", fix, "medium")
    else:
        add("hours", "Opening hours listed", "fail", "No opening hours on the profile.", fix, "medium")

    # 8. Category
    cat = (place.get("primaryTypeDisplayName") or {}).get("text", "")
    fix = "Choose the most specific primary category and add every relevant secondary category."
    add("category", "Main category set", "pass" if cat else "fail",
        f"Category: {cat}." if cat else "No category is shown.", fix, "high")

    # 9. Rating
    rating, count = place.get("rating"), place.get("userRatingCount")
    fix = "Reply to every review, fix the causes of bad ones and ask happy customers to review."
    if rating is not None:
        add("rating", "Star rating 4.5 or higher", "pass" if rating >= 4.5 else ("warn" if rating >= 4.0 else "fail"),
            f"{rating:.1f} stars" + (f" from {count} reviews." if count is not None else "."), fix, "high")
    elif api or count == 0:
        add("rating", "Star rating 4.5 or higher", "fail", "No rating yet: the profile has no reviews.", fix, "high")
    else:
        na("rating", "Star rating 4.5 or higher", fix, "high")

    # 10. Number of reviews
    fix = "Ask every happy customer for a review (QR code at the counter, link in invoices and follow-up emails)."
    if count is not None:
        add("reviews", "At least 50 reviews", "pass" if count >= 50 else ("warn" if count >= 10 else "fail"),
            f"{count} reviews on Google.", fix, "high")
    else:
        na("reviews", "At least 50 reviews", fix, "high")

    # 11. Recent reviews
    fix = "Keep new reviews coming every month: Google favours active profiles."
    latest = None
    for rv in place.get("reviews") or []:
        t = rv.get("publishTime")
        if t:
            try:
                d = datetime.fromisoformat(t.replace("Z", "+00:00"))
                latest = d if latest is None or d > latest else latest
            except ValueError:
                pass
    if latest:
        days = (datetime.now(timezone.utc) - latest).days
        add("recent_reviews", "A review in the last 2 months", "pass" if days <= 60 else ("warn" if days <= 180 else "fail"),
            f"Newest review Google returned: {days} days ago.", fix, "medium")
    else:
        na("recent_reviews", "A review in the last 2 months", fix, "medium")

    # 12. Photos
    fix = "Upload 10-20+ real photos (outside, inside, team, work done) and add new ones every month."
    if api:
        photos = len(place.get("photos") or [])
        add("photos", "10 or more photos", "pass" if photos >= 10 else ("warn" if photos else "fail"),
            f"{photos}{'+' if photos >= 10 else ''} photos on the profile.", fix, "medium")
    else:
        na("photos", "10 or more photos", fix, "medium")

    # 13. Description / summary
    fix = "Write the business description (up to 750 characters) with your main services and the city."
    if api:
        summ = (place.get("editorialSummary") or {}).get("text", "")
        add("summary", "Business summary shown", "pass" if summ else "warn",
            f"Google shows: \"{summ[:140]}\"." if summ else "Google shows no summary for this business.", fix, "low")
    else:
        na("summary", "Business summary shown", fix, "low")

    # 14. Booking / order / quote button
    fix = "Add a booking, appointment, quote or order link to the profile so customers can act straight from Google."
    acts = place.get("actions")
    if acts is not None:
        add("actions", "Booking or order button", "pass" if acts else "warn",
            f"Shown: {', '.join(acts[:3])}." if acts else "No booking, appointment or order button on the listing.",
            fix, "low")
    else:
        na("actions", "Booking or order button", fix, "low", "Could not be read from Google Maps.")
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
CSS = """
:root{--ink:#14172B;--muted:#667085;--line:#E4E7EC;--bg:#F6F7FB;--blue:#2F5BFF;--navy:#141A4D;--mint:#12B76A}
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',Inter,Arial,sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}
.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 28px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.top b{margin-right:auto}.btn{border:1px solid #D0D5DD;background:#fff;color:var(--ink);border-radius:8px;padding:7px 12px;font-weight:600;text-decoration:none;font-size:13px;cursor:pointer}
.btn.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
.wrap{max-width:1040px;margin:0 auto;padding:28px}
.cover{background:linear-gradient(135deg,var(--blue),var(--navy));color:#fff;border-radius:18px;padding:34px 36px;display:flex;gap:28px;align-items:center}
.cover h1{margin:0 0 6px;font-size:30px}.cover p{margin:2px 0;opacity:.9}
.big{width:130px;height:130px;border-radius:50%;background:conic-gradient(#3EE6A8 calc(var(--p)*1%),rgba(255,255,255,.18) 0);display:grid;place-items:center;flex:none}
.big span{width:104px;height:104px;border-radius:50%;background:var(--navy);display:grid;place-items:center;font-size:34px;font-weight:800}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:18px 0}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px}
.gauge{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;text-align:center}
.ring{width:84px;height:84px;margin:0 auto 8px;border-radius:50%;background:conic-gradient(var(--c) calc(var(--p)*1%),#EEF0F4 0);display:grid;place-items:center}
.ring span{width:66px;height:66px;border-radius:50%;background:#fff;display:grid;place-items:center;font-size:22px;font-weight:800}
.g-label{font-weight:700}.g-sub{color:var(--muted);font-size:12px}
section{margin-top:34px;page-break-before:always}section:first-of-type{page-break-before:auto}
h2{font-size:22px;margin:0 0 4px}.lead{color:var(--muted);margin:0 0 14px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}th{background:#F9FAFB;color:#475467}
.issue{background:#fff;border:1px solid var(--line);border-left:5px solid #98A2B3;border-radius:12px;padding:14px 16px;margin:10px 0;page-break-inside:avoid}
.sev-critical{border-left-color:#D92D20}.sev-high{border-left-color:#F04438}.sev-medium{border-left-color:#F79009}.sev-low{border-left-color:#2E90FA}
.issue-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.issue h4{margin:0;font-size:15px}
.badge{font-size:11px;font-weight:700;border-radius:20px;padding:2px 9px;color:#fff}.b-critical{background:#D92D20}.b-high{background:#F04438}.b-medium{background:#F79009}.b-low{background:#2E90FA}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:20px;padding:1px 8px}
.issue p{margin:8px 0 0;font-size:13.5px}.sol{margin-top:8px;background:#F0FDF7;border:1px solid #ABEFC6;border-radius:10px;padding:8px 12px;font-size:13.5px}
.sol ul{margin:4px 0 0 18px;padding:0}.where{margin-top:6px;font-size:12px;color:var(--muted);word-break:break-all}.where span{margin-right:6px}
.passed{margin-top:12px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 14px}.passed ul{columns:2;margin:8px 0 0 0;padding-left:0;list-style:none;color:#067647;font-size:13px}
.note{font-size:12px;color:var(--muted)}.ok{color:#067647;font-weight:700}.bad{color:#D92D20;font-weight:700}
.empty{background:#fff;border:1px dashed var(--line);border-radius:12px;padding:16px;color:var(--muted)}
@media print{.top{display:none}body{background:#fff}.wrap{padding:0}.card,.gauge,.issue,table{break-inside:avoid}details{display:block}details>*{display:block}}
"""


def build(result, downloads=(), gbp=None, gbp_note="no_key"):
    """The full report as one HTML page. ``downloads``: [(label, file name)]."""
    domain = result.get("domain") or ""
    sc = result.get("score") or {}
    score = sc.get("score")
    checks = result.get("checks") or []
    problems_all = [c for c in checks if c.get("status") in ("fail", "warn")]
    crit = sum(1 for c in problems_all if c.get("severity") in ("critical", "high"))
    psi = result.get("psi") or {}
    mob, desk = psi.get("mobile") or {}, psi.get("desktop") or {}
    mob_perf = (mob.get("scores") or {}).get("performance") if mob.get("ok") else None
    desk_perf = (desk.get("scores") or {}).get("performance") if desk.get("ok") else None
    dl = "".join(f'<a class="btn" href="{E(f)}" download>⬇ {E(lbl)}</a>' for lbl, f in downloads)
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full SEO Audit — {E(domain)}</title><style>{CSS}</style></head><body>
<div class="top"><b>Full SEO Audit · {E(domain)}</b>{dl}<button class="btn primary" onclick="window.print()">Print / Save as PDF</button></div>
<div class="wrap">
<div class="cover"><div class="big" style="--p:{score or 0}"><span>{E(str(score if score is not None else '—'))}</span></div>
<div><h1>{E(domain)}</h1><p>Overall SEO health: <b>{E(str(sc.get('label') or ''))}</b> (grade {E(str(sc.get('grade') or '—'))})</p>
<p>{len(problems_all)} problems found · {crit} urgent · {result.get('pages_crawled') or 0} pages checked · {E(result.get('date') or '')}</p></div></div>
<div class="grid">{_gauge(mob_perf, 'Mobile speed', 'Google PageSpeed')}{_gauge(desk_perf, 'Desktop speed', 'Google PageSpeed')}
{_gauge(_dimension(result, 'technical'), 'Technical SEO')}{_gauge(_dimension(result, 'onpage'), 'On-page SEO')}
{_gauge(_dimension(result, 'content'), 'Content')}{_gauge(_dimension(result, 'security'), 'Security')}
{_gauge(_dimension(result, 'local'), 'Local')}{_gauge(_dimension(result, 'ux'), 'Design & UX')}</div>
<div class="card"><b>Summary</b><p style="margin:6px 0 0">{E(result.get('exec_summary') or '')}</p></div>"""]

    # Website speed
    rows = []
    for name, data in (("Mobile", mob), ("Desktop", desk)):
        lab = data.get("lab") or {}
        if data.get("ok"):
            rows.append(f"<tr><td><b>{name}</b></td><td>{(data.get('scores') or {}).get('performance', '—')}</td>"
                        f"<td>{_ms(lab.get('lcp_ms'))}</td><td>{_ms(lab.get('fcp_ms'))}</td><td>{_ms(lab.get('tbt_ms'))}</td>"
                        f"<td>{lab.get('cls') if lab.get('cls') is None else round(lab['cls'], 3)}</td><td>{_ms(lab.get('speed_index_ms'))}</td></tr>")
        else:
            rows.append(f"<tr><td><b>{name}</b></td><td colspan='6' class='note'>PageSpeed data not available "
                        f"({E((data.get('error') or 'no answer from Google')[:120])}). Add a Google API key in the tool for reliable results.</td></tr>")
    speed_table = ("<table><tr><th>Device</th><th>Score</th><th>Largest content (LCP)</th><th>First content (FCP)</th>"
                   "<th>Blocking time (TBT)</th><th>Layout shift (CLS)</th><th>Speed index</th></tr>" + "".join(rows) + "</table>"
                   "<p class='note'>Good targets: LCP under 2.5 s, TBT under 200 ms, CLS under 0.1. Slow sites lose visitors and rankings.</p>")
    opps = (mob.get("top_opportunities") or [])[:6]
    opp_html = ""
    if opps:
        opp_html = "<div class='card'><b>Biggest speed opportunities (Google)</b><ul>" + "".join(
            f"<li>{E(str(o.get('title') or o.get('id') or o))}" + (f" — saves about {_ms(o.get('savings_ms'))}" if isinstance(o, dict) and o.get('savings_ms') else "")
            + "</li>" for o in opps) + "</ul></div>"

    for key, title, cats in SECTIONS:
        sec_checks, problems = _section_checks(result, cats)
        leads = {"speed": "How fast the website loads for visitors and for Google.",
                 "seo": "Everything search engines read: technical setup, page titles, headings, content and links.",
                 "design": "How easy and pleasant the site is to use on phones and computers, for everyone.",
                 "local": "Signals that decide whether the business shows up in Google Maps and the local 3-pack.",
                 "security": "Protection of the site and its visitors (HTTPS, certificates, security headers)."}[key]
        body = [f'<section id="{key}"><h2>{E(title)}</h2><p class="lead">{leads}</p>']
        if key == "speed":
            body.append(speed_table + opp_html)
        if key == "security":
            ssl = result.get("ssl_cert") or {}
            body.append(f"<div class='card'><b>SSL certificate:</b> "
                        + (f"<span class='ok'>valid</span> · issued by {E(str(ssl.get('issuer') or '—'))} · expires {E(str(ssl.get('not_after') or '—'))} "
                           f"({ssl.get('days_left')} days left) · {E(str(ssl.get('protocol') or ''))}" if ssl.get("valid")
                           else f"<span class='bad'>problem</span> · {E(str(ssl.get('error') or 'not valid'))}") + "</div>")
        if key == "seo":
            auth, traffic = result.get("authority") or {}, result.get("traffic") or {}
            sm = result.get("sitemap") or {}
            sm_count = sm.get("count", "—")
            if sm.get("capped"):
                sm_count = f"{sm_count}+"
            body.append("<div class='grid'>"
                        + f"<div class='card'><b>Domain authority</b><div style='font-size:24px;font-weight:800'>{E(_measured(auth.get('domain_authority')))}</div><span class='note'>{E(_measured_note(auth))}</span></div>"
                        + f"<div class='card'><b>Monthly visits</b><div style='font-size:24px;font-weight:800'>{E(_measured(traffic.get('monthly_visits')))}</div><span class='note'>{E(_measured_note(traffic))}</span></div>"
                        + f"<div class='card'><b>Ranking keywords</b><div style='font-size:24px;font-weight:800'>{E(_measured(traffic.get('ranking_keywords')))}</div><span class='note'>{E(_measured_note(traffic))}</span></div>"
                        + f"<div class='card'><b>Pages in sitemap</b><div style='font-size:24px;font-weight:800'>{E(str(sm_count))}</div></div></div>")
        if problems:
            groups = {}
            for c in problems:
                groups.setdefault(c.get("category"), []).append(c)
            for cat in cats:
                if cat in groups:
                    if len(cats) > 1:
                        body.append(f"<h3>{E(CATEGORY_LABEL.get(cat, cat))}</h3>")
                    body += [_issue_card(c) for c in groups[cat]]
        else:
            body.append("<div class='empty'>No problems found in this area.</div>")
        body.append(_passed_list(sec_checks))
        if key == "local":
            body.append(_gbp_section(gbp, gbp_note, domain, result.get("gbp_hints")))
        body.append("</section>")
        parts.append("".join(body))

    parts.append(_competitors(result))
    # Action plan
    plan = sorted(problems_all, key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
    rows = "".join(f"<tr><td>{i}</td><td><span class='badge b-{E(c.get('severity') or 'low')}'>{SEV_LABEL.get(c.get('severity'), '')}</span></td>"
                   f"<td><b>{E(c.get('label') or '')}</b><br><span class='note'>{E(CATEGORY_LABEL.get(c.get('category'), ''))}</span></td>"
                   f"<td>{E(_solutions(c)[0])}</td></tr>" for i, c in enumerate(plan, 1))
    parts.append("<section id='plan'><h2>Action plan</h2><p class='lead'>Every problem in order of importance, with the first step to fix it. "
                 "Fix the Critical and High items first.</p>"
                 + (f"<table><tr><th>#</th><th>Priority</th><th>Problem</th><th>First step</th></tr>{rows}</table>" if plan
                    else "<div class='empty'>Nothing to fix.</div>")
                 + f"<p class='note'>Generated {datetime.now():%d %b %Y %H:%M} by SEO Audit Tool · Authority and traffic figures are shown "
                   "only when measured by a data provider.</p></section></div></body></html>")
    return "".join(parts)


def _measured(v):
    """A real measured value, or the honest 'Not measured' when it is None."""
    if v is None or v == "":
        return "Not measured"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return "{:,}".format(int(v)) if float(v).is_integer() else str(v)
    return str(v)


def _measured_note(d):
    src = (d or {}).get("source")
    if not src or src == "not_measured":
        return "no data source"
    return str(src)


def _gbp_section(place, note, domain, hints=None):
    head = "<h3>Google Business Profile</h3>"
    if place:
        name = (place.get("displayName") or {}).get("text", "")
        link = place.get("googleMapsUri")
        info = (f"<div class='card'><b>{E(name)}</b><br>{E(place.get('formattedAddress') or '')}"
                + (f"<br><a href='{E(link)}'>Open on Google Maps</a>" if link else "")
                + ("<br><span class='note'>Matched by the phone number on the website.</span>" if note == "matched_phone" else "")
                + "</div>")
        checks = gbp_checks(place, domain, hints)
        problems = [c for c in checks if c["status"] in ("fail", "warn")]
        good = [c for c in checks if c["status"] == "pass"]
        cards = "".join(_issue_card(c) for c in problems) or "<div class='empty'>The profile looks complete.</div>"
        passed = ("<details class='passed'><summary>" + f"{len(good)} profile checks passed</summary><ul>"
                  + "".join(f"<li>✓ {E(c['label'])}: {E(c['detail'])}</li>" for c in good) + "</ul></details>") if good else ""
        return head + info + cards + passed
    why = {"no_key": "Add a Google API key (with the Places API (New) turned on) in the tool to check the profile automatically.",
           "not_found": "No Google Business Profile was found for this business. Create and verify one at business.google.com.",
           "unreachable": "Google could not be reached during the audit."}.get(note, f"Google returned an error: {note}")
    checklist = ["Profile claimed and verified", "Exact business name, address and phone (same as the website)",
                 "Most specific primary category + secondary categories", "Website link and booking/appointment link",
                 "Opening hours and holiday hours", "10–20+ real photos, new photos every month",
                 "Description with main services and city", "Services/products listed with short descriptions",
                 "Steady new reviews every month, every review answered", "Weekly Google posts (offers, news, work done)"]
    return (head + f"<div class='card'><b>Automatic check not done.</b> {E(why)}</div>"
            "<div class='card'><b>Checklist to verify by hand</b><ul>" + "".join(f"<li>☐ {E(x)}</li>" for x in checklist) + "</ul></div>")


def _competitors(result):
    comps = [c for c in result.get("competitors") or [] if not c.get("error")]
    head = "<section id='competitors'><h2>Competitors</h2><p class='lead'>Side-by-side check of the competitors you entered (home page of each).</p>"
    if not comps:
        return head + ("<div class='empty'>No competitors were entered. In the tool, write them after the website on the same line, "
                       "for example: <b>mysite.com, competitor1.com, competitor2.com</b>.</div></section>")
    me = {"domain": result.get("domain"), "score": (result.get("score") or {}).get("score"),
          "perf_mobile": ((result.get("psi") or {}).get("mobile") or {}).get("scores", {}).get("performance"),
          "technical": _dimension(result, "technical"), "onpage": _dimension(result, "onpage"),
          "content_score": _dimension(result, "content"), "https": (result.get("final_url") or "").startswith("https"),
          "word_count": (result.get("home_facts") or {}).get("word_count"), "sd": None}
    rows = [("SEO score", "score"), ("Mobile speed", "perf_mobile"), ("Technical", "technical"), ("On-page", "onpage"),
            ("Content", "content_score"), ("HTTPS", "https"), ("Words on home page", "word_count"), ("Structured data", "sd")]
    cols = [me] + comps

    def cell(v):
        if v is True:
            return "<span class='ok'>Yes</span>"
        if v is False:
            return "<span class='bad'>No</span>"
        return E(str(v)) if v is not None else "—"
    table = ("<table><tr><th></th>" + "".join(f"<th>{E(str(c.get('domain')))}{' (you)' if i == 0 else ''}</th>" for i, c in enumerate(cols))
             + "</tr>" + "".join("<tr><td><b>" + E(lbl) + "</b></td>" + "".join(f"<td>{cell(c.get(k))}</td>" for c in cols) + "</tr>"
                                 for lbl, k in rows) + "</table>")
    ahead = [c["domain"] for c in comps if (c.get("score") or 0) > (me["score"] or 0)]
    note = (f"<p class='note'>{', '.join(E(a) for a in ahead)} score higher overall: close the gaps above, starting with speed and content.</p>"
            if ahead else "<p class='note'>You score higher than the competitors entered: keep the lead with fresh content and reviews.</p>")
    return head + table + note + "</section>"


# ---------------------------------------------------------------------------
# PDF from the HTML (Microsoft Edge or Chrome, headless)
# ---------------------------------------------------------------------------
def _browser():
    for name in ("msedge", "chrome", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.isfile(p):
            return p
    return None


def html_to_pdf(html_path, pdf_path, timeout=120):
    exe = _browser()
    if not exe:
        return False
    import tempfile
    profile = tempfile.mkdtemp(prefix="seo-pdf-")
    abs_pdf = os.path.abspath(pdf_path)
    abs_html = os.path.abspath(html_path)
    try:
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                        f"--user-data-dir={profile}", f"--print-to-pdf={abs_pdf}",
                        "file:///" + abs_html.replace("\\", "/")],
                       capture_output=True, timeout=timeout, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 1000


# ---------------------------------------------------------------------------
# Client report: short, plain language, for the business owner
# ---------------------------------------------------------------------------
CLIENT_AREAS = [
    ("errors", "Errors on your website", ("technical", "onpage", "content", "performance", "offpage"),
     "Google can't fully read or trust these pages, so they show up less often in search — and slow or broken "
     "pages send visitors away before they call."),
    ("design", "Look, feel & mobile", ("ux", "mobile", "accessibility"),
     "Most visitors come on a phone. When text is hard to read, buttons are hard to tap or the next step "
     "isn't obvious, they leave and contact someone else."),
    ("local", "Google Maps & local search", ("local",),
     "When people nearby search for what you do, Google shows three businesses on the map first. Missing local "
     "signals mean those customers see your competitors instead."),
    ("security", "Security", ("security",),
     "Weak security settings make the site easier to attack and can trigger browser warnings that scare visitors off."),
]


def _plain_now(c):
    detail = (c.get("detail") or "").strip().rstrip(".")
    why = (c.get("why") or c.get("impact_text") or "").strip()
    return f"{detail}. {why}".strip() if why else f"{detail}."


def _client_card(c):
    sev = c.get("severity") or "low"
    fix = _solutions(c)[0]
    return (f'<div class="issue sev-{E(sev)}"><div class="issue-head"><span class="badge b-{E(sev)}">'
            f'{"Urgent" if sev in ("critical", "high") else "Should fix"}</span><h4>{E(c.get("label") or "")}</h4></div>'
            f'<p><b>What is happening now:</b> {E(_plain_now(c))}</p>'
            f'<div class="sol"><b>How we fix it:</b> {E(fix)}</div></div>')


def build_client(result, downloads=(), gbp=None, gbp_note="no_key", per_area=4):
    """A short, convincing report for the business owner: what is wrong, what it is
    costing them right now, and the fix in one line. No prices."""
    domain = result.get("domain") or ""
    sc = result.get("score") or {}
    score = sc.get("score")
    checks = result.get("checks") or []
    psi = (result.get("psi") or {}).get("mobile") or {}
    lcp = (psi.get("lab") or {}).get("lcp_ms") if psi.get("ok") else None
    mob = (psi.get("scores") or {}).get("performance") if psi.get("ok") else None
    problems = [c for c in checks if c.get("status") in ("fail", "warn")]
    urgent = [c for c in problems if c.get("severity") in ("critical", "high")]
    gbp_issues = [c for c in gbp_checks(gbp, domain, result.get("gbp_hints")) if c["status"] in ("fail", "warn")] if gbp else []
    dl = "".join(f'<a class="btn" href="{E(f)}" download>⬇ {E(lbl)}</a>' for lbl, f in downloads)
    speed_line = (f"Your home page takes about <b>{_ms(lcp)}</b> to show its main content on a phone "
                  f"(Google's target is under 2.5 s). Mobile speed score: <b>{mob}/100</b>."
                  if lcp is not None else "Google's speed test did not answer during the audit.")
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Website Health Report — {E(domain)}</title><style>{CSS}
.hero-list li{{margin:4px 0}}.cta{{background:#fff;border:2px solid var(--blue);border-radius:14px;padding:18px 20px;margin-top:22px}}</style></head><body>
<div class="top"><b>Website Health Report · {E(domain)}</b>{dl}<button class="btn primary" onclick="window.print()">Print / Save as PDF</button></div>
<div class="wrap">
<div class="cover"><div class="big" style="--p:{score or 0}"><span>{E(str(score if score is not None else '—'))}</span></div>
<div><h1>{E(domain)}</h1><p>We checked {result.get('pages_crawled') or 0} pages of your website like Google does.</p>
<p><b>{len(problems)} problems</b> found · <b>{len(urgent)} urgent</b>{f' · <b>{len(gbp_issues)}</b> on your Google profile' if gbp else ''}</p></div></div>
<div class="card" style="margin-top:16px"><b>What this means for your business right now</b><ul class="hero-list">
<li>{speed_line}</li>
<li>{len(urgent)} urgent problems are stopping Google from showing your pages as often as it could.</li>
<li>Every problem below has a clear fix — most can be done in days, not months.</li></ul></div>"""]
    for key, title, cats, why in CLIENT_AREAS:
        items = sorted([c for c in problems if c.get("category") in cats],
                       key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
        if key == "local" and gbp_issues:
            items = gbp_issues + items
        seen, unique = set(), []
        for c in items:  # one card per plain-language problem
            t = c["label"] if str(c.get("id", "")).startswith("gbp_") else plain(c)[0]
            if t not in seen:
                seen.add(t)
                unique.append(c)
        items = unique
        if not items:
            continue
        more = len(items) - per_area
        parts.append(f"<section style='page-break-before:auto'><h2>{E(title)}</h2><p class='lead'>{E(why)}</p>"
                     + "".join(_client_card(c) for c in items[:per_area])
                     + (f"<p class='note'>+ {more} more in this area (see the full audit).</p>" if more > 0 else "")
                     + "</section>")
    if not gbp:
        parts.append("<section style='page-break-before:auto'><h2>Your Google Business Profile</h2>"
                     "<div class='card'>Your Google Business Profile is the first thing local customers see. We will check its "
                     "photos, reviews, hours, category and description and complete everything that is missing.</div></section>")
    parts.append("<div class='cta'><b>Next step</b><p style='margin:6px 0 0'>We fix the urgent items first, then the rest, and "
                 "re-run this audit so you can see the score go up. Ask us for the full technical audit for every detail.</p></div>"
                 f"<p class='note'>Report generated {datetime.now():%d %b %Y}.</p></div></body></html>")
    return "".join(parts)


# Plain-language titles for the client report: (words in id/label, title, what is happening, how we fix it)
PLAIN = [
    (("lcp",), "Your website loads slowly on phones",
     "The main part of the page takes {secs} to appear. Google wants under 2.5 seconds, and many visitors give up and go back to Google while they wait.",
     "We compress the big images, load the important parts first and speed up the server."),
    (("fcp", "first_contentful", "first contentful"), "Visitors see a blank screen first",
     "Nothing shows for {secs} after someone opens your site. That first moment decides if they stay.",
     "We make the first view load instantly by loading the essentials first."),
    (("cls", "layout_shift"), "The page jumps while loading",
     "Text and buttons move as the page loads, so people tap the wrong thing.",
     "We reserve space for images and banners so nothing moves."),
    (("tbt", "tti", "interactive"), "The site freezes for a moment",
     "Heavy scripts keep the page from responding to taps and clicks right away.",
     "We remove or delay scripts that are not needed."),
    (("render_block",), "Code is holding the page back",
     "Some scripts must finish before the page can even start showing.",
     "We load those scripts later, after the visible page is ready."),
    (("unused",), "The site loads code it never uses",
     "Visitors download extra code on every page, which slows the site down.",
     "We strip the unused code from the theme and plugins."),
    (("orphan", "internal_link"), "Some pages are hard to find",
     "A few pages have almost no links pointing to them, so Google and visitors rarely reach them.",
     "We link them from related pages and menus."),
    (("author", "e_e_a_t", "eeat"), "No expert or author is shown",
     "Google ranks content higher when it can see who wrote it and why they are trusted.",
     "We add author names, credentials and an About section."),
    (("readab",), "Text is hard to read",
     "Long, complex sentences make visitors skim and leave.",
     "We rewrite key pages in short, clear sentences."),
    (("schema", "structured"), "Google is missing key business details",
     "Your site does not give Google structured details (business type, address, hours), so you miss richer search results.",
     "We add the business details in the format Google reads, on every key page."),
    (("gbp", "maps"), "Your Google Maps profile is not connected",
     "Your website and Google Maps listing are not clearly linked, which weakens local rankings.",
     "We connect the profile and add a map to the contact page."),
    (("title",), "Page titles need work", "The titles shown in Google are missing, too long or not convincing.",
     "We write clear titles with your main service and city."),
    (("meta", "description"), "Search previews do not sell", "The text under your link in Google is missing or weak, so fewer people click.",
     "We write short, persuasive descriptions for every key page."),
    (("h1", "heading"), "Page headings are unclear", "Headings are missing or out of order, so Google and visitors miss what the page is about.",
     "We restructure the headings around your services."),
    (("alt",), "Images are invisible to Google", "Images have no descriptions, so Google cannot use them to rank you.",
     "We add short descriptions to every important image."),
    (("ssl", "https", "certificate"), "Security certificate problem", "Visitors may see a Not secure warning in their browser.",
     "We install and auto-renew a valid certificate."),
    (("hsts", "csp", "frame", "x_content", "referrer", "header"), "Missing security protections",
     "The site is missing standard protections that stop common attacks and data theft.",
     "We switch on the standard security settings on the server."),
    (("cookie",), "Cookies are not protected", "Visitor sessions could be read or misused on unsafe networks.",
     "We secure the cookie settings."),
    (("review",), "Not enough customer reviews", "Reviews are one of the biggest reasons people choose a local business.",
     "We set up a simple way to ask every happy customer for a Google review."),
    (("nap", "phone", "address", "citation"), "Business details are inconsistent",
     "Your name, address or phone are missing or different across the web, which confuses Google.",
     "We make them identical on the website and every directory."),
    (("contrast", "aria", "accessible", "label", "a11y", "lighthouse"), "Parts of the site are hard to use",
     "Some buttons, links or text are hard to read or use for visitors, and Google checks this too.",
     "We fix the page code so every button, link and heading is clear."),
    (("viewport", "mobile", "tap", "font"), "Hard to use on phones", "Text is small or buttons are too close together on phones.",
     "We make every page comfortable to use on a phone."),
    (("sitemap", "robots", "canonical", "index", "crawl"), "Google cannot index every page",
     "Settings that guide Google through your site are missing or wrong, so some pages may not show in search.",
     "We fix the sitemap and indexing settings and submit them to Google."),
    (("broken", "404"), "Broken links", "Some links lead to pages that do not exist, which frustrates visitors and wastes Google's time.",
     "We repair or redirect every broken link."),
]


def plain(c):
    """(title, what is happening, how we fix it) in plain words for a business owner."""
    key = f"{c.get('id', '')} {c.get('label', '')} {c.get('detail', '')}".lower().replace("-", "_")
    secs = "a few seconds"
    m = re.search(r"(\d{3,6})\s*ms", c.get("detail") or "")
    if m:
        secs = f"{int(m.group(1)) / 1000:.1f} seconds"
    for needles, title, now, fix in PLAIN:
        if any(n in key for n in needles):
            return title, now.format(secs=secs), fix
    return (c.get("label") or "Problem found", _plain_now(c), _solutions(c)[0])


def _client_card(c):  # noqa: F811 - plain-language version
    sev = c.get("severity") or "low"
    title, now, fix = plain(c) if not str(c.get("id", "")).startswith("gbp_") else (c["label"], c["detail"], c["fix"])
    return (f'<div class="issue sev-{E(sev)}"><div class="issue-head"><span class="badge b-{E(sev)}">'
            f'{"Urgent" if sev in ("critical", "high") else "Should fix"}</span><h4>{E(title)}</h4></div>'
            f'<p><b>What is happening now:</b> {E(now)}</p><div class="sol"><b>How we fix it:</b> {E(fix)}</div></div>')
