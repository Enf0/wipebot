"""A raid plan shown for one player (report/plan.py, `show` with a player): the scene's lines cut to their part."""
from wipebot.core.report import plan


def test_a_plan_shown_for_one_player_is_cut_to_their_part():
    """`show` with a player printed every spot of every scene, theirs first: 17k characters on a plan of several
    scenes. Their spot, the notes and the shapes that hold their spot are kept, the rest counted."""
    scene = ['Note: "Stack on the marker"', "In the side panel, not in the room: #21 Enfo (healer)",
             "Asked: #13 Torv (Torvek) (rdps): left of the boss", "#1 Mire (Mirelle) (tank): below the boss",
             "#2 Halv (Halvard) (tank): right of the boss", "Ability icon Cleave [1]: above the boss",
             "The red circle: left of the boss: holds #13 Torv (Torvek) (rdps), #1 Mire (Mirelle) (tank)",
             "The blue circle: right of the boss: holds #2 Halv (Halvard) (tank)"]
    assert plan._own_part(scene, 3) == [
        scene[0], scene[2], scene[6],
        "Left out: 5 more lines of this scene, 2 other spots among them (`show` without `player` prints them)"]
    unnamed = [line.removeprefix("Asked: ") for line in scene]  # no spot is theirs: any numbered one may be
    assert plan._own_part(unnamed, 3) == unnamed
