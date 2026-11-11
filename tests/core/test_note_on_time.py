"""What "on time" means for a planned cooldown (report/note.py, ON_TIME_S): one band, read by a pull's rows, a
night's counts and the note's card. It was 1 s in four places; of 1,592 presses matched to their line on the four
recorded nights, 30% were within 1 s and 64% within 3 s, so most presses that followed the plan read early or late."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

SPIRIT = 98008
REP = cast(Any, SimpleNamespace(pull_no=lambda f: f["id"], ability_ref=lambda spell: f"Spirit Link [{spell}]"))
OFFSETS = (0.4, 1.5, -2.9, 3.0, -3.0, 7.0)  # seconds from the line's time: three inside the band, three at or past it


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _uses() -> list[note.PlannedUse]:
    """A line at 1:00 pressed OFFSETS from it, one press a pull."""
    line = note.Cooldown(60, 1, "Mirelle", SPIRIT)
    return [note.PlannedUse(60_000, line, 60_000 + off * 1000, "alive", [60_000 + off * 1000]) for off in OFFSETS]


def test_a_press_under_three_seconds_from_its_time_is_on_time_and_one_at_three_keeps_its_offset():
    assert note.ON_TIME_S == 3  # noqa: PLR2004
    assert [note.timing(off) for off in OFFSETS] == ["on time", "on time", "on time", "late", "early", "late"]
    assert note._pull_tally(_uses()) == "on time 3, early 1, late 2"
    night = [SimpleNamespace(f={"id": i}, until=600_000, planned=note.Planned([u], [], 0))
             for i, u in enumerate(_uses(), 1)]
    head, player, line = note._night_cooldowns(REP, cast(Any, night))
    assert "on time (under 3 s from the note's time), early or late (the median)" in head
    assert player == "  Mirelle: 6 planned uses; on time 3, early 1 (3 s), late 2 (5 s)"
    assert line == "    1:00 Spirit Link [98008], 6 pulls: on time 3, early 1 (3 s), late 2 (5 s)"


async def test_a_pulls_rows_read_the_band(recorded):
    """Sszorak pull 42: Enfo's Spirit Link Totem planned at 1:12 was cast 1.36 s after its time and read "1 s late",
    Namsus's Divine Hymn planned at 3:14 was cast 2.82 s before it and read "3 s early": both on time. Enfo's
    Ascendance, 8.6 s late, still reads so."""
    text, failed = await server.call("raid_note", CASES["raid-note"].args)
    assert not failed
    rows = text.splitlines()
    assert "  1:12 Enfo Spirit Link Totem [98008]: cast at 1:13, on time" in rows
    assert "  3:14 Namsus Divine Hymn [64843]: cast at 3:11, on time" in rows
    assert "  1:08 Enfo Ascendance [114052]: cast at 1:16, 9 s late" in rows
    assert "Planned cooldowns (time in the note: what happened; on time: under 3 s from it):" in rows
    off = [r for r in rows if r.endswith((" s late", " s early"))]
    assert off and not [r for r in off if r.endswith((", 1 s late", ", 2 s late", ", 1 s early", ", 2 s early"))]


async def test_the_notes_card_reads_the_same_band(recorded):
    """The card's cells over the Sszorak night are on time under the same 3 s (the test beside the card holds their
    counts against the night's text): it had its own 1 s."""
    key = await server.view_key("raid_note", CASES["raid-note-night"].args)
    data = (await server.view_data(**cast(Any, key))).structured_content
    assert data is not None
    cells = [x for p in data["cooldowns"]["pulls"] for x in p["cells"] if x and "off_s" in x]
    assert {x["state"] for x in cells} == {"on time", "early", "late"}
    assert all((abs(x["off_s"]) < note.ON_TIME_S) == (x["state"] == "on time")
               for x in cells if abs(x["off_s"]) != note.ON_TIME_S)  # off_s is rounded to a tenth: 3.0 is either
    assert max(abs(x["off_s"]) for x in cells if x["state"] == "on time") > 2  # noqa: PLR2004


def test_the_tools_description_states_the_band_the_code_reads():
    described = " ".join((server.raid_note.__doc__ or "").split())
    assert f"(on time: under {note.ON_TIME_S} s from its time in the note)" in described
