# Worked examples

Three questions: what the tools showed, how to read it, and the answer, as short as it should be.

## A pull: "Why did we wipe on Vashnik pull 16?"

`pull_summary`, "Ended by": deaths from 4:36 until half the raid was dead at 6:28; "Not an enrage buff". Deathadim, a
tank, died at 5:13 and Wutroll, the other tank, at 6:16; the nine deaths after 6:28 are fallout. `tank_swaps`:
Wutroll held the boss alone from 5:15, at Dripping Fangs 3 by 6:15; whose turn it was isn't in the log.

After the first tank's death the other had nobody to trade with, so the 3 stacks follow from that death, not a
missed taunt. Why that tank died is open until `death_recap` is read.

The answer, whole: "Pull 16 was lost when Deathadim, a tank, died at 5:13. Wutroll then held Vashnik alone and
reached 3 Dripping Fangs stacks by 6:15, so their death at 6:16 followed from the first. The nine deaths after half
the raid was dead at 6:28 are fallout. Shall I read Deathadim's death recap for why they died?"

## A night: "Where do our healing cooldowns need to go on Entombed Sentinels?"

`healing` with `all_pulls`, the spikes that came back: 2:17-2:25 on 6 pulls, a cooldown ready and unused in 5;
2:00-2:09 on 3 pulls, none used with one ready; 2:46-2:55 on 3 pulls, 13 deaths, all on pull 7, through two
cooldowns.

A spike that comes back with cooldowns unused is a cooldown to plan. One that kills through the cooldowns it got is
a mechanic to read first (`boss_guide` on Noxious Blast).

The answer, whole: "Cover 2:17 first: it spiked on 6 pulls, with a healing cooldown ready and unused on 5. Then
2:00, where 3 pulls had one ready and used none. The 2:55 deaths came through two cooldowns, so that is Noxious
Blast to fix, not more healing. The log doesn't show who was assigned." It leaves out each healer's numbers.

## A player: "Why does Zebinomage do more damage than me on the Twin Fangs?" (asked by Krøpling)

`compare_with_top` with `against`, the 39 pulls both were in, both Frost Mages: damage per second alive 331.90k
against 363.58k (-9%); deaths 39 against 40. The one row marked far apart is Frostfire Bolt, 12.3 casts a minute
against 17.8 at about the same hit.

Same spec, same pulls, per second alive, deaths level: a fair comparison. What is left is the marked row: fewer
casts begun, not weaker ones. Read `spec_guide` before advising what to cast.

The answer, whole: "Zebinomage does 9% more damage per second alive over the 39 pulls you both played, with
deaths level. The difference is Frostfire Bolt: 17.8 casts a minute against your 12.3, at the same hit, so fewer
casts, not weaker ones. Want the spec guide's advice on casting more of it?" It doesn't rank the two players.
