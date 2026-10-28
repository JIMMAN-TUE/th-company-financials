"""Optional Chromium rendering in the thfin package, with the shared HTTP policy."""

from __future__ import annotations

import os
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import getproxies

from .config import ConfigError, RuntimeCfg
from .http import FetchError, UrllibTransport


def _proxy() -> dict | None:
    """Keep the session's proxy and NO_PROXY choices; never disable TLS verification."""
    proxies = getproxies()
    value = proxies.get("https") or proxies.get("http")
    if not value:
        return None
    try:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        host = parsed.hostname
        if not host:
            raise ValueError("missing proxy host")
        host = f"[{host}]" if ":" in host else host
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError as e:
        raise ConfigError("invalid HTTP(S) proxy configuration") from e
    result = {"server": f"{parsed.scheme}://{host}{port}"}
    if parsed.username:
        result["username"] = unquote(parsed.username)
        result["password"] = unquote(parsed.password or "")
    bypass = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
    if bypass:
        result["bypass"] = bypass
    return result


class ChromiumTransport:
    def __init__(self, runtime: RuntimeCfg, authorize: Callable[[str], None]):
        self.runtime, self.authorize = runtime, authorize
        self.robots_transport = UrllibTransport()

    def get(self, url: str, headers: dict[str, str], timeout: float):
        # Read robots as plain HTTP, without executing a page before knowing its policy.
        if urlsplit(url).path == "/robots.txt":
            return self.robots_transport.get(url, headers, timeout)
        try:
            from playwright.sync_api import Error, TimeoutError, sync_playwright
        except ImportError as e:
            raise ConfigError('Chromium requires pip install "thfin[browser]" and playwright install chromium') from e
        blocked: list[FetchError] = []
        initial = True

        def route_request(route):
            nonlocal initial
            request = route.request
            if request.resource_type in {"image", "font", "media"}:
                route.abort()
                return
            try:
                if initial and request.is_navigation_request() and request.url == url:
                    initial = False  # already authorized/throttled by PoliteClient.get
                else:
                    self.authorize(request.url)
                # Playwright routing is not called again for redirects. Inspect the response
                # without following it, so neither redirects nor subresource redirects escape policy.
                response = route.fetch(max_redirects=0, timeout=timeout * 1000)
                if 300 <= response.status < 400 and response.headers.get("location"):
                    destination = urljoin(request.url, response.headers["location"])
                    self.authorize(destination)
                    raise FetchError("Chromium redirect stopped; configure the final allowed URL directly")
            except FetchError as e:
                blocked.append(e)
                route.abort()
                return
            except Error:
                blocked.append(FetchError("Chromium resource request failed; check proxy/TLS and availability"))
                route.abort()
                return
            route.fulfill(response=response)

        try:
            with sync_playwright() as playwright:
                launch = {"headless": self.runtime.chromium_headless}
                if self.runtime.chromium_executable:
                    launch["executable_path"] = self.runtime.chromium_executable
                proxy = _proxy()
                if proxy:
                    launch["proxy"] = proxy
                browser = playwright.chromium.launch(**launch)
                try:
                    context = browser.new_context(user_agent=headers["User-Agent"],
                                                  service_workers="block", accept_downloads=False)
                    context.set_extra_http_headers({k: v for k, v in headers.items() if k != "User-Agent"})
                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda ws: ws.close())
                    page = context.new_page()
                    page.set_default_timeout(timeout * 1000)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    if blocked:
                        raise blocked[0]
                    if response is None:
                        raise FetchError("Chromium navigation returned no HTTP response")
                    resp_headers = response.all_headers()
                    if "html" not in resp_headers.get("content-type", "").lower():
                        body = response.body()
                    else:
                        if response.status == 200:
                            try:
                                page.wait_for_selector(self.runtime.chromium_wait_selector, state="attached")
                            except TimeoutError:
                                pass  # return the DOM so probe can report empty parsing
                        body = page.content().encode("utf-8")
                    if blocked:
                        raise blocked[0]
                    return response.status, resp_headers, body
                finally:
                    browser.close()
        except Error as e:
            if blocked:
                raise blocked[0] from e
            raise FetchError("Chromium failed; check browser installation, proxy/TLS and page availability") from e
