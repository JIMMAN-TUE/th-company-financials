---
description: Probe one company page and fix the parser/URL if nothing is extracted
argument-hint: <13-digit juristic id>
---

Probe company $ARGUMENTS.

1. Run `python -m thfin probe $ARGUMENTS`. Needs a real `runtime.user_agent` contact and network access
   to the allowed host; if it cannot connect, say so and stop (do not try other routes).
2. If years and fields are printed, show them and ask the user to compare with the website.
3. If nothing was extracted, read `.cache/probe/$ARGUMENTS.html`: count tables, search for the revenue
   label (รายได้รวม) and for script/JSON endpoints. Propose the fix: a different `financials.url_template`,
   a JSON endpoint, or new patterns in `FIELD_PATTERNS`.
4. For any parser change, add a test in `tests/test_parsers.py` with a fictional fixture (never commit
   real company pages), then run `make test`.
