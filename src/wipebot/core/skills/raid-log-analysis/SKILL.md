---
name: raid-log-analysis
description: How to read World of Warcraft raid logs from the Wipebot tools the way an experienced raid leader does, and how to answer from them. Use when a question needs any Wipebot tool - load it before the first tool call: why a pull wiped, how a raid night went, what keeps wiping the raid, how a player did or why their damage or healing is low, how they compare with top players, whether the raid needs more damage or another healer, or what to change before the next pull, even when the question only names a boss, a pull or a player.
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
- A question that assumes its answer ("do we need more DPS?", "the healers lost us that, right?") gets the
  assumption checked first: open with what the log shows lost it, yes or no, and only then what else is worth
  fixing. A second problem never leads because the question named it.
- About one pull, give at most one change for the next pull; about a night or a player, up to three, the most
  important first. A change says who does what and when (a cast, a phase, a time).
- Keep to what decided the question. A cooldown, mechanic or player that didn't is left out, however interesting,
  or offered in a line; so is the rest of a big question (a whole fight, every raider's job) after the part asked
  for or the first one. Say each fact once.

## Reading a wipe

First find what ended the pull, then why. Killing blows tell how players died, not why the pull was lost.
`pull_summary` opens with how the pull ended as the log shows it ("Ended by"; the guide says what each mechanic is),
then every death with the players alive after it, the buffs the boss kept to the end, and when the wipe was decided;
`pull_timeline` gives the order of events, `boss_guide` how each mechanic works. Pulls end in a few ways:

- A reset: the boss untouched. Say so in a line.
- A timer: the boss gained a buff it kept to the end, and the guide calls it an enrage or berserk (or a cast the
  guide says the boss must die before). Every death after it is fallout. Find why it came: the boss didn't die in
  time (`compare_pulls` against a kill shows where the raid fell behind a kill's pace; early deaths cost damage
  too), or a mechanic that holds it off (a stack, a soak, a kick, an energy reset) was late, early, missed or done
  by too few: check it each time it was due, against the boss's power and a kill's timing. A buff gained as
  another boss died comes with that kill, not a timer.
- A mechanic that needs players or preparation: a soak or stack with fewer players alive than it needs (the deaths
  before it lost the pull), a raid-wide hit the guide says to cooldown for with deaths right after it and no healing
  cooldown before it (the fix is the cooldown plan; but when a mechanic the raid missed caused the hit or made it
  bigger, that mechanic is the fix: cooldowns are assigned in a plan the log can't show), a stacking debuff at its
  lethal cap (the cause is where the extra stacks came from).
- A raid-wiping mechanic: many dying to one ability in the same second or two, or in pulses over a few seconds.
  Find which mechanic failed and how: who carried it, who soaked it (who should have, only the raid note says),
  what was left alive.
- A cascade: deaths one after another, each making the next more likely.

"Ended by" names the deaths one after another that led into a moment killing many ("Before it, 5 deaths ... from
0:46"), counts one ability killing in pulses over a few seconds as one moment, and starts a cascade at the first death
that stayed (a battle-ressed player came back). After a wipe no enrage buff decided, a "Not an enrage buff" line says
deaths, not a damage check, lost it, unless the guide names an enrage that is a cast.

Then work back to the first domino; a wipe at 2:40 is often decided at 0:48. Take the first one to three deaths
before the wipe was decided and `death_recap` each: a one-shot, a slow bleed healing didn't cover, or an earlier hit
that made a later one lethal; it replays what was ready to say whether it would have saved them. A tank's death:
`tank_swaps` and the guide's swap timing. Follow the cascade: a dead player leaves a soak, a stack or a role short.
With a raid note, `raid_note` shows who was meant to soak and which cooldowns were planned when. For where people
stood when a mechanic went off, `pull_map` with the ability in `at` shows each time: who had it, and whose burst or
area hit whom. With a raid plan (positions, from raidstrats.gg), `raid_plan` also shows who stood away from their
spot. A player off their spot in every pull of a stretch, at another player's spot, may
mean the plan changed and wasn't saved again: say so rather than calling it a mistake. A spot the plan's notes say
moves (a player who runs out to bait) isn't a mistake either. Adds and their casts: `enemies`, which also says who
had a kick ready when a cast went through.

The killing blow can mislead: during an enrage anything kills; for "no killing blow recorded", `death_recap` shows
the damage before it and whether they were thrown off; another player's damage-amplifying buff named as the killing
blow means other players killed them (mind control, a curse that turns them); a tank mechanic that kills a non-tank
is positioning, not healing. A guide's warning is something to check, not a cause.

## Reviewing a night

Look for patterns, not incidents. The same ending on many pulls is the one thing to fix: say how many pulls it
ended, and when. `progress` counts the pulls by the first thing that ended each ("How the pulls ended"): the biggest
count is usually that thing. It also names the night's killer, which counts deaths, not pulls lost, so it can be
another ability: its deaths, how often it was the first, whom it killed and who took most of its hits. Who keeps
dying first, to what, and whether that mechanic hits everyone or targets one player or role: the same player dying
to a raid-wide mechanic again and again points at them; one aimed at a role may be an assignment, so check who was
hit by role (`mechanic_hits`). `progress` without a boss opens with every boss of the night and covers the latest; a
night with several bosses in progress takes one call per boss.

A question about the whole night ("who missed their soak most?", "who pressed personals when told?", "who keeps
standing off their spot?") is one call with `all_pulls` (`raid_note`, `raid_plan`, `pull_map`, `mechanic_hits`), not
one per pull: those count per player over every pull on the boss.

Improving or not: the best and average wipes early against late, and how far pulls got. A fight is a damage check
only when an enrage or a damage race ends it; when mechanics end the pulls, say so (`compare_pulls` against a kill
shows whether the damage would do once they're fixed). A death inside a window where the boss takes extra damage
costs double. On a kill, what went right is what differs from the best wipe (`compare_pulls` with `other`).

A summary for the raid leads with the log's own finding, then the guide's rule for it, never the guide's general
advice alone.

## Judging a player

Answer the question they asked, and lead with what cost them the most. A death before the wipe was decided usually
does (nothing is done after it), so it often comes first; but "why is my damage low" is about their damage while
alive, and a small cost never leads just because of its kind. `player_pull`'s "Time lost" line puts a death, casting
gaps and a capped resource in one unit (seconds before the wipe was decided) to weigh them by. About a night, cover
every boss they pulled, kills first: a kill is the best evidence, a progression wipe the weakest. Across nights,
`player_history` with deaths shows what they keep dying to.

How to play a spec comes from its guide, not from memory. Before telling a player to change how they play it (what
to cast or spend and when, procs, targets, positioning, cooldowns), read `spec_guide` for their spec on the rotation,
once a conversation (another topic when the advice is about it), and give no advice the guide contradicts. Give its
rule as a fix's reason, plainly ("spend Holy Power before it caps"), and when it has one for what the log shows (a
spender that overheals, a resource that caps), give it. A fix added to an answer about a count needs the guide too.

- Deaths: only those before the wipe was decided count, and how often they were among the first. Everyone dies on
  a wipe.
- Mechanics: damage from mechanics others avoided (`mechanic_hits` without an ability; `breakdown` of damage
  taken by ability, until the wipe was decided), judged against how the mechanic works (`boss_guide`); a soaker or
  a targeted player takes more by design.
- Throughput against the right reference: on a kill, the parse. On a wipe, where their output per second alive
  falls among the spec's ranked kills (below the 25th percentile is a real gap; above it proves little, as short
  pulls run high), and their own best pull. Only against the same spec, never by totals across pulls. A target
  is a point of the spread (the 75th percentile, say), never the #1 parse; a healer's top parse is read with its
  raid's healers (how many, their share) before it's held up as a level to reach.
- Build and prep (`compare_with_top`, build): a gap that changes output (talents nearly every top player and the
  guides take, an AoE build on a single-target boss), checked against the spec guide's build for that kind of
  fight. One utility talent for another is a choice; a different hero tree only when asked. A missing consumable
  is a concrete find.
- How they play (`compare_with_top`, casts and cooldowns): waste first (a resource at its cap, charges left full,
  a buff or DoT falling off, a cooldown held for nothing), then core casts and cooldown timing against top players
  of the same spec and hero tree. The same cast dropped again and again is a habit worth a sentence.

A difference from top players is a question until you've ruled out the plain reasons: a talent choice, a spell
logged under another id, a cooldown held for Bloodlust or adds, a cast before the pull, a short pull, or the fight
itself (a gap at a mechanic or a death is the fight; one with nothing going on is time to win back). When the player
answers that something was the fight's doing (the mechanics, movement, their job), check before you agree or hold to
it: top players of their spec meet the same fight, and `compare_with_top` casts shows how much of it they have too
(time at the resource cap, casting gaps, the movement spells they use to get back). Say which part the fight
explains and which is left to win back. A count means
little without the moment it answered: defensives and active mitigation are judged around a death or a big hit,
cooldowns by their timing, never by uses against possible uses (one held for a spike, Bloodlust or adds is right
even when it costs a use).

- Healers: raid cooldowns against the damage spikes (`healing`; a spike that comes back pull after pull with no
  cooldown is a plan to make), mana over the pull (spent at about the rate the boss loses
  health is paced right; out early, or lots left as the raid died, both cost), a resource they spend besides mana
  lost at the cap (Holy Power, Essence), dispels. Healing on a wipe is capped
  by damage taken, and overhealing alone isn't a fault.
- Tanks: staying alive, defensives on tank hits, taunts (`tank_swaps`), active mitigation while holding the boss.
  Never rank tanks by damage taken.
- Defensives: say one would have saved them only when the recap's replay says so, in one line after the cause.

Say what they did well, and when there's little to fix, say so.

## Planning the next pull or the next night

A raid leader decides what to change, not only what went wrong.
- Same plan or a new one: when a mechanic failed on a pull but went right on others (the best wipe, the kill),
  it's execution: the same plan again, with the one change for who does what. When it ends pull after pull the same
  way, the plan may be the problem: say so, with the count, and what the guide's plan for it is.
- A damage spike that comes back pull after pull with no healing cooldown on it (`healing` with `all_pulls`) is a
  cooldown to plan: name the spike's time, the ability behind it, and the healers' cooldowns ready then.
- More damage, another healer, another tank: what the fastest kills of the boss brought (`compare_with_top` with
  only `boss`) against what ended the raid's pulls. A fight whose pulls end on mechanics needs the mechanic, not a
  different composition; the enrage or a damage race ending them is what makes damage the answer.
- Progress over nights: `progress` with `weeks` gives the boss night by night (the best wipe, the kill, how the pulls
  ended); fewer pulls ending on the same mechanic is progress before the kill is.

Worked examples of reading a pull, a night and a cooldown plan: [examples.md](examples.md).

Anything else (a boss, a spec, loot, parses and rankings, raid prep): the tools' descriptions say which one answers it.
