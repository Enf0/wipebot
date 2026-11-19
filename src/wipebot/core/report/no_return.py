"""Where a wipe was lost before the log's own end (`NoReturn`): the moment raid leaders call it, earlier than half the
raid dead or a hit killing many. From the log alone: an enemy debuff landing at once on nearly every living player
and staying on them, what a failed mechanic leaves on the whole raid for the rest of the pull (an add that sprays
everyone as it hatches). One that lands at the same moment on most of the boss's pulls is the fight's own script,
not a failure. (The raid down a player it can't battle res is battle_res.py's line: kills go through that too.)

pull_summary leads with it and lists what happened to a few players in the seconds before (what set it off, read
with the guide); progress gives it per pull and counts it over the night."""
from collections import defaultdict
from dataclasses import dataclass

from ..text import clock
from .base import Report

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
    """Each wipe's point of no return among `fights` (one boss's), by fight id (Report.lost_at; a wipe with none
    is left out), with what happened to a few players just before it: the hits then in one query."""
    found: dict[int, NoReturn] = {}
    for f in fights:
        if x := await rep.lost_at(f):
            found[f["id"]] = NoReturn(RaidDebuff(x.at_ms, rep.ability(x.ability), (x.ability,), x.hit, x.alive))
    lead = [f for f in fights if f["id"] in found]
    if lead:
        windows = [(f["id"], {"startTime": f["startTime"] + found[f["id"]].at_ms - BEFORE_MS,
                              "endTime": f["startTime"] + found[f["id"]].at_ms + 1}) for f in lead]
        hits = await rep.events_for(windows, "DamageTaken")
        for f, struck in zip(lead, hits, strict=True):
            n = found[f["id"]]
            at = f["startTime"] + n.at_ms
            found[f["id"]] = NoReturn(n.debuff, *_before_rows(rep, at, await rep.raid_debuffs(f), struck,
                                                                 await rep.deaths(f), await rep.raid_size(f)))
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
