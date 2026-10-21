# -*- coding: utf-8 -*-
"""
Crawler fetch limits (huge pages truncated, binary files never downloaded,
HTML sniffed from text/plain, declared charsets honoured) and the realtime hub
never blocking a publisher on a client that stopped reading.
"""

import http.server
import threading
import time
import unittest

from tests import helpers
import crawler
from backend import realtime

assert helpers.os.environ["SEO_DB"] == "sqlite"  # importing helpers isolated the environment

BIG = b"<!doctype html><html><head><title>Big</title></head><body>" + b"<p>row</p>" * 600_000 + b"</body></html>"
SERVED = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {
        "/big": ("text/html; charset=utf-8", BIG),
        "/file.zip": ("application/zip", b"PK" + b"\0" * 5_000_000),
        "/plain-html": ("text/plain", b"<!DOCTYPE html><html><head><title>Plain</title></head><body>x</body></html>"),
        "/data.xml": ("application/xml", b"<?xml version='1.0'?><root>" + b"<i/>" * 100_000 + b"</root>"),
        "/latin": ("text/html", "<html><head><meta charset=\"iso-8859-1\"><title>Café</title></head></html>"
                   .encode("iso-8859-1")),
    }

    def do_GET(self):  # noqa: N802
        ctype, body = self.routes.get(self.path, ("text/html", b"<html></html>"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        sent = 0
        try:
            for i in range(0, len(body), 65536):
                self.wfile.write(body[i:i + 65536])
                sent += len(body[i:i + 65536])
        except (ConnectionError, OSError):
            pass  # the client stopped reading — that is the point
        SERVED[self.path] = sent

    def log_message(self, *args):
        pass


class FetchLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_huge_page_is_truncated(self):
        page = crawler.fetch_page(self.base + "/big")
        self.assertTrue(page["is_html"])
        self.assertLessEqual(len(page["html"].encode("utf-8")), crawler.MAX_HTML_BYTES)
        self.assertIn("<title>Big</title>", page["html"])

    def test_binary_and_data_files_are_not_downloaded(self):
        page = crawler.fetch_page(self.base + "/file.zip")
        self.assertEqual((page["status"], page["is_html"], page["html"]), (200, False, ""))
        page = crawler.fetch_page(self.base + "/data.xml")
        self.assertFalse(page["is_html"])
        time.sleep(0.2)
        self.assertLess(SERVED.get("/file.zip", 0), 1_000_000)

    def test_html_served_as_text_plain_is_detected(self):
        page = crawler.fetch_page(self.base + "/plain-html")
        self.assertTrue(page["is_html"])
        self.assertIn("<title>Plain</title>", page["html"])

    def test_declared_charset_is_used(self):
        page = crawler.fetch_page(self.base + "/latin")
        self.assertIn("Café", page["html"])


class _StuckSocket:
    """A client socket whose send blocks (the peer stopped reading)."""

    def __init__(self):
        self.release = threading.Event()
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.release.wait(10)
        self.sent.append(payload)

    def close(self):
        self.closed = True
        self.release.set()


class RealtimeTests(unittest.TestCase):
    def test_publish_never_blocks_on_a_stalled_client(self):
        ws = _StuckSocket()
        conn = realtime._Conn(ws)
        realtime._register("usr_stuck", "c1", conn)
        try:
            t = time.monotonic()
            for i in range(realtime._OUTBOX + 50):
                realtime.publish(["usr_stuck"], {"type": "audit_progress", "n": i})
            self.assertLess(time.monotonic() - t, 1.0)  # publishers return immediately
            self.assertTrue(ws.closed)  # the stalled connection was closed instead
        finally:
            realtime._unregister("usr_stuck", "c1")
            conn.close()

    def test_frames_are_delivered_in_order(self):
        ws = _StuckSocket()
        ws.release.set()
        conn = realtime._Conn(ws)
        for i in range(20):
            conn.send(str(i))
        for _ in range(100):
            if len(ws.sent) == 20:
                break
            time.sleep(0.01)
        conn.close()
        self.assertEqual(ws.sent, [str(i) for i in range(20)])


if __name__ == "__main__":
    unittest.main()
