"""A player's major cooldowns on a pull: their uses against possible uses, and buffs up at the pull."""
from collections import Counter
from collections.abc import Collection

from .. import knowledge
from ..knowledge import KnowledgeError
from ..text import clock, name_key, ordinal
from .adds import boss_units
from .base import REPEAT_CAST_MS, Player, Report, unavailable
from .casting import alive

# Spells used for a moment (a big hit, a movement) rather than on cooldown: never judged by their count.
NOT_ON_COOLDOWN = {"defensive", "move", "tank"}
# Lorrgs tags a throughput cooldown so even when it gives no cooldown (Ascendance, Deeply Rooted Elements, 0 s).
ON_COOLDOWN = {"dps", "raid_cd"}
PREPULL_WINDOW_MS = 60_000  # a buff cast before the pull runs out within this (a potion lasts 30 s)
OVER_BASE = "more than its base cooldown allows: a talent or effect shortens it"


async def cooldown_lines(rep: Report, f: dict, p: Player, casts: list[dict]) -> list[str]:
    """Major cooldowns with use times: damage and healing cooldowns against their possible uses, defensives and
    movement spells only when they were used (they're for a moment, not for cooldown), and Bloodlust and the
    phases, the windows cooldowns are often saved for."""
    early = (await prepull(rep, [f], p))[f["id"]]
    lines = ["Own buffs already up at the pull (cast or drunk before it, so not in the casts): "
             + (", ".join(early) or "none")]
    try:
        rows, untalented = await cooldown_use(rep, p, f, casts, early)
    except KnowledgeError as e:
        return lines + [unavailable("Major cooldowns", e)]
    if not rows:
        return lines + [f"Lorrgs lists no cooldowns for {p.spec} {p.class_name}."]
    judged = [r for r in rows if not NOT_ON_COOLDOWN & set(r[0].get("tags") or [])]
    moments = [r for r in rows if NOT_ON_COOLDOWN & set(r[0].get("tags") or [])]
    if judged:
        own = [d for d in await rep.deaths(f) if d["targetID"] == p.id]
        _, alive_until = alive(rep, f, p, own, casts)
        decided = await rep.decided_at(f)
        cut = f["startTime"] + decided[0] if decided else f["endTime"]
        lines.append("Major cooldowns (list from Lorrgs; uses / possible if pressed on cooldown from the pull until "
                     "they died or the wipe was decided, ignoring charges, cooldown reduction and holding for a burst "
                     "window; one already up at the pull counts as used at 0:00"
                     + ("" if untalented is not None else "; talents not in the log, so 0 uses can mean not "
                        "talented") + "):")
        for sp, times, possible in judged:
            tags = ", ".join(sp.get("tags") or []) or "untagged"
            # The spell Lorrgs lists can be cast under another name and id (Incarnation for Celestial Alignment).
            cast_as = sorted({rep.ability_ref(c["abilityGameID"]) for c in casts if rep.is_spell(c, sp)
                              and c["abilityGameID"] != sp["spell_id"]
                              and name_key(rep.ability(c["abilityGameID"])) != name_key(sp["name"])})
            if possible is None:
                count = f"{len(times)} (cooldown unknown to Lorrgs)"
            elif len(times) > possible:
                count = f"{len(times)} ({OVER_BASE})"
            else:
                count = f"{len(times)}/{possible}"
            lines.append(f"  {sp['name']} [{sp['spell_id']}] ({tags}"
                         + (f"; cast as {', '.join(cast_as)}" if cast_as else "") + f"): {count}"
                         + (f" at {', '.join(clock(t) for t in times)}" if times else "")
                         + (" (and once before the pull)" if sp["name"] in early else "")
                         + _ready_again(rep, f, sp, times, possible, alive_until, cut))
    if untalented:
        lines.append("Not talented (their talents leave these out, e.g. the other side of a choice node; not a "
                     "missed cooldown): " + ", ".join(untalented))
    used = [(sp, times) for sp, times, _ in moments if times]
    lines.append("Defensive, tank and movement spells used (they answer a moment, so their count isn't a fault): "
                 + (", ".join(f"{sp['name']} [{sp['spell_id']}] at {', '.join(clock(t) for t in times)}"
                              for sp, times in used)
                    or "none"))
    try:
        windows = [f"Bloodlust at {clock(at - f['startTime'])}" for at, _, _ in await rep.lusts(f)]
    except KnowledgeError as e:
        windows = [unavailable("Bloodlust", e).removesuffix(".")]
    if phases := rep.phases(f):
        windows.append(f"phases {phases}")
    try:
        if timeline := await boss_timeline(rep, f):
            windows.append(timeline)
    except KnowledgeError as e:
        windows.append(unavailable("The boss's main casts", e).removesuffix("."))
    if windows:
        lines.append("Windows to line cooldowns up with: " + "; ".join(windows))
    return lines


def _phase_at(rep: Report, f: dict, at_ms: float) -> str:
    """'P1 Sszorak (2nd time)' for a moment of a pull, '' when the boss has no phases."""
    seen: Counter = Counter()
    label = ""
    for t in f.get("phaseTransitions") or []:
        if t["startTime"] - f["startTime"] > at_ms:
            break
        seen[t["id"]] += 1
        name = rep.phase_names.get((f["encounterID"], t["id"]))
        label = f"P{t['id']}" + (f" {name}" if name else "") + (f" ({ordinal(seen[t['id']])} time)"
                                                                  if seen[t["id"]] > 1 else "")
    return label


def _ready_again(rep: Report, f: dict, sp: dict, times: list[float], possible: int | None, alive_until: float,
                 cut: float) -> str:
    """' (ready again at 2:07 in P1 (2nd time), unused)' for a cooldown used less than it could be: when it came
    back after its last use, and in which phase, so a use that fell in an intermission can be read as such. Nothing
    when it came back once the wipe was decided (`cut`, report ms) or the pull was over: no use was missed."""
    if not times or possible is None or len(times) >= possible:
        return ""
    ready = times[-1] + (sp.get("cooldown") or 0) * 1000
    if ready >= cut - f["startTime"]:
        return ""
    if ready > alive_until - f["startTime"]:
        return " (died before it was ready again)"
    phase = _phase_at(rep, f, ready)
    return f" (ready again at {clock(ready)}" + (f" in {phase}" if phase else "") + ", unused)"


async def prepull(rep: Report, fights: list[dict], p: Player) -> dict[int, list[str]]:
    """Per pull, the player's own buffs that were already up when it started: a cooldown cast or a potion drunk
    before the pull. Such a buff has no applybuff in the pull; its first event is its removal or a stack change.
    Every pull's in one query."""
    found = await rep.events_for([(f["id"], {"endTime": f["startTime"] + PREPULL_WINDOW_MS}) for f in fights],
                                 "Buffs", sourceID=p.id)
    return {f["id"]: _up_at_pull(rep, p, events) for f, events in zip(fights, found, strict=True)}


def _up_at_pull(rep: Report, p: Player, events: list[dict]) -> list[str]:
    first: dict[int, str] = {}
    for e in sorted(events, key=lambda e: e["timestamp"]):
        if e.get("sourceID") == p.id and e.get("targetID") == p.id:
            first.setdefault(e["abilityGameID"], e["type"])
    return [rep.ability(a) for a, kind in first.items()
            if kind in ("removebuff", "removebuffstack", "refreshbuff", "applybuffstack")]


def one_per_use(times: list[float], apart_ms: float = REPEAT_CAST_MS) -> list[float]:
    """Cast times with the ticks of one channel (Divine Hymn logs six casts in 4 s) or a cast logged twice counted
    once: a cooldown of a minute or more can't be pressed again so soon."""
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] >= apart_ms:
            out.append(t)
    return out


BOSS_CAST_MS = 2_000  # one boss cast logged under two abilities (Mutilate [1277027] and [1277031]) is one


async def boss_timeline(rep: Report, f: dict) -> str | None:
    """When the boss cast the abilities Lorrgs tracks for it (its timeline's casts, Imbibe on Vashnik), the moments
    a raid cooldown is held for; None when Lorrgs doesn't know the boss."""
    tracked = {sp["name"]: sp.get("spell_id") for sp in await knowledge.boss_spells(f["name"])
               if sp.get("show", True) and sp.get("name")}
    bosses = boss_units(rep, f)
    times: dict[str, list[float]] = {}
    for c in await rep.events(f["id"], "Casts", hostilityType="Enemies"):
        name = rep.ability(c["abilityGameID"])
        if c["type"] == "cast" and c.get("sourceID") in bosses and (
                name in tracked or c["abilityGameID"] in tracked.values()):
            times.setdefault(name, []).append(c["timestamp"] - f["startTime"])
    if not times:
        return None
    return "the boss's main casts (those Lorrgs tracks) " + "; ".join(
        f"{name} at {', '.join(clock(t) for t in one_per_use(ts, BOSS_CAST_MS))}" for name, ts in times.items())


async def cooldown_use(rep: Report, p: Player, f: dict, casts: list[dict], early: Collection[str] = ()
                        ) -> tuple[list[tuple[dict, list[float], int | None]], list[str] | None]:
    """For each of the spec's cooldowns of a minute or more (Lorrgs) the player has, and each throughput cooldown
    Lorrgs tags but gives no cooldown for: (spell, use times from pull start, possible uses in the pull, or None
    when the cooldown isn't known; one whose buff was already up at the pull, in `early`, used at 0:00, cast
    before it: Stormkeeper before the pull); and the names of those their talents leave out (one side of a choice
    node, a talent skipped), or None when the talents aren't in the log. Uses and possible uses count until the
    player died (for good) or the wipe was decided."""
    _, alive_until = alive(rep, f, p, [d for d in await rep.deaths(f) if d["targetID"] == p.id], casts)
    decided = await rep.decided_at(f)
    end_ms = min(alive_until, f["startTime"] + decided[0] if decided else f["endTime"]) - f["startTime"]
    dur_s = end_ms / 1000
    talents = ((await rep.details(f)).get(p.id, {}).get("combatantInfo") or {}).get("talentTree") or []
    tree = await knowledge.tree_for(p.class_name, p.spec) if talents else None
    untaken = knowledge.untaken_spells(tree, {t["id"] for t in talents}) if tree else set()
    rows, skipped = [], []
    for sp in await rep.spells(p):
        cd = sp.get("cooldown") or 0
        unknown = not cd and bool(ON_COOLDOWN & set(sp.get("tags") or []))
        if cd < 60 and not unknown:
            continue
        times = one_per_use([t for c in casts if rep.is_spell(c, sp)
                             and (t := c["timestamp"] - f["startTime"]) <= end_ms])
        if name_key(sp["name"]) in {name_key(e) for e in early}:
            times.insert(0, 0.0)
        if sp["spell_id"] in untaken and not times:
            skipped.append(sp["name"])
            continue
        rows.append((sp, times, None if unknown else int(dur_s // cd) + 1))
    return rows, skipped if tree else None
