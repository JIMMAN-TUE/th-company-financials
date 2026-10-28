# Legal and ethical checklist

This is a working checklist, not legal advice. Have your own counsel or compliance team confirm it
before using the data commercially or at scale.

## Before the first real run

- [ ] Read the terms of use of every host in `runtime.allowed_hosts` (currently `datawarehouse.dbd.go.th`).
      Write down what they say about automated access: ______________________
- [ ] If automated access is not clearly permitted, ask the data owner in writing (for open data the
      contact listed on the dataset page is `document@dbd.go.th`) or use `financials.mode: files`.
- [ ] Set a real contact in `runtime.user_agent` (the tool refuses to run with the placeholder).
- [ ] Keep `respect_robots: true`.
- [ ] Decide who owns the collected data and where it may be stored. `data/` is gitignored on purpose.

## Built-in safeguards (do not remove)

- Host allowlist: the client cannot call a host that is not listed.
- `robots.txt` is checked per host; unreadable robots.txt means no fetching.
- Rate limit (default 0.5 requests/s), exponential backoff, honours `Retry-After`.
- Circuit breaker: stops after 10 consecutive errors rather than hammering a struggling site.
- Disk cache: a page is never fetched twice unless `--refetch` is used.
- `max_companies` cap and a confirmation flag (`--yes`) for runs above 1,000 companies.

## Personal data (PDPA)

- Scope is juristic persons (companies). Do not collect directors, shareholders, individuals' names,
  phone numbers or addresses. The data model has no fields for them; keep it that way.
- Company names can contain a person's name (for example a sole-director firm). If you publish or share
  results outside the team, review whether that is acceptable.

## Decisions log

| Date | Decision | By |
|---|---|---|
| 2026-10-29 | Project created with `datawarehouse.dbd.go.th` as the only allowed host; ToS not yet reviewed | initial build |
