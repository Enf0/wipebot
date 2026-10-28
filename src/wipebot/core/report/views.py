"""What the views draw: the page of Wipebot's views (app/, an MCP App in Claude and ChatGPT, the bot's pictures) and
each view's data, computed from the same facts as its tool's text so the two can't disagree.

A tool with a view returns a small key ({"view": ..., "report": ..., "pull": ...}); the page asks for the view's
data with it (`view_data`), and draws it. The replay viewer's data is maps.replay.
"""
import asyncio
import functools
import hashlib
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from importlib import resources
from typing import Any

from ..classes import class_hex
from ..knowledge import KnowledgeError, matches_boss, spaced
from ..pulls import DIFFICULTIES, FIRST_DEATHS, NO_KILLING_BLOW, is_reset, phase_label, wipe_pct, wipe_pcts
from ..text import clock, day, ordinal
from . import adds, awards, mana, mechanics, tables, versus
from . import note as notes
from .base import ROLE_NAMES, PullRef, Report, alive_after
from .build import hero_of
from .casting import alive, casting, raid_pauses, without
from .coaching import dead_spans, night_cooldowns, night_pulls, night_trend, resource_use, time_lost
from .cooldowns import NOT_ON_COOLDOWN, cooldown_use, prepull
from .deaths import BURST_MS, RECAP_WINDOW_MS, defensive_state, if_pressed, killed_outright, replay_deaths, savers
from .ending import HalfDead, KeptBuff, MassDeath, kind_of_end, mass_death_of, pull_ending
from .fight import DAMAGE_BUCKET_MS, boss_casts, raid_cooldown_casts, raid_damage, tracks
from .meter import Meter
from .night import BossNight, kill_rankings, wipe_thirds
from .ranks import Spread, kill_parses, kills_spread, metric_of
from .top import cast_comparison

APP = resources.files("wipebot.core").joinpath("app")
# The page's parts, in the order they're joined: the bridge to the host first, then what draws, then the views.
SCRIPTS = ["bridge.js", "kit.js", "pull.js", "player.js", "night.js", "death.js", "raidnight.js", "meter.js",
           "replay.js", "note.js", "versus.js", "mana.js", "main.js"]
HEALTH_STEP = 0.5  # boss health points closer than this many % to the last kept one are left out
# what view_data draws (the replay: maps.replay)
VIEWS = ("pull", "player", "player_night", "death", "night", "meter", "note", "versus", "mana")
METER_TOOLS = ("breakdown", "mechanic_hits", "enemies")  # the tools whose rows a meter draws (meters_of)


@functools.cache
def page() -> str:
    """The views' page: one HTML file with its style and scripts inline, as MCP Apps hosts want it."""
    shell = APP.joinpath("page.html").read_text(encoding="utf-8")
    style = APP.joinpath("page.css").read_text(encoding="utf-8")
    script = "\n".join(APP.joinpath(name).read_text(encoding="utf-8") for name in SCRIPTS)
    return shell.replace("/*STYLE*/", style).replace("/*SCRIPT*/", script)


def page_uri() -> str:
    """The page's address: hosts keep a page by its address, so a changed page gets a new one."""
    return f"ui://wipebot/views-{hashlib.sha256(page().encode()).hexdigest()[:10]}.html"


async def view_data(rep: Report, view: str, pull: PullRef = None, boss: str | None = None,
                    player: str | None = None, top: bool = False, death: int = 1, tool: str | None = None,
                    args: dict | None = None, nights: list[BossNight] | None = None, note: str = "",
                    source: str = "", with_awards: bool = False) -> dict:
    """The data `view` draws, for the pull a tool was asked about (`pull` and `boss` resolve as the tool's did); the
    player view's `player`, and with `top` their casts against top players of their spec (compare_with_top's); the
    player's night view's `player` on `boss`; the death view's `player` and which of their deaths (`death`); the
    meter's `tool` and the `args` it was called with; the night view with the boss on each raid night (`nights`,
    progress's `weeks`) and the night's awards (`with_awards`, progress's `awards`); the raid note's view with the
    `note` checked against every pull on `boss` (raid_note's `args` with all_pulls), else the note as it is (where it
    came from, `source`)."""
    if view not in VIEWS:
        raise ValueError(f"no view {view!r}")
    if view == "note":  # over every pull on a boss; else, for a boss not pulled yet or the next raid, as it is
        if (args or {}).get("all_pulls"):
            return await note_view(rep, note, boss)
        return await note_plan_view(rep, note, boss or "", source)
    if view == "meter":
        return await meter_view(rep, tool or "", args or {})
    if view == "mana":
        return await mana_view(rep, pull, boss)
    if view == "versus":
        a = args or {}
        return await versus_view(rep, player or "", a.get("against") or "", pull, boss, bool(a.get("all_pulls")))
    if view not in ("pull", "night") and not player:
        raise ValueError(f"the {view} view needs a player")
    if view == "night":
        return await night_view(rep, rep.boss(boss), nights, with_awards)
    if view == "player_night":
        return await player_night_view(rep, rep.boss(boss), player or "")
    f = rep.fight(pull, boss)
    if view == "pull":
        return await pull_view(rep, f)
    if view == "death":
        return await death_view(rep, f, player or "", death)
    return await player_view(rep, f, player or "", top)


async def pull_view(rep: Report, f: dict) -> dict:  # noqa: PLR0915 (one dict, field by field)
    """One pull at a glance: how it ended, the boss's health and the raid's damage taken over time, phases, the boss's
    casts, raid and healing cooldowns, Bloodlust and every death, as pull_summary and pull_timeline state them."""
    t0, length = f["startTime"], f["endTime"] - f["startTime"]
    deaths, raid, decided = await rep.deaths(f), await rep.raid_size(f), await rep.decided_at(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    casts, found = await boss_casts(rep, f)
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
        "boss_pct": f.get("bossPercentage"), "raid": raid, "best": await _best(rep, f),
        "ranking": (await kill_rankings(rep)).get(f["id"]) if f["kill"] else None,
        "ended": _ended(await pull_ending(rep, f)),
        "decided": {"ms": round(decided.at_ms), "why": decided.why} if decided else None,
        "phases": [{"ms": round(t["startTime"] - t0), "id": t["id"],
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
GAPS_SHARE = 0.05  # casting gaps over this share of their time until the wipe was decided are worth fixing
STEADY_SHARE = 0.02  # under this share, worth saying they kept casting
CAPPED_SHARE = 0.10  # their resource capped over this share of it
NOTES = 2  # things that went well, and things to fix, on a player's card


def player_notes(out: dict) -> dict[str, list[str]]:
    """What went well and what to fix on a player's pull, at most NOTES of each, the weightiest first, from the
    player view's facts: a death before the wipe was decided (not one in a hit killing many: that hit wiped the
    raid, whoever's job it was to stop it), casting gaps, a capped resource, a personal cooldown left unused, where
    they stand among ranked kills or their parse, their rank in their role. A raid cooldown left
    unused isn't one to fix: the log doesn't show whose job it was."""
    good: list[tuple[float, str]] = []
    fix: list[tuple[float, str]] = []
    lost, o, s = out["lost"], out["output"], out["standing"]
    until_s = max(lost["until_ms"] / 1000, 1)
    died = next((d for d in out["died"] if not d["fallout"] and not d.get("with")), None)
    if died:
        to = "" if died["by"] == NO_KILLING_BLOW else f" to {died['by']}"
        fix.append((100, f"Died at {clock(died['ms'])}{to}, before the wipe was decided"))
    if lost["capped_s"] >= CAPPED_SHARE * until_s:
        share = lost["capped_s"] / until_s
        fix.append((60 * share, f"{lost['resource']} capped for {round(lost['capped_s'])} s ({round(100 * share)}%)"))
    if lost["gaps_s"] >= GAPS_SHARE * until_s:
        share = lost["gaps_s"] / until_s
        fix.append((80 * share, f"{round(lost['gaps_s'])} s without casting ({round(100 * share)}%)"))
    elif lost["gaps_s"] < STEADY_SHARE * until_s and not died:
        good.append((30, "Never stopped casting" if lost["gaps_s"] < 1
                     else f"Kept casting: {round(lost['gaps_s'])} s of gaps in {clock(lost['until_ms'])}"))
    judged = [c for c in out["cooldowns"] if c["possible"]]
    missed = [c for c in judged if len(c["uses"]) < c["possible"]]
    for c in missed:
        if not c["raid"]:
            fix.append((25 * (c["possible"] - len(c["uses"])) / c["possible"],
                        f"{c['name']}: used {len(c['uses'])} of the {c['possible']} times it was ready"))
    if judged and not missed:
        good.append((25, "Used every major cooldown each time it was ready"))
    if s and s["kind"] == "parse":
        if s["percentile"] >= 75:
            good.append((40, f"{ordinal(s['percentile'])} percentile parse"))
        elif s["percentile"] < 25:
            fix.append((30, f"{ordinal(s['percentile'])} percentile parse"))
    elif s and s["kind"] == "kills" and s["exact"]:
        above = next((label for label, v in s["points"] if s["mine"] >= v), None)
        if above and above.startswith(("90th", "75th")):
            good.append((40, f"Above the {above} of ranked kills"))
        elif above is None and s["points"][-1][0].startswith("25th"):
            fix.append((30, f"Below the {s['points'][-1][0]} of ranked kills"))
    if o and o["rank"] == 1 and o["of"] > 1:
        good.append((35, f"Most {o['what']} of the {o['of']} {o['role']}"))
    pick = lambda xs: [t for _, t in sorted(xs, key=lambda x: -x[0])[:NOTES]]  # noqa: E731
    return {"good": pick(good), "fix": pick(fix)}


async def player_view(rep: Report, f: dict, player: str, top: bool = False) -> dict:  # noqa: PLR0915
    """One player on one pull, as player_pull states it: their output and rank in their role, where it stands
    among the spec's ranked kills (a wipe) or their parse (a kill), the time they lost, when they cast, sat at
    their resource's cap or were dead, their major cooldowns against possible uses, their casts; with `top`, their
    casts per minute against top players of their spec (compare_with_top's casts)."""
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
    decided, ended, table, (_, resource, capped), early, (boss, found) = await asyncio.gather(
        rep.decided_at(f), pull_ending(rep, f), rep.table(f["id"], "Healing" if what == "healing" else "DamageDone"),
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
    elif p.role != "tank" and output:
        spread = await kills_spread(rep, f, p)
        if isinstance(spread, Spread) and spread.points:
            standing = {"kind": "kills", "n": spread.n, "exact": spread.exact,
                        "points": [[label, round(v)] for label, v in spread.points], "mine": output["per_s_alive"]}
    try:
        rows, _ = await cooldown_use(rep, p, f, casts, early[f["id"]])
    except KnowledgeError:
        rows = []
    counts = Counter(rep.ability(c["abilityGameID"]) for c in casts)
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
        "lost": {"until_ms": round(lost.until_ms), "dead_s": round(lost.dead_s, 1), "gaps_s": round(lost.gaps_s, 1),
                 "capped_s": round(lost.capped_s, 1), "resource": resource},
        "alive_s": round(alive_s), "alive_until_ms": round(alive_until - t0),
        "gaps": span(flow.gaps), "capped": span(capped), "dead": span(dead_spans(f, own, casts)),
        "died": [{"ms": round(d["timestamp"] - t0), "by": (by := rep.ability(d.get("killingAbilityGameID"))),
                  "fallout": bool(decided and d["timestamp"] - t0 > decided.at_ms),
                  "with": m.killed if (m := mass_death_of(ended, d["timestamp"] - t0, by)) else 0} for d in own],
        "cooldowns": [{"name": sp["name"], "uses": [round(t) for t in times], "possible": possible,
                       "raid": "raid_cd" in (sp.get("tags") or []), "icon": icon(sp.get("spell_id"))}
                      for sp, times, possible in rows if not NOT_ON_COOLDOWN & set(sp.get("tags") or [])],
        "casts": [{"name": n, "n": k, "per_min": round(60 * k / max(alive_s, 1), 1), "icon": icon(ids.get(n))}
                  for n, k in counts.most_common(CASTS_SHOWN)],
        "phases": [{"ms": round(t["startTime"] - t0), "id": t["id"]} for t in f.get("phaseTransitions") or []],
        "tracked": [{"ms": round(c.at - t0), "name": c.name, "icon": icon(found.ids.get(c.name))}
                    for c in boss if c.tracked],
        "top": None,
    }
    out["notes"] = player_notes(out)
    if top:
        out["top"] = await _against_top(rep, f, p, ids)
    return out


async def _against_top(rep: Report, f: dict, p, ids: dict[str, int]) -> dict | str:
    """The player's casts per minute against top players of their spec (top.cast_comparison), or why not."""
    c = await cast_comparison(rep, p.name, rep.pull_no(f), rep.boss_key(f))
    if isinstance(c, str):
        return c
    return {"hero": c.hero, "same_hero": c.same_hero, "parses": len(c.found),
            "rates": [{"name": r.name, "icon": icon(ids.get(r.name) or (c.names.named(r.name) or {}).get("spell_id")),
                       "mine": round(r.mine, 1), "median": None if r.median is None
                       else round(r.median, 1), "low": r.low and round(r.low, 1), "high": r.high and round(r.high, 1),
                       "flag": r.flag} for r in c.rates],
            "total": [round(sum(v or 0 for v in c.my_cpm.values())),
                      round(statistics.median([sum(v or 0 for v in x.cpm.values()) for x in c.found]))]}


async def player_night_view(rep: Report, boss: str, player: str) -> dict:
    """One player across every pull on a boss, as player_night states it: per pull their output per second alive and
    rank in their role, their first death (its place among the pull's deaths, and whether it came after the wipe
    was decided), their major cooldowns used of possible; over the night, the deaths that counted, time dead, their
    best pull, and their cooldowns summed."""
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
        judged = [(sp, times, possible) for sp, times, possible in x.cooldowns
                  if not NOT_ON_COOLDOWN & set(sp.get("tags") or []) and possible is not None]
        death = None
        if x.death:
            k, d, _ = x.death
            death = {"place": k + 1, "ms": round(d["timestamp"] - f["startTime"]),
                     "by": rep.ability(d.get("killingAbilityGameID")), "icon": icon(d.get("killingAbilityGameID")),
                     "fallout": x.fallout, "mass": x.mass}
        rows.append({
            "pull": rep.pull_no(f), "kill": bool(f["kill"]), "reset": is_reset(f), "wipe_pct": wipe_pct(f),
            "duration_ms": round(f["endTime"] - f["startTime"]), "role": x.played.role,
            "spec": spaced(x.played.spec), "per_s_alive": round(mine["total"] / max(x.alive_s, 1)) if mine else None,
            "rank": x.peers.index(mine) + 1 if mine and mine in x.peers else None, "of": len(x.peers),
            "death": death, "dead_s": round(x.dead_s, 1), "counted_s": round(x.counted_s, 1),
            "cooldowns": [sum(min(len(t), n) for _, t, n in judged), sum(n for _, _, n in judged)],
        })
    counted = [r for r in rows if r["death"] and not r["death"]["fallout"] and not r["death"]["mass"]]
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
        k = killers.setdefault(r["death"]["by"], {"by": r["death"]["by"], "icon": r["death"]["icon"], "pulls": []})
        k["pulls"].append({"pull": r["pull"], "ms": r["death"]["ms"], "place": r["death"]["place"],
                           "saved": bool(savers(more)) if more else False if replay and replay.outright else None,
                           "outright": bool(replay and replay.outright)})
    judged = [x["saved"] for k in killers.values() for x in k["pulls"]]
    dead_s, counted_s = sum(x.dead_s for x in pulls), sum(x.counted_s for x in pulls)  # unrounded, as the text sums
    ranked = [r for r in rows if r["per_s_alive"] is not None and not r["reset"] and r["role"] == role]
    best = max(ranked, key=lambda r: r["per_s_alive"], default=None)
    return {
        "view": "player_night", "report": rep.code, "boss": boss, "title": f"{p.name} on {boss}",
        "player": {"name": p.name, "spec": spaced(p.spec), "class": spaced(p.class_name),
                   "color": class_hex(p.class_name), "role": role},
        "what": "healing" if role == "healer" else "damage", "role_name": ROLE_NAMES[role],
        "pulls": rows, "total_pulls": len([f for f in rep.by_boss[boss] if not f.get("inProgress")]),
        "died": len(counted), "first": sum(r["death"]["place"] == 1 for r in counted),
        "early": sum(r["death"]["place"] <= FIRST_DEATHS for r in counted), "early_n": FIRST_DEATHS,
        "dead_s": round(dead_s), "counted_s": round(counted_s), "dead_pct": round(100 * dead_s / max(counted_s, 1)),
        "cooldowns_total": [sum(r["cooldowns"][0] for r in rows), sum(r["cooldowns"][1] for r in rows)],
        "best": {"pull": best["pull"], "per_s_alive": best["per_s_alive"]} if best else None,
        "killers": sorted(killers.values(), key=lambda k: (k["by"] == NO_KILLING_BLOW, -len(k["pulls"]))),
        "lived": {"saved": sum(v is True for v in judged), "of": len(judged),
                  "unknown": sum(v is None for v in judged), "by": saved_by.most_common(),
                  "outright": sum(x["outright"] for k in killers.values() for x in k["pulls"])},
        "cooldowns": [{"name": n, "used": cds.used[n], "possible": cds.possible[n] if n not in cds.unknown else None,
                       "icon": icon(ids.get(n))} for n in cds.used],
        "cooldowns_unavailable": cds.unavailable,
        "thirds": [{"pulls": [rows[i]["pull"] for i in t.pulls], "died": t.died,
                    "rate": round(t.rate) if t.rate is not None else None} for t in night_trend(pulls, role) or []],
    }


async def night_view(rep: Report, boss: str, nights: list[BossNight] | None = None, with_awards: bool = False) -> dict:
    """The raid's night on a boss, as progress states it: each pull's boss health left, phase, length, what ended it
    (pull_summary's "Ended by") and first death; the night's best, the guild's best before it, how the pulls ended
    and the killers of each pull's first deaths, the trend in thirds; and the night's other bosses. With `nights`
    (progress's `weeks`), the boss on each raid night: its pulls, the kill or the best wipe and the phase it got to,
    and what ended most pulls. With `with_awards` (progress's `awards`), the night's awards."""
    fights = rep.by_boss[boss]
    await rep.decided_cuts(fights)
    before = await rep.guild_best(fights[0])
    endings = await asyncio.gather(*(pull_ending(rep, f) for f in fights))
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
        if new:
            best = pct
        first = deaths[0] if deaths else None
        rows.append({"pull": rep.pull_no(f), "pct": pct, "kill": bool(f["kill"]), "reset": is_reset(f),
                     "live": bool(f.get("inProgress")), "duration_ms": round(f["endTime"] - f["startTime"]),
                     "phase": phase_label(f), "ended": kind_of_end(e), "best": new,
                     "first": {"name": rep.names.get(first["targetID"], "?"),
                               "by": rep.ability(first.get("killingAbilityGameID")),
                               "ms": round(first["timestamp"] - f["startTime"])} if first else None})
    ends = Counter(r["ended"] for r in rows if not r["live"])
    thirds = wipe_thirds(wipe_pcts(fights))
    return {
        "view": "night", "report": rep.code, "boss": boss, "title": f"{boss}, {day(rep.start_ms)}",
        "difficulty": DIFFICULTIES.get(fights[0].get("difficulty"), ""), "url": rep.url(fights[0]).split("#")[0],
        "combat_ms": round(sum(f["endTime"] - f["startTime"] for f in fights)), "pulls": rows,
        "guild_best": {"pct": before.pct, "day": day(before.start_ms), "pull": before.pull_no} if before else None,
        "ends": ends.most_common(), "firsts": firsts.most_common(6), "first_n": FIRST_DEATHS,
        "thirds": [round(t, 1) for t in thirds] if thirds else None,
        "bosses": [{"boss": b, "pulls": len(fs), "kill": any(f["kill"] for f in fs),
                    "best": min(wipe_pcts(fs), default=None)} for b, fs in rep.by_boss.items()],
    } | ({"weeks": [weeks_row(n) for n in nights]} if nights is not None else {}) | (
        {"awards": await awards_rows(rep, boss)} if with_awards else {})


async def awards_rows(rep: Report, boss: str) -> list[dict]:
    """The night's awards on the boss, as progress's `awards` states them."""
    _, found = await awards.awards(rep, boss)
    def who(p) -> dict:
        return {"name": p.name, "color": class_hex(p.class_name), "spec": p.spec}
    return [{"title": a.title, "unit": a.unit, "winner": who(a.winner), "value": a.value, "said": awards.said(a)}
            | ({"runner_up": who(a.runner_up[0]) | {"value": a.runner_up[1], "said": awards.said(a, True, brief=True)}}
               if a.runner_up else {}) for a in found]


def weeks_row(n: BossNight) -> dict:
    """One raid night on the boss, as progress's `weeks` states it."""
    top = next(((what, k) for what, k in n.ends if what not in ("a kill", "a reset")), None)
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
    return {
        "view": "death", "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f), "title": rep.label(f),
        "difficulty": DIFFICULTIES.get(f.get("difficulty"), ""), "url": rep.url(f),
        "player": {"name": p.name, "spec": spaced(p.spec), "class": spaced(p.class_name),
                   "color": class_hex(p.class_name), "role": p.role},
        "death": death, "deaths": len(own), "at_ms": round(at - t0), "window_ms": RECAP_WINDOW_MS,
        "by": killer(d), "icon": icon(kid),
        "fallout": bool(decided and at - t0 > decided.at_ms),
        "outright": bool(kid and killed_outright(rep, f, d, hits)),
        "hits": [{"ms": round(h["timestamp"] - t0), "by": rep.ability(h["abilityGameID"]),
                  "icon": icon(h["abilityGameID"]), "amount": h.get("amount", 0), "absorbed": h.get("absorbed", 0),
                  "overkill": h.get("overkill", 0), "hp": hp(h)} for h in hits],
        "healing": [[ms, round(v)] for ms, v in sorted(per_s.items())],
        "defensives": defs, "stones": stones,
        "saved_by": savers(await if_pressed(rep, f, p, d, hits)),
        "with": [{"name": rep.names.get(x["targetID"], "?"), "ms": round(x["timestamp"] - t0),
                  "by": killer(x)}
                 for x in deaths if x is not d and abs(x["timestamp"] - at) <= BURST_MS],
    }


async def note_view(rep: Report, text: str, boss: str | None) -> dict:
    """The raid note against every pull on the boss, as raid_note with all_pulls counts it: each kick assignment's
    casts pull by pull (kicked by its player, covered by another, went through, its player dead), the pulls whose
    wipe followed casts that went through and whose cast went through first; each planned cooldown pull by pull (on
    time, early, late, not cast, dead)."""
    night = await notes.check_night(rep, text, boss)
    f0 = night.fights[0]
    def color(name: str) -> str:
        p = rep.find_player(name)
        return class_hex(p.class_name) if p else "#9aa3b8"
    return {"view": "note", "report": rep.code, "boss": rep.boss_key(f0), "url": rep.url(f0).split("#")[0],
            "title": f"{rep.boss_key(f0)}: the raid note",
            "kicks": _note_kicks(rep, night, color), "cooldowns": _note_cooldowns(rep, night, color)}


def _note_kicks(rep: Report, night: notes.Night, color: Callable[[str], str]) -> dict | None:
    """Each kick assignment ("Kick #1 left", in the note's order) with its player, and per pull its casts in order:
    kicked, covered (by whom), went through, dead or not in the pull. The pulls before the note's kick order came in
    are drawn but not counted, as raid_note counts them."""
    start = night.change.start if night.change else 0
    rows = [(i, c, k) for i, c in enumerate(night.checks) for k in c.kicks]
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
        for k in sorted(c.kicks, key=lambda k: k.at):
            cells[k.callout.text].append(cell(k) | {"lead": k is lead})
            if i >= start and k.state == "alive":
                alive += 1
                made += k.kicked
        out_pulls.append({"pull": rep.pull_no(c.f), "counted": i >= start, "lost": bool(before),
                          "cells": [cells[s] for s in slots]})
    return {"slots": [{"text": s, "player": (n := who[s].most_common(1)[0][0] if who[s] else ""),
                       "color": color(n) if n else "#9aa3b8"} for s in slots],
            "from_pull": rep.pull_no(night.checks[start].f) if start else None,
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


async def meters_of(rep: Report, tool: str, args: dict) -> list[Meter]:
    """The meters a tool's answer ranks players in, from its own rows: breakdown by player (damage, healing, damage
    taken, kicks, dispels), mechanic_hits for one ability, enemies' damage on adds. None for another call: a table
    by ability or pull, one player's part, every ability's outliers, too few players."""
    a = args
    match tool:
        case "breakdown" if a.get("group_by", "player") == "player" and not a.get("player"):
            found = await tables.meter(rep, a.get("data_type", ""), a.get("pulls"), a.get("boss"), a.get("ability"),
                                       a.get("cutoff"), bool(a.get("whole_pull")), a.get("start"), a.get("end"))
            return [found] if found else []
        case "mechanic_hits" if a.get("ability"):
            found = await mechanics.meter(rep, a["ability"], a.get("pull"), a.get("boss"), bool(a.get("all_pulls")),
                                          bool(a.get("whole_pull")), a.get("start"), a.get("end"))
            return [found] if found else []
        case "enemies":
            return await adds.meters(rep, a.get("pull"), a.get("boss"), bool(a.get("all_pulls")))
    return []


async def meter_view(rep: Report, tool: str, args: dict) -> dict:
    """A tool's ranking of players as in-game damage meters draw it (meters_of): per meter each player's amount, its
    share of the top's, per second over the combat time, and their class colour, spec and role."""
    found = await meters_of(rep, tool, args)
    if not found:
        raise ValueError(f"{tool} ranks no players here")
    players = {p.name: p for p in rep.players.values()}

    def bar(b) -> dict:
        p = players.get(b.name)
        return {"name": b.name, "value": b.value, "note": b.note, "died_after": b.died_after,
                "killed_by": b.killed_by, "color": class_hex(p.class_name) if p else "#9aa3b8",
                "spec": spaced(p.spec) if p else "", "role": p.role if p else ""} | (
                    {"per_pull": b.per_pull, "died_in": b.died_in} if b.per_pull else {})
    meters = [{"title": m.title, "scope": m.scope, "unit": m.unit, "seconds": round(m.seconds, 1),
               "icon": icon(m.ability), "note": m.note, "others": m.others,
               "total": sum(b.value for b in m.bars), "median": statistics.median(b.value for b in m.bars),
               "bars": [bar(b) for b in m.bars]} | ({"pulls": m.pulls, "per_pull": m.per_pull} if m.pulls else {})
              for m in found]
    return {"view": "meter", "report": rep.code, "tool": tool, "args": args, "title": found[0].title,
            "url": rep.url(rep.fight(args.get("pull"), args.get("boss"))).split("#")[0], "meters": meters}


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


def _ended(e) -> dict | None:
    """How the pull ended, in a few words for the card, and when; None for a kill."""
    if e is None:
        return None
    match e.kind:
        case "live":
            return {"text": "still going", "ms": None}
        case "reset":
            return {"text": "a reset", "ms": None}
        case "cascade":
            return {"text": "deaths one after another", "ms": e.half_dead_ms}
        case "called_off":
            return {"text": "called off", "ms": None}
    match e.moments[0]:
        case MassDeath(ability="", at_ms=at, killed=n):
            return {"text": f"{n} dying at once", "ms": at}
        case MassDeath(ability=name, at_ms=at, killed=n):
            return {"text": f"{name} killing {n}", "ms": at}
        case KeptBuff(name=name, at_ms=at):
            return {"text": f"the boss gaining {name}", "ms": at}
        case HalfDead(at_ms=at):
            return {"text": "half the raid dead", "ms": at}
    return None
