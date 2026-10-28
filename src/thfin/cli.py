"""Command line: `python -m thfin <command> [--config FILE] [--set key=value ...]`."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .config import Config, ConfigError, load_config
from .http import FetchError, PoliteClient
from .manual import DEFAULT_QUEUE, create_queue, watch_downloads
from .parsers.statement import html_tables_to_rows, parse_html
from .pipeline import LARGE_RUN, build_plan, import_files, run
from .sources.registry import ALIASES, read_headers, registry_files, resolve_columns
from .store import Store, write_csv, write_xlsx


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config/scope.yaml", help="YAML config (default: %(default)s)")
    common.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
        help="override a config value, e.g. --set scope.provinces=ชลบุรี,ระยอง (repeatable)",
    )
    p = argparse.ArgumentParser(prog="thfin", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", parents=[common], help="show what the scope selects and the cost; fetches nothing")
    sp.add_argument("--refetch", action="store_true")

    sp = sub.add_parser("run", parents=[common], help="collect financials for the scoped companies")
    sp.add_argument("--limit", type=int, help="process at most N companies this run")
    sp.add_argument("--refetch", action="store_true", help="ignore cache and previous results")
    sp.add_argument("--yes", action="store_true", help=f"confirm runs above {LARGE_RUN:,} companies")

    sp = sub.add_parser("probe", parents=[common], help="fetch ONE company and show what the parser extracts")
    sp.add_argument("juristic_id")

    sub.add_parser("registry-headers", parents=[common], help="show registry CSV headers and detected mapping")
    sub.add_parser("import-files", parents=[common], help="parse manually exported statements from files_dir")

    sp = sub.add_parser("queue", parents=[common], help="create an offline HTML queue for manual exports")
    sp.add_argument("--out", default=DEFAULT_QUEUE, help="HTML output; progress saved alongside as .html.json")

    sp = sub.add_parser("watch-downloads", parents=[common], help="move new manual exports in queue order")
    sp.add_argument("--dir", required=True, type=Path, help="dedicated browser download directory")
    sp.add_argument("--queue", default=DEFAULT_QUEUE, type=Path, help="HTML queue created by thfin queue")

    sp = sub.add_parser("export", parents=[common], help="write CSV/XLSX from the database")
    sp.add_argument("--format", choices=["csv", "xlsx", "both"], default="both")
    sp.add_argument("--out", help="output path without extension")

    sub.add_parser("status", parents=[common], help="database counts and fetch statuses")

    sp = sub.add_parser("ui", parents=[common], help="open the local scope-builder UI (nothing is fetched)")
    sp.add_argument("--port", type=int, default=8765, help="0 = pick a free port")
    sp.add_argument("--no-browser", action="store_true")
    return p


def _load(args) -> Config:
    return load_config(args.config, args.overrides)


def cmd_plan(args) -> int:
    cfg = _load(args)
    db = Path(cfg.runtime.db_path)
    store = Store(db) if db.exists() else None
    plan = build_plan(cfg, store, args.refetch)
    u, s = plan.universe, cfg.scope
    print(f"Scope: provinces={s.provinces or 'ALL'} tsic={s.tsic_prefixes or 'ALL'} "
          f"types={s.juristic_types or 'ALL'} years={s.effective_years()}")
    print(f"Matched in registry : {u.total_matched:,}")
    print(f"Selected (cap {s.max_companies:,}) : {len(u.companies):,}")
    print(f"Already collected   : {plan.already_done:,}")
    print(f"To fetch now        : {plan.est_requests:,}  (~{plan.est_hours:.2f} h at {cfg.runtime.requests_per_second}/s)")
    if u.by_province:
        print("Top provinces       : " + ", ".join(f"{k}={v:,}" for k, v in u.by_province.most_common(5)))
    if u.by_tsic2:
        print("Top TSIC (2-digit)  : " + ", ".join(f"{k}={v:,}" for k, v in u.by_tsic2.most_common(5)))
    for w in plan.warnings:
        print(f"WARNING: {w}")
    return 0


def cmd_run(args) -> int:
    cfg = _load(args)
    if cfg.financials.mode == "files":
        store = Store(cfg.runtime.db_path)
        return 0 if import_files(cfg, store).errors == 0 else 1
    store = Store(cfg.runtime.db_path)
    plan = build_plan(cfg, store, args.refetch)
    n = min(plan.est_requests, args.limit) if args.limit else plan.est_requests
    if n > LARGE_RUN and not args.yes:
        print(f"Refusing to start {n:,} requests without --yes (or use --limit). Run `plan` first.")
        return 2
    client = PoliteClient(cfg.runtime)
    summary = run(cfg, client, store, limit=args.limit, refetch=args.refetch)
    print(f"done: {summary}")
    return 1 if summary.aborted else 0


def cmd_probe(args) -> int:
    cfg = _load(args)
    url = cfg.financials.url_template.format(juristic_id=args.juristic_id)
    client = PoliteClient(cfg.runtime)
    try:
        resp = client.get(url, use_cache=False)
    except FetchError as e:
        print(f"fetch failed: {e}")
        return 1
    out = Path(".cache/probe")
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"{args.juristic_id}.html"
    raw.write_bytes(resp.body)
    data = parse_html(resp.text)
    print(f"GET {url}\nHTTP {resp.status}, {len(resp.body):,} bytes, saved raw to {raw}")
    print(f"tables found: {len(html_tables_to_rows(resp.text))}")
    if not data:
        print("Parser extracted NOTHING. Open the raw file, find where revenue/cost appear, then update "
              "financials.url_template and/or FIELD_PATTERNS in src/thfin/parsers/statement.py "
              "(the page may load numbers via a JSON endpoint: look for it in the browser network tab).")
        return 3
    for year, fields in sorted(data.items()):
        print(year, {k: v for k, v in fields.items() if v is not None})
    return 0


def cmd_registry_headers(args) -> int:
    cfg = _load(args)
    files = registry_files(cfg)
    if not files:
        print(f"no files match {cfg.registry.csv_paths}; put DBD open-data CSVs in data/registry/")
        return 1
    for f in files:
        headers = read_headers(f, cfg)
        mapping = resolve_columns(headers, cfg.registry.columns)
        print(f"\n{f}\n  headers: {headers}")
        for fld in ALIASES:
            print(f"  {fld:<20} -> {mapping.get(fld, '(not detected)')}")
    return 0


def cmd_import_files(args) -> int:
    cfg = _load(args)
    return 0 if import_files(cfg, Store(cfg.runtime.db_path)).errors == 0 else 1


def cmd_queue(args) -> int:
    count = create_queue(_load(args), Path(args.out))
    print(f"Queued {count:,} companies -> {args.out}; start watch-downloads, then open the HTML yourself")
    return 0


def cmd_watch_downloads(args) -> int:
    watch_downloads(_load(args), args.dir, args.queue)
    return 0


def cmd_export(args) -> int:
    cfg = _load(args)
    store = Store(cfg.runtime.db_path)
    rows = store.export_rows(cfg.scope)
    base = Path(args.out) if args.out else Path(cfg.runtime.export_dir) / f"financials_{date.today():%Y%m%d}"
    written = []
    if args.format in ("csv", "both"):
        written.append(write_csv(rows, base.with_suffix(".csv")))
    if args.format in ("xlsx", "both"):
        written.append(write_xlsx(rows, base.with_suffix(".xlsx")))
    print(f"{len(rows):,} rows -> " + ", ".join(str(p) for p in written))
    return 0


def cmd_status(args) -> int:
    cfg = _load(args)
    if not Path(cfg.runtime.db_path).exists():
        print("no database yet; run `thfin run` first")
        return 0
    store = Store(cfg.runtime.db_path)
    print("tables:", store.counts())
    print("fetch statuses:", store.status_counts())
    return 0


def cmd_ui(args) -> int:
    from .uiserver import serve

    return serve(args.config, args.overrides, args.port, not args.no_browser)


COMMANDS = {
    "plan": cmd_plan, "run": cmd_run, "probe": cmd_probe, "registry-headers": cmd_registry_headers,
    "import-files": cmd_import_files, "export": cmd_export, "status": cmd_status, "ui": cmd_ui,
    "queue": cmd_queue, "watch-downloads": cmd_watch_downloads,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"file error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; progress is saved, re-run to resume", file=sys.stderr)
        return 130
