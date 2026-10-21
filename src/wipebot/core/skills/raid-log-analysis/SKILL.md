---
name: raid-log-analysis
description: Analyses World of Warcraft raid logs from Warcraft Logs with the Wipebot tools, the way an experienced raid leader reads them. Use when someone asks why a pull wiped, how a raid night went, what keeps wiping the raid, how a player did or why their damage or healing is low, or what to change before the next pull.
---

# Analysing raid logs

The Wipebot tools compute the facts from the log and fetch the guides; this is how to turn them into an answer.

## Every answer

- Call several tools for a "why": checking is cheap, a wrong answer isn't. Before judging a
  mechanic, read `boss_guide` with it as `mechanic`: who it targets, whether it can be avoided, what happens when
  it fails.
- Numbers, timestamps and what a class or spec can do come from the tools, never from memory; use a tool's count
  or total rather than counting yourself. Back claims about a pull with timestamps (m:ss from the pull).
- Deaths after the wipe was decided (the boss's enrage, half the raid dead) are fallout, never the cause.
- State facts plainly; name a source only when asked, when sources disagree, or when unsure.
- The log can't show who was assigned to what, where someone was meant to stand, what was said on voice, why
  someone did something, or what it doesn't record. Say so in a sentence, then what it does show. Never present a
  guess or an inference as what happened.
- Who stood where, carried a debuff, had a kick ready or was hit is a fact about a named player; that they caused
  the wipe or failed their job is not, even as a likely guess, unless the raid note or plan shows it was their job
  (raids have reserves and swapped spots). Where the host says blame is wanted, say plainly who
  made the costly mistakes, still from what the log shows.
- Before saying something can't be found or done, try another way: other arguments (a boss's full name, all
  pulls, another report), another tool, `web_lookup` for any page of the guide and news sites (the rest of a cut
  page, a name no tool recognised), `wcl_query` for anything else Warcraft Logs' API holds (rankings by item
  level, a fight's players, events by a filter). Say "can't" only after that, and say what you tried.
- Refer to a player by their character's name, or with a neutral word ("they", Norwegian "de"): the log doesn't
  show anyone's gender, so never "he" or "she".
- "I", "me" and "my" are the person asking: a tool that names the signed-in user's characters, where there is
  one, or their name matched in `roster`. If none matches, ask which character is theirs, unless the host says how
  to find it; never answer with someone else's.
- A pull number that fits several bosses comes back asking which: ask, don't guess. A boss with no pulls in the
  report: say so, and name the bosses it has.
- About one pull, give at most one change for the next pull; about a night or a player, up to three, the most
  important first. A change says who does what and when (a cast, a phase, a time).

## Which question

- Why did a pull wipe, who wiped us, what do we fix: [reading-a-wipe.md](reading-a-wipe.md)
- How did the night go, are we improving, what keeps wiping us: [reviewing-a-night.md](reviewing-a-night.md)
- How did a player do, why is their damage or healing low: [judging-a-player.md](judging-a-player.md)

Anything else (a boss, a spec, loot, parses and rankings, raid prep): the tools' descriptions say which one answers it.
