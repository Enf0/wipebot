"""A player's cooldowns against Lorrgs' top parses of their spec on the boss (warcraft_cli's cooldown packet)."""
import math
from collections import Counter, defaultdict

from ..text import clock, fight_url, plural
from . import fetch
from .bosses import boss_spells
from .fetch import KnowledgeError, NotFound
from .names import lorrgs_slug, slug
from .specs import spec_spells
from .talents import class_and_spec


async def cooldown_comparison(report_code: str, fight_id: int, player: str, phase: int = 1) -> dict:
    """warcraft_cli's cooldown packet: one player's cooldown casts on a pull (all of it: filter by the phase's
    window), the phase, and Lorrgs' top parses of the spec on the boss in that phase with the boss's casts."""
    return await fetch.cli("warcraft", "cooldown-packet",
                           fight_url(report_code, fight_id),
                           "--actor-name", player, "--phase", str(phase))


async def cooldown_comparison_text(report_code: str, fight_id: int, player: str, phase: int = 1) -> str:
    """compare_with_top's cooldowns aspect: one player's cooldown casts in a phase of a pull, next to top parses of
    the same spec and boss."""
    data = await cooldown_comparison(report_code, fight_id, player, phase)
    ph = (data.get("phase") or {}).get("selected") or {}
    me = data.get("player") or {}
    casts = ((data.get("cooldowns") or {}).get("player_casts") or {}).get("tracked_casts") or []
    mine: dict[str, list[str]] = defaultdict(list)
    lo, hi = ph.get("start_ms", 0), ph.get("end_ms") or math.inf
    for c in casts:
        if lo <= c["timestamp_ms"] <= hi:  # warcraft_cli lists the whole pull's casts
            mine[c["spell"]["name"]].append(clock(c["timestamp_ms"]))
    comp = data.get("comparison") or {}
    window = (f"phase {ph.get('label', phase)} ({clock(ph.get('start_ms', 0))}-{clock(ph['end_ms'])})"
              if ph.get("end_ms") else "the whole pull (the boss has no phases)")
    lines = [f"{me.get('name')} ({me.get('spec_slug')}), {window} of this pull.",
             "Their cooldown casts: " + ("; ".join(f"{n} at {', '.join(t)}" for n, t in mine.items()) or "none")]
    if comp.get("status") != "ready":
        lines.append(f"No top-parse comparison: {comp.get('reason') or comp.get('status')}.")
    else:
        n = comp.get("phase_sample_count") or comp.get("sample_count")
        lines.append(f"Top parses compared: {n} (from Lorrgs' ranking of this spec on this boss).")
        freq = comp.get("selected_phase_spell_frequency") or []
        lines.append("How many of them cast each cooldown in this phase, and total casts: " + "; ".join(
            f"{f['spell']['name']} {f['sample_count']}/{n} ({plural(f['total_casts'], 'cast')})" for f in freq))
        for s in (comp.get("samples") or [])[:5]:
            times: dict[str, list[str]] = defaultdict(list)
            for c in s.get("selected_phase_casts") or []:
                times[c["spell"]["name"]].append(clock(c["timestamp_ms"]))
            boss: dict[str, list[str]] = defaultdict(list)
            for c in s.get("selected_phase_boss_casts") or []:
                boss[c["spell"]["name"]].append(clock(c["timestamp_ms"]))
            lines.append(f"  {s['player']['name']} ({s.get('region')}, report {s.get('report_id')}, kill "
                         f"{clock(s.get('duration_ms') or 0)}): "
                         + "; ".join(f"{k} at {', '.join(v)}" for k, v in times.items())
                         + (" | boss: " + "; ".join(f"{k} at {', '.join(v)}" for k, v in boss.items()) if boss else ""))
        kills = sorted(s.get("duration_ms") or 0 for s in comp.get("samples") or [])
        if kills:
            lines.append(f"These top kills took {clock(kills[0])}-{clock(kills[-1])} and may use another strategy or "
                         "push phases at other times: compare when cooldowns go out against the boss casts, not the "
                         "clock.")
    if (data.get("lorrgs") or {}).get("missing"):
        lines.append("Lorrgs had no copy of this report, so boss casts and the player's deaths are not in this "
                     "comparison.")
    spec_slug, boss_slug = me.get("spec_slug"), (data.get("boss") or {}).get("boss_slug")
    lorrgs = f"https://lorrgs.io/spec_ranking/{spec_slug}/{boss_slug}" if spec_slug and boss_slug else "https://lorrgs.io"
    lines.append(f"Sources: Warcraft Logs {fight_url(report_code, fight_id)} "
                 f"(this player's casts), Lorrgs {lorrgs} (top parses)")
    return "\n".join(lines)


TOP_TIMELINES = 5  # top parses whose cooldown times are listed in full
PERSONAL = {"defensive", "move", "tank"}  # a moment's spells, not the cooldowns a raid plans around


async def top_cooldowns_text(spec: str, boss: str, difficulty: str | None = None) -> str:
    """compare_with_top's cooldowns without a player: when the top parses of a spec on a boss pressed their major
    cooldowns (Lorrgs' spec ranking), with the boss's tracked casts beside them to line them up by."""
    class_name, spec_name = await class_and_spec(spec)
    spells = {int(s["spell_id"]): s["name"] for s in await spec_spells(class_name, spec_name)
              if s.get("spell_id") and not PERSONAL & set(s.get("tags") or [])}
    diff = (difficulty or "mythic").lower()
    try:
        data = await fetch.cli("lorrgs", "spec-ranking", lorrgs_slug(class_name, spec_name), slug(boss),
                               "--difficulty", diff)
    except NotFound:
        data = {}
    tracked = {int(s["spell_id"]): s["name"] for s in await boss_spells(boss) if s.get("spell_id") and s.get("show", 1)}
    fights = [(r, f) for r in data.get("reports") or [] for f in (r.get("fights") or [])[:1]]
    if not fights or not spells:
        raise KnowledgeError(f"Lorrgs has no top {spec_name} {class_name} parses on {boss} ({diff})")
    pressed: Counter = Counter()
    lines = []
    for k, (r, f) in enumerate(fights, 1):
        mine: dict[str, list[float]] = defaultdict(list)
        for c in ((f.get("players") or [{}])[0]).get("casts") or []:
            if c.get("id") in spells:
                mine[spells[c["id"]]].append(c["ts"])
        pressed.update(mine.keys())
        if k > TOP_TIMELINES:
            continue
        theirs: dict[str, list[float]] = defaultdict(list)
        for c in (f.get("boss") or {}).get("casts") or []:
            if c.get("id") in tracked:
                theirs[tracked[c["id"]]].append(c["ts"])
        lines.append(f"  #{k} ({r.get('region')}, report {r.get('report_id')}, kill {clock(f.get('duration') or 0)}): "
                     + "; ".join(f"{n} at {', '.join(clock(t) for t in ts)}" for n, ts in mine.items())
                     + (" | boss: " + "; ".join(f"{n} at {', '.join(clock(t) for t in _sets(ts))}"
                                                for n, ts in theirs.items()) if theirs else ""))
    return "\n".join([
        f"Major cooldowns of the top {len(fights)} {spec_name} {class_name} parses on {boss} ({diff}), from Lorrgs: "
        "how many of them pressed each, then when the top " + str(min(TOP_TIMELINES, len(fights))) + " pressed theirs, "
        "beside the boss's tracked casts (kills differ in pace: line them up by the boss's casts, not the clock).",
        "Pressed by: " + ", ".join(f"{n} {c}/{len(fights)}" for n, c in pressed.most_common()), *lines])


BOSS_SET_MS = 5000  # a boss's casts this close together are one set (Stone Breaker's three swirls): its first


def _sets(times: list[float]) -> list[float]:
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] >= BOSS_SET_MS:
            out.append(t)
    return out
