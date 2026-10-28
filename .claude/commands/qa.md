---
description: Spot-check the collected data for obvious errors
---

Quality-check `data/thfin.sqlite` (or the db in the active config).

1. Run `python -m thfin status` and `python -m thfin export --format csv`.
2. With a short Python script over the exported CSV report: row count, share of rows with NULL
   revenue_total, negative revenue, cost_of_sales greater than revenue, net_margin_pct outside -100..100,
   duplicate (juristic_id, fiscal_year), and the 10 largest revenues (typos/unit errors show up there).
3. Pick 3 random companies and ask the user to compare them with the website.
4. Summarise findings in a short list with counts. Do not edit data to "fix" it; trace problems to the
   parser or to the source.
