"""Where players and bosses stood during a pull, and the moments a mechanic happened.

Positions come with events read with `includeResources`, in hundredths of a yard, x east, y north. A player's come
with the damage they take and their casts, a boss's with the hits it takes. Between two samples a position is
interpolated; with no sample close by, it isn't known.

A mechanic is an ability, by name (a mechanic often has several ids of one name). Its moments are when it happened,
read from the log alone: a debuff on players when it ends (or is applied), a boss's cast, or its hits on players.
"""
import bisect
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal

from ..text import clock
from .base import Report

POSITION_GAP_MS = 2_500  # a position is known with a sample this close: players log one every second or two in combat
REZ_GAP_MS = 1_000  # a position logged this long after a death means they were brought back: a corpse logs none
MOMENT_GAP_MS = 2_000  # a mechanic's events this close together are one occurrence: one cast's lands within a second
TICK_MS = 300  # a hit and the end of the debuff that set it off are logged this close together (one server batch)
MOVED_WINDOW_MS = 3_000  # last_moves: how far a player moved over this long before their death

Event = Literal["ends", "applied", "cast", "hit"]
Sample = tuple[float, float, float]  # (report ms, x, y)


def distance_yd(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) / 100


def off_facing(a: tuple[float, float], unit: tuple[float, float], facing: float) -> float:
    """How many degrees `a` is off the way `unit` faces: 0 straight in front, 180 straight behind."""
    turn = math.atan2(a[1] - unit[1], a[0] - unit[0]) - facing
    return abs(math.degrees((turn + math.pi) % (2 * math.pi) - math.pi))


def at(samples: list[Sample], t: float) -> tuple[float, float] | None:
    """A position at time t from samples sorted by time: between the samples on each side, or the closest one."""
    i = bisect.bisect_right(samples, t, key=lambda s: s[0])
    before = samples[i - 1] if i else None
    after = samples[i] if i < len(samples) else None
    if before and after and after[0] - before[0] <= 2 * POSITION_GAP_MS:
        k = (t - before[0]) / (after[0] - before[0])
        return before[1] + k * (after[1] - before[1]), before[2] + k * (after[2] - before[2])
    near = min((s for s in (before, after) if s), key=lambda s: abs(s[0] - t), default=None)
    return (near[1], near[2]) if near and abs(near[0] - t) <= POSITION_GAP_MS else None


def where_fell(samples: list[Sample], death: float) -> tuple[float, float] | None:
    i = bisect.bisect_right(samples, death, key=lambda s: s[0])
    return (samples[i - 1][1], samples[i - 1][2]) if i else None


def dead_spans(samples: list[Sample], deaths: list[float], end: float) -> list[tuple[float, float]]:
    """When a player was dead: from each death until their next logged position (a battle res), or `end`."""
    spans: list[tuple[float, float]] = []
    for d in sorted(deaths):
        i = bisect.bisect_right(samples, d + REZ_GAP_MS, key=lambda s: s[0])
        if not spans or d >= spans[-1][1]:
            spans.append((d, samples[i][0] if i < len(samples) else end))
    return spans


def dead_since(spans: list[tuple[float, float]], t: float) -> float | None:
    """The death `t` falls after, while dead; None while alive."""
    return next((a for a, b in spans if a <= t < b), None)


def angle(facing: float) -> float:
    """The log's facing as radians from +x, counter-clockwise: it is -100 times that."""
    return -facing / 100


@dataclass
class Positions:
    """Players' and bosses' logged positions in (part of) a pull, in time order, and the map they were on."""
    samples: dict[int, list[Sample]]  # actor id -> samples
    facing: dict[int, list[tuple[float, float]]]  # boss id -> (report ms, radians); a boss faces whom it hits
    on_map: list[tuple[float, int]]  # (report ms, map id)
    deaths: dict[int, list[float]] = field(default_factory=dict)  # player id -> when they died
    end: float = math.inf  # the pull's end

    def map_id(self, near: float | None = None) -> int | None:
        """The map most positions were on, near `near` or over everything read."""
        seen = Counter(m for t, m in self.on_map if near is None or abs(t - near) <= POSITION_GAP_MS)
        return seen.most_common(1)[0][0] if seen else None

    def dead(self, pid: int, t: float) -> float | None:
        """When a player died, if they were dead at `t`."""
        return dead_since(dead_spans(self.samples.get(pid, []), self.deaths.get(pid, []), self.end), t)

    def where(self, pid: int, t: float) -> tuple[tuple[float, float] | None, bool]:
        """(position, dead) at `t`: the dead where they fell."""
        own = self.samples.get(pid, [])
        if (death := self.dead(pid, t)) is not None:
            return where_fell(own, death), True
        return at(own, t), False

    def facing_at(self, boss: int, t: float) -> float | None:
        near = [s for s in self.facing.get(boss, []) if abs(s[0] - t) <= POSITION_GAP_MS]
        return min(near, key=lambda s: abs(s[0] - t))[1] if near else None


def bosses(rep: Report, f: dict) -> list[int]:
    """The bosses of this pull (a council has several)."""
    return [n["id"] for n in f.get("enemyNPCs") or [] if n["id"] in rep.bosses] or sorted(rep.bosses)


def gather(rep: Report, f: dict, events: list[dict], deaths: list[dict]) -> Positions:
    """Positions from events read with `includeResources`: a player's on the hits they take and their casts
    (`resourceActor` 1 is the source's position, 2 the target's), a boss's on the hits it takes."""
    boss_ids = set(bosses(rep, f))
    samples: dict[int, list[Sample]] = defaultdict(list)
    facing: dict[int, list[tuple[float, float]]] = defaultdict(list)
    on_map = []
    for e in events:
        if "x" not in e:
            continue
        who = e.get("sourceID") if e.get("resourceActor") == 1 else e.get("targetID")
        if isinstance(who, int) and (who in rep.names or who in boss_ids):
            samples[who].append((e["timestamp"], e["x"], e["y"]))
            if who in boss_ids and "facing" in e:
                facing[who].append((e["timestamp"], angle(e["facing"])))
            if who in rep.names and e.get("mapID"):
                on_map.append((e["timestamp"], e["mapID"]))
    for found in (samples, facing):
        for v in found.values():
            v.sort()
    died: dict[int, list[float]] = defaultdict(list)
    for d in deaths:
        died[d["targetID"]].append(d["timestamp"])
    return Positions(dict(samples), dict(facing), on_map, dict(died), f["endTime"])


READS = (("DamageTaken", {"hostilityType": "Friendlies"}), ("Casts", {}))  # where players' positions are


async def pull_positions(rep: Report, f: dict, boss_near: float | None = None, boss_all: bool = False
                         ) -> Positions:
    """Every player's position logged in a pull; the bosses' around report time `boss_near`, or with `boss_all`
    over the whole pull (from the hits they took)."""
    events = [e for dt, kw in READS for e in await rep.events(f["id"], dt, includeResources=True, **kw)]
    if boss_all:
        for b in bosses(rep, f):
            events += await rep.events(f["id"], "DamageDone", includeResources=True, targetID=b)
    elif boss_near is not None:
        events += await rep.events(f["id"], "DamageDone", includeResources=True,
                                   startTime=boss_near - POSITION_GAP_MS, endTime=boss_near + POSITION_GAP_MS)
    return gather(rep, f, events, await rep.deaths(f))


async def window_positions(rep: Report, windows: list[tuple[dict, float, float]], boss: bool = False
                           ) -> list[Positions]:
    """The positions logged in each (pull, from, to) window (report ms), all read in a few queries."""
    items = [(f["id"], {"startTime": a, "endTime": b}) for f, a, b in windows]
    reads = [await rep.events_for(items, dt, includeResources=True, **kw) for dt, kw in READS]
    if boss:
        reads.append(await rep.events_for(items, "DamageDone", includeResources=True))
    await rep.all_deaths(list({f["id"]: f for f, _, _ in windows}.values()))
    return [gather(rep, f, [e for r in reads for e in r[i]], await rep.deaths(f))
            for i, (f, _, _) in enumerate(windows)]


async def last_moves(rep: Report, f: dict, pid: int, death: float) -> str | None:
    """Where a player was last logged before a death with no killing blow, against the rest of the raid then: how far
    they had moved in the MOVED_WINDOW_MS before, and how far from the middle of the others (with how far 90% of the
    others stood from it). None without positions."""
    pos = await pull_positions(rep, f)
    own = [x for x in pos.samples.get(pid, []) if x[0] <= death]
    if not own:
        return None
    last_t, x, y = own[-1]
    before = at(own, last_t - MOVED_WINDOW_MS) or (own[0][1], own[0][2])
    others = [o for k, v in pos.samples.items() if k != pid and k in rep.names and (o := at(v, last_t))]
    if len(others) < 3:
        return None
    mid = (sorted(o[0] for o in others)[len(others) // 2], sorted(o[1] for o in others)[len(others) // 2])
    spread = sorted(distance_yd(o, mid) for o in others)[int(0.9 * (len(others) - 1))]
    return (f"Last logged position at {clock(last_t - f['startTime'])}: {distance_yd((x, y), mid):.0f} yd from the "
            f"middle of the rest of the raid (90% of them within {spread:.0f} yd of it), after moving "
            f"{distance_yd((x, y), before):.0f} yd in the {MOVED_WINDOW_MS // 1000} s before. A long, fast move "
            "ending far from everyone fits being thrown or knocked off (look for a knockback just before); close to "
            "the raid, an instant kill the log doesn't name.")


# ---------- a mechanic's moments ----------

@dataclass
class Moment:
    f: dict
    at: float  # report ms: the middle of the occurrence
    on: dict[int, float]  # player id -> their own time: as theirs ended or began, as it hit them
    carried: dict[int, tuple[float, float]] = field(default_factory=dict)  # a debuff: player id -> (from, to)
    hits: list[tuple[float, int]] = field(default_factory=list)  # a debuff: hits of its name on those without it


@dataclass
class Mechanic:
    """When an ability happened on each pull."""
    name: str
    ids: list[int]
    event: Event | None  # None: it never happened
    moments: dict[int, list[Moment]]  # fight id -> its moments
    logged: dict[str, int]  # how often each event was logged, to say what else could be asked for

    def all(self) -> list[Moment]:
        return [m for ms in self.moments.values() for m in ms]

    def described(self) -> str:
        what = {"ends": "running out on players", "applied": "being put on players", "cast": "cast by the boss",
                "hit": "hitting players"}
        ref = f"{self.name} [{self.ids[0]}]"
        return f"{ref} {what[self.event]}" if self.event else ref


def bunch(times: list[tuple[float, int]]) -> list[list[tuple[float, int]]]:
    """(time, player) pairs grouped where they come within MOMENT_GAP_MS of each other."""
    groups: list[list[tuple[float, int]]] = []
    for t in sorted(times):
        if groups and t[0] - groups[-1][-1][0] <= MOMENT_GAP_MS:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


Stints = dict[int, list[tuple[float, float]]]  # player id -> their spans with a debuff, each id of the name its own


def _stints(rep: Report, f: dict, events: list[dict]) -> Stints:
    spans: Stints = defaultdict(list)
    began: dict[tuple[int, int], float] = {}
    for e in sorted(events, key=lambda e: e["timestamp"]):
        who = e.get("targetID")
        if not isinstance(who, int) or who not in rep.names:
            continue
        if e.get("type") == "applydebuff":
            began[(who, e["abilityGameID"])] = e["timestamp"]
        elif e.get("type") == "removedebuff":
            spans[who].append((began.pop((who, e["abilityGameID"]), f["startTime"]), e["timestamp"]))
    for (who, _), t in began.items():
        spans[who].append((t, f["endTime"]))
    return {who: sorted(ss) for who, ss in spans.items()}


def _carrying(stints: Stints, p: int, t: float) -> bool:
    """Whether a player had the debuff since before `t` (its own damage lands on them as it ends, too)."""
    return any(a < t - TICK_MS and t <= b + TICK_MS for a, b in stints.get(p, []))


def _bursts(hits: list[list[dict]], stints: list[Stints]) -> set[int]:
    """The ids of the name whose hits mostly land as another player's debuff ends: what it sets off as it runs out.
    Their hits count on a player who has it too (one about to burst themselves)."""
    near, total = Counter(), Counter()
    for hs, st in zip(hits, stints, strict=True):
        ends = sorted((b, q) for q, ss in st.items() for _, b in ss)
        for e in hs:
            t, i = e["timestamp"], e["abilityGameID"]
            total[i] += 1
            lo = bisect.bisect_left(ends, (t - TICK_MS, -1))
            near[i] += any(q != e["targetID"] for b, q in ends[lo:] if b <= t + TICK_MS)
    return {i for i, n in total.items() if 2 * near[i] > total[i]}


async def mechanic(rep: Report, fights: list[dict], ability: str, event: Event | None = None) -> Mechanic:
    """Each pull's moments of an ability. By default a debuff on players as it runs out, else the boss's casts of it,
    else its hits on players; `event` picks one."""
    ids = rep.ability_ids(ability)
    name = rep.ability(ids[0])
    items = [(f["id"], {"filterExpression": f"ability.id in ({', '.join(map(str, ids))})"}) for f in fights]
    debuffs = await rep.events_for(items, "Debuffs", hostilityType="Friendlies")
    casts = await rep.events_for(items, "Casts", hostilityType="Enemies")
    taken = await rep.events_for(items, "DamageTaken", hostilityType="Friendlies")
    stints = [_stints(rep, f, d) for f, d in zip(fights, debuffs, strict=True)]
    cast_times = [[(e["timestamp"], 0) for e in c if e.get("type") == "cast"] for c in casts]
    hits = [[e for e in t if e.get("type") in ("damage", "absorbed") and e.get("targetID") in rep.names]
            for t in taken]
    logged = {"ends": sum(len(v) for s in stints for v in s.values()), "cast": sum(map(len, cast_times)),
              "hit": sum(map(len, hits))}
    chosen = event or next((e for e in ("ends", "cast", "hit") if logged[e]), None)
    burst = _bursts(hits, stints)
    moments = {f["id"]: _moments(f, chosen, st, cs, hs, burst) if chosen else []
               for f, st, cs, hs in zip(fights, stints, cast_times, hits, strict=True)}
    return Mechanic(name, ids, chosen if any(moments.values()) else None, moments, logged)


def _moments(f: dict, event: Event, stints: Stints, casts: list[tuple[float, int]], hits: list[dict],
             burst: set[int]) -> list[Moment]:
    if event in ("ends", "applied"):
        out = []
        # one occurrence: the debuffs put on players together (one cast), each player's ids of the name as one span
        for group in bunch([(a, p) for p, ss in stints.items() for a, _ in ss]):
            carried: dict[int, tuple[float, float]] = {}
            for a, p in group:
                b = max(b for s, b in stints[p] if s == a)
                carried[p] = (min(a, carried.get(p, (a, b))[0]), max(b, carried.get(p, (a, b))[1]))
            on = {p: span[1 if event == "ends" else 0] for p, span in carried.items()}
            m = Moment(f, sorted(on.values())[len(on) // 2], on, carried)
            m.hits = [(e["timestamp"], e["targetID"]) for e in hits if _from_another(e, carried, stints, burst)]
            out.append(m)
        return out
    hit_times = [(e["timestamp"], e["targetID"]) for e in hits]
    if event == "cast":
        out = []
        for group in bunch(casts):
            a, b = group[0][0], group[-1][0] + MOMENT_GAP_MS
            out.append(Moment(f, a, {p: t for t, p in sorted(hit_times) if a <= t <= b}))
        return out
    return [Moment(f, g[0][0], {p: t for t, p in g}) for g in bunch(hit_times)]


def _from_another(e: dict, carried: dict[int, tuple[float, float]], stints: Stints, burst: set[int]) -> bool:
    """A hit that came from another player's debuff of this moment: on someone without it while they had it, or a
    burst as theirs ended."""
    t, p = e["timestamp"], e["targetID"]
    return any(q != p and a <= t <= b + TICK_MS and (
        not _carrying(stints, p, t) or e["abilityGameID"] in burst and abs(b - t) <= TICK_MS)
        for q, (a, b) in carried.items())


@dataclass
class Facts:
    """What happened at one moment, with positions."""
    moment: Moment
    pos: Positions  # read around it
    bursts: list[tuple[int, int, float | None]]  # (whose, who it hit, yards apart), one per pair: from another's
    closest: tuple[int, int, float] | None  # the two who had it standing closest, each at their own time
    at_end: list[tuple[int, int]] = field(default_factory=list)  # (whose, whom): those that burst as theirs ended

    def merged(self) -> list[tuple[int, int, float | None]]:
        """One per (whose, whom): an area that ticks on a neighbour hits them many times."""
        return list({(s, d): (s, d, yd) for s, d, yd in reversed(self.bursts)}.values())[::-1]


def facts(m: Moment, pos: Positions) -> Facts:
    """Whose debuff each hit came from (the one whose end was logged last before it, else the nearest player who had
    it), and the two who had it standing closest."""
    bursts, at_end = [], []
    for t, p in m.hits:
        here = at(pos.samples.get(p, []), t)
        sources = [q for q, (a, b) in m.carried.items() if q != p and a <= t <= b + TICK_MS]
        apart = {q: distance_yd(here, there) if here and (there := at(pos.samples.get(q, []), t)) else None
                 for q in sources}
        if ended := [q for q in sources if abs(m.carried[q][1] - t) <= TICK_MS]:  # the end logged last before it
            src = min(ended, key=lambda q: (m.carried[q][1] > t, abs(t - m.carried[q][1])))
            at_end += [(src, p)] if (src, p) not in at_end else []
        else:
            src = min(sources, key=lambda q: (d if (d := apart[q]) is not None else math.inf))
        bursts.append((src, p, apart[src]))
    stood = {p: q for p, t in m.on.items() if (q := at(pos.samples.get(p, []), t))}
    pairs = [(distance_yd(stood[a], stood[b]), a, b) for a in stood for b in stood if a < b]
    closest = min(pairs) if pairs and m.carried else None
    return Facts(m, pos, bursts, (closest[1], closest[2], closest[0]) if closest else None, at_end)


def window(m: Moment) -> tuple[dict, float, float]:
    """The part of the pull a moment's positions are read from."""
    times = [m.at, *m.on.values(), *(t for t, _ in m.hits)]
    return m.f, min(times) - POSITION_GAP_MS, max(times) + POSITION_GAP_MS
