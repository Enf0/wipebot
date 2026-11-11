"""A night's count of the casts on kick assignments that went through (report/note.py, `_went_through`): one that
went through while its player was dead is counted apart from one they missed alive. They were one number, so an
assignment with one cast missed and three while its player lay dead read 4, level with four missed alive."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

REP = cast(Any, SimpleNamespace(pull_no=lambda f: f["id"]))


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _kick(text: str, player: str, state: str = "alive", how: str = "went through") -> note.KickCheck:
    """A kick assignment called at 1:40 whose cast ended `how`, its player in this `state` at the callout."""
    by = player if how == "kicked" else None
    return note.KickCheck(100_000, note.Callout(100, 1, (player,), text), player, (100_000, how, by), how == "kicked",
                          state)


def test_a_cast_that_went_through_while_its_player_was_dead_is_counted_apart_and_ranks_below_those_missed_alive():
    dead = "dead since 1:28"
    pulls = [[_kick("Kick #1 right", "Stonewall"), _kick("Kick #2 left", "Mirelle"), _kick("Kick #3 left", "Halvard")],
             [_kick("Kick #1 right", "Stonewall", dead), _kick("Kick #2 left", "Mirelle"),
              _kick("Kick #3 left", "Halvard", how="kicked")],
             [_kick("Kick #1 right", "Stonewall", dead), _kick("Kick #2 left", "Mirelle"),
              _kick("Kick #3 left", "Halvard"), _kick("Kick #4 left", "Rowan", note.ABSENT)],
             [_kick("Kick #1 right", "Stonewall", dead), _kick("Kick #2 left", "Mirelle"),
              _kick("Kick #4 left", "Rowan", dead, how="kicked")]]
    checks = [SimpleNamespace(f={"id": i}, decided=None, kicks=kicks) for i, kicks in enumerate(pulls, 1)]
    lines = note._night_kicks(REP, cast(Any, checks))
    assert lines[-1] == (
        "  Their casts that went through, per assignment, most missed first (missed: its player was alive at the "
        'callout): "Kick #2 left" 4 missed; "Kick #3 left" 2 missed; "Kick #1 right" 1 missed, 3 while dead; '
        '"Kick #4 left" 1 while not in the pull')
    assert "  Stonewall (Kick #1 right): kicked 0 of 1; not kicked 1: p1 1:40; dead for 3 more" in lines


async def test_the_recorded_night_ranks_a_tanks_one_missed_kick_below_the_four_missed_alive(recorded):
    """The Twin Fangs night of p7jRwgv8b1rY6LBd, kicks counted from pull 12: Karadim's cast of Kick #1 right went
    through on pulls 20, 23, 32 and 37, and they were alive at the callout only on 32 (dead since 3:05, 1:28 and 6:47
    on the others). The line read '"Kick #1 right" 4', beside the 4 of Kick #2 right and Kick #4 left, whose
    players were alive each time."""
    text, failed = await server.call("raid_note", CASES["raid-note-order-change"].args)
    assert not failed
    [line] = [row for row in text.splitlines() if row.startswith("  Their casts that went through")]
    assert line.split(": ", 2)[2] == (
        '"Kick #2 left" 6 missed; "Kick #3 left" 5 missed; "Kick #2 right" 4 missed; "Kick #4 left" 4 missed; '
        '"Kick #5 left" 3 missed; "Kick #3 right" 2 missed, 1 while dead; "Kick #1 left" 1 missed, 1 while dead; '
        '"Kick #1 right" 1 missed, 3 while dead; "Kick #4 right" 1 missed, 1 while dead; "Kick #5 right" 1 missed')
    assert "  Karadim (Kick #1 right): kicked 56 of 57; not kicked 1: p32 1:39; dead for 3 more" in text.splitlines()
