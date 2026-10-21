# Models: eval results and costs

How the Claude models answer the eval cases (`evals/cases/`), in each way Wipebot is used, with what an answer costs
and how long it takes. Each section is a snapshot of the suite and the judge that day.

## 21 October 2026

The 66 cases of 21 October (62 outside the bot; all of them three times), judged by Sonnet 5.5.
"Plugin" is Claude with the connector and the skill, "connector only" a client with just the connector (as ChatGPT
or claude.ai without the plugin), "bot" the Discord bot. The Opus runs are the baselines (`evals/baseline*.json`).

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

Cost is at each provider's API list price for the answers, without the judge. "Why did we wipe?" is the mean of
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

### List prices used

Per million tokens, input / output, as of 14 October 2026. Anthropic's from its API reference; the others from
the providers' pages as quoted by price aggregators. A cache write costs 1.25x input at Anthropic and plain input
elsewhere.

| Model | Price |
|---|---|
| Fable 5.1 | $10 / $50 (cache read $0.25) |
| Opus 5.5 | $4 / $20 (cache read $0.20) |
| Sonnet 5.5 | $2 / $10 (cache read $0.20) |
| Haiku 5.5 | $0.10 / $0.50 for prompts up to 100K tokens |
| GPT-6 Astra | $10 / $50 |
| GPT-6.1 Sol, GPT-6 Sol | $2 / $10 |
| GPT-6 Luna | $0.10 / $0.50 |
| Gemini 4 Argon | $2 / $10 until 30 November 2026, then $4 / $20 |
| Gemini 3.8 Flash | $0.75 / $3.75 until 31 December 2026 |
| Grok 4.7 | $2 / $6 (cache read $0.50) |
| Kimi K3 | $3 / $15 at Moonshot; other hosts from about $0.56 / $14 |

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
