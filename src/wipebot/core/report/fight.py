"""One pull in order: its summary and how it ended (`pull_summary`), and its timeline (`pull_timeline`)."""
import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError
from ..pulls import (
    DIFFICULTIES,
    END_BUFF_WINDOW_MS,
    killed_by,
    pct_text,
    phase_health,
    phase_label,
    subject,
    trend_line,
)
from ..text import clock, exact, ids_line, ordinal, parse_clock, plural, ranges, short
from ..wcl import WCL, WCLError
from . import rows
from .adds import ADD_GROUP_MS, add_wave, boss_units, interruptible_casts
from .base import (
    PARTNER_DEATH_MS,
    PARTNER_SLACK_MS,
    REPEAT_CAST_MS,
    SAME_DEATH_MS,
    FactError,
    PullRef,
    Report,
    alive_after,
    half_dead,
    unavailable,
)
from .ending import (
    ALIVE,
    Ending,
    KeptBuff,
    ending_text,
    kind_of_end,
    moment_kind,
    pull_ending,
    pull_endings,
    ruled_out,
)
from .taken import raid_taken

TIMELINE_DEBUFF_GROUPS = 12  # a boss debuff landing in more separate bursts than this is ambient, not an event
TIMELINE_GROUP_MS = 3_000  # debuff applications this close together are one event
SAME_HIT_MS = 1_000  # debuff applications this close together are one hit of the ability; later ones another
STACKS_SHOWN = 3  # a stacking debuff that reaches this many stacks is worth a line
TRACKED_MARK = "◆"  # after a boss cast Lorrgs tracks for the boss: the casts its timeline is built on
TIMELINE_MAX_LINES = 150
NTH_ABILITY = re.compile(r" \(\d+\w\w ability\)$")  # "Ravage (2nd ability)": an ability's further ids, by its name
DAMAGE_BUCKET_MS = 5_000  # raid damage taken is shown per this much time
BOSS_HEALTH = 1000  # WCL's Resources graph: abilityID 1000 is health, 100 + a power type that power
RAID_WIDE = 3  # a damage dealer's or tank's cooldown is a raid cooldown when it buffs or heals this many others


def boss_left(f: dict) -> str:
    """'the boss at 62.6% health' (to one decimal, as a wipe's % is given everywhere: pct_text), or the health being
    unknown; on a boss counted by phase, the phase too ('the boss at 80.8% health in P2'). A kill is 'the boss
    dead': WCL often leaves one at 0.01% (an Entombed Sentinels kill), which reads as a near miss. The wipe's % is
    added wherever it prints as another number, however close the two are: the summary's first line gives that one."""
    if f.get("kill"):
        return "the boss dead"
    pct = f.get("bossPercentage")
    if pct is None:
        return "the boss's health not logged"
    phase = f" in {phase_label(f)}" if phase_health(f) is not None else ""
    fight = f.get("fightPercentage")  # what the summaries call the wipe: phases weighed in (wipe_pct)
    apart = fight is not None and pct_text(fight) != pct_text(pct)
    return f"the boss at {pct_text(pct)} health{phase}" + (f" (the wipe at {pct_text(fight)}, phases weighed in)"
                                                           if apart else "")


NOT_A_RAID = ("Not a raid pull: Warcraft Logs gives this fight no raid difficulty, as it does a dungeon run. It is "
              "read as one boss pull all the same: \"the boss\", \"raid size\" and the kill or wipe are the whole "
              "fight's, and nothing here knows of a key's level, its timer or its trash.")


async def pull_summary(rep: Report, pull: PullRef = None, boss: str | None = None) -> str:
    """One pull, the answer first: what it was, how it ended, how that compares with earlier pulls; then the deaths
    until the wipe was decided, a row each, and the fallout after it in one line."""
    f = rep.fight(pull, boss)
    deaths = await rep.deaths(f)
    raid = await rep.raid_size(f)
    buffs = await rep.end_buffs(f)
    decided = await rep.decided_at(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    s = await rep.summarized(f)
    lines = [subject(s, rep.url(f))]
    if f.get("difficulty") not in DIFFICULTIES:
        lines.append(NOT_A_RAID)
    ended = await pull_ending(rep, f)
    if text := ending_text(ended):
        lines.append(text)
    if not_it := ruled_out(ended, buffs, decided):
        lines.append(not_it)
    if again := await _same_ending(rep, f, ended):
        lines.append(again)
    if trend := trend_line(s):
        lines.append(trend)
    if phases := rep.phases(f):
        lines.append(f"Phases: {phases}")
    alive = alive_after(deaths, backs, raid)
    later = [d for d in deaths if decided and d["timestamp"] - f["startTime"] > decided.at_ms]  # fallout
    lines += ["", *([killed] if (killed := killed_by(s)) else []),
              f"Raid size {raid}. Deaths {'until then, ' if later else ''}in order (time from pull start, players "
              "alive after it, a battle res or self-res counted):"]
    for i, (d, left) in enumerate(zip(deaths[:len(deaths) - len(later)], alive, strict=False)):
        p = rep.players.get(rep.names[d["targetID"]].lower())
        who = p.label if p else rep.names[d["targetID"]]
        lines.append(f"{i + 1}. {clock(d['timestamp'] - f['startTime'])} {who} - "
                     f"{rep.ability_ref(d.get('killingAbilityGameID'))} ({left} alive)")
    if len(later) == len(deaths):
        lines.append("none")
    half = decided.at_ms if decided and not decided.enrage else None
    ran = ""
    if half is not None:  # the moment half the raid was dead, when its deaths ran on past it
        at = clock(half_dead(deaths, backs, raid) - f["startTime"])
        ran = "" if at == clock(half) else f" at {at}, in one moment's deaths that ran to {clock(half)}"
    if later and decided:
        lines.append(_fallout(rep, f, later, f"{clock(decided.at_ms)} ({decided.why}{ran})", alive[-1], raid))
    elif ran:
        lines.append(f"Half the raid was dead{ran}.")
    told = {m.name for m in ended.moments if isinstance(m, KeptBuff)} if ended else set()  # "Ended by" has these
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
        elif name not in told and not (not_it and decided and gained > decided.at_ms):  # nor in what it wasn't
            lines.append(f"The boss gained {name} at {clock(gained)} and kept it to the end; "
                         f"{plural(before, 'player')} had died before that. Look the buff up: an enrage makes every "
                         "later death its fallout.")
    ended_at = "Logged so far" if f.get("inProgress") else "Pull ended"
    lines.append(f"{ended_at} at {clock(f['endTime'] - f['startTime'])} with {boss_left(f)}.")
    return "\n".join(lines)


def _fallout(rep: Report, f: dict, deaths: list[dict], decided: str, alive: int, raid: int) -> str:
    """The deaths after a wipe was decided, in one line: what decided it said once, then each killing blow with when
    it killed whom, in the order the first of each came."""
    by_blow: dict[int | None, dict[str, list[str]]] = {}
    for d in deaths:
        at = clock(d["timestamp"] - f["startTime"])
        by_blow.setdefault(d.get("killingAbilityGameID"), {}).setdefault(at, []).append(rep.names[d["targetID"]])
    blows = [f"{rep.ability_ref(blow)} ×{sum(map(len, at.values()))} ("
             + "; ".join(f"{when} {', '.join(who)}" for when, who in at.items()) + ")" for blow, at in by_blow.items()]
    return (f"Fallout, {plural(len(deaths), 'death')} after the wipe was decided at {decided}, by killing blow (when, "
            f"who): {' · '.join(blows)}; {alive} of {raid} alive after the last.")


REPEAT_ENDINGS = 3  # an ending that ended this many earlier pulls is the one to say this pull got past


async def _same_ending(rep: Report, f: dict, ended: Ending | None) -> str | None:
    """How this wipe's ending compares with the earlier pulls on the boss in this log: the same as some of them (by
    the first thing that ended each), or past the one that ended most of them. None for a first pull, a reset, a kill
    or a pull still going."""
    if ended is None or ended.kind in ("live", "reset"):
        return None
    earlier = [x for x in rep.by_boss[rep.boss_key(f)] if x["startTime"] < f["startTime"] and not x.get("inProgress")]
    if not earlier:
        return None
    await rep.decided_cuts(earlier)  # every earlier pull's deaths, buffs and battle res at once
    ends = await pull_endings(rep, earlier)
    kinds = {rep.pull_no(x): kind_of_end(e) for x, e in zip(earlier, ends, strict=True)}
    this = kind_of_end(ended)
    same = [n for n, k in kinds.items() if k == this]
    nothing = ("a reset", "a kill", ALIVE)  # no pull gets past these: nothing in the log ended them
    common, most = Counter(k for k in kinds.values() if k not in nothing).most_common(1)[0] \
        if any(k not in nothing for k in kinds.values()) else ("", 0)
    parts = []
    if same:
        parts.append(f"ended the way {plural(len(same), 'earlier pull')} on {f['name']} in this log did (pull"
                     f"{'s' if len(same) > 1 else ''} {ranges(same)}): {this}")
    here = None
    if common and common != this and most >= REPEAT_ENDINGS:
        of = f"{most} of {len(earlier)}"
        came = await _came_here(rep, f, ended, next(e for e in ends if kind_of_end(e) == common))
        if came is None:
            parts.append(f"got past what ended the most earlier pulls: {common} ({of})")
        else:
            here = f"What ended the most earlier pulls ({of}), {came}."
    return " ".join(([f"This pull {'; it '.join(parts)}."] if parts else []) + ([here] if here else [])) or None


async def _came_here(rep: Report, f: dict, ended: Ending, earlier: Ending | None) -> str | None:
    """Where this pull had what ended most earlier pulls (the first moment of `earlier`, one of them) and so didn't
    get past it: "<that moment>, came on this pull too", when, and what it came after; None where it didn't come.
    It came as a moment of this pull's own ending, behind the one that leads or with deaths leading into it (or
    without those that led into it earlier: the moment is what is named), or as a buff the boss gained in the
    pull's last END_BUFF_WINDOW_MS and kept: no part of the ending when it came after the wipe was decided or as a
    phase began. When it came is said unless a line above says it (the ending's moments, a buff after the wipe was
    decided)."""
    if earlier is None or earlier.kind != "moments":
        return None
    led, decided = earlier.moments[0], await rep.decided_at(f)
    at, said = None, True
    if hit := next((m for m in ended.moments if moment_kind(m) == moment_kind(led)), None):
        at = hit.at_ms
    elif isinstance(led, KeptBuff):
        up: dict[tuple[int, int], float] = {}
        for e in await rep.boss_buffs(f):
            if rep.ability(e["abilityGameID"]) == led.name and e["type"] in ("applybuff", "removebuff"):
                key = (e["targetID"], e["abilityGameID"])
                up.setdefault(key, e["timestamp"]) if e["type"] == "applybuff" else up.pop(key, None)
        if kept := [t for t in up.values() if f["endTime"] - t <= END_BUFF_WINDOW_MS]:
            at = min(kept) - f["startTime"]
            said = bool(decided and at > decided.at_ms and led.name in {name for name, _ in await rep.end_buffs(f)})
    if at is None:
        return None
    first = ended.moments[0].at_ms if ended.moments else ended.half_dead_ms
    return f"{moment_kind(led)}, came on this pull too" + ("" if said else f", at {clock(at)}") + (
        ", after the wipe was decided" if decided and at > decided.at_ms
        else ", after what ended it" if first is not None and at > first else "")


@dataclass
class Found:
    """What one source of the timeline found: (report ms, text) events, ability ids for lookups, and lines for after
    the events."""
    events: list[tuple[float, str]] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


async def pull_timeline(rep: Report, pull: PullRef = None, boss: str | None = None, start: str | None = None,
                        end: str | None = None, ability: str | None = None, player: str | None = None) -> str:
    """One pull in time order: phases, the boss's casts and buffs, its debuffs on players (how many each time),
    deaths with players alive, Bloodlust, battle res and raid cooldowns, and raid damage taken over time. With
    `ability` or `player`, only that ability's events or that player's casts, a row each (`_only`)."""
    f = rep.fight(pull, boss)
    t0 = f["startTime"]
    lo = t0 + (_at_time(start) if start else 0)
    hi = min(f["endTime"], t0 + _at_time(end)) if end else f["endTime"]
    if hi <= lo:
        raise FactError("the window's end must be after its start")
    window = f", {clock(lo - t0)}-{clock(hi - t0)}" if start or end else ""
    if ability or player:
        return await _only(rep, f, lo, hi, window, ability, player)
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
    seconds = [f"{clock(sec * 1000)} " + "; ".join(texts) for sec, texts in sorted(by_second.items())]
    if len(seconds) > TIMELINE_MAX_LINES:
        seconds = seconds[:TIMELINE_MAX_LINES] + [f"... {len(seconds) - TIMELINE_MAX_LINES} more lines: ask for a "
                                                  "narrower window with start and end"]
    ids: dict[str, int] = {}
    notes = [n for part in found for n in part.notes]
    shown = "\n".join([*seconds, *notes])  # a note names abilities too: the debuffs left out as too frequent
    for part in found:
        for name, i in part.ids.items():
            # a window lists the ids of what it names, not of the whole pull's abilities
            if not window or NTH_ABILITY.sub("", name) in shown:
                ids.setdefault(name, i)
    return "\n".join([_timeline_head(rep, f, window), *seconds, *notes, *resources, damage, ids_line(ids)])


def _timeline_head(rep: Report, f: dict, of: str) -> str:
    """'Sszorak pull 13 timeline, 2:25-2:40 (link). Ended at 2:42 with the boss at 62.6% health.'; `of` is the
    window and what the timeline is narrowed to."""
    return (f"{rep.label(f)} timeline{of} ({rep.url(f)}). Ended at {clock(f['endTime'] - f['startTime'])} with "
            f"{boss_left(f)}." + (" KILL." if f["kill"] else ""))


async def _only(rep: Report, f: dict, lo: float, hi: float, window: str, ability: str | None,
                player: str | None) -> str:
    """A pull's timeline narrowed to one ability, to one player's casts, or to one ability by or on one player:
    each logged event a row with its time to the millisecond (rows.py), counted, the first TIMELINE_MAX_LINES of
    them. Of an ability: its casts (the enemies' and the players'), its hits on players and their pets, its debuff
    going on and off players, its buff on the boss, and the deaths it dealt. Of a player: every cast they and their
    pets began or finished, and their deaths, which a window with no cast after one names. What the plain timeline
    reads is in hand after it; an ability's hits are one more request, whatever number of ids it is logged under."""
    p = rep.player(player) if player else None
    if p:
        rep.require_in(f, p)
    ids = sorted(rep.ability_ids(ability)) if ability else []
    fid, died_before = f["id"], ""
    if ability:
        *logged, hits, deaths = await asyncio.gather(
            rep.events(fid, "Casts", hostilityType="Enemies"), rep.events(fid, "Casts"),
            rep.events(fid, "Debuffs", hostilityType="Friendlies"), rep.boss_buffs(f),
            rep.events_for([(fid, {"abilityID": i}) for i in ids], "DamageTaken", hostilityType="Friendlies"),
            rep.deaths(f))
        events = [e for part in (*logged, *hits) for e in part if e.get("abilityGameID") in ids]
        events += [d for d in deaths if d.get("killingAbilityGameID") in ids]
        name = " and ".join(sorted({rep.ability(i) for i in ids}))
        if p:
            events = [e for e in events if p.id in (rep.owner(e.get("sourceID", 0)), rep.owner(e.get("targetID", 0)))]
        only = f"{name} by or on {p.name}" if p else name
        what = (f"What the log has of {only} (its casts, its hits on players and their pets, its debuff going on "
                "and off players, its buff on the boss, the deaths it dealt)")
        nothing = f"Nothing of {only} is logged"
    else:
        assert p is not None
        casts, deaths = await asyncio.gather(rep.events(fid, "Casts"), rep.deaths(f))
        died = [d for d in deaths if d["targetID"] == p.id]
        events = [e for e in casts if rep.owner(e.get("sourceID", 0)) == p.id] + died
        only = f"{p.name}'s casts"
        what = f"Every cast {p.name} and their pets began or finished, and their deaths"
        nothing = f"No cast of {p.name}'s is logged"
        # rows that stop, or none at all, read as a player who stopped pressing: the log knows they had died
        if before := [d for d in died if d["timestamp"] < lo]:
            died_before = f": their last death before it was at {exact(before[-1]['timestamp'] - f['startTime'])}"
    events = sorted((e for e in events if lo <= e["timestamp"] <= hi), key=lambda e: e["timestamp"])
    head = _timeline_head(rep, f, f"{window}, only {only}")
    if not events:
        return f"{head}\n{nothing} in this {'window' if window else 'pull'}{died_before}."
    listed = rows.listed(rep, events, rows.in_short, most=TIMELINE_MAX_LINES)
    more = ["For the rows left out, ask for a narrower window with start and end."] if listed.left_out else []
    return "\n".join([head, f"{what}: {plural(len(events), 'event')}{listed.where}.", *listed.lines, *more])


async def _run(source, what: str, rep: Report, f: dict) -> Found:
    """One source; one whose knowledge source can't answer says so instead."""
    try:
        return await source(rep, f)
    except KnowledgeError as e:
        return Found(notes=[unavailable(what, e)])


async def _phases(rep: Report, f: dict) -> Found:
    return Found([(t["startTime"], f"phase {rep.phase(f, t['id'])}" + (f" ({name})" if (name := rep.phase_names.get(
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


@dataclass(frozen=True)
class BossCast:
    """The boss casting one ability in one second of a pull: `at` is that second (report ms), `abilities` how many
    ability ids share the name (one cast), `n` how many casts."""
    at: float
    name: str
    tracked: bool  # one Lorrgs tracks for the boss
    abilities: int
    n: int


async def boss_casts(rep: Report, f: dict) -> tuple[list[BossCast], Found]:
    """The boss's casts, one per ability per second, and what the timeline adds about them: the ability ids, and a
    note when Lorrgs couldn't say which it tracks."""
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
    casts = []
    for (sec, name), per_id in same_second.items():
        if len(per_id) > 1:  # several abilities share the name (Mutilate [1277027] and [1277031]): one cast
            for k, gid in enumerate(i for i in per_id if i != out.ids.get(name)):
                out.ids.setdefault(f"{name} ({ordinal(k + 2)} ability)", gid)
        casts.append(BossCast(f["startTime"] + sec * 1000, name, name.lower() in tracked or bool(set(per_id) & tracked),
                              len(per_id), min(per_id.values())))
    return casts, out


async def _boss_casts(rep: Report, f: dict) -> Found:
    """The boss's casts, one line per ability per second, ◆ after those Lorrgs tracks for the boss."""
    casts, out = await boss_casts(rep, f)
    marked = False
    for c in casts:
        text = c.name
        if c.tracked:
            text += f" {TRACKED_MARK}"
            marked = True
        if c.abilities > 1:
            text += f" ({c.abilities} abilities)"
        out.events.append((c.at, f"boss casts {text}" + (f" ×{c.n}" if c.n > 1 else "")))
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
    """A stacking debuff's highest stack in the pull, and the step before it: when it was first reached and on whom,
    everyone who reached it in that moment (within SAME_HIT_MS: the log orders two in one millisecond by nothing)."""
    stacks: dict[str, dict[int, list[dict]]] = defaultdict(dict)  # debuff -> stack count -> the events reaching it
    for e in await _boss_debuffs(rep, f):
        if e["type"] == "applydebuffstack" and e.get("stack"):
            stacks[rep.ability(e["abilityGameID"])].setdefault(e["stack"], []).append(e)
    out = Found()
    for name, reached in stacks.items():
        if (peak := max(reached)) >= STACKS_SHOWN:
            for first, *rest in (reached[n] for n in (peak - 1, peak) if n in reached):
                who = list(dict.fromkeys(rep.names[e["targetID"]] for e in (first, *rest)
                                         if e["timestamp"] - first["timestamp"] <= SAME_HIT_MS))
                on = ", ".join(who) if len(who) <= 3 else plural(len(who), "player")
                out.events.append((first["timestamp"], f"{name} reaches {first['stack']} stacks on {on}"))
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
    backs = (await rep.resurrects([f]))[f["id"]]

    def taken(e: dict) -> str:
        """A res is logged when it is cast: one the player never took (Report._taken) brought nobody back."""
        took = any(abs(t - e["timestamp"]) <= SAME_DEATH_MS for t in backs.get(e.get("targetID", -1), []))
        return "" if took else " (not taken: they did nothing after it, and stay dead in the counts)"
    return Found([(e["timestamp"], f"battle res: {rep.ability(e['abilityGameID'])} by "
                                   f"{rep.actor(rep.owner(e.get('sourceID', 0)))} on {rep.actor(e.get('targetID', 0))}"
                                   f"{taken(e)}")
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


async def raid_cooldown_casts(rep: Report, f: dict) -> list[tuple[float, str, str, str]]:
    """(report ms, "raid cooldown" or "healing cooldown", the spell, who cast it) of each raid and healing cooldown
    used in a pull, in order."""
    raid_cds = await _raid_cooldowns(rep, f)
    last_cast: dict[tuple[int, str], float] = {}
    out = []
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
            out.append((c["timestamp"], kind, name, rep.actor(rep.owner(c.get("sourceID", 0)))))
    return out


async def _raid_cooldowns_used(rep: Report, f: dict) -> Found:
    return Found([(at, f"{kind}: {name} ({who})") for at, kind, name, who in await raid_cooldown_casts(rep, f)])


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
    return await rep.keep(("boss tracks", f["id"]), [f["id"]], lambda: boss_tracks(rep.wcl, rep.code, f, rep.bosses),
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


async def raid_damage(rep: Report, f: dict, lo: float, hi: float) -> dict[int, float] | None:
    """Raid damage taken per second in each DAMAGE_BUCKET_MS from the pull's start, within `lo`-`hi` (taken.py, as
    healing reads it): bucket -> per second; None when Warcraft Logs has none."""
    try:
        taken = (await raid_taken(rep, [f]))[f["id"]]
    except WCLError:
        return None
    if taken is None:
        return None
    t0, out = f["startTime"], {}
    for b in range(int((lo - t0) // DAMAGE_BUCKET_MS), math.ceil((hi - t0) / DAMAGE_BUCKET_MS)):
        a, z = max(lo, t0 + b * DAMAGE_BUCKET_MS), min(hi, t0 + (b + 1) * DAMAGE_BUCKET_MS)
        if z > a:
            out[b] = taken.rate(a, z)
    return out


async def _raid_damage(rep: Report, f: dict, lo: float, hi: float) -> str:
    """Raid damage taken per second, averaged per DAMAGE_BUCKET_MS, as a line."""
    values = await raid_damage(rep, f, lo, hi)
    if values is None:
        return "Raid damage taken over time: unavailable."
    if not values:
        return "Raid damage taken over time: none in this window."
    typical = sorted(values.values())[len(values) // 2]
    return (f"Raid damage taken per second, {DAMAGE_BUCKET_MS // 1000} s averages (median {short(typical)}/s; "
            "* marks twice the median or more): "
            + " · ".join(f"{clock(b * DAMAGE_BUCKET_MS)} {short(v)}{'*' if v >= 2 * typical else ''}"
                         for b, v in values.items()))


async def boss_tracks(wcl: WCL, code: str, fight: dict, bosses: set[int]) -> dict[str, list[tuple[float, float]]]:
    """The boss's health % and power bar (0-100) over a fight, as (ms from the pull, value) from WCL's Resources
    graph. The power type comes from the boss's own events; a boss without a power bar has no "power". The health
    is the boss that cast most, then, once it died or left, the boss that took over (the next phase's boss, such as
    the Coiled Altar's in P2), each from its own 100%."""
    casts = [c for c in await wcl.events(code, fight["id"], "Casts", hostilityType="Enemies", includeResources=True)
             if c.get("sourceID") in bosses]
    if not casts:
        return {}
    cast_by = Counter(c["sourceID"] for c in casts)
    boss = cast_by.most_common(1)[0][0]
    power = next((c["classResources"][0].get("type") for c in casts
                  if c.get("sourceID") == boss and c.get("classResources")), None)
    out: dict[str, list[tuple[float, float]]] = {}
    for key, ability in (("health", BOSS_HEALTH), ("power", 100 + power if power is not None else None)):
        if ability is None:
            continue
        try:  # health of every enemy, to follow the boss into the next phase; power the boss's own
            graph = await wcl.graph(code, fight, "Resources", hostilityType="Enemies", abilityID=ability,
                                    **({} if key == "health" else {"sourceID": boss}))
        except WCLError:
            continue
        series = {x.get("id"): [(t - fight["startTime"], v) for t, v in x.get("data") or []]
                  for x in graph.get("series") or [] if x.get("id") in bosses}
        out[key] = _taken_over(series, boss, cast_by) if key == "health" else series.get(boss, [])
    return out


async def boss_series(wcl: WCL, code: str, fight: dict, bosses: dict[int, tuple[int, str]]) -> list[dict]:
    """Each boss's own health % and power bar over a fight with several bosses, as {game_id, name, health, power}
    with (ms from the pull, value) points from WCL's Resources graph. `bosses` are the fight's boss units, actor id
    to (NPC game id, name): the game id is the same in every report, so two pulls can follow the same boss
    (compare.same_boss), where `boss_tracks` picks one by what happened in the pull. A boss without a power bar
    has no power points."""
    casts = [c for c in await wcl.events(code, fight["id"], "Casts", hostilityType="Enemies", includeResources=True)
             if c.get("sourceID") in bosses]
    power: dict[int, int] = {}
    for c in casts:
        if c.get("classResources") and (kind := c["classResources"][0].get("type")) is not None:
            power.setdefault(c["sourceID"], kind)

    async def points(ability: int, **only) -> dict[int, list[tuple[float, float]]]:
        try:
            graph = await wcl.graph(code, fight, "Resources", hostilityType="Enemies", abilityID=ability, **only)
        except WCLError:
            return {}
        return {x.get("id"): [(t - fight["startTime"], v) for t, v in x.get("data") or []]
                for x in graph.get("series") or [] if x.get("id") in bosses}
    health = await points(BOSS_HEALTH)
    return [{"game_id": game_id, "name": name, "health": health.get(boss, []),
             "power": (await points(100 + power[boss], sourceID=boss)).get(boss, []) if boss in power else []}
            for boss, (game_id, name) in sorted(bosses.items())]


def _taken_over(series: dict[int, list[tuple[float, float]]], boss: int,
                cast_by: Counter) -> list[tuple[float, float]]:
    """`boss`'s points, then, after its last, the points of the boss whose health starts changing next (the one
    that cast most of those starting at the same moment), and so on: one boss's health at a time."""
    track = list(series.get(boss, []))
    left = {b: pts for b, pts in series.items() if b != boss and pts}
    while track and left:
        end = track[-1][0]
        later = {b: [p for p in pts if p[0] > end] for b, pts in left.items()}
        moving = {b: pts for b, pts in later.items()  # a boss standing at full health hasn't taken over yet
                  if pts and (pts[0][1] < 100 or any(v != pts[0][1] for _, v in pts))}
        if not moving:
            break
        nxt = min(moving, key=lambda b: (moving[b][0][0], -cast_by[b]))
        track += moving[nxt]
        left.pop(nxt)
    return track
