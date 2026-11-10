"""Raid bosses: the strategy guides (Method, Wowhead, Icy Veins), their abilities' tooltips and their loot, from
Wowhead; the abilities Lorrgs tracks."""
import asyncio
import html
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from ..text import fold
from . import fetch
from .fetch import KnowledgeError, NotFound
from .guides import GUIDE_SOURCES, SOURCE_NAMES, Guide, cut, sections, updated, updated_on, wowhead_text
from .names import boss_words, fold_quotes, names_boss, slug
from .news import Hotfix, hotfixes
from .specs import GearPages, gear_pages
from .spells import Spell, tooltip

# The three strategy guides together are cut to this many characters (~10k tokens): Method's and Icy Veins' (at most
# 20k and 11k in The Venomous Abyss) first, Wowhead's cheat sheet and dungeon journal (up to 39k) what they leave.
GUIDES_CHARS = 40000
WOWHEAD_MIN_CHARS = 4000  # Wowhead's share when the others fill the rest: its cheat sheet and a little more
GUIDE_CHARS = 12000  # with `mechanic`, each guide's parts naming it are cut here
REPEAT_CHARS = 80  # a paragraph this long that a guide repeats word for word is left out the second time
HOTFIX_DAYS = 365  # Blizzard's hotfixes read for a boss; the running posts reach back a patch or two anyway
MECHANIC_AFTER = 2  # a long paragraph naming a mechanic: the sentences after each naming it, kept with it
PARAGRAPH_CHARS = 600  # a paragraph longer than this is cut to its sentences about the mechanic
WOWHEAD_BOSS_SKIP = re.compile(r"rewards|^gear$|^more rewards$", re.I)  # loot sections of Wowhead's boss pages
NAMING_S = 12  # looking for a mechanic under the other bosses gives up after this: the answer mustn't wait for it
TOOLTIPS_NAMED = 4  # of the spells sharing a mechanic's name, those whose tooltips are read (two are printed)
_DIFFICULTY = "Normal|Heroic|Mythic|Raid Finder"
# What Wowhead's journal ends an entry with that is on some difficulties only: "(Mythic)", "(Normal, Raid Finder)"
_ON_DIFFICULTIES = rf"\((?:{_DIFFICULTY})(?:, (?:{_DIFFICULTY}))*\)"
JOURNAL_NOTE = ("The dungeon journal's damage and healing amounts are left out: Wowhead's page gives most of them "
                "unscaled. boss_guide with topic tooltips, or spell_info, has them.")

log = logging.getLogger(__name__)


async def _wowhead_boss_listing(boss: str) -> dict:
    """Wowhead's guide listing row for a raid boss's guide ("Sszorak Raid Boss Guide - ...", "The Unbinding of
    Kith'ix Raid Guide - ...: Boss Cheat Sheet ...")."""
    listing = await fetch.cli("wowhead", "guides", "raids", "--", boss)
    rows = [r for r in listing.get("results") or [] if "guide" in (r.get("title") or r.get("name") or "").lower()
            and names_boss(r.get("title") or r.get("name") or "", boss)]
    if not rows:
        raise KnowledgeError(f"no Wowhead raid boss guide matching {boss!r}")
    return rows[0]


async def _wowhead_boss_page(boss: str) -> tuple[dict, dict]:
    """(the listing row, the page) of the boss's Wowhead guide."""
    try:
        row = await _wowhead_boss_listing(boss)
        return row, await fetch.cli("wowhead", "guide-full", row["url"])
    except KnowledgeError as e:
        raise KnowledgeError(f"no Wowhead guide for {boss}: {e}") from e


def _linked(page: dict, kind: str) -> dict[int, str]:
    """id -> name of the spells or items a Wowhead page links."""
    return {e["id"]: e.get("name") for e in (page.get("linked_entities") or {}).get("items") or []
            if e.get("entity_type") == kind and e.get("name")}


NAME_CHARS = 60  # what a page gives as a linked spell's name is none when longer: it's the spell's journal entry
# A link to a spell in a Wowhead guide's markup: the spell's id, its name as Wowhead slugs it in the address (a
# link to the bare id has none) and the link's text
_SPELL_LINK = re.compile(r'<a\b[^>]*\bhref="[^"]*?spell=(\d+)(?:/([a-z0-9-]+))?[^"]*"[^>]*>(.*?)</a>', re.S)


def _spell_names(page: dict) -> dict[int, str]:
    """id -> name of the spells a Wowhead boss guide links. The page's list of them (`_linked`) leaves a spell
    linked only from the text without a name, and names one linked around its journal entry by that whole entry
    (118 of the 400 spells on Midnight's 17 boss pages in November 2026, Gravebound and Toxic Deluge among them).
    Those are named by their links: the address of one holds the name as Wowhead slugs it
    ("/spell=1286837/gravebound"), and the name is what the page writes that slugs to the same, another spell's
    name in the list or a link's text ("[Gravebound]"), else the slug's words ("manifestation of dread", which
    `_key` still tells from no other name). A spell with neither a name nor such a link is left out."""
    def slugged(name: str) -> str:
        return slug(fold(fold_quotes(name)))
    raw = "\n".join(c.get("content_raw") or "" for c in (page.get("body") or {}).get("section_chunks") or [])
    links = [(int(spell_id), in_address, html.unescape(re.sub(r"<!--.*?-->|<[^>]+>", "", text, flags=re.S)))
             for spell_id, in_address, text in _SPELL_LINK.findall(raw)]
    slugs = {spell_id: in_address for spell_id, in_address, _ in reversed(links) if in_address}
    listed = {e["id"]: e.get("name") or "" for e in (page.get("linked_entities") or {}).get("items") or []
              if e.get("entity_type") == "spell"}
    written: dict[str, str] = {}  # a slug -> the name as the page writes it
    for name in [*listed.values(), *(text.strip().strip("[]") for _, _, text in links)]:
        if name and len(name) <= NAME_CHARS:
            written.setdefault(slugged(name), name)
    names = {}
    for spell_id, name in listed.items():
        in_address = slugs.get(spell_id)
        if in_address and slugged(name) != in_address:  # no name, an entry, or a name with more in it
            name = written.get(in_address) or in_address.replace("-", " ")
        if name and len(name) <= NAME_CHARS:
            names[spell_id] = name
    return names


def _key(name: str) -> str:
    """A spell's name for telling whether two are the same: as `_plain`, without the apostrophes, which a name read
    from a link's address has lost ("ulateks presence")."""
    return _plain(name).replace("'", "")


async def _linked_spells(boss: str) -> dict[int, str]:
    """id -> name of the spells the boss's Wowhead guide links (`_spell_names`): its abilities by name, with no
    tooltip read. None when Wowhead has no guide for it."""
    try:
        return _spell_names((await _wowhead_boss_page(boss))[1])
    except KnowledgeError:
        return {}


async def _tooltips(ids: Iterable[int]) -> list[Spell]:
    """Wowhead's tooltips of these spells, those it gives with text."""
    found = await asyncio.gather(*(tooltip(i) for i in ids), return_exceptions=True)
    return [s for s in found if isinstance(s, Spell) and s.text]


# ---------- tooltips ----------

@dataclass
class BossAbilities:
    guide: str  # the Wowhead guide's title
    url: str
    spells: list[Spell]  # those with tooltip text
    unread: int  # tooltips Wowhead didn't give


async def boss_abilities(boss: str) -> BossAbilities:
    """A raid boss's abilities with Wowhead's tooltips: the spells its Wowhead guide links."""
    row = await _wowhead_boss_listing(boss)
    page = await fetch.cli("wowhead", "guide-full", row["url"])
    ids = [e["id"] for e in (page.get("linked_entities") or {}).get("items", []) if e.get("entity_type") == "spell"]
    found = await asyncio.gather(*(tooltip(i) for i in ids), return_exceptions=True)
    return BossAbilities(row.get("title") or row.get("name") or boss, row["url"],
                         [s for s in found if isinstance(s, Spell) and s.text],
                         sum(isinstance(s, Exception) for s in found))


async def boss_tooltips_text(boss: str) -> str:
    """boss_guide's tooltips topic."""
    a = await boss_abilities(boss)
    lines = [f"{a.guide}. Source: Wowhead {a.url}",
             "Abilities (Wowhead tooltip text; numbers may be for one difficulty):"]
    lines += [f"- {s.name} (spell {s.id}): {s.text}" for s in a.spells]
    if a.unread:
        lines.append(f"({a.unread} more abilities linked: their tooltips are unavailable)")
    return "\n".join(lines)


async def boss_spells(boss: str) -> list[dict]:
    """The boss abilities Lorrgs tracks on its timelines (name, spell_id, show); none for a boss Lorrgs doesn't
    know."""
    try:
        data = await fetch.cli("lorrgs", "boss-spells", slug(boss))
    except NotFound:
        return []
    return [s for s in data.values() if isinstance(s, dict)]


async def raid_boss_spells() -> dict[int, str]:
    """The boss abilities Lorrgs tracks, spell id -> name, over every boss of the current expansion's raids."""
    bosses = [b for _, names in await raids() for b in names]
    found = await asyncio.gather(*(boss_spells(b) for b in bosses))
    return {s["spell_id"]: s.get("name") or "" for spells in found for s in spells if s.get("spell_id")}


async def raids() -> list[tuple[str, list[str]]]:
    """The current expansion's raids, each with its bosses in the game's order, from Raider.IO. Warcraft Logs files a
    single-boss raid's boss under the tier's raid (Kith'ix under The Venomous Abyss); the game doesn't."""
    data = await fetch.cli("raiderio", "raids")
    return [(r["name"], [e["name"] for e in r.get("encounters") or []]) for r in data.get("rows") or []]


# ---------- strategy ----------

async def boss_guides(boss: str, raid: str, difficulty: str = "mythic") -> tuple[list[Guide], list[str]]:
    """A raid boss's strategy guides, Method's, Wowhead's and Icy Veins', and why any is missing. Raises when none
    can be read."""
    found = await asyncio.gather(method_boss_guide(boss, raid, difficulty), wowhead_boss_guide(boss, difficulty),
                                 icy_veins_boss_guide(boss, raid, difficulty), return_exceptions=True)
    guides = [g for g in found if isinstance(g, Guide)]
    missing = [str(e) for e in found if isinstance(e, Exception)]
    if not guides:
        raise KnowledgeError("; ".join(missing))
    return guides, missing


async def boss_guide_text(boss: str, raid: str, difficulty: str = "mythic") -> str:
    """boss_guide's strategy topic: Blizzard's hotfixes to the boss since the guides were written, then the three
    guides side by side within GUIDES_CHARS, then those missing."""
    (guides, missing), fixes = await asyncio.gather(boss_guides(boss, raid, difficulty), _hotfixes(boss))
    texts = [_repeats_out(strategy_text(g)) for g in guides]
    cuts = [_cut(g, text, chars) for g, text, chars in zip(guides, texts, _budgets(guides, texts), strict=True)]
    return _tight("\n\n".join([hotfixes_after(boss, guides, fixes), *cuts,
                               *(f"(Not available: {e})" for e in missing)]))


def _tight(text: str) -> str:
    """The guides' text with a list's items on consecutive lines: Wowhead's journal comes with a blank line
    before each, which the paragraphs are told apart by until here."""
    return re.sub(r"\n\n+(?=- )", "\n", text)


def _budgets(guides: list[Guide], texts: list[str]) -> list[int]:
    """How many characters each guide gets of GUIDES_CHARS: Method's and Icy Veins' strategy whole while they fit
    (else shared by length), Wowhead's cheat sheet and dungeon journal what they leave, at least WOWHEAD_MIN_CHARS."""
    strategy = [i for i, g in enumerate(guides) if g.source != "wowhead"]
    room = GUIDES_CHARS - (WOWHEAD_MIN_CHARS if len(strategy) < len(guides) else 0)
    wanted = sum(len(texts[i]) for i in strategy)
    budgets = [len(t) if wanted <= room else room * len(t) // wanted for t in texts]
    rest = GUIDES_CHARS - sum(min(budgets[i], len(texts[i])) for i in strategy)
    return [budgets[i] if i in strategy else rest for i in range(len(guides))]


def _repeats_out(text: str) -> str:
    """A guide without the paragraphs it repeats word for word (Wowhead's journal lists a boss's abilities again in
    each phase that has them, there with or without the difficulties it's on): an ability's line keeps its name."""
    seen, out = set(), []
    for para in re.split(r"\n\s*\n", text):
        key = re.sub(rf"\s*{_ON_DIFFICULTIES}$", "", " ".join(para.split()))
        if len(key) >= REPEAT_CHARS and key in seen:
            if named := re.match(r"- ([^:\n]{1,60}):", para):
                out.append(f"- {named[1]}: as above")
            continue
        seen.add(key)
        out.append(para)
    return "\n\n".join(out)


def _cut(g: Guide, text: str, chars: int) -> str:
    """One guide's text cut to `chars`, naming the headings and phases cut and where the rest is."""
    if len(text) <= chars:
        return text
    head = cut(text, chars)
    gone = re.findall(r"^(?:#{2,3} (.+)|- ((?:Stage|Phase|Intermission)\b[^\n]*))$", text[len(head) - 4:], re.M)
    names = list(dict.fromkeys(a or b for a, b in gone))
    return head + (f"\n(Cut here: {', '.join(names)} and the rest; " if names else "\n(Cut here: ") \
        + f"web_lookup with {g.url} reads the whole guide.)"


async def _hotfixes(boss: str) -> tuple[list[str], list[Hotfix]] | KnowledgeError:
    """Blizzard's hotfixes mentioning the boss over HOTFIX_DAYS, or why they couldn't be read: the guides' dates
    choose among them, so the answer doesn't depend on today."""
    try:
        return await hotfixes(boss, HOTFIX_DAYS)
    except KnowledgeError as e:
        return e


def hotfixes_after(boss: str, guides: list[Guide], found: tuple[list[str], list[Hotfix]] | KnowledgeError,
                   mechanic: str | None = None) -> str:
    """Blizzard's hotfixes to the boss on or after a guide's last update (by day: the posts give no time), under
    the guides each run of them is newer than and where Blizzard's post has them, or that there's none. With
    `mechanic`, those naming it, and how many others there are."""
    if isinstance(found, KnowledgeError):
        return f"(Blizzard's hotfixes couldn't be read, so whether {boss} changed since these guides is unknown: " \
               f"{found})"
    urls, fixes = found
    days = {g.site: d.date() for g in guides if (d := updated_on(g.last_updated))}
    newer = [(h, [site for site, day in days.items() if _fix_day(h) >= day]) for h in fixes]
    newer = [(h, sites) for h, sites in newer if sites]
    source = f"Source: Blizzard's hotfix posts on Wowhead's blue tracker {', '.join(urls)}"
    since = f"since {oldest.isoformat()}" if (oldest := min(days.values(), default=None)) \
        else "since the guides were updated"
    if not newer:
        return f"# No hotfix to {boss} {since}. {source}"
    others = ""
    if mechanic:
        named = [(h, sites) for h, sites in newer if _names(f"{h.path} {h.text}", mechanic)]
        if rest := len(newer) - len(named):
            others = (f"{rest} other {'hotfix' if rest == 1 else 'hotfixes'} to {boss} "
                      f"{'doesn' if rest == 1 else 'don'}'t name it: boss_guide without `mechanic` lists them")
        if not named:
            return f"# No hotfix naming {mechanic} {since} ({others}). {source}"
        newer = named
    lines = [f"# Blizzard's hotfixes to {boss} on or after a guide's last update (the guides named can't have them). "
             + source]
    under = None
    for h, sites in newer:
        if (sites, h.path) != under:
            under = (sites, h.path)
            lines.append(f"Newer than {', '.join(sites)}{f' ({h.path})' if h.path else ''}:")
        lines.append(f"- {h.date}: {h.text}")
    return "\n".join(lines + ([f"({others}.)"] if others else []))


def _fix_day(h: Hotfix) -> date:
    return datetime.fromtimestamp(h.when, UTC).date()


async def boss_mechanic_text(boss: str, raid: str, difficulty: str, mechanic: str) -> str:
    """boss_guide's strategy topic for one mechanic: the hotfixes naming it, its Wowhead tooltip, then each guide's
    sections about it: a section titled for it whole, of the rest the paragraphs naming it (a long one cut to the
    sentences naming it and the few after). Of the boss's tooltips only the mechanic's are read, found by the
    names Wowhead's guide links its spells under."""
    (guides, missing), linked, fixes = await asyncio.gather(boss_guides(boss, raid, difficulty), _linked_spells(boss),
                                                            _hotfixes(boss))
    tips = await _tooltips([i for i, name in linked.items() if _key(name) == _key(mechanic)][:TOOLTIPS_NAMED])
    parts = [f"{mechanic} on {boss}: its tooltip, then the parts of each guide that name it (boss_guide without "
             "`mechanic` reads the whole guides).", hotfixes_after(boss, guides, fixes, mechanic)]
    parts += [f"Tooltip (Wowhead, spell {s.id}): {s.text}" for s in tips[:2]]
    found = False
    for g in guides:
        kept = mechanic_sections(g, mechanic)
        found = found or bool(kept)
        if not kept:
            parts.append(f"# {g.title}: names no {mechanic}. Source: {g.site} {g.url}")
            continue
        parts.append(_cut(g, _repeats_out(strategy_text(replace(g, sections=kept))), GUIDE_CHARS))
    if not (found or tips):
        known = ", ".join(dict.fromkeys(linked.values()))
        raise KnowledgeError(f"no guide for {boss} names {mechanic!r}"
                             + (f"; its abilities, as Wowhead's guide links them: {known}" if known else ""))
    return _tight("\n\n".join(parts + [f"(Not available: {e})" for e in missing]))


async def mechanic_tooltips_text(ids: Iterable[int]) -> str:
    """What Wowhead's tooltips say of an ability no guide names, by the spell ids a report has it under: one line
    per tooltip that differs, '' when Wowhead gives none."""
    tips = {s.text: s for s in await _tooltips(list(ids)[:TOOLTIPS_NAMED])}
    return "\n".join(f"Tooltip (Wowhead, spell {s.id}): {s.text}" for s in list(tips.values())[:2])


async def boss_naming(mechanic: str, but: str, raid: str | None = None) -> tuple[str, str] | None:
    """(raid, boss) of the current expansion's raids, `but` aside, whose abilities (Lorrgs' and those Wowhead's
    guide links, by name: no tooltip is read) include `mechanic`; None when none does, the raids can't be read, or
    NAMING_S passed first (what was read by then is kept, so asking again gets further). The bosses of `raid` (the
    report's) are read first, the other raids' only when none of them has it."""
    async def names(boss: str) -> set[str]:
        tracked, linked = await asyncio.gather(boss_spells(boss), _linked_spells(boss), return_exceptions=True)
        listed = [s.get("name") or "" for s in tracked] if isinstance(tracked, list) else []
        return {_key(n) for n in listed + list(linked.values() if isinstance(linked, dict) else [])}
    try:
        async with asyncio.timeout(NAMING_S):
            bosses = [(r, b) for r, names_ in await raids() for b in names_ if not names_boss(b, but)]
            near = [rb for rb in bosses if raid and rb[0] == raid]
            for part in (near, [rb for rb in bosses if rb not in near]):
                found = await asyncio.gather(*(names(b) for _, b in part))
                if hit := next((rb for rb, known in zip(part, found, strict=True) if _key(mechanic) in known),
                               None):
                    return hit
    except KnowledgeError:
        return None
    except TimeoutError:
        log.info("looking for %r under the bosses other than %s took over %s s: given up", mechanic, but, NAMING_S)
    return None


def _plain(s: str) -> str:
    """Words for matching a mechanic's name in a guide: no case, accents or curly quotes."""
    return " ".join(re.findall(r"[\w']+", fold_quotes(fold(s.lower()))))


# Names guides use for the same thing: asking for one finds the others.
SAME_MECHANIC = ({"enrage", "berserk"},)


def _names(text: str, mechanic: str) -> bool:
    """Whether `text` names the mechanic, as a whole name ("Searing Brand's damage" too, not "Searing Brandish")."""
    plain = _plain(mechanic)
    names = next((same for same in SAME_MECHANIC if plain in same), {plain})
    words = f" {_plain(text)} "
    return any(f" {name} " in words or f" {name}'s " in words for name in names)


def mechanic_sections(g: Guide, mechanic: str) -> list[tuple[str, str]]:
    """A guide's sections about a mechanic: one titled for it whole; of the rest, the paragraphs naming it (each
    once), a long one cut to the sentences naming it and the MECHANIC_AFTER after each."""
    out = []
    for title, text in g.sections:
        if _names(title, mechanic):
            out.append((title, text))
            continue
        kept: list[str] = []
        for para in re.split(r"\n\s*\n", text):
            if not _names(para, mechanic) or para in kept:
                continue
            if len(para) > PARAGRAPH_CHARS:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                keep = sorted({j for i, s in enumerate(sentences) if _names(s, mechanic)
                               for j in range(i, min(i + 1 + MECHANIC_AFTER, len(sentences)))})
                para = " ".join(sentences[j] if k == 0 or keep[k - 1] == j - 1 else "... " + sentences[j]
                                for k, j in enumerate(keep))
            kept.append(para)
        if kept:
            out.append((title, "\n\n".join(kept)))
    return out


def strategy_text(g: Guide) -> str:
    """One boss guide: its title, last update and URL (and its note), then its sections."""
    return "\n\n".join([f"# {g.title}, {updated(g.last_updated)}. Source: {g.site} {g.url}"
                        + (f"\n({g.note})" if g.note else "")]
                       + [f"## {title}\n{text}" for title, text in g.sections])


async def method_boss_guide(boss: str, raid: str, difficulty: str = "mythic") -> Guide:
    """Method's strategy guide for a raid boss (setup, strategy, what wipes raids). Method writes one for Mythic and
    one for Heroic, and sometimes only one of them (Ula'tek has only a Heroic page): then the other stands in, titled
    so. warcraft_cli's search doesn't index these pages, so the URL is built from Method's pattern:
    /guides/<raid>/<boss>[-heroic]."""
    heroic = difficulty.lower().startswith("h")
    url, other_url = (f"https://www.method.gg/guides/{slug(raid)}/{slug(boss)}" + suffix
                      for suffix in (("-heroic", "") if heroic else ("", "-heroic")))
    wanted, other = ("Heroic", "Mythic") if heroic else ("Mythic", "Heroic")
    title = f"Method's {wanted} guide for {boss} ({raid})"
    try:
        data = await fetch.cli("method", "guide-full", url)
    except KnowledgeError as e:
        try:
            data = await fetch.cli("method", "guide-full", other_url)
        except KnowledgeError:
            raise KnowledgeError(f"no Method guide for {boss} ({wanted}) in {raid}: {e}") from e
        url, title = other_url, f"Method has no {wanted} guide for {boss} ({raid}); its {other} guide"
    found = []
    for s in sections(data):
        name = s["title"].split(": ", 1)[-1] if s["title"].lower().startswith(slug(boss).replace("-", " ")) \
            or s["title"].startswith(boss) else s["title"]
        if s["text"].strip() and s["text"].strip() != "Expand for details":
            found.append((name, s["text"].strip()))
    return Guide("method", title, url, (data.get("guide") or {}).get("last_updated"), found)


async def _icy_veins_raid_family(boss: str, raid: str) -> dict:
    """Icy Veins' raid guide as one family of pages (a page per boss), found through its search: by the boss's
    name, then its first word ("Vashnik": the search doesn't know "The" or "the Malignant"), then the raid's."""
    words = boss_words(boss)
    queries = list(dict.fromkeys([boss, *words[:1], " ".join(boss_words(raid))]))
    for query in queries:
        found = await fetch.cli("icy-veins", "search", "--", query)
        ref = next((r["id"] for r in found.get("results") or []
                    if (r.get("metadata") or {}).get("content_family") == "raid_guide"), None)
        if ref:
            data = await fetch.cli("icy-veins", "guide-full", ref)
            if _icy_veins_boss_page(data, boss):
                return data
    raise KnowledgeError(f"no Icy Veins raid guide for {boss}")


def _icy_veins_boss_page(data: dict, boss: str) -> dict | None:
    """The boss's page in the family: its title ("7 Twin Fangs", "6 Vashnik") is the name or a part of it."""
    words = boss_words(boss)

    def title_words(p: dict) -> list[str]:
        return boss_words(re.sub(r"^\d+\s+", "", (p.get("guide") or {}).get("section_title") or ""))
    pages = [p for p in data.get("pages") or [] if title_words(p)]
    return (next((p for p in pages if set(title_words(p)) <= set(words)), None)
            or next((p for p in pages if title_words(p)[0] == words[0]), None))


async def icy_veins_boss_guide(boss: str, raid: str, difficulty: str = "mythic") -> Guide:
    """Icy Veins' guide to a raid boss: every mechanic, a quick guide and the Mythic changes (when the page has a
    Mythic section)."""
    data = await _icy_veins_raid_family(boss, raid)
    page = _icy_veins_boss_page(data, boss)
    if not page:
        raise KnowledgeError(f"Icy Veins' raid guide has no page for {boss}")
    guide, family = page.get("guide") or {}, data.get("guide") or {}
    mythic = not difficulty.lower().startswith("h")
    heading, has_mythic, found = "", False, []
    for s in (page.get("article") or {}).get("sections") or []:
        title, text = s.get("title") or "", (s.get("text") or "").strip()
        if title.startswith(("Mechanic Breakdown", "Quick Guide", "Notable Changes", "Mythic Changes")):
            heading = title
            has_mythic = has_mythic or heading.startswith(("Notable", "Mythic"))
            continue  # an intro line; the sections after it hold the text
        if not text or title == "Changelog" or (heading.startswith(("Notable", "Mythic")) and not mythic):
            continue
        found.append((f"{heading + ': ' if heading else ''}{title}", text))
    about = ("Mythic changes at the end" if mythic and has_mythic
             else "the page has no Mythic section" if mythic else "without the Mythic changes")
    return Guide("icy-veins", f"Icy Veins' guide for {boss} (every difficulty; {about})",
                 guide.get("page_url") or family.get("page_url") or "",
                 guide.get("last_updated") or family.get("last_updated"), found)


async def wowhead_boss_guide(boss: str, difficulty: str | None = None) -> Guide:
    """Wowhead's boss guide: the cheat sheet (style, lust timing, assignments per phase), the full strategy and
    the dungeon journal, rendered from the page's markup (its plain text drops the linked ability names). With a
    difficulty, the journal's abilities on that one. The journal's damage and healing amounts are left out
    (guides.JOURNAL_AMOUNT), which the guide's note says when it had any."""
    row, page = await _wowhead_boss_page(boss)
    names = _linked(page, "spell")
    found, amounts = [], False
    for chunk in (page.get("body") or {}).get("section_chunks") or []:
        title, raw = (chunk.get("title") or "").strip(), chunk.get("content_raw") or ""
        raw = journal_on(raw, difficulty) if difficulty else raw
        text = wowhead_text(raw, names, amounts=False)
        if text and not WOWHEAD_BOSS_SKIP.search(title):
            found.append((title, text))
            amounts = amounts or text != wowhead_text(raw, names)
    if not found:
        raise KnowledgeError(f"Wowhead's guide for {boss} has no strategy text")
    journal = f"the dungeon journal on {difficulty.title()}" if difficulty else "journal; difficulties as marked"
    return Guide("wowhead", f"Wowhead's guide for {boss} (cheat sheet, strategy, {journal})", row["url"],
                 row.get("last_updated"), found, note=JOURNAL_NOTE if amounts else "")


# A journal entry that is on some difficulties only: its <li> ends in them, "(Normal, Raid Finder)"
_ON_SOME = re.compile(r"<li\b[^>]*>(?:(?!</li>).)*?\(((?:Normal|Heroic|Mythic|Raid Finder)"
                      r"(?:, (?:Normal|Heroic|Mythic|Raid Finder))*)\)</div></li>", re.S)
_LIST_TAG = re.compile(r"<(/?)ul\b[^>]*>")


def journal_on(raw: str, difficulty: str) -> str:
    """Wowhead's dungeon journal markup without the entries for other difficulties than `difficulty`: an entry the
    journal marks with others only, and the list of abilities under it (the <ul> right after its <li>). The journal
    lists an ability once per difficulty it differs on, so a Mythic answer would read the Heroic numbers too."""
    out, pos = [], 0
    for m in _ON_SOME.finditer(raw):
        if m.start() < pos or difficulty.title() in m[1].split(", "):  # inside an entry left out, or on it
            continue
        out.append(raw[pos:m.start()])
        pos = m.end()
        if under := re.compile(r"\s*<ul\b").match(raw, pos):
            depth = 0
            for tag in _LIST_TAG.finditer(raw, under.end() - 3):
                depth += -1 if tag[1] else 1
                if depth == 0:
                    pos = tag.end()
                    break
    return "".join(out) + raw[pos:]


# ---------- loot ----------

WOWHEAD_GEAR = re.compile(r"^gear\b", re.I)  # "Gear", "Gear Drop Information for ..."
WOWHEAD_MORE_REWARDS = re.compile(r"^(?:more|all other) .*rewards", re.I)
_GEAR_ROW = re.compile(r"\[item=(\d+)[^\]]*\]\[/td\]\s*\[td[^\]]*\]([^\[]+)\[/td\]")
DIFFICULTIES = ("LFR", "Normal", "Heroic", "Mythic")
_ILVL_ROW = re.compile(r"\[td[^\]]*\](?:\[pad\])*\s*(LFR|Raid F\w+|Normal|Heroic|Mythic)\s*\[/td\]\s*"
                       r"\[td[^\]]*\]\s*(\d+)\s*(?:\[i\]([^\[]+)\[/i\])?", re.I)
# Tooltip text around the stats: the name, difficulty and item level before; requirements and selling after.
_TOOLTIP_HEAD = re.compile(r"^.*?Binds when (?:picked up|equipped)\s*", re.S)
_TOOLTIP_TAIL = re.compile(r"\s*(?:Requires Level|Sell Price|Dropped by)\b.*$", re.S)


@dataclass
class Drop:
    id: int
    name: str
    slot: str  # as Wowhead's table has it: "Trinket", "Leather Chest", "Mail Tier Set Token - Legs"
    stats: str | None  # the tooltip's stats and effects, None when Wowhead gave no tooltip
    tooltip_level: str | None  # the item level the tooltip shows
    named_in: list[str] | None = None  # with a spec: the spec's gear pages naming it ("Method's trinket advice")


@dataclass
class Loot:
    boss: str
    url: str
    last_updated: str | None
    levels: list[tuple[str, str, str | None]]  # (difficulty, item level, upgrade track)
    drops: list[Drop]
    others: list[str]  # the other rewards' names
    spec: str | None = None
    gear_pages: list[GearPages] | None = None  # with a spec: its gear pages read
    gear_pages_missing: list[str] | None = None  # the sites whose gear pages couldn't be read


async def boss_loot(boss: str, spec: str | None = None) -> Loot:
    """What a raid boss drops, from its Wowhead guide: each item's slot and tooltip, the item level per difficulty
    and the other rewards. With a spec, which drops its gear pages on Wowhead, Method and Icy Veins name
    (best-in-slot lists, trinket advice)."""
    row, page = await _wowhead_boss_page(boss)
    chunks = (page.get("body") or {}).get("section_chunks") or []
    gear = "\n".join(c.get("content_raw") or "" for c in chunks if WOWHEAD_GEAR.search((c.get("title") or "").strip()))
    slots = {int(i): " ".join(s.split()) for i, s in _GEAR_ROW.findall(gear)}
    if not slots:
        raise KnowledgeError(f"Wowhead's guide for {boss} lists no gear drops")
    names = _linked(page, "item")

    async def one(item_id: int) -> tuple[str, str] | None:
        try:
            return await _item_tooltip(item_id)
        except KnowledgeError:
            return None

    drops = []
    for (item_id, slot), tip in zip(slots.items(), await asyncio.gather(*(one(i) for i in slots)), strict=True):
        stats = shown = None
        if tip and tip[1]:
            shown = re.search(r"Item Level (\d+)", tip[1])
            stats = re.sub(r"\s*Durability \d+ / \d+", "", _TOOLTIP_TAIL.sub("", _TOOLTIP_HEAD.sub("", tip[1])))
        drops.append(Drop(item_id, tip[0] if tip else names.get(item_id) or f"item {item_id}", slot, stats,
                          shown[1] if shown else None))
    more = "\n".join(c.get("content_raw") or "" for c in chunks
                     if WOWHEAD_MORE_REWARDS.search((c.get("title") or "").strip()))
    loot = Loot(boss, row["url"], row.get("last_updated"),
                [(d, lvl, track.strip() if track else None) for d, lvl, track in _ILVL_ROW.findall(gear)], drops,
                [names.get(int(i)) or f"item {i}" for i in dict.fromkeys(re.findall(r"\[item=(\d+)", gear + more))
                 if int(i) not in slots], spec)
    if spec:
        await _mark_gear_pages(loot, spec)
    return loot


async def _item_tooltip(item_id: int, bonus_ids: list[int] | None = None) -> tuple[str, str]:
    """(an item's name, its tooltip as text). Read from the tooltip's markup: Wowhead's plain text drops what's in
    brackets, such as the primary stat of armor ("+189 [Agility or Intellect]"). With bonus IDs, the piece as worn."""
    bonus = ("--no-include-comments", "--linked-entity-preview-limit", "0",
             "--bonus", ":".join(map(str, bonus_ids))) if bonus_ids else ()
    data = await fetch.cli("wowhead", "entity", "item", str(item_id), *bonus)
    entity, tip = data.get("entity") or {}, data.get("tooltip") or {}
    h = re.sub(r"<!--.*?-->", "", tip.get("html") or "", flags=re.S)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h)).split()) or (tip.get("text") or "").strip()
    return entity.get("name") or f"item {item_id}", text


_STAT = re.compile(r"\+\s*([\d,]+) (Critical Strike|Haste|Mastery|Versatility|Leech|Avoidance|Speed)\b")


async def worn_item(item_id: int, bonus_ids: list[int]) -> tuple[str, int | None, list[tuple[str, int]]]:
    """A piece as worn, from Wowhead's tooltip with its bonus IDs (its upgrade, a crafted piece's chosen stats):
    (its name, its item level, its secondary and minor stats in the tooltip's order)."""
    name, text = await _item_tooltip(item_id, bonus_ids)
    level = re.search(r"Item Level (\d+)", text)
    return name, int(level[1]) if level else None, [(stat, int(v.replace(",", ""))) for v, stat in _STAT.findall(text)]


async def _mark_gear_pages(loot: Loot, spec: str) -> None:
    """The spec's gear pages on each site, and per drop which of them name it."""
    found = await asyncio.gather(*(gear_pages(spec, s) for s in GUIDE_SOURCES), return_exceptions=True)
    pages = [p for p in found if isinstance(p, GearPages)]
    if not pages:
        return
    loot.gear_pages = pages
    loot.gear_pages_missing = [SOURCE_NAMES[s] for s, p in zip(GUIDE_SOURCES, found, strict=True)
                               if not isinstance(p, GearPages)]

    def fold(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", fold_quotes(s.lower())))

    for d in loot.drops:
        d.named_in = [f"{p.site}{"'" if p.site.endswith('s') else "'s"} {where}" for p in pages
                      for where, text in (("best-in-slot list", p.best_in_slot), ("trinket advice", p.trinkets))
                      if fold(d.name) in fold(text)]


async def boss_loot_text(boss: str, spec: str | None = None) -> str:
    """boss_guide's loot topic."""
    loot = await boss_loot(boss, spec)
    lines = [f"# What {boss} drops, from Wowhead's guide ({updated(loot.last_updated)}). Source: Wowhead {loot.url}"]
    if loot.levels:
        lines.append("Item level of the drops: " + ", ".join(
            f"{'LFR' if d.lower().startswith(('lfr', 'raid f')) else d.title()} {lvl}"
            + (f" ({track})" if track else "") for d, lvl, track in loot.levels))
        named = {"LFR" if d.lower().startswith(("lfr", "raid f")) else d.title() for d, _, _ in loot.levels}
        if missing := [d for d in DIFFICULTIES if d not in named]:
            lines.append(f"Not in Wowhead's table: {', '.join(missing)}; its item level and upgrade track aren't "
                         "known here (a gear page or the raider's own sim may give them)")
    who = (spec or "").title()
    if loot.gear_pages:
        read = "; ".join(f"{p.site} {p.url} ({updated(p.last_updated)})" for p in loot.gear_pages)
        lines.append(f"{who}'s gear pages read: {read}"
                     + (f" (not available: {', '.join(loot.gear_pages_missing)})" if loot.gear_pages_missing else ""))
    lines.append("## Gear (tooltip stats are at the item level Wowhead shows, not the drop's)")
    for d in loot.drops:
        line = f"- {d.name} (item {d.id}), {d.slot}"
        if d.stats is not None:
            line += f": {d.stats}" + (f" [tooltip at item level {d.tooltip_level}]" if d.tooltip_level else "")
        if loot.gear_pages:
            token = "token" in d.slot.lower()
            line += f"\n  {who}: " + (
                "named in " + ", ".join(d.named_in) if d.named_in
                else "a tier token; the gear pages name the set piece it makes, not the token" if token
                else "not named in their gear pages")
        lines.append(line)
    if loot.others:
        lines.append("Also drops: " + ", ".join(loot.others))
    return "\n".join(lines)
