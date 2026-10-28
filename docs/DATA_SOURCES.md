# Data sources

Status key: **seen** = confirmed from a public page while building this; **assumed** = reasonable
but not confirmed; **unknown** = needs checking by someone with network access.

## 1. DBD Open Data (company universe)

- Portals: `opendata.dbd.go.th`, `bdex.dbd.go.th/opendata`, mirrored on `data.go.th`.
- **seen**: CSV files, one per year (B.E. 2563-2568 on the dataset page we read), licence "Open Data
  Common", no access restrictions, updated monthly, contact กองข้อมูลธุรกิจ `document@dbd.go.th`.
- **unknown**: exact column names; whether a given dataset lists one row per company (with the 13-digit
  juristic ID, province, TSIC, status, registered capital) or only aggregates. The page for the dataset
  we read described itself as statistical data. If the datasets are aggregates only, ask DBD for the
  per-company registry extract, or build the list of IDs from another source and use `scope.include_ids`.
- **seen** (2026-10-28): `opendata.dbd.go.th` sits behind Imperva; reachable from a Thai IP, blocked (403)
  from a US cloud IP. robots.txt disallows `/api/`, `/revision/`, `/dataset/rate/`, dataset history and sets
  `Crawl-Delay: 10`. Datasets found by search: `dataset_11_01` (newly registered, monthly) and
  `dataset_11_02` (dissolved, monthly); file downloads appear to be served from `openapi.dbd.go.th` (assumed).
- How it is used: download the CSV(s) into `data/registry/`, run `python -m thfin registry-headers`,
  fix `registry.columns` if needed.

## 2. DBD DataWarehouse+ (financial statements)

- Site: `datawarehouse.dbd.go.th`.
- **seen** (third-party article): web only, no login/registration needed, shows balance sheet and
  profit-and-loss statements per company, export to PDF or Excel.
- **seen** (2026-10-28): every path, including `/robots.txt`, answers with an Imperva Incapsula bot challenge
  (302 to itself with a "Loading" page) from both a Thai IP and a cloud IP. Automated access is therefore not
  possible without working around the block, which this project does not do. Use `financials.mode: files`
  or ask DBD for access.
- **unknown**: URL pattern, whether tables are in static HTML or loaded by script, any API, usage limits,
  terms of use for automated access, how many years are shown.
- Two ways to use it in this project: `financials.mode: web` (fetch pages, needs the URL and parser
  confirmed with `probe`) or `financials.mode: files` (a person exports Excel/HTML per company and drops
  it in `data/statements/`, file name must contain the 13-digit ID; `import-files` parses it).

## 3. Not implemented yet (extension points)

- **SET (listed companies)**: richer data and quarterly figures, but covers only listed firms.
  Add a new module under `src/thfin/sources/` and keep the same `Financials` model.
- **Commercial or official bulk data**: for national coverage this is the realistic route (see below).

## Coverage: what "all Thai entrepreneurs" can and cannot mean

- Only **juristic persons that file financial statements with DBD** have revenue/cost figures. Sole
  proprietors and many small businesses do not file, so they cannot be covered by any scraper.
- Filing is often late or missing, so a company can legitimately return "not found" or a year with no data.
- Statements are the company's own filing (unaudited for some small firms). Treat them as reported,
  not verified.
- The whole country is very large. At the default polite rate of 0.5 requests/s, every 1,000 companies
  takes about 33 minutes; `plan` prints the real estimate for the current scope and warns above 1,000
  and 20,000 requests. National coverage by scraping is impractical; use scoped runs, or ask DBD about
  bulk access.
