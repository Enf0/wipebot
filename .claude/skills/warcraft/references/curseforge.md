# CurseForge

**Tier: experimental.** The command surface is thin (four commands). The endpoints are confirmed
against the live API; `provenance.verified` is `true`.

## Best For

- looking up a World of Warcraft addon by slug or numeric mod id
- the packaged addon surface users actually install: metadata, latest files, changelog
- complementing `warcraft-wiki` (programming/usage context) with the real addon project

## Start With

- readiness + auth posture: `warcraft curseforge doctor`
- an addon by slug: `warcraft curseforge addon deadly-boss-mods`
- an addon by mod id: `warcraft curseforge addon 3358`

## Auth

- needs a CurseForge API key: set `CURSEFORGE_API_KEY` (discovered from `.env.local`, the provider
  env file, or the environment)
- with no key, `addon` returns a clean `missing_api_key` error; `doctor` reports whether the key is
  configured

## Effective Use

- `addon` returns `data.metadata` (the raw mod record), `data.latest_files`, and `data.changelog`
  for the newest file in one call
- `data.latest_files` is newest first by `fileDate` and mixes game flavors and release types: for
  "the current version", take the first row with `releaseType` 1 (release; 2 beta, 3 alpha) whose
  `gameVersions` match the flavor asked about, not row 0
- a numeric argument is treated as a mod id and validated to be a WoW project; a non-numeric
  argument is matched to the exact addon slug, so a near-miss returns `addon_not_found` rather than
  the wrong addon; its `error.details.candidates` lists the addons a name search finds (`weakauras`
  -> `weakauras-2`), most popular first, so retry with one of those slugs
- changelog is best-effort: it is top-level `null` only when the addon has no files; otherwise an
  object for the newest file with its changelog `body` (`null` when that file exposes no notes) and
  `source_url`, or an explicit `{file_id, error}` marker on a failed fetch — the lookup still returns
  metadata + files. Detect empty notes via `changelog.body`, not `changelog is null`.
- the newest file can be an alpha or beta: check `changelog.release_type` (1 release, 2 beta, 3 alpha) and `changelog.display_name` before quoting its notes as "the latest release"
- every payload carries `provenance` (mod id, slug, resolved-by, source URLs) and
  `provenance.verified: true` — host, auth, search, lookup, and changelog endpoints are confirmed
  live; keys without search access can only resolve numeric mod ids (`curseforge addon 3358`)
- lookups are cached for an hour: `provenance.cache.hit` says the answer was replayed and
  `provenance.cache.oldest_hit_age_seconds` how old the replay is

## Boundaries

- `search` / `resolve` are coming soon: use `addon` with a known slug or id
- deferred: dependency graphs, game-version compatibility, file downloads
- registered with `expansion_mode=none`: addon game-version compatibility lives inside file
  records, so `curseforge` stays out of `--expansion` fanout
