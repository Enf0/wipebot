# Method

## Best For

- supported Method.gg guide/article families with simple article structure
- article fetch, export, and local query

## Start With

- discovery: `method search "<query>"`
- conservative match: `method resolve "<query>"`
- fetch: `method guide <slug>`, or one section of a class guide: `method guide <slug>/<section>` (`talents`, `gearing`, `stats-races-and-consumables`, `playstyle-and-rotation`, `interface-and-macros`)
- deeper content: `method guide-full <slug>`
- local export/query: `method guide-export ...`, `method guide-query ...`

## Effective Use

- use Method when an article-style guide is easier to traverse than a Wowhead guide page
- prefer `guide` before `guide-full`
- Method titles never say "Mythic+": `mythic+`, `m+` and `mythic plus` search for "mythic dungeon" pages
- class and spec shorthand works in queries (`bm hunter`, `disc priest`, `frost dk`)
- a section word after a spec (`arcane mage talents`, `frost dk gearing`, `mw monk rotation`, `stats`, `bis`, `macros`) finds the class guide and its `follow_up.command` opens that section
- query words match whole slug words (`mage` does not match "damage"); names with hyphens or apostrophes work as typed (`k'aresh`, `zul'aman`, `kriegval's rest`, `nerub-ar palace`)
- expect explicit support boundaries; unsupported families return structured failures or `scope_hint`
- `guide.last_updated` is an ISO date (the page's wording is in `last_updated_text`); search rows carry `metadata.sitemap_lastmod`
- `resolve --limit` only trims the candidates shown; confidence is judged on every match. An unresolved `resolve` reports `confidence: "low"` when its top candidates tie on score and `"medium"` otherwise
- a one-word query resolves only to a guide that word names, or to the spec guide of a spec word only one class has (`shadow`); any other top guide (`void`, `light`) comes back `medium` with `confidence_cap`
- `search` and `resolve` report `count` as the rows returned, `total_matches` as every match, and `truncated: true` when `--limit` cut the list
- guide references are lowercase slugs (`Frost-Mage` reads as `frost-mage`); anything else fails `invalid_guide_ref` (exit 2) before a request
- `build_references` holds explicit build evidence from the page: embedded Wowhead talent-calc links (`reference_type: wowhead_talent_calc_url`) and published WoW loadout import strings (`reference_type: wow_talent_export`, where `url` is the import string). There is no slug/title-based guide hardlinking
- current class guides publish import strings on their `/talents` section, so `guide-full` on a class guide is what feeds `guide-builds-simc`
- `wowhead_talent_calc_url` rows always decode unaided, because the URL path names the class and spec
- an import string names neither, so SimC identifies it by probing every spec it knows: builds of every role, healers included, decode without `--actor-class` or `--spec`
- additive `analysis_surfaces` highlight comparison-relevant guide topics without replacing raw guide content

## Validated Families

- class guides
- profession guides
- delve guides
- reputation guides
- article guides

## Boundaries

- not all Method.gg content is intentionally supported
- tier-list and index-style roots are intentionally excluded
- a page whose article container no longer matches fails with `parse_failed`; an empty article is never reported as success. So do a sitemap that lists no guides and a class guide whose navigation no longer parses in `guide-full`/`guide-export`
- `guide-full` and `guide-export` skip a navigation page they cannot fetch or parse and list it in `data.failed_pages`
- when Method serves another guide than the one asked for (a redirected URL), `guide`, `guide-full` and `guide-export` set `data.redirect` to `{requested, served, message}` and everything else describes the served guide; check it before treating the content as the page you asked for. It is `null` otherwise
