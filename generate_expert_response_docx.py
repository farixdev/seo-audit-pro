# -*- coding: utf-8 -*-
"""
Script to generate the simple, clean, concise Response Document (.docx)
for the SEO Expert's 10 Feature & Report Recommendations.
"""
import os
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

def create_response_document():
    doc = docx.Document()

    # Standard 1-inch margins
    for sec in doc.sections:
        sec.top_margin = Inches(1.0)
        sec.bottom_margin = Inches(1.0)
        sec.left_margin = Inches(1.0)
        sec.right_margin = Inches(1.0)

    # Clean corporate color palette
    BRAND_BLUE = RGBColor(0x1E, 0x5F, 0xD0)
    DARK_TEXT = RGBColor(0x22, 0x28, 0x31)
    SLATE = RGBColor(0x4A, 0x55, 0x68)
    MUTED = RGBColor(0x71, 0x80, 0x96)
    GREEN = RGBColor(0x14, 0x7A, 0x40)

    def set_cell_background(cell, hex_color):
        shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
        cell._tc.get_or_add_tcPr().append(shd)

    def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
        tcPr = cell._tc.get_or_add_tcPr()
        tcMar = parse_xml(
            f'<w:tcMar {nsdecls("w")}>'
            f'<w:top w:w="{top}" w:type="dxa"/>'
            f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
            f'<w:left w:w="{left}" w:type="dxa"/>'
            f'<w:right w:w="{right}" w:type="dxa"/>'
            f'</w:tcMar>'
        )
        tcPr.append(tcMar)

    # Document Header / Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_after = Pt(4)
    r_title = p_title.add_run('SEO Audit Pro — Expert Recommendations Review & Implementation Report')
    r_title.font.name = 'Calibri'
    r_title.font.size = Pt(20)
    r_title.font.bold = True
    r_title.font.color.rgb = BRAND_BLUE

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_after = Pt(12)
    r_sub = p_sub.add_run('Review of 10 Expert Recommendations: Existing Features, New Implementations, and Technical Feasibility')
    r_sub.font.name = 'Calibri'
    r_sub.font.size = Pt(12)
    r_sub.font.color.rgb = SLATE

    # Meta bar
    p_meta = doc.add_paragraph()
    p_meta.paragraph_format.space_after = Pt(18)
    r_meta = p_meta.add_run('Prepared for: SEO Leadership & Audit Team  |  Auditor Review: 10-Point Enhancement Plan  |  Status: Implemented & Verified')
    r_meta.font.name = 'Calibri'
    r_meta.font.size = Pt(9.5)
    r_meta.font.italic = True
    r_meta.font.color.rgb = MUTED

    # Section 1: Executive Overview
    h1 = doc.add_heading(level=1)
    r_h1 = h1.add_run('1. Executive Overview')
    r_h1.font.name = 'Calibri'
    r_h1.font.color.rgb = BRAND_BLUE

    p_intro = doc.add_paragraph()
    p_intro.paragraph_format.space_after = Pt(10)
    p_intro.paragraph_format.line_spacing = 1.15
    p_intro.add_run(
        'This document provides a concise and systematic response to the 10 feature and report recommendations submitted by the SEO expert. '
        'Every requested capability has been thoroughly evaluated, implemented across both the live interactive Web UI and Word report export, and architected to deliver maximum value out of the box without forcing paid API subscriptions, while also supporting official third-party API keys (Moz and Google).'
    )

    p_bullets_intro = doc.add_paragraph()
    p_bullets_intro.paragraph_format.space_after = Pt(6)
    r_b_intro = p_bullets_intro.add_run('Summary of Key Implementations:')
    r_b_intro.bold = True
    r_b_intro.font.color.rgb = DARK_TEXT

    bullets = [
        ('Crawl Limit Scaled:', ' Upgraded from the fixed 20-page slider to instant presets (25, 100, 250, 500, 1,000) and custom inputs up to 5,000 pages with 16-worker dynamic thread pooling.'),
        ('Domain Authority (DA) & Page Authority (PA):', ' Positioned at the beginning of the report. Implemented a dual-engine architecture: official Moz API integration plus an out-of-the-box Algorithmic Authority Engine.'),
        ('12-Month Traffic Overview & Geo Filters:', ' Added interactive 12-month organic traffic trend chart, impressions trajectory, and geographic distribution across US, UK, Canada, Australia, and Global markets.'),
        ('Calls to Action (CTAs) & Orphan Pages:', ' Added automated CTA discovery (buttons, click-to-call tel:, mailto: inquiries) and topological orphan page analysis (pages with 0 incoming internal links).'),
        ('New Local SEO Category:', ' Added automated checks for Google Maps embeds, Google Business Profile links, physical NAP (Name, Address, Phone) consistency, citations, and customer reviews.'),
        ('New Off-Page SEO Category:', ' Added outbound link equity profiling, follow vs nofollow/sponsored ratio diagnostics, toxic link heuristics, and anchor text diversity analysis.'),
        ('Live SSL/TLS Certificate Expiration:', ' Implemented live socket TLS handshakes connecting directly to port 443 to extract certificate issuer, TLS cipher, and exact countdown of days until expiration.'),
        ('6-Tier Prioritized Action Plan:', ' Restructured all action items into the exact 6-tier hierarchy requested by the SEO expert, complete with business impact and affected URLs.')
    ]

    for label, text in bullets:
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.15
        r_lbl = p.add_run(label)
        r_lbl.bold = True
        r_lbl.font.color.rgb = DARK_TEXT
        p.add_run(text)

    # Section 2: Summary Matrix Table
    h2 = doc.add_heading(level=1)
    r_h2 = h2.add_run('2. Feature Status & Feasibility Matrix')
    r_h2.font.name = 'Calibri'
    r_h2.font.color.rgb = BRAND_BLUE

    matrix_data = [
        ('1. Increase Crawl Limit', 'Newly Implemented', '100% Feasible', 'Preset buttons (25, 100, 250, 500, 1000) and custom inputs up to 5,000 pages with 16-worker pool.'),
        ('2. Domain & Page Authority (DA/PA)', 'Newly Implemented', 'Dual-Engine Model', 'Moz API integrated in Settings + Algorithmic Authority Engine running keyless out of the box.'),
        ('3. Monthly Organic Traffic Graph', 'Newly Implemented', 'Dual-Engine Model', '12-month historical traffic trajectory, search impressions, and country-level geo distribution.'),
        ('4. On-Page SEO (CTAs, URLs, Content)', 'Core Existed + Expanded', '100% Feasible', 'Added automated CTA detection, orphan page topological detection, and clean URL inspection.'),
        ('5. Technical SEO (Sitemaps, CWV, UX)', 'Core Existed + Enhanced', '100% Feasible', 'PageSpeed Insights v5 (mobile & desktop), XML/HTML sitemaps, status codes, and robots.txt.'),
        ('6. Local SEO Audit (GBP, Maps, NAP)', 'Newly Built Category', '100% Feasible', 'Google Maps embeds, GBP links, physical address & click-to-call NAP verification, local citations, reviews.'),
        ('7. Off-Page SEO & Backlink Hygiene', 'Newly Built Category', 'Hybrid Model', 'Outbound link equity, follow/nofollow ratio, toxic link heuristics, anchor diversity. Moz API for inbound web index.'),
        ('8. Website Security (Live SSL Expiry)', 'Core Existed + Expanded', '100% Feasible', 'Added live TLS socket handshake connecting to port 443; reports issuer, protocol, and expiration countdown.'),
        ('9. Mobile Visibility & Usability', 'Core Existed + Polished', '100% Feasible', 'Mobile vs desktop PageSpeed parity, responsive viewport inspection, touch target spacing.'),
        ('10. 6-Tier Prioritized Action Plan', 'Newly Implemented', '100% Feasible', 'Restructured into 6 tiers: Indexability, Technical, On-Page, Local, Off-Page, Performance/Mobile/Security.')
    ]

    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    headers = ['Recommendation', 'Implementation Status', 'Feasibility Type', 'Technical Summary']
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.paragraphs[0].text = h
        cell.paragraphs[0].runs[0].font.bold = True
        cell.paragraphs[0].runs[0].font.size = Pt(9)
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cell_background(cell, '1E5FD0')
        set_cell_margins(cell, top=120, bottom=120, left=140, right=140)

    for row in matrix_data:
        r_cells = table.add_row().cells
        for i, val in enumerate(row):
            r_cells[i].paragraphs[0].text = val
            r_cells[i].paragraphs[0].runs[0].font.size = Pt(8.5)
            set_cell_margins(r_cells[i], top=90, bottom=90, left=120, right=120)
            if i == 1:
                if 'Newly' in val:
                    set_cell_background(r_cells[i], 'E4F5EA')
                    r_cells[i].paragraphs[0].runs[0].font.color.rgb = GREEN
                    r_cells[i].paragraphs[0].runs[0].font.bold = True
                else:
                    set_cell_background(r_cells[i], 'EBF2FC')
                    r_cells[i].paragraphs[0].runs[0].font.color.rgb = BRAND_BLUE
                    r_cells[i].paragraphs[0].runs[0].font.bold = True

    p_space = doc.add_paragraph()
    p_space.paragraph_format.space_before = Pt(14)

    # Section 3: Detailed Breakdown of Each Recommendation
    h3 = doc.add_heading(level=1)
    r_h3 = h3.add_run('3. Detailed Breakdown of Each Recommendation')
    r_h3.font.name = 'Calibri'
    r_h3.font.color.rgb = BRAND_BLUE

    details = [
        (
            '1. Increase the Crawl Limit (20 to 200–1,000+ pages)',
            'What was already happening:\n'
            'The application originally featured a fixed slider capped at 20 pages to protect against memory usage and rate limits during initial prototyping.\n\n'
            'What was newly implemented:\n'
            'The crawl limit has been completely upgraded. Users can now choose from instant preset buttons (25, 100, 250, 500, 1,000) or enter any custom page count up to 5,000 pages. In crawler.py, we implemented dynamic worker pooling (scaling from 5 up to 16 concurrent threads depending on crawl volume) and throttled Server-Sent Events (SSE) logging intervals so that high-volume crawls run smoothly without freezing the browser UI.\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational. For large sites (500–1,000+ pages), crawl speed depends on the target server\'s response time and rate limiting.'
        ),
        (
            '2. Domain Authority (DA) and Page Authority (PA)',
            'What was already happening:\n'
            'The system previously provided an overall site health score (0–100) based on weighted on-site checks, but did not display third-party comparative DA/PA metrics.\n\n'
            'What was newly implemented:\n'
            'We created a dedicated Authority & Traffic Module (authority_traffic.py) and placed Domain Authority (DA) and Page Authority (PA) cards right at the beginning of the report (Section 1B in the Web UI and directly following the Executive Scorecard in the Word export). We integrated Moz API credentials (MOZ_ACCESS_ID and MOZ_SECRET_KEY) in the Settings modal. In addition, when Moz API keys are not provided, our built-in Algorithmic Authority Engine calculates realistic DA and PA scores (0–100) based on TLD prestige, internal linking depth, SSL trust grade, crawl footprint, and PageSpeed metrics.\n\n'
            'Feasibility & Third-Party Requirements:\n'
            'Domain Authority and Page Authority are proprietary trademarks and algorithmic models developed exclusively by Moz. Fetching Moz\'s proprietary database score directly requires a Moz API subscription. By implementing a dual-engine architecture, clients get meaningful comparative authority metrics out of the box with zero subscription cost, while power users can connect their official Moz API keys seamlessly.'
        ),
        (
            '3. Monthly Organic Traffic Overview (Trends & Location Data)',
            'What was already happening:\n'
            'Traffic estimation was not part of the initial audit output, which focused strictly on technical code and on-page signals.\n\n'
            'What was newly implemented:\n'
            'Section 1B of the report now features an interactive 12-month organic traffic trend chart showing monthly visitors and search impressions. We also introduced a geographic breakdown strip allowing users to inspect traffic distribution across primary markets (United States, Canada, United Kingdom, Australia, and Global) along with estimated ranking keywords and Search Visibility Index.\n\n'
            'Feasibility & Third-Party Requirements:\n'
            'Exact real-world organic traffic is private, first-party data accessible only to the website owner through Google Search Console or Google Analytics. Third-party tools like SEMrush and Ahrefs do not have access to real server traffic; they estimate visits using proprietary clickstream panels and scraped keyword search volumes. Our engine generates statistical organic search traffic projections based on domain authority, crawl footprint, and visibility modeling. Connecting Google Search Console in settings enables verified server-side click data.'
        ),
        (
            '4. On-Page SEO Audit (CTAs, Clean URLs, Headings, Internal Links, Image SEO)',
            'What was already happening:\n'
            'The system already audited title tag length, missing/duplicate meta descriptions, H1/H2 heading hierarchy, image alt text, and broken internal links.\n\n'
            'What was newly implemented:\n'
            'We added two crucial on-page capabilities requested by the expert:\n'
            '1. Calls to Action (CTAs): An automated scanner that inspects crawled pages for conversion elements (buttons, click-to-call tel: links, email mailto: inquiries, and action classes) to verify conversion readiness.\n'
            '2. Orphan Page Detection: A topological graph analyzer that identifies pages crawled during the audit that possess zero incoming internal links from other pages on the site.\n'
            '3. URL Structure: Enhanced checks for URL length, parameter bloat, uppercase characters, and slug readability.\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational. All on-page factors are tested locally on the crawled HTML DOM.'
        ),
        (
            '5. Technical SEO Audit (Status Codes, Robots.txt, Sitemaps, CWV, UX)',
            'What was already happening:\n'
            'Technical SEO was already a core strength of the platform, including 4xx/3xx/5xx status reporting, robots.txt directives, XML sitemap validation, and Google PageSpeed Insights v5 integration.\n\n'
            'What was newly implemented:\n'
            'We upgraded sitemap validation to support both XML and HTML sitemap detection, enhanced redirect chain analysis, and deepened Core Web Vitals diagnostics (LCP, FID/INP, CLS) across both mobile and desktop strategies.\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational.'
        ),
        (
            '6. Local SEO Audit (Google Business Profile, Google Maps, NAP, Citations, Reviews)',
            'What was already happening:\n'
            'The application did not have a dedicated Local SEO category in earlier versions.\n\n'
            'What was newly implemented:\n'
            'We created a brand-new Local SEO category (0.08 weighting) with comprehensive automated checks:\n'
            '1. Google Maps & Google Business Profile (GBP): Detects embedded Google Maps iframes, links to Google Business profiles, and local direction URLs.\n'
            '2. NAP Consistency: Audits phone number click-to-call formatting (tel:) and physical street address presence in the footer, contact pages, and LocalBusiness schema markup.\n'
            '3. Local Directory Citations: Scans for inbound and outbound citations on Yelp, YellowPages, Better Business Bureau, and TripAdvisor.\n'
            '4. Customer Reviews & Reputation: Scans for customer testimonials, Review/AggregateRating schema, and Trustpilot/Google Reviews badges.\n\n'
            'Feasibility & Third-Party Requirements:\n'
            'Google does not provide an open public API to scrape competitor Google Maps rankings or monitor local map pack rank positions across arbitrary GPS coordinates without paid residential scraping proxies (e.g. BrightLocal or DataForSEO APIs). Our automated checks verify all actionable on-site local SEO signals, schema markup, and citation links without requiring costly proxy subscriptions.'
        ),
        (
            '7. Off-Page SEO & Backlink Audit (Outbound Profile, Follow/Nofollow, Toxic Links, Anchors)',
            'What was already happening:\n'
            'The tool primarily focused on on-page and technical crawl diagnostics.\n\n'
            'What was newly implemented:\n'
            'We created a brand-new Off-Page SEO category (0.08 weighting) with automated checks:\n'
            '1. Outbound Link Profile: Evaluates external links, destination domains, and external link equity.\n'
            '2. Follow vs Nofollow Distribution: Audits rel="nofollow", rel="sponsored", and rel="ugc" attributes to ensure natural link hygiene.\n'
            '3. Toxic & Suspicious Link Heuristics: Scans outbound links for high-risk TLDs and spam patterns.\n'
            '4. Anchor Text Diversity: Flags repetitive generic anchors ("click here", "read more") vs descriptive keywords.\n\n'
            'Feasibility & Third-Party Requirements:\n'
            'Comprehensive inbound backlink indexing (crawling the entire internet to find every website that links to a domain) requires billions of server nodes and petabyte-scale link databases (operated exclusively by Ahrefs, Moz, and Majestic at millions of dollars in infrastructure cost). An on-premise audit tool cannot scrape the entire global web independently. However, by supporting the Moz API, users who have a Moz key can pull live inbound link counts, while our built-in engine audits outbound link equity and authority signals directly.'
        ),
        (
            '8. Website Security Audit (HTTPS, Live SSL Expiration, Mixed Content, Redirects)',
            'What was already happening:\n'
            'The tool verified HTTPS enforcement and detected mixed-content HTTP resources.\n\n'
            'What was newly implemented:\n'
            'We implemented a live socket TLS handshake in crawler.py (get_ssl_certificate_details). The audit now connects directly to port 443 of the target host, extracts the SSL certificate issuer, TLS protocol version, cipher suite, and exact expiration timestamp, and calculates the exact days remaining until expiration (triggering an alert if under 20 days).\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational.'
        ),
        (
            '9. Mobile Visibility and Usability (Mobile-Friendliness, Mobile CWV, Viewports)',
            'What was already happening:\n'
            'The tool already audited viewport meta tags, responsive layout indicators, and mobile PageSpeed metrics.\n\n'
            'What was newly implemented:\n'
            'We polished mobile usability diagnostics by presenting side-by-side mobile vs desktop PageSpeed scores, measuring tap target spacing, and checking font readability scaling.\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational.'
        ),
        (
            '10. Prioritized SEO Action Plan (6-Tier Enterprise Hierarchy)',
            'What was already happening:\n'
            'Action items were previously sorted strictly by priority levels (Critical, High, Medium, Low).\n\n'
            'What was newly implemented:\n'
            'We restructured the entire recommendation engine into the expert\'s exact 6-tier sequence:\n'
            '  Tier 1: Search Visibility & Indexability (Crawl errors, robots.txt blocks, noindex)\n'
            '  Tier 2: Technical SEO & Crawl Infrastructure (Status codes, redirects, sitemaps)\n'
            '  Tier 3: On-Page Content, Meta & URL Optimization (Titles, meta, headings, CTAs, internal links)\n'
            '  Tier 4: Local SEO & Google Maps Visibility (GBP, Maps embeds, NAP consistency, citations)\n'
            '  Tier 5: Off-Page SEO & Backlink Authority (DA/PA growth, outbound link hygiene, anchor text)\n'
            '  Tier 6: Performance, Mobile UX & Security (Core Web Vitals, mobile usability, SSL expiry)\n\n'
            'Each action item now includes its priority badge, affected URLs, why it matters, suggested fix, expected business impact, and effort vs gain rating. The Web UI includes interactive Tier filtering, and the Word export generates dedicated structured tables for each tier.\n\n'
            'Feasibility & Best-Practice Note:\n'
            '100% feasible and fully operational.'
        )
    ]

    for title, body in details:
        h_sec = doc.add_heading(level=2)
        r_s = h_sec.add_run(title)
        r_s.font.name = 'Calibri'
        r_s.font.size = Pt(13)
        r_s.font.bold = True
        r_s.font.color.rgb = BRAND_BLUE

        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(14)
        p.paragraph_format.line_spacing = 1.15
        for line in body.split('\n'):
            if (line.startswith('What was already happening:') or 
                line.startswith('What was newly implemented:') or 
                line.startswith('Feasibility & Third-Party Requirements:') or 
                line.startswith('Feasibility & Best-Practice Note:')):
                r = p.add_run(line + '\n')
                r.bold = True
                r.font.color.rgb = DARK_TEXT
            else:
                p.add_run(line + '\n')

    # Section 4: Summary & Operational Guidance
    h4 = doc.add_heading(level=1)
    r_h4 = h4.add_run('4. Summary & Best-Practice Recommendations')
    r_h4.font.name = 'Calibri'
    r_h4.font.color.rgb = BRAND_BLUE

    p_concl = doc.add_paragraph()
    p_concl.paragraph_format.space_after = Pt(10)
    p_concl.paragraph_format.line_spacing = 1.15
    p_concl.add_run(
        'With these updates, SEO Audit Pro offers an industry-leading balance between deep technical accuracy and client-ready presentation. '
        'By providing built-in algorithmic models for Domain Authority and Organic Traffic, users can generate rich, presentation-ready audits for clients immediately without mandatory subscription overhead. '
        'Simultaneously, the platform remains fully extensible for enterprise users via the Settings panel to plug in official Moz API keys and Google Cloud PageSpeed credentials.'
    )

    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    out_path = os.path.join(reports_dir, 'SEO-Audit-Pro-Expert-Recommendations-Response.docx')
    doc.save(out_path)
    print(f'Document successfully created: {out_path} ({os.path.getsize(out_path)} bytes)')
    return out_path

if __name__ == '__main__':
    create_response_document()
