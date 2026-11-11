"""The healers: the raid's damage spikes on a pull or over a boss's pulls (a pull without one, its busiest 5 s) and the
healing into them (`healing`), and what each healer was doing as a player died (`death_recap`)."""
import asyncio
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..knowledge import KnowledgeError
from ..text import clock, ids_line, plural, short
from ..wcl import WCLError
from .base import COOLDOWNS_NOT_LOGGED, Player, PullRef, Report, dead_since, unavailable
from .casting import casting, raid_pauses, without
from .cooldowns import one_per_use
from .taken import Taken, raid_taken

# A spike: raid damage taken at SPIKE_TIMES the pull's median or more, averaged over SPIKE_AVG_MS. The timeline
# marks twice its median; a Mythic fight's waves of damage seldom reach that.
SPIKE_TIMES = 1.5
SPIKE_AVG_MS = 5_000
# Read a second at a time from the pull's start (taken.py's damage, exact to the second): spikes start and end on whole
# seconds, and their tables are asked for those.
SPIKE_STEP_MS = 1_000
SPIKE_JOIN_MS = 3_000  # spikes this close together are one
SPIKE_MIN_MS = 3_000  # shorter is one hit, not a spike
SPIKES_SHOWN = 4  # one pull: its biggest spikes in full; the others in a line, with the healing cooldowns up in each
NIGHT_SPIKES = 3  # a boss's pulls: each pull's biggest
# One pull without a spike: its busiest SPIKE_AVG_MS, read as a spike is, and called nothing else. Read second by
# second, 4 of 121 pulls that weren't resets had none (6 bosses in 6 reports; Sszorak pulls 26 and 32 of
# KBYT4xXn7VNjDgh8, the Coiled Altar's 6 and 19 in 64hVgfmydQGNxC7A): each reached 1.5x its median for under 3 s, and
# its worst moment is still where a healing cooldown was wanted. Over a boss's pulls, those without one are left out.
SAME_SPIKE_MS = 10_000  # spikes in different pulls starting this close to the first of them: the same moment
COOLDOWN_LEAD_MS = 5_000  # a healing cooldown this soon before a spike was used for it (a totem goes down first)
# Pressed earlier, it counts while it lasts by Lorrgs' duration through half the spike or more: Avenging Wrath at 3:32
# lasts through 3:39-3:47, which read "none" (The Twin Fangs), but an opener's last second isn't the 0:20 spike's.
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
    up: set[tuple[int, str, float]] = field(default_factory=set)  # the presses behind `cooldowns`: (healer, name, when)

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
    ready: int = 0


async def healing(rep: Report, pull: PullRef = None, boss: str | None = None, all_pulls: bool = False,
                  jobs: str = COOLDOWNS_NOT_LOGGED) -> str:
    """The spikes (one pull without any, its busiest 5 s) and the healing into them; `jobs` follows them, once, when a
    healing cooldown was ready and unused in one: who was meant to press it isn't in the log (server.py says whether a
    raid note was saved)."""
    pulls = rep.by_boss[rep.boss(boss)] if all_pulls else [rep.fight(pull, boss)]
    fights, going = (rep.ended(pulls), rep.still_going(pulls)) if all_pulls else (pulls, "")
    cut = await rep.decided_cuts(fights)  # what follows is fallout: everyone heals the few still alive
    ends = {f["id"]: cut.get(f["id"], f["endTime"]) for f in fights}
    try:
        taken = await raid_taken(rep, fights)
    except WCLError as e:
        return going + unavailable("Raid damage taken over time", e)
    found = {f["id"]: _spikes(f, taken[f["id"]], ends[f["id"]]) for f in fights}
    spikes = [s for f in fights for s in sorted(found[f["id"]][:NIGHT_SPIKES if all_pulls else SPIKES_SHOWN],
                                                key=lambda s: s.start)]
    # One pull with more spikes than are shown in full: the others in a line, so none is left out unsaid
    more = [] if all_pulls else sorted(found[fights[0]["id"]][SPIKES_SHOWN:], key=lambda s: s.start)
    busiest, held = None, ""  # one pull without a spike: its busiest seconds, as "the busiest 5 s"
    if not spikes and not all_pulls and (peak := _peak(fights[0], taken[fights[0]["id"]], ends[fights[0]["id"]])):
        held, a, b, per_s = peak
        if per_s > 0:
            busiest, spikes = f"the busiest {_seconds(a, b)} s", [Spike(fights[0], a, b)]
    scope = f"all {plural(len(fights), fights[0]['name'] + ' pull')}" if all_pulls else rep.label(fights[0])
    if cut:
        scope += (" until the wipe was decided (the boss's enrage or half the raid dead"
                  + (" in each pull" if all_pulls else f", {clock(cut[fights[0]['id']] - fights[0]['startTime'])}")
                  + ")")
    shown = (f"{busiest.removeprefix('the ')} of damage taken and the healing into them" if busiest else
             f"{len(spikes)} biggest damage spikes of {len(spikes) + len(more)} (by the damage taken in them) and the "
             "healing into them" if more else
             "damage spikes and the healing into them" if spikes else "damage taken")
    head = (f"The raid's {shown}, {scope}"
            + ("" if all_pulls else f" ({rep.url(fights[0])})") + f". A spike: raid damage taken at {SPIKE_TIMES}x "
            f"the pull's median or more over {SPIKE_AVG_MS // 1000} s, at least {SPIKE_MIN_MS // 1000} s long.")
    if not spikes:
        return going + head + "\n" + _sentence("; ".join(
            (f"pull {rep.pull_no(f)}: " if all_pulls else "") + _even(f, taken[f["id"]], ends[f["id"]])
            for f in fights)) + "."
    whole = await _fill(rep, fights, spikes, ends, more)
    healers = {p.id: p for f in fights for p in rep.pull_players(f).values() if p.role == "healer"}
    lines = [going + head]
    if busiest:
        lines.append(f"The raid's damage taken {held}; {busiest}:")
    if all_pulls:
        lines += _night_lines(rep, fights, spikes, healers)
    else:
        lines += [_spike_line(rep, s, healers) for s in spikes]
        lines += _more_lines(more)
    if any(s.ready for s in spikes):
        lines.append(jobs)
    lines += _healer_lines(rep, fights, spikes, healers, whole, ends, busiest, more)
    lines.append(ids_line({name: i for s in spikes for name, i, _ in s.abilities}))
    return "\n".join(lines)


def _averages(f: dict, taken: Taken | None, end: float) -> tuple[list[tuple[float, float, float, float]], float]:
    """Raid damage taken until `end` (taken.py) for each SPIKE_STEP_MS from the pull's start, averaged over the
    SPIKE_AVG_MS centred on it, fewer at the pull's start and at `end`: ((its start, the average's start and end, per
    second) for each, the averages' median)."""
    if taken is None:
        return [], 0.0
    t0, half = f["startTime"], SPIKE_AVG_MS / 2
    avg = []
    for i in range(math.ceil((end - t0) / SPIKE_STEP_MS)):
        t = t0 + i * SPIKE_STEP_MS
        a, b = max(t0, t + SPIKE_STEP_MS / 2 - half), min(end, t + SPIKE_STEP_MS / 2 + half)
        avg.append((t, a, b, taken.rate(a, b)))
    return avg, statistics.median(v for *_, v in avg) if avg else 0.0


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _seconds(a: float, b: float) -> int:
    """How many whole seconds `a`-`b` is, one at the least."""
    return max(round((b - a) / 1000), 1)


def _peak(f: dict, taken: Taken | None, end: float) -> tuple[str, float, float, float] | None:
    """A pull without spikes: how its damage held against SPIKE_TIMES its median, and its highest average over
    SPIKE_AVG_MS (start, end, per second); None with no damage recorded. The average is named by where its seconds
    begin, as pull_timeline names its averages of as long (named by their middle, the same seconds would read 2 s
    later here than there); at the pull's start and at `end` it has fewer seconds, and those are the seconds said."""
    avg, typical = _averages(f, taken, end)
    if not avg:
        return None
    _, a, b, peak = max(avg, key=lambda p: p[3])
    median = f"{SPIKE_TIMES}x its median of {short(typical)}/s"
    if typical <= 0:  # no damage in half its 5 s or more: nothing is measured against that median
        held = f"had a median of 0/s over {SPIKE_AVG_MS // 1000} s, against which no stretch is measured"
    elif peak >= SPIKE_TIMES * typical:
        held = f"reached {median} for under {SPIKE_MIN_MS // 1000} s at a time"  # runs too short to be one
    else:
        held = f"stayed under {median} over any {SPIKE_AVG_MS // 1000} s"
    return held, a, b, peak


def _even(f: dict, taken: Taken | None, end: float) -> str:
    """A pull without spikes in a clause: how its damage held (`_peak`) and its highest average, when and how long."""
    if (peak := _peak(f, taken, end)) is None:
        return "no raid damage taken recorded"
    held, a, b, per_s = peak
    return (f"the raid's damage taken {held}, highest {short(per_s)}/s over the {_seconds(a, b)} s from "
            f"{clock(a - f['startTime'])}")


def _spikes(f: dict, taken: Taken | None, end: float) -> list[Spike]:
    """A pull's spikes, biggest by their damage first: where raid damage taken per second, averaged over
    SPIKE_AVG_MS, is SPIKE_TIMES its median until `end` or more."""
    avg, typical = _averages(f, taken, end)
    if taken is None or typical <= 0:
        return []
    runs: list[list[float]] = []
    for t, _, _, v in avg:
        if v >= SPIKE_TIMES * typical:
            if runs and t - runs[-1][1] <= SPIKE_JOIN_MS:
                runs[-1][1] = t
            else:
                runs.append([t, t])
    kept = [s for a, b in runs if (s := Spike(f, a, min(b + SPIKE_STEP_MS, end))).end - s.start >= SPIKE_MIN_MS]
    for s in kept:
        s.taken = taken.damage(s.start, s.end)  # a spike shown in full gets its tables' sum instead (`_fill`)
    return sorted(kept, key=lambda s: -s.taken)


async def _fill(rep: Report, fights: list[dict], spikes: list[Spike], ends: dict[int, float],
                more: Sequence[Spike] = ()) -> dict[int, dict]:
    """Each spike's damage by ability, healing by player, healers' casting and cooldowns, and deaths, and of the
    spikes in `more` (a pull's smaller ones) only the cooldowns up in them, which need no table; returns each pull's
    whole Healing table (until decided), for the healing outside the spikes. Tables in one query each."""
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
    own = {(f["id"], pid): [c for c in by_fight[f["id"]] if c.get("sourceID") == pid]
           for f in fights for pid in {pid for pid, _ in cds} | {p.id for p in rep.pull_players(f).values()
                                                                if p.role == "healer"}}
    # (pull, healer) -> each healing cooldown of theirs with its casts in the pull
    presses = {(fid, pid): {name: (sp, [c["timestamp"] for c in casts if c["type"] == "cast" and rep.is_spell(c, sp)])
                            for (who, name), sp in cds.items() if who == pid} for (fid, pid), casts in own.items()}

    def uses(fid: int, pid: int, name: str, at: list[float]) -> set[tuple[int, str, float]]:
        """The uses these casts belong to: a channel logs a cast for each tick, and its first is the press."""
        first = one_per_use(presses[fid, pid][name][1])
        return {(pid, name, max((u for u in first if u <= t), default=t)) for t in at}
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
            gaps = without(casting(own[f["id"], p.id], s.end, died).gaps, pauses[f["id"]])
            mine.idle_s = sum(max(0.0, min(b, s.end) - max(a, s.start)) for a, b in gaps) / 1000
            for name, (sp, theirs) in presses[f["id"], p.id].items():
                if at := _up(theirs, sp, s.start, s.end):
                    s.cooldowns.append(f"{name} ({p.name}) at {clock(at[0] - t0)}")
                    s.up |= uses(f["id"], p.id, name, at)
                elif (p.id, name) in pressed and gone is None and _ready(theirs, sp, s.start):
                    s.ready.append(f"{name} ({p.name})")
                    mine.ready += 1
            s.healers[p.id] = mine
    for s in more:
        for p in (p for p in rep.pull_players(s.fight).values() if p.role == "healer"):
            for name, (sp, theirs) in presses[s.fight["id"], p.id].items():
                if at := _up(theirs, sp, s.start, s.end):
                    s.cooldowns.append(f"{name} ({p.name}) at {clock(at[0] - s.fight['startTime'])}")
                    s.up |= uses(s.fight["id"], p.id, name, at)
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


def _more_lines(more: list[Spike]) -> list[str]:
    """A pull's spikes beyond the SPIKES_SHOWN biggest, in a line: when, how much, and the healing cooldowns up."""
    if not more:
        return []
    t0 = more[0].fight["startTime"]
    return [f"The {'one smaller spike' if len(more) == 1 else f'{len(more)} smaller spikes'} (time, damage taken per "
            "second: the healing cooldowns up in it, by the same rule): "
            + " · ".join(f"{clock(s.start - t0)}-{clock(s.end - t0)} {short(s.taken / s.seconds)}/s: "
                         f"{', '.join(s.cooldowns) or 'none'}" for s in more)]


def _in_spike(p: Player, h: InSpike, seconds: float) -> str:
    if h.dead_s >= seconds:
        return f"{p.name} dead"
    text = f"{p.name} {short(h.healing / seconds)}/s ({_over(h.healing, h.overheal)} overheal)"
    if h.idle_s:
        text += f", no cast begun for {h.idle_s:.0f} s"
    if h.dead_s:
        text += f", dead for the last {h.dead_s:.0f} s"
    return text


def _night_lines(rep: Report, fights: list[dict], spikes: list[Spike], healers: dict[int, Player]) -> list[str]:
    """Each pull's biggest spikes in a line, then the spikes by the ability behind most of their damage."""
    lines = [f"Each pull's {NIGHT_SPIKES} biggest spikes (time, length, damage taken per second and the ability "
             "behind most of it, healing as a share of it, healing cooldowns used, deaths in it):"]
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
        lines.append(f"The spikes that came back pull after pull (starts within {SAME_SPIKE_MS // 1000} s of each "
                     "other; the abilities behind most of their damage, pulls, typical length, damage taken and "
                     "healing per second, healing cooldowns used per spike, spikes with a healing cooldown ready "
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
            f"{plural(len(group), 'spike')} in {plural(pulls, 'pull')}, "
            f"{statistics.median(s.seconds for s in group):.0f} s, "
            f"{short(statistics.median(s.taken / s.seconds for s in group))}/s taken, healing "
            f"{short(statistics.median(s.healing / s.seconds for s in group))}/s; "
            f"{sum(len(s.cooldowns) for s in group) / len(group):.1f} healing cooldowns per spike, "
            f"{sum(1 for s in group if s.ready)} with one ready and unused; "
            f"{plural(sum(len(s.deaths) for s in group), 'death')}")
    ready = Counter(r for s in spikes for r in s.ready)
    if ready:
        lines.append("Healing cooldowns most often ready and unused in a spike: "
                     + ", ".join(f"{r} {plural(n, 'time')}" for r, n in ready.most_common(5)))
    return lines


def _healer_lines(rep: Report, fights: list[dict], spikes: list[Spike], healers: dict[int, Player],
                  whole: dict[int, dict], ends: dict[int, float], busiest: str | None = None,
                  more: Sequence[Spike] = ()) -> list[str]:
    """Each healer in the spikes (or a pull's `busiest` seconds) against the rest of the time: healing per second,
    overhealing, time with no cast begun or dead, healing cooldowns used in a spike and left ready in one. With
    `more` (a pull's smaller spikes, which have no healing read) the cooldowns count those spikes too: each press
    once, whatever number of spikes it was up in."""
    shown = busiest or ("those spikes" if more else "the spikes")
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
        used = len({(name, t) for s in [*spikes, *more] for who, name, t in s.up if who == pid})
        rows.append(f"  {p.label}: {short(heal / spike_s)}/s ({_over(heal, over)} overheal) in {shown}, "
                    f"{rest} the rest of the time; {missed} in "
                    + (f"those {spike_s:.0f} s" if busiest or more else f"{spike_s:.0f} s of spikes") + "; "
                    f"{plural(used, 'healing cooldown')} in "
                    + ("them" if busiest else "a spike") + f", {sum(h.ready for _, h in mine)} left ready in "
                    + ("them" if busiest else "one"))
    if not rows:
        return []
    return [f"Each healer in {f'the {len(spikes)} biggest spikes' if more else shown} against the rest of the time "
            "(healing on a wipe is capped by the damage taken, so a healer topping a quiet stretch shows high "
            "overheal; no cast begun leaves out raid-wide stuns"
            + (f"; healing cooldowns in a spike count all {len(spikes) + len(more)} spikes, left ready the "
               f"{len(spikes)} biggest" if more else "") + "):", *rows]


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
