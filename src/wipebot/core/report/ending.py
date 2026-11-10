"""How a pull ended, from the log alone (`Ending`): one ability killing many at once, a buff the boss kept to the end,
half the raid dead, deaths one after another, a reset, or, with the raid alive, a cast of the boss's or nothing the
log shows. What each mechanic is, the guide says. One reading serves every tool: pull_summary writes it out
(`ending_text`), progress and the views count and label pulls by it (`kind_of_end`), and the evals check what it
leads with (`lead`)."""
from collections import defaultdict
from dataclasses import dataclass, replace

from ..pulls import MASS_DEATH_MS, NO_KILLING_BLOW, WAVE_MS, Decided, is_reset, mass_hits, mass_of
from ..text import clock
from .base import Report, alive_at, half_dead

LEAD_IN_GAP_MS = 60_000  # deaths this close one after another lead into the moment that follows them
END_CAST_MS = 10_000  # a debuff of the boss's landing on the living this close to the end may be what ended the pull
LAND_MS = 2_000  # one cast's debuff lands on everyone it reaches within this
CAST_LEAD_MS = 5_000  # the boss's cast this soon before the debuff landed is the cast it came from
ALIVE = "no death, the raid alive"  # kind_of_end of a pull that ended with no death deciding it


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


@dataclass(frozen=True)
class EndCast:
    """What the boss did as a pull ended with most of the raid alive: a debuff of its landed on at least half of
    the living in the pull's last seconds (END_CAST_MS), as it had at no earlier moment of the pull and on no
    earlier pull that went on. The log shows that it landed and that the pull ended; what the cast is, the guide
    says."""
    at_ms: float  # when it landed
    debuff: str
    ids: tuple[int, ...]
    hit: int  # players it landed on
    alive: int  # players alive then
    cast: str = ""  # the boss's last cast before it (within CAST_LEAD_MS), "" when the log has none
    cast_ms: float = 0

    @property
    def name(self) -> str:
        """The cast to read up on: the boss's, or the debuff when the log has no cast before it."""
        return self.cast or self.debuff


Moment = MassDeath | KeptBuff | HalfDead


@dataclass(frozen=True)
class Ending:
    """How a pull ended. `kind`: "live" (still going), "reset" (the boss untouched), "moments" (one or more of the
    moments below, earliest first), "cascade" (deaths one after another until half the raid was dead), "cast" (none
    of these, and a cast of the boss's landed on the living as the pull ended: `cast`) or "alive" (none of these: the
    pull ended with the raid alive, called off or reset, which the log can't tell apart)."""
    kind: str
    raid: int
    end_ms: float  # the pull's length (logged so far, when live)
    moments: tuple[Moment, ...] = ()
    first_death_ms: float | None = None  # a cascade's first death
    half_dead_ms: float | None = None  # a cascade's end: half the raid dead
    alive_at_end: int | None = None  # "alive" and "cast": players still alive
    lead_in: LeadIn | None = None  # "moments" led by a moment killing many: the deaths that led into it
    cast: EndCast | None = None  # "cast": what the boss did as the pull ended

    @property
    def mass(self) -> int:
        """How many dying at once is a raid-wiping mechanic in this raid: a fifth of it, at least 3."""
        return mass_of(self.raid)

    @property
    def led_in(self) -> bool:
        """Whether the deaths that led into the first moment lead the ending: as many of them as a hit killing many
        takes. Every reading of the ending (its text, its label, what the evals check) leads with the same thing."""
        return bool(self.lead_in and self.lead_in.deaths >= self.mass)


async def pull_ending(rep: Report, f: dict) -> Ending | None:
    """How a pull ended (`ending`), from what the report has read or reads for it. A pull that ended with the raid
    alive is read once more, for a cast of the boss's that ended it (`_end_cast`: the enemies' debuffs and casts)."""
    deaths, backs, raid = await rep.deaths(f), (await rep.resurrects([f]))[f["id"]], await rep.raid_size(f)
    e = ending(rep, f, deaths, backs, raid, await rep.enrages(f), await rep.decided_at(f))
    if e and e.kind == "alive" and (cast := await _end_cast(rep, f, deaths, backs, raid)):
        return replace(e, kind="cast", cast=cast)
    return e


async def pull_endings(rep: Report, fights: list[dict]) -> list[Ending | None]:
    """`pull_ending` of each pull, in order. What the pulls that ended with the raid alive need read comes in one
    query per kind, not one per pull."""
    await rep.decided_cuts(fights)
    alive = [f for f in fights
             if (e := ending(rep, f, await rep.deaths(f), (await rep.resurrects([f]))[f["id"]], await rep.raid_size(f),
                             await rep.enrages(f), await rep.decided_at(f))) and e.kind == "alive"]
    for kind, side in (("Debuffs", "Friendlies"), ("Casts", "Enemies")) if alive else ():
        await rep.events_for([(f["id"], {}) for f in alive], kind, hostilityType=side)
    return [await pull_ending(rep, f) for f in fights]


def _landed_on(events: list[dict], many: int) -> list[dict] | None:
    """The first run of a debuff's applications (within LAND_MS of its first) that reached `many` players, if any."""
    for n, first in enumerate(events):
        run = [e for e in events[n:] if e["timestamp"] - first["timestamp"] <= LAND_MS]
        if len({e["targetID"] for e in run}) >= many:
            return run
    return None


async def _end_cast(rep: Report, f: dict, deaths: list[dict], backs: dict[int, list[float]], raid: int
                    ) -> EndCast | None:
    """The cast of the boss's that ended a pull no death decided (`EndCast`), if the log shows one. The pull's
    debuffs from enemies and the enemies' casts are read (as pull_timeline reads them), and, only for a debuff that
    qualifies, its applications on the boss's earlier pulls in one query."""
    t0, end = f["startTime"], f["endTime"]
    landed: dict[str, list[dict]] = defaultdict(list)
    for e in sorted(await rep.events(f["id"], "Debuffs", hostilityType="Friendlies"), key=lambda e: e["timestamp"]):
        if e["type"] == "applydebuff" and e.get("targetID") in rep.names and e.get("sourceID") not in rep.names:
            landed[rep.ability(e["abilityGameID"])].append(e)
    found = []
    for name, events in landed.items():
        late = [e for e in events if e["timestamp"] >= end - END_CAST_MS]
        alive = alive_at(deaths, backs, raid, late[0]["timestamp"]) if late else 0
        many = max(1, -(-alive // 2))  # at least half of the living
        if (run := _landed_on(late, many)) and not _landed_on([e for e in events if e["timestamp"] < run[0][
                "timestamp"]], many):
            ids = tuple(sorted({e["abilityGameID"] for e in events}))
            found.append((run[0]["timestamp"], name, ids, len({e["targetID"] for e in run}), alive, many))
    if not found:
        return None
    at, name, ids, hit, alive, many = min(found)
    earlier = [g for g in rep.by_boss[rep.boss_key(f)] if g["startTime"] < t0 and not is_reset(g)]
    if earlier:  # a debuff that landed on as many on a pull that went on is part of the fight, not its end
        only = {"filterExpression": f'type = "applydebuff" and ability.id in ({", ".join(map(str, ids))})'}
        for g, events in zip(earlier, await rep.events_for([(g["id"], only) for g in earlier], "Debuffs",
                                                           hostilityType="Friendlies"), strict=True):
            if _landed_on(sorted((e for e in events if e.get("targetID") in rep.names
                                  and e["timestamp"] < g["endTime"] - END_CAST_MS), key=lambda e: e["timestamp"]),
                          many):
                return None
    casts = [c for c in await rep.events(f["id"], "Casts", hostilityType="Enemies")
             if c["type"] == "cast" and c.get("sourceID") in rep.bosses and at - CAST_LEAD_MS <= c["timestamp"] <= at]
    cast = max(casts, key=lambda c: c["timestamp"], default=None)
    return EndCast(at - t0, name, ids, hit, alive, rep.ability(cast["abilityGameID"]) if cast else "",
                   cast["timestamp"] - t0 if cast else 0)


def ending(rep: Report, f: dict, deaths: list[dict], backs: dict[int, list[float]], raid: int,
           buffs: list[tuple[str, float]], decided: Decided | None) -> Ending | None:
    """How a wipe ended, from the log alone, earliest first: one ability killing many at once, a buff the boss kept to
    the end (`buffs`: the ones that can decide a wipe, Report.enrages), half the raid dead, or deaths one after
    another. A hit killing many is found among all the pull's deaths and counts whole when it began before the wipe
    was decided (Report.decided_at ends the moment on its last death). None for a kill."""
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
    moments: list[Moment] = [m for m in _mass_deaths(rep, deaths, t0, raid) if t0 + m.at_ms <= until]
    moments += [KeptBuff(gained, name, alive_at(deaths, backs, raid, t0 + gained)) for name, gained in buffs
                if t0 + gained <= until]  # gained once the wipe was decided: fallout too
    if not moments and half is not None:  # half the raid dead, with no hit killing many and no enrage: a cascade
        stayed = [d for d in before if not _brought_back(d, backs)] or before  # a battle res undid a death
        return Ending("cascade", raid, length, first_death_ms=stayed[0]["timestamp"] - t0, half_dead_ms=half - t0)
    if not moments:
        return Ending("alive", raid, length, alive_at_end=alive_at(deaths, backs, raid, f["endTime"]))
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
    """Each hit killing many among a pull's deaths (pulls.mass_hits), as a moment."""
    found = []
    for h in mass_hits(deaths, raid, rep.ability):
        ids = tuple(sorted({gid for d in h.deaths if (gid := d.get("killingAbilityGameID"))}))
        span = max(1, round((h.last - h.first) / 1000))
        found.append(MassDeath(h.first - t0, h.ability if ids else "", ids, len(h.deaths), span, h.waves))
    return found


def mass_death_of(e: Ending | None, at_ms: float, ability: str) -> MassDeath | None:
    """The moment killing many that a death at `at_ms` (from the pull's start) to `ability` was one of, if any: the
    hit that wiped the raid, not one player's death before it."""
    for m in e.moments if e else ():
        if (isinstance(m, MassDeath) and (m.ability or NO_KILLING_BLOW) == ability
                and 0 <= at_ms - m.at_ms <= (WAVE_MS if m.waves else MASS_DEATH_MS)):
            return m
    return None


async def mass_death(rep: Report, f: dict, death: dict) -> MassDeath | None:
    """The hit killing many (pull_summary's "Ended by") a death before the wipe was decided was one of, if any."""
    decided = await rep.decided_at(f)
    at = death["timestamp"] - f["startTime"]
    if not decided or at > decided.at_ms:
        return None
    return mass_death_of(await pull_ending(rep, f), at, rep.ability(death.get("killingAbilityGameID")))


# A death in a hit killing many isn't one player's to fix: the raid failed to stop it (a kick, a soak), and a
# defensive would at most have bought a few seconds of a pull already lost.
MASS_HIT = "one of {n} killed at once from {at}"
MASS_NOTE = MASS_HIT + ", a hit killing many: the raid's to stop, not counted as theirs"


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
            return (f"{n} died at once, no killing blow logged, {within} (one moment killing many: a fall, a "
                    "knockback, a hit the log doesn't name, or the raid jumping off after a call; find which it was)")
        case KeptBuff(at_ms=at, name=name, alive=alive):
            return (f"the boss gained {name} at {clock(at)} and kept it to the end, {alive} of {raid} alive (look it "
                    "up: an enrage makes every later death fallout)")
        case HalfDead(at_ms=at):
            return f"half the raid was dead at {clock(at)}"


def _lead_in_text(lead_in: LeadIn) -> str:
    tanks = f", {'a tank' if lead_in.tanks == 1 else f'{lead_in.tanks} tanks'} among them" if lead_in.tanks else ""
    return f"{lead_in.deaths} deaths one after another from {clock(lead_in.first_ms)}{tanks}, none brought back"


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
        case "alive":
            return (f"Ended by: no death. The pull ended at {clock(e.end_ms)} with {e.alive_at_end} of {e.raid} "
                    "alive and nothing in the log decided it: called off or reset, the log doesn't say which.")
        case "cast":
            c = e.cast
            assert c is not None
            before = round((e.end_ms - c.at_ms) / 1000)
            what = (f"the boss cast {c.cast} at {clock(c.cast_ms)} and {c.debuff}" if c.cast and c.cast != c.debuff
                    else f"the boss cast {c.cast} at {clock(c.cast_ms)} and it" if c.cast else f"the boss's {c.debuff}")
            return (f"Ended by: {what} [{', '.join(map(str, c.ids))}] landed on {c.hit} of the {c.alive} alive at "
                    f"{clock(c.at_ms)}, {before} s before the pull ended at {clock(e.end_ms)} with {e.alive_at_end} "
                    f"of {e.raid} alive. No death decided it, and it landed on that many at no earlier moment of "
                    "this pull or of the pulls before it: look the cast up, one that ends the fight is what ended "
                    "this pull.")
    moments = [moment_text(m, e.raid) for m in e.moments]
    if e.lead_in and e.led_in:  # as many as a hit killing many takes: they lead
        return ("Ended by (from the log, earliest first): " + "; then ".join(
            [f"{_lead_in_text(e.lead_in)} (what each left short may be what lost the rest)", *moments]) + ".")
    text = "Ended by (from the log, earliest first): " + "; then ".join(moments) + "."
    if e.lead_in:
        text += f" Before it, {_lead_in_text(e.lead_in)}: what each left short may be what lost it."
    return text


def lead(e: Ending | None) -> str:
    """What the ending leads with, in the words a case's `cause` uses (evals/endings.py): "enrage: <buff>",
    "ability: <ability>", "moment: no killing blow", "deaths from <m:ss>", "cast: <the boss's cast>", "reset",
    "alive" (no death decided it), "live" or "kill"."""
    if e is None:
        return "kill"
    match e.kind:
        case "cascade":
            return f"deaths from {clock(e.first_death_ms or 0)}"
        case "cast":
            return f"cast: {e.cast.name if e.cast else ''}"
        case "moments":
            if e.lead_in and e.led_in:
                return f"deaths from {clock(e.lead_in.first_ms)}"
            match e.moments[0]:
                case MassDeath(ability=name):
                    return f"ability: {name}" if name else "moment: no killing blow"
                case KeptBuff(name=name):
                    return f"enrage: {name}"
                case HalfDead():
                    return "half the raid dead"
    return e.kind


def ending_ability(e: Ending | None) -> str | None:
    """The ability, buff or cast a wipe's ending leads with (as `lead` names it), the mechanic to read up on first;
    None when no single one ended it: deaths one after another, half the raid dead, a hit with no name, the raid
    alive, or a kill."""
    if e is not None and e.kind == "cast" and e.cast:
        return e.cast.name
    if e is None or e.kind != "moments" or e.led_in:
        return None
    match e.moments[0]:
        case MassDeath(ability=name) | KeptBuff(name=name):
            return name or None
    return None


def ruled_out(e: Ending | None, kept: list[tuple[str, float]], decided: Decided | None) -> str | None:
    """What a wipe no enrage buff decided wasn't: a buff gained near the end (`kept`: every one the boss kept to the
    end, Report.end_buffs), or a damage check, unless the guide names an enrage that is a cast (the log can't tell one
    from a hit killing many). None for a kill, a reset, a pull still going, or one an enrage buff decided."""
    if e is None or e.kind not in ("moments", "cascade") or any(isinstance(m, KeptBuff) for m in e.moments):
        return None
    late = [(name, at) for name, at in kept if decided and at > decided.at_ms]
    if late:
        head = (f"Not an enrage buff: the boss gained {late[0][0]} at {clock(late[0][1])}, after the wipe was decided "
                f"at {clock(decided.at_ms) if decided else '?'}, so it's fallout.")
    else:
        head = "Not an enrage buff: no buff the boss gained near the end decided it."
    return head + " Unless the guide names a cast that ends the fight on a timer, deaths lost it, not a damage check."


def kind_of_end(e: Ending | None) -> str:
    """What ended a pull, in a few words to count the night's pulls by and to label one with: what `lead` leads
    with. The first moment of a wipe ("Mutilate killing many"), after "deaths leading into" when those lead;
    "deaths one after another"; "the boss casting <cast>"; or "a reset", "a kill", ALIVE, "still going"."""
    if e is None:
        return "a kill"
    match e.kind:
        case "cascade":
            return "deaths one after another"
        case "alive":
            return ALIVE
        case "cast":
            return f"the boss casting {e.cast.name if e.cast else ''}"
        case "reset":
            return "a reset"
        case "live":
            return "still going"
    return ("deaths leading into " if e.led_in else "") + _moment_kind(e.moments[0])


def _moment_kind(m: Moment) -> str:
    match m:
        case MassDeath(ability=""):
            return "many dying at once, no killing blow"
        case MassDeath(ability=name):  # at once or in pulses: one ability's way of ending pulls
            return f"{name} killing many"
        case KeptBuff(name=name):
            return f"the boss gaining {name} and keeping it to the end"
    return "half the raid dead"


def card_text(e: Ending | None) -> tuple[str, float | None] | None:
    """How the pull ended, in a few words for a card, and when (ms from the pull): the same reading as
    `kind_of_end`, with the count of a hit killing many. None for a kill."""
    if e is None:
        return None
    match e.kind:
        case "cascade":
            return kind_of_end(e), e.half_dead_ms
        case "cast":
            return kind_of_end(e), e.cast.at_ms if e.cast else None
        case "live" | "reset" | "alive":
            return kind_of_end(e), None
    m = e.moments[0]
    match m:
        case MassDeath(ability="", killed=n):
            text = f"{n} dying at once"
        case MassDeath(ability=name, killed=n):
            text = f"{name} killing {n}"
        case KeptBuff(name=name):
            text = f"the boss gaining {name}"
        case _:
            text = "half the raid dead"
    return ("deaths leading into " if e.led_in else "") + text, m.at_ms
