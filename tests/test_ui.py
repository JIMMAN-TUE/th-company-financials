import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tests.helpers import REGISTRY_CSV
from thfin.config import load_config
from thfin.uiserver import PROVINCES, ScopeUI, make_server


def write_config(root: Path, csv_path: str = str(REGISTRY_CSV)) -> Path:
    (root / "config").mkdir()
    p = root / "config" / "scope.yaml"
    p.write_text(
        f"""# keep me
scope:
  provinces: [สมุทรปราการ]
  years_back: 3
registry:
  csv_paths: ["{csv_path}"]
runtime:
  db_path: "{root}/t.sqlite"
  user_agent: "thfin-test/0.1 (+mailto:t@example.org)"
""",
        encoding="utf-8",
    )
    return p


class UiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cfg_path = write_config(self.root)
        self.ui = ScopeUI(self.cfg_path)
        self.server = make_server(self.ui, 0)
        self.port = self.ui.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-Token"] = self.ui.token
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse()
        raw = r.read().decode("utf-8")
        conn.close()
        return r.status, (json.loads(raw) if r.getheader("Content-Type", "").startswith("application/json") else raw)

    # -- page and security ------------------------------------------------------------
    def test_page_is_served_with_the_token_filled_in(self):
        status, html = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.ui.token, html)
        self.assertNotIn("__TOKEN__", html)

    def test_api_requires_token_and_correct_host(self):
        self.assertEqual(self.call("GET", "/api/options", token=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/preview", {"scope": {}}, token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/options", host="evil.example")[0], 403)  # DNS rebinding
        self.assertEqual(self.call("GET", "/", host="evil.example", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/options")[0], 200)

    def test_unknown_route(self):
        self.assertEqual(self.call("GET", "/nope")[0], 404)

    def test_province_list_is_complete(self):
        self.assertEqual(len(PROVINCES), 77)
        self.assertEqual(len(set(PROVINCES)), 77)

    # -- options and preview ----------------------------------------------------------
    def test_options_reflect_registry_and_current_scope(self):
        status, o = self.call("GET", "/api/options")
        self.assertEqual(status, 200)
        self.assertEqual(o["total"], 7)  # 7 valid ids, the malformed one is skipped
        self.assertIn(["สมุทรปราการ", 1], [list(x) for x in o["provinces"]])
        self.assertEqual(o["current_scope"]["provinces"], ["สมุทรปราการ"])
        self.assertEqual(len(o["effective_years"]), 3)
        self.assertEqual(o["registry_error"], "")

    def test_preview_counts_and_estimates(self):
        scope = {"provinces": ["สมุทรปราการ", "สมุทรสาคร"], "tsic_prefixes": ["10"], "years_back": 2}
        status, r = self.call("POST", "/api/preview", {"scope": scope})
        self.assertEqual(status, 200)
        self.assertEqual((r["matched"], r["selected"], r["to_fetch"]), (2, 2, 2))
        self.assertEqual(len(r["years"]), 2)
        self.assertIn("scope:", r["yaml"])
        self.assertIn(str(self.cfg_path), r["command"])

    def test_preview_with_cap_warns(self):
        _, r = self.call("POST", "/api/preview", {"scope": {"max_companies": 1, "provinces": [], "tsic_prefixes": []}})
        self.assertTrue(r["truncated"])
        self.assertTrue(any("max_companies" in w for w in r["warnings"]))

    def test_invalid_scope_is_a_400_with_details(self):
        status, r = self.call("POST", "/api/preview", {"scope": {"years_back": 99, "include_ids": ["12"]}})
        self.assertEqual(status, 400)
        self.assertTrue(any("years_back" in d for d in r["details"]))

    def test_filter_on_a_column_the_registry_lacks_is_flagged(self):
        csv_path = self.root / "thin.csv"
        csv_path.write_text("JURISTIC_ID,JURISTIC_NAME_TH\n0105500000001,x\n", encoding="utf-8")
        ui = ScopeUI(write_config(Path(tempfile.mkdtemp()), str(csv_path)))
        r = ui.preview({"provinces": ["ชลบุรี"], "juristic_types": [], "statuses": []})
        self.assertTrue(any("province" in w for w in r["warnings"]))

    def test_missing_registry_still_gives_yaml(self):
        ui = ScopeUI(write_config(Path(tempfile.mkdtemp()), "/nonexistent/*.csv"))
        r = ui.preview({"provinces": ["ชลบุรี"]})
        self.assertIsNone(r["matched"])
        self.assertIn("ชลบุรี", r["yaml"])
        self.assertTrue(r["warnings"])

    # -- saving -----------------------------------------------------------------------
    def test_save_rewrites_only_the_scope_section(self):
        status, r = self.call("POST", "/api/save", {"scope": {"provinces": ["ชลบุรี"], "years_back": 4}})
        self.assertEqual(status, 200)
        text = self.cfg_path.read_text(encoding="utf-8")
        self.assertIn("# keep me", text)
        self.assertIn("registry:", text)
        cfg = load_config(self.cfg_path)
        self.assertEqual((cfg.scope.provinces, cfg.scope.years_back), (["ชลบุรี"], 4))

    def test_save_to_a_new_preset_inside_config_dir(self):
        status, r = self.call("POST", "/api/save", {"scope": {"provinces": ["ภูเก็ต"]}, "path": "presets/phuket.yaml"})
        self.assertEqual(status, 200)
        self.assertEqual(load_config(self.root / "config" / "presets" / "phuket.yaml").scope.provinces, ["ภูเก็ต"])

    def test_save_refuses_paths_outside_config_and_non_yaml(self):
        for bad in ["../evil.yaml", "/etc/passwd.yaml", "notes.txt", "../../etc/cron.d/x.yaml"]:
            status, _ = self.call("POST", "/api/save", {"scope": {}, "path": bad})
            self.assertEqual(status, 400, bad)
        self.assertFalse((self.root / "evil.yaml").exists())

    def test_bad_json_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/preview", "{nope", {"Host": f"127.0.0.1:{self.port}", "X-Token": self.ui.token})
        self.assertEqual(conn.getresponse().status, 400)


if __name__ == "__main__":
    unittest.main()
