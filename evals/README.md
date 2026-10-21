# Evals

The tests check the code; the evals check the answers. A tool can print every number right and the model still
blame the wrong player, miss what ended the pull, or say it can't when it could. An eval asks Wipebot a real
raider's question about a real raid night, end to end with a model, and a second model judges the answer.

## Evals and tests

| | `tests/` | `evals/` |
|---|---|---|
| Checks | the tools: what they compute and print | the answers: what the model makes of the tools |
| Proves | the rules hold (unit tests); each tool's output is what was last reviewed (snapshots) | the model, given the tools, answers raiders' questions well |
| Doesn't prove | that an answer is good, or that today's Warcraft Logs answers the same | that a number is right: a case's facts come from the tools' output |
| Model | none | the bot's (or another), plus a judge |
| Data | recorded, in the repo (`tests/fixtures/`) | fetched once into `evals/cache.sqlite`, never in the repo |
| Runs | every commit (`just check`), seconds, offline | when a change can move answers, minutes, costs money |
| Result | pass or fail | a pass rate, compared with a baseline |

They meet in two places. `just record-tools` records the tool tests' data from the evals' cache, so both read the
same frozen nights. `just evals --lint` replays the tool calls the eval answers made, with today's code, and flags a
number in a case that no tool prints any more: a snapshot changing (`just snapshot`) can leave a case stale. It
checks only the cases some run in `results/` answered, says how many, and fails when none were.

## A case

`cases/` holds them, one file per kind of question (`bot.toml`: the bot's own rules, run only with `--bot`;
`connector.toml`: the connector's own, an officer saving a pasted note, run only without), each on
a report of The Dragonfell Tribe (EU-Kazzak), the maintainers' guild, whose uploader agreed to their use
(`cases/README.md` lists the reports and explains every field):

- `question`: what a raider asks, with what a Discord message tells the bot (who asks, where, which pull).
  A list is a conversation; the last answer is judged.
- `expect`: the main point a right answer gets across. Missing it fails the answer.
- `wrong`: what would make the answer wrong, often the tempting misreading of the log (blaming the deaths after
  an enrage, calling a soak a damage check). Stating any of it fails the answer.
- `facts`: supporting points a full answer states. Counted, never failing.

Every number in a case comes from a tool's output, checked with `just call` against the frozen data.

## A run

`just evals` asks every case as Claude does with the plugin, the way the README sets members up: the connector, and
the raid-log-analysis skill in its prompt; four at a time. `--connector-only` asks as a client with only the
connector (ChatGPT, or Claude without the plugin): no skill, no server instructions. `--bot` asks as the Discord bot
does (its prompt, with the skill), and only it runs the cases marked `bot_only` (the bot's channels). Each mode has
its own baseline (`baseline.json`, `baseline-connector-only.json`, `baseline-bot.json`).

The judge (Sonnet by default, `checks.py`) asks one question for `expect`, one for `wrong` and one to count the
`facts`. A failed check is asked twice more and the majority kept, since the judge's usual error is failing a
right answer on a detail. Code adds warnings that never fail an answer: numbers no tool gave, a site credited for a
fact.

Warcraft Logs and the guide sites come from `cache.sqlite`, frozen: what a run asks that isn't there is fetched once
and kept, so every run on a machine reads the same data, with the clock set to when it was recorded. It holds
rankings and the sites' pages, so it's never in the repo. Runs go to `results/`, with every answer and tool call.
Two ways to get the data:

- **Maintainers**: `just eval-data` fetches the copy the cases were written against (a compressed release asset of
  their private repository, about 360 MB; `gh` signed in to an account that may read it), `--replace` over an older
  one; `just eval-data push` stores this machine's after a run fetched what a new case reads.
- **Anyone else**: the first run fetches what the cases read with your own Warcraft Logs keys, and keeps it. The
  guild's finished reports read the same, but what moves is that day's: the 14 cases with `drifts` (rankings,
  percentiles, the top parses: `compare_with_top`, `player_history`'s ranks, a kill's percentile) and the cases that
  lean on a guide (`boss_guide`, `spec_guide`, `web_lookup`, `hotfixes`: pages are rewritten, patches change
  tuning) can pass or fail on your copy for a reason other than your change. Make your own baseline
  (`--save-baseline`) before a change and compare after.

## Reading a run

A run is compared with its baseline only on the cases unchanged since (each case and the judge carry a hash; a
changed judge gets a warning). A case is listed as moved only when its pass rate moved beyond noise, and the total
comes with its expected spread: one trial can be off by several points, so run `--trials 3` before believing a
difference. Cases marked `hard` (the bot still gets them wrong often) are counted apart, so the total shows a
regression and the hard set an improvement.

## In the usual terms

A case is a task; each answer to it a trial, saved with its transcript (every tool call) in `results/`. The judge
is a model-based grader with a rubric (`expect`, `wrong`, `facts`), and the majority vote on a fail a small
consensus. Its agreement with human marks (`--label`, `--agreement`) is how it's calibrated: read the transcripts of
a run before trusting a moved number, since a fail can be the judge's. The regular cases are regression evals,
expected to pass and watched for slips; the hard set is the capability evals, low pass rates to improve. "Right on
every trial" over `--trials 3` is consistency (pass^k), which matters more for a bot raiders ask once than a lucky
best of three.

## Commands

```sh
just evals                          # every case once, as Claude with the plugin
just evals -k wipe --trials 3       # some cases, three times each
just evals --connector-only         # as a client with only the connector
just evals --bot                    # as the Discord bot
just evals --model sonnet           # another model for the answers
just evals --save-baseline          # keep this run as the baseline
just evals --lint                   # the cases' numbers against the tools now (no model, about a minute)
just eval-data                      # the frozen data the cases were written against (maintainers)
just evals --label results/X.json   # mark a run's answers yourself (labels.jsonl)
just evals --agreement              # how often the judge agrees with your marks
just evals --api-key                # bill the run to ANTHROPIC_API_KEY, not your Claude login
```

A run is on your Claude Code login: it ignores an `ANTHROPIC_API_KEY` in `.env` (the bot's), so it's never billed
to the API by accident; `--api-key` bills it there instead. It says which at the start and keeps it in its results.
A full run is about $14 at API prices and 30 minutes per mode with one trial (about $40 with `--trials 3`), so run what a change needs: the cases that call a
changed tool, or the whole suite for a change to the skill, the bot's prompt or the tool descriptions.

## Files

- `cases/`: the cases, and in its README the cases' format and rules. `run.py`: running, comparing, labelling. `checks.py`: the judge. `lint.py`: the
  numbers check.
- `baseline.json`, `baseline-connector-only.json`, `baseline-bot.json`: the runs others are compared with, one per
  mode.
- `test_*.py`: tests of the eval code itself, run with the rest by `just check`.
- Untracked: `cache.sqlite` (the frozen data), `results/` (runs), `labels.jsonl` (your marks).
