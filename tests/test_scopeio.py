import tempfile
import unittest
from pathlib import Path

import yaml

from tests.helpers import ROOT
from thfin.config import ConfigError, Scope, load_config
from thfin.scopeio import replace_scope_block, scope_block, write_scope_block

REPO_CFG = (ROOT / "config" / "scope.yaml").read_text(encoding="utf-8")


class ScopeBlockTests(unittest.TestCase):
    def test_roundtrip_including_leading_zero_ids_and_thai(self):
        scope = Scope(
            provinces=["กรุงเทพมหานคร", "ชลบุรี"], tsic_prefixes=["10", "4711"], years_back=5,
            include_ids=["0105536001234", "0108999999999"], capital_min=1_000_000.0, max_companies=50,
        )
        again = Scope.model_validate(yaml.safe_load(scope_block(scope))["scope"])
        self.assertEqual(again, scope)
        self.assertIn("กรุงเทพมหานคร", scope_block(scope))  # not escaped to ก...

    def test_other_sections_and_comments_survive(self):
        new = replace_scope_block(REPO_CFG, Scope(provinces=["ระยอง"], years_back=2))
        for keep in ["registry:", "financials:", "runtime:", "# UNVERIFIED", "allowed_hosts", "THFIN_USER_AGENT"]:
            self.assertIn(keep, new)
        self.assertIn("ระยอง", new)
        self.assertNotIn("# e.g. [กรุงเทพมหานคร, ชลบุรี]", new)  # old scope comments replaced
        cfg = load_config(self._tmp(new))
        self.assertEqual(cfg.scope.provinces, ["ระยอง"])
        self.assertEqual(cfg.scope.years_back, 2)
        self.assertEqual(cfg.runtime.allowed_hosts, ["datawarehouse.dbd.go.th"])

    def test_flow_style_one_liner_is_replaced(self):
        text = "scope: {provinces: [x], max_companies: 5}\nruntime: {db_path: a.sqlite}\n"
        new = replace_scope_block(text, Scope(provinces=["ชลบุรี"]))
        self.assertEqual(yaml.safe_load(new)["scope"]["provinces"], ["ชลบุรี"])
        self.assertEqual(yaml.safe_load(new)["runtime"], {"db_path": "a.sqlite"})

    def test_missing_scope_is_appended(self):
        new = replace_scope_block("runtime: {db_path: a.sqlite}\n", Scope(years_back=2))
        data = yaml.safe_load(new)
        self.assertEqual(data["scope"]["years_back"], 2)
        self.assertEqual(data["runtime"], {"db_path": "a.sqlite"})

    def test_write_is_atomic_creates_new_files_and_validates(self):
        d = Path(tempfile.mkdtemp())
        out = write_scope_block(d / "presets" / "mine.yaml", Scope(provinces=["ภูเก็ต"]), base_text=REPO_CFG)
        self.assertEqual(load_config(out).scope.provinces, ["ภูเก็ต"])
        self.assertFalse(list(d.rglob("*.tmp")))
        with self.assertRaises(ConfigError):
            write_scope_block(d / "bad.yaml", Scope(), base_text="not_a_section: 1\n")
        self.assertFalse((d / "bad.yaml").exists())  # nothing half-written

    @staticmethod
    def _tmp(text: str) -> Path:
        p = Path(tempfile.mkdtemp()) / "c.yaml"
        p.write_text(text, encoding="utf-8")
        return p


if __name__ == "__main__":
    unittest.main()
