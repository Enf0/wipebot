"""A run of some cases merged into the baseline (`-k ... --trials 3 --save-baseline`): a corrected case costs three
trials of itself. The run's cases replace their own entries, every other case's entry stays, the file says what was
merged from where, and what a baseline refuses is refused for the cases merged."""
import json

import pytest

from evals.run import judged_by, save_baseline
from evals.test_run import suite_on_disk  # noqa: F401  the made-up suite with its baseline in a file


def some(tally: dict, cases: list[str], **changed) -> dict:
    """A run's tally cut down to the cases a -k picked."""
    return tally | {key: {c: v for c, v in tally[key].items() if c in cases}
                    for key in ("passed", "case_hashes", "errors")} | changed


def test_a_filtered_run_replaces_its_own_cases_and_leaves_the_rest(suite_on_disk, capsys):  # noqa: F811
    path, a_run = suite_on_disk
    before = json.loads(path.read_text())
    run = some(a_run({"s0": 2, "f0": 3}), ["s0", "s1", "f0"], tested={"sdk": "1.2"})
    save_baseline(run, "bot", filtered="s0,s1,f0", run="targeted-20261111.json")
    kept = json.loads(path.read_text())
    assert [kept["passed"][c] for c in ("s0", "s1", "f0")] == [2, 3, 3] and before["passed"]["f0"] == 1
    others = [c for c in before["passed"] if c not in ("s0", "s1", "f0")]
    assert len(others) == 90
    assert all(kept[key][c] == before[key][c] for key in ("passed", "case_hashes") for c in others)
    assert kept["commit"] == "abc" and kept["trials"] == 3 and "replaced" not in kept  # still the baseline it was
    assert kept["merged"] == [{
        "cases": ["f0", "s0", "s1"], "commit": "def", "run": "targeted-20261111.json", "filter": "s0,s1,f0",
        "trials": "3 trials", "tested": {"sdk": "1.2"}, "passed": [8, 9],
        "before": {"f0": [1, 3], "s0": [3, 3], "s1": [3, 3]}, "verdict": kept["merged"][0]["verdict"]}]
    assert kept["merged"][0]["verdict"].startswith("VERDICT: ")
    assert "merged 3 cases into evals/baseline-bot.json (f0, s0, s1): 90 other cases keep their entries" in \
        capsys.readouterr().out
    save_baseline(some(a_run(), ["s2"]), "bot", filtered="s2", run="targeted-20261112.json")  # a second merge adds on
    assert [m["cases"] for m in json.loads(path.read_text())["merged"]] == [["f0", "s0", "s1"], ["s2"]]


def test_a_reworded_case_comes_in_with_its_new_hash_and_its_own_trials(suite_on_disk, monkeypatch):  # noqa: F811
    from evals import run as harness
    path, a_run = suite_on_disk
    cases = [c | {"expect": "The enrage, not the deaths."} if c["id"] == "s0" else c for c in harness.load_cases()]
    monkeypatch.setattr(harness, "load_cases", lambda: cases)
    new_hash = harness.case_hash(cases[0])
    run = some(a_run({"s0": 4}), ["s0"], trials={"s0": 5}, case_hashes={"s0": new_hash})
    save_baseline(run, "bot", filtered="s0", run="r.json")
    kept = json.loads(path.read_text())
    assert kept["case_hashes"]["s0"] == new_hash and kept["passed"]["s0"] == 4
    assert kept["trials"]["s0"] == 5 and kept["trials"]["s1"] == 3  # five answers for the one, three for the rest


def test_a_merge_is_refused_for_what_a_baseline_refuses(suite_on_disk):  # noqa: F811
    path, a_run = suite_on_disk
    before = path.read_text()
    with pytest.raises(SystemExit, match="not a baseline: 1 cases have fewer than 3 judged answers"):
        save_baseline(some(a_run(), ["s0"], trials={"s0": 2}), "bot", filtered="s0", accept="two is not three")
    with pytest.raises(SystemExit, match="not merged into the baseline: 1 answer errored"):
        save_baseline(some(a_run(errors={"s0": 1}), ["s0"], trials={"s0": 4}), "bot", filtered="s0")
    down = some(a_run({f"s{i}": 0 for i in range(10)}), [f"s{i}" for i in range(10)])
    with pytest.raises(SystemExit, match="spreads below the baseline's entries for the cases unchanged since"):
        save_baseline(down, "bot", filtered="s")
    with pytest.raises(SystemExit, match="1 of its cases changed since the run or are not this mode's \\(s0\\)"):
        save_baseline(some(a_run(), ["s0"], case_hashes={"s0": "older"}), "bot", filtered="s0")
    for other, said in (({"judge": judged_by("opus")}, "judged by"), ({"model": "sonnet"}, "answered by sonnet")):
        with pytest.raises(SystemExit, match=f"not merged into the baseline: this run was {said}"):
            save_baseline(some(a_run(), ["s0"], **other), "bot", filtered="s0", accept="no reason mixes two judges")
    assert path.read_text() == before
    save_baseline(down, "bot", filtered="s", accept="the ten were reworded to ask more", run="r.json")
    merged = json.loads(path.read_text())["merged"][0]
    assert merged["accepted"]["reason"] == "the ten were reworded to ask more" and merged["passed"] == [0, 30]


def test_a_merged_case_is_counted_by_the_baselines_checks(suite_on_disk):  # noqa: F811
    """A baseline counts every case by one list of checks: an answer that failed only a check the baseline never
    made counts as it would have then, and the file keeps which check it was."""
    path, a_run = suite_on_disk
    base = json.loads(path.read_text()) | {"parts": ["expect", "wrong"], "failed": {}}
    path.write_text(json.dumps(base))
    run = some(a_run({"s0": 1}), ["s0"], parts=["expect", "wrong", "numbers"], failed={"s0": [["numbers"], ["expect"]]})
    save_baseline(run, "bot", filtered="s0", run="r.json", accept="one answer down on one case: within noise")
    kept = json.loads(path.read_text())
    assert kept["passed"]["s0"] == 2 and kept["failed"]["s0"] == [["numbers"], ["expect"]] and kept["parts"] == [
        "expect", "wrong"]
    assert kept["merged"][0]["accepted"]["despite"] == [
        "it is 1.1 spreads below the baseline's entries for the cases unchanged since"]
