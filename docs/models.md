# Models: eval results and costs

How the Claude models answer the eval cases (`evals/cases/`), in each way Wipebot is used, with what an answer costs
and how long it takes. Each section is a snapshot of the suite and the judge that day.

## 11 November 2026

The 104 bot cases of 11 November, judged by Sonnet, run in the bot (medium effort) on the code before 1.0.0. Opus's
three trials (commit 69b21e9) are the bot's baseline; Sonnet ran three trials of the same commit (one asked for, made
three by a fault in the runner since fixed) and Haiku one (e996bab, the same answers' code). The split skill (a core
of seven steps with a part for each kind of question, on commit e3d450a) ran on all three models before it went in.

| Model | Skill | Passed | Cases right every trial | Median s | 90% within s | Slowest s | Tool calls | Per answer, judge included | One run |
|---|---|---|---|---|---|---|---|---|---|
| Opus | one file | 273/312 (88%) | 80/104 | 29 | 46 | 134 | 4.1 | $0.18 | $55.88 (3 trials) |
| Sonnet | one file | 222/312 (71%) | 60/104 | 23 | 32 | 74 | 2.5 | $0.09 | $27.85 (3 trials) |
| Haiku | one file | 68/104 (65%) | 68/104 | 25 | 41 | 83 | 3.4 | $0.05 | $4.82 (1 trial) |
| Opus | split | 198/229 (86%) | 84/104 | 26 | 43 | 124 | 4.2 | $0.18 | $41.29 (auto) |
| Sonnet | split | 77/104 (74%) | 77/104 | 23 | 33 | 79 | 2.5 | $0.09 | $9.67 (1 trial) |
| Haiku | split | 73/104 (70%) | 73/104 | 19 | 40 | 77 | 3.5 | $0.05 | $4.80 (1 trial) |

- On their first trial alone, Opus passed 95 of the 104 cases, Sonnet 72 and Haiku 68. Opus stays the bot's model.
- Sonnet's answers are longer (a median of 210 words against Opus's 173) and built on fewer tool calls: on the 20
  cases where it fell two or more answers behind it made 3.1 calls an answer against Opus's 5.0, most often leaving
  out `pull_timeline`, and answered from the first tool's summary. Most of its misses are the main point, not a wrong
  claim (78 failed on the point against Opus's 23 of 312).
- The split skill is no worse on any model: Opus 276 of 312 counted as the baseline counts (+3, within the ±5.4
  expected from run to run), Sonnet 77 against the 74 its one-file rates predict, Haiku 73 against 68. It went in
  for the size it saves, not for a gain the runs show. Neither skill makes Sonnet call more tools.
- Haiku costs half what Sonnet does per answer and passes about as many on one trial; neither is close to Opus.

## 29 October 2026

The 91 cases of 29 October in the bot and the 84 with the plugin, all three times, judged by Sonnet (the bot's run on
commit 0bcd623, the plugin's on 172edb8). These runs are the baselines for the bot and the plugin; the connector-only
baseline is still the run of 24 October below.

| Model | Run as | Passed | Cases right every trial | Median s | 90% within s | Slowest s | Per answer, judge included | One run |
|---|---|---|---|---|---|---|---|---|
| Opus | bot (medium effort) | 246/273 (90%) | 71/91 | 29 | 49 | 123 | $0.16 | $43.79 |
| Opus | plugin | 229/252 (91%) | 67/84 | 40 | 66 | 134 | $0.27 | $67.08 |

- The totals count every case, the hard set of that day too (8 cases in the bot, 22/24; 7 with the plugin, 19/21).
  Without it: 224/249 and 210/231 answers, 65/83 and 62/77 cases right on every trial.
- Both are lower than the runs they replaced, like for like: the bot's of 28 October (commit 898a940, 247/270 on 90
  cases, with no section here) and the plugin's of 24 October below. On the cases unchanged between the two baselines
  the bot passed 185 of 207 answers against 194 (69 cases: -9, 1.8 times the ±5.0 expected from run to run) and the
  plugin 161 of 174 against 168 (58 cases: -7, 1.9 times ±3.7). That is short of a drop shown (two spreads) and worth
  a look both times, and nothing recorded it when the baselines were saved. What moved them isn't known: between the
  baselines 18 and 15 cases changed and 4 and 11 were new (3 left the bot's), and the runs weren't repeated. A
  baseline saved over such a run now needs a reason (`--accept`, evals/README.md), kept in its file with this
  like-for-like total.
- A pass rate near 90% is at the judge's ceiling. Its recorded score with today's prompts (17 October,
  `just evals --agreement`): it passed 92 of 101 answers labelled right and failed 27 of 33 labelled wrong. A suite of
  all-right answers would read about 91%, so these totals say little about how many answers are wrong; the cases
  right on every trial and the comparison with the baseline say more.
- 21 of the bot's 273 answers and 49 of the plugin's 252 asked Warcraft Logs live, for data the frozen file didn't
  hold then.
- Which model the `opus` alias was that day, and the Agent SDK and Claude Code versions, were not recorded. Runs keep
  them since 10 November, and a comparison warns when they differ from the baseline's.

## 24 October 2026

The 77 cases of 24 October (73 outside the bot; all three times, 8 in the hard set), judged by Sonnet 5.5, on the
code before that day's tank-swap fix. The plugin's run was its baseline until 29 October and the connector-only one
still is; the bot's was replaced before then (its baseline of 28 October is named above).

| Model | Run as | Passed | Cases right every trial | Median s | Slowest s | Per answer, judge included | One run |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | plugin | 209/219 (95%) | 66/73 | 36 | 162 | $0.30 | $65.72 |
| Opus 5.5 | bot (medium effort) | 214/231 (93%) | 65/77 | 25 | 81 | $0.21 | $47.39 |
| Opus 5.5 | connector only | 201/219 (92%) | 61/73 | 30 | 95 | $0.22 | $47.77 |

On the cases both days have, each mode passed 5 or 6 more answers than on 22 October, beyond the noise. The bot's
row is the medium-effort reference that other efforts and models are compared with on the same cases.

Effort in the bot, the same cases on 0.5.3 (the medium row is the run above; its tank cases rerun on 0.5.3 passed
23/24 against 22):

| Model | Effort | Passed | Cases right every trial | Median s | 90% within s | Slowest s | Tool calls | Per answer | One run |
|---|---|---|---|---|---|---|---|---|---|
| Opus 5.5 | low | 201/228 (88%) | 58/76 | 15 | 23 | 35 | 2.2 | $0.14 | $31.47 |
| Opus 5.5 | medium | 214/231 (93%) | 65/77 | 25 | 45 | 81 | 4.6 | $0.21 | $47.39 |
| Opus 5.5 | high | 212/228 (93%) | 63/76 | 33 | 62 | 195 | 6.1 | $0.25 | $57.39 |
| Sonnet 5.5 | high | 185/227 (81%) | 51/76 | 17 | 38 | 109 | 4.9 | $0.15 | $34.04 |

- Medium stays the bot's effort. High passes no more (-2, within noise) for a third more time and a slower tail.
- Low is twice as fast, but 13 answers behind, beyond the noise: it calls half the tools, and the night summaries,
  a healer's night and follow-ups suffer most.
- Sonnet at high is 28 answers behind Opus at medium for a little over half the price: not the bot's model.
- By kind of question, Sonnet keeps up only on what the log can't tell (18/18); on wipes it is 12 behind (41/53
  against 53/54). Picking a model per question would take a step guessing the kind first, for no case it wins.

The posts after a wipe (`posts.toml`, 9 cases, three times), the one kind of answer the bot can give another model
without guessing (it asks for them itself):

| Model | Passed | Median s | Slowest s | Per answer |
|---|---|---|---|---|
| Opus 5.5 (medium) | 26/27 | 19 | 36 | $0.15 |
| Sonnet 5.5 | 11/27 | 13 | 22 | $0.09 |
| Haiku 5.5 | 14/27 | 15 | 21 | $0.06 |

Sonnet and Haiku miss the point the post exists for (8 and 10 of 27): a Water Jet death they turn into "press a
defensive" instead of standing out of the tank's line. Opus stays for posts too.

With the post's first tool run before the model is asked (`pull_summary`, or `player_pull` for a raider's own
feedback): 25/27 against 26/27 (within noise), median 14 s against 19, slowest 23 s against 36, $0.12 a post against
$0.15. The one case that moved, the unkicked add, passed when an answer also read the enemies' casts, which the
prefetched answers called less. With the guides on the mechanic that ended the pull (or killed the player) run up
front too, when code names one: 25/27, median 12 s, slowest 21 s, $0.10 a post, 2.1 model rounds against 3.

Two lines in the bot's prompt (independent tools in one turn, nothing written before the answer), on the 10 cases
with the most tool rounds: 26/30 against 28, 4.9 rounds an answer against 5.1, 40 s against 45, same cost. Not
worth a full run.

- With the connector alone, a case needing the spec's guide read first failed on that step only: the step is the
  skill's, so a case's required calls now count only where the skill is.
- The grounded recheck of the plugin run (Opus, $51) found a claim the tool outputs don't back in 174 of 219
  answers, 398 in all, most often a cause the outputs don't show ("that run of deaths is what lost it"), then fixes
  ranked with nothing ranking them, assignments the log doesn't hold, and dropped hedges ("if talented").

## 22 October 2026

The 70 cases of 22 October (all three times; 8 of them in the hard set, the questions a raid leader asks beyond
diagnosis: ranking problems, false premises, a cooldown plan, week over week), judged by Sonnet 5.5. The plugin mode
now loads the skill the way Claude does, when a question calls for it, so its rows aren't comparable with 21
October's, which had the whole skill in the prompt.

| Model | Run as | Passed | Cases right every trial | Median s | Per answer, judge included | One run |
|---|---|---|---|---|---|---|
| Opus 5.5 | plugin | 195/210 (93%) | 60/70 | 32 | $0.30 | $63.85 |
| Opus 5.5 | bot | 202/222 (91%) | 59/74 | 21 | $0.20 | $44.59 |
| Opus 5.5 | connector only | 188/210 (90%) | 52/70 | 27 | $0.21 | $45.14 |
| Haiku 5.5 | plugin | 138/209 (66%) | 31/70 | 19 | $0.08 | $15.98 |

Without the hard set, Opus passes 94% with the plugin, 93% in the bot and 92% with the connector alone (87% on 21
October): what code can decide of the method is now in the tools' output, which a client without the skill reads too.

- Opus 5.5 stays the bot's model and the one to recommend in Claude.
- Haiku loaded the skill in only 60 of its 209 answers here, so most of them had no method: its 77% of 21 October was
  with the skill pasted into the prompt, which members never get. The skill's description now has Claude load it
  before the first tool call; on 12 cases afterwards Haiku loaded it in all 12 answers and passed 10 (one trial each),
  so expect its plugin number to rise; a full run is due with the next model comparison.
- The plugin's answers cost more than the others' ($0.30): Claude reads the skill and calls more tools (6.3 an answer,
  against 4.8 with the connector alone).
- The held-out cases, written blind on a night nothing in the skill or the other cases was built on (21 October), all
  passed with Opus and the plugin: 9 of 9. The method holds on a night it wasn't fitted to.

## 21 October 2026

The 66 cases of 21 October (62 outside the bot; all of them three times), judged by Sonnet 5.5.
"Plugin" is Claude with the connector and the skill, "connector only" a client with just the connector (as ChatGPT
or claude.ai without the plugin), "bot" the Discord bot. The Opus runs are the baselines (`evals/baseline*.json`).
In these runs "plugin" had the whole skill in its prompt; from 22 October the plugin mode loads the skill as Claude
does, when a question calls for it, so later plugin rows aren't comparable with these.

| Model | Run as | Passed | Cases right every trial | Median s | Per answer, judge included | One run |
|---|---|---|---|---|---|---|
| Opus 5.5 | bot | 188/198 (95%) | 57/66 | 23 | $0.20 | $39.40 |
| Opus 5.5 | plugin | 175/186 (94%) | 54/62 | 32 | $0.24 | $44.62 |
| Opus 5.5 | connector only | 162/186 (87%) | 46/62 | 28 | $0.20 | $37.21 |
| Sonnet 5.5 | plugin | 143/186 (77%) | 40/62 | 16 | $0.13 | $24.41 |
| Haiku 5.5 | plugin | 143/185 (77%) | 39/62 | 22 | $0.07 | $13.45 |
| Sonnet 5.5 | bot | 132/197 (67%) | 34/66 | 14 | $0.12 | $24.04 |
| Haiku 5.5 | bot | 123/198 (62%) | 26/66 | 18 | $0.07 | $13.73 |

"Passed" counts every answer; an answer that errored (one each for Sonnet in the bot and Haiku with the plugin) isn't
counted. The judge costs about $0.06 an answer: a Haiku answer itself costs under a cent, so an Opus answer is about
$0.14-0.18 and a Sonnet one about $0.07.

- Opus 5.5 stays the bot's model and the one to recommend in Claude: 94-95% with the method (the bot's prompt or the
  plugin's skill), well ahead of the others.
- The skill is worth having: Opus passes 7 points more with the plugin than with the connector alone.
- With the plugin in Claude, Haiku 5.5 answers as well as Sonnet 5.5 (77%) at half the cost, so a member on a
  smaller plan loses less than the model's name suggests. In the bot both fall further behind (62-67%).

## 14 October 2026

A snapshot: the eval cases of 14 October 2026 (48 of them, judged by Sonnet 5.5 alone) answered by each model,
Claude's and others'. The suite and the judge have changed since, so read the numbers as how the models compared
that day. Checked against the real answers, not the model's own claims.

### How it was run

- "Bot" rows ran in the bot's own session (`just evals --model ...`); "MCP" rows in an agent client with Wipebot as
  an MCP server, with the client's own prompt around ours. It costs Sonnet 5.5 a lot (69% in the bot, 50% over MCP)
  and Opus 5.5 nothing (86% and 85%).
- 48 cases: `wipe-orb-explosion` is left out, its report went private on 13 October.
- Three trials for the models a decision rests on, one for the rest. A single trial can be off by several points:
  Gemini 4 Argon passed 79% in one and 72% over three. Read one-trial rows as within about 5 points.

### Results

Cost is at each provider's API list price of that day for the answers, without the judge. "Why did we wipe?" is the mean of
the single-question `wipe-*` cases; "why is my DPS low?" is `me-low-dps`, which compares the player with top logs.

| Model | Run in | Trials | Passed | Median s | One "why did we wipe?" | One "why is my DPS low?" | One run (48 cases) |
|---|---|---|---|---|---|---|---|
| Opus 5.5 | bot | 3 | 124/144 (86%) | 19 | $0.21 | $0.15 | $7.51 |
| Fable 5.1 | bot | 1 | 40/48 (83%) | 34 | $0.85 | $0.61 | $28.22 |
| Opus 5.5 | MCP | 1 | 41/48 (85%) | 20 | $0.19 | $0.16 | $7.98 |
| Grok 4.7 | MCP | 1 | 38/48 (79%) | 130 | $0.69 | $0.31 | $20.16 |
| GPT-6 Astra | MCP | 1 | 35/48 (73%) | 23 | $0.30 | $0.36 | $13.27 |
| GPT-6.1 Sol | MCP | 3 | 105/144 (73%) | 25 | $0.06 | $0.07 | $2.67 |
| Gemini 4 Argon | MCP | 3 | 104/144 (72%) | 22 | $0.10 | $0.08 | $4.12 |
| GPT-6 Sol | MCP | 1 | 34/48 (71%) | 17 | $0.07 | $0.07 | $2.86 |
| Sonnet 5.5 | bot | 3 | 100/144 (69%) | 12 | $0.08 | $0.07 | $2.66 |
| Gemini 3.8 Flash | MCP | 1 | 32/48 (67%) | 41 | $0.08 | $0.02 | $2.61 |
| Haiku 5.5 | bot | 3 | 91/144 (63%) | 19 | $0.008 | $0.008 | $0.28 |
| Kimi K3 | MCP | 1 | 29/48 (60%) | 65 | $0.26 | $0.17 | $10.23 |
| Sonnet 5.5 | MCP | 1 | 24/48 (50%) | 13 | $0.08 | $0.05 | $3.41 |
| GPT-6 Luna | MCP | 1 | 23/48 (48%) | 27 | $0.003 | $0.003 | $0.15 |

### What it means

- Opus 5.5 stays the bot's model: the best, and the three trials hold it up.
- Fable 5.1 answers no better than Opus at about four times the price and twice the time.
- Opus 5.5 answers as well over MCP as in the bot; Sonnet 5.5 loses 19 points over MCP.
- Outside Claude, GPT-6.1 Sol and Gemini 4 Argon over three trials both pass about 72%, near Sonnet in the bot,
  at about $0.06 and $0.10 a question. Grok 4.7 matched Argon in one trial but takes two minutes an answer and
  costs seven times as much.
- Kimi K3 passes less than Haiku at thirty times its price.
- Haiku 5.5 answers under a cent a question at 63%: a server paying its own key can leave it on.
- GPT-6 Luna is as cheap as Haiku but passes 15 points less.

### Speed (14 October 2026)

- Asking the bot to call its tools together, in one go, changed nothing: one trial each of the 49 cases at the same
  commit, with and without the line, gave 3.35 and 3.29 rounds of tool calls per answer, 1.26 and 1.33 calls per
  round, a median answer of 16.7 and 16.6 s, and 44 and 43 passed. Answers start from one tool (`pull_summary`
  most often) and choose the next from what it says. Each run cost $10.56 and $11.23 on Opus 5.5.
- Where a bot answer's time goes (12-14 October): a median of 28 s, about 5 s of it in tools and 18 s in the model
  (27 answers with timed tool calls); the model writes about 1,060 tokens an answer for about 300 of text (318
  answers). A tool on cached data answers in under a second; the slow ones are first fetches
  ([reference/wcl-api.md](reference/wcl-api.md), "Using the API efficiently").
