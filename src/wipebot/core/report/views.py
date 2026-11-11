"""What the views draw: the page of Wipebot's views (app/, an MCP App in Claude and ChatGPT, the bot's pictures) and
each view's data, computed from the same facts as its tool's text so the two can't disagree.

A tool with a view returns a small key ({"view": ..., "report": ..., "pull": ...}); the page asks for the view's
data with it (`view_data`), and draws it. The replay viewer's data is maps.replay.
"""
import asyncio
import dataclasses
import functools
import hashlib
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from importlib import resources
from typing import Any

from ..classes import class_hex
from ..knowledge import KnowledgeError, matches_boss, spaced
from ..pulls import (
    DIFFICULTIES,
    FIRST_DEATHS,
    NO_KILLING_BLOW,
    by_phase,
    is_reset,
    pct_text,
    phase_health,
    phase_label,
    wipe_pct,
    wipe_pcts,
    wipe_text,
)
from ..text import clock, day, ordinal, plural, short
from . import adds, mana, mechanics, tables, versus
from . import note as notes
from .base import ROLE_NAMES, Player, PullRef, Report, alive_after, pull_window
from .build import hero_of
from .casting import alive, cast_counts, casting, raid_pauses, without
from .coaching import (
    BEST_MIN_S,
    LOST_MIN_S,
    OPENER_SHARE,
    NightCooldowns,
    TimeLost,
    best_pulls,
    dead_spans,
    death_note,
    night_cooldowns,
    night_pulls,
    night_trend,
    resource_use,
    row_cooldowns,
    time_lost,
)
from .cooldowns import NOT_ON_COOLDOWN, came_back, cooldown_use, prepull
from .deaths import (
    BURST_MS,
    NOT_ENOUGH,
    RECAP_WINDOW_MS,
    defensive_state,
    if_pressed,
    killed_outright,
    replay_deaths,
    savers,
)
from .ending import (
    KILL,
    QUIET_ENDS,
    RESET,
    Ending,
    HalfDead,
    KeptBuff,
    MassDeath,
    kind_of_end,
    mass_death,
    pull_ending,
    pull_endings,
)
from .fight import DAMAGE_BUCKET_MS, boss_casts, raid_cooldown_casts, raid_damage, tracks
from .meter import Meter
from .night import BossNight, kill_rankings, wipe_thirds
from .ranks import CAPPED_HEALING, SHORT_PULL, Spread, kill_parses, kills_spread, metric_of
from .top import cast_comparison

APP = resources.files("wipebot.core").joinpath("app")
# The page's parts, in the order they're joined: the bridge to the host first, then what draws, then the views.
SCRIPTS = ["bridge.js", "kit.js", "pull.js", "player.js", "night.js", "death.js", "raidnight.js", "meter.js",
           "trend.js", "replay.js", "note.js", "versus.js", "mana.js", "main.js"]
# The drawings only the Discord bot posts, as pictures (viewhost's Painter): in Claude and ChatGPT no tool's result
# names them and no view opens them, so the page those load leaves their script out (a fifth of it)
PICTURES_ONLY = frozenset({"note.js", "versus.js", "mana.js"})
HEALTH_STEP = 0.5  # boss health points closer than this many % to the last kept one are left out
# what view_data draws (the replay: maps.replay)
VIEWS = ("pull", "player", "player_night", "death", "night", "meter", "trend", "note", "versus", "mana")
# The tools whose rows a meter draws (meters_of); breakdown grouped by pull is a trend instead (trend_of), which
# mechanic_hits over every pull has too, a click away from its meter
METER_TOOLS = ("breakdown", "mechanic_hits", "enemies")


@functools.cache
def page(pictures: bool = False) -> str:
    """The views' page: one HTML file with its style and scripts inline, as MCP Apps hosts want it. It holds the
    views Claude and ChatGPT show; with `pictures`, the drawings only the Discord bot posts too (the Painter's)."""
    shell = APP.joinpath("page.html").read_text(encoding="utf-8")
    style = APP.joinpath("page.css").read_text(encoding="utf-8")
    script = "\n".join(APP.joinpath(name).read_text(encoding="utf-8") for name in SCRIPTS
                       if pictures or name not in PICTURES_ONLY)
    return shell.replace("/*STYLE*/", style).replace("/*SCRIPT*/", script)


def page_uri() -> str:
    """The address of the page Claude and ChatGPT load: hosts keep a page by its address, so a changed page gets a
    new one."""
    return f"ui://wipebot/views-{hashlib.sha256(page().encode()).hexdigest()[:10]}.html"


async def view_data(rep: Report, view: str, pull: PullRef = None, boss: str | None = None,
                    player: str | None = None, top: bool = False, death: int = 1, tool: str | None = None,
                    args: dict | None = None, nights: list[BossNight] | None = None, note: str = "",
                    source: str = "", other: Report | None = None) -> dict:
    """The data `view` draws, for the pull a tool was asked about (`pull` and `boss` resolve as the tool's did); the
    player view's `player`, and with `top` their casts against top players of their spec (compare_with_top's); the
    player's night view's `player` on `boss`; the death view's `player` and which of their deaths (`death`); the
    meter's `tool` and the `args` it was called with (against the `other` night's report, its `against`); the
    trend's `tool` and `args` too, with the `player` whose own row it adds (the one asking); the night view with the
    boss on each raid night (`nights`, progress's `weeks`); the raid note's view with the `note` checked against
    every pull on `boss` (raid_note's `args` with all_pulls), else the note as it is (where it came from,
    `source`)."""
    if view not in VIEWS:
        raise ValueError(f"no view {view!r}")
    if view == "note":  # over every pull on a boss; else, for a boss not pulled yet or the next raid, as it is
        if (args or {}).get("all_pulls"):
            return await note_view(rep, note, boss)
        return await note_plan_view(rep, note, boss or "", source)
    if view == "meter":
        return await meter_view(rep, tool or "", args or {}, other)
    if view == "trend":
        return await trend_view(rep, tool or "", args or {}, player)
    if view == "mana":
        return await mana_view(rep, pull, boss)
    if view == "versus":
        a = args or {}
        return await versus_view(rep, player or "", a.get("against") or "", pull, boss, bool(a.get("all_pulls")))
    if view not in ("pull", "night") and not player:
        raise ValueError(f"the {view} view needs a player")
    if view == "night":
        return await night_view(rep, rep.boss(boss), nights)
    if view == "player_night":
        return await player_night_view(rep, rep.boss(boss), player or "")
    f = rep.fight(pull, boss)
    if view == "pull":
        return await pull_view(rep, f)
    if view == "death":
        return await death_view(rep, f, player or "", death)
    return await player_view(rep, f, player or "", top)


async def pull_view(rep: Report, f: dict) -> dict:  # noqa: PLR0915 (one dict, field by field)
    """One pull at a glance: how it ended (in one sentence, the card's headline), the boss's health and the raid's
    damage taken over time, phases, the boss's casts, raid and healing cooldowns, Bloodlust and every death, as
    pull_summary and pull_timeline state them."""
    t0, length = f["startTime"], f["endTime"] - f["startTime"]
    deaths, raid, decided = await rep.deaths(f), await rep.raid_size(f), await rep.decided_at(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    casts, found = await boss_casts(rep, f)
    ended = await pull_ending(rep, f)
    health, kept = [], None
    for t, v in (await tracks(rep, f)).get("health") or []:
        if kept is None or abs(v - kept) >= HEALTH_STEP:
            health.append([round(t), round(v, 2)])
            kept = v
    damage = await raid_damage(rep, f, t0, f["endTime"]) or {}
    try:
        lusts = [round(at - t0) for at, _, _ in await rep.lusts(f)]
    except KnowledgeError:  # Bloodlust's debuffs come from a guide: the view does without
        lusts = []
    out_deaths = []
    for d, left in zip(deaths, alive_after(deaths, backs, raid), strict=True):
        name = rep.names.get(d["targetID"], "?")
        p = rep.players.get(name.lower())
        at = d["timestamp"] - t0
        out_deaths.append({"ms": round(at), "name": name, "spec": p.spec if p else "",
                           "color": class_hex(p.class_name) if p else "#9aa3b8",
                           "by": rep.ability(d.get("killingAbilityGameID")),
                           "icon": icon(d.get("killingAbilityGameID")),
                           "alive": left,
                           "fallout": bool(decided and at > decided.at_ms)})
    return {
        "view": "pull", "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f),
        "title": rep.label(f), "difficulty": DIFFICULTIES.get(f.get("difficulty"), ""),
        "url": rep.url(f), "kill": bool(f["kill"]), "live": bool(f.get("inProgress")), "duration_ms": round(length),
        "raid": raid, "best": await _best(rep, f),
        # how far the wipe got as pull_summary's first line states it (pulls.wipe_text: "62.6%", or on a boss
        # counted by phase "P3 0.8%" with, in `got_wcl`, Warcraft Logs' measure of the whole fight, what ranks wipes
        # as "best" compares them); health_end is the boss's health at the pull's end, where the health line ends,
        # written as that first line writes a %
        "got": None if f["kill"] or wipe_pct(f) is None else wipe_text(f, wcl=False),
        "got_wcl": pct_text(wipe_pct(f)) if phase_health(f) is not None else None,
        "health_end": pct_text(f["bossPercentage"]) if f.get("bossPercentage") is not None else None,
        "phase": phase_label(f) if phase_health(f) is not None else None,
        "ranking": (await kill_rankings(rep)).get(f["id"]) if f["kill"] else None,
        "headline": pull_headline(f, ended, len(deaths)),
        "decided": {"ms": round(decided.at_ms), "why": decided.why} if decided else None,
        "phases": [{"ms": round(t["startTime"] - t0), "id": t["id"], "label": rep.phase(f, t["id"]),
                    "name": rep.phase_names.get((f["encounterID"], t["id"]), "")}
                   for t in f.get("phaseTransitions") or []],
        "health": health,
        "damage": {"step_ms": DAMAGE_BUCKET_MS, "per_s": [[b * DAMAGE_BUCKET_MS, round(v)] for b, v in damage.items()],
                   "peaks": damage_peaks(damage)},
        "casts": [{"ms": round(c.at - t0), "name": c.name, "tracked": c.tracked, "icon": icon(found.ids.get(c.name))}
                  for c in casts],
        "cooldowns": [{"ms": round(at - t0), "kind": kind.split()[0], "name": name, "player": who}
                      for at, kind, name, who in await raid_cooldown_casts(rep, f)],
        "lusts": lusts,
        "deaths": out_deaths,
    }


PEAK = 2  # a raid damage peak: averages at this many times the pull's median or more, as pull_timeline marks them


def damage_peaks(damage: dict[int, float]) -> list[dict]:
    """The pull's raid damage peaks: runs of DAMAGE_BUCKET_MS averages at PEAK times the median or more (the median
    as pull_timeline takes it), each with its start, end and highest average per second."""
    if not damage:
        return []
    typical = sorted(damage.values())[len(damage) // 2]
    peaks: list[dict] = []
    for b, v in damage.items():
        if v < PEAK * typical:
            continue
        if peaks and peaks[-1]["to_ms"] == b * DAMAGE_BUCKET_MS:
            peaks[-1]["to_ms"] += DAMAGE_BUCKET_MS
            peaks[-1]["per_s"] = max(peaks[-1]["per_s"], round(v))
        else:
            peaks.append({"from_ms": b * DAMAGE_BUCKET_MS, "to_ms": (b + 1) * DAMAGE_BUCKET_MS, "per_s": round(v)})
    return peaks


async def _best(rep: Report, f: dict) -> dict | None:
    """How a wipe compares with the log's earlier wipes on the boss and the guild's earlier logs, as pull_summary's
    post marks it (pulls.best_marks): None for a kill, a reset, a pull still going or one without a boss %."""
    pct = wipe_pct(f)
    if f["kill"] or f.get("inProgress") or is_reset(f) or pct is None:
        return None
    earlier = wipe_pcts([g for g in rep.by_boss[rep.boss_key(f)]
                         if g.get("difficulty") == f.get("difficulty") and g["startTime"] < f["startTime"]])
    tonight, before = min(earlier, default=None), await rep.guild_best(f)
    return {"tonight": tonight, "new_tonight": tonight is not None and pct < tonight,
            "guild": {"pct": before.pct, "day": day(before.start_ms)} if before else None,
            "new_guild": before is not None and pct < min(before.pct, tonight if tonight is not None else before.pct)}


CASTS_SHOWN = 12  # a player's most cast spells in their view


async def player_view(rep: Report, f: dict, player: str, top: bool = False) -> dict:  # noqa: PLR0915
    """One player on one pull, as player_pull states it: their output and rank in their role, where it stands
    among the spec's ranked kills (a wipe, with why that proves little by itself) or their parse (a kill), the time
    they lost as its "Time lost" line lists it, each death with what its "Died" line notes of it, when they cast,
    sat at their resource's cap or were dead, their major cooldowns against possible uses and when one came back
    unused, their casts; with `top`, their casts per minute against top players of their spec (compare_with_top's
    casts) in their place. The card judges nothing the text doesn't: no note of its own on what went well or what
    to fix."""
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
    flow.gaps = without(flow.gaps, [(a, min(b, alive_until)) for a, b in pauses if a < alive_until])
    what = "healing" if p.role == "healer" else "damage"
    decided, table, (_, resource, capped), early, (boss, found) = await asyncio.gather(
        rep.decided_at(f), rep.table(f["id"], "Healing" if what == "healing" else "DamageDone"),
        resource_use(rep, f, p, casts, alive_s, alive_until), prepull(rep, [f], p), boss_casts(rep, f))
    try:
        hero = await hero_of(rep, p, det)
    except KnowledgeError:
        hero = None
    lost = time_lost(f, own, casts, flow.gaps, resource, capped, decided)
    here = rep.pull_players(f)
    peers = sorted((e for e in table["entries"] if (q := here.get(e.get("id"))) and q.role == p.role),
                   key=lambda e: -e.get("total", 0))
    mine = next((e for e in table["entries"] if e.get("id") == p.id), None)
    output = None
    if mine:
        output = {"what": what, "total": round(mine["total"]), "per_s_alive": round(mine["total"] / max(alive_s, 1)),
                  "rank": peers.index(mine) + 1 if mine in peers else None, "of": len(peers),
                  "role": ROLE_NAMES[p.role],
                  "top": [{"name": a["name"], "total": round(a["total"]), "icon": icon(a.get("guid"))}
                          for a in mine.get("abilities", [])[:6]]}
    standing = None
    if f["kill"]:
        if (pct := (await kill_parses(rep, f, metric_of(p))).get(p.name)) is not None:
            standing = {"kind": "parse", "percentile": pct}
    elif p.role != "tank" and output and alive_s >= max(BEST_MIN_S, (f["endTime"] - t0) / 1000 / OPENER_SHARE):
        spread = await kills_spread(rep, f, p)  # an opener is placed among no kills, as player_pull leaves it out
        if isinstance(spread, Spread) and spread.points:
            standing = {"kind": "kills", "n": spread.n, "exact": spread.exact,
                        "points": [[label, round(v)] for label, v in spread.points], "mine": output["per_s_alive"],
                        "caveat": SHORT_PULL + (f" {CAPPED_HEALING}" if p.role == "healer" else "")}
    try:
        rows, _ = await cooldown_use(rep, p, f, casts, early[f["id"]])
    except KnowledgeError:
        rows = []
    cut = t0 + decided.at_ms if decided else f["endTime"]

    def unused_from(sp: dict, times: list[float], possible: int | None) -> int | None:
        """When the cooldown came back, alive and before the wipe was decided, and wasn't used again (came_back)."""
        back = came_back(f, sp, times, possible, alive_until, cut)
        return round(back[0]) if back and back[1] else None
    counts = Counter({r["name"]: r["total"] for r in await cast_counts(rep, f, p)})  # as player_pull counts them
    ids = {rep.ability(c["abilityGameID"]): c["abilityGameID"] for c in casts}  # a spell's id, by its name
    span = lambda spans: [[round(a - t0), round(b - t0)] for a, b in spans]  # noqa: E731
    out = {
        "view": "player", "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f), "title": rep.label(f),
        "difficulty": DIFFICULTIES.get(f.get("difficulty"), ""), "url": rep.url(f), "kill": bool(f["kill"]),
        "duration_ms": round(f["endTime"] - t0),
        "player": {"name": p.name, "spec": spaced(p.spec), "class": spaced(p.class_name),
                   "color": class_hex(p.class_name), "role": p.role, "hero": hero, "ilvl": det.get("maxItemLevel")},
        "decided": {"ms": round(decided.at_ms), "why": decided.why} if decided else None,
        "output": output, "standing": standing,
        # the "Time lost" line's costs, biggest first, in its numbers: [seconds, what, share of the time]
        "lost": {"until_ms": round(lost.until_ms), "resource": resource,
                 "costs": [[f"{sec:.0f}", cost.partition(" (")[0], f"{100 * sec / max(lost.until_ms / 1000, 1):.0f}%"]
                           for sec, cost in lost.costs() if sec >= LOST_MIN_S]},
        "alive_s": round(alive_s), "alive_until_ms": round(alive_until - t0),
        "gaps": span(flow.gaps), "capped": span(capped), "dead": span(dead_spans(f, own, casts)),
        "died": await _died(rep, f, own),
        "cooldowns": [{"name": sp["name"], "uses": [round(t) for t in times], "possible": possible,
                       "ready_ms": unused_from(sp, times, possible), "icon": icon(sp.get("spell_id"))}
                      for sp, times, possible in rows if not NOT_ON_COOLDOWN & set(sp.get("tags") or [])],
        "casts": [{"name": n, "n": k, "per_min": round(60 * k / max(alive_s, 1), 1), "icon": icon(ids.get(n))}
                  for n, k in counts.most_common(CASTS_SHOWN)],
        "phases": [{"ms": round(t["startTime"] - t0), "id": t["id"]} for t in f.get("phaseTransitions") or []],
        "tracked": [{"ms": round(c.at - t0), "name": c.name, "icon": icon(found.ids.get(c.name))}
                    for c in boss if c.tracked],
        "top": None,
    }
    if top:
        out["top"] = await _against_top(rep, f, p, ids)
        if isinstance(out["top"], dict):
            out["casts"] = []  # the comparison is drawn in their place
    out["headline"] = player_headline(out, lost)
    return out


async def _died(rep: Report, f: dict, own: list[dict]) -> list[dict]:
    """A player's deaths on a pull, each with what player_pull's "Died" line notes of it in its brackets
    (coaching.death_note: nothing on a kill or a wipe never decided) and the two facts that note stands on."""
    t0, decided = f["startTime"], await rep.decided_at(f)
    return [{"ms": round(d["timestamp"] - t0), "by": rep.ability(d.get("killingAbilityGameID")),
             "note": await death_note(rep, f, d), "fallout": bool(decided and d["timestamp"] - t0 > decided.at_ms),
             "mass": bool(await mass_death(rep, f, d))} for d in own]


async def _against_top(rep: Report, f: dict, p, ids: dict[str, int]) -> dict | str:
    """The player's casts per minute against top players of their spec (top.cast_comparison), or why not."""
    c = await cast_comparison(rep, p.name, rep.pull_no(f), rep.boss_key(f))
    if isinstance(c, str):
        return c
    return {"hero": c.hero, "same_hero": c.same_hero, "parses": len(c.found),
            "rates": [{"name": r.name, "icon": icon(ids.get(r.name) or (c.names.named(r.name) or {}).get("spell_id")
                                                    or next((x.ids[r.name] for x in c.found if r.name in x.ids), None)),
                       "mine": round(r.mine, 1), "median": None if r.median is None
                       else round(r.median, 1), "low": r.low and round(r.low, 1), "high": r.high and round(r.high, 1),
                       "flag": r.flag} for r in c.rates],
            "total": [round(sum(v or 0 for v in c.my_cpm.values())),
                      round(statistics.median([sum(v or 0 for v in x.cpm.values()) for x in c.found]))]}


async def player_night_view(rep: Report, boss: str, player: str) -> dict:
    """One player across every pull on a boss, as player_night states it: per pull their output per second alive and
    rank in their role, their first death (its place among the pull's deaths, and whether it came after the wipe
    was decided) and their major cooldowns in the words of the pull's row; over the night, the deaths that counted,
    time dead, their best pull, and each cooldown as its text counts it: uses of possible uses, or, for one held for
    a moment of the fight, the pulls that got there and on how many it was used. No total over the cooldowns: the
    text states none."""
    p = rep.player(player)
    fights = [f for f in rep.pulls_with(p, rep.by_boss[boss]) if not f.get("inProgress")]
    if not fights:
        raise ValueError(f"{p.name}'s only pull on {boss} is still going")
    (pulls, skipped, why), replayed = await asyncio.gather(night_pulls(rep, p, fights), replay_deaths(rep, p, fights))
    cds = night_cooldowns(pulls, skipped, why)
    role = Counter(x.played.role for x in pulls).most_common(1)[0][0]
    ids = {sp["name"]: sp.get("spell_id") for x in pulls for sp, _, _ in x.cooldowns}
    rows: list[dict[str, Any]] = []
    for x in pulls:
        f, mine = x.f, x.mine
        death = None
        if x.death:
            k, d, _ = x.death
            kid = d.get("killingAbilityGameID")
            death = {"place": k + 1, "ms": round(d["timestamp"] - f["startTime"]),
                     "by": rep.ability(kid), "no_blow": not kid, "icon": icon(kid),
                     "fallout": x.fallout, "mass": x.mass}
        rows.append({
            "pull": rep.pull_no(f), "kill": bool(f["kill"]), "reset": is_reset(f), "wipe_pct": wipe_pct(f),
            # how far the wipe got in the words of the pull's row in the text, without and with Warcraft Logs' %
            "got": wipe_text(f, wcl=False), "got_wcl": wipe_text(f),
            "duration_ms": round(f["endTime"] - f["startTime"]), "role": x.played.role,
            "spec": spaced(x.played.spec), "per_s_alive": round(mine["total"] / max(x.alive_s, 1)) if mine else None,
            "rank": x.peers.index(mine) + 1 if mine and mine in x.peers else None, "of": len(x.peers),
            "death": death, "dead_s": round(x.dead_s, 1), "counted_s": round(x.counted_s, 1),
            "cooldowns": row_cooldowns(x, cds),
        })
    counted = [r for r in rows if r["death"] and not r["reset"] and not r["death"]["fallout"]
               and not r["death"]["mass"]]  # as NightPull.counted: a reset is no pull of the night's
    # each death before its wipe was decided, replayed with what was ready (the defensives tool's replay): True when a
    # ready defensive or a healthstone would have kept them alive, False when nothing ready would have, None can't tell
    # (the deaths the card counts: their first in each pull, a later one after a battle res left out, as above)
    replays = {(rep.pull_no(r.f), round(r.d["timestamp"] - r.f["startTime"])): r for r in replayed}
    saved_by: Counter = Counter()
    killers: dict[str, dict[str, Any]] = {}
    for r in counted:
        replay = replays.get((r["pull"], r["death"]["ms"]))
        more = replay.more if replay else []
        saved_by.update(savers(more))
        k = killers.setdefault(r["death"]["by"], {"by": r["death"]["by"], "no_blow": r["death"]["no_blow"],
                                                  "icon": r["death"]["icon"], "pulls": []})
        k["pulls"].append({"pull": r["pull"], "ms": r["death"]["ms"], "place": r["death"]["place"],
                           "saved": bool(savers(more)) if more else False if replay and replay.outright else None,
                           "outright": bool(replay and replay.outright)})
    judged = [x["saved"] for k in killers.values() for x in k["pulls"]]
    dead_s, counted_s = (sum(x.dead_s for x in pulls if not x.reset),  # unrounded and without resets, as the text
                         sum(x.counted_s for x in pulls if not x.reset))
    eligible = {rep.pull_no(x.f) for x in best_pulls(pulls, role)}  # as the text picks it
    best = max((r for r in rows if r["pull"] in eligible), key=lambda r: r["per_s_alive"], default=None)
    lived = sum(v is True for v in judged)
    return {
        "view": "player_night", "report": rep.code, "boss": boss, "title": f"{p.name} on {boss}",
        "url": rep.url(fights[0]).split("#")[0],
        "headline": player_night_headline(len(counted), sum(not r["reset"] for r in rows), lived),
        "player": {"name": p.name, "spec": spaced(p.spec), "class": spaced(p.class_name),
                   "color": class_hex(p.class_name), "role": role},
        "what": "healing" if role == "healer" else "damage", "role_name": ROLE_NAMES[role],
        "pulls": rows, "total_pulls": len([f for f in rep.by_boss[boss] if not f.get("inProgress")]),
        "died": len(counted), "first": sum(r["death"]["place"] == 1 for r in counted),
        "early": sum(r["death"]["place"] <= FIRST_DEATHS for r in counted), "early_n": FIRST_DEATHS,
        "dead_s": round(dead_s), "counted_s": round(counted_s), "dead_pct": round(100 * dead_s / max(counted_s, 1)),
        "best": {"pull": best["pull"], "per_s_alive": best["per_s_alive"]} if best else None,
        "killers": sorted(killers.values(), key=lambda k: (k["by"] == NO_KILLING_BLOW, -len(k["pulls"]))),
        "lived": {"saved": lived, "of": len(judged),
                  "unknown": sum(v is None for v in judged), "by": saved_by.most_common(),
                  "outright": sum(x["outright"] for k in killers.values() for x in k["pulls"])},
        "cooldowns": [_night_cooldown(cds, n) | {"icon": icon(ids.get(n))} for n in cds.used],
        "cooldowns_unavailable": cds.unavailable,
        "thirds": [{"pulls": [rows[i]["pull"] for i in t.pulls], "died": t.died,
                    "rate": round(t.rate) if t.rate is not None else None} for t in night_trend(pulls, role) or []],
    }


def _night_cooldown(cds: NightCooldowns, name: str) -> dict:
    """A cooldown over a player's night as player_night counts it (coaching.night_cooldowns): `count` and what it
    counts (`of`), and both in one phrase (`said`). One held for a moment of the fight (coaching.Held) has no uses
    against possible uses: the pulls that got to the moment, and on how many of them it was used."""
    used, held = cds.used[name], cds.held.get(name)
    if not used:
        return {"name": name, "count": "0", "of": "never cast on the night", "said": f"{name} never cast"}
    if held:
        there = f"pulls that got to {clock(held.at_ms)}"
        more = f"{plural(used, 'use')} over the night" if used != held.used else ""
        return {"name": name, "count": f"{held.used} of {held.reached}", "of": "; ".join(filter(None, [there, more])),
                "said": (f"{name} used on {held.used} of the {plural(held.reached, 'pull')} that got to "
                         f"{clock(held.at_ms)}" + (f" ({more})" if more else ""))}
    said = cds.text(name, used, cds.possible[name])  # "Deathmark 20/31", with its note where there are more uses
    if name in cds.unknown:  # a cooldown Lorrgs gives no length for: its uses alone
        return {"name": name, "count": str(used), "of": "uses", "said": said}
    return {"name": name, "count": f"{used}/{cds.possible[name]}", "of": "uses of possible", "said": said}


async def night_view(rep: Report, boss: str, nights: list[BossNight] | None = None) -> dict:
    """The raid's night on a boss, as progress states it: each pull's boss health left, phase, length, what ended it
    (pull_summary's "Ended by") and first death; the night's best, the guild's best before it, how the pulls ended
    and the killers of each pull's first deaths, the trend in thirds; and the night's other bosses. With `nights`
    (progress's `weeks`), the boss on each raid night: its pulls, the kill or the best wipe and the phase it got to,
    and what ended most pulls."""
    fights = rep.by_boss[boss]
    await rep.decided_cuts(fights)
    before = await rep.guild_best(fights[0])
    endings = await pull_endings(rep, fights)
    rows, firsts, best = [], Counter(), None
    for f, e in zip(fights, endings, strict=True):
        deaths = await rep.deaths(f)
        enrages = await rep.enrages(f)
        cutoff = min((at for _, at in enrages), default=None)
        early = [d for d in deaths if cutoff is None or d["timestamp"] - f["startTime"] < cutoff]
        for d in early[:FIRST_DEATHS]:
            firsts[rep.ability(d.get("killingAbilityGameID"))] += 1
        pct = 0.0 if f["kill"] else wipe_pct(f)
        new = (not f["kill"] and not is_reset(f) and not f.get("inProgress") and pct is not None
               and (best is None or pct < best))
        # the night's first wipe is its best until then by being first: unmarked, as in progress's text (night._marks)
        starred = new and best is not None
        if new:
            best = pct
        first = deaths[0] if deaths else None
        rows.append({"pull": rep.pull_no(f), "pct": pct, "kill": bool(f["kill"]), "reset": is_reset(f),
                     "live": bool(f.get("inProgress")), "duration_ms": round(f["endTime"] - f["startTime"]),
                     "phase": phase_label(f), "health": phase_health(f), "ended": kind_of_end(e), "best": starred,
                     "first": {"name": rep.names.get(first["targetID"], "?"),
                               "by": rep.ability(first.get("killingAbilityGameID")),
                               "ms": round(first["timestamp"] - f["startTime"])} if first else None})
    ends = Counter(r["ended"] for r in rows if not r["live"])
    thirds = wipe_thirds(wipe_pcts(fights))
    return {
        "view": "night", "report": rep.code, "boss": boss, "title": f"{boss}, {day(rep.start_ms)}",
        "headline": night_headline(rows, ends.most_common()),
        "difficulty": DIFFICULTIES.get(fights[0].get("difficulty"), ""), "url": rep.url(fights[0]).split("#")[0],
        "combat_ms": round(sum(f["endTime"] - f["startTime"] for f in fights)), "pulls": rows,
        "guild_best": {"pct": before.pct, "day": day(before.start_ms), "pull": before.pull_no,
                       "phase": before.phase, "health": before.health} if before else None,
        "phased": by_phase(fights),
        "ends": ends.most_common(), "firsts": firsts.most_common(6), "first_n": FIRST_DEATHS,
        "thirds": [round(t, 1) for t in thirds] if thirds else None,
        "bosses": [{"boss": b, "pulls": len(fs), "kill": any(f["kill"] for f in fs),
                    "best": min(wipe_pcts(fs), default=None)} for b, fs in rep.by_boss.items()],
    } | ({"weeks": [weeks_row(n) for n in nights]} if nights is not None else {})


def weeks_row(n: BossNight) -> dict:
    """One raid night on the boss, as progress's `weeks` states it."""
    top = next(((what, k) for what, k in n.ends if what not in (KILL, RESET)), None)
    return {"day": day(n.start_ms), "start_ms": round(n.start_ms), "report": n.code, "pulls": n.pulls,
            "kill": n.kill, "pct": n.best[0] if n.best else None, "pull": n.best[1] if n.best else None,
            "phase": n.best_phase, "health": n.best_health, "ended": list(top) if top else None}


async def death_view(rep: Report, f: dict, player: str, death: int = 1) -> dict:
    """One death, as death_recap states it, from the same answers: the hits in the window before it with their
    health after each, the healing they received per second, the killing blow and whether it killed outright, their
    defensives then and what a ready one or a healthstone would have changed, and who died within BURST_MS."""
    p = rep.player(player)
    rep.require_in(f, p)
    deaths = await rep.deaths(f)
    own = [d for d in deaths if d["targetID"] == p.id]
    if not own:
        raise ValueError(f"{p.name} did not die on {rep.label(f)}")
    if not 1 <= death <= len(own):
        raise ValueError(f"{p.name} died {len(own)} time(s) on {rep.label(f)}")
    p = rep.in_pull(f, p)
    d = own[death - 1]
    t0, at = f["startTime"], d["timestamp"]
    hits, heals, casts, (stones, _), decided = await asyncio.gather(
        rep.events(f["id"], "DamageTaken", sourceID=p.id, hostilityType="Friendlies", startTime=at - RECAP_WINDOW_MS,
                   endTime=at + 1, includeResources=True),
        rep.events(f["id"], "Healing", targetID=p.id, startTime=at - RECAP_WINDOW_MS, endTime=at + 1),
        rep.casts(f, p, until=at), rep.potions(f, p, until=at), rep.decided_at(f))
    try:
        spells = await rep.defensive_spells(p)
    except KnowledgeError:
        spells = None
    per_s: Counter = Counter()
    for e in heals:
        if e["type"] == "heal":
            per_s[(e["timestamp"] - t0) // 1000 * 1000] += e.get("amount", 0)
    defs = None
    if spells is not None:
        defs = []
        for sp in spells:
            kind, last = defensive_state(rep, casts, sp, at)
            defs.append({"name": sp["name"], "icon": icon(sp.get("spell_id")), "state": kind,
                         "last_ms": None if last is None else round(last - t0), "cooldown_s": sp.get("cooldown")})
    hp = lambda h: (round(100 * h["hitPoints"] / h["maxHitPoints"])  # noqa: E731
                    if h.get("resourceActor") == 2 and h.get("maxHitPoints") else None)
    kid = d.get("killingAbilityGameID")
    killer = lambda x: rep.ability(k) if (k := x.get("killingAbilityGameID")) else NO_KILLING_BLOW  # noqa: E731
    outright = bool(kid and killed_outright(rep, f, d, hits))
    replayed = await if_pressed(rep, f, p, d, hits)
    fallout = bool(decided and at - t0 > decided.at_ms)
    last_hp = next((v for h in reversed(hits) if (v := hp(h)) is not None), None)
    return {
        "view": "death", "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f), "title": rep.label(f),
        "headline": death_headline(killer(d), at - t0, outright, last_hp, replayed, fallout),
        "difficulty": DIFFICULTIES.get(f.get("difficulty"), ""), "url": rep.url(f),
        "player": {"name": p.name, "spec": spaced(p.spec), "class": spaced(p.class_name),
                   "color": class_hex(p.class_name), "role": p.role},
        "death": death, "deaths": len(own), "at_ms": round(at - t0), "window_ms": RECAP_WINDOW_MS,
        "by": killer(d), "no_blow": not kid, "icon": icon(kid),
        "fallout": fallout, "outright": outright,
        "hits": [{"ms": round(h["timestamp"] - t0), "by": rep.ability(h["abilityGameID"]),
                  "icon": icon(h["abilityGameID"]), "amount": h.get("amount", 0), "absorbed": h.get("absorbed", 0),
                  "overkill": h.get("overkill", 0), "hp": hp(h)} for h in hits],
        "healing": [[ms, round(v)] for ms, v in sorted(per_s.items())],
        "defensives": defs, "stones": stones,
        "saved_by": savers(replayed),
        "with": [{"name": rep.names.get(x["targetID"], "?"), "ms": round(x["timestamp"] - t0),
                  "by": killer(x)}
                 for x in deaths if x is not d and abs(x["timestamp"] - at) <= BURST_MS],
    }


NOTE_FEW = 3  # a kick assignment or callout fewer pulls than this reached isn't drawn (nor, in the view, a cooldown)


async def note_view(rep: Report, text: str, boss: str | None) -> dict:
    """The raid note against every pull on the boss, as raid_note with all_pulls counts it: each kick assignment's
    casts pull by pull (kicked by its player, covered by another, went through, its player dead), the pulls whose
    wipe followed casts that went through and whose cast went through first; each planned cooldown pull by pull (on
    time, early, late, not cast, dead); each callout's players pull by pull (pressed what answers it, only something
    else, none, dead). Kick assignments and callouts fewer than NOTE_FEW pulls reached are left out."""
    night = await notes.check_night(rep, text, boss)
    f0 = night.fights[0]
    def color(name: str) -> str:
        p = rep.find_player(name)
        return class_hex(p.class_name) if p else "#9aa3b8"
    return {"view": "note", "report": rep.code, "boss": rep.boss_key(f0), "url": rep.url(f0).split("#")[0],
            "title": f"{rep.boss_key(f0)}: the raid note",
            "kicks": _note_kicks(rep, night, color), "cooldowns": _note_cooldowns(rep, night, color),
            "callouts": _note_callouts(rep, night, color)}


def _note_kicks(rep: Report, night: notes.Night, color: Callable[[str], str]) -> dict | None:
    """Each kick assignment ("Kick #1 left", in the note's order) with its player, and per pull its casts in order:
    kicked, covered (by whom), went through, dead or not in the pull. The pulls before the note's kick order came in
    are drawn but not counted, as raid_note counts them."""
    start = night.change.start if night.change else 0
    reached: dict[str, set[int]] = defaultdict(set)
    for i, c in enumerate(night.checks):
        for k in c.kicks:
            reached[k.callout.text].add(i)
    kept = {t for t, pulls in reached.items() if len(pulls) >= NOTE_FEW}
    rows = [(i, c, k) for i, c in enumerate(night.checks) for k in c.kicks if k.callout.text in kept]
    if not rows:
        return None
    first_at: dict[str, float] = {}
    who: dict[str, Counter] = defaultdict(Counter)
    for i, _, k in rows:
        first_at[k.callout.text] = min(first_at.get(k.callout.text, k.at), k.at)
        if i >= start:
            who[k.callout.text][k.player] += 1
    slots = sorted(first_at, key=lambda s: first_at[s])

    def cell(k: notes.KickCheck) -> dict:
        at, how, by = k.cast
        if k.state != "alive":
            state = "dead" if k.state.startswith("dead") else "absent"
        elif k.kicked:
            state = "kicked"
        elif how == "kicked":
            state = "covered"
        else:
            state = "through"
        return {"ms": round(at), "state": state, "player": k.player, "how": how, "by": by or ""}
    out_pulls: list[dict] = []
    first: Counter = Counter()
    made = alive = 0
    for i, c in enumerate(night.checks):
        before = notes.through_before(c) if i >= start else []
        lead = before[0] if before and before[0].state == "alive" else None
        if lead:
            first[lead.player] += 1
        cells = {s: [] for s in slots}
        for k in sorted((k for k in c.kicks if k.callout.text in kept), key=lambda k: k.at):
            cells[k.callout.text].append(cell(k) | {"lead": k is lead})
            if i >= start and k.state == "alive":
                alive += 1
                made += k.kicked
        out_pulls.append({"pull": rep.pull_no(c.f), "counted": i >= start, "lost": bool(before),
                          "cells": [cells[s] for s in slots]})
    return {"slots": [{"text": s, "player": (n := who[s].most_common(1)[0][0] if who[s] else ""),
                       "color": color(n) if n else "#9aa3b8"} for s in slots],
            "from_pull": rep.pull_no(night.checks[start].f) if start else None,
            "fewer": len(reached) - len(kept),
            "made": made, "alive": alive, "lost": sum(p["lost"] for p in out_pulls if p["counted"]),
            "counted": sum(p["counted"] for p in out_pulls),
            "first": [{"name": n, "count": k, "color": color(n)}
                      for n, k in sorted(first.items(), key=lambda i: (-i[1], i[0]))],
            "pulls": out_pulls}


def _note_cooldowns(rep: Report, night: notes.Night, color: Callable[[str], str]) -> dict | None:
    """Each planned cooldown (player, spell, its time in the note) and per pull that reached it before the wipe was
    decided: on time (within 1 s), early or late (by how much), not cast within raid_note's window while alive, or
    dead."""
    uses = [(c, u) for c in night.checks if c.planned for u in c.planned.uses]
    if not uses:
        return None
    key = lambda u: (u.cooldown.player, u.cooldown.spell, u.cooldown.phase, u.cooldown.at)  # noqa: E731
    planned: dict[tuple, notes.PlannedUse] = {}
    for _, u in sorted(uses, key=lambda cu: cu[1].at):
        planned.setdefault(key(u), u)
    order = list(planned)
    by_pull: dict[int, dict[tuple, dict]] = defaultdict(dict)
    for c, u in uses:
        if u.cast_at is not None:
            off = (u.cast_at - u.at) / 1000
            state = "on time" if abs(off) < 1 else "early" if off < 0 else "late"
            cell = {"state": state, "off_s": round(off, 1)}
        else:
            cell = {"state": "not cast" if u.state == "alive" else "dead" if u.state.startswith("dead") else "absent"}
        by_pull[c.f["id"]][key(u)] = cell | {"ms": round(u.at)}
    return {"uses": [{"player": (u := planned[k]).cooldown.player, "spell": rep.ability(u.cooldown.spell),
                      "icon": icon(u.cooldown.spell), "phase": u.cooldown.phase, "at_s": u.cooldown.at,
                      "color": color(u.cooldown.player)} for k in order],
            "pulls": [{"pull": rep.pull_no(c.f), "cells": [by_pull[c.f["id"]].get(k) for k in order]}
                      for c in night.checks]}


def _note_callouts(rep: Report, night: notes.Night, color: Callable[[str], str]) -> dict | None:
    """Each callout for personals (in the note's order) that NOTE_FEW pulls reached, and per player it names and pull,
    a square per time it came: they pressed a defensive, none, were dead, or pressed none but aren't counted (they
    pressed the cooldown the note plans for them then, or a tank kept one for just after); as raid_note counts them.
    The callouts the log can't check aren't drawn."""
    groups: dict[tuple[str, tuple[str, ...]], list[tuple[int, notes.CalloutCheck]]] = defaultdict(list)
    for i, c in enumerate(night.checks):
        for k in c.callouts:
            if k.in_pull:
                groups[(k.callout.text, k.callout.players)].append((i, k))
    out = []
    for (text, named), rows in groups.items():
        if len({i for i, _ in rows}) < NOTE_FEW:
            continue
        cells: dict[str, list[list[str]]] = defaultdict(lambda: [[] for _ in night.checks])
        for i, k in sorted(rows, key=lambda r: r[1].at):
            for n, _ in k.pressed:
                cells[n][i].append("answered")
            for n, _ in k.idle:
                cells[n][i].append("none")
            for n, _ in k.excused:
                cells[n][i].append("excused")
            for n in k.dead:
                cells[n][i].append("dead")
        count = {n: (sum(s == "answered" for p in ps for s in p), sum(s in ("answered", "none") for p in ps for s in p))
                 for n, ps in cells.items()}
        players = sorted(cells, key=lambda n: (count[n][0] / max(count[n][1], 1), n))
        out.append({"text": text, "to": " ".join(named) or notes.EVERYONE,
                    "times": sorted({clock(k.at) for _, k in rows}),
                    "rows": [{"player": n, "color": color(n), "made": count[n][0], "alive": count[n][1],
                              "cells": cells[n]} for n in players]})
    if not out:
        return None
    return {"pulls": [rep.pull_no(c.f) for c in night.checks], "callouts": out}


async def versus_view(rep: Report, player: str, other: str, pull: PullRef, boss: str | None, all_pulls: bool) -> dict:
    """Two raid mates side by side, as compare_with_top with `against` states it: output per second alive, time alive,
    deaths and item level, and per spell (most output first) casts per minute alive, average hit, crit share and
    share of their output, with what's far apart."""
    v = await versus.versus(rep, player, other, pull, boss, all_pulls)
    f0 = v.fights[0]
    def side(s: versus.Side) -> dict:
        return {"name": s.p.name, "spec": f"{spaced(s.p.spec)} {spaced(s.p.class_name)}",
                "color": class_hex(s.p.class_name), "per_s": round(s.per_s), "alive_s": round(s.alive_s),
                "deaths": s.deaths, "ilvl": round(s.ilvl)}
    def spell(s: versus.Side, n: str) -> dict | None:
        sp = s.spells.get(n)
        if not sp:
            return None
        return {"cpm": None if sp.cpm is None else round(sp.cpm, 1),
                "per_hit": round(sp.per_hit) if sp.per_hit else None,
                "crit": None if sp.crit_share is None else round(sp.crit_share, 2),
                "share": round(sp.total / s.output, 3) if s.output else 0}
    one = len(v.fights) == 1
    return {"view": "versus", "report": rep.code, "boss": rep.boss_key(f0), "metric": v.metric,
            "url": rep.url(f0) if one else rep.url(f0).split("#")[0], "pull": rep.pull_no(f0) if one else None,
            "pulls": len(v.fights), "title": f"{v.a.p.name} against {v.b.p.name}", "a": side(v.a), "b": side(v.b),
            "spells": [{"name": n, "icon": (v.a.spells.get(n) or v.b.spells.get(n) or versus.Spell()).icon or None,
                        "a": spell(v.a, n), "b": spell(v.b, n), "far": v.far_off(n)} for n in v.order]}


MANA_STEP = 2  # mana points closer than this many % to the last kept one are left out


async def mana_view(rep: Report, pull: PullRef, boss: str | None) -> dict:
    """The healers' mana over a pull until the wipe was decided and the druids' Innervates, as healing with `mana`
    states them: each healer's mana, when they were low and died; each Innervate cast (on whom, at what mana) and
    when it sat ready while a healer was low."""
    m = await mana.mana(rep, pull, boss)
    pm = m.pulls[0]
    f = pm.f
    def line(points: list[tuple[float, float]]) -> list[list[float]]:
        kept: list[list[float]] = []
        for t, v in points:
            if not kept or abs(v - kept[-1][1]) >= MANA_STEP or t == points[-1][0]:
                kept.append([round(t), round(v, 1)])
        return kept
    def color(p) -> str:
        return class_hex(p.class_name)
    return {"view": "mana", "report": rep.code, "boss": rep.boss_key(f), "pull": rep.pull_no(f), "url": rep.url(f),
            "title": f"{rep.label(f)}: healers' mana", "until_ms": round(pm.until_ms), "decided": pm.decided,
            "low": mana.LOW, "cooldown_s": m.cooldown_s, "never": m.never,
            "healers": [{"name": h.p.name, "spec": f"{spaced(h.p.spec)} {spaced(h.p.class_name)}", "color": color(h.p),
                         "points": line(h.points), "dead_ms": None if h.dead_ms is None else round(h.dead_ms),
                         "lowest": h.lowest and [round(h.lowest[0]), round(h.lowest[1])],
                         "low": [[round(a), round(b)] for a, b in h.low()]} for h in pm.healers],
            "innervates": [{"druid": i.druid.name, "color": color(i.druid),
                            "casts": [{"ms": round(t), "on": who, "pct": None if v is None else round(v)}
                                      for t, who, v in i.casts],
                            "ready_low": [{"from_ms": round(a), "to_ms": round(b), "who": who}
                                          for a, b, who in i.ready_low]} for i in pm.innervates]}


PLAN_PAD_S = 15  # the note's timeline runs this far past its last entry in each phase
ROLE_ORDER = {"tank": 0, "healer": 1, "dps": 2}


async def note_plan_view(rep: Report, text: str, boss: str, source: str = "") -> dict:
    """The raid note as it is, for a boss not pulled yet or the next raid (raid_note's `upcoming`): each player it
    names with their jobs (the callouts to them and the groups they're in) and, on the fight's timeline phase by
    phase, their planned cooldowns and callouts; the callouts to everyone above. Players by role, then name."""
    found = notes.parse(text)
    n = next((x for x in found if matches_boss(boss, x.boss) or matches_boss(x.boss, boss)), found[0])
    await rep.name_spells({c.spell for c in n.cooldowns})
    timed = [*n.cooldowns, *n.callouts]
    phases = sorted({c.phase for c in timed}) or [1]
    items: dict[str, list[dict]] = defaultdict(list)
    said: dict[str, list[str]] = defaultdict(list)  # the callouts to each player, in order
    groups: dict[str, list[dict]] = defaultdict(list)  # the groups each is in: {heading, label}
    everyone: list[dict] = []
    for c in sorted(n.cooldowns, key=lambda c: (c.phase, c.at)):
        items[c.player].append({"phase": c.phase, "at_s": c.at, "spell": rep.ability(c.spell), "icon": icon(c.spell)})
    for o in sorted(n.callouts, key=lambda o: (o.phase, o.at)):
        item = {"phase": o.phase, "at_s": o.at, "text": o.text}
        if not o.players:
            everyone.append(item)
        for name in o.players:
            items[name].append(item)
            if o.text not in said[name]:
                said[name].append(o.text)
    for heading, labels in n.groups.items():
        for label, names in labels.items():
            for name in names:  # a group a callout to them already says (a kick's order) adds nothing
                if label not in said[name]:
                    groups[name].append({"heading": heading, "label": "" if label.lower() in heading.lower()
                                         else label})
    def row(name: str) -> dict:
        p = rep.find_player(name)
        return {"player": name, "color": class_hex(p.class_name) if p else "#9aa3b8", "role": p.role if p else "",
                "callouts": said.get(name, []), "groups": groups.get(name, []),
                "items": sorted(items.get(name, []), key=lambda i: (i["phase"], i["at_s"]))}
    rows = sorted((row(name) for name in {*items, *groups}),
                  key=lambda r: (ROLE_ORDER.get(r["role"], 3), r["player"].lower()))
    return {"view": "note", "report": rep.code, "boss": n.boss, "difficulty": n.difficulty, "source": source,
            "title": f"{n.boss}: the raid note",
            "plan": {"phases": [{"phase": p, "length_s": max(c.at for c in timed if c.phase == p) + PLAN_PAD_S
                                 if timed else PLAN_PAD_S} for p in phases],
                     "everyone": everyone, "rows": rows}}


@dataclasses.dataclass
class Stated:
    """What a meter's tool states around its rows and a Meter has no field for, handed to the page as stated."""
    # breakdown by player (tables.stated): its heading's total (0: none stated), and by player their share of it as
    # their row writes it ("13%") and, for one in only some of the pulls, in how many and their rate over those
    total: float = 0
    rows: dict[str, tuple[str, str]] = dataclasses.field(default_factory=dict)
    # mechanic_hits' "Total" line: the deaths with it as the killing blow, and of any cause soon after a hit
    deaths: tuple[int, int] | None = None


async def meters_of(rep: Report, tool: str, args: dict, other: Report | None = None) -> list[Meter]:
    """The meters a tool's answer ranks players in, from its own rows and in its text's order: breakdown by player
    (damage, healing, damage taken, kicks, dispels), mechanic_hits for one ability, enemies' damage on the most
    damaged kind of add. None for another call: a table by ability or pull, one player's part, every ability's
    outliers, too few players."""
    return [m for m, _ in await _ranked(rep, tool, args, other)]


async def _ranked(rep: Report, tool: str, args: dict, other: Report | None = None) -> list[tuple[Meter, Stated]]:
    """meters_of, each meter with what its tool states around its rows (Stated)."""
    a = args
    match tool:
        case "breakdown" if a.get("group_by", "player") == "player" and not a.get("player"):
            asked = (a.get("data_type", ""), a.get("pulls"), a.get("boss"), a.get("ability"), a.get("cutoff"),
                     bool(a.get("whole_pull")), a.get("start"), a.get("end"))
            found = await tables.meter(rep, *asked, bool(a.get("least")), a.get("target"))
            return [(found, Stated(*await tables.stated(rep, *asked, a.get("target"))))] if found else []
        case "mechanic_hits" if a.get("ability") and other is not None:  # against another night (`other`)
            found = await mechanics.meter_against(rep, other, a["ability"], a.get("boss"), bool(a.get("whole_pull")),
                                                  a.get("start"), a.get("end"))
            return [(found, Stated())] if found else []
        case "mechanic_hits" if a.get("ability"):
            asked = (a["ability"], a.get("pull"), a.get("boss"), bool(a.get("all_pulls")), bool(a.get("whole_pull")),
                     a.get("start"), a.get("end"))
            if (found := await mechanics.meter(rep, *asked)) is None:
                return []
            h, order, killed_by, died_after = await mechanics.ranking(rep, *asked)
            return [(_as_its_text_ranks(rep.names, found, h, order), Stated(deaths=(killed_by, died_after)))]
        case "enemies":
            # Each player's damage on every kind of add summed (adds.EVERY_KIND) is a number enemies' text states
            # nowhere: the picture is the list it does state, who damaged the most damaged kind over the pull. Its
            # waves (`by_wave`) are the text's alone, and the picture says it isn't one of them
            whole = ", the whole pull, not by wave" if a.get("by_wave") and not a.get("all_pulls") else ""
            return [(dataclasses.replace(m, scope=m.scope + whole), Stated())
                    for m in await adds.meters(rep, a.get("pull"), a.get("boss"), bool(a.get("all_pulls")))
                    if m.title != adds.EVERY_KIND]
    return []


PER_MINUTE_THERE = " per minute of the pulls they were in"  # what a night's mechanic is ranked by, after "hits"


def _as_its_text_ranks(names: dict[int, str], m: Meter, h: mechanics.Hits,
                       order: list[tuple[int, float, int | None]]) -> Meter:
    """mechanics.meter's bars as mechanic_hits' text lists its players (mechanics.ranking): in its order, each bar's
    amount what that order is by (their hits, or the debuff's applications where those count who it caught; over
    every pull, per minute of the pulls they were in; for an ability that only ticks, the ticks its order falls to),
    and in the bar's few words the row's other numbers as the text writes them, the damage among them. `names`: the
    players' by id."""
    drawn = {b.name: b for b in m.bars}
    only_ticks = not any(rate for _, rate, _ in order)
    counted = "ticks" if only_ticks else "applications" if h.by_debuff else "hits"
    bars = []
    for who, rate, pulls in order:
        hits = plural(h.hits[who], "hit") if h.hits[who] or counted == "hits" else ""
        hits += f" (+{plural(h.ticks[who], 'tick')})" if hits and h.ticks[who] and not only_ticks else ""
        if pulls is None:  # one pull: the bar's own amount isn't said twice
            said = [plural(h.ticks[who], "tick") if h.ticks[who] else ""] if counted == "hits" else [hits]
        else:
            there = f"in {plural(pulls, 'pull')}"
            said = ([f"{hits} {there}"] if counted == "hits"
                    else [f"{plural(h.debuffs[who], 'application')} {there}", hits] if counted == "applications"
                    else [there])
        note = ", ".join(x for x in (*said, short(h.damage[who]) if h.damage[who] else "") if x)
        bars.append(dataclasses.replace(drawn[names[who]], value=float(h.ticks[who]) if only_ticks else rate,
                                        note=note))
    night = order and order[0][2] is not None and not only_ticks
    return dataclasses.replace(m, bars=bars, unit=counted + (PER_MINUTE_THERE if night else ""))


def _change(now: float, was: float) -> str:
    """'+26%', '-3%', 'same'; 'new' against none: a change as mechanic_hits' `against` writes it."""
    if not was:
        return "new"
    pct = round((now / was - 1) * 100)
    return f"{pct:+d}%" if pct else "same"


def meter_amount(m: Meter, v: float) -> str:
    """An amount of a meter as its tool writes it: damage and healing short ("53.83M"), a count whole, a count per
    minute to two places ("2.14")."""
    if m.unit == "amount":
        return short(v)
    if "per minute" in m.unit:  # against another night damage, or a debuff's applications (a few a minute)
        return short(v) if m.unit == "per minute" and not m.scope.startswith("debuff") else f"{v:.2f}"
    return f"{v:.0f}"


async def meter_view(rep: Report, tool: str, args: dict, other: Report | None = None) -> dict:
    """A tool's ranking of players as in-game damage meters draw it (meters_of): per meter each player's amount,
    their class colour, spec and role, and what the tool's text states around them, as it states it (Stated): the
    total, its rate and the combat time of breakdown's heading, each row's share of it and "in N of M pulls", a
    mechanic's deaths. Nothing here is summed over the bars. With it the ranking in one sentence (the card's
    headline); and, for one over several pulls that its tool also states pull by pull, the call whose trend shows
    it (`by_pull`: the trend view's tool and args)."""
    found = await _ranked(rep, tool, args, other)
    if not found:
        raise ValueError(f"{tool} ranks no players here")
    players = {p.name: p for p in rep.players.values()}

    def written(m: Meter, v: float | None) -> dict:
        """An amount the page's own short form would get wrong, written here: a rate of a few a minute as the text
        writes it, and a median that falls between two counts ("2.5" kicks), which the page would round to a count
        the headline's "times the median" doesn't fit."""
        if v is None or m.unit == "amount":
            return {}
        if "per minute" in m.unit:
            return {"text": meter_amount(m, v)} if m.unit != "per minute" or m.scope.startswith("debuff") else {}
        return {"text": f"{v:.1f}"} if v % 1 else {}

    def bar(m: Meter, said: Stated, b) -> dict:
        p = players.get(b.name)
        share, there = said.rows.get(b.name, ("", ""))
        return {"name": b.name, "value": b.value, "note": ", ".join(x for x in (b.note, there) if x),
                "share": share if said.total else "", "died_after": b.died_after, "killed_by": b.killed_by,
                "color": class_hex(p.class_name) if p else "#9aa3b8", "spec": spaced(p.spec) if p else "",
                "role": p.role if p else ""} | written(m, b.value) | (
                    {"per_pull": b.per_pull, "died_in": b.died_in} if b.per_pull else {}) | (
                    {"was": b.was, "was_rank": b.was_rank, "change": _change(b.value, b.was)}
                    | {f"was_{k}": v for k, v in written(m, b.was).items()} if b.was is not None else {})

    def then(t) -> dict:
        """The other night's numbers, with the raid's change and the deaths per minute as `against` writes them."""
        return dataclasses.asdict(t) | {
            "change": _change(t.raid, t.raid_was),
            "deaths_per_min": [f"{n / max(minutes, 1e-9):.2f}"
                               for n, minutes in ((t.deaths_was, t.minutes_was), (t.deaths, t.minutes))]}

    def one(m: Meter, said: Stated) -> dict:
        median = statistics.median(b.value for b in m.bars)
        return {"title": m.title, "scope": m.scope, "unit": m.unit, "seconds": round(m.seconds, 3),
                "icon": icon(m.ability), "note": m.note, "others": m.others, "headline": meter_headline(m, said),
                # the heading's own: "(0:10 of combat; total 14.83M, 1.48M/s)"
                "stated": {"total": short(said.total), "over": clock(m.seconds * 1000),
                           "per_s": f"{short(said.total / m.seconds)}/s" if m.seconds else ""} if said.total else None,
                "median": median, "bars": [bar(m, said, b) for b in m.bars]} | (
                    {f"median_{k}": v for k, v in written(m, median).items()}) | (
                    {"deaths": {"killed_by": said.deaths[0], "after": said.deaths[1]}} if said.deaths else {}) | (
                    {"within_s": mechanics.HIT_THEN_DEATH_MS // 1000} if tool == "mechanic_hits" else {}) | (
                    {"pulls": m.pulls, "per_pull": m.per_pull} if m.pulls else {}) | (
                    {"then": then(m.then)} if m.then else {}) | ({"least": True} if m.least else {})
    by_pull = await _by_pull_call(rep, tool, args)
    return {"view": "meter", "report": rep.code, "boss": rep.boss(args.get("boss")), "tool": tool, "args": args,
            "title": found[0][0].title,
            "url": rep.url(rep.fight(args.get("pull"), args.get("boss"))).split("#")[0],
            "meters": [one(m, said) for m, said in found]} | ({"by_pull": by_pull} if by_pull else {})


async def _by_pull_call(rep: Report, tool: str, args: dict) -> dict | None:
    """The call whose trend shows a ranking pull by pull, when its tool states one over these pulls (trend_of):
    mechanic_hits over every pull as it was called; breakdown by player grouped by pull instead (without `least`,
    which ranks players). None for one pull, or a table no trend draws."""
    if tool == "mechanic_hits" and args.get("ability") and args.get("all_pulls") and not args.get("against"):
        return {"tool": tool, "args": args}
    if tool != "breakdown" or args.get("data_type") not in TREND_TYPES:
        return None
    by_pull = {k: v for k, v in args.items() if k != "least"} | {"group_by": "pull"}
    pulls = (await _by_pull(rep, by_pull, read=False))[0].fights
    return {"tool": tool, "args": by_pull} if len(pulls) >= TREND_MIN_PULLS else None


def meter_headline(m: Meter, said: Stated | None = None) -> dict:
    """A ranking in one sentence of its tool's rows: who is first (with `least`, last), with how much, for one in
    only some of the pulls in how many as their row says it (`said`), and how that stands against the median of the
    players ranked; against another night, the raid's per minute both nights and the change."""
    def amount(v: float) -> str:
        return meter_amount(m, v) + ("" if m.unit in ("amount", "per minute") else f" {m.unit}")
    if m.then:
        t = m.then
        change = _change(t.raid, t.raid_was).replace("same", "the same")  # the one its tile and its text state
        return _headline(f"The raid's {m.scope.split(' per minute')[0]} from {m.title} per minute of its pulls: "
                         f"{amount(t.raid_was)} on {t.was_day}, {amount(t.raid)} on {t.day} ({change}).")
    b = m.bars[-1 if m.least else 0]
    there = said.rows.get(b.name, ("", ""))[1] if said else ""
    with_ = f"{amount(b.value)}{f', {there}' if there else ''}"
    median = statistics.median(x.value for x in m.bars)
    place = "last" if m.least else "first"
    if not median:
        return _headline(f"{b.name} is {place} of the {len(m.bars)} players ranked with {with_}.")
    against = f"{100 * b.value / median:.0f}% of" if m.least else f"{b.value / median:.1f} times"
    return _headline(f"{b.name} is {place} with {with_}, {against} the median of the {len(m.bars)} players ranked.")


# ---- a trend by pull: one amount pull by pull over a night ----

# The tables breakdown states by pull that a trend draws, and what each sums in the headline's words. The others
# have no amount per pull to compare: an aura's uptimes, a kick's counts by cast.
TREND_TYPES = {"DamageDone": "damage", "Healing": "healing", "DamageTaken": "damage taken", "Casts": "casts"}
TREND_TITLES = {"Casts": "Casts"}  # a table's title where the meters (tables.METERS) have none
TREND_MIN_PULLS = 2  # one pull is no trend
THIRDS_MIN = 6  # fewer counted pulls have no thirds, as player_night's and progress's (coaching.night_trend)
TREND_TOP = 3  # each pull's biggest rows named, as breakdown by pull names them


@dataclasses.dataclass
class Trend:
    """One amount pull by pull, as its tool states it: breakdown grouped by pull (each pull's total over its counted
    time), or mechanic_hits over every pull (the raid's hits or debuff applications in each)."""
    title: str  # "Healing by pull", "Tempest by pull"
    scope: str  # the pulls: "all 33 Sszorak pulls, each until its wipe was decided"
    subject: str  # what a pull's amount is, in a sentence: "healing", "Tempest hits"
    per: str  # what a pull's amount is divided by to compare pulls of different lengths: "second" or "minute"
    count: bool  # a count (casts, hits), not an amount
    fights: list[dict]
    amounts: list[float]  # each pull's
    seconds: list[float]  # each pull's counted time
    # each pull's amount by name: every player's in the pull (0 without any), or one player's abilities
    rows: list[dict[str, float]]
    player: Player | None = None  # one player's trend, else the raid's
    ability: int | None = None  # the ability's id, for its icon

    def rate(self, amount: float, seconds: float) -> float:
        return amount / seconds * (60 if self.per == "minute" else 1) if seconds else 0.0

    def text(self, rate: float) -> str:
        return f"{rate:.1f}" if self.count else short(rate)


async def trend_of(rep: Report, tool: str, args: dict) -> Trend | None:
    """The trend a tool's answer states pull by pull: breakdown grouped by pull for damage, healing, damage taken
    and casts (TREND_TYPES), mechanic_hits for one ability over every pull. None for another call, or fewer than
    TREND_MIN_PULLS pulls."""
    if tool == "breakdown" and args.get("group_by") == "pull" and args.get("data_type") in TREND_TYPES:
        return await _table_trend(rep, args)
    if tool == "mechanic_hits" and args.get("ability") and args.get("all_pulls") and not args.get("against"):
        return await _mechanic_trend(rep, args)
    return None


async def _by_pull(rep: Report, a: dict, read: bool = True) -> tuple[tables.Asked, list[tuple[list[tuple], float]]]:
    """breakdown grouped by pull for its arguments (tables.by_pull): what it is over (the pulls that got to its
    window, the table and its heading's words) and, with `read`, each pull's rows and counted seconds."""
    return await tables.by_pull(rep, a["data_type"], a.get("pulls"), a.get("boss"), a.get("player"), a.get("ability"),
                                a.get("cutoff"), bool(a.get("whole_pull")), a.get("start"), a.get("end"),
                                a.get("target"), read)


async def _table_trend(rep: Report, a: dict) -> Trend | None:
    """breakdown grouped by pull: each pull's rows, total and counted time, as the tool's own reading gives them
    (tables.by_pull; a test holds these to the text's lines). A table lists only the players with some of the amount:
    the others in the pull get a row of none, so that none is told from not being there."""
    q, found = await _by_pull(rep, a)  # the tables its text read: nothing more is fetched
    if len(q.fights) < TREND_MIN_PULLS:
        return None
    amounts, seconds, rows = [], [], []
    for f, (merged, counted) in zip(q.fights, found, strict=True):
        seconds.append(counted)
        amounts.append(sum(v for _, v, _ in merged))
        here = {} if q.player else {p.name: 0.0 for p in rep.pull_players(f).values()}
        rows.append(here | {name: v for name, v, _ in merged})
    what = TREND_TYPES[q.data_type]
    subject = (what if not q.ability else f"{what} from {q.ability}" if q.data_type == "DamageTaken"
               else f"{q.ability} {what}") + (f" on {q.target}" if q.target else "")
    title = ((tables.METERS.get(q.data_type) or TREND_TITLES[q.data_type]) + (f": {q.ability}" if q.ability else "")
             + (f" on {q.target}" if q.target else "") + " by pull")
    scope = q.scope + q.window + (", each until its wipe was decided" if q.cut else "") + (
        f", each until its {ordinal(q.cutoff)} death" if q.cutoff else "")
    return Trend(title, scope, subject, "minute" if q.data_type == "Casts" else "second", q.data_type == "Casts",
                 q.fights, amounts, seconds, rows, q.player, q.ids[0] if q.ability else None)


async def _mechanic_trend(rep: Report, a: dict) -> Trend | None:
    """mechanic_hits over every pull: the raid's hits in each pull (its debuff's applications, for one that lands
    more often than it deals damage), as the tool's per-pull line counts them, over each pull's time until its wipe
    was decided (mechanics.night_of's minutes), and each player's. The pulls are the tool's: a reset is none."""
    night = await mechanics.night_of(rep, a["ability"], a.get("boss"), bool(a.get("whole_pull")), a.get("start"),
                                     a.get("end"))
    h, fights = night.h, night.fights
    if len(fights) < TREND_MIN_PULLS or not (h.hits or h.debuffs):
        return None
    cut = {} if a.get("whole_pull") else await rep.decided_cuts(fights)
    window = pull_window(a.get("start"), a.get("end"))
    per = h.pull_debuffs if h.by_debuff else h.pull_hits
    seconds, rows = [], []
    for f in fights:
        span = min(cut.get(f["id"], f["endTime"]), f["endTime"]) - f["startTime"]
        if window:
            span = max(0.0, min(span, window[1]) - window[0])
        seconds.append(span / 1000)
        rows.append({p.name: 0.0 for p in rep.pull_players(f).values()}  # in the pull and never hit: none
                    | {rep.names[who]: float(k) for (fid, who), k in per.items() if fid == f["id"]})
    name = h.name or a["ability"]
    left_out = rep.attempts(rep.by_boss[rep.boss(a.get("boss"))])[1]  # " (1 reset left out)", as the tool's text
    scope = f"all {plural(len(fights), fights[0]['name'] + ' pull')}{left_out}"
    if window:
        scope += f", {clock(window[0])}-{clock(window[1]) if window[1] < float('inf') else 'end'} of each"
    return Trend(f"{name} by pull", scope + (", each until its wipe was decided" if cut else ""),
                 f"{name} {'debuff applications' if h.by_debuff else 'hits'}", "minute", True, fights,
                 [sum(r.values()) for r in rows], seconds, rows, ability=h.ability_id)


def trend_thirds(rates: list[float], resets: list[bool]) -> list[tuple[list[int], float]] | None:
    """The pulls in thirds, first, middle and last, each with its median rate: (the third's pulls, as indexes; the
    median). Resets are left out, and fewer than THIRDS_MIN counted pulls have none, as in player_night's trend
    (coaching.night_trend), whose median this is too: the upper of two middle ones."""
    counted = [i for i, reset in enumerate(resets) if not reset]
    if len(counted) < THIRDS_MIN:
        return None
    third = len(counted) // 3
    out = []
    for part in (counted[:third], counted[third:-third], counted[-third:]):
        found = sorted(rates[i] for i in part)
        out.append((part, found[len(found) // 2]))
    return out


async def trend_view(rep: Report, tool: str, args: dict, mark: str | None = None) -> dict:
    """A tool's amount pull by pull (trend_of): each pull's total, its counted time, the amount per second or minute
    of it, how the pull ended and its biggest rows; the pulls in thirds with each third's median, and the trend in
    one sentence (the card's headline). With `mark` (the player asking) on the raid's trend, their own amounts pull
    by pull beside it when they have any: none in a pull they were in without any, no amount for a pull they sat
    out."""
    t = await trend_of(rep, tool, args)
    if t is None:
        raise ValueError(f"{tool} states nothing pull by pull here")
    rates = [t.rate(v, s) for v, s in zip(t.amounts, t.seconds, strict=True)]
    resets = [is_reset(f) for f in t.fights]
    thirds = trend_thirds(rates, resets)
    counted = sorted(r for r, reset in zip(rates, resets, strict=True) if not reset)
    mine = rep.players.get((mark or "").lower()) if not t.player else None
    if mine and not any(row.get(mine.name) for row in t.rows):  # none of it all night: nothing to draw
        mine = None
    own = [t.rate(row[mine.name], s) if mine.name in row else None
           for row, s in zip(t.rows, t.seconds, strict=True)] if mine else []
    own_thirds = trend_thirds([r or 0.0 for r in own],  # the pulls they weren't in left out, as a reset is
                              [reset or r is None for r, reset in zip(own, resets, strict=True)]) if mine else None
    nos = [rep.pull_no(f) for f in t.fights]
    # a count's rate to the one decimal it is written with (Trend.text), so that the page, which rounds what it is
    # given, never writes 20.3 under a headline's 20.2 (20.249 handed on as 20.25)
    places = 1 if t.count else 2
    pulls = [{"pull": no, "kill": bool(f["kill"]), "reset": reset, "pct": 0.0 if f["kill"] else wipe_pct(f),
              "seconds": round(s, 3), "amount": round(v, 1), "rate": round(r, places),
              "top": [[n, round(x, 1)] for n, x in sorted(row.items(), key=lambda kv: (-kv[1], kv[0]))[:TREND_TOP]
                      if x]}
             | ({"mine": round(mined, places) if (mined := own[i]) is not None else None} if mine else {})
             for i, (no, f, reset, s, v, r, row) in enumerate(zip(nos, t.fights, resets, t.seconds, t.amounts, rates,
                                                                  t.rows, strict=True))]

    def third_rows(found: list[tuple[list[int], float]] | None) -> list[dict] | None:
        return [{"pulls": [nos[i] for i in part], "rate": round(rate, places)}
                for part, rate in found] if found else None

    def who(p: Player) -> dict:
        return {"name": p.name, "color": class_hex(p.class_name), "spec": spaced(p.spec)}
    return {"view": "trend", "report": rep.code, "boss": rep.boss_key(t.fights[0]), "tool": tool, "args": args,
            "title": t.title, "scope": t.scope, "subject": t.subject, "per": t.per, "count": t.count,
            "who": who(t.player) if t.player else None, "icon": icon(t.ability),
            "url": rep.url(t.fights[0]).split("#")[0],
            "headline": trend_headline(t, nos, rates, thirds),
            "pulls": pulls, "thirds": third_rows(thirds),
            "median": round(counted[len(counted) // 2], places) if counted else None,
            "mine": who(mine) | {"thirds": third_rows(own_thirds)} if mine else None}


TREND_LISTED = 5  # a trend over this many pulls or fewer has its headline name each pull's


def trend_headline(t: Trend, nos: list[int], rates: list[float],
                   thirds: list[tuple[list[int], float]] | None) -> dict:
    """A trend in one sentence of its tool's numbers: the median pull of each third of the night, first, middle and
    last (trend_thirds); over fewer pulls, each pull's own. A plain fact: whether more is better depends on what is
    counted."""
    head = f"{t.player.name if t.player else 'The raid'}'s {t.subject} per {t.per}"
    if thirds:
        a, b, c = (t.text(rate) for _, rate in thirds)
        n = sum(len(part) for part, _ in thirds)
        return _headline(f"{head}: {a} in the first third of the {n} pulls, {b} in the middle and {c} in the last "
                         "(each third's median pull).")
    if len(nos) <= TREND_LISTED:
        return _headline(f"{head}: " + ", ".join(f"{t.text(r)} on pull {no}" for no, r in zip(nos, rates, strict=True))
                         + ".")
    found = sorted(rates)
    return _headline(f"{head}: {t.text(found[len(found) // 2])} on the median of the {len(nos)} pulls.")


def icon(spell_id: int | str | None) -> str | None:
    """A spell's icon, by the path the views load it at (<img base>spells/<id>.jpg, maps.asset); None without one."""
    try:
        return f"spells/{int(spell_id)}.jpg" if spell_id else None
    except (TypeError, ValueError):
        return None


def icons(data: object) -> set[str]:
    """Every icon a view's data names (its "icon" fields), for the server to serve those alone."""
    found: set[str] = set()
    if isinstance(data, dict):
        if isinstance(data.get("icon"), str):
            found.add(data["icon"])
        for v in data.values():
            found |= icons(v)
    elif isinstance(data, list):
        for v in data:
            found |= icons(v)
    return found


def _headline(text: str, kind: str = "") -> dict:
    """A card's headline: one sentence of what the view's tool states, written here so that no card and no model
    words it. `kind` is the mark it is drawn with (kit.js MARK): "good" what went well, "fix" what to do
    differently, "mind" what changes how the rest reads; "" a plain fact."""
    return {"text": text, "kind": kind}


def pull_headline(f: dict, e: Ending | None, deaths: int) -> dict:
    """How the pull ended, in one sentence: pull_summary's "Ended by" (ending.ending_text) led by what that leads
    with, without the ability's ids and what to look up; a kill by its length and its deaths."""
    if e is None:
        return _headline(f"Killed in {clock(f['endTime'] - f['startTime'])} "
                         + (f"with {plural(deaths, 'death')}." if deaths else "without a death."))
    match e.kind:
        case "live":
            return _headline(f"Still going: {clock(e.end_ms)} logged so far.")
        case "reset":
            return _headline("A reset: the boss untouched.")
        case "cascade":
            return _headline(f"Deaths one after another from {clock(e.first_death_ms or 0)} until half the raid was "
                             f"dead at {clock(e.half_dead_ms or 0)}, no single hit killing many.")
        case "alive":
            return _headline(f"Ended at {clock(e.end_ms)} with {e.alive_at_end} of {e.raid} alive: no death "
                             "decided it.")
        case "cast":
            c = e.cast
            assert c is not None
            what = (f"The boss cast {c.cast} at {clock(c.cast_ms)} and {c.debuff}" if c.cast and c.cast != c.debuff
                    else f"The boss cast {c.cast} at {clock(c.cast_ms)} and it" if c.cast else f"The boss's {c.debuff}")
            return _headline(f"{what} landed on {c.hit} of the {c.alive} alive at {clock(c.at_ms)}, as the pull "
                             f"ended with {e.alive_at_end} of {e.raid} alive.")
    match e.moments[0]:
        case MassDeath(at_ms=at, ability=name, killed=n, span_s=span, waves=True) if name:
            text = f"{name} killed {n} in pulses within {span} s from {clock(at)}"
        case MassDeath(at_ms=at, ability="", killed=n, span_s=span):
            text = f"{n} died at once within {span} s at {clock(at)}, no killing blow logged"
        case MassDeath(at_ms=at, ability=name, killed=n, span_s=span):
            text = f"{name} killed {n} within {span} s at {clock(at)}"
        case KeptBuff(at_ms=at, name=name, alive=alive):
            text = f"The boss gained {name} at {clock(at)} and kept it to the end, with {alive} of {e.raid} alive"
        case HalfDead(at_ms=at):
            text = f"Half the raid was dead at {clock(at)}"
    if e.lead_in and e.led_in:  # as many as a hit killing many takes: the deaths lead, as in "Ended by"
        then = text if isinstance(e.moments[0], MassDeath) and e.moments[0].ability else text[0].lower() + text[1:]
        text = f"{e.lead_in.deaths} deaths one after another from {clock(e.lead_in.first_ms)}, then {then}"
    elif e.lead_in:
        text += f", after {e.lead_in.deaths} deaths one after another from {clock(e.lead_in.first_ms)}"
    return _headline(text + ".")


def death_headline(by: str, at_ms: float, outright: bool, last_hp: int | None, replayed: list[str],
                   fallout: bool) -> dict:
    """What a death comes down to, in one sentence of death_recap's facts: that it killed outright (no defensive's
    to stop); that it came after the wipe was decided (fallout, nothing of theirs to fix); what was ready and would
    have kept them alive (if_pressed's lines, `replayed`); that nothing ready would have been enough (every one of
    those lines says so); else the killing blow and when."""
    if outright:
        return _headline(f"{by} killed them outright from {last_hp}% health: it wasn't damage that outpaced their "
                         "healing.", "mind")
    how = f"Died at {clock(at_ms)}, {by}" if by == NO_KILLING_BLOW else f"Killed by {by} at {clock(at_ms)}"
    if fallout:
        return _headline(f"{how}, after the wipe was decided.")
    if saved := savers(replayed):
        ready = " or ".join(saved)
        return _headline(f"{ready[0].upper()}{ready[1:]}, used in time, would have kept them alive.", "fix")
    if len(replayed) > 1 and all(line.endswith(f": {NOT_ENOUGH}") for line in replayed[1:]):
        how += ": nothing ready would have been enough on its own"
    return _headline(how + ".")


def night_headline(rows: list[dict], ends: list[tuple[str, int]]) -> dict:
    """The raid's night on a boss in one sentence of progress's facts: the kill and on which pull, else what ended
    the most pulls (its "Ended by" counted over the pulls that ended, resets among them: the one count the card
    states), in progress's words for it after "ended with", which every one of them reads after. Where no mechanic
    ended a pull, the pulls as progress counts them."""
    ended = [r for r in rows if not r["live"]]
    kill = next((r for r in rows if r["kill"]), None)
    if kill:
        return _headline("Killed on the first pull." if len(rows) == 1
                         else f"Killed on pull {kill['pull']} of {len(rows)}.")
    top = next(((what, k) for what, k in ends if what not in QUIET_ENDS), None)
    if top and len(ended) == 1:
        return _headline(f"Pull {ended[0]['pull']} ended with {top[0]}.")
    if top:
        return _headline(f"{top[1]} of the {len(ended)} pulls ended with {top[0]}.")
    if not ended:
        return _headline(f"Pull {rows[-1]['pull']} is still going.")
    if all(r["reset"] for r in rows):
        return _headline(f"{plural(len(rows), 'pull')}, none past a reset.")
    counts = [f"{what} {k}" for what, k in ends]
    if live := len(rows) - len(ended):
        counts.append(f"still going {live}")
    return _headline(f"{plural(len(rows), 'pull')}: {'; '.join(counts)}.")  # one of them has a comma of its own


def player_headline(out: dict, lost: TimeLost) -> dict:
    """A player's pull in one sentence of player_pull's own: their first death that its "Died" line counts as theirs
    (not after the wipe was decided, not one of a hit killing many), in that line's words, with the time dead as
    its "Time lost" line states it; else their output and rank, its first line. No verdict, and nothing weighed by
    the card: the text does neither."""
    died = next((d for d in out["died"] if not d["fallout"] and not d["mass"]), None)
    if died:
        head = f"Died at {clock(died['ms'])}" + ("" if died["by"] == NO_KILLING_BLOW else f" to {died['by']}")
        if died["note"]:  # "before the wipe was decided": said only where the text says it
            head += f", {died['note']} at {clock(lost.until_ms)}"
        if lost.dead_s < LOST_MIN_S:
            return _headline(f"{head}.")
        window, of = lost.until_ms / 1000, "those" if lost.decided else "the pull's"
        return _headline(f"{head}: dead for {lost.dead_s:.0f} of {of} {int(window)} s "
                         f"({100 * lost.dead_s / max(window, 1):.0f}%).")
    o = out["output"]
    if not o:
        return _headline(f"No output logged in {clock(out['duration_ms'])}.")
    rank = f", rank {o['rank']} of {o['of']} {o['role']} by total" if o["rank"] else ""
    return _headline(f"{short(o['per_s_alive'])} {o['what']} per second over the {out['alive_s']} s alive{rank}.")


def player_night_headline(died: int, pulls: int, lived: int) -> dict:
    """A player's night on a boss in one sentence of player_night's facts: on how many pulls they died before the
    wipe was decided, and at how many of those deaths a ready defensive or a healthstone would have kept them
    alive (the defensives tool's replay)."""
    if not died:
        return _headline(f"Never died before a wipe was decided in {plural(pulls, 'pull')}.", "good")
    head = f"Died before the wipe was decided on {died} of {plural(pulls, 'pull')}"
    if not lived:
        return _headline(head + ".")
    at = f" at {lived} of those {died} deaths" if died > 1 else ""
    return _headline(f"{head}; a ready defensive or a healthstone would have kept them alive{at}.", "fix")
