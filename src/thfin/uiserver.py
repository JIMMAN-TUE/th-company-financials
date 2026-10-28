"""Local scope-builder UI: `python -m thfin ui`.

A tiny standard-library web server for one person on one machine:
  * binds to 127.0.0.1 only, checks the Host header (DNS-rebinding) and requires a per-run token
  * never fetches anything from the internet; it only reads the registry CSVs and writes the
    `scope:` section of a YAML file inside the config folder
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from .config import Config, ConfigError, Scope, load_config
from .models import Company
from .pipeline import build_plan
from .scopeio import plan_command, scope_block, write_scope_block
from .sources.registry import Matcher, load_all, registry_options
from .store import Store

HTML_PATH = Path(__file__).parent / "ui" / "index.html"
MAX_BODY = 1_000_000

PROVINCES = [
    "กระบี่", "กรุงเทพมหานคร", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น", "จันทบุรี", "ฉะเชิงเทรา",
    "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย", "เชียงใหม่", "ตรัง", "ตราด", "ตาก", "นครนายก",
    "นครปฐม", "นครพนม", "นครราชสีมา", "นครศรีธรรมราช", "นครสวรรค์", "นนทบุรี", "นราธิวาส", "น่าน",
    "บึงกาฬ", "บุรีรัมย์", "ปทุมธานี", "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี", "พระนครศรีอยุธยา",
    "พังงา", "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี", "เพชรบูรณ์", "แพร่", "พะเยา", "ภูเก็ต",
    "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน", "ยโสธร", "ยะลา", "ร้อยเอ็ด", "ระนอง", "ระยอง", "ราชบุรี",
    "ลพบุรี", "ลำปาง", "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร", "สงขลา", "สตูล", "สมุทรปราการ",
    "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี", "สิงห์บุรี", "สุโขทัย", "สุพรรณบุรี",
    "สุราษฎร์ธานี", "สุรินทร์", "หนองคาย", "หนองบัวลำภู", "อ่างทอง", "อำนาจเจริญ", "อุดรธานี",
    "อุตรดิตถ์", "อุทัยธานี", "อุบลราชธานี",
]

# Indicative labels for 2-digit TSIC (ISIC Rev.4-aligned) divisions. Counts come from the registry.
TSIC2_LABELS = {
    "01": "Crop & animal production", "03": "Fishing & aquaculture", "10": "Food products",
    "11": "Beverages", "13": "Textiles", "14": "Apparel", "16": "Wood products", "17": "Paper",
    "20": "Chemicals", "21": "Pharmaceuticals", "22": "Rubber & plastics", "23": "Non-metallic minerals",
    "24": "Basic metals", "25": "Fabricated metal", "26": "Computers & electronics",
    "27": "Electrical equipment", "28": "Machinery", "29": "Motor vehicles", "41": "Building construction",
    "42": "Civil engineering", "43": "Specialised construction", "45": "Motor vehicle trade",
    "46": "Wholesale trade", "47": "Retail trade", "49": "Land transport", "52": "Warehousing",
    "55": "Accommodation", "56": "Food & beverage service", "58": "Publishing", "62": "Computer programming",
    "64": "Financial services", "68": "Real estate", "69": "Legal & accounting", "70": "Management consulting",
    "71": "Architecture & engineering", "85": "Education", "86": "Human health",
}


class ScopeUI:
    """State shared by all requests: config, token, lazily loaded registry."""

    def __init__(self, config_path: str | Path, overrides: list[str] | None = None):
        self.config_path = Path(config_path)
        self.overrides = overrides or []
        self.cfg: Config = load_config(self.config_path, self.overrides)
        self.token = secrets.token_urlsafe(16)
        self.port = 0
        self._lock = threading.Lock()
        self._companies: list[Company] | None = None
        self._options: dict | None = None
        self.registry_error = ""

    def companies(self) -> list[Company]:
        with self._lock:
            if self._companies is None:
                try:
                    self._companies = load_all(self.cfg)
                except ConfigError as e:
                    self._companies, self.registry_error = [], str(e)
                self._options = registry_options(self._companies)
            return self._companies

    def options(self) -> dict:
        self.companies()
        opts = dict(self._options or {})
        opts.update(
            registry_error=self.registry_error,
            all_provinces=PROVINCES,
            tsic_labels=TSIC2_LABELS,
            current_scope=self.cfg.scope.model_dump(mode="python"),
            effective_years=self.cfg.scope.effective_years(),
            config_path=str(self.config_path),
            max_in_cap_default=Scope().max_companies,
        )
        return opts

    def preview(self, scope_raw: dict) -> dict:
        scope = Scope.model_validate(scope_raw)
        cfg = self.cfg.model_copy(update={"scope": scope})
        out: dict = {
            "ok": True,
            "years": scope.effective_years(),
            "yaml": scope_block(scope),
            "command": plan_command(self.config_path),
            "warnings": [],
            "matched": None,
        }
        companies = self.companies()
        if self.registry_error and not scope.include_ids:
            out["warnings"].append("ยังไม่มีไฟล์ registry CSV: " + self.registry_error)
            return out
        present = set((self._options or {}).get("present_fields", []))
        missing = [f for f in Matcher(scope).required_fields() if f not in present] if companies else []
        if missing:
            out["warnings"].append(f"registry ไม่มีคอลัมน์ {missing} ตัวกรองเหล่านี้จะไม่เจอบริษัทเลย")
        db = Path(cfg.runtime.db_path)
        store = Store(db) if db.exists() else None
        try:
            plan = build_plan(cfg, store, companies=companies)
        finally:
            if store:
                store.close()
        u = plan.universe
        out.update(
            matched=u.total_matched,
            selected=len(u.companies),
            already_done=plan.already_done,
            to_fetch=plan.est_requests,
            est_hours=round(plan.est_hours, 3),
            truncated=u.truncated,
            top_provinces=u.by_province.most_common(8),
            top_tsic=u.by_tsic2.most_common(8),
        )
        out["warnings"] += plan.warnings
        return out

    def save(self, scope_raw: dict, rel_path: str | None) -> Path:
        scope = Scope.model_validate(scope_raw)
        config_dir = self.config_path.parent.resolve()
        target = (config_dir / rel_path).resolve() if rel_path else self.config_path.resolve()
        inside = config_dir in target.parents or target == self.config_path.resolve()
        if target.suffix not in {".yaml", ".yml"} or not inside:
            raise ConfigError(f"can only write .yaml files inside {config_dir}")
        base = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        return write_scope_block(target, scope, base_text=base)


def _make_handler(ui: ScopeUI):
    class Handler(BaseHTTPRequestHandler):
        server_version = "thfin-ui"

        def log_message(self, fmt, *args):  # quiet; errors go through send_error
            pass

        # -- helpers --
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{ui.port}", f"localhost:{ui.port}"}

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token", ""), ui.token)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ConfigError("request too large")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ConfigError("expected a JSON object")
            return data

        # -- routes --
        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "bad host"})
            if self.path in ("/", "/index.html"):
                html = HTML_PATH.read_text(encoding="utf-8").replace("__TOKEN__", ui.token)
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/options":
                if not self._authorised():
                    return self._json(403, {"ok": False, "error": "forbidden"})
                self._json(200, {"ok": True, **ui.options()})
            else:
                self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._authorised():
                return self._json(403, {"ok": False, "error": "forbidden"})
            try:
                data = self._body()
                if self.path == "/api/preview":
                    return self._json(200, ui.preview(data.get("scope", {})))
                if self.path == "/api/save":
                    saved = ui.save(data.get("scope", {}), data.get("path"))
                    return self._json(200, {"ok": True, "saved": str(saved), "command": plan_command(saved)})
                self._json(404, {"ok": False, "error": "not found"})
            except ValidationError as e:
                msgs = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
                self._json(400, {"ok": False, "error": "invalid scope", "details": msgs})
            except (ConfigError, ValueError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": str(e)})

    return Handler


def make_server(ui: ScopeUI, port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(ui))
    ui.port = server.server_address[1]
    return server


def serve(config_path: str | Path, overrides: list[str] | None, port: int, open_browser: bool) -> int:
    ui = ScopeUI(config_path, overrides)
    server = make_server(ui, port)
    url = f"http://127.0.0.1:{ui.port}/"
    print(f"Scope builder running at {url}  (Ctrl+C to stop)")
    print("Local only. It reads your registry CSVs and edits the scope section of", ui.config_path)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
