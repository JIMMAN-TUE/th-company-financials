import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from tests.helpers import URL, FakeClock, FakeTransport, make_cfg
from thfin.http import PoliteClient
from thfin.pipeline import build_plan, run
from thfin.config import Scope
from thfin.store import Store

TODAY = date(2026, 10, 29)  # B.E. 2569 -> last closed year 2568
HTML = (Path(__file__).parent / "fixtures" / "statement_sample.html").read_bytes()
J1, J2 = "0105500000001", "0105500000002"


class EffectiveYearsTests(unittest.TestCase):
    def test_default_is_last_three_closed_years(self):
        self.assertEqual(Scope().effective_years(TODAY), [2566, 2567, 2568])

    def test_years_back(self):
        self.assertEqual(Scope(years_back=5).effective_years(TODAY), [2564, 2565, 2566, 2567, 2568])
        self.assertEqual(Scope(years_back=1).effective_years(TODAY), [2568])

    def test_latest_year_override_and_gregorian_conversion(self):
        self.assertEqual(Scope(latest_fiscal_year=2567).effective_years(TODAY), [2565, 2566, 2567])
        self.assertEqual(Scope(latest_fiscal_year=2024).effective_years(TODAY), [2565, 2566, 2567])

    def test_explicit_years_win(self):
        self.assertEqual(Scope(fiscal_years=[2567, 2564], years_back=9).effective_years(TODAY), [2564, 2567])

    def test_bounds(self):
        for bad in (0, 16):
            with self.assertRaises(ValidationError):
                Scope(years_back=bad)

    def test_year_rolls_over_with_the_calendar(self):
        self.assertEqual(Scope().effective_years(date(2027, 1, 2)), [2567, 2568, 2569])


class WideningYearsTests(unittest.TestCase):
    def test_done_ids_respects_requested_years(self):
        store = Store(Path(tempfile.mkdtemp()) / "s.sqlite")
        store.mark("A", "ok", years=[2567])
        store.mark("B", "not_found", years=[2567])
        store.mark("C", "parse_empty")
        store.mark("D", "error", "x", years=[2567])
        self.assertEqual(store.done_ids([2567]), {"A", "B", "C"})
        self.assertEqual(store.done_ids([2566, 2567]), {"B", "C"})  # A must be redone for 2566
        self.assertEqual(store.done_ids(), {"A", "B", "C"})

    def test_old_database_without_years_column_is_migrated(self):
        path = Path(tempfile.mkdtemp()) / "old.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE fetch_log (juristic_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT, fetched_at TEXT)")
        db.execute("INSERT INTO fetch_log VALUES ('X','ok','', 't')")
        db.commit()
        db.close()
        store = Store(path)
        self.assertEqual(store.done_ids([2567]), set())  # unknown coverage -> redo (cache makes it cheap)
        store.mark("X", "ok", years=[2567])
        self.assertEqual(store.done_ids([2567]), {"X"})

    def test_widening_reuses_the_cached_page_and_adds_the_older_year(self):
        tmp = Path(tempfile.mkdtemp())
        routes = {URL.format(jid=J1): (200, {}, HTML)}  # J2 -> 404
        cfg = make_cfg(tmp, provinces=["สมุทรปราการ", "สมุทรสาคร"], tsic_prefixes=["10"], fiscal_years=[2567])
        clock = FakeClock()
        transport = FakeTransport(routes)
        client = PoliteClient(cfg.runtime, transport, clock.sleep, clock.time)
        store = Store(cfg.runtime.db_path)
        run(cfg, client, store, progress=lambda _: None)
        self.assertEqual([r["fiscal_year"] for r in store.export_rows(cfg.scope)], [2567])
        calls_before = len(transport.calls)

        cfg.scope.fiscal_years = [2566, 2567]  # user widens the window
        plan = build_plan(cfg, store)
        self.assertEqual([c.juristic_id for c in plan.pending], [J1])  # J2 (404) is not redone
        summary = run(cfg, client, store, progress=lambda _: None)
        self.assertEqual((summary.attempted, summary.ok, summary.from_cache), (1, 1, 1))
        self.assertEqual(len(transport.calls), calls_before)  # no new network traffic
        self.assertEqual([r["fiscal_year"] for r in store.export_rows(cfg.scope)], [2566, 2567])
        self.assertEqual(build_plan(cfg, store).est_requests, 0)  # now fully covered


if __name__ == "__main__":
    unittest.main()
