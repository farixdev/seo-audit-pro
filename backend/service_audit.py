# -*- coding: utf-8 -*-
"""
Campaign 3: Website Service Auditing and LLM Pitch Generation.

Performs deep extraction of a business's current website services and offerings,
then compares them against the agency's services to generate custom pitches,
opportunity scores, and spam-proof outreach emails.
"""

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from backend import db, security
from backend.util import now_iso

log = logging.getLogger("seo_audit_pro.service_audit")

_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DEFAULT_AGENCY_SERVICES = (
    "1. High-Performance Website Redesign & Modernization (Mobile-First, Lightning Fast)\n"
    "2. Local SEO & Google Business Profile Optimization (Rank #1 in Google Maps)\n"
    "3. Automated Online Appointment & Lead Booking Funnels\n"
    "4. Google Ads & Targeted Local Customer Acquisition\n"
    "5. 24/7 AI Receptionist & Website Lead Capture Chatbots\n"
    "6. Website Speed & Core Web Vitals Optimization (Under 1.5s load time)"
)


def get_agency_settings():
    row = db.query_one("SELECT * FROM agency_settings WHERE id = 'default'")
    if not row:
        return {
            "services": DEFAULT_AGENCY_SERVICES,
            "pitch_prompt": "",
            "custom_instructions": "",
            "llm_provider": "",
            "llm_model": "",
        }
    return dict(row)


def save_agency_settings(services=None, pitch_prompt=None, custom_instructions=None,
                         llm_provider=None, llm_model=None, llm_api_key=None):
    current = get_agency_settings()
    now = now_iso()
    fields = {
        "services": ("\n".join(str(x) for x in services) if isinstance(services, list) else str(services)) if services is not None else current["services"],
        "pitch_prompt": pitch_prompt if pitch_prompt is not None else current["pitch_prompt"],
        "custom_instructions": custom_instructions if custom_instructions is not None else current["custom_instructions"],
        "llm_provider": llm_provider if llm_provider is not None else current["llm_provider"],
        "llm_model": llm_model if llm_model is not None else current["llm_model"],
        "updated_at": now,
    }
    with db.transaction() as tx:
        tx.execute("""INSERT INTO agency_settings (id, services, pitch_prompt, custom_instructions,
                                                 llm_provider, llm_model, updated_at)
                      VALUES ('default', %s, %s, %s, %s, %s, %s)
                      ON CONFLICT(id) DO UPDATE SET
                          services = excluded.services,
                          pitch_prompt = excluded.pitch_prompt,
                          custom_instructions = excluded.custom_instructions,
                          llm_provider = excluded.llm_provider,
                          llm_model = excluded.llm_model,
                          updated_at = excluded.updated_at""",
                   (fields["services"], fields["pitch_prompt"], fields["custom_instructions"],
                    fields["llm_provider"], fields["llm_model"], now))
    return fields


def extract_website_services(url):
    """Crawl homepage + service pages to detect services and digital gaps."""
    if not url:
        return {"error": "No website URL provided", "services": []}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})

    pages_text = []
    service_links = []
    base_domain = urlparse(url).netloc.lower()

    try:
        resp = session.get(url, timeout=12, verify=False)
        if resp.status_code < 400:
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            h1s = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
            h2s = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)][:8]
            h3s = [h.get_text(strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)][:8]
            meta_desc = ""
            m = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
            if m and m.get("content"):
                meta_desc = m["content"].strip()

            pages_text.append(f"Title: {title}\nMeta: {meta_desc}\nH1: {' | '.join(h1s)}\nHeadings: {' | '.join(h2s + h3s)}")

            # Find internal links to service/about pages
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)
                if parsed.netloc.lower() == base_domain and full_url not in service_links and full_url != url:
                    path = parsed.path.lower()
                    if any(k in path for k in ("service", "solution", "about", "what-we-do", "treatment", "practice")):
                        service_links.append(full_url)
                        if len(service_links) >= 3:
                            break
    except Exception as e:
        log.warning("Could not fetch homepage %s: %s", url, e)

    # Fetch up to 2 service pages
    for s_url in service_links[:2]:
        try:
            r = session.get(s_url, timeout=10, verify=False)
            if r.status_code < 400:
                s_soup = BeautifulSoup(r.text, "html.parser")
                s_h1 = [h.get_text(strip=True) for h in s_soup.find_all("h1") if h.get_text(strip=True)]
                s_h2 = [h.get_text(strip=True) for h in s_soup.find_all("h2") if h.get_text(strip=True)][:6]
                pages_text.append(f"Subpage {s_url}:\nHeadings: {' | '.join(s_h1 + s_h2)}")
        except Exception:
            pass

    combined = "\n\n".join(pages_text)

    # Detect services and gaps from headings and text
    raw_services = []
    for line in combined.splitlines():
        if any(h in line for h in ("H1:", "Headings:")):
            parts = line.split(":", 1)[-1].split("|")
            for p in parts:
                cleaned = re.sub(r"\s+", " ", p).strip()
                if 4 <= len(cleaned) <= 60 and not any(skip in cleaned.lower() for skip in (
                    "menu", "navigation", "cookie", "privacy", "copyright", "home", "contact", "read more"
                )):
                    if cleaned not in raw_services:
                        raw_services.append(cleaned)

    # Filter top 6 distinct services
    services_found = raw_services[:8]
    has_booking = bool(re.search(r"book|schedule|appointment|reservation|quote", combined, re.I))
    has_chat = bool(re.search(r"chat|tawk|crisp|intercom|drift|zendesk", combined, re.I))
    has_testimonials = bool(re.search(r"review|testimonial|client story|what our clients say", combined, re.I))

    digital_gaps = []
    if not has_booking:
        digital_gaps.append("No automated online booking or direct appointment scheduling")
    if not has_chat:
        digital_gaps.append("No 24/7 AI live chat or instant lead capture")
    if not has_testimonials:
        digital_gaps.append("Limited or missing social proof/reviews visible on landing pages")
    if len(services_found) < 3:
        digital_gaps.append("Service offerings are briefly listed with limited detail for Google SEO ranking")

    summary = {
        "website": url,
        "services": services_found or ["Standard Trade / Business Services"],
        "digital_gaps": digital_gaps,
        "has_online_booking": has_booking,
        "has_live_chat": has_chat,
        "has_testimonials": has_testimonials,
        "scanned_pages": 1 + len(service_links[:2]),
        "summary_text": f"Found {len(services_found)} primary service areas with {len(digital_gaps)} identified growth opportunities.",
        "audited_at": now_iso(),
    }
    return summary


def compare_and_generate_pitch(lead, service_data, agency_services=None, sender_name="Mindcob Team"):
    """
    Compare lead's services with user's agency services.
    Uses LLM if configured; otherwise provides high-converting spam-free pitch logic.
    """
    company = lead.get("company") or "there"
    contact = lead.get("contact_name") or ""
    greeting = f"Hi {contact.split()[0]}," if contact else "Hello,"
    domain = lead.get("normalized_domain") or lead.get("website") or ""

    services_list = service_data.get("services") or []
    gaps_list = service_data.get("digital_gaps") or []

    if not agency_services:
        cfg = get_agency_settings()
        agency_services = cfg.get("services") or DEFAULT_AGENCY_SERVICES

    # Check if LLM is available
    llm_result = None
    try:
        import llm
        client = llm.from_env()
        if client:
            prompt = (
                f"You are a top B2B digital agency consultant writing a high-converting, spam-proof cold email.\n"
                f"Prospect Company: {company} ({domain})\n"
                f"Prospect's Current Services: {', '.join(services_list)}\n"
                f"Identified Website Gaps: {', '.join(gaps_list)}\n\n"
                f"Our Agency Services:\n{agency_services}\n\n"
                f"TASK:\n"
                f"1. Choose 1-2 complementary services from our agency that will directly bring more revenue to {company}.\n"
                f"2. Write a short, personalized, natural cold outreach email (under 120 words).\n"
                f"3. Calculate an Opportunity Score (70-98).\n"
                f"4. Provide 2 bullet points of why this is a prime pitch.\n\n"
                f"Return ONLY valid JSON:\n"
                f'{{"score": 88, "pitch_notes": "...", "subject": "...", "body": "..."}}'
            )
            raw = client.complete(
                model=client.default_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                response_format={"type": "json_object"}
            )
            data = json.loads(raw.strip())
            if "score" in data and "body" in data:
                llm_result = data
    except Exception as e:
        log.info("LLM not used or returned error, using built-in generator: %s", e)

    if llm_result:
        score = int(llm_result.get("score") or 85)
        pitch_notes = str(llm_result.get("pitch_notes") or "")
        subject = str(llm_result.get("subject") or f"Idea for {company}'s service inquiries")
        body = str(llm_result.get("body") or "")
    else:
        # High-converting smart fallback (spam-proof, conversational)
        score = min(96, 75 + len(gaps_list) * 6)
        main_service = services_list[0] if services_list else "services"
        top_gap = gaps_list[0] if gaps_list else "increasing online qualified inquiries"

        subject = f"Quick idea regarding {company}'s {main_service}"
        body_lines = [
            greeting,
            "",
            f"I was recently reviewing {company}'s website ({domain}) and noticed your great work around {main_service}.",
            "",
            f"While checking out your offerings, I noticed an opportunity around {top_gap.lower()}. "
            f"We recently helped another firm in your space implement automated lead capture and high-ranking local SEO, "
            f"resulting in a 38% increase in booked appointments within 45 days.",
            "",
            f"Would you be open to a brief 5-minute call this Thursday to share what worked for them?",
            "",
            "Best regards,",
            sender_name,
            "",
            "PS: If you're not the right person or prefer not to hear from me, just reply 'no' and I won't reach out again."
        ]
        body = "\n".join(body_lines)
        pitch_notes = f"Pitched {main_service} enhancements & {top_gap.lower()}."

    return {
        "score": score,
        "grade": "High Opportunity" if score >= 85 else ("Good Match" if score >= 75 else "Moderate"),
        "pitch_notes": pitch_notes,
        "subject": subject,
        "body": body,
        "services_extracted": services_list,
        "digital_gaps": gaps_list,
    }

