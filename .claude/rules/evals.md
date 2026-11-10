---
paths:
  - "evals/**"
---

# Writing eval cases

- **Judge a wipe by what ended it** (an enrage, too few players alive for a mechanic, a cascade), not by the killing
  blows. Check `pull_timeline` (`just call`) before writing a case's `expect`.
- Every number in `expect` and `facts` comes from a tool's output. `just evals --lint` flags a number no tool prints
  now: fix the case, or, for one worked out from the output (a gap between two times, ms as m:ss), list it in
  `derived` with the arithmetic in a comment. It looks anywhere in the output, so a stale time that is also another
  event's passes: read the case when one of its numbers is flagged. It reads a time before a colon ("5:27: 5
  before") and none inside a longer clock (1:05 in 1:05:27). A case it leaves unchecked for lack of data (a call that
  fails for it, or an output with a section "Not available") is not verified: its numbers stand until a machine with
  Warcraft Logs keys runs the lint.
- A wipe case may name its `cause`; run `just evals --endings` (no model) after a change to how pulls end. It fails
  below the agreed count (`AGREED` in evals/endings.py) or when a pull couldn't be read. When a cause moves, check it
  against pull_summary's output before changing the case's `cause` or the count.
- Run `--trials 3` before believing a moved case, and record what a run costs per model in docs/models.md when it
  changes the picture.
