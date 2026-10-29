"""Local application controller: scope snapshots, background jobs and scoped results."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .config import Config, ConfigError, Scope, load_config
from .http import PoliteClient
from .manual import DownloadWatcher, create_queue, queue_lock
from .pipeline import build_plan, import_files, run
from .sources.registry import load_universe, read_headers, resolve_columns
from .store import Store, write_csv, write_xlsx

UPLOAD_LIMIT = 24 * 1024 * 1024
ACTIONS = {"collect", "queue", "watch", "import", "discover", "auto"}


def default_workspace() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "thfin"
    return Path.home() / ".local" / "share" / "thfin"


def prepare_workspace(root: Path) -> Path:
    """First launch only: user data stays outside the distributable and the source repo."""
    root = root.resolve()
    for name in ("config", "registry", "statements", "exports", "queues", "cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    config = root / "config" / "scope.yaml"
    if not config.exists():
        cfg = Config()
        cfg.registry.csv_paths = [str(root / "registry" / "*.csv")]
        cfg.financials.files_dir = str(root / "statements")
        cfg.runtime.db_path = str(root / "financials.sqlite")
        cfg.runtime.cache_dir = str(root / "cache" / "http")
        cfg.runtime.export_dir = str(root / "exports")
        cfg.runtime.transport = "chromium"
        config.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config


class AppController:
    def __init__(self, ui, root: Path):
        self.ui, self.root = ui, root.resolve()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._job = {"status": "idle", "action": None, "logs": [], "error": "", "summary": None}
        self._queue_file = self.root / "active-queue.json"

    def _config(self, scope_raw: dict) -> Config:
        cfg = load_config(self.ui.config_path, self.ui.overrides)
        return cfg.model_copy(update={"scope": Scope.model_validate(scope_raw)}, deep=True)

    def _require_idle(self):
        if self._job["status"] in {"running", "stopping"}:
            raise ConfigError("มีงานกำลังทำอยู่ ให้หยุดหรือรอให้เสร็จก่อน")

    def settings(self) -> dict:
        cfg = load_config(self.ui.config_path, self.ui.overrides)
        return {"workspace": str(self.root), "url_template": cfg.financials.url_template,
                "user_agent": cfg.runtime.user_agent, "transport": cfg.runtime.transport,
                "unit_multiplier": cfg.financials.unit_multiplier,
                "files_dir": cfg.financials.files_dir}

    def save_settings(self, data: dict) -> dict:
        with self._lock:
            self._require_idle()
            cfg = load_config(self.ui.config_path)
            template = data.get("url_template", cfg.financials.url_template)
            try:
                if "{juristic_id}" not in template:
                    raise ValueError("URL ต้องมี {juristic_id} สำหรับเลขทะเบียนแต่ละบริษัท")
                parsed = urlsplit(template.format(juristic_id="0105500000001"))
                if parsed.scheme not in {"http", "https"} or parsed.username:
                    raise ValueError("URL ต้องเป็น HTTP(S) และไม่มีรหัสผ่าน")
                if parsed.hostname not in cfg.runtime.allowed_hosts:
                    raise ValueError("โฮสต์นี้ไม่อยู่ใน allowed_hosts ของโปรเจกต์")
            except (TypeError, KeyError, ValueError, IndexError) as e:
                raise ConfigError(f"URL ไม่ถูกต้อง: {e}") from e
            agent = data.get("user_agent", cfg.runtime.user_agent)
            if not isinstance(agent, str) or not agent.strip() or "\r" in agent or "\n" in agent:
                raise ConfigError("ข้อมูลติดต่อไม่ถูกต้อง")
            raw = cfg.model_dump()
            multiplier = data.get("unit_multiplier", cfg.financials.unit_multiplier)
            if not isinstance(multiplier, (float, int)) or not math.isfinite(multiplier) or multiplier <= 0:
                raise ConfigError("ตัวคูณหน่วยงบต้องเป็นตัวเลขมากกว่า 0")
            raw["financials"].update(url_template=template, unit_multiplier=multiplier)
            raw["runtime"].update(user_agent=agent.strip(), transport=data.get("transport", cfg.runtime.transport))
            updated = Config.model_validate(raw)
            temp = self.ui.config_path.with_suffix(".tmp")
            temp.write_text(yaml.safe_dump(updated.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
            temp.replace(self.ui.config_path)
            self.ui.cfg = updated
            return self.settings()

    def upload(self, data: dict) -> dict:
        with self._lock:
            self._require_idle()
            kind, name = data.get("kind"), data.get("name", "")
            if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
                raise ConfigError("ชื่อไฟล์ไม่ถูกต้อง")
            suffix = Path(name).suffix.lower()
            if kind == "registry" and suffix == ".csv":
                folder = self.root / "registry"
            elif kind == "statement" and suffix in {".xlsx", ".xlsm", ".html", ".htm"}:
                if not re.search(r"(?<!\d)[0-9]{13}(?!\d)", name):
                    raise ConfigError("ชื่อไฟล์งบต้องมีเลขทะเบียน 13 หลัก หรือใช้ตัวเฝ้าดาวน์โหลดช่วยตั้งชื่อ")
                folder = self.root / "statements"
            else:
                raise ConfigError("เลือก CSV ทะเบียน หรือ XLSX/HTML งบการเงิน")
            try:
                encoded = data["content"]
                if not isinstance(encoded, str) or len(encoded) > (UPLOAD_LIMIT * 4 // 3 + 4):
                    raise ValueError("file too large")
                body = base64.b64decode(encoded, validate=True)
            except (KeyError, ValueError, binascii.Error) as e:
                raise ConfigError("ไฟล์อ่านไม่ได้หรือเกิน 24 MB") from e
            if not body or len(body) > UPLOAD_LIMIT:
                raise ConfigError("ไฟล์ว่างหรือเกิน 24 MB")
            target = folder / name
            temp = folder / (uuid.uuid4().hex + ".tmp")
            try:
                temp.write_bytes(body)
                if kind == "registry":
                    cfg = self._config(self.ui.cfg.scope.model_dump())
                    mapping = resolve_columns(read_headers(temp, cfg), cfg.registry.columns)
                    if "juristic_id" not in mapping:
                        raise ConfigError("ไม่พบคอลัมน์เลขทะเบียนใน CSV; ตรวจชื่อคอลัมน์ก่อนนำเข้า")
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
            if kind == "registry":
                with self.ui._lock:
                    self.ui._companies = self.ui._options = None
                    self.ui.registry_error = ""
            return {"saved": name, "kind": kind}

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._job))

    def _log(self, message: str):
        with self._lock:
            self._job["logs"] = (self._job["logs"] + [message])[-150:]

    def start(self, data: dict) -> dict:
        action = data.get("action")
        if action not in ACTIONS:
            raise ConfigError("คำสั่งไม่ถูกต้อง")
        with self._lock:
            self._require_idle()
            cfg = self._config(data.get("scope", {}))
            refetch = data.get("refetch", False)
            if type(refetch) is not bool:
                raise ConfigError("ตัวเลือกประมวลผลใหม่ไม่ถูกต้อง")
            store = Store(cfg.runtime.db_path) if Path(cfg.runtime.db_path).exists() else None
            try:
                plan = (None if action in {"discover", "auto"} else
                        build_plan(cfg, store, refetch=refetch and action == "collect"))
            finally:
                if store:
                    store.close()
            if plan is not None and not plan.universe.companies:
                raise ConfigError("ยังไม่มีบริษัทตรงขอบเขตที่เลือก; โหลดทะเบียนและตรวจตัวกรองก่อน")
            limit = data.get("limit", 25)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ConfigError("จำนวนบริษัทต่อรอบต้องอยู่ระหว่าง 1 ถึง 1000")
            if action == "collect" and "CHANGE-ME" in cfg.runtime.user_agent:
                raise ConfigError("ใส่ข้อมูลติดต่อของคุณในส่วนตั้งค่าแหล่งข้อมูลก่อนเริ่มดึง")
            if action in {"auto", "discover"} and not (cfg.scope.tsic_prefixes or cfg.scope.include_ids):
                raise ConfigError("เลือกหมวด TSIC หรือระบุเลขทะเบียนก่อนค้น DBD")
            queue = None
            directory = None
            if action == "watch":
                queue = self._active_queue(cfg.scope)
                directory = Path(data.get("directory", "")).expanduser().resolve()
                if not data.get("directory") or not directory.is_dir():
                    raise ConfigError("กรุณาระบุโฟลเดอร์ Downloads ที่มีอยู่จริง")
            self._stop.clear()
            self._job = {"id": uuid.uuid4().hex, "action": action, "status": "running", "error": "",
                         "logs": [], "summary": None, "scope": cfg.scope.model_dump(), "refetch": refetch,
                         "plan": ({"matched": plan.universe.total_matched, "selected": len(plan.universe.companies),
                                   "pending": plan.est_requests, "est_hours": plan.est_hours} if plan else None)}
            self._thread = threading.Thread(target=self._work, args=(action, cfg, limit, queue, directory, refetch), daemon=True)
            self._thread.start()
            return self.status()

    def cancel(self) -> dict:
        with self._lock:
            if self._job["status"] in {"running", "stopping"}:
                self._stop.set()
                self._job["status"] = "stopping"
            return self.status()

    def shutdown(self):
        self.cancel()
        if self._thread:
            self._thread.join(timeout=5)

    def _active_queue(self, scope: Scope | None = None) -> Path:
        try:
            saved = json.loads(self._queue_file.read_text(encoding="utf-8"))
            path = (self.root / "queues" / saved["name"]).resolve()
            if path.parent != (self.root / "queues").resolve() or not path.is_file():
                raise ValueError("invalid queue path")
            if scope is not None and saved["scope"] != scope.model_dump():
                raise ConfigError("ตัวกรองต่างจากคิวเดิม ให้สร้างคิวใหม่หรือเลือก scope เดิมก่อนเฝ้าดาวน์โหลด")
            return path
        except (OSError, KeyError, ValueError, TypeError) as e:
            raise ConfigError("ยังไม่มีคิว export ให้กดสร้างคิวก่อน") from e

    def queue_html(self) -> bytes:
        with self._lock:
            return self._active_queue().read_bytes()

    def _work(self, action, cfg, limit, queue, directory, refetch):
        store = None
        try:
            if action in {"discover", "auto"}:
                summary = self._automatic(action, cfg, limit, refetch)
            elif action == "queue":
                queue = self.root / "queues" / (uuid.uuid4().hex + ".html")
                count = create_queue(cfg, queue)
                temp = self._queue_file.with_suffix(".tmp")
                temp.write_text(json.dumps({"name": queue.name, "scope": cfg.scope.model_dump()}, ensure_ascii=False),
                                encoding="utf-8")
                temp.replace(self._queue_file)
                summary = {"queued": count}
                self._log(f"สร้างคิว {count} บริษัทแล้ว เปิดรายการและ export ตามลำดับ")
            elif action == "watch":
                with queue_lock(queue):
                    watcher = DownloadWatcher(cfg, directory, queue, progress=self._log)
                    while not watcher.done and not self._stop.wait(1):
                        watcher.poll()
                    summary = {"complete": watcher.done, "next": watcher.state["next"], "total": len(watcher.state["ids"])}
            else:
                store = Store(cfg.runtime.db_path)
                universe = load_universe(cfg)
                store.upsert_companies(universe.companies)
                if action == "collect":
                    result = run(cfg, PoliteClient(cfg.runtime), store, limit=limit, progress=self._log,
                                 should_stop=self._stop.is_set, refetch=refetch)
                else:
                    result = import_files(cfg, store, progress=self._log,
                                          allowed_ids={c.juristic_id for c in universe.companies},
                                          should_stop=self._stop.is_set)
                summary = asdict(result)
            with self._lock:
                self._job["summary"] = summary
                self._job["status"] = ("cancelled" if self._stop.is_set() else
                                       "failed" if summary.get("aborted") or summary.get("errors") else "completed")
                self._job["error"] = summary.get("aborted", "")
        except Exception as e:
            with self._lock:
                self._job["status"] = "failed"
                self._job["error"] = str(e)
            self._log(f"หยุด: {e}")
        finally:
            if store:
                store.close()

    def _automatic(self, action, cfg, limit, refetch):
        from .dbd import DBDClient, DBDSession, save_registry
        self._log("กำลังค้น DBD ตาม scope · จำนวนยังไม่ทราบจนกว่าจะค้นเสร็จ")
        with DBDSession(cfg, self._log, self._stop.is_set) as session:
            discovery = session.discover()
            if self._stop.is_set():
                return {"aborted": "cancelled"}
            registry = self.root / "registry" / "dbd-discovered.csv"
            save_registry(registry, discovery.companies)
            cfg.registry.csv_paths = [str(registry)]
            cfg.registry.columns = {}
            cfg.registry.encoding = "utf-8-sig"
            cfg.registry.delimiter = ","
            with self.ui._lock:
                self.ui._companies = self.ui._options = None
                self.ui.registry_error = ""
            store = Store(cfg.runtime.db_path)
            try:
                plan = build_plan(cfg, store, refetch=refetch)
                with self._lock:
                    self._job["plan"] = {"matched": plan.universe.total_matched,
                                         "selected": len(plan.universe.companies), "pending": plan.est_requests,
                                         "est_hours": plan.est_hours, "complete": discovery.complete}
                self._log(f"แผน: พบ {plan.universe.total_matched} · เลือก {len(plan.universe.companies)} · "
                          f"ค้าง {plan.est_requests} · อย่างน้อย {plan.est_hours:.3f} ชม. (ยังไม่รวมโหลดหน้า/สคริปต์)")
                store.upsert_companies(discovery.companies)
                result = {"discovered": len(discovery.companies), "pages": discovery.pages,
                          "complete": discovery.complete}
                if action == "auto" and plan.universe.companies:
                    summary = run(cfg, DBDClient(session), store, limit=limit, refetch=refetch,
                                  progress=self._log, should_stop=self._stop.is_set)
                    result.update(asdict(summary))
                return result
            finally:
                store.close()

    def rows(self, scope_raw: dict) -> list[dict]:
        cfg = self._config(scope_raw)
        try:
            selected = {c.juristic_id for c in load_universe(cfg).companies}
        except ConfigError:
            return []
        if not Path(cfg.runtime.db_path).is_file():
            return []
        store = Store(cfg.runtime.db_path)
        try:
            return [row for row in store.export_rows(cfg.scope) if row["juristic_id"] in selected]
        finally:
            store.close()

    def export(self, data: dict) -> tuple[bytes, str, str]:
        fmt = data.get("format", "xlsx")
        if fmt not in {"csv", "xlsx"}:
            raise ConfigError("เลือก CSV หรือ XLSX")
        rows = self.rows(data.get("scope", {}))
        path = self.root / "exports" / f"financials_{uuid.uuid4().hex}.{fmt}"
        (write_csv if fmt == "csv" else write_xlsx)(rows, path)
        mime = ("text/csv; charset=utf-8" if fmt == "csv" else
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return path.read_bytes(), f"financials.{fmt}", mime
