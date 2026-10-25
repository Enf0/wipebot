"""The raid plan from raidstrats.gg: reading its export, laying it over the room, picking each pull's version."""
import base64
import json
import math
import statistics
import zlib

import pytest

from tests.support import PLAN
from wipebot.core.report import FactError, plan_export
from wipebot.core.report import plan as plans
from wipebot.core.report.base import Player, match_player

EXPORT = PLAN.read_text(encoding="utf-8").strip()


def export(data: dict) -> str:
    """A plan as raidstrats.gg exports it: a version byte, then the JSON deflated without a header."""
    deflate = zlib.compressobj(wbits=-15)
    raw = b"\x01" + deflate.compress(json.dumps(data).encode()) + deflate.flush()
    return "raidstrats-addon-" + base64.b64encode(raw).decode()


def test_the_planners_export_gives_the_spots_in_the_room_and_leaves_out_the_side_panel():
    [p] = plan_export.parse(f"here's tonight's plan: {EXPORT} thanks")
    assert (p.name, p.boss, p.raid) == ("Wowutils - Sszorak Mythic Spread", "Sszorak", "The Venomous Abyss")
    [scene] = p.scenes
    named = [s.label for s in scene.spots if s.label]
    assert len(named) == 16 and "Krøp" in named and "Mart" in named
    assert [s.role for s in scene.spots if not s.label] == ["rdps"] * 4  # open spots
    assert scene.aside == ["Enfo", "Nams", "Mira", "Lïzå", "Mart"]  # in the black panel beside the room
    assert scene.spells == [1297367, 1297707]  # Serpent's Fury, Virulence
    assert (["Mart"], "Player has to run far, to bait the Serpents Fury") in scene.notes  # the panel row it's by
    assert ([], "*Positions are Approximate") in scene.notes
    tanks = {s.label: s for s in scene.spots if s.role == "tank"}
    # x is scaled by the plan's shape (icons are round), so the tanks' gap in x and y read in the same units
    assert tanks["Poma"].x - tanks["Nexs"].x == pytest.approx((51.69 - 46.06) * 3.99 / 2.24, rel=0.01)


def test_a_paste_without_a_plan_or_with_a_broken_one_is_an_error():
    with pytest.raises(FactError, match="raidstrats-addon-"):
        plan_export.parse("EncounterID:3420;Name:Sszorak")
    with pytest.raises(FactError, match="doesn't decode"):
        plan_export.parse("raidstrats-addon-AAAA")


def test_a_paste_of_saved_versions_gives_each_oldest_first():
    one = export({"planId": "a", "planName": "Spread", "boss": "Sszorak", "scenes": []})
    versions = plan_export.parse(f"[saved 2000 by Mirelle] {one}\n[saved 1000 by Torvek] {one}")
    assert [(v.saved_at, v.saved_by) for v in versions] == [(1000.0, "Torvek"), (2000.0, "Mirelle")]


@pytest.mark.parametrize("mirrored", [False, True])
def test_the_fit_finds_the_turn_scale_and_mirror_and_isnt_pulled_by_one_player_away(mirrored):
    plan = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0), (5.0, 3.0), (2.0, 8.0), (7.0, 6.0), (4.0, 1.0)]
    turn, scale, move = math.radians(118), 1.6, (-500.0, 600.0)

    def room(x: float, y: float) -> tuple[float, float]:
        x = -x if mirrored else x
        return (scale * (math.cos(turn) * x - math.sin(turn) * y) + move[0],
                scale * (math.sin(turn) * x + math.cos(turn) * y) + move[1])
    stood = [room(*p) for p in plan]
    stood[2] = (stood[2][0] + 25, stood[2][1])  # one player 25 yd away from their spot
    f = plans.fit(plan, stood)
    assert f is not None and f.mirrored == mirrored and f.scale == pytest.approx(scale, rel=0.01)
    off = [math.dist(f.place(p), q) for p, q in zip(plan, stood, strict=True)]
    assert off[2] == pytest.approx(25, abs=0.5) and max(off[:2] + off[3:]) < 0.5
    assert f.holds and f.tolerance == plans.MIN_TOLERANCE_YD  # spots this close: the floor, not half their spacing
    wide = plans.fit([(x * 10, y * 10) for x, y in plan], [(x * 10, y * 10) for x, y in stood])
    gap = statistics.median([min(math.dist(a, b) for b in plan if b is not a) for a in plan])
    assert wide and wide.tolerance == pytest.approx(10 * scale * gap / 2, rel=0.01)  # half the spots' spacing
    assert plans.fit(plan[:3], stood[:3]) is None  # too few to fit
    back = f.unplace(f.place((3.0, 4.0)))
    assert back == pytest.approx((3.0, 4.0))


def test_a_label_is_the_player_whose_name_it_starts():
    players = {i: Player(i, n, "Druid", "Balance", "dps", "", "", ()) for i, n in
               enumerate(["Brøndal", "Tovin", "Tovinar", "Ashcaller"])}

    def named(label: str) -> str | None:
        return p.name if (p := match_player(label, players.values())) else None
    assert named("Brond") == "Brøndal"  # accents optional
    assert named("Ashc") == "Ashcaller"
    assert named("Tovin") == "Tovin"  # the whole name wins
    assert named("Tov") is None  # two players: ambiguous


def test_a_spot_numbered_by_the_planner_is_open():
    icon = {"kind": "icon", "w": 2.24, "h": 3.99, "icon": "roles/rdps.svg"}
    data = {"planId": "a", "planName": "P", "boss": "Sszorak", "scenes": [{"items": [
        {**icon, "label": "Brøndal", "x": 10, "y": 10}, {**icon, "label": "17", "x": 20, "y": 10}]}]}
    [p] = plan_export.parse(export(data))
    assert [s.label for s in p.scenes[0].spots] == ["Brøndal", ""]
