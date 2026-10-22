"""A player's build and preparation against the top parses of their spec (`compare_with_top` build), and
the raid's (`raid_prep`)."""
import asyncio
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError, spaced
from ..pulls import DIFFICULTIES
from ..text import clock, plural
from ..wcl import WCLError
from .base import FactError, Player, Report, unavailable
from .ranks import NO_TOP, metric_of, readable, top_parses

TOP_PARSES = 50  # top parses a build is compared with
STAT_PARSES = 10  # top parses whose stats are read from their own logs (one query, kept for good)
SECONDARIES = ("Crit", "Haste", "Mastery", "Versatility")
SAME_HERO_MIN = 10  # enough top parses of the player's hero tree to compare talents with those alone
MAX_BUILD_TALENTS = 90  # a guide build that decodes to more talents than any build has wasn't read right
# WoW's inventory slot ids, a client constant (no API names them).
SLOTS = {0: "head", 1: "neck", 2: "shoulders", 4: "chest", 5: "waist", 6: "legs", 7: "feet", 8: "wrists",
         9: "hands", 10: "ring", 11: "ring", 12: "trinket", 13: "trinket", 14: "back", 15: "main hand",
         16: "off hand"}


async def hero_of(rep: Report, p: Player, det: dict) -> str | None:
    """The player's hero talent tree on a pull, from their talents and Raidbots' tree."""
    talents = (det.get("combatantInfo") or {}).get("talentTree") or []
    tree = await knowledge.tree_for(p.class_name, p.spec)
    return knowledge.hero_tree(tree, {t.get("nodeID") for t in talents}) if tree and talents else None


async def consumables(rep: Report, f: dict) -> dict[int, dict[str, list[str]]]:
    """Per player id, the consumables at the pull: flask, food, augment rune and vantus rune (auras at the
    start) and weapon oil, from the combatant info (advanced combat logging)."""
    listed = await knowledge.consumable_items()  # Raidbots' flasks, augment runes, weapon oils
    flasks = set(listed["flask"].values())
    out: dict[int, dict[str, list[str]]] = {}
    for info in await rep.events(f["id"], "CombatantInfo"):
        found: dict[str, list[str]] = {k: [] for k in ("flask", "food", "augment rune", "vantus rune", "oil")}
        unknown: list[tuple[int, str]] = []
        for a in info.get("auras") or []:
            gid = a.get("ability")
            if not gid:
                continue
            name = rep.abilities.get(gid) or a.get("name") or await _spell_name(gid)
            # Food buffs and vantus runes aren't in any game data list, so their names decide; a flask's or
            # augment rune's aura is usually named like the item (the augment rune's "Void-Touched").
            if "well fed" in name.lower():
                found["food"].append(name)
            elif name.lower().startswith("vantus rune"):
                found["vantus rune"].append(name)
            elif name in flasks:
                found["flask"].append(name)
            elif any(item.startswith(name) for item in listed["augment rune"].values()):
                found["augment rune"].append(name)
            else:
                unknown.append((gid, name))
        if not found["flask"]:  # an aura named unlike its flask: ask Wowhead which item gives it
            for gid, name in unknown:
                if await knowledge.items_granting(gid) & listed["flask"].keys():
                    found["flask"].append(name)
        weapons = [g for slot, g in enumerate(info.get("gear") or []) if slot in (15, 16)]  # in slot order
        found["oil"] = sorted({listed["weapon oil"][g["temporaryEnchant"]] for g in weapons
                               if g.get("temporaryEnchant") in listed["weapon oil"]})
        if not found["oil"] and any(g.get("temporaryEnchant") for g in weapons):
            found["oil"] = ["a class imbue or poison instead"]
        out[info.get("sourceID", 0)] = found
    return out


async def _spell_name(gid: int) -> str:
    """An aura's name from Wowhead, else its number."""
    try:
        return await knowledge.spell_name(gid) or f"spell {gid}"
    except KnowledgeError:
        return f"spell {gid}"


async def prepared(rep: Report, f: dict, p: Player) -> str:
    """Consumables at the pull: flask, food, augment rune, vantus rune (auras at the start), weapon oil."""
    try:
        found = (await consumables(rep, f)).get(p.id)
    except KnowledgeError as e:
        return unavailable("Consumables at the pull", e)
    if not found:
        return "Consumables at the pull: not in the log (needs advanced combat logging)."
    return ("Consumables at the pull: " + " · ".join(f"{kind} {', '.join(names) or 'none'}"
                                                     for kind, names in found.items() if kind != "oil")
            + f" · weapon oil or stone {', '.join(found['oil']) or 'none'}")


async def _build_gaps(rep: Report, f: dict, p: Player) -> dict | str:
    """A player's build next to the top parses of their spec on the boss: talents most of them take that the
    player doesn't, and the other way round, trinkets, and enchants and gems most of them have. A string when
    that can't be told."""
    det = (await rep.details(f)).get(p.id) or {}
    info = det.get("combatantInfo") or {}
    if not info.get("talentTree"):
        return f"{p.name}'s talents and gear aren't in the log for {rep.label(f)} (needs advanced combat logging)."
    metric = metric_of(p)
    top = (await top_parses(rep, f, p))[:TOP_PARSES]
    if not top:
        return NO_TOP.format(spec=spaced(p.spec), cls=spaced(p.class_name), boss=f["name"])
    # Talents are compared with the top parses of the player's hero tree when there are enough of them: the
    # other tree's build differs by design. Gear is compared with all of them.
    hero = await hero_of(rep, p, det)
    tree = await knowledge.tree_for(p.class_name, p.spec)
    same_hero = [r for r in top if hero and tree and knowledge.build_hero(
        tree, {t.get("talentID") for t in r.get("talents") or []}) == hero]
    pool = same_hero if len(same_hero) >= SAME_HERO_MIN else top
    n = len(pool)
    mine = {t["id"]: t.get("rank", 1) for t in info["talentTree"] if t.get("rank", 1) > 0}
    taken: Counter = Counter()
    trinkets: Counter = Counter()
    enchanted: Counter = Counter()
    enchants: dict[int, Counter] = defaultdict(Counter)  # slot -> the top's enchant ids on it
    gemmed: Counter = Counter()
    for r in pool:
        taken.update({t["talentID"] for t in r.get("talents") or [] if t.get("points", 1) > 0})
    for r in top:
        for slot, g in enumerate(r.get("gear") or []):
            if slot in (12, 13) and g.get("name"):
                trinkets[g["name"]] += 1
            if g.get("permanentEnchant"):
                enchanted[slot] += 1
                enchants[slot][int(g["permanentEnchant"])] += 1
            if g.get("gems"):
                gemmed[slot] += 1
    gear = info.get("gear") or []
    by_slot = {g.get("slot"): g for g in gear if g.get("id")}
    enchant_names = await knowledge.enchant_names()

    def enchant(e: int | str | None, fallback: str | None = None) -> str:
        return (enchant_names.get(int(e or 0)) or re.sub(r"^Enchant \w+ - ", "", fallback or "")
                or f"enchant {e}")
    # Per slot most top players enchant: (slot, theirs or None, the item they wear, the top's enchants by use).
    enchant_slots = [(SLOTS[slot], enchant(g["permanentEnchant"], g.get("permanentEnchantName"))
                      if g.get("permanentEnchant") else None, g.get("name") or "?",
                      [(enchant(e), c) for e, c in enchants[slot].most_common(3)])
                     for slot in SLOTS if (g := by_slot.get(slot)) and enchanted[slot] / len(top) >= 0.7]
    return {
        "det": det, "metric": metric, "n": n, "n_all": len(top), "hero": hero, "same_hero": len(same_hero),
        "mine": mine, "taken": taken, "trinkets": trinkets,
        "top": top,
        "durations": sorted(float(r.get("duration") or 0) for r in top),
        "ilvls": sorted(float(r.get("bracketData") or 0) for r in top),
        "missing": sorted((i for i in taken if i not in mine and taken[i] / n >= 0.6), key=lambda i: -taken[i]),
        "unusual": sorted((i for i in mine if taken[i] / n <= 0.25), key=lambda i: taken[i]),
        "my_trinkets": [g.get("name") for g in gear if g.get("slot") in (12, 13) and g.get("name")],
        "no_enchant": [f"{slot} (worn: {item})" for slot, theirs, item, _ in enchant_slots if theirs is None],
        "enchants": enchant_slots,
        "no_gem": [f"{SLOTS[slot]} ({by_slot[slot].get('name')})" for slot in SLOTS if slot in by_slot
                   and gemmed[slot] / len(top) >= 0.7 and not by_slot[slot].get("gems")],
    }


async def compare_build(rep: Report, player: str, pull: int | None = None, boss: str | None = None) -> str:
    """A player's talents, item level, trinkets, enchants and gems on a pull, next to the top parses of their
    spec on the same boss and difficulty, and to their guides' raid builds."""
    f = rep.fight(pull, boss)
    p = rep.player(player)
    rep.require_in(f, p)
    p = rep.in_pull(f, p)
    gaps = await _build_gaps(rep, f, p)
    if isinstance(gaps, str):
        return gaps
    names, parts = await knowledge.talent_names(), await knowledge.talent_parts()

    def named(i: int) -> str:
        return names.get(i, f"talent {i}")

    n, n_all, taken, mine = gaps["n"], gaps["n_all"], gaps["taken"], gaps["mine"]
    half = n_all // 2
    lines = [f"{p.label} on {rep.label(f)} against the top {plural(n_all, gaps['metric'] + ' parse')} of their spec on "
             f"{f['name']} ({DIFFICULTIES.get(f.get('difficulty'), '?')}); their median kill took "
             f"{clock(gaps['durations'][half])}, median item level {gaps['ilvls'][half]:.0f} (theirs "
             f"{float(gaps['det'].get('maxItemLevel') or 0):.0f})."]
    if gaps["hero"]:
        lines.append(f"Hero tree: {gaps['same_hero']} of the top {n_all} play {gaps['hero']} as they do; talents "
                     + (f"compared with those {n}." if gaps["same_hero"] >= SAME_HERO_MIN
                        else f"compared with all {n_all} (fewer than {SAME_HERO_MIN} play their tree)."))
    lines.append(f"Talents: {len(mine) - len(gaps['unusual'])} of their {plural(len(mine), 'pick')} are common "
                 "among the top. By part of the tree: a talent only replaces one in its own part (class, spec or "
                 "hero), which has its own points.")
    lines.append("  Most top players take, they don't: " + (by_part(
        [(parts.get(i, ""), f"{named(i)} ({100 * taken[i] // n}%)") for i in gaps["missing"][:12]]) or "none"))
    if gaps["missing"]:
        lines.append("  Talents most top players take and they don't may be AoE or utility choices: check the "
                     "spec guide's single-target build before calling one a gap.")
    lines.append("  They take, few top players do: " + (by_part(
        [(parts.get(i, ""), f"{named(i)} ({100 * taken[i] // n}%)") for i in gaps["unusual"][:12]]) or "none"))
    versions = await knowledge.choice_versions()
    if swapped := [(i, versions[i]) for i in gaps["missing"] if versions.get(i) in gaps["unusual"]]:
        differences = await asyncio.gather(*(knowledge.versions_difference(theirs, i) for i, theirs in swapped))
        lines.append("  Of those, two versions of one spell on a choice node, where they take the other: " + "; ".join(
            f"{named(theirs)} ({100 * taken[theirs] // n}%) for {named(i)} ({100 * taken[i] // n}%)"
            + (f": {difference}" if difference else "") for (i, theirs), difference in zip(swapped, differences,
                                                                                          strict=True)))
    lines += await _against_guides(rep, p, gaps["det"], mine, named, parts)
    lines.append("Trinkets: theirs " + (", ".join(gaps["my_trinkets"]) or "unknown") + "; most used by the top: "
                 + ", ".join(f"{t} ({100 * c // n_all}%)" for t, c in gaps["trinkets"].most_common(5)))
    lines += _enchant_lines(gaps["enchants"], n_all)
    lines.append("Gems most top players have and they don't: " + (", ".join(gaps["no_gem"]) or "none missing"))
    lines.append(gear_stats(gaps["det"]))
    lines.append(await _top_stats(rep, gaps))
    lines.append(await prepared(rep, f, p))
    try:
        if code := await rep.keep(("talent code", f["id"], p.id), [f["id"]],
                                  lambda: rep.wcl.talent_code(rep.code, f["id"], p.id), FOREVER):
            lines.append(f"Their talents as an import string (paste into the game's talent window): {code}")
    except WCLError:
        pass
    lines.append("Top parses are the highest-damage kills; what they take is popular, not proven best for a "
                 "progression pull. Sources: Warcraft Logs rankings https://www.warcraftlogs.com/zone/rankings/"
                 f"{rep.zone_id or ''}, talent names from Raidbots https://www.raidbots.com")
    return "\n".join(lines)


def by_part(items: list[tuple[str, str]]) -> str:
    """(part of the tree, text) pairs as "class: a, b; spec: c", the parts in the tree's order."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for part, text in items:
        grouped[part or "other"].append(text)
    return "; ".join(f"{part}: {', '.join(texts)}" for part in ("class", "spec", "hero", "other")
                     if (texts := grouped.get(part)))


def _enchant_lines(slots: list[tuple[str, str | None, str, list[tuple[str, int]]]], n_top: int) -> list[str]:
    """Enchants on the slots most top players enchant: theirs next to the top's where they lack one or use another;
    the rest named once."""
    differ, same = [], []
    for slot, theirs, item, top in slots:
        if top and theirs == top[0][0]:
            same.append(f"{slot} {theirs}")
            continue
        used = ", ".join(f"{name} {100 * c // n_top}%" for name, c in top)
        differ.append(f"  {slot}: theirs {theirs or f'none (worn: {item})'}; the top use {used}")
    lines = ["Enchants on the slots most top players enchant (enchant names, not items):"]
    lines += list(dict.fromkeys(differ)) or ["  none missing or different"]  # two rings alike: once
    if same:
        lines.append("  the same as most top players: " + ", ".join(dict.fromkeys(same)))
    return lines


def _secondary(stats: dict) -> dict[str, int]:
    """The secondary stats' ratings in a combatantInfo's stats (their max over the pull: gear, not procs)."""
    out = {}
    for name in SECONDARIES:
        v = (stats.get(name) or {}).get("max")
        if isinstance(v, int | float):
            out[name] = int(v)
    return out


async def _top_stats(rep: Report, gaps: dict) -> str:
    """The top parses' secondary stats, from their own logs, next to the player's: each stat's share of the
    secondary ratings (item levels differ, so shares compare, ratings don't)."""
    top = [r for r in gaps["top"] if readable(r)][:STAT_PARSES]
    try:
        found = await rep.wcl.parse_stats([(r["report"]["code"], r["report"]["fightID"], r["name"]) for r in top])
    except WCLError:
        return "Stats of the top players: Warcraft Logs didn't answer."
    shares = []
    for stats in found:
        sec = _secondary(stats or {})
        if len(sec) == len(SECONDARIES) and (total := sum(sec.values())):
            shares.append({n: 100 * v / total for n, v in sec.items()})
    mine = _secondary((gaps["det"].get("combatantInfo") or {}).get("stats") or {})
    if not shares:
        return "Stats of the top players: not in their logs."
    order = sorted(SECONDARIES, key=lambda n: -statistics.median(s[n] for s in shares))
    text = ", ".join(f"{n} {statistics.median(s[n] for s in shares):.0f}% ({min(s[n] for s in shares):.0f}-"
                     f"{max(s[n] for s in shares):.0f})" for n in order)
    line = (f"Stats of the top {len(shares)} at their pulls, from their logs (each secondary's share of their "
            f"secondary ratings, the median and the range; shares compare across item levels): {text}")
    if len(mine) == len(SECONDARIES) and (total := sum(mine.values())):
        line += "; theirs: " + ", ".join(f"{n} {100 * mine[n] / total:.0f}%" for n in order)
    return line + "."


def gear_stats(det: dict) -> str:
    """A player's stats at the pull from the log's combatant info: their gear's ratings, before buffs (a flask,
    a trinket's proc)."""
    stats = (det.get("combatantInfo") or {}).get("stats") or {}

    def value(name: str) -> int | None:
        v = (stats.get(name) or {}).get("max")
        return int(v) if isinstance(v, int | float) else None
    secondary = sorted(((n, v) for n in ("Crit", "Haste", "Mastery", "Versatility") if (v := value(n)) is not None),
                       key=lambda x: -x[1])
    if not secondary:
        return "Stats: not in the log (needs advanced combat logging)."
    primary = [(n, v) for n in ("Strength", "Agility", "Intellect") if (v := value(n))]
    minor = [(n, v) for n in ("Leech", "Avoidance", "Speed") if (v := value(n))]
    return ("Stats from their gear at the pull (ratings, before buffs such as a flask or a trinket's proc): "
            + " · ".join(f"{n} {v}" for n, v in secondary)
            + "".join(f"; {n} {v}" for n, v in primary)
            + ("; " + ", ".join(f"{n} {v}" for n, v in minor) if minor else "") + ".")


async def raid_prep(rep: Report, pull: int | None = None, boss: str | None = None) -> str:
    """Every player's preparation on a pull: consumables missing at the pull, and builds that stray from the
    top parses of their spec (talents most take, enchants and gems most have)."""
    f = rep.fight(pull, boss)
    players = sorted(rep.pull_players(f).values(), key=lambda p: p.name)
    lines = [f"Preparation on {rep.label(f)} ({rep.url(f)}), {plural(len(players), 'player')}."]
    try:
        found = await consumables(rep, f)
    except KnowledgeError as e:
        found = None
        lines.append(unavailable("Consumables", e))
    if found is None:
        pass
    elif not found:
        lines.append("Consumables: not in the log (needs advanced combat logging).")
    else:
        for kind, label in (("flask", "No flask"), ("food", "No food buff"), ("augment rune", "No augment rune"),
                            ("vantus rune", "No vantus rune"), ("oil", "No weapon oil, stone or class imbue")):
            lacking = [p.name for p in players if p.id in found and not found[p.id][kind]]
            lines.append(f"{label} ({len(lacking)} of {len(found)}): " + ", ".join(lacking) if lacking
                         else f"{label}: everyone had one")
    names, versions = await knowledge.talent_names(), await knowledge.choice_versions()
    sem = asyncio.Semaphore(3)  # a top-parse query per spec, with every player's gear and talents in it

    async def gaps_of(p: Player) -> dict | str:
        async with sem:
            try:
                return await _build_gaps(rep, f, p)
            except FactError as e:
                return str(e)

    builds = []
    for p, gaps in zip(players, await asyncio.gather(*(gaps_of(p) for p in players)), strict=True):
        if isinstance(gaps, str):
            continue
        problems = []
        if gaps["missing"]:
            problems.append("lacks talents most top players take: " + ", ".join(
                names.get(i, f"talent {i}") + (" (takes the other version)" if versions.get(i) in gaps["mine"] else "")
                for i in gaps["missing"][:6]))
        if odd := [i for i in gaps["unusual"] if gaps["taken"][i] == 0]:
            problems.append("takes talents no top player does: "
                            + ", ".join(names.get(i, f"talent {i}") for i in odd[:6]))
        if gaps["no_enchant"]:
            problems.append("no enchant on " + ", ".join(gaps["no_enchant"]))
        if gaps["no_gem"]:
            problems.append("no gem in " + ", ".join(gaps["no_gem"]))
        if problems:
            builds.append(f"  {p.label}: " + "; ".join(problems))
    lines.append(f"Builds against the top {TOP_PARSES} parses of each spec on {f['name']} (popular, not proven "
                 "best; talents can be a choice for the fight): " + ("" if builds else "nothing stands out"))
    return "\n".join(lines + builds)


async def _against_guides(rep: Report, p: Player, det: dict, mine: dict[int, int],
                          named: Callable[[int], str], parts: dict[int, str]) -> list[str]:
    """The player's talents against the raid builds of their spec's guides (Icy Veins, Method), those for their
    hero tree first: what each build takes that they don't, and the other way round."""
    try:
        builds = await knowledge.guide_builds(p.class_name, p.spec)
    except KnowledgeError as e:
        return [unavailable("Guide builds", e)]
    unread = [b for b in builds if len(b["talents"]) > MAX_BUILD_TALENTS]  # a decode gone wrong
    builds = [b for b in builds if b not in unread]
    if not builds:
        return ["Guide builds: none found" + (f" ({len(unread)} guide build(s) couldn't be read)." if unread else ".")]
    hero = await hero_of(rep, p, det)
    builds = [b for b in builds if b["hero"] == hero] or builds
    tree = await knowledge.tree_for(p.class_name, p.spec) or {}
    free = {e["id"] for key in ("classNodes", "specNodes", "heroNodes", "subTreeNodes")
            for node in tree.get(key) or [] if node.get("freeNode") or key == "subTreeNodes"
            for e in node.get("entries") or []}
    lines = [f"Against the guides' raid builds{f' for {hero}' if hero else ''} (talents differ by boss; a guide "
             "build is a starting point):"]
    theirs = {named(i) for i in mine if i not in free}  # by name: a talent can have an entry id per rank
    part = {named(i): parts.get(i, "") for b in builds for i in [*mine, *b["talents"]]}
    for b in builds[:4]:
        guide = {named(i) for i in b["talents"] if i not in free}
        lines.append(f"  {b['source']} \"{b['label']}\" ({b['url']}): they lack "
                     + (by_part([(part[t], t) for t in sorted(guide - theirs)]) or "nothing")
                     + "; they take, the build doesn't: "
                     + (by_part([(part[t], t) for t in sorted(theirs - guide)]) or "nothing"))
    if unread:
        lines.append(f"  {len(unread)} guide build(s) couldn't be read.")
    return lines
