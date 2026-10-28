import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from tests.helpers import ROOT
from thfin.config import ConfigError, Scope, apply_overrides, load_config


class ScopeTests(unittest.TestCase):
    def test_gregorian_years_are_converted_and_deduped(self):
        self.assertEqual(Scope(fiscal_years=[2024, 2567, 2025]).fiscal_years, [2567, 2568])

    def test_comma_string_becomes_list(self):
        self.assertEqual(Scope(provinces="ชลบุรี, ระยอง").provinces, ["ชลบุรี", "ระยอง"])

    def test_none_becomes_empty_list(self):
        self.assertEqual(Scope(provinces=None).provinces, [])

    def test_ids_must_be_quoted_13_digit_strings(self):
        with self.assertRaises(ValidationError):
            Scope(include_ids=[105536001234])
        with self.assertRaises(ValidationError):
            Scope(include_ids=["123"])
        self.assertEqual(Scope(include_ids=["0105536001234"]).include_ids, ["0105536001234"])

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            Scope(province=["x"])  # typo for `provinces`

    def test_cap_must_be_positive(self):
        with self.assertRaises(ValidationError):
            Scope(max_companies=0)


class LoadTests(unittest.TestCase):
    def test_repo_default_and_presets_are_valid(self):
        load_config(ROOT / "config" / "scope.yaml")
        for p in (ROOT / "config" / "presets").glob("*.yaml"):
            load_config(p)

    def test_overrides(self):
        raw = {"scope": {"max_companies": 5}}
        out = apply_overrides(raw, ["scope.max_companies=9", "runtime.requests_per_second=1"])
        self.assertEqual(out["scope"]["max_companies"], 9)
        self.assertEqual(out["runtime"]["requests_per_second"], 1)
        self.assertEqual(raw["scope"]["max_companies"], 5)  # input not mutated

    def test_bad_override(self):
        with self.assertRaises(ConfigError):
            apply_overrides({}, ["nonsense"])

    def test_invalid_value_is_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text("scope:\n  max_companies: -1\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/config.yaml")

    def test_set_overrides_scope_end_to_end(self):
        cfg = load_config(ROOT / "config" / "scope.yaml", ["scope.provinces=[ชลบุรี, ระยอง]", "scope.fiscal_years=[2024]"])
        self.assertEqual(cfg.scope.provinces, ["ชลบุรี", "ระยอง"])
        self.assertEqual(cfg.scope.fiscal_years, [2567])


if __name__ == "__main__":
    unittest.main()
