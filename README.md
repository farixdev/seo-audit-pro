# SEO Audit Pro

Give it a website URL. It **crawls the whole site**, runs a world-class SEO
audit across seven categories, and exports a **client-ready Word (.docx) report
with charts** — plus a live, tabbed preview inside the app.

**100% free. No API keys required.** All data comes from your own HTTP crawler
and Google's **PageSpeed Insights v5**, which runs *keyless* (it returns
Lighthouse scores **and** real-user Core Web Vitals from the CrUX dataset). An
optional free Groq/OpenRouter/Anthropic/Gemini key only adds an AI-written
executive summary; everything else works fully offline.

Part of the **Mindcob Toolbox** — launch it from the toolbox, or run it directly.

## Run (1-Click Web Launch)

Just double-click **`run.bat`** or execute:

```bat
.venv\Scripts\python.exe web_app.py
```

It starts the local web server and **automatically opens the Web UI in your default browser** (`http://127.0.0.1:5055`).

Alternatively:
```bat
:: python app.py also launches the Web UI by default
.venv\Scripts\python.exe app.py

:: Headless CLI mode:
python app.py --cli https://example.com --out report.docx --pages 25
```

## Features in the New Web UI

- **Dual-Mode Dashboard**: Seamlessly switch between **Executive / Layman View** (simplified business insights, plain English priorities) and **SEO Expert Matrix** (deep technical diagnostics, heading trees, schema inspection, crawled inventory).
- **Interactive Visualizations**: Animated circular health score gauge with letter grade (A+ through F), 8-axis category radar footprint, issue severity donut chart, and Core Web Vitals threshold meters.
- **SERP & Social Card Simulators**: Live Google search snippet preview (Desktop & Mobile with character & pixel length gauges) and OpenGraph / Twitter card previews.
- **Core Web Vitals & PageSpeed v5**: Side-by-side Mobile & Desktop Lighthouse lab simulations plus real-user CrUX field data.
- **Export Deliverables**:
  - **Word Report (.docx)**: Client-ready document with native styled tables and executive summary.
  - **PDF Report**: High-fidelity export via Microsoft Word or instant browser print-to-PDF engine.
  - **1-Click Section Image Download**: Dedicated camera icon on every card and chart to export high-resolution PNG snapshots (powered by html2canvas).
- **SEO Progress Tracker**: Historical multi-line trend chart tracking overall score and category progression over time, plus a delta comparison engine (Before vs. After: score delta, fixed issues, new issues, and speed change).
- **Web-Based Settings**: Configure AI keys (Groq, Gemini, OpenRouter), PageSpeed API keys, proxy, and crawler parameters directly from the browser.

## What it checks (8 scored categories, ~70 checks)

| Category | Examples |
|----------|----------|
| **Technical SEO** | robots.txt, XML sitemap, canonical, noindex/indexability, redirect chains, HTTP→HTTPS, broken links, URL structure, **structured data / schema validation**, Open Graph/Twitter, hreflang, pagination, AI-crawler rules |
| **On-Page SEO** | title & meta length + uniqueness across the crawl, single H1, heading hierarchy, thin content, image alt, internal links, favicon |
| **Performance** | PageSpeed mobile+desktop (Performance/SEO/Best-Practices), lab LCP/TBT/CLS/FCP, **real-user CrUX** LCP/INP/CLS/FCP/TTFB, render-blocking, page weight, compression, cache, unused CSS/JS |
| **Content Quality** | readability, content depth, keyword density, author/E-E-A-T, dates, About/Contact (NAP), thin content |
| **Mobile** | viewport + configuration, responsive-design signals, legible font sizes, tap targets, mobile page speed |
| **Accessibility** | document language, alt text, form labels, descriptive link text, landmarks, **live Lighthouse a11y audits** (color-contrast, ARIA, …) |
| **Security** | HTTPS/SSL, HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, mixed content, cookie flags |
| **User Experience** | layout stability (CLS), interactivity (INP), perceived load speed, font loading |

## Scoring

Each check is `pass` / `warn` / `fail` (or `info` / `na`). Checks roll up into a
**severity-weighted per-category score**, then into a **weighted overall score
and A+–F grade**. Categories with no applicable checks are dropped and the
weights renormalise, with a confidence/completeness indicator. Honest penalties
(noindexed homepage, no HTTPS, critical failures) are surfaced, not hidden. Every
non-pass finding becomes a **prioritized action item** with **Problem · Impact ·
Recommendation · Difficulty · Estimated SEO Gain · Priority**, sorted Critical →
Low so the quick wins float to the top.

## Competitor comparison (optional)

Add competitor URLs in **⚙ Options** to benchmark up to three rivals side-by-side
— overall score, mobile speed, HTTPS, title/meta, word count and schema — with a
comparison chart and table.

## The report

A branded, agency-grade document with: **cover** (domain, date, overall grade,
score gauge, KPI cards), **table of contents**, **executive summary** with
strengths / weaknesses / opportunities, **score dashboard** (per-category bars +
radar chart + colored scorecard), **prioritized action plan** (severity donut +
top-issues chart + Problem/Impact/Recommendation/Difficulty/Gain table),
**per-category detailed findings**, **competitor comparison**, a crawl/data
appendix, branded page header/footer with page numbers, and a methodology note.
Charts are rendered with matplotlib.

## Export

- **DOCX** — the primary deliverable (auto-generated and opened when an audit
  finishes).
- **PDF** — rendered via Microsoft Word if installed (updates the table of
  contents and page numbers); otherwise open the DOCX and "Save as PDF".
- **HTML** and **JSON** — standalone exports.

## Notes & limits

- **No API key needed.** PageSpeed Insights is keyless; an optional
  `PAGESPEED_API_KEY` only raises quota. An optional AI key (any of Groq /
  OpenRouter / Anthropic / Gemini, auto-detected) writes the executive summary.
- The crawler fetches rendered-by-server HTML (no headless browser), so
  JavaScript-only content may be under-counted — same limitation as the other
  Mindcob tools.
- The audit is bounded (default 25 pages, configurable) to stay fast and free.
- Paid data an agency might also buy — keyword volumes, backlinks, competitor
  rankings, SERP snapshots — is intentionally **not** included (that needs a paid
  provider like SE Ranking). This tool covers everything obtainable for free.
