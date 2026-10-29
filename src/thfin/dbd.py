"""DBD's public search UI, verified in Chromium on 2026-10-29.

Only company registry columns and financial tables leave the browser DOM.
No profile HTML (which contains directors) is saved.
"""
from __future__ import annotations

import csv
import re
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .browser import _proxy
from .config import ConfigError
from .http import FetchError, PoliteClient, Response, UrllibTransport
from .models import Company
from .parsers.numbers import parse_number
from .sources.registry import Matcher, norm_province

ORIGIN = "https://datawarehouse.dbd.go.th"


def parse_companies(html: str) -> list[Company]:
    """Read by column title, never by an unvalidated numeric cell position."""
    soup = BeautifulSoup(html, "html.parser")
    result = []
    fields = {"เลขทะเบียนนิติบุคคล": "juristic_id", "ชื่อนิติบุคคล": "name_th",
              "ประเภทนิติบุคคล": "juristic_type", "สถานะ": "status", "จังหวัด": "province",
              "รหัสประเภทธุรกิจ": "tsic_code", "ชื่อประเภทธุรกิจ": "tsic_desc",
              "ทุนจดทะเบียน (บาท)": "registered_capital"}
    for table in soup.select("table"):
        mapping = {}
        for tr in table.select("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]
            if "เลขทะเบียนนิติบุคคล" in cells:
                mapping = {i: fields[c] for i, c in enumerate(cells) if c in fields}
                continue
            if not mapping:
                continue
            row = {field: cells[i] for i, field in mapping.items() if i < len(cells)}
            if not re.fullmatch(r"[0-9]{13}", row.get("juristic_id", "")):
                continue
            row["registered_capital"] = parse_number(row.get("registered_capital", ""))
            result.append(Company(**row))
    return result


@dataclass
class Discovery:
    companies: list[Company]
    pages: int
    complete: bool
    # Counts are observed matching companies, not a claim about the entire country.


def save_registry(path: Path, companies: list[Company]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(Company.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(c) for c in companies)
    temp.replace(path)


class DBDSession(AbstractContextManager):
    """One browser per job, including scripts/XHR policy, rate limits and cancellation."""
    def __init__(self, cfg, progress=print, should_stop=lambda: False, origin=ORIGIN, policy_checks=False):
        self.cfg, self.progress, self.should_stop, self.origin = cfg, progress, should_stop, origin
        self.policy_checks = policy_checks
        self.policy = PoliteClient(cfg.runtime, transport=UrllibTransport())
        self.blocked = []
        self.assets = {}

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        if self.policy_checks and "CHANGE-ME" in self.cfg.runtime.user_agent:
            raise ConfigError("ใส่ข้อมูลติดต่อในตั้งค่าแหล่งข้อมูลก่อนค้น DBD")
        self.manager = sync_playwright().start()
        try:
            options = {"headless": (self.cfg.runtime.chromium_headless if self.policy_checks else
                                     self.cfg.runtime.dbd_headless)}
            if self.cfg.runtime.chromium_executable:
                options["executable_path"] = self.cfg.runtime.chromium_executable
            if self.policy_checks and _proxy():
                options["proxy"] = _proxy()
            self.browser = self.manager.chromium.launch(**options)
            if not self.policy_checks:
                # Owner-authorized native browser mode: normal Chromium identity and network
                # stack, including ordinary JavaScript. No robots preflight via another client.
                self.context = self.browser.new_context(accept_downloads=False)
                self.page = self.context.new_page()
                self.page.set_default_timeout(max(60000, self.cfg.runtime.timeout_seconds * 1000))
                return self
            # Read policy through Chromium's network stack with JS disabled. No challenge
            # execution and no redirect following before knowing the site's robots rules.
            session = self

            class RobotsTransport:
                def get(self, url, headers, timeout):
                    session.policy._check_host(url)
                    context = session.browser.new_context(java_script_enabled=False,
                                                          user_agent=headers["User-Agent"], service_workers="block")
                    try:
                        page = context.new_page()
                        session._guard(page, robots_url=url)
                        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                        if response is None:
                            raise FetchError("robots.txt returned no response")
                        return response.status, response.headers, response.body()
                    finally:
                        context.close()

            self.policy.transport = RobotsTransport()
            if not self.policy._robots_ok(self.origin + "/"):
                raise FetchError("DBD robots.txt ไม่อนุญาตให้ค้นอัตโนมัติ")
            self.context = self.browser.new_context(user_agent=self.cfg.runtime.user_agent,
                                                     service_workers="block", accept_downloads=False)
            self.context.route_web_socket("**/*", lambda ws: ws.close())
            self.page = self.context.new_page()
            self._guard(self.page)
            self.page.set_default_timeout(max(60000, self.cfg.runtime.timeout_seconds * 1000))
            return self
        except Exception:
            if hasattr(self, "browser"):
                self.browser.close()
            self.manager.stop()
            if self.blocked:
                raise FetchError(self.blocked[0])
            raise

    def __exit__(self, *args):
        self.browser.close()
        self.manager.stop()

    def _guard(self, page, robots_url=None):
        # CDP pauses *every redirect hop*. Continue with Chromium's own network stack;
        # APIRequestContext/route.fetch would send requests through Node instead.
        cdp = page.context.new_cdp_session(page)

        def paused(event):
            request, kind = event["request"], event.get("resourceType", "")
            url, request_id = request["url"], event["requestId"]
            try:
                if robots_url:
                    if url != robots_url and kind != "Document":
                        cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
                        return
                    if url != robots_url or event.get("redirectedRequestId"):
                        raise FetchError("robots.txt redirect stopped")
                else:
                    if self.should_stop():
                        raise FetchError("cancelled")
                    if kind in {"Image", "Font", "Media"} or urlsplit(url).hostname not in self.cfg.runtime.allowed_hosts:
                        cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
                        return
                    self.policy._authorize_browser_request(url)
                cdp.send("Fetch.continueRequest", {"requestId": request_id})
            except Exception as e:
                self.blocked.append(str(e))
                cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})

        def response_received(response):
            if (response.status in {401, 403, 429} or response.status >= 500) and not robots_url:
                self.blocked.append(f"DBD HTTP {response.status}; หยุดเพื่อไม่ส่งคำขอซ้ำ")

        cdp.on("Fetch.requestPaused", paused)
        cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        page.on("response", response_received)

    def check(self):
        if self.should_stop():
            raise FetchError("cancelled")
        if self.blocked:
            raise FetchError(self.blocked[0])

    def navigate(self, path):
        self.check()
        self.policy._throttle(self.origin + path)
        try:
            response = self.page.goto(self.origin + path, wait_until="networkidle")
            if response is not None and response.status >= 400:
                raise FetchError(f"DBD HTTP {response.status}: {self.page.url}")
        except Exception:
            self.check()
            raise
        self.check()
        close = self.page.get_by_role("button", name="ปิด", exact=True)
        if close.is_visible():
            close.click()
        cookies = self.page.get_by_role("button", name="ยอมรับเฉพาะที่จำเป็น", exact=True)
        if cookies.is_visible():
            cookies.click()

    def wait_list(self):
        self.page.wait_for_function("""() => {
          const t = [...document.querySelectorAll('table')].find(t => t.textContent.includes('เลขทะเบียนนิติบุคคล'));
          return t && !t.textContent.includes('Loading') &&
            ([...t.querySelectorAll('td')].some(c => /^[0-9]{13}$/.test(c.textContent.trim())) || t.textContent.includes('ไม่พบข้อมูล') || document.body.textContent.includes('0 รายการ'));
        }""")
        self.check()

    def expand_tsic(self, prefixes):
        codes = set()
        for prefix in dict.fromkeys(prefixes):
            if re.fullmatch(r"[0-9]{5}", prefix):
                codes.add(prefix)
                continue
            self.navigate("/biztype/searchBizInfo?keyword=" + quote(prefix))
            self.page.wait_for_function("""() => {
              const t = document.querySelector('table');
              return t && t.textContent.includes('จำนวนนิติบุคคล') && !t.textContent.includes('Loading') &&
                (t.querySelector('tbody td') !== null);
            }""")
            for number in range(1, 1001):
                self.check()
                table = self.page.locator("table").first
                rows = BeautifulSoup(table.inner_html(), "html.parser").select("tbody tr")
                for row in rows:
                    cells = [c.get_text(" ", strip=True) for c in row.select("td")]
                    if len(cells) > 3 and re.fullmatch(r"[0-9]{5}", cells[1]) and cells[1].startswith(prefix):
                        # The category screen includes empty hierarchy nodes such as 55000.
                        if parse_number(cells[3]) or not self.cfg.scope.statuses:
                            codes.add(cells[1])
                nav = self.page.get_by_role("navigation")
                total = re.search(r"/\s*([\d,]+)", nav.inner_text())
                total_pages = int(total[1].replace(",", "")) if total else 1
                if number >= total_pages:
                    break
                old = table.inner_text()
                nav.get_by_role("spinbutton").fill(str(number + 1))
                nav.get_by_role("spinbutton").press("Enter")
                self.page.wait_for_function("old => document.querySelector('table').innerText !== old && !document.querySelector('table').innerText.includes('Loading')", arg=old)
        self.progress(f"ขยายหมวดเป็นรหัส TSIC ย่อย {len(codes)} รหัส")
        return sorted(codes)

    def discover(self) -> Discovery:
        scope = self.cfg.scope
        if not scope.include_ids and not scope.tsic_prefixes:
            raise ConfigError("เลือกหมวด TSIC อย่างน้อยหนึ่งหมวด หรือระบุเลขทะเบียนก่อนค้น DBD")
        if scope.include_ids:
            companies = []
            wanted = sorted(set(scope.include_ids) - set(scope.exclude_ids))
            for jid in wanted[:scope.max_companies]:
                self.navigate("/juristic/searchInfo?keyword=" + jid)
                self.page.get_by_role("heading", name=re.compile("เลขทะเบียนนิติบุคคล.*" + jid)).wait_for()
                heading = self.page.get_by_role("heading", level=3)
                name = heading.first.inner_text().split(":", 1)[-1].strip() if heading.count() else ""
                companies.append(Company(juristic_id=jid, name_th=name))
            return Discovery(companies, len(companies), len(wanted) <= scope.max_companies)
        terms = self.expand_tsic(scope.tsic_prefixes)
        selected = {}
        pages = 0
        for term in dict.fromkeys(terms):
            self.check()
            suffix = "" if scope.include_ids else "&v=biz"
            self.navigate("/juristic/searchInfo?keyword=" + quote(term) + suffix)
            self.wait_list()
            if not parse_companies("<table>" + self.page.locator("table").first.inner_html() + "</table>"):
                continue
            if scope.provinces and not scope.include_ids:
                self.page.get_by_role("button", name=re.compile("^ตัวกรองข้อมูลเพิ่มเติม")).click()
                for province in scope.provinces:
                    combo = self.page.get_by_role("combobox").nth(1)
                    if combo.get_attribute("aria-expanded") != "true":
                        combo.click()
                    self.page.get_by_role("option", name=norm_province(province), exact=True).click()
                with self.page.expect_response(lambda r: r.request.resource_type in {"xhr", "fetch"}):
                    self.page.get_by_role("button", name="ค้นหาข้อมูล", exact=True).click()
                self.wait_list()
            previous = None
            for page_number in range(1, 1001):
                self.check()
                companies = parse_companies("<table>" + self.page.locator("table").first.inner_html() + "</table>")
                signature = tuple(c.juristic_id for c in companies)
                if previous == signature and signature:
                    raise FetchError("DBD ส่งหน้าซ้ำ; หยุดเพื่อไม่ข้ามหรือวนบริษัท")
                previous = signature
                pages += 1
                for company in companies:
                    matches = (company.juristic_id in scope.include_ids if scope.include_ids else Matcher(scope)(company))
                    if matches and company.juristic_id not in scope.exclude_ids:
                        selected[company.juristic_id] = company
                        if len(selected) >= scope.max_companies:
                            self.progress(f"พบตรงเงื่อนไข {len(selected)} บริษัท · ถึงเพดานที่เลือกแล้ว (ยังไม่ใช่ยอดทั้งหมด)")
                            return Discovery(list(selected.values()), pages, False)
                self.progress(f"TSIC/คำค้น {term} หน้า {page_number}: พบตรงเงื่อนไข {len(selected)} บริษัท")
                nav = self.page.get_by_role("navigation")
                total = re.search(r"/\s*([\d,]+)", nav.inner_text())
                total_pages = int(total.group(1).replace(",", "")) if total else 1
                if page_number >= total_pages:
                    if total_pages >= 1000:
                        raise FetchError("DBD จำกัด 1,000 หน้า กรุณาเลือกจังหวัดหรือหมวดให้แคบลง")
                    break
                first_id = signature[0] if signature else ""
                nav.get_by_role("spinbutton").fill(str(page_number + 1))
                nav.get_by_role("spinbutton").press("Enter")
                if first_id:
                    self.page.get_by_role("cell", name=first_id, exact=True).wait_for(state="detached")
                self.wait_list()
        return Discovery(list(selected.values()), pages, True)

    def financials(self, jid: str) -> str:
        # Resolve via the real search result, including DBD's entity-type prefix.
        self.navigate("/juristic/searchInfo?keyword=" + quote(jid))
        self.page.get_by_role("heading", name=re.compile("เลขทะเบียนนิติบุคคล.*" + jid)).wait_for()
        self.page.get_by_role("link", name="ข้อมูลงบการเงิน", exact=True).click()
        empty = self.page.get_by_role("heading", name="ไม่พบข้อมูล", exact=True)
        self.page.get_by_text("งบกำไรขาดทุน", exact=True).or_(empty).first.wait_for()
        if empty.is_visible():
            self.progress(f"{jid}: DBD ยังไม่มีงบการเงินเผยแพร่")
            return "<html><body></body></html>"
        select = self.page.get_by_role("combobox")
        available = [int(v) for v in select.locator("option").all_text_contents() if v.strip().isdigit()]
        # DBD normally exposes five filing years. Select older available anchors as needed.
        wanted = set(self.cfg.scope.effective_years())
        anchors = [max(available)] if available else []
        if available:
            anchors += sorted({min(available, key=lambda a: abs(a - y)) for y in wanted if y < max(available) - 4})
        output = []
        for anchor in anchors:
            self.check()
            select.select_option(label=str(anchor))
            for label, row_label in [("งบแสดงฐานะการเงิน", "สินทรัพย์รวม"), ("งบกำไรขาดทุน", "รายได้รวม")]:
                self.page.get_by_text(label, exact=True).click()
                table = self.page.locator("table").filter(has=self.page.get_by_role("rowheader", name=row_label, exact=True)).first
                table.wait_for()
                self.page.wait_for_function("""({anchor, label}) => [...document.querySelectorAll('table')].some(t => {
                  const years = [...t.querySelectorAll('thead th')].map(e => e.textContent.trim())
                    .filter(s => /^[0-9]{4}$/.test(s)).map(Number);
                  return years.length && Math.max(...years) === anchor &&
                    [...t.querySelectorAll('tbody th')].some(e => e.textContent.trim() === label) &&
                    !t.textContent.includes('Loading');
                })""", arg={"anchor": anchor, "label": row_label})
                self.check()
                output.append(table.evaluate("e => e.outerHTML"))
        return "<html><body>" + "\n".join(output) + "</body></html>"


class DBDClient:
    """Adapter to the existing resumable financial pipeline; cache financial tables only."""
    native_browser = True
    def __init__(self, session):
        self.session = session

    def get(self, url, *, use_cache=True):
        jid = re.search(r"([0-9]{13})$", url)
        if not jid:
            raise ConfigError("DBD financial URL must end in a 13-digit registration number")
        path = Path(self.session.cfg.runtime.cache_dir) / ("dbd-financial-v1-" + jid[1] + ".html")
        if use_cache and path.exists():
            return Response(url, 200, path.read_bytes(), from_cache=True)
        body = self.session.financials(jid[1]).encode("utf-8")
        # Empty parsing is diagnostic, never persisted as a successful cache entry.
        from .parsers.statement import parse_html
        if parse_html(body.decode()):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return Response(url, 200, body)
