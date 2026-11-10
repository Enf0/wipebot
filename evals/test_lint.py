"""The lint's reading of the numbers in a case and in the tools' output (evals/lint.py)."""
from evals.lint import in_output, numbers, recorded_calls, stale


def test_it_reads_times_amounts_percentages_and_counts_as_written():
    assert numbers("Imbibe at 1:48; Crimson Vial heals 164k of 67.33k overkill, 34.1% left, 16 of 20 and 2.5M") == [
        "1:48", "164k", "67.33k", "2.5M", "34.1%", "16", "20"]


def test_a_number_matches_the_output_as_rounded_and_a_time_exactly():
    out = "1:48 boss casts Imbibe\nheals 163.64k\nwipe at **34.1%**\n4:10 health 38/24\nfirst third 68.9"
    assert all(in_output(n, out) for n in ("1:48", "164k", "34%", "38%", "24%", "69%"))
    assert not in_output("1:47", out) and not in_output("150k", out) and not in_output("50%", "1:07 50 · 1:13 55")


def test_a_stale_number_is_flagged_unless_the_case_derives_it():
    case = {"expect": "Imbibe at 1:47", "facts": ["Divine Hymn 9 s before it"], "derived": ["9"]}
    assert stale(case, "1:48 boss casts Imbibe") == [("expect", "1:47")]


def test_the_calls_replayed_are_each_runs_that_worked_once_and_none_the_bot_fills():
    call = {"name": "pull_timeline", "args": {"pull": 19}, "failed": False}
    runs = [{"cases": {"x": [{"calls": [call, call, {**call, "failed": True, "args": {}},
                                        {"name": "raid_note", "args": {"pull": 19}, "failed": False}]}]}},
            {"cases": {"y": [{"calls": [{"name": "enemies", "args": {}, "failed": False}]}]}}]
    assert recorded_calls(runs, "x") == [("pull_timeline", {"pull": 19})]


def test_a_number_from_rankings_that_moved_is_reported_apart_not_as_stale():
    from evals.lint import drifted
    case = {"expect": "a low parse", "facts": ["below the 25th percentile of ranked kills"], "drifts": ["25th"]}
    assert stale(case, "below the 30th percentile") == [] and drifted(case, "below the 30th percentile") == ["25"]


async def test_without_runs_to_replay_no_case_is_checked(tmp_path, capsys):
    from evals.lint import lint
    assert await lint([{"id": "x", "expect": "Imbibe at 1:47"}], tmp_path) == (0, 0, 0, 0)
    assert capsys.readouterr().out == ""  # the run's summary says so once, not each case


async def test_an_empty_results_folder_checks_nothing_and_does_not_pass(tmp_path, capsys):
    from evals.run import _lint_then_drain
    assert await _lint_then_drain("", tmp_path) == 1
    assert capsys.readouterr().out.startswith("checked nothing: no run in ")


async def test_a_replay_that_fails_is_a_failure_and_one_without_data_leaves_its_case_unchecked(tmp_path, capsys,
                                                                                              monkeypatch):
    """A tool that fails on replay fails the lint; one that fails for lack of data says nothing about the case, whose
    numbers are then not flagged, and the lint says it didn't check."""
    import json

    from evals.lint import lint
    from evals.run import case_hash, lint_line
    from wipebot.core import server

    def pull_summary(pull: int = 0) -> str: return ""

    async def call(name: str, args: dict) -> tuple[str, bool]:
        if args["pull"] == 1:
            return "Pull 1 ended at 6:00 by the enrage.", False
        if args["pull"] == 2:
            raise RuntimeError("Error executing tool pull_summary: auth_failed: Warcraft Logs returned HTTP 401")
        return "pull_summary failed with an unexpected error: TypeError", True
    monkeypatch.setattr(server, "configure", lambda: None)
    monkeypatch.setattr(server, "_TOOLS", [("pull_summary", pull_summary)])
    monkeypatch.setattr(server, "call", call)
    cases = [{"id": c, "report": "R", "question": "why?", "expect": "The enrage at 6:01.", "wrong": ["x"]}
             for c in ("fine", "no-data", "crash", "all-fail")]
    pulls = {"fine": [1], "no-data": [1, 2], "crash": [1, 3], "all-fail": [3]}
    (tmp_path / "run.json").write_text(json.dumps({
        "case_hashes": {c["id"]: case_hash(c) for c in cases},
        "cases": {c: [{"calls": [{"name": "pull_summary", "args": {"pull": n}, "failed": False} for n in ns]}]
                  for c, ns in pulls.items()}}))
    found = await lint(cases, tmp_path)
    assert found == (2, 2, 2, 1)  # 6:01 flagged where its calls replayed; two crashes; one case without data
    out = capsys.readouterr().out
    assert "no-data: not checked: 1 of its 2 calls need data the frozen file lacks and Warcraft Logs didn't give " \
           "(auth_failed)" in out and "no-data: expect states" not in out
    assert "crash: <pull_summary failed: pull_summary failed with an unexpected error: TypeError>" in out
    assert "all-fail: every call of its answers fails now: its numbers aren't checked" in out
    assert lint_line(found, 4).startswith("2 of 4 cases checked: 2 numbers no tool prints now; 2 replayed calls fail "
                                          "now; 1 cases NOT checked for lack of data")
    assert lint_line(type(found)(0, 4), 4) == "4 of 4 cases checked: every number is in its tools' output"


def test_the_ended_by_line_names_the_experts_cause_first():
    """What the model and the raider read (ending_text), not only the engine's summary for the evals (lead)."""
    from evals.endings import names_first
    enrage = ("Ended by (from the log, earliest first): the boss gained Unbound Ferocity at 6:00 and kept it to the "
              "end, 14 of 20 alive (look it up); then Mutilate [1] killed 9 within 2 s at 6:20 (find which).")
    assert names_first(enrage, "enrage: Unbound Ferocity") and not names_first(enrage, "ability: Mutilate")
    cascade = "Ended by: deaths from 0:46 until half the raid was dead at 2:10, no single hit killing many: start..."
    assert names_first(cascade, "deaths from 0:47") and not names_first(cascade, "deaths from 0:50")
    led = ("Ended by (from the log, earliest first): 4 deaths one after another from 3:00, a tank among them, none "
           "brought back (what each left short may be what lost the rest); then Malignance [2] killed 8 within 1 s.")
    assert names_first(led, "deaths from 3:00") and not names_first(led, "ability: Malignance")
    after = ("Ended by (from the log, earliest first): Malignance [2] killed 8 within 1 s at 4:00 (find which). "
             "Before it, 2 deaths one after another from 3:00, none brought back: what each left short may be it.")
    assert names_first(after, "ability: Malignance") and not names_first(after, "deaths from 3:00")
    assert names_first("Ended by: a reset (the boss untouched).", "reset") and names_first(None, "kill")
    assert not names_first(None, "reset") and not names_first(cascade, "reset")
