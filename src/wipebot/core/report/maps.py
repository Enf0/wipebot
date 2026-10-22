"""Where everyone stood at moments of a pull (`pull_map`), drawn on the boss room's map.

A moment is a time, a death, or each time a mechanic happened (positions.mechanic). A `Scene` is one moment as
both renderers draw it: draw.py's picture (Discord, stdio) and, from the same positions, `replay`'s whole pull for
the viewer (remote.py, pull_map_app.html). The room's map is the image Warcraft Logs' replay uses, laid over the box
the game's UiMapAssignment table gives it (knowledge.map_bounds).
"""
import asyncio
import base64
import io
import math
import struct
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field

from PIL import Image

from .. import knowledge
from ..classes import class_hex, class_rgb
from ..text import clock, parse_clock, plural
from . import draw
from .base import FactError, Report
from .positions import (
    Event,
    Facts,
    Mechanic,
    Positions,
    at,
    bosses,
    dead_since,
    dead_spans,
    distance_yd,
    facts,
    mechanic,
    off_facing,
    pull_positions,
    where_fell,
    window,
    window_positions,
)

LISTED = 12  # players, pairs or moments listed in a night's lines; the rest are counted
@dataclass
class Dot:
    name: str
    color: tuple[int, int, int]
    icon: str  # Warcraft Logs' spec icon
    role: str
    x: float  # hundredths of a yard
    y: float
    dead: bool = False  # drawn where they fell
    focus: bool = False  # asked about: on top, a larger name
    marked: bool = False  # the mechanic was on them or hit them


@dataclass
class Boss:
    name: str
    icon: str
    x: float
    y: float
    facing: float | None  # radians from +x, counter-clockwise


@dataclass
class Spot:
    """A raid plan's spot laid over the room."""
    x: float
    y: float
    color: tuple[int, int, int]  # its player's class colour
    label: str = ""  # written by it: its player when they're away from it, or an open spot's role
    ring: bool = False  # its player stood outside the tolerance: a red ring
    fill: bool = False  # the spot of the player whose every moment is shown
    to: str | None = None  # a line from it to where this player stood


@dataclass
class Mark:
    """A small point: one player's position at one of many moments."""
    x: float
    y: float
    color: tuple[int, int, int]
    label: str = ""


@dataclass
class Scene:
    title: str  # "Sszorak · pull 42"
    at: str  # "4:12"
    map_id: int
    bounds: tuple[float, float, float, float]  # minX, maxX, minY, maxY
    dots: list[Dot] = field(default_factory=list)
    bosses: list[Boss] = field(default_factory=list)
    spots: list[Spot] = field(default_factory=list)
    ring_yd: float = 0.0  # a spot ring's radius
    arrows: list[tuple[str, str]] = field(default_factory=list)  # (from, to) players: a hit from one's debuff
    marks: list[Mark] = field(default_factory=list)


def boss_icon(f: dict) -> str:
    return f"bosses/{f['encounterID'] % 50000}-icon.jpg"


async def scene(rep: Report, f: dict, t: float, pos: Positions, focus: Collection[int] = (),
                own: dict[int, float] | None = None, marked: Collection[int] = ()) -> Scene:
    """Everyone's position at report time `t` of pull `f` (each in `own` at their own time), the dead where they fell,
    and the bosses."""
    map_id = pos.map_id(near=t) or pos.map_id()
    if map_id is None:
        raise FactError(f"no positions logged around {clock(t - f['startTime'])}")
    sc = Scene(f"{rep.boss_key(f)} · pull {rep.pull_no(f)}", clock(t - f["startTime"]), map_id,
               await knowledge.map_bounds(map_id))
    for pid, p in rep.pull_players(f).items():
        xy, dead = pos.where(pid, (own or {}).get(pid, t))
        if xy:
            sc.dots.append(Dot(p.name, class_rgb(p.class_name), f"icons/{p.class_name}-{p.spec}.jpg", p.role, *xy,
                               dead, pid in focus, pid in marked))
    for b in bosses(rep, f):
        if xy := at(pos.samples.get(b, []), t):
            name = rep.actor(b) if len(bosses(rep, f)) > 1 else rep.boss_key(f)
            sc.bosses.append(Boss(name, boss_icon(f), *xy, pos.facing_at(b, t)))
    if not sc.dots:
        raise FactError(f"no positions logged around {clock(t - f['startTime'])}")
    return sc


async def drawn(sc: Scene) -> bytes:
    """The scene as a WebP picture, with the room's map and the icons."""
    icons = {}
    for path in {d.icon for d in sc.dots} | {b.icon for b in sc.bosses}:
        try:
            icons[path] = await knowledge.wcl_image(path)
        except knowledge.KnowledgeError:
            pass  # a plain marker instead
    room = await knowledge.map_image(sc.map_id)
    return await asyncio.to_thread(draw.render, sc, room, icons)  # seconds of CPU: off the event loop


# ---------- the tool ----------

@dataclass
class MapAnswer:
    text: str  # the facts, for the model
    at_ms: float  # the moment drawn, from the pull's start
    image: bytes | None  # the map as WebP, unless it wasn't asked for


async def pull_map(rep: Report, pull: int | None = None, boss: str | None = None, at_: str | None = None,
                   players: list[str] | None = None, event: Event | None = None, all_pulls: bool = False,
                   draw_it: bool = True) -> MapAnswer:
    """Where everyone stood at `at_`: a time (m:ss), or each time an ability happened; by default the first death of
    `players`, else the pull's first death. With an ability and `all_pulls`, every pull on the boss."""
    f = rep.fight(pull, boss)
    focus = [rep.in_pull(f, rep.player(n)) if not all_pulls else rep.player(n) for n in players or []]
    at_ms = parse_clock(at_) if at_ else None
    if at_ and at_ms is None:
        pulls = rep.by_boss[rep.boss_key(f)]
        fights = rep.ended(pulls) if all_pulls else [f]
        mech = await mechanic(rep, fights, at_, event)
        if all_pulls:
            ans = await _night(rep, mech, fights, focus, draw_it)
            ans.text = rep.still_going(pulls) + ans.text
            return ans
        return await _pull_mechanic(rep, f, mech, {p.id for p in focus}, draw_it, focus[0].name if focus else None)
    if all_pulls:
        raise FactError("all_pulls needs an ability in `at`: the moments to look at on every pull")
    deaths = await rep.deaths(f)
    if at_ms is not None:
        t, why = f["startTime"] + at_ms, ""
    elif first := next((d for d in deaths if d["targetID"] in {p.id for p in focus}), None) or (
            deaths[0] if deaths else None):
        t, why = first["timestamp"] - 1, f", just before {rep.names[first['targetID']]} died"
    else:
        t, why = f["endTime"] - 1000, ", the end of the pull"
    pos = await pull_positions(rep, f, boss_near=t)
    sc = await scene(rep, f, t, pos, {p.id for p in focus})
    lines = [f"A map of {sc.title} at {sc.at}{why} is shown to the user with this answer: "
             + _legend([p.name for p in focus])]
    lines += _around(rep, f, sc, focus[0].name if focus else None)
    return MapAnswer("\n".join(lines), t - f["startTime"], await drawn(sc) if draw_it else None)


def _legend(focus: list[str], extra: str = "") -> str:
    return ("each player's spec icon in a ring of their class colour (a tight stack nudged apart a little), the dead "
            "grey with a red cross where they fell, the boss with its facing as a gold cone, and the names"
            + (f", {', '.join(focus)} larger" if focus else "") + extra + ". Refer to it; don't describe it again.")


def _around(rep: Report, f: dict, sc: Scene, near: str | None = None) -> list[str]:
    """Each player's distance to the boss and how far off its facing they stood, and to `near` (the first player
    asked about: a soak or stack is around them); who isn't on the map."""
    lines = []
    to = next((d for d in sc.dots if d.name == near), None)
    if sc.bosses:
        b = sc.bosses[0]
        lines.append(f"Distance to {b.name} (yd)" + (", and degrees off the way it faced (0 straight in front, 180 "
                                                      "straight behind)" if b.facing is not None else "")
                     + (f"; then distance to {to.name} (yd)" if to else "") + ":")
        for d in sorted(sc.dots, key=lambda d: distance_yd((d.x, d.y), (b.x, b.y))):
            yd = distance_yd((d.x, d.y), (b.x, b.y))
            angle = f", {off_facing((d.x, d.y), (b.x, b.y), b.facing):.0f}°" if b.facing is not None and yd >= 1 else ""
            apart = f"; {distance_yd((d.x, d.y), (to.x, to.y)):.0f} from {to.name}" if to and d is not to else ""
            lines.append(f"- {d.name} ({d.role}){' dead' if d.dead else ''}: {yd:.0f}{angle}{apart}")
    else:
        lines.append(f"The boss's position isn't logged at {sc.at}.")
    if missing := [p.name for p in rep.pull_players(f).values() if p.name not in {d.name for d in sc.dots}]:
        lines.append(f"Not on the map (no position logged then): {', '.join(missing)}")
    if outside := [d.name for d in sc.dots if d.dead and not draw.in_view(sc, d.x, d.y)]:
        lines.append(f"Off the map's edge (died earlier, elsewhere): {', '.join(outside)}")
    return lines


def _what(rep: Report, mech: Mechanic, where: str) -> list[str]:
    head = f"{mech.described()} on {where}"
    others = [f"{k} {n}" for k, n in mech.logged.items() if n and k != mech.event and k != "applied"]
    note = (f" (also logged: {', '.join(others)}; `event` picks another)" if others else "")
    if mech.event is None:
        raise FactError(f"{mech.name} didn't happen on {where}" + note)
    return [head + f": {plural(len(mech.all()), 'time')}" + note + "."]


def _moment_line(rep: Report, fx: Facts) -> str:
    m = fx.moment
    names = [rep.names[p] for p in sorted(m.on, key=lambda p: m.on[p])]
    verb = "on" if m.carried else "hit"
    parts = [f"{verb} {len(names)}: " + ", ".join(names)]
    if m.carried:
        parts.append("hit by someone else's: " + (", ".join(
            f"{rep.names[dst]} by {rep.names[src]}'s" + (f" ({yd:.0f} yd apart)" if yd is not None else "")
            for src, dst, yd in fx.merged()) or "nobody"))
    if fx.closest:
        a, b, yd = fx.closest
        parts.append(f"the closest two with it: {rep.names[a]} and {rep.names[b]}, {yd:.0f} yd apart")
    return f"  {clock(m.at - m.f['startTime'])}: " + "; ".join(parts)


async def _pull_mechanic(rep: Report, f: dict, mech: Mechanic, focus: set[int], draw_it: bool,
                         near: str | None = None) -> MapAnswer:
    """Every moment of the mechanic on one pull, and a map of the one where it hit most players from someone else's
    (else the first)."""
    lines = _what(rep, mech, rep.label(f) + f" ({clock(f['endTime'] - f['startTime'])}, "
                  + ("KILL" if f.get("kill") else "wipe") + f", {rep.url(f)})")
    if mech.event in ("ends", "applied"):
        lines.append("Each player with it where they stood as theirs " + ("ran out" if mech.event == "ends" else
                     "was put on") + ". Hit by someone else's: a hit of its name from another player's (a burst as "
                     "theirs ran out, put on the player whose end was logged last before it; or an area around them "
                     "while they had it, put on the nearest).")
    moments = mech.all()
    pos = await pull_positions(rep, f)
    found = [facts(m, pos) for m in moments]
    lines += [_moment_line(rep, fx) for fx in found]
    best = max(found, key=lambda fx: (len(fx.merged()), -fx.moment.at))
    m = best.moment
    pos = await pull_positions(rep, f, boss_near=m.at)
    sc = await scene(rep, f, m.at, pos, focus, m.on, set(m.on) | {d for _, d, _ in best.bursts})
    sc.arrows = [(rep.names[s], rep.names[d]) for s, d in best.at_end]
    lines.append(f"A map of {sc.title} at {sc.at}"
                 + (" (the moment it hit most players from someone else's)" if best.bursts else "")
                 + " is shown to the user with this answer: " + _legend(
                     [rep.names[p] for p in focus],
                     ", a red ring around each player it " + ("was on or hit" if m.carried else "hit")
                     + (f", gold arrows from whose {mech.name} burst as it ran out to whom it hit" if best.at_end
                        else "")))
    lines += _around(rep, f, sc, near)
    return MapAnswer("\n".join(lines), m.at - f["startTime"], await drawn(sc) if draw_it else None)


async def _night(rep: Report, mech: Mechanic, fights: list[dict], focus, draw_it: bool) -> MapAnswer:
    """Every pull's moments counted per player and per pair; for a player asked about, each of their moments and a
    map of where they stood at every one."""
    lines = _what(rep, mech, f"every {rep.boss_key(fights[0])} pull ({len(fights)})")
    moments = mech.all()
    found = [facts(m, p) for m, p in zip(moments, await window_positions(rep, [window(m) for m in moments]),
                                         strict=True)]
    pairs = Counter((s, d) for fx in found for s, d, _ in fx.merged())
    on = Counter(p for m in moments for p in m.on)
    gave, took = Counter(s for s, _ in pairs.elements()), Counter(d for _, d in pairs.elements())
    verb = "had it" if mech.event in ("ends", "applied") else "hit"
    lines.append(f"Per player ({verb} / theirs hit someone else / hit by someone else's, moments):"
                 if pairs else f"Per player ({verb}, of {len(moments)}):")
    lines.append("  " + ", ".join(f"{rep.names[p]} {on[p]}" + (f"/{gave[p]}/{took[p]}" if pairs else "")
                                  for p in sorted(set(on) | set(took), key=lambda p: (-took[p] - gave[p], -on[p],
                                                                                         rep.names[p]))))
    if pairs:
        shown = pairs.most_common(LISTED)
        lines.append("Hit by someone else's (whose -> whom, moments): "
                     + ", ".join(f"{rep.names[s]} -> {rep.names[d]} {n}x" for (s, d), n in shown)
                     + (f"; {len(pairs) - LISTED} more pairs" if len(pairs) > LISTED else ""))
    if not focus:
        return MapAnswer("\n".join(lines), moments[0].at - moments[0].f["startTime"] if moments else 0, None)
    p = focus[0]
    marks: list[tuple[int, Mark]] = []
    plain, more = [], []
    for fx in found:
        m, mine = fx.moment, fx.merged()
        what = [f"hit {rep.names[d]}" for s, d, _ in mine if s == p.id]
        what += [f"hit by {rep.names[s]}'s" for s, d, _ in mine if d == p.id]
        if fx.closest and p.id in fx.closest[:2]:
            what.append(f"closest pair with it ({fx.closest[2]:.0f} yd)")
        if not what and p.id not in m.on:
            continue
        label = f"p{rep.pull_no(m.f)} {clock(m.at - m.f['startTime'])}"
        (more if what else plain).append(f"{label}: {', '.join(([verb] if p.id in m.on else []) + what)}")
        xy, dead = fx.pos.where(p.id, m.on.get(p.id, m.at))
        if xy and not dead and (map_id := fx.pos.map_id(near=m.at)):
            marks.append((map_id, Mark(*xy, class_rgb(p.class_name) if p.id in m.on else (150, 156, 170), label)))
    lines.append(f"{p.name} {verb} at {plural(len(plain) + sum(verb in x for x in more), 'moment')}; where more "
                 f"happened (pull m:ss): " + ("; ".join(more) or "nowhere"))
    if not draw_it or not marks:
        return MapAnswer("\n".join(lines), 0, None)
    map_id = Counter(k for k, _ in marks).most_common(1)[0][0]
    sc = Scene(f"{rep.boss_key(fights[0])} · {mech.name}, every pull", p.name, map_id,
               await knowledge.map_bounds(map_id), marks=[mk for k, mk in marks if k == map_id])
    lines.append(f"A map of where {p.name} stood at each of those moments is shown to the user with this answer: a "
                 f"dot each time, in their class colour when they {verb}, grey when not, labelled with the pull and "
                 "time, on the room (not relative to the raid, which can stand elsewhere on another pull). Refer to "
                 "it; don't describe it again.")
    return MapAnswer("\n".join(lines), 0, await drawn(sc))


# ---------- the replay (MCP App) ----------

REPLAY_STEP_MS = 1_000  # one sample a second; the viewer moves smoothly between them
REPLAY_UNIT = 50  # positions in half yards (hundredths of a yard / 50) from the map's corner, to fit in an int16
UNKNOWN = -32768
DEAD_FACING = -2  # in a track's facing: dead (between a death and a res); the position is where they fell


def pack_track(samples: list, facings: list[tuple[float, float]], start: float, steps: int,
           dead: list[tuple[float, float]], origin: tuple[float, float]) -> str:
    """Position and facing at each step from `start`, as base64 little-endian int16 triples: x and y in half yards
    from `origin`, facing in degrees (-1 unknown, DEAD_FACING while dead, at where they fell); x is UNKNOWN while the
    position is. About 8 characters a second, so a long pull for twenty players fits a tool result."""
    out: list[int] = []
    for i in range(steps):
        t = start + i * REPLAY_STEP_MS
        if (death := dead_since(dead, t)) is not None:
            xy, face = where_fell(samples, death), DEAD_FACING
        else:
            xy = at(samples, t)
            near = min(facings, key=lambda s: abs(s[0] - t), default=None)
            face = round(math.degrees(near[1])) % 360 if near and abs(near[0] - t) <= REPLAY_STEP_MS else -1
        out += ([round((xy[0] - origin[0]) / REPLAY_UNIT), round((xy[1] - origin[1]) / REPLAY_UNIT), face] if xy
                else [UNKNOWN, 0, -1])
    return base64.b64encode(struct.pack(f"<{len(out)}h", *out)).decode()


async def replay(rep: Report, f: dict, at_ms: float, focus: Collection[int] = ()) -> dict:
    """The whole pull for the replay viewer: every player's and boss's track, the deaths and when the wipe was
    decided; the room's map and the icons by their Warcraft Logs asset path (served at /img/<path>)."""
    start, steps = f["startTime"], int((f["endTime"] - f["startTime"]) // REPLAY_STEP_MS) + 1
    pos = await pull_positions(rep, f, boss_all=True)
    if (map_id := pos.map_id()) is None:
        raise FactError("no positions logged in this pull")
    bounds = await knowledge.map_bounds(map_id)
    origin = (bounds[0], bounds[2])
    deaths, decided, players, boss_ids = await rep.deaths(f), await rep.decided_at(f), rep.pull_players(f), bosses(
        rep, f)

    def tracks() -> list[str]:  # seconds of CPU on a long pull: off the event loop
        return [pack_track(pos.samples.get(b, []), pos.facing.get(b, []), start, steps, [], origin)
                for b in boss_ids] + [
            pack_track(pos.samples.get(pid, []), [], start, steps,
                       dead_spans(pos.samples.get(pid, []), pos.deaths.get(pid, []), f["endTime"]), origin)
            for pid in players]
    packed = await asyncio.to_thread(tracks)
    return {
        "title": f"{rep.boss_key(f)} · pull {rep.pull_no(f)}",
        "step_ms": REPLAY_STEP_MS, "duration_ms": f["endTime"] - start, "at_ms": round(at_ms),
        "decided": {"ms": round(decided.at_ms), "why": decided.why} if decided else None,
        "map": {"image": f"maps/{map_id}-map.png", "size": [(bounds[1] - bounds[0]) / REPLAY_UNIT,
                                                           (bounds[3] - bounds[2]) / REPLAY_UNIT],
                "origin": [origin[0], origin[1]]},  # in the log's coordinates: where raid_plan's spots are
        "unit_yd": REPLAY_UNIT / 100,
        "bosses": [{"name": rep.actor(b) if len(boss_ids) > 1 else rep.boss_key(f), "icon": boss_icon(f), "track": tr}
                   for b, tr in zip(boss_ids, packed, strict=False)],
        "players": [{"name": p.name, "spec": p.spec, "color": class_hex(p.class_name),
                     "icon": f"icons/{p.class_name}-{p.spec}.jpg", "focus": pid in focus, "track": tr}
                    for (pid, p), tr in zip(players.items(), packed[len(boss_ids):], strict=True)],
        "deaths": [{"name": rep.names[d["targetID"]], "ms": d["timestamp"] - start,
                    "by": rep.ability(d.get("killingAbilityGameID"))} for d in deaths if d["targetID"] in rep.names],
    }


def webp(data: bytes, quality: int = 80) -> bytes:
    """An image (the room's map) as WebP: a fraction of the PNG's size."""
    buf = io.BytesIO()
    Image.open(io.BytesIO(data), formats=draw.IMAGE_FORMATS).convert("RGB").save(buf, "WEBP", quality=quality)
    return buf.getvalue()
