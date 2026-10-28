import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import REGISTRY_CSV
from thfin.cli import main


def write_cfg(tmp: Path, user_agent="thfin-test/0.1 (+mailto:t@example.org)") -> Path:
    p = tmp / "c.yaml"
    p.write_text(
        f"""
scope: {{provinces: [สมุทรปราการ, สมุทรสาคร], tsic_prefixes: ["10"], fiscal_years: [2567]}}
registry: {{csv_paths: ["{REGISTRY_CSV.as_posix()}"]}}
runtime:
  db_path: "{tmp.as_posix()}/t.sqlite"
  cache_dir: "{tmp.as_posix()}/cache"
  export_dir: "{tmp.as_posix()}/out"
  user_agent: "{user_agent}"
""",
        encoding="utf-8",
    )
    return p


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = write_cfg(self.tmp)

    def test_plan(self):
        code, out, _ = call("plan", "--config", str(self.cfg))
        self.assertEqual(code, 0)
        self.assertIn("Matched in registry : 2", out)
        self.assertIn("To fetch now        : 2", out)

    def test_set_narrows_scope_without_editing_files(self):
        code, out, _ = call("plan", "--config", str(self.cfg), "--set", "scope.provinces=[สมุทรปราการ]")
        self.assertEqual(code, 0)
        self.assertIn("Matched in registry : 1", out)

    def test_run_refuses_placeholder_contact(self):
        bad = write_cfg(self.tmp, user_agent="thfin/0.1 (+mailto:CHANGE-ME@example.com)")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("THFIN_USER_AGENT", None)  # a real contact in the env would hide the placeholder
            code, _, err = call("run", "--config", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("CHANGE-ME", err)

    def test_config_error_exit_code(self):
        code, _, err = call("plan", "--config", str(self.tmp / "missing.yaml"))
        self.assertEqual(code, 2)
        self.assertIn("config error", err)

    def test_registry_headers(self):
        code, out, _ = call("registry-headers", "--config", str(self.cfg))
        self.assertEqual(code, 0)
        self.assertIn("province", out)
        self.assertIn("PROVINCE", out)

    def test_status_and_export_on_empty_db(self):
        code, out, _ = call("status", "--config", str(self.cfg))
        self.assertEqual(code, 0)
        self.assertIn("no database", out)
        code, out, _ = call("export", "--config", str(self.cfg), "--format", "csv")
        self.assertEqual(code, 0)
        self.assertIn("0 rows", out)
        self.assertTrue(any((self.tmp / "out").glob("*.csv")))


if __name__ == "__main__":
    unittest.main()
