import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup

from tests.helpers import FakeClock, STATEMENT_HTML, make_cfg
from tests.test_cli import call, write_cfg
from thfin.config import ConfigError, Scope
from thfin.manual import DownloadWatcher, _move_export, create_queue, queue_lock, state_path, watch_downloads
from thfin.pipeline import import_files
from thfin.store import Store

J1, J2 = "0105500000001", "0105500000002"


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = make_cfg(self.root, provinces=["สมุทรปราการ", "สมุทรสาคร"], tsic_prefixes=["10"])
        self.cfg.financials.files_dir = str(self.root / "statements")
        self.cfg.financials.url_template = "https://example.org/company/{juristic_id}?a=1&b=2"
        self.downloads = self.root / "Downloads with spaces"
        self.downloads.mkdir()
        self.queue = self.root / "queue.html"
        self.clock = FakeClock()
        self.messages = []

    def create(self):
        return create_queue(self.cfg, self.queue)

    def watcher(self):
        return DownloadWatcher(self.cfg, self.downloads, self.queue, clock=self.clock.time,
                               progress=self.messages.append)

    def settle(self, watcher):
        watcher.poll()
        self.clock.sleep(2)
        return watcher.poll()

    def state(self):
        return json.loads(state_path(self.queue).read_text(encoding="utf-8"))

    def test_scope_links_names_and_cap_without_network_or_database(self):
        self.cfg.scope.max_companies = 1
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("offline only")):
            self.assertEqual(self.create(), 1)
        soup = BeautifulSoup(self.queue.read_text(encoding="utf-8"), "html.parser")
        self.assertIn("ตัวอย่างอาหาร", soup.get_text())
        links = soup.find_all("a")
        self.assertEqual([a["href"] for a in links], [f"https://example.org/company/{J1}?a=1&b=2"])
        self.assertIn(J1, soup.get_text())
        self.assertNotIn(J2, soup.get_text())
        self.assertFalse(Path(self.cfg.runtime.db_path).exists())
        self.assertFalse(Path(self.cfg.runtime.cache_dir).exists())

    def test_escapes_company_name_and_url(self):
        registry = self.root / "fictional.csv"
        registry.write_text(f'JURISTIC_ID,JURISTIC_NAME_TH\n{J1},<script>alert(1)</script>\n', encoding="utf-8")
        self.cfg.registry.csv_paths = [str(registry)]
        self.cfg.scope = Scope(include_ids=[J1], juristic_types=[], statuses=[])
        self.cfg.financials.url_template = 'https://example.org/{juristic_id}?x="onclick="bad'
        self.create()
        soup = BeautifulSoup(self.queue.read_text(encoding="utf-8"), "html.parser")
        self.assertIsNone(soup.find("script"))
        self.assertIn("<script>alert(1)</script>", soup.get_text())
        self.assertNotIn("onclick", soup.find("a").attrs)

    def test_bad_template_creates_no_queue(self):
        for template in ("javascript:alert(1)", "https://example.org/{unknown}", "https://user:pass@example.org/"):
            self.cfg.financials.url_template = template
            with self.assertRaises(ConfigError):
                self.create()
            self.assertFalse(self.queue.exists())
            self.assertFalse(state_path(self.queue).exists())

    def test_queue_regeneration_preserves_progress(self):
        self.create()
        original = state_path(self.queue).read_bytes()
        with self.assertRaises(ConfigError):
            self.create()
        self.assertEqual(state_path(self.queue).read_bytes(), original)

    def test_moves_new_stable_files_in_order_then_imports_offline(self):
        self.create()
        watcher = self.watcher()
        first = self.downloads / "รายงาน.HTML"
        first.write_bytes(STATEMENT_HTML.read_bytes())
        self.assertEqual(watcher.poll(), 0)
        self.clock.sleep(2)
        self.assertEqual(watcher.poll(), 1)
        self.assertFalse(first.exists())
        self.assertEqual(self.state()["next"], 1)
        # Restart continues the same saved FIFO, rather than reading a changed scope.
        self.cfg.scope.include_ids = [J1]
        watcher = self.watcher()
        (self.downloads / "second.html").write_bytes(STATEMENT_HTML.read_bytes())
        self.assertEqual(self.settle(watcher), 1)
        self.assertTrue(watcher.done)
        store = Store(self.cfg.runtime.db_path)
        self.addCleanup(store.close)
        self.assertEqual(import_files(self.cfg, store, progress=lambda _: None).ok, 2)
        self.assertEqual({r["juristic_id"] for r in store.export_rows(self.cfg.scope)}, {J1, J2})

    def test_old_files_and_partial_or_unrelated_extensions_ignored(self):
        self.create()
        old = self.downloads / "old.xlsx"
        old.write_bytes(b"old")
        watcher = self.watcher()
        old.write_bytes(b"modified old file")
        for name in ("export.xlsx.crdownload", "export.html.part", "notes.txt"):
            (self.downloads / name).write_bytes(b"in progress")
        self.assertEqual(self.settle(watcher), 0)
        self.assertEqual(self.state()["next"], 0)
        # A browser's rename to its final extension is discovered as a new file.
        (self.downloads / "export.xlsx.crdownload").rename(self.downloads / "export.xlsx")
        self.assertEqual(self.settle(watcher), 1)
        self.assertEqual((Path(self.cfg.financials.files_dir) / f"{J1}.xlsx").read_bytes(), b"in progress")
        self.assertTrue(old.exists())

    def test_growing_empty_and_active_partial_files_wait(self):
        self.create()
        watcher = self.watcher()
        path = self.downloads / "export.xlsx"
        path.touch()
        self.assertEqual(self.settle(watcher), 0)
        path.write_bytes(b"first")
        watcher.poll()
        self.clock.sleep(2)
        path.write_bytes(b"first-second")
        self.assertEqual(watcher.poll(), 0)
        self.clock.sleep(2)
        marker = self.downloads / "export.xlsx.crdownload"
        marker.touch()
        self.assertEqual(watcher.poll(), 0)
        marker.unlink()
        self.assertEqual(watcher.poll(), 1)

    def test_later_download_cannot_overtake_growing_first(self):
        self.create()
        watcher = self.watcher()
        first = self.downloads / "first.html"
        first.write_bytes(b"first")
        watcher.poll()
        second = self.downloads / "second.html"
        second.write_bytes(b"second")
        watcher.poll()
        self.clock.sleep(2)
        first.write_bytes(b"first updated")
        self.assertEqual(watcher.poll(), 0)
        self.clock.sleep(2)
        self.assertEqual(watcher.poll(), 2)
        dest = Path(self.cfg.financials.files_dir)
        self.assertEqual((dest / f"{J1}.html").read_bytes(), b"first updated")
        self.assertEqual((dest / f"{J2}.html").read_bytes(), b"second")

    def test_collision_never_overwrites_or_advances_queue(self):
        self.create()
        watcher = self.watcher()
        dest = Path(self.cfg.financials.files_dir)
        dest.mkdir()
        target = dest / f"{J1}.html"
        target.write_bytes(b"existing")
        source = self.downloads / "export.html"
        source.write_bytes(b"new")
        with self.assertRaisesRegex(ConfigError, "destination exists"):
            self.settle(watcher)
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(source.read_bytes(), b"new")
        self.assertEqual(self.state()["next"], 0)

    def test_move_error_and_restart_retry_same_company(self):
        self.create()
        watcher = self.watcher()
        source = self.downloads / "export.xlsx"
        source.write_bytes(b"fictional")
        with mock.patch("thfin.manual._move_export", side_effect=PermissionError("locked by Excel")):
            with self.assertRaisesRegex(ConfigError, "move failed"):
                self.settle(watcher)
        self.assertEqual(self.state()["next"], 0)
        self.assertIsNotNone(self.state()["inflight"])
        watcher = self.watcher()
        self.assertEqual(self.settle(watcher), 1)
        self.assertEqual(self.state()["next"], 1)

    def test_destination_created_during_move_is_not_overwritten(self):
        self.create()
        watcher = self.watcher()
        source = self.downloads / "export.xlsx"
        source.write_bytes(b"new export")

        def raced_move(src, target):
            target.write_bytes(b"another writer")
            _move_export(src, target)

        with mock.patch("thfin.manual._move_export", side_effect=raced_move):
            with self.assertRaisesRegex(ConfigError, "move failed"):
                self.settle(watcher)
        self.assertEqual((Path(self.cfg.financials.files_dir) / f"{J1}.xlsx").read_bytes(), b"another writer")
        self.assertEqual(source.read_bytes(), b"new export")
        self.assertEqual(self.state()["next"], 0)

    def test_restart_recovers_move_completed_before_progress_save(self):
        self.create()
        watcher = self.watcher()
        (self.downloads / "export.xlsx").write_bytes(b"fictional")
        with mock.patch.object(watcher, "_finish", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.settle(watcher)
        self.assertEqual(self.state()["next"], 0)
        self.watcher()
        self.assertEqual(self.state()["next"], 1)
        self.assertIsNone(self.state()["inflight"])

    def test_empty_queue_finishes_without_moving_anything(self):
        self.cfg.scope.name_contains = ["no fictional match"]
        self.assertEqual(self.create(), 0)
        watcher = self.watcher()
        (self.downloads / "export.xlsx").write_bytes(b"leave here")
        self.assertTrue(watcher.done)
        self.assertEqual(self.settle(watcher), 0)
        self.assertFalse(Path(self.cfg.financials.files_dir).exists())

    def test_extra_files_after_queue_complete_remain_in_downloads(self):
        self.cfg.scope.max_companies = 1
        self.create()
        watcher = self.watcher()
        (self.downloads / "first.xlsx").write_bytes(b"one")
        self.settle(watcher)
        extra = self.downloads / "extra.html"
        extra.write_bytes(b"two")
        self.assertEqual(self.settle(watcher), 0)
        self.assertTrue(extra.exists())

    def test_invalid_directories_config_and_state(self):
        self.create()
        self.cfg.financials.files_dir = str(self.root / "different")
        with self.assertRaisesRegex(ConfigError, "differs"):
            self.watcher()
        self.cfg.financials.files_dir = str(self.downloads)
        state = self.state()
        state["files_dir"] = str(self.downloads)
        state_path(self.queue).write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "must differ"):
            self.watcher()
        state_path(self.queue).write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "cannot read queue state"):
            self.watcher()
        with self.assertRaisesRegex(ConfigError, "not found"):
            DownloadWatcher(self.cfg, self.root / "missing", self.queue)

    def test_single_watcher_lock_and_cleanup_on_interrupt(self):
        self.create()
        with self.assertRaises(KeyboardInterrupt):
            with queue_lock(self.queue):
                with self.assertRaisesRegex(ConfigError, "locked"):
                    with queue_lock(self.queue):
                        pass
                raise KeyboardInterrupt
        with queue_lock(self.queue):
            pass

    def test_watch_loop_uses_polling_and_releases_lock(self):
        self.cfg.scope.max_companies = 1
        self.create()

        def sleep(seconds):
            self.clock.sleep(seconds)
            if not (self.downloads / "export.xlsx").exists():
                (self.downloads / "export.xlsx").write_bytes(b"fictional")

        with mock.patch("thfin.manual.time.sleep", side_effect=sleep), mock.patch(
            "thfin.manual.time.monotonic", side_effect=self.clock.time
        ):
            # Pass clock explicitly because the constructor's default was bound at import time.
            real = DownloadWatcher
            with mock.patch("thfin.manual.DownloadWatcher", side_effect=lambda *a, **kw:
                            real(*a, **kw, clock=self.clock.time, progress=self.messages.append)):
                watch_downloads(self.cfg, self.downloads, self.queue)
        self.assertEqual(self.state()["next"], 1)
        with queue_lock(self.queue):
            pass

    def test_cli_creates_queue_and_handles_missing_queue(self):
        cfg_path = write_cfg(self.root)
        code, out, _ = call("queue", "--config", str(cfg_path), "--out", str(self.queue))
        self.assertEqual(code, 0)
        self.assertIn("Queued 2 companies", out)
        code, _, err = call("watch-downloads", "--config", str(cfg_path), "--dir", str(self.downloads),
                            "--queue", str(self.root / "missing.html"))
        self.assertEqual(code, 2)
        self.assertIn("create a queue", err)


if __name__ == "__main__":
    unittest.main()
