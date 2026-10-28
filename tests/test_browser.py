"""Optional real Chromium tests; all endpoints and numbers are fictional, on loopback."""

import importlib.util
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests.helpers import STATEMENT_HTML, FakeClock, make_cfg
from thfin.browser import ChromiumTransport, _proxy
from thfin.http import FetchError, HostNotAllowed, PoliteClient, RobotsDisallowed
from thfin.parsers.statement import parse_html


def chromium_path():
    if importlib.util.find_spec("playwright") is None:
        return None
    configured = os.environ.get("THFIN_TEST_CHROMIUM") or shutil.which("chromium")
    if configured:
        return configured
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        candidate = p.chromium.executable_path
        return candidate if Path(candidate).is_file() else None


class BrowserUnitTests(unittest.TestCase):
    def test_proxy_keeps_credentials_and_bypass(self):
        with mock.patch("thfin.browser.getproxies", return_value={"https": "http://user:p%40ss@proxy.test:8080"}), \
                mock.patch.dict(os.environ, {"no_proxy": "localhost,127.0.0.1"}):
            self.assertEqual(_proxy(), {"server": "http://proxy.test:8080", "username": "user",
                                        "password": "p@ss", "bypass": "localhost,127.0.0.1"})

    def test_chromium_uses_plain_transport_for_robots(self):
        cfg = make_cfg()
        transport = ChromiumTransport(cfg.runtime, lambda _: self.fail("not a page request"))
        with mock.patch.object(transport.robots_transport, "get", return_value=(403, {}, b"blocked")) as fetch:
            self.assertEqual(transport.get("https://example.org/robots.txt", {}, 2)[0], 403)
            fetch.assert_called_once()

    def test_config_selects_chromium_without_launching_it(self):
        cfg = make_cfg()
        cfg.runtime.transport = "chromium"
        client = PoliteClient(cfg.runtime)
        self.assertIsInstance(client.transport, ChromiumTransport)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install thfin[browser] for Chromium tests")
class ChromiumIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = chromium_path()
        if not cls.executable:
            raise unittest.SkipTest("Chromium not installed; run playwright install chromium")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        seen = self.requests
        self.options = options = {"redirect_robots": False}
        statement = STATEMENT_HTML.read_text(encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                if self.path == "/robots.txt":
                    if options["redirect_robots"]:
                        self.send_response(302)
                        self.send_header("Location", "/forbidden")
                        self.end_headers()
                        return
                    body = b"User-agent: *\nDisallow: /forbidden\nCrawl-delay: 1"
                    content_type = "text/plain"
                elif self.path in {"/redirect", "/allowed-redirect"}:
                    self.send_response(302)
                    self.send_header("Location", "/forbidden" if self.path == "/redirect" else "/render")
                    self.end_headers()
                    return
                else:
                    if self.path == "/blocked":
                        script = 'fetch("/forbidden");'
                    elif self.path == "/cross-host":
                        script = f'fetch("http://localhost:{self.server.server_port}/side");'
                    else:
                        script = ""
                    body = ("<!doctype html><meta charset='utf-8'><script>" + script
                            + "setTimeout(() => {document.body.innerHTML = " + json.dumps(statement)
                            + ";}, 100);</script><body>Loading fictional statement</body>").encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.base = f"http://127.0.0.1:{server.server_port}"
        self.cfg = make_cfg(self.temp.name)
        self.cfg.runtime.transport = "chromium"
        self.cfg.runtime.chromium_executable = self.executable
        self.cfg.runtime.allowed_hosts = ["127.0.0.1"]
        self.cfg.runtime.timeout_seconds = 2
        self.cfg.runtime.max_retries = 0
        self.clock = FakeClock()
        self.client = PoliteClient(self.cfg.runtime, sleep=self.clock.sleep, clock=self.clock.time)
        # Loopback fixtures only. External proxy behavior is covered separately above.
        proxy = mock.patch("thfin.browser._proxy", return_value=None)
        proxy.start()
        self.addCleanup(proxy.stop)

    def test_javascript_rendered_table_is_parsed_and_cached(self):
        response = self.client.get(self.base + "/render")
        self.assertEqual(response.status, 200)
        self.assertEqual(parse_html(response.text)[2567]["revenue_total"], 1_200_000)
        self.assertEqual(self.requests, ["/robots.txt", "/render"])
        self.assertEqual(self.clock.slept, [1])
        self.assertTrue(self.client.get(self.base + "/render").from_cache)
        self.assertEqual(self.requests, ["/robots.txt", "/render"])

    def test_xhr_respects_robots_before_request_is_sent(self):
        with self.assertRaises(RobotsDisallowed):
            self.client.get(self.base + "/blocked")
        self.assertNotIn("/forbidden", self.requests)
        self.assertEqual(self.requests.count("/blocked"), 1)

    def test_cross_host_xhr_is_blocked_by_allowlist(self):
        with self.assertRaises(HostNotAllowed):
            self.client.get(self.base + "/cross-host")
        self.assertNotIn("/side", self.requests)

    def test_allowed_redirect_stops_without_following(self):
        with self.assertRaisesRegex(FetchError, "Chromium redirect stopped"):
            self.client.get(self.base + "/allowed-redirect")
        self.assertNotIn("/render", self.requests)

    def test_redirect_respects_robots(self):
        with self.assertRaises(RobotsDisallowed):
            self.client.get(self.base + "/redirect")
        self.assertNotIn("/forbidden", self.requests)

    def test_robots_redirect_is_not_followed(self):
        self.options["redirect_robots"] = True
        with self.assertRaisesRegex(FetchError, "cannot read robots.txt"):
            self.client.get(self.base + "/render")
        self.assertEqual(self.requests, ["/robots.txt"])


if __name__ == "__main__":
    unittest.main()
