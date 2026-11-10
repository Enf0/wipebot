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
flags a number in a case that no tool prints any more, as a snapshot change can leave a case stale. It looks for a
number anywhere in those outputs, so it misses most wrong numbers (seven in ten of slightly changed ones are still
found): a flag is worth reading, a pass proves little. A replayed call that fails now fails it too. A case whose calls
need data the frozen file lacks and Warcraft Logs didn't give is left unchecked and said so, and the lint doesn't
pass; so doesn't a results folder with no run to replay. `just evals --endings` fails below the agreed count of pulls
whose "Ended by" line names the expert's cause first, or when a pull couldn't be read.

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
twice; a case the baseline passed only sometimes, a new or changed one, and every case after the judge changed are
asked three times. Once the total is half a spread below the baseline's, every case gets its third answer, so a run
that looks low is a three-trial run and reads as one. Every answer counts, and the total weighs each case as the
baseline did, however often it was asked. On today's baselines that is 207 of the bot's 279 answers and 190 of the
plugin's 258 (three in four) when nothing looks low, which about two unchanged runs in three do. A baseline asks
every case three times (`--trials 3 --save-baseline`), so `--trials auto` can't save one.

In any run with a baseline, a case that moved beyond noise is asked again, up to six answers, before it is printed:
three answers put an unchanged case there in most runs (`--no-confirm` skips it).

The judge (Sonnet by default, `checks.py`) asks one question for `expect`, one for `wrong` and one to count the
`facts`. A failed check is asked twice more and the majority kept, since the judge's usual error is failing a right
answer on a detail; an undecided judge is asked again. Its recorded score with today's prompts (17 October,
`just evals --agreement`): it failed 27 of 33 answers labelled wrong and passed 92 of 101 labelled right. So a suite
of all-right answers reads about 91%, and a pass rate of 90% sits at the judge's ceiling.

### What a pass means

An answer passes when the judge finds the case's main point in it (`expect`), finds nothing of its `wrong` list
stated, and the conversation made the calls the case requires (`calls`, `asks_first`). Each trial keeps which of these
named checks it passed; a baseline keeps the list it was scored by, and a later run is scored by that list when the
two are compared, so a check added later moves no comparison until the next baseline.

An answer fails, whatever the judge made of its text, when the raider would have met a failure: a tool crashed, the
answer hit the bot's cap of turns or spend, or (in the bot) it took longer than the bot waits. The time a tool call
spent waiting for the next hour's Warcraft Logs points is the eval's own rule, timed apart and left out.

A pass does not mean the answer is right. The judge reads the question and the answer, not what the tools returned:
a wrong number or a made-up cause no case foresaw passes, and about one wrong answer in five passes on the judge's
error alone. Nor is it about form: code adds warnings (numbers no tool gave, a site credited for a fact) that never
fail an answer and are in no headline number, since most of them are false alarms.

An error is only what says nothing about the answer: a usage limit, a judge with no verdict to the end, a service
that was down for a call a failed answer needed, data this machine may not read. Errors are in neither passes nor
trials, a summary and a comparison start with how many there were, and a run with one exits non-zero. A run checks
its Warcraft Logs keys once at its start and stops when they are refused.

Code also measures how every answer is built (`wipebot.discord.form`, no model): its words to read, the verdict's
and each bullet's, the sentences in a bullet, where the change to make stands, its follow-ups. As the bot, an answer
that leaves the form the bot's prompt asks for gets a `form:` line under its verdict (a bullet over 30 words, a
second sentence beside the verdict, the change to make not first, a paragraph after the last bullet), held to what
its case is: an answer in chat, one while the raid is going on, or a post after a wipe. It never fails an answer. In
a client the form is the client's own, so there it's only measured.

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

A summary starts with what could flatter it: the answers that errored, how many asked Warcraft Logs live for data
the frozen file didn't hold, and how many cases the comparison skips and why. Its headline is the answers in which the
main point got across (the passes), with its spread from run to run and the cases right on every trial. Under it, from
the baseline: the totals where the baseline always, sometimes and never passed, and its known failures (cases it
fails more than once in three), so a failure the baseline already had isn't read as news. The `hard` mark in a case
is no longer what splits the total.

A run is compared with its baseline only on the cases unchanged since (each case and the judge carry a hash). The
total weighs each case as the baseline did; the cases the baseline judged the same on every trial and the others are
also totalled apart. A case is listed as moved only when its pass rate moved beyond noise, with how often chance alone
does that. One verdict line ends the comparison:

- **no change shown**, with the smallest drop the run would have shown (a full run: about 4 points). When the total
  is one spread below the baseline's it says "look" and still exits zero: one spread is where one unchanged run in six
  lands.
- **a drop shown**: the total is two spreads below the baseline's, or a case stayed down on six answers. The run
  exits non-zero. On made-up unchanged runs shaped like the baselines this fires about one time in twenty; a real
  drop of 5 points shows in about six runs of ten, one of 10 points nearly always.
- **a rise shown**: two spreads above.
- **cannot tell**: the judge changed since the baseline, more than a tenth of the run's cases aren't compared, errors
  could have made it a drop, or the answers are too few to have shown a drop of 10 points (any `-k` run).

A run and a baseline also keep what was tested: the models that answered and judged by the full names the API gave
(an alias such as `opus` moves to another model without a word), the Agent SDK and Claude Code versions, the effort,
the commit and whether it had uncommitted changes, and when the frozen data was last added to. The comparison warns
when one differs. The date in a question's first line is the recorded data's, like the tools' clock.

A run also compares the supporting facts its answers stated, their tool calls per answer, how often the first call
was one of the case's `tools`, their words and, as the bot, how many leave its prompt's form; the run ends with the
middle answer's words, bullets and verdict and each kind of slip counted. After a change to the bot's prompt, read
those lines against the run before it: a rule about length that the answers don't follow shows there, not in the pass
rate. `just evals --form evals/results/X.json` measures a saved run again, free. Read the transcripts before trusting
a moved number: a fail can be the judge's.

`--save-baseline` prints the comparison with the baseline it replaces and refuses a run that errored, was filtered
or doesn't cover every case as it is today, or is a spread or more below it, unless `--accept "<reason>"` says why;
the reason is kept in the file with what was replaced and the like-for-like total on the unchanged cases.
`--save-baseline evals/results/X.json` makes a saved three-trial run the baseline without asking a model.

A full run (no `-k`, the mode's own model and effort) is kept as its mode's last in `full-runs.json`, which is
tracked: commit it. `just release` prints how many commits that can change an answer and how many days lie since
each mode's last full run, writes that into the release commit, and stops past 30 such commits or 21 days unless
`--without-evals "<reason>"`. A run made where the file can't be committed is recorded after with
`--record evals/results/X.json`.

The judge sees the question and the answer, not what the tools returned, so it can't catch a claim no case foresaw.
`just evals --recheck evals/results/X.json` checks a saved run's answers again, without asking again (about $0.20 an
answer), and keeps what it finds in `results/rechecks/`. Neither check fails an answer or goes into a headline
number (against labelled answers the grounded check flagged 9 of 16 clean ones):

- **Grounded** (Opus): reads the answer's tool calls and outputs, and lists each claim that would mislead a raider
  and that they don't back. A list to read and compare between runs, not a verdict.
- **Rubric**: does the answer open with its main finding, does a recommended change say who does what and when, and
  are the changes few and ordered.

## Commands

```sh
just evals                          # every case once, as Claude with the plugin
just evals -k wipe --trials 3       # some cases, three times each
just evals --bot --trials auto      # the full suite: twice where the baseline was sure, three times elsewhere
just evals --connector-only         # as a client with only the connector
just evals --bot                    # as the Discord bot
just evals --model sonnet           # another model for the answers
just evals --trials 3 --save-baseline             # keep this run as the baseline (--accept "<reason>" if refused)
just evals --save-baseline evals/results/X.json   # a saved three-trial run as the baseline (no model)
just evals --summary evals/results/X.json         # a saved run's summary and comparison again (no model)
just evals --record evals/results/X.json          # a saved full run as its mode's last in full-runs.json (no model)
just evals --lint                   # the cases' numbers against the tools now (no model, two to five minutes)
just evals --coverage               # what the cases cover: tools, kinds of question, places, bosses (no model)
just evals --endings                # pull_summary's "Ended by" against each case's `cause` (no model)
just evals --held-out --trials 3    # the held-out cases alone, at a milestone (never while changing the method)
just evals --recheck evals/results/X.json # a saved run's answers again: claims its tools don't back, and the rubric
just evals --form evals/results/X.json    # how a saved run's answers are built, against the bot's prompt (no model)
just evals --label results/X.json   # mark a run's answers yourself (labels.jsonl)
just evals --agreement              # how often the judge agrees with your marks
just evals --api-key                # bill the run to ANTHROPIC_API_KEY, not your Claude login
```

## Files

- `cases/`: the cases, and in its README their format. `run.py`: running, comparing, labelling. `checks.py`: the
  judge, and the answers' form. `lint.py`: the numbers check. `coverage.py`: what the cases cover (`just check`
  fails when a core tool has no case and isn't in its `NO_CASE_YET`).
- `baseline*.json`: the runs others are compared with, one per mode. `full-runs.json`: each mode's last full run,
  which `just release` measures a release's distance from.
- `test_*.py`: tests of the eval code itself, run by `just check`.
- Untracked: `cache.sqlite` (the frozen data), `results/` (runs), `labels.jsonl` (your marks).
