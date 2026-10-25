"""What the views draw: the page of Wipebot's views (app/, an MCP App in Claude and ChatGPT, the bot's pictures) and
each view's data, computed from the same facts as its tool's text so the two can't disagree.

A tool with a view returns a small key ({"view": ..., "report": ..., "pull": ...}); the page asks for the view's
data with it (`view_data`), and draws it. The replay viewer's data is maps.replay.
"""
import functools
import hashlib
from importlib import resources

from ..classes import class_hex
from ..knowledge import KnowledgeError
from ..pulls import DIFFICULTIES
from .base import PullRef, Report, alive_after
from .ending import HalfDead, KeptBuff, MassDeath, pull_ending
from .fight import DAMAGE_BUCKET_MS, boss_casts, raid_cooldown_casts, raid_damage, tracks

APP = resources.files("wipebot.core").joinpath("app")
# The page's parts, in the order they're joined: the bridge to the host first, then what draws, then the views.
SCRIPTS = ["bridge.js", "kit.js", "pull.js", "replay.js", "main.js"]
HEALTH_STEP = 0.5  # boss health points closer than this many % to the last kept one are left out
VIEWS = ("pull",)  # what view_data draws (the replay is maps.replay)


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


async def view_data(rep: Report, view: str, pull: PullRef = None, boss: str | None = None) -> dict:
    """The data `view` draws, for the pull a tool was asked about (`pull` and `boss` resolve as the tool's did)."""
    if view != "pull":
        raise ValueError(f"no view {view!r}")
    return await pull_view(rep, rep.fight(pull, boss))


async def pull_view(rep: Report, f: dict) -> dict:  # noqa: PLR0915 (one dict, field by field)
    """One pull at a glance: how it ended, the boss's health and the raid's damage taken over time, phases, the boss's
    casts, raid and healing cooldowns, Bloodlust and every death, as pull_summary and pull_timeline state them."""
    t0, length = f["startTime"], f["endTime"] - f["startTime"]
    deaths, raid, decided = await rep.deaths(f), await rep.raid_size(f), await rep.decided_at(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    casts, _ = await boss_casts(rep, f)
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
    for d, alive in zip(deaths, alive_after(deaths, backs, raid), strict=True):
        name = rep.names.get(d["targetID"], "?")
        p = rep.players.get(name.lower())
        at = d["timestamp"] - t0
        out_deaths.append({"ms": round(at), "name": name, "spec": p.spec if p else "",
                           "color": class_hex(p.class_name) if p else "#9aa3b8",
                           "by": rep.ability(d.get("killingAbilityGameID")), "alive": alive,
                           "fallout": bool(decided and at > decided.at_ms)})
    return {
        "view": "pull", "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f),
        "title": rep.label(f), "difficulty": DIFFICULTIES.get(f.get("difficulty"), ""),
        "url": rep.url(f), "kill": bool(f["kill"]), "live": bool(f.get("inProgress")), "duration_ms": round(length),
        "boss_pct": f.get("bossPercentage"), "raid": raid,
        "ended": _ended(await pull_ending(rep, f)),
        "decided": {"ms": round(decided.at_ms), "why": decided.why} if decided else None,
        "phases": [{"ms": round(t["startTime"] - t0), "id": t["id"],
                    "name": rep.phase_names.get((f["encounterID"], t["id"]), "")}
                   for t in f.get("phaseTransitions") or []],
        "health": health,
        "damage": {"step_ms": DAMAGE_BUCKET_MS, "per_s": [[b * DAMAGE_BUCKET_MS, round(v)] for b, v in damage.items()]},
        "casts": [{"ms": round(c.at - t0), "name": c.name, "tracked": c.tracked} for c in casts],
        "cooldowns": [{"ms": round(at - t0), "kind": kind.split()[0], "name": name, "player": who}
                      for at, kind, name, who in await raid_cooldown_casts(rep, f)],
        "lusts": lusts,
        "deaths": out_deaths,
    }


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
