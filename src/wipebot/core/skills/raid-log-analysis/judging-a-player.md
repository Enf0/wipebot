# Judging a player

Answer the question they asked, and lead with what cost them the most. A death before the wipe was decided usually
does (nothing is done after it), so it often comes first; but "why is my damage low" is about their damage while
alive, and a small cost never leads just because of its kind. `player_pull`'s "Time lost" line puts a death, casting
gaps and a capped resource in one unit (seconds before the wipe was decided) to weigh them by. About a night, cover
every boss they pulled, kills first: a kill is the best evidence, a progression wipe the weakest. Across nights,
`player_history` with deaths shows what they keep dying to.

- Deaths: only those before the wipe was decided count, and how often they were among the first. Everyone dies on
  a wipe.
- Mechanics: damage from mechanics others avoided (`mechanic_hits` without an ability; `breakdown` of damage
  taken by ability, until the wipe was decided), judged against how the mechanic works (`boss_guide`); a soaker or
  a targeted player takes more by design.
- Throughput against the right reference: on a kill, the parse. On a wipe, where their output per second alive
  falls among the spec's ranked kills (below the 25th percentile is a real gap; above it proves little, as short
  pulls run high), and their own best pull. Only against the same spec, never by totals across pulls.
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
  health is paced right; out early, or lots left as the raid died, both cost), dispels. Healing on a wipe is capped
  by damage taken, and overhealing alone isn't a fault.
- Tanks: staying alive, defensives on tank hits, taunts (`tank_swaps`), active mitigation while holding the boss.
  Never rank tanks by damage taken.
- Defensives: say one would have saved them only when the recap's replay says so, in one line after the cause.

Check advice against the spec's guide for their hero tree (`spec_guide`). Say what they did well, and when there's
little to fix, say so.
