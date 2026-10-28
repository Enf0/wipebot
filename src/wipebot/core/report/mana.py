"""Healers' mana and the druids' Innervates (healing's `mana`): each healer's mana until the wipe was decided, how
low it went and from when it was low, and each Innervate cast (on whom, at what mana) or ready while a healer was
low, on a pull or every pull on a boss."""
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError, spaced
from ..text import clock, plural
from ..wcl import WCLError
from .base import FactError, Player, PullRef, Report, unavailable
from .coaching import resource_points

LOW = 30  # a healer under this % of their mana is low
LOW_GAP_MS = 15000  # back above it for less than this is still low (a tick of regen, a mana potion's moment)
INNERVATE = "Innervate"


@dataclass
class HealerMana:
    p: Player
    points: list[tuple[float, float]]  # (ms from the pull's start, % mana), until the cut
    dead_ms: float | None  # their death before the cut

    @property
    def lowest(self) -> tuple[float, float] | None:
        return min(self.points, key=lambda x: (x[1], x[0])) if self.points else None

    def low(self) -> list[tuple[float, float]]:
        """The stretches they were alive and under LOW, gaps under LOW_GAP_MS closed: (from, to) ms from the pull's
        start."""
        out: list[list[float]] = []
        end = self.dead_ms if self.dead_ms is not None else (self.points[-1][0] if self.points else 0)
        for (t, v), (t2, _) in zip(self.points, [*self.points[1:], (end, 0)], strict=True):
            if t >= end or v >= LOW:
                continue
            if out and out[-1][1] >= t - LOW_GAP_MS:
                out[-1][1] = min(t2, end)
            else:
                out.append([t, min(t2, end)])
        return [(a, b) for a, b in out if b > a]


@dataclass
class Innervate:
    druid: Player
    casts: list[tuple[float, str, float | None]]  # (ms from the pull's start, on whom, their mana then)
    ready_low: list[tuple[float, float, list[str]]]  # (from, to) ms it was ready while these healers were low


@dataclass
class PullMana:
    f: dict
    until_ms: float  # the cut: when the wipe was decided, else the pull's length
    decided: bool
    healers: list[HealerMana]
    innervates: list[Innervate]
    unread: str = ""  # why the mana couldn't be read


@dataclass
class Mana:
    pulls: list[PullMana]
    cooldown_s: float | None  # Innervate's, from Wowhead
    spell_id: int | None
    never: list[str] = field(default_factory=list)  # druids in the pulls who never cast it in the report


def _at(points: list[tuple[float, float]], ms: float) -> float | None:
    """Mana at a moment: the last point at or before it."""
    before = [v for t, v in points if t <= ms]
    return before[-1] if before else None


def _spans_ready(casts: list[float], cooldown_ms: float, end: float) -> list[tuple[float, float]]:
    """When a cooldown was ready, ready at the pull (as cooldown_use assumes): before the first cast and from each
    cast's cooldown on to the next."""
    out, free = [], 0.0
    for c in casts:
        if c > free:
            out.append((free, c))
        free = c + cooldown_ms
    if end > free:
        out.append((free, end))
    return out


def _overlap(ready: list[tuple[float, float]], low: dict[str, list[tuple[float, float]]]
             ) -> list[tuple[float, float, list[str]]]:
    """The stretches a cooldown was ready while any healer was low, merged, with who was low."""
    cuts = sorted({t for a, b in ready for t in (a, b)}
                  | {t for spans in low.values() for a, b in spans for t in (a, b)})
    out: list[tuple[float, float, list[str]]] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        mid = (a + b) / 2
        if not any(x <= mid < y for x, y in ready):
            continue
        who = sorted(n for n, spans in low.items() if any(x <= mid < y for x, y in spans))
        if not who:
            continue
        if out and out[-1][1] == a:
            out[-1] = (out[-1][0], b, sorted(set(out[-1][2]) | set(who)))
        else:
            out.append((a, b, who))
    return out


async def _graphs(rep: Report, fights: list[dict]) -> dict[int, dict]:
    """Every player's mana over each pull, one graph a pull, in one query."""
    async def fetch(missing: list[int]) -> dict[int, dict]:
        return await rep.wcl.graphs(rep.code, [f for f in fights if f["id"] in missing], "Resources", abilityID=100)
    return await rep.keep_many({f["id"]: (("graph", f["id"], "Resources", "all", 0), f["id"]) for f in fights},
                               fetch, FOREVER)


async def _innervate(rep: Report) -> tuple[int | None, float | None]:
    """Innervate's id, as a druid's casts in the report log it (none cast it: None), and its cooldown from Wowhead."""
    every = [f["id"] for f in rep.fights]
    druids = [p for p in rep.players.values() if p.class_name == "Druid"]
    tables = await asyncio.gather(*(rep.table(every, "Casts", sourceID=p.id) for p in druids))
    spell_id = next((e["guid"] for t in tables for e in t.get("entries") or []
                     if e.get("name", "").lower() == INNERVATE.lower() and e.get("guid")), None)
    if spell_id is None:
        return None, None
    try:
        return spell_id, await knowledge.spell_cooldown(spell_id)
    except KnowledgeError:
        return spell_id, None


async def mana(rep: Report, pull: PullRef = None, boss: str | None = None, all_pulls: bool = False) -> Mana:
    pulls = rep.ended(rep.by_boss[rep.boss(boss)]) if all_pulls else [rep.fight(pull, boss)]
    cuts, (spell_id, cooldown_s) = await asyncio.gather(rep.decided_cuts(pulls), _innervate(rep))
    try:
        graphs = await _graphs(rep, pulls)
    except WCLError as e:
        raise FactError(unavailable("Mana over the pulls", e)) from e
    every = [f["id"] for f in rep.fights]
    casts = [c for c in (await rep.events(every, "Casts", abilityID=spell_id) if spell_id else [])
             if c["type"] == "cast"]
    by_druid: dict[int, list[dict]] = defaultdict(list)
    for c in casts:
        by_druid[c["sourceID"]].append(c)
    out, druids_seen = [], {}
    for f in pulls:
        players = rep.pull_players(f)
        until = cuts.get(f["id"], f["endTime"]) - f["startTime"]
        deaths = {}
        for d in await rep.deaths(f):
            deaths.setdefault(d["targetID"], d["timestamp"] - f["startTime"])
        healers = []
        for p in sorted((q for q in players.values() if q.role == "healer"), key=lambda q: q.name):
            pts = [(t - f["startTime"], v) for t, v in resource_points(graphs.get(f["id"]) or {}, p.id, f["endTime"])
                   if t - f["startTime"] <= until]
            dead = deaths.get(p.id)
            healers.append(HealerMana(p, pts, dead if dead is not None and dead < until else None))
        low = {h.p.name: h.low() for h in healers}
        mana_of = {h.p.id: h.points for h in healers}
        innervates = []
        for druid in sorted((q for q in players.values() if q.class_name == "Druid"), key=lambda q: q.name):
            druids_seen[druid.id] = druid
            if druid.id not in by_druid:
                continue
            here = [c for c in by_druid[druid.id] if c["fight"] == f["id"]]
            at = [c["timestamp"] - f["startTime"] for c in here if c["timestamp"] - f["startTime"] <= until]
            dead = deaths.get(druid.id)
            end = min(until, dead) if dead is not None else until
            ready = _spans_ready(at, (cooldown_s or 0) * 1000, end) if cooldown_s else []
            innervates.append(Innervate(druid, [
                (c["timestamp"] - f["startTime"], players[c["targetID"]].name if c.get("targetID") in players else "?",
                 _at(mana_of.get(c.get("targetID"), []), c["timestamp"] - f["startTime"]))
                for c in here if c["timestamp"] - f["startTime"] <= until], _overlap(ready, low)))
        out.append(PullMana(f, until, f["id"] in cuts, healers, innervates,
                            "" if any(h.points for h in healers) else "no mana in the log"))
    never = sorted(d.name for i, d in druids_seen.items() if i not in by_druid)
    return Mana(out, cooldown_s, spell_id, never)


def _pct(v: float | None) -> str:
    return "?" if v is None else f"{v:.0f}%"


def _spans(spans: list[tuple[float, float]]) -> str:
    return ", ".join(f"{clock(a)}-{clock(b)}" for a, b in spans)


def _pull_lines(rep: Report, pm: PullMana) -> list[str]:
    lines = []
    for h in pm.healers:
        lo, low = h.lowest, h.low()
        last = h.points[-1][1] if h.points else None
        lines.append(f"  {h.p.name} ({spaced(h.p.spec)} {spaced(h.p.class_name)}): "
                     + (f"lowest {_pct(lo[1])} at {clock(lo[0])}" if lo else "no mana in the log")
                     + (f"; under {LOW}% {_spans(low)}" if low else "")
                     + (f"; {_pct(last)} at {clock(pm.until_ms)}" if last is not None else "")
                     + (f"; died {clock(h.dead_ms)}" if h.dead_ms is not None else ""))
    for i in pm.innervates:
        cast = "; ".join(f"cast {clock(t)} on {who} (at {_pct(v)})" for t, who, v in i.casts) or "not cast"
        ready = "; ".join(f"ready {clock(a)}-{clock(b)} while {', '.join(who)} under {LOW}%"
                          for a, b, who in i.ready_low)
        lines.append(f"  {INNERVATE}, {i.druid.name} ({spaced(i.druid.spec)} {spaced(i.druid.class_name)}): {cast}"
                     + (f"; {ready}" if ready else ""))
    return lines


def mana_text(rep: Report, m: Mana) -> str:
    end = "until the wipe was decided" if any(p.decided for p in m.pulls) else "the whole pull"
    cd = (f"{INNERVATE}: Wowhead's cooldown {m.cooldown_s / 60:g} min (talents aside), taken as ready at the pull"
          if m.cooldown_s else f"{INNERVATE}: no druid cast it in this report" if not m.spell_id
          else f"{INNERVATE}'s cooldown unavailable (Wowhead), so when it was ready isn't counted")
    if len(m.pulls) == 1:
        pm = m.pulls[0]
        lines = [f"Healers' mana on {rep.label(pm.f)} ({rep.url(pm.f)}), {end}"
                 + (f" ({clock(pm.until_ms)})" if pm.decided else "") + f"; low is under {LOW}%. {cd}."]
        lines += _pull_lines(rep, pm)
    else:
        lines = [f"Healers' mana on all {plural(len(m.pulls), m.pulls[0].f['name'] + ' pull')}, each {end}; "
                 f"low is under {LOW}%. {cd}."]
        by_healer: dict[str, list[tuple[int, HealerMana]]] = defaultdict(list)
        for pm in m.pulls:
            for h in pm.healers:
                by_healer[h.p.name].append((rep.pull_no(pm.f), h))
        for name, rows in sorted(by_healer.items()):
            low = [n for n, h in rows if h.low()]
            lo = min(((n, h.lowest) for n, h in rows if h.lowest), key=lambda x: x[1][1], default=None)
            lines.append(f"  {name}: low on {len(low)} of {plural(len(rows), 'pull')}"
                         + (f" ({', '.join(map(str, low))})" if low else "")
                         + (f"; lowest {_pct(lo[1][1])} on pull {lo[0]} at {clock(lo[1][0])}" if lo else ""))
        by_druid: dict[str, list[tuple[int, Innervate]]] = defaultdict(list)
        for pm in m.pulls:
            for i in pm.innervates:
                by_druid[i.druid.name].append((rep.pull_no(pm.f), i))
        for name, rows in sorted(by_druid.items()):
            on = Counter(who for _, i in rows for _, who, _ in i.casts)
            idle = [n for n, i in rows if i.ready_low]
            lines.append(f"  {INNERVATE}, {name}: cast {plural(on.total(), 'time')} over {plural(len(rows), 'pull')}"
                         + (f" (on {', '.join(f'{who} {n}' for who, n in on.most_common())})" if on else "")
                         + (f"; ready while a healer was low on {len(idle)} ({', '.join(map(str, idle))})"
                            if idle else ""))
    if m.never:
        lines.append(f"Druids in the pulls who never cast {INNERVATE} in this report (it may not be talented): "
                     + ", ".join(m.never) + ".")
    return "\n".join(lines)
