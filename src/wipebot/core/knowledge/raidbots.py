"""Raidbots' game data files (warcraft_cli doesn't cover them): talent trees, consumables, enchants."""
import re

import httpx

from ..cache import DAY, cache, cache_key
from .fetch import KnowledgeError, Memo

RAIDBOTS_URL = "https://www.raidbots.com/static/data/live/{}"


async def raidbots(file: str) -> list:
    """One of Raidbots' generated game data files (talents.json, flasks.json, ...), kept in the cache for a day; a
    stale copy when Raidbots can't be reached."""
    async def get() -> list | dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(RAIDBOTS_URL.format(file))
            r.raise_for_status()
            return r.json()
    try:
        data = await cache().fetch(cache_key("raidbots", file), DAY, get)
    except (httpx.HTTPError, ValueError) as e:
        raise KnowledgeError(f"Raidbots' {file} can't be fetched: {e}") from e
    if not isinstance(data, list):
        raise KnowledgeError(f"Raidbots' {file} isn't a list")
    return [x for x in data if isinstance(x, dict)]


_talents = Memo()


async def talent_names() -> dict[int, str]:
    """Talent entry id (WCL's talentTree `id`, rankings' `talentID`) -> talent name. Kept a day. The two versions
    of a spell on one choice node (Earthquake, Blizzard) share a name, so each gets its spell id: "Earthquake
    [61882]"."""
    if "all" not in _talents:
        versions = await choice_versions()
        _talents.keep("all", {e["id"]: e["name"] + (f" [{e['spellId']}]" if e["id"] in versions else "")
                              for node in await _nodes() for e in node.get("entries") or []
                              if "id" in e and e.get("name")})
    return _talents["all"]


async def choice_versions() -> dict[int, int]:
    """Talent entry id -> the other version of the same spell on its choice node, for the nodes whose two choices
    share a name."""
    if "versions" not in _talents:
        pairs = {}
        for node in await _nodes():
            es = [e for e in node.get("entries") or [] if "id" in e and e.get("name")]
            if node.get("type") == "choice" and len(es) == 2 and es[0]["name"] == es[1]["name"]:
                pairs |= {es[0]["id"]: es[1]["id"], es[1]["id"]: es[0]["id"]}
        _talents.keep("versions", pairs)
    return _talents["versions"]


async def _nodes() -> list[dict]:
    return [node for spec in await talent_trees() for key in ("classNodes", "specNodes", "heroNodes", "subTreeNodes")
            for node in spec.get(key) or []]


async def talent_trees() -> list[dict]:
    """Raidbots' talent trees, one per spec (className, specName, specId, the nodes, fullNodeOrder)."""
    return await raidbots("talents.json")


async def consumable_items() -> dict[str, dict[int, str]]:
    """This expansion's consumables from Raidbots, id -> name: the items of flasks and augment runes, and the
    enchants of weapon oils and stones (temporary enchants)."""
    out: dict[str, dict[int, str]] = {}
    for kind, file, key in (("flask", "flasks.json", "itemId"), ("augment rune", "augments.json", "itemId"),
                            ("weapon oil", "temp-enchants.json", "effectId")):
        out[kind] = {x[key]: re.sub(r" \(Quality \d\)$", "", x.get("name", "")) for x in await raidbots(file)
                     if x.get(key)}
    return out


async def enchant_names() -> dict[int, str]:
    """Enchants from Raidbots, enchant id -> name ("Akil'zon's Swiftness", "Forest Hunter's Armor Kit"): the ids
    Warcraft Logs gives for gear's permanent enchants, the top parses' included."""
    return {x["id"]: x.get("itemName") or x.get("baseDisplayName") or x.get("displayName") or f"enchant {x['id']}"
            for x in await raidbots("enchantments.json") if x.get("id")}
