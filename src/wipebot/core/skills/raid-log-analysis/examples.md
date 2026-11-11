# Worked examples

Three questions on three bosses: what the tools showed, how to read it, what the answer holds.

## A pull: "Why did we wipe on Vashnik pull 16?"

`pull_summary`, "Ended by": deaths from 4:36 until half the raid was dead at 6:28; "Not an enrage buff". Deathadim, a
tank, died at 5:13 and Wutroll, the other tank, at 6:16. The nine deaths after 6:28 are marked as fallout.
`tank_swaps`: Wutroll held the boss alone from 5:15 and stood at Dripping Fangs 3 at 6:15; whose turn it was to taunt
isn't in the log.

Follow the chain to the first tank's death at 5:13: after it the other tank had nobody to trade with, so the 3
stacks follow from that death and aren't a missed taunt. Why that tank died is open until `death_recap` is read.

The answer leads with the tank's death at 5:13, then the chain with its times, and one change only if the recap
gives one. It leaves out the deaths after 6:28 and whose taunt it was.

## A night: "Where do our healing cooldowns need to go on Entombed Sentinels?"

`healing` with `all_pulls`, the spikes that came back: at 2:17-2:26 on 4 pulls, one cooldown ready and unused in all
4; at 1:06-1:16 on 2 pulls, one ready and unused in both; at 2:55 on 2 pulls, 14 deaths, 13 of them on pull 7 with
two cooldowns used.

A spike that comes back with cooldowns ready and unused is a cooldown to plan: 2:17 first, then 1:06. A spike that
kills through the cooldowns it got is a mechanic to read first, so 2:55 waits for `boss_guide` on Noxious Blast.

The answer leads with the spike to cover and what was free for it, then the next: at most three changes, each a
cooldown with its player and a time. It says once that the log doesn't show who was assigned. It leaves out each
healer's numbers.

## A player: "Why does Zebinomage do more damage than me on the Twin Fangs?" (asked by Krøpling)

`compare_with_top` with `against`, over the 39 pulls both were in, both Frost Mages: damage per second alive 331.90k
against 363.58k (-9%); deaths 39 against 40. The one row marked as far apart is Frostfire Bolt: 12.3 casts a minute
against 17.8, at about the same average hit. Shatter hits smaller, with a crit share of 47% against 55%.

The same spec on the same pulls, per second alive, is the right comparison, and deaths are level. Shatter's smaller
hit goes with the lower crit share, so the table doesn't show it to be a mistake in play. What is left to win back is
the marked row: fewer casts begun, not weaker ones. `spec_guide` is read before any advice on what to cast.

The answer leads with the size of the gap and the one difference in play. It leaves out the other spells' rows and
doesn't rank the two players.
