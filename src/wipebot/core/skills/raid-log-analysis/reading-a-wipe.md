# Reading a wipe

Read "Ended by" in `pull_summary` first, then find why: killing blows tell how players died, not why the pull was
lost. "Ended by" reads one of these ways, and each says where the why is.

- A reset, the boss untouched: say so in a line.
- The boss gained a buff and kept it to the end: when the guide calls it an enrage or berserk, find why it came.
  Where a mechanic holds it off (a stack, a soak, a kick, an energy reset), check it each time it was due, against
  the boss's power and a kill's timing, and the players alive then against the number it needs: fewer alive means
  the deaths before it lost the pull, enough alive means the mechanic was late, early or missed. Where nothing
  holds it off, the boss did not die in time: `compare_pulls` against a kill shows where the raid fell behind, and
  players dead early, or in a window where the boss takes extra damage, are damage lost.
- One ability killed many, at once or in pulses: find which mechanic failed and how (who carried it, who soaked
  it, how many were alive for it). A death in that hit says nothing about that player's fault either way.
- A raid-wide hit killed players: when a mechanic the raid missed caused the hit or made it bigger, that mechanic
  is the fix. The cooldown plan is the fix only when the guide says to use healing cooldowns for the hit and none
  came before it.
- A stacking debuff reached its lethal cap: the cause is where the extra stacks came from.
- Deaths one after another: start at the first death it names and follow what each left short: a soak, a stack,
  a role.

Then work back to the first domino. Call `death_recap` on the first one to three deaths that stayed dead before
the wipe was decided. It shows a one-shot, a slow bleed that healing did not cover, or an earlier hit that made a
later one lethal: when a small tick finished them, the cause is the hit that took most of their health. Another
player's damage buff named as the killing blow means other players killed them (a mind control, a curse that turns
them).

What to check a death or a moment with:

- A tank's death: `tank_swaps`, against the guide's swap timing.
- Where people stood when a mechanic went off: `pull_map` with the ability in `at` shows who had it and whose
  burst or area hit whom.
- Adds and their casts: `enemies`, which also says who had a kick ready when a cast went through.
- The order of events around a moment: `pull_timeline`.
- Whose job it was: with a raid note saved, `raid_note` shows who was meant to soak and which cooldowns were
  planned when; with a raid plan, `raid_plan` shows who stood away from their spot.
