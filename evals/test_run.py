"""Comparing a run with the baseline: only cases unchanged since it, the judge's changes called out, noise apart;
and who a run bills."""
import json
import os

import pytest

from evals.run import (
    HERE,
    Trial,
    beyond_noise,
    billed_to,
    case_hash,
    compare,
    judged_by,
    plugin_folder,
    skill_use,
    spread,
    summary,
)

CASE = {"id": "a", "report": "R", "question": "Why did we wipe?", "expect": "An enrage.", "wrong": ["deaths"]}


def tally(passed: dict, trials: int = 3, errors: dict | None = None, hashes: dict | None = None, judge=None) -> dict:
    return {"model": "opus", "commit": "abc", "trials": trials, "judge": judge or judged_by("sonnet"),
            "case_hashes": hashes or {c: "h" for c in passed}, "passed": passed, "errors": errors or {}}


def test_a_case_hash_ignores_spacing_but_not_words():
    assert case_hash(CASE) == case_hash({**CASE, "id": "b", "expect": "An\n   enrage. "})
    assert case_hash(CASE) != case_hash({**CASE, "expect": "A cascade."})
    assert case_hash(CASE) != case_hash({**CASE, "wrong": ["deaths", "the tank"]})
    assert case_hash(CASE) != case_hash({**CASE, "facts": ["the enrage at 6:00"]})


def test_the_judge_hash_follows_its_prompts(monkeypatch):
    from evals import run
    before = judged_by("sonnet")
    monkeypatch.setattr(run, "WRONG", run.WRONG + " Be strict.")
    assert judged_by("sonnet")["prompts"] != before["prompts"] and judged_by("opus")["model"] == "opus"


def test_noise_scales_with_trials():
    assert not beyond_noise(2, 3, 3, 3) and beyond_noise(1, 3, 3, 3)  # 1 of 3 is noise, 2 of 3 is a move
    assert not beyond_noise(3, 5, 5, 5) and beyond_noise(2, 5, 5, 5)
    assert not beyond_noise(7, 10, 10, 10) and beyond_noise(6, 10, 10, 10)
    assert not beyond_noise(1, 1, 2, 3) and beyond_noise(0, 1, 3, 3)  # one trial: only all or nothing


def test_the_spread_matches_what_runs_have_shown():
    # nine of fifty cases passing 1 or 2 of 3: past runs of 150 answers wandered by 3-4
    rates = {f"c{i}": (1 / 3 if i < 9 else 1.0) for i in range(50)}
    assert 3 < spread(rates, dict.fromkeys(rates, 3)) < 4
    assert spread({"a": 1.0, "b": 0.0}, {"a": 3, "b": 3}) == 0


def test_the_summary_marks_flaky_cases_and_leaves_errors_out():
    lines = summary(tally({"a": 3, "b": 1, "c": 0}, errors={"c": 1}))
    assert lines[0].split() == ["a", "3/3"]
    assert lines[1].split() == ["b", "1/3", "flaky"]
    assert lines[2].split() == ["c", "0/2", "(1", "errored)"]
    assert lines[-2].startswith("4/8 answers passed, ±") and "1 flaky; 1 errored" in lines[-2]
    assert lines[-1] == "1/3 cases right on every trial"


def test_only_unchanged_cases_are_compared():
    base = tally({"a": 3, "b": 3, "c": 0}, hashes={"a": "h", "b": "old", "c": "h"})
    lines = compare(base, tally({"a": 3, "b": 0, "c": 3, "d": 1}))
    text = "\n".join(lines)
    assert "WARNING" not in text and "note:" not in text
    assert "compared 2 of 4 cases; skipped 1 changed since the baseline (b); 1 not in it (d)" in text
    assert "total 6/6, the baseline's 3 at these trials: +3, beyond the ±1.2" in text
    assert "moved beyond noise" in text and "\n    c 3/3 (baseline 0/3)" in text and "b 0/3" not in text


def test_a_move_of_one_is_noise_on_one_line():
    lines = compare(tally({"a": 3, "b": 2, "c": 1}), tally({"a": 2, "b": 3, "c": 1}))
    assert lines[-2].endswith("2 of 3): none")
    assert lines[-1] == "  within noise: a 2/3 (baseline 3/3), b 3/3 (baseline 2/3)"


def test_a_changed_judge_is_called_out_first():
    base = tally({"a": 3}, judge={"prompts": "old", "model": "sonnet"})
    lines = compare(base, tally({"a": 3}))
    assert lines[0].startswith("WARNING: the judge changed since the baseline: differences may be the judge's "
                               "(prompts old -> ")
    assert "model" not in lines[0]
    lines = compare(tally({"a": 3}, judge={**judged_by("sonnet"), "model": "haiku"}), tally({"a": 3}))
    assert lines[0].endswith("(model haiku -> sonnet)")


def test_an_old_baseline_without_hashes_compares_everything_with_a_note():
    old = {"model": "opus", "commit": "abc", "trials": 3, "passed": {"a": 3, "b": 1}, "errors": {}}
    text = "\n".join(compare(old, tally({"a": 1, "b": 1})))
    assert "WARNING" not in text and "note: the baseline is older than case and judge hashes" in text
    assert "compared 2 of 2 cases\n" in text and "a 1/3 (baseline 3/3)" in text


def test_errored_trials_are_left_out_of_the_rates():
    lines = compare(tally({"a": 3}), tally({"a": 1, "b": 0}, errors={"a": 2, "b": 3}))
    text = "\n".join(lines)
    assert "1 not in it (b)" in text and "total 1/1" in text and "none" in text  # 1 of 1 passed: no move


def test_the_baselines_hold_hashes_for_every_case():
    for path in [HERE / "baseline-bot.json", HERE / "baseline-connector-only.json", HERE / "baseline.json"]:
        if not path.exists():  # the plugin mode's, until its first saved run
            continue
        base = json.loads(path.read_text())
        assert set(base["case_hashes"]) == set(base["passed"]) and base["judge"]["prompts"]


def test_a_run_drops_an_api_key_unless_asked_to_use_it(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert billed_to(api_key=True) == "the API (ANTHROPIC_API_KEY)"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert billed_to(api_key=False) == "your Claude Code login"
    assert "ANTHROPIC_API_KEY" not in os.environ  # the answers and the judge can't bill it by accident


def test_asking_for_the_api_without_a_key_stops(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is not set"):
        billed_to(api_key=True)


def test_the_plugin_mode_loads_the_plugin_members_get_without_its_connector():
    """The skill as the plugin ships it; the session's in-process tools stand in for the connector."""
    from pathlib import Path
    folder = Path(plugin_folder())
    manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text())
    assert "mcpServers" not in manifest and manifest["version"]
    assert {p.name for p in (folder / "skills" / "raid-log-analysis").iterdir()} >= {"SKILL.md", "reading-a-wipe.md"}


def test_skill_use_counts_answers_that_loaded_it_and_the_files_read():
    loaded = ["skill:wipebot:raid-log-analysis", "read:reading-a-wipe.md", "read:reading-a-wipe.md"]
    results = {"wipe": [Trial(loaded=loaded), Trial()], "night": [Trial(loaded=loaded[:1])],
               "broken": [Trial(errored=True)]}
    assert skill_use(results) == ("The skill was loaded in 2 of 3 answers; files read: reading-a-wipe.md 1\n"
                                  "  answered without the skill on some trial: wipe")


def test_a_run_counts_facts_calls_and_first_calls_apart_from_passing():
    from evals.run import answers, how_answered
    call = {"name": "pull_summary", "args": {}, "result": "", "failed": False}
    other = {**call, "name": "progress"}
    cases = [{"id": "wipe", "tools": ["pull_summary"]}, {"id": "night"}]
    results = {"wipe": [Trial(facts={"stated": 2, "of": 3, "missing": ["x"]}, calls=[call, other]),
                        Trial(facts={"stated": 3, "of": 3, "missing": []}, calls=[other]),
                        Trial(errored=True, calls=[call])],
               "night": [Trial(calls=[call, call, call])]}
    counts = answers(cases, results)
    assert counts == {"facts": {"wipe": [5, 6]}, "calls": {"wipe": 3, "night": 3}, "first_tagged": {"wipe": 1}}
    t = tally({"wipe": 1, "night": 1}, trials=3, errors={"wipe": 1}) | counts  # wipe: 2 answers judged
    assert how_answered(t, ["wipe"]) == ("supporting facts stated 5/6 (83%); 1.5 tool calls an answer; first call "
                                         "one of the case's tools in 1/2 (50%)")


def test_a_recheck_leads_with_answers_whose_tools_contradict_them():
    from evals.run import recheck_summary
    def x(passed, contradicted, unsupported, scores):
        return {"passed": passed, "grounded": {"ok": not (contradicted or unsupported), "contradicted": contradicted,
                                                "unsupported": unsupported}, "rubric": scores, "cost_usd": 0.0}
    out = {"a": [x(True, ["wrong time"], [], {"verdict_first": True, "concrete_fix": None, "few_changes": True}),
                 x(False, [], ["a guess"], {"verdict_first": False, "concrete_fix": True, "few_changes": True})],
           "b": [x(True, [], [], {"verdict_first": True, "concrete_fix": None, "few_changes": None})]}
    assert recheck_summary(out) == [
        "answers stating something their tool outputs contradict: 1/3 (1 of them passed); claims contradicted 1, "
        "unsupported 1, over 3 answers",
        "verdict_first: 2/3 (67%)", "concrete_fix: 1/1 (100%)", "few_changes: 2/2 (100%)"]
