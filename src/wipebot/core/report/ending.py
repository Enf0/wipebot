"""How a pull ended, from the log alone (`Ending`): one ability killing many at once, a buff the boss kept to the end,
half the raid dead, deaths one after another, or a reset. What each mechanic is, the guide says; pull_summary writes
it out (`ending_text`)."""
import math
from dataclasses import dataclass

from ..pulls import Decided, is_reset
from ..text import clock
from .base import Report, alive_at, half_dead

MASS_DEATH_MS = 2000  # deaths to one ability this close together are one hit killing many
MASS_DEATH_SHARE = 5  # a fifth of the raid (at least 3) dying in one hit is a raid-wiping mechanic


@dataclass(frozen=True)
class MassDeath:
    """One ability (or one moment with no killing blow logged) killing many players at once."""
    at_ms: float  # the first of those deaths, from the pull's start
    ability: str  # "" when the log names no killing blow
    ids: tuple[int, ...]  # its ability ids, as the deaths logged them
    killed: int
    span_s: int  # from the first of those deaths to the last, at least 1


@dataclass(frozen=True)
class KeptBuff:
    """A buff the boss gained before the wipe was decided and kept to the end, such as an enrage."""
    at_ms: float
    name: str
    alive: int  # players alive when the boss gained it


@dataclass(frozen=True)
class HalfDead:
    """Half the raid dead, what decided the wipe when no enrage did."""
    at_ms: float


Moment = MassDeath | KeptBuff | HalfDead


@dataclass(frozen=True)
class Ending:
    """How a pull ended. `kind`: "live" (still going), "reset" (the boss untouched), "moments" (one or more of the
    moments below, earliest first), "cascade" (deaths one after another until half the raid was dead) or "called_off"
    (none of these: the raid stopped)."""
    kind: str
    raid: int
    end_ms: float  # the pull's length (logged so far, when live)
    moments: tuple[Moment, ...] = ()
    first_death_ms: float | None = None  # a cascade's first death
    half_dead_ms: float | None = None  # a cascade's end: half the raid dead
    alive_at_end: int | None = None  # called off: players still alive


def ending(rep: Report, f: dict, deaths: list[dict], backs: dict[int, list[float]], raid: int,
           buffs: list[tuple[str, float]], decided: Decided | None) -> Ending | None:
    """How a wipe ended, from the log alone, earliest first: one ability killing many at once, a buff the boss kept to
    the end (`buffs`: the ones that can decide a wipe, Report.enrages), half the raid dead, or deaths one after
    another. None for a kill."""
    if f["kill"]:
        return None
    t0, length = f["startTime"], f["endTime"] - f["startTime"]
    if f.get("inProgress"):
        return Ending("live", raid, length)
    if is_reset(f):
        return Ending("reset", raid, length)
    until = t0 + decided.at_ms if decided else f["endTime"]
    before = [d for d in deaths if d["timestamp"] <= until]
    half = half_dead(deaths, backs, raid) if decided and not decided.enrage else None
    moments: list[Moment] = [*_mass_deaths(rep, before, t0, raid)]
    moments += [KeptBuff(gained, name, alive_at(deaths, backs, raid, t0 + gained)) for name, gained in buffs
                if t0 + gained <= until]  # gained once the wipe was decided: fallout too
    if not moments and half is not None:  # half the raid dead, with no hit killing many and no enrage: a cascade
        return Ending("cascade", raid, length, first_death_ms=before[0]["timestamp"] - t0, half_dead_ms=half - t0)
    if not moments:
        return Ending("called_off", raid, length, alive_at_end=alive_at(deaths, backs, raid, f["endTime"]))
    if half is not None:
        moments.append(HalfDead(half - t0))
    return Ending("moments", raid, length, tuple(sorted(moments, key=lambda m: (m.at_ms, moment_text(m, raid)))))


def _mass_deaths(rep: Report, deaths: list[dict], t0: float, raid: int) -> list[MassDeath]:
    """Each run of deaths to one ability within MASS_DEATH_MS that killed a fifth of the raid (at least 3)."""
    found, mass, i = [], max(3, math.ceil(raid / MASS_DEATH_SHARE)), 0
    while i < len(deaths):
        name = rep.ability(deaths[i].get("killingAbilityGameID"))  # by name: an ability logs under several ids
        hit = [d for d in deaths[i:] if d["timestamp"] - deaths[i]["timestamp"] <= MASS_DEATH_MS
               and rep.ability(d.get("killingAbilityGameID")) == name]
        if len(hit) >= mass:
            ids = tuple(sorted({gid for d in hit if (gid := d.get("killingAbilityGameID"))}))
            span = max(1, round((hit[-1]["timestamp"] - hit[0]["timestamp"]) / 1000))
            found.append(MassDeath(hit[0]["timestamp"] - t0, name if ids else "", ids, len(hit), span))
            i = deaths.index(hit[-1]) + 1
        else:
            i += 1
    return found


def moment_text(m: Moment, raid: int) -> str:
    match m:
        case MassDeath(at_ms=at, ability=name, ids=ids, killed=n, span_s=span):
            within = f"within {span} s at {clock(at)}"
            if ids:
                return (f"{name} [{', '.join(map(str, ids))}] killed {n} {within} (one ability killing many at once: "
                        "find which mechanic failed)")
            return (f"{n} died at once, no killing blow logged, {within} (one moment killing many: a fall, a knockback "
                    "or a hit the log doesn't name; find which mechanic failed)")
        case KeptBuff(at_ms=at, name=name, alive=alive):
            return (f"the boss gained {name} at {clock(at)} and kept it to the end, {alive} of {raid} alive (look it "
                    "up: an enrage makes every later death fallout)")
        case HalfDead(at_ms=at):
            return f"half the raid was dead at {clock(at)}"


def ending_text(e: Ending | None) -> str | None:
    """The ending as pull_summary's "Ended by" line; None for a kill."""
    if e is None:
        return None
    match e.kind:
        case "live":
            return f"Ended by: not yet, the pull is still going ({clock(e.end_ms)} logged so far)."
        case "reset":
            return "Ended by: a reset (the boss untouched)."
        case "cascade":
            return (f"Ended by: deaths from {clock(e.first_death_ms or 0)} until half the raid was dead at "
                    f"{clock(e.half_dead_ms or 0)}, no single hit killing many: start from the first death and follow "
                    "what each left short.")
        case "called_off":
            return f"Ended by: called off at {clock(e.end_ms)} with {e.alive_at_end} of {e.raid} alive."
    return ("Ended by (from the log, earliest first): " + "; then ".join(moment_text(m, e.raid) for m in e.moments)
            + ".")
