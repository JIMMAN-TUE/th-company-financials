"""Polite HTTP client: host allowlist, robots.txt, rate limit, retries with backoff, disk cache.

Uses only the standard library so it runs anywhere (it honours HTTPS_PROXY / SSL_CERT_FILE).
The transport is injectable so tests never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .config import RuntimeCfg

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Network-level or policy failure."""


class HostNotAllowed(FetchError):
    pass


class RobotsDisallowed(FetchError):
    pass


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, str], bytes]:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # redirects must never escape the allowlist/robots checks


class UrllibTransport:
    def get(self, url: str, headers: dict[str, str], timeout: float):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read() or b""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(f"network error for {url}: {e}") from e


class PoliteClient:
    def __init__(
        self,
        rt: RuntimeCfg,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.rt = rt
        self._sleep, self._clock = sleep, clock
        self._last_request = -1e9
        self._robots: dict[str, RobotFileParser | bool] = {}
        self.cache_dir = Path(rt.cache_dir)
        self.network_calls = 0
        if transport is not None:
            self.transport = transport
        elif rt.transport == "chromium":
            from .browser import ChromiumTransport

            self.transport = ChromiumTransport(rt, self._authorize_browser_request)
        else:
            self.transport = UrllibTransport()

    def _authorize_browser_request(self, url: str) -> None:
        """Apply the same policy to browser redirects, scripts and XHRs before they leave."""
        self._check_host(url)
        if not self._robots_ok(url):
            raise RobotsDisallowed(f"robots.txt disallows browser request {url}")
        self._throttle(url)
        self.network_calls += 1

    # -- policy -------------------------------------------------------------------------
    def _check_host(self, url: str) -> str:
        host = urlparse(url).hostname or ""
        if host not in self.rt.allowed_hosts:
            raise HostNotAllowed(
                f"{host!r} is not in runtime.allowed_hosts {self.rt.allowed_hosts}. "
                "Add it deliberately after reading its terms (docs/LEGAL.md)."
            )
        return host

    def _robots_ok(self, url: str) -> bool:
        if not self.rt.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            status, _, body = self._network_get(f"{origin}/robots.txt")
            if status in (401, 403):
                self._robots[origin] = False  # everything disallowed
            elif status == 200:
                rp = RobotFileParser()
                rp.parse(body.decode("utf-8", errors="replace").splitlines())
                self._robots[origin] = rp
            elif 400 <= status < 500:
                self._robots[origin] = True  # no robots.txt -> allowed
            else:
                raise FetchError(f"cannot read robots.txt for {origin} (HTTP {status}); not fetching")
        verdict = self._robots[origin]
        if isinstance(verdict, bool):
            return verdict
        return verdict.can_fetch(self.rt.user_agent, url)

    # -- network ------------------------------------------------------------------------
    def _throttle(self, url: str) -> None:
        parsed = urlparse(url)
        robots = self._robots.get(f"{parsed.scheme}://{parsed.netloc}")
        crawl_delay = robots.crawl_delay(self.rt.user_agent) if isinstance(robots, RobotFileParser) else None
        interval = max(1.0 / self.rt.requests_per_second, crawl_delay or 0)
        wait = interval - (self._clock() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    def _network_get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        headers = {"User-Agent": self.rt.user_agent, "Accept": "text/html,application/json,*/*"}
        last_exc: Exception | None = None
        for attempt in range(self.rt.max_retries + 1):
            self._throttle(url)
            self.network_calls += 1
            try:
                status, resp_headers, body = self.transport.get(url, headers, self.rt.timeout_seconds)
            except (HostNotAllowed, RobotsDisallowed):
                raise
            except FetchError as e:
                last_exc, status, resp_headers, body = e, 0, {}, b""
            if status and status not in RETRY_STATUS:
                return status, resp_headers, body
            if attempt == self.rt.max_retries:
                break
            retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else 2.0**attempt
            self._sleep(min(delay, 120.0))
        if last_exc:
            raise FetchError(f"giving up on {url}: {last_exc}")
        raise FetchError(f"giving up on {url}: HTTP {status} after {self.rt.max_retries + 1} attempts")

    # -- cache --------------------------------------------------------------------------
    def _paths(self, url: str) -> tuple[Path, Path]:
        cache_key = url if self.rt.transport == "urllib" else f"chromium:{self.rt.chromium_wait_selector}:{url}"
        key = hashlib.sha256(cache_key.encode()).hexdigest()
        return self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.json"

    def get(self, url: str, *, use_cache: bool = True) -> Response:
        self._check_host(url)
        body_p, meta_p = self._paths(url)
        if use_cache and body_p.exists() and meta_p.exists():
            meta = json.loads(meta_p.read_text())
            return Response(url, meta["status"], body_p.read_bytes(), from_cache=True)
        if not self._robots_ok(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.rt.user_agent!r}")
        status, _, body = self._network_get(url)
        if status in (200, 404):  # only cache definitive answers
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            body_p.write_bytes(body)
            meta_p.write_text(json.dumps({"status": status, "url": url}))
        return Response(url, status, body)
