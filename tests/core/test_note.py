"""Reading a raid note (report/note.py; NSRT's format). How it's checked against real pulls is pinned by the tool
cases (test_tools.py)."""
from collections import Counter
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE
from wipebot.core.report import FactError, note

VASHNIK = "EncounterID:3418;Difficulty:Mythic;Name:Vashnik\ntime:30;ph:2;tag:Torvek;spellid:98008;\n"


def test_a_raid_note_is_read_per_boss():
    notes = note.parse(NOTE.read_text(encoding="utf-8") + "\n" + VASHNIK)
    assert [(n.encounter_id, n.boss) for n in notes] == [(3420, "Sszorak"), (3418, "Vashnik")]
    s = notes[0]
    assert len(s.cooldowns) == 59 and s.cooldowns[6] == note.Cooldown(27, 1, "Scopidruid", 106898, 1297707)
    assert s.groups["Mutilate Soaks"]["Right"][-2:] == ["Lïzårðmâñ", "Miralupa"]
    assert s.groups["Virulence Spreads"]["Bait Serpent's Fury / Bite"] == ["Martin"]
    assert note.Callout(75, 1, (), "Big Personals") in s.callouts
    assert note.Callout(25.5, 1, ("Martin",), "Trigger Virulence") in s.callouts
    assert notes[1].cooldowns == [note.Cooldown(30, 2, "Torvek", 98008)] and "invitelist" not in str(s.groups)
    assert note.for_boss(notes, 3418) is notes[1] and note.for_boss(notes, 1) is None
    with pytest.raises(FactError, match="EncounterID"):
        note.parse("time:30;ph:1;tag:Torvek;spellid:98008;")


def test_a_raid_note_saved_in_versions_is_read_per_version():
    """The bot passes every saved version, each after a [saved ...] line (test_saved.py has which pull gets which);
    a paste without marks is one version."""
    v1 = "EncounterID:3418;Name:Vashnik\ntime:30;ph:1;tag:Torvek;spellid:98008;\n"
    v2 = "EncounterID:3418;Name:Vashnik\ntime:40;ph:1;tag:Torvek;spellid:98008;\n"
    found = note.versions(f"[saved 2000 by Torvek]\n{v2}\n[saved 1000 by Mirelle]\n{v1}")
    assert [(v.saved_at, v.saved_by, v.notes[0].cooldowns[0].at) for v in found] == [(1000, "Mirelle", 30),
                                                                                     (2000, "Torvek", 40)]
    assert found[1].source() == "saved by Torvek Thu 1 Jan 00:33 UTC"
    only = note.versions(v1)
    assert len(only) == 1 and only[0].saved_at is None and only[0].source() == "as pasted"


def test_a_player_hit_by_almost_none_of_their_groups_soaks_is_named_as_likely_not_in_it():
    """A reserve or a player moved since the note was written misses nearly every soak of the group the note puts
    them in: under a fifth of at least three while alive is named apart, not only counted as missed."""
    took, alive = Counter(Torvek=1, Mirelle=3, Halvard=0), Counter(Torvek=8, Mirelle=4, Halvard=2)
    line = note._few_soaks(took, alive)
    assert "likely not soaking with this group" in line and line.endswith(": Torvek (1 of 8)")
    assert note._few_soaks(Counter(Mirelle=3), Counter(Mirelle=4)) == ""


def _kicks_by(*pulls: Mapping[str, str | None], missing: tuple[str, ...] = ()) -> list:
    """Night checks with one kick per assignment and pull, {"Kick #1 left": who kicked its cast, or None when it went
    through}, each assignment its player's in the note (Kick #1 left: Sonyosh, Kick #2 left: Dragepie); `missing`,
    named in the note, absent from pulls 1-4."""
    owners = {"Kick #1 left": "Sonyosh", "Kick #2 left": "Dragepie"}

    def kick(text: str, by: str | None) -> note.KickCheck:
        cast = (0, "kicked", by) if by else (0, "went through", None)
        return note.KickCheck(0, note.Callout(0, 1, (owners[text],), text), owners[text], cast, by == owners[text],
                              "alive")
    return [SimpleNamespace(missing=list(missing) if i < 4 else [], kicks=[kick(t, by) for t, by in p.items()])
            for i, p in enumerate(pulls)]


def test_a_kick_order_the_pulls_before_had_is_found_and_a_backup_kicker_isnt_one():
    """Pulls 1-4 had Attractîv on Kick #1 left (and Zebinomage, whom the note names, absent), pulls 5-8 the note's
    order; a player who takes a few of a slot's casts all night is a backup, not another order."""
    early = {"Kick #1 left": "Attractîv", "Kick #2 left": "Dragepie"}
    late = {"Kick #1 left": "Sonyosh", "Kick #2 left": "Dragepie"}
    checks = _kicks_by(early, early, {**early, "Kick #2 left": "Karadim"}, early, late, late, late, late,
                       missing=("Zebinomage",))
    change = note._order_change(checks)
    assert change == note.OrderChange(4, [("Kick #1 left", "Attractîv", 4, 4)], ["Zebinomage"])
    backup = _kicks_by(*[{**late, "Kick #2 left": "Karadim" if i % 3 == 0 else "Dragepie"} for i in range(9)])
    assert note._order_change(backup) is None
    tank = _kicks_by(*[{**late, "Kick #1 left": "Wutroll"}] * 4, late, late, late, late)
    assert note._order_change(tank, tanks={"Wutroll"}) is None and note._order_change(tank)  # a tank backs up


def test_a_player_kicking_a_cast_not_theirs_pull_after_pull_is_a_mistake_and_not_another_order():
    """Dragepie, whom the note gives Kick #2 left, kicked Kick #1 left's cast in pulls 1-4 while their own went
    through, and the note's player for it was there: one player's mistake, which the kicks show."""
    sniped = {"Kick #1 left": "Dragepie", "Kick #2 left": None}
    late = {"Kick #1 left": "Sonyosh", "Kick #2 left": "Dragepie"}
    assert note._order_change(_kicks_by(sniped, sniped, sniped, sniped, late, late, late, late)) is None


def test_pulls_checked_against_a_note_saved_after_they_began_are_named():
    """A note saved only after the night (its first version, so the pulls are checked against it) may not be the one
    those pulls had; a pasted note has no time."""
    rep = SimpleNamespace(start_ms=1_000_000, pull_no=lambda f: f["id"])
    fights = [{"id": i, "startTime": i * 100_000} for i in (1, 2, 3)]
    saved = note.Version([], saved_at=1_250, saved_by="Rowan")  # between pulls 2 and 3
    assert note._saved_late(cast(Any, rep), fights, {i: saved for i in (1, 2, 3)}) == [
        "The note was saved after pulls 1-2 began, with none saved before them: the plan for them may have been "
        "different."]
    assert note._saved_late(cast(Any, rep), fights, {i: note.Version([]) for i in (1, 2, 3)}) == []


async def test_one_cooldown_written_twice_in_a_note_is_checked_as_one_use(monkeypatch):
    """A note with Zephyr for Lïzårðmâñ at both 0:42 and 0:43: one cast can cover only one line, so the other read as
    not cast on every pull (13 of 18 one night, said to the raider). Checked as one use, and the output says so."""
    zephyr = 374227
    plan = note.Note(3418, "Mythic", "Vashnik", cooldowns=[note.Cooldown(at, 1, "Lïzårðmâñ", zephyr)
                                                            for at in (42, 43, 90)])
    rep = SimpleNamespace(ids_named=lambda names: set(), ability=lambda spell: "Zephyr", was_in=lambda f, p: True,
                          ability_ref=lambda spell: "Zephyr [374227]")

    async def casts(rep, f, n):
        return [{"type": "cast", "sourceID": 5, "abilityGameID": zephyr, "timestamp": 43_200},
                {"type": "cast", "sourceID": 5, "abilityGameID": zephyr, "timestamp": 91_000}]
    monkeypatch.setattr(note, "_planned_casts", casts)
    players = cast(Any, {"Lïzårðmâñ": SimpleNamespace(id=5)})
    planned = await note._cooldowns(cast(Any, rep), {"startTime": 0}, plan, players, lambda name, at: "alive",
                                    lambda c: c.at * 1000, 600_000)
    assert planned is not None
    assert [(u.at, u.cast_at) for u in planned.uses] == [(42_000, 43_200), (90_000, 91_000)]
    assert planned.twice == [(42_000, 43_000, plan.cooldowns[1])] and planned.skipped == 0
    assert note._twice(cast(Any, rep), planned.twice[0]) == (
        "The note plans Lïzårðmâñ's Zephyr [374227] at both 0:42 and 0:43: one use written twice, checked as the first")
