"""A player across the season and the guild's nights (`player_history`): ranked kills, deaths night by
night, and the guild rankings."""
import asyncio
import math
import statistics
from collections import Counter, defaultdict

from .. import knowledge
from ..knowledge import KnowledgeError, spaced
from ..pulls import DIFFICULTIES, attempt
from ..text import clock, date, fight_url, plural, short
from ..wcl import WCLError
from .base import ROLE_NAMES, FactError, Report

TREND_KILLERS = 6  # the abilities named for what a player keeps dying to


async def deaths_by_night(nights: list[tuple[float, list[Report]]], player: str, difficulty: int,
                        boss: str | None = None) -> str:
    """What a player died to before each wipe was decided (a kill's every death), night by night over the guild's
    raid nights (each a time and its reports), on the raid's difficulty, and the abilities that come back."""
    rows: list[str] = []
    by_night: dict[str, Counter] = defaultdict(Counter)  # ability -> deaths, per night
    label = player
    for start, reps in nights:
        pulls = early = first = 0
        killers: Counter = Counter()
        bosses: set[str] = set()
        for rep in reps:
            try:
                p = rep.player(player)
            except FactError:
                continue
            label = p.label
            if boss:
                try:
                    wanted = {rep.boss(boss)}
                except FactError:
                    continue
            else:
                wanted = set(rep.by_boss)
            fights = [f for b, fs in rep.by_boss.items() if b in wanted for f in fs
                      if f.get("difficulty") == difficulty and attempt(f) and rep.was_in(f, p)]
            if not fights:
                continue
            decided = await rep.decided_cuts(fights)
            for f in fights:
                bosses.add(f["name"])
                pulls += 1
                deaths = [d for d in await rep.deaths(f) if d["timestamp"] <= decided.get(f["id"], math.inf)]
                k = next((i for i, d in enumerate(deaths) if d["targetID"] == p.id), None)
                if k is None:
                    continue
                early += 1
                first += k == 0
                killers[rep.ability(deaths[k].get("killingAbilityGameID"))] += 1
        if not pulls:
            continue
        day = date(start)
        by_night[day] = killers
        rows.append(f"- {day}: {plural(pulls, 'pull')} on {', '.join(sorted(bosses))}; died before the wipe was "
                    f"decided in {early} ({100 * early / pulls:.0f}%), the first death {plural(first, 'time')}"
                    + (": " + ", ".join(f"{a} {n}" for a, n in killers.most_common(TREND_KILLERS)) if killers
                       else ""))
    if not rows:
        return f"{player} wasn't in a {DIFFICULTIES.get(difficulty, '')} pull" + (f" on {boss}" if boss else "") + \
            " on the guild's raid nights in that time."
    total: Counter = sum(by_night.values(), Counter())
    nights_with = Counter(a for c in by_night.values() for a in c)
    lines = [f"{label}'s deaths before each wipe was decided (a kill's every death; later deaths are fallout), on the "
             f"guild's {DIFFICULTIES.get(difficulty, '')} raid nights{f' on {boss}' if boss else ''}, oldest "
             "first:", *rows]
    lines.append(f"Over the {plural(len(rows), 'night')}: " + ", ".join(
        f"{a} {n} (on {nights_with[a]} night{'s' if nights_with[a] > 1 else ''})"
        for a, n in total.most_common(TREND_KILLERS)) if total
        else f"Over the {plural(len(rows), 'night')}: no early deaths.")
    return "\n".join(lines)


GUILD_RANKING_ROLES = {"dps": "DPS", "healer": "Healer", "tank": "Tank"}  # ours -> WCL's RoleType


def _zone_summary(z: dict) -> str:
    """'average of the best kill per boss 86.8 over 5 bosses, of the median kill 70.1; All Stars 502 points (top
    3%)' from a character's zoneRankings, or '' when they have no ranked kill."""
    ranked = [r for r in z.get("rankings") or [] if r.get("rankPercent") is not None]
    if not ranked or z.get("bestPerformanceAverage") is None:
        return ""
    stars = ", ".join(f"{a.get('points') or 0:.0f} points as {spaced(a.get('spec') or '?')}"
                      + (f" (top {100 - a['rankPercent']:.0f}% of the spec)" if a.get("rankPercent") is not None
                         else "")
                      for a in z.get("allStars") or [])
    return (f"average of each boss's best kill {z['bestPerformanceAverage']:.1f} over {len(ranked)} "
            f"boss{'es' * (len(ranked) > 1)}, of each boss's median kill {z.get('medianPerformanceAverage') or 0:.1f}"
            + (f"; All Stars {stars}" if stars else ""))


HISTORY_DIFFICULTIES = (5, 4, 3)  # Mythic, Heroic, Normal; LFR isn't worth judging a player by
HISTORY_KILLS_SHOWN = (3, 10)  # per boss and difficulty, newest first: for the whole raid, for one boss


async def ranked_kills(rep: Report, player: str, boss: str | None = None) -> str:  # noqa: C901, PLR0915
    """Every ranked kill of a player's character this season in the report's raid, from Warcraft Logs' rankings
    (any report, any group, not only this guild's logs): per boss and difficulty, the percentile against the same
    spec with the date, item level, kill time and report."""
    p = rep.player(player)
    if not (p.server and p.region):
        raise FactError(f"the log doesn't give {p.name}'s realm")
    if not rep.zone_id:
        raise FactError("this report has no raid, so there's no raid to look up kills in")
    try:
        char = await rep.wcl.find_character(p.name, p.server, p.region.lower())
        raid, encounters = await rep.wcl.zone_encounters(rep.zone_id)
    except WCLError as e:
        raise FactError(f"Warcraft Logs unavailable: {e}") from e
    if not char:
        return f"Warcraft Logs has no character {p.name} on {p.server}-{p.region.upper()}."
    if boss:
        encounters = [e for e in encounters if knowledge.matches_boss(boss, e["name"])]
        if not encounters:
            raise FactError(f"{raid} has no boss matching {boss!r}")
    shown = HISTORY_KILLS_SHOWN[1 if boss else 0]
    # Warcraft Logs ranks every kill by the metric asked for, whatever the spec (a Restoration kill gets a dps
    # percentile too): each kill takes hps if it was played as a healer spec, dps otherwise.
    try:
        by_metric = dict(zip(("dps", "hps"), await asyncio.gather(*(
            rep.wcl.encounter_rankings(char["id"], [e["id"] for e in encounters], list(HISTORY_DIFFICULTIES), m,
                                       rep.zone_id)
            for m in ("dps", "hps"))), strict=True))
    except WCLError as e:
        raise FactError(f"Warcraft Logs rankings unavailable: {e}") from e
    specs = {k["spec"] for ranked in by_metric.values() for r in ranked.values() for k in r.get("ranks") or []
             if k.get("spec")}
    try:
        healer = {s for s in specs if await knowledge.heals(p.class_name, s)}
    except KnowledgeError as e:
        raise FactError(f"can't tell which of {p.name}'s specs heal: {e}") from e

    def metric_of(k: dict) -> str:
        return "hps" if k.get("spec") in healer else "dps"
    played = sorted({(d, k["spec"]) for m, ranked in by_metric.items() for (_, d), r in ranked.items()
                     for k in r.get("ranks") or [] if k.get("spec") and metric_of(k) == m},
                    key=lambda x: (-x[0], x[1] != p.spec, x[1]))
    try:
        summaries = await asyncio.gather(*(
            rep.wcl.zone_rankings([char["id"]], rep.zone_id, d, "hps" if spec in healer else "dps", spec)
            for d, spec in played))
    except WCLError:
        summaries = []
    server = char.get("server") or {}
    page = (f"https://www.warcraftlogs.com/character/{p.region.lower()}/{server.get('slug', '')}/"
            f"{p.name.lower()}")
    lines = [f"{p.label}, {server.get('name') or p.server}-{p.region.upper()}: every ranked kill in {raid} this "
             f"season, from any report (pugs too), newest first, per boss, difficulty and spec played. Each: "
             f"percentile against the same spec and difficulty (dps, or hps for a healer spec; over the whole "
             f"kill, so a death lowers it), item level, kill time. Wipes are never ranked. Character page: {page}"]
    for (d, spec), z in zip(played, summaries, strict=True):
        if (line := _zone_summary(z.get(char["id"]) or {})):
            lines.append(f"Warcraft Logs' summary, {DIFFICULTIES[d]} as {spaced(spec)} "
                         f"({'hps' if spec in healer else 'dps'}; as on the character page): {line}")
    unranked = []
    for e in encounters:
        found = False
        for d in HISTORY_DIFFICULTIES:
            by_spec: dict[str, list[dict]] = defaultdict(list)
            for m, ranked in by_metric.items():
                for k in (ranked.get((e["id"], d)) or {}).get("ranks") or []:
                    if metric_of(k) == m:
                        by_spec[k.get("spec") or "?"].append(k)
            for spec, kills in sorted(by_spec.items(), key=lambda x: (x[0] != p.spec, -len(x[1]))):
                found = True
                kills.sort(key=lambda k: -(k.get("startTime") or 0))
                pcts = sorted(k.get("rankPercent") or 0 for k in kills)
                lines.append(f"{e['name']}, {DIFFICULTIES[d]}, as {spaced(spec)} ({metric_of(kills[0])}): "
                             f"{len(kills)} kill{'s' * (len(kills) > 1)}, best {pcts[-1]:.0f}%, median "
                             f"{statistics.median(pcts):.0f}%"
                             + (f" (the newest {shown} below; ask for the boss for more)" if len(kills) > shown
                                else ""))
                for k in kills[:shown]:
                    guild = (k.get("guild") or {}).get("name")
                    extra = "" if guild == (rep.guild_name or None) else \
                        f", with {guild}" if guild else ", in a pug or personal log"
                    report = k.get("report") or {}
                    lines.append(f"  {date(k.get('startTime') or 0)} {k.get('rankPercent') or 0:.0f}% "
                                 f"({short(k.get('amount') or 0)}/s, item level {k.get('bracketData') or '?'}, "
                                 f"{clock(k.get('duration') or 0)}{extra}) "
                                 + fight_url(report.get("code", ""), report.get("fightID", 0)))
        if not found:
            unranked.append(e["name"])
    if unranked:
        lines.append("No ranked kill (not killed, or not logged): " + ", ".join(unranked))
    return "\n".join(lines)


async def guild_rankings(rep: Report, difficulty: str = "Mythic", role: str = "dps") -> str:
    """The report's raiders of a role ranked the way the guild's rankings page on Warcraft Logs ranks them: per
    player each boss's best kill this season (any report, pugs too) as a percentile against their spec, and the
    average of those. Damage dealers and tanks by dps, healers by hps; only kills of a spec of that role."""
    d = next((k for k, v in DIFFICULTIES.items() if v.lower() == difficulty.lower()), None)
    if d is None:
        raise FactError(f"no difficulty {difficulty!r}; one of {', '.join(DIFFICULTIES.values())}")
    if role not in GUILD_RANKING_ROLES:
        raise FactError(f"no role {role!r}; one of {', '.join(GUILD_RANKING_ROLES)}")
    if not rep.zone_id:
        raise FactError("this report has no raid, so there's no raid to rank")
    metric = "hps" if role == "healer" else "dps"
    try:
        ids = await rep.wcl.ranked_characters(rep.code)
        players = [(p, ids[p.name]) for p in sorted(rep.players.values(), key=lambda p: p.name) if p.name in ids]
        summaries = await rep.wcl.zone_rankings([i for _, i in players], rep.zone_id, d, metric,
                                                role=GUILD_RANKING_ROLES[role])
        raid, _ = await rep.wcl.zone_encounters(rep.zone_id)
    except WCLError as e:
        raise FactError(f"Warcraft Logs rankings unavailable: {e}") from e
    rows, none = [], []
    for p, i in players:
        z = summaries.get(i) or {}
        if not (summary := _zone_summary(z)):
            if p.role == role:
                none.append(p.name)
            continue
        bosses = [f"{(r.get('encounter') or {}).get('name', '?')} {r['rankPercent']:.0f}"
                  + (f" as {spaced(r['spec'])}" if r.get("spec") and r["spec"] != p.spec else "")
                  for r in z.get("rankings") or [] if r.get("rankPercent") is not None]
        rows.append((z["bestPerformanceAverage"], f"{p.label}: {summary}. Best per boss: {', '.join(bosses)}"))
    lines = [f"{ROLE_NAMES[role].capitalize()} of report {rep.code} in {raid}, {DIFFICULTIES[d]}, ranked as on the "
             f"guild's rankings page on Warcraft Logs: each boss's best kill this season as a {ROLE_NAMES[role][:-1]} "
             f"spec ({metric} percentile against the same spec, from any report, pugs too), and the average of those "
             f"over the bosses they killed. Only this report's raiders; the page also lists alts and past members."]
    lines += [f"{n}. {row}" for n, (_, row) in enumerate(sorted(rows, key=lambda r: -r[0]), 1)]
    if none:
        lines.append(f"No ranked {DIFFICULTIES[d]} kill as a {ROLE_NAMES[role][:-1]}: " + ", ".join(none))
    return "\n".join(lines)
