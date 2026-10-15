# -*- coding: utf-8 -*-
"""
HTML renderers for the in-app QTextBrowser tabs (and a standalone HTML export).

Mirrors the .docx report sections so the on-screen preview equals the
deliverable. Charts are embedded as base64 data-URIs (QTextBrowser renders
them). Every function is read-only over the audit result dict.
"""

import html
import json

from seo_checks import CATEGORY_LABEL

STATUS = {
    "pass": ("#1a7f37", "PASS"),
    "warn": ("#c98a00", "WARN"),
    "fail": ("#cf222e", "FAIL"),
    "info": ("#5a6b7b", "INFO"),
    "na": ("#98a2af", "N/A"),
}
PRIORITY_BG = {"Critical": "#fdece9", "High": "#fff1e8", "Medium": "#fff8e6",
               "Low": "#eef4fe"}
PRIORITY_FG = {"Critical": "#cf222e", "High": "#e8590c", "Medium": "#9a6700",
               "Low": "#2e7df2"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def build_charts(renderer, result):
    """Render every chart once; return {name: {'png','b64'} | None}."""
    sc = result.get("score") or {}
    target = {"label": "This site", "domain": result.get("domain", ""),
              "score": sc.get("score")}
    return {
        "gauge": renderer.gauge(sc.get("score", 0), sc.get("grade", "?")),
        "category_bars": renderer.category_bars(sc.get("dimensions") or []),
        "radar": renderer.radar(sc.get("dimensions") or []),
        "severity_donut": renderer.severity_donut(
            result.get("priority_counts") or {}),
        "psi_grouped": renderer.psi_grouped(result.get("psi") or {}),
        "cwv": renderer.cwv(_home_field(result)),
        "top_issues": renderer.top_issues(result.get("action_items") or []),
        "competitor_bars": renderer.competitor_bars(
            target, result.get("competitors") or []),
    }


def _home_field(result):
    psi = result.get("psi") or {}
    src = psi.get("mobile") if (psi.get("mobile") or {}).get("ok") else \
        psi.get("desktop")
    return (src or {}).get("field")


def _img(chart, width=560):
    if not chart or not chart.get("b64"):
        return ""
    return ("<img src='data:image/png;base64,%s' style='max-width:%dpx;"
            "width:100%%;height:auto;margin:8px 0'>" % (chart["b64"], width))


def _badge(status):
    col, lbl = STATUS.get(status, STATUS["info"])
    return ("<span style='background:%s;color:#fff;padding:1px 8px;"
            "border-radius:9px;font-size:11px;font-weight:700'>%s</span>"
            % (col, lbl))


def _checks_table(checks):
    if not checks:
        return "<p style='color:#888'>No checks in this section.</p>"
    rows = ["<table style='border-collapse:collapse;width:100%;font-size:13px'>"
            "<tr style='color:#8b95a3;font-size:11px;text-align:left'>"
            "<th style='padding:5px 8px'>Check</th><th>Status</th>"
            "<th style='padding:5px 8px'>Detail</th></tr>"]
    order = {"fail": 0, "warn": 1, "na": 2, "info": 3, "pass": 4}
    for c in sorted(checks, key=lambda x: order.get(x["status"], 5)):
        fix = ("<div style='color:#2e7df2;font-size:12px;margin-top:2px'>→ %s"
               "</div>" % esc(c["fix"])) if c.get("fix") else ""
        where = ""
        if c.get("where"):
            where = ("<div style='color:#98a2af;font-size:11px'>%s%s</div>"
                     % (esc(", ".join(c["where"][:3])),
                        " …" if len(c["where"]) > 3 else ""))
        rows.append(
            "<tr style='border-top:1px solid #eef1f5'>"
            "<td style='padding:6px 8px;vertical-align:top'><b>%s</b>"
            "<div style='color:#98a2af;font-size:11px'>%s</div></td>"
            "<td style='padding:6px 8px;vertical-align:top'>%s</td>"
            "<td style='padding:6px 8px;vertical-align:top'>%s%s%s</td></tr>"
            % (esc(c["label"]), esc(c["severity"]), _badge(c["status"]),
               esc(c["detail"]), fix, where))
    rows.append("</table>")
    return "".join(rows)


def _wrap(inner):
    return ("<div style='font-family:Segoe UI,Arial;padding:6px;color:#1b2430'>"
            + inner + "</div>")


def overview_html(result, charts):
    sc = result.get("score")
    if not sc:
        return _wrap("<p style='color:#888'>No score could be computed.</p>")
    s = ["<h2 style='margin:2px 0'>%s</h2>" % esc(result.get("domain", "")),
         "<div style='color:#8b95a3;font-size:12px;margin-bottom:10px'>"
         "%s · %d page(s) crawled · %s</div>"
         % (esc(result.get("final_url", "")), result.get("pages_crawled", 0),
            esc(result.get("generated_at", "")))]
    s.append("<table width='100%'><tr><td style='width:280px;vertical-align:top'>")
    s.append(_img(charts.get("gauge"), width=300))
    s.append("</td><td style='vertical-align:top;padding-left:10px'>")
    s.append("<div style='font-size:20px;font-weight:700;color:#1b2430'>%s</div>"
             % esc(sc["label"]))
    s.append("<div style='color:#66707e;font-size:13px;margin:4px 0 10px'>"
             "Confidence: <b>%s</b> · based on %d%% of the full rubric.%s</div>"
             % (esc(sc["confidence"]), sc["completeness_pct"],
                (" Penalties: -%g." % sc["penalty_total"])
                if sc.get("penalty_total") else ""))
    src = result.get("exec_summary_source")
    tag = ("<span style='background:#eef4fe;color:#2e7df2;padding:1px 7px;"
           "border-radius:9px;font-size:11px'>%s</span>"
           % ("AI-written" if src == "ai" else "auto-summary"))
    s.append("<div style='font-size:13px;line-height:1.55'>%s %s</div>"
             % (tag, esc(result.get("exec_summary", ""))))
    s.append("</td></tr></table>")

    pc = result.get("priority_counts") or {}
    chips = []
    for k in ("Critical", "High", "Medium", "Low"):
        if pc.get(k):
            chips.append("<span style='background:%s;color:%s;padding:2px 10px;"
                         "border-radius:10px;font-weight:700;font-size:12px;"
                         "margin-right:6px'>%s: %d</span>"
                         % (PRIORITY_BG[k], PRIORITY_FG[k], k, pc[k]))
    if chips:
        s.append("<div style='margin:12px 0'>" + "".join(chips) + "</div>")

    dp, dn = sc.get("drivers_positive"), sc.get("drivers_negative")
    if dp or dn:
        s.append("<table width='100%'><tr><td width='50%' valign='top'>"
                 "<b style='color:#1a7f37'>What's working</b><ul>")
        s += ["<li>%s</li>" % esc(x) for x in (dp or ["—"])]
        s.append("</ul></td><td width='50%' valign='top'>"
                 "<b style='color:#cf222e'>What to fix</b><ul>")
        s += ["<li>%s</li>" % esc(x) for x in (dn or ["—"])]
        s.append("</ul></td></tr></table>")
    return _wrap("".join(s))


def dashboard_html(result, charts):
    sc = result.get("score") or {}
    s = ["<h2>Score dashboard</h2>"]
    s.append("<table width='100%'><tr><td valign='top'>")
    s.append(_img(charts.get("category_bars"), 420))
    s.append("</td><td valign='top' align='center'>")
    s.append(_img(charts.get("radar"), 300))
    s.append("</td></tr></table>")
    s.append("<table style='border-collapse:collapse;width:100%;font-size:13px;"
             "margin-top:8px'>"
             "<tr style='color:#8b95a3;font-size:11px;text-align:left'>"
             "<th style='padding:5px 8px'>Category</th><th>Score</th>"
             "<th>Weight</th><th>Contribution</th><th>Checks</th></tr>")
    for d in sc.get("dimensions", []):
        cnt = d.get("counts", {})
        s.append("<tr style='border-top:1px solid #eef1f5'>"
                 "<td style='padding:6px 8px'><b>%s</b></td>"
                 "<td style='padding:6px 8px;font-weight:700;color:%s'>%d</td>"
                 "<td style='padding:6px 8px'>%.0f%%</td>"
                 "<td style='padding:6px 8px'>%.1f</td>"
                 "<td style='padding:6px 8px;color:#66707e'>%d Pass / %d Warn / %d Fail"
                 "</td></tr>"
                 % (esc(d["label"]),
                    "#1a7f37" if d["score"] >= 90 else
                    ("#c98a00" if d["score"] >= 50 else "#cf222e"),
                    d["score"], d["weight_effective"] * 100, d["contribution"],
                    cnt.get("pass", 0), cnt.get("warn", 0), cnt.get("fail", 0)))
    s.append("</table>")
    return _wrap("".join(s))


def action_html(result, charts):
    items = result.get("action_items") or []
    s = ["<h2>Prioritized action plan</h2>"]
    s.append("<table width='100%'><tr><td width='45%' valign='top'>")
    s.append(_img(charts.get("severity_donut"), 300))
    s.append("</td><td valign='top'>")
    s.append(_img(charts.get("top_issues"), 340))
    s.append("</td></tr></table>")
    if not items:
        return _wrap("".join(s) + "<p style='color:#1a7f37'>No issues found — "
                     "clean bill of health.</p>")
    s.append("<table style='border-collapse:collapse;width:100%;font-size:12px'>"
             "<tr style='color:#8b95a3;font-size:11px;text-align:left'>"
             "<th style='padding:5px 8px'>Priority</th>"
             "<th>Problem</th><th>Impact</th><th>Recommendation</th>"
             "<th>Difficulty</th><th>Gain</th></tr>")
    for it in items:
        s.append("<tr style='border-top:1px solid #eef1f5'>"
                 "<td style='padding:6px 8px'><span style='background:%s;"
                 "color:%s;padding:1px 8px;border-radius:9px;font-weight:700;"
                 "font-size:11px'>%s</span></td>"
                 "<td style='padding:6px 8px'><b>%s</b>"
                 "<div style='color:#98a2af;font-size:11px'>%s</div></td>"
                 "<td style='padding:6px 8px;color:#66707e;font-size:11px'>%s</td>"
                 "<td style='padding:6px 8px;color:#2e7df2;font-size:12px'>%s</td>"
                 "<td style='padding:6px 8px'>%s</td>"
                 "<td style='padding:6px 8px;font-weight:700;color:%s'>%s</td>"
                 "</tr>"
                 % (PRIORITY_BG.get(it["priority"], "#eee"),
                    PRIORITY_FG.get(it["priority"], "#333"), it["priority"],
                    esc(it["label"]), esc(it["detail"]),
                    esc(it.get("impact_text", "")), esc(it["fix"]),
                    esc(it.get("difficulty", "")),
                    {"High": "#1a7f37", "Medium": "#c98a00",
                     "Low": "#8b95a3"}.get(it.get("gain"), "#8b95a3"),
                    esc(it.get("gain", ""))))
    s.append("</table>")
    return _wrap("".join(s))


def competitor_html(result, charts):
    comps = result.get("competitors") or []
    s = ["<h2>Competitor comparison</h2>"]
    if not comps:
        return _wrap("".join(s) + "<p style='color:#888'>No competitor URLs "
                     "were provided. Add them in Options to benchmark speed, "
                     "content and technical scores side by side.</p>")
    s.append(_img(charts.get("competitor_bars"), 560))
    sc = result.get("score") or {}
    rows = [{"domain": result.get("domain", "") + "  (you)",
             "score": sc.get("score"), "grade": sc.get("grade"),
             "perf_mobile": _psi_perf(result), "https": _is_https(result),
             "title_len": (result.get("home_facts") or {}).get("title_len"),
             "meta_len": (result.get("home_facts") or {}).get("meta_desc_len"),
             "word_count": (result.get("home_facts") or {}).get("word_count"),
             "sd": _target_sd(result)}]
    rows += comps
    s.append("<table style='border-collapse:collapse;width:100%;font-size:12px'>"
             "<tr style='color:#8b95a3;font-size:11px;text-align:left'>"
             "<th style='padding:5px 8px'>Site</th><th>Overall</th><th>Grade</th>"
             "<th>Mobile speed</th><th>HTTPS</th><th>Title</th><th>Meta</th>"
             "<th>Words</th><th>Schema</th></tr>")
    for i, r in enumerate(rows):
        if r.get("error"):
            s.append("<tr style='border-top:1px solid #eef1f5'><td style='"
                     "padding:6px 8px'>%s</td><td colspan='8' style='color:#cf222e"
                     "'>%s</td></tr>" % (esc(r.get("domain", r.get("url", ""))),
                                         esc(r["error"])))
            continue
        bold = "font-weight:700;" if i == 0 else ""
        s.append("<tr style='border-top:1px solid #eef1f5;%s'>"
                 "<td style='padding:6px 8px'>%s</td>"
                 "<td style='padding:6px 8px;color:%s;font-weight:700'>%s</td>"
                 "<td style='padding:6px 8px'>%s</td><td>%s</td><td>%s</td>"
                 "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (bold, esc(r.get("domain", "")),
                    "#1a7f37" if (r.get("score") or 0) >= 75 else "#c98a00",
                    esc(r.get("score", "—")), esc(r.get("grade", "—")),
                    esc(("%d" % r["perf_mobile"]) if r.get("perf_mobile")
                        is not None else "—"),
                    "Yes" if r.get("https") else "No", esc(r.get("title_len", "—")),
                    esc(r.get("meta_len", "—")), esc(r.get("word_count", "—")),
                    "Yes" if r.get("sd") else "No"))
    s.append("</table>")
    return _wrap("".join(s))


def _psi_perf(result):
    psi = result.get("psi") or {}
    m = psi.get("mobile") or {}
    return (m.get("scores") or {}).get("performance") if m.get("ok") else None


def _is_https(result):
    return (result.get("final_url", "") or "").startswith("https://")


def _target_sd(result):
    sd = next(iter((result.get("structured") or {}).values()), None)
    return bool(sd and sd.get("present"))


def category_html(result, category_key, charts=None):
    checks = [c for c in result.get("checks", []) if c["category"] == category_key]
    label = CATEGORY_LABEL.get(category_key, category_key)
    s = ["<h2>%s</h2>" % esc(label)]
    if category_key == "performance" and charts:
        s.append("<table width='100%'><tr><td valign='top'>")
        s.append(_img(charts.get("psi_grouped"), 340))
        s.append("</td><td valign='top'>")
        s.append(_img(charts.get("cwv"), 340))
        s.append("</td></tr></table>")
    s.append(_checks_table(checks))
    return _wrap("".join(s))


def crawlmap_html(result):
    s = ["<h2>Crawl map</h2>"]
    robots = result.get("robots") or {}
    sitemap = result.get("sitemap") or {}
    s.append("<div style='color:#66707e;font-size:13px;margin-bottom:8px'>"
             "robots.txt: <b>%s</b> · sitemap URLs: <b>%d</b> · pages crawled: "
             "<b>%d</b></div>"
             % ("present" if robots.get("present") else "missing",
                sitemap.get("count", 0), result.get("pages_crawled", 0)))
    s.append("<table style='border-collapse:collapse;width:100%;font-size:12px'>"
             "<tr style='color:#8b95a3;font-size:11px;text-align:left'>"
             "<th style='padding:4px 6px'>URL</th><th>Status</th><th>Title len</th>"
             "<th>Meta len</th><th>Words</th><th>H1</th><th>Canonical</th></tr>")
    for f in result.get("all_facts", []):
        s.append("<tr style='border-top:1px solid #eef1f5'>"
                 "<td style='padding:4px 6px'>%s</td><td>%s</td><td>%d</td>"
                 "<td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>"
                 % (esc(f.get("final_url", "")), esc(f.get("status", "")),
                    f.get("title_len", 0), f.get("meta_desc_len", 0),
                    f.get("word_count", 0), f.get("h1_count", 0),
                    "yes" if f.get("canonical") else "no"))
    s.append("</table>")
    errs = result.get("errors") or []
    if errs:
        s.append("<h3>Stage warnings</h3><ul style='color:#9a6700;font-size:12px'>")
        s += ["<li>%s</li>" % esc(e) for e in errs]
        s.append("</ul>")
    return _wrap("".join(s))


def full_html(result, charts):
    """Standalone HTML export (all sections)."""
    parts = ["<!doctype html><meta charset='utf-8'><title>SEO Audit — %s</title>"
             "<body style='max-width:900px;margin:24px auto;font-family:"
             "Segoe UI,Arial'>" % esc(result.get("domain", "")),
             overview_html(result, charts), dashboard_html(result, charts),
             action_html(result, charts)]
    for key, _lbl, _w in __import__("seo_checks").CATEGORIES:
        if any(c["category"] == key for c in result.get("checks", [])):
            parts.append(category_html(result, key, charts))
    if result.get("competitors"):
        parts.append(competitor_html(result, charts))
    parts.append(crawlmap_html(result))
    parts.append("</body>")
    return "\n".join(parts)


def raw_json_html(result):
    slim = {k: v for k, v in result.items()
            if k not in ("all_facts", "home_facts")}
    try:
        txt = json.dumps(slim, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        txt = "Could not serialize: %s" % e
    return ("<pre style='font-family:Consolas,monospace;font-size:11px;"
            "white-space:pre-wrap'>%s</pre>" % esc(txt))
