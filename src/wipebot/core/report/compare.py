"""Two pulls side by side, or a pull next to a kill (`compare_pulls`)."""
import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass

from ..cache import FOREVER
from ..pulls import KILLERS_SHOWN
from ..text import clock, fight_url, ordinal, plural, short
from ..wcl import WCLError
from .base import SAME_DEATH_MS, FactError, PullRef, Report
from .fight import boss_series, boss_tracks, tracks
from .tables import totals_by_name

PACE_POINTS = 3  # boss health this many points above the kill's at the same moment is behind its pace
PACE_STEP_MS = 10_000  # the moments the pace is compared at
TABLE_ROWS = 12  # about this many rows of the side-by-side table


@dataclass(frozen=True)
class Span:
    """A phase both pulls entered, in the order entered: Warcraft Logs' id for it (None for a fight without
    phases, the whole of it), when each pull entered it and how long each stayed, in ms from its own start, and
    which time the fight entered that phase (2 for a phase it went back to)."""
    phase: int | None
    ours: float
    kill: float
    our_length: float
    kill_length: float
    visit: int = 1


@dataclass(frozen=True)
class Moment:
    """One moment of a phase on both pulls: so far into it, which is another time of each pull when they entered
    the phase at different times."""
    phase: int | None
    into: float  # ms into the phase
    ours: float  # ms from our pull's start
    kill: float  # ms from the kill's
    visit: int = 1  # which time the fight entered the phase


def spans(f: dict, k: dict) -> list[Span]:
    """The phases to compare a pull and a kill in: each phase both entered, in the order entered, as far as they
    went through the same phases; a fight without phases is one span. A phase only one of them reached has
    nothing beside it."""
    ours = [(t["id"], t["startTime"] - f["startTime"]) for t in f.get("phaseTransitions") or []]
    kill = [(t["id"], t["startTime"] - k["startTime"]) for t in k.get("phaseTransitions") or []]
    our_end, kill_end = f["endTime"] - f["startTime"], k["endTime"] - k["startTime"]
    out = []
    entered: Counter = Counter()
    if len(ours) > 1 or len(kill) > 1:
        for i, ((phase, at), (kill_phase, kat)) in enumerate(zip(ours, kill, strict=False)):
            if phase != kill_phase:
                break
            entered[phase] += 1
            out.append(Span(phase, at, kat, (ours[i + 1][1] if i + 1 < len(ours) else our_end) - at,
                            (kill[i + 1][1] if i + 1 < len(kill) else kill_end) - kat, entered[phase]))
    return out or [Span(None, 0, 0, our_end, kill_end)]


def moments(spans: list[Span], step: int, upto: float, past_kill: bool = False) -> list[Moment]:
    """Every `step` ms into each span while both pulls were in it (with `past_kill`, while ours was), up to
    `upto` of our pull."""
    out = []
    for s in spans:
        length = s.our_length if past_kill else min(s.our_length, s.kill_length)
        out += [Moment(s.phase, into, s.ours + into, s.kill + into, s.visit)
                for into in range(step, int(length) + 1, step) if s.ours + into <= upto]
    return out


def same_boss(ours: list[dict], kill: list[dict], in_phases: list[Span]) -> tuple[dict, dict, str]:
    """A pull's and a kill's boss health and power on a fight with several bosses (fight.boss_series of each): one
    boss at a time, at every moment compared the same boss on both sides, and who that was. In each phase both
    entered, ours follows the boss the raid was on (_one_at_a_time), and the kill is read as that boss's own
    health and power at the same moment of the phase, whichever boss the kill was on then. So no row sets one
    boss beside another, and one boss's full power bar before the next one's empty bar is no reset (`resets` holds
    each boss's own). Where the kill went on after ours, it follows its own bosses by the same rule."""
    theirs = {b["game_id"]: b for b in kill}
    shared = [b for b in ours if b["game_id"] in theirs]
    mine, there = _Track(), _Track()
    names: list[str] = []
    last = None
    for s in in_phases:
        our_end, kill_end = s.ours + s.our_length, s.kill + s.kill_length
        run = _one_at_a_time(shared, s.ours, our_end, last)
        for i, (boss, since) in enumerate(run):
            ends_it = i + 1 == len(run)  # the phase's last boss: to the end of the phase on each side
            until = our_end if ends_it else run[i + 1][1]
            mine.add(boss, since, until)
            kill_since = s.kill + since - s.ours  # the same moment of the phase in the kill
            if in_kill := kill_since < kill_end:
                there.add(theirs[boss["game_id"]], kill_since, kill_end if ends_it else min(s.kill + until - s.ours,
                                                                                            kill_end))
            if boss is not last:
                names.append(boss["name"] if not names else f"then {boss['name']} from {clock(since)}" + (
                    f" (the kill {clock(kill_since)})" if in_kill and round(kill_since) != round(since) else ""))
            last = boss
    who = ", ".join(names)
    # past the phases both entered, each side by itself: nothing is compared there
    end = in_phases[-1]
    mine.alone(shared, end.ours + end.our_length, last)
    after = there.alone(kill, end.kill + end.kill_length, theirs[last["game_id"]] if last else None)
    if later := [f"{b['name']} from {clock(since)}" for b, since in after if last and b["game_id"] != last["game_id"]]:
        who += f" (the kill then {', then '.join(later)})"
    return mine.tracks(), there.tracks(), who


class _Track:
    """The health and power of the bosses one pull follows, a stretch per boss, and when a power bar was emptied."""

    def __init__(self) -> None:
        self.health: list[tuple[float, float]] = []
        self.power: list[tuple[float, float]] = []
        self.resets: list[float] = []

    def add(self, boss: dict, since: float, until: float) -> None:
        """One boss from a moment to another. Each stretch begins with the boss's own value then (its last before
        it, or its first), so that the track reads this boss from there and not the one before it."""
        for key, points in (("health", self.health), ("power", self.power)):
            own = _value(boss[key], since)
            stretch = ([(since, own)] if own is not None else []) + [(t, v) for t, v in boss[key] if since < t <= until]
            points += stretch
            if key == "power":
                self.resets += _resets(stretch)

    def alone(self, bosses: list[dict], since: float, last: dict | None) -> list[tuple[dict, float]]:
        """The rest of a pull from a moment on, by its own bosses (_one_at_a_time), and who they were."""
        run = _one_at_a_time(bosses, since, math.inf, last)
        for i, (boss, at) in enumerate(run):
            self.add(boss, at, run[i + 1][1] if i + 1 < len(run) else math.inf)
        return run

    def tracks(self) -> dict:
        return {key: points for key, points in (("health", self.health), ("power", self.power)) if points} | {
            "resets": self.resets}


def _fell(points: list[tuple[float, float]], after: float, upto: float) -> list[float]:
    """When a boss's health fell between two moments: each point lower than the one before it (a boss starts at
    100). Health that stands still or is healed is no boss the raid is on."""
    out, was = [], 100.0
    for t, v in points:
        if v < was and after < t <= upto:
            out.append(t)
        was = v
    return out


def _one_at_a_time(bosses: list[dict], since: float, until: float, last: dict | None) -> list[tuple[dict, float]]:
    """(boss, followed from when) through a stretch of a fight: the boss followed before it (`last`) if its health
    still falls, else the boss whose health fell first in the stretch (the lower game id of two within
    PACE_STEP_MS); then, from when that one's last fell, the boss whose health went on falling for longer than
    PACE_STEP_MS after it. So bosses fought side by side read as one of them throughout, not as a change of boss at
    every phase and at the last second of a wipe. Where no boss's health fell, the boss followed before."""
    out: list[tuple[dict, float]] = []
    at, left = since, {b["game_id"]: b for b in bosses}
    while left:
        fell = {g: times for g, b in left.items()
                if (times := _fell(b["health"], at, until)) and (not out or times[-1] - at > PACE_STEP_MS)}
        if not fell:
            break
        first = (last["game_id"] if not out and last and last["game_id"] in fell
                 else min(fell, key=lambda g: ((fell[g][0] - at) // PACE_STEP_MS, g)))
        out.append((left.pop(first), at))
        at = fell[first][-1]
    return out or ([(last, since)] if last else [])


def _one_boss(f: dict, rep: Report) -> dict[int, tuple[int, str]]:
    """A fight's boss units that come once, actor id to (NPC game id, name): its bosses, not the boss-type adds
    that come in numbers."""
    return {n["id"]: (n["gameID"], rep.actor(n["id"])) for n in f.get("enemyNPCs") or []
            if n["id"] in rep.bosses and (n.get("instanceCount") or 1) == 1 and n.get("gameID")}


ANOTHER_BOSS_MS = 30_000  # a kill's boss whose health is first logged this long after ours' is another boss


def _another_boss(ours: dict, kill: dict) -> bool:
    """Whether a kill's health is another boss's than ours, when each is its pull's busiest boss
    (fight.boss_tracks): on a fight whose bosses come one after another, a wipe on the first has only that one in
    its log, while the kill's busiest is a later one, whose health is first logged when it comes."""
    mine, theirs = ours.get("health"), kill.get("health")
    return bool(mine and theirs) and theirs[0][0] - mine[0][0] > ANOTHER_BOSS_MS


async def _boss_tracks(rep: Report, f: dict, k: dict, code: str, fight_id: int,
                       in_phases: list[Span]) -> tuple[dict, dict, str]:
    """The boss's health and power over our pull and over the kill, and on a fight with several bosses, which
    boss they follow (same_boss): the same one on both sides, by NPC game id, not the boss that happened to cast
    most in each. A fight has several when any of the report's pulls on it had (a wipe on the first of two has only
    that one in its log), or when the kill's busiest boss turns out to be another one than ours."""
    units = _one_boss(f, rep)
    known = {game_id for x in rep.fights if x["encounterID"] == f["encounterID"]
             for game_id, _ in _one_boss(x, rep).values()}
    theirs = {b["id"]: (b["gameID"], b["name"]) for b in k["bosses"] if b.get("gameID") in known}
    # by game id only where the kill's report has every boss of our pull
    both = bool(units) and {game_id for game_id, _ in units.values()} <= {game_id for game_id, _ in theirs.values()}
    if not (both and len({game_id for game_id, _ in theirs.values()}) >= 2):
        ours, kill = await tracks(rep, f), await rep.keep(
            ("boss tracks", code, fight_id), [], lambda: boss_tracks(rep.wcl, code, k, {b["id"] for b in k["bosses"]}),
            FOREVER)
        if not (both and _another_boss(ours, kill)):
            return ours, kill, ""
    ours, kill = await asyncio.gather(
        rep.keep(("boss series", f["id"]), [f["id"]], lambda: boss_series(rep.wcl, rep.code, f, units), FOREVER),
        # by the bosses asked for: a later pull of a log still going can bring one more
        rep.keep(("boss series", code, fight_id, *sorted(game_id for game_id, _ in theirs.values())), [],
                 lambda: boss_series(rep.wcl, code, k, theirs), FOREVER))
    return same_boss(ours, kill, in_phases)


async def compare_with_kill(rep: Report, pull: PullRef = None, boss: str | None = None,
                            kill: str | None = None) -> str:
    """A wipe next to a kill of the same boss and difficulty: phase changes, when the boss's power bar was
    reset, the pull against the kill's pace, and boss health and power at the same moments of each phase. Without
    `kill`, the kill with the closest item level among the 50 fastest."""
    f = rep.fight(pull, boss)
    if kill:
        m = re.search(r"reports/([A-Za-z0-9]+).*fight=(\d+)", kill)
        if not m:
            raise FactError("give the kill as a Warcraft Logs link with #fight=N")
        code, fight_id, why = m.group(1), int(m.group(2)), "the kill asked for"
    else:
        ranked = await rep.keep(("fastest", f["encounterID"], f.get("difficulty")), [],
                                  lambda: rep.wcl.fastest_kills(f["encounterID"], f.get("difficulty") or 5))
        if not ranked:
            return f"Warcraft Logs returned no ranked kills of {f['name']} on this difficulty: nothing to compare with."
        mine = f.get("averageItemLevel") or 0
        best = min(ranked, key=lambda r: (abs((r.get("bracketData") or 0) - mine), r.get("duration") or 0))
        code, fight_id = best["report"]["code"], best["report"]["fightID"]
        why = (f"the kill with the closest item level among the {len(ranked)} fastest "
               + (f"({plural(d, 'death')})" if isinstance(d := best.get("deaths"), int) else "(deaths unknown)"))
    try:
        k = await rep.keep(("kill", code, fight_id), [], lambda: rep.wcl.kill(code, fight_id))
    except WCLError as e:
        raise FactError(f"can't read that kill: {e}") from e
    if not k or not k.get("kill") or k.get("encounterID") != f["encounterID"]:
        raise FactError("that fight isn't a kill of the same boss")
    in_phases = spans(f, k)
    ours, theirs, followed = await _boss_tracks(rep, f, k, code, fight_id, in_phases)
    end = f["endTime"] - f["startTime"]
    lines = [f"{rep.label(f)} ({'kill' if f['kill'] else 'wipe'} at {clock(end)}, raid item level "
             f"{f.get('averageItemLevel') or 0:.1f}) next to a kill: {k.get('guild') or 'a raid'}, "
             f"{clock(k['endTime'] - k['startTime'])}, item level {k.get('averageItemLevel') or 0:.1f}, {why}. "
             f"Kill: {fight_url(code, fight_id)}"]
    lines.append(f"Phases: ours {rep.phases(f) or '-'}; kill {rep.phases(k, f['encounterID']) or '-'}")
    if followed:
        lines.append("Several bosses: the health and power below are one boss's at a time, the boss our pull was on, "
                     f"and the same boss's in the kill at that moment, whichever the kill was on: {followed}.")
    lines.append("Boss power bar reset (a drop of 50 or more): ours at "
                 + (", ".join(clock(t) for t in _reset_times(ours)) or "never") + "; kill at "
                 + (", ".join(clock(t) for t in _reset_times(theirs)) or "never"))
    phased = in_phases[0].phase is not None
    if phased:
        lines.append("By phase, in the order entered (entered at ours/kill; boss health then ours/kill):")
        for i, s in enumerate(in_phases):
            lines.append(f"  {i + 1}. {_phase(rep, f, s)}: {clock(s.ours)}/{clock(s.kill)}; health "
                         f"{_at(ours.get('health'), s.ours)}/{_at(theirs.get('health'), s.kill)}; lasted "
                         f"{clock(s.our_length)}/{clock(s.kill_length)}"
                         + (" (ours ended in it)" if s.ours + s.our_length >= end and not f["kill"] else ""))
    lines.append(await _pace(rep, f, k, code, fight_id, ours, theirs, in_phases))
    step = max(1, round(end / TABLE_ROWS / PACE_STEP_MS)) * PACE_STEP_MS
    lines.append("At the same moments of each phase, counted from its start, while both pulls were in it (time of "
                 "ours/kill; boss health % ours/kill; boss power ours/kill):" if phased else
                 "At the same moments (boss health % ours/kill; boss power ours/kill):")
    for m in moments(in_phases, step, end, past_kill=not phased):
        row = [f"health {_at(ours.get('health'), m.ours)}/{_at(theirs.get('health'), m.kill)}"]
        if ours.get("power") or theirs.get("power"):
            row.append(f"power {_at(ours.get('power'), m.ours)}/{_at(theirs.get('power'), m.kill)}")
        when = f"{_when(rep, f, m)} ({clock(m.ours)}/{clock(m.kill)})" if phased else clock(m.ours)
        lines.append(f"  {when} " + "; ".join(row))
    lines.append("A kill this fast is better geared and may use another strategy: compare mechanic timings "
                 "(power resets, phases) more than raw health.")
    return "\n".join(lines)


def _phase(rep: Report, f: dict, at: Span | Moment) -> str:
    """'P2', or 'P1 (2nd time)' for a phase the fight went back to, as the post names it."""
    return rep.phase(f, at.phase or 0) + (f" ({ordinal(at.visit)} time)" if at.visit > 1 else "")


def _when(rep: Report, f: dict, m: Moment) -> str:
    """A moment as the comparison names it: its time, or on a fight with phases 'P2 +0:30', so far into the phase
    ('P1 (2nd time) +0:30' in a phase the fight went back to, which is no moment of its first time there)."""
    return clock(m.ours) if m.phase is None else f"{_phase(rep, f, m)} +{clock(m.into)}"


async def _pace(rep: Report, f: dict, k: dict, code: str, fight_id: int, ours: dict, theirs: dict,
                in_phases: list[Span]) -> str:
    """The pull against the kill's pace, until our wipe was decided or the kill ended: boss health at the same
    moments (of each phase, on a fight with phases), since when ours was behind, the enrage if the boss gained one,
    and deaths so far on each side."""
    decided = await rep.decided_at(f)
    our_end = decided.at_ms if decided else f["endTime"] - f["startTime"]
    kill_end = k["endTime"] - k["startTime"]
    phased = in_phases[0].phase is not None
    compared = [m for m in moments(in_phases, PACE_STEP_MS, our_end)
                if _value(ours.get("health"), m.ours) is not None and _value(theirs.get("health"), m.kill) is not None]
    if not compared:
        return "Pace against the kill: boss health not logged."
    behind = [(_value(ours.get("health"), m.ours) or 0) - (_value(theirs.get("health"), m.kill) or 0)
              for m in compared]
    when = [_when(rep, f, m) for m in compared]
    until = (f"until our wipe was decided at {clock(our_end)} ({decided.why})" if decided and (phased or
                                                                                               our_end <= kill_end)
             else f"until the kill ended at {clock(kill_end)}" if kill_end <= our_end and not phased
             else f"until our pull ended at {clock(our_end)}")
    text = ("Pace against the kill (boss health at the same moment of each phase, counted from its start, while both "
            f"were in it, {until}): " if phased else
            f"Pace against the kill (boss health at the same moment, {until}): ")
    since = next((i for i in range(len(behind)) if all(b >= PACE_POINTS for b in behind[i:])), None)
    if since is None and all(b < PACE_POINTS for b in behind):
        text += f"kept it (within {PACE_POINTS} points or ahead)"
    elif since is not None:
        text += f"behind from {when[since]} ({behind[since]:.0f} points)"
    else:  # behind for a while, not to the end
        since = next(i for i, b in enumerate(behind) if b >= PACE_POINTS)
        back = next(i for i, b in enumerate(behind) if i > since and b < PACE_POINTS)
        text += f"behind from {when[since]} ({behind[since]:.0f} points), no longer by {when[back]}"
    last = compared[-1]
    text += (f", {_standing(behind[-1])} at {when[-1]} (ours {_value(ours.get('health'), last.ours):.0f}%, the "
             f"kill's {_value(theirs.get('health'), last.kill):.0f}%)")
    for name, gained in await rep.end_buffs(f):
        text += (f"; the boss gained {name} at {clock(gained)} with ours at "
                 f"{_value(ours.get('health'), gained) or 0:.0f}%")
        if not phased:  # the same time of the kill is another phase of it once the phases began at other times
            kill_then = _value(theirs.get("health"), gained)
            text += ", the kill " + ("over by then" if gained >= kill_end else f"at {kill_then or 0:.0f}% then")
    kill_deaths = await rep.keep(("kill deaths", code, fight_id), [], lambda: rep.wcl.events(code, fight_id, "Deaths"),
                                 FOREVER)
    ours_deaths = await rep.deaths(f)
    counts = [f"by {when[i]} ours {sum(d['timestamp'] - f['startTime'] <= compared[i].ours for d in ours_deaths)}, "
              f"the kill {_deaths_by(kill_deaths, k['startTime'] + compared[i].kill)}"
              for i in dict.fromkeys(i for i in (since, len(compared) - 1) if i is not None)]
    return text + ". Deaths " + "; ".join(counts) + "."


def _standing(points: float) -> str:
    """Where a pull stood against the kill, from our boss health minus the kill's at one moment: '12 points
    behind', '5 points ahead', or 'level with it' within a point."""
    if not (n := round(abs(points))):
        return "level with it"
    return f"{plural(n, 'point')} {'behind' if points > 0 else 'ahead'}"


def _deaths_by(deaths: list[dict], at: float) -> int:
    """Deaths up to a moment (report ms), one listed twice milliseconds apart counted once."""
    last: dict[int, float] = {}
    n = 0
    for e in sorted((e for e in deaths if e["timestamp"] <= at), key=lambda e: e["timestamp"]):
        n += e["timestamp"] - last.get(e.get("targetID", -1), -math.inf) > SAME_DEATH_MS
        last[e.get("targetID", -1)] = e["timestamp"]
    return n


def _value(points: list[tuple[float, float]] | None, t: float) -> float | None:
    """A track's value at a moment (its last point before it, else its first), or None."""
    before = [v for at, v in points or [] if at <= t] or [v for _, v in (points or [])[:1]]
    return before[-1] if before else None


def _reset_times(track: dict) -> list[float]:
    """When the boss's power bar was emptied over a pull: of several bosses followed one at a time, each one's own
    (same_boss)."""
    return track["resets"] if "resets" in track else _resets(track.get("power"))


def _resets(points: list[tuple[float, float]] | None) -> list[float]:
    """Times a power bar dropped by 50 or more: when a mechanic emptied it."""
    points = points or []
    return [t for (_, before), (t, v) in zip(points, points[1:], strict=False) if before - v >= 50]


def _at(points: list[tuple[float, float]] | None, t: float) -> str:
    """A track's value at a moment (its last point before it), or '-'."""
    return f"{v:.0f}" if (v := _value(points, t)) is not None else "-"


async def compare_pulls(rep: Report, pull_a: PullRef, pull_b: PullRef, boss: str | None = None) -> str:
    """Two pulls side by side: posts, killing abilities before and after the wipe was decided, most damage taken."""
    out = []
    for n in (pull_a, pull_b):
        f = rep.fight(n, boss)
        decided = await rep.decided_at(f)
        cut = f["startTime"] + decided.at_ms if decided else math.inf
        deaths = await rep.deaths(f)
        killers = [Counter(rep.ability(d.get("killingAbilityGameID"))
                           for d in deaths if (d["timestamp"] <= cut) == before) for before in (True, False)]
        until, note = await rep.until_decided(f, False)
        t = await rep.table(f["id"], "DamageTaken", hostilityType="Friendlies", viewBy="Ability", **until)
        top = totals_by_name(t, "DamageTaken")[:8]
        # the post's "Killed by" line names a wipe's killers until it was decided, all of them up to KILLERS_SHOWN
        posted = not f["kill"] and bool(deaths) and len(killers[0]) <= KILLERS_SHOWN
        named = [", ".join(f"{a} ×{c}" for a, c in k.most_common()) or "none" for k in killers]
        out.append("\n".join([
            await rep.summary(f),
            *([] if posted else [(f"All killers until the wipe was decided ({decided.why} at "
                                  f"{clock(decided.at_ms)}): " if decided else "All killers: ") + named[0]]),
            *([f"All killers after the wipe was decided (fallout): {named[1]}"] if decided else []),
            f"Most damage taken{note}: " + ", ".join(f"{name} {short(total)}" for name, total in top),
        ]))
    return "\n\n".join(out)
