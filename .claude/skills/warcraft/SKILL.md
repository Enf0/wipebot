---
name: warcraft
description: Use the local `warcraft` CLI as the root Warcraft data entrypoint when the source is unclear or the user may need more than one provider. Best for wrapper `search`, `resolve`, `doctor`, expansion-aware routing, and handing off to the right provider CLI.
---

# Warcraft

Use `warcraft` first when the caller does not already know which provider they need.

## Start Here

- Source unclear:
  - `warcraft resolve "<query>"`
  - if unresolved: `warcraft search "<query>"`
  - read `data.failed_providers` and `data.provider_warnings` on both: a provider that failed or
    warned (for example Icy Veins' stale sitemap) may be missing results
- Source known:
  - `warcraft <provider> ...`
- Version-specific request:
  - `warcraft --expansion <profile> ...`
- Global flags (every binary, always before the subcommand):
  - `--pretty` pretty-print JSON; default output is compact JSON
  - `--compact` truncate long prose strings, with `--compact-max-chars <n>` to set the cut; URLs,
    talent/transport strings, export codes and `*command`/`*input` values stay whole, and every cut path is
    listed in `provenance.compacted_paths`
  - `--fields <a.b,c>` keep only these dot paths, rooted at the envelope (`data.results`,
    `data.entity.name`); repeatable. The output is the projection, not an envelope, and a path that
    did not resolve is listed under `fields_missing` instead of vanishing. A path walks object keys
    only and stops at a list: `data.results` keeps the whole list, while `data.results.name` does
    not resolve (it lands in `fields_missing`). A failure envelope is always printed whole
  - `--fields-strict` fail with `missing_fields` (exit 2) instead of listing a path under `fields_missing`
  - `--profile agent|human` presets (`agent` is the default compact JSON, `human` pretty JSON)
  - `wowhead --stream` writes JSON Lines instead of one object (see Output Contract)
  - `warcraft --expansion <profile>` additionally restricts routing to expansion-aware providers
  - example: `warcraft --pretty --fields data.results search "<query>"`
- Trust check:
  - `warcraft doctor`
- Log actor to Raider.IO profile:
  - `warcraft actor-profile <report-code> <character-name> --fight-id <id>`
- Cross-provider guide evidence:
  - `warcraft talent-packet <source>`
  - `warcraft talent-describe <source> --apl-path <apl>`
  - `warcraft cooldown-packet <warcraftlogs-report-url> --actor-id <source-id> --phase <n>`
  - `warcraft guide-compare <bundle-a> <bundle-b>`
  - `warcraft guide-compare-query "<guide query>"`
  - `warcraft guide-compare-query "<guide query>" --simc-build-handoff --simc-apl-path <apl>`
  - `warcraft guide-builds-simc <bundle-or-orchestration-root>`
  - `warcraft guide-builds-simc <bundle-or-orchestration-root> --apl-path <apl>`

## Output Contract

Every binary emits one JSON object, the envelope: `ok`, `provider`, `command`, `kind`,
`schema_version`, `query`, `provenance`, and `data`, plus `error` on failure. Nothing else sits at
the top level; a binary refuses to print anything else and fails with `internal_error` instead.
Read the payload from `data`. `provenance.cache` tells you whether the answer was replayed from the
local cache: `hit` (any lookup was), `all_hits` (nothing came off the wire) and
`oldest_hit_age_seconds` (null whenever any hit came from Redis, which records no store time).
`errors` above 0 means the cache backend is broken (a Redis that is down or refuses the
credentials), not cold. With `--fields` request it explicitly, and treat its absence as unknown.
For a fresh answer, turn that provider's cache off for one run with `<PROVIDER>_CACHE_BACKEND=none`
(`RAIDERIO_CACHE_BACKEND=none raiderio character us illidan Name`; `ICY_VEINS_`, `WARCRAFT_WIKI_` and
so on for the others, one variable per provider a wrapper command reaches).

On failure the object goes to stderr with `ok: false`, `data: {}`, `query` echoing the parameters
the command parsed (`null` when it failed before parsing them), and
`error: {"code": ..., "message": ..., "details"?: ...}`. The process exit
code tells you what to do next:

| Exit | Meaning | Reaction |
| --- | --- | --- |
| `0` | success | continue |
| `1` | generic failure | read `error.code`; do not retry blindly |
| `2` | usage error (bad flag or argument) | fix the command |
| `3` | auth required or rejected | run the provider's `doctor` / `auth status` |
| `4` | target not found | try a different id or `resolve` again |
| `5` | network or upstream failure | back off and retry once |

Providers map some of their own codes onto the same exits (for example `simc` `unknown_talent`
exits 2, `curseforge` `missing_api_key` exits 3), so branch on the exit code and read `error.code`
for the detail. A traceback is always a bug — report it rather than parsing it.

`wowhead --stream` is the one opt-in exception to "one object": when the payload has a row list,
stdout is JSON Lines, a header line that is the envelope with that list emptied and
`data.stream: {"field", "count"}` naming it, then one `{"record": <row>}` line per row. Parse it line
by line.

## Provider Synopsis

Tiers are the support level to expect: **core** is deeply covered, **supported** is stable but
narrower, **experimental** is thin and may change.

| Provider | Tier | Best for | First commands |
| --- | --- | --- | --- |
| `wowhead` | core | entities, guides, comments, timelines, tool-state refs | `warcraft wowhead search ...`, `warcraft wowhead entity ...`, `warcraft wowhead guide ...` |
| `method` | supported | supported article/guide families with simple article structure | `warcraft method search ...`, `warcraft method guide ...` |
| `icy-veins` | supported | spec guides, hubs, guide subpages, and calculator builds converted to import strings | `warcraft icy-veins search ...`, `warcraft icy-veins guide ...` |
| `raiderio` | supported | character/guild profiles, Mythic+, sampled run analytics | `warcraft raiderio character ...`, `warcraft raiderio sample ...` |
| `warcraft-wiki` | supported | API docs, events, systems, lore, reference pages | `warcraft warcraft-wiki api ...`, `warcraft warcraft-wiki article ...` |
| `warcraftlogs` | core | official raid-log API, world metadata, guild/character/report lookups | `warcraftlogs doctor`, `warcraftlogs guild ...`, `warcraftlogs report-fights ...` |
| `simc` | core | local SimulationCraft inspection, exact-build priority analysis, APL comparison, and runs | `warcraft simc doctor`, `warcraft simc priority ...`, `warcraft simc compare-apls ...` |
| `raidbots` | experimental | reading shared Raidbots reports and bridging their SimC input to local `simc` | `warcraft raidbots inspect-report <url-or-id>`, `warcraft raidbots input <url-or-id>`, `warcraft raidbots explain-input` |
| `blizzard` | experimental; verified live for us/eu/kr/tw | official realms/items, PvP seasons/leaderboards/ratings, collections, and auction/commodity prices | `warcraft blizzard doctor`, `warcraft blizzard realm ...`, `warcraft blizzard character ...` |
| `curseforge` | experimental; verified live | World of Warcraft addon lookup: metadata, latest files, changelog | `warcraft curseforge doctor`, `warcraft curseforge addon <slug-or-id>` |
| `lorrgs` | supported | top-parse cooldown timelines, composition rankings, report overview handoffs, and static spec/boss/spell metadata | `warcraft lorrgs resolve ...`, `warcraft lorrgs spec-ranking ...` |

## Routing Rules

- Prefer `resolve` when you want one conservative next command.
- Prefer `search` when you want to inspect candidates across providers.
- In every provider `search` / `resolve` payload (`warcraft <provider> search|resolve`), `count` is the rows returned, `total_matches` is every match the provider knows of (`null` on a `coming_soon` stub) and `truncated` says more exist. A provider `resolve` is `resolved` only at `high` confidence; an unresolved answer still names its top row in `match`, with `fallback_search_command` beside it (`null` when nothing matched, and always `null` from Warcraft Logs, which matches only explicit report references).
- A global flag goes before the subcommand (`raiderio --pretty affixes`); typed after it, the error says so in `error.details.global_flag`. A group called without a subcommand lists them in `error.details.commands`.
- A one-word query (`shadow`, `illidan`) resolves at `high` only when the word names the top match: its whole name, its head before a `,` or `:` (`thunderfury` for "Thunderfury, Blessed Blade of the Windseeker"), or a provider identity such as an exact wiki title, an expansion alias, or a spec word only one class has naming that spec's guide. Anything else comes back `medium` with `data.confidence_cap: {"rule": "single_word_query", "from": "high"}`, even when it is right ("Illidan Stormrage"): check `match` and run its `follow_up.command` yourself, or add a word.
- An unresolved `warcraft resolve` has `match: null`: read `data.best_unresolved_candidate` (its `unresolved_reason`, `single_word_query_not_named_exactly` for the one-word rule, and `follow_up_command`) together with `data.fallback_search_command`, and check `data.provider_resolved_candidates`: a provider answer resolved at `high` that a better-ranked unresolved match kept from being the answer, each with its `next_command`. `warcraft search` keeps each provider's total in `data.merge_policy.provider_total_matches` (`null` for a stub or for a provider listed in `failed_providers`).
- Prefer `warcraft guild ...` for one guild's Raider.IO snapshot with normalized region/realm/name input (Oceanic realms are in region `us`; `oce` is read as `us`); `data.sources.raiderio` carries `summary` and `provenance` (use `warcraft raiderio guild` for the raw Raider.IO payload), and `summary.raids[]` carries each raid's normal/heroic/mythic world, region, and realm ranks. A rank of `0` means unranked at that difficulty, not first place, and Raider.IO only covers the current expansion.
- Use `warcraft actor-profile <report-code-or-url> <name>` to hand a Warcraft Logs report actor to their Raider.IO profile. Pass `--fight-id` when you know it; without it the wrapper searches a bounded set of the report's fights (`query.fight_scope`).
- Lorrgs boss composition matches with words beyond the boss name (`ulatek mythic`, `ulatek top`) stay unresolved at `medium` with `data.confidence_cap.rule: "words_beyond_boss_name"`. The wrapper reports `provider_did_not_resolve`; inspect the follow-up command and `provider_resolved_candidates`. A bare boss name or explicit Lorrgs URL can still resolve, while heroic, normal and LFR boss queries stay `low`.
- Blizzard PvP reads cover retail, progression Classic and Anniversary. Collections and commodity prices are retail-only; Classic auctions omit trade goods, so a trade good in `not_listed` does not prove nobody is selling it. Classic Era has no PvP seasons, and Era/Anniversary auction houses may return `not_found`. See `references/blizzard-api.md`.
- Preserve provider provenance. `warcraft` is a router, not a source.
- Use `warcraft guide-compare` when you already have exported guide bundles and want additive cross-provider evidence instead of a synthesized summary.
- Use `warcraft guide-compare-query` when you want the wrapper to resolve, export, and compare guide candidates conservatively across supported guide providers.
- `guide-compare-query` may use a provider search fallback only when the top guide result is clearly decisive; it should not guess across weak or ambiguous guide candidates.
- `guide-compare-query` should reuse prior orchestrated bundles only through explicit freshness rules like `--max-age-hours` and `--force-refresh`, not through invisible cache-like behavior.
- A `guide-compare-query` `provider_results` row (exported or reused) with a non-null `redirect` means that provider served another guide than the candidate it resolved (a retired page); the comparison uses the served guide.
- Steer `guide-compare-query` orchestration with:
  - `--provider <name>` repeatable, to restrict the run to `wowhead`, `method`, or `icy-veins`
  - `--out-root <dir>` to choose where the orchestrated bundles are written (default `<data root>/guide_compare/<query-slug>`, the `paths.data_root` that `warcraft doctor` reports, never the current directory)
  - `--max-age-hours <n>` (1-720, default 24) and `--force-refresh` for bundle reuse
  - `--simc-build-handoff` plus `--simc-apl-path <apl>`, `--simc-decode` / `--no-simc-decode`, and `--simc-build-limit <n>` (1-200, default 20) for the SimC handoff
  - example: `warcraft guide-compare-query "<guide query>" --provider wowhead --provider icy-veins --out-root ./tmp/guide-compare`
- Use `warcraft talent-packet` when the source is already an explicit build ref, scoped log actor, or packet file and you want the wrapper to route it into the shared transport contract.
- Use `warcraft talent-describe` when you want that same routed packet handed directly into `simc describe-build` without manually chaining commands.
- Use `warcraft cooldown-packet` for player-specific log questions like "how can I improve my
  cooldowns in P2"; it joins Lorrgs phase/spell/top-parse context with exact Warcraft Logs cast
  events for the selected actor and keeps both sources visible. For a report Lorrgs cannot serve
  it still returns the Warcraft Logs half with `data.lorrgs.status: "unavailable"`, the actor
  (`--actor-id` or `--actor-name`) and spec from the fight's Warcraft Logs roster, and phase windows
  from the Warcraft Logs fight (`data.phase.source` names where they came from); without an actor flag
  it fails `missing_actor` listing the roster. Externals the player did not cast (Power Infusion or
  Bloodlust from someone else) are not compared: they are in `data.cooldowns.received_auras`. One the
  player cast, such as a priest's own Power Infusion, is compared.
  Read `data.sources.*.graphql_warnings` and `data.cooldowns.player_casts.complete` before treating zero
  casts as absence. Partial errors or pagination make the comparison `partial`; `no_samples` means
  Lorrgs supplied no usable top-parse comparison evidence.
- Typical packet flow:
  - `warcraftlogs report-player-talents <report> --fight-id <id> --actor-id <id> --out ./tmp/actor-packet.json`
  - `simc validate-talent-transport --build-packet ./tmp/actor-packet.json --out ./tmp/actor-packet-validated.json`
  - `warcraft talent-describe ./tmp/actor-packet-validated.json --apl-path <apl>`
- Failure contract:
  - producer commands fail with `invalid_transport_packet` if they would otherwise emit malformed packet JSON
  - malformed Wowhead-like talent refs, including exact packet refs without a build code, fail with `invalid_tool_ref`
  - `simc identify-build|decode-build|describe-build|validate-talent-transport --build-packet <path>` fails with `invalid_build_packet` when the packet file is malformed; no other simc command takes `--build-packet`
  - wrapper routing preserves provider `invalid_transport_packet` failures instead of replacing them with a generic wrapper error
- Add `--simc-build-handoff` when you want the orchestration packet to include explicit guide build refs handed into `simc`; add `--simc-apl-path` when you also want exact-build `describe-build` output.
- Icy Veins calculator conversions are explicit `wow_talent_export` build refs with class/spec identity and `source.converted_from`. They can be handed to SimC on PvE and PvP pages; the import string omits PvP talents, so keep the calculator URL when those talents matter. Published import strings can still have unknown class/spec until SimC identifies them.
- Use `warcraft guide-builds-simc` when you want explicit guide build refs handed into `simc` without inferring claims from guide prose; the handoff packet includes provenance, citations, and source freshness so agents can tell how trustworthy the build inputs are. Branch on `summary.simc_handoff_status`: `ok`, `partial` (a leg worked for some builds), `failed` (a requested leg produced nothing, named in `summary.empty_requested_legs`), `no_build_references`, or `all_references_excluded` (build references existed but every one is in `excluded_builds`); when every requested leg produced nothing the command fails with `simc_handoff_failed` (exit 1).
- Add `--apl-path` when you want the wrapper to include exact-build `simc describe-build` output for those same explicit guide build refs.
- When `--expansion` matters, trust only the providers the wrapper says are included.
- Once the provider is known, switch to the provider CLI or the provider reference below.

## Read Next

- `wowhead`: see `references/wowhead.md`
- `method`: see `references/method.md`
- `icy-veins`: see `references/icy-veins.md`
- `raiderio`: see `references/raiderio.md`
- `warcraft-wiki`: see `references/warcraft-wiki.md`
- `warcraftlogs`: see `references/warcraftlogs.md`
- `simc`: see `references/simc.md`
- `raidbots`: see `references/raidbots.md`
- `blizzard`: see `references/blizzard-api.md`
- `curseforge`: see `references/curseforge.md`
- `lorrgs`: see `references/lorrgs.md`

## Notes

- This skill is the umbrella consumer skill.
- Keep provider details in the reference files so they can become standalone skills later without rewriting the root skill.
- Keep reference links portable: use relative paths like `references/simc.md`, not machine-specific absolute paths.
