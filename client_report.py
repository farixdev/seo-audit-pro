# -*- coding: utf-8 -*-
"""
Client report (Website Health Report) for the SEO Audit Tool.

Short, direct and convincing: red when something is wrong, every problem in
plain words with what is happening now, where, how we fix it and what it costs
the business. Sections follow the agreed order:

  Speed · Search visibility & technical · On-page SEO · Design & mobile ·
  Local ranking & Google Business Profile · Off-page & backlinks · Security ·
  Competitors · Prioritized action plan

Only real audit data is shown; estimates are labelled; no prices.
"""

import base64
import os
from datetime import datetime

from full_report import E, SEV_ORDER, _dimension, _ms, _where, gbp_checks, plain

GROUPS = [  # (key, section title, why it matters, business impact, plan order)
    ("visibility", "Search visibility & indexing",
     "If Google can't crawl and index a page, it can never show it to customers.",
     "Pages Google can't find bring zero visitors.", 1),
    ("technical", "Technical errors",
     "Errors, broken links and wrong redirects waste Google's time and send visitors to dead ends.",
     "Lost rankings and visitors who leave before contacting you.", 2),
    ("onpage", "On-page SEO",
     "Titles, descriptions, headings, content and links decide what Google ranks you for and whether people click.",
     "Fewer clicks from Google and weaker rankings for your services.", 3),
    ("local", "Local ranking & Google Business Profile",
     "Local customers pick from the three businesses Google shows on the map.",
     "Nearby customers call your competitors first.", 4),
    ("offpage", "Off-page & backlinks",
     "Links from other trusted websites tell Google your business is credible.",
     "Lower authority keeps you below competitors.", 5),
    ("speed", "Website speed",
     "Slow pages lose visitors before they even see your offer, and Google ranks them lower.",
     "Visitors leave before the page loads; lower mobile rankings.", 6),
    ("design", "Website design & mobile",
     "Most visitors are on a phone. Hard-to-read text, small buttons or unclear next steps make them leave.",
     "Visitors give up instead of calling or booking.", 6),
    ("security", "Security",
     "Weak security puts visitors at risk and can show a 'Not secure' warning that scares them away.",
     "Lost trust and possible browser warnings.", 6),
]
GROUP = {g[0]: g for g in GROUPS}
VISIBILITY_WORDS = ("robots", "sitemap", "index", "canonical", "crawl", "status", "4xx", "5xx", "redirect",
                    "http_", "broken", "404", "noindex", "orphan")


def group_of(c):
    key = f"{c.get('id', '')} {c.get('label', '')}".lower()
    cat = c.get("category")
    if str(c.get("id", "")).startswith("gbp_") or cat == "local":
        return "local"
    if cat in ("technical", "onpage") and any(w in key for w in VISIBILITY_WORDS):
        return "visibility" if any(w in key for w in ("robots", "sitemap", "index", "canonical", "crawl")) else "technical"
    if cat == "technical":
        return "technical"
    if cat in ("onpage", "content") or "cta" in key or "call_to_action" in key:
        return "onpage"
    if cat == "performance":
        return "speed"
    if cat in ("ux", "mobile", "accessibility"):
        return "design"
    if cat == "offpage":
        return "offpage"
    if cat == "security":
        return "security"
    return "technical"


CSS = """
:root{--ink:#1D1D24;--muted:#667085;--line:#EAECF0;--red:#D92D20;--red2:#912018;--bg:#FFF8F7;--green:#079455}
*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',Inter,Arial,sans-serif;color:var(--ink);background:var(--bg);line-height:1.5}
.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:10px 28px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top b{margin-right:auto}.btn{border:1px solid #D0D5DD;background:#fff;color:var(--ink);border-radius:8px;padding:7px 12px;font-weight:600;text-decoration:none;font-size:13px;cursor:pointer}
.btn.primary{background:var(--red);color:#fff;border-color:var(--red)}
.wrap{max-width:1000px;margin:0 auto;padding:26px}
.cover{background:linear-gradient(135deg,#F04438,#7A271A);color:#fff;border-radius:18px;padding:30px 34px}
.cover h1{margin:0;font-size:30px;line-height:1.2}.cover .site{opacity:.9;margin-top:4px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px}
.stat{background:rgba(255,255,255,.14);border-radius:12px;padding:12px 14px}.stat b{display:block;font-size:28px;line-height:1.1}.stat span{font-size:12px;opacity:.9}
.alert{background:#fff;border:2px solid var(--red);border-radius:14px;padding:16px 20px;margin-top:16px}
.alert h3{margin:0 0 6px;color:var(--red)}.alert ul{margin:4px 0 0 18px;padding:0}
section{margin-top:30px;page-break-before:always}section.first{page-break-before:auto}
h2{font-size:22px;margin:0 0 4px;display:flex;align-items:center;gap:10px}.count{background:var(--red);color:#fff;font-size:13px;border-radius:20px;padding:2px 10px}
.count.ok{background:var(--green)}.lead{color:var(--muted);margin:0 0 12px}
.issue{background:#fff;border:1px solid var(--line);border-left:6px solid var(--red);border-radius:12px;padding:14px 16px;margin:10px 0;page-break-inside:avoid}
.issue.med{border-left-color:#F79009}.issue h4{margin:0;font-size:16px}
.head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.badge{font-size:11px;font-weight:700;border-radius:20px;padding:2px 9px;color:#fff;background:var(--red)}
.badge.med{background:#F79009}.issue p{margin:8px 0 0;font-size:14px}
.fix{margin-top:8px;background:#ECFDF3;border:1px solid #ABEFC6;border-radius:10px;padding:8px 12px;font-size:14px}
.impact{margin-top:6px;font-size:13px;color:var(--red2)}.where{font-size:12px;color:var(--muted);word-break:break-all;margin-top:4px}
.ok-box{background:#fff;border:1px solid #ABEFC6;border-radius:12px;padding:14px 16px;color:#067647}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line)}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}
th{background:#FEF3F2;color:var(--red2)}.bad{color:var(--red);font-weight:700}.good{color:var(--green);font-weight:700}
.shots{display:grid;grid-template-columns:3fr 1fr;gap:14px;align-items:start}.shot{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px}
.shot img{width:100%;border-radius:8px;border:1px solid var(--line);display:block}.shot div{font-size:12px;color:var(--muted);margin-top:6px;text-align:center}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.kpis .card b{display:block;font-size:24px}
.note{font-size:12px;color:var(--muted)}.cta{background:#fff;border:2px solid var(--red);border-radius:14px;padding:18px 20px;margin-top:24px}
@media print{.top{display:none}body{background:#fff}.wrap{padding:0}}
@media (max-width:700px){.stats,.kpis{grid-template-columns:1fr 1fr}.shots{grid-template-columns:1fr}}
"""


def _img(path):
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return f"data:{mime};base64,{data}"


def _card(c):
    sev = c.get("severity") or "low"
    urgent = sev in ("critical", "high")
    if str(c.get("id", "")).startswith("gbp_"):
        title, now, fix = c["label"], c["detail"], c["fix"]
    else:
        title, now, fix = plain(c)
    where = _where(c)[:3]
    g = GROUP[group_of(c)]
    return (f'<div class="issue{"" if urgent else " med"}"><div class="head"><span class="badge{"" if urgent else " med"}">'
            f'{"URGENT" if urgent else "IMPORTANT"}</span><h4>{E(title)}</h4></div>'
            f'<p><b>What is happening now:</b> {E(now)}</p>'
            + (f'<div class="where"><b>Where:</b> {" · ".join(E(u) for u in where)}</div>' if where else "")
            + f'<div class="fix"><b>How we fix it:</b> {E(fix)}</div>'
            f'<div class="impact"><b>Business impact:</b> {E(g[3])}</div></div>')


def _unique(items):
    seen, out = set(), []
    for c in items:
        t = c["label"] if str(c.get("id", "")).startswith("gbp_") else plain(c)[0]
        if t not in seen:
            seen.add(t)
            out.append(c)
    return out


def build(result, downloads=(), gbp=None, gbp_note="no_key", shots=None, per_section=5):
    shots = shots or {}
    domain = result.get("domain") or ""
    sc = result.get("score") or {}
    score = sc.get("score") or 0
    checks = result.get("checks") or []
    gbp_items = [c for c in gbp_checks(gbp, domain) if c["status"] in ("fail", "warn")] if gbp else []
    problems = sorted([c for c in checks if c.get("status") in ("fail", "warn")] + gbp_items,
                      key=lambda c: (SEV_ORDER.get(c.get("severity"), 9), c.get("status") != "fail"))
    by_group = {}
    for c in problems:
        by_group.setdefault(group_of(c), []).append(c)
    urgent = [c for c in problems if c.get("severity") in ("critical", "high")]
    psi = result.get("psi") or {}
    mob, desk = psi.get("mobile") or {}, psi.get("desktop") or {}
    mob_score = (mob.get("scores") or {}).get("performance") if mob.get("ok") else None
    lcp = (mob.get("lab") or {}).get("lcp_ms") if mob.get("ok") else None
    risk = "HIGH RISK" if score < 60 or len(urgent) >= 5 else ("AT RISK" if score < 80 or urgent else "LOW RISK")
    headline = ("Your website is losing customers every day" if risk != "LOW RISK"
                else "Your website is doing well, but a few gaps are costing you")
    dl = "".join(f'<a class="btn" href="{E(f)}" download>⬇ {E(lbl)}</a>' for lbl, f in downloads)

    bullets = []
    if lcp is not None:
        bullets.append(f"On phones your page takes <b>{_ms(lcp)}</b> to show its main content "
                       f"({'slower than' if lcp > 2500 else 'within'} Google's 2.5 s target).")
    if urgent:
        bullets.append(f"<b>{len(urgent)} urgent problem{'s are' if len(urgent) != 1 else ' is'}</b> holding back your Google rankings right now.")
    vis = by_group.get("visibility", [])
    if vis:
        bullets.append(f"<b>{len(vis)} problems</b> stop Google from finding or indexing parts of your site.")
    if by_group.get("local"):
        bullets.append("Local customers searching on Google Maps are more likely to find your competitors first.")
    if by_group.get("security"):
        bullets.append("Security settings are missing that protect your visitors and your reputation.")
    bullets.append("Every problem below has a clear fix — most can be done within days.")

    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Website Health Report — {E(domain)}</title><style>{CSS}</style></head><body>
<div class="top"><b>Website Health Report · {E(domain)}</b>{dl}<button class="btn primary" onclick="window.print()">Print / Save as PDF</button></div>
<div class="wrap">
<div class="cover"><div style="font-weight:700;letter-spacing:.08em;opacity:.9">{risk}</div><h1>{E(headline)}</h1>
<div class="site">{E(domain)} · checked {E(result.get('date') or datetime.now().strftime('%Y-%m-%d'))} · {result.get('pages_crawled') or 0} pages</div>
<div class="stats"><div class="stat"><b>{score}/100</b><span>SEO health score</span></div>
<div class="stat"><b>{len(problems)}</b><span>problems found</span></div>
<div class="stat"><b>{len(urgent)}</b><span>urgent</span></div>
<div class="stat"><b>{mob_score if mob_score is not None else '—'}</b><span>mobile speed /100</span></div></div></div>
<div class="alert"><h3>What is happening right now</h3><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul></div>"""]

    # Screenshots
    desk_img, mob_img = _img(shots.get("desktop")), _img(shots.get("mobile"))
    if desk_img or mob_img:
        parts.append("<section class='first'><h2>Your website today</h2><p class='lead'>This is what your visitors see right now "
                     "on a computer and on a phone.</p><div class='shots'>"
                     + (f"<div class='shot'><img src='{desk_img}' alt='Desktop view'><div>Computer</div></div>" if desk_img else "")
                     + (f"<div class='shot'><img src='{mob_img}' alt='Phone view'><div>Phone</div></div>" if mob_img else "")
                     + "</div></section>")

    # Speed table first (always shown)
    def speed_row(name, d):
        if not d.get("ok"):
            return (f"<tr><td><b>{name}</b></td><td colspan='4' class='note'>Google's speed test did not answer during the audit "
                    "(add a Google API key in the tool for reliable speed scores).</td></tr>")
        s = (d.get("scores") or {}).get("performance")
        lab = d.get("lab") or {}
        cls = "good" if (s or 0) >= 90 else "bad"
        return (f"<tr><td><b>{name}</b></td><td class='{cls}'>{s}/100</td><td>{_ms(lab.get('lcp_ms'))}</td>"
                f"<td>{_ms(lab.get('tbt_ms'))}</td><td>{round(lab['cls'], 3) if lab.get('cls') is not None else '—'}</td></tr>")

    order = ["speed", "visibility", "technical", "onpage", "design", "local", "offpage", "security"]
    for key in order:
        g = GROUP[key]
        items = _unique(by_group.get(key, []))
        n = len(items)
        unmeasured = key == "speed" and not mob.get("ok") and not desk.get("ok")
        badge = ("<span class='count' style='background:#667085'>not measured</span>" if unmeasured else
                 f"<span class='count{' ok' if not n else ''}'>{n} problem{'s' if n != 1 else ''}</span>")
        head = f"<section><h2>{E(g[1])} {badge}</h2><p class='lead'>{E(g[2])}</p>"
        body = []
        if key == "speed":
            body.append("<table><tr><th>Device</th><th>Google speed score</th><th>Main content shows</th>"
                        "<th>Frozen time</th><th>Layout jumps</th></tr>" + speed_row("Phone", mob) + speed_row("Computer", desk)
                        + "</table><p class='note'>Google's targets: main content under 2.5 s, frozen time under 200 ms, "
                          "layout jumps under 0.1.</p>")
        if key == "offpage":
            auth, traffic = result.get("authority") or {}, result.get("traffic") or {}
            def _mv(v):  # never show an unmeasured value as a number
                return "Not measured" if v is None else ("{:,}".format(v) if isinstance(v, int) else str(v))
            body.append(f"<div class='kpis'><div class='card'><b>{E(_mv(auth.get('domain_authority')))}</b>Domain authority</div>"
                        f"<div class='card'><b>{E(_mv(traffic.get('monthly_visits')))}</b>Monthly visits</div>"
                        f"<div class='card'><b>{E(_mv(traffic.get('ranking_keywords')))}</b>Ranking keywords</div></div>"
                        "<p class='note'>Exact backlink numbers (total links, referring domains, follow/nofollow, anchor text, "
                        "new/lost and potentially harmful links) come from a backlink database; we review them in the full link "
                        "audit and only flag links as harmful after checking them, not automatically.</p>")
        if key == "security":
            ssl = result.get("ssl_cert") or {}
            https = (result.get("final_url") or "").startswith("https")
            redir = (result.get("http_redirect") or {}).get("redirects_to_https")
            mixed = [c for c in checks if "mixed" in f"{c.get('id', '')}".lower()]
            # ok: True / False / None (None = not checked; shown neutrally).
            rc = next((c for c in checks if c.get("id") == "sec_http_redirect"), None)
            if rc is not None and rc.get("status") in ("na", "info"):
                redir_row = ("HTTP → HTTPS redirect", None, "Could not be tested")
            elif rc is not None and rc.get("status") == "pass" and "port closed" in (rc.get("detail") or ""):
                redir_row = ("HTTP → HTTPS redirect", True, "Only the secure address is served (HTTP is closed)")
            elif rc is None and result.get("http_redirect") is None:
                redir_row = ("HTTP → HTTPS redirect", None, "Could not be tested")
            else:
                redir_ok = (rc.get("status") == "pass") if rc is not None else bool(redir)
                redir_row = ("HTTP → HTTPS redirect", redir_ok, "Visitors are moved to the secure address" if redir_ok
                             else "Visitors can land on the insecure address")
            mixed_bad = any(c.get("status") in ("fail", "warn") for c in mixed)
            mixed_ok = None if (mixed and all(c.get("status") in ("na", "info") for c in mixed)) else (not mixed_bad)
            rows = [("HTTPS (secure address)", https, "Your site uses https://" if https else "Your site is not on https://"),
                    ("SSL certificate", bool(ssl.get("valid")),
                     f"Valid, expires {ssl.get('not_after')} ({ssl.get('days_left')} days left)" if ssl.get("valid")
                     else f"Problem: {ssl.get('error') or 'not valid'}"),
                    redir_row,
                    ("Mixed content", mixed_ok,
                     "Could not be scanned" if mixed_ok is None else
                     ("No insecure files found on secure pages" if not mixed_bad
                      else "Some files load insecurely on secure pages"))]
            body.append("<table><tr><th>Check</th><th>Result</th><th>Details</th></tr>" + "".join(
                f"<tr><td>{E(a)}</td><td class='{'' if ok is None else ('good' if ok else 'bad')}'>"
                f"{'NOT CHECKED' if ok is None else ('OK' if ok else 'PROBLEM')}</td><td>{E(d)}</td></tr>"
                for a, ok, d in rows) + "</table>")
        if key == "local" and not gbp:
            body.append("<div class='card'><b>Google Business Profile:</b> "
                        + E({"no_key": "not checked automatically — we review photos, reviews, hours, category and description "
                                       "by hand and complete anything missing.",
                             "not_found": "we could not find a Google Business Profile for this business. Without one, "
                                          "the business cannot appear on Google Maps."}.get(gbp_note, "not checked."))
                        + "</div>")
        if items:
            body += [_card(c) for c in items[:per_section]]
            if n > per_section:
                body.append(f"<p class='note'>+ {n - per_section} more in this area — listed in the full SEO audit.</p>")
        elif key not in ("speed", "security"):
            body.append("<div class='ok-box'>✓ No problems found in this area.</div>")
        parts.append(head + "".join(body) + "</section>")

    parts.append(_competitors(result))

    # Prioritized action plan in the agreed order
    plan = sorted(_unique(problems), key=lambda c: (GROUP[group_of(c)][4], SEV_ORDER.get(c.get("severity"), 9)))
    steps = {1: "1. Search visibility", 2: "2. Technical SEO", 3: "3. On-page SEO", 4: "4. Local SEO", 5: "5. Off-page SEO",
             6: "6. Speed, mobile & security"}
    rows = []
    for c in plan:
        g = GROUP[group_of(c)]
        title, _now, fix = (c["label"], c["detail"], c["fix"]) if str(c.get("id", "")).startswith("gbp_") else plain(c)
        urgent_c = c.get("severity") in ("critical", "high")
        where = _where(c)[:2]
        rows.append(f"<tr><td>{steps[g[4]]}</td><td class='{'bad' if urgent_c else ''}'>{'Urgent' if urgent_c else 'Important'}</td>"
                    f"<td><b>{E(title)}</b>" + (f"<div class='where'>{' · '.join(E(u) for u in where)}</div>" if where else "")
                    + f"</td><td>{E(fix)}</td><td>{E(g[3])}</td></tr>")
    parts.append("<section><h2>Your action plan</h2><p class='lead'>Fixed in this order: first make sure Google can find you, "
                 "then remove errors, then improve pages, local presence, authority, and finally speed, mobile and security.</p>"
                 + ("<table><tr><th>Step</th><th>Priority</th><th>Problem</th><th>Solution</th><th>Business impact</th></tr>"
                    + "".join(rows) + "</table>" if rows else "<div class='ok-box'>Nothing to fix.</div>")
                 + "<div class='cta'><b>Next step</b><p style='margin:6px 0 0'>We start with the urgent items this week and "
                   "re-run this audit afterwards so you can see the score go up.</p></div>"
                 + f"<p class='note'>Report generated {datetime.now():%d %b %Y} · estimates are labelled as estimates.</p>"
                 "</section></div></body></html>")
    return "".join(parts)


def _competitors(result):
    comps = [c for c in result.get("competitors") or [] if not c.get("error")]
    if not comps:
        return ""
    me = {"domain": result.get("domain"), "score": (result.get("score") or {}).get("score"),
          "perf_mobile": ((result.get("psi") or {}).get("mobile") or {}).get("scores", {}).get("performance"),
          "onpage": _dimension(result, "onpage"), "content_score": _dimension(result, "content")}
    cols = [me] + comps
    rows = [("SEO score", "score"), ("Mobile speed", "perf_mobile"), ("On-page SEO", "onpage"), ("Content", "content_score")]

    def cell(i, k):
        v = cols[i].get(k)
        if v is None:
            return "—"
        best = max((c.get(k) or 0) for c in cols)
        return f"<span class='{'good' if v == best else ('bad' if i == 0 else '')}'>{E(str(v))}</span>"
    table = ("<table><tr><th></th>" + "".join(f"<th>{E(str(c.get('domain')))}{' (you)' if i == 0 else ''}</th>"
                                             for i, c in enumerate(cols)) + "</tr>"
             + "".join("<tr><td><b>" + E(lbl) + "</b></td>" + "".join(f"<td>{cell(i, k)}</td>" for i in range(len(cols)))
                       + "</tr>" for lbl, k in rows) + "</table>")
    ahead = [c["domain"] for c in comps if (c.get("score") or 0) > (me["score"] or 0)]
    note = (f"<div class='alert'><b>{', '.join(E(a) for a in ahead)} {'is' if len(ahead) == 1 else 'are'} ahead of you on Google.</b> "
            "Fixing the items in this report closes the gap.</div>" if ahead else
            "<div class='ok-box'>You are ahead of these competitors overall — the fixes keep it that way.</div>")
    return ("<section><h2>Competitors</h2><p class='lead'>How you compare with the competitors you named.</p>"
            + table + note + "</section>")


def screenshots(url, folder, timeout=40):
    """Desktop and phone screenshots of the live site with Edge/Chrome headless.
    Returns {"desktop": path, "mobile": path} for the ones that worked (JPEG)."""
    import shutil
    import subprocess
    import tempfile
    from full_report import _browser

    out = {}
    # 1. Reuse already captured screenshots if they exist
    for name in ("desktop", "mobile"):
        for ext in (".jpg", ".png"):
            p = os.path.join(folder, f"screenshot-{name}{ext}")
            if os.path.isfile(p) and os.path.getsize(p) > 2000:
                out[name] = p
                break
    if len(out) == 2:
        return out

    exe = _browser()
    if not exe:
        return out

    phone_ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

    def capture_one(name, size, extra):
        if name in out:
            return name, out[name]
        png = os.path.join(folder, f"screenshot-{name}.png")
        profile = tempfile.mkdtemp(prefix="seo-shot-")
        try:
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                            "--run-all-compositor-stages-before-draw",
                            f"--user-data-dir={profile}", f"--window-size={size}", "--virtual-time-budget=2500",
                            f"--screenshot={png}", *extra, url],
                           capture_output=True, timeout=timeout, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return name, None
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if os.path.isfile(png) and os.path.getsize(png) > 2000:
            return name, _to_jpeg(png)
        return name, None

    targets = [
        ("desktop", "1366,860", []),
        ("mobile", "390,844", [f"--user-agent={phone_ua}"]),
    ]

    # Both captures at the same time (plain threads: concurrent.futures is a lazy
    # import in this Python and fails when called from a frozen build).
    import threading
    found = {}

    def worker(name, size, extra):
        try:
            found[name] = capture_one(name, size, extra)[1]
        except Exception:  # noqa: BLE001 - a screenshot is optional
            found[name] = None

    threads = [threading.Thread(target=worker, args=t, daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 5)
    out.update({k: v for k, v in found.items() if v})

    return out


def _to_jpeg(png):
    """Smaller files for the report (Qt is available in the tool)."""
    try:
        from PySide6.QtGui import QImage
        img = QImage(png)
        if img.isNull():
            return png
        jpg = png[:-4] + ".jpg"
        if img.save(jpg, "JPG", 82):
            os.remove(png)
            return jpg
    except Exception:  # noqa: BLE001
        pass
    return png
