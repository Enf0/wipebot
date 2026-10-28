"""The night's awards on a boss (progress's `awards`): the raid's best by a few measures over its pulls, each until
its wipe was decided, and only praise: most damage and healing a pull, most kicks and dispels, the fewest deaths
before the wipe was decided. Each award with the runner-up, so a close one shows as close."""
import asyncio
from collections import Counter
from dataclasses import dataclass

from ..text import plural, short
from .base import Player, Report
from .tables import meter

SURVIVOR_SHARE = 0.5  # the survivor award goes to a player in at least this share of the pulls


@dataclass
class Award:
    title: str  # "Top damage"
    unit: str  # what the value counts: "damage a pull", "kicks"
    winner: Player
    value: float
    runner_up: tuple[Player, float] | None
    pulls: tuple[int, int] | None = None  # the survivor's: the pulls the winner and the runner-up were in


def _amount(unit: str, v: float) -> str:
    return short(v) if unit.endswith("a pull") else f"{v:g}"


def said(a: Award, runner_up: bool = False, brief: bool = False) -> str:
    """The winner's value as the awards state it, or the runner-up's: "18.70M damage a pull", "3 deaths in 30 pulls";
    `brief`, the number alone ("18.70M", "3 deaths")."""
    v = a.runner_up[1] if runner_up and a.runner_up else a.value
    if a.pulls and brief:
        return plural(int(v), 'death')
    if a.pulls:
        return f"{plural(int(v), 'death')} in {plural(a.pulls[1 if runner_up else 0], 'pull')}"
    return _amount(a.unit, v) if brief else f"{_amount(a.unit, v)} {a.unit}"


async def awards(rep: Report, boss: str | None) -> tuple[list[dict], list[Award]]:
    """The boss's pulls (those still going left out) and their awards."""
    fights = rep.ended(rep.by_boss[rep.boss(boss)])
    numbers = [rep.pull_no(f) for f in fights]
    cuts = await rep.decided_cuts(fights)
    damage, healing, kicks, dispels = await asyncio.gather(
        *(meter(rep, t, numbers, boss) for t in ("DamageDone", "Healing", "Interrupts", "Dispels")))
    present: Counter = Counter(p.id for f in fights for p in rep.pull_players(f).values())
    roles: dict[int, Counter] = {}
    for f in fights:
        for p in rep.pull_players(f).values():
            roles.setdefault(p.id, Counter())[p.role] += 1
    def role(p: Player) -> str:
        return roles[p.id].most_common(1)[0][0] if p.id in roles else p.role
    out: list[Award] = []

    def best(title: str, unit: str, m, keep, per_pull: bool) -> None:
        if not m:
            return
        rows = []
        for b in m.bars:
            p = rep.find_player(b.name)
            if p and keep(p) and present[p.id]:
                rows.append((p, b.value / present[p.id] if per_pull else b.value))
        rows.sort(key=lambda r: -r[1])
        if rows and rows[0][1] > 0:
            out.append(Award(title, unit, rows[0][0], rows[0][1], rows[1] if len(rows) > 1 else None))

    best("Top damage", "damage a pull", damage, lambda p: role(p) == "dps", True)
    best("Top healing", "healing a pull", healing, lambda p: role(p) == "healer", True)
    best("Most kicks", "kicks", kicks, lambda p: True, False)
    best("Most dispels", "dispels", dispels, lambda p: True, False)
    died: Counter = Counter()
    for f in fights:
        cut = cuts.get(f["id"], f["endTime"])
        for pid in {d["targetID"] for d in await rep.deaths(f) if d["timestamp"] <= cut}:
            died[pid] += 1
    regulars = [p for p in rep.players.values() if present[p.id] >= SURVIVOR_SHARE * len(fights)]
    ranked = sorted(regulars, key=lambda p: (died[p.id] / present[p.id], -present[p.id], p.name))
    if ranked:
        first, second = ranked[0], ranked[1] if len(ranked) > 1 else None
        out.append(Award("Survivor", "deaths", first, died[first.id], (second, died[second.id]) if second else None,
                         (present[first.id], present[second.id] if second else 0)))
    return fights, out


def awards_text(rep: Report, fights: list[dict], found: list[Award]) -> str:
    lines = [f"Awards for the night on {fights[0]['name']}, {plural(len(fights), 'pull')}, each until its wipe was "
             "decided; damage and healing per pull a player was in, the survivor among those in at least half the "
             "pulls (fewest deaths before the wipe was decided, per pull), only praise:"]
    for a in found:
        nxt = f" (next: {a.runner_up[0].name} {said(a, True)})" if a.runner_up else ""
        lines.append(f"  {a.title}: {a.winner.name} {said(a)}{nxt}")
    if not found:
        lines.append("  none: no player stood out by these measures")
    return "\n".join(lines)
