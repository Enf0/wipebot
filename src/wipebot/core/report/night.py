"""A whole night: the report at a glance, every pull on a boss and what keeps ending them (`progress`),
and the guild's raid nights (`roster`)."""
import asyncio
import math
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass

from ..cache import DAY
from ..pulls import DIFFICULTIES, FIRST_DEATHS, Decided, attempt, is_reset, pct_text, phase_label, wipe_pct, wipe_pcts
from ..text import clock, clusters, date, ordinal, plural
from ..wcl import GUILD_REPORTS_S, WCLError
from .base import GUILD_RUN_SHARE, FactError, Report, unavailable
from .ending import kind_of_end, pull_ending


def wipe_thirds(wipes: list[float]) -> tuple[float, float, float] | None:
    """Average wipe % over the first, middle and last third of a night's wipes; None for fewer than six."""
    if len(wipes) < 6:
        return None
    third = len(wipes) // 3
    early, middle, late = wipes[:third], wipes[third:-third], wipes[-third:]
    return sum(early) / len(early), sum(middle) / len(middle), sum(late) / len(late)


async def overview(rep: Report) -> str:
    """The night at a glance: every boss, pulls, kills (with Warcraft Logs' speed and execution percentiles), best
    wipe and time spent, and the guild's progress rank in the raid."""
    lines = [f"Report {rep.code} \"{rep.title}\" ({rep.zone}), {date(rep.start_ms, with_time=True)}, "
             f"{clock(rep.end_ms - rep.start_ms)} long, {plural(len(rep.players), 'player')}."]
    (ranked, no_ranks), (progress, no_progress), doubt = await asyncio.gather(
        _or_unavailable("Kills' speed and execution percentiles", _kill_rankings(rep)),
        _or_unavailable(f"{rep.guild_name}'s progress rank", _guild_progress(rep)), rep.guild_run_doubt())
    if doubt:
        lines.append(f"Not a guild night? {doubt[0].upper()}{doubt[1:]}.")
    if progress:
        lines.append(f"{rep.guild_name}'s progress rank in {rep.zone} on Warcraft Logs: "
                     + ", ".join(f"{n} in the {where}" for where, n in progress.items()) + ".")
    lines += [line for line in (no_progress, no_ranks if any(f["kill"] for f in rep.fights) else None) if line]
    for b, pulls in rep.by_boss.items():
        kills = [f for f in pulls if f["kill"]]
        wipes = wipe_pcts(pulls)
        resets = sum(1 for f in pulls if is_reset(f))
        spent = sum(f["endTime"] - f["startTime"] for f in pulls)
        diff = DIFFICULTIES.get(pulls[0].get("difficulty"), "?")
        text = f"- {b} ({diff}): {plural(len(pulls), 'pull')}" + (f" ({resets} reset{'s' if resets > 1 else ''})"
                                                          if resets else "")
        text += f", {clock(spent)} in combat, "
        if kills:
            text += f"killed on pull {pulls.index(kills[0]) + 1}"
            if k := ranked.get(kills[0]["id"]):
                text += (f" (against other kills of it: speed {ordinal(k['speed'])} percentile, execution "
                         f"{ordinal(k['execution'])} percentile, which weighs deaths and damage taken)")
        else:
            text += f"best wipe {min(wipes):.1f}%" if wipes else "no attempt past a reset"
        if thirds := wipe_thirds(wipes):
            text += f", average wipe {thirds[0]:.1f}% early vs {thirds[2]:.1f}% late"
        lines.append(text)
    return "\n".join(lines)


class _NotAllRanked(Exception):
    def __init__(self, ranked: dict):
        self.ranked = ranked


async def kill_rankings(rep: Report) -> dict[int, dict[str, int]]:
    """{fight id: {'speed': percentile, 'execution': percentile}} for the report's ranked kills ({} when Warcraft
    Logs can't answer)."""
    try:
        return await _kill_rankings(rep)
    except WCLError:
        return {}


async def _kill_rankings(rep: Report) -> dict[int, dict[str, int]]:
    """kill_rankings, or WCLError. Kept for the day once every kill is ranked (more kills come in, so the
    percentiles move)."""
    kills = {f["id"] for f in rep.fights if f.get("kill")}
    if not kills:
        return {}

    async def fetch() -> dict[int, dict[str, int]]:
        data = await rep.wcl.rankings(rep.code, None, "dps")
        # A kill with nothing to rank against (no parses yet, e.g. a Heroic pug) has rankPercent "-".
        ranked = {k["fightID"]: {"speed": k["speed"]["rankPercent"], "execution": k["execution"]["rankPercent"]}
                  for k in data if isinstance((k.get("speed") or {}).get("rankPercent"), int | float)
                  and isinstance((k.get("execution") or {}).get("rankPercent"), int | float)}
        if not kills <= set(ranked):
            raise _NotAllRanked(ranked)  # asked again next time
        return ranked
    try:
        return await rep.keep(("kill rankings", int(time.time() // DAY)), [f["id"] for f in rep.fights], fetch)
    except _NotAllRanked as e:
        return e.ranked


async def _or_unavailable(what: str, get: Awaitable[dict]) -> tuple[dict, str | None]:
    """What `get` gives, or {} and the line saying it's unavailable."""
    try:
        return await get, None
    except WCLError as e:
        return {}, unavailable(what, e)


async def _guild_progress(rep: Report) -> dict[str, int]:
    if not (rep.guild_id and rep.zone_id):
        return {}
    return await rep.wcl.guild_progress(rep.guild_id, rep.zone_id)


SAME_NIGHT_MS = 6 * 3600 * 1000  # logs starting this close together are one raid night (two people logging it)
RAID_PULLS = 3  # a log with fewer boss pulls is a side group or a personal log, not a raid night


async def raid_nights(rep: Report, weeks: int) -> tuple[list[dict], int, dict[str, str]]:
    """The guild's raid nights in the report's raid over the last weeks, oldest first, from Warcraft Logs'
    attendance: [{start, codes, players: {name: presence (1 there, 2 benched)}}], the difficulty (the report's
    hardest), and the logs left out as not the guild's (code -> why). A log counts with enough pulls on that
    difficulty (not an alt run) and most of its players in the guild (not a pug logged to the guild's page)."""
    if not (rep.guild_id and rep.zone_id):
        raise FactError("this report has no guild or no raid, so there are no raid nights to look up")
    since = rep.end_ms - weeks * 7 * 86400 * 1000
    try:
        logs, reports, members = await asyncio.gather(rep.wcl.attendance(rep.guild_id, rep.zone_id, since),
                                                      rep.wcl.guild_reports(rep.guild_id, since, pulls=True,
                                                                              cache_for=GUILD_REPORTS_S),
                                                      rep.wcl.guild_members(rep.guild_id))
    except WCLError as e:
        raise FactError(f"Warcraft Logs attendance unavailable: {e}") from e
    hardest = max(f.get("difficulty") or 0 for f in rep.fights)
    pulls = {r["code"]: (r.get("pulls_by_difficulty") or {}).get(hardest, 0) for r in reports}
    logs = [log for log in logs if pulls.get(log["code"], RAID_PULLS) >= RAID_PULLS]
    not_guild: dict[str, str] = {}  # code -> why
    for log in logs if members else []:
        names = [p["name"] for p in log.get("players") or []]
        if names and (inside := sum(n in members for n in names)) < GUILD_RUN_SHARE * len(names):
            not_guild[log["code"]] = f"{inside} of {len(names)} in the guild"
    logs = [log for log in logs if log["code"] not in not_guild]
    nights: list[dict] = []
    for log in sorted(logs, key=lambda n: n["startTime"]):
        if not nights or log["startTime"] - nights[-1]["start"] > SAME_NIGHT_MS:
            nights.append({"start": log["startTime"], "codes": [], "players": {}})
        night = nights[-1]
        night["codes"].append(log["code"])
        for p in log.get("players") or []:  # there in any log of the night beats benched
            night["players"][p["name"]] = min(night["players"].get(p["name"], 9), p.get("presence") or 1)
    return nights, hardest, not_guild


async def attendance(rep: Report, weeks: int = 4) -> str:
    """Who raided the report's raid on which nights, for its guild over the last weeks: Warcraft Logs' attendance,
    one entry per night."""
    nights, hardest, not_guild = await raid_nights(rep, weeks)
    if not nights:
        return (f"No {DIFFICULTIES.get(hardest, '')} raid nights of {rep.guild_name} in {rep.zone} logged in the "
                f"last {plural(weeks, 'week')}.")
    there: Counter = Counter(n for night in nights for n, presence in night["players"].items() if presence == 1)
    benched: Counter = Counter(n for night in nights for n, presence in night["players"].items() if presence == 2)
    lines = [f"{rep.guild_name}'s {DIFFICULTIES.get(hardest, '')} raid nights in {rep.zone} over the last "
             f"{plural(weeks, 'week')}, from Warcraft Logs' attendance ({plural(len(nights), 'night')}; logs of one "
             f"evening count once, logs with fewer than {RAID_PULLS} {DIFFICULTIES.get(hardest, '')} pulls are left "
             "out):"]
    lines += [f"- {date(n['start'])}: {plural(len(n['players']), 'player')} "
              f"({', '.join(n['codes'])})" for n in nights]
    if not_guild:
        lines.append("Left out, as mostly players from outside the guild (a pug or another group logged to the "
                     "guild's page): " + ", ".join(f"{code} ({why})" for code, why in not_guild.items()))
    lines.append("Nights raided, of " + str(len(nights)) + ": " + ", ".join(
        f"{name} {there[name]}" + (f" (+{benched[name]} benched)" if benched[name] else "")
        for name in sorted(set(there) | set(benched), key=lambda n: (-there[n], n))))
    return "\n".join(lines)


async def roster(rep: Report) -> str:
    """Every player in the report with class, spec and role."""
    lines = [f"{plural(len(rep.players), 'player')} in report {rep.code}:"]
    for role, heading in (("tank", "Tanks"), ("healer", "Healers"), ("dps", "Damage dealers")):
        members = sorted((p for p in rep.players.values() if p.role == role), key=lambda p: p.name)
        if members:
            lines.append(f"{heading}: " + ", ".join(p.label for p in members))
    return "\n".join(lines)


async def each_pull(rep: Report, boss: str | None = None, posts: bool = True, first_hits: bool = False) -> str:
    """Every pull on a boss: its post (pulls.py), and/or who hit the boss first on each."""
    fights = rep.by_boss[rep.boss(boss)]
    out = [await rep.summary(f) for f in fights] if posts else []
    if first_hits:
        out.append(await _first_hits(rep, fights))
    return "\n\n".join(out)


FIRST_HIT_WINDOW_MS = 5_000  # a pull's first hits on the boss are looked for this long after it began
EARLY_GAP_MS = 1_000  # a first hit this far ahead of the next player's is a pull on their own


async def _first_hits(rep: Report, fights: list[dict]) -> str:
    """Who hit the boss first on each pull, with what, and how far ahead of the next player: who pulls. One
    events query for every pull."""
    if not rep.bosses:
        return "First hits on the boss: the log names no boss."
    events = await rep.events_for([(f["id"], {"startTime": f["startTime"],
                                              "endTime": f["startTime"] + FIRST_HIT_WINDOW_MS}) for f in fights],
                                  "DamageDone")  # WCL's filterExpression `target.id in (...)` finds nothing
    lines = ["First hits on the boss, per pull (the log has no pull timer, so how early against a countdown can't "
             "be told; a first hit well ahead of the next player's is someone pulling on their own):"]
    first: Counter = Counter()
    ahead: Counter = Counter()
    for f, evs in zip(fights, events, strict=True):
        hits: dict[str, tuple[float, int]] = {}  # player -> (first hit, ability)
        for e in sorted(evs, key=lambda e: e["timestamp"]):
            who = rep.names.get(rep.owner(e.get("sourceID") or 0))
            if who and e.get("type") == "damage" and e.get("targetID") in rep.bosses and who not in hits:
                hits[who] = (e["timestamp"] - f["startTime"], e.get("abilityGameID") or 0)
        order = sorted(hits.items(), key=lambda kv: kv[1][0])
        if not order:
            lines.append(f"  {rep.label(f)}: no hit on the boss in its first {FIRST_HIT_WINDOW_MS // 1000} s")
            continue
        (name, (at, ability)), rest = order[0], order[1:]
        first[name] += 1
        line = f"  {rep.label(f)}: {name} ({rep.ability(ability)}) at {at / 1000:.1f} s"
        if rest:
            gap = rest[0][1][0] - at
            line += f", {gap / 1000:.1f} s before the next player ({rest[0][0]})"
            if gap >= EARLY_GAP_MS:
                ahead[name] += 1
        else:
            line += f", nobody else hit the boss in its first {FIRST_HIT_WINDOW_MS // 1000} s"
            ahead[name] += 1
        if is_reset(f):  # the raid didn't fight it: a pull it wasn't ready for, or a reset it chose
            line += f"; reset with the boss at {pct_text(wipe_pct(f))}"
        lines.append(line)
    lines.append(f"First on the boss over {plural(len(fights), 'pull')}: "
                 + ", ".join(f"{n} {k}" for n, k in first.most_common())
                 + (f"; at least {EARLY_GAP_MS / 1000:g} s ahead of everyone else: "
                    + ", ".join(f"{n} {k}" for n, k in ahead.most_common()) if ahead else ""))
    return "\n".join(lines)


def _end_buffs(buffs: list[tuple[str, float]], enrages: list[tuple[str, float]], decided: Decided | None) -> str:
    """'; boss <buff> from <when>' for each buff the boss kept to the end, saying so of one that didn't decide the
    wipe: an "enrage" there misleads."""
    def why(name: str, at: float) -> str:
        if (name, at) not in enrages:
            return " (as another boss died: not an enrage)"
        if decided and at > decided.at_ms:
            return f" (after the wipe was decided at {clock(decided.at_ms)}: fallout)"
        return ""
    return "; boss " + ", ".join(f"{n} from {clock(at)}{why(n, at)}" for n, at in buffs) if buffs else ""


async def progress(rep: Report, boss: str | None = None) -> str:
    b = rep.boss(boss)
    fights = rep.by_boss[b]
    await rep.decided_cuts(fights)  # every pull's deaths, boss buffs, battle res and council bosses' deaths at once
    lines = [f"{b}: {plural(len(fights), 'pull')} on the night (pull, boss %, phase, duration, first death, buffs "
             "the boss gained and kept to the end)"]
    if before := await rep.guild_best(fights[0]):
        lines.append(f"The guild's best wipe before the night: {before.text()}.")
    first_killers: Counter = Counter()
    kept: dict[str, list[float]] = defaultdict(list)  # end buff -> when it was gained, per pull
    best = None
    for i, f in enumerate(fights, 1):
        deaths = await rep.deaths(f)
        buffs = await rep.end_buffs(f)
        enrages = await rep.enrages(f)  # a buff a council boss gains as its partner dies decides nothing
        for name, at in enrages:
            kept[name].append(at)
        cutoff = min((at for _, at in enrages), default=None)
        early = [d for d in deaths if cutoff is None or d["timestamp"] - f["startTime"] < cutoff]
        for d in early[:FIRST_DEATHS]:
            first_killers[rep.ability(d.get("killingAbilityGameID"))] += 1
        pct = 0.0 if f["kill"] else wipe_pct(f)
        mark = ""
        if f["kill"]:
            mark = " KILL"
        elif f.get("inProgress"):
            mark = " in progress"
        elif is_reset(f):
            mark = " reset"
        elif pct is not None and (best is None or pct < best):
            best, mark = pct, " best of the night until then"
        first = (f"{rep.names[deaths[0]['targetID']]} ({rep.ability(deaths[0].get('killingAbilityGameID'))}, "
                 f"{clock(deaths[0]['timestamp'] - f['startTime'])})" if deaths else "-")
        phase = phase_label(f) or ""
        end = _end_buffs(buffs, enrages, await rep.decided_at(f))
        lines.append(f"  {i}: {pct_text(pct)} {phase} {clock(f['endTime'] - f['startTime'])} first death "
                     f"{first}{end}{mark}")
    wipes = wipe_pcts(fights)
    if thirds := wipe_thirds(wipes):
        lines.append("Average wipe % (resets left out): first third {:.1f}, middle {:.1f}, last third {:.1f}"
                     .format(*thirds))
    for name, times in kept.items():
        lines.append(f"The boss gained {name} and kept it to the end on {len(times)} of {plural(len(fights), 'pull')}, "
                     f"gained at {clusters(times)}.")
    ends = Counter([kind_of_end(await pull_ending(rep, f)) for f in fights if not f.get("inProgress")])
    lines.append("How the pulls ended, by the first thing that ended each (pull_summary's \"Ended by\"): "
                 + ", ".join(f"{what} {n}" for what, n in ends.most_common()) + ".")
    lines.append(f"Killers of the first {FIRST_DEATHS} deaths per pull"
                 + (" (deaths after the boss gained one of those buffs left out)" if kept else "") + ": "
                 + ", ".join(f"{a} x{n}" for a, n in first_killers.most_common(6)))
    if killer := await _nights_killer(rep, fights):
        lines.append(killer)
    return "\n".join(lines)


KILLER_NAMES = 4  # players named for the night's killer: whom it killed most, and who it hit most


async def _nights_killer(rep: Report, fights: list[dict]) -> str | None:
    """The ability that killed most players over the pulls (a kill's every death, a wipe's until it was decided):
    its deaths, the pulls where it was the first death, whom it killed, and who took most of its hits (a tank
    taking a mechanic every pull shows here)."""
    pulls = [f for f in fights if attempt(f)]
    cut = await rep.decided_cuts(pulls)
    killed: Counter = Counter()  # by name: an ability logs under several ids
    first: Counter = Counter()
    victims: dict[str, Counter] = defaultdict(Counter)
    ids: dict[str, set[int]] = defaultdict(set)
    for f in pulls:
        deaths = [d for d in await rep.deaths(f) if d["timestamp"] <= cut.get(f["id"], math.inf)]
        for k, d in enumerate(deaths):
            if gid := d.get("killingAbilityGameID"):
                name = rep.ability(gid)
                ids[name].add(gid)
                killed[name] += 1
                first[name] += k == 0
                victims[name][rep.names[d["targetID"]]] += 1
    if not killed:
        return None
    name, n = killed.most_common(1)[0]
    hits: Counter = Counter()
    for gid in sorted(ids[name]):
        for e in await rep.events([f["id"] for f in pulls], "DamageTaken", hostilityType="Friendlies",
                                  abilityID=gid):
            if (e.get("type") == "damage" and not e.get("tick") and e["targetID"] in rep.names
                    and e["timestamp"] <= cut.get(e.get("fight"), math.inf)):
                hits[rep.names[e["targetID"]]] += 1

    def named(c: Counter) -> str:
        return ", ".join(f"{who}{rep.tank_tag(who)} {k}" for who, k in c.most_common(KILLER_NAMES))
    line = (f"The night's killer (deaths until each wipe was decided): {rep.ability_refs(name, ids[name])}, "
            f"{plural(n, 'death')} over {plural(len(pulls), 'pull')}, the first death on {first[name]} of them; "
            f"killed most: {named(victims[name])}")
    return line + (f"; hit most (until each wipe was decided): {named(hits)}." if hits else ".")


ENDINGS_SHOWN = 3  # per night, the most common ways its pulls on the boss ended


@dataclass(frozen=True)
class BossNight:
    """One raid night's pulls on a boss: the evidence for "are we getting better over the weeks?"."""
    start_ms: float
    pulls: int
    kill: int | None  # the first kill's pull number on the boss that night
    best: tuple[float, int] | None  # the furthest wipe before the kill (or of the night): wipe %, pull number
    ends: tuple[tuple[str, int], ...]  # how the pulls ended (kind_of_end), the most common first


def boss_pulls(rep: Report, encounter: int, difficulty: int | None) -> list[dict]:
    """A report's finished pulls on one boss and difficulty, in order."""
    return [f for fights in rep.by_boss.values() for f in fights
            if f["encounterID"] == encounter and f.get("difficulty") == difficulty and not f.get("inProgress")]


async def boss_night(reps: list[Report], start_ms: float, encounter: int, difficulty: int | None) -> BossNight | None:
    """One night's pulls on the boss over its reports (a night logged twice counts each pull once: the first log's),
    or None when the night didn't pull it."""
    pulls: list[tuple[Report, dict]] = []
    for r in reps:
        for f in boss_pulls(r, encounter, difficulty):
            if not any(abs(f2["startTime"] + r2.start_ms - f["startTime"] - r.start_ms) < 60_000 for r2, f2 in pulls):
                pulls.append((r, f))
    if not pulls:
        return None
    pulls.sort(key=lambda x: x[0].start_ms + x[1]["startTime"])
    for r in reps:
        await r.decided_cuts(boss_pulls(r, encounter, difficulty))  # each report's pulls' deaths and buffs at once
    kill = next((i for i, (_, f) in enumerate(pulls, 1) if f["kill"]), None)
    wipes = [(pct, i) for i, (_, f) in enumerate(pulls, 1) if not f["kill"] and not is_reset(f)
             and (kill is None or i < kill) and (pct := wipe_pct(f)) is not None]
    ends = Counter([kind_of_end(await pull_ending(r, f)) for r, f in pulls])
    return BossNight(start_ms, len(pulls), kill, min(wipes) if wipes else None, tuple(ends.most_common()))


def weeks_text(boss: str, weeks: int, nights: list[BossNight]) -> str:
    """The boss night by night: pulls, the kill, the best wipe, and how the pulls ended."""
    if not nights:
        return f"The guild's raid nights of the last {plural(weeks, 'week')} have no pulls on {boss}."
    lines = [f"{boss} night by night over the last {plural(weeks, 'week')} (the guild's raid nights in this raid: "
             "pulls, the kill, the best wipe before it, and how the pulls ended, by the first thing that ended each):"]
    for n in nights:
        kill = f"killed on pull {n.kill}" if n.kill else "no kill"
        best = f", best wipe {n.best[0]:.1f}% (pull {n.best[1]})" if n.best else ""
        ends = ", ".join(f"{what} {k}" for what, k in n.ends if what != "a kill")
        lines.append(f"  {date(n.start_ms)}: {plural(n.pulls, 'pull')}, {kill}{best}; ended by {ends or 'nothing yet'}")
    bests = [f"kill on pull {n.kill}" if n.kill else f"{n.best[0]:.1f}" if n.best else "-" for n in nights]
    lines.append("Best wipe night by night: " + " → ".join(bests))
    return "\n".join(lines)
