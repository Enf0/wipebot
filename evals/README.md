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
same frozen nights. `just evals --lint` replays the tool calls the saved eval answers made, with today's code, and
flags a number in a case that no tool prints any more, as a snapshot change can leave a case stale.

## A case

`cases/` holds them, one file per kind of question, each on a report of The Dragonfell Tribe (EU-Kazzak), the
maintainers' guild, whose uploader agreed to their use. A case is a `question` (with what a Discord message tells the
bot), the `expect`ed main point (missing it fails the answer), what would make it `wrong` (stating any of it fails
the answer, often the tempting misreading of the log), and supporting `facts` (counted, never failing). Every number
in a case comes from a tool's output. Every field: `cases/README.md`.

## A run

`just evals` asks every case as Claude does with the plugin: the connector, and the raid-log-analysis skill, which
Claude loads itself when a question calls for it; four at a time. The run says in how many answers it loaded the
skill. `--connector-only` asks as a client with only the connector (ChatGPT, or Claude without the plugin): no skill,
no server instructions. `--bot` asks as the Discord bot does, and only it runs the cases marked `bot_only`. Each mode
has its own baseline (`baseline.json`, `baseline-connector-only.json`, `baseline-bot.json`).

`--trials auto` is the full suite's: a case its mode's baseline judged the same on every trial (most of them) is asked
once, and twice more when that answer differs from the baseline's verdict or errors; a case the baseline passed only
sometimes, a new or changed one, and every case after the judge changed are asked three times. A regression on a
steady case still shows as 1 of 3 against the baseline's 3 of 3, for about half the cost of `--trials 3`. A baseline
asks every case as often (`--trials 3 --save-baseline`), so `--trials auto` can't save one.

The judge (Sonnet by default, `checks.py`) asks one question for `expect`, one for `wrong` and one to count the
`facts`. A failed check is asked twice more and the majority kept, since the judge's usual error is failing a right
answer on a detail. Code adds warnings that never fail an answer: numbers no tool gave, a site credited for a fact. A
trial that says nothing about the answer is an error, counted in neither passes nor trials: a crash, a judge with no
verdict, or a failed answer with a call Warcraft Logs or a site didn't answer.

Warcraft Logs and the guide sites come from `cache.sqlite`, frozen: what a run asks that isn't there is fetched once
and kept, with the clock set to when it was recorded. It holds rankings and the sites' pages, so it's never in the
repo. Runs go to `results/`, with every answer and tool call.

Your first run fetches what the cases read with your own Warcraft Logs keys. The guild's finished reports read the
same as the maintainers' copy, but the cases with `drifts` (rankings, percentiles, the top parses) and those that lean
on a guide can pass or fail on yours for a reason other than your change. Make your own baseline (`--save-baseline`)
before a change and compare after.

A run is on your Claude Code login: it ignores an `ANTHROPIC_API_KEY` in `.env` (the bot's), so it's never billed to
the API by accident; `--api-key` bills it there instead. What a run costs per model: [docs/models.md](../docs/models.md).

## Reading a run

A run is compared with its baseline only on the cases unchanged since (each case and the judge carry a hash). A case
is listed as moved only when its pass rate moved beyond noise; one trial can be off by several points, so run
`--trials 3` before believing a difference. Cases marked `hard` are counted apart, so the total shows a regression and
the hard set an improvement. A run also compares the supporting facts its answers stated, their tool calls per answer,
and how often the first call was one of the case's `tools`. Read the transcripts before trusting a moved number: a
fail can be the judge's.

The judge sees the question and the answer, not what the tools returned, so it can't catch a claim no case foresaw.
`just evals --recheck evals/results/X.json` checks a saved run's answers again, without asking again (about $0.20 an
answer), and keeps what it finds in `results/rechecks/`. Neither check fails an answer:

- **Grounded** (Opus): reads the answer's tool calls and outputs, and lists each claim that would mislead a raider
  and that they don't back. A list to read and compare between runs, not a verdict.
- **Rubric**: does the answer open with its main finding, does a recommended change say who does what and when, and
  are the changes few and ordered.

## Commands

```sh
just evals                          # every case once, as Claude with the plugin
just evals -k wipe --trials 3       # some cases, three times each
just evals --bot --trials auto      # the full suite: once where the baseline was sure, three times elsewhere
just evals --connector-only         # as a client with only the connector
just evals --bot                    # as the Discord bot
just evals --model sonnet           # another model for the answers
just evals --save-baseline          # keep this run as the baseline
just evals --lint                   # the cases' numbers against the tools now (no model, about a minute)
just evals --coverage               # what the cases cover: tools, kinds of question, places, bosses (no model)
just evals --endings                # pull_summary's "Ended by" against each case's `cause` (no model)
just evals --held-out --trials 3    # the held-out cases alone, at a milestone (never while changing the method)
just evals --recheck evals/results/X.json # a saved run's answers again: claims its tools don't back, and the rubric
just evals --label results/X.json   # mark a run's answers yourself (labels.jsonl)
just evals --agreement              # how often the judge agrees with your marks
just evals --api-key                # bill the run to ANTHROPIC_API_KEY, not your Claude login
```

## Files

- `cases/`: the cases, and in its README their format. `run.py`: running, comparing, labelling. `checks.py`: the
  judge. `lint.py`: the numbers check. `coverage.py`: what the cases cover (`just check` fails when a core tool has
  no case and isn't in its `NO_CASE_YET`).
- `baseline*.json`: the runs others are compared with, one per mode.
- `test_*.py`: tests of the eval code itself, run by `just check`.
- Untracked: `cache.sqlite` (the frozen data), `results/` (runs), `labels.jsonl` (your marks).
