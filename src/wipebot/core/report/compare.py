"""Two pulls side by side, or a pull next to a kill (`compare_pulls`)."""
import math
import re
from collections import Counter

from ..cache import FOREVER
from ..text import clock, fight_url, plural, short
from ..wcl import WCLError
from .base import SAME_DEATH_MS, FactError, Report
from .fight import boss_tracks, tracks

PACE_POINTS = 3  # boss health this many points above the kill's at the same moment is behind its pace
PACE_STEP_MS = 10_000  # the moments the pace is compared at
TABLE_ROWS = 12  # about this many rows of the side-by-side table


async def compare_with_kill(rep: Report, pull: int | None = None, boss: str | None = None,
                            kill: str | None = None) -> str:
    """A wipe next to a kill of the same boss and difficulty: phase changes, when the boss's power bar was
    reset, the pull against the kill's pace, and boss health and power at the same moments. Without `kill`, the
    kill with the closest item level among the 50 fastest."""
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
    kill_bosses = {b["id"] for b in k["bosses"]}
    ours, theirs = await tracks(rep, f), await rep.keep(
        ("tracks", code, fight_id), [], lambda: boss_tracks(rep.wcl, code, k, kill_bosses), FOREVER)
    end = f["endTime"] - f["startTime"]
    lines = [f"{rep.label(f)} ({'kill' if f['kill'] else 'wipe'} at {clock(end)}, raid item level "
             f"{f.get('averageItemLevel') or 0:.1f}) next to a kill: {k.get('guild') or 'a raid'}, "
             f"{clock(k['endTime'] - k['startTime'])}, item level {k.get('averageItemLevel') or 0:.1f}, {why}. "
             f"Kill: {fight_url(code, fight_id)}"]
    lines.append(f"Phases: ours {rep.phases(f) or '-'}; kill {rep.phases(k, f['encounterID']) or '-'}")
    lines.append("Boss power bar reset (a drop of 50 or more): ours at "
                 + (", ".join(clock(t) for t in _resets(ours.get('power'))) or "never") + "; kill at "
                 + (", ".join(clock(t) for t in _resets(theirs.get("power"))) or "never"))
    ours_ph = [(t["id"], t["startTime"] - f["startTime"]) for t in f.get("phaseTransitions") or []]
    kill_ph = [(t["id"], t["startTime"] - k["startTime"]) for t in k.get("phaseTransitions") or []]
    if len(ours_ph) > 1 or len(kill_ph) > 1:
        lines.append("By phase, in the order entered (entered at ours/kill; boss health then ours/kill):")
        ours_end, kill_end = f["endTime"] - f["startTime"], k["endTime"] - k["startTime"]
        for i, ((ph, at), (_, kat)) in enumerate(zip(ours_ph, kill_ph, strict=False)):
            took = (ours_ph[i + 1][1] if i + 1 < len(ours_ph) else ours_end) - at
            kill_took = (kill_ph[i + 1][1] if i + 1 < len(kill_ph) else kill_end) - kat
            lines.append(f"  {i + 1}. P{ph}: {clock(at)}/{clock(kat)}; health {_at(ours.get('health'), at)}"
                         f"/{_at(theirs.get('health'), kat)}; lasted {clock(took)}/{clock(kill_took)}"
                         + (" (ours ended in it)" if i + 1 == len(ours_ph) and not f["kill"] else ""))
        lines.append("When a phase starts at another time, the rows below compare different phases after it.")
    lines.append(await _pace(rep, f, k, code, fight_id, ours, theirs))
    step = max(1, round(end / TABLE_ROWS / PACE_STEP_MS)) * PACE_STEP_MS
    lines.append("At the same moments (boss health % ours/kill; boss power ours/kill):")
    for t in range(step, int(end) + 1, step):
        row = [f"health {_at(ours.get('health'), t)}/{_at(theirs.get('health'), t)}"]
        if ours.get("power") or theirs.get("power"):
            row.append(f"power {_at(ours.get('power'), t)}/{_at(theirs.get('power'), t)}")
        lines.append(f"  {clock(t)} " + "; ".join(row))
    lines.append("A kill this fast is better geared and may use another strategy: compare mechanic timings "
                 "(power resets, phases) more than raw health.")
    return "\n".join(lines)


async def _pace(rep: Report, f: dict, k: dict, code: str, fight_id: int, ours: dict, theirs: dict) -> str:
    """The pull against the kill's pace, until our wipe was decided or the kill ended: boss health at the same
    moments, since when ours was behind, the enrage if the boss gained one, and deaths so far on each side."""
    decided = await rep.decided_at(f)
    our_end = decided.at_ms if decided else f["endTime"] - f["startTime"]
    kill_end = k["endTime"] - k["startTime"]
    upto = min(our_end, kill_end)
    moments = [t for t in range(PACE_STEP_MS, int(upto) + 1, PACE_STEP_MS)
               if _value(ours.get("health"), t) is not None and _value(theirs.get("health"), t) is not None]
    if not moments:
        return "Pace against the kill: boss health not logged."
    behind = {t: (_value(ours.get("health"), t) or 0) - (_value(theirs.get("health"), t) or 0) for t in moments}
    last = moments[-1]
    until = (f"until our wipe was decided at {clock(our_end)} ({decided.why})" if decided and our_end <= kill_end
             else f"until the kill ended at {clock(kill_end)}" if kill_end <= our_end
             else f"until our pull ended at {clock(our_end)}")
    text = f"Pace against the kill (boss health at the same moment, {until}): "
    since = next((t for t in moments if all(behind[u] >= PACE_POINTS for u in moments if u >= t)), None)
    if since is None and all(b < PACE_POINTS for b in behind.values()):
        text += f"kept it (within {PACE_POINTS} points or ahead)"
    else:
        since = since or next(t for t in moments if behind[t] >= PACE_POINTS)
        text += f"behind from {clock(since)} ({behind[since]:.0f} points), {behind[last]:.0f} points behind at"
    text += (f" {clock(last)} (ours {_value(ours.get('health'), last):.0f}%, the kill's "
             f"{_value(theirs.get('health'), last):.0f}%)")
    for name, gained in await rep.end_buffs(f):
        kill_then = _value(theirs.get("health"), gained)
        text += (f"; the boss gained {name} at {clock(gained)} with ours at "
                 f"{_value(ours.get('health'), gained) or 0:.0f}%, the kill "
                 + ("over by then" if gained >= kill_end else f"at {kill_then or 0:.0f}% then"))
    kill_deaths = await rep.keep(("kill deaths", code, fight_id), [], lambda: rep.wcl.events(code, fight_id, "Deaths"),
                                 FOREVER)
    ours_deaths = await rep.deaths(f)
    counts = [f"by {clock(at)} ours {sum(d['timestamp'] - f['startTime'] <= at for d in ours_deaths)}, the kill "
              f"{_deaths_by(kill_deaths, k['startTime'] + at)}" for at in dict.fromkeys(
                  t for t in (since, last) if t is not None)]
    return text + ". Deaths " + "; ".join(counts) + "."


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


async def compare_pulls(rep: Report, pull_a: int | None, pull_b: int, boss: str | None = None) -> str:
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
        top = sorted(t["entries"], key=lambda e: -e.get("total", 0))[:8]
        out.append("\n".join([
            await rep.summary(f),
            (f"All killers until the wipe was decided ({decided.why} at {clock(decided.at_ms)}): " if decided
             else "All killers: ") + (", ".join(f"{a} x{c}" for a, c in killers[0].most_common()) or "none"),
            *(["All killers after that (fallout): "
               + (", ".join(f"{a} x{c}" for a, c in killers[1].most_common()) or "none")] if decided else []),
            f"Most damage taken{note}: " + ", ".join(f"{e['name']} {short(e['total'])}" for e in top),
        ]))
    return "\n\n".join(out)
