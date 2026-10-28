import base64
import csv
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import REGISTRY_CSV, STATEMENT_HTML, FakeClock, FakeTransport
from tests import test_ui
from thfin.app import AppController, prepare_workspace
from thfin.config import ConfigError, load_config
from thfin.http import PoliteClient
from thfin.models import Financials
from thfin.pipeline import build_plan
from thfin.store import Store
from thfin.uiserver import ScopeUI, make_server

J1, J2 = "0105500000001", "0105500000002"
SCOPE = {"provinces": ["สมุทรปราการ", "สมุทรสาคร"], "tsic_prefixes": ["10"], "fiscal_years": [2567]}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = prepare_workspace(self.root)
        self.ui = ScopeUI(self.config)
        self.app = self.ui.app = AppController(self.ui, self.root)
        self.addCleanup(self.app.shutdown)
        self.upload("registry", "fictional.csv", REGISTRY_CSV.read_bytes())

    def upload(self, kind, name, body):
        return self.app.upload({"kind": kind, "name": name, "content": base64.b64encode(body).decode()})

    def complete(self):
        self.app._thread.join(timeout=5)
        self.assertFalse(self.app._thread.is_alive())
        return self.app.status()

    def test_workspace_paths_are_independent_and_existing_config_preserved(self):
        cfg = load_config(self.config)
        self.assertEqual(Path(cfg.financials.files_dir), self.root / "statements")
        self.assertEqual(cfg.runtime.transport, "chromium")
        before = self.config.read_bytes()
        prepare_workspace(self.root)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(Path(cfg.runtime.db_path).exists())
        self.assertFalse((self.root / "cache" / "http").exists())

    def test_queue_uses_scope_snapshot_and_survives_restart(self):
        self.app.start({"action": "queue", "scope": SCOPE})
        job = self.complete()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["plan"]["selected"], 2)
        html = self.app.queue_html().decode()
        self.assertIn(J1, html)
        self.assertIn(J2, html)
        self.assertNotIn("0105500000003", html)
        restarted = AppController(self.ui, self.root)
        self.assertEqual(restarted.queue_html(), self.app.queue_html())
        with self.assertRaisesRegex(ConfigError, "ตัวกรองต่าง"):
            restarted._active_queue(load_config(self.config).scope)

    def test_import_results_and_exports_obey_company_year_and_revenue_scope(self):
        self.upload("statement", f"{J1}.html", STATEMENT_HTML.read_bytes())
        self.upload("statement", f"{J2}.html", STATEMENT_HTML.read_bytes())
        scope = {**SCOPE, "max_companies": 1}
        self.app.start({"action": "import", "scope": scope})
        job = self.complete()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["summary"]["attempted"], 1)
        rows = self.app.rows(scope)
        self.assertEqual([(r["juristic_id"], r["fiscal_year"]) for r in rows], [(J1, 2567)])
        self.assertIn("ตัวอย่างอาหาร", rows[0]["name_th"])
        # Existing financials from another scope must never leak into this export.
        cfg = load_config(self.config)
        store = Store(cfg.runtime.db_path)
        try:
            store.upsert_financials([Financials(juristic_id=J2, fiscal_year=2567, revenue_total=5)])
        finally:
            store.close()
        body, name, mime = self.app.export({"scope": scope, "format": "csv"})
        exported = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual([r["juristic_id"] for r in exported], [J1])
        self.assertEqual(name, "financials.csv")
        self.assertIn("text/csv", mime)
        self.assertEqual(self.app.rows({**scope, "revenue_min": 2_000_000}), [])
        self.assertEqual(self.app.rows({**scope, "fiscal_years": [2560]}), [])

    def test_collect_uses_saved_source_and_widget_scope(self):
        self.app.save_settings({"user_agent": "thfin-test/1 (+mailto:test@example.org)",
                                "url_template": "https://datawarehouse.dbd.go.th/company/{juristic_id}",
                                "transport": "chromium"})
        cfg = load_config(self.config)
        clock = FakeClock()
        routes = {cfg.financials.url_template.format(juristic_id=J1): (200, {}, STATEMENT_HTML.read_bytes())}
        transport = FakeTransport(routes)
        client = PoliteClient(cfg.runtime, transport=transport, clock=clock.time, sleep=clock.sleep)
        with mock.patch("thfin.app.PoliteClient", return_value=client):
            self.app.start({"action": "collect", "scope": {**SCOPE, "max_companies": 1}, "limit": 25})
            self.assertEqual(self.complete()["status"], "completed")
        self.assertEqual(len(self.app.rows(SCOPE)), 1)
        self.assertNotIn(cfg.financials.url_template.format(juristic_id=J2), transport.calls)

    def test_watch_cancel_and_concurrent_work_protection(self):
        self.app.start({"action": "queue", "scope": SCOPE})
        self.complete()
        downloads = self.root / "Downloads"
        downloads.mkdir()
        self.app.start({"action": "watch", "scope": SCOPE, "directory": str(downloads)})
        with self.assertRaises(ConfigError):
            self.app.start({"action": "import", "scope": SCOPE})
        with self.assertRaises(ConfigError):
            self.upload("registry", "second.csv", REGISTRY_CSV.read_bytes())
        with self.assertRaises(ConfigError):
            self.app.save_settings({})
        self.app.cancel()
        self.assertEqual(self.complete()["status"], "cancelled")

    def test_stopping_collection_preserves_completed_company_and_skips_next(self):
        self.app.save_settings({"user_agent": "thfin-test (+mailto:test@example.org)"})
        cfg = load_config(self.config)
        first = cfg.financials.url_template.format(juristic_id=J1)
        second = cfg.financials.url_template.format(juristic_id=J2)
        transport = FakeTransport({first: (200, {}, STATEMENT_HTML.read_bytes()),
                                   second: (200, {}, STATEMENT_HTML.read_bytes())})
        original = transport.get

        def stop_after_first(*args):
            response = original(*args)
            if args[0] == first:
                self.app.cancel()
            return response

        transport.get = stop_after_first
        clock = FakeClock()
        client = PoliteClient(cfg.runtime, transport, sleep=clock.sleep, clock=clock.time)
        with mock.patch("thfin.app.PoliteClient", return_value=client):
            self.app.start({"action": "collect", "scope": SCOPE})
            job = self.complete()
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["summary"]["attempted"], 1)
        self.assertNotIn(second, transport.calls)
        self.assertEqual([r["juristic_id"] for r in self.app.rows(SCOPE)], [J1])

    def test_explicit_reprocessing_applies_corrected_units_to_completed_companies(self):
        self.app.save_settings({"user_agent": "thfin-test (+mailto:test@example.org)"})
        cfg = load_config(self.config)
        url = cfg.financials.url_template.format(juristic_id=J1)
        transport = FakeTransport({url: (200, {}, STATEMENT_HTML.read_bytes())})
        clock = FakeClock()
        scope = {**SCOPE, "max_companies": 1}
        def factory(rt):
            return PoliteClient(rt, transport, sleep=clock.sleep, clock=clock.time)
        with mock.patch("thfin.app.PoliteClient", side_effect=factory):
            self.app.start({"action": "collect", "scope": scope})
            self.assertEqual(self.complete()["summary"]["ok"], 1)
            self.app.save_settings({"unit_multiplier": 1000})
            self.app.start({"action": "collect", "scope": scope})
            self.assertEqual(self.complete()["summary"]["attempted"], 0)
            self.app.start({"action": "collect", "scope": scope, "refetch": True})
            job = self.complete()
        self.assertEqual(job["summary"]["ok"], 1)
        self.assertEqual(job["plan"]["pending"], 1)
        self.assertEqual(transport.calls.count(url), 2)
        self.assertEqual(self.app.rows(scope)[0]["revenue_total"], 1_200_000_000)

    def test_upload_rejects_traversal_unidentified_statements_and_invalid_csv(self):
        for name in ("../outside.csv", "..\\outside.csv", "C:outside.csv", ""):
            with self.assertRaises(ConfigError):
                self.upload("registry", name, b"x")
        with self.assertRaises(ConfigError):
            self.upload("statement", "unknown.html", b"x")
        with self.assertRaises(ConfigError):
            self.upload("registry", "bad.csv", b"not,the,right,columns\n")
        self.assertFalse((self.root / "registry" / "bad.csv").exists())
        self.assertFalse(list((self.root / "registry").glob("*.tmp")))

    def test_settings_cannot_add_hosts_or_change_cap_and_scope(self):
        before = load_config(self.config)
        with self.assertRaises(ConfigError):
            self.app.save_settings({"url_template": "https://outside.example/{juristic_id}"})
        self.app.save_settings({"user_agent": "fictional-test-agent", "transport": "urllib"})
        after = load_config(self.config)
        self.assertEqual(after.scope, before.scope)
        self.assertEqual(after.runtime.allowed_hosts, before.runtime.allowed_hosts)
        self.assertEqual(after.runtime.user_agent, "fictional-test-agent")

    def test_scope_preview_matches_actual_job_selection(self):
        preview = self.ui.preview(SCOPE)
        cfg = load_config(self.config)
        cfg.scope = cfg.scope.model_validate(SCOPE)
        plan = build_plan(cfg)
        self.assertEqual((preview["matched"], preview["selected"]), (2, 2))
        self.assertAlmostEqual(preview["est_hours"], round(plan.est_hours, 3))


class AppApiTests(unittest.TestCase):
    call = test_ui.UiTests.call

    def setUp(self):
        # Reuse the established host/token checks against an app-enabled server.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg_path = prepare_workspace(self.root)
        self.ui = ScopeUI(self.cfg_path)
        self.app = self.ui.app = AppController(self.ui, self.root)
        self.addCleanup(self.app.shutdown)
        self.server = make_server(self.ui, 0)
        self.port = self.ui.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # Only API behavior is shared; the original UiTests' fixture-dependent assertions stay there.
    def test_app_apis_require_session_token_and_correct_host(self):
        for path in ("/api/job", "/api/settings"):
            self.assertEqual(self.call("GET", path, token=False)[0], 403)
            self.assertEqual(self.call("GET", path, host="evil.example")[0], 403)
            self.assertEqual(self.call("GET", path)[0], 200)
        for path in ("/api/job/start", "/api/job/stop", "/api/upload", "/api/export", "/api/settings"):
            self.assertEqual(self.call("POST", path, {}, token=False)[0], 403)

    def test_app_page_includes_controls_and_script(self):
        status, html = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn('id="collectJob"', html)
        self.assertIn('src="/app.js"', html)
        self.assertNotIn("__APP_", html)
        status, js = self.call("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("/api/job/start", js)

    def test_invalid_actions_and_json_types_do_not_start_work(self):
        self.assertEqual(self.call("POST", "/api/job/start", {"action": "shell"})[0], 400)
        self.assertEqual(self.app.status()["status"], "idle")
