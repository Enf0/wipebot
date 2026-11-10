"""A raid note's callouts for personals and its planned cooldowns not cast (report/note.py): whether a defensive was
running, was ready or could be pressed, by one rule (cooldowns.spell_at) over the player's own casts on the night."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE
from tests.support.recorded import Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note
from wipebot.core.report.cooldowns import spell_at

MIRROR_IMAGE = 55342
# as Lorrgs has them: no duration for Ice Block or Earth Elemental, neither one nor a cooldown for Reincarnation
IMAGE = {"name": "Mirror Image", "spell_id": MIRROR_IMAGE, "cooldown": 120, "duration": 40, "tags": ["defensive"]}
BLOCK = {"name": "Ice Block", "spell_id": 45438, "cooldown": 240, "duration": 0, "tags": ["defensive"]}
SHIFT = {"name": "Astral Shift", "spell_id": 108271, "cooldown": 90, "duration": 12, "tags": ["defensive"]}
ANKH = {"name": "Reincarnation", "spell_id": 21169, "cooldown": 0, "duration": 0, "tags": ["defensive"]}
ELEMENTAL = {"name": "Earth Elemental", "spell_id": 381755, "cooldown": 300, "duration": 0, "tags": ["defensive"]}
GRACE = {"name": "Spiritwalker's Grace", "spell_id": 79206, "cooldown": 120, "duration": 15, "tags": ["move"]}
MAGE, SHAMAN = 1, 2
NAMES = {MAGE: "Zebinomage", SHAMAN: "Enfo"}
KITS = {MAGE: [IMAGE, BLOCK], SHAMAN: [SHIFT, ANKH, ELEMENTAL, GRACE]}


class Raid:
    """A stand-in report of one boss's pulls: a mage and a shaman in each, their specs' spells as Lorrgs has them,
    and each pull's casts (player, spell, ms) and deaths (player, ms)."""

    def __init__(self, casts: dict[int, list[tuple[int, dict, float]]], deaths: dict[int, list[tuple[int, float]]]):
        self.casts, self.died = casts, deaths
        self.by_boss = {"Vashnik": [{"id": i, "startTime": 0} for i in casts]}
        self.live: set[int] = set()  # the pulls still going
        self.asked: list[list[int]] = []  # the pulls each read of the casts was for

    def boss_key(self, f: dict) -> str:
        return "Vashnik"

    def pull_players(self, f: dict) -> dict[int, Any]:
        return {i: SimpleNamespace(id=i, name=name, role="dps") for i, name in NAMES.items()}

    def in_pull(self, f: dict, p: Any) -> Any:
        return p

    async def spells(self, q: Any) -> list[dict]:
        return KITS[q.id]

    def is_spell(self, c: dict, sp: dict) -> bool:
        return c["abilityGameID"] == sp["spell_id"]

    async def tracked_casts(self, fights: list[dict]) -> dict[int, list[dict]]:
        self.asked.append([f["id"] for f in fights])
        return {f["id"]: [{"sourceID": who, "abilityGameID": sp["spell_id"], "timestamp": at}
                          for who, sp, at in self.casts[f["id"]]] for f in fights}

    async def deaths(self, f: dict) -> list[dict]:
        return [{"targetID": who, "timestamp": at} for who, at in self.died.get(f["id"], [])]


async def _personals(raid: Raid, pull: int, at: float) -> note.CalloutCheck:
    """A callout for personals to everyone at `at` (ms) of a pull, checked."""
    rep = cast(Any, raid)
    players = {name: q for q in raid.pull_players({}).values() for name in [q.name]}
    plan = note.Note(3418, "Mythic", "Vashnik")
    [check] = await note._callouts(rep, {"id": pull, "startTime": 0}, plan, players, lambda name, at: None,
                                   lambda c: c.at * 1000, [(at, note.Callout(at / 1000, 1, (), "Big Personals"))])
    return check


def test_where_a_spell_stood_is_read_from_the_players_own_casts_and_lorrgs_numbers():
    assert spell_at(IMAGE, [], 199_000) == (None, None) and spell_at(IMAGE, [], 199_000).ready  # never pressed
    then = spell_at(IMAGE, [174_000], 199_000)  # pressed 25 s before, lasting 40 s
    assert then.running == 174_000 and then.cooling == 174_000 and not then.ready
    later = spell_at(IMAGE, [174_000], 230_000)  # 56 s after: it ran out, and isn't back
    assert later.running is None and later.cooling == 174_000
    assert spell_at(IMAGE, [174_000], 294_000).ready  # 120 s after: back
    assert spell_at(IMAGE, [210_000], 199_000).ready  # a cast after the moment says nothing about it
    assert spell_at(ANKH, [190_000], 199_000).running is None  # no duration from Lorrgs: never said to be running


async def test_a_defensive_pressed_before_the_window_and_still_up_isnt_pressing_none():
    """A mage who pressed Mirror Image (40 s) at 2:54 read as pressing none at the 3:19 callout, in 10 of the 18
    callouts they were named to the raid leader for: only a cast from 3 s before to 5 s after counted."""
    check = await _personals(Raid({1: [(MAGE, IMAGE, 174_000), (SHAMAN, SHIFT, 198_000)]}, {}), 1, 199_000)
    assert check.pressed == [("Enfo", [(198_000, SHIFT)])] and check.idle == []
    assert check.excused == [
        ("Zebinomage", "already running: Mirror Image [defensive] pressed at 2:54, lasting 40 s")]
    late = await _personals(Raid({1: [(MAGE, IMAGE, 150_000)]}, {}), 1, 199_000)  # 49 s before: it had run out
    assert late.excused == [] and [name for name, _ in late.idle] == ["Enfo", "Zebinomage"]


async def test_a_cast_after_dying_inside_the_window_isnt_pressing():
    """A shaman who died at 3:21 and came back with Reincarnation at 3:22 read as having pressed a defensive for the
    3:19 callout. They pressed none while alive, and the line says when they died."""
    raid = Raid({1: [(SHAMAN, ANKH, 202_000), (SHAMAN, SHIFT, 60_000)]}, {1: [(SHAMAN, 201_000)]})
    check = await _personals(raid, 1, 199_000)
    assert check.pressed == [] and check.fell == {"Enfo": 201_000}
    assert dict(check.idle)["Enfo"] == [SHIFT] and "Enfo" not in check.others


async def test_ready_lists_only_what_answers_the_callout_and_the_player_cast_that_night():
    """"ready:" listed every spell of the spec with a cooldown: a fifth of them the player never cast on any pull of
    the night (a talent they don't have, a passive such as Cheating Death), and movement spells beside them."""
    raid = Raid({1: [(SHAMAN, SHIFT, 30_000), (MAGE, BLOCK, 20_000)],  # the night before the pull asked about
                 2: [(SHAMAN, GRACE, 58_000), (MAGE, IMAGE, 110_000), (MAGE, IMAGE, 300_000)]}, {})
    check = await _personals(raid, 2, 200_000)
    ready = {name: [sp["name"] for sp in spells] for name, spells in check.idle}
    # Earth Elemental and Reincarnation never cast all night, Spiritwalker's Grace no defensive; Mirror Image, pressed
    # at 1:50 for 40 s, had run out and wasn't back
    assert ready == {"Enfo": ["Astral Shift"], "Zebinomage": ["Ice Block"]}
    assert check.others == {}  # Spiritwalker's Grace at 0:58 is outside the window


async def test_one_pulls_check_leaves_a_pull_still_going_out_of_the_night_it_reads():
    """One pull's check read the casts of every pull on the boss, the one still being logged among them: those are
    never kept, so every check of an ended pull asked Warcraft Logs for them again, and a spell pressed so far only
    in the pull still going read as one the player has. The pull asked about is read whatever it is."""
    raid = Raid({1: [(SHAMAN, SHIFT, 30_000)], 2: [], 3: [(MAGE, BLOCK, 20_000)]}, {})
    raid.live = {3}
    check = await _personals(raid, 2, 200_000)
    assert raid.asked == [[1, 2]]
    assert {name: [sp["name"] for sp in spells] for name, spells in check.idle} == {
        "Enfo": ["Astral Shift"], "Zebinomage": []}
    await _personals(raid, 3, 10_000)
    assert raid.asked[1:] == [[1, 2, 3]]


async def test_a_planned_cooldown_not_cast_says_when_the_spell_was_on_cooldown_from_their_own_cast():
    """"not cast (alive)" read as a miss where the spell couldn't be pressed: the player had cast it off the plan, or
    the note plans it again inside its cooldown. By Lorrgs' base cooldown, unless two casts of theirs on the night
    were closer than that (a talent that shortens it): then by those."""
    plan = note.Note(3418, "Mythic", "Vashnik", cooldowns=[note.Cooldown(58, 1, "Zebinomage", MIRROR_IMAGE),
                                                            note.Cooldown(200, 1, "Zebinomage", MIRROR_IMAGE)])
    players = cast(Any, {"Zebinomage": SimpleNamespace(id=MAGE)})

    async def uses(raid: Raid, at: float) -> list[note.PlannedUse]:
        rep = cast(Any, raid)
        rep.ids_named, rep.ability, rep.was_in = lambda names: set(), lambda spell: "Mirror Image", lambda f, p: True
        f = {"id": 2, "startTime": 0}

        async def casts(rep, f, n):
            return [{"type": "cast", "sourceID": MAGE, "abilityGameID": MIRROR_IMAGE, "timestamp": at}]
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(note, "_planned_casts", casts)
            planned = await note._cooldowns(rep, f, plan, players, lambda name, at: "alive", lambda c: c.at * 1000,
                                            600_000, night=note._Night(rep, raid.by_boss["Vashnik"]))
        assert planned is not None
        return planned.uses

    first, again = await uses(Raid({2: [(MAGE, IMAGE, 23_000)]}, {}), 23_000)  # cast at 0:23, 35 s before its time
    assert first.cast_at is None and first.cooling == (23_000, 120, 120)
    assert again.cast_at is None and again.cooling is None  # 2:57 later: it was back
    # two casts of theirs 32 s apart on another pull of the night: at 0:58, 35 s after their cast, it could be back
    close = Raid({1: [(MAGE, IMAGE, 10_000), (MAGE, IMAGE, 42_000)], 2: [(MAGE, IMAGE, 23_000)]}, {})
    assert [u.cooling for u in await uses(close, 23_000)] == [None, None]
    # ... and 50 s apart: it couldn't, by their own casts, with Lorrgs' 120 s beside them
    apart = Raid({1: [(MAGE, IMAGE, 10_000), (MAGE, IMAGE, 60_000)], 2: [(MAGE, IMAGE, 23_000)]}, {})
    assert [u.cooling for u in await uses(apart, 23_000)] == [(23_000, 50, 120), None]


def test_a_night_counts_the_planned_uses_not_cast_near_the_end_and_on_cooldown():
    rep = SimpleNamespace(pull_no=lambda f: f["id"], ability_ref=lambda spell: f"Spell [{spell}]")
    image = note.Cooldown(58, 1, "Zebinomage", MIRROR_IMAGE)

    def check(i: int, until: float, cooling: tuple[float, float, float] | None) -> Any:
        use = note.PlannedUse(58_000, image, None, "alive", [23_000] if cooling else [], cooling)
        return SimpleNamespace(f={"id": i}, until=until, planned=note.Planned([use], [], 0))
    lines = note._night_cooldowns(cast(Any, rep), [check(1, 300_000, None), check(2, 64_000, None),
                                                   check(3, 61_000, (23_000, 120, 120))])
    counts = "2 of them in the last 10 s, 1 of them on cooldown"
    assert lines[1] == f"  Zebinomage: 3 planned uses; not cast 3, {counts}"
    assert lines[2] == f"    0:58 Spell [55342], 3 pulls: not cast 3 (p1, p2, p3), {counts}"


async def test_the_recorded_night_says_who_had_one_running_who_died_and_what_was_on_cooldown(monkeypatch):
    """The Sszorak night as recorded. Pull 42: the 3:19 callout's mage with Mirror Image up since 2:54, the shaman
    who died at 3:21 before Reincarnation, and Zephyr planned at 0:58 after a cast at 0:23. Pull 4: Divine Hymn, back
    after 124 s for this priest on another pull, where Lorrgs' base cooldown is 180 s."""
    serve_from(monkeypatch, Replay())
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    plan = NOTE.read_text(encoding="utf-8")
    night = await note.note_check(rep, plan, None, "Sszorak", all_pulls=True)  # first: it reads every pull's deaths
    assert ("    2:23 Divine Hymn [64843], 8 pulls: late 3 (9 s), not cast 5 (p4, p7, p23, p27, p32), 5 of them in the "
            "last 10 s, 2 of them on cooldown") in night
    assert "not counted at 10 (one already running 10)" in night
    assert ("  2:23 Miralupa Divine Hymn [64843]: not cast within 30s (alive; cast at 0:35; on cooldown from the 0:35 "
            "cast, 124 s by their closest two casts of it on the night (Lorrgs' base cooldown: 180 s); 9 s before the "
            "wipe was decided)") in await note.note_check(rep, plan, 4, "Sszorak")
    text = await note.note_check(rep, plan, 42, "Sszorak")
    assert "Zebinomage (already running: Mirror Image [defensive] pressed at 2:54, lasting 40 s)" in text
    assert "Enfo (died at 3:21; ready: none)" in text and "Enfo Reincarnation" not in text
    assert "(alive; cast at 0:23, 3:00; on cooldown from the 0:23 cast, 120 s by Lorrgs' base cooldown)" in text
    assert "Zen Meditation" not in text and "Earth Elemental" not in text  # never cast by them on the night
