"""A raid plan as raidstrats.gg's planner exports it: its scenes, the spots on them, and each scene told in words.

The planner's Export gives `raidstrats-addon-` and base64: one version byte (1), then the plan as JSON, deflated (raw,
no header). The JSON has the plan's name, raid and boss, and `scenes`, each with `items`: `kind` (`icon`, `boss`,
`trash`, `text`, `shape`, `line`), `label`, `x`, `y`, `w`, `h` (the item's top left corner and size, in % of the
plan's width and height). A player's `icon` is their class or role (`roles/rdps.svg`), `slotIndex` its number (what
an assignment note fills with a raider), `label` a name as typed (often its first letters) or nothing; a raid
marker's `icon` is `markers/<name>`; a boss ability carries `spellId`. A `shape` is a `circle`, `triangle` (with its
`corners`) or `rect`; a `line` (`line` or `arrow`) runs from `x1`, `y1` to `x2`, `y2`. A filled shape with icons
inside is a side panel (players who stay out, notes), not the room; a text box of several lines is a note, a short
one a label in the room.

The plan has no coordinates of the room, so a scene is told relative to what's drawn on it: the boss, the raid
markers, the labels and shapes, as the plan draws them (above is toward the top of the picture).
"""
import base64
import binascii
import colorsys
import json
import math
import re
import statistics
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from . import saved
from .base import FactError

EXPORT = re.compile(r"raidstrats-addon-([A-Za-z0-9+/=]+)")
NOTE_CHARS = 40  # a text this long, or of several lines, is a note; a shorter one labels a place in the room
AT, BY = 4, 9  # % of the plan's height: a point this near a marker is at it, or by it
NEAR = 5  # an arrow's end this near something starts or ends at it
ROW_GAP = 2.5  # icons in the side panel this near in height are one row
RING_AREA = math.pi * 6 ** 2  # a shape smaller than this around one thing marks it
CLOSE, FAR = 10, 30  # this near the boss is just beside it, this far is far from it


@dataclass
class Spot:
    label: str  # as typed on the plan; "" for an open spot
    role: str  # the icon's name: "tank", "mdps", "rdps", "healer", or a class
    x: float  # in % of the plan's height, from its left (so x and y have the same scale)
    y: float  # down
    slot: int | None = None  # its number on the plan, which an assignment note fills with a raider


@dataclass
class Scene:
    name: str
    spots: list[Spot]  # in the room
    aside: list[str]  # names in a side panel
    spells: list[int]  # boss abilities drawn on the scene
    notes: list[tuple[list[str], str]]  # (the side panel's names it's about, or none), text
    items: list[dict] = field(default_factory=list)  # everything drawn in the room, as exported
    aspect: float = 16 / 9  # the plan's width over its height
    panel: list[Group] = field(default_factory=list)  # the side panel, row by row
    panel_notes: list[str] = field(default_factory=list)  # the side panel's notes about no row


@dataclass
class Group:
    """A row of player icons in the side panel: the heading above it ("Purple Orb Carriers"), its spots with any tag
    written under one ("Goalkeeper"), and the notes about it (instructions)."""
    heading: str
    spots: list[tuple[Spot, str]]
    notes: list[str] = field(default_factory=list)


@dataclass
class Plan:
    plan_id: str
    name: str
    boss: str
    raid: str
    link: str
    scenes: list[Scene]
    export: str  # raidstrats-addon-..., as pasted (what the bot saves)
    saved_at: float | None = None  # epoch seconds, when the bot was given it
    saved_by: str = ""


def scene_link(plan: Plan, n: int) -> str:
    """The plan's view link opened on its scene `n` (from 1): raidstrats.gg's view link ends in the scene it opens
    on ("…?view=<id>/9"), as Viserio reads it. "" for a plan with no link."""
    return f"{re.sub(r'/\d+$', '', plan.link)}/{n}" if plan.link else ""


def parse(text: str) -> list[Plan]:
    """Every plan in a paste, each with the version mark before it (saved.py). A paste without one is an error."""
    plans = [decode(m[1], at, by) for chunk, at, by in saved.split(text) for m in EXPORT.finditer(chunk)]
    if not plans:
        raise FactError("no raid plan in that: paste the plan's export from raidstrats.gg (the planner's Export "
                        "button), the text starting raidstrats-addon-")
    return plans


def decode(blob: str, saved_at: float | None = None, saved_by: str = "") -> Plan:
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4))
        data = json.loads(zlib.decompress(raw[1:], -15))
    except (binascii.Error, zlib.error, ValueError) as e:
        raise FactError("the raid plan's export doesn't decode: copy it again from raidstrats.gg's Export, "
                        "whole") from e
    return Plan(data.get("planId") or data.get("planName") or "", data.get("planName") or "", data.get("boss") or "",
                data.get("raid") or "", data.get("planLink") or "", [_scene(s) for s in data.get("scenes") or []],
                "raidstrats-addon-" + blob, saved_at, saved_by)


def _middle(i: dict) -> tuple[float, float]:
    return i.get("x", 0) + i.get("w", 0) / 2, i.get("y", 0) + i.get("h", 0) / 2


def _scene(s: dict) -> Scene:
    items = s.get("items") or []
    icons = [i for i in items if i.get("kind") == "icon" and i.get("w") and i.get("h")]
    aspect = statistics.median(i["h"] / i["w"] for i in icons) if icons else 16 / 9  # icons are round: the plan's shape
    panels = [i for i in items if i.get("kind") == "shape" and _filled(i.get("fill"))]

    def aside(i: dict) -> bool:
        x, y = _middle(i)
        return any(p["x"] <= x <= p["x"] + p.get("w", 0) and p["y"] <= y <= p["y"] + p.get("h", 0) for p in panels)
    spots, beside, rows = [], [], defaultdict(list)  # rows: the side panel's names by the row they're in
    for i in items:
        if not _player(i):
            continue
        label = (i.get("label") or "").strip()
        label = "" if label.isdigit() else label  # the planner numbers a spot nobody is named for
        if aside(i):
            if label:
                rows[round(_middle(i)[1])].append(label)
                beside.append(label)
        else:
            x, y = _middle(i)
            slot = i.get("slotIndex")
            spots.append(Spot(label, _role(i), x * aspect, y, slot if isinstance(slot, int) else None))
    spells = list(dict.fromkeys(int(i["spellId"]) for i in items if str(i.get("spellId", "")).isdigit()))
    notes = []
    for i in items:
        if i.get("kind") == "text" and (text := " ".join((i.get("label") or "").split())):
            # a side panel's note is about the row of names nearest it
            row = min(rows, key=lambda y: abs(y - _middle(i)[1]), default=None) if aside(i) else None
            notes.append((rows[row] if row is not None else [], text))
    room = [i for i in items if i not in panels and not (i.get("kind") != "line" and aside(i))]
    groups, loose = _panel([i for i in items if i not in panels and i.get("kind") != "line" and aside(i)], aspect)
    return Scene(str(s.get("name") or ""), spots, beside, spells, notes, room, aspect, groups, loose)


def _panel(items: list[dict], aspect: float) -> tuple[list[Group], list[str]]:
    """The side panel's groups and its notes about none. A row of player icons is a group; its heading the short
    text just above it, a tag a short text just under one icon. A note goes to the group whose heading starts it,
    else to the one whose heading has its colour, else (a panel without headings) to the nearest row."""
    icons = sorted((i for i in items if _player(i)), key=lambda i: _middle(i)[1])
    texts = [i for i in items if i.get("kind") == "text" and (i.get("label") or "").strip()]
    rows: list[list[dict]] = []
    for i in icons:
        if rows and abs(_middle(i)[1] - _middle(rows[-1][0])[1]) <= ROW_GAP:
            rows[-1].append(i)
        else:
            rows.append([i])
    used: set[int] = set()
    headings: list[dict | None] = []
    for n, row in enumerate(rows):  # every heading before any tag: a row's heading sits just under the row above
        top = min(i.get("y", 0) for i in row)
        floor = max(i.get("y", 0) + i.get("h", 0) for i in rows[n - 1]) if n else -math.inf
        above = [t for t in texts if not _is_note(t) and floor <= _middle(t)[1] < top]
        headings.append(heading := max(above, key=lambda t: _middle(t)[1], default=None))
        if heading:
            used.add(id(heading))
    groups, colours = [], []
    for row, heading in zip(rows, headings, strict=True):
        spots = []
        for i in sorted(row, key=lambda i: i.get("x", 0)):
            under = [t for t in texts if not _is_note(t) and id(t) not in used
                     and i.get("x", 0) - 1 <= _middle(t)[0] <= i.get("x", 0) + i.get("w", 0) + 1
                     and 0 <= t.get("y", 0) - (i.get("y", 0) + i.get("h", 0)) <= i.get("h", 0)]
            tag = min(under, key=lambda t: t.get("y", 0), default=None)
            if tag:
                used.add(id(tag))
            label = (i.get("label") or "").strip()
            slot = i.get("slotIndex") if isinstance(i.get("slotIndex"), int) else None
            spots.append((Spot("" if label.isdigit() else label, _role(i), 0, 0, slot), _text(tag) if tag else ""))
        groups.append(Group(_text(heading) if heading else "", spots))
        colours.append((heading or {}).get("textColor"))
    loose = []
    for t in texts:
        if id(t) in used:
            continue
        first = (t.get("label") or "").strip().splitlines()[0].strip().casefold()
        headed = [g for g in groups if g.heading and g.heading.casefold() == first]
        coloured = [g for g, c in zip(groups, colours, strict=True) if c and c == t.get("textColor")]
        nearest = [min(range(len(rows)), key=lambda k: abs(_middle(rows[k][0])[1] - _middle(t)[1]))] if rows else []
        to = headed or coloured or ([] if any(g.heading for g in groups) else [groups[k] for k in nearest])
        if to:
            lines = (t.get("label") or "").strip().splitlines()
            to[0].notes.append(" ".join(" ".join(lines[1:] if headed else lines).split()))  # not the heading again
        else:
            loose.append(_text(t))
    return groups, loose


def _text(i: dict) -> str:
    return " ".join((i.get("label") or "").split())


def _player(i: dict) -> bool:
    """A player's icon: their class or role (an icon of an item or an ability isn't one)."""
    return i.get("kind") == "icon" and not i.get("spellId") and (i.get("icon") or "").startswith(("classes/", "roles/"))


def _is_note(i: dict) -> bool:
    """A text box of several lines or a long one: a note, not a label of a place in the room."""
    label = i.get("label") or ""
    return "\n" in label.strip() or len(" ".join(label.split())) >= NOTE_CHARS


def _marker(i: dict) -> str:
    """A raid marker's name ("skull"), or ""."""
    icon = i.get("icon") or ""
    return icon.split("/", 1)[1] if icon.startswith("markers/") else ""


def _role(i: dict) -> str:
    return (i.get("icon") or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _filled(fill: str | None) -> bool:
    """A fill that hides what's under it: a colour without transparency, or an rgba with alpha 0.5 or more."""
    if not fill or fill in ("transparent", "none"):
        return False
    m = re.match(r"rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\)", fill)
    return float(m[1]) >= 0.5 if m else True


# ---------- a scene in words ----------

Point = tuple[float, float]
DIRECTIONS = ["right of", "below right of", "below", "below left of", "left of", "above left of", "above",
              "above right of"]


def _rgb(colour: str | None) -> tuple[tuple[float, float, float], float] | None:
    """A CSS colour as rgb (0-1) and its alpha, or None."""
    if not colour:
        return None
    if m := re.fullmatch(r"#([0-9a-fA-F]{6})", colour.strip()):
        r, g, b = (int(m[1][k:k + 2], 16) / 255 for k in (0, 2, 4))
        return (r, g, b), 1.0
    if m := re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", colour.strip()):
        return (float(m[1]) / 255, float(m[2]) / 255, float(m[3]) / 255), float(m[4]) if m[4] else 1.0
    return None


def colour_name(colour: str | None) -> str:
    """A colour in a word ("green"), or "" for none."""
    found = _rgb(colour)
    if not found:
        return ""
    h, s, v = colorsys.rgb_to_hsv(*found[0])
    if s < 0.25:
        return "white" if v > 0.8 else "black" if v < 0.25 else "grey"
    for upto, name in ((15, "red"), (40, "orange"), (65, "yellow"), (165, "green"), (195, "teal"), (255, "blue"),
                       (290, "purple"), (345, "pink"), (360, "red")):
        if h * 360 < upto:
            return name
    return "red"


@dataclass
class _Thing:
    """Something drawn that has a place: a spot, the boss, an add, an ability, a marker or a label."""
    name: str
    at: Point


@dataclass
class _Shape:
    item: dict
    noun: str  # circle, cone, box
    colour: str
    centre: Point
    contains: Callable[[Point], bool]
    area: float
    corners: list[Point] = field(default_factory=list)
    ref: str = ""


class _Room:
    """A scene's drawing in one scale (% of the plan's height), and where things are on it in words."""

    def __init__(self, scene: Scene, who: Callable[[str, str], str | None], spell: Callable[[int], str]):
        a = scene.aspect
        self.who, self.spell = who, spell

        def pt(x: float, y: float) -> Point:
            return x * a, y
        self.pt = pt
        self.markers: list[_Thing] = []
        self.bosses: list[_Thing] = []
        self.adds: list[_Thing] = []
        self.abilities: list[_Thing] = []
        self.labels: list[_Thing] = []
        self.shapes: list[_Shape] = []
        self.lines: list[dict] = []
        for i in scene.items:
            kind, at = i.get("kind"), pt(*_middle(i))
            label = " ".join((i.get("label") or "").split())
            if kind == "icon" and (m := _marker(i)):
                self.markers.append(_Thing(m, at))
            elif kind == "icon" and str(i.get("spellId", "")).isdigit():  # its label says what it's for ("Gloom 1")
                ability = spell(int(i["spellId"]))
                self.abilities.append(_Thing(f'"{label}" ({ability})' if label else ability, at))
            elif kind == "boss":
                self.bosses.append(_Thing("the boss" if label in ("", "BOSS") else label, at))
            elif kind == "trash" and label:
                self.adds.append(_Thing(re.sub(r"( \(\d+\))+$", "", label), at))
            elif kind == "text" and label and not _is_note(i):
                self.labels.append(_Thing(label, at))
            elif kind == "shape" and (s := self._shape(i)):
                self.shapes.append(s)
            elif kind == "line" and None not in (i.get("x1"), i.get("y1"), i.get("x2"), i.get("y2")):
                ends = pt(i["x1"], i["y1"]), pt(i["x2"], i["y2"])
                if math.dist(*ends) >= 1:  # a click, not a line
                    self.lines.append(i | {"ends": ends})
        self.spots = [_Thing(self.spot_name(s), (s.x, s.y)) for s in scene.spots]
        things = self.spots + self.adds + self.abilities
        # a small ring around one thing marks it, it isn't a place: unless it's named ("Drage", an orb carrier's)
        self.shapes = [s for s in self.shapes if s.item.get("label") or s.area > RING_AREA
                       or sum(s.contains(t.at) for t in things) > 1]
        if len(self.bosses) > 1 and all(b.name == "the boss" for b in self.bosses):
            for n, b in enumerate(self.bosses, 1):
                b.name = f"boss {n}"
        self._name_shapes()

    def spot_name(self, s: Spot) -> str:
        num = f"#{s.slot} " if s.slot is not None else ""
        if not s.label:
            return f"{num}(open {s.role} spot)".strip()
        player = self.who(s.label, s.role)
        return f"{num}{s.label}" + (f" ({player})" if player and player != s.label else "") + f" ({s.role})"

    def _shape(self, i: dict) -> _Shape | None:
        shape, (cx, cy) = i.get("shape"), self.pt(*_middle(i))
        fill, stroke = _rgb(i.get("fill")), _rgb(i.get("stroke"))
        faint = fill is None or fill[1] < 0.5 or colour_name(i.get("fill")) == "white"
        colour = colour_name(i.get("stroke") if faint and stroke and colour_name(i.get("stroke")) != "white"
                             else i.get("fill")) or colour_name(i.get("stroke"))
        if shape == "circle":
            rx, ry = i.get("w", 0) * self.pt(1, 0)[0] / 2, i.get("h", 0) / 2
            if not rx or not ry:
                return None
            return _Shape(i, "circle", colour, (cx, cy),
                          lambda p: ((p[0] - cx) / rx) ** 2 + ((p[1] - cy) / ry) ** 2 <= 1, math.pi * rx * ry)
        if shape == "triangle" and len(i.get("corners") or []) == 3:
            cs = [self.pt(c["x"], c["y"]) for c in i["corners"]]
            area = abs((cs[1][0] - cs[0][0]) * (cs[2][1] - cs[0][1]) - (cs[2][0] - cs[0][0]) * (cs[1][1] - cs[0][1]))
            return _Shape(i, "cone", colour, (sum(c[0] for c in cs) / 3, sum(c[1] for c in cs) / 3),
                          lambda p: _in_triangle(p, cs), area / 2, cs)
        if shape == "rect":
            x0, y0 = self.pt(i.get("x", 0), i.get("y", 0))
            x1, y1 = self.pt(i.get("x", 0) + i.get("w", 0), i.get("y", 0) + i.get("h", 0))
            return _Shape(i, "box", colour, (cx, cy), lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1,
                          (x1 - x0) * (y1 - y0))
        return None

    def _name_shapes(self) -> None:
        """Each shape's name: its colour and kind, and its own label or a label written inside it."""
        named: Counter[str] = Counter()
        self.naming: list[_Thing] = []  # labels that name a shape: told as its name, not again on their own
        for s in self.shapes:
            label = " ".join((s.item.get("label") or "").split())
            if not label and (inside := [t for t in self.labels if s.contains(t.at)]):
                self.naming.append(t := min(inside, key=lambda t: math.dist(t.at, s.centre)))
                label = t.name
            s.ref = f"{s.colour} {s.noun}".strip() + (f' "{label}"' if label else "")
            named[s.ref] += 1
        seen: Counter[str] = Counter()
        for s in self.shapes:
            if named[s.ref] > 1:
                seen[s.ref] += 1
                s.ref = f"{s.ref} {seen[s.ref]}"

    def where(self, p: Point, skip: _Shape | None = None) -> str:
        """Where a point is: by the boss, the nearest raid markers, and the smallest shapes it's in."""
        parts = []
        if self.bosses:
            b = min(self.bosses, key=lambda b: math.dist(b.at, p))
            d = math.dist(b.at, p)
            if d <= AT:
                parts.append(f"on {b.name}")
            else:
                angle = math.degrees(math.atan2(p[1] - b.at[1], p[0] - b.at[0])) % 360
                how = "just " if d <= CLOSE else "far " if d >= FAR else ""
                parts.append(f"{how}{DIRECTIONS[round(angle / 45) % 8]} {b.name}")
        if self.markers:
            near = sorted(self.markers, key=lambda m: math.dist(m.at, p))
            d0 = math.dist(near[0].at, p)
            if d0 <= AT:
                parts.append(f"at the {near[0].name} marker")
            elif d0 <= BY:
                parts.append(f"by the {near[0].name} marker")
            elif len(near) > 1 and math.dist(near[1].at, p) <= 1.5 * d0:
                parts.append(f"between the {near[0].name} and {near[1].name} markers")
            else:
                parts.append(f"nearest the {near[0].name} marker")
        inside = sorted((s for s in self.shapes if s is not skip and s.contains(p)), key=lambda s: s.area)[:2]
        parts += [f"in the {s.ref}" for s in inside]
        return ", ".join(parts)

    def end(self, p: Point) -> str:
        """An arrow's end: the thing drawn there, or where it is."""
        things = self.spots + self.bosses + self.adds + self.labels + self.markers + self.abilities
        near = min(things, key=lambda t: math.dist(t.at, p), default=None)
        if near and math.dist(near.at, p) <= NEAR:
            return near.name if near in self.spots + self.bosses + self.adds else (
                f'"{near.name}"' if near in self.labels else f"the {near.name}"
                + (" marker" if near in self.markers else ""))
        return self.where(p)


def _in_triangle(p: Point, cs: list[Point]) -> bool:
    def side(a: Point, b: Point) -> float:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    d = [side(cs[0], cs[1]), side(cs[1], cs[2]), side(cs[2], cs[0])]
    return not (any(x < 0 for x in d) and any(x > 0 for x in d))


def describe(scene: Scene, who: Callable[[str, str], str | None], spell: Callable[[int], str],
             asked: Callable[[str, str], bool] = lambda label, icon: False) -> list[str]:
    """A scene in words: its notes, the side panel's groups (what each number does), then each spot, the boss, adds,
    abilities, labels, shapes and arrows, each
    placed relative to the boss and the raid markers. `who` names the raider a label and its icon (a class or a
    role) mean (or None), `spell` an ability's id; a spot `asked` (its label and icon) is listed first."""
    room = _Room(scene, who, spell)
    lines = []
    room_notes = [_text(i) for i in scene.items if i.get("kind") == "text" and _is_note(i)]
    lines += [f'Note: "{text}"' for text in room_notes + scene.panel_notes]
    for g in scene.panel:
        mine = any(asked(spot.label, spot.role) for spot, _ in g.spots)
        lines.append(("Asked: " if mine else "") + "In the side panel, not in the room: "
                     + (f"{g.heading}: " if g.heading else "")
                     + ", ".join(room.spot_name(spot) + (f" ({tag})" if tag else "") for spot, tag in g.spots)
                     + "".join(f' | "{note}"' for note in g.notes))
    spots = scene.spots
    order = sorted(range(len(spots)), key=lambda k: (not asked(spots[k].label, spots[k].role), spots[k].slot or 999))
    for k in order:
        lines.append(("Asked: " if asked(spots[k].label, spots[k].role) else "")
                     + f"{room.spots[k].name}: {room.where(room.spots[k].at)}")
    if len(room.bosses) > 1 or room.bosses and room.markers:
        for b in room.bosses:
            near = min(room.markers, key=lambda m: math.dist(m.at, b.at), default=None)
            lines.append(f"{b.name[0].upper()}{b.name[1:]}: " + (f"nearest the {near.name} marker" if near else ""))
    lines += _grouped(room, room.adds, "") + _grouped(room, room.abilities, "Ability icon ")
    lines += [f'Label "{t.name}": {room.where(t.at)}' for t in room.labels if t not in room.naming]
    lines += [_shape_line(room, s) for s in room.shapes]
    for ln in room.lines:
        kind = "arrow" if ln.get("shape") == "arrow" else "line"
        a, b = ln["ends"]
        lines.append(f"{colour_name(ln.get('stroke'))} {kind}".strip().capitalize()
                     + f" from {room.end(a)} to {room.end(b)}")
    return lines


def _grouped(room: _Room, things: list[_Thing], kind: str) -> list[str]:
    """Things of one name on one line, with how many and where ("Manifestation of Dread ×6: 4 in …; by …")."""
    groups: dict[str, list[_Thing]] = defaultdict(list)
    for t in things:
        groups[t.name].append(t)
    lines = []
    for name, ts in groups.items():
        places = Counter(room.where(t.at) for t in ts)
        lines.append(f"{kind}{name}" + (f" ×{len(ts)}" if len(ts) > 1 else "") + ": " + "; ".join(
            (f"{n} " if len(places) > 1 and n > 1 else "") + w for w, n in places.items()))
    return lines


def _shape_line(room: _Room, s: _Shape) -> str:
    """A shape, where it is (a cone from the boss: which way it points), and what's in it."""
    what = room.where(s.centre, s)
    if s.noun == "cone" and room.bosses:
        boss = min(room.bosses, key=lambda b: min(math.dist(b.at, c) for c in s.corners))
        apex = min(s.corners, key=lambda c: math.dist(c, boss.at))
        base = [c for c in s.corners if c is not apex]
        mid = ((base[0][0] + base[1][0]) / 2, (base[0][1] + base[1][1]) / 2)
        if math.dist(apex, boss.at) <= 2 * AT:
            what = f"from {boss.name} toward {room.where(mid, s)}"
    held = Counter([t.name for t in room.spots + room.adds + room.abilities if s.contains(t.at)]
                   + [f"the {m.name} marker" for m in room.markers if s.contains(m.at)])
    return f"The {s.ref}: {what}" + (": holds " + ", ".join(
        f"{n}×{h}" if n > 1 else h for h, n in held.items()) if held else "")
