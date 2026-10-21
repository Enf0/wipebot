"""Tanks (`tank_swaps`): who had the boss when, their debuff stacks, taunts, and taunts over the nights."""
import asyncio
from collections import Counter, defaultdict

from .. import knowledge
from ..knowledge import KnowledgeError
from ..text import clock, date, day, plural
from .base import Player, Report


async def tank_swaps(rep: Report, pull: int | None = None, boss: str | None = None) -> str:
    """Tank swaps on one pull: who had the boss's aggro when and what took it (usually a taunt), and per tank
    the stacks of each debuff the boss put on them (one line per stint, from first application to removal),
    when they cast each spell that took a boss and when it was ready again, plus tank deaths."""
    f = rep.fight(pull, boss)
    t0 = f["startTime"]
    tanks = pull_tanks(rep, f)
    if not tanks:
        return f"No tanks in {rep.label(f)}."
    lines = [f"Tank swaps on {rep.label(f)} ({rep.url(f)}), tanks: {', '.join(p.label for p in tanks)}."]
    # For Debuffs, WCL's sourceID filter selects the player who has the debuff.
    aggro, debuffs, _ = await asyncio.gather(
        _aggro(rep, f), rep.events_for([(f["id"], {"sourceID": p.id}) for p in tanks], "Debuffs",
                                       hostilityType="Friendlies"),
        asyncio.gather(*(rep.casts(f, p) for p in tanks)))  # for _taunts
    for p, events in zip(tanks, debuffs, strict=True):
        stints: dict[str, list[list]] = defaultdict(list)  # debuff -> [start, peak stack, peak time, end]
        up: Counter = Counter()  # debuff -> copies on the tank now (two bosses, or two casts, can each put one)
        for e in sorted(events, key=lambda e: e["timestamp"]):
            if e.get("targetID") != p.id or e.get("sourceID") in rep.names:  # only what the boss applied
                continue
            name, at = rep.ability(e["abilityGameID"]), e["timestamp"] - t0
            runs = stints[name]
            if e["type"] == "applydebuff":
                if not up[name]:
                    runs.append([at, 0, at, None])
                up[name] += 1
                stack = up[name]
            elif e["type"] == "applydebuffstack" and up[name]:
                stack = e.get("stack", 0)
            elif e["type"] == "removedebuff" and up[name]:
                up[name] -= 1
                if not up[name]:
                    runs[-1][3] = at
                continue
            else:
                continue
            if stack > runs[-1][1]:
                runs[-1][1], runs[-1][2] = stack, at
        lines.append(f"{p.name}:")
        # A stack change or removal with no application seen first leaves an empty entry: nothing to show.
        stints = {name: runs for name, runs in stints.items() if runs}
        for name, runs in sorted(stints.items(), key=lambda kv: kv[1][0][0]):
            lines.append(f"  {name}: " + "; ".join(
                f"{clock(start)}-{clock(end) if end is not None else 'end'}, peak {peak} at {clock(peak_at)}"
                for start, peak, peak_at, end in runs))
        if not stints:
            lines.append("  no boss debuffs")
        lines += await _taunts(rep, f, p, aggro)
        deaths = [d for d in await rep.deaths(f) if d["targetID"] == p.id]
        if deaths:
            lines.append("  died at " + ", ".join(
                f"{clock(d['timestamp'] - t0)} to {rep.ability_ref(d.get('killingAbilityGameID'))}"
                for d in deaths))
    several = len({boss for *_, boss in aggro}) > 1
    lines.append("Boss aggro in order (who took it, and with what): " + ("; ".join(
        f"{clock(at - t0)} {who}{f' on {boss}' if several else ''} ({how})" for at, who, how, boss in aggro)
        or "none"))
    return "\n".join(lines)


async def _taunts(rep: Report, f: dict, p: Player, aggro: list[tuple[float, str, str, str]]) -> list[str]:
    """A tank's casts on a pull of each spell that took a boss's aggro for them (their taunts, as the log shows
    them), with when each was ready again from its Wowhead cooldown: whether a taunt was there when needed."""
    spells = sorted({how for _, who, how, _ in aggro if who == p.name and how not in ("Melee", "?")})
    out = []
    for name in spells:
        ids = rep.ids_named([name])
        casts = [c["timestamp"] - f["startTime"] for c in await rep.casts(f, p) if c["abilityGameID"] in ids]
        if not casts:
            continue
        cooldown, why = None, ""
        try:
            for i in sorted(ids):
                if cooldown := await knowledge.spell_cooldown(i):
                    break
        except KnowledgeError:
            why = " (cooldown unavailable)"
        line = f"    {name}" + (f" ({cooldown:g} s cooldown)" if cooldown else why) + " cast at " + ", ".join(
            clock(at) for at in casts)
        if cooldown:
            line += "; ready again at " + ", ".join(clock(at + cooldown * 1000) for at in casts)
        out.append(line)
    if out:
        out[0:0] = ["  spells that took a boss for them on this pull (cooldowns from Wowhead; a talent can shorten "
                    "them):"]
    return out


def pull_tanks(rep: Report, f: dict) -> list[Player]:
    """The tanks of a pull, or else of the report, by name."""
    here = [p for p in rep.pull_players(f).values() if p.role == "tank"]
    return sorted(here or [p for p in rep.players.values() if p.role == "tank"], key=lambda p: p.name)


async def aggro_history(reports: list[Report], tanks: list[str], weeks: int) -> str:
    """The spells each tank took a boss's aggro with over the guild's raid nights (`reports`, the last `weeks`),
    every boss: how often, and the last time. What a tank's kit can taunt with, as the log shows it (a Death Knight's
    Death Grip takes a boss as well as Dark Command does)."""
    took: dict[str, dict[str, list]] = {t: {} for t in tanks}  # tank -> spell -> [times, (when, last pull)]
    for rep in sorted(reports, key=lambda r: r.start_ms):
        fights = [f for f in rep.fights if f.get("encounterID") and not f.get("inProgress")]
        for f, t in zip(fights, await rep.tables([(f["id"], {}) for f in fights], "Threat"), strict=True):
            for at, who, how, _ in _aggro_bands(rep, t):
                if who in took and how not in ("Melee", "?"):  # the boss turning on its own isn't a taunt
                    seen = took[who].setdefault(how, [0, None])
                    seen[0] += 1
                    seen[1] = (rep.start_ms + at, rep.label(f))
    nights = len({date(r.start_ms) for r in reports})
    parts = []
    for tank, spells in took.items():
        listed = ", ".join(f"{spell} {n} (last {label}, {day(when)})"
                           for spell, (n, (when, label)) in sorted(spells.items(), key=lambda kv: -kv[1][0]))
        parts.append(f"{tank}: {listed or 'none'}")
    return (f"Spells each tank took a boss's aggro with on the guild's {nights} raid night{'s' * (nights != 1)} of "
            f"the {plural(weeks, 'week')} up to this one "
            f"(every boss; times, and the last time): " + "; ".join(parts))


async def _aggro(rep: Report, f: dict) -> list[tuple[float, str, str, str]]:
    """(time, player, what took it, boss) each time a player took a boss's aggro, from WCL's Threat table."""
    return _aggro_bands(rep, await rep.table(f["id"], "Threat"))


def _aggro_bands(rep: Report, t: dict) -> list[tuple[float, str, str, str]]:
    out = []
    for player in t.get("threat") or []:
        for target in player.get("targets") or []:
            if target.get("id") not in rep.bosses:
                continue
            for band in target.get("bands") or []:
                start = band.get("startEvent") or {}
                how = (start.get("ability") or {}).get("name") or start.get("type") or "?"
                out.append((band["startTime"], player["name"], how, rep.actor(target["id"])))
    return sorted(out)
