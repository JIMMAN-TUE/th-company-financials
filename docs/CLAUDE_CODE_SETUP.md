# Claude Code setup (environment + prompts)

## 1. Repository

Create an empty private repo (no README), push this project, then open the repo in Claude Code
(web, desktop or CLI). Protect `main` and work on branches: one branch per task, small commits, PR
with CI green before merge.

## 2. Cloud environment (New environment -> Cloud)

| Field | Value | Why |
|---|---|---|
| Name | `th-company-financials` | |
| Network access | **Custom allowlist**, package managers **on**. Domains: `datawarehouse.dbd.go.th`, `opendata.dbd.go.th`, `bdex.dbd.go.th`, `data.go.th` | The build sandbox could not reach these hosts, so nothing was verified live. Allow only these, never "all". Add a domain only after reading its terms (`docs/LEGAL.md`). |
| Network secrets | none | Public data, no API keys. If a paid or official API is added later, its key goes here, never in the repo. |
| Setup script | `pip install -e ".[dev]" && make test` | Installs deps and fails fast if the environment is broken. |
| Environment variables | `THFIN_USER_AGENT=thfin-research/0.1 (+mailto:<a monitored team mailbox>)` | Required before `run`. Overrides the YAML value, so no email is committed. |

The same domains must also be allowed by the account or organisation (Settings -> Capabilities,
network access) or the environment cannot use them. Team and Enterprise plans: ask the admin.

## 3. Habits that keep Claude Code reliable here

- Start non-trivial work in **plan mode**; approve the plan before edits.
- `CLAUDE.md` is loaded automatically: keep it short and current (`/handover` refreshes the docs).
- Use the project commands: `/scope`, `/probe`, `/collect`, `/qa`, `/handover`.
- Make Claude run `make test` before every commit; parser changes need a fictional fixture test.
- Review `git diff` of `config/` and `docs/LEGAL.md` yourself; those are the risk surface.
- Never paste real company pages, tokens or emails into prompts that end up committed.

## 4. Prompts

### A. First session (verify against the live sites)

```
Read CLAUDE.md and docs/HANDOVER.md. Goal: verify the four "unverified assumptions" against the live
DBD sites and get a 25-company pilot working. Use plan mode first and show me the plan.

Rules: respect robots.txt and the rate limit, do not add hosts beyond runtime.allowed_hosts, do not
collect personal data, do not commit anything under data/ or .cache/. If a site refuses access or its
terms forbid automation, stop and tell me instead of working around it.

Steps: (1) get the registry CSV into data/registry/ and run `thfin registry-headers`; fix
registry.columns / statuses / tsic format until `plan` shows plausible counts. (2) read the
DataWarehouse+ terms and summarise them for docs/LEGAL.md (I will approve the wording).
(3) `thfin probe` on <one company id I can check in my browser>; if the parser extracts nothing,
find where the numbers load from and fix url_template/parser with a fictional-fixture test.
(4) `thfin run --limit 25`, `status`, `export`, then list anything suspicious. Commit each step
separately on a branch and open a PR.
```

### B. Change the scope

```
/scope add Chonburi and Rayong, TSIC 10 and 46, fiscal year 2567 only, revenue 50-500M baht
```

### C. Run a batch

```
/collect 100
```

### D. Hand over to a teammate

```
/handover
Then summarise for a new teammate in Thai: what works, what is verified live, what I should do first
today, and which decisions are still waiting for the owner.
```
