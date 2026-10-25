"""Spells from Wowhead's tooltips (name, text, cooldown, what a defensive does) and Lorrgs (Bloodlust's debuffs)."""
import base64
import html
import re
from dataclasses import dataclass

import httpx

from ..cache import DAY, cache, cache_key
from . import fetch
from .fetch import KnowledgeError, Memo, NotFound

ICON_URL = "https://wow.zamimg.com/images/wow/icons/large/{}.jpg"  # a Wowhead icon by name, 56 px


@dataclass(frozen=True)
class Spell:
    """A spell as Wowhead's tooltip has it. `name` is 'spell <id>' when Wowhead gives none."""
    id: int
    name: str
    text: str  # the tooltip as text, '' when it has none
    url: str
    icon: str = ""  # the icon's name on Wowhead ("inv_ability_poison_beam"): ICON_URL


async def tooltip(spell_id: int) -> Spell:
    data = await fetch.cli("wowhead", "entity", "spell", str(spell_id))
    entity, tip = data.get("entity") or {}, data.get("tooltip") or {}
    text = tooltip_text(tip.get("html") or "") or (tip.get("text") or "").strip()
    return Spell(spell_id, entity.get("name") or f"spell {spell_id}", text, entity.get("page_url", ""),
                 tip.get("icon") or "")


def tooltip_text(markup: str) -> str:
    """A Wowhead tooltip's HTML as text, keeping what applies to one spec only, "[Blood: and forcing the enemy to
    attack you]": warcraft_cli's own text drops those parts (Death Grip's taunt, on 14 Oct 2026)."""
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", " ", markup)))
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([\]:.,])", r"\1", re.sub(r"\[\s+", "[", text)).strip()


@dataclass(frozen=True)
class Effect:
    """What a defensive does, read from its Wowhead tooltip: what the death recap's replay applies to the hits."""
    # "reduction" (pct of damage), "immunity", "heal" (pct of maximum health, up to what's missing), "absorb" (pct
    # of maximum health on top of it: a shield, or temporary health)
    kind: str
    pct: float | None = None
    only: str | None = None  # "magic" or "aoe" when it covers only that damage
    duration_s: float | None = None
    radius_yd: float | None = None  # a raid cooldown that reaches only those within this range of it
    sure_s: float | None = None  # how long the effect is sure when less than its duration ("a moment of immunity")

    def __str__(self) -> str:
        if self.kind == "immunity":  # no pct: the other wordings mustn't be formatted
            what = f"immune to {self.only or 'all'} damage"
        elif self.kind == "heal":
            what = f"heals {self.pct:g}% of maximum health"
        elif self.kind == "absorb":
            what = f"absorbs {self.pct:g}% of maximum health"
        else:
            what = f"{self.pct:g}% less {self.only + ' ' if self.only else ''}damage"
        return what + (f" for {self.duration_s:g} s" if self.duration_s else "") + (
            f", within {self.radius_yd:g} yd" if self.radius_yd else "")


_NUM = r"(\d+(?:\.\d+)?)"
MOMENT_S = 1.0  # "a moment of immunity": what's sure of it
_REDUCTION = [  # (pattern, the group with the %, where "magic"/"aoe" is said); tooltips' own wording
    re.compile(r"reduc\w*(?: the)?(?: all)? (magic )?damage (?:you(?: and your pet)? take|taken)"
               r"(?: from (area-of-effect) attacks)?"
               r"(?: by [^%.]*?)? by " + _NUM + r" ?%", re.I),
    re.compile(r"tak\w* " + _NUM + r" ?% less damage", re.I),
    re.compile(r"" + _NUM + r" ?% reduced damage", re.I),
    re.compile(r"damage reduction increased to " + _NUM + r" ?%", re.I),
]
_COOLDOWN = re.compile(r"(\d+(?:\.\d+)?) (sec|min) cooldown", re.I)


def parse_effect(text: str) -> Effect | None:
    """A defensive's effect from its tooltip text, or None when the wording isn't one of the known kinds (then the
    recap says it can't tell, never guesses)."""
    dur = re.search(r"(?:for|lasts) " + _NUM + r" sec", text, re.I)
    radius = re.search(r"within " + _NUM + r" (?:yards|yds)", text, re.I)
    duration_s, radius_yd = float(dur[1]) if dur else None, float(radius[1]) if radius else None
    if re.search(r"immun\w* to all damage|from all attacks and damage", text, re.I):
        return Effect("immunity", duration_s=duration_s, radius_yd=radius_yd)
    if re.search(r"magic immunity", text, re.I):  # Cloak of Shadows: "a moment of magic immunity", then resists
        moment = MOMENT_S if re.search(r"a moment of magic immunity", text, re.I) else None
        return Effect("immunity", only="magic", duration_s=duration_s, radius_yd=radius_yd, sure_s=moment)
    for pattern in _REDUCTION:
        if m := pattern.search(text):
            groups = [g for g in m.groups() if g]
            only = ("magic" if any(g.strip().lower() == "magic" for g in groups[:-1])
                    else "aoe" if any(g.lower() == "area-of-effect" for g in groups[:-1]) else None)
            return Effect("reduction", float(groups[-1]), only, duration_s, radius_yd)
    # "absorbing [35 / 100 * Total Health * (1 + Versatility)] damage" (Ice Barrier; Versatility left out, so it's
    # the least it absorbs), "granting ... 10% temporary and maximum health" (Rallying Cry)
    if m := (re.search(r"absorbing \[" + _NUM + r" ?/ ?100 ?\* ?Total Health", text, re.I)
             or re.search(r"" + _NUM + r" ?% temporary and maximum health", text, re.I)):
        return Effect("absorb", float(m[1]), duration_s=duration_s, radius_yd=radius_yd)
    # "Heals you for 30% and your pet for 100% of maximum health" (Exhilaration): the first % is the player's
    if m := re.search(r"(?:heals you for|restores) " + _NUM + r" ?% (?:[^.%]*?% )?(?:of (?:your )?maximum )?health",
                      text, re.I):
        return Effect("heal", float(m[1]), duration_s=duration_s, radius_yd=radius_yd)
    # "Increases maximum health by [Light's Inspiration: 35 / 25]% ... and instantly heals you for that amount"
    # (Desperate Prayer): a talent's value and the base one; the smaller, so a verdict never rests on the talent
    if m := re.search(r"increases maximum health by \[?[^\]%]*?((?:\d+(?:\.\d+)? ?/ ?)*\d+(?:\.\d+)?)\]? ?%[^.]*"
                      r"heals you for that amount", text, re.I):
        pct = min(float(n) for n in re.findall(r"\d+(?:\.\d+)?", m[1]))
        return Effect("heal", pct, duration_s=duration_s, radius_yd=radius_yd)
    return None


async def defensive_effect(spell_id: int) -> Effect | None:
    """A defensive's effect from its Wowhead tooltip; None when the tooltip's wording isn't a known kind or Wowhead
    has no such spell."""
    try:
        return parse_effect((await tooltip(spell_id)).text)
    except NotFound:
        return None


async def spell_cooldown(spell_id: int) -> float | None:
    """A spell's base cooldown in seconds from its Wowhead tooltip ("15 sec cooldown"; a talent can shorten it);
    None when the tooltip names none or Wowhead has no such spell."""
    try:
        m = _COOLDOWN.search((await tooltip(spell_id)).text)
    except NotFound:
        return None
    return float(m.group(1)) * (60 if m.group(2).lower() == "min" else 1) if m else None


_spell_names = Memo()


async def spell_name(spell_id: int) -> str | None:
    """A spell's name from Wowhead, for ids a report doesn't name (e.g. auras present at the pull); None when
    Wowhead has no name for it. Kept a day."""
    if spell_id not in _spell_names:
        try:
            name = (await tooltip(spell_id)).name
            _spell_names.keep(spell_id, None if name == f"spell {spell_id}" else name)
        except NotFound:
            _spell_names.keep(spell_id, None)
    return _spell_names[spell_id]


async def find_spell(spell: str) -> Spell:
    """A spell by id or name. Names are ambiguous (a boss's Tempest and a shaman talent share one), so ids from the
    log are better."""
    if spell.strip().isdigit():
        return await tooltip(int(spell))
    data = await fetch.cli("wowhead", "resolve", "--entity-type", "spell", "--", spell)
    match = data.get("match") or {}
    if not match.get("id"):
        raise NotFound(f"Wowhead has no spell matching {spell!r}")
    return await tooltip(int(match["id"]))


async def spell_text(spell: str) -> str:
    """spell_info's answer: what a spell does, by id or name."""
    s = await find_spell(spell)
    return f"{s.name} (spell {s.id}): {s.text or 'no tooltip text'}\nSource: Wowhead {s.url}"


_granted_by = Memo()


async def items_granting(spell_id: int) -> set[int]:
    """The items whose use gives a spell or aura (Wowhead's links), e.g. a flask's buff -> the flask items; none when
    Wowhead has no such spell. Kept a day."""
    if spell_id not in _granted_by:
        try:
            data = await fetch.cli("wowhead", "entity-page", "spell", str(spell_id))
        except NotFound:
            data = {}
        links = (data.get("linked_entities") or {}).get("items") or []
        _granted_by.keep(spell_id, {x["id"] for x in links if x.get("entity_type") == "item" and x.get("id")})
    return _granted_by[spell_id]


_lust = Memo()


async def _bloodlust() -> dict:
    """Lorrgs' Bloodlust, with its variations: the debuffs it and its kin leave. Kept a day."""
    if "all" not in _lust:
        data = await fetch.cli("lorrgs", "spell", "2825")
        if not data.get("variations"):
            raise KnowledgeError("Lorrgs lists no debuffs for Bloodlust")
        _lust.keep("all", data)
    return _lust["all"]


async def lust_debuffs() -> list[int]:
    """The debuffs Bloodlust and its kin leave (Sated, Exhaustion, Temporal Displacement, ...): the variations
    Lorrgs lists for Bloodlust."""
    return sorted(set((await _bloodlust())["variations"]))


async def lust_duration_s() -> float:
    """How long Bloodlust and its kin last, as Lorrgs gives Bloodlust's duration."""
    return float((await _bloodlust()).get("duration") or 0)


async def spell_icon(spell_id: int) -> bytes:
    """A spell's icon from Wowhead, kept on disk; KnowledgeError when Wowhead has none."""
    icon = (await tooltip(spell_id)).icon
    if not icon:
        raise KnowledgeError(f"Wowhead has no icon for spell {spell_id}")

    async def get() -> str:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Wipebot"}) as client:
            r = await client.get(ICON_URL.format(icon))
            r.raise_for_status()
            return base64.b64encode(r.content).decode()
    try:
        return base64.b64decode(await cache().fetch(cache_key("wowhead-icon", icon), 365 * DAY, get))
    except httpx.HTTPError as e:
        raise KnowledgeError(f"Wowhead's icon {icon} can't be fetched: {e}") from e
