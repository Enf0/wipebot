# Worked examples

Three questions on three bosses, each as what was asked, what the tools showed, how to read it, and what the answer
holds and leaves out. The numbers are the tools'; how a mechanic works comes from `boss_guide` and how a spec is
played from `spec_guide`, read before judging either.

## A pull: "Why did we wipe on Vashnik pull 16?"

What the tools showed. `pull_summary`: the best pull of the log so far. "Ended by": deaths from 4:36 until half the
raid was dead at 6:28, no single hit killing many. "Not an enrage buff". It got past what ended most earlier pulls,
Malignance killing many (4 of 15). Two players died at 3:48 and both were back up, as the players alive after each
death show. The deaths that stayed: a damage dealer at 4:36 and a healer, Lïzårðmâñ, at 4:59; Deathadim, a tank, at
5:13 to Melee with 17 alive; Wutroll, the other tank, at 6:16 to Melee with 14 alive; two more to Melee at 6:20 and
6:22. The nine deaths after 6:28 are marked as fallout.
`tank_swaps`: the tanks traded the boss back and forth until 4:24; Wutroll held it alone from 5:15 and stood at
Dripping Fangs 3 at 6:15, where their stints otherwise end at 1; whose turn it was to taunt isn't in the log.

How to read it. No hit killed many and the boss gained nothing, so follow the chain from its first link. The deaths
at 3:48 were bought back, so they aren't it. Of those that stayed, the link the rest hangs on is the first tank's
death at 5:13: after it the other tank had nobody to trade with, the debuff the tanks swap on climbed to 3, Wutroll
died at 6:16 and the boss's melee went through the raid. The 3 stacks follow from the first tank's death; they
aren't a missed taunt. Why a tank died to Melee while holding the boss at 1 stack is the open question, with a healer
dead since 4:59 as one thing to check: `death_recap` on that death shows the hits and the healing before it and
which defensives were ready, and until it's read the answer doesn't guess.

What the answer holds. It leads with what the pull was: the best so far, past the Malignance that ended most pulls
before it, lost when the first tank died at 5:13. Then the chain in two or three links with their times, and one
change only if the recap gives one. It leaves out the nine deaths after 6:28, the two that were bought back, the
rest of the death list, and any word on whose taunt it was.

## A night: "Where do our healing cooldowns need to go on Entombed Sentinels?"

What the tools showed. `healing` with `all_pulls`, over the 8 pulls: the spikes that came back pull after pull. At
2:17-2:26, Mark of Blood and Toxic Droplets on 4 pulls: 2.64M/s taken against 2.42M/s healing, 1.2 healing cooldowns
per spike, one ready and unused in all 4, 1 death. At 1:06-1:16, Mark of Acid and Mark of Blood on 2 pulls: 2.03M/s
against 2.01M/s, one ready and unused in both, 1 death. At 2:55, Noxious Blast and Mark of Blood on 2 pulls: 3.76M/s
against 2.66M/s and 14 deaths, 13 of them on pull 7 with two cooldowns used. Most often ready and unused in a spike:
Zephyr (Lïzårðmâñ) 6 times, Rewind and Stasis (Lïzårðmâñ) 3 times each, Divine Hymn (Miralupa) 3 times.

How to read it. A spike that comes back at the same time with cooldowns ready and unused is a cooldown to plan: 2:17
first, since it came back most often, then 1:06. A spike that kills through the cooldowns it got is a mechanic to
read before anything is moved onto it: 2:55 killed 13 on one pull with two cooldowns out, so `boss_guide` with
Noxious Blast as `mechanic` comes first, and `pull_summary` of that pull says whether that hit ended it. The log
shows what was ready, not who was assigned to what; a saved raid note (`raid_note`) holds that.

What the answer holds. It leads with the spike to cover and what was free for it, then the next one: at most three
changes, each a cooldown with its player and a time. It says once that the log doesn't show the assignments. It
leaves out the list of spikes pull by pull, each healer's healing per second and overheal, and the spikes that
were covered.

## A player: "Why does Zebinomage do more damage than me on the Twin Fangs?" (asked by Krøpling)

What the tools showed. `compare_with_top` with `against`, over the 39 pulls both were in, Krøpling against
Zebinomage, both Frost Mages: damage per second alive 331.90k against 363.58k (-9%); alive 5373 s against 5472 s;
deaths 39 against 40; item level 332 against 334. Per spell, the one row marked as far apart is Frostfire Bolt: 12.3
casts a minute against 17.8, at about the same average hit (9.72k against 9.53k). Shatter hits for 90.80k against
97.26k, with a crit share of 47% against 55%. Glacial Spike, Flurry and Ice Lance are cast about as often by both.

How to read it. The same spec on the same pulls, per second alive, is the right comparison, and the gap is 9%. Time
alive and deaths are level, so it isn't dying. Shatter's smaller hit goes with the lower crit share and item level;
the tool's last line says an average hit also moves with the buffs lined up with it and the targets it hit, so this
table doesn't show that part to be a mistake in play. What's left to win back is the one row the tool marks: fewer
Frostfire Bolts a minute at about the same hit, so fewer casts begun, not weaker ones. Whether that is movement,
casts cut short or another spell in their place isn't in this table: `player_pull` on one pull shows the casting
gaps, and `spec_guide` says what a Frost Mage fills with, read before any advice on what to cast.

What the answer holds. It leads with the size of the gap and the one difference in play that stands out, with both
numbers. It says in a line that the smaller hits go with crit and item level, and that deaths aren't it. A change
in how to play comes only with the guide's rule as its reason. It leaves out the other spells' rows and doesn't rank
the two players.
