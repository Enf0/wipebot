"""One pull in order: its summary and how it ended (`pull_summary`), and its timeline (`pull_timeline`)."""
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError
from ..pulls import is_reset
from ..text import clock, ids_line, ordinal, parse_clock, plural, short
from ..wcl import WCL, WCLError
from .adds import ADD_GROUP_MS, add_wave, boss_units, interruptible_casts
from .base import (
    PARTNER_DEATH_MS,
    PARTNER_SLACK_MS,
    REPEAT_CAST_MS,
    FactError,
    PullRef,
    Report,
    alive_after,
    half_dead,
    unavailable,
)
from .ending import ending, ending_text, ruled_out

TIMELINE_DEBUFF_GROUPS = 12  # a boss debuff landing in more separate bursts than this is ambient, not an event
TIMELINE_GROUP_MS = 3_000  # debuff applications this close together are one event
SAME_HIT_MS = 1_000  # debuff applications this close together are one hit of the ability; later ones another
STACKS_SHOWN = 3  # a stacking debuff that reaches this many stacks is worth a line
TRACKED_MARK = "◆"  # after a boss cast Lorrgs tracks for the boss: the casts its timeline is built on
TIMELINE_MAX_LINES = 150
DAMAGE_BUCKET_MS = 5_000  # raid damage taken is shown per this much time
BOSS_HEALTH = 1000  # WCL's Resources graph: abilityID 1000 is health, 100 + a power type that power
RAID_WIDE = 3  # a damage dealer's or tank's cooldown is a raid cooldown when it buffs or heals this many others


def boss_left(f: dict) -> str:
    """'the boss at 62.63%' (WCL's own precision), or the health being unknown. A kill is 'the boss dead': WCL often
    leaves one at 0.01% (an Entombed Sentinels kill), which reads as a near miss."""
    if f.get("kill"):
        return "the boss dead"
    pct = f.get("bossPercentage")
    if pct is None:
        return "the boss's health not logged"
    fight = f.get("fightPercentage")  # what the summaries call the wipe: phases weighed in (wipe_pct)
    return f"the boss at {pct}% health" + (f" (the wipe at {fight:.1f}%, phases weighed in)"
                                           if fight is not None and abs(fight - pct) >= 0.05 else "")


async def pull_summary(rep: Report, pull: PullRef = None, boss: str | None = None) -> str:
    f = rep.fight(pull, boss)
    deaths = await rep.deaths(f)
    raid = await rep.raid_size(f)
    buffs = await rep.end_buffs(f)
    decided = await rep.decided_at(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    lines = [await rep.summary(f), f"Warcraft Logs: {rep.url(f)}"]
    enrages = await rep.enrages(f)
    ended = ending(rep, f, deaths, backs, raid, enrages, decided)
    if text := ending_text(ended):
        lines.append(text)
    if not_it := ruled_out(ended, enrages, decided):
        lines.append(not_it)
    if phases := rep.phases(f):
        lines.append(f"Phases: {phases}")
    lines += ["", f"Raid size {raid}. Deaths in order (time from pull start, players alive after it, a battle res "
                  "or self-res counted):"]
    half = decided.at_ms if decided and not decided.enrage else None
    for i, (d, alive) in enumerate(zip(deaths, alive_after(deaths, backs, raid), strict=True)):
        p = rep.players.get(rep.names[d["targetID"]].lower())
        at = d["timestamp"] - f["startTime"]
        notes = [f"{alive} alive"]
        notes += [f"after the boss gained {name}" for name, gained in buffs if at >= gained]
        if half is not None and at > half:
            notes.append("after half the raid was dead")
        who = p.label if p else rep.names[d["targetID"]]
        lines.append(f"{i + 1}. {clock(at)} {who} - {rep.ability_ref(d.get('killingAbilityGameID'))}"
                     f" ({', '.join(notes)})")
    if not deaths:
        lines.append("none")
    boss_deaths = [(e["timestamp"] - f["startTime"], rep.actors.get(e["targetID"], {}).get("name") or "a boss")
                   for e in await rep.events(f["id"], "Deaths", hostilityType="Enemies")
                   if e.get("targetID") in rep.bosses] if buffs else []
    for name, gained in buffs:
        before = sum(1 for d in deaths if d["timestamp"] - f["startTime"] < gained)
        partner = next((who for at, who in boss_deaths if gained - PARTNER_DEATH_MS <= at <= gained + PARTNER_SLACK_MS),
                       None)
        if partner:  # what one boss of a council gains when another dies: the kill sees it too
            lines.append(f"The boss gained {name} at {clock(gained)}, when {partner} died, and kept it to the end; "
                         f"{plural(before, 'player')} had died before that. A buff gained as another boss dies "
                         "comes with killing that boss, not from a timer: look it up before calling it an enrage.")
        else:
            lines.append(f"The boss gained {name} at {clock(gained)} and kept it to the end; "
                         f"{plural(before, 'player')} had died before that. Look the buff up: an enrage makes every "
                         "later death its fallout.")
    if half is not None:
        at = clock(half_dead(deaths, backs, raid) - f["startTime"])
        ran = "" if at == clock(half) else f", in deaths at once that ran to {clock(half)}"
        lines.append(f"Half the raid was dead at {at}{ran}; later deaths are fallout of the wipe.")
    if is_reset(f):
        lines.append("The boss was untouched: this was a reset, not an attempt.")
    ended = "Logged so far" if f.get("inProgress") else "Pull ended"
    lines.append(f"{ended} at {clock(f['endTime'] - f['startTime'])} with {boss_left(f)}.")
    return "\n".join(lines)


@dataclass
class Found:
    """What one source of the timeline found: (report ms, text) events, ability ids for lookups, and lines for after
    the events."""
    events: list[tuple[float, str]] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


async def pull_timeline(rep: Report, pull: PullRef = None, boss: str | None = None, start: str | None = None,
                        end: str | None = None) -> str:
    """One pull in time order: phases, the boss's casts and buffs, its debuffs on players (how many each time),
    deaths with players alive, Bloodlust, battle res and raid cooldowns, and raid damage taken over time."""
    f = rep.fight(pull, boss)
    t0 = f["startTime"]
    lo = t0 + (_at_time(start) if start else 0)
    hi = min(f["endTime"], t0 + _at_time(end)) if end else f["endTime"]
    if hi <= lo:
        raise FactError("the window's end must be after its start")
    # Every source at once: what several read (the enemies' casts, the deaths) is fetched once.
    found, resources, damage = await asyncio.gather(
        asyncio.gather(*(_run(source, what, rep, f) for source, what in TIMELINE_SOURCES)),
        _boss_resources(rep, f, lo, hi), _raid_damage(rep, f, lo, hi))
    by_second: dict[int, list[str]] = defaultdict(list)
    # In time order; at the same moment, in the sources' order
    events = sorted((at, k, n, text) for k, part in enumerate(found) for n, (at, text) in enumerate(part.events))
    for at, _, _, text in events:
        if lo <= at <= hi:
            by_second[int((at - t0) // 1000)].append(text)
    window = f", {clock(lo - t0)}-{clock(hi - t0)}" if start or end else ""
    lines = [f"{rep.label(f)} timeline{window} ({rep.url(f)}). Ended at {clock(f['endTime'] - t0)} with "
             f"{boss_left(f)}." + (" KILL." if f["kill"] else "")]
    rows = [f"{clock(sec * 1000)} " + "; ".join(texts) for sec, texts in sorted(by_second.items())]
    if len(rows) > TIMELINE_MAX_LINES:
        rows = rows[:TIMELINE_MAX_LINES] + [f"... {len(rows) - TIMELINE_MAX_LINES} more lines: ask for a "
                                            "narrower window with start and end"]
    ids: dict[str, int] = {}
    for part in found:
        for name, i in part.ids.items():
            ids.setdefault(name, i)
    return "\n".join([*lines, *rows, *(n for part in found for n in part.notes), *resources, damage, ids_line(ids)])


async def _run(source, what: str, rep: Report, f: dict) -> Found:
    """One source; one whose knowledge source can't answer says so instead."""
    try:
        return await source(rep, f)
    except KnowledgeError as e:
        return Found(notes=[unavailable(what, e)])


async def _phases(rep: Report, f: dict) -> Found:
    return Found([(t["startTime"], f"phase {t['id']}" + (f" ({name})" if (name := rep.phase_names.get(
        (f["encounterID"], t["id"]))) else "")) for t in f.get("phaseTransitions") or []])


async def _add_bursts(rep: Report, f: dict) -> Found:
    """The adds' instant casts (no cast bar), in bursts: 'adds: 4 Broodling casting'."""
    bosses = boss_units(rep, f)
    interruptible = set(await interruptible_casts(rep, f))
    adds: dict[str, list[list[dict]]] = defaultdict(list)  # an add's name -> bursts of their instant casts
    for c in await rep.events(f["id"], "Casts", hostilityType="Enemies"):
        if c["type"] != "cast" or c.get("sourceID") in bosses:
            continue
        if (rep.actors.get(c.get("sourceID", -1)) or {}).get("type") == "NPC" \
                and rep.actor(c["sourceID"]) != "Environment" \
                and (rep.actor(c["sourceID"]), rep.ability(c["abilityGameID"])) not in interruptible:
            runs = adds[rep.actor(c["sourceID"])]
            if runs and c["timestamp"] - runs[-1][-1]["timestamp"] <= ADD_GROUP_MS:
                runs[-1].append(c)
            else:
                runs.append([c])
    return Found([(run[0]["timestamp"], f"adds: {len({(c['sourceID'], c.get('sourceInstance')) for c in run})} "
                                        f"{name} casting") for name, runs in adds.items() for run in runs])


async def _add_waves(rep: Report, f: dict) -> Found:
    """The adds' casts with a cast bar, in waves, and whether each was kicked."""
    out = Found()
    for (name, ability), casts in (await interruptible_casts(rep, f)).items():
        out.ids.setdefault(ability, casts[0][3])
        waves: list[list[tuple]] = []
        for cast in casts:
            if waves and cast[0] - waves[-1][-1][0] <= ADD_GROUP_MS:
                waves[-1].append(cast)
            else:
                waves.append([cast])
        out.events += [(wave[0][0], add_wave(f, name, ability, wave)) for wave in waves]
    return out


async def _adds_died(rep: Report, f: dict) -> Found:
    died: dict[str, list[list[float]]] = defaultdict(list)
    for e in sorted(await rep.events(f["id"], "Deaths", hostilityType="Enemies"), key=lambda e: e["timestamp"]):
        if e.get("targetID") in rep.bosses:
            continue
        runs = died[rep.actor(e.get("targetID", 0))]
        if runs and e["timestamp"] - runs[-1][-1] <= TIMELINE_GROUP_MS:
            runs[-1].append(e["timestamp"])
        else:
            runs.append([e["timestamp"]])
    return Found([(run[0], f"adds: {len(run)} {name} died") for name, runs in died.items() for run in runs])


async def _boss_casts(rep: Report, f: dict) -> Found:
    """The boss's casts, one line per ability per second, ◆ after those Lorrgs tracks for the boss."""
    out = Found()
    bosses = boss_units(rep, f)
    same_second: dict[tuple[int, str], Counter] = defaultdict(Counter)  # (second, name) -> casts per ability id
    for c in await rep.events(f["id"], "Casts", hostilityType="Enemies"):
        if c["type"] == "cast" and c.get("sourceID") in bosses:
            sec = (c["timestamp"] - f["startTime"]) // 1000  # the second of the pull, as clock() shows it
            same_second[(sec, rep.ability(c["abilityGameID"]))][c["abilityGameID"]] += 1
            out.ids.setdefault(rep.ability(c["abilityGameID"]), c["abilityGameID"])
    try:
        tracked = await _tracked(rep, f)
    except KnowledgeError as e:
        tracked = set()
        out.notes.append(unavailable(f"Lorrgs' tracked boss casts ({TRACKED_MARK})", e))
    marked = False
    for (sec, name), per_id in same_second.items():
        text = name
        if name.lower() in tracked or set(per_id) & tracked:
            text += f" {TRACKED_MARK}"
            marked = True
        if len(per_id) > 1:  # several abilities share the name (Mutilate [1277027] and [1277031]): one cast
            text += f" ({len(per_id)} abilities)"
            for k, gid in enumerate(i for i in per_id if i != out.ids.get(name)):
                out.ids.setdefault(f"{name} ({ordinal(k + 2)} ability)", gid)
            n = min(per_id.values())
        else:
            n = next(iter(per_id.values()))
        out.events.append((f["startTime"] + sec * 1000, f"boss casts {text}" + (f" x{n}" if n > 1 else "")))
    if marked:
        out.notes.append(f"{TRACKED_MARK} marks the boss abilities Lorrgs tracks for this boss: the casts its timeline "
                         "is built on, to line pulls up by.")
    return out


async def _boss_buffs(rep: Report, f: dict) -> Found:
    out = Found()
    for e in await rep.boss_buffs(f):
        out.ids.setdefault(rep.ability(e["abilityGameID"]), e["abilityGameID"])
        verb = "gains" if e["type"] == "applybuff" else "loses"
        out.events.append((e["timestamp"], f"boss {verb} {rep.ability(e['abilityGameID'])}"))
    return out


async def _boss_debuffs(rep: Report, f: dict) -> list[dict]:
    """The enemies' debuffs on players in order, Bloodlust's kin left out."""
    try:
        lust = set(await knowledge.lust_debuffs())
    except KnowledgeError:  # Bloodlust says so; a player's Sated has the player as its source anyway
        lust = set()
    return [e for e in sorted(await rep.events(f["id"], "Debuffs", hostilityType="Friendlies"),
                              key=lambda e: e["timestamp"])
            if e.get("targetID") in rep.names and e["abilityGameID"] not in lust and e.get("sourceID") not in rep.names]


async def _debuff_stacks(rep: Report, f: dict) -> Found:
    """A stacking debuff's highest stack in the pull, and the step before it."""
    stacks: dict[str, dict[int, dict]] = defaultdict(dict)  # debuff -> stack count -> the first event reaching it
    for e in await _boss_debuffs(rep, f):
        if e["type"] == "applydebuffstack" and e.get("stack"):
            stacks[rep.ability(e["abilityGameID"])].setdefault(e["stack"], e)
    out = Found()
    for name, reached in stacks.items():
        if (peak := max(reached)) >= STACKS_SHOWN:
            out.events += [(e["timestamp"], f"{name} reaches {n} stacks on {rep.names[e['targetID']]}")
                           for n in (peak - 1, peak) if (e := reached.get(n))]
    return out


async def _debuff_landings(rep: Report, f: dict) -> Found:
    """Each time a debuff landed on players: on whom, or how many (per hit when one cast lands on several groups)."""
    out = Found()
    groups: dict[str, list[list[dict]]] = defaultdict(list)
    for e in await _boss_debuffs(rep, f):
        if e["type"] != "applydebuff":
            continue
        out.ids.setdefault(rep.ability(e["abilityGameID"]), e["abilityGameID"])
        runs = groups[rep.ability(e["abilityGameID"])]
        if runs and e["timestamp"] - runs[-1][-1]["timestamp"] <= TIMELINE_GROUP_MS:
            runs[-1].append(e)
        else:
            runs.append([e])
    left_out = []
    for name, runs in groups.items():
        if len(runs) > TIMELINE_DEBUFF_GROUPS:
            left_out.append(f"{name} ({plural(sum(len(r) for r in runs), 'application')})")
            continue
        for run in runs:
            who = {rep.names[e["targetID"]] for e in run}
            hits: list[list[dict]] = []  # one cast landing on several groups in turn (Ravenous Feast: 6, 6, 7)
            for e in run:
                if hits and e["timestamp"] - hits[-1][-1]["timestamp"] <= SAME_HIT_MS:
                    hits[-1].append(e)
                else:
                    hits.append([e])
            volleys = [len({e["targetID"] for e in hit}) for hit in hits]
            if len(who) <= 3:
                text = ", ".join(sorted(who))
            elif len(volleys) > 1 and sum(n >= 3 for n in volleys) >= 2:  # not a debuff rolling on one by one
                text = ", then ".join(map(str, volleys)) + " players"
            else:
                text = plural(len(who), 'player')
            out.events.append((run[0]["timestamp"], f"{name} on {text}"))
    if left_out:
        out.notes.append("Left out as too frequent to list (debuffs that land all the time): " + ", ".join(left_out))
    return out


async def _lusts(rep: Report, f: dict) -> Found:
    return Found([(at, f"Bloodlust ({debuff} on {plural(players, 'player')})")
                  for at, debuff, players in await rep.lusts(f)])


async def _battle_res(rep: Report, f: dict) -> Found:
    return Found([(e["timestamp"], f"battle res: {rep.ability(e['abilityGameID'])} by "
                                   f"{rep.actor(rep.owner(e.get('sourceID', 0)))} on {rep.actor(e.get('targetID', 0))}")
                  for e in await rep.events(f["id"], "All", filterExpression='type = "resurrect"')]
                 + [(e["timestamp"], f"{rep.names[e['sourceID']]} back to life by themselves (first cast since dying: "
                                     f"{rep.ability(e['abilityGameID'])})") for e in await rep.self_res(f)])


async def _deaths(rep: Report, f: dict) -> Found:
    raid, deaths, backs = await rep.raid_size(f), await rep.deaths(f), (await rep.resurrects([f]))[f["id"]]
    out = Found()
    for d, alive in zip(deaths, alive_after(deaths, backs, raid), strict=True):
        killer = rep.ability(d.get("killingAbilityGameID"))
        out.events.append((d["timestamp"], f"DEATH {rep.names[d['targetID']]} ({killer}), {alive} alive"))
    return out


async def _raid_cooldowns_used(rep: Report, f: dict) -> Found:
    raid_cds = await _raid_cooldowns(rep, f)
    last_cast: dict[tuple[int, str], float] = {}
    out = Found()
    for c in sorted(await rep.events(f["id"], "Casts"), key=lambda c: c["timestamp"]):
        if c["type"] != "cast":
            continue
        seen = (c.get("sourceID", 0), rep.ability(c["abilityGameID"]))
        repeat = c["timestamp"] - last_cast.get(seen, -REPEAT_CAST_MS) < REPEAT_CAST_MS  # a channel's ticks
        last_cast[seen] = c["timestamp"]
        if repeat:
            continue
        name = rep.ability(c["abilityGameID"])
        kind = raid_cds.get((c.get("sourceID"), name)) or raid_cds.get((c.get("sourceID"), c["abilityGameID"]))
        if kind:
            out.events.append((c["timestamp"], f"{kind}: {name} ({rep.actor(rep.owner(c.get('sourceID', 0)))})"))
    return out


# Each source of the timeline's events, and what it's called when its source can't answer. At one moment, events
# are listed in this order.
TIMELINE_SOURCES = [
    (_phases, "Phases"), (_add_bursts, "Adds' casts"), (_add_waves, "Adds' casts with a cast bar"),
    (_adds_died, "Adds' deaths"), (_boss_casts, "Boss casts"), (_boss_buffs, "Boss buffs"),
    (_debuff_stacks, "Debuff stacks"), (_lusts, "Bloodlust"), (_battle_res, "Battle res"),
    (_debuff_landings, "Boss debuffs"), (_deaths, "Deaths"), (_raid_cooldowns_used, "Raid and healing cooldowns"),
]


def _at_time(text: str) -> float:
    if (ms := parse_clock(text)) is None:
        raise FactError(f"not a time: {text!r} (use m:ss)")
    return ms


async def _tracked(rep: Report, f: dict) -> set[int | str]:
    """The ids and lowercase names of the boss abilities Lorrgs tracks for this boss (its timeline's casts), or
    nothing when Lorrgs doesn't know the boss."""
    out: set[int | str] = set()
    for sp in await knowledge.boss_spells(f["name"]):
        if sp.get("show", True) and sp.get("name"):
            out.add(sp["name"].lower())
            if sp.get("spell_id"):
                out.add(int(sp["spell_id"]))
    return out


async def tracks(rep: Report, f: dict) -> dict[str, list[tuple[float, float]]]:
    return await rep.keep(("tracks", f["id"]), [f["id"]], lambda: boss_tracks(rep.wcl, rep.code, f, rep.bosses),
                          FOREVER)


async def _boss_resources(rep: Report, f: dict, lo: float, hi: float) -> list[str]:
    """The boss's health and its power bar (energy, rage, ...) over time: one line each, a point per 5 % or 5
    points of change (resets of a power bar are what many mechanics are about)."""
    out = []
    for key, label in (("health", "Boss health %"), ("power", "Boss power (0-100)")):
        window = (lo - f["startTime"], hi - f["startTime"])
        points = [(t, v) for t, v in (await tracks(rep, f)).get(key) or [] if window[0] <= t <= window[1]]
        shown, last = [], None
        for t, v in points:
            if last is None or abs(v - last) >= 5:
                shown.append(f"{clock(t)} {v:.0f}")
                last = v
        if shown:
            out.append(f"{label} over time: " + " · ".join(shown))
    return out


async def _raid_cooldowns(rep: Report, f: dict) -> dict[tuple[int, int | str], str]:
    """(player id, spell id or name) -> 'healing cooldown' or 'raid cooldown', for the spells Lorrgs tags
    raid_cd of every player in a pull. A healer's are healing cooldowns. Lorrgs also tags some damage cooldowns
    (Convoke the Spirits, Avenging Wrath), so a damage dealer's or tank's counts only when the log shows it
    reaching other players: its buffs or heals on RAID_WIDE of them in this pull."""
    players = list(rep.pull_players(f).values())
    tagged = [(p, sp) for p, sps in zip(players, await asyncio.gather(*(rep.spells(p) for p in players)), strict=True)
              for sp in sps if "raid_cd" in (sp.get("tags") or []) and "move" not in (sp.get("tags") or [])]
    raid_wide = await _reaching_others(rep, f, {sp["name"] for p, sp in tagged if p.role != "healer"})
    out: dict[tuple[int, int | str], str] = {}
    for p, sp in tagged:
        kind = "healing cooldown" if p.role == "healer" else "raid cooldown" if sp["name"] in raid_wide else None
        if kind:
            out[(p.id, sp["spell_id"])] = out[(p.id, sp["name"])] = kind
    return out


async def _reaching_others(rep: Report, f: dict, names: set[str]) -> set[str]:
    """Which of these abilities buffed or healed RAID_WIDE or more players other than their source in a pull.
    By name: a cooldown's buff has its own spell id, and some (Anti-Magic Zone) come with no source."""
    if not names:
        return set()
    expr = "ability.name in (" + ", ".join(json.dumps(n) for n in sorted(names)) + ")"
    reached: dict[str, set[int]] = defaultdict(set)
    for data_type in ("Buffs", "Healing"):
        for e in await rep.events(f["id"], data_type, filterExpression=expr):
            if (target := e.get("targetID")) is not None and target != e.get("sourceID"):
                reached[rep.ability(e["abilityGameID"])].add(target)
    return {name for name, targets in reached.items() if len(targets) >= RAID_WIDE}


async def _raid_damage(rep: Report, f: dict, lo: float, hi: float) -> str:
    """Raid damage taken per second, averaged per DAMAGE_BUCKET_MS, from WCL's graph."""
    try:
        graph = await rep.keep(("graph", f["id"], "DamageTaken"), [f["id"]],
                               lambda: rep.wcl.graph(rep.code, f, "DamageTaken", hostilityType="Friendlies"), FOREVER)
    except WCLError:
        return "Raid damage taken over time: unavailable."
    total = next((s for s in graph.get("series") or [] if s.get("name") == "Total"), None)
    if not total or not total.get("pointInterval"):
        return "Raid damage taken over time: unavailable."
    buckets: dict[int, list[float]] = defaultdict(list)
    for i, v in enumerate(total.get("data") or []):
        at = total.get("pointStart", f["startTime"]) + i * total["pointInterval"]
        if lo <= at <= hi:
            buckets[int((at - f["startTime"]) // DAMAGE_BUCKET_MS)].append(v)
    values = {b: sum(v) / len(v) for b, v in sorted(buckets.items())}
    if not values:
        return "Raid damage taken over time: none in this window."
    typical = sorted(values.values())[len(values) // 2]
    return (f"Raid damage taken per second, {DAMAGE_BUCKET_MS // 1000} s averages (median {short(typical)}/s; "
            "* marks twice the median or more): "
            + " · ".join(f"{clock(b * DAMAGE_BUCKET_MS)} {short(v)}{'*' if v >= 2 * typical else ''}"
                         for b, v in values.items()))


async def boss_tracks(wcl: WCL, code: str, fight: dict, bosses: set[int]) -> dict[str, list[tuple[float, float]]]:
    """The boss's health % and power bar (0-100) over a fight, as (ms from the pull, value) from WCL's Resources
    graph. The power type comes from the boss's own events; a boss without a power bar has no "power"."""
    casts = [c for c in await wcl.events(code, fight["id"], "Casts", hostilityType="Enemies", includeResources=True)
             if c.get("sourceID") in bosses]
    if not casts:
        return {}
    boss = Counter(c["sourceID"] for c in casts).most_common(1)[0][0]
    power = next((c["classResources"][0].get("type") for c in casts
                  if c.get("sourceID") == boss and c.get("classResources")), None)
    out: dict[str, list[tuple[float, float]]] = {}
    for key, ability in (("health", BOSS_HEALTH), ("power", 100 + power if power is not None else None)):
        if ability is None:
            continue
        try:
            graph = await wcl.graph(code, fight, "Resources", hostilityType="Enemies", abilityID=ability,
                                    sourceID=boss)
        except WCLError:
            continue
        series = next((x for x in graph.get("series") or [] if x.get("id") == boss), None)
        out[key] = [(t - fight["startTime"], v) for t, v in (series or {}).get("data") or []]
    return out
