"""Fictional DBD search/financial UI, including the real Chromium policy path."""
import csv
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests.test_browser import chromium_path
from thfin.config import Config
from thfin.dbd import DBDClient, DBDSession, parse_companies, save_registry
from thfin.http import FetchError, PoliteClient
from thfin.parsers.statement import parse_html

J1, J2 = "0105500000001", "0105500000002"
HEAD = ["เปรียบเทียบ", "ลำดับที่", "เลขทะเบียนนิติบุคคล", "ชื่อนิติบุคคล", "ประเภทนิติบุคคล", "สถานะ",
        "รหัสประเภทธุรกิจ", "ชื่อประเภทธุรกิจ", "จังหวัด", "ทุนจดทะเบียน (บาท)"]


def company_table(jid=J1, province="กรุงเทพมหานคร"):
    values = ["", "1", jid, "บริษัทสมมติ", "บริษัทจำกัด", "ยังดำเนินกิจการอยู่", "55101", "โรงแรม", province, "1,000,000.00"]
    return ("<table><thead><tr>" + "".join(f"<th>{v}</th>" for v in HEAD) + "</tr></thead><tbody><tr>"
            + "".join(f"<td>{v}</td>" for v in values) + "</tr></tbody></table>")


FINANCE = '''<table><thead><tr><th rowspan="2">หน่วย : บาท</th><th colspan="2">2567</th><th colspan="2">2568</th></tr>
<tr><th>จำนวนเงิน</th><th>%เปลี่ยนแปลง</th><th>จำนวนเงิน</th><th>%เปลี่ยนแปลง</th></tr></thead><tbody>
<tr><th scope="row">รายได้รวม</th><td>100,000</td><td>99</td><td>120,000</td><td>20</td></tr>
<tr><th scope="row">รายได้หลัก</th><td>90,000</td><td>50</td><td>110,000</td><td>22.22</td></tr>
<tr><th scope="row">ต้นทุนขาย</th><td>40,000</td><td>25</td><td>50,000</td><td>25</td></tr>
<tr><th scope="row">รายจ่ายรวม</th><td>70,000</td><td>33</td><td>80,000</td><td>14.29</td></tr>
<tr><th scope="row">กำไร(ขาดทุน) สุทธิ</th><td>(5,000)</td><td>-12</td><td>20,000</td><td>500</td></tr>
<tr><th scope="row">สินทรัพย์รวม</th><td>500,000</td><td>25</td><td>600,000</td><td>20</td></tr></tbody></table>'''


class DBDTests(unittest.TestCase):
    def test_header_mapping_preserves_ids_and_ignores_nonregistry_tables(self):
        found = parse_companies(company_table() + FINANCE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].juristic_id, J1)
        self.assertEqual(found[0].registered_capital, 1_000_000)
        self.assertEqual(found[0].province, "กรุงเทพมหานคร")

    def test_invalid_id_is_not_a_company(self):
        self.assertEqual(parse_companies(company_table("123")), [])

    def test_automatic_registry_contains_only_company_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "registry.csv"
            save_registry(path, parse_companies(company_table()))
            text = path.read_text(encoding="utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            self.assertEqual(rows[0]["juristic_id"], J1)
            self.assertNotIn("director", rows[0])
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_html_robots_challenge_is_never_treated_as_allow_all(self):
        cfg = Config()
        fake = mock.Mock()
        fake.get.return_value = (200, {}, b"<html><script>challenge()</script></html>")
        client = PoliteClient(cfg.runtime, transport=fake)
        with self.assertRaisesRegex(FetchError, "HTML/challenge"):
            client.get("https://datawarehouse.dbd.go.th/")
        self.assertEqual(fake.get.call_count, 1)


class DBDChromiumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = chromium_path()
        if not cls.executable:
            raise unittest.SkipTest("Chromium required")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requests = []
        seen = self.requests
        self.options = options = {"deny": False}
        table1, table2 = json.dumps(company_table()), json.dumps(company_table(J2))
        html = f'''<!doctype html><meta charset="utf-8"><body><div id="list">{company_table()}</div>
<nav><span>หน้า / 2</span><input type="number" value="1"></nav>
<h4>เลขทะเบียนนิติบุคคล : {J1}</h4><a href="#" onclick="document.querySelector('#finance').hidden=false">ข้อมูลงบการเงิน</a>
<div id="finance" hidden><select><option>2568</option><option>2567</option></select>
<span onclick="">งบแสดงฐานะการเงิน</span><span onclick="">งบกำไรขาดทุน</span>{FINANCE}</div>
<script>document.querySelector('input').onkeydown=function(event){{if(event.key==='Enter'){{document.querySelector('#list').innerHTML=this.value==='2'?{table2}:{table1};}}}};
document.querySelectorAll('td').forEach(e=>{{if(e.textContent==='{J1}')e.onclick=()=>{{document.querySelector('#list').hidden=true;document.querySelector('nav').hidden=true;}}}});</script>'''
        category_head = '<table><thead><tr><th>ลำดับที่</th><th>รหัสประเภทธุรกิจ</th><th>ชื่อประเภทธุรกิจ</th><th>จำนวนนิติบุคคล (ราย)</th></tr></thead><tbody>'
        category1 = category_head + '<tr><td>1</td><td>55000</td><td>หมวดสมมติ</td><td>0</td></tr><tr><td>2</td><td>55101</td><td>ธุรกิจสมมติ</td><td>9</td></tr></tbody></table>'
        category2 = category_head + '<tr><td>3</td><td>55102</td><td>ธุรกิจสมมติย่อย</td><td>1</td></tr></tbody></table>'
        category_html = ('<!doctype html><meta charset="utf-8"><div id="list">' + category1
                         + '</div><nav>หน้า / 2 <input type="number" value="1"></nav><script>'
                         + "document.querySelector('input').onkeydown=e=>{if(e.key==='Enter')document.querySelector('#list').innerHTML="
                         + json.dumps(category2) + ";};</script>")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                if self.path == "/robots.txt":
                    body = ("User-agent: *\nDisallow: /" if options["deny"] else "User-agent: *\nDisallow:").encode()
                    mime = "text/plain"
                else:
                    content = category_html if self.path.startswith('/biztype/') else html
                    if options.get("no_finance"):
                        content = content.replace('<div id="finance" hidden>', '<div id="finance" hidden><h3>ไม่พบข้อมูล</h3>')
                        content = content.replace('<span onclick="">งบกำไรขาดทุน</span>', '')
                    body = content.encode()
                    mime = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{server.server_port}"
        self.cfg = Config()
        self.cfg.runtime.user_agent = "fictional-test"
        self.cfg.runtime.dbd_headless = True
        self.cfg.runtime.chromium_executable = self.executable
        self.cfg.runtime.allowed_hosts = ["127.0.0.1"]
        self.cfg.runtime.cache_dir = self.tmp.name
        self.cfg.runtime.max_retries = 0
        self.cfg.scope.tsic_prefixes = ["55101"]
        self.cfg.scope.max_companies = 3
        patch = mock.patch("thfin.dbd._proxy", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def test_discovery_pages_and_financial_adapter_in_real_browser(self):
        with DBDSession(self.cfg, progress=lambda _: None, origin=self.origin) as session:
            result = session.discover()
            self.assertEqual([c.juristic_id for c in result.companies], [J1, J2])
            self.assertTrue(result.complete)
            self.assertEqual(result.pages, 2)
            client = DBDClient(session)
            response = client.get(self.origin + "/company/" + J1)
            self.assertEqual(parse_html(response.text)[2568]["revenue_total"], 120000)
            before = len(self.requests)
            self.assertTrue(client.get(response.url).from_cache)
            self.assertEqual(len(self.requests), before)
            self.assertNotIn("เลขทะเบียนนิติบุคคล", response.text)

    def test_cap_stops_before_next_page_and_reports_partial(self):
        self.cfg.scope.max_companies = 1
        with DBDSession(self.cfg, progress=lambda _: None, origin=self.origin) as session:
            result = session.discover()
        self.assertEqual(len(result.companies), 1)
        self.assertFalse(result.complete)
        self.assertEqual(result.pages, 1)

    def test_prefix_expands_all_category_pages_and_deduplicates_exact_codes(self):
        with DBDSession(self.cfg, progress=lambda _: None, origin=self.origin) as session:
            self.assertEqual(session.expand_tsic(["55", "55101"]), ["55101", "55102"])

    def test_company_with_no_filings_returns_empty_financials_without_timeout(self):
        self.options["no_finance"] = True
        with DBDSession(self.cfg, progress=lambda _: None, origin=self.origin) as session:
            self.assertEqual(parse_html(session.financials(J1)), {})

    def test_disallowed_robots_sends_no_search_request(self):
        self.options["deny"] = True
        with self.assertRaises(FetchError):
            with DBDSession(self.cfg, origin=self.origin, policy_checks=True):
                self.fail("must not open search")
        self.assertEqual(self.requests, ["/robots.txt"])

    def test_stop_prevents_search(self):
        with DBDSession(self.cfg, should_stop=lambda: True, origin=self.origin, policy_checks=True) as session:
            with self.assertRaisesRegex(FetchError, "cancelled"):
                session.discover()
        self.assertEqual(self.requests, ["/robots.txt"])
