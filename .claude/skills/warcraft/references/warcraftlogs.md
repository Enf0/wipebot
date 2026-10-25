## When To Use

Use `warcraftlogs` when the user needs official Warcraft Logs API data instead of a guide or ranking-site summary.

Best fits:
- guild progression from the official source
- character identity lookups on the log platform
- report and fight inspection by report code or report URL
- world metadata like regions, servers, zones, and encounters

## Start Here

- health/auth:
  - `warcraftlogs doctor`
  - `warcraftlogs auth status`
  - `warcraftlogs auth client`
  - `warcraftlogs auth token`
  - `warcraftlogs auth whoami`
  - `warcraftlogs auth login --redirect-uri <uri>`
  - `warcraftlogs auth pkce-login --redirect-uri <uri>`
  - `warcraftlogs auth logout`
  - `warcraftlogs rate-limit`
- world metadata:
  - `warcraftlogs regions`
  - `warcraftlogs expansions`
  - `warcraftlogs server <region> <slug>`
  - `warcraftlogs zones`
  - `warcraftlogs zone <id>`
  - `warcraftlogs encounter <id>`
- direct lookup:
  - `warcraftlogs guild <region> <realm> <name>`
  - `warcraftlogs guild-members <region> <realm> <name>`
  - `warcraftlogs guild-attendance <region> <realm> <name>`
  - `warcraftlogs guild-rankings <region> <realm> <name>`
  - `warcraftlogs guild-reports <region> <realm> <name>`
  - `warcraftlogs character <region> <realm> <name>`
  - `warcraftlogs character-rankings <region> <realm> <name>`
  - `warcraftlogs encounter-rankings --zone-id ... --boss-id ...`
  - `warcraftlogs reports --guild-region ... --guild-realm ... --guild-name ...`
  - `warcraftlogs report <code>`
  - `warcraftlogs report-fights <code>`
  - `warcraftlogs report-player-details <code> --fight-id ...`
  - `warcraftlogs report-player-talents <report-url-or-code> --fight-id ... --actor-id ...`
  - `warcraftlogs report-master-data <code>`
  - `warcraftlogs report-events <code> --fight-id ...`
  - `warcraftlogs report-table <code> --data-type damage-done --fight-id ...`
  - `warcraftlogs report-graph <code> --data-type damage-done --fight-id ...`
  - `warcraftlogs report-rankings <code> --fight-id ...` (raid fights rank healers on hps and everyone else on dps, Mythic+ runs rank everyone on score; `--player-metric` sets one metric for every role)
  - `warcraftlogs graphql --query <query|@path|->`
  - `warcraftlogs report-encounter <report-url-or-code>`
  - `warcraftlogs report-encounter-players <report-url-or-code>`
  - `warcraftlogs report-encounter-casts <report-url-or-code>`
  - `warcraftlogs report-encounter-buffs <report-url-or-code>`
  - `warcraftlogs report-encounter-aura-summary <report-url-or-code> --ability-id ...`
  - `warcraftlogs report-encounter-aura-compare <report-url-or-code> --ability-id ... --left-window-start-ms ... --left-window-end-ms ... --right-window-start-ms ... --right-window-end-ms ...`
  - `warcraftlogs report-encounter-damage-source-summary <report-url-or-code>`
  - `warcraftlogs report-encounter-damage-target-summary <report-url-or-code>`
  - `warcraftlogs report-encounter-damage-breakdown <report-url-or-code>`
  - `warcraftlogs boss-kills --zone-id ... --boss-id ... --difficulty ...`
  - `warcraftlogs top-kills --zone-id ... --boss-id ... --difficulty ...`
  - `warcraftlogs spec-kill-samples --zone-id ... --boss-id ... --spec-name ...`
  - `warcraftlogs kill-time-distribution --zone-id ... --boss-id ... --difficulty ...`
  - `warcraftlogs boss-spec-usage --zone-id ... --boss-id ... --difficulty ...`
  - `warcraftlogs comp-samples --zone-id ... --boss-id ... --difficulty ...`
  - `warcraftlogs ability-usage-summary --zone-id ... --boss-id ... --difficulty ... --ability-id ...`

## Current Boundaries

- site profile selection is explicit: `warcraftlogs --site retail|classic|fresh ...`
- every report command takes a report URL (localized hosts such as `de.warcraftlogs.com` included) or a bare report code; anything else (empty, a non-Warcraft Logs host) is `invalid_query` (exit 2) before a request
- a URL's `#fight=N` (or a bare `CODE#fight=N` / `CODE?fight=N`) scopes `report-events`, `report-table`, `report-graph`, `report-player-details`, `report-rankings` and the `report-encounter*` commands when `--fight-id` is absent; an explicit `--fight-id` wins
- a report code only exists on its own site: `resolve`/`search` on a `classic.` or `fresh.` report URL return a follow-up command with that `--site`, and the `report-encounter*` commands fail with `invalid_query` (exit 2) when the URL's site is not the selected `--site`
- `server`, `guild*` (except `guild-reports`), `character` and `character-rankings` take a realm in any spelling (`Azjol-Nerub`, `azjolnerub`, `Mal'Ganis`) and try each slug spelling; `reports`/`guild-reports`, `--guild-realm` and `encounter-rankings --server-slug` send one slug, so pass the slug `server` reports (`azjolnerub`)
- public OAuth client credentials are the default auth mode
- manual user-auth groundwork now exists for authorization-code and PKCE exchange, plus saved user-token verification via `warcraftlogs auth whoami`
- current surface works both standalone and through the root `warcraft` wrapper, but wrapper discovery is still intentionally narrow
- `resolve` on a report URL is `resolved: true` with a `next_command`; on a bare report code it is `confidence: medium`, `resolved: false` and `next_command: null`, because the code is only judged by its shape. Run `match.follow_up.command` once you know the code is a report
- commands use typed payloads when available, with `graphql` as a raw official API escape hatch for explicitly scoped queries

## Inputs

- credentials are loaded in this order:
  - repo-local `.env.local`
  - XDG config: `~/.config/warcraft/providers/warcraftlogs.env`
  - process environment
- runtime auth state is stored separately:
  - `~/.local/state/warcraft/providers/warcraftlogs.json`
- required variables:
  - `WARCRAFTLOGS_CLIENT_ID`
  - `WARCRAFTLOGS_CLIENT_SECRET`
- region/realm inputs still benefit from normalized forms:
  - `us`
  - `illidan`
- regions are `us`, `eu`, `kr`, `tw`, `cn` (or an alias); `oce` reads `us`, and anything else fails
  with exit 2
- realm names work in their own script (`아즈샤라`, `Ревущий фьорд`) on `server`, `guild*` and
  `character*`; the single-slug flags (`--guild-realm`, `encounter-rankings --server-slug`) need
  the slug `server` reports
- `zones --expansion-id` takes Warcraft Logs' own expansion ids (`warcraftlogs expansions`; Midnight
  is 7, not Raider.IO's 11)
- a sampled command that matched no kill says so in `notes`: widen with `--report-pages` or use
  `encounter-rankings` for a late or rarely killed boss

## Good Consumer Workflows

- guild snapshot:
  - `warcraftlogs guild us illidan Liquid`
- Classic/Fresh site profile:
  - `warcraftlogs --site classic guild us mankrik <guild>`
  - `warcraftlogs --site fresh expansions`
  - `warcraft --expansion fresh warcraftlogs auth client`
- guild progress in a specific zone:
  - `warcraftlogs guild us illidan Liquid --zone-id 38`
- guild rankings in a specific zone:
  - `warcraftlogs guild-rankings us illidan Liquid --zone-id 38 --size 20 --difficulty 5`
- guild roster:
  - `warcraftlogs guild-members us illidan Liquid --limit 5`
- guild attendance history:
  - `warcraftlogs guild-attendance us illidan Liquid --limit 2`
- guild report history:
  - `warcraftlogs guild-reports us illidan Liquid --limit 10`
- character identity:
  - `warcraftlogs character us illidan Roguecane`
- character rankings, when the API allows them:
  - `warcraftlogs character-rankings us illidan Roguecane --zone-id 38 --difficulty 5 --metric dps --size 20`
  - `--spec-name` takes any provider's spelling or shorthand (`Beast Mastery`, `beast-mastery`, `hunter-beastmastery`, `bm`, `bm hunter`); Warcraft Logs would otherwise ignore it and return another spec's rankings, so on the retail site an unknown spec is `invalid_query` (exit 2); `--site classic`/`fresh` pass it through (Combat exists there)
- encounter rankings for real boss/class/spec leaderboard queries:
  - `warcraftlogs encounter-rankings --zone-id 46 --boss-id 3180 --difficulty mythic --class-name Druid --spec-name Balance --limit 10`
  - without `--metric` a Mythic+ zone is ranked on `playerscore`; in a raid zone a healer spec (Discipline, Holy, Mistweaver, Preservation, Restoration) is ranked on `hps` and every other spec on `dps`. `query.metric` names the metric that ranked the rows
  - `--class-name`/`--spec-name` take any provider's spelling or shorthand (`death-knight`, `Death Knight`, `dk`, `beast-mastery`, `bm`); the CLI sends Warcraft Logs' own `DeathKnight`/`BeastMastery`, and an unknown `--class-name` is `invalid_query` (exit 2) because Warcraft Logs would answer it unfiltered
  - Warcraft Logs needs a class with a spec here: a spec spelling that names one class (`bm hunter`, `fdk`, `ret`) supplies it, a bare `frost` or `holy` needs `--class-name`, and a spec of another class than `--class-name` is `invalid_query` (exit 2)
- guild report listing:
  - `warcraftlogs reports --guild-region us --guild-realm illidan --guild-name Liquid --limit 10`
  - pass all three guild flags or none, here and on the sampled commands; a partial guild scope is `invalid_query` (exit 2) because Warcraft Logs drops it and lists every guild's reports
- report inspection:
  - `warcraftlogs report <code>`
  - `warcraftlogs report-fights <code> --difficulty 5`
  - `warcraftlogs report-encounter 'https://www.warcraftlogs.com/reports/<code>#fight=47'`
  - `warcraftlogs report-encounter-players 'https://www.warcraftlogs.com/reports/<code>#fight=47'`
  - `warcraftlogs report-encounter-casts 'https://www.warcraftlogs.com/reports/<code>#fight=47' --preview-limit 20`
  - `warcraftlogs report-encounter-buffs 'https://www.warcraftlogs.com/reports/<code>#fight=47' --view-by source --preview-limit 20`
  - `warcraftlogs report-encounter-aura-summary 'https://www.warcraftlogs.com/reports/<code>#fight=47' --ability-id 20473 --window-start-ms 30000 --window-end-ms 90000`
  - `warcraftlogs report-encounter-aura-compare 'https://www.warcraftlogs.com/reports/<code>#fight=47' --ability-id 20473 --left-window-start-ms 30000 --left-window-end-ms 90000 --right-window-start-ms 90000 --right-window-end-ms 150000`
  - `warcraftlogs report-encounter-damage-source-summary 'https://www.warcraftlogs.com/reports/<code>#fight=47' --window-start-ms 30000 --window-end-ms 90000`
  - `warcraftlogs report-encounter-damage-target-summary 'https://www.warcraftlogs.com/reports/<code>#fight=47' --window-start-ms 30000 --window-end-ms 90000`
  - `warcraftlogs report-encounter-damage-breakdown 'https://www.warcraftlogs.com/reports/<code>#fight=47' --window-start-ms 30000 --window-end-ms 90000`
  - `warcraftlogs report-player-details <code> --fight-id 47`
  - `warcraftlogs report-player-talents <code> --fight-id 47 --actor-id 1739`
  - `warcraftlogs report-master-data <code> --actor-type Player`
  - `warcraftlogs report-events <code> --fight-id 47 --limit 100`
  - `warcraftlogs report-table <code> --data-type damage-done --fight-id 47`
  - `warcraftlogs report-graph <code> --data-type damage-done --fight-id 47`
  - `warcraftlogs report-rankings <code> --fight-id 47 --player-metric dps --timeframe historical --compare rankings`
  - `warcraftlogs graphql --query 'query Report($code: String!) { reportData { report(code: $code) { code title } } }' --report-code <code>`
  - `warcraftlogs graphql --query @./query.graphql --variables-json '{"code":"<code>"}' --operation-name Report`
- sampled cross-report analytics:
  - `warcraftlogs boss-kills --zone-id 53 --boss-id 3429 --difficulty 5 --limit 10`
  - `warcraftlogs top-kills --zone-id 53 --boss-name 'Coiled Altar' --difficulty 5 --limit 5`
  - `warcraftlogs spec-kill-samples --zone-id 53 --boss-id 3429 --difficulty 5 --spec-name Balance --limit 5`
  - `warcraftlogs kill-time-distribution --zone-id 53 --boss-id 3429 --difficulty 5 --bucket-seconds 30`
  - `warcraftlogs boss-spec-usage --zone-id 53 --boss-id 3429 --difficulty 5 --limit 10`
  - `warcraftlogs comp-samples --zone-id 53 --boss-id 3429 --difficulty 5 --limit 5`
  - `warcraftlogs ability-usage-summary --zone-id 53 --boss-id 3429 --difficulty 5 --ability-id 20473 --preview-limit 5`

## Notes

- prefer `warcraftlogs` when official log data matters more than convenience summaries
- use `raiderio` for its own ranking/profile strengths, not as a substitute for Warcraft Logs report data
- `guild-members`, `character` and `character-rankings` return `class_id` in Warcraft Logs' own numbering (1 Death Knight, 2 Druid, 3 Hunter, 4 Mage, 5 Monk, 6 Paladin, 7 Priest, 8 Rogue, 9 Shaman, 10 Warlock, 11 Warrior, 12 Demon Hunter, 13 Evoker), which is not Blizzard's; read `class_name` instead
- `character-rankings` can return a provider permission error or a provider-side failure for some characters; treat it as useful but less stable than `guild-rankings`
- `guild-members` depends on Warcraft Logs being able to verify the guild roster for that game; treat it as a retail-capable roster surface, not a universal promise across every future site profile
- `guild-attendance` is part of the official schema, but live public queries can still fail with a provider-side internal error; use it when it works, but do not assume the endpoint is fully stable
- `guild-reports` is the easiest official path when the user wants report history for one guild without manually shaping the broader `reports` query
- for one-fight analysis from a report link, prefer `report-encounter*` commands over manually combining `report-fights`, `report-player-details`, and `report-events`
- every `report-encounter*` command accepts `--allow-unlisted` for reports that are not publicly listed
- `report-encounter-casts`, `report-encounter-buffs`, and `report-encounter-damage-breakdown` support encounter-relative timeline filters:
  - `--window-start-ms`
  - `--window-end-ms`
- `report-encounter-casts` also includes additive `by_target` and `by_source_target` summaries for target-scoped cast analysis inside the selected fight/window; targets are named from report master data, including NPCs and pets, so bosses and adds come back by name; actor id `-1` is the no-target slot and Warcraft Logs names it `Environment`
- `report-encounter-casts` requests at most `--event-limit` cast events (default 200, max 10000; `--limit` still works) in one page: a busy fight overflows that easily, so check `casts.truncated` before reading any `by_*` count as a whole-fight total, and raise `--event-limit` or narrow `--window-start-ms`/`--window-end-ms` until it is `false`
- cast counts in `report-encounter-casts` and `ability-usage-summary` count only completed `cast` events: `begincast`, `empowerstart` and `empowerend` are skipped, so an empowered spell counts once per press, a cast-time spell once per finished cast, and a channel once when it starts; `casts.event_count` is the raw page size and `casts.cast_count` the counted casts
- aura rows name their actor by role, never `source`/`target`: `aura_holder` is the actor that had the aura, `applied_by` the actor that cast it; `buffs.row_actor`, `aura_summary.row_actor` and `comparison.row_actor` say which one the rows carry. `--view-by source` (the default) groups by holder and `--view-by target` by caster; `--source-id` pins the grouping actor (the holder under `--view-by source`, the caster under `--view-by target`) and the rows then name the other one, while `--target-id` filters the other actor. So "who gave Power Infusion" is `report-encounter-buffs --ability-id 10060 --view-by target` (rows are `applied_by`), and "who did Nyrolock get it from" is `report-encounter-aura-summary --ability-id 10060 --source-id <Nyrolock's actor id>`
- `report-encounter-buffs` returns typed `buffs.preview` rows with `aura` (with identity contract) and reported buff-table fields (`reported_total_uptime`, `reported_total_uses`, `reported_bands`); the unfiltered table is aura-aggregate, so the row actor comes back null there — pass `--ability-id` or use `report-encounter-aura-summary` for per-actor rows, whose `aura` is the requested ability; use `--preview-limit` to bound the row count and `buffs.preview_truncated` to detect truncation
- `report-encounter-aura-summary` is the narrower aura workflow: it requires one explicit `--ability-id` and returns typed `aura_holder` rows (or `applied_by` rows under `--source-id`) with the buff-table fields Warcraft Logs reports (`reported_total_uptime`, `reported_total_uses`, `reported_bands`) for that selected fight/window
- `report-encounter-aura-compare` is stricter still: same report, same fight, same aura, and two fully explicit windows; each `comparison.rows[]` entry carries left, right and right-minus-left values for `reported_total_uptime` (ms) and `reported_total_uses`, largest uptime change first; the windows may differ in length, so compare uptime against each window's `query.effective_window_duration_ms`
- `report-encounter-damage-source-summary` is the equivalent narrow damage workflow for source-grouped damage rows; use it when you want typed source identities without depending on the broader raw breakdown payload alone
- `report-encounter-damage-target-summary` is the target-grouped sibling; use it when the question is really about damage on explicit encounter targets or adds
- the damage and aura summaries return typed rows only; pass `--include-raw` to attach the untyped Warcraft Logs table entry per row (gear, pets, per-ability detail), or use `report-encounter-damage-breakdown` / `report-table` for the whole raw table
- those encounter-scoped commands echo the resolved report-relative window (`start_time`, `end_time`, `window_start_ms`, `window_end_ms`) in the envelope `query` block, so the agent does not have to derive report timestamps manually; `data.fight.start_time`/`end_time` are the fight's own bounds
- a window that runs past the end of the fight (or starts before it) is clamped to the fight: `query.effective_window_start_ms`/`effective_window_end_ms`/`effective_window_duration_ms` give the span the numbers really cover, `query.window_clamped` is `true`, and a note says so; divide uptime by `effective_window_duration_ms`, not the requested length
- `report-player-details` is the easiest way to inspect the participants in a report slice before deeper event/table work
- `report-player-talents` is the first narrow build-transport lane:
  - use it when you need one actor's selected talents for one explicit fight
  - it returns a scoped `talent_transport_packet` sourced from `combatant_info.talentTree`
  - for normal multi-fight reports, give it `--fight-id` or a report URL that already includes `?fight=<id>` or `#fight=<id>`
  - it only emits a packet when every selected talent-tree row is fully formed, and then keeps normalized raw `entry/node_id/rank` rows from the source tree as evidence
  - it never validates the build itself: the packet always comes back `transport_status: raw_only`, with `validation.reason: simc_backend_unavailable` because Warcraft Logs does not run SimulationCraft (or `missing_class_spec_identity` / `unsupported_actor_class` when the actor's class and spec do not resolve)
  - to get validated `simc_split_talents`, write the packet with `--out <path>` and run `simc validate-talent-transport --build-packet <path>`, or use `warcraft talent-packet` which chains both steps
  - in that validated packet the hero-tree selection node is resolved (tree `selection`, named after the hero tree) but never enters the split strings; every entry, including each entry of a tiered node that spreads its ranks over several entries, is compared rank by rank, and an entry whose rank could not be read back fails validation with `simc_round_trip_mismatch`; a talent row that repeats an entry (`reason: duplicate_entry` on the row) or has a negative rank (`reason: negative_rank`) leaves the packet unvalidated with `simc_trait_resolution_incomplete`, and rows from two hero trees leave it unvalidated with `multiple_hero_trees` (`hero_tree_ids`); entries SimC grants from a hero tree this build did not pick are listed under `validation.round_trip.ignored_unselected_hero_entries`
  - malformed or incomplete talent-tree rows fail with `missing_talent_tree` instead of emitting a partial packet
- `report-events`, `report-table`, `report-graph`, `report-rankings`, and `report-player-details` fail with `not_found` (exit 4) when any listed `--fight-id` is not in the report (or not on the given `--encounter-id`/`--difficulty`); `error.details.missing_fight_ids` names them, and the failure's `query` echoes the parsed flags
- `report-fights` is still the stable broad fight-list surface; use it to get fight IDs first, then move to `report-player-details`, `report-events`, `report-table`, or `report-graph` for deeper filtered analysis
- `report-table` and `report-graph` accept user-friendly enum filters like `damage-done` and normalize them for the API
- `graphql` is the raw official API escape hatch for queries not covered by typed commands; it keeps auth routing and the standard JSON envelope; `data` is the GraphQL result's own `data` object (`data.__schema` under `--introspect`), and partial errors are in `provenance.graphql_warnings`
- `graphql --query` accepts literal query text, `@path`, or `-` for stdin; use `--introspect` when the next step needs schema discovery
- `graphql` variables can come from `--variables-json` or repeatable `--var key=value`; `--var` wins on conflicts and values are JSON-coerced when possible
- `graphql` scoping helpers inject only declared variables: `--report-code` -> `code`, `--fight-id` -> `fightID` or `fightIDs`, `--encounter-id` -> `encounterID`, `--start-time` -> `startTime`, `--end-time` -> `endTime`, `--difficulty` -> `difficulty`, `--zone-id` -> `zoneID`, `--source-id` -> `sourceID`, `--target-id` -> `targetID`, `--ability-id` -> `abilityID`, and `--allow-unlisted` -> `allowUnlisted=true`
- `graphql --endpoint auto` uses saved user auth when available, `--endpoint client` forces client credentials, and `--endpoint user` forces saved user auth; `--cache-ttl` is opt-in for client-endpoint queries and user-endpoint raw queries are not cached
- `report-events` and `report-player-details` accept exactly the slice shapes Warcraft Logs answers: `--fight-id`, or both `--start-time` and `--end-time`; anything wider (including `--encounter-id` on its own, which filters a slice but does not define one) fails with `missing_scope` (exit 2) instead of returning an empty result
- `report-events`, `report-table`, `report-graph`, `report-rankings`, and `report-player-details` fail with `not_found` (exit 4) when a slice names a fight the report does not have — an unknown `--fight-id`, or an `--encounter-id`/`--difficulty` the report never pulled; the rejected slice is echoed in the failure envelope's `query`, and a request that names no fight at all is left alone because an empty answer to it is a real answer
- `report-player-details` additionally fails with `not_found` when its `--start-time`/`--end-time` window matches no fight, because a fight Warcraft Logs actually has always returns a roster, so an empty one means the slice missed rather than that the report has no players
- a `--start-time` after `--end-time`, or after every selected fight ended, is `invalid_query` (exit 2) on `report-events`, `report-table`, `report-graph`, and `report-player-details`
- `--difficulty` takes an id or a name: `lfr` = 1, `normal` = 3, `heroic` = 4, `mythic` = 5 (`warcraftlogs zone <id>` lists a zone's difficulties)
- the enum flags (`--hostility-type`, `--kill-type`, `--view-by`, `--data-type`, `--compare`, `--timeframe`, `--leaderboard`, `--hard-mode-level`) list their values in `--help`; any other value is `invalid_argument` (exit 2) with the valid values, before a request. A `--metric nope` that Warcraft Logs' schema rejects is `invalid_query` (exit 2); an unknown guild is `not_found` (exit 4)
- `--start-time`/`--end-time` on `reports`, `guild-reports` and the sampled commands take UNIX epoch milliseconds or an ISO-8601 date (`2026-09-01` or `20260901`); on `report-events`, `report-table`, `report-graph` and `report-player-details` they are milliseconds from the report's start
- the row cap is `--limit`; `--top` still works on `encounter-rankings`, `character-rankings` and the sampled commands
- `report-events` can still return `events: null` for some valid report slices; use it as a typed event-query surface, not a guarantee of non-empty data
- `report-events` returns one page: when `next_page_timestamp` is set the events stop there and a note says how to fetch the next page (the same filters plus `--start-time <next_page_timestamp>`; with `--fight-id` and no `--end-time` the page runs to the end of the selected fights); page to the end before counting events over a fight
- `report-rankings` can legitimately return zero rows for a valid public report slice
- `encounter-rankings` is the ranking surface to use when the user means boss/class/spec leaderboard results like "top Balance parses on Vanguard"
- `boss-kills`, `top-kills`, and `kill-time-distribution` are sampled cross-report analytics, not a promise that the CLI searched every possible public report
- `boss-kills` and `top-kills` do accept `--spec-name`, but on those sampled commands the filter means "keep sampled kills whose participants included that spec", not "return spec rankings"
- `boss-spec-usage` is also sampled cross-report analytics; it reports spec presence within the filtered kill cohort, not a site-wide meta snapshot; each row is one `class_name` + `spec_name` + `role`, so Frost Mage and Frost Death Knight are counted apart
- `--spec-name` on sampled commands takes the class too (`'Frost Mage'`, `frost-death-knight`, `deathknight-frost`, `fdk`); a bare spec name matches every class with that spec, and when it matched several the payload lists them in `sample.matched_spec_classes` and adds a note; on the retail site a name that is no spec, or a class without that spec (`'Frost Rogue'`), is `invalid_query` (exit 2) instead of an empty cohort
- `comp-samples` is sampled cross-report analytics too; it returns sampled kill rosters plus additive class-presence and exact class-signature summaries for that filtered cohort
- `spec-kill-samples` is the participant-cohort sibling of `boss-kills`: it requires `--spec-name`, returns the fastest sampled kills that contained that spec, and reports `sample.truncation_order` so the returned head is never mistaken for a random sample or a spec leaderboard
- `ability-usage-summary` is sampled cross-report analytics too; it reports explicit cast counts for one requested `--ability-id` across the filtered kill cohort; a kill whose events overflow `--event-limit` is counted in `sample.kills_with_truncated_events_count`, and `usage.total_casts_is_lower_bound` then says the totals are floors; it counts player-side casts only, so a boss ability reads zero (use `report-encounter-casts --hostility-type enemies` for boss casts)
- the sampled commands validate `--zone-id` and `--boss-id`/`--boss-name` against Warcraft Logs world data first, so a typo fails with `not_found` (exit 4) instead of returning an empty cohort
- the sampled commands collapse one real pull that several raiders logged into a single kill: same encounter, difficulty and raid size, and either the same guild with wall-clock start and end within 5 s, or the same set of players within 30 s (personal logs without a guild are matched this way); the collapse is reported, never silent — `sample.duplicates_removed` counts it, a note states the rule, and the kept kill's `duplicate_reports` cites the folded-in report codes and fight ids. A kill timed within 5 s of an earlier one with a different roster, where either report has no guild, is kept and marked `possible_duplicate_of`, counted in `sample.possible_duplicates`, with a note
- `sample.difficulty_counts` gives kills per difficulty; without `--difficulty` a cohort can mix difficulties, and a note says so because their kill times and frequencies are not comparable
- Mythic+ kill rows carry `fight.keystone_level` and `fight.keystone_time_ms` (the in-game key timer); `sample.keystone_level_counts` gives runs per key level, and a note warns when the cohort mixes levels, because the fastest-kill order ranks raw durations across them
- these sampled analytics commands include freshness and citation metadata for the sampled report cohort so agents can preserve trust boundaries when composing follow-up steps; `freshness.sampled_at` is when the command ran, and `freshness.cache_hit_count`/`upstream_request_count`/`served_entirely_from_cache` say whether the cohort was fetched live or replayed from cache
- those sampled analytics include kills from reports still being logged (a finished kill is final; each kill row says `report_finished`) and surface sample/truncation metadata instead of faking global certainty
- `warcraftlogs auth status` is the first place to check when auth looks wrong; it shows credential source and whether any persisted auth state exists
- `warcraftlogs auth login --redirect-uri ...` and `warcraftlogs auth pkce-login --redirect-uri ...` are two-step flows:
  - first run prints the authorize URL and saves pending state locally
  - second run exchanges the returned `code` and `state`
- `warcraftlogs auth whoami` is the clearest verification that a saved user token actually works against the private user endpoint
- pass both `--scope view-user-profile` and `--scope view-private-reports` when running `auth login` or `auth pkce-login`; `view-user-profile` alone resolves `userData.currentUser` but private and guild-stealth report queries still return `"The user did not grant your application permission to view this report."` until `view-private-reports` is also granted
- once a saved user token exists, every `warcraftlogs` command routes its GraphQL through `/api/v2/user` automatically, so private-report visibility is gated entirely on the saved token's scopes — verify with `warcraftlogs auth status` or `warcraftlogs auth token` (look for `scopes.has_view_user_profile` and `scopes.has_view_private_reports`)
- WCL does not echo the granted scope set in the OAuth token response (`token.scope` comes back `null`); the CLI parses scopes out of the JWT body's `scopes` claim, so `scopes.granted` reflects what was actually granted at the server
- the token-exchange step of `auth login` and `auth pkce-login` reports `scopes.granted`, `scopes.requested`, `scopes.has_view_user_profile`, `scopes.has_view_private_reports`, and a `scope_warning` so a missing scope is visible immediately
