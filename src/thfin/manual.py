"""Offline helpers for a person exporting one company at a time in queue order."""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from contextlib import contextmanager
from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import Config, ConfigError
from .sources.registry import load_universe

SUFFIXES = {".xlsx", ".html"}
DEFAULT_QUEUE = "manual-queue.html"


def state_path(queue: Path) -> Path:
    return queue.with_suffix(queue.suffix + ".json")


@contextmanager
def queue_lock(queue: Path):
    lock = state_path(queue).with_suffix(".lock")
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as e:
        raise ConfigError(f"queue is locked: {lock}; another watcher may be running") from e
    try:
        with handle:
            handle.write("thfin manual queue\n")
        yield
    finally:
        lock.unlink()


def _save(path: Path, state: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _load(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        ids = state["ids"]
        next_index = state["next"]
        if (state["version"] != 1 or not isinstance(ids, list)
                or any(not isinstance(j, str) or not re.fullmatch(r"[0-9]{13}", j) for j in ids)
                or len(set(ids)) != len(ids) or type(next_index) is not int
                or not 0 <= next_index <= len(ids)):
            raise ValueError("invalid IDs or queue position")
        if not Path(state["files_dir"]).is_absolute():
            raise ValueError("files_dir must be absolute")
        item = state["inflight"]
        if item is not None:
            if next_index == len(ids) or not Path(item["source"]).is_absolute():
                raise ValueError("invalid in-flight source")
            if item["filename"] not in {ids[next_index] + s for s in SUFFIXES}:
                raise ValueError("invalid in-flight destination")
        return state
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise ConfigError(f"cannot read queue state {path}: {e}; create a queue with `thfin queue`") from e


def _move_export(source: Path, target: Path) -> None:
    # Exclusive creation also protects against a destination created after the collision check.
    # Copy + unlink works across disks on Windows. On failure leave both files for inspection.
    before = source.stat()
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("download changed while being copied; source has been retained")
    source.unlink()


def create_queue(cfg: Config, queue: Path) -> int:
    """Create escaped HTML and a separate durable FIFO; never fetch a company page."""
    queue = queue.resolve()
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue_lock(queue):
        path = state_path(queue)
        if queue.exists() or path.exists():
            raise ConfigError("queue already exists; resume it or use --out with a new filename")
        universe = load_universe(cfg)
        rows = []
        for number, company in enumerate(universe.companies, 1):
            try:
                url = cfg.financials.url_template.format(juristic_id=company.juristic_id)
                parsed = urlsplit(url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
                    raise ValueError("company links must be HTTP(S) URLs without credentials")
            except (KeyError, ValueError, IndexError) as e:
                raise ConfigError(f"invalid financials.url_template: {e}") from e
            rows.append(f'<tr><td>{number}</td><td>{escape(company.name_th or company.name_en)}</td>'
                        f'<td>{company.juristic_id}</td><td><a href="{escape(url, quote=True)}" '
                        f'target="_blank" rel="noopener noreferrer">เปิดบริษัท / Open company</a></td></tr>')
        html = '''<!doctype html>
<html lang="th"><meta charset="utf-8"><title>thfin — Manual export queue</title>
<style>body{font:18px sans-serif;max-width:1100px;margin:2rem auto;padding:1rem}
table{border-collapse:collapse;width:100%}td,th{padding:.6rem;border:1px solid #ccc;text-align:left}</style>
<h1>คิว export ด้วยมือ / Manual export queue</h1>
<p>Start watch-downloads before exporting. Open and export ONE company at a time, in row order.
Wait for the watcher to report a move before starting the next company. Export all years in ONE file.
The watcher assigns IDs by queue order; it cannot verify which company a file belongs to.</p>
<p>เริ่มตัวเฝ้าไฟล์ก่อนดาวน์โหลด เปิดและ export ทีละบริษัทตามลำดับ รอข้อความย้ายไฟล์แล้วจึงทำบริษัทถัดไป
ใช้โฟลเดอร์ดาวน์โหลดเฉพาะงานนี้ ลิงก์มาจาก url_template ซึ่งยังต้องตรวจสอบด้วยคน
หน้านี้ไม่แสดงความคืบหน้า ให้ดูเลขทะเบียนถัดไปใน terminal</p>
<table><thead><tr><th>#</th><th>ชื่อ / Name</th><th>เลขทะเบียน / ID</th><th>Link</th></tr></thead><tbody>
''' + "\n".join(rows) + "</tbody></table></html>\n"
        _save(path, {"version": 1, "ids": [c.juristic_id for c in universe.companies],
                     "next": 0, "files_dir": str(Path(cfg.financials.files_dir).resolve()), "inflight": None})
        queue.write_text(html, encoding="utf-8")
    return len(universe.companies)


class DownloadWatcher:
    """Poll new files only, waiting for unchanged size/mtime across a settling interval.

    Caller must hold queue_lock for the lifetime of this object. No browser or HTTP is used.
    """

    def __init__(self, cfg: Config, directory: Path, queue: Path, *, settle_seconds: float = 2,
                 clock: Callable[[], float] = time.monotonic, progress: Callable[[str], None] = print):
        if not math.isfinite(settle_seconds) or settle_seconds <= 0:
            raise ConfigError("settle_seconds must be positive and finite")
        self.directory, self.path = directory.resolve(), state_path(queue.resolve())
        if not self.directory.is_dir():
            raise ConfigError(f"download directory not found: {self.directory}")
        self.state = _load(self.path)
        self.destination = Path(cfg.financials.files_dir).resolve()
        if str(self.destination) != self.state["files_dir"]:
            raise ConfigError("financials.files_dir differs from the saved queue; use the original config")
        if self.destination == self.directory:
            raise ConfigError("download directory and financials.files_dir must differ")
        self.clock, self.progress, self.settle_seconds = clock, progress, settle_seconds
        self.ignored = set(self._snapshot())
        self.pending: dict[Path, tuple[tuple[int, int], float]] = {}
        self._recover()
        self.report_next()

    @property
    def done(self) -> bool:
        return self.state["next"] == len(self.state["ids"])

    def report_next(self):
        self.progress("Queue complete" if self.done else f'Next company: {self.state["ids"][self.state["next"]]}')

    def _snapshot(self) -> dict[Path, tuple[int, int]]:
        files = {}
        for path in self.directory.iterdir():
            if path.suffix.lower() not in SUFFIXES or path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
                files[path] = (stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                continue  # browser renamed it while scanning
        return files

    def _finish(self):
        self.state["next"] += 1
        self.state["inflight"] = None
        _save(self.path, self.state)

    def _recover(self):
        item = self.state["inflight"]
        if item is None:
            return
        source, target = Path(item["source"]), self.destination / item["filename"]
        if source.parent != self.directory:
            raise ConfigError("resume with the same download directory as the interrupted move")
        if target.exists():
            if source.exists():
                raise ConfigError(f"interrupted move or collision: inspect {source} and {target}; queue unchanged")
            self._finish()
        elif source.is_file():
            # Re-check stability after restart before retrying the saved assignment.
            self.ignored.discard(source)
        else:
            raise ConfigError(f"interrupted move: neither {source} nor {target} exists")

    def poll(self) -> int:
        if self.done:
            return 0
        snapshot, now = self._snapshot(), self.clock()
        for path in list(self.pending):
            if path not in snapshot:
                del self.pending[path]
        for path, fingerprint in sorted(snapshot.items(), key=lambda item: (item[1][1], item[0].name)):
            if path in self.ignored:
                continue
            previous = self.pending.get(path)
            if previous is None or previous[0] != fingerprint:
                self.pending[path] = (fingerprint, now)
        # Preserve discovery order; a still-growing earlier file must not be overtaken.
        candidates = list(self.pending)
        if self.state["inflight"]:
            candidates = [Path(self.state["inflight"]["source"])]
        moved = 0
        for source in candidates:
            pending = self.pending.get(source)
            if pending is None or now - pending[1] < self.settle_seconds or pending[0][0] == 0:
                break
            if any(source.with_name(source.name + suffix).exists() for suffix in (".crdownload", ".part")):
                break
            jid = self.state["ids"][self.state["next"]]
            target = self.destination / (jid + source.suffix.lower())
            if target.exists():
                raise ConfigError(f"destination exists: {target}; no file overwritten, queue unchanged")
            self.destination.mkdir(parents=True, exist_ok=True)
            self.state["inflight"] = {"source": str(source), "filename": target.name}
            _save(self.path, self.state)  # reserve this ID before moving, for restart recovery
            try:
                _move_export(source, target)
            except OSError as e:
                raise ConfigError(f"move failed for {source}: {e}; stop and inspect before resuming") from e
            self._finish()
            del self.pending[source]
            self.progress(f"Moved {source.name} -> {target}")
            self.report_next()
            moved += 1
            if self.done:
                break
        return moved


def watch_downloads(cfg: Config, directory: Path, queue: Path, *, poll_seconds: float = 1,
                    settle_seconds: float = 2) -> None:
    with queue_lock(queue.resolve()):
        watcher = DownloadWatcher(cfg, directory, queue, settle_seconds=settle_seconds)
        while not watcher.done:
            time.sleep(poll_seconds)
            watcher.poll()
