# Blizzard API

**Tier: experimental.** Explicit reads only: there is no search or resolve yet. The Battle.net hosts and namespaces are
confirmed against the live API for `us`, `eu`, `kr`, and `tw` (`provenance.verified: true`);
`cn` is unverified. Cross-check anything load-bearing against `wowhead` or `warcraftlogs`.

## Best For

- authoritative World of Warcraft data straight from the official Battle.net API
- realm and item records from the Game Data APIs
- character profiles from the Profile API, plus a character's PvP ratings, professions, and
  mount/pet/toy/heirloom/transmog collections
- the current PvP season, its leaderboards, and its title rating cutoffs (Gladiator, Legend, Hero)
- auction house and commodity prices per item id, dated by Blizzard's own snapshot time
- a canonical source to cross-check community providers

## Start With

- readiness + auth/region posture: `warcraft blizzard doctor`
- a realm: `warcraft blizzard realm <slug>` (e.g. `illidan`)
- an item: `warcraft blizzard item <id>` (e.g. `19019`)
- a character: `warcraft blizzard character <realm> <name>` (`<region> <realm> <name>` works too)
- a character's raw PvP, professions or collections body: add `--section pvp-summary`,
  `--section pvp-bracket/3v3` (also `2v2`, `rbg`, or a `shuffle-<class>-<spec>` bracket that
  `pvp-summary` lists), `--section professions`, or `--section collections/mounts` (also `pets`,
  `toys`); a bracket the character has not played is `not_found`
- PvP: `warcraft blizzard pvp-season` (current season, bracket names, title cutoffs),
  `warcraft blizzard pvp-leaderboard 3v3 --limit 25` (also `2v2`, `rbg`, `shuffle-overall`,
  `blitz-overall`, `shuffle-<class>-<spec>`), `warcraft blizzard pvp-character <realm> <name>`
  (honor plus rating and record in every bracket played)
- collections: `warcraft blizzard collections <realm> <name>` (counts plus the first 20 of each by
  name); narrow with `--kind mounts` (`pets`, `toys`, `heirlooms`, `transmogs`) and
  `--match "<text>"` to check whether a character owns something
- prices: `warcraft blizzard commodities --item-id <id>` (region-wide reagents, ore, herbs) and
  `warcraft blizzard auctions <realm> --item-id <id>` (that realm's auction house); without
  `--item-id` you get the most-listed items
- realms may be a slug or a display name (`malganis`, `Mal'Ganis`, `Tarren Mill`), including a
  native-script name (`Ревущий фьорд`, `아즈샤라`), which is looked up in the realm index

## Auth

- needs OAuth client credentials: set `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET`
  (discovered from `.env.local`, the provider env file, or the environment)
- with no credentials, commands return a clean `missing_client_credentials` error; `doctor`
  reports whether they are configured

## Effective Use

- pick a region with `--region` (`us`/`eu`/`kr`/`tw`/`cn`, plus aliases like `na`; `oce` reads
  `us`, where Oceanic realms live); defaults to `BLIZZARD_REGION`, else `us`
- envelopes say `provider: "blizzard-api"`, but the command is `blizzard` (`warcraft blizzard ...`);
  there is no `warcraft blizzard-api` command
- `provenance.cache.hit` says the answer was replayed from cache; it can be up to
  `provenance.cache.oldest_hit_ttl_seconds` old (a day for items, 15 minutes for realms and
  characters, an hour for auctions, leaderboards, cutoffs and collections)
- auction prices are copper (10000 = 1 gold); `median_unit_price` is weighted by units. Rows are per
  item id, so every battle pet is one Pet Cage row (`82800`) and gear item levels merge; rows carry
  no item names (use `blizzard item <id>` or `wowhead`)
- `data.freshness.last_modified` on auctions, commodities, leaderboards and cutoffs is when
  Blizzard built that snapshot, and `age_seconds` its age now; quote it with any price or rank
- `commodities` and `collections` are retail-only. Blizzard publishes no Classic commodity prices:
  ore, cloth, herbs and other trade goods are absent from `auctions <realm> --classic` too, so a
  trade good in `not_listed` there does not mean nobody is selling it. `auctions <realm> --classic`
  prices gear, pets and other non-commodity items
- PvP works on retail, `--classic` and `--game-version classic-anniversary`; Classic Era has no PvP
  seasons (`not_found`). Classic Era and Anniversary auction houses are not readable (`not_found`)
- use `--game-version classic` (or the `--classic` shorthand), after the subcommand
  (`warcraft blizzard item 19019 --classic`), for the current progression Classic namespace
  (Mists of Pandaria Classic today); `--game-version classic-era` reads Classic Era realms such as
  Whitemane and `--game-version classic-anniversary` the Anniversary realms such as Dreamscythe.
  Each works for realms, items and characters. Season of Discovery is not verified
- `--locale` passes through (default `en_US`)
- a bad `--region` or `--game-version`, a blank realm or a blank character name is rejected
  offline with exit 2 (usage): fix the input instead of retrying
- every payload carries `provenance` (region, namespace, namespace class, source URL) and
  `provenance.verified` — `true` for us/eu/kr/tw, `false` for `cn`, whose host could not be
  reached to confirm it
- prefer Blizzard for the official record; prefer community providers for analytics, rankings,
  and guide content
- not covered: achievements, and item names or battle-pet species on auction rows

## Boundaries

- `search` / `resolve` are coming soon: the wrapper does not fan out discovery to `blizzard-api`
  yet — use it with an explicit realm/item/character
- registered with `expansion_mode=none`: region/namespace routing is not the wrapper's expansion
  axis, so `blizzard` stays out of `--expansion` fanout
