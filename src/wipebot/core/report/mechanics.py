"""Who got hit by what (`mechanic_hits`): one ability per player, or every ability's outliers."""
import itertools
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from ..pulls import attempt
from ..text import clock, parse_clock, plural, short
from .base import JOBS_NOT_LOGGED, FactError, PullRef, Report, alive_ms, dead_since
from .meter import MIN_BARS, Bar, Meter

HIT_THEN_DEATH_MS = 6_000  # a mechanic hit followed by death this soon counts as "died after"
SAME_MOMENT_MS = 1_000  # a second hit this soon after the one before landed together with it
APPLICATIONS_LISTED = 30  # debuff applications listed one by one, at most


@dataclass
class Hits:
    """One ability's hits and debuffs per player over some pulls, until each wipe was decided."""
    name: str | None = None  # as the log names the ids that hit
    ability_id: int | None = None  # the id that hit hardest
    hits: Counter = field(default_factory=Counter)
    ticks: Counter = field(default_factory=Counter)
    damage: Counter = field(default_factory=Counter)
    died_after: Counter = field(default_factory=Counter)  # deaths, of any cause, with a hit from it just before
    killed_by: Counter = field(default_factory=Counter)  # deaths where it was the killing blow
    debuffs: Counter = field(default_factory=Counter)
    # A hit while the debuff from an earlier hit is still up: how long after the hit before it, per player
    rehit_gaps: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    spans: dict[int, list[list[float]]] = field(default_factory=dict)  # per player, each [applied, removed]

    @property
    def by_debuff(self) -> bool:
        """A debuff that lands more often than the ability deals damage (a wave that only stacks a debuff): its
        applications are what tell who got caught."""
        return sum(self.debuffs.values()) > sum(self.hits.values()) + sum(self.ticks.values())


async def mechanic_hits(rep: Report, ability: str, pull: PullRef = None, boss: str | None = None,
                        all_pulls: bool = False, whole_pull: bool = False, start: str | None = None,
                        end: str | None = None, jobs: str = JOBS_NOT_LOGGED) -> str:
    fights, going, cut, window, found = await _asked(rep, ability, pull, boss, all_pulls, whole_pull, start, end)
    scope = f"all {plural(len(fights), fights[0]['name'] + ' pull')}" if all_pulls else rep.label(fights[0])
    if window:
        scope += f", from {clock(window[0])} to {clock(window[1]) if window[1] < math.inf else 'the end'} of each"
    if not found.hits and not found.ticks and not found.debuffs:
        return going + f"Nobody took damage from or got a debuff named {ability!r} on {scope}."
    name = found.name or ability
    if cut:
        scope += (" until the wipe was decided (the boss's enrage or half the raid dead"
                  + (" in each pull" if all_pulls else f", {clock(cut[fights[0]['id']] - fights[0]['startTime'])}")
                  + "; whole_pull for the rest)")
    h = found
    by_debuff = h.by_debuff
    lines = [going + f"{name} on {scope}. Per player"
             + (", most debuff applications first (it was applied more often than it dealt damage, so the "
                "applications count who it caught)" if by_debuff else "")
             + ": hits (and periodic ticks), damage incl. absorbed and ticks, "
             f"times the debuff was applied (applications over the whole {'night' if all_pulls else 'pull'}, not "
             f"stacks), deaths of any cause within {HIT_THEN_DEATH_MS // 1000} s after a hit or tick, deaths with "
             f"{name} itself as the killing blow (what it leaves behind, a spawn or a debuff that kills later, "
             "counts under that ability's name); then, if any, hits while the debuff from an earlier hit was "
             "still up and how long after the hit before:"]
    for who in sorted(set(h.hits) | set(h.ticks) | set(h.debuffs),
                      key=lambda w: (-h.debuffs[w], -h.hits[w], -h.ticks[w]) if by_debuff
                      else (-h.hits[w], -h.ticks[w], -h.debuffs[w])):
        lines.append(_player_line(rep, h, who))
    lines.append(f"Total with {name} as the killing blow: {sum(h.killed_by.values())}"
                 + (f" ({n} death{'s' if n > 1 else ''} within {HIT_THEN_DEATH_MS // 1000} s of a hit: the "
                    "deaths it set up, by any killing blow)" if (n := sum(h.died_after.values())) else ""))
    return "\n".join(lines[:31] + await _applications(rep, fights, h, _counting(fights, cut, window), name)
                     + [jobs])


async def _asked(rep: Report, ability: str, pull: PullRef, boss: str | None, all_pulls: bool, whole_pull: bool,
                 start: str | None, end: str | None) -> tuple[list[dict], str, dict[int, float],
                                                          tuple[float, float] | None, Hits]:
    """(the pulls, a note on one still going, when each wipe was decided, the window in each pull, the hits)."""
    pulls = rep.by_boss[rep.boss(boss)] if all_pulls else [rep.fight(pull, boss)]
    fights, going = (rep.ended(pulls), rep.still_going(pulls)) if all_pulls else (pulls, "")
    cut = {} if whole_pull else await rep.decided_cuts(fights)  # later hits are fallout
    window = _window(start, end)
    found = await _hits(rep, fights, rep.ability_ids(ability), _counting(fights, cut, window))
    return fights, going, cut, window, found


async def meter(rep: Report, ability: str, pull: PullRef = None, boss: str | None = None, all_pulls: bool = False,
                whole_pull: bool = False, start: str | None = None, end: str | None = None) -> Meter | None:
    """mechanic_hits for one ability as a meter: each player's damage from it (or its debuff's applications, for a
    debuff that lands more often than it deals damage), their hits and ticks, the deaths soon after a hit and those
    it dealt; the players in the pulls it never hit. None for fewer than MIN_BARS players hit."""
    fights, _, cut, window, h = await _asked(rep, ability, pull, boss, all_pulls, whole_pull, start, end)
    who = set(h.hits) | set(h.ticks) | set(h.debuffs)
    if len(who) < MIN_BARS:
        return None
    by_debuff = h.by_debuff
    bars = []
    for w in sorted(who, key=lambda w: (-h.debuffs[w], -h.damage[w]) if by_debuff else (-h.damage[w], -h.hits[w])):
        note = (plural(h.hits[w], "hit") if h.hits[w] else "") + (", " if h.hits[w] and h.ticks[w] else "") + (
            plural(h.ticks[w], "tick") if h.ticks[w] else "")
        if by_debuff:
            note = ", ".join(x for x in (short(h.damage[w]) if h.damage[w] else "", note) if x)
        bars.append(Bar(rep.names[w], h.debuffs[w] if by_debuff else h.damage[w], note, h.died_after[w],
                        h.killed_by[w]))
    present = {p.id: p.name for f in fights for p in rep.pull_players(f).values()}
    scope = f"all {plural(len(fights), fights[0]['name'] + ' pull')}" if all_pulls else rep.label(fights[0])
    if window:
        scope += f", {clock(window[0])}-{clock(window[1]) if window[1] < math.inf else 'end'} of each"
    if cut:
        scope += ", each until its wipe was decided" if all_pulls else ", until the wipe was decided"
    return Meter(h.name or ability, scope, bars, "applications" if by_debuff else "amount", ability=h.ability_id,
                 note=(f"{plural(sum(h.killed_by.values()), 'death')} with it as the killing blow, "
                       f"{sum(h.died_after.values())} within {HIT_THEN_DEATH_MS // 1000} s of a hit (the deaths it "
                       "set up)."),
                 others=sorted(n for pid, n in present.items() if pid not in who))


def _window(start: str | None, end: str | None) -> tuple[float, float] | None:
    """(from, to) in ms from each pull's start, or None for the whole pull."""
    if start is None and end is None:
        return None
    lo, hi = parse_clock(start) if start else 0.0, parse_clock(end) if end else math.inf
    if lo is None or hi is None or lo > hi:
        raise FactError(f"start and end are m:ss from the pull's start, the start first (got {start!r}, {end!r})")
    return lo, hi


def _counting(fights: list[dict], cut: dict[int, float], window: tuple[float, float] | None) -> Callable[[dict], bool]:
    """Whether an event counts: before its pull's wipe was decided (`cut`), and within `window` of its pull's start."""
    starts = {f["id"]: f["startTime"] for f in fights}

    def counts(e: dict) -> bool:
        fight = e.get("fight", fights[0]["id"])
        if e["timestamp"] > cut.get(fight, math.inf):
            return False
        return window is None or window[0] <= e["timestamp"] - starts.get(fight, fights[0]["startTime"]) <= window[1]
    return counts


async def _applications(rep: Report, fights: list[dict], h: Hits, counts: Callable[[dict], bool],
                        name: str) -> list[str]:
    """Each time the debuff went on a player, with when and how long they kept it, when that's few times; for a
    debuff one player has most of the time (baited or assigned), each time it went on anyone else, and whether that
    player was alive."""
    total = sum(h.debuffs.values())
    if not total:
        return []
    holder, most = h.debuffs.most_common(1)[0]
    baited = most > total / 2 and len(h.debuffs) > 1
    rows = []
    for who, theirs in h.spans.items():
        for start, end in theirs if not (baited and who == holder) else []:
            f = next((f for f in fights if f["startTime"] <= start <= f["endTime"]), None)
            if f and counts({"timestamp": start, "fight": f["id"]}):
                rows.append((start, f, who, end))
    if not rows or not baited and len(rows) > APPLICATIONS_LISTED:
        return []
    if baited:
        lines = [f"{name} went on {rep.names[holder]} {most} of {total} times; each time it went on someone else "
                 f"(pull, when, who, how long they kept it, whether {rep.names[holder]} was alive):"]
    else:
        lines = [f"Each time {name} went on a player (pull, when, who, how long they kept it):"]
    shown = sorted(rows, key=lambda r: r[0])[:APPLICATIONS_LISTED]
    backs = await rep.resurrects(list({id(f): f for _, f, _, _ in shown}.values())) if baited else {}
    for start, f, who, end in shown:
        kept = ("to the end of the pull" if end >= f["endTime"]
                else f"{max(1, round((end - start) / 1000))} s")
        line = f"  {rep.label(f)} {clock(start - f['startTime'])}: {rep.names[who]}, kept {kept}"
        if baited:
            died = dead_since(await rep.deaths(f), backs[f["id"]].get(holder, []), holder, start)  # battle res: alive
            line += (f"; {rep.names[holder]} dead since {clock(died - f['startTime'])}" if died is not None
                     else f"; {rep.names[holder]} alive")
        lines.append(line)
    if len(rows) > APPLICATIONS_LISTED:
        lines.append(f"  and {len(rows) - APPLICATIONS_LISTED} more")
    return lines


def _player_line(rep: Report, h: Hits, who: int) -> str:
    line = (f"  {rep.names[who]}{rep.tank_tag(rep.names[who])}: {plural(h.hits[who], 'hit')}"
            + (f" (+{plural(h.ticks[who], 'tick')})" if h.ticks[who] else "") + f", {short(h.damage[who])}, "
            f"{plural(h.debuffs[who], 'application')}, {plural(h.died_after[who], 'death')} soon after, "
            f"{h.killed_by[who]} with it as the killing blow")
    if gaps := h.rehit_gaps[who]:
        together = sum(1 for g in gaps if g <= SAME_MOMENT_MS)
        later = sorted(g for g in gaps if g > SAME_MOMENT_MS)
        line += (f"; {plural(len(gaps), 'hit')} with the debuff up: {together} within {SAME_MOMENT_MS / 1000:g} s "
                 "(one hit or two, the log can't tell)")
        if later:
            line += f", {len(later)} later ({later[0] / 1000:.1f}" + (
                f"-{later[-1] / 1000:.1f}" if len(later) > 1 else "") + " s)"
    return line


async def _hits(rep: Report, fights: list[dict], ability_ids: list[int], counts: Callable[[dict], bool]) -> Hits:
    """The ability's hits and debuffs over the pulls, those `counts` keeps: one query per ability id covers every pull
    (timestamps are report-wide, so pulls don't mix)."""
    out = Hits()
    ids = [f["id"] for f in fights]
    await rep.all_deaths(fights)
    t = await rep.table(ids, "DamageTaken", hostilityType="Friendlies", viewBy="Ability")
    deaths = [d for f in fights for d in (await rep.deaths(f)) if counts({**d, "fight": f["id"]})]
    all_hits: dict[int, list[tuple[float, bool]]] = defaultdict(list)  # every hit (not tick): time, before the cut
    hit_times: dict[int, list[float]] = defaultdict(list)  # of every id: a death after hits of two counts once
    for gid in [e["guid"] for e in t["entries"] if e["guid"] in ability_ids]:
        out.name = rep.ability(gid)
        out.ability_id = out.ability_id or gid
        for e in await rep.events(ids, "DamageTaken", hostilityType="Friendlies", abilityID=gid):
            if e["targetID"] in rep.names and e.get("type") == "damage":
                if not e.get("tick"):
                    all_hits[e["targetID"]].append((e["timestamp"], counts(e)))
                if counts(e):
                    (out.ticks if e.get("tick") else out.hits)[e["targetID"]] += 1
                    out.damage[e["targetID"]] += e.get("amount", 0) + e.get("absorbed", 0)
                    hit_times[e["targetID"]].append(e["timestamp"])
    for d in deaths:
        if d.get("killingAbilityGameID") in ability_ids:
            out.killed_by[d["targetID"]] += 1
        if any(0 <= d["timestamp"] - at <= HIT_THEN_DEATH_MS for at in hit_times[d["targetID"]]):
            out.died_after[d["targetID"]] += 1
    spans = out.spans = await _debuff_spans(rep, ids, ability_ids, counts, out.debuffs)
    # The log can't tell whether a hit with the debuff up was the same source again or another one (every hit has
    # the boss as its source).
    for who, theirs in spans.items():
        for start, end in theirs:
            inside = [h for h in sorted(all_hits[who]) if start <= h[0] < end]
            out.rehit_gaps[who] += [b - a for (a, _), (b, counted) in itertools.pairwise(inside) if counted]
    return out


async def _debuff_spans(rep: Report, fight_ids: list[int], ability_ids: list[int], counts: Callable[[dict], bool],
                        applied: Counter) -> dict[int, list[list[float]]]:
    """Per player, each [applied, removed] of the ability's debuff; `applied` counts those before the cut."""
    spans: dict[int, list[list[float]]] = defaultdict(list)
    for gid in ability_ids:
        for e in sorted(await rep.events(fight_ids, "Debuffs", hostilityType="Friendlies", abilityID=gid),
                        key=lambda e: e["timestamp"]):
            if e["targetID"] not in rep.names:
                continue
            theirs = spans[e["targetID"]]
            if e.get("type") == "applydebuff":
                if theirs and theirs[-1][1] == math.inf:
                    theirs[-1][1] = e["timestamp"]
                theirs.append([e["timestamp"], math.inf])
                if counts(e):
                    applied[e["targetID"]] += 1
            elif e.get("type") == "removedebuff" and theirs and theirs[-1][1] == math.inf:
                theirs[-1][1] = e["timestamp"]
    return spans


async def mechanic_outliers(rep: Report, pull: PullRef = None, boss: str | None = None,
                            all_pulls: bool = False) -> str:
    """For each enemy ability that hurt the raid before the wipe was decided: how many players took it, and who
    took far more of it per minute alive than the rest. A mechanic few players take is avoided or assigned; one
    everyone takes shows who stood in it longest."""
    pulls = rep.by_boss[rep.boss(boss)]
    fights = [f for f in pulls if attempt(f)] if all_pulls else [rep.fight(pull, boss)]
    if not fights:
        raise FactError(f"no {pulls[0]['name']} pull has ended yet, other than resets")
    decided = await rep.decided_cuts(fights)
    cuts = [decided.get(f["id"], f["endTime"]) for f in fights]
    # Events, not a table: WCL's tables list only the top five per ability or player. Every pull's in one query.
    per_pull = await rep.events_for([(f["id"], {"endTime": cut}) for f, cut in zip(fights, cuts, strict=True)],
                                    "DamageTaken", hostilityType="Friendlies")

    damage: dict[str, Counter] = defaultdict(Counter)  # ability (by name: one logs under several ids) -> player
    killed: dict[str, Counter] = defaultdict(Counter)
    gids: dict[str, set[int]] = defaultdict(set)
    alive_min: dict[str, float] = defaultdict(float)  # player -> minutes alive before each wipe was decided, summed
    present: Counter = Counter()  # player -> pulls they were in
    backs = await rep.resurrects(fights)  # read with the cuts
    for f, events, cut in zip(fights, per_pull, cuts, strict=True):
        ids = set(f.get("friendlyPlayers") or []) or set(rep.names)  # who was in the pull
        deaths = await rep.deaths(f)
        for d in deaths:
            if d["timestamp"] <= cut:
                killed[rep.ability(d.get("killingAbilityGameID"))][rep.names[d["targetID"]]] += 1
        for pid in ids:
            if pid in rep.names:
                present[rep.names[pid]] += 1
                alive_min[rep.names[pid]] += alive_ms(deaths, backs[f["id"]].get(pid, []), pid, f["startTime"],
                                                      cut) / 60000
        for e in events:
            source = rep.actors.get(e.get("sourceID", -1), {}).get("type")
            if source in ("Player", "Pet") or e.get("targetID") not in rep.names:
                continue  # friendly fire and self-inflicted damage
            amount = e.get("amount", 0) + e.get("absorbed", 0)
            damage[rep.ability(e.get("abilityGameID"))][rep.names[e["targetID"]]] += amount
            gids[rep.ability(e.get("abilityGameID"))].add(e.get("abilityGameID") or 0)
    raid = len(present)
    going = " and the pull still going" if any(f.get("inProgress") for f in pulls) else ""
    scope = (f"all {plural(len(fights), fights[0]['name'] + ' pull')} (resets{going} left out)" if all_pulls
             else rep.label(fights[0]))
    lines = [f"Enemy abilities on {scope}, each pull until the wipe was decided (the boss's enrage or half the "
             f"raid dead), {plural(raid, 'player')}. Per ability: players who took it, the median damage per minute "
             "alive of those who did, and who took most (times the median: everyone at 2x or more, else the top three; "
             "deaths it killed):"]
    rows = []
    for ability, per in damage.items():
        rates = {n: v / max(alive_min[n], 0.1) for n, v in per.items() if v > 0}
        if not rates:
            continue
        med = statistics.median(rates.values())
        high = sorted(((n, r / med) for n, r in rates.items() if med and (r >= 2 * med or len(rates) > 3)),
                      key=lambda x: -x[1])
        high = [h for h in high if h[1] >= 2] or high[:3]  # nobody at twice the median: the top three anyway
        rows.append((sum(per.values()), ability, len(rates), med, high))
    for total, ability, hit, med, high in sorted(rows, key=lambda r: -r[0])[:15]:
        few = " (few players: avoided or assigned)" if hit < raid / 2 else ""
        text = ", ".join(f"{n}{rep.tank_tag(n)} {x:.1f}x"
                         + (f" ({killed[ability][n]} killed)" if killed[ability][n] else "")
                         for n, x in high[:6]) or "-"
        lines.append(f"  {rep.ability_refs(ability, gids[ability])}: {hit} of {raid} took it{few}, median "
                     f"{short(med)}/min, total {short(total)}; most: {text}")
    if not rows:
        lines.append("  no enemy damage recorded")
    lines.append("Damage per minute alive evens out early deaths, not assignments: a soaker or the player a "
                 "mechanic targets takes more by design.")
    return "\n".join(lines)
