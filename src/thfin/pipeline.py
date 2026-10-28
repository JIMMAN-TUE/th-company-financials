"""Orchestration: scope -> plan -> fetch -> parse -> store. Safe to stop and re-run."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config, ConfigError
from .http import FetchError, HostNotAllowed, PoliteClient, RobotsDisallowed
from .models import Company, Financials
from .parsers.statement import YearData, parse_excel, parse_html
from .sources.registry import Universe, load_universe
from .store import Store

LARGE_RUN = 1000
HUGE_RUN = 20000


@dataclass
class Plan:
    universe: Universe
    pending: list[Company]
    already_done: int
    est_requests: int
    est_hours: float
    warnings: list[str] = field(default_factory=list)


def build_plan(
    cfg: Config, store: Store | None = None, refetch: bool = False, companies: Iterable[Company] | None = None
) -> Plan:
    uni = load_universe(cfg, companies)
    done = set() if (refetch or store is None) else store.done_ids(cfg.scope.effective_years())
    pending = [c for c in uni.companies if c.juristic_id not in done]
    hours = len(pending) / cfg.runtime.requests_per_second / 3600
    warnings: list[str] = []
    if uni.truncated:
        warnings.append(
            f"scope matched {uni.total_matched:,} companies but max_companies={cfg.scope.max_companies:,}; "
            "only the first (by juristic ID) will be collected. Narrow the scope or raise the cap."
        )
    if len(pending) > HUGE_RUN:
        warnings.append(
            f"{len(pending):,} requests at {cfg.runtime.requests_per_second}/s is ~{hours:,.0f} hours. "
            "Collecting 'all of Thailand' this way is impractical: prefer official bulk data (docs/DATA_SOURCES.md)."
        )
    elif len(pending) > LARGE_RUN:
        warnings.append(f"large run: ~{hours:,.1f} hours. Run in batches with --limit.")
    if cfg.scope.include_ids:
        warnings.append("include_ids is set: other scope filters are ignored for the company list.")
    return Plan(uni, pending, len(uni.companies) - len(pending), len(pending), hours, warnings)


@dataclass
class RunSummary:
    attempted: int = 0
    ok: int = 0
    not_found: int = 0
    parse_empty: int = 0
    errors: int = 0
    from_cache: int = 0
    aborted: str = ""


def _to_rows(juristic_id: str, data: YearData, cfg: Config, source: str) -> list[Financials]:
    years = set(cfg.scope.effective_years())
    mult = cfg.financials.unit_multiplier
    rows = []
    for year, fields in sorted(data.items()):
        if years and year not in years:
            continue
        scaled = {k: (v * mult if v is not None else None) for k, v in fields.items()}
        rows.append(Financials(juristic_id=juristic_id, fiscal_year=year, source=source, **scaled))
    return rows


def run(
    cfg: Config,
    client: PoliteClient,
    store: Store,
    *,
    limit: int | None = None,
    refetch: bool = False,
    progress: Callable[[str], None] = print,
) -> RunSummary:
    if "CHANGE-ME" in cfg.runtime.user_agent:
        raise ConfigError(
            "runtime.user_agent still has the CHANGE-ME placeholder. Put a real contact "
            "(e.g. 'thfin-research/0.1 (+mailto:you@company.com)') so site owners can reach you."
        )
    plan = build_plan(cfg, store, refetch)
    for w in plan.warnings:
        progress(f"WARNING: {w}")
    store.upsert_companies(plan.universe.companies)
    todo = plan.pending[:limit] if limit else plan.pending
    summary = RunSummary()
    years_wanted = cfg.scope.effective_years()
    consecutive_errors = 0
    for i, company in enumerate(todo, 1):
        url = cfg.financials.url_template.format(juristic_id=company.juristic_id)
        summary.attempted += 1
        try:
            resp = client.get(url, use_cache=not refetch)
        except (HostNotAllowed, RobotsDisallowed) as e:
            summary.aborted = str(e)
            progress(f"STOP: {e}")
            break
        except FetchError as e:
            summary.errors += 1
            consecutive_errors += 1
            store.mark(company.juristic_id, "error", str(e))
            progress(f"[{i}/{len(todo)}] {company.juristic_id} error: {e}")
            if consecutive_errors >= cfg.runtime.max_consecutive_errors:
                summary.aborted = f"{consecutive_errors} consecutive errors; stopping to avoid hammering the site"
                progress(f"STOP: {summary.aborted}")
                break
            continue
        consecutive_errors = 0
        summary.from_cache += int(resp.from_cache)
        if resp.status == 404:
            summary.not_found += 1
            store.mark(company.juristic_id, "not_found")
        elif resp.status != 200:
            summary.errors += 1
            store.mark(company.juristic_id, "error", f"HTTP {resp.status}")
        else:
            data = parse_html(resp.text)
            if not data:
                summary.parse_empty += 1
                store.mark(company.juristic_id, "parse_empty", "no year/label match; run `thfin probe`")
            else:
                rows = _to_rows(company.juristic_id, data, cfg, source="web")
                store.upsert_financials(rows)
                summary.ok += 1
                detail = "" if rows else f"no scope years; found {sorted(data)}"
                store.mark(company.juristic_id, "ok", detail, years=years_wanted)
        if i % 25 == 0 or i == len(todo):
            progress(f"[{i}/{len(todo)}] ok={summary.ok} not_found={summary.not_found} "
                     f"parse_empty={summary.parse_empty} errors={summary.errors}")
    return summary


def import_files(cfg: Config, store: Store, progress: Callable[[str], None] = print) -> RunSummary:
    """Offline mode: parse statements you exported manually (xlsx / html) from DBD DataWarehouse+."""
    folder = Path(cfg.financials.files_dir)
    if not folder.is_dir():
        raise ConfigError(f"financials.files_dir not found: {folder}")
    summary = RunSummary()
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".html", ".htm"}:
            continue
        m = re.search(r"(?<!\d)(\d{13})(?!\d)", path.name)
        if not m:
            progress(f"skip {path.name}: no 13-digit juristic ID in the file name")
            continue
        jid = m.group(1)
        summary.attempted += 1
        try:
            data = (
                parse_excel(path)
                if path.suffix.lower() in {".xlsx", ".xlsm"}
                else parse_html(path.read_text(encoding="utf-8", errors="replace"))
            )
        except Exception as e:  # corrupt file etc.
            summary.errors += 1
            store.mark(jid, "error", f"{path.name}: {e}")
            continue
        if not data:
            summary.parse_empty += 1
            store.mark(jid, "parse_empty", path.name)
            continue
        store.upsert_financials(_to_rows(jid, data, cfg, source=f"file:{path.name}"))
        store.mark(jid, "ok", path.name, years=cfg.scope.effective_years())
        summary.ok += 1
    progress(f"imported ok={summary.ok} parse_empty={summary.parse_empty} errors={summary.errors}")
    return summary
