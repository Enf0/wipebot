"""What a raid note's check says once and not on every row (report/note.py): why a planned spell was never cast, the
time of a callout made once a pull, and a job the note gives several players at the same moments. The recorded
Coiled Altar night ran past 20,000 characters with each repeated."""
from types import SimpleNamespace
from typing import Any, cast

from wipebot.core.report import note
from wipebot.core.text import clock

CURSE = 1714
REP = cast(Any, SimpleNamespace(pull_no=lambda f: f["id"], ability_ref=lambda spell: f"Curse [{spell}]"))
PHASES = note.lay_phases([1], {}, [])


def test_why_a_spell_was_never_cast_is_said_on_its_first_row_and_pointed_to_on_the_next():
    """A warlock's note plans Curse of Tongues five times a pull and they cast it on none: each of the five rows
    carried the same 113 characters on why that may be. The first says it; the others point to it."""
    first, again = note.Cooldown(23, 1, "Zebinolock", CURSE), note.Cooldown(56, 1, "Zebinolock", CURSE)
    checks = [SimpleNamespace(f={"id": i}, until=600_000, planned=note.Planned(
        [note.PlannedUse(cd.at * 1000, cd, None, "alive", []) for cd in (first, again)], [], 0)) for i in (1, 2, 3)]
    assert note._night_cooldowns(REP, cast(Any, checks))[1:] == [
        "  Zebinolock: 6 planned uses; not cast 6",
        "    0:23 Curse [1714], 3 pulls: not cast 3 (p1, p2, p3); never cast on any of these pulls: a talent may "
        "replace it, or another spell do its job (breakdown of their casts)",
        "    0:56 Curse [1714], 3 pulls: not cast 3 (p1, p2, p3); never cast, as above"]


def _personals(at: float, idle: bool) -> note.CalloutCheck:
    """A "Personals" callout to everyone at `at` s that Mirelle pressed nothing at (`idle`) or a defensive."""
    wall = (at * 1000, {"name": "Shield Wall", "tags": ["defensive"]})
    return note.CalloutCheck(at * 1000, note.Callout(at, 1, (), "Personals"), True,
                             [] if idle else [("Mirelle", [wall])], [("Mirelle", [])] if idle else [], [])


def test_a_callout_made_once_a_pull_lists_the_pulls_without_the_time_its_own_line_gives():
    """ "Personals" at 1:47 on every pull: a raider's row read "p1 1:47, p2 1:47, p3 1:47" up to twelve and counted
    the rest. With one time, said on the callout's line, the row gives every pull, as ranges. A callout made at two
    times keeps each moment."""
    night = [SimpleNamespace(f={"id": i}, phases=PHASES, unchecked=[], callouts=[_personals(107, i != 4)])
             for i in range(1, 16)]
    assert note._night_callouts(REP, cast(Any, night))[1:] == [
        '  "Personals" to everyone (15 callouts over 15 pulls, at 1:47):',
        "    Mirelle: alive for 15; pressed at 1 (defensive 1); none at 14: pulls 1-3, 5-15 (one ready at 0)"]
    one = [SimpleNamespace(f={"id": 7}, phases=PHASES, unchecked=[], callouts=[_personals(107, True)])]
    assert note._night_callouts(REP, cast(Any, one))[2].endswith("none at 1: pull 7 (one ready at 0)")
    twice = [SimpleNamespace(f={"id": i}, phases=PHASES, unchecked=[],
                             callouts=[_personals(75, True), _personals(199, i == 2)]) for i in (1, 2)]
    assert note._night_callouts(REP, cast(Any, twice))[1:] == [
        '  "Personals" to everyone (4 callouts over 2 pulls, at 1:15, 3:19):',
        "    Mirelle: alive for 4; pressed at 1 (defensive 1); none at 3: p1 1:15, p2 1:15, p2 3:19 (one ready at 0)"]


def test_a_job_given_to_several_players_at_the_same_moments_is_one_entry():
    """ "CC Mindcontrol" to three players at five moments was fifteen entries on a pull and three on a night, each
    with the same words and times. One entry names the three; the same words at other times stay apart."""
    calls = [(at * 1000, note.Callout(at, 1, (name,), "CC Mindcontrol")) for at in (156, 192)
             for name in ("Torvek", "Mirelle", "Halvard")]
    calls += [(200_000, note.Callout(200, 1, ("Rowan",), "CC Mindcontrol")),
              (210_000, note.Callout(210, 1, ("Rowan", "Brightleaf"), "Grab red orb")),
              (220_000, note.Callout(220, 1, (), "Stack"))]
    assert note._to_whom((co.text, co.players, f"at {clock(at)}") for at, co in calls[::3]) == [
        '"CC Mindcontrol" to Torvek at 2:36', '"CC Mindcontrol" to Torvek at 3:12',
        '"CC Mindcontrol" to Rowan at 3:20']
    night = [SimpleNamespace(f={"id": i}, phases=PHASES, unchecked=calls) for i in (1, 2)]
    assert note._night_unchecked(cast(Any, night)) == [
        note.UNCHECKED + ': "CC Mindcontrol" to Torvek, Mirelle, Halvard at 2:36, 3:12 (2 pulls); "CC Mindcontrol" to '
        'Rowan at 3:20 (2 pulls); "Grab red orb" to Rowan Brightleaf at 3:30 (2 pulls); "Stack" to everyone at 3:40 '
        "(2 pulls)"]
