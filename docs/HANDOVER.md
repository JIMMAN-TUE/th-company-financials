# Handover

**สรุปภาษาไทย:** โปรเจกต์นี้ดึงรายได้ ต้นทุนขาย ค่าใช้จ่าย และกำไรของนิติบุคคลไทยจากแหล่งข้อมูลทางการ
ปรับขอบเขต (จังหวัด, อุตสาหกรรม TSIC, ทุนจดทะเบียน, ปีงบ, ช่วงรายได้) ได้จากไฟล์ `config/scope.yaml`
โค้ดและเทสต์ 99 ข้อผ่านครบ แต่ **ยังไม่เคยทดสอบกับเว็บ DBD จริง** (เครื่องที่สร้างต่อเว็บนั้นไม่ได้)
งานแรกของคนที่รับช่วงคือยืนยัน 4 ข้อใน "First steps" ด้านล่าง ก่อนรันจริง

## State (2026-10-29)

| Area | State |
|---|---|
| Config + scope filters (incl. `years_back`, e.g. last 3 closed years) | done, tested |
| Scope-builder UI (`thfin ui`) with live counts and safe YAML save | done; tests + driven in headless Chromium against the fictional registry; not tried with a real registry |
| Widening years later (re-parse from cache, no new requests) | done, tested |
| Registry CSV loader (column auto-detect, Bangkok aliases, leading-zero repair) | done, tested on a fictional CSV |
| Polite HTTP client (allowlist, robots, rate limit, retries, cache) | done, tested with a fake transport |
| Statement parser (Thai/English labels, B.E./C.E. year headers, HTML + Excel) | done, tested on fictional fixtures |
| Pipeline (plan, resumable run, circuit breaker, offline `import-files`) | done, tested |
| SQLite store + CSV/XLSX export with gross/net margin | done, tested |
| CLI, presets, CI workflow, Claude Code commands | done |
| **Live DBD fetch** | **not verified** |
| Registry CSV from the real DBD open data | **not verified** |

Tests: `make test` (99, offline). Lint (`ruff`) could not be installed in the build sandbox, so the
CI lint job is the first time it runs; fix any findings it reports.

## First steps (needs a machine that can reach DBD sites)

1. **Registry**: download the DBD open-data CSV(s) into `data/registry/`, run
   `python -m thfin registry-headers`. Fix `registry.columns`, the status text in `scope.statuses` and the
   TSIC format in `scope.tsic_prefixes` until `plan` shows plausible counts.
2. **Terms**: read DataWarehouse+ terms, fill in `docs/LEGAL.md`, set a real contact via the
   `THFIN_USER_AGENT` environment variable (or `runtime.user_agent`).
3. **Probe**: `python -m thfin probe <id of a company you can also view in the browser>`.
   - Tables found and years/fields printed: compare with the website by eye, then go to 4.
   - 0 tables or empty result: the numbers are probably loaded by a script. Find the JSON/XHR endpoint in
     the browser network tab, set `financials.url_template`, and adapt the parser (add a test with a
     fictional fixture). Fallback: `financials.mode: files` with manual Excel exports.
4. **Pilot**: `python -m thfin run --limit 25`, then `status`, then `export`. Check 3 companies against the
   website, check margins look sane, check `parse_empty` / `not_found` counts.
5. Only then widen the scope (`plan` first, run in batches with `--limit`).

## Decisions already made

- Standard library HTTP (no httpx/requests) so it runs in locked-down environments; transport is injectable.
- Default `max_companies: 200`, rate 0.5/s, robots respected, one allowed host. Loosen only on purpose.
- `include_ids` means "exactly these companies" and ignores the other scope filters.
- Years are Buddhist Era internally; Gregorian years in config are converted.
- Revenue band (`revenue_min/max`) filters at export, because revenue is only known after fetching.

## Open questions for the owner

- Is automated access to DataWarehouse+ allowed, or should the team ask DBD for bulk/API access?
- Which fields matter beyond revenue, cost of sales, expenses, net profit, total assets?
- Which industries and regions are the first real scope? (Presets in `config/presets/` are examples.)
- Where may the collected data live, and who may see it?

## Working on this with Claude

- **Claude Code**: clone the repo, open the folder, start Claude Code. `CLAUDE.md` loads automatically and
  these slash commands exist: `/scope` (change the scope safely), `/probe` (test one company and fix the
  parser), `/collect` (run a batch), `/qa` (spot-check data), `/handover` (refresh this file).
- **claude.ai**: attach the repo to a session, or paste `CLAUDE.md` and this file into a Project so
  teammates start from the same context.
