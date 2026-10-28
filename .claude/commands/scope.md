---
description: Change the collection scope safely (add/remove provinces, industries, years, size bands)
argument-hint: e.g. "add Chonburi and Rayong, only TSIC 10, 2567 only"
---

Change the scope as requested: $ARGUMENTS

1. Read `config/scope.yaml` (and the preset file if the user named one).
2. Make the smallest YAML edit that does what was asked. Quote 13-digit IDs. Years are Buddhist Era.
   Do not touch `runtime.*` or `allowed_hosts`.
3. Run `python -m thfin plan` (add `--config` if a preset is in use).
4. Report in 3-5 lines: what changed, matched in registry, selected after the cap, estimated hours, and
   any WARNING from the plan. If the cap truncates the scope, say so and ask before raising it.
5. Do NOT run `thfin run` in this command.
