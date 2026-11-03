# -*- coding: utf-8 -*-
"""
Campaign 3: Website Service Auditing & AI Opportunity Sniffer, plus AI Outreach
Generation across all 3 Campaigns (C1: SEO Audit, C2: No-Website Pitch, C3: Service Match).

Performs deep inspection of a business's website (navigation, headings, service cards,
JSON-LD schema, scripts, forms, widgets, pixels) to sniff out:
  1. Exact services the business provides
  2. Technical, conversion & marketing gaps on their website
  3. Exact services we can provide to this business (matched against Agency Offerings)
  4. Opportunity score (0-100), pitch notes, and spam-proof outreach email
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
import urllib3

import llm
from backend import db, security
from backend.util import now_iso

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("seo_audit_pro.service_audit")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_AGENCY_SERVICES = (
    "1. High-Performance Website Redesign & Modernization (Mobile-First, Lightning Fast)\n"
    "2. Local SEO & Google Business Profile Optimization (Rank #1 in Google Maps)\n"
    "3. Automated Online Appointment & Lead Booking Funnels\n"
    "4. Google Ads, Meta Retargeting Pixels & Local Customer Acquisition\n"
    "5. 24/7 AI Receptionist, Live Chat & Instant Lead Capture Chatbots\n"
    "6. Website Speed, Schema Markup & Core Web Vitals Optimization"
)

_BOILERPLATE_SKIP = (
    "menu", "navigation", "cookie", "privacy", "copyright", "home", "contact",
    "read more", "learn more", "view all", "click here", "get started", "sign in",
    "log in", "subscribe", "newsletter", "all rights reserved", "terms of",
    "skip to content", "toggle", "search", "follow us", "quick links", "our company",
    "welcome to", "who we are", "why choose us", "get in touch", "call us", "email us",
    "sitemap", "faq", "testimonials", "reviews", "careers", "blog", "news",
)


def _mask_key(key):
    if not key or len(key) < 8:
        return ""
    return f"{key[:4]}••••{key[-4:]}"


def get_llm_credentials():
    """Return (api_key, provider, model) from agency_settings DB or environment."""
    row = db.query_one("SELECT llm_provider, llm_model, llm_api_key_enc FROM agency_settings WHERE id = 'default'")
    api_key = ""
    provider = ""
    model = ""
    if row:
        provider = (row.get("llm_provider") or "").strip()
        model = (row.get("llm_model") or "").strip()
        enc = row.get("llm_api_key_enc")
        if enc:
            try:
                api_key = security.decrypt_secret(enc).strip()
            except Exception:
                api_key = ""
    if not api_key:
        for prov, env_name in llm.ENV_KEYS.items():
            val = (os.environ.get(env_name) or "").strip()
            if val:
                api_key = val
                provider = provider or prov
                break
    if api_key and not provider:
        provider = llm.provider_for_key(api_key)
    if api_key and not model:
        model = llm.default_model(provider)
    return api_key, provider, model


def get_agency_settings():
    row = db.query_one("SELECT * FROM agency_settings WHERE id = 'default'")
    api_key, det_provider, det_model = get_llm_credentials()
    if not row:
        return {
            "services": DEFAULT_AGENCY_SERVICES,
            "pitch_prompt": "",
            "custom_instructions": "",
            "llm_provider": det_provider,
            "llm_model": det_model,
            "has_llm_key": bool(api_key),
            "llm_key_hint": _mask_key(api_key),
            "llm_provider_label": llm.provider_label(det_provider) if det_provider else "",
        }
    out = dict(row)
    out.pop("llm_api_key_enc", None)
    out["llm_provider"] = out.get("llm_provider") or det_provider
    out["llm_model"] = out.get("llm_model") or det_model
    out["has_llm_key"] = bool(api_key)
    out["llm_key_hint"] = _mask_key(api_key)
    out["llm_provider_label"] = llm.provider_label(out["llm_provider"]) if out.get("llm_provider") else ""
    return out


def save_agency_settings(services=None, pitch_prompt=None, custom_instructions=None,
                         llm_provider=None, llm_model=None, llm_api_key=None, clear_llm_key=False):
    row = db.query_one("SELECT * FROM agency_settings WHERE id = 'default'") or {}
    current = get_agency_settings()
    now = now_iso()

    key_enc = row.get("llm_api_key_enc")
    raw_key = (llm_api_key or "").strip() if isinstance(llm_api_key, str) else ""
    if clear_llm_key:
        key_enc = None
    elif raw_key:
        key_enc = security.encrypt_secret(raw_key)

    prov = (llm_provider if llm_provider is not None else current.get("llm_provider") or "").strip()
    if raw_key and not prov:
        prov = llm.provider_for_key(raw_key)
    mdl = (llm_model if llm_model is not None else current.get("llm_model") or "").strip()
    if (raw_key or prov) and not mdl:
        mdl = llm.default_model(prov or llm.provider_for_key(raw_key))

    fields = {
        "services": ("\n".join(str(x) for x in services) if isinstance(services, list) else str(services)) if services is not None else current["services"],
        "pitch_prompt": pitch_prompt if pitch_prompt is not None else current["pitch_prompt"],
        "custom_instructions": custom_instructions if custom_instructions is not None else current["custom_instructions"],
        "llm_provider": prov,
        "llm_model": mdl,
        "updated_at": now,
    }
    with db.transaction() as tx:
        tx.execute("""INSERT INTO agency_settings (id, services, pitch_prompt, custom_instructions,
                                                 llm_provider, llm_model, llm_api_key_enc, updated_at)
                      VALUES ('default', %s, %s, %s, %s, %s, %s, %s)
                      ON CONFLICT(id) DO UPDATE SET
                          services = excluded.services,
                          pitch_prompt = excluded.pitch_prompt,
                          custom_instructions = excluded.custom_instructions,
                          llm_provider = excluded.llm_provider,
                          llm_model = excluded.llm_model,
                          llm_api_key_enc = excluded.llm_api_key_enc,
                          updated_at = excluded.updated_at""",
                   (fields["services"], fields["pitch_prompt"], fields["custom_instructions"],
                    fields["llm_provider"], fields["llm_model"], key_enc, now))
    return get_agency_settings()


def _clean_service_phrase(text):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip(" -–—•·|:/\t\r\n")
    if not (4 <= len(cleaned) <= 58):
        return ""
    low = cleaned.lower()
    if any(skip in low for skip in _BOILERPLATE_SKIP):
        return ""
    if re.search(r"(\d{3}[-\.\s]\d{3}|@\w+\.|\bhttp)", low):
        return ""
    return cleaned


def extract_website_services(url):
    """
    Deeply inspect a business website (homepage + up to 3 internal service/about/contact pages)
    to sniff out:
      - Services offered by the business
      - Conversion, technical & marketing gaps (booking, chat, forms, pixels, schema, reviews, SEO)
      - Exact services we can provide to this business (`what_we_can_provide`)
    """
    url = (url or "").strip()
    if not url:
        return {"error": "No website URL provided", "services": [], "what_we_can_provide": [], "digital_gaps": []}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})

    pages_text = []
    raw_html_blobs = []
    service_links = []
    nav_service_candidates = []
    schema_services = []
    base_domain = urlparse(url).netloc.lower().lstrip("www.")

    resp = None
    try:
        resp = session.get(url, timeout=6, verify=False)
    except Exception as e:
        if url.startswith("https://"):
            try:
                resp = session.get("http://" + url[8:], timeout=5, verify=False)
            except Exception:
                resp = None
        log.info("Homepage fetch attempt on %s: %s", url, e)

    if resp and resp.status_code < 400 and resp.text:
        raw_html_blobs.append(resp.text)
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
            h2s = [h.get_text(" ", strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)][:12]
            h3s = [h.get_text(" ", strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)][:14]
            meta_desc = ""
            m = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if m and m.get("content"):
                meta_desc = m["content"].strip()

            # Extract JSON-LD schema services if present
            for sc in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
                raw_json = (sc.string or sc.get_text() or "").strip()
                if raw_json:
                    for match in re.findall(r'"(?:serviceType|name)"\s*:\s*"([^"]{4,55})"', raw_json):
                        c = _clean_service_phrase(match)
                        if c and c not in schema_services:
                            schema_services.append(c)

            # Extract navigation & internal service links
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                link_txt = _clean_service_phrase(a.get_text(" ", strip=True))
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)
                netloc = parsed.netloc.lower().lstrip("www.")
                if netloc == base_domain and full_url != url:
                    path = parsed.path.lower()
                    if any(k in path for k in (
                        "service", "solution", "what-we-do", "treatment", "practice",
                        "offer", "specialty", "specialties", "pricing", "packages", "work", "repair", "install"
                    )):
                        if link_txt and link_txt not in nav_service_candidates:
                            nav_service_candidates.append(link_txt)
                        if full_url not in service_links and len(service_links) < 3:
                            service_links.append(full_url)

            # Extract short bulleted service items
            li_items = []
            for li in soup.find_all("li")[:40]:
                c = _clean_service_phrase(li.get_text(" ", strip=True))
                if c and len(c.split()) <= 6:
                    li_items.append(c)

            body_snippet = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:1200]
            pages_text.append(
                f"Title: {title}\nMeta: {meta_desc}\nH1: {' | '.join(h1s)}\n"
                f"Headings: {' | '.join(h2s + h3s)}\nNav Services: {' | '.join(nav_service_candidates[:10])}\n"
                f"Bullets: {' | '.join(li_items[:12])}\nExcerpt: {body_snippet}"
            )
        except Exception as e:
            log.warning("Error parsing homepage for %s: %s", url, e)

    def _fetch_sub(s_url):
        try:
            r = session.get(s_url, timeout=3.5, verify=False)
            if r.status_code < 400 and r.text:
                s_soup = BeautifulSoup(r.text, "html.parser")
                s_h1 = [h.get_text(" ", strip=True) for h in s_soup.find_all("h1") if h.get_text(strip=True)]
                s_h2 = [h.get_text(" ", strip=True) for h in s_soup.find_all("h2") if h.get_text(strip=True)][:8]
                s_h3 = [h.get_text(" ", strip=True) for h in s_soup.find_all("h3") if h.get_text(strip=True)][:8]
                return r.text, f"Subpage {s_url}:\nHeadings: {' | '.join(s_h1 + s_h2 + s_h3)}"
        except Exception:
            pass
        return None, None

    # Fetch subpages sequentially to prevent thread pool deadlocks and connection exhaustion
    for s_url in service_links[:2]:
        sub_html, sub_txt = _fetch_sub(s_url)
        if sub_html:
            raw_html_blobs.append(sub_html)
        if sub_txt:
            pages_text.append(sub_txt)

    if not raw_html_blobs:
        return {
            "website": url,
            "services": ["Local Business & Professional Services"],
            "what_we_can_provide": [
                "Website Restoration & Modernization",
                "High-Speed Cloud Hosting & SSL Security",
                "Mobile-First Responsive Website Redesign",
                "Local SEO & Google Business Profile Optimization"
            ],
            "digital_gaps": [
                "Website is unreachable, slow or experiencing server downtime",
                "Missing reliable SSL certificate and modern cloud infrastructure",
                "Losing potential customer inquiries due to website loading failure"
            ],
            "unreachable": True,
            "has_online_booking": False,
            "has_live_chat": False,
            "has_lead_form": False,
            "has_ad_pixels": False,
            "has_schema_markup": False,
            "has_testimonials": False,
            "scanned_pages": 0,
            "raw_context": "Website connection failed or timed out.",
            "summary_text": "Website is offline or unresponsive. Opportunity: Website Restoration, Modern Hosting & Redesign.",
            "audited_at": now_iso(),
        }

    combined_text = "\n\n".join(pages_text)
    combined_html = "\n".join(raw_html_blobs)

    # Candidate services in priority order: nav service links -> headings -> schema -> bullets
    raw_services = []
    for item in nav_service_candidates + schema_services:
        c = _clean_service_phrase(item)
        if c and c not in raw_services:
            raw_services.append(c)

    for line in combined_text.splitlines():
        if any(line.startswith(prefix) for prefix in ("H1:", "Headings:", "Bullets:")):
            parts = line.split(":", 1)[-1].split("|")
            for p in parts:
                c = _clean_service_phrase(p)
                if c and c not in raw_services:
                    raw_services.append(c)

    services_found = raw_services[:8]

    # Deep HTML & Script Sniffer for Conversion, Marketing & Technical Gaps
    has_booking = bool(re.search(
        r"calendly|acuity|simplybook|mindbody|servicetitan|housecallpro|getjobber|setmore|square\.site|"
        r"book-online|book-now|schedule-appointment|online-booking|request-appointment",
        combined_html, re.I
    ))
    has_chat = bool(re.search(
        r"intercom|tawk\.to|crisp\.chat|drift\.com|tidio|livechatinc|zendesk|hubspot|podium|birdeye|"
        r"wa\.me/|api\.whatsapp\.com|chat-widget|live-chat",
        combined_html, re.I
    ))
    has_lead_form = bool(re.search(r"<form\b[^>]*>.*?(?:email|phone|tel|message|textarea).*?</form>", combined_html, re.I | re.S))
    has_pixels = bool(re.search(r"googletagmanager\.com|gtag\(|connect\.facebook\.net|fbq\(|fbevents\.js|analytics\.tiktok\.com", combined_html, re.I))
    has_schema = bool(re.search(r"application/ld\+json", combined_html, re.I))
    has_testimonials = bool(re.search(r"testimonial|google\s*reviews|trustpilot|what\s+our\s+(?:clients|customers)\s+say|star-rating", combined_html, re.I))
    has_viewport = bool(re.search(r'<meta[^>]+name=["\']viewport["\']', combined_html, re.I))

    digital_gaps = []
    what_we_can_provide = []

    if not has_booking:
        digital_gaps.append("No automated online appointment booking or self-scheduling funnel")
        what_we_can_provide.append("Automated Online Appointment & Booking Funnel")
    if not has_chat:
        digital_gaps.append("No 24/7 AI live chat or instant WhatsApp/SMS lead capture widget")
        what_we_can_provide.append("24/7 AI Receptionist & Website Lead Capture Chatbot")
    if not has_lead_form:
        digital_gaps.append("No instant quote or lead capture form on the primary landing page")
        what_we_can_provide.append("High-Converting Instant Quote & Lead Capture Funnel")
    if not has_pixels:
        digital_gaps.append("Missing Google Tag Manager / Meta Ad retargeting pixels (losing unbooked visitors)")
        what_we_can_provide.append("Google Ads & Meta Retargeting Pixel Setup")
    if not has_schema or len(services_found) < 3:
        digital_gaps.append("Missing LocalBusiness Schema.org markup & thin service-page SEO coverage")
        what_we_can_provide.append("Local SEO, Dedicated Service Pages & Google Maps Optimization")
    if not has_testimonials:
        digital_gaps.append("No embedded Google Reviews widget or conversion social proof on landing page")
        what_we_can_provide.append("Automated Google Review Widget & Reputation Funnel")
    if not has_viewport:
        digital_gaps.append("Not optimized for mobile responsiveness (missing mobile viewport configuration)")
        what_we_can_provide.append("Mobile-First Website Redesign & Speed Optimization")

    # If the site had all technical widgets, still recommend growth & SEO expansion
    if not what_we_can_provide:
        digital_gaps.append("Opportunity to expand dedicated local landing pages for high-intent searches")
        what_we_can_provide.append("Local SEO & High-Intent Search Expansion")

    summary = {
        "website": url,
        "services": services_found or ["Local Business & Professional Services"],
        "what_we_can_provide": what_we_can_provide[:4],
        "digital_gaps": digital_gaps[:5],
        "has_online_booking": has_booking,
        "has_live_chat": has_chat,
        "has_lead_form": has_lead_form,
        "has_ad_pixels": has_pixels,
        "has_schema_markup": has_schema,
        "has_testimonials": has_testimonials,
        "scanned_pages": 1 + len(service_links[:3]),
        "raw_context": combined_text[:2200],
        "summary_text": (
            f"Detected {len(services_found)} core services; sniffed {len(digital_gaps)} conversion/SEO gaps. "
            f"Top pitch: {', '.join(what_we_can_provide[:2])}."
        ),
        "audited_at": now_iso(),
    }
    return summary


def compare_and_generate_pitch(lead, service_data, agency_services=None, sender_name="Our Team", llm_creds=None, custom_instructions=None):
    """
    Compare lead's scraped website & conversion gaps against the user's Agency Services.
    Uses the configured LLM to:
      1. Refine the exact list of services the business provides
      2. Sniff out the exact services we can provide to this business (`what_we_can_provide`)
      3. Score the opportunity (0-100) and write clear `pitch_notes`
      4. Generate a personalized, spam-free outreach email
    """
    company = lead.get("company") or "your business"
    contact = lead.get("contact_name") or ""
    greeting = f"Hi {contact.split()[0]}," if contact else "Hello,"
    domain = lead.get("normalized_domain") or lead.get("website") or ""
    niche = lead.get("niche") or ""
    area = lead.get("area") or ""

    if not sender_name or sender_name.lower() in ("mindcob", "mindcob team", "faris@mindcob.com"):
        try:
            smtp_row = db.query_one("SELECT from_name FROM smtp_settings WHERE id = 'default'")
            sender_name = (smtp_row.get("from_name") if smtp_row and smtp_row.get("from_name") else None) or "Outreach Team"
        except Exception:
            sender_name = "Outreach Team"

    services_list = list(service_data.get("services") or [])
    gaps_list = list(service_data.get("digital_gaps") or [])
    we_can_provide = list(service_data.get("what_we_can_provide") or [])
    raw_context = (service_data.get("raw_context") or "")[:1800]

    if agency_services is None:
        try:
            cfg = get_agency_settings()
            agency_services = cfg.get("services") or DEFAULT_AGENCY_SERVICES
            if custom_instructions is None:
                custom_instructions = (cfg.get("custom_instructions") or "").strip()
        except Exception:
            agency_services = DEFAULT_AGENCY_SERVICES
            custom_instructions = custom_instructions or ""

    if custom_instructions is None:
        custom_instructions = ""

    llm_result = None
    try:
        if llm_creds:
            api_key, provider, model = llm_creds
        else:
            api_key, provider, model = get_llm_credentials()

        if api_key and not service_data.get("unreachable"):
            sys_prompt = (
                "You are an elite B2B website auditor and agency growth strategist. "
                "Your job is to inspect a business's scraped website signals, identify the exact services "
                "they provide, sniff out the highest-impact services our agency can provide them to increase "
                "their leads/revenue, and write a hyper-personalized, human, spam-free cold email. "
                "Never use spam trigger words (free, guarantee, urgent, act now, 100%, click here, obligation). "
                "Return ONLY valid JSON."
            )
            user_prompt = (
                f"Prospect Company: {company} ({domain})\n"
                f"Category / Niche: {niche or 'Local Business'} | Location: {area or 'Local Area'}\n"
                f"Contact Name: {contact or 'Owner / Decision Maker'}\n"
                f"Scraped Website Text & Headings:\n{raw_context}\n\n"
                f"Initial Extracted Services: {', '.join(services_list)}\n"
                f"Technical & Conversion Gaps Sniffed on Website: {', '.join(gaps_list)}\n"
                f"Initial Recommended Services We Can Provide: {', '.join(we_can_provide)}\n\n"
                f"OUR AGENCY SERVICES & OFFERINGS:\n{agency_services}\n\n"
                + (f"Custom Pitch Instructions: {custom_instructions}\n\n" if custom_instructions else "")
                + f"Sender Name: {sender_name}\n\n"
                f"TASK:\n"
                f"1. `their_services`: Clean list of 3-6 specific services {company} actually provides to their customers.\n"
                f"2. `what_we_can_provide`: Pick 2-3 specific, high-ROI services from OUR AGENCY SERVICES that directly fix {company}'s website/conversion gaps.\n"
                f"3. `score`: Integer 70-98 representing how strong of a prospect they are.\n"
                f"4. `pitch_notes`: Concise 1-2 sentence breakdown starting with 'We Can Provide: [services] — ' followed by why they need it based on their site.\n"
                f"5. `subject`: Short, natural, non-salesy email subject line mentioning {company} or their service.\n"
                f"6. `body`: Conversational outreach email (95-135 words) referencing their specific services, pointing out the exact gap we noticed on {domain}, pitching what we can provide, and asking for a quick 5-minute chat. Sign off with {sender_name} and a polite 1-line opt-out PS.\n\n"
                f"Return ONLY valid JSON with keys:\n"
                f'{{"their_services": ["..."], "what_we_can_provide": ["..."], "score": 90, '
                f'"pitch_notes": "We Can Provide: ... — ...", "subject": "...", "body": "..."}}'
            )
            data = llm.chat_json(
                api_key, model, sys_prompt, user_prompt,
                timeout=35, max_tokens=1000, temperature=0.35, max_retries=2, provider=provider
            )
            if isinstance(data, dict) and data.get("body") and data.get("subject"):
                llm_result = data
    except Exception as e:
        log.info("LLM not used or returned error in compare_and_generate_pitch: %s", e)

    if llm_result:
        if isinstance(llm_result.get("their_services"), list) and llm_result["their_services"]:
            services_list = [str(x).strip() for x in llm_result["their_services"] if str(x).strip()][:6]
            service_data["services"] = services_list
        if isinstance(llm_result.get("what_we_can_provide"), list) and llm_result["what_we_can_provide"]:
            we_can_provide = [str(x).strip() for x in llm_result["what_we_can_provide"] if str(x).strip()][:4]
            service_data["what_we_can_provide"] = we_can_provide
        try:
            score = max(60, min(99, int(llm_result.get("score") or 88)))
        except (TypeError, ValueError):
            score = 88
        pitch_notes = str(llm_result.get("pitch_notes") or "").strip()
        if not pitch_notes and we_can_provide:
            pitch_notes = f"We Can Provide: {', '.join(we_can_provide[:2])} — Matched to {len(gaps_list)} website gaps."
        subject = str(llm_result.get("subject") or f"Quick idea for {company}").strip()
        body = str(llm_result.get("body") or "").strip()
    else:
        score = min(96, 74 + len(gaps_list) * 5)
        main_service = services_list[0] if services_list else (niche or "core services")
        top_provide = ", ".join(we_can_provide[:2]) if we_can_provide else "Online Booking & Local SEO Optimization"
        top_gap = gaps_list[0] if gaps_list else "capturing more local website visitors into booked inquiries"

        subject = f"Quick question about {company}'s {main_service.lower()}"
        body_lines = [
            greeting,
            "",
            f"I was reviewing {company}'s website ({domain}) and noticed your focus on {', '.join(services_list[:3]) if services_list else main_service}.",
            "",
            f"While checking the site, I noticed one key growth opportunity: {top_gap.lower()}. "
            f"We help businesses in your space implement {top_provide} so high-intent visitors can book or request a quote immediately instead of bouncing to a competitor.",
            "",
            f"Would you be open to a quick 5-minute call this week so I can share two specific ideas for {company}?",
            "",
            "Best regards,",
            sender_name,
            "",
            "PS: If you prefer not to hear from me, simply reply 'no' and I won't reach out again.",
        ]
        body = "\n".join(body_lines)
        pitch_notes = f"We Can Provide: {top_provide} | Gap: {top_gap}"

    return {
        "score": score,
        "grade": "High Opportunity" if score >= 85 else ("Good Match" if score >= 75 else "Moderate"),
        "pitch_notes": pitch_notes,
        "what_we_can_provide": we_can_provide,
        "subject": subject,
        "body": body,
        "services_extracted": services_list,
        "digital_gaps": gaps_list,
        "service_data": service_data,
    }


def generate_seo_audit_email_ai(lead, audit_row, base_draft, sender_name="Our Team"):
    """
    Use the configured LLM to enhance Campaign 1 (Technical SEO Audit email) with
    hyper-specific, spam-free copy referencing the lead's actual SEO score & top findings.
    Falls back to `base_draft` if no LLM key is configured.
    """
    api_key, provider, model = get_llm_credentials()
    if not api_key or not audit_row:
        return base_draft

    smtp_row = db.query_one("SELECT from_name FROM smtp_settings WHERE id = 'default'")
    if smtp_row and smtp_row.get("from_name"):
        sender_name = smtp_row["from_name"]

    cfg = get_agency_settings()
    agency_services = cfg.get("services") or DEFAULT_AGENCY_SERVICES
    custom_instructions = (cfg.get("custom_instructions") or "").strip()

    company = lead.get("company") or "your business"
    domain = lead.get("normalized_domain") or lead.get("website") or ""
    contact = lead.get("contact_name") or ""
    score = audit_row.get("score")
    grade = audit_row.get("grade") or ""

    try:
        sys_prompt = (
            "You are a senior technical SEO consultant writing a personalized, non-spammy "
            "outreach email to a business owner after running a technical SEO & performance audit "
            "on their website. Never use spam trigger words (free, guarantee, urgent, act now, 100%). "
            "Return ONLY valid JSON with keys `subject` and `body`."
        )
        user_prompt = (
            f"Company: {company} ({domain})\n"
            f"Contact: {contact or 'Team'}\n"
            f"Audit Score: {score}/100 ({grade})\n"
            f"Key Findings Summary from Our Audit:\n{base_draft.get('body', '')}\n\n"
            f"Our Agency Services:\n{agency_services}\n"
            + (f"Custom Guidance: {custom_instructions}\n" if custom_instructions else "")
            + f"Sender Name: {sender_name}\n\n"
            f"Rewrite this into a sharp, human, high-converting email (100-140 words) that:\n"
            f"1. Mentions their specific SEO score ({score}/100) and the 2-3 concrete technical issues found on {domain}.\n"
            f"2. Notes that we've attached the full PDF SEO Audit Report for their review.\n"
            f"3. Connects those fixes to getting more local organic traffic and inquiries.\n"
            f"4. Signs off as {sender_name} with a polite 1-line opt-out PS.\n"
            f'Return ONLY JSON: {{"subject": "...", "body": "..."}}'
        )
        res = llm.chat_json(api_key, model, sys_prompt, user_prompt, timeout=30, max_tokens=700, temperature=0.35, max_retries=2, provider=provider)
        if isinstance(res, dict) and res.get("subject") and res.get("body"):
            out = dict(base_draft)
            out["subject"] = str(res["subject"]).strip()
            out["body"] = str(res["body"]).strip()
            out["ai_generated"] = True
            return out
    except Exception as e:
        log.info("LLM not used for C1 SEO Audit email, using smart built-in draft: %s", e)
    return base_draft


def generate_no_website_pitch_ai(lead, base_subject, base_body, sender_name="Our Team"):
    """
    Use the configured LLM to craft a hyper-personalized Campaign 2 (No-Website Lead)
    pitch email & call script tailored to the lead's niche, city, rating, and review count.
    """
    api_key, provider, model = get_llm_credentials()
    if not api_key:
        return {"subject": base_subject, "body": base_body, "ai_generated": False}

    smtp_row = db.query_one("SELECT from_name FROM smtp_settings WHERE id = 'default'")
    if smtp_row and smtp_row.get("from_name"):
        sender_name = smtp_row["from_name"]

    cfg = get_agency_settings()
    agency_services = cfg.get("services") or DEFAULT_AGENCY_SERVICES
    custom_instructions = (cfg.get("custom_instructions") or "").strip()

    company = lead.get("company") or "your business"
    niche = lead.get("niche") or "local services"
    area = lead.get("area") or "your area"
    contact = lead.get("contact_name") or ""
    rating = lead.get("rating") or ""
    reviews = lead.get("reviews") or ""

    try:
        sys_prompt = (
            "You are a web design & local lead-generation specialist reaching out to an established "
            "local business that appears on Google Maps without an active website. "
            "Write a natural, respectful, spam-proof outreach message (under 125 words) plus a 2-sentence phone call opener. "
            "Never use spam trigger words (free, guarantee, urgent, act now, 100%). Return ONLY valid JSON."
        )
        user_prompt = (
            f"Business Name: {company}\n"
            f"Category / Niche: {niche}\n"
            f"City / Area: {area}\n"
            f"Contact: {contact or 'Owner'}\n"
            f"Google Maps Rating: {rating or 'N/A'} ({reviews or 0} reviews)\n"
            f"Our Offerings:\n{agency_services}\n"
            + (f"Custom Guidance: {custom_instructions}\n" if custom_instructions else "")
            + f"Sender Name: {sender_name}\n\n"
            f"Write:\n"
            f"1. `subject`: Conversational subject line mentioning {company}.\n"
            f"2. `body`: Warm, specific outreach email praising their local presence in {area}, noting they don't have a website linked on Google Maps, explaining how a fast mobile-ready site + online booking captures customers searching for {niche} in {area}, and offering to show a quick mockup preview on a 5-minute call. Sign off as {sender_name} with a polite opt-out PS.\n"
            f'Return ONLY JSON: {{"subject": "...", "body": "..."}}'
        )
        res = llm.chat_json(api_key, model, sys_prompt, user_prompt, timeout=30, max_tokens=650, temperature=0.4, max_retries=2, provider=provider)
        if isinstance(res, dict) and res.get("subject") and res.get("body"):
            return {
                "subject": str(res["subject"]).strip(),
                "body": str(res["body"]).strip(),
                "ai_generated": True,
            }
    except Exception as e:
        log.info("LLM not used for C2 No-Website pitch, using built-in draft: %s", e)
    return {"subject": base_subject, "body": base_body, "ai_generated": False}
