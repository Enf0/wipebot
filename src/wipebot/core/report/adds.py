"""The adds of a pull (`enemies`): how many came and died, who hit them, their casts and the kicks."""
import asyncio
import math
from collections import Counter, defaultdict
from collections.abc import Callable

from .. import knowledge
from ..knowledge import KnowledgeError
from ..text import clock, few, plural, short
from .base import SAME_DEATH_MS, Report, dead_since, unavailable

ADD_GROUP_MS = 15_000  # an add's casts this close together are one wave of them
ADD_GONE_MS = 10_000  # an add nobody hit for this long before a wipe ended was gone, or left alone


def boss_units(rep: Report, f: dict) -> set[int]:
    """The boss (or bosses) of a pull: a boss-type unit that comes once. One that comes several times (Nek'zali's
    Drowned Echoes, Ula'tek's Gore Rattles) is an add."""
    return {n["id"] for n in f.get("enemyNPCs") or [] if n["id"] in rep.bosses and (n.get("instanceCount") or 1) == 1}


async def interruptible_casts(rep: Report, f: dict) -> dict[tuple[str, str], list[tuple[float, str, str | None, int]]]:
    """Per (add, ability), the interruptible casts (those with a cast bar: a begincast) the enemies other than
    the bosses began in a pull, in order: (when, how it ended, who kicked it, ability id). An interrupt event on
    the add ends it kicked, the cast event ends it gone through, and neither (the add died or was moved) ends it
    otherwise. A cast whose begin isn't logged on its own instance finishes another (`_finish_orphans`)."""
    by_unit: dict[tuple, list[dict]] = defaultdict(list)  # (actor, instance) -> its casts and the kicks on it
    bosses = boss_units(rep, f)
    for c in await rep.events(f["id"], "Casts", hostilityType="Enemies"):
        source = c.get("sourceID", -1)
        if c["type"] in ("begincast", "cast") and source not in bosses \
                and (rep.actors.get(source) or {}).get("type") == "NPC" and rep.actor(source) != "Environment":
            by_unit[(source, c.get("sourceInstance"))].append(c)
    for k in await rep.events(f["id"], "Interrupts"):
        if k.get("type") == "interrupt":
            by_unit[(k.get("targetID"), k.get("targetInstance"))].append(k)
    out: dict[tuple[str, str], list[tuple[float, str, str | None, int]]] = defaultdict(list)

    def ended(begun: dict, how: str, by: str | None = None) -> None:
        out[(rep.actor(begun["sourceID"]), rep.ability(begun["abilityGameID"]))].append(
            (begun["timestamp"], how, by, begun["abilityGameID"]))
    with_bar = {rep.ability(e["abilityGameID"]) for evs in by_unit.values() for e in evs if e["type"] == "begincast"}
    orphans: list[dict] = []
    for evs in by_unit.values():
        begun = None
        for e in sorted(evs, key=lambda e: e["timestamp"]):
            if e["type"] == "begincast":
                if begun:
                    ended(begun, "ended otherwise")
                begun = e
            elif begun and e["type"] == "cast" \
                    and rep.ability(e["abilityGameID"]) == rep.ability(begun["abilityGameID"]):
                ended(begun, "went through")
                begun = None
            elif not begun and e["type"] == "cast" and rep.ability(e["abilityGameID"]) in with_bar:
                orphans.append(e)
            elif begun and e["type"] == "interrupt":
                ended(begun, "kicked", rep.actor(rep.owner(e.get("sourceID", -1))))
                begun = None
        if begun:
            ended(begun, "ended otherwise")
    _finish_orphans(out, orphans, lambda e: (rep.actor(e["sourceID"]), rep.ability(e["abilityGameID"])))
    return {key: sorted(casts) for key, casts in out.items()}


def _finish_orphans(out: dict[tuple[str, str], list], orphans: list[dict],
                    key: Callable[[dict], tuple[str, str]]) -> None:
    """A cast with no begin logged on its own instance finishes the last unfinished begin of that add and ability
    before it (the log can name another instance for it: a Drowned Echo's Soulcoiler's Curse on Nek'zali), as
    Warcraft Logs' own tables count it. With none open it's no cast of its own: a later stage of one already done
    (Hungering Pyre logs a second cast about 8 s after the first)."""
    for e in orphans:
        casts = out.get(key(e), [])
        open_ = [i for i, c in enumerate(casts) if c[1] == "ended otherwise" and c[0] < e["timestamp"]]
        if open_:
            i = max(open_, key=lambda i: casts[i][0])
            casts[i] = (casts[i][0], "went through", None, casts[i][3])


def add_wave(f: dict, name: str, ability: str, wave: list[tuple[float, str, str | None, int]]) -> str:
    """'Broodling of Ithraz casts Visceral Burst x10: 9 kicked (Balomino, Amascut, +7), 1 went through (0:45)'."""
    t0 = f["startTime"]
    if len(wave) == 1:
        at, how, by, _ = wave[0]
        return f"{name} casts {ability}: " + (f"kicked by {by}" if how == "kicked" else how
                                              + (" (the add died or was moved)" if how == "ended otherwise" else ""))
    parts = []
    for how in ("kicked", "went through", "ended otherwise"):
        these = [c for c in wave if c[1] == how]
        if not these:
            continue
        text = f"{len(these)} {how}"
        if how == "kicked":
            kickers = Counter(c[2] for c in these)
            named = [k for k, _ in kickers.most_common(3)]
            more = f", +{len(kickers) - 3}" if len(kickers) > 3 else ""
            text += " (" + ", ".join(str(k) for k in named) + more + ")"
        elif len(these) <= 3:
            text += " (" + ", ".join(clock(c[0] - t0) for c in these) + ")"
        parts.append(text)
    return f"{name} casts {ability} x{len(wave)}: " + ", ".join(parts)


async def enemies(rep: Report, pull: int | None = None, boss: str | None = None) -> str:
    """The enemies other than the boss in a pull (adds): how many came, when they died, what became of the rest
    (alive at the end, or gone without a death: despawned, transformed or removed), the damage they took and who
    did it, and their interruptible casts (begun, kicked, gone through). A boss-type unit that comes several
    times (Nek'zali's Drowned Echoes) is an add; one that comes once is a boss and left out."""
    f = rep.fight(pull, boss)
    t0 = f["startTime"]
    end = f["endTime"] - t0
    bosses = boss_units(rep, f)
    npcs = [n for n in f.get("enemyNPCs") or [] if n["id"] not in bosses]
    if not npcs:
        return f"No enemies other than the boss on {rep.label(f)}."
    died_events, done, casts = await asyncio.gather(rep.events(f["id"], "Deaths", hostilityType="Enemies"),
                                                    rep.table(f["id"], "DamageDone", viewBy="Target"),
                                                    interruptible_casts(rep, f))
    kind = same_kind(rep, npcs, bosses)
    deaths = _deaths(died_events, kind, t0)
    by_actor = {e.get("id"): e.get("total", 0) for e in done.get("entries") or []}
    taken = _taken(by_actor, kind)
    lines = [f"Enemies other than the boss on {rep.label(f)} ({clock(end)}{', KILL' if f['kill'] else ''}, "
             f"{rep.url(f)}), most damaged first (instances, deaths and when, what became of the rest, damage "
             "taken, who did most; then their casts with a cast bar: begun, kicked and by whom, gone through):"]
    shown = sorted(npcs, key=lambda n: -taken.get(n["id"], 0))[:10]
    survivors = [n for n in shown if came_of(n, deaths) > len(deaths[n["id"]])]
    # The actors asked about for each kind: the one the pull lists, and another of its kind only when it took damage
    # (else nobody hit it, and there's nothing to ask).
    pairs = [(n["id"], a) for n in shown
             for a in [n["id"], *(a for a, k in kind.items() if k == n["id"] and a != n["id"] and by_actor.get(a))]]
    alive_kinds = {n["id"] for n in survivors}
    # On a wipe, when each kind still there was last hit (gone before the end, or still there); who hit each kind;
    # who had a kick ready for each cast that went through: each pull's in one query.
    last_hit, by_pair, (kickable, ready, unknown, no_kicks) = await asyncio.gather(
        _last_hits(rep, f, [] if f["kill"] else [x for x in pairs if x[0] in alive_kinds]),
        rep.tables([(f["id"], {"targetID": a}) for _, a in pairs], "DamageDone"), _kicks(rep, f, casts))
    dealt: dict[int, Counter] = defaultdict(Counter)
    for (n_id, _), table in zip(pairs, by_pair, strict=True):
        for e in table.get("entries") or []:
            dealt[n_id][e["name"]] += e.get("total", 0)
    for n in shown:
        name = rep.actor(n["id"])
        died = sorted(deaths[n["id"]])
        dealers = dealt[n["id"]].most_common(4)
        came = came_of(n, deaths)
        alive = came - len(died)
        when = ", ".join(clock(t) for t in died[:8]) + (" ..." if len(died) > 8 else "")
        rest = ""
        if alive and f["kill"]:
            rest = f", {alive} with no death recorded: gone with the kill (despawned, transformed or removed)"
        elif alive and n["id"] in last_hit and end - last_hit[n["id"]] > ADD_GONE_MS:
            rest = (f", {alive} with no death recorded, last hit at {clock(last_hit[n['id']])} (despawned, "
                    "transformed or removed, or left alone after that)")
        elif alive:
            rest = f", {alive} alive at the end"
        lines.append(f"  {name}{' (a boss-type unit)' if n['id'] in rep.bosses else ''}: {came} came, "
                     f"{len(died)} died" + (f" (at {when})" if died else "") + rest
                     + f", took {short(taken.get(n['id'], 0))}; most by "
                     + (", ".join(f"{who} {short(total)}" for who, total in dealers) or "nobody"))
        lines += [_cast_line(ability, these, kickable, ready, t0) for (add, ability), these in casts.items()
                  if add == name]
    if ready:
        lines.append("Kick ready: alive with their interrupt (the one they kicked with on this boss that night) off "
                     "its base cooldown when the cast began; range and stuns not checked"
                     + (f"; never kicked on this boss that night, so not counted: {', '.join(unknown)}." if unknown
                        else "."))
    if no_kicks:
        lines.append(no_kicks)
    if unrecorded := [rep.actor(n["id"]) for n in shown if came_of(n, deaths) > 1 and not deaths[n["id"]]
                      and taken.get(n["id"])]:
        lines.append(_no_deaths(unrecorded))
    if any(n["id"] in rep.bosses for n in shown):
        lines.append("A boss-type unit that comes once is the boss, left out: "
                     + ", ".join(dict.fromkeys(rep.actor(b) for b in bosses)) + ".")
    return "\n".join(lines)


def _deaths(died_events: list[dict], kind: dict[int, int], t0: float) -> dict[int, list[float]]:
    """Each kind of add's deaths, from the pull's start (Deaths events of enemies; WCL lists some twice)."""
    deaths: dict[int, list[float]] = defaultdict(list)
    last_death: dict[tuple, float] = {}
    for e in sorted(died_events, key=lambda e: e["timestamp"]):
        unit = (e.get("targetID", 0), e.get("targetInstance"))
        if e["timestamp"] - last_death.get(unit, -math.inf) > SAME_DEATH_MS:
            deaths[kind.get(unit[0], unit[0])].append(e["timestamp"] - t0)
        last_death[unit] = e["timestamp"]
    return deaths


def _taken(by_actor: dict[int, float], kind: dict[int, int]) -> dict[int, float]:
    """Each kind of add's damage taken, from the damage per enemy actor."""
    taken: dict[int, float] = defaultdict(float)
    for actor, total in by_actor.items():
        taken[kind.get(actor, actor)] += total
    return taken


def _no_deaths(names: list[str]) -> str:
    return (f"No death recorded for any {', '.join(names)}: the log can't show when they were killed or in what "
            "order, only how many came, the damage they took and their casts.")


NIGHT_KINDS = 4  # with all_pulls, the most damaged kinds of add named on each pull's line


async def enemies_night(rep: Report, boss: str | None = None) -> str:
    """The adds over every pull on a boss: per kind how many came and died over the pulls and the damage they
    took, their casts with a cast bar (begun, kicked and by whom, gone through), then each pull in a line. Each
    pull's deaths, damage by enemy and casts are fetched in one query each for all the pulls."""
    fights = rep.by_boss[rep.boss(boss)]
    items = [(f["id"], {}) for f in fights]
    died_all, done_all, _, _ = await asyncio.gather(
        rep.events_for(items, "Deaths", hostilityType="Enemies"), rep.tables(items, "DamageDone", viewBy="Target"),
        rep.events_for(items, "Casts", hostilityType="Enemies"), rep.events_for(items, "Interrupts"))
    came: Counter = Counter()
    died: Counter = Counter()
    taken: dict[str, float] = defaultdict(float)
    pulls_with: Counter = Counter()
    casts: dict[tuple[str, str], list[tuple[float, str, str | None, int]]] = defaultdict(list)
    per_pull = []
    for f, died_events, done in zip(fights, died_all, done_all, strict=True):
        bosses = boss_units(rep, f)
        npcs = [n for n in f.get("enemyNPCs") or [] if n["id"] not in bosses]
        kind = same_kind(rep, npcs, bosses)
        deaths = _deaths(died_events, kind, f["startTime"])
        hurt = _taken({e.get("id"): e.get("total", 0) for e in done.get("entries") or []}, kind)
        here = []
        for n in sorted(npcs, key=lambda n: -hurt.get(n["id"], 0)):
            name, k = rep.actor(n["id"]), came_of(n, deaths)
            came[name] += k
            died[name] += len(deaths[n["id"]])
            taken[name] += hurt.get(n["id"], 0)
            pulls_with[name] += 1
            here.append(f"{name} {k} came, {len(deaths[n['id']])} died")
        pull_casts = await interruptible_casts(rep, f)
        for key, these in pull_casts.items():
            casts[key] += these
        how = Counter(c[1] for these in pull_casts.values() for c in these)
        cast_text = (f"; casts with a cast bar: {sum(how.values())} begun, {how['kicked']} kicked, "
                     f"{how['went through']} went through" if how else "")
        per_pull.append(f"  pull {rep.pull_no(f)} ({clock(f['endTime'] - f['startTime'])}, "
                        f"{'KILL' if f['kill'] else 'wipe'}): " + ("; ".join(here[:NIGHT_KINDS]) or "no adds")
                        + (f"; {plural(len(here) - NIGHT_KINDS, 'more kind')}" if len(here) > NIGHT_KINDS else "")
                        + cast_text)
    if not came:
        return f"No enemies other than the boss on any {fights[0]['name']} pull."
    kickable = await kicked_tonight(rep, fights[0]) if casts else set()
    lines = [f"Enemies other than the boss on all {plural(len(fights), rep.boss_key(fights[0]) + ' pull')}, each "
             "whole pull, most damaged first (how many came and died over the pulls, in how many pulls, damage "
             "taken; then their casts with a cast bar: begun, kicked and by whom, gone through):"]
    for name in sorted(came, key=lambda n: -taken[n])[:10]:
        lines.append(f"  {name}: {came[name]} came over {plural(pulls_with[name], 'pull')}, {died[name]} died"
                     + (f", {came[name] - died[name]} with no death recorded" if came[name] > died[name] else "")
                     + f", took {short(taken[name])}")
        for (add, ability), these in sorted(casts.items()):
            if add == name:
                how = Counter(c[1] for c in these)
                kickers = Counter(c[2] for c in these if c[1] == "kicked")
                text = (f"    casts {ability}: {len(these)} begun, {how['kicked']} kicked, "
                        f"{how['went through']} went through")
                if how["ended otherwise"]:
                    text += f", {how['ended otherwise']} ended otherwise (the add died or was moved)"
                if kickers:
                    text += "; kicked by " + ", ".join(f"{k} {c}" for k, c in kickers.most_common(8))
                if how["went through"] and not any(c[3] in kickable for c in these):
                    text += " (nobody kicked it on this boss that night: it may not be kickable)"
                lines.append(text)
    shown = sorted(came, key=lambda n: -taken[n])[:10]
    if unrecorded := [n for n in shown if came[n] > 1 and not died[n] and taken[n]]:
        lines.append(_no_deaths(unrecorded))
    return "\n".join([*lines, "By pull (the most damaged kinds):", *per_pull])


def _cast_line(ability: str, these: list[tuple[float, str, str | None, int]], kickable: set[int],
               ready: dict[float, list[str]], t0: float) -> str:
    """An add's casts of one ability with a cast bar: begun, kicked and by whom, gone through and when, and who had
    a kick ready then."""
    how = Counter(c[1] for c in these)
    kickers = Counter(c[2] for c in these if c[1] == "kicked")
    text = f"    casts {ability}: {len(these)} begun, {how['kicked']} kicked, {how['went through']} went through"
    if how["ended otherwise"]:
        text += f", {how['ended otherwise']} ended otherwise (the add died or was moved)"
    if kickers:
        text += "; kicked by " + ", ".join(f"{k} {c}" for k, c in kickers.most_common(8))
    through = [c[0] for c in these if c[1] == "went through"]
    if through and not any(c[3] in kickable for c in these):
        text += " (nobody kicked it on this boss that night: it may not be kickable)"
    if through:
        text += "; went through at " + ", ".join(
            clock(t - t0) + (f" (kick ready: {few(ready[t])})" if t in ready else "") for t in through[:8]
        ) + (" ..." if len(through) > 8 else "")
    return text


async def _last_hits(rep: Report, f: dict, units: list[tuple[int, int]]) -> dict[int, float]:
    """When each of these kinds of add was last hit, from the pull's start: (the kind, one of its actors) pairs."""
    hits = await rep.events_for([(f["id"], {"targetID": a}) for _, a in units], "DamageDone",
                                hostilityType="Friendlies") if units else []
    out: dict[int, float] = {}
    for (n_id, _), evs in zip(units, hits, strict=True):
        if evs:
            out[n_id] = max(out.get(n_id, -math.inf), max(e["timestamp"] for e in evs) - f["startTime"])
    return out


def came_of(n: dict, deaths: dict[int, list[float]]) -> int:
    """How many of an add came: the pull's count for the actor it lists, or more when more died under all the actors
    of its kind (Vashnik's Malignant Totems: 46 dead under two actors where the one listed counts 36)."""
    return max(n.get("instanceCount") or 1, len(deaths[n["id"]]))


def same_kind(rep: Report, npcs: list[dict], bosses: set[int]) -> dict[int, int]:
    """Each enemy actor that is one of these adds, to the add the pull lists it as: a pull lists one actor per kind
    of enemy, but its events can name another actor of the same game id (Vashnik's Malignant Totems die under two,
    about half under each)."""
    listed = {n["gameID"]: n["id"] for n in npcs if n.get("gameID")}
    out = {n["id"]: n["id"] for n in npcs}
    for a in rep.actors.values():
        if a.get("type") == "NPC" and a["id"] not in out and a["id"] not in bosses and a.get("gameID") in listed:
            out[a["id"]] = listed[a["gameID"]]
    return out


async def _kicks(rep: Report, f: dict, casts: dict) -> tuple[set[int], dict[float, list[str]], list[str], str]:
    """(the spells kicked on this boss tonight, who had a kick ready at each cast of one that went through, the
    players whose kick isn't known, the line saying it's unavailable)."""
    kickable = await kicked_tonight(rep, f) if any(c[1] == "went through" for v in casts.values() for c in v) else set()
    through_at = [c[0] for these in casts.values() for c in these if c[1] == "went through" and c[3] in kickable]
    try:
        return kickable, *await kicks_ready(rep, f, through_at), ""
    except KnowledgeError as e:
        return kickable, {}, [], unavailable("Kick ready (interrupt cooldowns from Wowhead)", e)


async def kicked_tonight(rep: Report, f: dict) -> set[int]:
    """The spells someone interrupted on this boss tonight: a cast nobody ever kicked may not be kickable at all
    (a cast bar isn't proof), so who had a kick ready says nothing about it."""
    pulls = rep.by_boss[rep.boss_key(f)]
    return {e["extraAbilityGameID"] for e in await rep.events([x["id"] for x in pulls], "Interrupts")
            if e.get("type") == "interrupt" and e.get("extraAbilityGameID")}


async def kicks_ready(rep: Report, f: dict, times: list[float]) -> tuple[dict[float, list[str]], list[str]]:
    """Who could have kicked at each of these moments (casts that went through): alive (a battle res brings one
    back), with their interrupt off its base cooldown. A player's interrupt is the spell they kicked with on this
    boss tonight (a pet's counts for its owner), its cooldown from its tooltip; range, stuns and talents that shorten
    it aren't checked. Also the players of the pull who never kicked on the boss tonight, so their interrupt isn't
    known."""
    if not times:
        return {}, []
    pulls = rep.by_boss[rep.boss_key(f)]
    used: dict[int, Counter] = defaultdict(Counter)
    kicked, backs = await asyncio.gather(rep.events([x["id"] for x in pulls], "Interrupts"), rep.resurrects(pulls))
    for e in kicked:
        if e.get("type") == "interrupt" and e.get("abilityGameID"):
            used[rep.owner(e.get("sourceID", -1))][e["abilityGameID"]] += 1
    here = rep.pull_players(f)
    kick = {pid: c.most_common(1)[0][0] for pid, c in used.items() if pid in here}
    unknown = sorted(p.name for pid, p in here.items() if pid not in kick)
    if not kick:
        return {}, unknown
    spells = sorted(set(kick.values()))
    cooldown = dict(zip(spells, await asyncio.gather(*(knowledge.spell_cooldown(s) for s in spells)), strict=True))
    pressed: dict[int, list[float]] = defaultdict(list)
    expr = "ability.id in (" + ", ".join(str(s) for s in spells) + ")"
    for e in await rep.events(f["id"], "Casts", filterExpression=expr):
        if e.get("type") == "cast":
            pressed[rep.owner(e.get("sourceID", -1))].append(e["timestamp"])
    deaths = await rep.deaths(f)
    ready: dict[float, list[str]] = {}
    for t in times:
        names = []
        for pid, sid in kick.items():
            cd = cooldown.get(sid)
            if cd is None or dead_since(deaths, backs[f["id"]].get(pid, []), pid, t) is not None:  # a battle res: alive
                continue
            last = max((x for x in pressed[pid] if x <= t), default=None)
            if last is None or t - last >= cd * 1000:
                names.append(here[pid].name)
        ready[t] = sorted(names)
    return ready, unknown
