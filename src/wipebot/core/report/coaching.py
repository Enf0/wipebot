"""One player: a pull of theirs (`player_pull`), a night of theirs (`player_night`), and a buff at
their casts (`buff_at_casts`)."""
import asyncio
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError, spaced
from ..pulls import FIRST_DEATHS, Decided, is_reset, pct_text, wipe_pct
from ..text import clock, name_key, plural, short
from ..wcl import WCLError
from .base import LUST_GROUP_MS, ROLE_NAMES, FactError, Player, PullRef, Report, unavailable
from .build import gear_stats, hero_of, prepared
from .casting import alive, casting, casting_lines, raid_pauses, without
from .cooldowns import NOT_ON_COOLDOWN, OVER_BASE, cooldown_lines, cooldown_use, prepull
from .ranks import Spread, benchmark_line, kills_spread, parse

# WCL's power types (classResources `type`, Resources graphs at 100 + type), the game's Enum.PowerType.
POWER_TYPES = {0: "Mana", 1: "Rage", 2: "Focus", 3: "Energy", 4: "Combo Points", 5: "Runes", 6: "Runic Power",
               7: "Soul Shards", 8: "Astral Power", 9: "Holy Power", 11: "Maelstrom", 12: "Chi", 13: "Insanity",
               16: "Arcane Charges", 17: "Fury", 18: "Pain", 19: "Essence"}
AT_CAP = 0.98  # a resource this close to its maximum is capped: what it gains now is lost
CAP_STRETCHES = 4  # the longest stretches at the cap named, with their times
CAP_STRETCH_MS = 2000  # a shorter one is a cast's worth


async def player_pull(rep: Report, player: str, pull: PullRef = None, boss: str | None = None) -> str:
    f = rep.fight(pull, boss)
    p = rep.player(player)
    rep.require_in(f, p)
    p = rep.in_pull(f, p)
    t0 = f["startTime"]
    details, deaths, events, pauses = await asyncio.gather(rep.details(f), rep.deaths(f), rep.cast_events(f, p),
                                                           raid_pauses(rep, f))
    det = details.get(p.id, {})
    own = [d for d in deaths if d["targetID"] == p.id]
    casts = [c for c in events if c["type"] == "cast"]
    alive_s, alive_until = alive(rep, f, p, own, casts)
    flow = casting(events, alive_until, [d["timestamp"] for d in own])
    pauses = [(a, min(b, alive_until)) for a, b in pauses if a < alive_until]  # while they were alive
    flow.gaps = without(flow.gaps, pauses)  # a raid-wide stun isn't the player's gap
    # Every part at once; each fetches what it needs, and what two need is fetched once.
    (hero, parsed, spread, throughput, died, taken), (cooldowns, uptimes, (resource, resource_name, capped),
                                                      (stones, potions), ready, decided) = await asyncio.gather(
        asyncio.gather(_hero_text(rep, p, det), _parsed(rep, f, p), _spread(rep, f, p), _throughput_tables(rep, f, p),
                       _died_line(rep, f, own), _taken_line(rep, f, p)),
        asyncio.gather(cooldown_lines(rep, f, p, casts), _uptimes(rep, f, p, alive_s, alive_until),
                       resource_use(rep, f, p, casts, alive_s, alive_until), rep.potions(f, p), prepared(rep, f, p),
                       rep.decided_at(f)))
    lost = time_lost_text(time_lost(f, own, casts, flow.gaps, resource_name, capped, decided))
    lines = [f"{p.label}{f', hero talents {hero}' if hero else ''}, {p.role}, item level "
             f"{det.get('maxItemLevel', '?')}, on {rep.label(f)} ({clock(f['endTime'] - t0)}, {rep.url(f)})."]
    lines += [gear_stats(det), lost, parsed] if parsed is not None else [lost, gear_stats(det)]
    for (_, what), t in throughput:
        lines += _throughput_lines(rep, f, p, what, t, bool(own), alive_s, spread)
    lines += [died, taken]
    counts = Counter(rep.ability_ref(c["abilityGameID"]) for c in casts)
    lines.append(f"Casts: {len(casts)} ({60 * len(casts) / max(alive_s, 1):.0f} per minute alive; every cast "
                 "event, procs and items included): " + ", ".join(f"{a} {n}" for a, n in counts.most_common(18)))
    lines += casting_lines(rep, f, flow, alive_s, pauses)
    lines += cooldowns + uptimes
    if resource:
        lines.append(resource)
    lines.append(f"Healthstones and healing potions: {stones}, combat potions: {potions}")
    lines.append(ready)
    return "\n".join(lines)


async def _parsed(rep: Report, f: dict, p: Player) -> str | None:
    """A kill's parse."""
    return await parse(rep, f, p) if f["kill"] else None


async def _spread(rep: Report, f: dict, p: Player) -> Spread | str | None:
    """On a wipe, the spread of the spec's kills its throughput is placed in (not for a tank)."""
    return await kills_spread(rep, f, p) if not f["kill"] and p.role != "tank" else None


async def _hero_text(rep: Report, p: Player, det: dict) -> str | None:
    try:
        return await hero_of(rep, p, det)
    except KnowledgeError:
        return "unavailable (Raidbots' talent tree couldn't be read)"


async def _throughput_tables(rep: Report, f: dict, p: Player) -> list[tuple[tuple[str, str], dict]]:
    """WCL's tables of what the player's role is judged by: a healer's healing and damage, everyone else's damage."""
    kinds = (("Healing", "healing"), ("DamageDone", "damage")) if p.role == "healer" else (("DamageDone", "damage"),)
    return list(zip(kinds, await asyncio.gather(*(rep.table(f["id"], t) for t, _ in kinds)), strict=True))


def _throughput_lines(rep: Report, f: dict, p: Player, what: str, t: dict, died: bool, alive_s: float,
                      spread: Spread | str | None) -> list[str]:
    """The player's healing or damage on a pull: total, per second, rank in their role, the same spec, top
    abilities and targets, and on a wipe, where it stands against the spec's kills."""
    here = rep.pull_players(f)
    dur_s = (f["endTime"] - f["startTime"]) / 1000
    peers = sorted((e for e in t["entries"] if (q := here.get(e.get("id"))) and q.role == p.role),
                   key=lambda e: -e.get("total", 0))
    mine = next((e for e in t["entries"] if e.get("id") == p.id), None)
    if not mine:
        return [f"No {what} recorded."]
    line = f"{what.capitalize()}: {short(mine['total'])} ({short(mine['total'] / dur_s)}/s over the pull"
    if died:
        line += f", {short(mine['total'] / max(alive_s, 1))}/s over the {alive_s:.0f} s alive"
    line += ")"
    if mine in peers:
        line += f", rank {peers.index(mine) + 1} of {len(peers)} {ROLE_NAMES[p.role]} (all specs)"
    if what == "healing" and (over := mine.get("overheal", 0)):
        line += f", overhealing {100 * over / (over + mine['total']):.0f}%"
    if mine.get("activeTime"):
        line += f", active {100 * mine['activeTime'] / 1000 / dur_s:.0f}% of the pull"
    lines = [line]
    same = [e for e in peers if e is not mine and (q := here.get(e.get("id")))
            and (q.class_name, q.spec) == (p.class_name, p.spec)]
    if same:
        lines.append("  same spec in this pull: " + ", ".join(f"{e['name']} {short(e['total'] / dur_s)}/s"
                                                               for e in same))
    lines.append("  top: " + ", ".join(f"{a['name']} {short(a['total'])}" for a in mine.get("abilities", [])[:5]))
    if what == "damage" and (targets := sorted(mine.get("targets", []), key=lambda x: -x.get("total", 0))):
        lines.append("  by target: " + ", ".join(
            f"{x['name']} {100 * x['total'] / max(mine['total'], 1):.0f}%" for x in targets[:5]))
    if spread is not None and (what == "healing") == (p.role == "healer"):
        if bench := benchmark_line(f, p, mine["total"] / max(alive_s, 1), spread):
            lines.append(bench)
    return lines


async def _died_line(rep: Report, f: dict, own: list[dict]) -> str:
    return "Died: " + (", ".join([f"{clock(d['timestamp'] - f['startTime'])} to "
                                  f"{rep.ability(d.get('killingAbilityGameID'))}{await _death_note(rep, f, d)}"
                                  for d in own]) if own else "no")


async def _taken_line(rep: Report, f: dict, p: Player) -> str:
    until, note = await rep.until_decided(f, False)
    t = await rep.table(f["id"], "DamageTaken", hostilityType="Friendlies", sourceID=p.id, **until)
    taken = sorted(t["entries"], key=lambda e: -e.get("total", 0))[:6]
    return f"Damage taken{note}: " + ", ".join(f"{e['name']} {short(e['total'])}" for e in taken)


def _decided_ts(f: dict, decided: Decided | None) -> float:
    """When a pull stopped counting: the moment the wipe was decided, or its end."""
    return f["startTime"] + decided.at_ms if decided else f["endTime"]


def _dead_s(f: dict, deaths: list[dict], casts: list[dict], end: float) -> float:
    """Seconds a player was dead before `end`; one brought back (a battle res) is alive from their next cast, not a
    fake one."""
    spans = [(d["timestamp"], next((c["timestamp"] for c in casts if c["timestamp"] > d["timestamp"]
                                    and not c.get("fake")), f["endTime"])) for d in deaths]
    return sum(max(0.0, min(b, end) - a) for a, b in spans if a < end) / 1000


@dataclass(frozen=True)
class TimeLost:
    """Where a player's time went until the wipe was decided (or the pull's end), in seconds, so a death, casting gaps
    and a capped resource can be weighed in one unit: dead loses all output, a gap the casts not begun, the cap only
    that resource's gain."""
    until_ms: float  # from the pull's start: when the wipe was decided, or the pull's end
    decided: bool  # whether `until_ms` is when the wipe was decided
    dead_s: float
    gaps_s: float
    capped_s: float
    resource: str  # the capped resource's name

    def costs(self) -> list[tuple[float, str]]:
        """(seconds, what) of each cost, biggest first."""
        return sorted([(self.dead_s, "dead (no output at all)"), (self.gaps_s, "casting gaps (no cast begun)"),
                       (self.capped_s, f"{self.resource} at its cap (only its gain lost)")], key=lambda x: -x[0])


def time_lost(f: dict, deaths: list[dict], casts: list[dict], gaps: list[tuple[float, float]], resource: str,
              capped: list[tuple[float, float]], decided: Decided | None) -> TimeLost:
    """A player's time lost on a pull (TimeLost), from their deaths, casts, casting gaps and spans at the cap."""
    end = _decided_ts(f, decided)

    def within(spans: list[tuple[float, float]]) -> float:
        return sum(max(0.0, min(b, end) - a) for a, b in spans if a < end) / 1000
    return TimeLost(end - f["startTime"], decided is not None, _dead_s(f, deaths, casts, end), within(gaps),
                    within(capped), resource)


def time_lost_text(lost: TimeLost) -> str:
    """player_pull's "Time lost" line: each cost of a second or more, biggest first, with its share of the time."""
    window = lost.until_ms / 1000
    head = (f"Time lost until {clock(lost.until_ms)}, when the wipe was decided" if lost.decided
            else f"Time lost over the {clock(lost.until_ms)} pull")
    shown = [f"{what} {s:.0f} s ({100 * s / max(window, 1):.0f}%)" for s, what in lost.costs() if s >= 1]
    return f"{head}, biggest first: " + (", ".join(shown) if shown else "none") + "."


async def _death_note(rep: Report, f: dict, death: dict) -> str:
    """' (after the wipe was decided at 2:32)' for a death in a wipe's fallout, else ''."""
    decided = await rep.decided_at(f)
    if decided and death["timestamp"] - f["startTime"] > decided.at_ms:
        return f" (after the wipe was decided at {clock(decided.at_ms)}: fallout)"
    return " (before the wipe was decided)" if decided else ""


async def _uptimes(rep: Report, f: dict, p: Player, alive_s: float, until_ts: float) -> list[str]:
    """The player's debuffs on enemies (DoTs, debuff windows) and their own buffs on themselves, as uptime over
    their time alive."""
    until = {"endTime": until_ts}
    who = p.name.replace('"', "")
    lines = []
    for label, data_type, filters in (
            ("Their debuffs on enemies (DoTs and the like)", "Debuffs",
             {"hostilityType": "Enemies", "filterExpression": f'source.name = "{who}"'}),
            ("Their own buffs on themselves", "Buffs",
             {"sourceID": p.id, "filterExpression": f'source.name = "{who}" and target.name = "{who}"'})):
        t = await rep.table(f["id"], data_type, **filters, **until)
        auras = sorted(t.get("auras") or [], key=lambda a: -a.get("totalUptime", 0))
        if auras:
            lines.append(f"{label}, uptime over their {alive_s:.0f} s alive: " + ", ".join(
                f"{a['name']} {min(100, 100 * a.get('totalUptime', 0) / 1000 / max(alive_s, 1)):.0f}%"
                for a in auras[:10]))
    return lines


async def resource_use(rep: Report, f: dict, p: Player, casts: list[dict], alive_s: float, until_ts: float  # noqa: C901
                       ) -> tuple[str | None, str, list[tuple[float, float]]]:
    """(line, resource name, stretches at the cap): mana over the pull for a healer; for a spender, the share of
    their time alive their resource sat at its cap, where what it gained was lost. A cast carries the resource it
    spends (a shaman's Lava Burst mana, their Elemental Blast Maelstrom), so the spec's resource is the first one
    in their casts that isn't mana, unless the spec heals."""
    kind, top = await resource_of(p, casts)
    if kind is None:
        return None, "", []
    try:
        graph = await rep.keep(("graph", f["id"], "Resources", p.id, kind), [f["id"]], lambda: rep.wcl.graph(
            rep.code, f, "Resources", sourceID=p.id, abilityID=100 + kind), FOREVER)
    except WCLError:
        return None, "", []
    end = until_ts
    points = resource_points(graph, p.id, end)
    if not points:
        return None, "", []
    name = POWER_TYPES[kind]
    if kind == 0:  # WCL draws mana in percent
        marks, last = [], None
        for t, v in points:
            if last is None or abs(v - last) >= 10:
                marks.append(f"{clock(t - f['startTime'])} {v:.0f}%")
                last = v
        marks.append(f"{clock(points[-1][0] - f['startTime'])} {points[-1][1]:.0f}% (last)")
        return "Mana over the pull: " + " · ".join(marks), name, []
    if not top:
        return None, "", []
    # The stretches at the cap, so "when do I cap?" has an answer: the opener, a movement, an intermission
    stretches = cap_stretches(points, top, end)
    capped = sum(b - a for a, b in stretches)
    longest = sorted((x for x in stretches if x[1] - x[0] >= CAP_STRETCH_MS), key=lambda x: x[0] - x[1])[:CAP_STRETCHES]
    when = ", ".join(f"{clock(a - f['startTime'])}-{clock(b - f['startTime'])} ({(b - a) / 1000:.0f} s)"
                     for a, b in sorted(longest))
    return (f"{name} at its cap ({top}) for {100 * capped / 1000 / max(alive_s, 1):.0f}% of their time alive "
            "(what it gained then was lost)" + (f"; longest stretches: {when}." if when else "."), name,
            [(a, b) for a, b in stretches])


async def resource_of(p: Player, casts: list[dict]) -> tuple[int | None, float]:
    """(the power type resource_use reads, its maximum) from a player's casts: the spec's spender, or mana for a
    healer; (None, 0) when the casts carry neither."""
    seen: dict[int, dict] = {}
    for c in casts:
        for r in c.get("classResources") or []:
            if r.get("type") in POWER_TYPES:
                seen.setdefault(r["type"], r)
    if not seen:
        return None, 0
    try:
        healer = await knowledge.heals(p.class_name, p.spec)
    except KnowledgeError:
        healer = p.role == "healer"
    spender = next((t for t in seen if t != 0), None)
    kind = 0 if healer or spender is None else spender
    if kind not in seen:
        return None, 0
    return kind, seen[kind].get("max") or 0


def resource_points(graph: dict, player_id: int, end: float) -> list[tuple[float, float]]:
    """A player's points of a Resources graph, up to `end`."""
    series = next((x for x in graph.get("series") or [] if x.get("id") == player_id), None)
    return [(t, v) for t, v in (series or {}).get("data") or [] if t <= end]


def cap_stretches(points: list[tuple[float, float]], top: float, end: float) -> list[tuple[float, float]]:
    """The stretches, up to `end`, when a resource sat at its cap (AT_CAP of `top`): (from, to) timestamps."""
    stretches: list[list[float]] = []
    for (t, v), (t2, _) in zip(points, points[1:] + [(end, 0)], strict=True):
        if v < AT_CAP * top:
            continue
        if stretches and stretches[-1][1] >= t:
            stretches[-1][1] = min(t2, end)
        else:
            stretches.append([t, min(t2, end)])
    return [(a, b) for a, b in stretches]


@dataclass
class NightPull:
    """One pull of a player's night, as the facts the night's text is written from."""
    f: dict
    played: Player  # as they played this pull (a player can heal one pull and deal damage the next)
    mine: dict | None  # their row of the pull's healing or damage table
    peers: list[dict]  # the rows of the players of their role, most first
    alive_s: float
    dead_s: float  # dead before the wipe was decided
    counted_s: float  # the pull until the wipe was decided
    death: tuple[int, dict, str] | None  # (place among the pull's deaths, their first death, its note)
    fallout: bool  # that death came after the wipe was decided
    cooldowns: list[tuple[dict, list[float], int | None]]  # cooldown_use's rows
    parse: str | None  # a kill's parse


@dataclass
class NightCooldowns:
    """A player's major cooldowns summed over a night's pulls."""
    used: Counter = field(default_factory=Counter)
    possible: Counter = field(default_factory=Counter)
    unknown: set[str] = field(default_factory=set)  # throughput cooldowns Lorrgs gives no cooldown for: uses only
    moments: Counter = field(default_factory=Counter)  # defensives and movement spells: uses only
    first_use: dict[str, list[tuple[dict, float]]] = field(default_factory=lambda: defaultdict(list))
    untalented: set[str] = field(default_factory=set)
    unavailable: str = ""  # why there are none

    @property
    def never(self) -> list[str]:
        return [n for n in self.used if not self.used[n]]

    def text(self, name: str, used: int, possible: int | None, note: bool = False) -> str:
        if name in self.unknown:
            return f"{name} {used}" + (" (cooldown unknown to Lorrgs: uses only)" if note else "")
        over = f" ({OVER_BASE if note else 'more than its base cooldown allows'})" if used > (possible or 0) else ""
        return f"{name} {used}/{possible}{over}"


async def player_night(rep: Report, player: str, boss: str | None = None) -> str:
    """One player across every pull on a boss: throughput rank, death and major cooldown use per pull."""
    p = rep.player(player)
    b = rep.boss(boss)
    fights = [f for f in rep.pulls_with(p, rep.by_boss[b]) if not f.get("inProgress")]
    if not fights:
        raise FactError(f"{p.name}'s only pull on {b} is still going: ask again once it has ended")
    pulls, skipped, unavailable_why = await _night_pulls(rep, p, fights)
    return _night_text(rep, p, b, pulls, _night_cooldowns(pulls, skipped, unavailable_why))


async def _night_pulls(rep: Report, p: Player, fights: list[dict]) -> tuple[list[NightPull], set[str], str]:
    """Each pull of a player's night; the cooldowns their talents leave out; why the cooldowns are unavailable."""
    await rep.all_deaths(fights)
    await rep.boss_buff_events(fights)
    # A table per pull (ranks are per pull), healing or damage by the spec played in it. The tables, each pull's
    # talents (cooldown_use) and every pull's casts come in one query each.
    played = [rep.in_pull(f, p) for f in fights]
    healed = [f for f, q in zip(fights, played, strict=True) if q.role == "healer"]
    dealt = [f for f in fights if f not in healed]
    healing, damage, _, events, up_at_pull = await asyncio.gather(
        rep.tables([(f["id"], {}) for f in healed], "Healing"),
        rep.tables([(f["id"], {}) for f in dealt], "DamageDone"), rep.details_many(fights),
        rep.events([f["id"] for f in fights], "Casts", sourceID=p.id), prepull(rep, fights, p))
    tables = dict(zip([f["id"] for f in healed + dealt], healing + damage, strict=True))
    casts: dict[int, list[dict]] = defaultdict(list)
    for c in events:
        if c["type"] == "cast":
            casts[c["fight"]].append(c)
    try:
        uses = [await cooldown_use(rep, q, f, casts[f["id"]], up_at_pull[f["id"]])
                for f, q in zip(fights, played, strict=True)]
        why = ""
    except KnowledgeError as e:
        uses, why = [([], None) for _ in fights], unavailable("Major cooldowns", e)
    out = []
    for f, q, (rows, _) in zip(fights, played, uses, strict=True):
        here = rep.pull_players(f)
        entries = sorted(tables[f["id"]]["entries"], key=lambda e: -e.get("total", 0))
        deaths = await rep.deaths(f)
        own = [d for d in deaths if d["targetID"] == p.id]
        ordered = sorted(casts[f["id"]], key=lambda c: c["timestamp"])
        decided = await rep.decided_at(f)
        end = _decided_ts(f, decided)
        k = next((k for k, d in enumerate(deaths) if d["targetID"] == p.id), None)
        death = (k, deaths[k], await _death_note(rep, f, deaths[k])) if k is not None else None
        out.append(NightPull(
            f, q, next((e for e in entries if e.get("id") == p.id), None),
            [e for e in entries if (o := here.get(e.get("id"))) and o.role == q.role],
            alive(rep, f, p, own, ordered)[0], _dead_s(f, own, ordered, end), (end - f["startTime"]) / 1000,
            death, bool(death and decided and death[1]["timestamp"] - f["startTime"] > decided.at_ms), rows,
            await parse(rep, f, q) if f["kill"] else None))
    skipped = {n for _, s in uses for n in s or []}
    return out, skipped, why


def _night_cooldowns(pulls: list[NightPull], skipped: set[str], why: str) -> NightCooldowns:
    cds = NightCooldowns(unavailable=why)
    for pull in pulls:
        for sp, times, possible in pull.cooldowns:
            if NOT_ON_COOLDOWN & set(sp.get("tags") or []):
                cds.moments[sp["name"]] += len(times)
                continue
            cds.used[sp["name"]] += len(times)
            if times:
                cds.first_use[sp["name"]].append((pull.f, min(times)))
            if possible is None:
                cds.unknown.add(sp["name"])
            else:
                cds.possible[sp["name"]] += possible
    cds.untalented = skipped - {sp["name"] for pull in pulls for sp, _, _ in pull.cooldowns}
    return cds


def _night_trend(pulls: list[NightPull], role: str, what: str) -> str | None:
    """How the night went for the player, first third of the pulls against the middle and the last (the thirds
    `progress` gives the raid's wipes in): deaths before the wipe was decided, and the median of their output per
    second alive. None for fewer than six pulls."""
    counted = [pull for pull in pulls if not is_reset(pull.f)]
    if len(counted) < 6:
        return None
    third = len(counted) // 3
    parts = [counted[:third], counted[third:-third], counted[-third:]]
    deaths = " / ".join(f"{sum(bool(x.death) and not x.fallout for x in part)} of {len(part)}" for part in parts)

    def rate(part: list[NightPull]) -> str:
        rates = sorted(x.mine["total"] / max(x.alive_s, 1) for x in part if x.mine and x.played.role == role)
        return short(rates[len(rates) // 2]) + "/s" if rates else "-"
    return (f"Over the night (the pulls in thirds: first / middle / last): died before the wipe was decided on "
            f"{deaths}; {what} per second alive, median, {' / '.join(rate(part) for part in parts)}.")


def _night_text(rep: Report, p: Player, b: str, pulls: list[NightPull], cds: NightCooldowns) -> str:
    main_role = Counter(pull.played.role for pull in pulls).most_common(1)[0][0]
    what = "healing" if main_role == "healer" else "damage"
    died = sum(pull.death is not None for pull in pulls)
    fallout = sum(pull.fallout for pull in pulls)
    first = sum(pull.death[0] == 0 for pull in pulls if pull.death and not pull.fallout)
    early = sum(pull.death[0] < FIRST_DEATHS for pull in pulls if pull.death and not pull.fallout)
    dead_s, counted_s = sum(pull.dead_s for pull in pulls), sum(pull.counted_s for pull in pulls)
    ranked = [(pull.mine["total"] / max(pull.alive_s, 1), rep.pull_no(pull.f)) for pull in pulls
              if pull.mine and not is_reset(pull.f) and pull.played.role == main_role]
    best = max(ranked, key=lambda x: x[0], default=None)  # the first of equals, as pulls go
    live = [rep.pull_no(f) for f in rep.by_boss[b] if f.get("inProgress")]
    total = len(rep.by_boss[b]) - len(live)
    which = (f"all {plural(total, 'pull')}" if len(pulls) == total
             else f"in {len(pulls)} of the {plural(total, 'pull')} (not in the others, so not judged on them)")
    which += "".join(f"; pull {n} is still going and left out until it ends" for n in live)
    # Everyone dies on a wipe, so "died in 42 of 42" says nothing: only the deaths before a wipe was decided count.
    lines = [f"{p.label} on {b}, {which}. Died before the wipe was decided on {died - fallout} of "
             f"{plural(len(pulls), 'pull')} (later deaths are fallout and don't count); first death "
             f"{plural(first, 'time')}, among the first {FIRST_DEATHS} deaths {plural(early, 'time')}; dead for "
             f"{dead_s:.0f} s of the {counted_s:.0f} s before each wipe was decided "
             f"({100 * dead_s / max(counted_s, 1):.0f}%: no output then)."]
    if best:
        lines.append(f"Best pull by {what} per second alive: pull {best[1]} ({short(best[0])}/s).")
    if trend := _night_trend(pulls, main_role, what):
        lines.append(trend)
    never = cds.never
    if cds.used:
        lines.append("Major cooldowns over the night (uses / possible; list from Lorrgs, uses from the log; each "
                     "pull below has the times, 0:00 for one up at the pull): "
                     + ", ".join(cds.text(n, cds.used[n], cds.possible[n], True) + _first_use(rep, cds.first_use[n])
                                 for n in cds.used if n not in never))
    if cds.unavailable:
        lines.append(cds.unavailable)
    if cds.untalented:
        lines.append("Not talented on the night (their talents leave these out): " + ", ".join(sorted(cds.untalented)))
    if never:
        lines.append("Never cast on the night (probably not talented): " + ", ".join(never))
    if cds.moments:
        lines.append("Defensive, tank and movement spells used over the night (for a moment, not on cooldown, "
                     "so not judged by count): " + ", ".join(f"{n} {c}" for n, c in cds.moments.items() if c))
    return "\n".join(lines + [_night_row(rep, p, pull, cds) for pull in pulls])


def _first_use(rep: Report, found: list[tuple[dict, float]]) -> str:
    """When a cooldown was first used on the pulls it was used on: the median, and the latest."""
    if not found:
        return ""
    last = max(found, key=lambda x: x[1])
    return (f" (first use on {len(found)} pull{'s' * (len(found) != 1)}: median "
            f"{clock(statistics.median(t for _, t in found))}, latest pull {rep.pull_no(last[0])} at "
            f"{clock(last[1])})")


def _night_row(rep: Report, p: Player, pull: NightPull, cds: NightCooldowns) -> str:
    f, q, mine = pull.f, pull.played, pull.mine
    own_what = "healing" if q.role == "healer" else "damage"
    perf = (f"{own_what} {short(mine['total'] / max(pull.alive_s, 1))}/s while alive, rank "
            f"{pull.peers.index(mine) + 1} of {len(pull.peers)} {ROLE_NAMES[q.role]} by total"
            if mine and mine in pull.peers else f"no {own_what}")
    if q.spec != p.spec:
        perf = f"as {spaced(q.spec)}: {perf}"
    if pull.parse:
        perf += "; " + pull.parse.removesuffix(".")
    death = "survived"
    if pull.death:
        k, d, note = pull.death
        death = (f"died #{k + 1} at {clock(d['timestamp'] - f['startTime'])} to "
                 f"{rep.ability_ref(d.get('killingAbilityGameID'))}{note}")
    never = cds.never
    cd_text = [cds.text(sp["name"], len(times), possible)
               + (" at " + ", ".join(clock(t) for t in sorted(times)) if times else "")
               for sp, times, possible in pull.cooldowns if sp["name"] in cds.used and sp["name"] not in never]
    pct = "kill" if f["kill"] else pct_text(wipe_pct(f))
    return (f"  pull {rep.pull_no(f)} ({pct}, {clock(f['endTime'] - f['startTime'])}): {perf}; {death}"
            + (f"; cooldowns {', '.join(cd_text)}" if cd_text else ""))


async def buff_at_casts(rep: Report, player: str, buff: str, spell: str, pull: PullRef = None,
                        boss: str | None = None, all_pulls: bool = False) -> str:
    """`buff` on `player` (and its stacks) at each of their casts of `spell` on a pull, from the buff's events
    (applied, a stack added or taken, removed) up to the cast. With `all_pulls`, on every pull on the boss they
    were in (fetched in two queries), with when they first cast it on each."""
    f = rep.fight(pull, boss)
    spell_ids = set(rep.ability_ids(spell))
    spell_name = rep.ability(min(spell_ids))
    if not all_pulls:
        p = rep.in_pull(f, rep.player(player))
        buff_ids, buff_name = await _buff_ids(rep, buff, [(f, p)])
        where = f"{rep.boss_key(f)} pull {rep.pull_no(f)}"
        states, stacked = await _buff_states(rep, f, p, buff_ids, spell_ids)
        if not states:
            return f"{p.name} didn't cast {spell} on {where}."
        if not any(n for _, n in states) and not stacked and states[0][1] is None:
            return (f"{buff_name} was never on {p.name} on {where}, at any of their "
                    f"{plural(len(states), spell_name + ' cast')}. If it's a buff they should have had, check its "
                    "name: the log may name it differently (a raid buff's name depends on who cast it).")
        lines = [f"{buff_name} on {p.name} at each {spell_name} cast, {where} (the buff's state just before the cast):"]
        lines += [f"- {clock(at)}: {_state(n, stacked)}" for at, n in states]
        tally = _tally([n for _, n in states], stacked)
        return "\n".join(lines + [f"Over the {plural(len(states), 'cast')}: {tally}."])
    who = rep.player(player)
    fights = rep.pulls_with(who, rep.by_boss[rep.boss_key(f)])
    played = [rep.in_pull(g, who) for g in fights]
    await rep.events_for([(g["id"], {"sourceID": q.id}) for g, q in zip(fights, played, strict=True)], "Buffs")
    buff_ids, buff_name = await _buff_ids(rep, buff, list(zip(fights, played, strict=True)))
    await rep.events_for([(g["id"], {"sourceID": q.id}) for g, q in zip(fights, played, strict=True)], "Casts")
    found = [(g, *await _buff_states(rep, g, q, buff_ids, spell_ids)) for g, q in zip(fights, played, strict=True)]
    stacked = any(st for _, _, st in found)
    cast_on = [(g, states) for g, states, _ in found if states]
    lines = [f"{buff_name} on {who.name} at each {spell_name} cast, on each {rep.boss_key(f)} pull they were in "
             f"({len(fights)}; the buff's state just before each cast):"]
    for g, states, _ in found:
        if len(states) > LISTED_CASTS:  # a spell cast all the time: what the buff was, and the first cast
            text = (f"{len(states)} casts, {_tally([n for _, n in states], stacked)}; first at {clock(states[0][0])} "
                    f"{_state(states[0][1], stacked)}")
        else:
            text = ", ".join(f"{clock(at)} {_state(n, stacked)}" for at, n in states) or f"no {spell_name}"
        lines.append(f"  pull {rep.pull_no(g)}: {text}")
    every = [n for _, states in cast_on for _, n in states]
    if not every:
        return f"{who.name} didn't cast {spell} on any {rep.boss_key(f)} pull."
    firsts = [(g, states[0][0]) for g, states in cast_on]
    last = max(firsts, key=lambda x: x[1])
    lines.append(f"Over the {plural(len(every), 'cast')}: {_tally(every, stacked)}. First {spell_name} on "
                 f"{len(firsts)} of {plural(len(fights), 'pull')}: median "
                 f"{clock(statistics.median(t for _, t in firsts))}, earliest {clock(min(t for _, t in firsts))}, "
                 f"latest pull {rep.pull_no(last[0])} at {clock(last[1])}.")
    if not any(every) and not stacked:
        lines.append(f"{buff_name} was never up at a cast: if it's a buff they should have had, check its name (a raid "
                     "buff's name depends on who cast it).")
    return "\n".join(lines)


LISTED_CASTS = 6  # with all_pulls, a pull's casts listed one by one up to this many
LUST_NAMES = ("lust", "bloodlust", "heroism", "hero")  # what raiders call Bloodlust and its kin
LUST_SAME_MS = 100  # a buff gained this close to a player's Sated (& co.) came with it
LUST_LASTED = 0.75  # and lasted at least this share of Bloodlust's duration, or until the pull ended


async def _buff_ids(rep: Report, buff: str, items: list[tuple[dict, Player]]) -> tuple[set[int], str]:
    """The ids of the buff a tool was given, and its name. Bloodlust by any of its names is the buff each Bloodlust
    of its kin gave the player on these pulls, whoever cast it (Time Warp, Primal Rage, ...), as pull_timeline
    finds Bloodlust: the buff gained with Sated or its kin, lasting about as long as Bloodlust."""
    if name_key(buff) not in LUST_NAMES:
        ids = set(rep.ability_ids(buff))
        return ids, rep.ability(min(ids))
    lust, duration = set(await knowledge.lust_debuffs()), await knowledge.lust_duration_s() * 1000
    await rep.events_for([(f["id"], {}) for f, _ in items], "Debuffs", hostilityType="Friendlies")
    found: set[int] = set()
    for f, p in items:
        sated = [e["timestamp"] for e in await rep.events(f["id"], "Debuffs", hostilityType="Friendlies")
                 if e["type"] == "applydebuff" and e.get("targetID") == p.id and e.get("abilityGameID") in lust]
        if not sated:
            continue
        auras = sorted(await rep.events(f["id"], "Buffs", sourceID=p.id), key=lambda e: e["timestamp"])
        for i, e in enumerate(auras):
            if e["type"] != "applybuff" or not any(abs(e["timestamp"] - t) <= LUST_SAME_MS for t in sated):
                continue
            end = next((x["timestamp"] for x in auras[i + 1:] if x["type"] == "removebuff"
                        and x.get("abilityGameID") == e.get("abilityGameID")), f["endTime"])
            if end - e["timestamp"] >= LUST_LASTED * duration or end >= f["endTime"] - LUST_GROUP_MS:
                found.add(e["abilityGameID"])
    names = sorted({rep.ability(i) for i in found})
    return found, f"Bloodlust ({', '.join(names)})" if names else "Bloodlust"


async def _buff_states(rep: Report, f: dict, p: Player, buff_ids: set[int], spell_ids: set[int]
                       ) -> tuple[list[tuple[float, int | None]], bool]:
    """Each cast of the spell on a pull: (ms from the pull's start, the buff's stacks then: 0 not up, None when
    the buff never showed on the pull), and whether the buff stacks."""
    auras = sorted((e for e in await rep.events(f["id"], "Buffs", sourceID=p.id) if e.get("abilityGameID") in buff_ids
                    and e["type"] in ("applybuff", "applybuffstack", "removebuffstack", "removebuff")),
                   key=lambda e: e["timestamp"])
    casts = [c for c in await rep.events(f["id"], "Casts", sourceID=p.id)
             if c.get("type") == "cast" and c.get("abilityGameID") in spell_ids]
    if not auras:
        return [(c["timestamp"] - f["startTime"], None) for c in casts], False
    # What the buff was when the pull started: up if the first thing it did was come off.
    first = auras[0]
    up_at_start = 0
    if first["type"] == "removebuff":
        up_at_start = 1
    elif first["type"] == "removebuffstack":
        up_at_start = first.get("stack", 0) + 1
    stacked = any(e["type"] in ("applybuffstack", "removebuffstack") for e in auras)
    out: list[tuple[float, int | None]] = []
    for c in casts:
        stacks, i = up_at_start, 0
        while i < len(auras) and auras[i]["timestamp"] < c["timestamp"]:
            e = auras[i]
            stacks = (1 if e["type"] == "applybuff" else 0 if e["type"] == "removebuff"
                      else e.get("stack", stacks))
            i += 1
        out.append((c["timestamp"] - f["startTime"], stacks))
    return out, stacked


def _state(stacks: int | None, stacked: bool) -> str:
    n = stacks or 0
    return f"{n} stack{'s' * (n != 1)}" if stacked else "up" if n else "not up"


def _tally(stacks: list[int | None], stacked: bool) -> str:
    counts = Counter(n or 0 for n in stacks)
    if stacked:
        return ", ".join(f"{n} stack{'s' * (n != 1)} on {k}" for n, k in sorted(counts.items()))
    return f"up on {sum(k for n, k in counts.items() if n)} of {len(stacks)}"
