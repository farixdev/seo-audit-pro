# -*- coding: utf-8 -*-
"""The client PDF: the HTML client report printed with Edge/Chrome headless
(backgrounds on, no browser header or footer).

Made on the computer that ran the audit, right after it finishes; the
screenshots and the Google Business Profile lookup run alongside the crawl, so
only the print (a few seconds) is left at the end.
"""

import os
import threading

BRAND = {"name": "Brighton Analytics"}
REPORT_NAME = "Brighton Website SEO & Growth Report"


class Extras:
    """Website screenshots and the Google Maps profile, fetched in the background."""

    def __init__(self, url, folder, need_gbp=True):
        self.url, self.folder = url, folder
        self.shots, self.gbp, self.note = {}, None, None
        self._threads = [threading.Thread(target=self._shots, daemon=True)]
        if need_gbp:
            self._threads.append(threading.Thread(target=self._gbp, daemon=True))
        for t in self._threads:
            t.start()

    def _shots(self):
        try:
            import client_report
            self.shots = client_report.screenshots(self.url, self.folder)
        except Exception:  # noqa: BLE001 - screenshots are optional
            self.shots = {}

    def _gbp(self):
        try:
            import google_maps_scraper
            self.gbp, self.note = google_maps_scraper.scrape_google_maps(self.url, timeout=12)
        except Exception:  # noqa: BLE001 - the report says the profile was not checked
            self.gbp, self.note = None, "error"

    def wait(self, timeout=90):
        for t in self._threads:
            t.join(timeout)
        return self


def start(result_or_url, folder):
    """Begin the extras for an audit result (or a URL before the audit is done)."""
    os.makedirs(folder, exist_ok=True)
    if isinstance(result_or_url, dict):
        r = result_or_url
        url = r.get("final_url") or f"https://{r.get('domain') or ''}"
        return Extras(url, folder, need_gbp="gbp" not in r)
    return Extras(result_or_url, folder)


def write(result, folder, pdf_path, extras=None):
    """Write the HTML report and its PDF. Returns the PDF path, or None when no
    Edge/Chrome is installed (the HTML is still written)."""
    import full_report
    import mindcob_report
    extras = (extras or start(result, folder)).wait()
    if "gbp" not in result:
        result["gbp"], result["gbp_note"] = extras.gbp, extras.note
    domain = result.get("domain") or "site"
    html_path = os.path.join(folder, f"{domain} - {REPORT_NAME}.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(mindcob_report.build(result, BRAND, gbp=result.get("gbp"), shots=extras.shots,
                                      gbp_note=result.get("gbp_note") or "no_key"))
    part = pdf_path + ".part.pdf"
    if not full_report.html_to_pdf(html_path, part):
        return None
    os.replace(part, pdf_path)
    return pdf_path
