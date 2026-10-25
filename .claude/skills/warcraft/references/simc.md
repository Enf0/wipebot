# SimulationCraft

## Best For

- local repo inspection
- readonly APL analysis
- build decoding
- controlled local sim execution

## Start With

- readiness: `simc doctor`
- repo state: `simc repo`
- managed checkout: `simc checkout`
- upstream safety: `simc verify-clean`
- build identification: `simc identify-build`
- build summary: `simc describe-build`
- source inspection: `simc spec-files ...`, `simc apl-lists ...`, `simc apl-talents ...`
- reasoning: `simc priority ...`, `simc inactive-actions ...`, `simc opener ...`, `simc analysis-packet ...`
- direct sim runs: `simc sim ...`
- APL comparison: `simc build-harness ...`, `simc validate-apl ...`, `simc compare-apls ...`
- talent comparison: `simc compare-builds --base ... --other ...`
- talent modification: `simc modify-build --talents ... --swap-class-tree-from ... --add ... --remove ...`
- low-level execution: `simc run ...`

## Effective Use

- prefer readonly APL inspection before jumping to a real sim run
- if the user provides a talent string, import string, or Wowhead talent-calc URL with build code, assume they want the exact build only; use `describe-build` first for “what is this build doing?” requests, then use `priority` or `inactive-actions` with the same `--talents` value when you need finer evidence (`--build-packet` is accepted only by `describe-build`, `decode-build`, `identify-build`, and `validate-talent-transport`; for the other exact-build commands pass the packet's `simc_split_talents` strings as `--class-talents` / `--spec-talents` / `--hero-talents`)
- users may paste:
  - a bare WoW talent export string (this is what Method and Icy Veins guides publish; it names no class or spec, so the CLI identifies it by decoding it as each spec SimC knows)
  - a Wowhead talent-calc URL with build code, which names the class and spec by itself
  - a Wowhead `/talent-calc/blizzard/<hash>` URL, which is what `modify-build` publishes
  - SimC-native build/profile text
  `identify-build`, `describe-build`, and `decode-build` report `source_kind`, resolved class/spec, and the normalized generated profile so you can verify the handoff before reasoning from it
- any other link (a guide page, an article, an addon export site) fails with `unsupported_build_reference` (exit 2) naming what it recognized under `error.details.reference_type`; do not retry it as a talent string. A Classic-era Wowhead calculator build (`/classic/`, `/cata/`, `/mop-classic/` ...) fails the same way with `wowhead_talent_calc_url_non_retail`: SimC decodes retail builds only (retail `/ptr/` and `/beta/` calculator URLs decode normally)
- for exact-build commands, `--talents` is now safe for the same common consumer inputs as `--build-text`, including bare WoW exports and Wowhead talent-calc URLs with build codes
- spec APLs live in the checkout's `ActionPriorityLists/default/` (`monk_brewmaster.simc`, `mage_frost.simc`); a bare file name like `monk_brewmaster.simc` reads the file there
- a build read against another spec's APL (`--apl-path monk_brewmaster.simc` for a mistweaver build, or a Frost talent-calc URL with `mage_fire.simc`) fails with `invalid_query` instead of describing the wrong rotation. The build's spec is the one its hash decodes as, not a talent-calc URL path the hash contradicts
- a talent string SimC rejects fails with `invalid_build` and SimC's own error line; it is never reported as a partial build. The envelope names the binary that rejected it under `error.details.simc_binary`, and when that binary is older than the checkout the message says so and asks for a rebuild; `simc doctor` reports the same mismatch under `repo.build_issues`
- decoded builds name the active hero tree under `hero_tree`; talents from the other hero tree are listed under `inactive_hero_talents` and are not part of the build. A hash with no hero tree selected at all has every hero talent there
- a split option combined with a hash (`--talents <hash> --class-talents shimmer:0`) overwrites that talent's rank as SimC does, and a rank above the maximum is clamped; a profile with several actors is read for its first actor only, named in `source_notes`
- a hash that decodes as no spec although its header names one fails `invalid_build` with SimC's reason: the export predates the current talent tree, so ask for a fresh export instead of a class and spec
- a tiered node comes back as one row per entry, each with its own rank; a row with `rank_known: false` is taken at a rank the decode could not recover, so do not quote a rank for it
- an empty value for a build-input option is a usage error, not the same as omitting the option
- `--enable` / `--disable` take a talent's display name or its SimC token; a value that names no talent of the actor's class fails with `unknown_talent` (exit 2) rather than being ignored
- do not tell the user they must provide class/spec unless `identify-build` could not identify the build first; the CLI decodes a bare WoW export as every spec SimC knows, healers included, when direct metadata is missing. `identify-build` itself never fails on an unidentifiable build: it answers `ok: true` with `identity.confidence: "none"` (no spec decodes it) or `"low"` (several do, listed in `identity.candidates`), null `actor_class`/`spec`, and a source note naming how many specs were probed, so check `identity.confidence` before treating it as identified. Commands that need the spec (`decode-build`, `describe-build`) fail with `invalid_query` in that case (`error.details.identity.candidates` lists the matches); then pass `--actor-class` / `--spec`. `identify-build` with an explicit class and spec the hash does not decode as returns `confidence: none`; say the build is not that spec. `identify_failed` (exit 1) means the SimC checkout or binary is broken (missing, not executable, or crashed), not the build. An `--actor-class` alone narrows the probe to that class, and the message names the specs it tried. Class and spec are case-insensitive and take the same shorthand as the other providers (`Death Knight` and `dk` mean `deathknight`, `bm` means `beast_mastery`, and `--spec bm` alone means hunter); a class or spec SimC has no spec for, or a pair such as `mage holy` or `mage fdk`, fails with `invalid_query` (exit 2) listing the valid values. The class and spec an APL file name or a Wowhead talent-calc URL path suggests are only a guess: a talent hash is decoded against them once, and a guess the hash contradicts is ignored with an `ignored apl name` / `ignored talent-calc url path` source note while the probe identifies the build. A name that is no SimC class/spec pair (a renamed `mage_arcane_variant.simc`) is ignored with an `ignored apl name` source note, never an error on its own; with `--enable`/`--disable` and no talents such a file fails with `unknown_talent` because no class is known, so pass `--actor-class` and `--spec`
- prefer `describe-build` over ad hoc prose synthesis when you need to talk about:
  - active hero/spec package
  - skipped capstones or alternate branches
  - ST vs AoE shape changes
  - dispatcher-to-leaf focus changes exposed through `focus_path`
- use `apl-prune`, `apl-branch-trace`, and `apl-intent` for conservative flow reasoning; a line is `dead` only when the build's talents, ranks, hero tree or target count prove it, so treat `possible` / `unknown` as runtime-dependent, not as inactive. A line whose action is itself a talent the build did not take is `dead` with the reason `talent.<action>=false [action]`
- use `priority` as the default build-scoped priority view; it follows the build into the list where its rotation lives (`focus_path`) and reports `total` and `truncated`, so raise `--limit` when `truncated` is true instead of calling the list complete
- a `--list` the APL has no action list of fails with `not_found`, and `error.details.available_lists` names the real ones
- a relative APL path is read from the current directory when the file is there, otherwise from the SimC checkout
- use `inactive-actions` when you need to prove a shared APL branch is not active for the current build; by default it lists only lines the build's talents or hero tree kill, so pass `--all-dead` to include lines dead only at this target count
- `apl-branch-compare` reports a rotation switch through `call_action_list` as well as `run_action_list`: read `dispatch_changed` and `decision_changes`
- use `opener` for a static early-action preview, then escalate to `first-cast` if runtime confirmation matters
- use `analysis-packet` when you want an agent-facing summary instead of assembling outputs manually
- use `first-cast` and `log-actions` when static analysis is not enough and you need runtime confirmation. `first-cast` times only the profile's first actor, never a pet's cast of the same name; `log-actions` names the `actor` of each hit (a pet reads `<player>_<pet>`), and `--actor NAME` keeps only that actor's lines
- use `sim` as the default consumer run path:
  - `simc sim ./profile.simc`
  - `cat ./profile.simc | simc sim -`
  - it always reports run settings, runtime, and core output metrics; `metrics.dps_error` is SimC's confidence-interval half-width around mean DPS and `run_settings.target_error_percent` is that error as a percent of DPS, so quote DPS with it
  - a profile with several actors reports the rest under `other_actors` (`actor_count` is the total), and a profile with profilesets (Top Gear / Droptimizer input) reports their ranked rows under `profilesets`
  - for stat weights, add `calculate_scale_factors=1` (and optionally `scale_only=intellect,crit_rating,haste_rating,mastery_rating,versatility_rating`) to the profile; `scale_factors.factors` holds DPS per point of each stat and `scale_factors.deltas` the amount each was measured with. A stat outside `scale_only` reads 0, meaning not measured. Without the line `scale_factors` is null
  - `run_settings.iterations_completed` is the sampled count, one less than requested because SimC discards the first iteration
  - the preset (1000 or 5000 iterations, `max_time=300`) fills only settings the profile leaves unset: a Raidbots input with its own `iterations`/`target_error`/`max_time` runs as written. `--iterations` and `--max-time` always win; `disclosures` says which profile settings were kept or replaced
  - each `profilesets` row carries SimC's `mean_error`; two rows closer than that are a tie, so do not rank them
- `simc run` returns SimC's headline lines under `result_lines`: each `Player:` / `Target:` header followed by its `DPS=` / `DTPS=` lines, so a `DTPS=` under `Target:` is the target's; its output previews are the last 20 lines
- `sim` and `run` fail with `missing_binary` when the checkout has no built binary; run `simc build`
- `sim` and `run` fail `invalid_query` (exit 2) on a profile SimC finds nothing to sim in (an empty file or no actor line) and `not_found` (exit 4) on a path that is not a file
- `compare-apls` labels must be unique plain file names (they name the files each variant writes), otherwise it fails with `invalid_query`
- `compare-apls` ranks variants on mean DPS; `action_counts` are SimC's mean executes per iteration of each player action (procs and triggered spells included, pets not) and `action_cpm` divides them by the mean fight length, so `top_action_deltas` are averages over every iteration
- `validate-apl` and the `compare-apls` validations list SimC's `Warning:` lines under `warnings`; a warning means SimC ignored part of the APL (a mistyped condition makes the action unconditional), so `valid` is false and `compare-apls` stops. A missing harness or APL fails `not_found` (exit 4)
- `spec-files` matches every word of the query against file names, so `simc spec-files "frost mage"` finds `mage_frost.simc`
- `spec-files`, `find-action`, and `trace-action` need ripgrep; without it they fail with `missing_dependency` and `simc doctor` marks them `unavailable`. Pointed at a directory that is not a SimulationCraft checkout they fail with `not_found` (exit 4) rather than reporting zero hits
- `find-action` finds a spell in the spell dumps by token (`rising_sun_kick`) or display name (`"Rising Sun Kick"`); `--class` (any spelling, including `dk` / `dh`) narrows the class-module and spell-dump hits to that class (the APL buckets still search every class) and fails with `invalid_query` for an unknown class
- use `compare-builds` to diff talent selections between two or more builds by tree; this is the right tool when the user asks "what changed between these two builds?"
  - `summary.failed` counts the `--other` builds SimC rejected; each keeps its `error` in `comparisons`, so say which comparisons are missing. When none decode the command fails instead
- use `modify-build` to produce a new talent export string from an existing build:
  - `--swap-class-tree-from` / `--swap-spec-tree-from` / `--swap-hero-tree-from` replace an entire tree from another build of the same spec; another spec's build fails with `invalid_query`
  - `--add name:rank` and `--remove name` adjust individual talents in any tree (names follow SimC's tokens, so "Anti-Magic Zone" works as written); a name or entry id must be a talent the build's spec can take, otherwise it fails with `unknown_talent` (exit 2). A hero talent counts when the spec can select its hero tree. Healer builds can be modified too
  - at least one `--swap-*-tree-from`, `--add` or `--remove` is required; without one the command fails with `invalid_argument` (exit 2)
  - the output includes the new WoW export string, a Wowhead URL, a diff from the base build, and `verified: true`, which means every requested edit is in the export and nothing else changed in the active trees; it does not mean the game will import it
  - when re-encoding changes anything in the active trees that was not requested, or a requested edit is not in the export at the requested rank (SimC silently clamps a rank above the maximum), the command fails with `encode_mismatch` and no export; do not retry, report the listed `unrequested_changes` and `unapplied_edits`. A swapped tree is checked against the build it came from, not the base
  - an `--add` on a choice node whose other entry the build takes fails with `invalid_argument` (exit 2) naming the talent; rerun with the `--remove` the message gives to swap the two
  - SimC enforces no point budget or node prerequisites: when an add makes a tree spend more points than the base build, `result.disclosures` says the game may refuse the export, so tell the user which talent to drop
  - a tree swap drops the base hash and rebuilds every tree from `entry:rank` pairs. Tiered nodes survive that, because the decoder reads their per-entry ranks back out of SimC. A row the read-back could not resolve (`rank_known: false`) still cannot be re-serialized, so a swap on such a build fails with `encode_mismatch` naming the talent that would have been lost; `--add` / `--remove` keep the base hash and still work
  - `diff_from_base.inactive_hero` lists hero talents the export gained from the hero tree the build did not select: SimC's encoder freely grants every hero keystone. They are inert in the sim, but the export string does differ from the input, so relay `result.disclosures` when it is non-empty
  - this uses SimC's own encoder, not reverse-engineered client-side encoding
- if the user wants to compare guide-derived or custom APLs, build a harness and use `compare-apls`; do not edit upstream SimC files
- use `verify-clean` before and after local comparison work when upstream cleanliness matters
- use `1000` iterations for most work
- use `5000+` iterations only when the user explicitly wants higher accuracy
- `sim --preset quick` is the default `1000`-iteration path
- `sim --preset high-accuracy` is the default `5000`-iteration path
- do not recommend a fixed thread count blindly; either omit `threads` or inspect the current machine first

## Comparison Workflow

- use `build-harness` to create one shared local profile baseline. It sims the build's own talents; with no talents it sims the spec's default ones. When `disclosures` (from `build-harness`, `sim` or `compare-apls`) says the profile sets `load_default_talents=1` on a profile that has a build, the results are the default talents': regenerate the harness with `build-harness`. It wears SimC's default gear (`load_default_gear=1`, named in `disclosures` by `build-harness` and `sim`), so its absolute DPS is far below a real character's: quote only relative differences on the same harness
- to answer "how much DPS does this build do", copy the checkout's `profiles/<tier>/<Tier>_<Class>_<Spec>.simc` (for example `profiles/MID2/MID2_Mage_Frost.simc`), replace its `talents=` line with the build, and `simc sim` that file
- use `validate-apl` to catch syntax or setup issues before longer sim runs
- use `compare-apls` to compare base and variant APLs on the same harness
- use `variant-report` to summarize winners, DPS deltas, and action-count deltas
- use `verify-clean` before and after the comparison if the user cares about upstream cleanliness

This is the default safe path for:

- guide-vs-guide APL comparisons
- custom APL experiments
- any workflow where local variants should stay out of the upstream SimC repo

## Talent Modification Workflow

- use `compare-builds` first to understand the per-tree differences between builds
- use `modify-build` with `--swap-*-tree-from` when the user wants to take an entire tree from another build
- use `modify-build` with `--add` / `--remove` when the user wants to adjust specific talents without a full tree swap
- both paths can be combined: swap a tree and then add/remove on top
- talent encoding goes through the local SimC binary, so `doctor` must report a working binary: when `repo.build_ready` is false, every binary-backed command is listed as `unavailable` in `capabilities` and under `dependencies.simc_binary`

## Boundaries

- `simc` is a local-tool provider, not a web-data provider
- current `search` / `resolve` remain limited compared with the other providers
