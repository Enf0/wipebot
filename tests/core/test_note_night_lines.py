"""What a raid note's check says once and not on every row (report/note.py): why a planned spell was never cast, the
time of a callout made once a pull, a job the note gives several players at the same moments, and a use the note
writes twice. The recorded Coiled Altar night ran past 20,000 characters with each repeated. And what a night's
counts of a player's planned cooldowns add up to: every pull that reached the line, the ones they weren't in too."""
import re
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note
from wipebot.core.text import clock

CURSE = 1714
REP = cast(Any, SimpleNamespace(pull_no=lambda f: f["id"], ability_ref=lambda spell: f"Curse [{spell}]"))
PHASES = note.lay_phases([1], {}, [])
ALTAR = TOOLS.parent / "notes" / "coiled-altar-2026-11-02.txt"


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


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


async def test_a_use_written_twice_in_a_later_phase_is_said_once_over_a_night(recorded):
    """The Coiled Altar note with its Time Spiral at 0:14 of ph:2 written again at 0:15: the night said so once for
    each of the 15 pulls that reached Stage Two, each with that pull's own times ("at both 2:50 and 2:51", "at both
    2:56 and 2:57"), since P2 began at another moment on each. It is one line of the note: said once, by the note's
    times, and still one use a pull."""
    spiral = "time:14;ph:2;tag:Lïzårðmâñ;spellid:374968;"
    text = ALTAR.read_text(encoding="utf-8")
    assert text.count(spiral) == 1
    text = text.replace(spiral, spiral + "\n" + spiral.replace("time:14;", "time:15;"))
    rep = await server.lib().report("fk2z6bCw4xRLNvht")
    rows = (await note.note_check(rep, text, boss="Coiled Altar", all_pulls=True)).splitlines()
    assert [row for row in rows if "written twice" in row] == [
        "  The note plans Lïzårðmâñ's Time Spiral [374968] at both P2 0:14 and P2 0:15: one use written twice, checked "
        "as the first"]
    assert [row for row in rows if row.startswith("    P2 0:14 Time Spiral")] == [
        "    P2 0:14 Time Spiral [374968], 15 pulls: late 12 (9 s), not cast 2 (p13, p23), dead 1"]
    assert not [row for row in rows if row.startswith("    P2 0:15 Time Spiral")]


def test_a_use_written_twice_on_one_pull_keeps_the_pulls_own_times():
    first, again = note.Cooldown(14, 2, "Mirelle", CURSE), note.Cooldown(15, 2, "Mirelle", CURSE)
    assert note._twice(REP, "2:41", "2:42", again) == (
        "The note plans Mirelle's Curse [1714] at both 2:41 and 2:42: one use written twice, checked as the first")
    phases = note.lay_phases([1, 2], {1: "P1", 2: "P2"}, [(2, 147_000)])
    night = [SimpleNamespace(f={"id": i}, until=600_000, phases=note.lay_phases([1, 2], {1: "P1", 2: "P2"}, [(2, at)]),
                             planned=note.Planned([note.PlannedUse(at + 14_000, first, None, "alive", [])], [], 0,
                                                  [(at + 14_000, at + 15_000, again, first)]))
             for i, at in ((1, 147_000), (2, 151_000), (3, 139_500))]
    assert phases.said(again) == "P2 0:15"
    assert note._night_cooldowns(REP, cast(Any, night))[-1] == (
        "  The note plans Mirelle's Curse [1714] at both P2 0:14 and P2 0:15: one use written twice, checked as the "
        "first")


def test_a_players_counts_add_up_to_their_planned_uses_with_the_pulls_they_were_not_in():
    """A raider in 2 of 5 pulls: the night read "5 planned uses; not cast 1, dead 1" and "5 pulls: not cast 1 (p4),
    dead 1", three of the five in no count. The pulls they weren't in are counted apart."""
    roar = note.Cooldown(45, 1, "Mirelle", CURSE)
    states = ("not in this pull", "not in this pull", "not in this pull", "alive", "dead since 0:40")
    night = [SimpleNamespace(f={"id": i}, until=600_000, planned=note.Planned(
        [note.PlannedUse(45_000, roar, None, state, [])], [], 0)) for i, state in enumerate(states, 1)]
    head, player, line = note._night_cooldowns(REP, cast(Any, night))
    assert head.endswith("dead by then, or not in the pull:")
    assert player == "  Mirelle: 5 planned uses; not cast 1, dead 1, not in the pull 3"
    assert line == "    0:45 Curse [1714], 5 pulls: not cast 1 (p4), dead 1, not in the pull 3"


async def test_the_recorded_night_counts_the_pulls_a_raider_was_not_in(recorded):
    """Scopidruid was in pulls 23 to 28 of the Coiled Altar night's 28: of their 64 planned uses the row counted 22
    (late 2, not cast 18, dead 2) and left 42 out, and the 0:45 roar read "26 pulls: not cast 6"."""
    rep = await server.lib().report("fk2z6bCw4xRLNvht")
    rows = (await note.note_check(rep, ALTAR.read_text(encoding="utf-8"), boss="Coiled Altar",
                                  all_pulls=True)).splitlines()
    assert ("  Scopidruid: 64 planned uses; late 2 (8 s), not cast 18, 2 of them on cooldown, dead 2, not in the pull "
            "42") in rows
    assert ("    0:45 Stampeding Roar [106898], 26 pulls: not cast 6 (p23, p24, p25, p26, p27, p28), not in the pull "
            "20") in rows
    counted = [m for row in rows if (m := re.fullmatch(r"  \S+: (\d+) planned uses?; (.*)", row))]
    assert len(counted) == 11  # noqa: PLR2004  (the players with a planned cooldown)
    for m in counted:  # every player's counts add up to their planned uses
        counts = re.findall(r"(?:on time|early|late|not cast|dead|not in the pull) (\d+)", m[2])
        assert sum(map(int, counts)) == int(m[1]), m[0]
