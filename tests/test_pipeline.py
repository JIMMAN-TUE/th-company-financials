import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from tests.helpers import ROBOTS, STATEMENT_HTML, URL, FakeClock, FakeTransport, make_cfg
from thfin.config import ConfigError
from thfin.http import PoliteClient
from thfin.pipeline import build_plan, import_files, run
from thfin.store import Store, write_csv, write_xlsx

HTML = STATEMENT_HTML.read_bytes()
J1, J2 = "0105500000001", "0105500000002"


def setup(routes, default=(404, {}, b""), **scope):
    tmp = Path(tempfile.mkdtemp())
    scope.setdefault("provinces", ["สมุทรปราการ", "สมุทรสาคร"])
    scope.setdefault("tsic_prefixes", ["10"])
    cfg = make_cfg(tmp, **scope)
    clock = FakeClock()
    transport = FakeTransport(routes, default)
    client = PoliteClient(cfg.runtime, transport, clock.sleep, clock.time)
    return cfg, client, Store(cfg.runtime.db_path), transport


class PipelineTests(unittest.TestCase):
    def test_end_to_end(self):
        cfg, client, store, t = setup({URL.format(jid=J1): (200, {}, HTML)})  # J2 -> 404
        s = run(cfg, client, store, progress=lambda _: None)
        self.assertEqual((s.attempted, s.ok, s.not_found, s.errors), (2, 1, 1, 0))
        rows = {r["fiscal_year"]: r for r in store.export_rows(cfg.scope)}
        self.assertEqual(sorted(rows), [2566, 2567])
        self.assertEqual(rows[2567]["revenue_total"], 1_200_000)
        self.assertEqual(rows[2567]["cost_of_sales"], 700_000)
        self.assertEqual(rows[2567]["gross_profit"], 500_000)
        self.assertAlmostEqual(rows[2567]["gross_margin_pct"], 41.67, places=2)
        self.assertAlmostEqual(rows[2567]["net_margin_pct"], 8.33, places=2)
        self.assertEqual(rows[2566]["net_profit"], -50_000)
        self.assertEqual(rows[2567]["province"], "สมุทรปราการ")

    def test_rerun_is_idempotent_and_resumable(self):
        cfg, client, store, t = setup({URL.format(jid=J1): (200, {}, HTML)})
        run(cfg, client, store, progress=lambda _: None)
        before = len(t.calls)
        s = run(cfg, client, store, progress=lambda _: None)
        self.assertEqual(s.attempted, 0)
        self.assertEqual(len(t.calls), before)
        self.assertEqual(store.counts()["financial_rows"], 2)

    def test_scope_years_limit_what_is_stored(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, HTML)}, fiscal_years=[2567])
        run(cfg, client, store, progress=lambda _: None)
        self.assertEqual([r["fiscal_year"] for r in store.export_rows(cfg.scope)], [2567])

    def test_limit(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, HTML)})
        s = run(cfg, client, store, limit=1, progress=lambda _: None)
        self.assertEqual(s.attempted, 1)

    def test_unparseable_page_is_marked_not_dropped(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, b"<html>no tables</html>")})
        s = run(cfg, client, store, progress=lambda _: None)
        self.assertEqual(s.parse_empty, 1)
        self.assertEqual(store.status_counts()["parse_empty"], 1)

    def test_circuit_breaker_stops_after_consecutive_errors(self):
        # every company page fails; robots.txt is fine
        cfg, client, store, t = setup(
            {ROBOTS: (404, {}, b"")}, default=(500, {}, b""), provinces=[], tsic_prefixes=[]
        )
        cfg.runtime.max_retries = 0
        s = run(cfg, client, store, progress=lambda _: None)
        self.assertEqual(s.errors, 2)
        self.assertIn("consecutive errors", s.aborted)
        self.assertLess(s.attempted, 5)

    def test_placeholder_user_agent_is_refused(self):
        cfg, client, store, _ = setup({})
        cfg.runtime.user_agent = "thfin-research/0.1 (+mailto:CHANGE-ME@example.com)"
        with self.assertRaises(ConfigError):
            run(cfg, client, store, progress=lambda _: None)

    def test_robots_block_stops_the_run(self):
        cfg, client, store, _ = setup({ROBOTS: (200, {}, b"User-agent: *\nDisallow: /")})
        s = run(cfg, client, store, progress=lambda _: None)
        self.assertIn("robots.txt", s.aborted)
        self.assertEqual(store.counts()["financial_rows"], 0)

    def test_unit_multiplier(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, HTML)}, fiscal_years=[2567])
        cfg.financials.unit_multiplier = 1000
        run(cfg, client, store, progress=lambda _: None)
        self.assertEqual(store.export_rows(cfg.scope)[0]["revenue_total"], 1_200_000_000)

    def test_revenue_band_applies_at_export(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, HTML)})
        run(cfg, client, store, progress=lambda _: None)
        cfg.scope.revenue_min = 1_100_000
        self.assertEqual([r["fiscal_year"] for r in store.export_rows(cfg.scope)], [2567])

    def test_plan_warns_on_truncation_and_estimates_time(self):
        cfg, _, store, _ = setup({}, provinces=[], tsic_prefixes=[], max_companies=2)
        plan = build_plan(cfg, store)
        self.assertEqual(plan.est_requests, 2)
        self.assertTrue(any("max_companies" in w for w in plan.warnings))
        self.assertAlmostEqual(plan.est_hours, 2 / cfg.runtime.requests_per_second / 3600)

    def test_exports(self):
        cfg, client, store, _ = setup({URL.format(jid=J1): (200, {}, HTML)})
        run(cfg, client, store, progress=lambda _: None)
        rows = store.export_rows(cfg.scope)
        d = Path(tempfile.mkdtemp())
        csv_p = write_csv(rows, d / "o.csv")
        xlsx_p = write_xlsx(rows, d / "o.xlsx")
        self.assertTrue(csv_p.read_bytes().startswith(b"\xef\xbb\xbf"))  # BOM for Excel
        self.assertIn("1200000", csv_p.read_text(encoding="utf-8-sig"))
        self.assertTrue(xlsx_p.stat().st_size > 0)


class ImportFilesTests(unittest.TestCase):
    def test_import_xlsx_by_filename_id(self):
        cfg, _, store, _ = setup({})
        folder = Path(tempfile.mkdtemp())
        cfg.financials.files_dir = str(folder)
        wb = Workbook()
        ws = wb.active
        ws.append(["รายการ", 2567, 2566])
        ws.append(["รายได้รวม", 900, 800])
        wb.save(folder / f"งบ_{J1}_export.xlsx")
        (folder / "no_id_here.xlsx").write_bytes(b"x")
        (folder / f"{J2}.html").write_bytes(HTML)
        s = import_files(cfg, store, progress=lambda _: None)
        self.assertEqual((s.ok, s.errors), (2, 0))
        got = {(r["juristic_id"], r["fiscal_year"]): r["revenue_total"] for r in store.export_rows(cfg.scope)}
        self.assertEqual(got[(J1, 2567)], 900)
        self.assertEqual(got[(J2, 2567)], 1_200_000)

    def test_missing_folder(self):
        cfg, _, store, _ = setup({})
        cfg.financials.files_dir = "/nonexistent"
        with self.assertRaises(ConfigError):
            import_files(cfg, store)


if __name__ == "__main__":
    unittest.main()
