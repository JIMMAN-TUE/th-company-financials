# Handover

**สรุปภาษาไทย:** โปรเจกต์นี้ดึงรายได้ ต้นทุนขาย ค่าใช้จ่าย และกำไรของนิติบุคคลไทยจากแหล่งข้อมูลทางการ
ปรับขอบเขต (จังหวัด, อุตสาหกรรม TSIC, ทุนจดทะเบียน, ปีงบ, ช่วงรายได้) ได้จากไฟล์ `config/scope.yaml`
โค้ดทดสอบด้วยข้อมูลบริษัทสมมติ ผลตรวจเว็บ DBD และข้อที่ยังไม่ยืนยันอยู่ใน Findings ด้านล่าง
Chromium backend กับตัวช่วย export ด้วยมือเพิ่มเมื่อ 2026-10-29

**อัปเดต 2026-10-28:** ตรวจกับเว็บจริงแล้ว DataWarehouse+ (`datawarehouse.dbd.go.th`) ใช้ระบบกันบอท
Imperva Incapsula ตอบ challenge ทุก path รวมถึง `robots.txt` ทั้งจาก cloud และจาก IP ในไทย
ผลตรวจนี้ยังไม่ยืนยันการเข้าถึงผ่าน Chromium; วันที่ 2026-10-29 เจ้าของแก้คำสั่งให้ใช้ Chromium
ในแพ็กเกจเดียวกันได้ ยกเลิกข้อห้าม headless แบบเหมารวม ตัวช่วย export ด้วยมือยังใช้ได้ ดู Findings ด้านล่าง

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
| **Live DBD fetch** | **blocked**: Incapsula bot challenge on every path incl. robots.txt (2026-10-28). Chromium access unverified |
| Registry CSV from the real DBD open data | **not verified**: site reachable from a Thai IP; no CSV downloaded or mapped yet |

Tests: `make test` (offline; Linux passes with ruff clean). The full development run includes
133 tests with real Chromium against a fictional loopback server. On Windows run
`python -m unittest discover -s tests`; the 19 Windows-only errors (backslash paths in YAML test configs)
were fixed on `chore/verify-dbd` but have not been re-run on Windows yet.

## Findings (2026-10-28, branch `chore/verify-dbd`)

One request per host, polite user agent, nothing collected.

| Host | From cloud (US IP) | From the owner's PC (Thai IP) |
|---|---|---|
| `datawarehouse.dbd.go.th/robots.txt` | 302 to itself ("Loading" page, Incapsula cookies), then 403 | same 302 "Loading" challenge |
| `opendata.dbd.go.th/robots.txt` | 403 Incapsula block | **200**, see below |
| `data.go.th` | 403 Cloudflare "Access Denied" | not tried |
| `openapi.dbd.go.th`, `www.dbd.go.th` | blocked by the session's egress proxy | not tried |

`opendata.dbd.go.th/robots.txt` (as served 2026-10-28):

```
User-agent: *
Disallow: /dataset/rate/
Disallow: /revision/
Disallow: /dataset/*/history
Disallow: /api/
Crawl-Delay: 10
```

What this means for the four unverified assumptions:

1. **URL template**: cannot be confirmed; every DataWarehouse+ URL returns the bot challenge.
2. **JS/XHR tables**: cannot be checked by a program for the same reason. A person can open a company in a
   normal browser and look at the network tab. Chromium rendering is now supported, subject to the shared policy.
3. **Registry columns**: still open. Owner downloads CSVs by hand, then `registry-headers`. A web search
   found only monthly "newly registered" (`dataset_11_01`) and "dissolved" (`dataset_11_02`) files, not a
   full registry; they may lack the 13-digit ID or a status column. Check before relying on them.
4. **Terms of use**: DataWarehouse+ terms not found by search and not reachable. The bot challenge itself
   says automated access is not welcome. `docs/LEGAL.md` is not yet updated (owner approves the wording).

`PoliteClient` already behaves correctly here: robots 403 = disallow everything; the 302 loop raises
`FetchError` and the run stops. The `Crawl-delay` gap was addressed on `feat/manual-export-queue`: the client now uses
`max(crawl_delay, 1 / requests_per_second)` seconds, including retries. No hosts were added.

Decision update (2026-10-29): the owner explicitly corrected the earlier instruction and authorized
Chromium in the same package. The blanket headless-browser prohibition is superseded.
The browser backend retains robots.txt, configured user agent, rate limits and the unchanged allowlist.
This is not evidence of working live DBD access or confirmation of the site terms.

## First steps (needs a machine that can reach DBD sites)

Status 2026-10-28: step 3 is blocked (see Findings). For the pilot use `financials.mode: files`
(a person exports Excel or saves the page HTML per company; file name contains the 13-digit ID),
and ask DBD (`document@dbd.go.th`) for an API, bulk file or written permission.

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
  (Live access and site terms remain unverified; Chromium support does not establish either.)
- Does the team want a helper for the manual route (list of company links + move exports from Downloads
  into `data/statements/` named by ID)?
- Which fields matter beyond revenue, cost of sales, expenses, net profit, total assets?
- Which industries and regions are the first real scope? (Presets in `config/presets/` are examples.)
- Where may the collected data live, and who may see it?

## Working on this with Claude

- **Claude Code**: clone the repo, open the folder, start Claude Code. `CLAUDE.md` loads automatically and
  these slash commands exist: `/scope` (change the scope safely), `/probe` (test one company and fix the
  parser), `/collect` (run a batch), `/qa` (spot-check data), `/handover` (refresh this file).
- **claude.ai**: attach the repo to a session, or paste `CLAUDE.md` and this file into a Project so
  teammates start from the same context.

## Manual export helper (2026-10-29)

`feat/manual-export-queue` builds on `chore/verify-dbd` (PR #1).

- `thfin queue` creates scoped company links in `manual-queue.html` with a durable JSON FIFO.
- `thfin watch-downloads --dir <dedicated Downloads>` moves new, stable `.xlsx` / `.html` files
  to `financials.files_dir`, named by the next 13-digit ID. Run it BEFORE exporting, one file per
  company in queue order; wait for `Moved` before starting the next company. It cannot verify identity.
- No company URLs are requested or opened by these commands; URL template remains unverified.
- `Ctrl+C` resumes from the same JSON state. Existing files are ignored; destination collisions stop
  without overwriting. See README for interrupted-copy recovery and stale lock handling.
- Offline fictional-data tests cover the helpers and crawl delay; CI now includes Windows + Linux
  on Python 3.10 / 3.12. The current development environment is Linux, so Windows results come from CI.

No real registry, financial files, `data/`, `.cache/`, allowed hosts or LEGAL wording were changed.
Chromium rendering is now an optional backend in the same package (`pip install -e ".[browser]"`),
selected by `--set runtime.transport=chromium`. Robots are read as plain HTTP before browser launch.
All page/script/XHR requests are checked; redirects stop and require a final allowed URL.
Real Chromium tests use a fictional loopback site. No live DBD Chromium check has been made.
