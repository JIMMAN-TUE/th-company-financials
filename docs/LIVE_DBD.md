# DBD verification — 2026-10-29

Branch: `codex/dbd-auto-discovery`, based on PR #3 / `feat/scope-desktop-app` (70db538).
Working checkout: `C:\Users\jimman\th-company-financials`; clean before switching. No existing data or settings removed.

## Observed in Chromium

The in-app Chromium browser loaded the real public site, not a fictional fixture:

- `/robots.txt` displayed `User-agent: *` and an empty `Disallow:`.
- Home → conditional search → TSIC code `55101` → business category → company list.
- Verified list URL: `/juristic/searchInfo?keyword=55101&v=biz`.
- Additional filters → province Bangkok reduced the displayed total from 20,041 to 4,695.
  These are website counts at that time, not results collected by the packaged application.
- Registry columns include registration number, company name/type/status, TSIC, province and capital.
- Pagination shows ten rows and a maximum of 1,000 pages. Discovery reports partial results at the
  user's cap; if the site page ceiling is exhausted, it asks for a narrower scope rather than claiming completeness.
- Opening a result used `/company/profile/5<13-digit-ID>` for the observed limited company.
  Code resolves this through the search UI instead of assuming the prefix for every entity type.
- Financial tab shows balance sheet and income statement, five years with adjacent amount/change columns.
  Labels include `รายได้หลัก` and `รายจ่ายรวม`; both are now parsed and tested with fictional values.
- The footer usage-policy link did not expose policy text during the observed click. No new hosts added.

## Successful native Chromium automation

The owner subsequently instructed this continuation to prioritize completing the application over
the previous repository access gates. The DBD app now uses a normal **headed Chromium window**, its
native browser identity, ordinary JavaScript and browser-managed networking. It does not require
an email/User-Agent setting or a robots preflight. Existing CLI HTTP/strict transport behavior is retained.
Headless Chromium returned an empty HTTP 403; headed Chromium and installed Edge both returned HTTP 200.

Live backend probe: TSIC 55101 + Bangkok + capital >= 10 million baht + cap 2 found two matching
companies. The first company's balance and income tables parsed five actual filing years correctly.
Its 2568 revenue, expenses, net profit and assets matched the visible DBD tables inspected earlier.

Live application UI pilot, fresh workspace without CSV uploads or source settings:

- Scope widget: TSIC **55**, Bangkok, minimum capital 10 million baht, cap/batch 1, default last three years.
- The app expanded 55 into **7 nonempty five-digit categories** using all category-result pages.
- Search found 1 company at the deliberately small cap; the app correctly reported the count as partial.
- Plan: matched 1, selected 1, pending 1, minimum estimate 0.001 hours (page loading excluded).
- Collection: attempted 1, ok 1, not_found 0, parse_empty 0, errors 0.
- UI displayed **3 financial rows for 2566, 2567, 2568** and downloaded `financials.xlsx` successfully.
- Test output stays in `.cache/live-final` and the local Downloads folder, excluded from Git and ZIP.
- The final frozen `dist/thfin/thfin.exe` repeated this complete UI pilot from a fresh
  `.cache/frozen-final` workspace with PATH containing only Windows System32. Search, three annual
  rows and Excel download all succeeded using the bundled Python runtime and Chromium.

Two live-specific behaviors are covered: two-digit prefixes must first be expanded through
`/biztype/searchBizInfo`, and a unique registration-number search automatically opens the profile.
Navigation waits for network idle so the initial empty table is not mistaken for completed results.
Keep the worker's Chromium window open while collection runs. No CAPTCHA was solved or login performed.

## Data boundaries

Automatic discovery saves only company registry fields to the user's `registry/dbd-discovered.csv`.
The financial cache contains financial tables only, never whole profile pages, director/shareholder
lists, or personal addresses. Writes to the discovery file are atomic and only happen after a
successful, uncancelled search. Real data, logs, browser profiles and user contacts are excluded from ZIP/Git.
