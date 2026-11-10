"""Two pulls side by side, or a pull next to a kill (`compare_pulls`)."""
import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass

from ..cache import FOREVER
from ..pulls import KILLERS_SHOWN
from ..text import clock, fight_url, plural, short
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
    phases, the whole of it), and when each pull entered it and how long each stayed, in ms from its own start."""
    phase: int | None
    ours: float
    kill: float
    our_length: float
    kill_length: float


@dataclass(frozen=True)
class Moment:
    """One moment of a phase on both pulls: so far into it, which is another time of each pull when they entered
    the phase at different times."""
    phase: int | None
    into: float  # ms into the phase
    ours: float  # ms from our pull's start
    kill: float  # ms from the kill's


def spans(f: dict, k: dict) -> list[Span]:
    """The phases to compare a pull and a kill in: each phase both entered, in the order entered, as far as they
    went through the same phases; a fight without phases is one span. A phase only one of them reached has
    nothing beside it."""
    ours = [(t["id"], t["startTime"] - f["startTime"]) for t in f.get("phaseTransitions") or []]
    kill = [(t["id"], t["startTime"] - k["startTime"]) for t in k.get("phaseTransitions") or []]
    our_end, kill_end = f["endTime"] - f["startTime"], k["endTime"] - k["startTime"]
    out = []
    if len(ours) > 1 or len(kill) > 1:
        for i, ((phase, at), (kill_phase, kat)) in enumerate(zip(ours, kill, strict=False)):
            if phase != kill_phase:
                break
            out.append(Span(phase, at, kat, (ours[i + 1][1] if i + 1 < len(ours) else our_end) - at,
                            (kill[i + 1][1] if i + 1 < len(kill) else kill_end) - kat))
    return out or [Span(None, 0, 0, our_end, kill_end)]


def moments(spans: list[Span], step: int, upto: float, past_kill: bool = False) -> list[Moment]:
    """Every `step` ms into each span while both pulls were in it (with `past_kill`, while ours was), up to
    `upto` of our pull."""
    out = []
    for s in spans:
        length = s.our_length if past_kill else min(s.our_length, s.kill_length)
        out += [Moment(s.phase, into, s.ours + into, s.kill + into) for into in range(step, int(length) + 1, step)
                if s.ours + into <= upto]
    return out


def same_boss(ours: list[dict], kill: list[dict]) -> tuple[dict, dict, str]:
    """A pull's and a kill's boss health and power on a fight with several bosses (fight.boss_series of each), one
    boss at a time and the same boss on both sides, and who that was. Ours follows the boss whose health dropped
    first, then, once it was dead or gone, the one whose health dropped next; the kill follows the same bosses in
    that order, and goes on by the same rule where it got further. Two that drop together go by game id, so both
    sides take the same one."""
    mine = _one_at_a_time(ours, [])
    theirs = _one_at_a_time(kill, [b["game_id"] for b, _ in mine])
    names = []
    for i, (b, since) in enumerate(mine):
        there = next((at for kb, at in theirs if kb["game_id"] == b["game_id"]), None)
        names.append(b["name"] if not i else f"then {b['name']} from {clock(since)}"
                     + (f" (the kill {clock(there)})" if there is not None else ""))
    who = ", ".join(names)
    if later := [f"{b['name']} from {clock(since)}" for b, since in theirs[len(mine):]]:
        who += f" (the kill then {', then '.join(later)})"
    return _joined(mine), _joined(theirs), who


def _one_at_a_time(bosses: list[dict], order: list[int]) -> list[tuple[dict, float]]:
    """(boss, followed from when) through a fight: the boss whose health drops first (in `order` where it names one
    that drops at all, else the earliest, the lower game id of two together), then after its last point the next."""
    out: list[tuple[dict, float]] = []
    end, left = -math.inf, {b["game_id"]: b for b in bosses}
    while left:
        drops = {g: at for g, b in left.items()
                 if (at := next((t for t, v in b["health"] if t > end and v < 100), None)) is not None}
        if not drops:
            break
        want = order[len(out)] if len(out) < len(order) else None
        game_id = want if want in drops else min(drops, key=lambda g: (drops[g] // PACE_STEP_MS, g))
        boss = left.pop(game_id)
        out.append((boss, max(end, 0.0)))
        end = boss["health"][-1][0]
    return out


def _joined(followed: list[tuple[dict, float]]) -> dict[str, list[tuple[float, float]]]:
    """The health and power of the bosses followed, each from when it was followed to its last health point."""
    out: dict[str, list[tuple[float, float]]] = {"health": [], "power": []}
    for i, (b, since) in enumerate(followed):
        until = b["health"][-1][0]
        for key in out:
            out[key] += [(t, v) for t, v in b[key] if (t > since or not i) and t <= until]
    return {key: points for key, points in out.items() if points}


async def _boss_tracks(rep: Report, f: dict, k: dict, code: str, fight_id: int) -> tuple[dict, dict, str]:
    """The boss's health and power over our pull and over the kill, and on a fight with several bosses, which
    boss they follow (same_boss): the one both sides were on, not the boss that happened to cast most in each."""
    units = {n["id"]: (n.get("gameID"), rep.actor(n["id"])) for n in f.get("enemyNPCs") or []
             if n["id"] in rep.bosses and (n.get("instanceCount") or 1) == 1 and n.get("gameID")}
    game_ids = {game_id for game_id, _ in units.values()}
    theirs = {b["id"]: (b["gameID"], b["name"]) for b in k["bosses"] if b.get("gameID") in game_ids}
    if len(units) < 2 or len(theirs) < 2:
        return await tracks(rep, f), await rep.keep(
            ("boss tracks", code, fight_id), [], lambda: boss_tracks(rep.wcl, code, k, {b["id"] for b in k["bosses"]}),
            FOREVER), ""
    ours, kill = await asyncio.gather(
        rep.keep(("boss series", f["id"]), [f["id"]], lambda: boss_series(rep.wcl, rep.code, f, units), FOREVER),
        rep.keep(("boss series", code, fight_id), [], lambda: boss_series(rep.wcl, code, k, theirs), FOREVER))
    return same_boss(ours, kill)


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
    ours, theirs, followed = await _boss_tracks(rep, f, k, code, fight_id)
    end = f["endTime"] - f["startTime"]
    lines = [f"{rep.label(f)} ({'kill' if f['kill'] else 'wipe'} at {clock(end)}, raid item level "
             f"{f.get('averageItemLevel') or 0:.1f}) next to a kill: {k.get('guild') or 'a raid'}, "
             f"{clock(k['endTime'] - k['startTime'])}, item level {k.get('averageItemLevel') or 0:.1f}, {why}. "
             f"Kill: {fight_url(code, fight_id)}"]
    lines.append(f"Phases: ours {rep.phases(f) or '-'}; kill {rep.phases(k, f['encounterID']) or '-'}")
    if followed:
        lines.append(f"Several bosses: the health and power below are one boss's at a time, the same boss on both "
                     f"sides: {followed}.")
    lines.append("Boss power bar reset (a drop of 50 or more): ours at "
                 + (", ".join(clock(t) for t in _resets(ours.get('power'))) or "never") + "; kill at "
                 + (", ".join(clock(t) for t in _resets(theirs.get("power"))) or "never"))
    in_phases = spans(f, k)
    phased = in_phases[0].phase is not None
    if phased:
        lines.append("By phase, in the order entered (entered at ours/kill; boss health then ours/kill):")
        for i, s in enumerate(in_phases):
            lines.append(f"  {i + 1}. {rep.phase(f, s.phase or 0)}: {clock(s.ours)}/{clock(s.kill)}; health "
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


def _when(rep: Report, f: dict, m: Moment) -> str:
    """A moment as the comparison names it: its time, or on a fight with phases 'P2 +0:30', so far into the phase."""
    return clock(m.ours) if m.phase is None else f"{rep.phase(f, m.phase)} +{clock(m.into)}"


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
