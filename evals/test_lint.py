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
    assert await lint([{"id": "x", "expect": "Imbibe at 1:47"}], tmp_path) == (0, 0)
    assert capsys.readouterr().out == ""  # the run's summary says so once, not each case
