# Reviewing a night

Look for patterns, not incidents. The same ending on many pulls is the one thing to fix: say how many pulls it
ended, and when. `progress` names the night's killer: its deaths, how often it was the first, whom it killed and
who took most of its hits. Who keeps dying first, to what, and whether that mechanic hits everyone or targets one
player or role: the same player dying to a raid-wide mechanic again and again points at them; one aimed at a role
may be an assignment, so check who was hit by role (`mechanic_hits`). `progress` without a boss opens with every
boss of the night and covers the latest; a night with several bosses in progress takes one call per boss.

A question about the whole night ("who missed their soak most?", "who pressed personals when told?", "who keeps
standing off their spot?") is one call with `all_pulls` (`raid_note`, `raid_plan`, `pull_map`, `mechanic_hits`), not
one per pull: those count per player over every pull on the boss.

Improving or not: the best and average wipes early against late, and how far pulls got. A fight is a damage check
only when an enrage or a damage race ends it; when mechanics end the pulls, say so (`compare_pulls` against a kill
shows whether the damage would do once they're fixed). A death inside a window where the boss takes extra damage
costs double. On a kill, what went right is what differs from the best wipe (`compare_pulls` with `other`).

A summary for the raid leads with the log's own finding, then the guide's rule for it, never the guide's general
advice alone.
