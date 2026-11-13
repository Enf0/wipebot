"""A pull's battle res: the charges the raid had and who could cast one (`pull_summary`, `pull_timeline`)."""
import math
from dataclasses import dataclass

from .. import knowledge
from ..knowledge import KnowledgeError, spaced
from ..pulls import DIFFICULTIES
from ..text import clock, plural
from .base import SAME_DEATH_MS, Player, Report, dead_since, unavailable

# The game's rule in a raid encounter since patch 6.0, which no source we read publishes as data: the raid shares
# its battle res, 1 charge when the boss is pulled and 1 more every 90 minutes over the players in the raid, counted
# from the pull; a res spends its charge when it is taken, not when it is cast. It fits every pull with a res in
# the reports checked (docs/reference/wcl-api.md), and `BattleRes.fits` says when a pull breaks it.
RECHARGE_MS_PER_PLAYER = 90 * 60_000
START_CHARGES = 1


@dataclass(frozen=True)
class Res:
    at_ms: float  # from the pull's start
    spell: str
    by: str
    on: str
    taken: bool  # the player showed a sign of life after it (Report._taken)
    casts: int = 1  # how many times it was cast on them before that, seconds apart


@dataclass(frozen=True)
class BattleRes:
    """A pull's battle res, times in ms from its start."""
    raid: int
    cast: list[Res]

    @property
    def every_ms(self) -> float:
        return RECHARGE_MS_PER_PLAYER / self.raid

    def charges(self, at_ms: float, after: bool = False) -> int:
        """The charges ready at a moment by the rule: before a res taken at that very moment, or `after` it.
        Below 0 when more were taken than the rule gives: `fits` says so."""
        spent = sum(1 for r in self.cast if r.taken and (r.at_ms <= at_ms if after else r.at_ms < at_ms))
        return START_CHARGES + math.floor(max(at_ms, 0) / self.every_ms) - spent

    def next_at(self, at_ms: float) -> float:
        """When the next charge comes after a moment."""
        return (math.floor(max(at_ms, 0) / self.every_ms) + 1) * self.every_ms

    @property
    def fits(self) -> bool:
        """Whether every res taken had a charge by the rule."""
        return all(self.charges(r.at_ms) >= 1 for r in self.cast if r.taken)


READY_SHOWN = 6  # the deaths a charge was ready at that are listed; a wipe's last seconds add many more
RES_REPEAT_MS = 15_000  # a res cast again on the same player this soon after one not taken is the same try


async def battle_res(rep: Report, f: dict) -> BattleRes:
    """A pull's battle res from the log: each one cast, by whom on whom and whether it was taken, one cast again
    and again on a player who didn't take it listed once (its first cast, or the one taken). No query beyond
    `Report.resurrects`."""
    backs = (await rep.resurrects([f]))[f["id"]]
    out: list[Res] = []
    last: dict[int, tuple[int, float]] = {}  # per target: (its place in `out`, when it was last cast)
    for e in await rep.res_casts(f):
        who, at = e.get("targetID", -1), e["timestamp"]
        taken = any(abs(t - at) <= SAME_DEATH_MS for t in backs.get(who, []))
        res = Res(at - f["startTime"], rep.ability(e["abilityGameID"]), rep.actor(rep.owner(e.get("sourceID", 0))),
                  rep.actor(who), taken)
        if who in last and at - last[who][1] <= SAME_DEATH_MS:  # Warcraft Logs lists one twice, a millisecond apart
            continue
        if who in last and not out[last[who][0]].taken and at - last[who][1] <= RES_REPEAT_MS:
            i, before = last[who][0], out[last[who][0]]
            out[i] = Res(res.at_ms if taken else before.at_ms, res.spell if taken else before.spell,
                         res.by if taken else before.by, before.on, taken, before.casts + 1)
            last[who] = (i, at)
            continue
        last[who] = (len(out), at)
        out.append(res)
    return BattleRes(await rep.raid_size(f), out)


async def res_casters(rep: Report, f: dict, res: BattleRes) -> dict[int, tuple[Player, str]]:
    """Who in a pull could cast a battle res, by player id, with the spell: the players of a class Wowhead gives
    one (knowledge.battle_res_classes), and anyone who cast one in the pull by other means (an engineer's item)."""
    classes = await knowledge.battle_res_classes()
    players = rep.pull_players(f)
    out = {p.id: (p, classes[p.class_name]) for p in players.values() if p.class_name in classes}
    by_name = {p.name: p for p in players.values()}
    for r in res.cast:
        if (p := by_name.get(r.by)) and p.id not in out:
            out[p.id] = (p, r.spell)
    return out


def _cast_text(r: Res) -> str:
    times = f", cast {r.casts} times" if r.casts > 1 else ""
    return f"{clock(r.at_ms)} {r.spell} by {r.by} on {r.on}{times}"


def res_left(res: BattleRes, at_ms: float, end_ms: float, after: bool = False) -> str:
    """'0 left, the next at 4:30': the charges at a moment and, with none, when the next comes."""
    left = res.charges(at_ms, after)
    if left > 0:
        return f"{left} left"
    nxt = res.next_at(at_ms)
    return f"0 left, the next at {clock(nxt)}" if nxt < end_ms else "0 left, none more before the pull's end"


async def battle_res_lines(rep: Report, f: dict, deaths: list[dict]) -> list[str]:
    """pull_summary's lines on battle res: the charges by the game's rule and what the pull did with them, who
    could cast one, and at each of `deaths` (the ones until the wipe was decided) how many charges were ready and
    how many of those casters were alive. None for a pull where nobody died until then and no res was cast, and
    for a fight that is no raid's, where the rule doesn't hold."""
    if f.get("difficulty") not in DIFFICULTIES:
        return []
    res = await battle_res(rep, f)
    every = await rep.deaths(f)
    if not deaths and not res.cast:
        return []
    end = f["endTime"] - f["startTime"]
    backs = (await rep.resurrects([f]))[f["id"]]
    taken, tried = [r for r in res.cast if r.taken], [r for r in res.cast if not r.taken]
    gained = math.floor(end / res.every_ms)
    rule = (f"Battle res: {START_CHARGES} charge at the pull and 1 more every {clock(res.every_ms)} (the raid's 90 "
            f"min over its {res.raid} players), {START_CHARGES + gained} in this pull's {clock(end)}, by that rule "
            "and the res in the log, which has no count of its own.")
    if not res.fits:
        return [f"{rule} More were taken than the rule gives ({'; '.join(map(_cast_text, taken))}): it doesn't fit "
                "this pull, so no count of charges is given."]
    lines = [rule]
    if taken:
        lines.append("Taken: " + "; ".join(f"{_cast_text(r)} ({res_left(res, r.at_ms, end, after=True)})"
                                           for r in taken) + ".")
    else:
        lines.append("None was taken.")
    if tried:
        lines[-1] += (" Cast but not taken (they did nothing after it; it spent no charge): "
                      + "; ".join(map(_cast_text, tried)) + ".")
    try:
        casters = await res_casters(rep, f, res)
    except KnowledgeError as e:
        return [*lines, unavailable("Who could cast one", e)]
    by_class: dict[str, list[str]] = {}
    for p, _ in sorted(casters.values(), key=lambda x: (x[0].class_name, x[0].name.lower())):
        by_class.setdefault(spaced(p.class_name), []).append(p.name)
    who = " · ".join(f"{', '.join(names)} ({cls})" for cls, names in by_class.items()) or "nobody"
    lines.append(f"Could cast one, by class (an engineer's item shows only once used): {who}.")
    rows = []
    for d in deaths:
        at = d["timestamp"]
        if res.charges(at - f["startTime"]) < 1:
            continue
        alive = sum(1 for pid in casters if dead_since(every, backs.get(pid, []), pid, at) is None)
        back = next((b for b in backs.get(d["targetID"], []) if b > at), None)
        again = next((x["timestamp"] for x in every if x["targetID"] == d["targetID"] and x["timestamp"] > at), None)
        came = (f", back at {clock(back - f['startTime'])}" if back is not None and (again is None or back < again)
                else "")
        rows.append(f"{clock(at - f['startTime'])} {rep.names[d['targetID']]} ({alive}){came}")
    if rows:
        of = "each of the" if len(rows) == len(deaths) else f"{len(rows)} of the"
        more = f" · and {len(rows) - READY_SHOWN} later" if len(rows) > READY_SHOWN else ""
        lines.append(f"A charge was ready at {of} {plural(len(deaths), 'death')} above (with how many of those who "
                     f"could cast one were alive): {' · '.join(rows[:READY_SHOWN])}{more}. Whom a charge was kept for "
                     "isn't in the log.")
    elif deaths:
        lines.append(f"No charge was ready at the {plural(len(deaths), 'death')} above.")
    return lines
