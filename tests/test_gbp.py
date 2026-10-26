# -*- coding: utf-8 -*-
"""Google Business Profile: matching the right listing and the checklist rules
(no network: Places API answers are stubbed)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google_maps_scraper as gms  # noqa: E402
from full_report import gbp_checks  # noqa: E402

HTML = """<html><head><title>Emergency Plumber Dallas | Joe's Plumbing</title>
<script type="application/ld+json">{"@type": "Plumber", "name": "Joe's Plumbing", "telephone": "(214) 555-0100",
 "address": {"@type": "PostalAddress", "streetAddress": "12 Main St", "addressLocality": "Dallas"}}</script>
</head><body><a href="tel:+12145550100">Call</a></body></html>"""


def statuses(checks):
    return {c["id"]: c["status"] for c in checks}


class GbpTests(unittest.TestCase):
    def test_hints_from_the_website(self):
        h = gms.hints_from_html(HTML, "https://www.joesplumbing.com/")
        self.assertEqual(h["names"][0], "Joe's Plumbing")
        self.assertEqual(h["city"], "Dallas")
        self.assertEqual(len(h["phones"]), 1)   # same number twice is kept once
        self.assertEqual(h["domain"], "joesplumbing.com")

    def test_only_this_business_is_accepted(self):
        hints = gms.hints_from_html(HTML, "https://joesplumbing.com")
        other = {"displayName": {"text": "Bob's Plumbing"}, "websiteUri": "https://bobsplumbing.com",
                 "nationalPhoneNumber": "(214) 555-9999"}
        mine_by_phone = {"displayName": {"text": "Joe's"}, "nationalPhoneNumber": "+1 214-555-0100"}
        mine_by_site = {"displayName": {"text": "Joe's"}, "websiteUri": "https://www.joesplumbing.com/?utm=gmb"}
        answers = {"(214) 555-0100": ([other, mine_by_phone], None)}
        place, note = gms._from_api(gms._queries(hints, "joesplumbing.com"),
                                    lambda q: answers.get(q, ([other], None)), "joesplumbing.com", hints)
        self.assertEqual((place["displayName"]["text"], note), ("Joe's", "matched_phone"))
        place, note = gms._from_api(["x"], lambda q: ([other, mine_by_site], None), "joesplumbing.com", hints)
        self.assertEqual(note, "matched_website")
        place, note = gms._from_api(["x"], lambda q: ([other], None), "joesplumbing.com", hints)
        self.assertIsNone(place)   # never another business's listing
        self.assertEqual(note, "not_found")
        place, note = gms._from_api(["a", "b"], lambda q: ([], "no_key"), "joesplumbing.com", hints)
        self.assertEqual((place, note), (None, "no_key"))

    def test_same_site_and_phone(self):
        self.assertTrue(gms.same_site("https://shop.example.com/x", "example.com"))
        self.assertFalse(gms.same_site("https://example.co", "example.com"))
        self.assertTrue(gms.same_phone("+1 (416) 555-0101", ["416.555.0101"]))
        self.assertFalse(gms.same_phone("", ["416.555.0101"]))

    def test_api_profile_is_judged_completely(self):
        place = {"_source": "places_api", "displayName": {"text": "Joe's Plumbing"}, "businessStatus": "OPERATIONAL",
                 "websiteUri": "https://joesplumbing.com", "nationalPhoneNumber": "(214) 555-0100",
                 "formattedAddress": "12 Main St, Dallas", "rating": 4.2, "userRatingCount": 8,
                 "primaryTypeDisplayName": {"text": "Plumber"}, "photos": [{}] * 3,
                 "reviews": [{"publishTime": "2020-01-01T00:00:00Z"}]}
        hints = gms.hints_from_html(HTML, "https://joesplumbing.com")
        s = statuses(gbp_checks(place, "joesplumbing.com", hints))
        self.assertEqual(s["gbp_website"], "pass")
        self.assertEqual(s["gbp_phone_match"], "pass")
        self.assertEqual(s["gbp_name_match"], "pass")
        self.assertEqual(s["gbp_hours"], "fail")          # the API returned no hours: really missing
        self.assertEqual(s["gbp_rating"], "warn")
        self.assertEqual(s["gbp_reviews"], "fail")
        self.assertEqual(s["gbp_photos"], "warn")
        self.assertEqual(s["gbp_recent_reviews"], "fail")
        self.assertEqual(s["gbp_summary"], "warn")
        self.assertEqual(s["gbp_claimed"], "na")          # the API does not say; never guessed
        self.assertEqual(s["gbp_actions"], "na")

    def test_maps_page_profile_marks_unseen_items_not_checked(self):
        place = {"_source": "maps_scrape", "displayName": {"text": "Joe's Plumbing"}, "businessStatus": "OPERATIONAL",
                 "websiteUri": "https://joesplumbing.com", "claimed": False, "actions": [],
                 "primaryTypeDisplayName": {"text": "Plumber"}}
        s = statuses(gbp_checks(place, "joesplumbing.com", {}))
        self.assertEqual(s["gbp_claimed"], "fail")
        self.assertEqual(s["gbp_actions"], "warn")
        for key in ("gbp_reviews", "gbp_rating", "gbp_photos", "gbp_summary", "gbp_recent_reviews",
                    "gbp_phone_match", "gbp_name_match"):
            self.assertEqual(s[key], "na", key)
        self.assertEqual(s["gbp_phone"], "fail")

    def test_closed_and_wrong_website(self):
        place = {"_source": "maps_scrape", "displayName": {"text": "X"}, "businessStatus": "CLOSED_PERMANENTLY",
                 "websiteUri": "https://other.com"}
        s = statuses(gbp_checks(place, "joesplumbing.com", {}))
        self.assertEqual(s["gbp_open"], "fail")
        self.assertEqual(s["gbp_website"], "fail")


if __name__ == "__main__":
    unittest.main()
