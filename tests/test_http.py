import tempfile
import unittest

from tests.helpers import HOST, ROBOTS, URL, FakeClock, FakeTransport, make_cfg
from thfin.http import FetchError, HostNotAllowed, PoliteClient, RobotsDisallowed

OK = (200, {}, b"<html>ok</html>")


def client(routes=None, default=(404, {}, b""), **rt):
    cfg = make_cfg(tempfile.mkdtemp())
    for k, v in rt.items():
        setattr(cfg.runtime, k, v)
    clock = FakeClock()
    transport = FakeTransport(routes, default)
    return PoliteClient(cfg.runtime, transport, clock.sleep, clock.time), transport, clock


class HttpTests(unittest.TestCase):
    def test_host_allowlist(self):
        c, t, _ = client()
        with self.assertRaises(HostNotAllowed):
            c.get("https://example.com/x")
        self.assertEqual(t.calls, [])  # nothing left the machine

    def test_robots_disallow(self):
        routes = {ROBOTS: (200, {}, b"User-agent: *\nDisallow: /company/"), f"https://{HOST}/open": OK}
        c, _, _ = client(routes)
        with self.assertRaises(RobotsDisallowed):
            c.get(URL.format(jid="1"))
        self.assertEqual(c.get(f"https://{HOST}/open").status, 200)

    def test_missing_robots_means_allowed(self):
        c, _, _ = client({URL.format(jid="1"): OK})  # robots -> 404
        self.assertEqual(c.get(URL.format(jid="1")).status, 200)

    def test_forbidden_robots_means_disallowed(self):
        c, _, _ = client({ROBOTS: (403, {}, b""), URL.format(jid="1"): OK})
        with self.assertRaises(RobotsDisallowed):
            c.get(URL.format(jid="1"))

    def test_unreadable_robots_blocks_fetching(self):
        c, _, _ = client({ROBOTS: (500, {}, b"")}, max_retries=0)
        with self.assertRaises(FetchError):
            c.get(URL.format(jid="1"))

    def test_robots_can_be_turned_off(self):
        routes = {ROBOTS: (200, {}, b"User-agent: *\nDisallow: /"), URL.format(jid="1"): OK}
        c, t, _ = client(routes, respect_robots=False)
        self.assertEqual(c.get(URL.format(jid="1")).status, 200)
        self.assertNotIn(ROBOTS, t.calls)

    def test_retry_then_success_with_backoff(self):
        u = URL.format(jid="1")
        c, t, clock = client({u: [(503, {}, b""), (200, {}, b"fine")]})
        self.assertEqual(c.get(u).text, "fine")
        self.assertEqual(t.calls.count(u), 2)
        self.assertIn(1.0, clock.slept)  # 2**0 backoff

    def test_retry_after_header_is_honoured(self):
        u = URL.format(jid="1")
        c, _, clock = client({u: [(429, {"Retry-After": "7"}, b""), OK]})
        c.get(u)
        self.assertIn(7.0, clock.slept)

    def test_gives_up_after_max_retries(self):
        u = URL.format(jid="1")
        c, t, _ = client({u: (500, {}, b"")}, max_retries=2)
        with self.assertRaises(FetchError):
            c.get(u)
        self.assertEqual(t.calls.count(u), 3)

    def test_cache_avoids_second_request(self):
        u = URL.format(jid="1")
        c, t, _ = client({u: OK})
        first = c.get(u)
        second = c.get(u)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(t.calls.count(u), 1)
        c.get(u, use_cache=False)
        self.assertEqual(t.calls.count(u), 2)

    def test_404_is_cached_but_errors_are_not(self):
        u404, u500 = URL.format(jid="404"), URL.format(jid="500")
        c, t, _ = client({u404: (404, {}, b""), u500: [(500, {}, b""), (200, {}, b"ok")]}, max_retries=0)
        c.get(u404)
        self.assertTrue(c.get(u404).from_cache)
        with self.assertRaises(FetchError):
            c.get(u500)
        self.assertEqual(c.get(u500).status, 200)  # not poisoned by the earlier failure

    def test_rate_limit_spaces_requests(self):
        routes = {URL.format(jid=str(i)): OK for i in range(3)}
        c, _, clock = client(routes, requests_per_second=2)
        start = clock.now
        for i in range(3):
            c.get(URL.format(jid=str(i)))
        self.assertGreaterEqual(clock.now - start, 1.0)  # >= 2 gaps of 0.5 s


if __name__ == "__main__":
    unittest.main()
