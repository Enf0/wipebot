# Warcraft Logs v2 API: verified facts

What the GraphQL API does that its schema doesn't say, each checked against real reports. Wipebot's queries are in
`src/wipebot/core/wcl.py`.

- Wipebot reads Warcraft Logs through the API and its public image assets only, never the site's pages.
- Endpoints `https://www.warcraftlogs.com/api/v2/client` (client credentials) and `/api/v2/user` (user
  login). Every request needs a token, even for public reports (no token: HTTP 404).
- Wipebot reads with client credentials (`WCL_CLIENT_ID`/`WCL_CLIENT_SECRET`: public reports only). warcraft_cli
  uses a user login saved in its store (`~/.local/state/warcraft/providers/warcraftlogs.json`) instead when there is
  one, so `wipebot serve` refuses to start with one. Wipebot had its own browser login until 19 October 2026 (PKCE,
  a public client); a user token lasts about 360 days. `wipebot serve` reads a signed-in user's tool calls with the
  token their sign-in gave (`wcl.reading_as`), at `/api/v2/user`, so their own points pay first ("Point types").
- Realm slugs aren't one rule: Warcraft Logs keeps some word breaks (`tarren-mill`, `area-52`), runs others together
  (`azjolnerub`; `azjol-nerub` finds nothing) and slugs some Russian realms in English (`Ревущий фьорд` is
  `howling-fjord`). So `WCL.find_guild` takes the realm as typed and lets warcraft_cli's client try each spelling.
  Player rows name the realm without spaces (`TarrenMill`); Raider.IO's client accepts that as is.
- Rate limit is 3600 points/hour per developer account (the one owning the clients), not per client or user:
  `rateLimitData` showed one counter for the client-credentials key, a second client of the same account and that
  account's user (PKCE) token; points spent through any of them moved all three (14 Oct 2026). Another account signed
  in through the server's client spends the same counter too: the limit belongs to the account that owns the
  clients, so users signing in with their own Warcraft Logs accounts don't add quota. That is the old point system;
  the new one (in beta from 20 October 2026) adds database and user points: "Point types" below.
- `report.fights(killType: Encounters)`: fight `startTime`/`endTime` are ms relative to the report start.
  Report `startTime`/`endTime` are UNIX ms. `inProgress` is true while a pull is still being logged.
- `difficulty`: 1 LFR, 3 Normal, 4 Heroic, 5 Mythic. `lastPhase` 0 means the boss has no phases.
- Phases repeat: `phaseTransitions` lists every phase entered (Sszorak goes P1 → P2 "Howling Maelstrom", the
  intermission → P1), so `lastPhase` 1 at 2:40 means the second time in P1. `report.phases` names them.
- Enrages and other boss states only show as enemy buffs: `events(dataType: Buffs, hostilityType: Enemies)`,
  bosses being the actors with `subType: "Boss"`. The deaths after an enrage name whatever ability hit last.
- For aura events (Buffs, Debuffs) the `sourceID` filter selects the player who has the aura; the events keep
  their real source. A dying player's auras are removed about 20 ms before the death event.
- `events(dataType: Healing)` takes `targetID` for the healing a player received.
- `report.playerDetails` returns `potionUse`/`healthstoneUse` 0, but `table(dataType: Summary)` →
  `playerDetails.{tanks,healers,dps}[]` has WCL's real counts (`healthstoneUse` = healthstones and healing
  potions, `potionUse` = combat potions, pre-pull ones included); pass `endTime` for "before a death".
- Battle res are `resurrect` events (`events(dataType: All, filterExpression: "type = \"resurrect\"")`),
  a Soulstone's too (Soulstone Resurrection); a shaman's Reincarnation is only a cast, 1-15 s after the death.
  WCL sometimes lists a battle res twice, a millisecond apart. Bloodlust shows as its debuff (Sated, Exhaustion,
  ...) on the players, whose source is each player; Lorrgs' `spell 2825` lists those debuff ids (`variations`).
- A dead player casts nothing until brought back (checked 2026-10-21 on 141 pulls, 1,957 player deaths): every
  cast after a death that no `resurrect` answered was a Reincarnation and the casts after it, except `fake: true`
  casts WCL derives (a dead Demon Hunter gets one about 1 s after dying). A Holy Priest's Spirit of Redemption
  logs the death when the spirit ends (vjt3KLbN8g62J1H9 fight 26: the buff at 4604387, the death 22.6 s later),
  so the spirit's casts come before it. Wipebot reads the first cast after each unanswered death in one query a
  night (`Report.resurrects`).
- In a filter expression `timestamp` counts ms from the fight's start (`timestamp > 45043` on KBYT4xXn7VNjDgh8
  fight 27 kept the casts after a death 45.0 s in; verified 2026-10-21), and `source.name` matches accented names
  as written (`"Lïzårðmâñ"`). A `type = "cast"` filter on Casts keeps `fake` casts.
- `table(dataType: Threat)` → `threat[].targets[].bands[{startTime, startEvent}]`: who had a boss's aggro
  when, and the event that took it (usually the taunt). Better than a list of taunt spells.
- Boss health and power over time: `graph(dataType: Resources, hostilityType: Enemies, sourceID: <boss>,
  abilityID: 1000)` (health %) or `abilityID: 100 + power type` (the power type is in `classResources` of the
  boss's events with `includeResources: true`; Sszorak's bar is type 3, reset by each Serpent's Fury pop).
- `wipeCutoff: N` on table/events/graph ignores everything after the N-th death (`breakdown`'s `cutoff`; the
  table's `totalTime` is cut too). Wipebot cuts damage taken and mechanic hits at the moment a wipe was
  decided (the boss's end buff or half the raid dead) with `endTime` or by filtering events.
- WCL tables cut lists to the top five: the all-players Casts table keeps each player's top 5 spells (pass
  `sourceID` for one player's full list), and `viewBy: Ability` tables list an ability's top 5 targets. For
  per-player counts over a raid, read events.
- Debuffs a player put on enemies (DoT uptime): `table(dataType: Debuffs, hostilityType: Enemies,
  filterExpression: 'source.name = "Name"')`; `source.id` in a filter expression matches nothing. A player's
  own buffs: `table(Buffs, sourceID: id, filterExpression: 'source.name = "N" and target.name = "N"')`.
- A player's resource over a fight: `graph(dataType: Resources, sourceID: player, abilityID: 100 + type)`;
  mana comes in percent, other resources raw (the max is in the cast events' `classResources`).
- `worldData.encounter.characterRankings(page: N)` gives 100 per page (each character's best kill) with
  `hasMorePages`; Mythic Sszorak had about 1,200 Assassination Rogues (13 pages), so the whole distribution
  takes a few cheap queries. `bracket` is a bracket index, not an item level.
- `fights { enemyNPCs { id gameID instanceCount } }` lists a fight's enemies; `events(Deaths, hostilityType:
  Enemies)` when they died. `events(All, hostilityType: Enemies, filterExpression: 'type = "death"')` is not the
  same: it also has the raid's own deaths, players and pets (Blood Beast, Niuzao). Nymrissa pull 1 of
  GM8XdBHaqpFgxK9Q (fight 4, a wipe; checked 2026-10-21): 27 death events there, 22 players and 5 pets; no add died,
  and the Deaths query rightly has none.
- `table(dataType: Survivability)` gives death stats over many pulls (we compute ours from Deaths events).
- `events(dataType: Deaths, death: K)` is WCL's own recap of the K-th death in a fight (damage, heals, absorbs,
  hit points per event) but no fit for us: its `buffs` field holds the healer's auras on heal events and the
  victim's on damage events, it has no debuffs, and its window is shorter (6.5 s on the death checked).
- `worldData.encounter(id).fightRankings(difficulty, metric: speed)` lists the 50 fastest kills (report
  code, fight id, duration, deaths, `bracketData` = average item level); another guild's public kill reads
  like our own report (`masterData { actors(type: "NPC", subType: "Boss") }` for its boss ids).
- Many bosses carry a buff to the end that isn't an enrage (Vashnik's Flame/Shadow Infusion from 0:24,
  Nymrissa's Wavecaller's Might, Nek'zali's Swirling Spirit). Sszorak's real enrage ended every pull within 18 s,
  so Wipebot calls a kept buff the end of a pull only when it was gained within 30 s of the end
  (`pulls.END_BUFF_WINDOW_MS`).
- A buff cast or drunk before the pull (Stormkeeper, a pre-potion) has no `applybuff` in the fight: its first
  event is `removebuff`/`removebuffstack`/`refreshbuff`. WCL sometimes lists a death twice, milliseconds apart.
  A player back from a battle res or Reincarnation is alive again from their next cast (not a `fake` one).
- WCL has no notion of a reset (`wipeCalledTime` is set only from the companion app): Wipebot calls a wipe
  with the boss at 99% or more a reset.
- `playerDetails(includeCombatantInfo: true)` has `talentTree` (`id` = Raidbots' talent entry id = the
  `talentID` in rankings) and gear with `slot`, enchants and gems. `CombatantInfo` events have the auras at
  the pull (unnamed ids resolve on Wowhead) and gear in slot order with `temporaryEnchant` (a weapon oil, a
  rogue's poison or a shaman's imbue: Raidbots' `temp-enchants.json` says which are oils). Which aura is a
  flask or augment rune: the item Wowhead links to the aura spell (`wowhead entity-page spell <id>`) is in
  Raidbots' `flasks.json`/`augments.json` (the augment rune's aura is just "Void-Touched"). Food buffs and
  vantus runes are in no data source: their aura names ("Well Fed", "Vantus Rune: ...") decide.
- Other guilds' reports can be read in one query, each under an alias (`r0: report(code: "...") { playerDetails(
  fightIDs: [21], includeCombatantInfo: true) }`): the stats of a spec's top 10 parses (rankings name the report,
  fight and character) came in 2.7 s, but as 1.8 MB, about 180 KB a report, so Wipebot keeps only each player's
  stats (`WCL.parse_stats`). Rankings carry gear and talents but no stats.
- `playerDetails` over a whole report lists a player who changed role during the night once per role (a shaman in
  8TcXMAP2gHQGr9pW: under `healers` as Restoration, count 2, and under `dps` as Elemental, count 18), and the
  order of the role keys changes from report to report (`tanks, healers, dps` there, `dps, tanks, healers` in
  3gBPRJMjfxbCX8K4). Over one pull each player is listed once, with that pull's spec and role; cheaper, the
  report's `fights { friendlySpecs }` lines up with `friendlyPlayers` (that shaman: Elemental in fight 45,
  Restoration in 46).
- `character.encounterRankings(metric: …)` ranks every kill by the metric asked for, whatever the spec: a
  Restoration kill gets a dps percentile (a 20k dps Restoration Vashnik kill: 78%) and an Elemental kill an hps one.
  `specName` filters to one spec. Which specs heal: `lorrgs specs` (`role: heal`).
- `character.zoneRankings(zoneID, difficulty, metric, role, specName)` is the character page and the guild
  rankings page: per boss the best kill, `bestPerformanceAverage` (the page's "Avg"), `medianPerformanceAverage`,
  `allStars` per spec. Its metric is a `CharacterPageRankingMetricType`, not `CharacterRankingMetricType`.
  `role: DPS` (or Healer, Tank) keeps that role's specs: the guild page's Damage tab is `metric: dps, role: DPS`
  (a Death Knight's 79.9 there; `role: Any` adds their Blood kill and gives 69.6, and ranks a Holy Paladin's kills
  on dps).
- `character.recentReports(limit, page)` lists every report a character is in, whoever uploaded it: the guild's
  nights, pugs and other guilds' runs (a raider's Heroic pug is in no guild's list), Mythic+, and one night logged
  by several people as separate reports. `limit` is at most 100. The order is by upload, not by
  start: a report started months earlier can sit among this week's. Wipes are in it, unlike the rankings. A report's
  `owner.name` can be an email address: don't print it.
- `report.rankedCharacters` gives every ranked player's character id (`id`, `name`, `server`) in one query, so
  per-character queries can be aliased into one without looking each character up.
- `report.rankings` gives percentiles (`rankPercent`, `bracketPercent`) for kills only; a wipe gives [], and so does
  a kill Warcraft Logs hasn't processed yet (don't remember that answer). It defaults to `timeframe: Today`, while
  `encounterRankings` and `zoneRankings` default to Historical: the same kill came out 27 and 28 (we ask Historical
  everywhere). Without `playerMetric` it ranks everyone on dps, healers too. Leaving `fightIDs` out gives every kill
  of the report in one query; `fightIDs: null` gives nothing. Each kill has `speed` and `execution` percentiles
  (Vashnik in 8TcXMAP2gHQGr9pW: 35 and 10), `deaths`, `duration`; each character `totalParses` of their class and
  spec (Holy Paladin and Holy Priest differ).
- `worldData.encounter.characterRankings(page:)` stops at page 20 (the best 2000): page 21 returns
  `{"error": "The maximum page value supported by the API is 20."}` and no rankings while page 20 still says
  `hasMorePages`, and nothing gives the total (no count field; an item-level `bracket` filter is still over 2000
  for Mythic Vashnik Resto Shamans). The real count comes from a kill's `totalParses` in some report.
- `fightPercentage` is Warcraft Logs' measure of how far a wipe got; it differs from `bossPercentage` on bosses
  with phases (Ula'tek phase 1 wipe in BfgjDv6LC2HaVbFm: 83.34 against 76.63; Coiled Altar 0.35 against 0.83).
- `reports(guildID:)` data can nest `fights(killType: Encounters) { difficulty }`: a report's boss pulls per
  difficulty in the listing, without loading it.
- `guild.attendance(zoneID, limit, page)` lists the guild's logs of a raid with `players {name type presence}`
  (presence 2 = benched): two people logging one night are two entries (3gBPRJMjfxbCX8K4 and 8TcXMAP2gHQGr9pW),
  and alt and side runs are in it (a 71-player log), so filter by pulls on the raid's difficulty.
- `guild.members(limit, page) { data { name } }` is the game's guild roster, alts included (several hundred
  names for one guild). Raiders log their pugs to the guild's page too; the share of a log's players in it tells
  them apart: the guild's nights had 86-100%, pugs and other groups 2-48% (Wipebot's line: two thirds).
- `guild.zoneRanking(zoneId) { progress { worldRank regionRank serverRank { number } } }`: the guild's progress
  rank in the world, its region and its realm.
- `fights { talentImportCode(actorID:) }` gives a player's talents on a pull as the game's import string.
  `worldData.encounter.characterRankings(includeCombatantInfo: true)` gives the top 100 with talents and gear.
- Anonymous parses (verified 2026-10-18): a top 100 of `characterRankings` had 0-6 rows with `name: "Anonymous"` and
  `hidden: true` (ten spec, boss and difficulty lists), with talents, gear and a report code `a:<code>`. That
  report can be read, but it calls every player "Player (N)", so the parse's player isn't found in it
  (`ranks.readable`). The 50 fastest kills of `fightRankings` had none hidden (fourteen lists).
- `graph` needs the fight's `startTime`/`endTime` or it spans the whole report; its series are rates.
- DamageDone/Healing table entries have `activeTime` (ms) and, for damage, `targets`.
- `events(dataType: Deaths)` → `targetID`, `killingAbilityGameID`. The killing ability is missing on
  about 15% of deaths, which are shown as "no killing blow recorded".
- For `dataType: DamageTaken` (table and events), the `sourceID` filter selects the player who *took* the
  damage. The events themselves have the normal source/target.
- `table(DamageTaken, hostilityType: Friendlies)` lists only the top 5 abilities per player; filter with
  `sourceID` for one player's full list. `viewBy: Ability` gives per-ability totals with targets.
- Dispels and Interrupts tables nest per ability: `entries[0].entries[]` with `spellsBegun`,
  `spellsInterrupted` (dispelled), `spellsCompleted` and per-player `details`. They don't say who was dispelled;
  `events(dataType: Dispels)` does: `type: "dispel"`, `sourceID` the dispeller (a totem, e.g. Poison Cleansing
  Totem, is its own actor: `petOwner` gives the shaman), `targetID` the dispelled player, `extraAbilityGameID`
  the aura removed (Sszorak pull 42 of vjt3KLbN8g62J1H9: 24 Tempest dispels).
- Positions: with `includeResources: true`, damage taken and cast events carry the unit's `x`, `y` (hundredths of a
  yard: on that pull the median distance to Sszorak was 7-11 yd for the tanks, 10-12 for melee, 13-15 for casters, 21
  for a BM Hunter and 15-26 for healers, and the bounding box is 143 x 141 yd), `facing`, `hitPoints`, `maxHitPoints`
  and `mapID` (the uiMapID) of one of its actors: `resourceActor` 1 is the source, 2 the target. Casts give the
  caster's; on Sszorak pull 42, 5,527 damage-taken events gave the player's (2) and 1,657 the attacker's (1). Gaps of
  15-20 yd within a second in a player's positions are real (Shimmer and other blinks). On that pull 7,184 of 8,095
  damage-taken events and 4,650 of 5,578 casts had them: about one position per player every 0.34 s (median; 90%
  within 1 s). The boss's casts (`hostilityType: Enemies`) have its position and facing too, and `fights { boundingBox
  { minX maxX minY maxY } }` the area of the pull.
- `facing` is -100 times the angle in radians, counter-clockwise from +x (so `-facing / 100` is the direction in
  the same x/y as positions). Verified on Sszorak pull 42 (vjt3KLbN8g62J1H9) and pull 13 (KBYT4xXn7VNjDgh8): a
  player's facing on 1,615 casts at the boss pointed at the boss within 6.5-6.8 degrees (median; 94% within 45),
  and the boss's facing (on hits it took, resourceActor 2) at the tank its melee swing hit within 1-2 degrees.
- Maps: the API gives a pull's map id (`fights { maps { id } }`; `gameData.map` only adds the name), but not where
  the map sits in the world. The replay draws `https://assets.rpglogs.com/img/warcraft/maps/<id>-map.png?v=2`
  stretched over a box, in the same hundredths of a yard as positions; image right is +x and up is +y, with no
  rotation. The box isn't in the API: it is the game's own `UiMapAssignment` table (yards) for that uiMapID, the
  same box the site's replay uses (map 2609, The Venomous Abyss, Sszorak; 2004 x 1336 px: x -103375..11375,
  y 17500..94000):
  minX = -100 * Region_4, maxX = -100 * Region_1, minY = 100 * Region_0, maxY = 100 * Region_3 (map 2609:
  Region 175, -113.75, 940, 1033.75, exactly the replay's box). The table is at wago.tools
  (`/db2/UiMapAssignment/csv`), and a full copy is in GitHub's acornellier/threechest
  (`src/data/coordinates/uimapassignment.json`, updated by hand; it had 2609 on 2026-10-13).
- `report.rankings` of a kill nobody ranks against (a Heroic pug, no parses yet) has `rankPercent: "-"` (a string)
  in `speed` and `execution`, with `totalParses: 0`. So does a guild's own kill right after it, before Warcraft
  Logs has ranked it (every kill of 14 Oct, cZ8rp13y72gBQKY9, at 19:00 UTC): every player's `rankPercent` is "-",
  `bracketPercent` 0 and `totalParses` 0, where a kill not processed at all gives [].
- `masterData { abilities }` is about 900 KB.
- Several guild members may log the same night as separate reports, so the follower locks onto one.
- `events(...)` pages by `nextPageTimestamp` (at most 10,000 events a page), but a next page asked for with
  `startTime` and `fightIDs` and no `endTime` comes back empty, so a big query stopped at its first page: over 6
  pulls, 5,250 of 26,030 friendly DamageTaken events (2026-10-13). Any `endTime` past the fights fixes it;
  `WCL.events` always sends one.

## Sign-in (verified 2026-10-17)

The remote server signs people in with Warcraft Logs (`core/accounts.py`).
- It uses the confidential client it reads with (`WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`): the authorization code
  flow, the token request authenticated with the client id and secret by HTTP basic auth (Warcraft Logs' API
  documentation, "Authorization Code Flow"). Warcraft Logs also accepts a PKCE verifier on that request, so the
  server sends both (verified live 19 October 2026: HTTP 200, `expires_in` 31104000, a refresh token included). The
  server's callback, `<public URL>/wcl/callback`, must be one of the client's redirect URLs (comma-separated in the
  client's settings). The token is kept, encrypted, for the user's tool calls; its scope stays `view-user-profile`,
  which reads public reports and rankings like the client credentials and no more, so what it reads may be cached
  for everyone. A token Warcraft Logs refuses (HTTP 401) is read past with the client credentials.
- `userData.currentUser` (user token, scope `view-user-profile`) gives `id` (an int), `name`, `battleTag` and
  `characters`: the ones the user claimed, in any guild.
- `Character.guildRank` is the in-game rank (0 = guild master). A character in no guild has `guildRank` 0 and
  `guilds` empty, so a rank means something only next to a guild.
- A character's `guilds[].server` is the guild's realm, not the character's: a character on one realm can be in a
  guild on another (connected realms). Region slugs come upper case (`EU`).
- `characterData.character(id)` with client credentials gives the same `guildRank` and `guilds` for any character,
  so membership is checked again without the user's token; aliased, one query covers a user's characters.
- An allowed guild is matched by its id in `guilds[].id`, found once by its slugs written out as a name (warcraft_cli's
  guild lookup, `the-dragonfell-tribe` on `kazzak` found, 21 October 2026); names alone would let in a look-alike.

## Using the API efficiently (verified 2026-10-14)

Every query adds to how long a raider waits in chat, and all of them spend the account's one hourly quota (above).

- Warcraft Logs' own advice (its API documentation, "Quota Management", December 2025): cache game data (abilities,
  classes) indefinitely; a report's fight and actor lists for 5-10 minutes while it has recent fights, else
  indefinitely ("old reports almost never have new fights added"); events, tables and graphs of a fight whose
  `inProgress` isn't true indefinitely; rankings, characters and guild data for hours. Points reset every hour, so
  low-priority work can wait in a queue when the hour's points run low, and `rateLimitData { limitPerHour
  pointsSpentThisHour pointsResetIn }` can be added to any query to watch them.
- What we keep on disk (`cache.py`; "for good" is `FOREVER`, 30 days, after which entries are dropped). An entry
  past its time is still served while a new one is fetched, up to twice its time; older, only when the source fails:
  - a finished pull's events and tables: for good (`Report.keep`);
  - a report without a pull in the last two hours (`base.SETTLED_S`): its actors, ability table (about 900 KB) and
    player details, for good. Its fight list is read again on each load, one small query;
  - a past raid night read for another night's question (`settled`, a day or more old): its fight list too;
  - rankings, attendance, guild progress: an hour; guild members, zones, encounters, ranked characters, the guild's
    furthest wipes before a night: a day (the furthest wipes for good once the night's log is settled).
- Loading a report is four queries (fights, actors, abilities, player details): about 1.3 s, 0.5 s with its
  names from disk. A tool that read ten raid nights took about 90 s the first time and 12 s with the per-pull
  data on disk, against 1 s for the same tool on one pull; with past nights loading from disk, about 2 s.
- Many pulls' tables go in one query with aliases (`i0: table(fightIDs: [1], ...) i1: ...`, `Report.tables`):
  one round trip for a whole night instead of forty.
- What a query costs in points (measured 2026-10-21 on a 20-player Mythic night with `rateLimitData` before and
  after): an aliased query is billed per alias, not per query. A table is 1 point an alias, and one table over 10
  fights 1.2; playerDetails with combatantInfo 2 an alias; events, aliased for 10 pulls, about 1 in all; a guild's
  report listing with each report's fights about 1 per report; a kill's rankings 2; zoneRankings about 120 for
  about 20 characters. So a whole night's per-pull tables cost as many points as there are pulls; one table over
  all of them, or events, where per-pull numbers aren't shown, cost about one.
- Events summed don't give a table's numbers (verified 2026-10-21, a 10 s window of one pull): raid damage taken by
  ability came to 23.06M in the table, 19.84M as the events' `amount` and 23.55M with `absorbed`; healing 31.06M in
  the table, 31.66M in the events. Swapping a shown table for events changes what a tool prints.
- A whole-report table isn't a shortcut: `table(dataType: Threat, killType: Encounters, startTime: 0, endTime:
  ...)` for two reports took 9.4 s in one query, and it joins aggro bands across pulls (Death Grip 18 times
  against 19 counted pull by pull). Ask per pull and batch the pulls.
- When Warcraft Logs hangs, a request fails after its timeout (20 s) on each try: warcraft_cli tries three times
  by default (62 s for two `progress` calls on 2026-10-14), Wipebot twice (`WCL_ATTEMPTS`).
- A query has a complexity limit of 50,000 (verified 2026-10-15): a guild's reports at `limit: 100` with each
  report's boss pulls as `{ id encounterID name difficulty kill fightPercentage }` came to 60,601 and was refused
  ("Max query complexity should be 50000"). The guild's furthest wipes (`WCL.guild_wipes`) ask 50 reports a page,
  filtered to the raid's `zoneID`.

## Point types (verified 2026-10-22)

Warcraft Logs' new point system (Archon's post "Archon API updates (beta)", 20 October 2026; its values may change
until the beta ends) splits points in three. Application points, as before. Database points, a cap inside them for
fields labelled [Database Points]: past it those fields fail and the rest still answers. User points, a signed-in
user's own pool, spent first on fields labelled [User-Eligible Points] when a query is made with their token.
- The schema labels the fields in their descriptions. [Database Points]: `ReportData.reports`, `Report.rankings`,
  `Encounter.characterRankings`, `Character.encounterRankings`, `zoneRankings` and `recentReports`,
  `Server.guilds` and `Server.characters`. [User-Eligible Points]: `Report.events`, `table`, `graph`, `fights`,
  `masterData`, `playerDetails`, `phases`, `guild`, `zone`, `owner`, `ReportFight.talentImportCode`, `GuildData.guild`
  and a guild's `members`, `attendance`, `tags`, `server`, `GuildZoneRankings`, `CharacterData.character(s)`,
  `Character.gameData`, `UserData`. `WorldData` (zones, encounters, `fightRankings`) has no label.
- `rateLimitData` gains `databaseLimitPerHour`, `databasePointsSpentThisHour`, `userHasRemainingPoints`,
  `userPointsResetIn` (null without a user token) and `currentRequest`: the request's `application`, `database` and
  `user` points and its `total` (at least 1). The spent counters it gives in a query leave that query out.
  `currentRequest` shows user and database points without opting in, so any query's cost can be measured now.
- A key not opted in keeps 3,600 points an hour; `databaseLimitPerHour` reads 250 but isn't enforced (the counter
  went past it and rankings still answered), and points `currentRequest` counts as `user` are charged to the
  application, user token or not. A client made on 22 October 2026 on the same account wasn't in the beta despite
  the post ("new keys ... will be opted into the beta"): 3,600 points an hour, and the same counters as the old key
  (101 points spent through it moved both). `@onlySpendUserPoints` (written after a query's variables, `query($code: String!)
  @onlySpendUserPoints { ... }`) is refused: "cannot require only user points be spent without authenticating a user
  and opting into the new point system".
- User points are per Warcraft Logs account, shared "across all applications the user authenticates with" (the
  schema's description of `userHasRemainingPoints`).
- A user token with only the `view-user-profile` scope reads a public report at `/api/v2/user` (fights, tables,
  events) and rankings, the same as the client credentials; private reports need `view-private-reports` (warcraft_cli
  says so; not tried).
- What each query costs, from `currentRequest` (KBYT4xXn7VNjDgh8, a Mythic kill, its guild's reports):

  | Query | Database | Application | User-eligible |
  |---|---|---|---|
  | A report's fights; a table of one pull; a whole report's death events | 0 | 0 | 1; 1; 1-10 |
  | A guild's reports, a page of 100, without their fights | 1 | 1 | 0 |
  | The same with each report's boss pulls (100 a page), or with players (25), or the furthest wipes (50) | 1 | 1 | 50-59 |
  | A guild's attendance, a page of 25; its members, a page of 100; its `zoneRanking` | 0 | 0 | 25; 1; 1 |
  | A kill's rankings; every kill's in a report | 3-5; 2 | 2 | 0 |
  | A spec's top 100 with talents and gear; a ranking page, page 1 to 20 alike; the fastest kills | 1-2 | 1 | 0 |
  | `zoneRankings`, per character (aliased: 20 characters 100) | 5 | 5 | 0 |
  | `encounterRankings`, one boss | 1 | 1 | 0 |
  | A character's `recentReports`, a page of 25 | 4 | 0.25 | 25 |

  So the database points go mostly to character pages (`zoneRankings`, 5 a character), and a ranking page costs one
  however deep; the user-eligible points mostly to a guild's report list with its pulls.

## Adds, interrupts and dispels (verified 2026-10-13)

- An add can be `subType: "Boss"` in masterData (the Drowned Echo and Echo of Jawae on Nek'zali, Ula'tek's Gore
  Rattle): `instanceCount` in the pull tells it from the boss (the boss comes once; such an add several times).
- WCL lists some add deaths twice a few ms apart, like players' deaths: dedupe per (actor, instance) within 1 s.
- `events(DamageDone, hostilityType: Friendlies, targetID: N)` filters by target: the last hit on a surviving add.
- Interrupts table: `spellsBegun = spellsInterrupted + spellsCompleted + the rest` (the rest ended otherwise: the
  caster died or was moved); `missedCasts[]` lists the casts that went through.
- Dispels table (verified 2026-10-15): `spellsInterrupted` is the number of dispels, each `dispel` event, one that
  only took a stack off a debuff that stayed included; `spellsCompleted` the applications that ran out, listed in
  `missedCasts[]` with `target`, the player who wasn't dispelled; `spellsBegun` the applications that ended,
  dispelled or ran out (one still up when the pull ended isn't in it). So dispels plus ran-outs can exceed the
  applications: Exploding Infection on the 19 Vashnik pulls of kTMfFRQ324Hvx6G7 (checked 2026-10-21) had 324
  begun, 307 interrupted, 18 completed, one dispel only taking a stack off and leaving the debuff on. The applications dispelled are `spellsBegun - spellsCompleted` (306); the `details[]`
  dispellers' totals add up to the dispels (307). Congealing Bolt in the same table added up: 1565 = 96 + 1469; on
  pulls 1-3 alone (fights 15, 18, 19) both did (Exploding Infection 34 = 28 + 6).
- Spirit Link Totem in the Healing table (verified 2026-10-15): by ability, it is two rows with the same `guid`
  (98021) and the totem as `actor`, the health it shares out: "Spirit Link", what it gave (positive), and "Spirit
  Link (Damage)", what it took, with a negative `total` and `spiritLinkDamage: true`. A player's `total` in the
  Healing table counts both, so the negative row comes off the shaman's healing: Latael on KBYT4xXn7VNjDgh8 fight 21
  (Sszorak pull 13) had 52,416,087, the sum of the ability rows with Spirit Link (Damage) -1,577,227 and Spirit
  Link +1,314,178. Wipebot's `breakdown` by ability leaves the negative row out of the total and the shares and
  shows it apart (53.99M there); by player, Warcraft Logs' totals stand.
- Mutilate on Sszorak is two spell ids (1277027 begincast and cast, 1277031 cast): two abilities sharing a name
  are not two casts.
- A one-player Casts table (`sourceID`) nests some spells as one `composite: true` row with `subentries`, one per
  id, and the row's `total` is only its own id's count (verified 2026-10-18, KBYT4xXn7VNjDgh8 fight 21 and
  8TcXMAP2gHQGr9pW fight 45). The other ids can be another spell the talent tree puts in its place (Shadow Bolt 20
  with Infernal Bolt 3 under it; a Shadow Priest's Mind Blast 30 with Void Blast 39), a spell whose own id has none
  (Verdant Embrace: total 0, its 360995 subentry 5; Hand of Fate 0, Fatebound Coin 9), or the same cast under
  several ids (Odyn's Fury 3 with 385059, 385060 and 385062 at 3 each and the off-hand 6), so neither the row's
  total nor the sum of its subentries is the casts. The all-players Casts table sums them (that Mind Blast: 69).
- A player's Casts events are `cast` (an instant, or a hard cast landing), `begincast` (a hard cast starting)
  and `empowerstart`/`empowerend` (an Evoker's empowers); none carries a duration or the GCD, and a channel's end
  isn't logged. A hard cast landing and the next one beginning share a millisecond, landing first in the data. A
  begun cast with no `cast` of the same id after it was dropped (on Sszorak pull 13 each one was followed by a
  Ghost Wolf, a Gust of Wind, an on-GCD instant or the same spell begun again).
- Lorrgs `boss-spells <slug>` lists the boss abilities its timeline tracks, `show: true` per spell.

## Ranking partitions (verified 2026-10-13)

- A zone's rankings live in partitions (`worldData.zone.partitions { id name compactName default }`: The
  Venomous Abyss has 1 = 12.1 and 2 = 12.1.5, the default). `characterRankings`, `fightRankings`, a character's
  `encounterRankings` and `zoneRankings` all take `partition`; without it they use the default, which is empty
  for a while after a patch opens a new one (on 2026-10-13 every spec's rankings came back `count: 0`, no error,
  while partition 1 had them). `wcl.py` falls back to the partitions before the default when the default is empty.

## Zones and the game's raids (verified 2026-10-15)

- A zone isn't a raid. Warcraft Logs lists the season's single-boss raids under the tier's zone: The Venomous Abyss
  (zone 53) has ten encounters, its eight bosses then Nymrissa Wavecaller (The Tidebound Grotto) and Kith'ix (The
  Unbinding of Kith'ix), and a report's `zone` is the tier for both. The game's raids and their boss order come
  from Raider.IO's raid list (`raiderio raids`, `knowledge.raids`); `Report.raids` groups a report's bosses by it.
- `fightRankings` (speed) rows carry each kill's `tanks`, `healers`, `melee` and `ranged` counts, so a kill's raid
  makeup needs no query of its report.
- Rankings by item level: `zone(id) { brackets { min max bucket type } }` (The Venomous Abyss: 272-344 in steps of
  3) numbers the brackets from 1 at `min`, so bracket 20 is item level 329-332; `fightRankings(bracket: 20, ...)`
  gave the 150 fastest Mythic Twin Fangs kills in it over 3 pages, all with 4 healers (15 Oct 2026).
