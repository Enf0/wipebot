"""A raid note's phases laid over the log's (report/note.py, `Phases`): which phase of the log a note's `ph` means,
where its lines start and where they stop. On made-up pulls, on the phase lists and transitions Warcraft Logs gave
the recorded reports, and on the recorded Coiled Altar pulls whose outputs the tool cases pin (test_tools.py)."""
from collections import Counter
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support.recorded import TOOLS, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import note
from wipebot.core.report.note import ENDED, NEVER, UNKNOWN, Callout, Cooldown, lay_phases

# Warcraft Logs' phases of three bosses under the labels every tool prints (pulls.phase_labels), and pulls as it
# logged them, in ms: the Coiled Altar on BfgjDv6LC2HaVbFm, Nek'zali on the same report, Sszorak on vjt3KLbN8g62J1H9
ALTAR = {1: "P1", 2: "P2", 3: "I1", 4: "P3"}
ALTAR_PULL = [(1, 0), (2, 103_000), (3, 194_000), (4, 232_000)]
NEKZALI = {1: "P1", 2: "I1", 3: "P2"}
NEKZALI_PULL = [(1, 0), (2, 172_000), (3, 233_000)]
SSZORAK = {1: "P1", 2: "P2"}
SSZORAK_PULL = [(1, 0), (2, 100_000), (1, 125_000)]


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


def line(phase: float, at: float) -> Cooldown:
    return Cooldown(at, phase, "Torvek", 98008)


def test_a_phase_is_a_number_as_the_addon_reads_it():
    """`ph:2.5` (the Coiled Altar's intermission in the guild's note) was no whole number, so its ten lines were
    dropped as not understood and never checked. A phase is a number; a whole one stays whole, so what is printed
    and drawn of it reads 2, not 2.0."""
    [plan] = note.parse("EncounterID:3429;Name:Coiled Altar\n"
                        "time:6;ph:2.5;tag:Torvek;spellid:98008;\n"
                        "time:16;ph:3;tag:Torvek;spellid:98008;\n"
                        "time:5;ph:2.5;tag:Mirelle;text:Soak Ghost;\n"
                        "time:9;tag:Torvek;spellid:98008;\n"
                        "time:7;ph:inf;tag:Torvek;spellid:98008;\n")
    assert plan.cooldowns == [Cooldown(6, 2.5, "Torvek", 98008), Cooldown(16, 3, "Torvek", 98008),
                              Cooldown(9, 1, "Torvek", 98008)]
    assert [type(c.phase) for c in plan.cooldowns] == [float, int, int]
    assert plan.callouts == [Callout(5, 2.5, ("Mirelle",), "Soak Ghost")]
    assert plan.unread == ["time:7;ph:inf;tag:Torvek;spellid:98008;"]


def test_a_notes_phase_is_the_logs_phase_of_that_label_not_of_that_id():
    """Warcraft Logs numbers the Coiled Altar's phases 1, 2, 3 (the intermission), 4 (Stage Three), and the note
    counts 1, 2, 2.5, 3. Read as Warcraft Logs' ids, `ph:3 time:22` lay 22 s into the intermission (3:36 on this
    pull) and the personals callout was judged where nobody was asked to press. By the labels it is 22 s into P3."""
    ph = lay_phases([1, 2, 2.5, 3], ALTAR, ALTAR_PULL)
    assert ph.starts == {1: 0, 2: 103_000, 2.5: 194_000, 3: 232_000}
    assert ph.labels == {2: "P2", 2.5: "I1", 3: "P3"} and not ph.unknown
    assert ph.when(line(3, 22)) == 254_000  # 4:14, not 3:36
    assert ph.when(line(2.5, 6)) == 200_000
    assert ph.said(line(3, 22)) == "P3 0:22" and ph.said(line(1, 107)) == "1:47"


def test_on_a_boss_whose_intermission_comes_second_the_notes_second_stage_is_not_it():
    """Nek'zali's phases are Stage One, the intermission and Stage Two in Warcraft Logs (ids 1, 2, 3): a note's
    `ph:2` was timed from the intermission. `ph:1.5` is the intermission; `ph:1.75`, a number the addon has for this
    boss and the log has no phase for, is not laid anywhere."""
    ph = lay_phases([1, 1.5, 1.75, 2], NEKZALI, NEKZALI_PULL)
    assert ph.starts == {1: 0, 1.5: 172_000, 2: 233_000}
    assert ph.unknown == {1.75: "the log's phases of this boss are P1, I1, P2"}
    assert ph.fate(line(1.75, 5)) == UNKNOWN and ph.when(line(1.75, 5)) is None
    assert ph.when(line(2, 10)) == 243_000


def test_a_phase_the_pull_never_reached_is_neither_timed_nor_in_doubt():
    ph = lay_phases([1, 2, 2.5, 3], ALTAR, ALTAR_PULL[:2])
    assert ph.starts == {1: 0, 2: 103_000} and ph.labels == {2: "P2", 2.5: "I1", 3: "P3"}
    assert ph.fate(line(3, 22)) == NEVER and ph.fate(line(2.5, 6)) == NEVER and not ph.unknown


def test_a_phases_lines_stop_when_the_notes_next_phase_begins():
    """The addon drops a phase's pending lines at the change of phase. The Coiled Altar note plans the push to
    Stage Two at 2:47 with five raid cooldowns; on a pull pushed at 2:27 they were pressed as the phase began and
    read "19 s early", and three others "not cast"."""
    ph = lay_phases([1, 2], ALTAR, [(1, 0), (2, 147_000)])
    assert ph.ends == {1: (147_000, "P2")}
    assert ph.fate(line(1, 146)) == "" and ph.when(line(1, 146)) == 146_000
    assert ph.fate(line(1, 167)) == ENDED and ph.when(line(1, 167)) is None
    assert ph.when(line(2, 2)) == 149_000  # the same cooldowns' lines in ph:2 are the ones checked


def test_a_note_with_only_its_first_phase_runs_on_whatever_the_log_does():
    """Sszorak's log goes P1, P2, P1 while the addon never leaves phase 1 there, and the guild's note has only ph:1
    lines: they are checked against the clock to the pull's end. Only a phase the note itself has lines for ends
    the one before it."""
    ph = lay_phases([1], SSZORAK, SSZORAK_PULL, in_order=False)
    assert ph.starts == {1: 0} and not ph.ends and not ph.unknown
    assert ph.when(line(1, 199)) == 199_000
    assert not lay_phases([1], ALTAR, ALTAR_PULL).ends  # nor on a boss whose phases go in order


def test_where_the_log_comes_back_to_a_phase_a_later_phase_of_the_note_is_not_timed_by_a_guess():
    """On Sszorak, the Entombed Sentinels and the Lost Explorers the log goes back to a phase, and the addon counts
    such a fight its own way: which phase of the log a note's `ph:2` is can't be told from Warcraft Logs' list. Its
    lines are not checked, and neither are the first phase's after the log's phase changed: the note's second may
    have begun then."""
    ph = lay_phases([1, 2], SSZORAK, SSZORAK_PULL, in_order=False)
    assert ph.starts == {1: 0} and list(ph.unknown) == [2] and "go back to a phase" in ph.unknown[2]
    assert ph.fate(line(2, 10)) == UNKNOWN
    assert ph.fate(line(1, 90)) == "" and ph.fate(line(1, 110)) == UNKNOWN  # the log changed phase at 1:40
    assert ph.ends == {1: (100_000, "")}
    # a boss the log has no phases for: nothing says where a second phase would be
    none = lay_phases([1, 2], {}, [])
    assert none.unknown == {2: "the log has no phases for this boss"} and none.when(line(1, 500)) == 500_000


def test_whether_a_boss_goes_through_its_phases_in_order_is_read_from_its_pulls_in_the_report():
    """One pull that wiped in Sszorak's first Howling Maelstrom looks like a boss with two phases in a row; another
    pull of the night shows the log going back. The boss's pulls in the report decide, so a night counts alike."""
    def pull(*ids: int) -> dict:
        return {"encounterID": 3420, "startTime": 0, "phaseTransitions": [{"id": i, "startTime": 100_000 * k}
                                                                          for k, i in enumerate(ids)]}
    plan = note.Note(3420, "Mythic", "Sszorak", cooldowns=[line(1, 5), line(2, 5)])
    short, back = pull(1, 2), pull(1, 2, 1)
    labels = {(3420, i): label for i, label in SSZORAK.items()}

    def rep(*pulls: dict) -> Any:
        return cast(Any, SimpleNamespace(phase_labels=labels, boss_key=lambda f: "Sszorak",
                                         by_boss={"Sszorak": list(pulls)}))
    assert note._phases(rep(short), short, plan).starts == {1: 0, 2: 100_000}
    assert note._phases(rep(short, back), short, plan).unknown.keys() == {2}
    skipped = pull(1, 4)  # the Lost Explorers go from their first phase to Warcraft Logs' fourth
    assert note._phases(rep(skipped), skipped, replace_phase(plan, 4)).unknown.keys() == {4}


def replace_phase(plan: note.Note, phase: float) -> note.Note:
    return note.Note(plan.encounter_id, plan.difficulty, plan.boss, cooldowns=[line(1, 5), line(phase, 5)])


def _check(phases: note.Phases, lines: Sequence[Cooldown | Callout], pull: int = 1) -> Any:
    """A pull's check as the first line reads it: the note's phases in it, and the lines not checked for theirs."""
    off = Counter((c.phase, phases.fate(c), type(c)) for c in lines if phases.fate(c))
    return cast(Any, SimpleNamespace(phases=phases, off=off, f={"id": pull}))


def test_a_pulls_first_line_says_where_each_phase_lies_and_what_is_not_checked_for_its_phase():
    lines = [line(1, 100), line(1, 150), line(1, 167), Callout(160, 1, ("Torvek",), "Soak"), line(2, 5),
             line(2, 300), line(2.5, 6), line(3, 22)]
    said = note._phases_said(_check(lay_phases([1, 2, 2.5, 3], ALTAR, [(1, 0), (2, 147_000), (3, 368_000)]), lines))
    assert said == (
        "Note times count from the pull's start (ph:1) or from the start of their phase: ph:2 is P2, from 2:27; "
        "ph:2.5 is I1, from 6:08; ph:3 is P3, not reached. A phase's lines stop when the note's next phase begins, "
        "as the addon drops them, so those timed later aren't checked: 2 planned cooldowns and 1 callout of ph:1 "
        "(P2 began at 2:27), 1 planned cooldown of ph:2 (I1 began at 6:08).")
    # a note with only ph:1 lines keeps the sentence it had
    assert note._phases_said(_check(lay_phases([1], SSZORAK, SSZORAK_PULL, False), [line(1, 150)])) == (
        "Note times count from the pull's start (ph:1) or from the phase's first start.")
    doubt = note._phases_said(_check(lay_phases([1, 2], SSZORAK, SSZORAK_PULL, False),
                                     [line(1, 90), line(1, 110), line(2, 5), line(2, 9)]))
    assert doubt == (
        "Note times count from the pull's start (ph:1). 2 planned cooldowns in ph:2 not checked: can't tell which "
        "phase of the log that is (this boss's pulls go back to a phase or skip one, so the log doesn't count as the "
        "note). 1 planned cooldown of ph:1 timed after the log's phase changed at 1:40 not checked: a phase of the "
        "note that can't be placed may have begun then.")


def test_a_nights_first_line_counts_them_over_the_pulls_and_names_a_later_phases_times_by_its_label():
    lines = [line(1, 150), line(1, 167), line(2, 5), line(2.5, 6)]
    checks = [_check(lay_phases([1, 2, 2.5], ALTAR, [(1, 0), (2, start)]), lines, i)
              for i, start in enumerate((147_000, 160_000, 200_000))]
    assert note._night_phases(checks) == (
        " The note's ph:2 is the log's P2, ph:2.5 its I1: a time in one counts from its start on each pull (\"P2 "
        "0:30\" is 30 s into P2). A phase's lines stop when the note's next phase begins, as the addon drops them, "
        "so those timed later aren't checked: 3 planned cooldowns of ph:1 over 2 pulls.")
    assert note._night_phases([_check(lay_phases([1], SSZORAK, SSZORAK_PULL, False), [line(1, 150)])]) == ""
    # a callout's times over the pulls: as the note has them, a phase's label once
    ph = checks[0].phases
    times = [(ph, Callout(at, phase, (), "Personals")) for phase, at in ((2, 45), (1, 107), (2, 9), (2, 45), (2.5, 5))]
    assert note._times_said(times) == "1:47, P2 0:09, 0:45, I1 0:05"


async def test_the_recorded_pull_into_stage_three_is_timed_by_the_logs_phases(recorded):
    """Pull 28 of fk2z6bCw4xRLNvht with the guild's note: Stage Two began at 2:27, the intermission at 6:08, Stage
    Three at 6:46, and the wipe was decided at 7:03. The nine cooldowns the note has in ph:1 after 2:27 are not
    rows, the seven of ph:2.5 are (from 6:08), and ph:3 counts from 6:46: one planned cooldown before the wipe was
    decided, and the "Personals" at 36 s past the pull's end (it was judged at 6:44, in the intermission)."""
    text = (TOOLS.parent / "notes" / "coiled-altar-2026-11-02.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("fk2z6bCw4xRLNvht")
    f = rep.fight(28, "Coiled Altar")
    check = await note._check(rep, f, note.parse(text)[0])
    assert check.phases.starts == {1: 0, 2: 147_634, 2.5: 368_662, 3: 406_392}
    assert check.planned
    uses = {(u.cooldown.phase, u.cooldown.at, u.cooldown.player): u for u in check.planned.uses}
    assert not any(phase == 1 and at * 1000 >= 147_634 for phase, at, _ in uses)
    assert sum(phase == 2.5 for phase, _, _ in uses) == 7 and uses[(2.5, 6, "Miralupa")].at == 374_662
    assert [(at, who, u.at) for (phase, at, who), u in uses.items() if phase == 3] == [(16, "Cptola", 422_392)]
    assert check.off == {(1, ENDED, Cooldown): 9, (2, ENDED, Cooldown): 2}
    assert [k.at for k in check.callouts] == [107_000]  # the one "Personals" the pull reached, at 1:47
    assert not check.note.unread
    out = await note.note_check(rep, text, 28, "Coiled Altar")
    assert out.splitlines()[0].endswith(
        "Note times count from the pull's start (ph:1) or from the start of their phase: ph:2 is P2, from 2:27; "
        "ph:2.5 is I1, from 6:08; ph:3 is P3, from 6:46. A phase's lines stop when the note's next phase begins, "
        "as the addon drops them, so those timed later aren't checked: 9 planned cooldowns of ph:1 (P2 began at "
        "2:27), 2 planned cooldowns of ph:2 (I1 began at 6:08).")
    rows = [row.strip() for row in out.splitlines()]
    # the push to Stage Two was planned at 2:47 in ph:1 and came at 2:27: those lines read "19 s early" and "not cast"
    assert not [row for row in rows if row.startswith("2:47 ")]
    assert "2:29 Enfo Spirit Link Totem [98008] for Dreadful Presence [1288635]: cast at 2:29, on time" in rows
    assert "6:32 Enfo Spirit Link Totem [98008]: cast at 6:33, 1 s late" in rows  # ph:2.5 at 0:24
    assert [row for row in rows if row.startswith("7:02 Cptola Avenging Wrath [31884]: not cast within 30 s (dead")]
    assert [row[:14] for row in rows if '"Personals"' in row] == ["1:47 everyone:"]
    assert "not understood" not in out


def test_phases_that_cant_be_placed_for_one_reason_say_why_once():
    """A note with lines in three later phases on a boss whose pulls go back to a phase (the Entombed Sentinels' note
    counts up at every change): the first line gave the same reason three times, once per phase. One sentence names
    the phases; a phase that can't be placed for another reason keeps its own."""
    lines = [line(1, 90), line(2, 5), line(2, 9), Callout(7, 2.5, ("Torvek",), "Soak"), line(3, 22)]
    back = "this boss's pulls go back to a phase or skip one, so the log doesn't count as the note"
    again = [(1, 0), (2, 100_000), (3, 150_000), (2, 180_000), (3, 250_000), (4, 300_000)]
    checks = [_check(lay_phases([1, 2, 2.5, 3], ALTAR, again, in_order=False), lines, i) for i in (1, 2)]
    assert note._phases_said(checks[0]) == (
        "Note times count from the pull's start (ph:1). 2 planned cooldowns in ph:2, 1 callout in ph:2.5, 1 planned "
        f"cooldown in ph:3 not checked: can't tell which phase of the log each is ({back}).")
    assert note._night_phases(checks) == (
        " 4 planned cooldowns in ph:2 over 2 pulls, 2 callouts in ph:2.5 over 2 pulls, 2 planned cooldowns in ph:3 "
        f"over 2 pulls not checked: can't tell which phase of the log each is ({back}).")
    mixed = _check(lay_phases([1, 2, 3], SSZORAK, SSZORAK_PULL, in_order=False), lines)
    assert note._phases_said(mixed) == (
        "Note times count from the pull's start (ph:1). 2 planned cooldowns in ph:2 not checked: can't tell which "
        f"phase of the log that is ({back}). 1 planned cooldown in ph:3 not checked: can't tell which phase of the "
        "log that is (the log's phases of this boss are P1, P2).")


def test_a_soak_hit_isnt_outside_the_notes_soak_times_where_its_soak_callouts_cant_be_placed():
    """A note that calls a group to soak in ph:1 and again in ph:2, on a boss whose log goes back to a phase: the
    second callout can't be laid over the log, so a hit after the log's first change of phase read "outside the
    note's soak times (no group called within 20 s of them)", which the log can't say of this note. Such a hit
    still counts for no one, for the reason that is true; a hit before the change, where every callout is placed,
    and a section the note times only in ph:1 keep "outside"."""
    group, others = ["Torvek", "Mirelle", "Halvard"], ["Rowan", "Brightleaf", "Stonewall"]
    plan = note.Note(3420, "Mythic", "Sszorak", groups={"Soaks": {"Left": group}, "Orbs": {"Carriers": others}},
                     callouts=[Callout(10, 1, tuple(group), "Soak"), Callout(5, 2, tuple(group), "Soak"),
                               Callout(20, 1, tuple(others), "Grab orbs")])

    def hits(*at: float) -> list[note.SoakHit]:
        return [note.SoakHit(ms, "", [], set(group), 100_000, [], [], note.OUTSIDE) for ms in at]
    given = note.SoakHit(10_500, "Left", group, set(group), 100_000, [], [])
    soaks = [note.Soaks("Soaks", {"Left": group}, 1285999, [given, *hits(50_000, 130_000)]),
             note.Soaks("Orbs", {"Carriers": others}, 1285999, hits(130_000))]
    phases = lay_phases([1, 2], SSZORAK, SSZORAK_PULL, in_order=False)  # the log's first change is at 1:40
    assert [[h.why for h in s.hits] for s in note._unplaced(soaks, plan, phases)] == [
        ["", note.OUTSIDE, note.UNPLACED], [note.OUTSIDE]]
    assert note.UNPLACED == "can't tell if the note calls a soak then"
    # where the note's phases are all placed, a hit far from every callout is outside its soak times
    placed = [note.Soaks("Soaks", {"Left": group}, 1285999, hits(130_000))]
    assert note._unplaced(placed, plan, lay_phases([1, 2], ALTAR, ALTAR_PULL))[0].hits[0].why == note.OUTSIDE
    # and such hits aren't among those "in the note's soak times" that say whether a section soaks at all
    assert note._no_soaks([*hits(1_000, 2_000), *note._unplaced(
        [note.Soaks("Soaks", {"Left": group}, 1285999, hits(130_000, 140_000, 150_000))], plan, phases)[0].hits]) == ""


async def test_on_the_recorded_sszorak_pull_a_note_with_a_second_phase_is_counted_not_checked(recorded):
    """Pull 42 of vjt3KLbN8g62J1H9 (the log goes P1, P2, P1, P2, P1) with the guild's note, its lines from 2:00 on
    written as ph:2: none of them is timed, the ph:1 lines stop being checked where the log first changed phase
    (1:40), and the four soaks after it, whose callouts are among the lines that can't be placed, count for no one
    without being called outside the note's soak times."""
    text = (TOOLS.parent / "notes" / "sszorak-2026-10-12.txt").read_text(encoding="utf-8")
    two = "\n".join(row.replace("ph:1;", "ph:2;") if row.startswith("time:") and float(row[5:row.index(";")]) >= 120
                    else row for row in text.splitlines())
    rep = await server.lib().report("vjt3KLbN8g62J1H9")
    out = await note.note_check(rep, two, 42, "Sszorak")
    assert out.splitlines()[0].endswith(
        "Note times count from the pull's start (ph:1). 37 planned cooldowns and 20 callouts in ph:2 not checked: "
        "can't tell which phase of the log that is (this boss's pulls go back to a phase or skip one, so the log "
        "doesn't count as the note). 1 planned cooldown of ph:1 timed after the log's phase changed at 1:40 not "
        "checked: a phase of the note that can't be placed may have begun then.")
    rows = [row.strip() for row in out.splitlines()]
    planned = [row for row in rows if "]: cast at " in row or "]: not cast within" in row]
    assert len(planned) == 21 and planned[-1].startswith("1:40 Scopidruid Stampeding Roar")
    assert [row[:47] for row in rows if "counted for no one" in row] == [
        f"{at} can't tell if the note calls a soak then: " for at in ("2:23", "2:31", "3:06", "3:14")]
    assert "outside the note's soak times" not in out


def test_one_raiders_part_of_the_next_raids_note_names_an_intermissions_lines():
    torvek = SimpleNamespace(id=1, name="Torvek")
    rep = cast(Any, SimpleNamespace(player=lambda name: torvek, ability_ref=lambda spell: f"Spell [{spell}]",
                                    find_player=lambda name: torvek if name == "Torvek" else None))
    shown = ("For the next raid, the newest note:\nEncounterID:3429;Name:Coiled Altar\n"
             "time:6;ph:2.5;tag:Torvek;spellid:98008;\ntime:16;ph:3;tag:Torvek;spellid:98008;\n")
    assert note.own_part(rep, shown, "torvek").splitlines()[1:] == [
        "Planned cooldowns: ph2.5 0:06 Spell [98008], ph3 0:16 Spell [98008]"]
