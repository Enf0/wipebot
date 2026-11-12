---
name: raid-log-analysis
description: How to read World of Warcraft raid logs from the Wipebot tools the way an experienced raid leader does, and how to answer from them. Use when a question needs any Wipebot tool - load it before the first tool call: why a pull wiped, how a raid night went, what keeps wiping the raid, how a player did or why their damage or healing is low, how they compare with top players, whether the raid needs more damage or another healer, or what to change before the next pull, even when the question only names a boss, a pull or a player.
---

# Analysing raid logs

Take these steps in order. Parts are files beside this one, or sections below.

## 1. Know who is asking

"I" and "my" are the person asking, never another player. When the question needs their character and neither a
tool nor `roster` gives it, ask which is theirs, unless the host says how to find it.

## 2. Read the question's part, if it has one, then call its first tool

- A wipe, what killed a player: [Reading a wipe](reading-a-wipe.md), then `pull_summary`.
- A night, what keeps wiping the raid, what to fix first: [Reviewing a night](reviewing-a-night.md), then
  `progress`.
- A player: [Judging a player](judging-a-player.md), then `player_pull`, or `player_night` for a night.
- A plan for the next raid (cooldowns, the raid's make-up, night against night): [Planning](planning.md).
- How a boss works (`boss_guide`), the guild's note or plan, loot: [Explaining a boss](explaining-a-boss.md).

[Worked examples](examples.md).

## 3. Take what code decided from the output

- What ended a pull is what its "Ended by" line says.
- A death marked as fallout, or as after the wipe was decided, is never the cause and nobody's fault.
- Numbers, times and what a class or spec can do come from the tools, never from memory or your own counting.

## 4. Read the guide before judging

- Before judging a mechanic, or a buff the boss kept to the end, read `boss_guide` with it as `mechanic`. A missed
  mechanic brings an enrage as often as a timer.
- State a cause that follows from how a mechanic works: a tank hit that kills another player shows where they stood
  ("the frontal only hits players in front of the boss, so they stood there").
- Fix a death with what the guide says to do (where to stand, what to soak or kick). Name a defensive or healthstone
  after that fix, never in its place, and only when a replay (`death_recap`) says it would have kept them alive.
- Before telling a player how to play their spec, read `spec_guide`.

## 5. Check, then stop

Check a "why" with more than one tool. Stop when the answer's lead and each change rest on a line a tool printed.
Before saying something can't be found or done, try other arguments (all pulls, another report), another tool,
`web_lookup` and `wcl_query`, then say what you tried.

## 6. Write the answer

- Lead with what the log shows decided the question, each claim about a pull with its time (m:ss). A question that
  assumes its answer ("do we need more DPS?") gets yes or no from the log first, then what else is worth fixing.
- Give one pull at most one change, a night or a player at most three, the most important first (for a night,
  what ended the most pulls). A change says who does what and when.
- Leave out what didn't decide the question. Say each fact once.
- Say only what the log shows a named player did (was hit, stood where, was late, had a kick ready). Asked who
  made the mistakes, name them from that. Say they failed their job or caused the wipe only when `raid_note` or
  `raid_plan` shows the job was theirs, never as a likely guess.
- Before saying the log lacks a raider's word ("orb"), check it for each name `boss_guide` (`mechanic`) gives.
  When the answer turns on what it doesn't record (an assignment, who set off what), say so once, with what it
  shows. Never give a guess, or a guide's warning, as what happened.
- Call a player by name or "they", never "he" or "she": the log shows no gender (in Norwegian repeat the name:
  "de" reads as several).

## 7. When the asker pushes back

Recheck each point the log can show with a tool call (a tool's description is not a check), then lead with the
verdict: your mistake, named, or that the claim stands, with its number. Open by agreeing only where a tool proved
you wrong. What no tool checked (their estimates) changes no verdict: answer it as "on your numbers", never as
agreement.
