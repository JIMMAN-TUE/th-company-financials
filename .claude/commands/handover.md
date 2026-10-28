---
description: Refresh docs/HANDOVER.md with the current state before passing work to the team
---

Update `docs/HANDOVER.md` so a teammate can continue without asking anyone:

1. Run `make test` and `git log --oneline -15`; update the State table (done / not verified).
2. Move anything that was verified against the live site out of "First steps" and into State.
3. Record new decisions and open questions; keep the Thai summary at the top in sync.
4. Do not include collected data, secrets or personal contact details.
