"""`breakdown`: any WCL table over any set of pulls on a boss, grouped by player, ability or pull."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal, get_args

from ..text import clock, name_key, ordinal, plural, short
from .base import FactError, Player, Report
from .casting import cast_rows
from .meter import MIN_BARS, Bar, Meter
from .ranks import kill_parses, metric_of

BreakdownType = Literal["DamageDone", "Healing", "DamageTaken", "Casts", "Buffs", "Debuffs", "EnemyDebuffs",
                        "Interrupts", "Dispels"]
BREAKDOWN_TYPES = get_args(BreakdownType)
GroupBy = Literal["player", "ability", "pull"]
UNTIL_DECIDED_TYPES = ("DamageDone", "Healing", "DamageTaken", "Casts")  # tables summed per pull, cut at a moment
TARGETS_SHOWN = 4  # damage taken by ability: the players who took most of each


@dataclass
class Asked:
    """What a breakdown is over: its pulls, the WCL table and filters behind it, and its heading."""
    data_type: str  # WCL's table (EnemyDebuffs is its Debuffs)
    group_by: str
    fights: list[dict]
    player: Player | None
    filters: dict
    ability: str | None
    ids: list[int | None]  # the ability's ids, or [None] for every ability
    cutoff: int | None
    ends: dict[int, float]  # fight id -> the moment its wipe was decided, but with whole_pull
    head: str
    scope: str  # "all 33 Sszorak pulls", "Sszorak pull 13"

    @property
    def dur_s(self) -> float:
        return sum(self.ends.get(f["id"], f["endTime"]) - f["startTime"] for f in self.fights) / 1000


async def breakdown(rep: Report, data_type: str, group_by: str = "player", pulls: list[int] | None = None,
                    boss: str | None = None, player: str | None = None, ability: str | None = None,
                    cutoff: int | None = None, whole_pull: bool = False) -> str:
    """Any WCL table over any set of pulls on a boss, grouped by player, ability or pull: those summed per pull
    (UNTIL_DECIDED_TYPES) each only until its wipe was decided, but with `whole_pull`."""
    q = await _asked(rep, data_type, group_by, pulls, boss, player, ability, cutoff, whole_pull)
    if q.data_type in ("Interrupts", "Dispels"):
        return await _kicks_text(rep, q)
    if q.group_by == "pull":
        return await _by_pull(rep, q)
    return await _summed(rep, q)


METERS = {"DamageDone": "Damage done", "Healing": "Healing", "DamageTaken": "Damage taken", "Interrupts": "Kicks",
          "Dispels": "Dispels"}  # the tables a meter draws by player, and its title


async def meter(rep: Report, data_type: str, pulls: list[int] | None = None, boss: str | None = None,
                ability: str | None = None, cutoff: int | None = None, whole_pull: bool = False) -> Meter | None:
    """The breakdown by player as a meter (its rows, as the text states them), for damage, healing, damage taken,
    kicks and dispels; None for another table or fewer than MIN_BARS players."""
    if data_type not in METERS:
        return None
    q = await _asked(rep, data_type, "player", pulls, boss, None, ability, cutoff, whole_pull)
    title = METERS[data_type] + (f": {q.ability}" if q.ability else "")
    scope = q.scope + (", each until its wipe was decided" if q.ends and len(q.fights) > 1
                       else ", until the wipe was decided" if q.ends else "")
    scope += f", each until its {ordinal(cutoff)} death" if cutoff else ""
    icon = q.ids[0] if q.ability else None
    if data_type in ("Interrupts", "Dispels"):
        per = await _kick_counts_by_player(rep, data_type, q.fights, q.filters, q.ability)
        bars = [Bar(name, sum(by.values()), ", ".join(f"{a} {n}" for a, n in by.most_common(3)) if len(by) > 1 else "")
                for name, by in sorted(per.items(), key=lambda kv: -sum(kv[1].values()))]
        return Meter(title, scope, bars, "kicks" if data_type == "Interrupts" else "dispels", ability=icon) \
            if len(bars) >= MIN_BARS else None
    s = await _summed_rows(rep, q)
    players = {p.name: p for p in rep.players.values()}
    bars = []
    for name, value, extra in s.rows:
        p = players.get(name)
        parse = f"parse {ordinal(s.parses[name])}" if p and name in s.parses and metric_of(p) == s.metric else ""
        bars.append(Bar(name, value, ", ".join(x for x in (parse, extra.removeprefix(", ")) if x)))
    note = (f"Parses: Warcraft Logs' {s.metric} percentiles against the same spec on this kill." if s.parses else "")
    return Meter(title, scope, bars, seconds=s.dur_s, ability=icon, note=note) if len(bars) >= MIN_BARS else None


async def _asked(rep: Report, data_type: str, group_by: str, pulls: list[int] | None, boss: str | None,
                 player: str | None, ability: str | None, cutoff: int | None, whole_pull: bool) -> Asked:
    if data_type not in BREAKDOWN_TYPES:
        raise FactError(f"data_type must be one of {', '.join(BREAKDOWN_TYPES)}")
    if group_by not in ("player", "ability", "pull"):
        raise FactError("group_by must be player, ability or pull")
    cut = not (whole_pull or cutoff) and data_type in UNTIL_DECIDED_TYPES  # a cutoff is its own cut
    b = rep.boss(boss)
    fights = [rep.fight(n, b) for n in pulls] if pulls else rep.ended(rep.by_boss[b])
    p = rep.player(player) if player else None
    if p and pulls:
        for f in fights:
            rep.require_in(f, p)
    elif p:
        fights = rep.pulls_with(p, fights)
    filters = _filters(data_type, p)
    if cutoff:
        filters["wipeCutoff"] = cutoff  # WCL ignores everything after this many deaths in each pull
    ids: list[int | None] = list(rep.ability_ids(ability)) if ability else [None]
    if ability and ability.strip().isdigit() and ids[0] in rep.abilities:
        ability = rep.ability(ids[0])  # an id: its name, which interrupts and dispels are matched by
    ends = await rep.decided_cuts(fights) if cut else {}
    scope = (f"{b} pull{'s' * (len(pulls) > 1)} {', '.join(str(n) for n in pulls)}" if pulls
             else f"all {plural(len(fights), b + ' pull')}")
    what = "Debuffs on enemies" if data_type == "EnemyDebuffs" else data_type
    who = (f", applied by {p.name}" if data_type == "EnemyDebuffs" else f", {p.name} only") if p else ""
    head = (("" if pulls else rep.still_going(rep.by_boss[b])) + f"{what} on {scope}{who}"
            + (f", {ability} only" if ability else "")
            + (f", each pull until its {ordinal(cutoff)} death" if cutoff else "")
            + (await _decided_note(rep, fights, ends) if cut else ""))
    return Asked("Debuffs" if data_type == "EnemyDebuffs" else data_type, group_by, fights, p, filters, ability,
                 ids, cutoff, ends, head, scope)


def _filters(data_type: str, p: Player | None) -> dict:
    """WCL's filters for a table, and for one player's part of it."""
    filters: dict = {}
    if data_type == "EnemyDebuffs":
        # Debuffs on enemies; a player's are picked by their name (WCL's source filters pick the aura's holder).
        filters["hostilityType"] = "Enemies"
        if p:
            filters["filterExpression"] = f'source.name = "{p.name}"'
    elif p:
        # Buffs and Debuffs filter on the player who has the aura; every other type takes sourceID, which
        # for DamageTaken means the player who took the damage.
        filters["targetID" if data_type in ("Buffs", "Debuffs") else "sourceID"] = p.id
    if data_type == "DamageTaken":
        filters["hostilityType"] = "Friendlies"
    return filters


async def _decided_note(rep: Report, fights: list[dict], ends: dict[int, float]) -> str:
    if len(fights) > 1:
        return (", each pull until its wipe was decided (the boss's enrage or half the raid dead; whole_pull for the "
                "rest)" if ends else "")
    f = fights[0]
    cut = (await rep.until_decided(f, False))[1]
    return ("," + cut if cut else ", a kill: the whole pull" if f["kill"]
            else ", the whole pull (its wipe was never decided before it ended)")


async def _kicks_text(rep: Report, q: Asked) -> str:
    lines = await _kicks(rep, q.data_type, q.fights, q.filters, q.ability)
    if q.data_type == "Dispels":
        lines += await _dispelled(rep, q.fights, q.player, q.ability)
    if q.group_by == "pull":
        lines += await _kicks_per_pull(rep, q.data_type, q.fights, q.filters, q.ability)
    elif q.group_by == "player" and q.data_type == "Interrupts":
        lines += await _kickers(rep, q.fights, q.filters, q.ability)
    return q.head + ":\n" + "\n".join(lines)


async def _by_pull(rep: Report, q: Asked) -> str:
    lines = [f"{q.head}, by pull:"]
    per_pull = await _per_pull_tables(rep, q.fights, q.data_type, q.filters, q.ids, q.ends)
    for f, tables_ in zip(q.fights, per_pull, strict=True):
        rows, tt = _merged_rows(tables_, q.data_type, "ability" if q.player else "player", q.ids != [None])
        rows = _without_moved(rows, q.data_type)[0]
        # WCL's time is cut at the cutoff too
        d = (tt if q.cutoff and tt else q.ends.get(f["id"], f["endTime"]) - f["startTime"]) / 1000
        total = (_fmt_value(sum(v for _, v, _ in rows), q.data_type, d, tt)
                 if q.data_type not in ("Buffs", "Debuffs") else "")
        top = ", ".join(f"{n} {_fmt_value(v, q.data_type, d, tt)}" for n, v, _ in rows[:3])
        parts = [total, f"top {top}" if top else ""]
        lines.append(f"  {rep.label(f)} ({clock(d * 1000)}): " + "; ".join(x for x in parts if x))
    return "\n".join(lines)


@dataclass
class Summed:
    """A table over every pull, one row per player, ability or aura: (name, value, extra text) rows biggest first,
    the health taken to give to others apart (healing), the combat time, and the parses on one whole kill."""
    rows: list[tuple]
    moved: list[tuple]
    dur_s: float
    total_time: float
    parses: dict[str, int]
    metric: str


async def _summed_rows(rep: Report, q: Asked) -> Summed:
    auras = q.data_type in ("Buffs", "Debuffs")
    view = {"viewBy": "Ability"} if q.group_by == "ability" and not auras else {}
    if q.ends:  # each pull cut at its own moment: a table per pull, summed
        tables_ = [t for per in await _per_pull_tables(rep, q.fights, q.data_type, {**q.filters, **view}, q.ids,
                                                         q.ends) for t in per]
    else:
        tables_ = [await rep.table([f["id"] for f in q.fights], q.data_type, **q.filters, **view, **_only(gid))
                   for gid in q.ids]
    rows, total_time = _merged_rows(tables_, q.data_type, q.group_by, per_hit=q.ids != [None])
    rows, moved = _without_moved(rows, q.data_type)
    dur_s = total_time / 1000 if q.cutoff and total_time else q.dur_s  # up to the cutoff, as WCL counts it
    parses, metric = await _parses(rep, q)
    return Summed(rows, moved, dur_s, total_time, parses, metric)


async def _summed(rep: Report, q: Asked) -> str:
    """The table over every pull, one row per player, ability or aura."""
    auras = q.data_type in ("Buffs", "Debuffs")
    s = await _summed_rows(rep, q)
    rows, dur_s, total_time = s.rows, s.dur_s, s.total_time
    total = sum(v for _, v, _ in rows) or 1
    lines = [f"{q.head}, by {'aura' if auras else q.group_by} ({clock(dur_s * 1000)} of combat):"]
    players = {p.name: p for p in rep.players.values()} if q.group_by == "player" else {}
    parsed = False
    for k, (name, value, extra) in enumerate(rows[:25], 1):
        share = f", {100 * value / total:.0f}% of total" if not auras else ""
        tag = rep.tank_tag(name) if q.data_type == "DamageTaken" and q.group_by == "player" else ""
        p = players.get(name)
        who = p.label if p else name
        parse = (f", parse {ordinal(s.parses[name])} percentile of the spec" if p and name in s.parses
                 and metric_of(p) == s.metric else "")
        parsed = parsed or bool(parse)
        lines.append(f"  {k}. {who}{tag}: {_fmt_value(value, q.data_type, dur_s, total_time)}{share}{parse}{extra}")
    if not rows:
        lines.append("  nothing recorded")
    for name, value, extra in s.moved:
        lines.append(f"  Not healing: {name}: {_fmt_value(value, q.data_type, dur_s, total_time)}{extra}: health "
                     "taken from players to give to others. Warcraft Logs counts it as negative healing and takes it "
                     "off the healer's total; it is left out of the total and the shares above.")
    if parsed:
        lines.append(f"Parses are Warcraft Logs' {s.metric} percentiles against the same spec on this kill, for the "
                     "players ranked by it: compare players by these, not by the totals across specs.")
    return "\n".join(lines)


async def _parses(rep: Report, q: Asked) -> tuple[dict[str, int], str]:
    """({player: parse}, metric) for damage or healing by player on one whole kill; ({}, "") otherwise (a wipe,
    several pulls, a cutoff, one ability: the parse is of the whole kill)."""
    metric = {"DamageDone": "dps", "Healing": "hps"}.get(q.data_type, "")
    one_kill = len(q.fights) == 1 and q.fights[0]["kill"] and not (q.cutoff or q.ends or q.ability)
    if not metric or q.group_by != "player" or not one_kill:
        return {}, ""
    return await kill_parses(rep, q.fights[0], metric), metric


def _without_moved(rows: list[tuple], data_type: str) -> tuple[list[tuple], list[tuple]]:
    """(healing rows, rows of health taken from players to give to others): Warcraft Logs lists Spirit Link Totem's
    share-out as healing, the health given to those below the average as a positive row and the health taken from
    the rest as a negative one, which isn't healing."""
    if data_type != "Healing":
        return rows, []
    return [r for r in rows if r[1] >= 0], [r for r in rows if r[1] < 0]


def _only(ability_id: int | None) -> dict:
    return {"abilityID": ability_id} if ability_id is not None else {}


async def _per_pull_tables(rep: Report, fights: list[dict], data_type: str, filters: dict,
                           ability_ids: list[int | None], ends: dict[int, float]) -> list[list[dict]]:
    """Each pull's tables, one per ability id (WCL filters on one id per query, and an ability can have several
    ids under one name), each pull up to its end in `ends`; the pulls fetched in one query per ability id."""
    items = [(f["id"], {"endTime": ends[f["id"]]} if f["id"] in ends else {}) for f in fights]
    per_id = [await rep.tables(items, data_type, **filters, **_only(gid)) for gid in ability_ids]
    return [[t[k] for t in per_id] for k in range(len(fights))]


def _merged_rows(tables: list[dict], data_type: str, group_by: str, per_hit: bool = False) -> tuple[list[tuple],
                                                                                                    float]:
    """(name, value, extra text) rows of several tables merged by name, and the combat time. With `per_hit` (one
    ability asked for) or by ability, each row says its hits, average hit and crits; damage taken by ability, who
    took most of it."""
    merged: dict[str, tuple[float, Counter]] = {}
    total_time = 0.0
    for t in tables:
        total_time = t.get("totalTime") or total_time
        for name, value, counts in _table_rows(t, data_type):
            old, old_counts = merged.get(name, (0.0, Counter()))
            merged[name] = (max(old, value), old_counts or counts) if data_type in ("Buffs", "Debuffs") \
                else (old + value, old_counts + counts)
    def kept(c: Counter) -> Counter:  # who took it: only by ability
        return c if group_by == "ability" else Counter({k: v for k, v in c.items() if not isinstance(k, tuple)})
    rows = sorted(((n, v, _extra(v, kept(c), data_type, per_hit or group_by == "ability"))
                   for n, (v, c) in merged.items()), key=lambda r: -r[1])
    return rows, total_time


RARELY_DISPELLED = 0.25  # a debuff dispelled on fewer applications than this share: not one anyone bothers with


def _kick_entries(t: dict, ability: str | None) -> list[dict]:
    """The per-ability rows of an Interrupts or Dispels table (or just `ability`'s)."""
    return [e for group in t.get("entries", []) for e in group.get("entries", [])
            if not ability or name_key(e.get("name", "")) == name_key(ability)]


def _kick_counts(e: dict) -> tuple[int, int, int, int]:
    """(began, stopped, went through, ended otherwise) of a table row: Warcraft Logs counts the casts that were
    interrupted and the ones that completed; the rest ended another way (the caster died or was moved)."""
    began, done = e.get("spellsBegun", 0), e.get("spellsCompleted", 0)
    stopped = e.get("spellsInterrupted", 0) + e.get("spellChannelsInterrupted", 0)
    return began, stopped, done, max(0, began - stopped - done)


def _dispel_counts(e: dict) -> tuple[int, int, int, int]:
    """(applied, dispelled, ran out, dispels that left it on) of a Dispels table row. Warcraft Logs counts every
    dispel as one, a dispel that only took a stack off a debuff that stayed too, so its dispels and ran-outs can
    add up to more than the applications: such dispels are counted apart, and every application was either
    dispelled or ran out."""
    began, stopped, done, _ = _kick_counts(e)
    dispelled = min(stopped, max(0, began - done))
    return began, dispelled, began - dispelled, stopped - dispelled


def _kick_text(data_type: str, e: dict) -> str:
    """'Visceral Burst: 750 began, 646 kicked, 56 went through, 48 ended otherwise (the caster died or was moved)'
    or 'Exploding Infection: 70 applied, 63 dispelled, 7 ran out, and 2 more dispels took a stack off and left it
    on'."""
    if data_type == "Interrupts":
        began, stopped, done, other = _kick_counts(e)
        text = f"{e['name']}: {began} began, {stopped} kicked, {done} went through"
        if other:
            text += f", {other} ended otherwise (the caster died or was moved)"
        return text
    applied, dispelled, ran_out, partial = _dispel_counts(e)
    text = f"{e['name']}: {applied} applied, {dispelled} dispelled, {ran_out} ran out"
    if partial:
        text += f", and {plural(partial, 'more dispel')} took a stack off and left it on"
    if applied and dispelled < RARELY_DISPELLED * applied:
        text += " (mostly left alone: not worth dispelling, or missed; check what it does)"
    return text


async def _kicks(rep: Report, data_type: str, fights: list[dict], filters: dict,
                 ability: str | None = None) -> list[str]:
    """Interrupts or dispels: per enemy cast or debuff (or just `ability`), how many began, how many were kicked
    or dispelled and by whom, how many went through or ran out, and for dispels on whom they ran out."""
    t = await rep.table([f["id"] for f in fights], data_type,
                             **{k: v for k, v in filters.items() if k != "hostilityType"})
    # by whom: each dispel counts, so the dispellers' numbers add up to the dispels, not the applications dispelled
    stopped = "kicked by" if data_type == "Interrupts" else "dispels by"
    out = []
    for e in sorted(_kick_entries(t, ability), key=lambda e: -_kick_counts(e)[1]):
        who = ", ".join(f"{d['name']} {d['total']}"
                        for d in sorted(e.get("details", []), key=lambda d: -d.get("total", 0)))
        text = f"  {_kick_text(data_type, e)}; {stopped} {who or 'nobody'}"
        if data_type == "Dispels":
            on = Counter((m.get("target") or {}).get("name") for m in e.get("missedCasts") or [])
            on.pop(None, None)
            if on:
                text += "; ran out on " + ", ".join(f"{n} {c}" for n, c in on.most_common(8))
        out.append(text)
    return out or ["  nothing recorded"]


async def _kicks_per_pull(rep: Report, data_type: str, fights: list[dict], filters: dict,
                          ability: str | None) -> list[str]:
    """The same counts per pull, the pulls' tables fetched in one query."""
    tables = await rep.tables([(f["id"], {}) for f in fights], data_type,
                              **{k: v for k, v in filters.items() if k != "hostilityType"})
    out = ["By pull:"]
    for f, t in zip(fights, tables, strict=True):
        rows = [_kick_text(data_type, e) for e in _kick_entries(t, ability)]
        out.append(f"  {rep.label(f)} ({clock(f['endTime'] - f['startTime'])}): " + ("; ".join(rows) or "none"))
    return out


async def _kickers(rep: Report, fights: list[dict], filters: dict, ability: str | None) -> list[str]:
    """Interrupts per player: who kicked how many of what. A cast that went through has nobody's name on it: who
    let it through isn't in the log."""
    per = await _kick_counts_by_player(rep, "Interrupts", fights, filters, ability)
    if not per:
        return []
    out = ["By player (kicks; who let a cast through isn't in the log: a cast nobody kicked has no name on it):"]
    for name, by in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        out.append(f"  {name}: {sum(by.values())}" + (" (" + ", ".join(f"{a} {n}" for a, n in by.most_common()) + ")"
                                                       if len(by) > 1 else ""))
    return out


async def _kick_counts_by_player(rep: Report, data_type: str, fights: list[dict], filters: dict,
                                 ability: str | None) -> dict[str, Counter]:
    """Kicks or dispels per player, by what they kicked or dispelled (a dispel that only took a stack off counts)."""
    t = await rep.table([f["id"] for f in fights], data_type,
                        **{k: v for k, v in filters.items() if k != "hostilityType"})
    per: dict[str, Counter] = defaultdict(Counter)
    for e in _kick_entries(t, ability):
        for d in e.get("details", []):
            per[d["name"]][e["name"]] += d.get("total", 0)
    return per


async def _dispelled(rep: Report, fights: list[dict], p: Player | None, ability: str | None) -> list[str]:
    """Who was dispelled of what, and by whom: the Dispels table only counts per dispeller, its events name both.
    A totem's or pet's dispels count for its owner."""
    by_aura: dict[str, Counter] = defaultdict(Counter)
    by_pair: dict[str, Counter] = defaultdict(Counter)
    for e in await rep.events([f["id"] for f in fights], "Dispels"):
        if e.get("type") != "dispel" or e.get("targetID") not in rep.names:
            continue
        dispeller = rep.names.get(rep.owner(e.get("sourceID", -1)))
        aura = rep.ability(e.get("extraAbilityGameID"))
        if (p and dispeller != p.name) or (ability and name_key(aura) != name_key(ability)):
            continue
        by_aura[aura][rep.names[e["targetID"]]] += 1
        if dispeller:
            by_pair[aura][(dispeller, rep.names[e["targetID"]])] += 1
    out = []
    for aura, targets in sorted(by_aura.items(), key=lambda kv: -sum(kv[1].values())):
        out.append(f"  {aura} dispelled off: " + ", ".join(f"{n} {c}" for n, c in targets.most_common(12)))
        if p:
            continue  # one dispeller: the targets say it all
        pairs = by_pair[aura].most_common(8)
        out.append("    most often (by > on): " + ", ".join(f"{a} > {b} {c}" for (a, b), c in pairs))
    return out


def _table_rows(t: dict, data_type: str) -> list[tuple[str, float, Counter]]:
    """(name, value, counts) rows of a WCL table, biggest first: applications of an aura; overhealing, hits,
    periodic ticks and crits of the rest."""
    rows = []
    if data_type in ("Buffs", "Debuffs"):
        for a in t.get("auras", []):
            rows.append((a["name"], a.get("totalUptime", 0), Counter(applications=a.get("totalUses", 0))))
    else:
        for e in cast_rows(t.get("entries", [])) if data_type == "Casts" else t.get("entries", []):
            counts: Counter = Counter(
                overheal=e.get("overheal") or 0, hits=e.get("hitCount") or 0, ticks=e.get("tickCount") or 0,
                crits=(e.get("critHitCount") or 0) + (e.get("critTickCount") or 0))
            if data_type == "DamageTaken":  # by ability: who took it
                counts.update({("on", x["name"]): x.get("total") or 0 for x in e.get("targets") or []})
            rows.append((e["name"], e.get("total", 0), counts))
    return sorted(rows, key=lambda r: -r[1])


def _extra(value: float, c: Counter, data_type: str, per_hit: bool) -> str:
    """A row's extra text: an aura's applications, overhealing, and (`per_hit`) hits, average hit and crits."""
    if data_type in ("Buffs", "Debuffs"):
        return f", {plural(c['applications'], 'application')}"
    extra = ""
    if data_type == "Healing" and c["overheal"]:
        extra = f", overheal {100 * c['overheal'] / (c['overheal'] + value):.0f}%"
    if per_hit and data_type in ("DamageDone", "Healing", "DamageTaken") and (n := c["hits"] + c["ticks"]):
        extra += (f", {plural(c['hits'], 'hit')}" + (f" and {plural(c['ticks'], 'tick')}" if c["ticks"] else "")
                  + f", {short(value / n)} on average")
        if c["crits"]:
            extra += f", {100 * c['crits'] / n:.0f}% crits"
    elif per_hit and c["hits"]:
        extra += f", {plural(c['hits'], 'hit')}"
    if on := sorted(((k[1], v) for k, v in c.items() if isinstance(k, tuple)), key=lambda kv: -kv[1]):
        extra += "; most on " + ", ".join(f"{who} {short(v)}" for who, v in on[:TARGETS_SHOWN])
    return extra



def _fmt_value(value: float, data_type: str, dur_s: float, total_time_ms: float | None) -> str:
    if data_type == "Casts":  # with the rate over the same combat time, so two pulls or players compare as is
        return plural(round(value), 'cast') + (f" ({60 * value / dur_s:.1f}/min)" if dur_s else "")
    if data_type in ("Buffs", "Debuffs"):
        return f"{100 * value / total_time_ms:.0f}% uptime" if total_time_ms else f"{value / 1000:.0f} s"
    return f"{short(value)} ({short(value / dur_s)}/s)" if dur_s else short(value)
