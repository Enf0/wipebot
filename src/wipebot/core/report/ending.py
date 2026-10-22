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
WAVE_MS = 6000  # deaths to one named ability this close together, in pulses, are one moment too (Stone Breaker's set)
LEAD_IN_GAP_MS = 60_000  # deaths this close one after another lead into the moment that follows them


@dataclass(frozen=True)
class MassDeath:
    """One ability (or one moment with no killing blow logged) killing many players at once."""
    at_ms: float  # the first of those deaths, from the pull's start
    ability: str  # "" when the log names no killing blow
    ids: tuple[int, ...]  # its ability ids, as the deaths logged them
    killed: int
    span_s: int  # from the first of those deaths to the last, at least 1
    waves: bool = False  # in pulses over a few seconds (WAVE_MS), not one hit


@dataclass(frozen=True)
class LeadIn:
    """The deaths that led into the first moment killing many: one after another, none more than LEAD_IN_GAP_MS from
    the next, brought-back players left out. A raid short of players then is often what lost the moment."""
    first_ms: float
    deaths: int
    tanks: int


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
    lead_in: LeadIn | None = None  # "moments" led by a moment killing many: the deaths that led into it

    @property
    def mass(self) -> int:
        """How many dying at once is a raid-wiping mechanic in this raid: a fifth of it, at least 3."""
        return max(3, math.ceil(self.raid / MASS_DEATH_SHARE))


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
        stayed = [d for d in before if not _brought_back(d, backs)] or before  # a battle res undid a death
        return Ending("cascade", raid, length, first_death_ms=stayed[0]["timestamp"] - t0, half_dead_ms=half - t0)
    if not moments:
        return Ending("called_off", raid, length, alive_at_end=alive_at(deaths, backs, raid, f["endTime"]))
    if half is not None:
        moments.append(HalfDead(half - t0))
    moments.sort(key=lambda m: (m.at_ms, moment_text(m, raid)))
    lead_in = _lead_in(rep, before, backs, t0, t0 + moments[0].at_ms) if isinstance(moments[0], MassDeath) else None
    return Ending("moments", raid, length, tuple(moments), lead_in=lead_in)


def _brought_back(death: dict, backs: dict[int, list[float]]) -> bool:
    """Whether the player was brought back after this death (a battle res, a self-res)."""
    return any(b > death["timestamp"] for b in backs.get(death["targetID"], []))


def _lead_in(rep: Report, deaths: list[dict], backs: dict[int, list[float]], t0: float, at: float) -> LeadIn | None:
    """The deaths that led into the moment at `at` (report time; the pull began at `t0`), or None for fewer than
    two."""
    chain, last = [], at
    for d in reversed([d for d in deaths if d["timestamp"] < at and not _brought_back(d, backs)]):
        if last - d["timestamp"] > LEAD_IN_GAP_MS:
            break
        chain.append(d)
        last = d["timestamp"]
    if len(chain) < 2:
        return None
    tanks = sum(p.role == "tank" for d in chain if (p := rep.players.get(rep.names.get(d["targetID"], "").lower())))
    return LeadIn(chain[-1]["timestamp"] - t0, len(chain), tanks)


def _mass_deaths(rep: Report, deaths: list[dict], t0: float, raid: int) -> list[MassDeath]:
    """Each run of deaths to one ability within MASS_DEATH_MS, or for a named ability in pulses within WAVE_MS, that
    killed a fifth of the raid (at least 3)."""
    found, mass, i = [], max(3, math.ceil(raid / MASS_DEATH_SHARE)), 0
    while i < len(deaths):
        name = rep.ability(deaths[i].get("killingAbilityGameID"))  # by name: an ability logs under several ids
        hit = [d for d in deaths[i:] if d["timestamp"] - deaths[i]["timestamp"] <= MASS_DEATH_MS
               and rep.ability(d.get("killingAbilityGameID")) == name]
        waves = False
        if len(hit) < mass and deaths[i].get("killingAbilityGameID"):  # the same ability in pulses
            hit = [d for d in deaths[i:] if d["timestamp"] - deaths[i]["timestamp"] <= WAVE_MS
                   and rep.ability(d.get("killingAbilityGameID")) == name]
            waves = True
        if len(hit) >= mass:
            ids = tuple(sorted({gid for d in hit if (gid := d.get("killingAbilityGameID"))}))
            span = max(1, round((hit[-1]["timestamp"] - hit[0]["timestamp"]) / 1000))
            found.append(MassDeath(hit[0]["timestamp"] - t0, name if ids else "", ids, len(hit), span, waves))
            i = deaths.index(hit[-1]) + 1
        else:
            i += 1
    return found


def moment_text(m: Moment, raid: int) -> str:
    match m:
        case MassDeath(at_ms=at, ability=name, ids=ids, killed=n, span_s=span, waves=True):
            return (f"{name} [{', '.join(map(str, ids))}] killed {n} in pulses within {span} s from {clock(at)} (one "
                    "ability killing many in a few seconds: find which mechanic failed)")
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
    text = "Ended by (from the log, earliest first): " + "; then ".join(moment_text(m, e.raid) for m in e.moments) + "."
    if e.lead_in:
        tanks = f", {'a tank' if e.lead_in.tanks == 1 else f'{e.lead_in.tanks} tanks'} among them" if e.lead_in.tanks \
            else ""
        text += (f" Before it, {e.lead_in.deaths} deaths one after another from {clock(e.lead_in.first_ms)}{tanks}, "
                 "none brought back: what each left short may be what lost it.")
    return text


def lead(e: Ending | None) -> str:
    """What the ending leads with, in the words a case's `cause` uses (evals/endings.py): "enrage: <buff>",
    "ability: <ability>", "moment: no killing blow", "deaths from <m:ss>", "reset", "called off", "live" or "kill"."""
    if e is None:
        return "kill"
    match e.kind:
        case "cascade":
            return f"deaths from {clock(e.first_death_ms or 0)}"
        case "called_off":
            return "called off"
        case "moments":
            if e.lead_in and e.lead_in.deaths >= e.mass:
                return f"deaths from {clock(e.lead_in.first_ms)}"
            match e.moments[0]:
                case MassDeath(ability=name):
                    return f"ability: {name}" if name else "moment: no killing blow"
                case KeptBuff(name=name):
                    return f"enrage: {name}"
                case HalfDead():
                    return "half the raid dead"
    return e.kind
