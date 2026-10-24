---
paths:
  - "evals/**"
---

# Writing eval cases

- **Judge a wipe by what ended it** (an enrage, too few players alive for a mechanic, a cascade), not by the killing
  blows. Check `pull_timeline` (`just call`) before writing a case's `expect`.
- Every number in `expect` and `facts` comes from a tool's output. `just evals --lint` flags a number no tool prints
  now: fix the case, or, for one worked out from the output (a gap between two times, ms as m:ss), list it in
  `derived`. It looks anywhere in the output, so a stale time that is also another event's passes: read the case when
  one of its numbers is flagged.
- A wipe case may name its `cause`; run `just evals --endings` (no model) after a change to how pulls end.
- Run `--trials 3` before believing a moved case, and record what a run costs per model in docs/models.md when it
  changes the picture.
