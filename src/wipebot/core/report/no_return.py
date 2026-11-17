"""Where a wipe was lost before the log's own end (`NoReturn`): the moment raid leaders call it, earlier than half the
raid dead or a hit killing many. From the log alone: an enemy debuff landing at once on nearly every living player
and staying on them, what a failed mechanic leaves on the whole raid for the rest of the pull (an add that sprays
everyone as it hatches). One that lands at the same moment on most of the boss's pulls is the fight's own script,
not a failure. (The raid down a player it can't battle res is battle_res.py's line: kills go through that too.)

pull_summary leads with it and lists what happened to a few players in the seconds before (what set it off, read
with the guide); progress gives it per pull and counts it over the night."""
from collections import defaultdict
from dataclasses import dataclass

from ..pulls import is_reset
from ..text import clock
from .base import Report, alive_at, dead_since

LAND_MS = 2_000  # one debuff lands on everyone it reaches within this
MANY = 0.75  # of the living, at least this share got it
STAYS_MS = 15_000  # and it was still on them this long after, or at the pull's end or their death
KEPT = 0.9  # on at least this share of those it landed on
SAME_TIME_MS = 3_000  # landing this close to the same time into the pull on a kill, or ...
SCRIPTED = 0.6  # ... on this share of the boss's pulls that lasted past it, is the fight's script
SCRIPT_PULLS = 3  # with at least this many pulls to tell by
BEFORE_MS = 2_500  # what happened in this long before it landed: what may have set it off
FEW = 3  # what reached this many players or fewer then is about them, not the raid
BEFORE_ROWS = 8
NEAR_MS = 1_000  # what happened this close before it landed is counted over a night: what set it off is among it


@dataclass(frozen=True)
class RaidDebuff:
    at_ms: float  # from the pull's start
    name: str
    ids: tuple[int, ...]
    hit: int
    alive: int


@dataclass(frozen=True)
class NoReturn:
    debuff: RaidDebuff
    before: tuple[str, ...] = ()  # what happened to a few players before it landed
    near: tuple[str, ...] = ()  # of that, what in the NEAR_MS before, without names ("Caustic Waves hitting"), to count

    @property
    def at_ms(self) -> float:
        return self.debuff.at_ms

    @property
    def kind(self) -> str:
        """What it was, in a few words to count a night's pulls by."""
        return f"{self.debuff.name} landing on the raid"


def _enemy_debuffs(rep: Report, events: list[dict]) -> dict[int, list[dict]]:
    """A pull's debuffs on players from enemies (no player's, no player's pet's), by ability id, in time order."""
    out: dict[int, list[dict]] = defaultdict(list)
    for e in sorted(events, key=lambda e: e["timestamp"]):
        if e.get("targetID") in rep.names and rep.owner(e.get("sourceID", -1)) not in rep.names:
            out[e["abilityGameID"]].append(e)
    return out


def _landings(rep: Report, f: dict, events: list[dict], deaths: list[dict], backs: dict[int, list[float]],
              raid: int) -> list[tuple[float, int, int, int]]:
    """Each time an enemy debuff landed on nearly every living player (at least half the raid alive) and stayed on
    them: (ms into the pull, ability id, players it landed on, players alive)."""
    found = []
    for aid, es in _enemy_debuffs(rep, events).items():
        applied = [e for e in es if e["type"] == "applydebuff"]
        i = 0
        while i < len(applied):
            at = applied[i]["timestamp"]
            run = [e for e in applied[i:] if e["timestamp"] - at <= LAND_MS]
            hit = {e["targetID"] for e in run}
            alive = alive_at(deaths, backs, raid, at)
            if alive * 2 < raid or len(hit) < max(FEW, MANY * alive):
                i += 1
                continue
            i += len(run)
            if f["endTime"] - at < STAYS_MS:  # too near the end to tell it stays: the pull's ending says the rest
                continue
            off = {e["targetID"] for e in es if e["type"] == "removedebuff" and e["targetID"] in hit
                   and at < e["timestamp"] <= at + STAYS_MS}
            off = {who for who in off if dead_since(deaths, backs.get(who, []), who, at + STAYS_MS) is None}
            if len(hit) - len(off) >= KEPT * len(hit):
                found.append((at - f["startTime"], aid, len(hit), alive))
    return sorted(found)


def _phase_at(f: dict, at_ms: float) -> tuple[int | None, float]:
    """The phase a moment of a pull fell in (Warcraft Logs' id, None for a boss without phases) and how far into it."""
    t0, phase = f["startTime"], (None, f["startTime"])
    for t in f.get("phaseTransitions") or []:
        if t.get("startTime") is not None and t["startTime"] <= t0 + at_ms:
            phase = (t["id"], t["startTime"])
    return phase[0], t0 + at_ms - phase[1]


def _scripted(landed: dict[int, list[tuple[float, int, int, int]]], fights: list[dict], aid: int, f: dict,
              at_ms: float) -> bool | None:
    """Whether the debuff lands about then, into the pull or into its phase, on a kill of the boss or on most of its
    pulls that got that far: the fight's own. None when too few pulls got that far to tell."""
    phase, into = _phase_at(f, at_ms)

    def got_to(g: dict, same_phase: bool) -> bool:
        if not same_phase:
            return g["endTime"] - g["startTime"] > at_ms + SAME_TIME_MS
        starts = [t["startTime"] for t in g.get("phaseTransitions") or [] if t["id"] == phase]
        return any(g["endTime"] - start > into + SAME_TIME_MS for start in starts)

    def lands(g: dict, same_phase: bool) -> bool:
        return any(a == aid and (abs(_phase_at(g, t)[1] - into) <= SAME_TIME_MS and _phase_at(g, t)[0] == phase
                                 if same_phase else abs(t - at_ms) <= SAME_TIME_MS)
                   for t, a, _, _ in landed.get(g["id"], ()))
    told = False
    for same_phase in (False, True) if phase is not None else (False,):
        reached = [g for g in fights if got_to(g, same_phase)]
        if any(g["kill"] and lands(g, same_phase) for g in reached):
            return True
        if len(reached) >= SCRIPT_PULLS:
            told = True
            if sum(lands(g, same_phase) for g in reached) >= SCRIPTED * len(reached):
                return True
    return False if told else None


def _before_rows(rep: Report, at: float, debuffs: list[dict], hits: list[dict], deaths: list[dict],
                 raid: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What happened to a few players in the BEFORE_MS before report time `at`, earliest first: deaths, enemy
    debuffs going on or off FEW players or fewer, and hits (no ticks) of enemy abilities that reached FEW or fewer;
    then what enemy debuffs those players had on them as it began that half the raid or fewer had (what they were
    carrying). And, without names, what of it came in the NEAR_MS before, to count over a night."""
    t0 = at - BEFORE_MS
    rows: list[tuple[float, str]] = []
    named: set[int] = set()
    near: set[str] = set()

    def add(when: float, ids: set[int], text: str, what: str) -> None:
        rows.append((when, text))
        named.update(ids)
        if when >= at - NEAR_MS:
            near.add(what)
    for d in deaths:
        if t0 <= d["timestamp"] <= at:
            add(d["timestamp"], {d["targetID"]}, f"{rep.names.get(d['targetID'], '?')} died", "a death")
    for verb, kind in (("on", "applydebuff"), ("came off", "removedebuff")):
        on: dict[int, list[dict]] = defaultdict(list)
        for e in debuffs:
            if (e["type"] == kind and t0 <= e["timestamp"] <= at + 100 and e.get("targetID") in rep.names
                    and rep.owner(e.get("sourceID", -1)) not in rep.names):
                on[e["abilityGameID"]].append(e)
        for aid, es in on.items():
            if len(ids := {e["targetID"] for e in es}) <= FEW:
                add(es[0]["timestamp"], ids, f"{rep.ability(aid)} {verb} {_names(rep, ids)}",
                    f"{rep.ability(aid)} {'going on' if verb == 'on' else 'coming off'}")
    struck: dict[int, list[dict]] = defaultdict(list)
    for e in hits:
        if (t0 <= e["timestamp"] <= at and not e.get("tick") and e.get("targetID") in rep.names
                and rep.owner(e.get("sourceID", -1)) not in rep.names and e.get("abilityGameID") != 1):
            struck[e["abilityGameID"]].append(e)
    for aid, es in struck.items():
        if len(ids := {e["targetID"] for e in es}) <= FEW:
            add(es[0]["timestamp"], ids, f"{rep.ability(aid)} hit {_names(rep, ids)}", f"{rep.ability(aid)} hitting")
    rows.sort()
    out = [f"{clock_ms(t - at)} {text}" for t, text in rows[-BEFORE_ROWS:]]
    if carried := _carrying(rep, debuffs, t0, named, raid):
        out.append(carried)
    return tuple(out), tuple(sorted(near))


def _carrying(rep: Report, debuffs: list[dict], t0: float, named: set[int], raid: int) -> str | None:
    """The enemy debuffs each of `named` had on them at report time `t0` that half the raid or fewer had: what they
    were carrying."""
    on_them: dict[int, set[int]] = defaultdict(set)  # debuff -> who had it then
    for e in sorted(debuffs, key=lambda e: e["timestamp"]):
        if e["timestamp"] > t0:
            break
        if rep.owner(e.get("sourceID", -1)) in rep.names:  # a player's own (Satiated, a talent's)
            continue
        if e["type"] == "applydebuff":
            on_them[e["abilityGameID"]].add(e["targetID"])
        elif e["type"] == "removedebuff":
            on_them[e["abilityGameID"]].discard(e["targetID"])
    few = {aid: ids for aid, ids in on_them.items() if 0 < len(ids) * 2 <= raid}
    carried = [f"{rep.names[who]} {', '.join(sorted(rep.ability(aid) for aid, ids in few.items() if who in ids))}"
               for who in sorted(named, key=lambda w: rep.names.get(w, ""))
               if who in rep.names and any(who in ids for ids in few.values())]
    return "having then (debuffs half the raid or fewer had): " + "; ".join(carried) if carried else None


def _names(rep: Report, ids: set[int]) -> str:
    return ", ".join(sorted(rep.names[i] for i in ids))


def clock_ms(rel: float) -> str:
    """'-1.2 s': a time before the moment, to a tenth of a second."""
    return f"{rel / 1000:+.1f} s"


async def no_return(rep: Report, fights: list[dict]) -> dict[int, NoReturn]:
    """Each wipe's point of no return among `fights` (one boss's), by fight id; a wipe with none is left out. The
    pulls' enemy debuffs come in one query, and the hits before each debuff that landed in one more. Every pull of
    the boss in the report is read to tell the fight's script from a failure."""
    if not fights:
        return {}
    every = [g for g in rep.by_boss[rep.boss_key(fights[0])] if not is_reset(g) and not g.get("inProgress")]
    debuffs = await rep.events_for([(g["id"], {}) for g in every], "Debuffs", hostilityType="Friendlies")
    landed: dict[int, list[tuple[float, int, int, int]]] = {}
    for g, events in zip(every, debuffs, strict=True):
        landed[g["id"]] = _landings(rep, g, events, await rep.deaths(g), (await rep.resurrects([g]))[g["id"]],
                                    await rep.raid_size(g))
    by_id = dict(zip((g["id"] for g in every), debuffs, strict=True))
    wipes = [f for f in fights if not f["kill"] and f["id"] in by_id]
    found: dict[int, NoReturn] = {}
    for f in wipes:
        decided = await rep.decided_at(f)
        until = decided.at_ms if decided else None
        first = next(((t, a, n, alive) for t, a, n, alive in landed[f["id"]]
                      if (until is None or t <= until) and _scripted(landed, every, a, f, t) is False), None)
        if first:
            found[f["id"]] = NoReturn(RaidDebuff(first[0], rep.ability(first[1]), (first[1],), first[2], first[3]))
    lead = [f for f in wipes if f["id"] in found]
    if lead:
        windows = [(f["id"], {"startTime": f["startTime"] + found[f["id"]].at_ms - BEFORE_MS,
                              "endTime": f["startTime"] + found[f["id"]].at_ms + 1}) for f in lead]
        hits = await rep.events_for(windows, "DamageTaken")
        for f, struck in zip(lead, hits, strict=True):
            n = found[f["id"]]
            at = f["startTime"] + n.at_ms
            dead = await rep.deaths(f)
            found[f["id"]] = NoReturn(n.debuff, *_before_rows(rep, at, by_id[f["id"]], struck, dead,
                                                                 await rep.raid_size(f)))
    return found


async def pull_no_return(rep: Report, f: dict) -> NoReturn | None:
    return (await no_return(rep, [f])).get(f["id"])


def no_return_text(n: NoReturn | None) -> list[str]:
    """pull_summary's lines on it, which lead the summary: the moment, then what happened just before."""
    if n is None:
        return []
    d = n.debuff
    lines = [f"Lost at {clock(d.at_ms)}, before the log's own end below: {d.name} [{', '.join(map(str, d.ids))}] "
             f"landed on {d.hit} of the {d.alive} alive at once and stayed on them, at no fixed time of the boss's "
             "pulls: a failed mechanic put it on the whole raid, where raid leaders call the wipe."]
    if n.before:
        lines.append(f"Just before {d.name} landed, what reached {FEW} players or fewer (deaths, debuffs, hits, "
                     f"seconds before it; what set it off is among them, as the guide's {d.name} says): "
                     + " · ".join(n.before) + ".")
    else:
        lines.append(f"Just before {d.name} landed, nothing in the log reached only a few players: it doesn't show "
                     "what set it off.")
    return lines
