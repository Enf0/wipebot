# Raider.IO

## Best For

- character and guild profile lookup
- guild raid rankings (world, region, or realm) for a raid and difficulty
- Mythic+ runs (`raiderio leaderboard mythic-plus`)
- sample-backed Mythic+ analytics

## Start With

- exact character: `raiderio character <region> <realm> <name>`
- exact guild: `raiderio guild <region> <realm> <name>`
- discovery: `raiderio search "<query>"`
- conservative match: `raiderio resolve "<query>"`
- raid slugs for the current expansion: `raiderio raids` (add `--expansion-id 10` for The War Within, `9` for Dragonflight)
- guild raid leaderboard: `raiderio leaderboard raids --raid <slug> --difficulty mythic --region us --limit 20`
- this week's Mythic+ affixes: `raiderio affixes --region eu`
- the season's Mythic+ dungeon pool and `--dungeon` slugs: `raiderio dungeons`
- the Mythic+ rating it takes to reach the top 0.1%, 1%, 10%, 25% or 40% of a region: `raiderio cutoffs --region us`

## Effective Use

- prefer structured queries like `character us illidan Roguecane`; multi-word realms work too (`character eu tarren mill Cotti`)
- the `guild`/`character` word only counts as a hint when it leads the query; anywhere else it is part of the name (`resolve "guild us malganis Old Guild Order"`, `search "Liquid Guild"`)
- pass `--kind character` or `--kind guild` when only one kind will do: it overrides the query word, and an empty result means nothing of that kind matched
- discover the `--raid` slug from `raiderio raids` (or a guild profile's progression) before asking for raid rankings; slugs change every tier
- the raid a guild is currently progressing is the `raiderio raids` row whose per-region `starts`/`ends` window covers now; join it to the guild payload's progression rows by `raid_slug`
- add `--realm` with a standard `--region` (`us`, `eu`, `kr`, `tw`, `cn`, or an alias such as `na`) to answer "where does this guild rank on its realm"; `rank` is then the realm position and `region_rank` the region position
- realms may be written as a display name or a slug (`Tarren Mill`, `tarren-mill`, `mal'ganis`, `malganis`) anywhere a realm is accepted
- read `sample.limit_reached` on raid leaderboards: `false` means the scope has fewer ranked guilds than you asked for
- a rank of `0` in guild rankings (or in a character's `mythic_plus.ranks`) means unranked, not first place
- `freshness.fetched_at` is when the data came off the wire and `freshness.cache_hit` says whether it was replayed from cache, so quote the fetch time rather than the time you ran the command
- for a profile's identity, guild, spec and score, quote `character.last_crawled_at` (or `guild.last_crawled_at`): when Raider.IO last read it from Blizzard, which can be months old for an inactive character even on a fresh fetch
- use `sample mythic-plus-runs` and `distribution mythic-plus-runs` for analytics questions
- use `sample mythic-plus-players` and `distribution mythic-plus-players` when you need participant-level slices instead of raw run rows
- narrow sampled analytics with filters like `--level-min`, `--contains-spec`, and `--player-region` when you need a tighter slice; `--contains-role` takes `tank`, `healer` or `dps`, `--contains-class`/`--contains-spec` take Raider.IO slugs (`death-knight`, `beast-mastery`, `priest-holy`) or any other provider's spelling (`DeathKnight`, `BeastMastery`, `deathknight-frost`, `balance-druid`), and an unknown role, class, spec or region fails with exit 2 instead of returning an empty sample
- `--limit` sets how many runs a sample reads (in 20-run pages), so raise `--limit` rather than `--pages` for a bigger sample
- `--page` counts from 0 here (`--page 1` starts at rank 21), unlike `wowhead` and `warcraftlogs`
- an unknown `--season` or `--raid` slug is `invalid_query` (exit 2), not something to retry; `raiderio dungeons` lists season slugs and `raiderio raids` raid slugs
- Raider.IO numbers expansions 11 = Midnight, 10 = The War Within; Warcraft Logs uses 7 for Midnight
- a character's best key per dungeon this season is `mythic_plus.best_runs` on `raiderio character` (one row per completed dungeon, `num_chests` 0 = not timed); `recent_runs` lists every recent run Raider.IO returns; each run's `spec`/`role` is what was played in that run, which need not be the active spec
- in `character` and `guild` payloads `realm` is the slug and `realm_name` the display name, as in search rows
- `raiderio guild` returns the roster highest guild rank first in `roster_preview` (10 by default; `--roster-limit 1000` for the whole tracked roster), each with `rank`, class, active spec and `active_spec_role` (`tank`, `healer` or `dps`), so count tanks and healers from it; `roster_truncated` says more exist, and `guild.member_count` counts only members Raider.IO tracks
- on `leaderboard raids` rows, `null` pulls or best percents with `privacy.raid_pulls_hidden`/`raid_percents_hidden` true mean the guild hid them
- the current dungeon pool is the main season in `raiderio dungeons` whose `starts`/`ends` window covers now
- a run was timed when `num_chests` is above 0; `logged_run_id` is a Raider.IO id, not a Warcraft Logs report code
- spec labels in analytics are class-qualified (`priest-holy`, `paladin-holy`) because spec names repeat across classes; pass `--contains-spec priest-holy` to select one class's spec, since `--contains-class` and `--contains-spec` each match any roster entry on their own
- use `threshold mythic-plus-runs` for sampled estimates around a single run's score or key level; a run score (about 500 at the top) is not a player's Mythic+ rating, so answer "what rating is top 1%" with `raiderio cutoffs`; leaderboard samples cover only the top of the ladder, so a target outside `threshold.sampled_range` comes back with `out_of_sample_range: true`, `estimate: null` and a `note` instead of a guess
- treat the analytics outputs as sampled leaderboard-derived summaries, not authoritative universal truths
- check the filtering counts when you narrow a slice so you do not over-trust a tiny sample
- check player truncation metadata when you use `--player-limit`, so you know whether you are looking at the full deduped participant set

## Boundaries

- strongest today on Mythic+ and profile data
- not the right primary source for raid-boss comp recommendations
