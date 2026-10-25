"""A raid plan shown with no pull: each scene of raidstrats.gg's export told in words (plan_export.describe), and
plan_show choosing the plans for a boss and the raiders their names mean. The plan is made up."""
from typing import cast

import pytest

from tests.core.test_plan import export
from wipebot.core.report import FactError, Report, plan_export
from wipebot.core.report import plan as plans
from wipebot.core.report.base import Player, match_player

ICON = {"kind": "icon", "w": 2.0, "h": 3.56}  # round on a 16:9 plan


def scene_items() -> list[dict]:
    return [
        {"kind": "boss", "label": "BOSS", "x": 49, "y": 48, "w": 2, "h": 3.56},
        {**ICON, "icon": "markers/skull", "x": 20, "y": 10},
        {**ICON, "icon": "markers/star", "x": 78, "y": 10},
        {**ICON, "icon": "classes/druid", "label": "Brøn", "slotIndex": 1, "x": 24, "y": 14},
        {**ICON, "icon": "roles/rdps.svg", "label": "2", "slotIndex": 2, "x": 74, "y": 12},
        {**ICON, "icon": "abilities/x/1310882_gloom", "spellId": 1310882, "label": "Gloom 1", "x": 80, "y": 60},
        {"kind": "shape", "shape": "circle", "x": 79.5, "y": 59, "w": 3, "h": 5.3,  # a ring around the icon
         "fill": "rgba(255, 255, 255, 0.2)", "stroke": "#a335ee"},
        {"kind": "shape", "shape": "triangle", "x": 18, "y": 8, "w": 32, "h": 42, "fill": "rgba(255, 165, 0, 0.2)",
         "corners": [{"x": 50, "y": 49.8}, {"x": 16, "y": 8}, {"x": 32, "y": 6}]},
        {"kind": "text", "label": "Frontal 1", "x": 29.7, "y": 19.8, "w": 6, "h": 3},
        {"kind": "text", "label": "P1: frontal\nto the skull", "x": 1, "y": 70, "w": 15, "h": 8},
        {"kind": "line", "shape": "arrow", "x1": 26, "y1": 16, "x2": 79, "y2": 11, "stroke": "#0070DD"},
        {"kind": "line", "shape": "arrow", "x1": 40, "y1": 40, "x2": 40, "y2": 40, "stroke": "#ffffff"},  # no end
    ]


def plan(boss: str = "Sszorak", name: str = "Spread") -> str:
    return export({"planId": name, "planName": name, "boss": boss, "planLink": "https://raidstrats.gg/planner?view=p",
                   "scenes": [{"name": "1", "items": scene_items()}]})


def spell(game_id: int) -> str:
    return f"Gloombomb [{game_id}]"


def test_a_scene_is_told_by_the_boss_the_markers_and_the_shapes_it_draws():
    [p] = plan_export.parse(plan())
    lines = plan_export.describe(p.scenes[0], lambda label, icon: "Brøndal" if label == "Brøn" else None, spell)
    assert lines[0] == 'Note: "P1: frontal to the skull"'
    assert "#1 Brøn (Brøndal) (druid): far above left of the boss, by the skull marker, in the orange cone " \
           '"Frontal 1"' in lines
    assert "#2 (open rdps spot): far above right of the boss, by the star marker" in lines
    assert 'Ability icon "Gloom 1" (Gloombomb [1310882]): far right of the boss, nearest the star marker' in lines
    assert not any(line.startswith("Label") for line in lines)  # the label inside the cone names it
    assert any(line.startswith('The orange cone "Frontal 1": from the boss toward far above left of the boss') and
               "holds #1 Brøn (Brøndal) (druid), the skull marker" in line for line in lines)
    assert "Blue arrow from #1 Brøn (Brøndal) (druid) to the star marker" in lines
    assert not any("circle" in line for line in lines)  # a ring around one icon marks it, it isn't a place
    assert len([line for line in lines if "arrow" in line]) == 1  # an arrow the export has no end for is left out


class Night:
    """A report as plan_show reads it: its raiders and the latest pull's boss."""
    players = {1: Player(1, "Brøndal", "Druid", "Balance", "dps", "", "", ()),
               2: Player(2, "Brønwen", "Mage", "Frost", "dps", "", "", ())}

    def boss(self, name: str | None) -> str:
        return "Sszorak"

    async def name_spells(self, ids: set[int]) -> None:
        pass

    def find_player(self, name: str, among=None) -> Player | None:
        return match_player(name, self.players.values() if among is None else among)

    def ability_ref(self, game_id: int | None) -> str:
        return spell(game_id or 0)


async def test_showing_a_plan_takes_the_newest_version_of_each_plan_for_the_boss_and_the_asker_first():
    pasted = (f"[saved 1000 by Halvard] {plan()}\n[saved 2000 by Halvard] {plan()}\n"
              f"[saved 1500 by Halvard] {plan('The Twin Fangs', 'Kicks')}")
    text = await plans.plan_show(cast(Report, Night()), pasted, player="Brøndal")
    assert text.count('The raid plan "Spread" for Sszorak') == 1 and "Kicks" not in text
    assert "saved by Halvard" in text and "with no pull" in text
    assert "  Asked: #1 Brøn (Brøndal) (druid): far above left of the boss" in text
    # a name matches a raider of the icon's class only: Brøn isn't the mage Brønwen
    assert "(Brønwen)" not in text


async def test_showing_a_plan_for_a_boss_without_one_lists_the_plans_there_are():
    with pytest.raises(FactError, match=r'no raid plan for Coiled Altar; the plans are "Spread" \(Sszorak\)'):
        await plans.plan_show(cast(Report, Night()), plan(), boss="Coiled Altar")


def test_the_side_panel_says_what_each_number_does():
    role = {**ICON, "icon": "roles/rdps.svg", "label": ""}
    items = [
        {"kind": "shape", "shape": "rect", "x": 0, "y": 0, "w": 25, "h": 100, "fill": "rgba(0, 0, 0, 1)"},
        {"kind": "text", "label": "Purple Carriers", "x": 2, "y": 8, "w": 12, "h": 3, "textColor": "#A330C9"},
        {**role, "slotIndex": 1, "x": 2, "y": 12}, {**role, "slotIndex": 2, "x": 7, "y": 12},
        {"kind": "text", "label": "Keeper", "x": 6.5, "y": 16, "w": 3, "h": 1.4},  # under #2
        {"kind": "text", "label": "Green Carriers", "x": 2, "y": 17.5, "w": 12, "h": 3, "textColor": "#00FF98"},
        {**role, "slotIndex": 3, "x": 2, "y": 22},
        {"kind": "text", "label": "Purple Carriers\nTake the orbs to your corner", "x": 2, "y": 40, "w": 18, "h": 6,
         "textColor": "#A330C9"},
        {"kind": "text", "label": "Each set has four orbs, and the frontal clears them", "x": 2, "y": 60, "w": 18,
         "h": 6, "textColor": "#ffffff"},
        {**ICON, "icon": "roles/rdps.svg", "slotIndex": 1, "x": 60, "y": 40},  # #1 in the room
    ]
    [p] = plan_export.parse(export({"planId": "o", "planName": "Orbs", "boss": "Sszorak",
                                    "scenes": [{"name": "1", "items": items}]}))
    lines = plan_export.describe(p.scenes[0], lambda label, icon: None, spell)
    assert 'Note: "Each set has four orbs, and the frontal clears them"' in lines  # about no group
    assert ("In the side panel, not in the room: Purple Carriers: #1 (open rdps spot), #2 (open rdps spot) (Keeper) "
            '| "Take the orbs to your corner"') in lines
    assert "In the side panel, not in the room: Green Carriers: #3 (open rdps spot)" in lines
    assert any(line.startswith("#1 (open rdps spot): ") for line in lines)  # the room's spots after the groups


def test_a_scenes_link_is_the_plans_view_link_opened_on_it():
    [p] = plan_export.parse(plan())  # its link opens on no scene in particular
    assert plan_export.scene_link(p, 2) == "https://raidstrats.gg/planner?view=p/2"
    p.link = "https://raidstrats.gg/planner?view=p/9"  # as saved while on scene 9
    assert plan_export.scene_link(p, 3) == "https://raidstrats.gg/planner?view=p/3"
    p.link = ""
    assert plan_export.scene_link(p, 1) == ""
