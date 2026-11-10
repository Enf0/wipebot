"""Logged events as rows, for the tools that list events one by one (wcl_query's event lists): when in the pull,
what kind of event, who, which ability and on whom, by name; then whatever else the event holds. Counting them is
code's too (`counted`), so nobody adds rows up by eye, and a list too long to show is cut and says how much of it
was left out (`listed`)."""
import json
import math
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..text import exact
from .base import Report

# What a row's head already says of an event.
HEAD_KEYS = frozenset({"timestamp", "type", "sourceID", "sourceInstance", "targetID", "targetInstance",
                       "abilityGameID", "fight"})
# An event's other actors and abilities, by the key Warcraft Logs logs each under: named in a row as the head's are.
ACTOR_KEYS = {"killerID": ("killer", "killerInstance"), "attackerID": ("attacker", None)}
ABILITY_KEYS = {"killingAbilityGameID": "killingAbility", "extraAbilityGameID": "extraAbility"}
COUNTED_TOP = 8  # of each count (kinds, abilities, sources, targets), this many are named
COUNTED_FROM = 10  # a list of events this long is counted too, by kind, ability, source and target
CUT_ROWS = 3  # a list of events cut to fit keeps at least this many rows


def unit(rep: Report, actor_id: int | None, instance: int | None = None) -> str:
    """An actor of an event by name: a player, 'Broodling #3' for one of several of an enemy, "Earth Elemental
    (Enfo's)" for a pet or totem, 'Environment' for the fight itself."""
    if actor_id is None:
        return ""
    name = rep.actor(actor_id)
    if (owner := rep.owner(actor_id)) != actor_id:
        name += f" ({rep.actor(owner)}'s)"
    return f"{name} #{instance}" if instance else name


def head(rep: Report, e: dict, t0: float, with_id: frozenset[str] = frozenset()) -> str:
    """'0:15.250 applydebuff: Sszorak → Tempest → Martin': an event's moment from `t0` (report ms: its pull's
    start), its kind, and source → ability → target, each left out where the event has none (a cast without a
    target, a death, which names who died). An ability named in `with_id` comes with its id, 'Ravage [1277002]':
    one of several ids of that name in the list, which would read as the same event twice."""
    source = None if e.get("type") == "death" else e.get("sourceID")
    target = e.get("targetID")
    ability = e.get("abilityGameID")
    parts = [unit(rep, source, e.get("sourceInstance")),
             (rep.ability_ref(ability) if rep.ability(ability) in with_id else rep.ability(ability)) if ability else "",
             unit(rep, target, e.get("targetInstance")) if target != -1 else ""]
    named = " → ".join(p for p in parts if p)
    return f"{exact(e['timestamp'] - t0)} {e.get('type', 'event')}" + (f": {named}" if named else "")


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def as_logged(rep: Report, e: dict) -> str:
    """Everything an event holds beyond its head, as 'key value' pairs in the log's own order and numbers: its
    other actors and abilities by name (ACTOR_KEYS, ABILITY_KEYS), the rest as Warcraft Logs wrote it."""
    out = []
    instances = {inst for _, inst in ACTOR_KEYS.values() if inst}
    for k, v in e.items():
        if k in HEAD_KEYS or k in instances:
            continue
        if k in ACTOR_KEYS and isinstance(v, int):
            label, instance = ACTOR_KEYS[k]
            out.append(f"{label} {unit(rep, v, e.get(instance) if instance else None)}")
        elif k in ABILITY_KEYS and isinstance(v, int):
            if v:
                out.append(f"{ABILITY_KEYS[k]} {rep.ability(v)}")
        else:
            out.append(f"{k} {_value(v)}")
    return ", ".join(out)


def row(rep: Report, e: dict, t0: float, rest: Callable[[Report, dict], str] = as_logged,
        with_id: frozenset[str] = frozenset()) -> str:
    """An event as one row: its head, then `rest` of it after a semicolon (`as_logged`: everything the log has)."""
    more = rest(rep, e)
    return head(rep, e, t0, with_id) + (f"; {more}" if more else "")


def _top(counts: Counter) -> str:
    """'Tempest 20, Ravage 17 and 3 more (5 events)': the most common first."""
    shown = counts.most_common(COUNTED_TOP)
    rest = sum(counts.values()) - sum(n for _, n in shown)
    return ", ".join(f"{name} {n}" for name, n in shown) + (
        f" and {len(counts) - len(shown)} more ({rest} events)" if rest else "")


def counted(rep: Report, events: Iterable[dict]) -> str:
    """'Counted: 20 damage, 17 cast; by ability: Tempest 20, Ravage 17; from: Sszorak 37; on: Wutroll 12, ...': a
    list of events by kind, ability, source and target, so a count comes from code however many rows are shown."""
    kinds: Counter = Counter()
    abilities: Counter = Counter()
    sources: Counter = Counter()
    targets: Counter = Counter()
    for e in events:
        kinds[e.get("type", "event")] += 1
        if e.get("abilityGameID"):
            abilities[rep.ability(e["abilityGameID"])] += 1
        if e.get("type") != "death" and e.get("sourceID") is not None:
            sources[unit(rep, e["sourceID"])] += 1
        if e.get("targetID") not in (None, -1):
            targets[unit(rep, e["targetID"])] += 1
    parts = [", ".join(f"{n} {kind}" for kind, n in kinds.most_common())]
    parts += [f"{label}: {_top(c)}" for label, c in (("by ability", abilities), ("from", sources), ("on", targets))
              if c]
    return "Counted: " + "; ".join(parts) + "."


def _ids(rep: Report, events: Iterable[dict]) -> dict[str, set[int]]:
    """The ids of each ability a list of events names, by the ability's name."""
    ids: dict[str, set[int]] = {}
    for e in events:
        for key in ("abilityGameID", *ABILITY_KEYS):
            if isinstance(gid := e.get(key), int) and gid > 10:
                ids.setdefault(rep.ability(gid), set()).add(gid)
    return ids


def several_ids(rep: Report, events: Iterable[dict]) -> frozenset[str]:
    """The abilities a list of events has under more than one id (Mutilate [1277027] and [1277031] for one cast):
    the names a row gives with the id (`head`)."""
    return frozenset(name for name, ids in _ids(rep, events).items() if len(ids) > 1)


def ability_ids(rep: Report, events: Iterable[dict]) -> str:
    """'Ability ids: Tempest [1287083], Virulence [1300089, 1312189]' for the abilities a list of events names, or
    '' for none: rows name abilities, and the ids are what spell_info and a filter take."""
    ids = _ids(rep, events)
    return ("Ability ids: " + ", ".join(rep.ability_refs(name, found) for name, found in sorted(ids.items()))
            if ids else "")


def pull_at(rep: Report, at: float, fight_id: int | None = None) -> dict | None:
    """The boss pull a moment of a report (its ms) is in: the fight an event names, else the one it falls in; None
    for trash and the time between pulls."""
    if fight_id is not None:
        return next((f for f in rep.fights if f["id"] == fight_id), None)
    return next((f for f in rep.fights if f["startTime"] <= at <= f["endTime"]), None)


@dataclass
class Listed:
    """A list of events as lines. `where` says which pull they are in and what their times count from (" in Sszorak
    pull 13, times from the pull's start"), for the caller's own first line; `lines` are the counts (from
    COUNTED_FROM events, or cut), a row an event under its pull's name where there are several pulls, what was left
    out, and the ids of the abilities named; `left_out` is how many events have no row."""
    where: str
    lines: list[str]
    left_out: int = 0


def listed(rep: Report, events: list[dict], rest: Callable[[Report, dict], str] = as_logged,
           room: int | None = None, most: int | None = None) -> Listed:
    """A list of events (in its own order) as lines, each time from its pull's start. With `room` (characters) or
    `most` (rows), only the first rows that fit, then how many were left out and up to when; the counts are of all."""
    pulls: list[tuple[dict | None, list[dict]]] = []  # in the list's order: a pull, its events
    for e in events:
        f = pull_at(rep, e["timestamp"], e["fight"] if isinstance(e.get("fight"), int) else None)
        if pulls and (pulls[-1][0] or {}).get("id") == (f or {}).get("id"):
            pulls[-1][1].append(e)
        else:
            pulls.append((f, [e]))
    where = (f" in {rep.label(pulls[0][0])}, times from the pull's start" if len(pulls) == 1 and pulls[0][0]
             else ", times from the report's start" if len(pulls) <= 1
             else f" over {len(pulls)} pulls, each time from its pull's start")
    with_id = several_ids(rep, events)
    body: list[tuple[str, bool]] = []  # each line, and whether it is an event's row
    for f, es in pulls:
        if len(pulls) > 1:
            body.append((f"{rep.label(f)}:" if f else "Outside the boss pulls (times from the report's start):",
                         False))
        body += [(row(rep, e, f["startTime"] if f else 0, rest, with_id), True) for e in es]
    counts, ids = counted(rep, events), ability_ids(rep, events)
    kept, size = body, sum(len(line) + 1 for line, _ in body)
    if (room is not None and size > room) or (most is not None and len(events) > most):
        left = math.inf if room is None else room - len(counts) - len(ids) - 300  # less the line on what was left out
        kept = []
        for line, is_row in body:
            shown = sum(r for _, r in kept)
            if shown >= CUT_ROWS and (left < len(line) + 1 or (most is not None and shown >= most)):
                break
            kept.append((line, is_row))
            left -= len(line) + 1
        while kept and not kept[-1][1]:  # a pull's name with none of its rows under it
            kept.pop()
    left_out = len(events) - sum(r for _, r in kept)
    lines = [counts] if left_out or len(events) >= COUNTED_FROM else []
    lines += [line for line, _ in kept]
    if left_out:
        last, (f, _) = events[-1], pulls[-1]
        lines.append(f"… {left_out:,} more events left out, the last at "
                     f"{exact(last['timestamp'] - (f['startTime'] if f else 0))}"
                     + (f" of {rep.label(f)}" if f and len(pulls) > 1 else "")
                     + f": the counts above are of all {len(events):,}.")
    return Listed(where, [*lines, *([ids] if ids else [])], left_out)
