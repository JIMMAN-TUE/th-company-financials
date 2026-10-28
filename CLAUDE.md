# th-company-financials (`thfin`)

Collects revenue, cost of sales, expenses and profit for **Thai juristic persons** (companies) from
official public sources, with a scope that can be widened or narrowed from a YAML file.
The owner and team communicate in Thai and English; reply in the language the user writes in.

## Commands (run from the repo root)

```bash
make test                                   # 99 offline unit tests, no network needed
python -m thfin plan                        # what the scope selects + time estimate. Fetches nothing
python -m thfin registry-headers            # show registry CSV headers and the detected column mapping
python -m thfin probe <13-digit-id>         # fetch ONE page and show what the parser extracts
python -m thfin run --limit 25              # collect (resumable; re-run to continue)
python -m thfin import-files                # offline mode: parse statements exported by hand
python -m thfin export                      # CSV (+XLSX) from the SQLite database
python -m thfin status
python -m thfin ui                          # local scope-builder widget (127.0.0.1, token-protected, no internet)
# any command accepts:  --config config/presets/hotels_bangkok.yaml   --set scope.provinces=ชลบุรี,ระยอง
```

Install: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`
(or just `PYTHONPATH=src python3 -m thfin ...` with pydantic, pyyaml, beautifulsoup4, openpyxl present).

## Changing the scope (the most common request)

1. Edit `config/scope.yaml` (or add a file under `config/presets/`, or pass `--set`).
2. **Always run `plan` and report** matched / selected / estimated hours to the user. Do not `run` first.
3. Scope fields: provinces, tsic_prefixes, juristic_types, statuses, name_contains, capital_min/max,
   years_back (default 3) + latest_fiscal_year, or explicit fiscal_years (Buddhist Era, wins over years_back),
   revenue_min/max (applied at export), include_ids, exclude_ids, max_companies.
   Widening the years later re-processes already collected companies from the HTTP cache, so it
   normally costs no new requests. The UI (`thfin ui`) edits only the `scope:` block of a config file.
4. Keep `max_companies` as a deliberate cap. Raising it is the user's decision, not a side effect.

## Code map

| Path | Role |
|---|---|
| `src/thfin/config.py` | pydantic config; rejects unknown keys; `--set` overrides; `Scope.effective_years()` |
| `src/thfin/scopeio.py`, `uiserver.py`, `ui/index.html` | scope-builder widget: rewrite the `scope:` block, live preview, local server |
| `src/thfin/sources/registry.py` | company universe from registry CSVs; column auto-detect; scope matching |
| `src/thfin/http.py` | polite client: host allowlist, robots.txt, rate limit, retries, disk cache |
| `src/thfin/parsers/statement.py` | table -> revenue/cost/profit by Thai/English label patterns (`FIELD_PATTERNS`) |
| `src/thfin/pipeline.py` | plan, run (circuit breaker, resumable), import_files |
| `src/thfin/store.py` | SQLite upserts, margin calculations, CSV/XLSX export |
| `tests/` | `unittest` (also runs under pytest); fake transport, fixtures with fictional companies |

## Hard rules

- Respect `robots.txt`, the rate limit and `runtime.allowed_hosts`. Do not lower the delay, disable
  robots checks, rotate user agents or work around a block. If a site refuses, stop and report it.
- Add a host to `allowed_hosts` only after reading its terms of use, and record the decision in
  `docs/LEGAL.md`.
- Juristic persons only. Do not collect or store directors, shareholders, phone numbers, addresses of
  individuals or any other personal data (PDPA). Do not add columns for them.
- Never commit anything under `data/` or `.cache/` (gitignored), tokens, or real contact emails you
  were not given.
- Any parser change needs a test in `tests/test_parsers.py` using a fictional fixture.
- Do not invent data. If a field cannot be extracted, store NULL and let `status` show `parse_empty`.

## Known unverified assumptions (first job of whoever has network access)

These could not be checked when the project was created (the build sandbox could not reach DBD sites):

1. `financials.url_template` is a placeholder; the real DataWarehouse+ URL pattern is unconfirmed.
2. The financial tables may be rendered by JavaScript or an XHR/JSON call rather than present in static
   HTML. If `probe` finds 0 tables, find the JSON endpoint in the browser network tab (or fall back to
   `import-files` with manual Excel exports).
3. Registry CSV column names, status text (`ดำเนินกิจการอยู่`) and TSIC code format are guesses.
   Run `registry-headers` and adjust `registry.columns` / `scope`.
4. The terms of use for automated access to DataWarehouse+ are unconfirmed. See `docs/LEGAL.md`.

Status 2026-10-28: DataWarehouse+ answers every request (robots.txt included) with an Imperva Incapsula
bot challenge, so `mode: web` is blocked; do not work around it. Details in `docs/HANDOVER.md` "Findings".

See `docs/HANDOVER.md` for status and next steps, and `docs/DATA_SOURCES.md` for sources.
