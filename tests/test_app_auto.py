import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dbd import FINANCE, company_table
from thfin.app import AppController, prepare_workspace
from thfin.config import ConfigError
from thfin.dbd import Discovery, parse_companies
from thfin.uiserver import ScopeUI


class AutomaticAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ui = ScopeUI(prepare_workspace(self.root))
        self.app = self.ui.app = AppController(self.ui, self.root)
        self.addCleanup(self.app.shutdown)
        self.scope = {"tsic_prefixes": ["55"], "provinces": ["กรุงเทพมหานคร"],
                      "fiscal_years": [2568], "max_companies": 1}

    def finish(self):
        self.app._thread.join(timeout=5)
        self.assertFalse(self.app._thread.is_alive())
        return self.app.status()

    def test_search_collect_export_and_resume_without_uploads(self):
        session = mock.MagicMock()
        session.cfg = self.ui.cfg
        session.__enter__.return_value = session
        session.discover.return_value = Discovery(parse_companies(company_table()), 1, False)
        session.financials.return_value = FINANCE
        with mock.patch("thfin.dbd.DBDSession", return_value=session) as factory:
            self.app.start({"action": "auto", "scope": self.scope})
            job = self.finish()
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["summary"]["ok"], 1)
            self.assertEqual(job["plan"]["selected"], 1)
            self.assertFalse(job["plan"]["complete"])
            self.assertEqual(factory.call_args.args[0].scope.provinces, ["กรุงเทพมหานคร"])
            rows = self.app.rows(self.scope)
            self.assertEqual(rows[0]["revenue_total"], 120000)
            self.assertEqual(rows[0]["fiscal_year"], 2568)
            self.assertEqual(len(rows), 1)
            self.app.start({"action": "auto", "scope": self.scope})
            self.assertEqual(self.finish()["summary"]["attempted"], 0)
        self.assertEqual(session.financials.call_count, 1)
        self.assertEqual(self.ui.preview(self.scope)["selected"], 1)
        self.assertIn(b"120000", self.app.export({"scope": self.scope, "format": "csv"})[0])

    def test_discovery_failure_preserves_existing_registry(self):
        path = self.root / "registry" / "dbd-discovered.csv"
        path.write_bytes(b"existing registry")
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.discover.side_effect = ConfigError("fixture refused")
        with mock.patch("thfin.dbd.DBDSession", return_value=session):
            self.app.start({"action": "discover", "scope": self.scope})
            job = self.finish()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(path.read_bytes(), b"existing registry")

    def test_requires_category_before_any_network(self):
        with self.assertRaises(ConfigError):
            self.app.start({"action": "auto", "scope": {}})
