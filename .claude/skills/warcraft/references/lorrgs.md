# Lorrgs

**Tier: supported.** Lorrgs is a narrow provider. Prefer `warcraftlogs` for anything that must be
authoritative, and use Lorrgs for its prebuilt aggregation.

## Best For

- inspecting top-parse cooldown timelines by spec and boss
- seeing composition rows for an encounter
- finding Lorrgs spec, boss, spell, and zone slugs
- complementing `warcraftlogs` report reads with Lorrgs' prebuilt visual aggregation

## Start With

- readiness: `warcraft lorrgs doctor`
- discovery: `warcraft lorrgs search "frost mage chimaerus"`
- conservative handoff: `warcraft lorrgs resolve "https://www.warcraftlogs.com/reports/bG3xDYPqKjLm8XaR?fight=22&type=damage-done"`
- report overview: `warcraft lorrgs report-overview <warcraftlogs-report-url-or-code>`
- player phase cooldown packet:
  `warcraft cooldown-packet <warcraftlogs-report-url> --actor-id <source-id> --phase 2`
  (Lorrgs only serves reports it has already cached; for any other report add
  `--spec-slug <spec>`, which takes any provider's spelling: `frost-death-knight`, `BeastMastery`, `balance-druid`). For a report Lorrgs has not cached, also pass
  `--boss-slug <slug>` (see `warcraft lorrgs bosses`) or the top-parse comparison is skipped with
  `comparison.reason: no_boss_slug`.
- current season raids: `warcraft lorrgs current-season`
- spec slugs: `warcraft lorrgs specs`; `spec`, `spec-spells`, `spec-ranking`, `spec-ranking-info` and
  `comp-ranking --spec` also take other spellings (`balance-druid`, `Frost Death Knight`, `BeastMastery`)
  and send the Lorrgs slug. An unknown spec on `spec`, `spec-spells`, `spec-ranking` or `spec-ranking-info`
  stays `not_found` (exit 4) with the closest Lorrgs slugs in `error.details.suggestions`; an unknown
  `comp-ranking --spec` name is not an error upstream and returns `reports: []`
- boss slugs: `warcraft lorrgs bosses`
- cooldown timelines: `warcraft lorrgs spec-ranking mage-frost chimaerus-the-undreamt-god`
- lightweight ranking metadata: `warcraft lorrgs spec-ranking-info mage-frost chimaerus-the-undreamt-god`
- encounter composition rows: `warcraft lorrgs comp-ranking chimaerus-the-undreamt-god --limit 10`

## Effective Use

- use `spec-ranking-info` first when you only need freshness, difficulty, metric, or dirty status
- use `spec-ranking` when you need the actual report/fight/player/boss cast timelines
- use `warcraft cooldown-packet` when the question is about a specific player's cooldowns in a
  report phase; Lorrgs supplies phase markers, spell metadata, boss casts, and top-parse samples,
  while Warcraft Logs supplies exact player cast events
- `cooldown-packet` gets phase markers from Lorrgs only when Lorrgs cached the report, which most
  guild and private reports are not. It still returns the Warcraft Logs half with
  `data.lorrgs.status: "unavailable"` and the player's casts intact: the actor (`--actor-id` or
  `--actor-name`) and spec come from the fight's Warcraft Logs roster, and the boss for the top-parse
  comparison from the fight's encounter id. Phase windows then come from
  the Warcraft Logs fight's phase transitions (`data.phase.source: "warcraftlogs"`): windows are
  numbered P1, P2, ... in order, one per transition, each with the encounter
  phase's `phase_id` and `name`. Lorrgs numbers phases its own way, so the top parses are then
  segmented by their own Warcraft Logs transitions too; a sample whose window of that number is
  another encounter phase is left out (`phase_not_in_top_parse`). A fight without phase transitions leaves
  `data.phase.status: "unavailable"` and a null `data.phase.selected`. `data.lorrgs.message` names
  the reason and only says "no cached copy" for a `not_found`; a timeout or transport failure says
  so instead. Without an actor flag the command fails `missing_actor` and lists the roster in
  `error.details.available_players`; `--spec-slug` is needed only when the roster names no spec
- `cooldown-packet` leaves out externals the player did not cast (Power Infusion or Bloodlust from
  someone else): they are in `data.cooldowns.received_auras` instead, while one the player cast (a
  priest's own Power Infusion) is compared, and a top parse that is the
  analyzed fight itself is skipped (`comparison.excluded_analyzed_fight`)
- `cooldown-packet` top-parse samples often lack phase markers: those samples have
  `phase_available: false` (`phase_unavailable_reason: "top_parse_has_no_phase_markers"`) and are
  left out of `selected_phase_spell_frequency`; when no sample has the phase,
  `comparison.status` is `no_phase_data` and `comparison.reason` names why, so there is no
  top-parse comparison for that phase
- use `resolve` when you have a Lorrgs URL, Warcraft Logs report URL, report code, or likely
  spec/boss query and want the next command chosen conservatively
- when `resolve` answers `resolved: false` with `confidence: "low"`, read `candidates`: either two
  candidates tied (`match` is only the first of them), so re-ask with a spec slug or boss slug (`frost` matches Mage and Death Knight;
  `salhadaar` matches two encounters), or the top candidate left a recognised word in
  `ranking.unmatched_terms` and would have answered a narrower question than you asked
- name the difficulty in a `resolve` query (`heroic frost mage chimaerus`) and the handoff carries
  `--difficulty`; without one, `spec-ranking` answers for mythic
- `resolve` hands over `next_command` only at `confidence: "high"`. A partial word match (`storm`)
  or a report reference comes back with `resolved: false`, `confidence: "medium"`, and the candidate
  in `match`; its `match.follow_up.command` is the command to run if it is what you meant. A report
  reference carries a `caveat`: nothing checked that Lorrgs can serve that report, and it refuses
  reports Warcraft Logs keeps private
- a boss query with any word beyond the boss name (`ulatek mythic`, `ulatek top`) comes back
  `confidence: "medium"` with `confidence_cap.rule: "words_beyond_boss_name"` and the extra words in
  `confidence_cap.terms`: `resolve` does not fetch the composition ranking, which can be empty for a
  new boss. Run `match.follow_up.command` and check `reports` and `notes` before treating it as the
  answer; the bare boss name (`ulatek`) or a Lorrgs `comp_ranking` URL still resolves. A heroic,
  normal, or LFR boss query (`ulatek heroic`) comes back `low`: `comp-ranking` takes no difficulty
- use `report-overview` for report metadata from any public Warcraft Logs URL, including one Lorrgs
  has not cached, without requesting Lorrgs' per-fight/player timeline generation
- an empty `comp-ranking` or `spec-ranking` (`reports: []`) carries `data.notes`: Lorrgs has no
  ranked rows for that boss (and spec or filters) yet, which is not a ranking and not evidence the
  spec is unplayed there
- use `user-report-fights <url> --type <report-type>` when the report URL carries a view type such
  as `damage-done`; the CLI also preserves that query parameter automatically from URLs
- use `spec-spells` and `boss-spells` to interpret spell ids in timeline rows
- use `comp-ranking` filters (`--role`, `--spec`, `--kill-time-min`, `--kill-time-max`; 0 = no bound) when you need
  a narrower comparison cohort; `--role` and `--spec` take `<name>.<op>.<n>` with `op` one of `eq`,
  `gt`, `gte`, `lt`, `lte` (`--role heal.gte.4`, `--spec mage-frost.gte.1`); a role is `tank`, `heal`,
  `mdps` or `rdps`, and a spec is a slug from `lorrgs specs`
- Lorrgs ranks Mythic and Heroic only: `--difficulty` takes `mythic` or `heroic`
- every result preserves `provenance.source_url`; follow that exact URL when you need to verify the
  source payload

## Boundaries

- user-report commands read already-cached Lorrgs records only
- queued load/dirty endpoints are intentionally not exposed
- do not turn top-parse timelines into universal cooldown recommendations without checking fight
  duration, phase timing, strategy, composition, and externals
- registered as fixed retail data for wrapper expansion filtering
