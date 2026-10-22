---
paths:
  - "evals/**"
---

# Evals

- **Judge a wipe by what ended it** (an enrage, too few players alive for a mechanic, a cascade), not by the killing
  blows. Check `pull_timeline` (`just call`) before writing a case's `expect`.
- A case's `expect` is the main point (missing it fails the answer); `facts` are supporting points (counted).
- Every number in `expect` and `facts` comes from a tool's output. `just evals --lint` replays the calls the case's
  answers made, with today's code, and flags a number none prints now: fix the case, or, for one worked out from the
  output (a gap between two times, ms as m:ss), list it in `derived`. It looks anywhere in the output, so a stale
  time that is also another event's passes: read the case when one of its numbers is flagged.
- Cases are in English. A run reads `evals/cache.sqlite`, frozen: what it lacks is fetched once and kept, so a new
  case's data is fetched on its first run.
- `just evals` asks as Claude with the plugin (the connector and the skill); `--connector-only` as a client with
  only the connector; `--bot` as the Discord bot, the only mode that runs `bot_only` cases. It lists the cases that
  moved against that mode's baseline; run `--trials 3` before believing a difference.
- The cases cover twelve reports and several ways to lose a pull: check a change against the other bosses too, not
  only Sszorak.
- Record what a run costs per model in docs/models.md when it changes the picture.
