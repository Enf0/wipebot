# Reviewing a night

Look for patterns, not incidents. `progress` without a boss opens with every boss of the night and covers the
latest: a night with several bosses in progress takes one call per boss.

- The same ending on many pulls is the one thing to fix: say how many pulls it ended, and when in the fight. Rank
  what to fix by the counts under "How the pulls ended", and give each with its count.
- Never lead with "The night's killer" in an ending's place: it counts deaths, not pulls lost, so it says what
  players die to, not what to fix first.
- When the most common ending is a buff the boss gained, read `boss_guide` with the buff as `mechanic`: what
  holds it off (a stack, a soak, a kick) is what to fix or practise, and early deaths matter as far as they leave
  it short.
- A fight is a damage check only when an enrage or a damage race ends its pulls. When mechanics end them, say
  so: `compare_pulls` against a kill shows whether the damage would do once they are fixed.
- Who keeps dying first, and to what: the same player dying to a raid-wide mechanic again and again points at
  them. A mechanic aimed at one role may be an assignment, so check who was hit by role with `mechanic_hits`.
- A question about every pull ("who missed their soak most?", "who pressed personals when told?", "who keeps
  standing off their spot?") is one call with `all_pulls` (`raid_note`, `raid_plan`, `pull_map`,
  `mechanic_hits`), never one call per pull.
- A player who pull after pull is hit by another group's soak, or stands at another player's spot, has likely
  changed places since the note or plan was saved: say that, and count none of it as misses. A spot the plan's
  notes say moves (a player who runs out to bait) is no mistake either.
- Improving or not is read from the best wipe and the average wipe, early against late. On a boss counted by
  phase, say how far a wipe got as the tools write it, the phase with its health ("P2 80.8%"), never the health
  alone.
- On a kill, what went right is what differs from the best wipe: `compare_pulls` with `other`.
- A summary for the raid leads with the log's own finding, then the guide's rule for it, never the guide's
  general advice alone.
