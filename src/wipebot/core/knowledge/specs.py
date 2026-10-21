"""A spec's guides (Method, Wowhead, Icy Veins: a topic's sections, its gear pages) and its cooldowns and role
(Lorrgs)."""
import re
from dataclasses import dataclass

from . import fetch
from .fetch import MEMO_S, KnowledgeError, Memo, NotFound
from .guides import (
    GUIDE_SOURCES,
    OFF_TOPIC,
    SOURCE_NAMES,
    Guide,
    cut,
    sections,
    spec_guide_ref,
    subpages,
    updated,
    wowhead_guide_updated,
)
from .names import lorrgs_slug, spaced
from .talents import class_and_spec

GUIDE_SECTION_CHARS = 2600
GUIDE_SECTIONS = 8
STOP_WORDS = {"and", "the", "for", "with", "how", "what", "when"}
# A spec guide's gear sections: its best-in-slot lists, and what it says about trinkets.
BIS_TITLE = re.compile(r"best in slot|best gear|\bbis\b", re.I)
TRINKET_TITLE = re.compile(r"trinket", re.I)


def stem(word: str) -> str:
    """'cooldowns', 'cooldown' -> 'coold'; 'defensives', 'defensive' -> 'defen': a topic word matched by its start."""
    return word[:5]


def topic_words(topic: str | None) -> list[str]:
    return [stem(w) for w in re.findall(r"\w+", (topic or "").lower()) if len(w) > 2 and w not in STOP_WORDS]


PAGE_HOSTS = {"method": "method.gg", "wowhead": "wowhead.com", "icy-veins": "icy-veins.com"}


async def spec_guide(spec: str, topic: str | None = None, source: str = "method", page: str | None = None) -> Guide:
    """A class guide page from Method, Wowhead or Icy Veins with the sections on `topic` (all of them without one),
    and its other pages (rotation, talents, raid tips, ...). A sub-page titled with the topic (Method's "Playstyle &
    Rotation", Wowhead's "Rotation", also for cooldowns) answers in place of the page found; on it, a section whose
    title matches the topic ranks above one whose text does. Raid sections only, unless the topic is about other
    content (leveling, Mythic+, PvP)."""
    if page:  # a page is read from its own site, whatever source came with it (the default's Method)
        source = next((s for s, host in PAGE_HOSTS.items() if host in page), source)
    if source not in GUIDE_SOURCES:
        raise KnowledgeError("source must be " + ", ".join(GUIDE_SOURCES))
    ref = page or await spec_guide_ref(spec, source)
    words = topic_words(topic)
    off_topic_asked = bool(OFF_TOPIC.search(topic or ""))

    def pick(found: list[dict]) -> list[dict]:
        found = [s for s in found if off_topic_asked or not OFF_TOPIC.search(s.get("title") or "")]
        if not words:
            return found
        by_title = [s for s in found if any(w in (s.get("title") or "").lower() for w in words)]
        by_text = [s for s in found if s not in by_title
                   and all(w in (s.get("content_text") or s.get("text") or "").lower() for w in words)]
        # The topic's own page first ("Rotation: ..." before "Overview: Rotational Aspects").
        by_title.sort(key=lambda s: not any(w in (s.get("title") or "").split(": ")[0].lower() for w in words))
        return by_title + by_text

    data = await fetch.cli(source, "guide-full", "--", ref)
    picked = pick(sections(data))
    if words and not page and "pages" not in data:  # a guide fetched whole (Method, Icy Veins) has every page
        sub = next((u for t, u in subpages(source, data)
                    if any(w in t.lower() or w in u.rsplit("/", 1)[-1].lower() for w in words)), None)
        if sub:
            data = await fetch.cli(source, "guide-full", sub)
            picked = pick(sections(data)) or sections(data)
    guide = data.get("guide") or {}
    url = guide.get("page_url") or (data.get("page") or {}).get("url") or ref
    when = guide.get("last_updated")
    if not when and source == "wowhead":
        when = await wowhead_guide_updated(spec, url, guide.get("id"))
    return Guide(source, f"{SOURCE_NAMES[source]} guide", url, when,
                 [(s.get("title") or "", (s.get("content_text") or s.get("text") or "").strip()) for s in picked],
                 subpages(source, data))


async def spec_guide_text(spec: str, topic: str | None = None, source: str = "method", page: str | None = None) -> str:
    """spec_guide's answer (but for the cooldowns topic): with a topic, its first sections, each cut; without, every
    section's start. Then the guide's other pages."""
    g = await spec_guide(spec, topic, source, page)
    lines = [f"{g.title}: {g.url} ({updated(g.last_updated)})"]
    if topic_words(topic):
        lines += [f"## {title}\n{cut(text, GUIDE_SECTION_CHARS)}" for title, text in g.sections[:GUIDE_SECTIONS]]
        if any(len(text) > GUIDE_SECTION_CHARS for _, text in g.sections[:GUIDE_SECTIONS]) \
                or len(g.sections) > GUIDE_SECTIONS:
            lines.append(f"(Cut here: web_lookup with {g.url} reads the whole page.)")
        if not g.sections:
            lines.append(f"No section on this page matches {topic!r}.")
    else:
        lines += [f"## {title}: {text[:200]} ..." for title, text in g.sections]
    if g.pages:
        lines.append("Other pages of this guide (pass one as `page`): "
                     + "; ".join(f"{t.strip()} = {u}" for t, u in g.pages[:20]))
    return "\n\n".join(lines)


@dataclass
class GearPages:
    """A spec guide's gear pages on one site."""
    source: str
    url: str
    last_updated: str | None
    best_in_slot: str  # its best-in-slot lists' text
    trinkets: str  # its trinket sections' text

    @property
    def site(self) -> str:
        return SOURCE_NAMES[self.source]


async def gear_pages(spec: str, source: str) -> GearPages:
    """A spec guide's gear pages on one site. Method and Icy Veins hold every page in one fetch; Wowhead's are on a
    sub-page of their own."""
    ref = await spec_guide_ref(spec, source)
    data = await fetch.cli(source, "guide-full", "--", ref)
    if source == "wowhead":
        sub = next((u for t, u in subpages(source, data) if re.search(r"\bbis\b|gear", t, re.I)), None)
        if not sub:
            raise KnowledgeError(f"Wowhead's {spec} guide has no gear page")
        data = await fetch.cli(source, "guide-full", sub)
    # The page with the best-in-slot list dates it, not the guide's first page.
    page = next((p for p in data.get("pages") or [] if any(BIS_TITLE.search(s.get("title") or "")
                 for s in (p.get("article") or {}).get("sections") or [])), None)
    guide = (page or {}).get("guide") or data.get("guide") or {}
    url = guide.get("page_url") or (data.get("page") or {}).get("url") or ref
    when = guide.get("last_updated") or (await wowhead_guide_updated(spec, url, guide.get("id"))
                                         if source == "wowhead" else None)
    found = [s for s in sections(data) if not OFF_TOPIC.search(s.get("title") or "")]

    def text(title: re.Pattern) -> str:
        return "\n".join(s.get("content_text") or s.get("text") or "" for s in found
                         if title.search(s.get("title") or "") or (source == "wowhead" and title is BIS_TITLE))
    return GearPages(source, url, when, text(BIS_TITLE), text(TRINKET_TITLE))


# ---------- Lorrgs ----------

_spec_spells = Memo()
_spec_roles = Memo()  # Lorrgs slug -> role
NOT_TRACKED_S = 10 * 60  # a spec Lorrgs said it doesn't track is asked again after this, not after a day


async def spec_spells(class_name: str, spec: str) -> list[dict]:
    """The spec's tracked cooldowns from Lorrgs: name, spell id, cooldown, duration, tags (defensive,
    raid_cd, dps, tank, move). Class and spec spells only, no trinkets or racials; none for a spec Lorrgs doesn't
    track. Kept a day."""
    slug = lorrgs_slug(class_name, spec)
    if slug not in _spec_spells:
        try:
            data, keep_s = await fetch.cli("lorrgs", "spec-spells", slug), MEMO_S
        except NotFound:
            data, keep_s = {}, NOT_TRACKED_S
        own = (class_name.replace(" ", "").lower(), slug)
        _spec_spells.keep(slug, [s for s in data.values() if isinstance(s, dict) and s.get("spell_type") in own],
                          keep_s)
    return _spec_spells[slug]


async def spec_role(class_name: str, spec: str) -> str:
    """A spec's role from Lorrgs: tank, heal, mdps or rdps. Kept a day."""
    if "all" not in _spec_roles:
        data = await fetch.cli("lorrgs", "specs")
        _spec_roles.keep("all", {s["full_name_slug"]: s["role"] for s in data.get("specs") or []
                                 if s.get("full_name_slug") and s.get("role")})
    role = _spec_roles["all"].get(lorrgs_slug(class_name, spec))
    if not role:
        raise KnowledgeError(f"Lorrgs has no role for {spec} {class_name}")
    return role


async def heals(class_name: str, spec: str) -> bool:
    """Whether a spec is a healer's by Lorrgs' role ("heal"), for one not played in a report, whose Warcraft Logs
    role ("healer") a Player has."""
    return await spec_role(class_name, spec) == "heal"


async def spec_cooldowns_text(spec: str) -> str:
    """spec_guide's cooldowns topic: the spec's cooldowns Lorrgs tracks. `spec` as a raider writes it."""
    class_name, spec = await class_and_spec(spec)
    spells = await spec_spells(class_name, spec)
    if not spells:
        raise KnowledgeError(f"Lorrgs has no cooldown list for {spec} {class_name}")
    rows = sorted(spells, key=lambda s: (",".join(s.get("tags") or ["~"]), s["name"]))
    lines = [f"{spaced(spec)} {spaced(class_name)} cooldowns tracked by Lorrgs (spell id, cooldown, duration, tags):"]
    for s in rows:
        lines.append(f"- {s['name']} ({s['spell_id']}): {s.get('cooldown', 0):.0f} s cd, {s.get('duration', 0):.0f} s, "
                     f"{', '.join(s.get('tags') or []) or 'untagged'}")
    return ("\n".join(lines) + "\nOnly these are tracked: a spell not listed (a taunt, an interrupt, utility) isn't "
            "missing from the spec; spell_info gives its cooldown.\nSource: Lorrgs https://lorrgs.io")
