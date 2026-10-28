import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_cfg
from thfin.config import ConfigError, RegistryCfg
from thfin.sources.registry import load_universe, norm_province, resolve_columns


def ids(uni):
    return [c.juristic_id for c in uni.companies]


class ColumnTests(unittest.TestCase):
    def test_autodetect_english_and_thai(self):
        m = resolve_columns(["JURISTIC_ID", "จังหวัด", "ทุนจดทะเบียน"], {})
        self.assertEqual(m["juristic_id"], "JURISTIC_ID")
        self.assertEqual(m["province"], "จังหวัด")
        self.assertEqual(m["registered_capital"], "ทุนจดทะเบียน")

    def test_override_must_exist(self):
        with self.assertRaises(ConfigError):
            resolve_columns(["A"], {"juristic_id": "B"})

    def test_province_aliases(self):
        self.assertEqual(norm_province("จังหวัดกรุงเทพฯ"), "กรุงเทพมหานคร")
        self.assertEqual(norm_province("Bangkok"), "กรุงเทพมหานคร")
        self.assertEqual(norm_province("จ. ชลบุรี"), "ชลบุรี")


class UniverseTests(unittest.TestCase):
    def test_province_and_tsic(self):
        uni = load_universe(make_cfg(provinces=["สมุทรปราการ", "สมุทรสาคร"], tsic_prefixes=["10"]))
        self.assertEqual(ids(uni), ["0105500000001", "0105500000002"])
        self.assertEqual(uni.total_matched, 2)
        self.assertFalse(uni.truncated)

    def test_bangkok_alias_matches_both_spellings(self):
        uni = load_universe(make_cfg(provinces=["กรุงเทพมหานคร"], tsic_prefixes=["55"]))
        self.assertEqual(ids(uni), ["0105500000003", "0105500000004"])

    def test_defaults_exclude_dissolved_and_partnerships(self):
        uni = load_universe(make_cfg())
        self.assertNotIn("0105500000005", ids(uni))  # dissolved
        self.assertNotIn("0105500000006", ids(uni))  # partnership

    def test_clearing_filters_includes_them(self):
        uni = load_universe(make_cfg(statuses=[], juristic_types=[], provinces=["กรุงเทพมหานคร"], tsic_prefixes=["47"]))
        self.assertEqual(ids(uni), ["0105500000005"])

    def test_leading_zero_restored_and_bad_ids_skipped(self):
        uni = load_universe(make_cfg(provinces=["ชลบุรี"], tsic_prefixes=["10"]))
        self.assertEqual(ids(uni), ["0105500000007"])

    def test_capital_band(self):
        uni = load_universe(make_cfg(capital_min=100_000_000))
        self.assertEqual(ids(uni), ["0105500000003"])
        uni = load_universe(make_cfg(capital_max=5_000_000, provinces=["สมุทรปราการ"]))
        self.assertEqual(ids(uni), ["0105500000001"])

    def test_name_contains(self):
        uni = load_universe(make_cfg(name_contains=["โรงแรม"]))
        self.assertEqual(ids(uni), ["0105500000003", "0105500000004"])

    def test_exclude_ids(self):
        uni = load_universe(make_cfg(name_contains=["โรงแรม"], exclude_ids=["0105500000003"]))
        self.assertEqual(ids(uni), ["0105500000004"])

    def test_include_ids_overrides_filters_and_allows_unknown(self):
        uni = load_universe(make_cfg(include_ids=["0105500000005", "0100000000000"], provinces=["ภูเก็ต"]))
        self.assertEqual(ids(uni), ["0100000000000", "0105500000005"])

    def test_cap_truncates_deterministically(self):
        uni = load_universe(make_cfg(max_companies=2))
        self.assertEqual(len(uni.companies), 2)
        self.assertTrue(uni.truncated)
        self.assertGreater(uni.total_matched, 2)
        self.assertEqual(ids(uni), sorted(ids(uni)))

    def test_scope_on_missing_column_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            p.write_text("JURISTIC_ID,JURISTIC_NAME_TH\n0105500000001,x\n", encoding="utf-8")
            cfg = make_cfg(provinces=["ชลบุรี"], juristic_types=[], statuses=[])
            cfg.registry = RegistryCfg(csv_paths=[str(p)])
            with self.assertRaises(ConfigError) as cm:
                load_universe(cfg)
            self.assertIn("province", str(cm.exception))

    def test_no_files(self):
        cfg = make_cfg()
        cfg.registry = RegistryCfg(csv_paths=["/nonexistent/*.csv"])
        with self.assertRaises(ConfigError):
            load_universe(cfg)


if __name__ == "__main__":
    unittest.main()
