"""Reading a raid note (report/note.py; NSRT's format) and its kick check. How it's checked against real pulls is
pinned by the tool cases (test_tools.py); the soak check has its own tests (test_note_soaks.py)."""
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE
from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.notebook import Notebook
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


def test_a_spell_line_to_everyone_is_a_reminder_and_a_realm_is_no_part_of_a_name():
    """A spell line to everyone shows the boss's cast or a move with an icon: it was read as a cooldown planned for a
    player called "everyone", who then "wasn't in the pull" seven times a pull. A name with its realm is the name;
    a role or a group stays the tag it is (the check sets it aside: it's no one's name)."""
    [plan] = note.parse("EncounterID:3418;Name:Vashnik\n"
                        "time:154;ph:1;tag:everyone;spellid:98008;text:Go to diamond;\n"
                        "time:30;ph:1;tag:everyone;text:Big Personals;\n"
                        "time:40;ph:1;tag:healer;text:Personals;\n"
                        "time:50;ph:1;tag:group1 Torvek-Kazzak;spellid:98008;\n"
                        "invitelist:Torvek-Kazzak,Mirelle-TarrenMill;\n")
    assert plan.reminders == [note.Reminder(154, 1, 98008, "Go to diamond")]
    assert plan.cooldowns == [note.Cooldown(50, 1, "group1", 98008), note.Cooldown(50, 1, "Torvek", 98008)]
    assert plan.callouts == [note.Callout(30, 1, (), "Big Personals"), note.Callout(40, 1, ("healer",), "Personals")]
    assert plan.invited == ["Torvek", "Mirelle"] and plan.unread == []
    assert note.receipt(plan) == "2 timed cooldowns, 2 callouts, 1 reminder to everyone"


def test_a_heading_or_a_label_written_twice_keeps_both():
    """Of two blocks under one heading the second's labels replaced the first's, and of two lines with one label
    the second replaced the first, without a word: a raider's job was gone from the assignments (Torvek's and
    Halvard's here). Both are kept, the second numbered."""
    [plan] = note.parse("EncounterID:3418;Name:Vashnik\n\n"
                        "Orb Carriers\ngreen orb: Torvek\ngreen orb: Mirelle\nred orb: Halvard\n\n"
                        "Orb Carriers\nred orb: Rowan\nblue orb: Brightleaf\n")
    assert plan.groups == {"Orb Carriers": {"green orb": ["Torvek"], "green orb (2)": ["Mirelle"],
                                            "red orb": ["Halvard"]},
                           "Orb Carriers (2)": {"red orb": ["Rowan"], "blue orb": ["Brightleaf"]}}
    assert plan.unread == []


def test_the_interrupt_order_between_intstart_and_intend_is_read_and_shown_as_written():
    """NSRT's interrupt block has no colon and no time, so its lines were taken for headings and shown nowhere: a
    raider third on its first line was told the note has no kick order. Which add a line is for is the addon's rule
    for the boss, so the lines are numbered and said to be unchecked."""
    [plan] = note.parse("EncounterID:3418;Name:Vashnik\n\nintstart\nTorvek Mirelle-Kazzak Halvard\n\n"
                        "Rowan Brightleaf Rowan\nintend\n\nOrbs\nred orb: Rowan\n")
    assert plan.interrupts == [["Torvek", "Mirelle", "Halvard"], ["Rowan", "Brightleaf", "Rowan"]]
    assert plan.groups == {"Orbs": {"red orb": ["Rowan"]}} and plan.unread == []
    assert note.receipt(plan) == "0 timed cooldowns, 0 callouts, an interrupt order of 2 lines"
    assert note._assignments(cast(Any, None), plan)[-1] == (
        "Interrupt order (the note's intstart block, each line in the addon's order; not checked against the log): "
        "line 1: Torvek Mirelle Halvard; line 2: Rowan Brightleaf Rowan")
    [open_] = note.parse("EncounterID:3418;Name:Vashnik\n\nintstart\nTorvek Mirelle\n\nOrbs\nred orb: Rowan\n")
    assert open_.interrupts == [] and open_.groups == {"Orbs": {"red orb": ["Rowan"]}}  # never closed: no block
    assert open_.unread == ["intstart", "Torvek Mirelle"]


def test_what_the_note_says_that_isnt_understood_is_kept_and_said():
    """A line the reading can't take was dropped without a word: a time as 1:03, a phase that's no whole number, a
    line with no tag, words under no heading's label. NSRT reads a line's fields wherever they stand, so a line
    whose time isn't first is a timed line (it was taken for a heading, and the line after it lost)."""
    [plan] = note.parse("EncounterID:3418;Name:Vashnik\n"
                        "ph:1;time:16;tag:Torvek;spellid:98008;\n"
                        "time:1:03;ph:1;tag:Torvek;spellid:98008;\n"
                        "time:7;ph:2.5;tag:Torvek;spellid:98008;\n"
                        "time:20;ph:1;spellid:98008;\n"
                        "time:25;ph:1;tag:Torvek;\n\n"
                        "Use a healthstone early\n\n"
                        "Orbs\nred orb: Rowan\n")
    assert plan.cooldowns == [note.Cooldown(16, 1, "Torvek", 98008)]
    assert plan.unread == ["time:1:03;ph:1;tag:Torvek;spellid:98008;", "time:7;ph:2.5;tag:Torvek;spellid:98008;",
                           "time:20;ph:1;spellid:98008;", "time:25;ph:1;tag:Torvek;", "Use a healthstone early"]
    assert note.not_understood(plan) == 'not understood: 5 lines, the first "time:1:03;ph:1;tag:Torvek;spellid:98008;"'
    assert note._not_checked(plan, {})[0].startswith("Lines of the note not understood: 5 lines, the first ")
    whole = note.parse(NOTE.read_text(encoding="utf-8"))[0]
    assert note.not_understood(whole) == "" and note._not_checked(whole, {}) == []


def test_saving_a_note_says_what_was_read_and_what_wasnt():
    """The officer who pastes a note hears at once what of it was read: the reminders and the interrupt order
    counted, and the lines not understood with the first of them."""
    pasted = ("EncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
              "time:30;ph:1;tag:Torvek;spellid:98008;\ntime:1:03;ph:1;tag:Torvek;spellid:98008;\n"
              "time:40;ph:1;tag:everyone;spellid:98008;\n\nintstart\nTorvek Mirelle\nintend\n\nOrbs\nred orb: Rowan\n")
    said = server.save_pasted(Notebook(":memory:"), 14169, pasted, "Rowan", at=1000.0)
    assert said.splitlines()[-1] == (
        "- **Vashnik** (Mythic): 1 timed cooldown, 0 callouts, 1 reminder to everyone, an interrupt order of 1 line; "
        'Orbs (red orb); not understood: 1 line, the first "time:1:03;ph:1;tag:Torvek;spellid:98008;"')


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


async def _pull_42(*edits: tuple[str, str]) -> list[str]:
    """The lines the check of Sszorak pull 42 gains when the recorded note is changed (old text, new text), with the
    note's spells as they are so the recorded answers still serve."""
    text = NOTE.read_text(encoding="utf-8")
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    before = (await note.note_check(rep, text, 42, "Sszorak")).splitlines()
    for old, new in edits:
        assert old in text
        text = text.replace(old, new)
    return [line for line in (await note.note_check(rep, text, 42, "Sszorak")).splitlines() if line not in before]


async def test_a_tag_that_is_no_player_is_set_aside_and_never_a_player_who_wasnt_in_the_pull(recorded):
    """A callout to healers or to a group read "not in this pull", with "healer" among those named in the note but
    absent, and a spell line to everyone as a cooldown "everyone" didn't cast. A role or a group is no one the log
    can name: its lines are listed once as not checked. A name with its realm is checked as the name."""
    new = await _pull_42(
        ("time:75;ph:1;tag:everyone;text:Big Personals;", "time:75;ph:1;tag:healer;text:Big Personals;"),
        ("time:199;ph:1;tag:everyone;text:Big Personals;", "time:199;ph:1;tag:group1 group2;text:Big Personals;"),
        ("time:16;ph:1;tag:Miralupa;spellid:64843;", "time:16;ph:1;tag:Miralupa-Kazzak;spellid:64843;"),
        ("time:71;ph:1;tag:Deathadim;spellid:51052;",
         "time:71;ph:1;tag:Deathadim melee;spellid:51052;\ntime:90;ph:1;tag:everyone;spellid:51052;"))
    assert not any("not in this pull" in line and "healer" in line for line in new)
    assert ("Tags that are no player of the report and no name on the note's invite list (a role, a group or a "
            "nickname, which the log can't match to players), their lines not checked: group1 (1 callout), group2 "
            "(1 callout), healer (1 callout), melee (1 cooldown)") in new
    assert any(line.startswith("Reminders to everyone (1 timed line with a spell and no player to press it")
               and line.endswith("not checked: Anti-Magic Zone [51052]") for line in new)
    assert not [line for line in new if "Miralupa" in line and "Divine Hymn" in line]  # checked as before
    assert not [line for line in new if line.startswith("Named in the note")]  # still only Mizaroth


async def test_words_under_a_heading_arent_read_as_players_names(recorded):
    """ "Swap: at 2 stacks, Deathadim first" under "Tanks" gave "Named in the note but not in this pull: 2, at,
    first, in, melee, stacks,, stay". A line is a list of players when at least half its words are players of the
    report or names on the invite list; else it's the note's words, shown with the assignments as written."""
    new = await _pull_42(("Virulence trigger\nTrigger: Martin",
                          "Virulence trigger\nTrigger: Martin\n\nTanks\nSwap: at 2 stacks, Deathadim first\n"
                          "Note: stay in melee"))
    assert new == ["  Tanks: Swap: at 2 stacks, Deathadim first; Note: stay in melee"]


def test_a_note_for_another_difficulty_than_the_pull_says_so():
    """A note is found by its boss alone, so a Mythic note was checked against a Heroic kill of the boss without a
    word under a header naming Mythic."""
    mythic = note.Note(3418, "Mythic", "Vashnik")
    assert note._other_difficulty(mythic, [{"difficulty": 4}]) == (
        " The note is for Mythic and this pull is Heroic: its times and jobs may not fit them.")
    assert note._other_difficulty(mythic, [{"difficulty": 5}, {"difficulty": 5}]) == ""
    assert note._other_difficulty(note.Note(3418, "", "Vashnik"), [{"difficulty": 4}]) == ""
    assert note._other_difficulty(mythic, [{}]) == ""  # the log doesn't say


async def test_the_recorded_mythic_pulls_checked_against_a_heroic_note_say_so_in_the_first_line(recorded):
    night = await note.note_check(await server.lib().report("vjt3KLbN8g62J1H9"),
                                  NOTE.read_text(encoding="utf-8").replace("Difficulty:Mythic", "Difficulty:Heroic"),
                                  boss="Sszorak", all_pulls=True)
    assert night.splitlines()[0].endswith(
        " The note is for Heroic and these pulls are Mythic: its times and jobs may not fit them.")
    [head, *_] = await _pull_42(("Difficulty:Mythic", "Difficulty:Heroic"))
    assert head.startswith("The raid note for Sszorak (Heroic), checked against Sszorak pull 42 (")
    assert head.endswith(" The note is for Heroic and this pull is Mythic: its times and jobs may not fit them.")


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
        return SimpleNamespace(f={"id": i}, until=300_000, planned=note.Planned(uses, [], 0))
    lines = note._night_cooldowns(cast(Any, rep), [check(i) for i in (1, 2, 3)])
    assert ("    3:10 Spell [1714], 3 pulls: not cast 3 (p1, p2, p3); never cast on any of these pulls: a talent may "
            "replace it, or another spell do its job (breakdown of their casts)") in lines
    assert "    0:41 Spell [192077], 3 pulls: not cast 3 (p1, p2, p3)" in lines


async def test_a_check_asked_for_one_player_prints_their_rows_and_counts_the_raids(recorded):
    """A third of the note checks were about one raider ("did I miss my soak?") and came back with twenty players'
    rows to find theirs in. With a player the same check prints their rows, with the raid's as counts."""
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    text = NOTE.read_text(encoding="utf-8")
    whole = await note.note_check(rep, text, 42, "Sszorak")
    own = await note.note_check(rep, text, 42, "Sszorak", player="enfo")
    lines = own.splitlines()
    assert len(own) < len(whole) / 2 and lines[0] == whole.splitlines()[0]
    assert lines[1].startswith("Printed for Enfo alone") and "Printed for" not in whole
    assert "Assignments in the note's text, Enfo's: Mutilate Soaks: Left (10 players); " in lines[2]
    assert "  3:14 Left: 6 of 10 hit, 351.59k each; Enfo: not hit (alive)" in lines
    assert "  0:24 Right: 7 of 9 hit, 370.75k each" in lines  # another group's soak: its count alone
    assert ("Planned cooldowns (time in the note: what happened), Enfo's 6 of the raid's 38 (on time 4, early 10, "
            "late 10, not cast 11, dead 3):") in lines
    assert "  1:12 Enfo Spirit Link Totem [98008]: cast at 1:13, 1s late" in lines
    assert "    Enfo: pressed none (died at 3:21; ready: none)" in lines
    assert lines[-1].endswith("isn't a spell the log can match to them; only callouts for personals are): 6 to other "
                              "players")
    assert not any(name in own for name in ("Zebinomage", "Scopidruid", "Miralupa", "Martin"))
    with pytest.raises(FactError, match="no single player"):
        await note.note_check(rep, text, 42, "Sszorak", player="Nobodyatall")


async def test_the_cooldowns_planned_after_the_wipe_was_decided_are_counted_with_the_next_three(recorded):
    """A wipe decided at 0:56 was followed by 48 lines of the plan it never reached; `upcoming` shows a whole note."""
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    text = NOTE.read_text(encoding="utf-8")
    assert ("  Planned after the wipe was decided at 4:08, so not checked against this pull: 21 lines, the next 4:25 "
            "Dragepie Zephyr [374227], 4:29 Namsus Apotheosis [200183], 4:30 Miralupa Divine Hymn [64843]"
            ) in (await note.note_check(rep, text, 42, "Sszorak")).splitlines()
    own = await note.note_check(rep, text, 42, "Sszorak", player="Zebinomage")  # in no planned line, and no kick
    assert "Planned cooldowns: the note plans none for Zebinomage." in own.splitlines()


def test_a_note_shown_as_it_is_is_cut_to_one_players_part():
    """The newest note for the next raid, asked for one raider ("what's my job tomorrow?"): their labels, their
    timed lines and the callouts to everyone, read from the note as shown."""
    torvek = SimpleNamespace(id=1, name="Torvek")
    rep = cast(Any, SimpleNamespace(player=lambda name: torvek, ability_ref=lambda spell: f"Spell [{spell}]",
                                    find_player=lambda name: torvek if name == "Torvek" else None))
    head = "For the next raid, so here is the newest raid note the guild saved for Vashnik, as it is:"
    shown = (f"{head}\nEncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
             "time:30;ph:2;tag:Torvek;spellid:98008 (Spirit Link Totem [98008]);\n"
             "time:45;ph:1;tag:Mirelle;spellid:64843 (Divine Hymn [64843]);\n"
             "time:75;ph:1;tag:everyone;text:Big Personals;\n"
             "time:80;ph:1;tag:Torvek;text:Grab the orb;\n\n"
             "Soaks\nLeft: Torvek Mirelle\nRight: Brightleaf\n")
    first, *rest = note.own_part(rep, shown, "torvek").splitlines()
    assert first.startswith(head.removesuffix(":") + ". Of it, Torvek's part (")
    assert rest == ["Assignments in the note's text, Torvek's: Soaks: Left (2 players)",
                    "Planned cooldowns: ph2 0:30 Spell [98008]",
                    'Callouts to them: 1:20 "Grab the orb"',
                    'Callouts to everyone: 1:15 "Big Personals"']
    none = "For the next raid, and the guild has no raid note saved for Vashnik."
    assert note.own_part(rep, none, "torvek") == none


async def test_a_check_asked_for_a_player_who_wasnt_in_the_pull_says_so(recorded):
    """One of the report's players who sat a pull out, or every pull on the boss, got a check that read as "no
    jobs, none missed": no assignments, no planned cooldowns, no row, and not a word that they weren't there."""
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    text = NOTE.read_text(encoding="utf-8")
    with pytest.raises(FactError, match="Sveini was not in Sszorak pull 42; in the report: Sszorak pulls 1-"):
        await note.note_check(rep, text, 42, "Sszorak", player="Sveini")
    fangs = await server.lib().report("fvkH4hGZwDRVnyXJ")
    plan = (TOOLS.parent / "notes" / "twin-fangs-2026-10-19.txt").read_text(encoding="utf-8")
    with pytest.raises(FactError, match="Miralupa was not in any pull on The Twin Fangs in this report; in the "
                                        "report: Sszorak"):
        await note.note_check(fangs, plan, boss="Twin Fangs", all_pulls=True, player="Miralupa")


async def test_one_players_check_leaves_out_another_players_use_written_twice(recorded):
    """Printed for one player, a pull still said of another's cooldown that the note plans it twice."""
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    spiral = "time:237;ph:1;tag:Dragepie;spellid:374968;"
    text = NOTE.read_text(encoding="utf-8").replace(spiral, spiral + "\n" + spiral.replace("237", "238"))
    twice = "  The note plans Dragepie's Time Spiral [374968] at both 3:57 and 3:58: one use written twice, checked as"
    assert twice in await note.note_check(rep, text, 42, "Sszorak")
    assert twice in await note.note_check(rep, text, 42, "Sszorak", player="Dragepie")
    assert "Dragepie" not in await note.note_check(rep, text, 42, "Sszorak", player="Enfo")


async def test_a_night_asked_for_one_player_says_how_many_of_the_pulls_they_were_in(recorded):
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    night = await note.note_check(rep, NOTE.read_text(encoding="utf-8"), boss="Sszorak", all_pulls=True,
                                  player="Sveini")
    assert night.splitlines()[1].startswith("Printed for Sveini alone, who was in 28 of the 42 pulls: their rows ")


def test_a_note_for_the_next_raid_cut_to_one_player_keeps_what_everyone_is_told_and_says_what_wasnt_read():
    """One raider's part of the note as it is left out the reminders to everyone ("Go to diamond") and, without a
    word, every line the reading couldn't take, their own among them; and a time with no phase after a ph:3 one
    read as that phase's. The lines are in the order of the fight, the reminders are listed, and the lines not
    understood are counted, theirs shown as written."""
    torvek = SimpleNamespace(id=1, name="Torvek")
    rep = cast(Any, SimpleNamespace(ability_ref=lambda spell: f"Spell [{spell}]",
                                    find_player=lambda name: torvek if name.lower() == "torvek" else None))
    shown = ("For the next raid, so here is the newest raid note the guild saved for Vashnik, as it is:\n"
             "EncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
             "time:48;ph:3;tag:Torvek;text:Grab the orb;\n"
             "time:132;ph:1;tag:Torvek;text:Soak;\n"
             "time:24;ph:2.5;tag:Torvek;spellid:98008 (Spirit Link Totem [98008]);\n"
             "time:6;ph:2.5;tag:Mirelle;spellid:64843 (Divine Hymn [64843]);\n"
             "time:7;ph:2;tag:everyone;spellid:1285643 (Dreadmarch [1285643]);text:Go to diamond;\n")
    assert note.own_part(rep, shown, "torvek").splitlines()[1:] == [
        'Callouts to them: 2:12 "Soak", ph3 0:48 "Grab the orb"',
        'Reminders to everyone (the boss\'s cast or a move, shown with an icon): ph2 0:07 "Go to diamond"',
        "Lines of the note not understood, so in none of the lists above: 2 lines; those tagged Torvek, as written: "
        '"time:24;ph:2.5;tag:Torvek;spellid:98008 (Spirit Link Totem [98008]);"']
    halvard = SimpleNamespace(id=2, name="Halvard")  # one of the report's players the note has no line for
    rep.find_player = lambda name: halvard if name.lower() == "halvard" else None
    assert note.own_part(rep, shown, "Halvard").splitlines()[1:] == [
        "The note gives Halvard no line of their own.",
        'Reminders to everyone (the boss\'s cast or a move, shown with an icon): ph2 0:07 "Go to diamond"',
        "Lines of the note not understood, so in none of the lists above: 2 lines, none of them tagged Halvard"]


def test_a_note_for_the_next_raid_is_cut_for_a_raider_the_report_doesnt_have():
    """The newest note names who comes next time, the latest report who came last: a raider who missed that night
    asked for their jobs and got "no single player matching" them in the report."""
    rep = cast(Any, SimpleNamespace(ability_ref=lambda spell: f"Spell [{spell}]", find_player=lambda name: None))
    shown = ("For the next raid, so here is the newest raid note the guild saved for Vashnik, as it is:\n"
             "EncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
             "time:30;ph:1;tag:Mírelle;spellid:64843 (Divine Hymn [64843]);\n\n"
             "Soaks\nLeft: Torvek Mírelle\n")
    first, *rest = note.own_part(rep, shown, "mirelle").splitlines()
    assert "Of it, Mírelle's part (" in first
    assert rest == ["Assignments in the note's text, Mírelle's: Soaks: Left (2 players)",
                    "Planned cooldowns: 0:30 Spell [64843]"]
    with pytest.raises(FactError, match="no single player matching 'Nobodyatall' in this report, and the note "
                                        "names no one so"):
        note.own_part(rep, shown, "Nobodyatall")


async def test_the_note_the_tool_shows_for_the_next_raid_is_the_one_cut_to_a_players_part():
    """The cut reads the note back from the text the tool shows (its spells named): the two must fit."""
    book = Notebook(":memory:")
    book.save_note(14169, 3418, "Vashnik", "EncounterID:3418;Difficulty:Mythic;Name:Vashnik\n"
                   "time:41;ph:1;tag:Torvek;spellid:192077;bossSpell:1285643;\n"
                   "time:50;ph:1;tag:Mirelle;spellid:64843;\n", "Rowan", at=1000.0)
    torvek = SimpleNamespace(id=1, name="Torvek")

    async def name_spells(ids):
        pass
    rep = cast(Any, SimpleNamespace(guild_name="The Copper Lanterns", name_spells=name_spells,
                                    ability_ref=lambda spell: f"Wind Rush Totem (Rank 2) [{spell}]",
                                    find_player=lambda name: torvek if name.lower() == "torvek" else None))
    shown = await server.unpulled_note(book, 14169, rep, "Vashnik", upcoming=True)
    assert "spellid:192077 (Wind Rush Totem (Rank 2) [192077]);bossSpell:1285643 (Wind Rush" in shown
    assert note.own_part(rep, shown, "Torvek").splitlines()[1:] == [
        "Planned cooldowns: 0:41 Wind Rush Totem (Rank 2) [192077] for Wind Rush Totem (Rank 2) [1285643]"]
