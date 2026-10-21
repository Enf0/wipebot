"""Reading a raid note (report/note.py; NSRT's format). How it's checked against real pulls is pinned by the tool
cases (test_tools.py)."""
from collections import Counter

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
