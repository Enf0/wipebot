---
paths:
  - "tests/**"
  - "evals/**"
---

# Only what we may publish

Why: the RPGLogs API terms (§5d) and the guide sites' terms; tests/README.md, "Where the data comes from".

- Real Warcraft Logs data in a tracked file comes only from reports of the maintainers' guild (The Dragonfell Tribe,
  EU-Kazzak), whose uploader agreed and whose raw responses Warcraft Logs' developers allowed.
- Never tracked as data (recordings, snapshots, fixtures): Warcraft Logs' rankings, percentiles, character pages,
  other guilds' reports, and Raidbots' data files. A tool case reading them is `local = true` (recording refuses one
  that isn't); a test needing such data makes it up. Eval cases and docs may cite what a tool computes from them about
  our raiders (a percentile, a gap to the top), never another guild's players or reports.
- The guide sites' writing and Warcraft Logs' images get a stand-in (`tests/support/standins.py`); game facts (spell
  ids, cooldowns, talent trees) may stay.
- Eval cases are on that guild's reports, in English; their data stays in the untracked `evals/cache.sqlite`.
