# Planning

What to change before the next pull or the next night, from what the log shows.

- The same plan or a new one: when a mechanic failed on a pull but went right on others (the best wipe, the
  kill), it is execution, so keep the plan and give the one change for who does what. When the pulls end the same
  way again and again, the plan may be the problem: say so, with the count and the guide's plan for it.
- A cooldown plan: a damage spike that comes back pull after pull with no healing cooldown on it (`healing` with
  `all_pulls`) is a cooldown to plan. Name the spike's time, the ability behind it and the healers' cooldowns
  ready then. A spike that kills through the cooldowns it got is a mechanic to read in `boss_guide` first.
- More damage, another healer, another tank: set what the fastest kills of the boss brought (`compare_with_top`
  with only `boss`) against what ended the raid's pulls. For a guild still progressing, use what the latest first
  kills brought (`kills` latest): those are guilds at its stage, not in farm gear. Pulls that end on a mechanic
  need the mechanic, not another make-up, and another healer is read from the damage spikes and the deaths in
  them (`healing`), not from overheal.
- Progress over nights: `progress` with `weeks` gives the boss night by night. Fewer pulls ending on the same
  mechanic is progress before the kill is.
- One mechanic against another night ("did we take less of it than last time?"): `mechanic_hits` with `against`,
  that night's report from `find_reports`. Compare per minute of the pulls, never totals, since nights differ in
  pulls and their length.
