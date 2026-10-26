# -*- coding: utf-8 -*-
"""Tests for Map Harvest integration into SEO Audit Pro."""

import os
import sys
import unittest

from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for widget testing
_app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])

from seo_audit_pro.core.session import session
from seo_audit_pro.map_harvest.core import enrich, exporter, filters, parse, settings
from seo_audit_pro.map_harvest.ui.lead_bank_dialog import AddToLeadBankDialog
from seo_audit_pro.map_harvest.ui.screen_input import InputScreen
from seo_audit_pro.map_harvest.ui.screen_results import ResultsScreen
from seo_audit_pro.ui.pages import registry
from seo_audit_pro.ui.pages.map_harvest import MapHarvestPage
from seo_audit_pro.ui.shell import navigation_for


class MapHarvestIntegrationTests(unittest.TestCase):
    def test_navigation_presence_for_all_roles(self):
        for role in ("MANAGER", "TEAM_LEAD", "AGENT"):
            nav = navigation_for(role)
            keys = [k for _sec, items in nav for k, _t, _i in items]
            self.assertIn("map_harvest", keys, f"map_harvest navigation missing for {role}")

    def test_page_registry(self):
        reg = registry()
        self.assertIn("map_harvest", reg)
        page_factory = reg["map_harvest"]
        page = page_factory(None)
        self.assertIsInstance(page, MapHarvestPage)
        self.assertEqual(page.title, "Map Harvest")
        self.assertEqual(page.nav_key, "map_harvest")

    def test_map_harvest_screens(self):
        page = MapHarvestPage(None)
        self.assertIsInstance(page.input_screen, InputScreen)
        self.assertIsInstance(page.results_screen, ResultsScreen)
        self.assertTrue(hasattr(page.results_screen, "lead_bank_btn"))

    def test_lead_bank_button_role_visibility(self):
        results = ResultsScreen()

        # Manager
        session.user = {"id": "usr_mgr", "role": "MANAGER", "is_active": 1}
        results._set_idle_mode()
        self.assertFalse(results.lead_bank_btn.isHidden())

        # Team Lead
        session.user = {"id": "usr_tl", "role": "TEAM_LEAD", "team_id": "team_1", "is_active": 1}
        results._set_idle_mode()
        self.assertFalse(results.lead_bank_btn.isHidden())

        # Agent (adds leads to themselves; the server checks the rest)
        session.user = {"id": "usr_ag", "role": "AGENT", "team_id": "team_1", "is_active": 1}
        results._set_idle_mode()
        self.assertFalse(results.lead_bank_btn.isHidden())

        # Scrape & Audit portal: no Lead Bank, an audit button instead
        results.quick_mode = True
        results._set_idle_mode()
        self.assertTrue(results.lead_bank_btn.isHidden())
        self.assertFalse(results.audit_btn.isHidden())

    def test_lead_bank_dialog_instantiation(self):
        sample = [
            {"name": "Alpha Roofing", "website": "https://alpha.com", "phone": "123-456", "address": "123 St"},
            {"name": "Beta Plumbing", "website": "https://beta.com", "phone": "789-012", "address": "456 St"},
        ]
        dlg = AddToLeadBankDialog(None, sample, default_domain="Roofing", default_area="Dallas")
        self.assertEqual(dlg.niche_input.text(), "Roofing")
        self.assertEqual(dlg.area_input.text(), "Dallas")
        self.assertEqual(len(dlg.results), 2)

    def test_core_parsing_and_filters(self):
        # Test filters
        spec = filters.normalize_spec({"min_rating": 4.0, "require_website": True})
        self.assertTrue(filters.cheap_pass({"rating": "4.5", "website": "https://test.com"}, spec))
        self.assertFalse(filters.cheap_pass({"rating": "3.5", "website": "https://test.com"}, spec))
        self.assertFalse(filters.cheap_pass({"rating": "4.5", "website": ""}, spec))

        # Test place url parsing
        url = "https://www.google.com/maps/place/Alpine+Roofing/data=!4m7!3m6!1s0x89d4cc9b150c8a9f:0xb0887b40788685bd!8m2!3d43.656728!4d-79.338035!19sChIJn4oMFZvM1IkRvYWGeEB7iLA"
        parsed = parse.parse_place_url(url)
        self.assertEqual(parsed["latitude"], "43.656728")
        self.assertEqual(parsed["longitude"], "-79.338035")
        self.assertEqual(parsed["place_id"], "ChIJn4oMFZvM1IkRvYWGeEB7iLA")


if __name__ == "__main__":
    unittest.main()
