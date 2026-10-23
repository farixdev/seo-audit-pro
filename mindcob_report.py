# -*- coding: utf-8 -*-
"""
Brighton Analytics Website SEO & Growth Audit — the client-facing report (HTML, printed to
PDF with Edge/Chrome). One deliverable design for the standalone SEO Audit Tool and the
SEO Audit Pro app.

Workflow:
  1 Search visibility & indexability · 2 Technical SEO · 3 On-page SEO ·
  (4 Local SEO & Google Business Profile — ONLY IF PROFILE EXISTS) ·
  5 Performance, mobile UX & security · then the action plan.
Every issue: what we found, why it matters, how we fix it. Inline SVG charts.
Only real audit data; no fake scoring; no fake off-page metrics.
"""

import base64
import math
import os
import re
from datetime import datetime

from full_report import E, SEV_ORDER, _ms, _where, gbp_checks, plain

# Load brand logo SVG from assets/brighnton.svg
_RAW_LOGO = ""
_cand_paths = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "brighnton.svg"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "seo_audit_pro", "assets", "brighnton.svg"),
]
import sys
if hasattr(sys, "_MEIPASS"):
    _cand_paths.extend([
        os.path.join(sys._MEIPASS, "assets", "brighnton.svg"),
        os.path.join(sys._MEIPASS, "seo_audit_pro", "assets", "brighnton.svg"),
    ])
for _p in _cand_paths:
    if os.path.isfile(_p):
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                _RAW_LOGO = _f.read().strip()
            if _RAW_LOGO:
                break
        except Exception:
            pass

# Version for orange cover (pure white monochrome so it pops cleanly against #f07e22)
_COVER_LOGO = _RAW_LOGO.replace('fill="#F07D22"', 'fill="#FFFFFF"') if _RAW_LOGO else ""

# Version for light page headers (icon & Brighton in orange #F07D22, Analytics in #101828)
_HEADER_LOGO = _RAW_LOGO.replace('fill="white"', 'fill="#101828"') if _RAW_LOGO else ""

VIS_IDS = ("robots", "sitemap", "noindex", "canonical", "index", "crawl", "hreflang", "ai_crawler")


def step_of_check(c, gbp_present=False):
    cid = str(c.get("id", "")).lower()
    cat = c.get("category")
    if cid.startswith("gbp_") or cat == "local":
        return 4 if gbp_present else None
    if cat == "technical":
        if any(w in cid for w in VIS_IDS):
            return 1
        return 3 if cid == "url_structure" else 2
    if cat in ("onpage", "content") or "cta" in cid:
        return 3
    if cat == "offpage":
        return None  # Offpage SEO is completely removed
    return 5 if gbp_present else 4


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------
def donut(segments, size=150, stroke=22):
    """segments = [(label, value, color)]; legend beside."""
    total = sum(v for _l, v, _c in segments) or 1
    r = (size - stroke) / 2
    circ = 2 * math.pi * r
    offset, arcs = 0, []
    for _label, v, color in segments:
        if v:
            arcs.append(f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{color}" stroke-width="{stroke}" '
                        f'stroke-dasharray="{circ * v / total:.1f} {circ:.1f}" stroke-dashoffset="{-offset:.1f}" '
                        f'transform="rotate(-90 {size/2} {size/2})"/>')
            offset += circ * v / total
    n = sum(v for _l, v, _c in segments)
    svg = (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
           f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="#F2F4F7" stroke-width="{stroke}"/>{"".join(arcs)}'
           f'<text x="50%" y="46%" text-anchor="middle" font-size="26" font-weight="800" fill="#101828">{n}</text>'
           f'<text x="50%" y="62%" text-anchor="middle" font-size="11" fill="#667085">issues</text></svg>')
    legend = "".join(f'<div class="lg"><span style="background:{c}"></span>{E(l)} <b>{v}</b></div>' for l, v, c in segments)
    return f'<div class="donut">{svg}<div>{legend}</div></div>'


def metric_bar(label, value, target, unit, worse_is_higher=True, cap_factor=2.5):
    """A speed metric against Google's target, drawn to scale."""
    if value is None:
        return f'<div class="mb"><div class="mb-l">{E(label)}</div><div class="mb-v na">not measured</div></div>'
    cap = target * cap_factor
    pct = min(value / cap, 1) * 100
    tpct = target / cap * 100
    ok = value <= target if worse_is_higher else value >= target
    shown = f"{value / 1000:.1f} s" if unit == "ms" and value >= 1000 else (f"{int(value)} ms" if unit == "ms" else f"{value:.2f}")
    tshown = f"{target / 1000:.1f} s" if unit == "ms" and target >= 1000 else (f"{int(target)} ms" if unit == "ms" else f"{target}")
    return (f'<div class="mb"><div class="mb-l">{E(label)}</div><div class="mb-track">'
            f'<div class="mb-fill" style="width:{pct:.0f}%;background:{"#079455" if ok else "#D92D20"}"></div>'
            f'<div class="mb-target" style="left:{tpct:.0f}%"></div></div>'
            f'<div class="mb-v {"ok" if ok else "bad"}">{shown}</div><div class="mb-t">target {tshown}</div></div>')


def hbars_issues(items, width=520):
    """Horizontal bar chart showing issues found per category: items = [(label, count)]."""
    row_h, label_w = 32, 210
    h = row_h * len(items) + 10
    max_val = max([v for _l, v in items] + [1])
    out = [f'<svg width="100%" viewBox="0 0 {width} {h}" class="chart">']
    for i, (label, v) in enumerate(items):
        y = 6 + i * row_h
        bw = (width - label_w - 60) * (v / max_val)
        bar_color = "#079455" if v == 0 else ("#D92D20" if v >= 5 else "#F79009")
        out.append(f'<text x="0" y="{y + 15}" font-size="12" fill="#344054">{E(label)}</text>'
                   f'<rect x="{label_w}" y="{y + 4}" width="{width - label_w - 60}" height="14" rx="7" fill="#F2F4F7"/>'
                   f'<rect x="{label_w}" y="{y + 4}" width="{max(0, bw):.1f}" height="14" rx="7" fill="{bar_color}"/>'
                   f'<text x="{width - 50}" y="{y + 15}" font-size="12" font-weight="700" fill="#101828">'
                   f'{v} issue{"s" if v != 1 else ""}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
CSS = """
@page{size:A4;margin:12mm}
:root{--ink:#101828;--muted:#667085;--line:#EAECF0;--red:#D92D20;--accent:#f07e22}
*{box-sizing:border-box}body{margin:0;font-family:'Inter','Segoe UI',Arial,sans-serif;color:var(--ink);background:#F2F4F7;line-height:1.5;font-size:14px}
.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 24px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top b{margin-right:auto}.btn{border:1px solid #D0D5DD;background:#fff;color:var(--ink);border-radius:8px;padding:7px 12px;font-weight:600;text-decoration:none;font-size:13px;cursor:pointer}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.page{background:#fff;max-width:900px;margin:22px auto;padding:40px 46px;border-radius:6px;box-shadow:0 1px 3px rgba(16,24,40,.08);page-break-after:always}
.ph{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:26px}
.ph .logo-wrap{height:30px;display:flex;align-items:center}
.ph .logo-wrap svg{height:28px;width:auto}
.ph span{font-size:11px;color:#98A2B3}
.eyebrow{color:var(--accent);font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;margin:0 0 6px}
h1{font-size:40px;line-height:1.1;margin:0}h2{font-size:26px;line-height:1.2;margin:0 0 8px}h3{font-size:17px;margin:0 0 8px}
.lead{color:#475467;margin:0 0 20px;font-size:15px}
.cover{background:#f07e22;color:#fff;min-height:1000px;display:flex;flex-direction:column;justify-content:space-between}
.cover .cover-logo-wrap{display:flex;align-items:center}
.cover .cover-logo-wrap svg{height:48px;width:auto}
.cover .meta{opacity:.92}.cover h1{font-size:46px;color:#fff}
.scorebox{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.32);border-radius:16px;padding:24px;display:flex;gap:24px;align-items:center}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 18px}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px}.tile b{display:block;font-size:28px;line-height:1.1}.tile span{font-size:12px;color:var(--muted)}
.rings{display:grid;gap:8px;margin-bottom:18px}
.rings div{border:1px solid var(--line);border-radius:12px;padding:12px 10px;text-align:center;font-size:11px}
.rings em{display:block;font-style:normal;font-weight:600;font-size:10px}
.box{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse}th{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#98A2B3;text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
td{padding:9px 6px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
.pill{display:inline-block;font-size:11px;font-weight:700;border-radius:20px;padding:3px 10px}
.p-ok{background:#ECFDF3;color:#067647}.p-bad{background:#FEF3F2;color:#B42318}.p-warn{background:#FFFAEB;color:#B54708}.p-na{background:#F2F4F7;color:#667085}
.issue{border:1px solid var(--line);border-left:4px solid var(--red);border-radius:10px;padding:14px 16px;margin:12px 0;page-break-inside:avoid}
.issue.medium{border-left-color:#DC6803}.issue.low{border-left-color:#2E90FA}
.ih{display:flex;gap:8px;align-items:center;margin-bottom:8px}.ih h4{margin:0;font-size:15px}
.icols{display:grid;grid-template-columns:1fr 1fr 1.1fr;gap:14px}.lbl{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#98A2B3;font-weight:700;margin-bottom:3px}
.icols p{margin:0;font-size:12.5px;color:#475467}.fixbox{background:#141414;color:#fff;border-radius:8px;padding:10px 12px;font-size:12.5px}
.fixbox .lbl{color:#F79009}.where{font-size:11px;color:#98A2B3;margin-top:4px;word-break:break-all}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{border:1px solid var(--line);border-radius:6px;padding:3px 9px;font-size:12px}
.chip:before{content:'✓ ';color:#079455;font-weight:700}
.sth{display:flex;gap:16px;align-items:center;margin-bottom:8px}
.mock{display:grid;grid-template-columns:3fr 1fr;page-break-inside:avoid;gap:18px;align-items:end}
.browser{border:1px solid #D0D5DD;border-radius:10px;overflow:hidden;box-shadow:0 4px 14px rgba(16,24,40,.1)}
.browser .bar{background:#F2F4F7;padding:6px 10px;font-size:11px;color:#667085}.browser .bar i{display:inline-block;width:8px;height:8px;border-radius:50%;background:#D0D5DD;margin-right:4px}
.browser img{width:100%;display:block}.phone img{width:100%;display:block;max-height:420px;object-fit:cover;object-position:top}.browser img{max-height:460px;object-fit:cover;object-position:top}.phone{border:8px solid #101828;border-radius:22px;overflow:hidden;box-shadow:0 4px 14px rgba(16,24,40,.2)}
.donut{display:flex;gap:16px;align-items:center}.lg{font-size:12px;margin:4px 0;display:flex;gap:6px;align-items:center}.lg span{width:10px;height:10px;border-radius:3px;display:inline-block}
.legend-row{display:flex;gap:14px;flex-wrap:wrap}.chart{display:block}
.mb{display:grid;grid-template-columns:150px 1fr 70px 90px;gap:10px;align-items:center;margin:8px 0;font-size:12.5px}
.mb-track{position:relative;height:12px;background:#F2F4F7;border-radius:6px}.mb-fill{height:12px;border-radius:6px}
.mb-target{position:absolute;top:-4px;width:2px;height:20px;background:#101828}.mb-v{font-weight:700}.mb-v.ok{color:#067647}.mb-v.bad{color:#B42318}.mb-t{color:#98A2B3;font-size:11px}
.checklist{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}.ck{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}
.dot{width:14px;height:14px;border-radius:50%;flex:none;margin-top:3px}.ck b{font-size:13px}.ck div div{font-size:11.5px;color:var(--muted)}
.road{display:grid;grid-template-columns:160px 1fr;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.road .st{color:var(--accent);font-weight:800}.road ul{margin:4px 0 0 18px;padding:0;font-size:13px;color:#344054}
.cta{background:#f07e22;color:#fff;border-radius:16px;padding:28px 30px;margin-top:22px}
.cta h2{color:#fff}.note{font-size:11px;color:#98A2B3}
@media print{
  *{-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important;color-adjust:exact !important}
  body{background:#fff;-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important}
  .top{display:none !important}
  .page{box-shadow:none;margin:0 auto;max-width:none;border-radius:0;padding:20px 24px;page-break-after:always}
  .cover{background:#f07e22 !important;min-height:265mm;padding:30px 34px;border-radius:0}
}
@media screen and (max-width:760px){.page{padding:24px}.icols,.grid2,.mock,.checklist{grid-template-columns:1fr}.rings{grid-template-columns:repeat(2,1fr)}.tiles{grid-template-columns:1fr}}
"""


def _img(path):
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{'image/jpeg' if path.lower().endswith(('.jpg', '.jpeg')) else 'image/png'};base64,{data}"


def _pill(ok, yes="Pass", no="Fix needed", na=False):
    if na:
        return '<span class="pill p-na">Not measured</span>'
    return f'<span class="pill {"p-ok" if ok else "p-bad"}">{yes if ok else no}</span>'


def _issue(c):
    sev = c.get("severity") or "low"
    gbp = str(c.get("id", "")).startswith("gbp_")
    title, now, fix = (c["label"], c["detail"], c["fix"]) if gbp else plain(c)
    found = c.get("detail") or now
    where = _where(c)[:3]
    tech = (c.get("fix") or "").strip()
    fix_text = fix if gbp or not tech or tech.lower() in fix.lower() or "lighthouse" in tech.lower() or tech.lower().startswith("optimise") \
        else f"{fix} ({tech.rstrip('.')})."
    level = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}.get(sev, "Low")
    cls = "" if sev in ("critical", "high") else (" medium" if sev == "medium" else " low")
    pill = "p-bad" if sev in ("critical", "high") else ("p-warn" if sev == "medium" else "p-na")
    return (f'<div class="issue{cls}"><div class="ih"><span class="pill {pill}">{level}</span><h4>{E(title)}</h4></div>'
            f'<div class="icols"><div><div class="lbl">What we found</div><p>{E(found)}</p>'
            + (f'<div class="where">{" · ".join(E(u) for u in where)}</div>' if where else "")
            + f'</div><div><div class="lbl">Why it matters</div><p>{E(now)}</p></div>'
            f'<div class="fixbox"><div class="lbl">How we fix it</div>{E(fix_text)}</div></div></div>')


def _unique(items):
    seen, out = set(), []
    for c in items:
        t = c["label"] if str(c.get("id", "")).startswith("gbp_") else plain(c)[0]
        if t not in seen:
            seen.add(t)
            out.append(c)
    return out


def _header(company_name, label, domain):
    logo_part = f'<div class="logo-wrap">{_HEADER_LOGO}</div>' if _HEADER_LOGO else f'<div style="font-weight:900;letter-spacing:.12em">{E(company_name.upper())}</div>'
    return f'<div class="ph">{logo_part}<span>{E(label)} · {E(domain)}</span></div>'


def build(result, company=None, business=None, downloads=(), gbp=None, gbp_note="no_key", shots=None, per_step=8):
    company = company or {}
    cname = company.get("name") or "Brighton Analytics"
    shots = shots or {}
    domain = result.get("domain") or ""
    client = business or domain

    # Check if GBP is stored in result or needs on-demand lookup
    if gbp is None and "gbp" in result:
        gbp = result.get("gbp")
        gbp_note = result.get("gbp_note") or gbp_note

    checks = [c for c in (result.get("checks") or []) if c.get("category") != "offpage"]
    gbp_all = gbp_checks(gbp, domain) if gbp else []
    all_checks = checks + gbp_all

    # Dynamic steps: Local SEO is ONLY added if GBP profile is found!
    active_steps = [
        ("visibility", "Search visibility & indexability",
         "Can Google find, crawl and index your important pages? If not, nothing else matters — those pages never show up."),
        ("technical", "Technical SEO",
         "Server errors, broken links, redirects and sitemap problems that block Google and send visitors to dead ends."),
        ("onpage", "On-page SEO",
         "Content, titles, descriptions, headings, internal links, URLs, images and calls to action — what decides your rankings and clicks."),
    ]
    if gbp:
        active_steps.append(
            ("local", "Local SEO & Google Business Profile",
             "Whether nearby customers find you on Google Maps and in the local 3-pack before your competitors.")
        )
    active_steps.append(
        ("experience", "Performance, mobile UX & security",
         "Speed, phone experience and safety — what visitors feel the moment they land on your site.")
    )
    steps = [(i + 1, k, t, d) for i, (k, t, d) in enumerate(active_steps)]

    by_step = {n: [c for c in all_checks if step_of_check(c, bool(gbp)) == n] for n, *_ in steps}
    problems = sorted([c for c in all_checks if c.get("status") in ("fail", "warn") and step_of_check(c, bool(gbp)) is not None],
                      key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
    urgent = [c for c in problems if c.get("severity") in ("critical", "high")]
    passed = [c for c in all_checks if c.get("status") == "pass" and step_of_check(c, bool(gbp)) is not None]
    rated = [c for c in all_checks if c.get("status") in ("pass", "warn", "fail") and step_of_check(c, bool(gbp)) is not None]

    psi = result.get("psi") or {}
    mob, desk = psi.get("mobile") or {}, psi.get("desktop") or {}
    mlab = (mob.get("lab") or {}) if mob.get("ok") else {}
    home = result.get("home_facts") or {}
    date = datetime.now().strftime("%B %d, %Y")
    dl = "".join(f'<a class="btn" href="{E(f)}" download>⬇ {E(lbl)}</a>' for lbl, f in downloads)
    contact = " · ".join(E(x) for x in (company.get("website"), company.get("email"), company.get("phone")) if x)

    p = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(cname)} — Website SEO & Growth Audit — {E(domain)}</title><style>{CSS}</style></head><body>
<div class="top"><b>{E(cname)} · Website SEO &amp; Growth Audit · {E(domain)}</b>{dl}<button class="btn primary" onclick="window.print()">Print / Save as PDF</button></div>"""]

    # Cover page (Background #f07e22)
    cover_logo_html = f'<div class="cover-logo-wrap">{_COVER_LOGO}</div>' if _COVER_LOGO else f'<div style="font-size:24px;font-weight:900">{E(cname.upper())}</div>'
    p.append(f"""<section class="page cover">
<div>{cover_logo_html}</div>
<div><div class="meta">{E(cname)} · Comprehensive Website Audit</div><h1>Website SEO &amp;<br>Growth Report</h1>
<p class="meta" style="font-size:18px;margin-top:18px">Prepared for <b>{E(client)}</b><br>{E(domain)}</p></div>
<div>
<div class="scorebox">
<div style="flex:1">
<div class="meta" style="font-size:11px;letter-spacing:.14em;font-weight:700">AUDIT DELIVERABLE &amp; ACTION PLAN</div>
<div style="font-size:24px;font-weight:800;margin:6px 0 10px">{len(problems)} issues identified · {len(urgent)} priority fixes</div>
<div style="font-size:13.5px;opacity:.9;line-height:1.4">A rigorous, multi-point evaluation across search visibility, technical stability, on-page optimization, {("local GBP presence, " if gbp else "")}speed, phone experience, and site security.</div>
</div>
</div>
<div style="display:flex;justify-content:space-between;margin-top:22px" class="meta"><span>{date}</span><span>{E(cname)}{(' · ' + contact) if contact else ''}</span></div>
</div></section>""")

    # Executive summary
    sev_counts = [("Critical", sum(1 for c in problems if c.get("severity") == "critical"), "#B42318"),
                  ("High", sum(1 for c in problems if c.get("severity") == "high"), "#F04438"),
                  ("Medium", sum(1 for c in problems if c.get("severity") == "medium"), "#F79009"),
                  ("Low", sum(1 for c in problems if c.get("severity") == "low"), "#2E90FA")]

    step_cards = []
    for n, _k, t, _d in steps:
        items_n = [c for c in problems if step_of_check(c, bool(gbp)) == n]
        urg_n = [c for c in items_n if c.get("severity") in ("critical", "high")]
        passed_n = [c for c in by_step[n] if c.get("status") == "pass"]
        status_color = "#D92D20" if urg_n else ("#F79009" if items_n else "#079455")
        step_cards.append(
            f"<div><b style='font-size:20px;color:{status_color}'>{len(items_n)}</b>"
            f"<div style='margin-top:3px;font-weight:700'>{n}. {E(t.split(' &')[0].split(' (')[0])}</div>"
            f"<em style='color:#667085'>{len(passed_n)} passed</em></div>"
        )
    step_cards_html = "".join(step_cards)

    def meas(label, value, target, ok, shown=None):
        na = value is None
        return (f"<tr><td>{E(label)}</td><td><b>{'—' if na else E(shown or str(value))}</b></td><td>{E(target)}</td>"
                f"<td>{_pill(ok, na=na)}</td></tr>")
    imgs, alts = home.get("images_total") or 0, home.get("images_with_alt") or 0
    rows = [meas("Main content visible on phones (LCP)", mlab.get("lcp_ms"), "under 2.5 s",
                 (mlab.get("lcp_ms") or 0) <= 2500, _ms(mlab.get("lcp_ms"))),
            meas("Layout stability (CLS)", mlab.get("cls"), "under 0.1", (mlab.get("cls") or 0) <= 0.1,
                 f"{mlab['cls']:.2f}" if mlab.get("cls") is not None else None),
            meas("Time the page is frozen (TBT)", mlab.get("tbt_ms"), "under 200 ms", (mlab.get("tbt_ms") or 0) <= 200,
                 _ms(mlab.get("tbt_ms"))),
            meas("Words of content on homepage", home.get("word_count"), "600+", (home.get("word_count") or 0) >= 600),
            meas("Images with descriptions (alt text)", f"{alts}/{imgs}" if imgs else None, "all", alts >= imgs),
            meas("Pages in XML sitemap", (result.get("sitemap") or {}).get("count"), "all key pages",
                 ((result.get("sitemap") or {}).get("count") or 0) > 0),
            meas("Secure connection (HTTPS)", "Yes" if (result.get("final_url") or "").startswith("https") else "No",
                 "Yes", (result.get("final_url") or "").startswith("https"))]

    issues_per_area = [(f"{n}. " + t.split(" &")[0].split(" (")[0], len([c for c in problems if step_of_check(c, bool(gbp)) == n]))
                       for n, _k, t, _d in steps]

    p.append(f"""<section class="page">{_header(cname, "Executive summary", domain)}
<p class="eyebrow">Executive summary</p><h2>How {E(domain)} performs for your customers</h2>
<p class="lead">We analysed {E(client)}'s website the way Google and your customers experience it — on a computer and on a phone —
and evaluated {len(rated)} checks across key areas that decide how easily you are found, how fast the site loads and how many visitors
turn into calls and enquiries.</p>
<div class="tiles">
<div class="tile"><b style="color:#D92D20">{len(urgent)}</b><span>Critical &amp; high-priority issues</span></div>
<div class="tile"><b>{len(problems)}</b><span>Total issues identified</span></div>
<div class="tile"><b style="color:#079455">{len(passed)}/{len(rated)}</b><span>Checks passed</span></div>
</div>
<div class="rings" style="grid-template-columns:repeat({len(steps)},1fr)">{step_cards_html}</div>
<div class="grid2">
<div class="box"><h3>Issues by area</h3>{hbars_issues(issues_per_area)}</div>
<div class="box"><h3>Issues by priority</h3>{donut(sev_counts)}</div>
</div>
<div class="box"><p class="eyebrow">Key measurements</p><table><tr><th>Metric</th><th>Your site</th><th>Target</th><th>Status</th></tr>
{''.join(rows)}</table></div></section>""")

    # What visitors see
    d_img, m_img = _img(shots.get("desktop")), _img(shots.get("mobile"))
    if d_img or m_img:
        p.append(f"""<section class="page">{_header(cname, "First impressions", domain)}<p class="eyebrow">First impressions</p>
<h2>What your visitors see</h2><p class="lead">Visitors decide within a few seconds whether to stay. This is the first screen of your
website on a laptop and on a phone, captured during the audit.</p><div class="mock">
{f'<div class="browser"><div class="bar"><i></i><i></i><i></i> {E(domain)}</div><img src="{d_img}" alt="Desktop"></div>' if d_img else '<div></div>'}
{f'<div class="phone"><img src="{m_img}" alt="Phone"></div>' if m_img else ''}</div></section>""")

    # The active steps
    for n, key, title, desc in steps:
        items = _unique([c for c in problems if step_of_check(c, bool(gbp)) == n])
        good = [c for c in by_step[n] if c.get("status") == "pass"]
        urg_n = [c for c in items if c.get("severity") in ("critical", "high")]

        body = [f"""<section class="page">{_header(cname, f"Step {n} · {title}", domain)}<p class="eyebrow">Step {n} of {len(steps)}</p>
<div class="sth"><div><h2 style="margin:0">{E(title)}</h2>
<div style="margin-top:6px;display:flex;gap:6px">
<span class="pill {'p-bad' if urg_n else 'p-ok'}">{len(urg_n)} critical/high</span>
<span class="pill p-na">{len(items)} issue{'s' if len(items) != 1 else ''} to fix</span>
<span class="pill p-ok">{len(good)} checks verified</span>
</div></div></div><p class="lead">{E(desc)}</p>"""]

        if key == "visibility":
            body.append(_facts_table([
                ("robots.txt found", (result.get("robots") or {}).get("present"), ""),
                ("Whole site blocked in robots.txt", not (result.get("robots") or {}).get("blocks_all"), "Not blocked"),
                ("XML sitemap", ((result.get("sitemap") or {}).get("count") or 0) > 0,
                 f"{(result.get('sitemap') or {}).get('count') or 0} URLs"),
                ("Homepage indexable (no noindex)", not home.get("noindex"), ""),
                ("Canonical tag on homepage", bool(home.get("canonical")), "")]))

        if key == "local" and gbp:
            body.append(_gbp_block(gbp, domain, gbp_all))

        if key == "experience":
            body.append("<div class='box'><h3>Speed on a phone vs Google's targets</h3>"
                        + metric_bar("Main content (LCP)", mlab.get("lcp_ms"), 2500, "ms")
                        + metric_bar("First content (FCP)", mlab.get("fcp_ms"), 1800, "ms")
                        + metric_bar("Frozen time (TBT)", mlab.get("tbt_ms"), 200, "ms")
                        + metric_bar("Layout jumps (CLS)", mlab.get("cls"), 0.1, "")
                        + ("" if mob.get("ok") else "<p class='note'>Google's speed test did not answer during this audit "
                                                    "(an API key gives reliable speed data).</p>") + "</div>")
            body.append(_security_block(result, checks))

        if items:
            body += [_issue(c) for c in items[:per_step]]
            if len(items) > per_step:
                body.append(f"<p class='note'>+ {len(items) - per_step} smaller items in this area are in the detailed audit.</p>")
        else:
            body.append("<div class='box' style='border-color:#ABEFC6;color:#067647'><b>✓ No problems found in this area.</b></div>")
        if good:
            body.append("<p class='eyebrow' style='margin-top:18px'>What's already working</p><div class='chips'>"
                        + "".join(f"<span class='chip'>{E(c.get('label') or '')}</span>" for c in good[:24]) + "</div>")
        body.append("</section>")
        p.append("".join(body))

    # Action Plan / Roadmap
    road = []
    for n, _k, title, _d in steps:
        items = _unique([c for c in problems if step_of_check(c, bool(gbp)) == n])
        if not items:
            continue
        lines = "".join(f"<li><span class='pill {'p-bad' if c.get('severity') in ('critical', 'high') else 'p-warn'}'>"
                        f"{'Urgent' if c.get('severity') in ('critical', 'high') else 'Next'}</span> {E((plain(c) if not str(c.get('id', '')).startswith('gbp_') else (c['label'], '', c['fix']))[2])}</li>"
                        for c in items[:6])
        road.append(f"<div class='road'><div class='st'>Step {n}<br><span style='color:#101828;font-weight:700'>{E(title)}</span></div><ul>{lines}</ul></div>")

    p.append(f"""<section class="page">{_header(cname, "Action plan", domain)}<p class="eyebrow">Action plan</p>
<h2>Your step-by-step plan to more calls and enquiries</h2>
<p class="lead">We address issues systematically: ensure Google can crawl and index your site, resolve technical roadblocks,
improve on-page elements, {("solidify local Google profile signals, " if gbp else "")}and maximize phone speed and security.</p>
{''.join(road) or "<div class='box'>No issues to fix — keep monitoring monthly.</div>"}
<div class="cta"><p class="eyebrow" style="color:#FDE68A">Next step</p><h2>Let's fix this together.</h2>
<p>Book a free 15-minute call and we'll walk you through this report, answer your questions, and tackle the urgent priorities first —
then re-run this audit to verify all fixes have been properly resolved.</p>
<p style="margin:12px 0 0;font-weight:700">{E(cname)}{(' · ' + contact) if contact else ''}</p></div>
<p class="note" style="margin-top:14px">This report was generated from an automated analysis of {E(result.get('final_url') or domain)} on {date}.
Results reflect the pages checked at the time of testing.</p></section></body></html>""")
    return "".join(p)


def _facts_table(rows):
    return ("<div class='box'><table><tr><th>Check</th><th>Status</th><th>Details</th></tr>"
            + "".join(f"<tr><td>{E(a)}</td><td>{_pill(bool(ok), 'OK', 'Problem')}</td><td>{E(d)}</td></tr>" for a, ok, d in rows)
            + "</table></div>")


def _gbp_block(place, domain, gbp_all):
    if not place:
        return ""
    name = (place.get("displayName") or {}).get("text", "") or place.get("name") or ""
    good = sum(1 for c in gbp_all if c["status"] == "pass")
    items = "".join(
        f"<div class='ck'><span class='dot' style='background:{'#101828' if c['status'] == 'pass' else '#FECDCA'}'></span>"
        f"<div><b>{E(c['label'])}</b><div>{E(c['detail'] if c['status'] == 'pass' else c['fix'])}</div></div></div>"
        for c in gbp_all)
    link = place.get("googleMapsUri") or place.get("maps_link")
    rating = place.get("rating")
    reviews = place.get("userRatingCount") or place.get("review_count") or 0
    rating_str = f" · ★ {rating:.1f} ({reviews} reviews)" if rating else ""

    return (f"<div class='box'><div style='display:flex;justify-content:space-between;align-items:center'><div>"
            f"<p class='eyebrow'>Google Business Profile</p><h3>Your Google Maps listing: {good} of {len(gbp_all)} essentials verified</h3>"
            f"<p class='note'><b>{E(name)}</b>{rating_str} · {E(place.get('formattedAddress') or place.get('address') or '')}"
            + (f" · <a href='{E(link)}' target='_blank'>Open on Google Maps</a>" if link else "") + "</p></div>"
            f"<span class='pill p-ok'>{good} / {len(gbp_all)} verified</span></div><div class='checklist'>{items}</div></div>")


def _security_block(result, checks):
    sec = {c["id"]: c for c in checks if c.get("category") == "security"}

    def state(cid):
        c = sec.get(cid) or sec.get("sec_" + cid)
        if not c or c.get("status") in ("na", "info"):
            return '<span class="pill p-na">Not checked</span>'
        return {"pass": '<span class="pill p-ok">Protected</span>', "warn": '<span class="pill p-warn">Weak</span>'}.get(
            c["status"], '<span class="pill p-bad">Missing</span>')
    ssl = result.get("ssl_cert") or {}
    https = (result.get("final_url") or "").startswith("https")
    redir = (result.get("http_redirect") or {}).get("redirects_to_https")
    headers = [("HSTS", "hsts"), ("Content-Security-Policy", "csp"), ("Clickjacking protection", "x_frame_options"),
               ("X-Content-Type-Options", "x_content_type"), ("Referrer-Policy", "referrer_policy"),
               ("Permissions-Policy", "permissions_policy")]
    left = "".join(f"<tr><td>{E(a)}</td><td style='text-align:right'>{state(k)}</td></tr>" for a, k in headers)
    right = (f"<tr><td>HTTPS encryption</td><td style='text-align:right'>{_pill(https, 'On', 'Off')}</td></tr>"
             f"<tr><td>Certificate valid until</td><td style='text-align:right'><b>{E(str(ssl.get('not_after') or '—'))}</b>"
             + (f" <span class='note'>({ssl.get('days_left')} days)</span>" if ssl.get("days_left") is not None else "") + "</td></tr>"
             f"<tr><td>HTTP → HTTPS redirect</td><td style='text-align:right'>{_pill(bool(redir), 'Yes', 'No')}</td></tr>"
             f"<tr><td>Mixed content</td><td style='text-align:right'>{state('mixed_content')}</td></tr>"
             f"<tr><td>Old protocols (TLS 1.0/1.1) off</td><td style='text-align:right'>{state('tls_deprecated_protocols')}</td></tr>"
             f"<tr><td>Email spoofing guard (SPF)</td><td style='text-align:right'>{state('dns_spf')}</td></tr>"
             f"<tr><td>Email spoofing guard (DMARC)</td><td style='text-align:right'>{state('dns_dmarc')}</td></tr>"
             f"<tr><td>Website firewall</td><td style='text-align:right'>{state('waf_protection')}</td></tr>")
    return (f"<div class='grid2'><div class='box'><p class='eyebrow'>Browser protections</p><h3>Security headers</h3><table>{left}</table></div>"
            f"<div class='box'><p class='eyebrow'>Encryption</p><h3>Connection safety</h3><table>{right}</table></div></div>")
