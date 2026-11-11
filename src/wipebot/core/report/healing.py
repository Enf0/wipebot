"""The healers: the raid's damage spikes on a pull or over a boss's pulls (where none has one, their busiest
stretches) and the healing into them (`healing`), and what each healer was doing as a player died (`death_recap`)."""
import asyncio
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..cache import FOREVER
from ..knowledge import KnowledgeError
from ..text import clock, ids_line, plural, short
from ..wcl import WCLError
from .base import COOLDOWNS_NOT_LOGGED, Player, PullRef, Report, dead_since, unavailable
from .casting import casting, raid_pauses, without

# A spike: raid damage taken at SPIKE_TIMES the pull's median or more, averaged over SPIKE_AVG_MS. The timeline
# marks twice its median; a Mythic fight's waves of damage seldom reach that.
SPIKE_TIMES = 1.5
SPIKE_AVG_MS = 5_000
SPIKE_JOIN_MS = 3_000  # spikes this close together are one
SPIKE_MIN_MS = 3_000  # shorter is one hit, not a spike
SPIKES_SHOWN = 4  # one pull: its biggest spikes
NIGHT_SPIKES = 3  # a boss's pulls: each pull's biggest
# Where no pull has a spike, each pull's busiest stretches: built as spikes are, at BUSY_TIMES its median or more. A
# fight whose damage is high all through (The Twin Fangs: a 1.11M/s median, peaking at 1.53M/s, 1.4x) never reaches
# SPIKE_TIMES, and its worst stretch is still where its healing cooldowns were wanted; a pull that never reaches
# BUSY_TIMES has none to show.
BUSY_TIMES = 1.25
BUSIEST_SHOWN = 3  # one pull: its busiest stretches
SAME_SPIKE_MS = 10_000  # spikes in different pulls starting this close to the first of them: the same moment
COOLDOWN_LEAD_MS = 5_000  # a healing cooldown this soon before a spike was used for it (a totem goes down first)
# Pressed earlier, it counts while it lasts by Lorrgs' duration through half the spike or more: Avenging Wrath at 3:32
# lasts through 3:38-3:44, which read "none" (The Twin Fangs), but an opener's last second isn't the 0:20 spike's.
ABILITIES_NAMED = 2  # the abilities behind each spike
AT_DEATH_MS = 5_000  # death_recap: each healer's casts in this window before a death
CASTS_NAMED = 4


@dataclass
class Spike:
    fight: dict
    start: float  # report time
    end: float
    taken: float = 0.0  # raid damage taken in it
    healing: float = 0.0  # everyone's effective healing in it
    abilities: list[tuple[str, int, float]] = field(default_factory=list)  # (name, id, share of the damage)
    healers: dict[int, InSpike] = field(default_factory=dict)
    cooldowns: list[str] = field(default_factory=list)  # used in it: "Spirit Link Totem (Enfo) at 1:13"
    ready: list[str] = field(default_factory=list)  # ready and unused
    deaths: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / 1000

    @property
    def what(self) -> str:
        return self.abilities[0][0] if self.abilities else "unknown"


@dataclass
class InSpike:
    """One healer in one spike."""
    healing: float = 0.0
    overheal: float = 0.0
    idle_s: float = 0.0  # alive with no cast begun
    dead_s: float = 0.0
    cooldowns: int = 0
    ready: int = 0


async def healing(rep: Report, pull: PullRef = None, boss: str | None = None, all_pulls: bool = False,
                  jobs: str = COOLDOWNS_NOT_LOGGED) -> str:
    """The spikes (or busiest stretches) and the healing into them; `jobs` follows them, once, when a healing
    cooldown was ready and unused in one: who was meant to press it isn't in the log (server.py says whether a raid
    note was saved)."""
    pulls = rep.by_boss[rep.boss(boss)] if all_pulls else [rep.fight(pull, boss)]
    fights, going = (rep.ended(pulls), rep.still_going(pulls)) if all_pulls else (pulls, "")
    cut = await rep.decided_cuts(fights)  # what follows is fallout: everyone heals the few still alive
    ends = {f["id"]: cut.get(f["id"], f["endTime"]) for f in fights}
    try:
        graphs = await _damage_graphs(rep, fights)
    except WCLError as e:
        return going + unavailable("Raid damage taken over time", e)
    spikes = _biggest(fights, graphs, ends, SPIKE_TIMES, NIGHT_SPIKES if all_pulls else SPIKES_SHOWN)
    busiest = not spikes  # no pull has a spike: each pull's busiest stretches, read the same way
    if busiest:
        spikes = _biggest(fights, graphs, ends, BUSY_TIMES, NIGHT_SPIKES if all_pulls else BUSIEST_SHOWN)
    scope = f"all {plural(len(fights), fights[0]['name'] + ' pull')}" if all_pulls else rep.label(fights[0])
    if cut:
        scope += (" until the wipe was decided (the boss's enrage or half the raid dead"
                  + (" in each pull" if all_pulls else f", {clock(cut[fights[0]['id']] - fights[0]['startTime'])}")
                  + ")")
    shown = ("damage spikes and the healing into them" if not busiest else
             "busiest stretches of damage taken and the healing into them" if spikes else "damage taken")
    head = (f"The raid's {shown}, {scope}"
            + ("" if all_pulls else f" ({rep.url(fights[0])})") + f". A spike: raid damage taken at {SPIKE_TIMES}x "
            f"the pull's median or more over {SPIKE_AVG_MS // 1000} s, at least {SPIKE_MIN_MS // 1000} s long"
            + (f"; where a pull has none, its busiest stretches: at {BUSY_TIMES}x or more, as long" if busiest else "")
            + ".")
    if not spikes:
        return going + head + "\n" + _sentence("; ".join(
            (f"pull {rep.pull_no(f)}: " if all_pulls else "") + _even(f, graphs.get(f["id"]), ends[f["id"]], BUSY_TIMES)
            for f in fights)) + "."
    whole = await _fill(rep, fights, spikes, ends)
    healers = {p.id: p for f in fights for p in rep.pull_players(f).values() if p.role == "healer"}
    lines = [going + head]
    if busiest and all_pulls:
        lines.append(f"No pull's raid damage taken was at {SPIKE_TIMES}x its median over {SPIKE_AVG_MS // 1000} s "
                     f"for {SPIKE_MIN_MS // 1000} s or more.")
    elif busiest:
        lines.append(_sentence(_even(fights[0], graphs.get(fights[0]["id"]), ends[fights[0]["id"]])) + "; its "
                     + ("busiest stretch:" if len(spikes) == 1 else f"{len(spikes)} busiest stretches:"))
    if all_pulls:
        lines += _night_lines(rep, fights, spikes, healers, busiest)
    else:
        lines += [_spike_line(rep, s, healers) for s in spikes]
    if any(s.ready for s in spikes):
        lines.append(jobs)
    lines += _healer_lines(rep, fights, spikes, healers, whole, ends, busiest)
    lines.append(ids_line({name: i for s in spikes for name, i, _ in s.abilities}))
    return "\n".join(lines)


async def _damage_graphs(rep: Report, fights: list[dict]) -> dict[int, dict]:
    """WCL's graph of raid damage taken for each pull, the ones not remembered in one query (the timeline's)."""
    async def fetch(missing: list[int]) -> dict[int, dict]:
        return await rep.wcl.graphs(rep.code, [f for f in fights if f["id"] in missing], "DamageTaken",
                                    hostilityType="Friendlies")
    return await rep.keep_many({f["id"]: (("graph", f["id"], "DamageTaken"), f["id"]) for f in fights}, fetch,
                               FOREVER)


def _points(f: dict, graph: dict | None, end: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]],
                                                              float]:
    """Raid damage taken per second until `end` from WCL's graph: (each point, each point averaged over
    SPIKE_AVG_MS, the averages' median) as (report time, per second)."""
    total = next((s for s in (graph or {}).get("series") or [] if s.get("name") == "Total"), None)
    if not total or not total.get("pointInterval"):
        return [], [], 0.0
    step = total["pointInterval"]
    start = total.get("pointStart", f["startTime"])
    points = [(start + i * step, v) for i, v in enumerate(total.get("data") or []) if start + i * step < end]
    if not points:
        return [], [], 0.0
    half = SPIKE_AVG_MS / 2
    avg = []
    for t, _ in points:
        near = [v for u, v in points if abs(u - t) <= half]
        avg.append((t, sum(near) / len(near)))
    return points, avg, statistics.median(v for _, v in avg)


def _biggest(fights: list[dict], graphs: dict[int, dict], ends: dict[int, float], times: float,
             shown: int) -> list[Spike]:
    """Each pull's `shown` biggest spikes at `times` its median (busiest stretches at BUSY_TIMES), in time order."""
    return [s for f in fights for s in sorted(_spikes(f, graphs.get(f["id"]), ends[f["id"]], times)[:shown],
                                              key=lambda s: s.start)]


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _even(f: dict, graph: dict | None, end: float, times: float = SPIKE_TIMES) -> str:
    """A pull without spikes, or without busiest stretches (`times` BUSY_TIMES): its median and peak against that
    bar. The peak is an average over SPIKE_AVG_MS and is named by where those begin, as pull_timeline names its
    averages of as long: by their middle, the same seconds read 2 s later here than there. At the pull's start and
    at `end` fewer seconds are averaged, and those are the seconds said."""
    points, avg, typical = _points(f, graph, end)
    if not avg:
        return "no raid damage taken recorded"
    at, peak = max(avg, key=lambda p: p[1])
    near = [u for u, _ in points if abs(u - at) <= SPIKE_AVG_MS / 2]  # the points `_points` averaged for it
    step = points[1][0] - points[0][0] if len(points) > 1 else SPIKE_AVG_MS
    over = min(SPIKE_AVG_MS, min(near[-1] + step, end) - near[0])
    median = f"{times}x its median of {short(typical)}/s"
    if typical <= 0:  # no damage in half its 5 s or more: nothing is measured against that median
        held = f"had a median of 0/s over {SPIKE_AVG_MS // 1000} s, against which no stretch is measured"
    elif peak >= times * typical:
        held = f"reached {median} for under {SPIKE_MIN_MS // 1000} s at a time"  # runs too short to be one
    else:
        held = f"stayed under {median} over any {SPIKE_AVG_MS // 1000} s"
    return (f"the raid's damage taken {held}, highest {short(peak)}/s over the {max(round(over / 1000), 1)} s from "
            f"{clock(near[0] - f['startTime'])}")


def _spikes(f: dict, graph: dict | None, end: float, times: float = SPIKE_TIMES) -> list[Spike]:
    """A pull's spikes, biggest first: where raid damage taken per second, averaged over SPIKE_AVG_MS, is `times`
    its median until `end` or more (BUSY_TIMES for its busiest stretches)."""
    points, avg, typical = _points(f, graph, end)
    if not points:
        return []
    step = points[1][0] - points[0][0] if len(points) > 1 else 0
    runs: list[list[float]] = []
    for t, v in avg:
        if typical > 0 and v >= times * typical:
            if runs and t - runs[-1][1] <= SPIKE_JOIN_MS:
                runs[-1][1] = t
            else:
                runs.append([t, t])
    # Ranked by the damage of the points in each run, its first to its last: its end, a step after its last point,
    # can be a hair past the next point, which isn't in it (the step isn't whole milliseconds).
    kept = [(s, sum(v for t, v in points if a <= t <= b)) for a, b in runs
            if (s := Spike(f, a, min(b + step, end))).end - s.start >= SPIKE_MIN_MS]
    return [s for s, _ in sorted(kept, key=lambda k: -k[1])]


async def _fill(rep: Report, fights: list[dict], spikes: list[Spike], ends: dict[int, float]) -> dict[int, dict]:
    """Each spike's damage by ability, healing by player, healers' casting and cooldowns, and deaths; returns each
    pull's whole Healing table (until decided), for the healing outside the spikes. Tables in one query each."""
    windows = [(s.fight["id"], {"startTime": s.start, "endTime": s.end}) for s in spikes]
    taken, healed, whole, casts, backs, used = await asyncio.gather(
        rep.tables(windows, "DamageTaken", viewBy="Ability", hostilityType="Friendlies"),
        rep.tables(windows, "Healing"),
        rep.tables([(f["id"], {"endTime": ends[f["id"]]}) for f in fights], "Healing"),
        rep.events_for([(f["id"], {}) for f in fights], "Casts"),
        rep.resurrects(fights), rep.defensive_casts())
    by_fight = {f["id"]: c for f, c in zip(fights, casts, strict=True)}
    pauses = dict(zip([f["id"] for f in fights], await asyncio.gather(*(raid_pauses(rep, f) for f in fights)),
                      strict=True))
    cds = await _healing_cooldowns(rep, fights)
    pressed = {(c.get("sourceID"), name) for c in used for (pid, name), sp in cds.items()
               if c.get("sourceID") == pid and rep.is_spell(c, sp)}
    for s, t, h in zip(spikes, taken, healed, strict=True):
        f, t0 = s.fight, s.fight["startTime"]
        by_name: dict[str, list] = {}  # an ability logged under several ids is one, under its biggest id's
        for e in sorted(t.get("entries") or [], key=lambda e: -(e.get("total") or 0)):
            name = e.get("name") or rep.ability(e.get("guid"))
            by_name.setdefault(name, [e.get("guid") or 0, 0])[1] += e.get("total") or 0
        damage = [(name, i, n) for name, (i, n) in by_name.items()]
        s.taken = sum(n for _, _, n in damage)
        s.abilities = [(name, i, n / s.taken) for name, i, n in sorted(damage, key=lambda d: -d[2])[:ABILITIES_NAMED]
                       if s.taken]
        rows = {e.get("id"): e for e in h.get("entries") or []}
        s.healing = sum(e.get("total") or 0 for e in rows.values())
        deaths = await rep.deaths(f)
        s.deaths = [f"{rep.names[d['targetID']]} ({rep.ability(d.get('killingAbilityGameID'))}, "
                    f"{clock(d['timestamp'] - t0)})" for d in deaths if s.start <= d["timestamp"] <= s.end]
        for p in (p for p in rep.pull_players(f).values() if p.role == "healer"):
            row, mine = rows.get(p.id) or {}, InSpike()
            mine.healing, mine.overheal = row.get("total") or 0, row.get("overheal") or 0
            died = sorted(d["timestamp"] for d in deaths if d["targetID"] == p.id)
            gone = dead_since(deaths, backs[f["id"]].get(p.id, []), p.id, s.end)
            if gone is not None:
                mine.dead_s = (s.end - max(gone, s.start)) / 1000
            own = [c for c in by_fight[f["id"]] if c.get("sourceID") == p.id]
            gaps = without(casting(own, s.end, died).gaps, pauses[f["id"]])
            mine.idle_s = sum(max(0.0, min(b, s.end) - max(a, s.start)) for a, b in gaps) / 1000
            for (pid, name), sp in cds.items():
                if pid != p.id:
                    continue
                theirs = [c["timestamp"] for c in own if c["type"] == "cast" and rep.is_spell(c, sp)]
                if at := _up(theirs, sp, s.start, s.end):
                    s.cooldowns.append(f"{name} ({p.name}) at {clock(at[0] - t0)}")
                    mine.cooldowns += 1
                elif (p.id, name) in pressed and gone is None and _ready(theirs, sp, s.start):
                    s.ready.append(f"{name} ({p.name})")
                    mine.ready += 1
            s.healers[p.id] = mine
    return {f["id"]: w for f, w in zip(fights, whole, strict=True)}


def _up(times: list[float], sp: dict, start: float, end: float) -> list[float]:
    """The casts of healing cooldown `sp` up in `start`-`end`: pressed in it or up to COOLDOWN_LEAD_MS before it, or
    earlier and lasting, by Lorrgs' duration, through half of it."""
    lasts, middle = 1000 * (sp.get("duration") or 0), (start + end) / 2
    return [t for t in times if start - COOLDOWN_LEAD_MS <= t <= end or t < start and t + lasts >= middle]


def _ready(times: list[float], sp: dict, at: float) -> bool:
    last = max((t for t in times if t <= at), default=None)
    return last is None or (at - last) / 1000 >= (sp.get("cooldown") or 0)


async def _healing_cooldowns(rep: Report, fights: list[dict]) -> dict[tuple[int, str], dict]:
    """(healer id, spell name) -> the Lorrgs spell, for every healer's raid cooldowns (Lorrgs' raid_cd tag) with a
    cooldown. A spec Lorrgs can't answer for has none."""
    healers = {p.id: p for f in fights for p in rep.pull_players(f).values() if p.role == "healer"}
    out: dict[tuple[int, str], dict] = {}
    for p in healers.values():
        try:
            spells = await rep.spells(p)
        except KnowledgeError:
            continue
        for sp in spells:
            tags = sp.get("tags") or []
            if "raid_cd" in tags and "move" not in tags and sp.get("cooldown"):
                out[(p.id, sp["name"])] = sp
    return out


def _share(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def _over(healing: float, overheal: float) -> str:
    return _share(overheal, healing + overheal)


def _spike_line(rep: Report, s: Spike, healers: dict[int, Player]) -> str:
    t0 = s.fight["startTime"]
    what = ", ".join(f"{name} {100 * share:.0f}%" for name, _, share in s.abilities) or "no damage recorded"
    out = [f"{clock(s.start - t0)}-{clock(s.end - t0)} ({s.seconds:.0f} s): {short(s.taken / s.seconds)}/s taken "
           f"({what}); healing {short(s.healing / s.seconds)}/s, {_share(s.healing, s.taken)} of the damage taken."]
    out.append("  Healers: " + "; ".join(_in_spike(healers[pid], h, s.seconds) for pid, h in s.healers.items()))
    out.append(f"  Healing cooldowns up in it (pressed in it or up to {COOLDOWN_LEAD_MS // 1000} s before, or earlier "
               "and lasting through half of it): "
               + (", ".join(s.cooldowns) or "none")
               + "; ready and unused (by healers who press them this report): " + (", ".join(s.ready) or "none"))
    if s.deaths:
        out.append("  Deaths in it: " + ", ".join(s.deaths))
    return "\n".join(out)


def _in_spike(p: Player, h: InSpike, seconds: float) -> str:
    if h.dead_s >= seconds:
        return f"{p.name} dead"
    text = f"{p.name} {short(h.healing / seconds)}/s ({_over(h.healing, h.overheal)} overheal)"
    if h.idle_s:
        text += f", no cast begun for {h.idle_s:.0f} s"
    if h.dead_s:
        text += f", dead for the last {h.dead_s:.0f} s"
    return text


def _kind(busiest: bool) -> tuple[str, str]:
    """What the stretches shown are called, one and many: spikes, or a pull's busiest stretches where none has one."""
    return ("busiest stretch", "busiest stretches") if busiest else ("spike", "spikes")


def _count(k: int, kind: tuple[str, str]) -> str:
    return f"{k} {kind[k != 1]}"


def _night_lines(rep: Report, fights: list[dict], spikes: list[Spike], healers: dict[int, Player],
                 busiest: bool = False) -> list[str]:
    """Each pull's biggest spikes (or busiest stretches) in a line, then the spikes by the ability behind most of
    their damage."""
    one, many = kind = _kind(busiest)
    lines = [f"Each pull's {NIGHT_SPIKES} {many if busiest else 'biggest ' + many} (time, length, damage taken per "
             "second and the ability behind most of it, healing as a share of it, healing cooldowns used, deaths in "
             "it):"]
    by_fight: dict[int, list[Spike]] = defaultdict(list)
    for s in spikes:
        by_fight[s.fight["id"]].append(s)
    for f in fights:
        theirs = by_fight.get(f["id"])
        if not theirs:
            continue
        t0 = f["startTime"]
        lines.append(f"  pull {rep.pull_no(f)}: " + " · ".join(
            f"{clock(s.start - t0)} {s.seconds:.0f} s {short(s.taken / s.seconds)}/s ({s.what}), healing "
            f"{_share(s.healing, s.taken)}, {plural(len(s.cooldowns), 'cooldown')}"
            + (f", {plural(len(s.deaths), 'death')}" if s.deaths else "") for s in theirs))
    groups: list[list[Spike]] = []
    for s in sorted(spikes, key=lambda s: s.start - s.fight["startTime"]):
        if groups and s.start - s.fight["startTime"] - (groups[-1][0].start - groups[-1][0].fight["startTime"]) \
                <= SAME_SPIKE_MS:
            groups[-1].append(s)
        else:
            groups.append([s])
    groups = [g for g in groups if len({s.fight["id"] for s in g}) > 1]
    if groups:
        lines.append(f"The {many} that came back pull after pull (starts within {SAME_SPIKE_MS // 1000} s of each "
                     "other; the abilities behind most of their damage, pulls, typical length, damage taken and "
                     f"healing per second, healing cooldowns used per {one}, {many} with a healing cooldown ready "
                     "and unused, deaths in them):")
    for group in sorted(groups, key=lambda g: -len(g)):
        pulls = len({s.fight["id"] for s in group})
        damage: Counter = Counter()
        for s in group:
            damage.update({name: share * s.taken for name, _, share in s.abilities})
        starts = [s.start - s.fight["startTime"] for s in group]
        lines.append(
            f"  {clock(min(starts))}" + (f"-{clock(max(starts))}" if clock(max(starts)) != clock(min(starts)) else "")
            + ", "
            f"{', '.join(name for name, _ in damage.most_common(ABILITIES_NAMED))}: "
            f"{_count(len(group), kind)} in {plural(pulls, 'pull')}, "
            f"{statistics.median(s.seconds for s in group):.0f} s, "
            f"{short(statistics.median(s.taken / s.seconds for s in group))}/s taken, healing "
            f"{short(statistics.median(s.healing / s.seconds for s in group))}/s; "
            f"{sum(len(s.cooldowns) for s in group) / len(group):.1f} healing cooldowns per {one}, "
            f"{sum(1 for s in group if s.ready)} with one ready and unused; "
            f"{plural(sum(len(s.deaths) for s in group), 'death')}")
    ready = Counter(r for s in spikes for r in s.ready)
    if ready:
        lines.append(f"Healing cooldowns most often ready and unused in a {one}: "
                     + ", ".join(f"{r} {plural(n, 'time')}" for r, n in ready.most_common(5)))
    return lines


def _healer_lines(rep: Report, fights: list[dict], spikes: list[Spike], healers: dict[int, Player],
                  whole: dict[int, dict], ends: dict[int, float], busiest: bool = False) -> list[str]:
    """Each healer in the spikes (or busiest stretches) shown against the rest of the time: healing per second,
    overhealing, time with no cast begun or dead, healing cooldowns used in a spike and left ready in one."""
    one, many = _kind(busiest)
    rows = []
    for pid, p in healers.items():
        mine = [(s, s.healers[pid]) for s in spikes if pid in s.healers]
        if not mine:
            continue
        spike_s = sum(s.seconds for s, _ in mine)
        heal = sum(h.healing for _, h in mine)
        over = sum(h.overheal for _, h in mine)
        theirs = [e for w in whole.values() for e in w.get("entries") or [] if e.get("id") == pid]
        all_heal = sum(e.get("total") or 0 for e in theirs)
        all_over = sum(e.get("overheal") or 0 for e in theirs)
        rest_s = sum((ends[f["id"]] - f["startTime"]) / 1000 for f in fights if rep.was_in(f, p)) - spike_s
        rest = (f"{short((all_heal - heal) / rest_s)}/s ({_over(all_heal - heal, all_over - over)} overheal)"
                if rest_s > 0 else "-")
        idle, dead = sum(h.idle_s for _, h in mine), sum(h.dead_s for _, h in mine)
        missed = ", ".join(x for x in (f"no cast begun for {idle:.0f} s" if idle >= 1 else "",
                                       f"dead for {dead:.0f} s" if dead >= 1 else "") if x) or "casting throughout"
        rows.append(f"  {p.label}: {short(heal / spike_s)}/s ({_over(heal, over)} overheal) in the {many}, "
                    f"{rest} the rest of the time; {missed} in {spike_s:.0f} s of {many}; "
                    f"{plural(sum(h.cooldowns for _, h in mine), 'healing cooldown')} in a {one}, "
                    f"{sum(h.ready for _, h in mine)} left ready in one")
    if not rows:
        return []
    return [f"Each healer in the {many} against the rest of the time (healing on a wipe is capped by the damage "
            "taken, so a healer topping a quiet stretch shows high overheal; no cast begun leaves out raid-wide "
            "stuns):", *rows]


async def at_death(rep: Report, f: dict, p: Player, at: float) -> list[str]:
    """What each other healer was doing in the AT_DEATH_MS before a player died: their casts and on whom (on the
    dying player marked), no cast begun, or dead."""
    healers = [q for q in rep.pull_players(f).values() if q.role == "healer" and q.id != p.id]
    if not healers:
        return []
    events, deaths, backs = await asyncio.gather(rep.events(f["id"], "Casts"), rep.deaths(f), rep.resurrects([f]))
    t0, since = f["startTime"], at - AT_DEATH_MS
    parts = []
    for q in healers:
        if (gone := dead_since(deaths, backs[f["id"]].get(q.id, []), q.id, at)) is not None:
            parts.append(f"{q.name} dead since {clock(gone - t0)}")
            continue
        theirs = [c for c in events if c.get("sourceID") == q.id and since <= c["timestamp"] <= at
                  and c["type"] in ("cast", "begincast")]
        if not theirs:
            last = max((c["timestamp"] for c in events if c.get("sourceID") == q.id and c["timestamp"] < since),
                       default=None)
            parts.append(f"{q.name} no cast begun" + (f" since {clock(last - t0)}" if last is not None else ""))
            continue
        on = Counter(rep.ability(c["abilityGameID"]) + (" on them" if c.get("targetID") == p.id else "")
                     for c in theirs if c["type"] == "cast")
        begun = [c for c in theirs if c["type"] == "begincast" and not any(
            x["type"] == "cast" and x["abilityGameID"] == c["abilityGameID"] and x["timestamp"] >= c["timestamp"]
            for x in theirs)]
        text = ", ".join(f"{name}" + (f" ×{n}" if n > 1 else "") for name, n in on.most_common(CASTS_NAMED))
        if begun:
            text += (", " if text else "") + f"casting {rep.ability(begun[-1]['abilityGameID'])}" + (
                " on them" if begun[-1].get("targetID") == p.id else "")
        parts.append(f"{q.label}: {text}")
    return [f"Healers in the {AT_DEATH_MS // 1000} s before the death (their casts, on the dying player marked): "
            + "; ".join(parts)]
