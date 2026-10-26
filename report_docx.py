# -*- coding: utf-8 -*-
"""
The official Mindcob SEO Audit report (.docx) — a client-first, visually-led
deliverable designed to win the lead.

Design principles (from best-in-class agency reports):
  * Executive summary and health score FIRST, in plain business language.
  * Visual scoring — score meters and progress bars a non-technical decision
    maker can read in seconds.
  * "So what?" framing — every section ties findings to visibility, traffic and
    revenue, not error codes.
  * A clear next-steps / how-we-help close.

All visuals are NATIVE, EDITABLE Word elements — progress bars, KPI tiles and
scorecards are built from real tables with cell shading (no rasterised PNG
charts), so the client can recolour, resize and edit everything in Word. The
`charts` argument is accepted for API compatibility but intentionally unused.
"""

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, Inches, RGBColor

from seo_checks import CATEGORIES

# ---- palette ---------------------------------------------------------------
BRAND = RGBColor(0x2E, 0x7D, 0xF2)
BRAND_DK = RGBColor(0x1E, 0x5F, 0xD0)
INK = RGBColor(0x1B, 0x24, 0x30)
SLATE = RGBColor(0x46, 0x51, 0x5F)
MUTED = RGBColor(0x8B, 0x95, 0xA3)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GOOD = RGBColor(0x14, 0x7A, 0x40)
WARN = RGBColor(0x8A, 0x60, 0x00)
BAD = RGBColor(0xC0, 0x2A, 0x2E)

SH_BAND = "2E7DF2"
SH_CARD = "F3F7FD"
SH_CALLOUT = "F4F8FF"
SH_HEADER = "1E5FD0"
SH_TRACK = "EDF1F6"
SH_CTA = "0E2A55"
GOOD_HEX, WARN_HEX, BAD_HEX, MUTED_HEX = "147A40", "8A6000", "C02A2E", "8B95A3"
STATUS_SHADE = {"pass": "E4F5EA", "warn": "FDF1CF", "fail": "FBDDDB",
                "info": "EEF1F5", "na": "F0F2F5"}
STATUS_FG = {"pass": GOOD, "warn": WARN, "fail": BAD, "info": SLATE,
             "na": MUTED}
STATUS_TEXT = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "info": "INFO",
               "na": "N/A"}
PRIORITY_SHADE = {"Critical": "FBDDDB", "High": "FCE6DA", "Medium": "FDF1CF",
                  "Low": "E7EFFC"}
PRIORITY_FG = {"Critical": BAD, "High": RGBColor(0xC0, 0x50, 0x1A),
               "Medium": WARN, "Low": BRAND_DK}
PRIORITY_HEX = {"Critical": "C02A2E", "High": "E8590C", "Medium": "C98A00",
                "Low": "2E7DF2"}

PAGE_W = 6.9  # usable width at 0.8" margins on Letter

# Plain-language "what this means for your business" per category.
CATEGORY_CLIENT = {
    "technical": "Can Google find, crawl and trust your site? Problems here can "
                 "keep pages out of search entirely — no matter how good they "
                 "are.",
    "onpage": "Does each page clearly tell Google and customers what it's "
              "about? Weak signals mean lost rankings and fewer clicks.",
    "performance": "How fast your pages load. Google measures this directly, "
                   "and slow pages quietly lose visitors before they convert.",
    "content": "Is your content deep, readable and trustworthy enough to rank "
               "and win the click? Thin or hard-to-read pages underperform.",
    "mobile": "How your site behaves on phones — where most of your visitors "
              "and Google's ranking signals now come from.",
    "accessibility": "How usable your site is for everyone, including people "
                     "using assistive technology. It overlaps with SEO and "
                     "lowers legal risk.",
    "security": "Do visitors and Google see your site as safe? Security gaps "
                "erode trust and can trigger browser warnings.",
    "ux": "The real experience visitors get — stability, responsiveness and "
          "smoothness. Poor experience costs conversions and rankings.",
    "local": "Evaluates your visibility in Google Maps, local search, Google Business "
             "Profile consistency, and local citations.",
    "offpage": "Domain Authority, backlink profile trust, follow vs nofollow link "
               "ratios, and referral link health.",
}


def _score_rgb(v):
    if v is None:
        return MUTED
    return GOOD if v >= 80 else (WARN if v >= 55 else BAD)


def _score_hex(v):
    if v is None:
        return MUTED_HEX
    return GOOD_HEX if v >= 80 else (WARN_HEX if v >= 55 else BAD_HEX)


def _score_shade(v):
    if v is None:
        return "F0F2F5"
    return "E4F5EA" if v >= 80 else ("FDF1CF" if v >= 55 else "FBDDDB")


# ---- low-level helpers -----------------------------------------------------
def _shade(cell, hexcolor):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hexcolor)
    tcpr.append(shd)


def _no_table_borders(table):
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:%s" % edge)
        e.set(qn("w:val"), "none")
        borders.append(e)
    table._tbl.tblPr.append(borders)


def _table_cell_margins(table, val=0):
    mar = OxmlElement("w:tblCellMar")
    for tag in ("top", "start", "bottom", "end"):
        e = OxmlElement("w:%s" % tag)
        e.set(qn("w:w"), str(val))
        e.set(qn("w:type"), "dxa")
        mar.append(e)
    table._tbl.tblPr.append(mar)


def _fixed_layout(table):
    el = OxmlElement("w:tblLayout")
    el.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(el)


def _set_table_width(table, inches):
    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(inches * 1440)))


def _row_height(row, twips, rule="exact"):
    trPr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(twips))
    h.set(qn("w:hRule"), rule)
    trPr.append(h)


def _cell_margins(cell, top=60, bottom=60, left=110, right=110):
    tcpr = cell._tc.get_or_add_tcPr()
    m = OxmlElement("w:tcMar")
    for tag, val in (("top", top), ("bottom", bottom), ("start", left),
                     ("end", right)):
        el = OxmlElement("w:%s" % tag)
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        m.append(el)
    tcpr.append(m)


def _left_accent(cell, hex6="2E7DF2", size=30):
    tcpr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(size))
    left.set(qn("w:color"), hex6)
    borders.append(left)
    tcpr.append(borders)


def _rule_below(paragraph, hex6="D9E2F1", size=8):
    pPr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), hex6)
    pbdr.append(bottom)
    pPr.append(pbdr)


def _runs(paragraph, chunks):
    for text, a in chunks:
        r = paragraph.add_run(text)
        r.bold = a.get("bold", False)
        r.italic = a.get("italic", False)
        if a.get("size"):
            r.font.size = Pt(a["size"])
        if a.get("color") is not None:
            r.font.color.rgb = a["color"]
    return paragraph


def _cell_text(cell, chunks, align=None, valign=None, shade=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(0)
    _runs(p, chunks)
    if valign is not None:
        cell.vertical_alignment = valign
    if shade:
        _shade(cell, shade)
    return p


def _set_widths(table, widths):
    table.autofit = False
    table.allow_autofit = False
    for row in table.rows:
        for i, w in enumerate(widths):
            if i < len(row.cells):
                row.cells[i].width = Inches(w)


def _spacer(doc, pt=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(pt)
    p.paragraph_format.space_before = Pt(0)
    return p


# ---- NATIVE progress bar (editable — real table + shading) -----------------
def _native_bar(cell, pct, color_hex, width_in=3.0, height_tw=150,
                track=SH_TRACK):
    """Draw an editable progress bar inside `cell` using shaded table cells."""
    pct = max(0.0, min(100.0, float(pct if pct is not None else 0)))
    if pct <= 0.5:
        t = cell.add_table(rows=1, cols=1)
        widths = [(width_in, track)]
    elif pct >= 99.5:
        t = cell.add_table(rows=1, cols=1)
        widths = [(width_in, color_hex)]
    else:
        t = cell.add_table(rows=1, cols=2)
        fw = round(width_in * pct / 100.0, 3)
        widths = [(fw, color_hex), (round(width_in - fw, 3), track)]
    _no_table_borders(t)
    _table_cell_margins(t, 0)
    _fixed_layout(t)
    _set_table_width(t, width_in)
    for c, (w, hexc) in zip(t.rows[0].cells, widths):
        c.width = Inches(max(0.02, w))
        _shade(c, hexc)
        c.text = ""
        pp = c.paragraphs[0]
        pp.paragraph_format.space_after = Pt(0)
        pp.paragraph_format.space_before = Pt(0)
        pp.add_run("").font.size = Pt(2)
    _row_height(t.rows[0], height_tw, "exact")
    return t


def _stacked_bar(doc, segments, width_in=PAGE_W, height_tw=280):
    """A single stacked bar (e.g. issue severity), proportional cells with
    labels. segments = [(label, count, color_hex)] — zero-count dropped."""
    segs = [(l, n, c) for (l, n, c) in segments if n > 0]
    if not segs:
        return
    total = sum(n for _l, n, _c in segs)
    t = doc.add_table(rows=1, cols=len(segs))
    _no_table_borders(t)
    _table_cell_margins(t, 40)
    _fixed_layout(t)
    _set_table_width(t, width_in)
    for cell, (label, n, hexc) in zip(t.rows[0].cells, segs):
        cell.width = Inches(max(0.5, width_in * n / total))
        _shade(cell, hexc)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        _runs(p, [("%s  %d" % (label, n), {"size": 9, "bold": True,
                                           "color": WHITE})])
    _row_height(t.rows[0], height_tw, "atLeast")


# ---- styling ---------------------------------------------------------------
def _base_styles(doc):
    normal = doc.styles["Normal"]
    normal.font.name = "Segoe UI"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    for name, size, color in [("Heading 1", 15, BRAND_DK),
                              ("Heading 2", 12.5, INK),
                              ("Heading 3", 11, SLATE)]:
        try:
            st = doc.styles[name]
            st.font.name = "Segoe UI Semibold"
            st.font.size = Pt(size)
            st.font.bold = True
            st.font.color.rgb = color
            st.paragraph_format.space_before = Pt(10)
            st.paragraph_format.space_after = Pt(4)
        except Exception:
            pass
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)


def _section_heading(doc, text):
    h = doc.add_heading(text, level=1)
    _rule_below(h)
    return h


def _header_footer(doc, result):
    section = doc.sections[0]
    hp = section.header.paragraphs[0]
    hp.text = ""
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _runs(hp, [("MINDCOB  ·  SEO Audit", {"size": 8, "color": MUTED,
                                          "bold": True})])
    fp = section.footer.paragraphs[0]
    fp.text = ""
    fp.paragraph_format.tab_stops.add_tab_stop(Inches(PAGE_W),
                                               WD_TAB_ALIGNMENT.RIGHT)
    _runs(fp, [("Prepared for %s" % result.get("domain", ""),
                {"size": 8, "color": MUTED})])
    fp.add_run("\t")
    run = fp.add_run()
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    _page_field(run)


def _page_field(run):
    f1 = OxmlElement("w:fldChar")
    f1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    f2 = OxmlElement("w:fldChar")
    f2.set(qn("w:fldCharType"), "end")
    for el in (f1, instr, f2):
        run._r.append(el)


def _toc(doc):
    _section_heading(doc, "Contents")
    p = doc.add_paragraph()
    run = p.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "Open in Word and press Ctrl+A then F9 to build contents."
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, sep, placeholder, end):
        run._r.append(el)


def _banner(doc, result):
    t = doc.add_table(rows=1, cols=1)
    _no_table_borders(t)
    _set_widths(t, [PAGE_W])
    cell = t.rows[0].cells[0]
    _shade(cell, SH_BAND)
    _cell_margins(cell, top=240, bottom=240, left=260, right=260)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p1 = cell.paragraphs[0]
    p1.paragraph_format.space_after = Pt(2)
    _runs(p1, [("S E O   A U D I T", {"size": 11, "color": WHITE,
                                      "bold": True})])
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(2)
    _runs(p2, [(result.get("domain", ""), {"size": 27, "color": WHITE,
                                           "bold": True})])
    p3 = cell.add_paragraph()
    _runs(p3, [("Prepared for %s   ·   %s   ·   %d page(s) reviewed"
                % (result.get("domain", ""), result.get("generated_at", ""),
                   result.get("pages_crawled", 0)),
                {"size": 9.5, "color": WHITE})])


def _callout(doc, title, text, accent="2E7DF2", shade=SH_CALLOUT,
             title_color=BRAND_DK):
    t = doc.add_table(rows=1, cols=1)
    _no_table_borders(t)
    _set_widths(t, [PAGE_W])
    cell = t.rows[0].cells[0]
    _shade(cell, shade)
    _left_accent(cell, accent, size=34)
    _cell_margins(cell, top=150, bottom=150, left=190, right=170)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(4)
    _runs(p, [(title, {"size": 11, "bold": True, "color": title_color})])
    body = cell.add_paragraph()
    _runs(body, [(text, {"size": 10.5, "color": INK})])
    return cell


def _kpi_tiles(doc, tiles):
    """tiles = [(value, label, RGBColor)]"""
    t = doc.add_table(rows=1, cols=len(tiles))
    _no_table_borders(t)
    _set_widths(t, [PAGE_W / len(tiles)] * len(tiles))
    for cell, (val, label, color) in zip(t.rows[0].cells, tiles):
        _shade(cell, SH_CARD)
        _cell_margins(cell, top=150, bottom=150, left=80, right=80)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        pv = cell.paragraphs[0]
        pv.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pv.paragraph_format.space_after = Pt(1)
        _runs(pv, [(str(val), {"size": 26, "bold": True, "color": color})])
        pl = cell.add_paragraph()
        pl.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _runs(pl, [(label, {"size": 8, "color": MUTED, "bold": True})])


def _style_header_row(table):
    for cell in table.rows[0].cells:
        _shade(cell, SH_HEADER)
        for p in cell.paragraphs:
            for r in p.runs:
                r.font.color.rgb = WHITE
                r.bold = True


def _checks_table(doc, checks):
    if not checks:
        doc.add_paragraph("No checks in this section.")
        return
    order = {"fail": 0, "warn": 1, "na": 2, "info": 3, "pass": 4}
    checks = sorted(checks, key=lambda x: order.get(x["status"], 5))
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, t in enumerate(["Check", "Status", "What we found & what to do"]):
        _cell_text(table.rows[0].cells[i], [(t, {"size": 9, "bold": True})])
    _style_header_row(table)
    for c in checks:
        cells = table.add_row().cells
        _cell_text(cells[0], [(c["label"], {"size": 9, "bold": True})])
        _cell_text(cells[1], [(STATUS_TEXT.get(c["status"], c["status"]),
                               {"size": 8.5, "bold": True,
                                "color": STATUS_FG.get(c["status"], SLATE)})],
                   align=WD_ALIGN_PARAGRAPH.CENTER,
                   valign=WD_ALIGN_VERTICAL.CENTER,
                   shade=STATUS_SHADE.get(c["status"]))
        detail = c["detail"]
        if c.get("fix"):
            detail += "  → " + c["fix"]
        _cell_text(cells[2], [(detail, {"size": 9})])
    _set_widths(table, [2.0, 0.8, 4.1])


# ---- sections --------------------------------------------------------------
def _cover(doc, result, sc):
    _banner(doc, result)
    _spacer(doc, 12)
    # score hero — big number + verdict + native bar
    score = sc.get("score", 0)
    grade = sc.get("grade", "?")
    hexc = _score_hex(score)
    hero = doc.add_table(rows=1, cols=2)
    _no_table_borders(hero)
    _set_widths(hero, [2.15, PAGE_W - 2.15])
    left = hero.rows[0].cells[0]
    _shade(left, _score_shade(score))
    _cell_margins(left, top=200, bottom=200, left=120, right=120)
    left.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    lp = left.paragraphs[0]
    lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    lp.paragraph_format.space_after = Pt(0)
    _runs(lp, [(str(score), {"size": 46, "bold": True,
                             "color": _score_rgb(score)})])
    lp2 = left.add_paragraph()
    lp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(lp2, [("OUT OF 100", {"size": 8.5, "color": MUTED, "bold": True})])
    lp3 = left.add_paragraph()
    lp3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(lp3, [("GRADE %s" % grade, {"size": 15, "bold": True, "color": INK})])

    right = hero.rows[0].cells[1]
    _cell_margins(right, top=180, bottom=180, left=160, right=120)
    right.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    rp = right.paragraphs[0]
    rp.paragraph_format.space_after = Pt(6)
    _runs(rp, [("Overall SEO Health", {"size": 10, "color": MUTED,
                                       "bold": True})])
    rp2 = right.add_paragraph()
    rp2.paragraph_format.space_after = Pt(8)
    _runs(rp2, [(sc.get("label", ""), {"size": 15, "bold": True,
                                       "color": _score_rgb(score)})])
    _native_bar(right, score, hexc, width_in=PAGE_W - 2.15 - 0.55,
                height_tw=190)
    cap = right.add_paragraph()
    cap.paragraph_format.space_before = Pt(6)
    _runs(cap, [("Graded strictly against current best-practice standards.",
                 {"size": 9, "color": MUTED, "italic": True})])

    _spacer(doc, 12)
    pc = result.get("priority_counts") or {}
    total_issues = sum(pc.values())
    _kpi_tiles(doc, [
        ("%d" % total_issues, "ISSUES FOUND",
         BAD if pc.get("Critical") else (WARN if total_issues else GOOD)),
        ("%d" % (pc.get("Critical", 0) + pc.get("High", 0)), "HIGH PRIORITY",
         BAD if (pc.get("Critical", 0) + pc.get("High", 0)) else GOOD),
        ("%d" % result.get("pages_crawled", 0), "PAGES REVIEWED", BRAND_DK),
        (grade, "OVERALL GRADE", _score_rgb(score)),
    ])
    _spacer(doc, 10)
    tl = doc.add_paragraph()
    tl.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(tl, [("An SEO audit by ", {"size": 9, "color": MUTED}),
               ("MINDCOB", {"size": 9, "color": BRAND_DK, "bold": True}),
               ("  ·  Grow with us", {"size": 9, "color": MUTED})])


def _exec_section(doc, result, sc):
    _section_heading(doc, "Executive Summary")
    src = result.get("exec_summary_source")
    tag = "Written by our analyst" if src == "ai" else "Summary"
    _callout(doc, "What this means for your business  ·  %s" % tag,
             result.get("exec_summary", ""))
    _spacer(doc, 6)
    kf = result.get("key_findings") or {}
    if kf.get("weaknesses"):
        doc.add_heading("The 3 things costing you the most", level=2)
        for x in kf["weaknesses"][:3]:
            doc.add_paragraph(str(x), style="List Bullet")


def _scorecard_section(doc, result, sc):
    _section_heading(doc, "Your SEO Health Scorecard")
    doc.add_paragraph("Each area is scored out of 100 against best-practice "
                      "standards. Green is strong, amber needs attention, red "
                      "is urgent.")
    dims = sc.get("dimensions", [])
    if not dims:
        doc.add_paragraph("No category scores could be computed.")
        return
    table = doc.add_table(rows=0, cols=3)
    _no_table_borders(table)
    _set_widths(table, [2.2, 3.7, 1.0])
    for d in dims:
        cells = table.add_row().cells
        cells[0].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        cells[1].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        cells[2].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _cell_text(cells[0], [(d["label"], {"size": 10.5, "bold": True})],
                   valign=WD_ALIGN_VERTICAL.CENTER)
        cells[1].text = ""
        cells[1].paragraphs[0].add_run("").font.size = Pt(2)
        _native_bar(cells[1], d["score"], _score_hex(d["score"]),
                    width_in=3.5, height_tw=175)
        from_grade = _score_grade(d["score"])
        _cell_text(cells[2], [("%d " % d["score"], {"size": 12, "bold": True,
                               "color": _score_rgb(d["score"])}),
                              (from_grade, {"size": 9, "color": MUTED})],
                   valign=WD_ALIGN_VERTICAL.CENTER)
        _row_height(table.rows[-1], 360, "atLeast")
    if sc.get("penalties"):
        p = doc.add_paragraph()
        _runs(p, [("Score penalties applied: ", {"size": 9, "bold": True,
                                                 "color": BAD}),
                  ("; ".join(sc["penalties"]), {"size": 9, "color": SLATE})])


def _authority_traffic_section(doc, result):
    """Shows only values a real data provider measured. Unmeasured DA/PA/
    traffic/keywords (None) are never shown as numbers; when nothing was
    measured the whole section is omitted."""
    auth = result.get("authority") or {}
    traffic = result.get("traffic") or {}

    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    da = _num(auth.get("domain_authority"))
    pa = _num(auth.get("page_authority"))
    visits = _num(traffic.get("monthly_visits"))
    kws = _num(traffic.get("ranking_keywords"))
    tiles = []
    if da is not None:
        tiles.append((str(int(da)), "DOMAIN AUTHORITY (DA)", _score_rgb(da)))
    if pa is not None:
        tiles.append((str(int(pa)), "PAGE AUTHORITY (PA)", _score_rgb(pa)))
    if visits is not None:
        tiles.append(("{:,}".format(int(visits)), "MONTHLY VISITS", BRAND_DK))
    if kws is not None:
        tiles.append(("{:,}".format(int(kws)), "ORGANIC KEYWORDS", GOOD))
    geo_list = [g for g in (traffic.get("geo_distribution") or [])
                if _num(g.get("visits")) is not None]
    if not tiles and not geo_list:
        return
    _section_heading(doc, "Domain Authority & Organic Traffic Overview")
    doc.add_paragraph(
        "Third-party authority and traffic metrics measured for this domain. "
        "Metrics that were not measured are not shown."
    )
    if tiles:
        _kpi_tiles(doc, tiles)
        _spacer(doc, 6)

    p = doc.add_paragraph()
    _runs(p, [
        ("Source: ", {"size": 8.5, "bold": True, "color": SLATE}),
        (str(auth.get("source") or traffic.get("source") or "") + ". ", {"size": 8.5, "color": SLATE}),
        ("Note: DA and PA are third-party comparative metrics, not direct Google ranking factors.", {"size": 8.5, "italic": True, "color": MUTED})
    ])

    if geo_list:
        doc.add_heading("Location-Based Traffic Distribution", level=2)
        doc.add_paragraph("Organic traffic distribution by country and geographical region:")
        t = doc.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        for i, h in enumerate(["Country / Region", "Share", "Monthly Visits"]):
            _cell_text(t.rows[0].cells[i], [(h, {"size": 8.5, "bold": True})])
        _style_header_row(t)
        for g in geo_list:
            c = t.add_row().cells
            _cell_text(c[0], [(f"{g.get('country', '')} ({g.get('code', '')})", {"size": 8.5, "bold": True})])
            _cell_text(c[1], [(f"{g.get('pct')}%" if g.get("pct") is not None else "Not measured", {"size": 8.5})], align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_text(c[2], [("{:,}".format(int(g.get("visits"))), {"size": 8.5})], align=WD_ALIGN_PARAGRAPH.RIGHT)
        _set_widths(t, [3.0, 1.2, 2.0])
        _spacer(doc, 8)


def _key_findings_section(doc, result, sc):
    kf = result.get("key_findings") or {}
    strengths = kf.get("strengths") or []
    weaknesses = kf.get("weaknesses") or []
    opportunities = kf.get("opportunities") or []
    if not (strengths or weaknesses or opportunities):
        return
    _section_heading(doc, "Key Findings at a Glance")
    table = doc.add_table(rows=1, cols=3)
    _no_table_borders(table)
    _set_widths(table, [PAGE_W / 3] * 3)
    cols = [("STRENGTHS", strengths or ["—"], "E4F5EA", GOOD),
            ("WEAKNESSES", weaknesses or ["—"], "FBDDDB", BAD),
            ("OPPORTUNITIES", opportunities or ["—"], "E7EFFC", BRAND_DK)]
    for cell, (title, items, shade, color) in zip(table.rows[0].cells, cols):
        _shade(cell, shade)
        _cell_margins(cell, top=140, bottom=140, left=140, right=120)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(6)
        _runs(p, [(title, {"size": 10, "bold": True, "color": color})])
        for it in items[:5]:
            b = cell.add_paragraph()
            b.paragraph_format.space_after = Pt(4)
            _runs(b, [("•  ", {"size": 9, "color": color}),
                      (str(it), {"size": 9, "color": INK})])


def _action_section(doc, result):
    _section_heading(doc, "Prioritized Action Plan")
    doc.add_paragraph("Ranked by severity and business impact across a 6-tier implementation hierarchy. "
                      "Begin with search discovery and indexing, then proceed through technical stability, on-page optimization, local visibility, and domain authority.")
    pc = result.get("priority_counts") or {}
    _stacked_bar(doc, [("Critical", pc.get("Critical", 0), "C02A2E"),
                       ("High", pc.get("High", 0), "E8590C"),
                       ("Medium", pc.get("Medium", 0), "C98A00"),
                       ("Low", pc.get("Low", 0), "2E7DF2")])
    _spacer(doc, 6)
    items = result.get("action_items") or []
    if not items:
        _callout(doc, "Clean bill of health",
                 "No issues were found in the checks that ran.",
                 accent="147A40", shade="EFF8F1", title_color=GOOD)
        return

    tier_order = [
        (1, "Tier 1: Search Visibility & Indexability"),
        (2, "Tier 2: Technical SEO & Crawl Infrastructure"),
        (3, "Tier 3: On-Page Content, Meta & URL Optimization"),
        (4, "Tier 4: Local SEO & Google Maps Visibility"),
        (5, "Tier 5: Off-Page SEO & Backlink Authority"),
        (6, "Tier 6: Performance, Mobile UX & Security"),
    ]

    gain_col = {"High": GOOD, "Medium": WARN, "Low": MUTED}

    for tier_num, tier_label in tier_order:
        tier_items = [it for it in items if it.get("tier") == tier_num]
        if not tier_items:
            continue

        doc.add_heading(tier_label, level=2)
        table = doc.add_table(rows=1, cols=6)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(["Priority", "Issue & Affected URLs", "Why It Matters",
                               "Recommended Action", "Business Impact", "Gain"]):
            _cell_text(table.rows[0].cells[i], [(h, {"size": 8.5, "bold": True})])
        _style_header_row(table)

        for it in tier_items:
            cells = table.add_row().cells
            _cell_text(cells[0], [(it["priority"], {"size": 8, "bold": True,
                                   "color": PRIORITY_FG.get(it["priority"], INK)})],
                       align=WD_ALIGN_PARAGRAPH.CENTER,
                       valign=WD_ALIGN_VERTICAL.CENTER,
                       shade=PRIORITY_SHADE.get(it["priority"]))

            prob_runs = [(it["label"], {"size": 8.5, "bold": True}),
                         ("\n" + it["detail"], {"size": 8, "color": SLATE})]
            aff_urls = it.get("affected_urls") or []
            if aff_urls:
                prob_runs.append(("\nAffected: " + ", ".join(aff_urls[:2]), {"size": 7.5, "italic": True, "color": MUTED}))
            _cell_text(cells[1], prob_runs)

            _cell_text(cells[2], [(it.get("impact_text", ""), {"size": 8, "color": SLATE})])
            _cell_text(cells[3], [(it.get("fix", ""), {"size": 8, "color": BRAND_DK})])
            _cell_text(cells[4], [(it.get("business_impact", "Enhances organic visibility and CTR."), {"size": 8, "color": SLATE})])
            _cell_text(cells[5], [(it.get("gain", "Medium"), {"size": 8.5, "bold": True,
                                   "color": gain_col.get(it.get("gain"), MUTED)})],
                       align=WD_ALIGN_PARAGRAPH.CENTER,
                       valign=WD_ALIGN_VERTICAL.CENTER)

        _set_widths(table, [0.75, 1.85, 1.3, 1.4, 1.1, 0.5])
        _spacer(doc, 6)


def _deepdive_section(doc, result, sc):
    _section_heading(doc, "Detailed Findings")
    checks = result.get("checks", [])
    subscores = {d["key"]: d["score"] for d in sc.get("dimensions", [])}
    for key, label, _w in CATEGORIES:
        cat_checks = [c for c in checks if c["category"] == key]
        if not cat_checks:
            continue
        score = subscores.get(key)
        h = doc.add_heading(label, level=2)
        if score is not None:
            r = h.add_run("   %d/100" % score)
            r.font.size = Pt(11)
            r.font.color.rgb = _score_rgb(score)
        # plain-language "what this means" + a native bar
        if key in CATEGORY_CLIENT:
            _callout(doc, "What this means for you", CATEGORY_CLIENT[key],
                     accent=_score_hex(score), shade="F7F9FC",
                     title_color=SLATE)
        if score is not None:
            holder = doc.add_table(rows=1, cols=1)
            _no_table_borders(holder)
            _set_widths(holder, [PAGE_W])
            hc = holder.rows[0].cells[0]
            hc.text = ""
            hc.paragraphs[0].add_run("").font.size = Pt(2)
            _native_bar(hc, score, _score_hex(score), width_in=PAGE_W,
                        height_tw=150)
            _spacer(doc, 4)
        _checks_table(doc, cat_checks)
        _spacer(doc, 4)


def _competitor_section(doc, result, sc, comps):
    _section_heading(doc, "How You Compare")
    doc.add_paragraph("Your overall score against the competitors you named. "
                      "The gap is the opportunity — and the risk if you don't "
                      "close it.")
    target = {"domain": result.get("domain", "") + " (you)",
              "score": sc.get("score"), "grade": sc.get("grade"),
              "perf_mobile": ((result.get("psi") or {}).get("mobile") or {}).get(
                  "scores", {}).get("performance"),
              "https": (result.get("final_url", "") or "").startswith("https"),
              "title_len": (result.get("home_facts") or {}).get("title_len"),
              "meta_len": (result.get("home_facts") or {}).get("meta_desc_len"),
              "word_count": (result.get("home_facts") or {}).get("word_count"),
              "sd": bool(next(iter((result.get("structured") or {}).values()),
                              {}).get("present"))}
    # native comparison bars
    bars = doc.add_table(rows=0, cols=3)
    _no_table_borders(bars)
    _set_widths(bars, [2.1, 3.8, 1.0])
    for idx, r in enumerate([target] + comps):
        if r.get("error"):
            continue
        cells = bars.add_row().cells
        for c in cells:
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _cell_text(cells[0], [(r.get("domain", ""),
                               {"size": 9.5, "bold": idx == 0})],
                   valign=WD_ALIGN_VERTICAL.CENTER)
        cells[1].text = ""
        cells[1].paragraphs[0].add_run("").font.size = Pt(2)
        _native_bar(cells[1], r.get("score") or 0,
                    _score_hex(r.get("score")) if idx else "2E7DF2",
                    width_in=3.6, height_tw=170)
        _cell_text(cells[2], [(str(r.get("score", "—")), {"size": 11,
                               "bold": True, "color": _score_rgb(
                                   r.get("score"))})],
                   valign=WD_ALIGN_VERTICAL.CENTER)
        _row_height(bars.rows[-1], 340, "atLeast")
    _spacer(doc, 6)
    table = doc.add_table(rows=1, cols=8)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(["Site", "Overall", "Grade", "Mobile\nspeed",
                           "HTTPS", "Title", "Words", "Schema"]):
        _cell_text(table.rows[0].cells[i], [(h, {"size": 8, "bold": True})])
    _style_header_row(table)
    for idx, r in enumerate([target] + comps):
        cells = table.add_row().cells
        if r.get("error"):
            _cell_text(cells[0], [(r.get("domain", r.get("url", "")),
                                   {"size": 8, "bold": idx == 0})])
            _cell_text(cells[1], [(r["error"], {"size": 8, "color": BAD})])
            continue
        _cell_text(cells[0], [(r.get("domain", ""), {"size": 8,
                               "bold": idx == 0})])
        _cell_text(cells[1], [(str(r.get("score", "—")), {"size": 8.5,
                               "bold": True, "color": _score_rgb(
                                   r.get("score"))})],
                   align=WD_ALIGN_PARAGRAPH.CENTER,
                   shade=_score_shade(r.get("score")))
        for j, val in enumerate([r.get("grade", "—"),
                                 r.get("perf_mobile") if r.get("perf_mobile")
                                 is not None else "—",
                                 "Yes" if r.get("https") else "No",
                                 r.get("title_len", "—"),
                                 r.get("word_count", "—"),
                                 "Yes" if r.get("sd") else "No"], start=2):
            _cell_text(cells[j], [(str(val), {"size": 8})],
                       align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_widths(table, [2.0, 0.7, 0.6, 0.7, 0.6, 0.6, 0.7, 0.7])


def _appendix_section(doc, result):
    _section_heading(doc, "Technical Appendix")
    doc.add_paragraph("For your implementation team — the underlying data behind "
                      "this audit.")
    robots = result.get("robots") or {}
    sitemap = result.get("sitemap") or {}
    info = doc.add_table(rows=0, cols=2)
    info.style = "Light Grid Accent 1"
    for k, v in [("robots.txt", "present" if robots.get("present") else
                  "missing"),
                 ("Blanket crawl block", "yes" if robots.get("blocks_all") else
                  "no"),
                 ("Sitemap URLs", ("%s+" % sitemap.get("count", 0))
                  if sitemap.get("capped") else sitemap.get("count", 0)),
                 ("Pages crawled", result.get("pages_crawled", 0))]:
        cells = info.add_row().cells
        cells[0].text, cells[1].text = str(k), str(v)

    doc.add_heading("Pages reviewed", level=2)
    facts = result.get("all_facts", [])
    if facts:
        # The PA column appears only when Page Authority was really measured.
        show_pa = any(isinstance(f.get("page_authority"), (int, float))
                      for f in facts)
        heads = (["URL", "Status"] + (["PA"] if show_pa else []) +
                 ["Title", "Meta", "Words", "H1"])
        table = doc.add_table(rows=1, cols=len(heads))
        table.style = "Table Grid"
        for i, h in enumerate(heads):
            _cell_text(table.rows[0].cells[i], [(h, {"size": 8, "bold": True})])
        _style_header_row(table)
        for f in facts:
            cells = table.add_row().cells
            _cell_text(cells[0], [(f.get("final_url", ""), {"size": 8})])
            pa_v = f.get("page_authority")
            vals = ([f.get("status", "")] +
                    ([pa_v if pa_v is not None else "—"] if show_pa else []) +
                    [f.get("title_len", 0), f.get("meta_desc_len", 0),
                     f.get("word_count", 0), f.get("h1_count", 0)])
            for j, val in enumerate(vals, start=1):
                _cell_text(cells[j], [(str(val), {"size": 8})],
                           align=WD_ALIGN_PARAGRAPH.CENTER)
        _set_widths(table, [2.8, 0.6] + ([0.6] if show_pa else []) +
                    [0.7, 0.6, 0.6, 0.6])

    psi = result.get("psi") or {}
    rows = []
    for strat in ("mobile", "desktop"):
        r = psi.get(strat) or {}
        if r.get("ok"):
            s = r.get("scores", {})
            rows.append(("%s — Perf / SEO / A11y / Best-Practices" %
                         strat.title(),
                         "%s / %s / %s / %s" % (s.get("performance"),
                                                s.get("seo"),
                                                s.get("accessibility"),
                                                s.get("best_practices"))))
    if rows:
        doc.add_heading("PageSpeed Insights", level=2)
        t = doc.add_table(rows=0, cols=2)
        t.style = "Light Grid Accent 1"
        for k, v in rows:
            c = t.add_row().cells
            c[0].text, c[1].text = str(k), str(v)

    errs = result.get("errors") or []
    if errs:
        doc.add_heading("Notes", level=2)
        for e in errs:
            doc.add_paragraph(str(e), style="List Bullet")
    note = doc.add_paragraph()
    _runs(note, [("Methodology: direct crawl + Google PageSpeed Insights v5 "
                  "(keyless). Scored strictly against best-practice standards. "
                  "Generated %s by Mindcob SEO Audit Pro."
                  % result.get("generated_at", ""),
                  {"size": 8, "color": MUTED, "italic": True})])


def _score_grade(v):
    for cut, g in [(95, "A+"), (90, "A"), (86, "A-"), (82, "B+"), (78, "B"),
                   (74, "B-"), (69, "C+"), (64, "C"), (59, "C-"), (53, "D+"),
                   (46, "D"), (0, "F")]:
        if v >= cut:
            return g
    return "F"


# ============================================================================
# CLIENT-FACING EXECUTIVE DELIVERABLE GENERATORS
# Designed for clients: less technical jargon, no DA/PA/Monthly Traffic/Local,
# with structured 4-tier Recommended Action Plan with business impact.
# ============================================================================

def _client_header_footer(doc, result):
    sec = doc.sections[0]
    sec.top_margin = Inches(0.7)
    sec.bottom_margin = Inches(0.7)
    sec.left_margin = Inches(0.8)
    sec.right_margin = Inches(0.8)

    # Header
    hdr = sec.header.paragraphs[0]
    hdr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _runs(hdr, [
        ("MINDCOB SEO AUDIT PRO  ·  CRITICAL VULNERABILITY & RISK REPORT",
         {"size": 8, "color": MUTED, "bold": True})
    ])

    # Footer
    ftr = sec.footer.paragraphs[0]
    _runs(ftr, [
        ("Confidential SEO Audit  ·  Prepared for ", {"size": 8, "color": MUTED}),
        (str(result.get("domain", "")), {"size": 8, "color": SLATE, "bold": True}),
        ("  ·  Page ", {"size": 8, "color": MUTED})
    ])
    _page_field(ftr.add_run())


def _get_category_issues(checks, categories=None, check_ids=None):
    """Filter checks down to failed and warning items only (no passes)."""
    issues = []
    categories = [c.lower() for c in (categories or [])]
    check_ids = [cid.lower() for cid in (check_ids or [])]
    for c in (checks or []):
        if c.get("status") not in ("fail", "warn"):
            continue
        cat = (c.get("category") or "").lower()
        cid = (c.get("id") or "").lower()
        match = False
        if categories and cat in categories:
            match = True
        if check_ids and cid in check_ids:
            match = True
        if match:
            pri = c.get("severity", "medium").capitalize()
            if c.get("status") == "fail" and pri == "Medium":
                pri = "High"
            urls = c.get("where") or []
            issues.append({
                "id": cid,
                "label": c.get("label", "Issue"),
                "priority": pri,
                "detail": c.get("detail", ""),
                "fix": c.get("fix") or "Apply standard technical remediation to resolve.",
                "where": urls,
                "why": c.get("why") or "",
            })
    return issues


def _client_audit_table(doc, items, headers=None, widths=None):
    """Draws a clean, high-urgency defect table with explicit solutions."""
    if not items:
        return

    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    if not headers:
        headers = ["Priority Level", "Identified Issue / Defect", "Risk Detail", "Affected URLs", "Suggested Solution"]
    for i, h in enumerate(headers):
        _cell_text(table.rows[0].cells[i], [(h, {"size": 8.5, "bold": True})])
    _style_header_row(table)

    for it in items:
        cells = table.add_row().cells
        pri = it.get("priority", "Medium")

        # 1. Priority Level
        _cell_text(cells[0], [(pri.upper(), {"size": 8, "bold": True,
                               "color": PRIORITY_FG.get(pri, INK)})],
                   align=WD_ALIGN_PARAGRAPH.CENTER,
                   valign=WD_ALIGN_VERTICAL.CENTER,
                   shade=PRIORITY_SHADE.get(pri))

        # 2. Defect Label
        _cell_text(cells[1], [(it.get("label", "Issue"), {"size": 8.5, "bold": True})])

        # 3. Risk Detail
        _cell_text(cells[2], [(it.get("detail", ""), {"size": 8, "color": SLATE})])

        # 4. Affected URLs
        urls = it.get("where") or []
        if urls:
            url_str = "\n".join(urls[:2])
            if len(urls) > 2:
                url_str += f"\n+{len(urls) - 2} more pages"
        else:
            url_str = "Homepage / Site-wide"
        _cell_text(cells[3], [(url_str, {"size": 7.5, "color": MUTED})])

        # 5. Suggested Solution
        _cell_text(cells[4], [(it.get("fix", "Follow standard technical SEO guidelines."),
                               {"size": 8, "color": BRAND_DK})])

    if not widths:
        widths = [0.9, 1.7, 1.9, 1.0, 1.4]
    _set_widths(table, widths)
    _spacer(doc, 8)


def _client_cover(doc, result):
    # Top agency banner
    b = doc.add_table(rows=1, cols=2)
    _no_table_borders(b)
    _set_widths(b, [4.5, PAGE_W - 4.5])
    c0, c1 = b.rows[0].cells
    _cell_text(c0, [
        ("MINDCOB", {"size": 13, "bold": True, "color": BRAND}),
        ("  ·  SEO AUDIT PRO", {"size": 10, "color": MUTED, "bold": True}),
        ("\nCRITICAL VULNERABILITY & RISK REPORT", {"size": 8.5, "bold": True, "color": RGBColor(0xC0, 0x2A, 0x2E)}),
        ("\nCritical Website Vulnerability & Risk Brief", {"size": 9.5, "color": SLATE})
    ])
    _cell_text(c1, [
        (str(result.get("domain", "")), {"size": 11, "bold": True, "color": INK}),
        ("\nAudited: " + str(result.get("date") or result.get("generated_at", "")[:10]),
         {"size": 8.5, "color": MUTED}),
        (f"\nScope: {result.get('pages_crawled', 1)} Page(s) Audited", {"size": 8.5, "color": SLATE, "bold": True})
    ], align=WD_ALIGN_PARAGRAPH.RIGHT)
    _spacer(doc, 10)

    # Calculate defect totals
    checks = result.get("checks") or []
    pc = result.get("priority_counts") or {}
    total_issues = sum(pc.values())
    crit_count = pc.get("Critical", 0)
    high_count = pc.get("High", 0)
    urgent_count = crit_count + high_count

    sec_issues = _get_category_issues(checks, categories=["security"])
    sec_count = len(sec_issues)

    # Severe Threat Hero Box (NO SCORING)
    hero = doc.add_table(rows=1, cols=2)
    _no_table_borders(hero)
    _set_widths(hero, [2.35, PAGE_W - 2.35])

    left = hero.rows[0].cells[0]
    _shade(left, "991B1B")  # Deep Crimson Red
    _cell_margins(left, top=160, bottom=160, left=120, right=120)
    left.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    lp0 = left.paragraphs[0]
    lp0.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(lp0, [("THREAT LEVEL: HIGH RISK", {"size": 8.5, "bold": True, "color": WHITE})])
    lp1 = left.add_paragraph()
    lp1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    lp1.paragraph_format.space_before = Pt(4)
    lp1.paragraph_format.space_after = Pt(2)
    _runs(lp1, [(str(total_issues), {"size": 42, "bold": True, "color": WHITE})])
    lp2 = left.add_paragraph()
    lp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _runs(lp2, [("ACTIVE VULNERABILITIES", {"size": 8.5, "bold": True, "color": WHITE})])
    lp3 = left.add_paragraph()
    lp3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    lp3.paragraph_format.space_before = Pt(4)
    _runs(lp3, [("IMMEDIATE ACTION REQUIRED", {"size": 8, "color": RGBColor(0xFE, 0xCD, 0xCD), "bold": True})])

    right = hero.rows[0].cells[1]
    _shade(right, "FEF2F2")  # Soft alert background
    _left_accent(right, "DC2626", size=36)
    _cell_margins(right, top=150, bottom=150, left=150, right=120)
    right.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    rp = right.paragraphs[0]
    rp.paragraph_format.space_after = Pt(4)
    _runs(rp, [("SEARCH VISIBILITY & DATA INTEGRITY ALERT", {"size": 10.5, "color": BAD, "bold": True})])
    rp2 = right.add_paragraph()
    rp2.paragraph_format.space_after = Pt(4)
    _runs(rp2, [
        (f"A comprehensive diagnostic scan of {result.get('domain', 'your website')} identified active defects directly impairing organic search discovery, exposing browser trust warnings, and causing mobile visitor abandonment.\n\n"
         "Unresolved security misconfigurations and broken crawl paths quietly bleed traffic to competitors. "
         "This audit report isolates every detected failure and provides immediate remediation solutions.",
         {"size": 9.5, "color": INK})
    ])

    _spacer(doc, 10)

    # 4 Urgent Threat KPI Tiles (Only errors, no green reassurance)
    _kpi_tiles(doc, [
        ("%d" % crit_count, "CRITICAL ERRORS", BAD if crit_count > 0 else MUTED),
        ("%d" % high_count, "HIGH THREATS", RGBColor(0xEA, 0x58, 0x0C) if high_count > 0 else MUTED),
        ("%d" % sec_count, "SECURITY FLAWS", BAD if sec_count > 0 else MUTED),
        ("%d" % result.get("pages_crawled", 1), "PAGES AUDITED", INK),
    ])
    _spacer(doc, 8)


def _client_threat_overview(doc, result):
    _section_heading(doc, "Executive Risk & Threat Summary")
    doc.add_paragraph(
        "Identified critical vulnerabilities and technical defects impacting domain security, search indexation, and user conversion:"
    )

    threats = [
        ("1. Security Flaws & 'Not Secure' Browser Alerts",
         "Insecure SSL/TLS protocols, missing defensive headers (HSTS, CSP), and exposed sensitive files trigger full-screen browser warnings and data breach risks.",
         "C02A2E"),
        ("2. Search Crawl & Indexing Blockers",
         "Server errors, multi-hop redirect chains, and canonical or noindex misconfigurations cause high-converting landing pages to be de-indexed by Google.",
         "E8590C"),
        ("3. Mobile Conversion Friction & Core Web Vitals Drop-off",
         "Mobile render latency (LCP/CLS) and responsive viewport anomalies trigger mobile-first ranking penalties and user abandonment.",
         "D97706"),
    ]

    for title, desc, col in threats:
        t = doc.add_table(rows=1, cols=1)
        _no_table_borders(t)
        _set_widths(t, [PAGE_W])
        c = t.rows[0].cells[0]
        _shade(c, "F8FAFC")
        _left_accent(c, col, size=30)
        _cell_margins(c, top=80, bottom=80, left=120, right=100)
        p = c.paragraphs[0]
        _runs(p, [(title, {"size": 10, "bold": True, "color": INK}),
                  ("\n" + desc, {"size": 8.5, "color": SLATE})])
        _spacer(doc, 4)
    _spacer(doc, 6)


def _client_security_audit_section(doc, result):
    checks = result.get("checks") or []
    sec_issues = _get_category_issues(checks, categories=["security"])
    if not sec_issues:
        return False
    _section_heading(doc, "1. Website Security Audit")
    doc.add_paragraph(
        "Direct vulnerability scan isolating SSL/TLS handshake defects, missing defensive headers, cookie attribute flaws, sensitive file exposures, and email spoofing risks."
    )
    _client_audit_table(doc, sec_issues)
    return True


def _client_technical_audit_section(doc, result):
    checks = result.get("checks") or []
    tech_issues = _get_category_issues(checks, categories=["technical", "ux"],
                                       check_ids=["non_200_pages", "redirect_chains", "noindex_pages",
                                                  "broken_internal_links", "robots_present", "sitemap_present",
                                                  "sitemap_status", "sitemap_valid", "canonical_home",
                                                  "canonical_site", "ux_layout_stability", "ux_interactivity",
                                                  "ux_render_blocking", "ux_font_optimization"])
    if not tech_issues:
        return False
    _section_heading(doc, "2. Technical SEO Audit")
    doc.add_paragraph(
        "Technical SEO governs how efficiently search engine spiders can crawl, index, and render your website without hitting server errors or broken redirect loops. "
        "Below are all identified technical infrastructure defects requiring remediation."
    )
    _client_audit_table(doc, tech_issues)
    return True


def _client_onpage_audit_section(doc, result):
    checks = result.get("checks") or []
    onpage_issues = _get_category_issues(checks, categories=["onpage", "content"],
                                         check_ids=["title_tag", "title_len", "meta_desc", "meta_desc_len",
                                                    "headings_h1", "headings_structure", "content_depth",
                                                    "thin_content", "img_alt_tags", "img_oversized",
                                                    "orphan_pages", "internal_linking", "cta_presence",
                                                    "cta_working", "url_structure"])
    if not onpage_issues:
        return False
    _section_heading(doc, "3. On-Page SEO Audit")
    doc.add_paragraph(
        "On-Page SEO dictates how search engines evaluate your topical authority, keyword relevance, and page structure. "
        "Missing title tags, weak heading hierarchy, orphan pages, and unoptimized imagery directly suppress keyword ranking positions."
    )
    _client_audit_table(doc, onpage_issues)
    return True


def _client_mobile_audit_section(doc, result):
    checks = result.get("checks") or []
    mobile_issues = _get_category_issues(checks, categories=["mobile", "performance"],
                                         check_ids=["viewport", "mobile_friendly", "mobile_psi", "mobile_lcp",
                                                    "mobile_cls", "mobile_touch_targets", "responsive_design"])
    if not mobile_issues:
        return False
    _section_heading(doc, "4. Mobile Visibility and Usability")
    doc.add_paragraph(
        "Google evaluates your site through smartphone crawlers. Mobile responsiveness defects, small tap targets, and layout shift latency directly penalize search positions and hurt conversion rates."
    )
    _client_audit_table(doc, mobile_issues)
    return True


def _client_offpage_audit_section(doc, result):
    checks = result.get("checks") or []
    offpage_issues = _get_category_issues(checks, categories=["offpage"],
                                          check_ids=["offpage_domain_authority", "offpage_page_authority",
                                                     "offpage_outbound_links", "offpage_toxic_risk"])
    if not offpage_issues:
        return False
    _section_heading(doc, "5. Off-Page SEO and Backlink Audit")
    doc.add_paragraph(
        "Assesses domain authority equity, referring link distribution, and distinguishes harmful or spammy outbound links from legitimate citations. "
        "Uncontrolled outbound link leakage or toxic references can dilute domain trust."
    )
    _client_audit_table(doc, offpage_issues)
    return True


def _client_action_plan_section(doc, result):
    items = result.get("action_items") or []

    # 6-Tier Recommended Priority Order as specified by the client guide
    tier_defs = [
        (1, "Priority Tier 1: Search Visibility and Indexability",
         "First, ensure that important website pages can be crawled, indexed, and discovered in search engines. Resolve any major visibility or indexing problems.",
         "C02A2E"),
        (2, "Priority Tier 2: Technical SEO",
         "Fix critical crawl errors, server errors, redirect issues, sitemap problems, and other technical barriers.",
         "E8590C"),
        (3, "Priority Tier 3: On-Page SEO",
         "Improve content quality, titles, meta descriptions, headings, internal linking, URLs, and image optimization.",
         "2E7DF2"),
        (4, "Priority Tier 4: Local SEO",
         "Optimize Google Business Profile, local landing pages, citations, and location-based keyword visibility.",
         "7C3AED"),
        (5, "Priority Tier 5: Off-Page SEO",
         "Improve backlink quality, build relevant links, and strengthen the website's authority and reputation.",
         "059669"),
        (6, "Priority Tier 6: Performance, Mobile UX, and Security",
         "Address any remaining issues affecting website usability, speed, mobile experience, or security.",
         "D97706"),
    ]

    client_buckets = {1: [], 2: [], 3: [], 4: [], 5: [], 6: []}

    for it in items:
        cat = (it.get("category") or "").lower()
        cid = (it.get("id") or "").lower()
        raw_tier = it.get("tier")

        target_tier = 2
        if raw_tier in (1, 2, 3, 4, 5, 6):
            target_tier = raw_tier
        elif cid in ("robots_present", "noindex_pages", "sitemap_present", "sitemap_status",
                     "sitemap_valid", "canonical_self", "canonical_match", "redirect_chains",
                     "broken_internal_links", "indexability", "crawlability", "meta_robots"):
            target_tier = 1
        elif cat == "technical":
            target_tier = 2
        elif cat in ("onpage", "content"):
            target_tier = 3
        elif cat == "local":
            target_tier = 4
        elif cat == "offpage":
            target_tier = 5
        elif cat in ("performance", "mobile", "security", "accessibility", "ux"):
            target_tier = 6

        # Business impact phrasing
        biz_impact = it.get("business_impact") or ""
        if not biz_impact or biz_impact.startswith("Ranked") or biz_impact.startswith("Ensures"):
            if target_tier == 1:
                biz_impact = "Ensures priority revenue pages are discovered and indexed by Google crawlers."
            elif target_tier == 2:
                biz_impact = "Eliminates server dropouts and crawl latency that quietly suppress keyword rankings."
            elif target_tier == 3:
                biz_impact = "Directly improves organic CTR on Google search results and lifts target keyword positions."
            elif target_tier == 4:
                biz_impact = "Strengthens Google Maps 3-pack local visibility and drives inbound calls."
            elif target_tier == 5:
                biz_impact = "Elevates domain trust equity and prevents toxic link association penalties."
            elif target_tier == 6:
                biz_impact = "Prevents scary browser 'Not Secure' blocks and cuts mobile visitor drop-off."

        urls = it.get("where") or it.get("affected_urls") or []
        client_buckets[target_tier].append({
            "priority": it.get("priority", "Medium"),
            "label": it.get("label", "Issue"),
            "detail": it.get("detail", ""),
            "fix": it.get("fix") or "Follow standard SEO guidelines to resolve.",
            "business_impact": biz_impact,
            "urls": urls,
        })

    total_client_items = sum(len(b) for b in client_buckets.values())
    if total_client_items == 0:
        return False

    _section_heading(doc, "6. Prioritized SEO Action Plan")
    doc.add_paragraph(
        "All action items are organized according to our recommended priority order to ensure immediate risk mitigation and maximum organic visibility return on investment. "
        "Each action item details its priority level, issue description, affected URLs, suggested solution, and expected business impact."
    )

    for t_num, t_title, t_desc, t_col in tier_defs:
        b_items = client_buckets[t_num]
        if not b_items:
            continue

        doc.add_heading(t_title, level=2)
        doc.add_paragraph(t_desc)

        table = doc.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        headers = ["Priority Level", "Issue Description", "Affected URLs", "Suggested Solution", "Expected Business Impact"]
        for i, h in enumerate(headers):
            _cell_text(table.rows[0].cells[i], [(h, {"size": 8.5, "bold": True})])
        _style_header_row(table)

        for item in b_items:
            cells = table.add_row().cells

            # 1. Priority Level
            _cell_text(cells[0], [(item["priority"].upper(), {"size": 8, "bold": True,
                                   "color": PRIORITY_FG.get(item["priority"], INK)})],
                       align=WD_ALIGN_PARAGRAPH.CENTER,
                       valign=WD_ALIGN_VERTICAL.CENTER,
                       shade=PRIORITY_SHADE.get(item["priority"]))

            # 2. Issue Description
            desc_chunks = [(item["label"], {"size": 8.5, "bold": True})]
            if item["detail"]:
                desc_chunks.append(("\n" + item["detail"], {"size": 8, "color": SLATE}))
            _cell_text(cells[1], desc_chunks)

            # 3. Affected URLs
            urls = item["urls"]
            if urls:
                url_str = "\n".join(urls[:2])
                if len(urls) > 2:
                    url_str += f"\n+{len(urls) - 2} more pages"
            else:
                url_str = "Homepage / Site-wide"
            _cell_text(cells[2], [(url_str, {"size": 7.5, "color": MUTED})])

            # 4. Suggested Solution
            _cell_text(cells[3], [(item["fix"], {"size": 8, "color": BRAND_DK})])

            # 5. Expected Business Impact
            _cell_text(cells[4], [(item["business_impact"], {"size": 8, "color": BRAND_DK})])

        _set_widths(table, [0.8, 1.8, 1.1, 1.7, 1.5])
        _spacer(doc, 8)

    return True


def _client_roadmap_section(doc, result):
    _section_heading(doc, "7. Implementation Roadmap & Next Steps")
    doc.add_paragraph(
        "To maximize search engine visibility and revenue recovery without exhausting internal resources, we recommend executing recommendations across a structured 3-phase timeline:"
    )

    phases = [
        ("Phase 1: Immediate Triage (Week 1)",
         "Resolve search visibility blockers, robots.txt, XML sitemap errors, critical 4xx/5xx crawl failures, and SSL/security response headers.",
         BAD),
        ("Phase 2: Core Optimization (Month 1)",
         "Optimize target title tags, compelling meta descriptions, H1-H6 heading hierarchy, image compression, and internal linking.",
         WARN),
        ("Phase 3: Experience & Usability (Ongoing)",
         "Refine mobile PageSpeed scores, satisfy Google Core Web Vitals thresholds, and establish proactive search monitoring.",
         BRAND_DK),
    ]

    t = doc.add_table(rows=1, cols=3)
    _no_table_borders(t)
    _set_widths(t, [PAGE_W / 3] * 3)

    shades = [("FBDDDB", BAD), ("FDF1CF", WARN), ("EBF2FE", BRAND_DK)]
    for cell, (title, text, col), (shade, _) in zip(t.rows[0].cells, phases, shades):
        _shade(cell, shade)
        _cell_margins(cell, top=100, bottom=100, left=100, right=100)
        p = cell.paragraphs[0]
        _runs(p, [(title, {"size": 9.5, "bold": True, "color": col}),
                  ("\n\n" + text, {"size": 8.5, "color": SLATE})])
    _spacer(doc, 10)

    _callout(doc, "Partner with Mindcob for Turnkey Remediation",
             "Mindcob specializes in end-to-end technical, on-page, and security SEO remediation. "
             "We can deploy these exact fixes directly with your engineering or content team to rapidly recover suppressed search rankings and traffic.",
             accent="2E7DF2", shade="F3F7FD", title_color=BRAND_DK)


# ---- Entry Points ----------------------------------------------------------

def generate_docx_report(result, path, charts=None):
    """Full comprehensive technical agency report."""
    doc = Document()
    _base_styles(doc)
    _header_footer(doc, result)
    sc = result.get("score") or {}

    _cover(doc, result, sc)
    doc.add_page_break()
    _toc(doc)
    doc.add_page_break()
    _exec_section(doc, result, sc)
    _scorecard_section(doc, result, sc)
    _authority_traffic_section(doc, result)
    _key_findings_section(doc, result, sc)
    doc.add_page_break()
    _action_section(doc, result)
    doc.add_page_break()
    _deepdive_section(doc, result, sc)
    if result.get("competitors"):
        doc.add_page_break()
        _competitor_section(doc, result, sc, result["competitors"])
    doc.add_page_break()
    _appendix_section(doc, result)

    doc.save(path)
    return path


def generate_client_docx_report(result, path, charts=None):
    """
    Executive Vulnerability & Risk Report:
    - NO SCORING (No overall score numbers, no grades, no progress meters)
    - High-urgency Threat Assessment Hero
    - Strictly focuses on ERRORS, WARNINGS, and CONCRETE SOLUTIONS
    - Structured according to the official client audit guide:
        1. Website Security Audit
        2. Technical SEO Audit
        3. On-Page SEO Audit
        4. Mobile Visibility and Usability
        5. Off-Page SEO and Backlink Audit
        6. Prioritized SEO Action Plan (in strict 6-tier recommended priority order)
        7. 3-Phase Implementation Roadmap
    """
    doc = Document()
    _base_styles(doc)
    _client_header_footer(doc, result)

    _client_cover(doc, result)
    doc.add_page_break()
    _client_threat_overview(doc, result)
    if _client_security_audit_section(doc, result):
        _spacer(doc, 10)
    if _client_technical_audit_section(doc, result):
        _spacer(doc, 10)
    if _client_onpage_audit_section(doc, result):
        _spacer(doc, 10)
    if _client_mobile_audit_section(doc, result):
        _spacer(doc, 10)
    if _client_offpage_audit_section(doc, result):
        _spacer(doc, 10)
    if _client_action_plan_section(doc, result):
        doc.add_page_break()
    _client_roadmap_section(doc, result)

    doc.save(path)
    return path

