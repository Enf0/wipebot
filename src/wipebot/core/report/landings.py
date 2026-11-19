"""Enemy debuffs that land on the whole raid at once and stay (`landings`): what a failed mechanic leaves on
everyone for the rest of the pull (an add that sprays the raid as it hatches), told from the fight's own script
(`scripted`). Report.lost_at builds on them: where a wipe was lost before its log's end."""
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from ..pulls import alive_at, dead_since

LAND_MS = 2_000  # one debuff lands on everyone it reaches within this
MANY = 0.75  # of the living, at least this share got it
STAYS_MS = 15_000  # and it was still on them this long after, or at the pull's end or their death
KEPT = 0.8  # on at least this share of those it landed on (an immunity clears it from one or two)
SAME_TIME_MS = 3_000  # landing this close to the same time into the pull on a kill, or ...
SCRIPTED = 0.6  # ... on this share of the boss's pulls that lasted past it, is the fight's script
SCRIPT_PULLS = 3  # with at least this many pulls to tell by
SHORTEST_MS = 3_000  # a pull ending sooner than this after it landed can't tell whether it stays
DIED_WITH_MS = 1_500  # a debuff coming off this soon before its player's death came off with the death
FEW = 3  # it landed on at least this many
AHEAD_MS = 2_000  # it lost the pull only this far ahead of the log's own end (closer is that end's own moment)
WITH_HIT_MS = 1_000  # landing this soon before a hit killing many, or during it, it came with that hit


@dataclass(frozen=True)
class Landing:
    at_ms: float  # from the pull's start
    ability: int
    hit: int  # players it landed on
    alive: int  # players alive then


class Actors(Protocol):
    names: dict[int, str]

    def owner(self, actor_id: int) -> int: ...


def enemy_debuffs(rep: Actors, events: list[dict]) -> dict[int, list[dict]]:
    """A pull's debuffs on players from enemies (no player's, no player's pet's), by ability id, in time order."""
    out: dict[int, list[dict]] = defaultdict(list)
    for e in sorted(events, key=lambda e: e["timestamp"]):
        if e.get("targetID") in rep.names and rep.owner(e.get("sourceID", -1)) not in rep.names:
            out[e["abilityGameID"]].append(e)
    return out


def landings(rep: Actors, f: dict, events: list[dict], deaths: list[dict], backs: dict[int, list[float]],
              raid: int) -> list[Landing]:
    """Each time an enemy debuff landed on nearly every living player (at least half the raid alive) and stayed on
    them, in time order."""
    found = []
    for aid, es in enemy_debuffs(rep, events).items():
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
            stays = min(STAYS_MS, f["endTime"] - at)  # a wipe soon after: to its end
            if stays < SHORTEST_MS:  # too near the end to tell it stays: the pull's ending says the rest
                continue
            off: dict[int, float] = {}  # who it came off within that, and when
            for e in es:
                if e["type"] == "removedebuff" and e["targetID"] in hit and at < e["timestamp"] <= at + stays:
                    off.setdefault(e["targetID"], e["timestamp"])
            off = {who: t for who, t in off.items()  # dying with it on is keeping it: it comes off at the death
                   if dead_since(deaths, backs.get(who, []), who, max(t + DIED_WITH_MS, at + stays)) is None}
            if len(hit) - len(off) >= KEPT * len(hit):
                found.append(Landing(at - f["startTime"], aid, len(hit), alive))
    return sorted(found, key=lambda x: x.at_ms)


def phase_at(f: dict, at_ms: float) -> tuple[int | None, float]:
    """The phase a moment of a pull fell in (Warcraft Logs' id, None for a boss without phases) and how far into it."""
    t0, phase = f["startTime"], (None, f["startTime"])
    for t in f.get("phaseTransitions") or []:
        if t.get("startTime") is not None and t["startTime"] <= t0 + at_ms:
            phase = (t["id"], t["startTime"])
    return phase[0], t0 + at_ms - phase[1]


def scripted(landed: dict[int, list[Landing]], fights: list[dict], aid: int, f: dict,
              at_ms: float) -> bool | None:
    """Whether the debuff lands about then, into the pull or into its phase, on a kill of the boss or on most of its
    pulls that got that far: the fight's own. None when too few pulls got that far to tell."""
    phase, into = phase_at(f, at_ms)

    def got_to(g: dict, same_phase: bool) -> bool:
        if not same_phase:
            return g["endTime"] - g["startTime"] > at_ms + SAME_TIME_MS
        starts = [t["startTime"] for t in g.get("phaseTransitions") or [] if t["id"] == phase]
        return any(g["endTime"] - start > into + SAME_TIME_MS for start in starts)

    def lands(g: dict, same_phase: bool) -> bool:
        return any(x.ability == aid and (abs(phase_at(g, x.at_ms)[1] - into) <= SAME_TIME_MS
                                         and phase_at(g, x.at_ms)[0] == phase
                                         if same_phase else abs(x.at_ms - at_ms) <= SAME_TIME_MS)
                   for x in landed.get(g["id"], ()))
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
