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
SIDES = {"Left": ["Torvek", "Mirelle", "Halvard", "Rowan", "Brightleaf", "Stonewall"],
         "Right": ["Ysolde", "Corwin", "Thessaly", "Garrick", "Linnea", "Osric"]}
REP = cast(Any, SimpleNamespace(ability_ref=lambda spell: f"Feast [{spell}]", pull_no=lambda f: f["id"]))


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _note(calls: dict[str, tuple[float, ...]], groups: dict[str, list[str]] = GROUPS) -> note.Note:
    """A note with `groups` under "Feast Soaks", each group called to soak at these seconds."""
    return note.Note(3418, "Mythic", "Vashnik", groups={"Feast Soaks": groups}, callouts=[
        note.Callout(at, 1, tuple(groups[label]), f"Soak {label}") for label, times in calls.items() for at in times])


async def _soaks(plan: note.Note, landings: dict[int, list[str]], until: float = 600_000) -> note.Soaks:
    """The soak check of a pull where Feast landed on these players, {ms from the pull's start: who}, the hits of one
    landing a few ms apart as in a log."""
    groups = plan.groups["Feast Soaks"]
    names = dict(enumerate(sorted({n for ns in groups.values() for n in ns} | {n for ns in landings.values()
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
                                  lambda name, at: "alive", lambda c: c.at * 1000, until)
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
    # 57 waves before a wipe was decided, each group in each; as wipes were decided, two hits on one player and on
    # two of the Left group and two of the Center, two on every living player of all three groups, and one on
    # three Center players at the Right group's time (test_note_soak_turns.py)
    assert labels == {"Right": 57, "Left": 57, "Center": 58, "": 4}


async def test_a_hit_far_from_every_soak_callout_is_no_groups_soak():
    """The Coiled Altar's Guillotine is soaked at the note's two Stage One callouts, 0:48 and 2:13, and comes again in
    Stage Two on one player. That hit, 33 s from any callout, went to the group with the player in it, and the rest
    of the group were listed as missing a soak (23 of the 57 missed soaks an officer was given one night)."""
    plan = _note({"Right": (48,), "Left": (133,), "Center": (400,)})
    section = await _soaks(plan, {48_200: ["Torvek", "Mirelle"], 133_100: ["Halvard", "Rowan"], 166_000: ["Torvek"]})
    assert [(h.label, h.why) for h in section.hits] == [("Right", ""), ("Left", ""), ("", note.OUTSIDE)]
    assert section.hits[2].absent == [] and section.hits[2].hit == {"Torvek"}  # listed, with no one missing it
    assert note._pull_soaks(REP, [section], 600_000) == [
        "Feast Soaks: each hit of Feast [1290662], given to the group called to soak within 20 s of it with most of "
        "its players hit (3 hits):",
        "  0:48 Right: 2 of 2 hit, 300.00k each",
        "  2:13 Left: 2 of 2 hit, 300.00k each",
        "  2:46 outside the note's soak times: 1 player hit, 300.00k each; counted for no one"]


async def test_a_hit_on_a_player_or_two_or_as_many_of_two_groups_is_given_to_neither():
    """Sszorak's note calls both sides at the same second and the side that goes first varies, so only who was hit
    says whose soak it was. One player of each (pull 19 at 0:08) went to the first group in the note and two of one
    (pull 1 at 0:08) to their group, each a miss for the rest of it; neither singles a group out. Four of one and a
    visitor do."""
    plan = _note({"Left": (11, 57), "Right": (11, 58)}, SIDES)
    section = await _soaks(plan, {8_050: ["Torvek", "Ysolde"], 20_000: ["Mirelle", "Halvard"],
                                  55_000: ["Torvek", "Mirelle", "Halvard", "Rowan", "Ysolde"]})
    assert [(h.label, h.why, h.near) for h in section.hits] == [
        ("", note.UNCLEAR, {"Left": 1, "Right": 1}), ("", note.UNCLEAR, {"Left": 2, "Right": 0}),
        ("Left", "", {"Left": 4, "Right": 1})]
    assert section.hits[2].absent == [("Brightleaf", "alive"), ("Stonewall", "alive")]
    assert section.hits[2].others == [("Ysolde", "Right")]
    assert note._pull_soaks(REP, [section], 600_000)[1:3] == [
        "  0:08 can't tell whose soak: 2 players hit (Left 1, Right 1), 300.00k each; counted for no one",
        "  0:20 can't tell whose soak: 2 players hit (Left 2, Right 0), 300.00k each; counted for no one"]


async def test_a_group_the_note_never_calls_is_matched_by_who_was_hit_alone_and_the_output_says_so():
    """A soak group with no timed callout of its own has no soak times in the note: a hit at any time may be its.
    A hit on none of its players, with no other group called then, is outside the note's soak times all the same
    (it read "can't tell whose soak ... (Center 0)")."""
    section = await _soaks(_note({"Right": (61,), "Left": (63,)}), {
        61_200: ["Torvek", "Mirelle"], 200_000: ["Brightleaf", "Stonewall", "Ysolde"], 300_000: ["Halvard", "Rowan"]})
    assert section.untimed == ["Center"]
    # the last on Left's players, with Left called 4 minutes before
    assert [(h.label, h.why, h.near) for h in section.hits] == [
        ("Right", "", {"Right": 2, "Left": 0, "Center": 0}), ("Center", "", {"Center": 3}), ("", note.OUTSIDE, {})]
    assert note._pull_soaks(REP, [section], 600_000)[-2:] == [
        "  5:00 outside the note's soak times: 2 players hit, 300.00k each; counted for no one",
        "  The note has no timed callout to Center: a hit at any time may be its, by the players hit alone"]


async def test_a_label_with_no_players_is_no_group_a_hit_could_be_given_to():
    """A label left empty under the soak heading ("Soak Right:" with no names yet) read as a group the note never
    times: the output said a hit at any time may be its, and a hit far from every callout was "can't tell whose soak
    ... (Soak Right 0)", not outside the note's soak times."""
    [plan] = note.parse("EncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
                        "time:61;ph:1;tag:Torvek Mirelle Halvard;text:Soak Left;\n\n"
                        "Feast Soaks\nSoak Left: Torvek Mirelle Halvard\nSoak Right:\n")
    assert plan.groups == {"Feast Soaks": {"Soak Left": ["Torvek", "Mirelle", "Halvard"], "Soak Right": []}}
    section = await _soaks(plan, {61_200: ["Torvek", "Mirelle"], 200_000: ["Rowan", "Corwin", "Garrick"]})
    assert section.untimed == []
    assert [(h.label, h.why, h.near) for h in section.hits] == [("Soak Left", "", {"Soak Left": 2}),
                                                                ("", note.OUTSIDE, {})]
    assert note._pull_soaks(REP, [section], 600_000)[1:] == [
        "  1:01 Soak Left: 2 of 3 hit, 300.00k each; not hit: Halvard (alive)",
        "  3:20 outside the note's soak times: 3 players hit, 300.00k each; counted for no one"]


async def test_the_same_players_under_two_labels_are_one_group_not_a_tie():
    """A note that sends one group to the first soak and the third lists it twice ("Soak 1", "Soak 3") and calls the
    same players both times. Each of their soaks was as many of two groups, so every one read "can't tell whose
    soak" and no miss of theirs was counted. They are one group, under its first label."""
    groups = {"Soak 1": ["Torvek", "Mirelle", "Halvard"], "Soak 2": ["Rowan", "Brightleaf", "Stonewall"],
              "Soak 3": ["Torvek", "Mirelle", "Halvard"]}
    plan = note.Note(3418, "Mythic", "Vashnik", groups={"Feast Soaks": groups}, callouts=[
        note.Callout(61 + 20 * i, 1, tuple(names), label) for i, (label, names) in enumerate(groups.items())])
    section = await _soaks(plan, {61_200: ["Torvek", "Mirelle", "Halvard"], 81_200: ["Rowan", "Brightleaf"],
                                  101_200: ["Torvek", "Mirelle"]})
    assert [(h.label, h.why) for h in section.hits] == [("Soak 1", ""), ("Soak 2", ""), ("Soak 1", "")]
    assert [h.absent for h in section.hits] == [[], [("Stonewall", "alive")], [("Halvard", "alive")]]
    night = note._night_soaks(REP, cast(Any, [SimpleNamespace(f={"id": 1}, soaks=[section])]))
    assert night[1:] == [
        "  Halvard (Soak 1): hit by 1 of 2; missed 1: p1 1:41",
        "  Stonewall (Soak 2): hit by 0 of 1; missed 1: p1 1:21",
        "  Missed none while alive: Brightleaf (Soak 2) 1 of 1; Mirelle (Soak 1) 2 of 2; Rowan (Soak 2) 1 of 1; "
        "Torvek (Soak 1) 2 of 2"]


async def test_a_night_counts_soaks_per_player_and_group_and_lists_the_hits_given_to_no_group():
    """A player in two groups (a soak in each stage) gets a row for each; a row labelled with the first group seen
    counted all of them. Those who missed none share a line, and hits given to no group are listed apart."""
    groups = {"Stage One": ["Torvek", "Mirelle", "Halvard"], "Stage Three": ["Torvek", "Rowan", "Brightleaf"]}
    plan = _note({"Stage One": (48,), "Stage Three": (300,)}, groups)
    pulls = [await _soaks(plan, {48_100: ["Torvek", "Mirelle", "Halvard"], 166_000: ["Rowan"],
                                 300_400: ["Rowan", "Brightleaf", "Halvard"]}),
             await _soaks(plan, {48_100: ["Torvek", "Mirelle"]})]
    checks = cast(Any, [SimpleNamespace(f={"id": i}, soaks=[s]) for i, s in enumerate(pulls, 1)])
    assert note._night_soaks(REP, checks) == [
        "Feast Soaks: 4 hits of Feast [1290662] over 2 pulls, each given to the group called to soak within 20 s of it "
        "with most of its players hit. Per player of a group, most missed first: of their group's hits while alive, "
        "how many hit them and which they missed:",
        "  Halvard (Stage One): hit by 1 of 2; missed 1: p2 0:48",
        "  Torvek (Stage Three): hit by 0 of 1; missed 1: p1 5:00",
        "  Missed none while alive: Brightleaf (Stage Three) 1 of 1; Mirelle (Stage One) 2 of 2; Rowan (Stage Three) "
        "1 of 1; Torvek (Stage One) 2 of 2",
        "  Hit by another group's soak too: Halvard 1",
        "  Counted for no one: 1 hit outside the note's soak times (no group called within 20 s of them): p1 2:46"]


async def test_groups_whose_ability_lands_on_a_player_or_two_each_time_are_not_soak_groups():
    """A list of groups for another job (who CCs which set of orbs) under a heading naming an ability the raid avoids
    read as soak groups: nine players each "missed" nine hits. When under a fifth of the hits in the note's times
    land on a group, the night says so once and counts no one."""
    plan = _note({"Left": (9, 50), "Right": (9, 50)}, SIDES)
    pulls = [await _soaks(plan, {9_000: ["Torvek"], 50_000: ["Ysolde", "Mirelle"]}),
             await _soaks(plan, {9_000: ["Corwin", "Garrick"]})]
    checks = cast(Any, [SimpleNamespace(f={"id": i}, soaks=[s]) for i, s in enumerate(pulls, 1)])
    assert note._night_soaks(REP, checks) == [
        "Feast Soaks: 3 hits of Feast [1290662] over 2 pulls, but only 0 of the 3 in the note's soak times landed on "
        "a group (under a fifth; the rest on a player or two of each): likely not soak groups (an ability to avoid, "
        "or groups for another job), so no one is counted as missing it"]


async def test_the_recorded_sszorak_night_charges_no_group_for_its_four_hits_on_a_few_players(recorded):
    """The night of vjt3KLbN8g62J1H9: four of its 172 Mutilate hits landed on two to four players as the pull fell
    apart (28 of the night's 328 "missed while alive"). Every hit of the night is within 20 s of a soak callout."""
    text = (TOOLS.parent / "notes" / "sszorak-2026-10-12.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    night = await note.check_night(rep, text, "Sszorak")
    hits = [(c.f, h) for c in night.checks for s in c.soaks for h in s.hits]
    assert [(rep.pull_no(f), h.at // 1000, h.why, len(h.hit)) for f, h in hits if not h.label] == [
        (1, 8, note.UNCLEAR, 2), (3, 59, note.UNCLEAR, 2), (10, 8, note.UNCLEAR, 4), (19, 8, note.UNCLEAR, 2)]
    assert sum(st == "alive" for _, h in hits for _, st in h.absent) == 300
