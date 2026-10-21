# Reading a wipe

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
- A raid-wiping mechanic: many dying in the same second or two to one ability. Find which mechanic failed and how:
  who carried it, who soaked it (who should have, only the raid note says), what was left alive.
- A cascade: deaths one after another, each making the next more likely.

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
