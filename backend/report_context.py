# -*- coding: utf-8 -*-
"""
Client-report view model (moved unchanged from the former web_app module).

Builds the context for templates/client_report.html from a stored audit
result. Only real findings from the audit are used.
"""


def prepare_client_report_context(result):
    checks = result.get("checks") or []
    pc = result.get("priority_counts") or {}
    total_issues = sum(pc.values())
    critical_count = pc.get("Critical", 0)
    high_count = pc.get("High", 0)
    urgent_count = critical_count + high_count

    psi = result.get("psi") or {}
    mob_psi = ((psi.get("mobile") or {}).get("scores") or {}).get("performance", "—")
    ssl_ok = bool((result.get("ssl_cert") or {}).get("valid", True))

    def _extract_issues(cats=None, check_ids=None):
        out = []
        cats = [c.lower() for c in (cats or [])]
        check_ids = [cid.lower() for cid in (check_ids or [])]
        for c in checks:
            if c.get("status") not in ("fail", "warn"):
                continue
            cat = (c.get("category") or "").lower()
            cid = (c.get("id") or "").lower()
            match = False
            if cats and cat in cats:
                match = True
            if check_ids and cid in check_ids:
                match = True
            if match:
                pri = c.get("severity", "medium").capitalize()
                if c.get("status") == "fail" and pri == "Medium":
                    pri = "High"
                urls = c.get("where") or []
                out.append({
                    "id": cid,
                    "label": c.get("label", "Issue"),
                    "priority": pri,
                    "detail": c.get("detail", ""),
                    "fix": c.get("fix") or "Apply standard technical remediation to resolve.",
                    "urls": urls,
                    "why": c.get("why") or "",
                })
        return out

    # 5 Specific Error Audit Sections matching user guide
    security_issues = _extract_issues(cats=["security"])
    technical_issues = _extract_issues(
        cats=["technical", "ux"],
        check_ids=["non_200_pages", "redirect_chains", "noindex_pages",
                   "broken_internal_links", "robots_present", "sitemap_present",
                   "sitemap_status", "sitemap_valid", "canonical_home",
                   "canonical_site", "ux_layout_stability", "ux_interactivity",
                   "ux_render_blocking", "ux_font_optimization"]
    )
    onpage_issues = _extract_issues(
        cats=["onpage", "content"],
        check_ids=["title_tag", "title_len", "meta_desc", "meta_desc_len",
                   "headings_h1", "headings_structure", "content_depth",
                   "thin_content", "img_alt_tags", "img_oversized",
                   "orphan_pages", "internal_linking", "cta_presence",
                   "cta_working", "url_structure"]
    )
    mobile_issues = _extract_issues(
        cats=["mobile", "performance"],
        check_ids=["viewport", "mobile_friendly", "mobile_psi", "mobile_lcp",
                   "mobile_cls", "mobile_touch_targets", "responsive_design"]
    )
    offpage_issues = _extract_issues(
        cats=["offpage"],
        check_ids=["offpage_domain_authority", "offpage_page_authority",
                   "offpage_outbound_links", "offpage_toxic_risk"]
    )

    # 6-Tier Prioritized Action Plan in Recommended Priority Order
    tier_defs = [
        (1, "Search Visibility and Indexability",
         "First, ensure that important website pages can be crawled, indexed, and discovered in search engines. Resolve any major visibility or indexing problems.",
         "#dc2626"),
        (2, "Technical SEO",
         "Fix critical crawl errors, server errors, redirect issues, sitemap problems, and other technical barriers.",
         "#ea580c"),
        (3, "On-Page SEO",
         "Improve content quality, titles, meta descriptions, headings, internal linking, URLs, and image optimization.",
         "#2563eb"),
        (4, "Local SEO",
         "Optimize the Google Business Profile, local landing pages, citations, and location-based keyword visibility.",
         "#7c3aed"),
        (5, "Off-Page SEO",
         "Improve backlink quality, build relevant links, and strengthen the website's authority and reputation.",
         "#059669"),
        (6, "Performance, Mobile UX, and Security",
         "Address any remaining issues affecting website usability, speed, mobile experience, or security.",
         "#d97706"),
    ]

    tier_map = {t[0]: {"tier_num": t[0], "title": t[1], "subtitle": t[2], "badge_color": t[3], "action_list": []} for t in tier_defs}

    items = result.get("action_items") or []
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
        tier_map[target_tier]["action_list"].append({
            "priority": it.get("priority", "Medium"),
            "label": it.get("label", "Issue"),
            "detail": it.get("detail", ""),
            "fix": it.get("fix") or "Follow standard search engine optimization best practices to resolve.",
            "business_impact": biz_impact,
            "urls": urls,
            "gain": it.get("gain", "Medium"),
            "effort": it.get("difficulty") or it.get("effort", "Moderate"),
        })

    active_tiers = [tier_map[k] for k in sorted(tier_map.keys()) if tier_map[k]["action_list"]]

    threat_level = "CRITICAL THREAT" if critical_count > 0 else ("HIGH RISK" if high_count > 0 else "MODERATE ATTENTION")
    threat_badge_color = "#dc2626" if critical_count > 0 else ("#ea580c" if high_count > 0 else "#d97706")

    threat_summary = (
        f"A comprehensive diagnostic audit of {result.get('domain', 'your website')} identified active defects directly impairing "
        "organic search discoverability, exposing browser trust warnings, and causing mobile visitor abandonment. "
        "Unresolved security misconfigurations and broken crawl paths quietly bleed traffic to competitors. "
        "This vulnerability audit isolates every detected failure and provides immediate remediation solutions."
    )

    return {
        "domain": result.get("domain", ""),
        "final_url": result.get("final_url", ""),
        "date": result.get("date") or result.get("generated_at", "")[:10],
        "pages_crawled": result.get("pages_crawled", 1),
        "total_issues": total_issues,
        "critical_count": critical_count,
        "high_count": high_count,
        "urgent_count": urgent_count,
        "security_count": len(security_issues),
        "threat_level": threat_level,
        "threat_badge_color": threat_badge_color,
        "threat_summary": threat_summary,
        "mob_psi": mob_psi,
        "ssl_ok": ssl_ok,
        "security_issues": security_issues,
        "technical_issues": technical_issues,
        "onpage_issues": onpage_issues,
        "mobile_issues": mobile_issues,
        "offpage_issues": offpage_issues,
        "tiers": active_tiers,
    }
