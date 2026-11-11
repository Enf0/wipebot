"""How big a soak hit was, and who it did nothing to (report/note.py, `_soaks` and `_soak_size`): the size is the
median of what each player took of it (halfway between the two middle ones when their number is even), and a player
it did no damage to (immune, or a miss in the log) is hit and named. On a made-up pull and on the recorded nights."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

BLADE = 1283594
GROUP = ["Torvek", "Mirelle", "Halvard", "Rowan", "Brightleaf", "Stonewall"]
REP = cast(Any, SimpleNamespace(ability_ref=lambda spell: f"Blade [{spell}]", pull_no=lambda f: f["id"]))
IMMUNE, MISS = {"amount": 0, "hitType": 10}, {"amount": 0, "hitType": 0}


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _hit(amount: int, absorbed: int = 0) -> dict:
    return {"amount": amount, "hitType": 1} | ({"absorbed": absorbed} if absorbed else {})


async def _rows(landings: dict[int, list[tuple[str, dict]]]) -> list[str]:
    """The soak rows of a pull whose note calls GROUP to soak at 0:48 and 2:13 and where Blade landed with these
    events, {ms from the pull's start: (player, the event's amount, absorbed and hitType)}."""
    plan = note.Note(3418, "Mythic", "Vashnik", groups={"Blade Soaks": {"Soak 1": GROUP}}, callouts=[
        note.Callout(at, 1, tuple(GROUP), "Soak") for at in (48, 133)])
    names = dict(enumerate(GROUP, 1))
    ids = {n: i for i, n in names.items()}

    async def table(*_: Any, **__: Any) -> dict:
        return {"entries": [{"guid": BLADE}]}

    async def events(*_: Any, **__: Any) -> list[dict]:
        return [{"type": "damage", "targetID": ids[n], "timestamp": at + 7 * k, **e}
                for at, hits in landings.items() for k, (n, e) in enumerate(hits)]
    rep = SimpleNamespace(table=table, events=events, names=names, actor=names.get,
                          ids_named=lambda words: {BLADE} if list(words) == ["Blade"] else set())
    players = cast(Any, {n: SimpleNamespace(name=n) for n in GROUP})
    soaks = await note._soaks(cast(Any, rep), {"id": 1, "startTime": 0}, plan, players, lambda name, at: "alive",
                              lambda c: c.at * 1000, 600_000)
    return note._pull_soaks(REP, soaks, 600_000)[1:]


async def test_a_soak_some_were_immune_to_says_who_and_what_it_did_to_the_others():
    """Three of the five who stood in a soak had an immunity up, so the median of the five was 0 and the row read
    "5 of 6 hit, 0 each". They are hit (the log has the event), the size is what it did to the other two, and the
    row names them; a hit that was all absorbed did its damage, and is no such player."""
    rows = await _rows({
        48_300: [(n, _hit(450_000)) for n in GROUP],
        133_200: [("Torvek", IMMUNE), ("Mirelle", _hit(700_000, 80_000)), ("Halvard", MISS), ("Rowan", IMMUNE),
                  ("Brightleaf", _hit(0, 760_000))],
        200_000: [("Torvek", IMMUNE), ("Mirelle", MISS)]})
    assert rows == [
        "  0:48 Soak 1: 6 of 6 hit, 450.00k each",
        "  2:13 Soak 1: 5 of 6 hit, 770.00k each but none to Halvard, Rowan, Torvek (immune or missed in the log, and "
        "counted as hit); not hit: Stonewall (alive)",
        "  3:20 outside the note's soak times: 2 players hit, no damage to any of them (immune or missed in the log, "
        "and counted as hit); counted for no one"]


async def test_a_second_event_that_did_a_player_nothing_isnt_a_hit_of_its_own():
    """A Twin Fangs soak comes with a second event on one of its players that does them nothing, beside the one that
    hit them: seven events for six players. The size is the median of what each of the six took, not of the seven
    events, and the player isn't one it did no damage to."""
    rows = await _rows({48_300: [("Torvek", IMMUNE), *((n, _hit(100_000 * k)) for k, n in enumerate(GROUP, 1))]})
    assert rows == ["  0:48 Soak 1: 6 of 6 hit, 350.00k each"]  # the median of 100k to 600k; with the 0, 300k


async def test_a_soak_on_an_even_number_of_players_is_sized_between_the_two_middle_ones():
    """With an even number of players damaged the size was the upper of the two middle values: two players who took
    600k and 800k read "800.00k each", a figure only one of them took. It is the median, halfway between the two
    middle ones; an odd number keeps its middle value."""
    rows = await _rows({
        48_300: [("Torvek", _hit(600_000)), ("Mirelle", _hit(800_000))],
        133_200: [(n, _hit(amount)) for n, amount in zip(GROUP, (100_000, 200_000, 400_000, 900_000), strict=False)],
        200_000: [(n, _hit(amount)) for n, amount in zip(GROUP, (100_000, 200_000, 900_000), strict=False)]})
    assert [row.split(", ")[1].split(";")[0] for row in rows] == ["700.00k each", "300.00k each", "200.00k each"]


async def test_the_recorded_immuned_soak_names_who_took_none_of_it(recorded):
    """Pull 28 of fk2z6bCw4xRLNvht: the second Guillotine at 2:13 hit five of its group, three of them for nothing
    (two with Divine Shield up, hitType 10, and one with Aspect of the Turtle, hitType 0), and read "5 of 6 hit,
    0 each". The two it damaged took 683.57k and 779.75k: 731.66k each, not the 779.75k one of them took. Pull 37 of
    p7jRwgv8b1rY6LBd: the 2:05 soak has seven events for six players, one a second one that did its player nothing
    (the two middle ones took 244,106 and 337,356); over that night no soak did a player no damage."""
    text = (TOOLS.parent / "notes" / "coiled-altar-2026-11-02.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("fk2z6bCw4xRLNvht")
    rows = (await note.note_check(rep, text, 28, "Coiled Altar")).splitlines()
    assert ("  2:13 P1 Soak 2: 5 of 6 hit, 731.66k each but none to Cptola, Martin, Nalaxd (immune or missed in the "
            "log, and counted as hit); not hit: Namsus (alive)") in rows
    assert not [row for row in rows if " 0 each" in row]
    text = (TOOLS.parent / "notes" / "twin-fangs-2026-10-21.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("p7jRwgv8b1rY6LBd")
    night = await note.check_night(rep, text, "Twin Fangs")
    [pull] = [c for c in night.checks if rep.pull_no(c.f) == 37]
    [hit] = [h for h in pull.soaks[0].hits if h.at // 1000 == 125]  # the row at 2:05
    assert (hit.label, len(hit.hit), round(hit.size), hit.unharmed) == ("Center", 6, 290_731, [])
    assert not [h for c in night.checks for s in c.soaks for h in s.hits if h.unharmed]
