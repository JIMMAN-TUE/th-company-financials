---
description: Run a collection batch for the current scope, then export
argument-hint: optional batch size (default 25)
---

Collect a batch. Batch size: ${ARGUMENTS:-25}.

1. Run `python -m thfin plan` and show the numbers. Stop and ask if the plan has a WARNING or the
   estimate is over 1 hour.
2. Confirm the first run of a new scope with the user. Then run `python -m thfin run --limit <N>`.
3. Run `python -m thfin status`. Report ok / not_found / parse_empty / error counts. If `parse_empty`
   is more than a few percent, stop and switch to `/probe` instead of continuing.
4. If the run stopped with a robots.txt, host or consecutive-error message, report it verbatim and stop.
   Never lower the rate limit or disable robots checks.
5. When the user is happy, run `python -m thfin export` and give the file paths.
