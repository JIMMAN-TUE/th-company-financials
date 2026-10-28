import tempfile
import threading
import unittest
from pathlib import Path

import yaml

from tests.helpers import REGISTRY_CSV, STATEMENT_HTML
from tests.test_browser import chromium_path
from thfin.app import AppController, prepare_workspace
from thfin.config import load_config
from thfin.uiserver import ScopeUI, make_server


class AppBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = chromium_path()
        if not cls.executable:
            raise unittest.SkipTest("install Chromium for the app UI integration test")

    def test_upload_filter_import_and_download_in_scope_widget(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = prepare_workspace(root)
            cfg = load_config(config)
            cfg.scope.fiscal_years = [2567]
            config.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True), encoding="utf-8")
            ui = ScopeUI(config)
            ui.app = AppController(ui, root)
            server = make_server(ui, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, executable_path=self.executable)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{ui.port}/")
                        page.wait_for_function("() => document.querySelector('#workspacePath').textContent.length > 0")
                        with page.expect_response(lambda r: r.url.endswith("/api/upload")):
                            page.locator("#registryUpload").set_input_files(REGISTRY_CSV)
                        page.wait_for_function("() => document.querySelector('#cfgpath').textContent.includes('7')")
                        for province in ("สมุทรปราการ", "สมุทรสาคร"):
                            page.locator("#provList label").filter(has_text=province).locator("input").check()
                        page.locator("#tsicList label").filter(has_text="10 ·").locator("input").check()
                        page.locator("#cap").fill("1")
                        page.wait_for_function("() => document.querySelector('#kMatched').textContent === '2' && document.querySelector('#kSelected').textContent === '1'")
                        with page.expect_response(lambda r: r.url.endswith("/api/upload")):
                            page.locator("#statementUpload").set_input_files({"name": "0105500000001.html",
                                                                             "mimeType": "text/html",
                                                                             "buffer": STATEMENT_HTML.read_bytes()})
                        page.locator("summary").filter(has_text="ใช้ไฟล์ที่ export ด้วยมือ").click()
                        page.locator("#importJob").click()
                        page.wait_for_function("() => document.querySelector('#jobStatus').textContent.includes('งานเสร็จแล้ว')")
                        page.wait_for_function("() => document.querySelector('#resultCount').textContent.includes('1')")
                        self.assertIn("0105500000001", page.locator("#resultWrap").inner_text())
                        self.assertNotIn("0105500000002", page.locator("#resultWrap").inner_text())
                        with page.expect_download() as download:
                            page.locator("#exportXlsx").click()
                        self.assertEqual(download.value.suggested_filename, "financials.xlsx")
                        self.assertGreater(Path(download.value.path()).stat().st_size, 0)
                        page.locator("#cap").fill("2")
                        page.wait_for_function("() => document.querySelector('#resultWrap').textContent.includes('ขอบเขตเปลี่ยนแล้ว')")
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                ui.app.shutdown()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
