# Warcraft Wiki

## Best For

- addon and API documentation
- UI events and framework pages
- systems, lore, and reference pages

## Start With

- programming lookup: `warcraft-wiki api <name>`
- game event lookup: `warcraft-wiki event PLAYER_LOGIN`
- general page: `warcraft-wiki article "<title>"`
- discovery: `warcraft-wiki search "<query>"`, `warcraft-wiki resolve "<query>"`

## Effective Use

- prefer `api` for function, enum (`Enum.ItemQuality`), framework (widget pages such as `UIOBJECT Button`, templates such as `SecureActionButtonTemplate`, `TOC format`), XML schema, CVar (`api autoLootDefault` returns the `CVar autoLootDefault` page; `Console variables` is the list), API-change and programming how-to pages
- prefer `event` for game events (`PLAYER_LOGIN`, `ENCOUNTER_START`) and UI handlers (`OnKeyDown`)
- `api` and `event` fail with `not_found` (exit 4) instead of returning an unrelated page; a page they do not classify as programming reference also fails, so try `article "<exact title>"` before concluding the page does not exist
- a title the wiki can never hold (`a|b`, a `Special:` page) or a search longer than 300 characters fails with `invalid_query` (exit 2), so fix the query rather than retrying; any other wiki-side refusal is `api_error` with the wiki's own code in `error.details.mediawiki_code`
- use `article` when the query is broader than programming
- `search` and `resolve` report `count` as the rows returned, `total_matches` as MediaWiki's total hit count, and `truncated: true` when more pages matched than came back; an unresolved `resolve` says `confidence: "low"` when its top candidates tie
- a one-word query resolves only to the page that word names: its exact title, its `API`/`Event` page (`CreateFrame`, or `SetPoint` for `API:ScriptRegionResizing SetPoint`), or its expansion (`legion`); a longer title (`illidan` gives "Illidan Stormrage") comes back `medium` with `confidence_cap`, so check `match` and open it with `match.follow_up.command`
- use `reference` metadata on article responses instead of parsing the full body first; on `api`/`event` pages `reference.arguments` holds the arguments (an event's top-level "Payload", or "Base Parameters" for `COMBAT_LOG_EVENT_UNFILTERED`), and `reference.signature` is the introduction's code block or `null`
- `reference.summary` is the page's opening text, which can start with a "For the ..., see ..." hatnote or infobox text on lore and API pages; read `content.text` when the summary looks like navigation

## Strong Families

- API functions and enums
- game events
- UI handlers
- framework pages
- XML schema
- API changes
- programming howtos
- systems pages
- expansion, class, profession, faction, lore, zone, and guide-style reference pages
