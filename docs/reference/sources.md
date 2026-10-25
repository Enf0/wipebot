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
  "RSGG Addon" button exports the plan with that section's raiders' names on its spots; raidstrats.gg's own export
  (by hand or the API) is the plan as last saved. The NSRT note Viserio exports keeps a mechanic section's lists as
  `Name: raiders` lines but not the general Raidplan section's numbering (25 October 2026).
- Viserio's default plans (the Wowutils ones an assignment note links unless changed) can't be read with a guild
  officer's login: `addon-export` and `thumbnail` answer 403 `access_denied` ("This account cannot open that plan"),
  with or without a `public=true` parameter, though `POST /plans/{id}/embed` answers 200 for them (`private: false`,
  `guildPlan: false`). The plan REST API, with a key from raidstrats.gg's Discord, serves non-guild plans; without a
  key it stops at Cloudflare's bot check. A duplicate in
  the officer's account can, once saved on raidstrats.gg (until then 404 `addon_export_missing`), checked 25 October
  2026. A refresh with a wrong client secret answers 401 `invalid_client`; a refused login 400 `invalid_grant`.
- raidstrats.gg's API (`/api/oauth/v1`, its developers page) reads only as a person who logged in and approved the
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

- Every command prints one JSON envelope (`ok`, `data`, `error`). Wipebot runs commands as
  `python -c "from <module>.main import run; run()" ...` (`wowhead_cli`, `method_cli`, `lorrgs_cli`, ...).
- It keeps its own HTTP cache under `~/.cache/warcraft`.
- Lorrgs `spec-spells <class-spec>` gives cooldowns with spell ids and tags (`defensive`, `raid_cd`, `dps`,
  `tank`, `move`); filter `spell_type` to the class and spec to drop trinkets and racials.
- `wowhead guide-full <boss guide url>` (the url from `wowhead guides raids "<boss>"`; the row's title says
  "Raid Boss Guide" or "Raid Guide ... Boss Cheat Sheet") has the boss's abilities in `linked_entities` (73 spells
  for the Coiled Altar) and the strategy in `body.section_chunks[*]` (9k chars of text for Sszorak: the cheat
  sheet, the dungeon journal as `[html]` islands; `body.sections[*].text` is empty). `content_text` drops the
  linked names ("builds to a massive  that pushes"); `content_raw` keeps `[spell=123]` and the journal's
  `<a href="/spell=123">[Name]</a>`, so `knowledge.guides.wowhead_text` renders the raw markup with `linked_entities`'
  names. `wowhead entity spell <id>` gives clean tooltip text, but `tooltip.text` drops a spec's own clause
  (Death Grip's "[Blood: and forcing the enemy to attack you]", verified 2026-10-15), so `knowledge` reads
  `tooltip.html`.
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
