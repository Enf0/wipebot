"""A planned cooldown that is for someone (report/note.py): the line's own words, where the note says who an
external is for, and who the matched cast was cast on, where the log gives the cast a player. On a made-up pull and on
the recorded nights whose notes plan externals."""
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note

SPIRIT, HYMN = 47788, 64843
PRIEST, TANK = 5, 9


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def test_a_spell_lines_text_is_kept_with_the_cooldown():
    """`time:22;tag:Mirelle;spellid:47788;text:Guardian Spirit Stonewall;`: the spell became a planned cooldown and
    the text, the only place the note says who it is for, was dropped."""
    [plan] = note.parse("EncounterID:3429;Name:Coiled Altar\n"
                        "time:22;ph:1;tag:Mirelle;spellid:47788;text:Guardian Spirit Stonewall;\n"
                        "time:44;ph:1;tag:Mirelle;spellid:64843;\n")
    assert [c.text for c in plan.cooldowns] == ["Guardian Spirit Stonewall", ""]
    assert plan.cooldowns[1] == note.Cooldown(44, 1, "Mirelle", HYMN)


async def test_a_matched_cast_says_who_it_was_cast_on_where_the_log_gives_it_a_player(monkeypatch):
    """A cast of an external carries its target in the log (Guardian Spirit, Blessing of Sacrifice and Time Dilation
    on the recorded nights); a raid cooldown's target is -1. The planned use carries the player the matched cast
    was on, "themselves" when that is the caster, and its row prints it beside the note's own words."""
    plan = note.Note(3429, "Mythic", "Coiled Altar", cooldowns=[
        note.Cooldown(22, 1, "Mirelle", SPIRIT, text="Guardian Spirit Stonewall"),
        note.Cooldown(44, 1, "Mirelle", HYMN),
        *(note.Cooldown(at, 1, "Mirelle", SPIRIT, text="Guardian Spirit Stonewall") for at in (200, 400))])
    names = {SPIRIT: "Guardian Spirit", HYMN: "Divine Hymn"}
    rep = cast(Any, SimpleNamespace(
        ids_named=lambda words: set(), ability=names.get, was_in=lambda f, p: True, actor={TANK: "Stonewall"}.get,
        ability_ref=lambda spell: f"{names[spell]} [{spell}]", names={PRIEST: "Mirelle", TANK: "Stonewall"}))

    async def casts(rep: Any, f: dict, n: note.Note) -> list[dict]:
        return [{"type": "cast", "sourceID": PRIEST, "abilityGameID": SPIRIT, "timestamp": 19_000, "targetID": TANK},
                {"type": "cast", "sourceID": PRIEST, "abilityGameID": HYMN, "timestamp": 44_500, "targetID": -1},
                {"type": "cast", "sourceID": PRIEST, "abilityGameID": SPIRIT, "timestamp": 203_000, "targetID": PRIEST}]
    monkeypatch.setattr(note, "_planned_casts", casts)
    planned = await note._cooldowns(rep, {"startTime": 0}, plan, cast(Any, {"Mirelle": SimpleNamespace(id=PRIEST)}),
                                    lambda name, at: "alive" if at < 300_000 else "dead since 5:00",
                                    lambda c: c.at * 1000, 600_000)
    assert planned is not None
    assert [(u.cast_at, u.on) for u in planned.uses] == [(19_000, "Stonewall"), (44_500, ""), (203_000, "themselves"),
                                                         (None, "")]
    assert note._planned_line(rep, 22_000, plan.cooldowns[0]) == (
        '0:22 Mirelle Guardian Spirit [47788] "Guardian Spirit Stonewall"')
    assert note._planned_line(rep, 44_000, plan.cooldowns[1]) == "0:44 Mirelle Divine Hymn [64843]"
    check = cast(Any, SimpleNamespace(f={"id": 1}, until=600_000, planned=planned))
    assert note._night_cooldowns(rep, [check])[2:] == [
        '    0:22 Guardian Spirit [47788] "Guardian Spirit Stonewall", 1 pull: early 1 (3 s); cast on Stonewall 1',
        "    0:44 Divine Hymn [64843], 1 pull: on time 1",
        '    3:20 Guardian Spirit [47788] "Guardian Spirit Stonewall", 1 pull: late 1 (3 s); cast on themselves 1',
        '    6:40 Guardian Spirit [47788] "Guardian Spirit Stonewall", 1 pull: dead 1']


async def test_the_recorded_externals_are_printed_with_the_notes_words_and_the_casts_target(recorded):
    """Pull 28 of fk2z6bCw4xRLNvht: the note has "Guardian Spirit Wutroll" at 0:22 and the priest's cast at 0:19 was
    on Wutroll. On p7jRwgv8b1rY6LBd the note has "Time Dilation on Wutroll" at 1:10, and on pull 21 the evoker's
    cast at 1:10 was on themselves, the one of the night's 81 matched externals not on Wutroll."""
    text = (TOOLS.parent / "notes" / "coiled-altar-2026-11-02.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("fk2z6bCw4xRLNvht")
    rows = [row.strip() for row in (await note.note_check(rep, text, 28, "Coiled Altar")).splitlines()]
    assert '0:22 Miralupa Guardian Spirit [47788] "Guardian Spirit Wutroll": cast at 0:19 on Wutroll, 3 s early' in rows
    assert ('3:35 Lïzårðmâñ Time Dilation [357170] "Time Dilation Wutroll": not cast within 30 s (alive; cast at '
            "5:05)") in rows
    assert "0:44 Miralupa Divine Hymn [64843]: cast at 0:44, on time" in rows  # no words, and no target in the log
    text = (TOOLS.parent / "notes" / "twin-fangs-2026-10-21.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("p7jRwgv8b1rY6LBd")
    night = await note.check_night(rep, text, "Twin Fangs")
    on = [(rep.pull_no(c.f), u.cooldown.text, u.on) for c in night.checks if c.planned for u in c.planned.uses if u.on]
    assert len(on) == 81 and {words for _, words, _ in on} == {"Time Dilation on Wutroll", "Sac Wutroll"}
    assert [(pull, who) for pull, _, who in on if who != "Wutroll"] == [(21, "themselves")]
