# Game knowledge sources: verified facts

How the sites behind `knowledge/` behave (Wowhead, Method, Icy Veins, Lorrgs, Raidbots and raid planners, mostly
through [warcraft_cli](https://github.com/aurokin/warcraft_cli)). MCP clients: `mcp-clients.md`. The Warcraft Logs
API: `wcl-api.md`.

## The sites

- Icy Veins is read through warcraft_cli, like the other guide sites. `icy-veins resolve` can pick an old
  expansion's guide; `icy-veins search "<spec> guide"` with `content_family == "spec_guide"` finds the current one. A
  raid's boss guides are one family of pages: `guide-full <boss>-raid-guide` returns every boss, pick the page by its
  title.
- Guides' `build_references` hold WoW talent import strings (`wow_talent_export`); `knowledge.talents.decode_loadout`
  reads them against Raidbots' `fullNodeOrder`. Guide labels can be wrong (Icy Veins' "Fatebound Raid Build"
  is a Deathstalker string): the sub-tree choice in the string decides the hero tree.
- Blizzard's hotfixes are one running blue post ("... Hotfixes - <date>", US forum) on Wowhead's blue tracker;
  its `body_html` has dated paragraphs and nested lists (raid › boss, class › spec).
- Raidbots' generated game data (`https://www.raidbots.com/static/data/live/<file>`, `metadata.json` lists the
  files and the game build) isn't in warcraft_cli; `knowledge.raidbots` fetches it: `talents.json`,
  `flasks.json`, `augments.json`, `temp-enchants.json` (weapon oils by enchant id).
- Viserio Cooldowns' API (`api.wowutils.com/v1`, OpenAPI at `api.wowutils.com/openapi.json`) gives a note's and an
  assignment's metadata but not their text, nor the raid plans embedded in them (checked 25 October 2026), so
  officers paste the NSRT note it exports. A setup in full (`/groups/{id}/latest-reset/setups/{id}/full`) has, per
  boss, who's in for it with class, spec and role (`bossAssignments`), not who stands on which spot of a plan.
  Checked live 25 October 2026: a group without a boost has 60 points an hour (`X-RateLimit-Limit`), a read costs
  one and a `304` to `If-None-Match` nothing; `/groups` names the group's guild (`name`, `realm` slug, `region`,
  lowercase); a setup's `eventRef.eventId` is the calendar night it's for, and only its bosses with `enabled` are
  planned (a boss not enabled may list everyone out); `calendar-events?upcoming=true` gives each night's `date`,
  `startTime`, `endTime` and `signups` (`status` present, absent, late or tentative; `responded` false for a
  default). The API terms (wowutils.com/api-terms, 11 October 2026) allow a bot serving the key's group, and ask
  for no copy kept past 30 days, `Retry-After` respected and no battletag shown where its member hasn't agreed. A line's `time:` counts from the pull's start, through the
  intermissions. A `bossSpell:` id can be one the boss never casts: it labels the mechanic the cooldown is for.
- raidstrats.gg's planner Export (no key, any plan you can open) gives `raidstrats-addon-` and base64: one version
  byte (1), then raw deflate (`zlib.decompress(b[1:], -15)`) of JSON: `v` 2, `planName`, `raid`, `boss`, `planId`,
  `planLink`, `scenes[]` with `bg` and `items[]`. Each item has `kind` (`icon`, `boss`, `text`, `shape`), `label`,
  and `x`, `y`, `w`, `h` in % of the plan's width and height (x, y the top left corner). Icons are round, so an
  icon's `h / w` is the plan's width over its height. A player's `icon` is their role (`roles/rdps.svg`), the label
  the name as typed (often shortened, accents kept); a boss ability carries `spellId`. A side panel is a filled
  `rect` with icons and text inside, nothing marking it as one. A plan may be drawn on one side of a symmetric
  room and played on the other: fit it mirrored too.
- More of the export, verified on the guild's plans 25 October 2026: a player icon's `icon` is `classes/<class>`
  (`demonhunter`) or `roles/<role>.svg`, and `slotIndex` is its number on the plan; a raid marker is an icon
  `markers/<name>`; `boss` and `trash` items carry a portrait; an ability icon's `label` says what it's for ("Gloom 1",
  "Grim to star", a raider carrying it); a `triangle` has its three `corners`; a `line` (`shape` `line` or `arrow`)
  runs from `x1`, `y1` to `x2`, `y2`, except that an arrow attached to objects in the planner is exported with both
  ends at its start, so where it points is lost. Labels drawn on the plan in the planner may not be in the export
  until the plan is saved.
- Plans the guild uses are templates: Viserio's assignment note embeds a plan and fills each numbered spot with the
  raider assigned to that number, the same on every scene of the embed (`players=`), so a number must mean the same
  raider on every scene. Each embed (the general Raidplan section, a section per mechanic) has its own lists. Its
  "RSGG Addon" button exports the plan with that section's raiders' names on its spots (on the first scene only: the other
  scenes' spots keep their numbers with no name, 27 October 2026); raidstrats.gg's own export
  (by hand or the API) is the plan as last saved. The NSRT note Viserio exports keeps a mechanic section's lists as
  `Name: raiders` lines but not the general Raidplan section's numbering (25 October 2026).
- Viserio's default plans (the Wowutils ones an assignment note links unless changed) can't be read with a guild
  officer's login: `addon-export` and `thumbnail` answer 403 `access_denied` ("This account cannot open that plan"),
  with or without a `public=true` parameter, though `POST /plans/{id}/embed` answers 200 for them (`private: false`,
  `guildPlan: false`). A duplicate in
  the officer's account can, once saved on raidstrats.gg (until then 404 `addon_export_missing`), checked 25 October
  2026. A refresh with a wrong client secret answers 401 `invalid_client`; a refused login 400 `invalid_grant`.
  Sending the plan REST API's key along with the login (any header or parameter) changes none of this.
- raidstrats.gg's plan REST API (raidstrats.gg/embed-doc#addon-rest-api; a key from raidstrats.gg's Discord, sent
  as `Authorization: Bearer <key>`) reads any non-guild plan, Viserio's defaults included, with no login:
  `GET /api/plans/<id>/addon-export` gives `{ok, planId, planName, addonExport}`, the same `!raidstrats-addon-` text
  the OAuth API gives (`plan_export.parse` reads it); `GET /api/plans/<id>/thumbnail` gives `{ok, planId, planName,
  thumbnail}` (a cdn.raidstrats.gg image URL, or null): an 800×449 JPG of the plan's first scene only, also on a
  plan of 5 scenes. `POST /api/plans/addon-export` and `/api/plans/thumbnail`
  with `{"planIds": [...]}` (up to 50) give `{ok, count, okCount, results}`, each failure `{ok: false, planId, code,
  error}`. A guild plan is refused: 403 (batch: per item) `guild_plan`; an unknown id 404 `not_found`; a plan not
  saved since the export shipped 404 `addon_export_missing`; a wrong or missing key 401. Calls take about 100 ms,
  limit 400 a minute (`ratelimit-*` headers). A default plan's spots are numbered with no names: Viserio's
  assignment fills them (26 October 2026). Python's client gets through Cloudflare; `curl` gets the bot check.
- The link Viserio's assignment note embeds (Copy Link on the raidstrats.gg badge of a section's raid plan) is
  `raidstrats.gg/planner?view=<plan id>&players=...`, URL-encoded. raidstrats.gg/embed-doc (10 November 2026) gives
  `players` as `Name|Class` entries by embed index, the spot's number (`slotIndex` in the export): an entry with no
  number is the next index from 1, `10:Name|cls` index 10, `7,18:Name|cls` both on every scene, `5/7:Name|cls` index
  7 on scene 5 only (scenes from 1), a scene's entry before a global one; `names` is the same without classes, and
  `id` names the plan in an embed (`view` in a planner link, which may end `/<scene>`). Viserio writes classes as two
  letters (`mg`, `dk`, `ev`, `wl`, `pr`, `dr`, `sh`, `hu`) and full names, where its RSGG Addon export cuts names to
  four letters. Not checked live: whether an entry with no number after a numbered one counts on from that number
  (Wipebot counts only the entries with no number). The plan REST API, 10 November 2026: works again with the key
  and the User-Agent `Wipebot`, from a cloud host too. A Wowutils default plan fetched by its Viserio link (one
  batch call, 0.6 s) got the link's 12 raiders on the right spots in the room and the side panel.
- raidstrats.gg's API (`/api/oauth/v1`, its developers page; Wipebot used it until November 2026 and no longer
  does: a plan is saved by its export or its link) reads only as a person who logged in and approved the
  app: the authorization code flow, no `client_credentials` (`unsupported_grant_type`, 19 October 2026). Access
  tokens last an hour; the refresh token 30 days, replaced on each refresh. `/plans` lists `plans` (theirs) and
  `guildPlans` (each with an `embedUrl`), with `createdAt` and no time last saved. With the scope `addon`,
  `GET /plans/<id>/addon-export` gives `{id, name, addonExport}`, the planner's Export text with a `!` before it, and
  `POST /plans/addon-export` with `{"planIds": [...]}` (up to 50) gives `{ok, count, okCount, results}`, each result
  `{ok, id, name, addonExport}` or `{ok: false, id, error}` (`not_found`; `addon_export_missing` for a plan not
  saved since the export text was added). The export is the plan as last saved: a planner link with `&players=`
  (`Name|class` for each spot) shows and exports that roster in the browser, but the API's export keeps the saved
  labels until the plan is saved with that roster (Allow Editing, then save), verified 19 October 2026.
- warcraft_cli 0.6.0 needs `tzdata` on Windows (a Wipebot dependency for that reason).

## warcraft_cli (v0.6.0)

- Its own agent skill, for working on Wipebot: `.claude/skills/warcraft/` is warcraft_cli's `skills/warcraft/` at the
  tag Wipebot pins (copied with the owner's consent; its agent configs left out). Copy it again from the new tag
  whenever pyproject.toml's warcraft_cli version changes, since a newer one describes commands the pinned CLI lacks.
- Every command prints one JSON envelope (`ok`, `data`, `error`). Wipebot runs commands as
  `python -c "from <module>.main import run; run()" ...` (`wowhead_cli`, `method_cli`, `lorrgs_cli`, ...).
- It keeps its own HTTP cache under `~/.cache/warcraft`.
- It starts its requests to one host 0.25 s apart (`WARCRAFT_HTTP_MIN_INTERVAL_SECONDS`, since 0.6.0), within a
  process. Each guide-site command is a process of its own and keeps that pause (`wcl.cli_env` leaves out the
  shorter one Wipebot sets for Warcraft Logs: wcl-api.md, "Using the API efficiently"). A request answered 429,
  500, 502, 503 or 504, or not at all, is tried again (three tries by default; Wipebot gives Warcraft Logs two,
  `wcl.WCL_ATTEMPTS`), after the answer's `Retry-After` up to 30 s, else a backoff (`warcraft_api/http.py`).
- Lorrgs `spec-spells <class-spec>` gives cooldowns with spell ids and tags (`defensive`, `raid_cd`, `dps`,
  `tank`, `move`); filter `spell_type` to the class and spec to drop trinkets and racials.
- Lorrgs' `cooldown` is the spell's base cooldown and `duration` its base duration, and either may be 0 (Reincarnation
  has both 0; Ice Block and Earth Elemental have no duration). A raider's own cooldown is often shorter: on the
  recorded nights (October 2026) a Holy priest cast Divine Hymn 124 s apart against a base of 180 s, and an evoker
  Obsidian Scales 22 s apart (two charges, base 150 s). So "on cooldown" by the base number says too much: the raid
  note check takes the time between the player's closest two casts of the spell on the night where that is shorter,
  and "ready" keeps to the base number and to spells the player cast at least once that night (of the defensive and
  tank spells off cooldown at a callout on the recorded Sszorak night, a fifth had never been cast by that player on
  any pull: talents not taken and passives).
- A note may plan a spell again inside its cooldown, and the planned times alone don't show a shorter one: the
  recorded Sszorak note plans Stampeding Roar 73 to 102 s apart against a base of 120 s, the druid's closest two
  casts of it that night were 126 s apart, and the use planned at 1:40 was cast on none of the 11 pulls they were
  alive at it (on cooldown each time from the cast the note plans at 0:27).
- An NSRT note's lines, as the addon reads them (its Reminders.lua and InterruptDisplay.lua, November 2026): a field is
  found anywhere in a line (`time:` in seconds, decimals allowed; `ph:` a number, decimals allowed; `tag:`; `text:`;
  `spellid:`), and a timed line needs a time, a tag and a text or a spell. A tag is `everyone`, a character's name or
  nickname, a role (`tank`, `healer`, `damager`, `melee`, `ranged`), a spec or class id, or `groupN`. The lines
  between `intstart` and `intend` are the interrupt order, names in the order they kick; which add a line is for is
  the addon's rule per boss, not in the note.
- A note's `ph` is the addon's own count of a boss's phases, not Warcraft Logs' ids. The guild's Coiled Altar note
  (November 2026) has lines in ph 1, 2, 2.5 and 3, where Warcraft Logs lists Stage One, Stage Two, the intermission
  and Stage Three as ids 1 to 4; Nek'zali's ids go Stage One, the intermission, Stage Two. The raid note check lays
  `ph:N` on the phase every tool labels P<N> and `ph:N.5` on the intermission between P<N> and the next
  (`pulls.phase_labels`), with no table per boss. On the recorded pull into Stage Three (fk2z6bCw4xRLNvht pull 28:
  Stage Two at 2:27, the intermission at 6:08, Stage Three at 6:46) read by ids, the 82 lines of ph:3 lay 38 s
  early, in the intermission, and the 10 of ph:2.5 were not read.
- The addon starts a phase's reminders when the boss changes phase and cancels the ones still pending from the
  phase before (Reminders.lua, November 2026), so a line in ph:1 timed after the push never shows. The same note
  plans the push to Stage Two at 2:47; over the recorded night's 28 pulls it came sooner on 15, with 119 planned
  cooldowns of ph:1 timed after it (on pull 28 nine, of which five read 15 to 29 s early and three not cast). The
  check stops a phase's lines where the note's next phase begins, and only there: Sszorak's log goes P1, P2, P1
  while the guild's note has only ph:1 lines, which run to the pull's end.
- Where a boss's pulls go back to a phase or skip one in the log (Sszorak 1, 2, 1; the Entombed Sentinels 1, 2, 1,
  2; the Lost Explorers 1, 4, 1, 2, 1, 3 on the recorded reports, October 2026), the addon counts the fight its own
  way and Warcraft Logs' list can't say which phase a note's `ph:2` is. Neither can it for a number the list has
  no phase for (the addon counts Nek'zali 1, 1.5, 1.75, 2; the log has Stage One, the intermission and Stage Two).
  The check counts such lines as not checked and times none of them, so on those bosses a note with lines in later
  phases is checked up to the log's first change of phase and no further. A soak that lands after it, where the
  note calls the soak's groups in a phase that can't be placed, counts for no one as "can't tell if the note calls
  a soak then" (no recorded note has one; the test rewrites the Sszorak note's later lines as ph:2).
- Whether a note's order of soak callouts is the order the groups soak in depends on how far apart it calls them.
  The two recorded Twin Fangs notes (October 2026) call their three groups 2 s apart, and of the 185 hits that who
  was hit gave to a group over the two nights, 182 landed within 0.5 s of that group's callout; the other three
  came as pulls fell apart (two on every living player of all three groups, one in the first group's turn on four
  players of the other two). The Sszorak note calls its two sides at the same second or 1 s apart and the side that
  soaks first varies: of the 168 hits given to a side, both were called at the same second on 94, and on 51 of the
  other 74 the nearer callout was the other side's. So the check reads a turn from the note only where its groups
  are called 1.5 s or more apart, and only on a pull whose clear soaks each landed in their group's turn.
- `wowhead guide-full <boss guide url>` (the url from `wowhead guides raids "<boss>"`; the row's title says
  "Raid Boss Guide" or "Raid Guide ... Boss Cheat Sheet") has the boss's abilities in `linked_entities` (73 spells
  for the Coiled Altar) and the strategy in `body.section_chunks[*]` (9k chars of text for Sszorak: the cheat
  sheet, the dungeon journal as `[html]` islands; `body.sections[*].text` is empty). `content_text` drops the
  linked names ("builds to a massive  that pushes"); `content_raw` keeps `[spell=123]` and the journal's
  `<a href="/spell=123">[Name]</a>`, so `knowledge.guides.wowhead_text` renders the raw markup with `linked_entities`'
  names. `wowhead entity spell <id>` gives clean tooltip text, but `tooltip.text` drops a spec's own clause
  (Death Grip's "[Blood: and forcing the enemy to attack you]", verified 2026-10-15), so `knowledge` reads
  `tooltip.html`.
- `linked_entities` names only some of the spells: on the 17 boss pages of Midnight's raids as fetched in November
  2026, 88 of the 400 linked spells have `name` null (those linked only from the text: Gravebound, Toxic Deluge)
  and 30 have their whole journal entry as `name` (those linked around it). The links carry the name as Wowhead
  slugs it, `href="/spell=1286837/gravebound"`, for 117 of those 118, and a link's text is often the name
  ("[Gravebound]"; in prose it may be inflected, "[Manifestations of Dread]"). `knowledge.bosses._spell_names`
  names a spell by them without reading its tooltip: its names equal the tooltips' for 399 of the 400.
- The dungeon journal in that page (the `[html]` islands) marks each number of a spell's effects,
  `<!--pts1:0:0:<spell id>-->5<!---->`, and as fetched most damage and healing amounts are unscaled: "inflicting 5
  Shadow damage every 2 sec" where the tooltip says 192,517 (16 of about 32 amounts under 1,000 for the Coiled
  Altar, 23 of 25 for Sszorak); a few come as a formula, "(5 * 8) healing". Percentages, seconds, yards and counts
  carry the same marker and are right, so nothing tells a wrong amount from a right one: for a boss guide
  `wowhead_text(amounts=False)` leaves every damage and healing amount of the journal out (556 over the 18 boss
  pages of Midnight's raids as fetched in October 2026, none left), and the tooltips have them. Two alternatives
  the journal leaves unresolved come side by side in brackets, "[multiple][2] Manifestations of Dread", rendered
  "multiple or 2". `<!--difficulty:16-->...<!--difficulty-->` (15 by the "On Heroic difficulty" lines it wraps,
  16 Mythic) wraps parts of a sentence, not whole alternatives ("inflicting [16: 41670 Nature damage every 2 sec
  to all players for][15: 10 sec]"), so every part is kept.
- A source's "not found" is an answer (Lorrgs `boss-spells` for a boss it doesn't track: 404 "Invalid Boss";
  Method for a guide it hasn't written) and is kept as long as a found one (`knowledge.fetch.cli`), so it isn't
  asked again on every call.
- The same Wowhead boss guide holds the loot: a section titled "Gear" (Sszorak, Nek'zali, The Twin Fangs) or "Gear
  Drop Information for ..." (Kith'ix) with a `[table]` of `[item=id bonus=...]` and slot ("Leather Chest", "Mail
  Tier Set Token - Legs") and a table of item level per difficulty ("Raid FInder" or "LFR" 285 ... Mythic 324);
  toys, hearthstones and decor are under "More Rewards" / "All Other ... Rewards". `wowhead entity item <id>`
  gives the tooltip at its highest upgrade (Myth 6/6, 334 or 340, not the drop's level); its `text` drops what's
  in brackets ("+189 [Agility or Intellect]" becomes "+189"), so read `tooltip.html`.
- Spec gear pages: Wowhead's is a sub-page of the spec guide (navigation "BiS Gear", `.../bis-gear`); Method's
  "Gearing" page and Icy Veins' "...-gear-best-in-slot" page come in the guide's `guide-full` `pages`, with
  overall, raid and Mythic+ best-in-slot tables and a trinket section. Icy Veins' page `last_updated` can be
  older than its own changelog's last entry.
- Wowhead's class guide pages carry no date; `wowhead guides classes "<spec>" --sort updated --limit 100` lists
  every page of the spec (76 for Balance) with `id`, `url` and `last_updated`. A page fetched by URL has
  `guide.id` null, so match by URL.
- Icy Veins' search knows "sszorak" and "vashnik" but not "The Twin Fangs", "Vashnik the Malignant" or
  "The Venomous Abyss" (the leading "the" and epithets break it): search the boss's first word, then the raid
  name without "the" (`venomous abyss` finds the family). The family's page titles are short ("6 Vashnik",
  "7 Twin Fangs", "10 Kithix"); Nymrissa Wavecaller (a lair boss) has no Icy Veins page.
- Method's boss pages: Ula'tek has only `ulatek-heroic` (the Mythic url 404s); Nymrissa Wavecaller and Kith'ix
  have only the Mythic page. `method guide-full brewmaster-monk` (the slug `method resolve` gives) returns every
  page; `brewmaster-monk-guide` 404s.
- A guide's talent string (`wow_talent_export`) often carries both hero trees' picks (Method's Brewmaster "Raid
  Advanced / Offensive (Shado-pan)" decodes to 88 talents, 13 Shado-Pan and 13 Master of Harmony): the game keeps
  the other tree's picks in the loadout. The `subTreeNodes` entry picked (`traitSubTreeId`) names the chosen tree;
  `decode_loadout` drops the other tree's hero nodes.
- Wowhead's blue tracker lists the running hotfix post on its first page (`--pages 2` is plenty). Blizzard posts
  weekly-reset tuning as news first: on 2026-10-12 Wowhead had "Massive Mythic Coiled Altar Nerfs with Weekly
  Reset" that the running post lacked. `wowhead news "<about>" --date-from <day> --pages N` scans N listing
  pages of 20 posts (about a day and a half each; `--date-from` filters, it doesn't stop the scan);
  `wowhead news-post <url>` gives the article's text in `content.text` (its `sections` are empty).
- Raider.IO (`raiderio` module): `raids` lists this expansion's raids with slugs and encounters (a lair boss is a
  raid of its own: `the-tidebound-grotto` for Nymrissa, `the-unbinding-of-kithix`); `guild <region> <realm>
  "<Guild Name>"` (the display name; the slug 404s; the realm as slug or title) gives `raiding.progression`
  (`summary` "4/8 M") and `raiding.rankings` per raid and difficulty (world, region, realm; 0 when unranked).
  `leaderboard raids --raid <slug> --difficulty mythic --region eu [--realm <slug>] --page P --limit N` gives
  20-row pages from `--page` (0-based), `rank` relative to the scope, `encounters_defeated` (first/last kill)
  and `encounters_pulled` (`num_pulls`, `best_percent`, empty for a guild that doesn't upload logs); the
  profile's rank can lag the board by a place. Their cache TTL is 15 min.
- Method's `guide-full` returns every page of a guide in `pages`; Wowhead's returns one page plus
  navigation markup.
- Method has raid boss guides at `method.gg/guides/<raid-slug>/<boss-slug>` (Mythic) and `...-heroic`;
  warcraft_cli's `method search` doesn't index them, but `method guide-full <url>` reads them.
- `warcraft cooldown-packet <report#fight=N> --actor-name X --phase N` compares a player's cooldowns with
  Lorrgs' top parses; each sample also has the boss's casts in that phase (`selected_phase_boss_casts`).
