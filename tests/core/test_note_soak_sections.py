"""Which sections of a raid note are soak groups (report/note.py, `_soak_sections`): groups under a heading that
names an ability, one of them called by a timed callout. Players listed for another job with a group of helpers that
is called too (the Coiled Altar's "Orb Carriers") are not: every label of theirs was taken for a soak group, so a
callout to exactly one of those players was dropped without a word, and the check said no ability of that name had
hit players. On a made-up note and on the recorded Coiled Altar night."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

BLADE = 1283594
REP = cast(Any, SimpleNamespace(ids_named=lambda words: {BLADE} if list(words) == ["Blade"] else set()))
SOAKERS, HELPERS = ("Torvek", "Mirelle", "Halvard"), ("Rowan", "Brightleaf")


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _plan() -> note.Note:
    """Blade's soak group, called at 0:48, and the orb carriers: one player an orb and two helpers, called at 0:07.
    Ysolde carries an orb and has a CC to do at 1:30; Torvek, who soaks, has one at 1:40."""
    return note.Note(3418, "Mythic", "Vashnik", groups={
        "Blade Soaks": {"Soak 1": list(SOAKERS)},
        "Orb Carriers": {"Red orb": ["Ysolde"], "Blue orb": ["Corwin"], "Orb Helpers": list(HELPERS)},
    }, callouts=[note.Callout(48, 1, SOAKERS, "Soak Blade"),
                 note.Callout(7, 1, ("Ysolde",), "Grab red orb"), note.Callout(7, 1, ("Corwin",), "Grab blue orb"),
                 note.Callout(7, 1, HELPERS, "Orb backup"), note.Callout(90, 1, ("Ysolde",), "CC Mindcontrol"),
                 note.Callout(100, 1, ("Torvek",), "CC Mindcontrol")])


def test_players_listed_for_another_job_are_no_soak_groups_though_their_helpers_are_called():
    plan = _plan()
    assert list(note._soak_sections(REP, plan)) == ["Blade Soaks"]  # "Orb Carriers" names no ability
    personals, unchecked = note._callouts_to_check(REP, plan, lambda c: c.at * 1000, 600_000, set())
    assert not personals
    assert [(co.text, co.players) for _, co in unchecked] == [  # all but the soak group's own callout
        ("Grab red orb", ("Ysolde",)), ("Grab blue orb", ("Corwin",)), ("Orb backup", HELPERS),
        ("CC Mindcontrol", ("Ysolde",)), ("CC Mindcontrol", ("Torvek",))]


async def test_the_recorded_orb_carriers_are_no_soak_and_the_callouts_to_them_are_listed(recorded):
    """The Coiled Altar note lists eight orb carriers one by one and four "Orb Helpers", who get a timed "Green orb
    backup". The check printed "Orb Carriers: no ability of that name hit players on this pull", and of the callouts
    to one player it listed only "CC Mindcontrol" to three of its four players: Scopidruid, who carries an orb, was
    left out, with every "Grab ... orb", the backup, the ghost soaks and the tank's external."""
    text, failed = await server.call("raid_note", CASES["raid-note-phases"].args)
    assert not failed
    assert "no ability of that name" not in text
    [unchecked] = [row for row in text.splitlines() if row.startswith("Callouts not checked")]
    for said in ('"CC Mindcontrol" to Wutroll, Pomaa, Fappachino, Scopidruid at 2:36, 3:12, 4:01, 4:37, 5:26',
                 '"Grab side green orb" to Peggerino, Scopidruid at 0:07, 0:50, 1:32',
                 '"Green orb backup" to Sonyosh Namsus Martin Enfo at 0:07, 0:50, 1:32',
                 '"Soak Ghost at {cross}" to Lïzårðmâñ at 6:13', '"External Guillotine Target" to Karadim at 2:08'):
        assert said in unchecked, said
    soaks = [row for row in text.splitlines() if row.startswith("Guillotine Soaks: each hit of Guillotine")]
    assert len(soaks) == 1  # the soak groups are still found, and their callouts aren't listed as not checked
    assert "Soak Guillotine" not in unchecked


async def test_the_recorded_night_no_longer_says_no_ability_named_orb_carriers_hit_players(recorded):
    text, failed = await server.call("raid_note", CASES["raid-note-phases-night"].args)
    assert not failed
    assert "no ability of that name" not in text and "\nGuillotine Soaks: 49 hits of Guillotine [1283594]" in text
    [unchecked] = [row for row in text.splitlines() if row.startswith("Callouts not checked")]
    assert '"CC Mindcontrol" to Wutroll, Pomaa, Fappachino, Scopidruid at P2 0:09, 0:45, 1:34, 2:10, 2:59' in unchecked
    assert '"Green orb backup" to Sonyosh Namsus Martin Enfo at 0:07, 0:50, 1:32' in unchecked


def test_a_label_with_no_players_in_a_soak_section_takes_no_callout_to_everyone():
    """A label left empty under a soak section's heading ("Soak 2:" with no names yet) is no group of players. Taken
    for one, its players were no one, which is how a callout to everyone reads: "Personals" to everyone was neither
    checked nor listed, and a hit outside the soak times read as one the note may call, for a line to everyone in a
    phase that can't be placed."""
    plan = _plan()
    plan.groups["Blade Soaks"]["Soak 2"] = []
    plan.callouts += [note.Callout(60, 1, (), "Personals"), note.Callout(70, 1, (), "Stack"),
                      note.Callout(5, 2, (), "Move")]
    personals, unchecked = note._callouts_to_check(REP, plan, lambda c: c.at * 1000, 600_000, set())
    assert [co.text for _, co in personals] == ["Personals"]
    assert [co.text for _, co in unchecked if not co.players] == ["Move", "Stack"]
    assert "Soak Blade" not in [co.text for _, co in unchecked]  # the group with players keeps its callout
    late = note.SoakHit(130_000, "", [], set(SOAKERS), 100_000, [], [], note.OUTSIDE)
    back = note.lay_phases([1, 2], {1: "P1", 2: "P2"}, [(2, 100_000), (1, 120_000)], in_order=False)
    assert 2 in back.unknown  # the log goes back to a phase: "Move" in ph:2 can't be placed
    [soaks] = note._unplaced([note.Soaks("Blade Soaks", plan.groups["Blade Soaks"], BLADE, [late])], plan, back)
    assert soaks.hits[0].why == note.OUTSIDE  # no soak group is called in ph:2, only everyone


async def test_the_recorded_callout_to_everyone_is_still_checked_beside_an_empty_soak_label(recorded):
    """The Coiled Altar note with a label and no names added under "Guillotine Soaks": pull 28's "Personals" to
    everyone at 1:47 dropped out of the check."""
    args = CASES["raid-note-phases"].args
    text, failed = await server.call("raid_note", args | {
        "note": args["note"].replace("Guillotine Soaks\n", "Guillotine Soaks\nP3 Soak 4:\n", 1)})
    assert not failed
    assert [row[:35] for row in text.splitlines() if row.startswith("  1:47 everyone")] == [
        '  1:47 everyone: "Personals": of 20']
