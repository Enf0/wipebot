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
    assert plan._own_part(scene) == [
        scene[0], scene[2], scene[6],
        "Left out: 5 more lines of this scene, other spots and what else it draws (`show` without `player` prints "
        "them)"]
    unnamed = [line.removeprefix("Asked: ") for line in scene]  # no spot is theirs: any numbered one may be
    assert plan._own_part(unnamed) == unnamed


def test_a_plan_shown_for_one_player_keeps_what_is_drawn_where_they_stand():
    """Cut to their spot alone, "where do I stand?" lost who stands there with them and the ability drawn there."""
    scene = ["Asked: #13 Torv (Torvek) (rdps): left of the boss", "#11 Mire (Mirelle) (rdps): left of the boss",
             "#2 Halv (Halvard) (tank): right of the boss", "Ability icon Cleave [1]: left of the boss",
             'Label "Tanks": right of the boss']
    assert plan._own_part(scene) == [
        scene[0], scene[1], scene[3],
        "Left out: 2 more lines of this scene, other spots and what else it draws (`show` without `player` prints "
        "them)"]


def test_a_plan_shown_for_a_player_of_the_side_panel_is_cut_to_their_row():
    """A raider only in the side panel (those who stay out) got every spot of the scene, under "isn't named on this
    plan". Their part is their row with its notes."""
    scene = ['Note: "Stack on the marker"',
             'Asked: In the side panel, not in the room: #21 Enfo (healer), #22 Nams (Namsus) (healer) | "Stay out"',
             "#1 Mire (Mirelle) (tank): below the boss", "#2 Halv (Halvard) (tank): right of the boss"]
    assert plan._own_part(scene) == [
        scene[0], scene[1],
        "Left out: 2 more lines of this scene, other spots and what else it draws (`show` without `player` prints "
        "them)"]
