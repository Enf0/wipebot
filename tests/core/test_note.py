"""Reading a raid note (report/note.py; NSRT's format). How it's checked against real pulls is pinned by the tool
cases (test_tools.py)."""
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE
from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import FactError, Player, Report, note

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


KICK_ORDER = {"Kick #1 left": "Torvek", "Kick #2 left": "Mirelle", "Kick #3 left": "Halvard", "Kick #4 left": "Rowan"}


async def _wave(monkeypatch, *casts: tuple[float, str | None], until: float = 600_000) -> list[note.KickCheck]:
    """The kick check of a pull whose note calls Kick #1 to #4 left at 1:40, 1:42, 1:44 and 1:46 (KICK_ORDER) and
    whose adds began these casts, (second, who kicked it or None when it went through)."""
    plan = note.Note(3418, "Mythic", "Vashnik", callouts=[
        note.Callout(100 + 2 * i, 1, (name,), text) for i, (text, name) in enumerate(KICK_ORDER.items())])

    async def kickable(rep, f):
        return {1}

    async def begun(rep, f):
        return {("Broodling", "Burst"): [(at * 1000, "kicked" if by else "went through", by, 1) for at, by in casts]}
    monkeypatch.setattr(note, "kicked_tonight", kickable)
    monkeypatch.setattr(note, "interruptible_casts", begun)
    players = cast(Any, {n: SimpleNamespace(name=n) for n in KICK_ORDER.values()})
    return await note._kicks(cast(Any, None), {"id": 1, "startTime": 0}, plan, players, lambda name, at: "alive",
                             lambda c: c.at * 1000, until)


async def test_a_player_whose_cast_went_through_and_who_kicked_the_next_one_didnt_make_no_kick(monkeypatch):
    """Mirelle's cast, the second of the wave, went through and she kicked the third, Halvard's; Rowan kicked
    nothing. "No kick (alive)" was said of both, and an answer named the first as not having kicked. Her cast still
    went through, so it stays hers and counts as not kicked; the line says which cast she kicked."""
    kicks = await _wave(monkeypatch, (100, "Torvek"), (102, None), (104, "Mirelle"), (106, None), (140, "Rowan"))
    assert [(k.player, k.kicked, k.cast[1:], k.other) for k in kicks] == [
        ("Torvek", True, ("kicked", "Torvek"), None), ("Mirelle", False, ("went through", None), 104_000),
        ("Halvard", False, ("kicked", "Mirelle"), None),
        ("Rowan", False, ("went through", None), None)]  # the cast Rowan kicked at 2:20 is past this wave
    assert [note._kick_line(k) for k in kicks] == [
        "kicked the cast begun at 1:40",
        "their cast, begun at 1:42, went through; they kicked the cast begun at 1:44",
        "covered: their cast, begun at 1:44, was kicked by Mirelle (alive)",
        "no kick (alive); their cast, begun at 1:46, went through"]
    dead = replace(kicks[1], state="dead since 1:41", other=100_000)  # she kicked the first cast, then died
    assert note._kick_line(dead) == "no kick (dead since 1:41); their cast, begun at 1:42, went through"


async def test_a_kick_on_a_players_other_assignment_of_the_wave_isnt_a_kick_on_another_cast(monkeypatch):
    """A player the note gives two kicks of a wave who made one of them kicked their own cast, not another."""
    monkeypatch.setitem(KICK_ORDER, "Kick #3 left", "Torvek")
    kicks = await _wave(monkeypatch, (100, None), (102, "Mirelle"), (104, "Torvek"), (106, "Rowan"))
    assert [(k.player, k.kicked, k.other) for k in kicks if k.player == "Torvek"] == [("Torvek", False, None),
                                                                                    ("Torvek", True, None)]


async def test_a_night_counts_the_casts_let_through_while_kicking_another_beside_those_not_kicked(monkeypatch):
    """Over three pulls Mirelle's cast went through each time and she kicked the next one on two: all three are
    still her casts that weren't kicked, with the two named apart and each with the cast she kicked."""
    late = ((100, "Torvek"), (102, None), (104, "Mirelle"), (106, "Rowan"))
    none = ((100, "Torvek"), (102, None), (104, "Halvard"), (106, "Rowan"))
    checks = [SimpleNamespace(f={"id": i}, decided=None, kicks=await _wave(monkeypatch, *casts))
              for i, casts in enumerate((late, none, late), 1)]
    lines = note._night_kicks(cast(Any, SimpleNamespace(pull_no=lambda f: f["id"])), cast(Any, checks))
    assert ("and the casts of theirs that weren't kicked (they went through unless it says otherwise; with the cast of "
            "the wave they kicked instead, if any); dead for how many more:") in lines[0]
    assert lines[1] == ("  Mirelle (Kick #2 left): kicked 0 of 3; not kicked 3, kicked another cast on 2: p1 1:42 "
                        "(kicked the 1:44 cast), p2 1:42, p3 1:42 (kicked the 1:44 cast)")
    assert "  Halvard (Kick #3 left): kicked 1 of 3; covered 2 (Mirelle 2)" in lines
    assert "  Their casts that went through, per assignment: \"Kick #2 left\" 3" in lines


async def test_the_recorded_kicks_one_cast_late_are_said_and_still_count_as_their_cast_going_through(monkeypatch):
    """The night of p7jRwgv8b1rY6LBd: on pulls 18 and 34 the cast of Kick #4 left went through and its player kicked
    the next one 2 s later, on pull 19 the same for Kick #3 left, on pull 28 the player of Kick #4 right kicked a
    cast 5 s after theirs. Each read "no kick (alive)", and they were named as not having kicked."""
    serve_from(monkeypatch, Replay())
    text = (TOOLS.parent / "notes" / "twin-fangs-2026-10-21.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("p7jRwgv8b1rY6LBd")
    night = await note.check_night(rep, text, "Twin Fangs")
    late = {(rep.pull_no(c.f), k.player, k.callout.text): (round(k.cast[0] / 1000, 1), round(k.other / 1000, 1))
            for c in night.checks for k in c.kicks if k.other is not None and rep.pull_no(c.f) in (18, 19, 28, 34)}
    assert late == {(18, "Dscopi", "Kick #4 left"): (104.5, 106.5), (19, "Algmor", "Kick #3 left"): (102.5, 104.5),
                    (28, "Peggerino", "Kick #4 right"): (104.5, 109.5), (34, "Dscopi", "Kick #4 left"): (104.5, 106.5)}
    through = [k for c in night.checks for k in c.kicks if k.cast[1] == "went through"]
    assert all(not k.kicked for k in through) and all(k.other is None for c in night.checks for k in c.kicks
                                                      if k.cast[1] == "kicked")


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


def test_a_callout_isnt_missed_by_one_the_note_gives_another_cooldown_or_a_tank_keeping_one_for_after():
    rep = cast(Report, SimpleNamespace(is_spell=lambda c, sp: c["abilityGameID"] == sp["spell_id"]))
    cry = {"name": "Rallying Cry", "spell_id": 97462, "tags": ["raid_cd"]}
    warrior, tank = SimpleNamespace(id=1, role="dps"), SimpleNamespace(id=2, role="tank")
    plan = note.Note(0, "Mythic", "Sszorak", cooldowns=[note.Cooldown(62, 1, "Brightleaf", 97462)])
    players = cast(dict[str, Player], {"Brightleaf": warrior, "Stonewall": tank})
    def when(c: note.Cooldown) -> float:  # one phase, from the pull's start
        return c.at * 1000
    assert note._excused(rep, plan, players, cast(Player, warrior), 60_000, when, [(61_000, cry)]) == (
        "pressed Rallying Cry at 1:01, the note's plan for them then")
    assert note._excused(rep, plan, players, cast(Player, warrior), 60_000, when, []) == ""  # planned, not pressed
    wall = {"name": "Shield Wall", "spell_id": 871, "tags": ["tank"]}
    assert note._excused(rep, plan, players, cast(Player, tank), 60_000, when, [(72_000, wall)]) == (
        "a tank, kept Shield Wall for 1:12")
    assert note._excused(rep, plan, players, cast(Player, tank), 60_000, when, [(90_000, wall)]) == ""  # too late


def test_a_planned_spell_never_cast_on_any_pull_says_another_may_do_its_job():
    """The note gave Peggerino Curse of Tongues; they cast Curse of the Satyr, a talent's curse that does its job, so
    Tongues read as not cast on every pull and an answer said they never cursed (corrected by the raider,
    4N9JMwBFPAjXyV3Z). A spell cast on some pulls, if not at its time, says nothing more."""
    rep = SimpleNamespace(pull_no=lambda f: f["id"], ability_ref=lambda spell: f"Spell [{spell}]")
    tongues, wind = note.Cooldown(190, 1, "Peggerino", 1714), note.Cooldown(41, 1, "Enfo", 192077)

    def check(i: int) -> Any:
        uses = [note.PlannedUse(190_000, tongues, None, "alive", []),
                note.PlannedUse(41_000, wind, None, "alive", [90_000] if i == 1 else [])]
        return SimpleNamespace(f={"id": i}, planned=note.Planned(uses, [], 0))
    lines = note._night_cooldowns(cast(Any, rep), [check(i) for i in (1, 2, 3)])
    assert ("    3:10 Spell [1714], 3 pulls: not cast 3 (p1, p2, p3); never cast on any of these pulls: a talent may "
            "replace it, or another spell do its job (breakdown of their casts)") in lines
    assert "    0:41 Spell [192077], 3 pulls: not cast 3 (p1, p2, p3)" in lines
