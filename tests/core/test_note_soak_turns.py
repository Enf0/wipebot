"""Whose soak a hit was when who was hit doesn't say (report/note.py, `_whose` and `_in_turn`): a hit on several
groups at once is no group's, and where the note calls its groups in turn, a hit with most of no group's living
players in it is the soak of the group whose turn it landed in. On made-up pulls and on the recorded nights."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

FEAST = 1290662
RIGHT, LEFT = ["Torvek", "Mirelle", "Halvard", "Rowan"], ["Brightleaf", "Stonewall", "Ysolde", "Corwin"]
CENTER = ["Thessaly", "Garrick", "Linnea", "Osric", "Wynne", "Aldous"]
GROUPS = {"Right": RIGHT, "Left": LEFT, "Center": CENTER}
REP = cast(Any, SimpleNamespace(ability_ref=lambda spell: f"Feast [{spell}]", pull_no=lambda f: f["id"]))
FIRST_WAVE = {61_200: RIGHT, 63_200: LEFT, 64_700: CENTER}  # each group in its turn, as the note calls them


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def _note(calls: dict[str, tuple[float, ...]], groups: dict[str, list[str]] = GROUPS) -> note.Note:
    """A note with `groups` under "Feast Soaks", each group called to soak at these seconds."""
    return note.Note(3418, "Mythic", "Vashnik", groups={"Feast Soaks": groups}, callouts=[
        note.Callout(at, 1, tuple(groups[label]), f"Soak {label}") for label, times in calls.items() for at in times])


async def _soaks(plan: note.Note, landings: dict[int, list[str]], dead: dict[str, int] | None = None) -> note.Soaks:
    """The soak check of a pull where Feast landed on these players, {ms from the pull's start: who}, with those
    `dead` from these ms on."""
    groups = plan.groups["Feast Soaks"]
    names = dict(enumerate(sorted({n for ns in groups.values() for n in ns} | {n for ns in landings.values()
                                                                               for n in ns}), 1))
    ids = {n: i for i, n in names.items()}

    async def table(*_: Any, **__: Any) -> dict:
        return {"entries": [{"guid": FEAST}]}

    async def events(*_: Any, **__: Any) -> list[dict]:
        return [{"type": "damage", "targetID": ids[n], "timestamp": at + 7 * k, "amount": 300_000}
                for at, ns in landings.items() for k, n in enumerate(ns)]

    def state(name: str, at: float) -> str:
        return "dead since 1:50" if at >= (dead or {}).get(name, float("inf")) else "alive"
    rep = SimpleNamespace(table=table, events=events, names=names, actor=names.get,
                          ids_named=lambda words: {FEAST} if list(words) == ["Feast"] else set())
    players = cast(Any, {n: SimpleNamespace(name=n) for n in names.values()})
    [section] = await note._soaks(cast(Any, rep), {"id": 1, "startTime": 0}, plan, players, state,
                                  lambda c: c.at * 1000, 600_000)
    return section


async def test_a_hit_on_most_of_the_living_players_of_several_groups_is_no_groups_soak():
    """As a Twin Fangs pull fell apart the whole raid stood in one soak: 14 players, every living one of all three
    groups. It went to the largest group, and the players of the other two were listed as hit by another group's
    soak (14 of a night's 32 such entries). It is no one group's soak and counts for no one."""
    dead = {"Torvek": 110_000, "Brightleaf": 110_000, "Thessaly": 110_000, "Garrick": 110_000}
    everyone = [n for ns in GROUPS.values() for n in ns if n not in dead]
    section = await _soaks(_note({"Right": (61, 122), "Left": (63, 124), "Center": (65, 126)}),
                           FIRST_WAVE | {122_200: everyone}, dead)
    last = section.hits[-1]
    assert (last.label, last.why, last.near) == ("", note.SEVERAL, {"Right": 3, "Left": 3, "Center": 4})
    assert not last.absent and not last.others
    assert note._pull_soaks(REP, [section], 600_000)[-1] == (
        "  2:02 on several groups at once: 10 players hit (Right 3, Left 3, Center 4), 300.00k each; counted for "
        "no one")
    night = note._night_soaks(REP, cast(Any, [SimpleNamespace(f={"id": 1}, soaks=[section])]))
    assert night[1:] == [
        "  Missed none while alive: " + "; ".join(sorted(f"{n} ({g}) 1 of 1" for g, ns in GROUPS.items() for n in ns)),
        "  Counted for no one: 1 hit on most of the living players of two or more groups at once, so no one group's "
        "soak: p1 2:02"]


async def test_a_group_and_a_few_of_another_is_still_that_groups_soak():
    """Most of one group's living players, and two of another's four: the second isn't most of its group."""
    section = await _soaks(_note({"Right": (61,), "Left": (63,), "Center": (65,)}),
                           {61_200: [*RIGHT, "Brightleaf", "Stonewall"]})
    [hit] = section.hits
    assert (hit.label, hit.why) == ("Right", "") and hit.others == [("Brightleaf", "Left"), ("Stonewall", "Left")]
    # two living players of each of two groups, all hit, single neither out and aren't "several groups" either:
    # a player or two in a hit may be standing in another group's soak
    dead = dict.fromkeys(["Torvek", "Mirelle", "Brightleaf", "Stonewall"], 0)
    section = await _soaks(_note({"Right": (61,), "Left": (63,)}, {"Right": RIGHT, "Left": LEFT}),
                           {61_200: ["Halvard", "Rowan", "Ysolde", "Corwin"]}, dead)
    assert [(h.label, h.why) for h in section.hits] == [("", note.UNCLEAR)]


async def test_two_groups_that_share_players_are_not_both_hit_by_a_hit_on_one():
    """A note may put the same players in two groups called together (the Coiled Altar's third stage: six players
    soak first, and five of them again with five others). A hit on the larger group has most of the smaller in it
    too; only the players a group shares with no other say whether it was hit as well."""
    small = ["Torvek", "Mirelle", "Halvard", "Rowan", "Brightleaf", "Stonewall"]
    large = ["Ysolde", *small[1:], "Corwin", "Thessaly", "Garrick", "Osric"]
    plan = _note({"Soak 1": (61,), "Soak 3": (70,)}, {"Soak 1": small, "Soak 3": large})
    section = await _soaks(plan, {61_200: small, 70_300: large})
    assert [(h.label, h.why) for h in section.hits] == [("Soak 1", ""), ("Soak 3", "")]
    assert section.hits[1].absent == [] and section.hits[0].absent == []


async def test_a_hit_in_a_groups_turn_with_most_of_no_group_in_it_is_that_groups_soak():
    """Pull 21 of p7jRwgv8b1rY6LBd at 2:02: the note calls Right at 2:02, Left at 2:04 and Center at 2:06, and the
    landing at 2:02.2 hit three of Center's six living players, one of Left's and none of Right's. By count it was
    Center's: three players who stood in it were charged a miss and Right's four, who weren't there, none."""
    plan = _note({"Right": (61, 122), "Left": (63, 124), "Center": (65, 126)})
    dead = {"Torvek": 110_000, "Mirelle": 110_000}
    landings = FIRST_WAVE | {122_200: ["Brightleaf", "Thessaly", "Garrick", "Linnea"]}
    section = await _soaks(plan, landings, dead)
    last = section.hits[-1]
    assert (last.label, last.by_turn, last.why) == ("Right", True, "")
    assert last.absent == [("Torvek", "dead since 1:50"), ("Mirelle", "dead since 1:50"), ("Halvard", "alive"),
                           ("Rowan", "alive")]
    assert last.others == [("Brightleaf", "Left"), ("Garrick", "Center"), ("Linnea", "Center"), ("Thessaly", "Center")]
    assert not any(h.by_turn for h in section.hits[:3])
    assert note._pull_soaks(REP, [section], 600_000)[-1] == (
        "  2:02 Right (its turn in the note; the hit had most of no group's living players): 0 of 4 hit, 300.00k "
        "each; not hit: Torvek (dead since 1:50), Mirelle (dead since 1:50), Halvard (alive), Rowan (alive); also "
        "hit: Brightleaf (Left), Garrick (Center), Linnea (Center), Thessaly (Center)")
    night = note._night_soaks(REP, cast(Any, [SimpleNamespace(f={"id": 1}, soaks=[section])]))
    assert night[1:3] == ["  Halvard (Right): hit by 1 of 2; missed 1: p1 2:02",
                          "  Rowan (Right): hit by 1 of 2; missed 1: p1 2:02"]
    assert night[-1] == ("  Given to the group whose turn it was in the note (the hit had most of no group's living "
                         "players): p1 2:02")
    # most of Center's living players in it: theirs by who was hit, whatever the turn (they soaked early)
    section = await _soaks(plan, FIRST_WAVE | {122_200: CENTER[:4]}, dead)
    assert (section.hits[-1].label, section.hits[-1].by_turn) == ("Center", False)


async def test_the_notes_turns_move_nothing_on_a_pull_that_doesnt_show_them_timed_right():
    """The turn a hit landed in is the group called nearest to it, which holds only if the note's times are right.
    A pull whose clear soaks each landed in their group's turn shows they are; one where a clear soak landed in
    another group's turn (a note a turn off), or with no clear soak, leaves every hit to who was hit."""
    half = {122_200: ["Brightleaf", "Thessaly", "Garrick", "Linnea"]}
    late = _note({"Right": (63, 124), "Left": (65, 126), "Center": (67, 128)})  # every call one turn late
    section = await _soaks(late, FIRST_WAVE | half)
    assert [(h.label, h.by_turn) for h in section.hits] == [("Right", False), ("Left", False), ("Center", False),
                                                            ("Center", False)]
    section = await _soaks(_note({"Right": (61, 122), "Left": (63, 124), "Center": (65, 126)}), half)
    assert [(h.label, h.by_turn) for h in section.hits] == [("Center", False)]


async def test_groups_called_together_have_no_turns():
    """Sszorak's note calls its two sides at the same second or a second apart, and the side that soaks first
    varies: the group called nearest to a hit says nothing there. Half a side's living players hit is that side's
    soak, as before, though the other side was called a second nearer to it."""
    sides = {"Left": [*RIGHT, *LEFT], "Right": CENTER}
    plan = _note({"Left": (11, 57), "Right": (11, 58)}, sides)
    section = await _soaks(plan, {8_000: sides["Left"], 16_000: sides["Right"], 59_000: sides["Left"][:4]})
    assert [(h.label, h.by_turn) for h in section.hits] == [("Left", False), ("Right", False), ("Left", False)]
    assert note._turn(59_000, {"Left": [11_000, 57_000], "Right": [11_000, 58_000]}) == ""
    assert note._turn(122_200, {"Right": [122_000], "Left": [124_000], "Center": [126_000]}) == "Right"
    assert note._turn(122_200, {"Right": [122_000], "Left": [124_000], "Center": None}) == ""  # one never timed
    assert note._turn(122_200, {"Right": [122_000], "Left": [300_000]}) == ""  # one called alone


async def test_the_recorded_nights_hits_as_their_wipes_were_decided(recorded):
    """The three hits of p7jRwgv8b1rY6LBd that were given to Center, the largest group, as pulls fell apart, and
    the one of fvkH4hGZwDRVnyXJ on all 18 players of the groups, which read "can't tell whose soak"."""
    text = (TOOLS.parent / "notes" / "twin-fangs-2026-10-21.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("p7jRwgv8b1rY6LBd")
    night = await note.check_night(rep, text, "Twin Fangs")
    hits = {(rep.pull_no(c.f), h.at // 1000): h for c in night.checks for s in c.soaks for h in s.hits}
    assert {k: len(h.hit) for k, h in hits.items() if h.why == note.SEVERAL} == {(5, 122): 11, (21, 124): 14}
    assert [k for k, h in hits.items() if h.by_turn] == [(21, 122)]
    turned = hits[(21, 122)]
    assert turned.label == "Right" and not turned.hit & set(turned.group)
    assert sorted(n for n, st in turned.absent if st == "alive") == ["Cptola", "Enfo", "Krøpling", "Namsus"]
    assert turned.others == [("Attractîv", "Left"), ("Draiman", "Center"), ("Nexsozz", "Center"),
                             ("Slushmania", "Center")]
    # every other hit given to a group landed in that group's turn, within half a second of its callout
    calls = {"Right": (61, 122, 216, 277, 371, 432), "Left": (63, 124, 218, 279, 373, 434),
             "Center": (65, 126, 220, 281, 375, 436)}
    given = [h for h in hits.values() if h.label and not h.by_turn]
    assert len(given) == 171
    assert all(min(abs(h.at / 1000 - t) for t in calls[h.label]) < 0.5 for h in given)
    earlier = (TOOLS.parent / "notes" / "twin-fangs-2026-10-19.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("fvkH4hGZwDRVnyXJ")
    night = await note.check_night(rep, earlier, "Twin Fangs")
    hits = {(rep.pull_no(c.f), h.at // 1000): h for c in night.checks for s in c.soaks for h in s.hits}
    assert {k: h.near for k, h in hits.items() if h.why == note.SEVERAL} == {
        (6, 65): {"Left": 6, "Right": 6, "Center": 6}}
    assert not any(h.by_turn for h in hits.values())


async def test_the_recorded_sszorak_and_coiled_altar_nights_have_no_such_hit(recorded):
    """Sszorak's note calls its sides together and the Coiled Altar's one group at a time: no hit of either night
    is on several groups at once or given by a turn."""
    for code, boss, name in (("vjt3KLbN8g62J1H9", "Sszorak", "sszorak-2026-10-12"),
                             ("fk2z6bCw4xRLNvht", "Coiled Altar", "coiled-altar-2026-11-02")):
        text = (TOOLS.parent / "notes" / f"{name}.txt").read_text(encoding="utf-8")
        night = await note.check_night(await server.lib().report(code), text, boss)
        hits = [h for c in night.checks for s in c.soaks for h in s.hits]
        assert hits and not any(h.by_turn or h.why == note.SEVERAL for h in hits)
