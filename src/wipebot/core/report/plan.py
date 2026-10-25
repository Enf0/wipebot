"""A raid plan drawn on raidstrats.gg, checked against pulls: where each player stood at each moment of the plan's
mechanic (positions.mechanic), against their spot on the plan; or shown as it is, with no pull. The plan itself, as
raidstrats.gg exports it, is plan_export.py.

The plan has no coordinates of the room. At each moment it is laid over where the raid stood (turned, scaled, moved,
and mirrored when that fits better: a raid uses a plan on either side of the room), fitted again without the worst
quarter so players away from their spot don't pull it. A player is off their spot when further from it than the
tolerance: half the median distance between neighbouring spots, in the room after the fit.
"""
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import knowledge
from ..classes import class_rgb
from ..text import clock, fold, parse_clock, plural
from . import maps, saved
from .base import FactError, Player, PullRef, Report
from .plan_export import Plan, Scene, describe, parse, scene_link
from .positions import Facts, Mechanic, Moment, facts, mechanic, pull_positions, window, window_positions

MIN_FIT = 4  # a fit turns, scales and moves the plan (four numbers): it needs more players than that to mean anything
MIN_TOLERANCE_YD = 3  # positions are interpolated between samples a second or two apart: nearer than this is noise
ROLES = {"tank", "healer", "mdps", "rdps", "dps", ""}  # a plan's player icon is one of these or a class


# ---------- laying the plan over the room ----------

@dataclass
class Fit:
    mirrored: bool
    scale: float  # yards per plan unit
    turn: float  # radians
    plan_mid: tuple[float, float]  # after mirroring
    room_mid: tuple[float, float]
    median_yd: float = 0.0  # the middle player's distance from their spot
    tolerance: float = 0.0  # further than this from their spot is off it

    @property
    def holds(self) -> bool:
        """The raid stood as the plan draws it."""
        return self.median_yd <= self.tolerance

    def place(self, p: tuple[float, float]) -> tuple[float, float]:
        """A point of the plan in the room, in yards."""
        x, y = (-p[0] if self.mirrored else p[0]) - self.plan_mid[0], p[1] - self.plan_mid[1]
        c, s = math.cos(self.turn), math.sin(self.turn)
        return self.scale * (c * x - s * y) + self.room_mid[0], self.scale * (s * x + c * y) + self.room_mid[1]

    def unplace(self, q: tuple[float, float]) -> tuple[float, float]:
        """A point of the room (yards) on the plan: `place` undone."""
        x, y = (q[0] - self.room_mid[0]) / self.scale, (q[1] - self.room_mid[1]) / self.scale
        c, s = math.cos(-self.turn), math.sin(-self.turn)
        x, y = c * x - s * y + self.plan_mid[0], s * x + c * y + self.plan_mid[1]
        return (-x if self.mirrored else x), y


def _similarity(plan: list[tuple[float, float]], room: list[tuple[float, float]], mirrored: bool) -> Fit:
    """The turn, scale and move that best lay `plan` over `room` (least squares; Procrustes)."""
    pts = [(-x if mirrored else x, y) for x, y in plan]
    n = len(pts)
    pm = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    rm = (sum(q[0] for q in room) / n, sum(q[1] for q in room) / n)
    a = b = d = 0.0
    for (x, y), (u, v) in zip(pts, room, strict=True):
        x, y, u, v = x - pm[0], y - pm[1], u - rm[0], v - rm[1]
        a, b, d = a + x * u + y * v, b + x * v - y * u, d + x * x + y * y
    return Fit(mirrored, math.hypot(a, b) / d if d else 0.0, math.atan2(b, a), pm, rm)


def fit(plan: list[tuple[float, float]], room: list[tuple[float, float]],
        spots: list[tuple[float, float]] | None = None) -> Fit | None:
    """The plan laid over where the raid stood, mirrored or not, whichever fits better; refitted twice without the
    worst quarter. Its tolerance is half the median distance between neighbouring `spots` (all of the plan's) once
    placed, at least MIN_TOLERANCE_YD. None with fewer than MIN_FIT points."""
    if len(plan) < MIN_FIT:
        return None
    best = None
    for mirrored in (False, True):
        keep = list(range(len(plan)))
        for _ in range(3):
            f = _similarity([plan[i] for i in keep], [room[i] for i in keep], mirrored)
            err = [math.dist(f.place(p), q) for p, q in zip(plan, room, strict=True)]
            cut = sorted(err)[int(0.75 * (len(err) - 1))]
            if sum(e <= cut for e in err) >= MIN_FIT:
                keep = [i for i, e in enumerate(err) if e <= cut]
        f.median_yd = statistics.median(err)
        if best is None or f.median_yd < best.median_yd:
            best = f
    placed = [best.place(p) for p in spots or plan]
    gaps = [min(math.dist(a, b) for b in placed if b is not a) for a in placed] if len(placed) > 1 else []
    best.tolerance = max(MIN_TOLERANCE_YD, statistics.median(gaps) / 2 if gaps else 0)
    return best


# ---------- checking ----------

@dataclass
class Stood:
    name: str
    label: str
    off_yd: float
    at_spot: str | None  # whose spot they stood at instead (a player's name, or "an open <role> spot")


@dataclass
class Checked:
    facts: Facts
    fit: Fit | None
    stood: list[Stood]
    dead: list[str]
    unplaced: list[str]  # no position logged then
    where: dict[str, tuple[float, float]] = field(default_factory=dict)  # each placed player, yards
    owners: dict[int, str] = field(default_factory=dict)  # a spot (its index) -> its player's name

    @property
    def moment(self) -> Moment:
        return self.facts.moment

    def off(self) -> list[Stood]:
        return sorted((s for s in self.stood if self.fit and s.off_yd > self.fit.tolerance), key=lambda s: -s.off_yd)


def few(mech: Mechanic) -> int:
    """Moments of a debuff on fewer than MIN_FIT players (one passed on by a burst): not what a plan is drawn for."""
    return sum(bool(m.carried) and len(m.on) < MIN_FIT for m in mech.all())


async def check(rep: Report, scene: Scene, mech: Mechanic) -> list[Checked]:
    """Every moment of the mechanic (but `few`) against the scene's spots: each player where they stood at their own
    time (as theirs ran out), the others at the moment's middle."""
    moments = [m for m in mech.all() if not m.carried or len(m.on) >= MIN_FIT]
    out = []
    for m, pos in zip(moments, await window_positions(rep, [window(m) for m in moments]), strict=True):
        players = rep.pull_players(m.f)
        owner = {i: p for i, s in enumerate(scene.spots) if (p := rep.find_player(s.label, players.values()))}
        c = Checked(facts(m, pos), None, [], [], [], owners={i: p.name for i, p in owner.items()})
        for p in players.values():
            xy, dead = pos.where(p.id, m.on.get(p.id, m.at))
            if xy and not dead:
                c.where[p.name] = (xy[0] / 100, xy[1] / 100)
            elif p in owner.values():
                (c.dead if dead else c.unplaced).append(p.name)
        who = [i for i, p in owner.items() if p.name in c.where]
        c.fit = fit([(scene.spots[i].x, scene.spots[i].y) for i in who], [c.where[owner[i].name] for i in who],
                    [(s.x, s.y) for s in scene.spots])
        if c.fit:
            placed = [c.fit.place((s.x, s.y)) for s in scene.spots]
            for i in who:
                q = c.where[owner[i].name]
                off = math.dist(placed[i], q)
                near = min((j for j in range(len(placed)) if j != i), key=lambda j: math.dist(placed[j], q))
                at_spot = None
                if off > c.fit.tolerance and math.dist(placed[near], q) <= c.fit.tolerance:
                    at_spot = owner[near].name if near in owner else f"an open {scene.spots[near].role} spot"
                c.stood.append(Stood(owner[i].name, scene.spots[i].label, off, at_spot))
        out.append(c)
    return out


def _spot_of(at_spot: str) -> str:
    return at_spot if at_spot.startswith("an open") else f"{at_spot}'s spot"


def _moment_line(rep: Report, c: Checked) -> str:
    t = clock(c.moment.at - c.moment.f["startTime"])
    if c.fit is None:
        return f"  {t}: fewer than {MIN_FIT} players with a spot and a position, too few to lay the plan over"
    head = (f"  {t} (plan {'mirrored' if c.fit.mirrored else 'as drawn'}, the middle player {c.fit.median_yd:.0f} yd "
            f"from their spot, off past {c.fit.tolerance:.0f} yd)")
    if not c.fit.holds:
        return head + ": the raid wasn't standing as the plan draws it"
    parts = ["off their spot: " + (", ".join(f"{s.name} {s.off_yd:.0f} yd" + (
        f" (at {_spot_of(s.at_spot)})" if s.at_spot else "") for s in c.off()) or "nobody")]
    if c.dead:
        parts.append("dead: " + ", ".join(c.dead))
    if c.unplaced:
        parts.append("no position logged: " + ", ".join(c.unplaced))
    if bursts := c.facts.merged():
        parts.append("hit by someone else's: " + ", ".join(
            f"{rep.names[d]} by {rep.names[s]}'s" + (f" ({yd:.0f} yd apart)" if yd is not None else "")
            for s, d, yd in bursts))
    return head + ": " + "; ".join(parts)


def _plan_lines(rep: Report, scene: Scene, players: dict[int, Player]) -> list[str]:
    named = [s for s in scene.spots if s.label]
    vs = players.values()
    shown = ", ".join(f"{s.label} ({p.name})" if (p := rep.find_player(s.label, vs)) and p.name != s.label
                      else s.label for s in named)
    open_ = Counter(s.role for s in scene.spots if not s.label)
    lines = [f"Spots in the room ({len(named)}): {shown}"
             + (f"; open: {', '.join(f'{n} {r}' for r, n in open_.items())}" if open_ else "")]
    if scene.aside:
        lines.append(f"In the plan's side panel, not in the room: {', '.join(scene.aside)}")
    if missing := [s.label for s in named if not rep.find_player(s.label, vs)]:
        lines.append(f"Not matched to one player of the pull: {', '.join(missing)}")
    on_plan = {p.id for label in [*(s.label for s in named), *scene.aside] if (p := rep.find_player(label, vs))}
    if others := sorted(p.name for p in vs if p.id not in on_plan):
        lines.append(f"In the pull but not on the plan: {', '.join(others)}")
    if scene.notes:
        def about(labels: list[str]) -> str:
            names = [p.name if (p := rep.find_player(label, vs)) else label for label in labels]
            return f"for {', '.join(names)}: " if names else ""
        lines.append("The plan's notes: " + " | ".join(f'{about(who)}"{text}"' for who, text in scene.notes))
    return lines


def _head(plan: Plan) -> str:
    src = saved.source(plan.saved_at, plan.saved_by)
    return (f'The raid plan "{plan.name}"' + (f" for {plan.boss}" if plan.boss else "")
            + (f" ({plan.link})" if plan.link else "") + (f", {src}" if src else ""))


async def _mechanic(rep: Report, scene: Scene, fights: list[dict], ability: str | None) -> tuple[Mechanic | None, str]:
    """The mechanic a scene is for: `ability` if asked, else of the scene's abilities the one on most players (a
    debuff first), else the one that happened most."""
    if ability:
        return await mechanic(rep, fights, ability), ""
    if not scene.spells:
        return None, "The plan has no boss ability on it to tell when to check: ask for the one it's for (`at`)."
    await rep.name_spells(set(scene.spells))
    found = []
    for s in scene.spells:  # by name: the plan's id may not be the one the log has
        try:
            found.append(await mechanic(rep, fights, rep.ability(s)))
        except FactError:  # not in this report
            continue
    happened = [m for m in found if m.event]
    if not happened:
        return None, "None of the plan's abilities happened: " + ", ".join(map(rep.ability_ref, scene.spells)) + "."
    best = max(happened, key=lambda m: (m.event in ("ends", "applied"), sum(len(x.on) for x in m.all()),
                                        len(m.all())))
    others = [f"{m.name} [{m.ids[0]}]" for m in found if m is not best]
    return best, ("Also on the plan: " + ", ".join(others) + "." if others else "")


def _how(mech: Mechanic) -> str:
    when = ("each player where they stood as theirs ran out, the others at the middle of that moment"
            if mech.event == "ends" else "everyone where they stood then")
    return (f"Checked at each {mech.described()} ({when}). The plan is laid over where the raid stood each time "
            "(turned, scaled, mirrored if that fits better), so off means further from their spot than half the "
            f"distance between neighbouring spots (at least {MIN_TOLERANCE_YD} yd), relative to the rest of the raid, "
            "not to the room." + (f" Left out: {plural(few(mech), 'time')} it was on fewer than {MIN_FIT} players "
                                  "(passed on)." if few(mech) else ""))


@dataclass
class PlanAnswer:
    text: str
    images: list[bytes]  # WebP maps
    fight: dict | None = None  # one pull's: what the replay viewer plays
    overlay: dict | None = None  # one pull's: the plan at each moment, for the viewer (`_overlay`)


async def plan_check(rep: Report, text: str, pull: PullRef = None, boss: str | None = None,
                     night: bool = False, at: str | None = None, player: str | None = None,
                     draw: bool = True, latest: bool = False) -> PlanAnswer:
    """The plan's spots against where each player stood at each moment of its mechanic: one pull, or with `night`
    every pull on the boss (each against the plan's version for it, or with `latest` the newest). `at`: the ability
    to check at, or on one pull the moment (m:ss) to draw. With `draw`, a map: one pull's moment (`at`, else the one
    with most players off their spot), or with `night` and `player` that player at every moment."""
    plans = parse(text)
    if boss is None:  # the plan's boss, when the report has it
        boss = next((p.boss for p in plans if p.boss and _boss_of(rep, p) is not None), None)
    f = rep.fight(pull, boss)
    key = rep.boss_key(f)
    versions: dict[str, list[Plan]] = defaultdict(list)
    for p in sorted(plans, key=lambda p: p.saved_at or 0):
        if _boss_of(rep, p) in (key, ""):
            versions[p.plan_id].append(p)
    if not versions:
        raise FactError(f"the raid plan is for {plans[0].boss or 'another boss'}, not {key}")
    asked = (rep.player(player) if night else rep.in_pull(f, rep.player(player))).name if player else None
    ability = at if at and parse_clock(at) is None else None
    out, images, overlays = [], [], []
    pulls = rep.ended(rep.by_boss[key]) if night else [f]
    for vs in versions.values():
        used = vs[-1:] if latest else vs
        if night:
            lines, image = await _night(rep, used, pulls, ability, asked, draw)
        else:
            lines, image, overlay = await _pull(rep, used, f, ability, None if ability else at, asked, draw)
            overlays += [overlay] if overlay else []
        checked = max((_version(rep, used, g) for g in pulls), key=vs.index)
        if checked is not vs[-1]:
            lines.insert(1, f"A newer version of this plan is saved ({saved.source(vs[-1].saved_at, vs[-1].saved_by)})"
                            ": each pull is checked against the version saved before it began; `latest` checks "
                            "against the newest.")
        out += lines
        images += [image] if image else []
    if night:
        return PlanAnswer(rep.still_going(rep.by_boss[key]) + "\n".join(out), images)
    return PlanAnswer("\n".join(out), images, f, {"at_ms": overlays[0]["at_ms"], "moments": sorted(
        (m for o in overlays for m in o["moments"]), key=lambda m: m["ms"])} if overlays else None)


async def plan_show(rep: Report, text: str, boss: str | None = None, player: str | None = None) -> str:
    """The newest version of each plan for `boss` (else the latest pull's boss), every scene in words (describe),
    with no pull. The report names the raiders the plan's labels mean; `player`'s spot is listed first."""
    plans = parse(text)
    wanted = boss or rep.boss(None)
    found = [p for p in plans if p.boss and (knowledge.matches_boss(wanted, p.boss)
                                             or knowledge.matches_boss(p.boss, wanted))]
    if not found:
        kept = ", ".join(dict.fromkeys(f'"{p.name}" ({p.boss or "no boss"})' for p in plans))
        raise FactError(f"no raid plan for {wanted}" + (" (the latest pull's boss)" if not boss else "")
                        + f"; the plans are {kept}: ask for one of their bosses")
    newest: dict[str, Plan] = {}
    for p in sorted(found, key=lambda p: p.saved_at or 0):
        newest[p.plan_id] = p
    shown = sorted(newest.values(), key=lambda p: -(p.saved_at or 0))
    await rep.name_spells({s for p in shown for sc in p.scenes for s in sc.spells})
    asked = rep.find_player(player) if player else None

    def who(label: str, icon: str) -> str | None:
        """The raider a label means, among those of the icon's class when it's one ("demonhunter", "DemonHunter")."""
        among = [p for p in rep.players.values() if icon in ROLES or icon.replace("-", "") == p.class_name.lower()]
        return p.name if label and (p := rep.find_player(label, among)) else None

    def is_asked(label: str, icon: str) -> bool:
        return bool(player and label and (asked and who(label, icon) == asked.name
                                          or fold(label).lower() == fold(player).lower()))
    lines = []
    if len(shown) > 1:
        lines.append(f"{len(shown)} plans for {shown[0].boss}, newest first: "
                     + ", ".join(f'"{p.name}"' for p in shown) + ".")
    for p in shown:
        lines.append(_head(p) + f": {plural(len(p.scenes), 'scene')}, shown as drawn, with no pull. Places are as "
                     "the plan draws them (above is toward the top of the picture), by the boss and the raid markers. "
                     "#N is a spot's number on the plan, which an assignment note fills with a raider; a name is as "
                     "typed on the plan, with the raider it matches in brackets.")
        for n, sc in enumerate(p.scenes, 1):
            if len(p.scenes) > 1:  # with its link, to point a raider at the scene itself
                lines.append(f"Scene {sc.name}" + (f" ({link})" if (link := scene_link(p, n)) else "") + ":")
            lines += ["  " + line for line in describe(sc, who, rep.ability_ref, is_asked)]
        if player and not any(is_asked(s.label, s.role) for sc in p.scenes for s in sc.spots):
            lines.append(f"{player} isn't named on this plan: spots are by number, filled by the night's "
                         "assignment.")
    return "\n".join(lines)


def _boss_of(rep: Report, plan: Plan) -> str | None:
    """The report's boss a plan is for: "" for any, None for one the report doesn't have."""
    if not plan.boss:
        return ""
    try:
        return rep.boss(plan.boss)
    except FactError:
        return None


def _version(rep: Report, versions: list[Plan], f: dict) -> Plan:
    return saved.pick(versions, (rep.start_ms + f["startTime"]) / 1000, lambda p: p.saved_at)


async def _pull(rep: Report, versions: list[Plan], f: dict, ability: str | None, at: str | None, asked: str | None,
                draw: bool) -> tuple[list[str], bytes | None, dict | None]:
    plan = _version(rep, versions, f)
    lines = [_head(plan) + f", checked against {rep.label(f)} ({clock(f['endTime'] - f['startTime'])}, "
             + ("KILL" if f.get("kill") else "wipe") + f", {rep.url(f)})."]
    if plan.saved_at and plan.saved_at > (rep.start_ms + f["startTime"]) / 1000:
        lines.append("It was saved after this pull began: the plan for this pull may have been different.")
    shown: list[tuple[Scene, Checked]] = []
    named: dict[str, str] = {}  # a scene's ability
    for scene in plan.scenes:
        if len(plan.scenes) > 1:
            lines.append(f"Scene {scene.name}:")
        lines += _plan_lines(rep, scene, rep.pull_players(f))
        mech, note = await _mechanic(rep, scene, [f], ability)
        lines += [note] if note else []
        if mech is None:
            continue
        if not mech.event:
            lines.append(f"{mech.name} [{mech.ids[0]}] didn't happen on this pull.")
            continue
        checked = await check(rep, scene, mech)
        named[scene.name] = mech.name
        lines += [_how(mech), f"{mech.name}: {len(checked)} time{'s' if len(checked) != 1 else ''}:"]
        lines += [_moment_line(rep, c) for c in checked]
        shown += [(scene, c) for c in checked if c.fit and c.fit.holds]
    if not shown:
        return lines, None, None
    if at and (ms := parse_clock(at)) is not None:
        want = f["startTime"] + ms
        scene, c = min(shown, key=lambda sc: abs(sc[1].moment.at - want))
    else:
        scene, c = max(shown, key=lambda sc: (len(sc[1].off()), -sc[1].moment.at))
    overlay = _overlay(plan, f, shown, c, named)
    if not draw:
        return lines, None, overlay
    sc = await _moment_scene(rep, scene, c, asked)
    lines.append(f"A map of {sc.title} at {sc.at} is shown to the user with this answer: each spot of the plan as a "
                 "dot in its player's class colour, laid over where the raid stood; for a player off their spot, a red "
                 "ring of the tolerance's radius around it, their name by it and a red line to where they stood; gold "
                 "arrows from whose debuff hit someone to whom; each player's spec icon where they stood, the dead "
                 "where they fell." + ("" if at else " The moment drawn is the one with most players off their spot.")
                 + " Refer to it; don't describe it again.")
    return lines, await maps.drawn(sc), overlay


def _overlay(plan: Plan, f: dict, shown: list[tuple[Scene, Checked]], first: Checked, named: dict[str, str]) -> dict:
    """The plan at each moment it held, for the replay viewer: the moment to open at (`first`), and per moment its
    spots laid over the room (in the log's coordinates, as the tracks are), each with its player and whether they
    stood off it, the tolerance, and who was off by how far."""
    moments = []
    for scene, c in shown:
        assert c.fit
        off = {s.name for s in c.off()}
        spots = []
        for i, spot in enumerate(scene.spots):
            x, y = c.fit.place((spot.x, spot.y))
            owner = c.owners.get(i, "")
            spots.append({"x": round(x * 100), "y": round(y * 100), "player": owner, "role": spot.role,
                          "off": owner in off})
        moments.append({"ms": round(c.moment.at - f["startTime"]), "ability": named[scene.name], "plan": plan.name,
                        "scene": scene.name,
                        "tolerance_yd": round(c.fit.tolerance, 1), "spots": spots,
                        "off": [{"name": s.name, "yd": round(s.off_yd), "at": _spot_of(s.at_spot) if s.at_spot
                                 else None} for s in c.off()]})
    return {"at_ms": round(first.moment.at - f["startTime"]), "moments": moments}


async def _moment_scene(rep: Report, scene: Scene, c: Checked, asked: str | None) -> maps.Scene:
    """One moment drawn: everyone where they were measured, the plan's spots laid over them, the hits."""
    m, f = c.moment, c.moment.f
    players = rep.pull_players(f)
    focus = {p.id for p in players.values() if p.name == asked}
    sc = await maps.scene(rep, f, m.at, await pull_positions(rep, f, boss_near=m.at), focus, m.on)
    off = {s.name for s in c.off()}
    colors = {p.name: class_rgb(p.class_name) for p in players.values()}
    assert c.fit
    for i, spot in enumerate(scene.spots):
        x, y = c.fit.place((spot.x, spot.y))
        owner = c.owners.get(i)
        sc.spots.append(maps.Spot(x * 100, y * 100, colors.get(owner or "", (150, 156, 170)),
                                  owner if owner in off else "" if owner else f"open {spot.role}",
                                  ring=owner in off, to=owner if owner in off else None))
    sc.ring_yd = c.fit.tolerance
    sc.arrows = [(rep.names[s], rep.names[d]) for s, d in c.facts.at_end]
    return sc


async def _night(rep: Report, versions: list[Plan], fights: list[dict], ability: str | None, asked: str | None,  # noqa: C901, PLR0915
                 draw: bool) -> tuple[list[str], bytes | None]:
    """Every pull on the boss, each against its own version of the plan; per player, how often they were off their
    spot and whose spot they stood at."""
    by_version: dict[int, list[dict]] = defaultdict(list)
    for f in fights:
        by_version[versions.index(_version(rep, versions, f))].append(f)
    lines = [_head(versions[max(by_version)]) + f", checked against every {rep.boss_key(fights[0])} pull "
             f"({len(fights)})."]
    if after := [rep.pull_no(f) for f in fights
                 if (_version(rep, versions, f).saved_at or 0) > (rep.start_ms + f["startTime"]) / 1000]:
        which = ("every pull" if len(after) == len(fights) else
                 f"pull{'s' if len(after) > 1 else ''} {', '.join(map(str, after))}")
        lines.append(f"It was saved after {which} began: the plan for {'it' if len(after) == 1 else 'them'} may "
                     "have been different.")
    if len(by_version) > 1:
        lines.append("Versions of the plan: " + "; ".join(
            f"{saved.source(versions[v].saved_at, versions[v].saved_by) or 'unsaved'}: pulls "
            + ", ".join(str(rep.pull_no(f)) for f in fs) for v, fs in sorted(by_version.items())))
    stats: dict[str, dict] = defaultdict(lambda: {"label": "", "seen": 0, "off": [], "at": Counter(), "dead": 0})
    pairs: Counter = Counter()
    loose: list[str] = []
    total, how = 0, ""
    good: list[tuple[Scene, Checked]] = []  # moments the plan fits, for the map
    for v, fs in sorted(by_version.items()):
        for scene in versions[v].scenes:
            mech, note = await _mechanic(rep, scene, fs, ability)
            if mech is None or not mech.event:
                lines.append(note or f"{mech.name if mech else 'The mechanic'} didn't happen on these pulls.")
                continue
            how = how or _how(mech)
            for c in await check(rep, scene, mech):
                total += 1
                if c.fit is None or not c.fit.holds:
                    loose.append(f"pull {rep.pull_no(c.moment.f)} {clock(c.moment.at - c.moment.f['startTime'])}")
                    continue
                for s in c.stood:
                    st = stats[s.name]
                    st["label"], st["seen"] = s.label, st["seen"] + 1
                    if s.off_yd > c.fit.tolerance:
                        st["off"].append((s.off_yd, f"pull {rep.pull_no(c.moment.f)} "
                                                    f"{clock(c.moment.at - c.moment.f['startTime'])}"))
                        if s.at_spot:
                            st["at"][s.at_spot] += 1
                for name in c.dead:
                    stats[name]["dead"] += 1
                pairs.update((s, d) for s, d, _ in c.facts.merged())
                good.append((scene, c))
    if how:
        lines.append(how)
    lines.append(f"{plural(total, 'moment')} checked" + (f"; the raid wasn't standing as drawn at {len(loose)}"
                                               + (": " + ", ".join(loose) if len(loose) <= maps.LISTED else "")
                                               if loose else "") + ".")
    lines.append("Per player (off: further from their spot than the tolerance):")
    for name, st in sorted(stats.items(), key=lambda kv: -len(kv[1]["off"]) / max(kv[1]["seen"], 1)):
        if not st["seen"]:
            continue
        line = f"- {name} ({st['label']}): off in {len(st['off'])} of {st['seen']}"
        if st["off"]:
            line += f", median {statistics.median(d for d, _ in st['off']):.0f} yd when off"
        if st["at"]:
            line += "; at " + ", ".join(f"{_spot_of(w)} ({n}x)" for w, n in st["at"].most_common(3))
        if st["dead"]:
            line += f"; dead at {st['dead']}"
        if 0 < len(st["off"]) <= 6:
            line += f" ({', '.join(p for _, p in st['off'])})"
        lines.append(line)
    if pairs:
        shown = pairs.most_common(maps.LISTED)
        lines.append("Hit by someone else's (whose -> whom, moments): "
                     + ", ".join(f"{rep.names[s]} -> {rep.names[d]} {n}x" for (s, d), n in shown)
                     + (f"; {len(pairs) - len(shown)} more pairs" if len(pairs) > len(shown) else ""))
    if not draw or not asked:
        return lines, None
    sc = await _player_scene(rep, good, asked)
    if sc is None:
        lines.append(f"No map: {asked} has no spot on the plan at any moment it fits.")
        return lines, None
    lines.append(f"A map of {asked} at every moment is shown to the user with this answer: the plan laid over the "
                 "room once for each way the raid used it (as drawn and mirrored, each where it fitted best), "
                 f"{asked}'s spot as a filled ring of the tolerance's radius, the other spots as small dots, and a dot "
                 "where they stood each time, laid over the plan the same way: green within the tolerance of their "
                 "spot, red further, labelled pull·moment. Refer to it; don't describe it again.")
    return lines, await maps.drawn(sc)


async def _player_scene(rep: Report, good: list[tuple[Scene, Checked]], name: str) -> maps.Scene | None:
    """One player at every moment: each position taken onto the plan through that moment's fit, then into the room
    through the best fit of the same side (mirrored or not), so all of them read against one spot."""
    mine = [(scene, c, s) for scene, c in good for s in c.stood if s.name == name]
    if not mine:
        return None
    refs: dict[bool, tuple[Scene, Checked]] = {}
    for scene, c, _ in mine:
        assert c.fit
        if c.fit.mirrored not in refs or c.fit.median_yd < refs[c.fit.mirrored][1].fit.median_yd:  # type: ignore
            refs[c.fit.mirrored] = (scene, c)
    first = next(iter(refs.values()))[1]
    map_id = first.facts.pos.map_id(near=first.moment.at)
    if map_id is None:
        return None
    sc = maps.Scene(f"{rep.boss_key(first.moment.f)} · every moment", name, map_id,
                    await knowledge.map_bounds(map_id))
    for scene, c in refs.values():
        assert c.fit
        sc.ring_yd = c.fit.tolerance
        own = next((i for i, n in c.owners.items() if n == name), None)
        for i, spot in enumerate(scene.spots):
            x, y = c.fit.place((spot.x, spot.y))
            sc.spots.append(maps.Spot(x * 100, y * 100, (120, 220, 200) if i == own else (150, 156, 170),
                                      fill=i == own))
    count: Counter = Counter()
    for _, c, s in mine:
        assert c.fit
        ref = refs[c.fit.mirrored][1].fit
        assert ref
        x, y = ref.place(c.fit.unplace(c.where[name]))
        pull = rep.pull_no(c.moment.f)
        count[pull] += 1
        on = s.off_yd <= c.fit.tolerance
        sc.marks.append(maps.Mark(x * 100, y * 100, (90, 214, 140) if on else (255, 110, 110),
                                  "" if on else f"{pull}·{count[pull]}"))
    return sc
