"""The raid note's soak check (report/note.py): which hits of the ability are one soak, and whose soak it was. On
made-up pulls, and on the recorded nights whose outputs the tool cases pin (test_tools.py)."""
from collections import Counter
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

FEAST = 1290662
GROUPS = {"Right": ["Torvek", "Mirelle"], "Left": ["Halvard", "Rowan"], "Center": ["Brightleaf", "Stonewall", "Ysolde"]}


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _note(calls: dict[str, tuple[float, ...]]) -> note.Note:
    """A note with GROUPS under "Feast Soaks", each group called to soak at these seconds."""
    return note.Note(3418, "Mythic", "Vashnik", groups={"Feast Soaks": GROUPS}, callouts=[
        note.Callout(at, 1, tuple(GROUPS[label]), f"Soak {label}") for label, times in calls.items() for at in times])


async def _soaks(plan: note.Note, landings: dict[int, list[str]], until: float = 600_000) -> note.Soaks:
    """The soak check of a pull where Feast landed on these players, {ms from the pull's start: who}, the hits of one
    landing a few ms apart as in a log."""
    names = dict(enumerate(sorted({n for ns in GROUPS.values() for n in ns} | {n for ns in landings.values()
                                                                               for n in ns}), 1))
    ids = {n: i for i, n in names.items()}

    async def table(*_: Any, **__: Any) -> dict:
        return {"entries": [{"guid": FEAST}]}

    async def events(*_: Any, **__: Any) -> list[dict]:
        return [{"type": "damage", "targetID": ids[n], "timestamp": at + 7 * k, "amount": 300_000}
                for at, ns in landings.items() for k, n in enumerate(ns)]
    rep = SimpleNamespace(table=table, events=events, names=names, actor=names.get,
                          ids_named=lambda words: {FEAST} if list(words) == ["Feast"] else set())
    players = cast(Any, {n: SimpleNamespace(name=n) for n in names.values()})
    [section] = await note._soaks(cast(Any, rep), {"id": 1, "startTime": 0}, plan, players,
                                  lambda name, at: "alive", until)
    return section


async def test_soaks_landing_two_seconds_apart_are_three_soaks_each_its_own_groups():
    """The Twin Fangs' Ravenous Feast lands on one group, on the next 2 s later and on the third 1.5 s after that
    (the note says Soak First, Second, Third). Read as one hit, it went to the largest group and the others' misses
    were gone: Rowan, who took none, was missing from no soak."""
    section = await _soaks(_note({"Right": (61,), "Left": (63,), "Center": (65,)}), {
        61_240: ["Torvek", "Mirelle"], 63_250: ["Halvard"], 64_740: ["Brightleaf", "Stonewall", "Ysolde"]})
    assert [(h.at, h.label, sorted(h.hit)) for h in section.hits] == [
        (61_240, "Right", ["Mirelle", "Torvek"]), (63_250, "Left", ["Halvard"]),
        (64_740, "Center", ["Brightleaf", "Stonewall", "Ysolde"])]
    assert section.total == 3 and [h.absent for h in section.hits] == [[], [("Rowan", "alive")], []]
    assert not any(h.others for h in section.hits)


async def test_the_recorded_twin_fangs_soaks_read_right_left_center_on_every_pull(recorded):
    """The night of p7jRwgv8b1rY6LBd: on each of its 35 pulls that reached the first Ravenous Feast, the landings at
    1:01, 1:03 and 1:05 are the Right, Left and Center groups' soaks, as the note calls them (they read Center, or
    Right then Center, with the Left group hit once all night)."""
    text = (TOOLS.parent / "notes" / "twin-fangs-2026-10-21.txt").read_text(encoding="utf-8")
    night = await note.check_night(await server.lib().report("p7jRwgv8b1rY6LBd"), text, "Twin Fangs")
    firsts = Counter(tuple(h.label for h in s.hits[:3]) for c in night.checks for s in c.soaks if s.hits)
    assert firsts == {("Right", "Left", "Center"): 35}
    labels = Counter(h.label for c in night.checks for s in c.soaks for h in s.hits)
    assert labels == {"Right": 57, "Left": 58, "Center": 61}  # 57 waves before a wipe was decided, each group in each
