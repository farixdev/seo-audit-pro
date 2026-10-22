# -*- coding: utf-8 -*-
"""
Mindcob Website SEO & Growth Audit — the client-facing report (HTML, printed to
PDF with Edge/Chrome). One design for the standalone SEO Audit Tool and the
SEO Audit Pro app.

Order follows the agreed workflow:
  1 Search visibility & indexability · 2 Technical SEO · 3 On-page SEO ·
  4 Local SEO (Google Business Profile) · 5 Off-page SEO (with competitors) ·
  6 Performance, mobile UX & security · then the action plan.
Every issue: what we found, why it matters, how we fix it. Charts are inline
SVG. Only real audit data; estimates are labelled; no prices.
"""

import base64
import math
import os
from datetime import datetime

from full_report import E, SEV_ORDER, _ms, _where, gbp_checks, plain

STEPS = [
    (1, "visibility", "Search visibility & indexability",
     "Can Google find, crawl and index your important pages? If not, nothing else matters — those pages never show up."),
    (2, "technical", "Technical SEO",
     "Server errors, broken links, redirects and sitemap problems that block Google and send visitors to dead ends."),
    (3, "onpage", "On-page SEO",
     "Content, titles, descriptions, headings, internal links, URLs, images and calls to action — what decides your rankings and clicks."),
    (4, "local", "Local SEO & Google Business Profile",
     "Whether nearby customers find you on Google Maps and in the local 3-pack before your competitors."),
    (5, "offpage", "Off-page SEO & authority",
     "How much Google trusts your website, based on the links and reputation it has across the web."),
    (6, "experience", "Performance, mobile UX & security",
     "Speed, phone experience and safety — what visitors feel the moment they land on your site."),
]
VIS_IDS = ("robots", "sitemap", "noindex", "canonical", "index", "crawl", "hreflang", "ai_crawler")


def step_of(c):
    cid = str(c.get("id", "")).lower()
    cat = c.get("category")
    if cid.startswith("gbp_") or cat == "local":
        return 4
    if cat == "technical":
        if any(w in cid for w in VIS_IDS):
            return 1
        return 3 if cid == "url_structure" else 2
    if cat in ("onpage", "content") or "cta" in cid:
        return 3
    if cat == "offpage":
        return 5
    return 6


def _step_score(checks):
    rated = [c for c in checks if c.get("status") in ("pass", "warn", "fail")]
    if not rated:
        return None
    return round(100 * sum({"pass": 1, "warn": 0.5, "fail": 0}[c["status"]] for c in rated) / len(rated))


def _color(score):
    if score is None:
        return "#98A2B3"
    return "#079455" if score >= 90 else "#DC6803" if score >= 50 else "#D92D20"


def _verdict(score):
    if score is None:
        return "Not measured"
    return "Good" if score >= 90 else "Needs work" if score >= 50 else "Poor"


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------
def ring(score, size=64, stroke=7, text_size=None, track="#F2F4F7", label_color="#101828"):
    r = (size - stroke) / 2
    circ = 2 * math.pi * r
    pct = 0 if score is None else max(0, min(100, score)) / 100
    color = _color(score)
    ts = text_size or int(size * 0.3)
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{track}" stroke-width="{stroke}"/>'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-dasharray="{circ * pct:.1f} {circ:.1f}" transform="rotate(-90 {size/2} {size/2})"/>'
            f'<text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" font-size="{ts}" font-weight="800" '
            f'fill="{label_color}">{"—" if score is None else score}</text></svg>')


def hbars(items, width=520):
    """Horizontal bar chart: items = [(label, value 0-100 or None)]."""
    row_h, label_w = 30, 210
    h = row_h * len(items) + 10
    out = [f'<svg width="100%" viewBox="0 0 {width} {h}" class="chart">']
    for i, (label, v) in enumerate(items):
        y = 6 + i * row_h
        bw = (width - label_w - 50) * (0 if v is None else v / 100)
        out.append(f'<text x="0" y="{y + 15}" font-size="12" fill="#344054">{E(label)}</text>'
                   f'<rect x="{label_w}" y="{y + 4}" width="{width - label_w - 50}" height="14" rx="7" fill="#F2F4F7"/>'
                   f'<rect x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="14" rx="7" fill="{_color(v)}"/>'
                   f'<text x="{width - 40}" y="{y + 15}" font-size="12" font-weight="700" fill="#101828">'
                   f'{"—" if v is None else v}</text>')
    out.append("</svg>")
    return "".join(out)


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


def vbars_compare(rows, names):
    """Grouped vertical bars: rows = [(metric label, [value per name])]."""
    colors = ["#D92D20", "#98A2B3", "#667085", "#344054"]
    w, h, pad = 560, 200, 30
    groups = len(rows)
    gw = (w - pad) / groups
    bw = min(22, (gw - 20) / max(1, len(names)))
    out = [f'<svg width="100%" viewBox="0 0 {w} {h + 40}" class="chart">',
           f'<line x1="{pad}" y1="{h}" x2="{w}" y2="{h}" stroke="#EAECF0"/>']
    for gi, (label, vals) in enumerate(rows):
        x0 = pad + gi * gw + (gw - bw * len(vals)) / 2
        for ni, v in enumerate(vals):
            bh = 0 if v is None else (h - 20) * v / 100
            out.append(f'<rect x="{x0 + ni * bw:.1f}" y="{h - bh:.1f}" width="{bw - 3:.1f}" height="{bh:.1f}" rx="3" '
                       f'fill="{colors[ni % len(colors)]}"/>'
                       f'<text x="{x0 + ni * bw + (bw - 3) / 2:.1f}" y="{h - bh - 4:.1f}" font-size="10" text-anchor="middle" '
                       f'fill="#344054">{"" if v is None else v}</text>')
        out.append(f'<text x="{pad + gi * gw + gw / 2:.1f}" y="{h + 16}" font-size="11" text-anchor="middle" fill="#475467">{E(label)}</text>')
    out.append("</svg>")
    legend = "".join(f'<div class="lg"><span style="background:{colors[i % len(colors)]}"></span>{E(n)}</div>'
                     for i, n in enumerate(names))
    return "".join(out) + f'<div class="legend-row">{legend}</div>'


# ---------------------------------------------------------------------------
CSS = """
@page{size:A4;margin:12mm}
:root{--ink:#101828;--muted:#667085;--line:#EAECF0;--red:#D92D20;--accent:#E04F16}
*{box-sizing:border-box}body{margin:0;font-family:'Inter','Segoe UI',Arial,sans-serif;color:var(--ink);background:#F2F4F7;line-height:1.5;font-size:14px}
.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 24px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top b{margin-right:auto}.btn{border:1px solid #D0D5DD;background:#fff;color:var(--ink);border-radius:8px;padding:7px 12px;font-weight:600;text-decoration:none;font-size:13px;cursor:pointer}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.page{background:#fff;max-width:900px;margin:22px auto;padding:40px 46px;border-radius:6px;box-shadow:0 1px 3px rgba(16,24,40,.08);page-break-after:always}
.ph{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:26px}
.wm{font-weight:900;letter-spacing:.12em;font-size:15px}.ph span{font-size:11px;color:#98A2B3}
.eyebrow{color:var(--accent);font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;margin:0 0 6px}
h1{font-size:40px;line-height:1.1;margin:0}h2{font-size:26px;line-height:1.2;margin:0 0 8px}h3{font-size:17px;margin:0 0 8px}
.lead{color:#475467;margin:0 0 20px;font-size:15px}
.cover{background:linear-gradient(150deg,#6E1406 0%,#C2410C 45%,#F59E0B 100%);color:#fff;min-height:1000px;display:flex;flex-direction:column;justify-content:space-between}
.cover .wm{font-size:22px}.cover .meta{opacity:.85}.cover h1{font-size:46px}
.scorebox{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.25);border-radius:16px;padding:22px;display:flex;gap:24px;align-items:center}
.scorebox .ringw{background:#fff;border-radius:50%;padding:6px}.mini{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;flex:1}
.mini div{text-align:center}.mini b{display:block;font-size:22px}.mini span{font-size:10px;opacity:.85;line-height:1.2;display:block}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 18px}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px}.tile b{display:block;font-size:28px;line-height:1.1}.tile span{font-size:12px;color:var(--muted)}
.rings{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:18px}
.rings div{border:1px solid var(--line);border-radius:12px;padding:12px 6px;text-align:center;font-size:11px}
.rings em{display:block;font-style:normal;font-weight:700;font-size:10px}
.box{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse}th{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#98A2B3;text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
td{padding:9px 6px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
.pill{display:inline-block;font-size:11px;font-weight:700;border-radius:20px;padding:2px 9px}
.p-ok{background:#ECFDF3;color:#067647}.p-bad{background:#FEF3F2;color:#B42318}.p-warn{background:#FFFAEB;color:#B54708}.p-na{background:#F2F4F7;color:#667085}
.issue{border:1px solid var(--line);border-left:4px solid var(--red);border-radius:10px;padding:14px 16px;margin:12px 0;page-break-inside:avoid}
.issue.medium{border-left-color:#DC6803}.issue.low{border-left-color:#2E90FA}
.ih{display:flex;gap:8px;align-items:center;margin-bottom:8px}.ih h4{margin:0;font-size:15px}
.icols{display:grid;grid-template-columns:1fr 1fr 1.1fr;gap:14px}.lbl{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#98A2B3;font-weight:700;margin-bottom:3px}
.icols p{margin:0;font-size:12.5px;color:#475467}.fixbox{background:#141414;color:#fff;border-radius:8px;padding:10px 12px;font-size:12.5px}
.fixbox .lbl{color:#F79009}.where{font-size:11px;color:#98A2B3;margin-top:4px;word-break:break-all}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{border:1px solid var(--line);border-radius:6px;padding:3px 9px;font-size:12px}
.chip:before{content:'✓ ';color:#079455;font-weight:700}
.sth{display:flex;gap:16px;align-items:center;margin-bottom:6px}
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
.road{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:14px 0;border-bottom:1px solid var(--line)}
.road .st{color:var(--accent);font-weight:800}.road ul{margin:4px 0 0 18px;padding:0;font-size:13px;color:#344054}
.cta{background:linear-gradient(150deg,#6E1406,#C2410C 55%,#F59E0B);color:#fff;border-radius:16px;padding:28px 30px;margin-top:22px}
.cta h2{color:#fff}.note{font-size:11px;color:#98A2B3}
@media print{body{background:#fff}.top{display:none}.page{box-shadow:none;margin:0;max-width:none;border-radius:0;padding:0 2mm}
.cover{min-height:270mm;padding:30px 34px;border-radius:0}}
@media screen and (max-width:760px){.page{padding:24px}.icols,.grid2,.mock,.checklist{grid-template-columns:1fr}.rings,.mini{grid-template-columns:repeat(3,1fr)}.tiles{grid-template-columns:1fr}}
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


def _header(company, label, domain):
    return f'<div class="ph"><div class="wm">{E(company.upper())}</div><span>{E(label)} · {E(domain)}</span></div>'


def build(result, company=None, business=None, downloads=(), gbp=None, gbp_note="no_key", shots=None, per_step=8):
    company = company or {}
    cname = company.get("name") or "Mindcob"
    shots = shots or {}
    domain = result.get("domain") or ""
    client = business or domain
    sc = result.get("score") or {}
    score = sc.get("score")
    checks = list(result.get("checks") or [])
    gbp_all = gbp_checks(gbp, domain) if gbp else []
    all_checks = checks + gbp_all
    by_step = {n: [c for c in all_checks if step_of(c) == n] for n, *_ in STEPS}
    step_scores = {n: _step_score(by_step[n]) for n, *_ in STEPS}
    problems = sorted([c for c in all_checks if c.get("status") in ("fail", "warn")],
                      key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
    urgent = [c for c in problems if c.get("severity") in ("critical", "high")]
    passed = [c for c in all_checks if c.get("status") == "pass"]
    rated = [c for c in all_checks if c.get("status") in ("pass", "warn", "fail")]
    psi = result.get("psi") or {}
    mob, desk = psi.get("mobile") or {}, psi.get("desktop") or {}
    mlab = (mob.get("lab") or {}) if mob.get("ok") else {}
    home = result.get("home_facts") or {}
    date = datetime.now().strftime("%B %d, %Y")
    verdict = ("Good" if (score or 0) >= 90 else "Fair" if (score or 0) >= 70 else "At risk")
    dl = "".join(f'<a class="btn" href="{E(f)}" download>⬇ {E(lbl)}</a>' for lbl, f in downloads)
    contact = " · ".join(E(x) for x in (company.get("website"), company.get("email"), company.get("phone")) if x)

    p = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(cname)} — Website SEO & Growth Audit — {E(domain)}</title><style>{CSS}</style></head><body>
<div class="top"><b>{E(cname)} · Website SEO &amp; Growth Audit · {E(domain)}</b>{dl}<button class="btn primary" onclick="window.print()">Print / Save as PDF</button></div>"""]

    # Cover
    mini = "".join(f"<div><b>{'—' if step_scores[n] is None else step_scores[n]}</b><span>{E(t.split(' &')[0].split(' (')[0])}</span></div>"
                   for n, _k, t, _d in STEPS)
    p.append(f"""<section class="page cover"><div><div class="wm">{E(cname.upper())}</div></div>
<div><div class="meta">{E(cname)} · Website audit</div><h1>Website SEO &amp;<br>Growth Report</h1>
<p class="meta" style="font-size:17px;margin-top:18px">Prepared for <b>{E(client)}</b><br>{E(domain)}</p></div>
<div><div class="scorebox"><div class="ringw">{ring(score, 110, 11, 34)}</div><div style="flex:1">
<div class="meta" style="font-size:11px;letter-spacing:.14em">OVERALL WEBSITE SCORE</div>
<div style="font-size:24px;font-weight:800;margin:4px 0 12px">{verdict} — {len(urgent)} priority issue{'s' if len(urgent) != 1 else ''} found</div>
<div class="mini">{mini}</div></div></div>
<div style="display:flex;justify-content:space-between;margin-top:18px" class="meta"><span>{date}</span><span>{E(cname)}{(' · ' + contact) if contact else ''}</span></div></div></section>""")

    # Executive summary
    ring_cards = "".join(f"<div>{ring(step_scores[n], 58, 6)}<div style='margin-top:4px'>{n}. {E(t.split(' &')[0].split(' (')[0])}</div>"
                         f"<em style='color:{_color(step_scores[n])}'>{_verdict(step_scores[n])}</em></div>" for n, _k, t, _d in STEPS)
    sev_counts = [("Critical", sum(1 for c in problems if c.get("severity") == "critical"), "#B42318"),
                  ("High", sum(1 for c in problems if c.get("severity") == "high"), "#F04438"),
                  ("Medium", sum(1 for c in problems if c.get("severity") == "medium"), "#F79009"),
                  ("Low", sum(1 for c in problems if c.get("severity") == "low"), "#2E90FA")]

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
            meas("Mobile speed score (Google)", (mob.get("scores") or {}).get("performance") if mob.get("ok") else None,
                 "90+", ((mob.get("scores") or {}).get("performance") or 0) >= 90),
            meas("Words of content on homepage", home.get("word_count"), "600+", (home.get("word_count") or 0) >= 600),
            meas("Images with descriptions (alt text)", f"{alts}/{imgs}" if imgs else None, "all", alts >= imgs),
            meas("Pages in XML sitemap", (result.get("sitemap") or {}).get("count"), "all key pages",
                 ((result.get("sitemap") or {}).get("count") or 0) > 0),
            meas("Secure connection (HTTPS)", "Yes" if (result.get("final_url") or "").startswith("https") else "No",
                 "Yes", (result.get("final_url") or "").startswith("https"))]
    p.append(f"""<section class="page">{_header(cname, "Executive summary", domain)}
<p class="eyebrow">Executive summary</p><h2>How {E(domain)} performs for your customers</h2>
<p class="lead">We analysed {E(client)}'s website the way Google and your customers experience it — on a computer and on a phone —
and ran {len(rated)} checks across the six areas that decide how easily you are found, how fast the site loads and how many visitors
turn into calls and enquiries.</p>
<div class="tiles"><div class="tile"><b style="color:{_color(score)}">{score if score is not None else '—'}/100</b><span>Overall score</span></div>
<div class="tile"><b style="color:#D92D20">{len(urgent)}</b><span>Critical &amp; high-priority issues</span></div>
<div class="tile"><b>{len(passed)}/{len(rated)}</b><span>Checks passed</span></div></div>
<div class="rings">{ring_cards}</div>
<div class="grid2"><div class="box"><h3>Score by area</h3>{hbars([(f'{n}. ' + t.split(' &')[0].split(' (')[0], step_scores[n]) for n, _k, t, _d in STEPS])}</div>
<div class="box"><h3>Issues by priority</h3>{donut(sev_counts)}</div></div>
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

    # The six steps
    for n, key, title, desc in STEPS:
        items = _unique([c for c in problems if step_of(c) == n])
        good = [c for c in by_step[n] if c.get("status") == "pass"]
        s = step_scores[n]
        body = [f"""<section class="page">{_header(cname, f"Step {n} · {title}", domain)}<p class="eyebrow">Step {n} of 6</p>
<div class="sth">{ring(s, 64, 7)}<div><h2 style="margin:0">{E(title)}</h2>
<span class="pill {'p-ok' if (s or 0) >= 90 else 'p-bad' if (s or 0) < 50 else 'p-warn'}">{_verdict(s)}</span>
<span class="pill p-na">{len(items)} issue{'s' if len(items) != 1 else ''}</span></div></div><p class="lead">{E(desc)}</p>"""]
        if n == 1:
            body.append(_facts_table([
                ("robots.txt found", (result.get("robots") or {}).get("present"), ""),
                ("Whole site blocked in robots.txt", not (result.get("robots") or {}).get("blocks_all"), "Not blocked"),
                ("XML sitemap", ((result.get("sitemap") or {}).get("count") or 0) > 0,
                 f"{(result.get('sitemap') or {}).get('count') or 0} URLs"),
                ("Homepage indexable (no noindex)", not home.get("noindex"), ""),
                ("Canonical tag on homepage", bool(home.get("canonical")), "")]))
        if n == 4:
            body.append(_gbp_block(gbp, gbp_note, domain, gbp_all))
        if n == 5:
            auth, traffic = result.get("authority") or {}, result.get("traffic") or {}
            body.append(f"""<div class="tiles"><div class="tile"><b>{E(str(auth.get('domain_authority', '—')))}</b><span>Domain authority (estimate)</span></div>
<div class="tile"><b>{E(str(traffic.get('monthly_visits', '—')))}</b><span>Monthly visits (estimate)</span></div>
<div class="tile"><b>{E(str(traffic.get('ranking_keywords', '—')))}</b><span>Ranking keywords (estimate)</span></div></div>
<p class="note">Exact backlink data — total links, referring domains, follow/nofollow split, anchor text, new and lost links —
comes from a backlink database and is reviewed in the full link audit. Links are only marked harmful after checking them.</p>""")
            body.append(_competitors(result, step_scores))
        if n == 6:
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

    # Roadmap
    road = []
    for n, _k, title, _d in STEPS:
        items = _unique([c for c in problems if step_of(c) == n])
        if not items:
            continue
        lines = "".join(f"<li><span class='pill {'p-bad' if c.get('severity') in ('critical', 'high') else 'p-warn'}'>"
                        f"{'Urgent' if c.get('severity') in ('critical', 'high') else 'Next'}</span> {E((plain(c) if not str(c.get('id', '')).startswith('gbp_') else (c['label'], '', c['fix']))[2])}</li>"
                        for c in items[:6])
        road.append(f"<div class='road'><div class='st'>Step {n}<br><span style='color:#101828;font-weight:700'>{E(title)}</span></div><ul>{lines}</ul></div>")
    p.append(f"""<section class="page">{_header(cname, "Action plan", domain)}<p class="eyebrow">Action plan</p>
<h2>Your step-by-step plan to more calls and enquiries</h2>
<p class="lead">We fix things in this order: first make sure Google can find you, then remove technical barriers, improve your pages,
strengthen your local presence and authority, and finish with speed, mobile experience and security.</p>
{''.join(road) or "<div class='box'>No issues to fix — keep monitoring monthly.</div>"}
<div class="cta"><p class="eyebrow" style="color:#FDE68A">Next step</p><h2>Let's fix this together.</h2>
<p>Book a free 15-minute call and we'll walk you through this report, answer your questions and start with the urgent items first —
then re-run this audit so you can see the score go up.</p>
<p style="margin:12px 0 0;font-weight:700">{E(cname)}{(' · ' + contact) if contact else ''}</p></div>
<p class="note" style="margin-top:14px">This report was generated from an automated analysis of {E(result.get('final_url') or domain)} on {date}.
Results reflect the pages checked at the time of testing. Estimates are labelled as estimates.</p></section></body></html>""")
    return "".join(p)


def _facts_table(rows):
    return ("<div class='box'><table><tr><th>Check</th><th>Status</th><th>Details</th></tr>"
            + "".join(f"<tr><td>{E(a)}</td><td>{_pill(bool(ok), 'OK', 'Problem')}</td><td>{E(d)}</td></tr>" for a, ok, d in rows)
            + "</table></div>")


def _gbp_block(place, note, domain, gbp_all):
    if place:
        name = (place.get("displayName") or {}).get("text", "")
        good = sum(1 for c in gbp_all if c["status"] == "pass")
        score = round(100 * good / max(1, len(gbp_all)))
        items = "".join(
            f"<div class='ck'><span class='dot' style='background:{'#101828' if c['status'] == 'pass' else '#FECDCA'}'></span>"
            f"<div><b>{E(c['label'])}</b><div>{E(c['detail'] if c['status'] == 'pass' else c['fix'])}</div></div></div>"
            for c in gbp_all)
        link = place.get("googleMapsUri")
        return (f"<div class='box'><div style='display:flex;justify-content:space-between;align-items:center'><div>"
                f"<p class='eyebrow'>Google Business Profile</p><h3>Your Google Maps listing: {good} of {len(gbp_all)} essentials in place</h3>"
                f"<p class='note'>{E(name)} · {E(place.get('formattedAddress') or '')}"
                + (f" · <a href='{E(link)}'>Open on Google Maps</a>" if link else "") + "</p></div>"
                f"{ring(score, 60, 6)}</div><div class='checklist'>{items}</div></div>")
    msg = {"no_key": "Not checked automatically for this report. We review the listing by hand against the checklist below.",
           "not_found": "We could not find a Google Business Profile for this business — without one it cannot appear on Google Maps."
           }.get(note, "The listing could not be checked during the audit.")
    items = ["Profile claimed and verified", "Website linked", "Phone number listed", "Opening hours listed",
             "Booking or contact link", "Rated 4.0 or higher", "Most specific business category", "New photos and reviews every month"]
    return (f"<div class='box'><p class='eyebrow'>Google Business Profile</p><h3>Your Google Maps listing</h3><p class='note'>{E(msg)}</p>"
            "<div class='checklist'>" + "".join(f"<div class='ck'><span class='dot' style='background:#EAECF0'></span><div><b>{E(x)}</b>"
                                                 f"<div>To verify</div></div></div>" for x in items) + "</div></div>")


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


def _competitors(result, step_scores):
    comps = [c for c in result.get("competitors") or [] if not c.get("error")]
    if not comps:
        return ("<div class='box'><p class='eyebrow'>Competitors</p><p class='note'>No competitors were entered for this audit. "
                "Add them after the website (mysite.com, competitor.com) to see a side-by-side comparison.</p></div>")
    me = {"domain": result.get("domain"), "score": (result.get("score") or {}).get("score"),
          "perf_mobile": ((result.get("psi") or {}).get("mobile") or {}).get("scores", {}).get("performance"),
          "onpage": step_scores.get(3), "technical": step_scores.get(2)}
    cols = [me] + comps
    names = [f"{c.get('domain')}{' (you)' if i == 0 else ''}" for i, c in enumerate(cols)]
    chart = vbars_compare([("SEO score", [c.get("score") for c in cols]), ("Mobile speed", [c.get("perf_mobile") for c in cols]),
                           ("On-page", [c.get("onpage") for c in cols]), ("Technical", [c.get("technical") for c in cols])], names)
    ahead = [c["domain"] for c in comps if (c.get("score") or 0) > (me["score"] or 0)]
    msg = (f"<p><span class='pill p-bad'>!</span> {', '.join(E(a) for a in ahead)} "
           f"{'scores' if len(ahead) == 1 else 'score'} higher than you on Google's checks — the fixes in this report close that gap.</p>"
           if ahead else "<p><span class='pill p-ok'>✓</span> You score higher than the competitors entered. The fixes keep you ahead.</p>")
    return f"<div class='box'><p class='eyebrow'>Competitors</p><h3>How you compare</h3>{chart}{msg}</div>"
