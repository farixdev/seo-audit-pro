# -*- coding: utf-8 -*-
"""Every audit failure gets a plain reason (what happened, why, what to do)."""

import errno
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from seo_audit_pro.errors import explain  # noqa: E402


class ExplainTests(unittest.TestCase):
    def check(self, exc, code, *words):
        msg, got = explain(exc)
        self.assertEqual(got, code, msg)
        for w in words:
            self.assertIn(w.lower(), msg.lower())
        self.assertNotIn("unexpected error.", msg)   # never the old generic text

    def test_site_problems(self):
        self.check(ValueError("Could not fetch the site: HTTPSConnectionPool: Max retries exceeded (NameResolutionError: "
                              "Failed to resolve 'nosuch.example')"), "site_unreachable", "could not be found")
        self.check(ValueError("Could not fetch the site: Read timed out. (read timeout=25)"), "site_unreachable",
                   "too long")
        self.check(ValueError("Could not fetch the site: SSLError(CERTIFICATE_VERIFY_FAILED)"), "site_unreachable",
                   "certificate")
        self.check(ValueError("Could not fetch the site: Exceeded 30 redirects."), "site_unreachable", "loop")
        self.check(ValueError("Could not fetch the site: weird thing"), "site_unreachable", "weird thing")

    def test_http_status(self):
        self.check(ValueError("The URL did not return an HTML page (status 403)."), "http_403", "refused", "firewall")
        self.check(ValueError("The URL did not return an HTML page (status 404)."), "http_404", "not found")
        self.check(ValueError("The URL did not return an HTML page (status 429)."), "http_429", "too many")
        self.check(ValueError("The URL did not return an HTML page (status 503)."), "http_503", "server")
        self.check(ValueError("The URL did not return an HTML page (status 200)."), "not_html", "does not show a web page")
        self.check(ValueError("Please enter a website URL."), "no_url", "no website")

    def test_computer_problems(self):
        self.check(PermissionError(13, "Access is denied", "C:/x"), "permission", "antivirus")
        self.check(OSError(errno.ENOSPC, "No space left on device"), "disk_full", "disk is full")
        self.check(MemoryError(), "memory", "memory")
        self.check(ModuleNotFoundError("No module named 'docx'", name="docx"), "install_damaged", "extract", "docx")
        self.check(requests.ConnectionError("Failed to establish a new connection"), "network", "could not be reached")

    def test_unknown_errors_say_what_happened(self):
        msg, code = explain(TypeError("'lazy_import' object is not callable"))
        self.assertEqual(code, "unexpected")
        self.assertIn("TypeError", msg)
        self.assertIn("log", msg)


if __name__ == "__main__":
    unittest.main()
