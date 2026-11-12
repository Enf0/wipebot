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
    settled,
    skill_use,
    spread,
    summary,
    uncalled,
)
from wipebot.core import server

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
    assert lines[0] == "ERRORS: 1 answer errored and is in no count below: c (1)"
    assert lines[1].split() == ["a", "3/3"]
    assert lines[2].split() == ["b", "1/3", "flaky"]
    assert lines[3].split() == ["c", "0/2", "(1", "errored)"]
    assert lines[-2].startswith("The main point got across in 4/8 answers, ±") and "1 flaky; 1 errored" in lines[-2]
    assert lines[-1] == "1/3 cases right on every trial"


def test_a_summary_says_what_the_comparison_skips_and_what_the_baseline_already_failed():
    """In place of a hand-kept hard set: the baseline's own results, as three groups and its known failures."""
    base = tally({"a": 3, "b": 2, "c": 1, "d": 0, "e": 3}, hashes={c: "h" for c in "abcd"} | {"e": "old"})
    lines = summary(tally({"a": 3, "b": 3, "c": 0, "d": 1, "e": 0, "f": 3}), base)
    assert lines[:2] == ["No answer errored.", "4 of 6 cases are compared with the baseline; skipped: 1 changed "
                                               "since the baseline; 1 not in it"]
    assert [line.split() for line in lines[2:8]] == [
        ["a", "3/3"], ["b", "3/3", "(baseline", "2/3)"], ["c", "0/3", "(known", "failure:", "baseline", "1/3)"],
        ["d", "1/3", "flaky", "(known", "failure:", "baseline", "0/3)"],
        ["e", "0/3"], ["f", "3/3"]]  # a changed or new case has no baseline to show
    assert lines[8].startswith("The main point got across in 10/18 answers") and lines[9].startswith("3/6 cases")
    assert lines[10:] == [
        "  where the baseline always passed (1 case): 3/3", "  where the baseline passed sometimes (2 cases): 3/6",
        "  where the baseline never passed (1 case): 1/3",
        "Known failures, which the baseline fails more than once in three: c 0/3 (baseline 1/3), d 1/3 "
        "(baseline 0/3)"]


def test_a_summary_says_which_answers_failed_whatever_the_judge_said_and_a_crashed_tool_is_red():
    from evals import run
    ok = {"ok": True, "reason": ""}
    crash = {"name": "progress", "failed": True, "result": "progress failed with an unexpected error: TypeError"}
    case = {**CASE, "kind": "wipe"}
    answers = (Trial(expect=ok, wrong=ok), Trial(expect=ok, wrong=ok, calls=[crash]),
               Trial(error="ModelError: error_max_turns"))
    results = {"a": [run.settle(t, case, "bot") for t in answers],
               "b": [run.settle(Trial(expect=ok, wrong=ok, seconds=400.0, waited_s=0.0), case, "bot")]}
    t = run.tally_of([{**case, "id": "a"}, {**case, "id": "b"}], results)
    assert t["broke"] == {"a tool crashed": {"a": 1}, "hit the bot's cap": {"a": 1}, "took too long": {"b": 1}}
    assert summary(t)[:4] == ["No answer errored.",
                              "1 answer failed whatever the judge said (a tool crashed): a (1)",
                              "1 answer failed whatever the judge said (hit the bot's cap): a (1)",
                              "1 answer failed whatever the judge said (took too long): b (1)"]
    assert run.red(t, None) == ["a tool crashed in 1 answer"]  # the cap and the wait are the model's, and counted
    assert run.red(t | {"broke": {}, "errors": {"a": 2}}, None) == ["2 answers errored"]
    assert run.red(tally({"a": 3}), None) == [] and run.exit_code(tally({"a": 3}), None) == 0


def test_a_run_in_which_every_answer_errored_still_has_a_total_line():
    lines = summary(tally({"a": 0, "b": 0}, trials=1, errors={"a": 1, "b": 1}))
    assert lines[0] == "ERRORS: 2 answers errored and are in no count below: a (1), b (1)"
    assert lines[-1] == ("The main point got across in 0/0 answers, ±0.0 from run to run; 0 flaky; 2 errored, not "
                         "counted")
    assert summary(tally({"a": 1}, trials=1))[0] == "No answer errored."


def test_a_tally_counts_errors_apart_and_the_answers_that_asked_warcraft_logs_live():
    from evals.run import tally_of
    live = {"name": "pull_summary", "args": {}, "result": "", "failed": False, "wcl_points": {"report": 12.5}}
    frozen = {**live, "wcl_points": {}}
    results = {"a": [Trial(passed=True, calls=[frozen]), Trial(passed=True, calls=[frozen, live]),
                     Trial(errored=True, calls=[live])],
               "b": [Trial(calls=[frozen])] * 3}
    t = tally_of([{**CASE, "id": "a"}, {**CASE, "id": "b", "hard": True}], results, model="opus")
    assert (t["trials"], t["passed"], t["errors"], t["live"]) == (3, {"a": 2, "b": 0}, {"a": 1}, {"a": 2})
    assert t["hard"] == ["b"] and t["model"] == "opus" and set(t["case_hashes"]) == {"a", "b"}
    assert summary(t)[1] == "2 of 6 answers asked Warcraft Logs live, for data the frozen file didn't hold."


def test_a_run_is_scored_by_the_checks_its_baseline_made():
    """A check added since the baseline (here "form") fails answers in the run's own summary, and none in the
    comparison; what fails an answer whatever the checks say (a crashed tool) fails it in both."""
    from evals import run
    ok = {"ok": True, "reason": ""}
    fine = Trial(expect=ok, wrong=ok)
    shapeless = Trial(expect=ok, wrong=ok, checks={"tone": False})  # a check no rule of today makes
    crashed = Trial(expect=ok, wrong=ok, calls=[{"name": "progress", "failed": True,
                                                 "result": "progress failed with an unexpected error"}])
    wrong = Trial(expect=ok, wrong={"ok": False, "reason": ""}, uncalled=["spec_guide"])
    case = {**CASE, "kind": "wipe"}
    results = {"a": [run.settle(t, case, "plugin") for t in (fine, shapeless, crashed, wrong)]}
    assert [t.passed for t in results["a"]] == [True, True, False, False]  # tone isn't one of today's checks
    assert wrong.checks == {"expect": True, "wrong": False, "calls": False}
    now = run.tally_of([{**case, "id": "a"}], results)
    assert now["parts"] == ["expect", "wrong", "calls", "language"] and now["passed"] == {"a": 2}
    assert now["failed"] == {"a": [["broke"], ["wrong", "calls"]]}
    with_form = {**now, "parts": ["expect", "wrong", "calls", "form"], "passed": {"a": 1},
                 "failed": {"a": [["form"], ["broke"], ["wrong", "calls"]]}}
    assert run.scored(with_form, ["expect", "wrong", "calls"])["passed"] == {"a": 2}
    assert run.scored(with_form, ["expect"])["passed"] == {"a": 3}  # only the crash still fails
    old = tally({"a": 4}, trials=4, hashes={"a": case_hash({**case, "id": "a"})})  # no parts: expect, wrong, calls
    text = "\n".join(compare(old, with_form | {"model": "opus", "commit": "abc", "judge": judged_by("sonnet")}))
    assert "scored by the baseline's checks (expect, wrong, calls): this run's form is left out" in text
    assert "total 2 of 4" in text
    # the other way round, a run from before a check against a baseline that counts it: nothing there failed it
    before = old | {"model": "opus", "commit": "abc", "judge": judged_by("sonnet")}
    with_tone = with_form | {"parts": [*now["parts"], "tone"], "model": "opus", "judge": judged_by("sonnet")}
    found = run.compared(with_tone, before)
    unmade = "the baseline counts a check this run never made (language, tone)"
    assert f"  {unmade}: every answer here passes it unseen" in found.lines
    assert found.verdict == "cannot tell" and unmade in found.lines[-1]
    # a check by code is never one: it is made of every answer each time a run is read (test_code_checks.py)
    assert "never made" not in "\n".join(compare(with_form | {"model": "opus", "judge": judged_by("sonnet")}, before))


def test_only_unchanged_cases_are_compared():
    base = tally({"a": 3, "b": 3, "c": 0}, hashes={"a": "h", "b": "old", "c": "h"})
    lines = compare(base, tally({"a": 3, "b": 0, "c": 3, "d": 1}))
    text = "\n".join(lines)
    assert "WARNING" not in text and "note:" not in text
    assert "compared 2 of 4 cases; skipped 1 changed since the baseline (b); 1 not in it (d)" in text
    assert "total 6 of 6, each case weighed as in the baseline, whose total was 3: +3, 2.4 times the ±1.2" in text
    assert "moved beyond noise" in text and "\n    c 3/3 (baseline 0/3), by chance 1 in 20" in text
    assert "b 0/3" not in text
    assert lines[-1] == ("VERDICT: cannot tell: 2 of this run's 4 cases not compared; too few answers (6 on 2 "
                         "cases): a drop of the total under 41 points could not show.")


def test_a_move_of_one_is_noise_on_one_line():
    lines = compare(tally({"a": 3, "b": 2, "c": 1}), tally({"a": 2, "b": 3, "c": 1}))
    assert lines[-3].endswith("2 of 3): none")
    assert lines[-2] == "  within noise: a 2/3 (baseline 3/3), b 3/3 (baseline 2/3)"
    assert lines[-1].startswith("VERDICT: cannot tell: too few answers (9 on 3 cases)")


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
    assert "1 not in it (b)" in text and "total 3 of 3" in text and "none" in text  # 1 of 1 passed: no move
    assert lines[1] == "  ERRORS: 5 answers errored and are in no count below: a (2), b (3)"


def test_auto_trials_ask_twice_only_what_the_baseline_was_sure_of():
    cases = [{**CASE, "id": c} for c in "abcdef"]
    h = case_hash(CASE)
    base = tally({"a": 3, "b": 0, "c": 2, "d": 3, "e": 1, "f": 3}, errors={"e": 2},
                 hashes={"a": h, "b": h, "c": h, "d": "old", "e": h})
    # c passed only sometimes, d changed since, e had a single judged trial, f has no hash: asked three times
    assert settled(base, cases, "sonnet") == {"a": True, "b": False}
    assert settled({**base, "judge": judged_by("opus")}, cases, "sonnet") == {}  # another judge: no verdict holds
    assert settled(None, cases, "sonnet") == {}
    from evals.run import planned
    assert planned(base, cases, "sonnet", "auto") == {"a": 2, "b": 2, "c": 3, "d": 3, "e": 3, "f": 3}
    assert planned(base, cases, "sonnet", 1) == dict.fromkeys("abcdef", 1)


def test_a_run_with_trials_per_case_compares_case_by_case():
    now = tally({"a": 1, "b": 1, "c": 2}) | {"trials": {"a": 1, "b": 3, "c": 3}}
    text = "\n".join(compare(tally({"a": 3, "b": 3, "c": 2}), now))
    # each case weighs as in the baseline, whatever it was asked now: a's one answer counts for three
    assert "total 6 of 9, each case weighed as in the baseline, whose total was 8: -2" in text
    assert "    the 2 the baseline judged the same on every trial: 4 of 6, the baseline's 6: -2 (±1.2)" in text
    assert "    the others: 2 of 3, the baseline's 2: +0 (±1.2)" in text
    assert "\n    b 1/3 (baseline 3/3), by chance 1 in 5" in text and "a 1/1" not in text  # one answer, as before
    assert summary(now)[1].split() == ["a", "1/1"] and summary(now)[2].split() == ["b", "1/3", "flaky"]
    assert "(opus, abc, 1-3 trials)" in "\n".join(compare(now, now))


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
    # the core and every part it sends a question to: a part the plugin left out could not be read
    shipped = {p.name for p in (folder / "skills" / "raid-log-analysis").iterdir()}
    assert shipped == {p.name for p in server.SKILL.iterdir()} and {"SKILL.md", "examples.md"} < shipped


def test_skill_use_counts_answers_that_loaded_it_and_the_files_read():
    loaded = ["skill:wipebot:raid-log-analysis", "read:examples.md", "read:examples.md"]
    results = {"wipe": [Trial(loaded=loaded), Trial()], "night": [Trial(loaded=loaded[:1])],
               "broken": [Trial(errored=True)]}
    assert skill_use(results) == ("The skill was loaded in 2 of 3 answers; files read: examples.md 1\n"
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
    assert counts == {"facts": {"wipe": [5, 6]}, "calls": {"wipe": 3, "night": 3}, "first_tagged": {"wipe": 1},
                      "words": {}, "slips": {}}
    t = tally({"wipe": 1, "night": 1}, trials=3, errors={"wipe": 1}) | counts  # wipe: 2 answers judged
    assert how_answered(t, ["wipe"]) == ("supporting facts stated 5/6 (83%); 1.5 tool calls an answer; first call "
                                         "one of the case's tools in 1/2 (50%)")


def test_a_run_counts_the_answers_words_and_as_the_bot_those_that_leave_its_form():
    from evals.run import answers, how_answered
    results = {"wipe": [Trial(form={"words": 180}, slips={}),
                        Trial(form={"words": 260}, slips={"a long bullet": "x"}),
                        Trial(errored=True, form={"words": 900}, slips={"a long bullet": "x"})],
               "night": [Trial(form={"words": 100})]}  # in a client: measured, held to no form
    counts = answers([{"id": "wipe"}, {"id": "night"}], results)
    assert counts["words"] == {"wipe": 440, "night": 100} and counts["slips"] == {"wipe": 1}
    t = tally({"wipe": 1, "night": 1}, errors={"wipe": 1}) | {"trials": {"wipe": 3, "night": 1}} | counts
    assert how_answered(t, ["wipe", "night"]) == ("0.0 tool calls an answer; 180 words an answer; 1/2 leave the "
                                                  "bot's form")
    before = {k: v for k, v in t.items() if k not in ("words", "slips")}  # a baseline from before they were kept
    assert how_answered(before, ["wipe", "night"]) == "0.0 tool calls an answer"


def test_a_saved_runs_answers_are_measured_again_without_a_model(tmp_path):
    from evals.run import form
    kept = "**The enrage ended it.**\n- ⚠️ **Kill the boss before 2:32.** It enrages then.\nFollow-up: Who died first?"
    long = "**The enrage ended it.**\n" + "\n".join(f"- point {n}" for n in range(4))
    run = {"mode": "bot", "cases": {
        "wipe-too-few-alive": [{"answer": kept}, {"answer": long}, {"answer": long, "errored": True}],
        "live-raid-plain-question": [{"answer": long}],  # while the raid is going on: at most three bullets
        "post-tank-death": [{"answer": "Kick the add.\nSecond.\nThird.\nFourth.\nFifth."}],
        "a-case-since-removed": [{"answer": long}]}}
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run), encoding="utf-8")
    assert form(path) == [
        "live-raid-plain-question           4 bullets (at most 3)",
        "post-tank-death                    5 lines (at most 4)",
        "how the answers are built (the middle one of 4): 12 words, 2 bullets of 2 words, a verdict of 4 words, "
        "0 follow-ups",
        "2 of 4 leave the form the bot's prompt asks for: many bullets 1, many lines 1"]
    assert form(path, "post-")[0].startswith("post-tank-death") and len(form(path, "post-")) == 3
    path.write_text(json.dumps(run | {"mode": "plugin"}), encoding="utf-8")
    assert len(form(path)) == 1  # in a client the form is the client's own: only measured


def test_a_recheck_leads_with_answers_stating_something_misleading():
    from evals.run import recheck_summary

    def x(passed, misleading, scores):
        return {"passed": passed, "grounded": {"ok": not misleading, "misleading": misleading}, "rubric": scores,
                "cost_usd": 0.0}
    out = {"a": [x(True, ["wrong time"], {"verdict_first": True, "concrete_fix": None, "few_changes": True}),
                 x(False, [], {"verdict_first": False, "concrete_fix": True, "few_changes": True})],
           "b": [x(True, [], {"verdict_first": True, "concrete_fix": None, "few_changes": None})]}
    assert recheck_summary(out) == [
        "answers stating something misleading their tool outputs don't back: 1/3 (1 of them passed; 1 claims)",
        "verdict_first: 2/3 (67%)", "concrete_fix: 1/1 (100%)", "few_changes: 2/2 (100%)"]


def test_an_answer_without_a_call_its_case_requires_fails():
    """The judge sees only the answer: a step of the method its text doesn't show, such as reading the spec's guide
    before advising, is checked from the calls of the whole conversation."""
    case = {"id": "me-healer", "calls": ["spec_guide"]}
    assert uncalled(case, [{"name": "player_pull"}, {"name": "spec_guide", "turn": 0}]) == []
    assert uncalled(case, [{"name": "player_pull"}]) == ["spec_guide"]
    assert uncalled(case, [{"name": "player_pull"}], "connector") == []  # no skill there to give the step
    assert uncalled({"id": "wipe"}, []) == []


def test_a_report_sent_before_the_user_agreed_fails():
    """`asks_first`: only the last question, where the user agrees, may send the report, and in every mode."""
    case = {"id": "report", "question": ["what killed us?", "that's wrong", "yes, send it"],
            "asks_first": ["report_answer"]}
    assert uncalled(case, [{"name": "pull_summary", "turn": 0}, {"name": "report_answer", "turn": 2}]) == []
    assert uncalled(case, [{"name": "pull_summary", "turn": 0}], "connector") == ["report_answer"]
    assert uncalled(case, [{"name": "report_answer", "turn": 1}, {"name": "report_answer", "turn": 2}]) == [
        "report_answer only after the last question (called it before the user agreed)"]


# ---- the verdict, on a made-up suite shaped like the baselines: 72 cases right on every trial, 21 sometimes ----

def suite(now: dict | None = None, trials: dict | None = None) -> tuple[dict, dict]:
    """(the baseline, a run): every case passes as in the baseline but for `now`, and has 3 trials but for `trials`."""
    passed = {f"s{i}": 3 for i in range(72)} | {f"f{i}": 1 + i % 2 for i in range(21)}
    run = tally(passed | (now or {}))
    return tally(passed), run | {"trials": {c: 3 for c in passed} | trials} if trials else run


def verdict_of(now: dict | None = None, trials: dict | None = None, **changed) -> tuple[str, str]:
    from evals.run import compared
    base, run = suite(now, trials)
    found = compared(base, run | changed)
    return found.verdict, found.lines[-1]


def test_a_comparison_ends_in_one_verdict_with_the_drop_it_could_have_shown():
    assert verdict_of() == ("no change shown", "VERDICT: no change shown: a drop of 10.6 answers (3.8 points) or "
                                               "more would have shown.")
    name, line = verdict_of({f"s{i}": 2 for i in range(8)})  # eight answers fewer: one spread, no case beyond noise
    assert name == "no change shown" and line.startswith(
        "VERDICT: no change shown, but look: the total is 1.4 spreads below the baseline's (a drop is shown at 2)")
    name, line = verdict_of({f"s{i}": 2 for i in range(14)})
    assert (name, line) == ("a drop shown", "VERDICT: a drop shown: the total is 2.2 spreads below the baseline's.")
    name, line = verdict_of({f"f{i}": 3 for i in range(21)})
    assert name == "a rise shown" and "spreads above the baseline's" in line


def test_a_case_shows_a_drop_only_when_it_stayed_down_on_six_answers():
    from evals.run import by_chance, compared
    assert round(1 / by_chance(0, 6, 3, 3)) == 84 and round(1 / by_chance(1, 6, 3, 3)) == 21
    assert round(1 / by_chance(0, 3, 3, 3)) == 20 and round(1 / by_chance(3, 3, 0, 3)) == 20
    assert by_chance(2, 3, 2, 3) > 0.5
    base, run = suite({"s0": 0})  # none of three: beyond noise, but three answers do that by chance, so ask again
    found = compared(base, run)
    assert found.verdict == "no change shown" and found.again == ["s0"]
    assert "\n    s0 0/3 (baseline 3/3), by chance 1 in 20" in "\n".join(found.lines)
    base, run = suite({"s0": 0}, {"s0": 6})
    found = compared(base, run)
    assert found.verdict == "a drop shown" and not found.again
    assert found.lines[-1] == "VERDICT: a drop shown: stayed down on 6 answers: s0."
    assert "\n    s0 0/6 (baseline 3/3), by chance 1 in 84: stayed down" in "\n".join(found.lines)
    assert verdict_of({"s0": 1}, {"s0": 6})[0] == "no change shown"  # one of six: listed with its chance, no drop


def test_a_comparison_cannot_tell_when_the_judge_changed_or_cases_were_skipped():
    name, line = verdict_of({f"s{i}": 0 for i in range(30)}, judge={"prompts": "new", "model": "sonnet"})
    assert name == "cannot tell" and line.startswith("VERDICT: cannot tell: the judge changed since the baseline")
    hashes = {c: "h" for c in suite()[0]["passed"]} | {f"s{i}": "new" for i in range(12)}
    name, line = verdict_of(case_hashes=hashes)
    assert (name, line) == ("cannot tell", "VERDICT: cannot tell: 12 of this run's 93 cases not compared.")
    name, line = verdict_of({f"s{i}": 2 for i in range(20, 34)}, case_hashes=hashes)  # a drop on the rest is one
    assert name == "a drop shown" and line.endswith("(but 12 of this run's 93 cases not compared).")
    # errors that as failed answers would have made it a drop
    name, line = verdict_of({f"s{i}": 2 for i in range(8)}, errors={f"s{i}": 1 for i in range(20, 26)},
                            trials={f"s{i}": 4 for i in range(20, 26)})
    assert name == "cannot tell" and "the 6 errored answers could have made it a drop" in line
    assert line.endswith("Look: the total is 1.4 spreads below the baseline's.")
    # five cases whose every answer errored are in no total: failed, their fifteen answers would have been a drop
    lost = {f"s{i}": 0 for i in range(5)}
    name, line = verdict_of(lost, errors=dict.fromkeys(lost, 3))
    assert (name, line) == ("cannot tell", "VERDICT: cannot tell: the 15 errored answers could have made it a drop.")
    everything = dict.fromkeys(suite()[0]["passed"], 0)
    name, line = verdict_of(everything, errors=dict.fromkeys(everything, 3))
    assert (name, line) == ("cannot tell", "VERDICT: cannot tell: none of this run's cases was compared.")


# ---- asking: --trials auto and the cases asked again, with made-up answers in place of the model ----

async def asked(trials, source: dict[str, list[bool]], base: dict | None = None, **how):
    """The tally after `ask_all`, each case answering from its `source` in turn (True: a pass), and what it
    printed."""
    import contextlib
    import io

    from evals.run import ask_all, tally_of
    cases = [{**CASE, "id": c} for c in source]
    base = (base or suite()[0]) | {"case_hashes": {c["id"]: case_hash(c) for c in cases}}
    results: dict[str, list[Trial]] = {c: [] for c in source}

    async def one(case: dict) -> None:
        got = results[case["id"]]
        got.append(Trial(passed=source[case["id"]][len(got)]))

    def so_far() -> dict:
        return tally_of(cases, results, model="opus", commit="abc", judge=judged_by("sonnet"))
    with contextlib.redirect_stdout(io.StringIO()) as said:
        await ask_all(cases, base, trials, "sonnet", one, so_far, **how)
    return base, so_far(), said.getvalue()


def answers_like_the_baseline(**other: list[bool]) -> dict[str, list[bool]]:
    """Six answers a case: the steady ones always right, the others as often as in the baseline, but for `other`."""
    flaky = {f"f{i}": [True, False, False] * 2 if i % 2 == 0 else [False, True, True] * 2 for i in range(21)}
    return {f"s{i}": [True] * 6 for i in range(72)} | flaky | other


async def test_auto_asks_the_steady_cases_twice_and_reads_like_three_trials():
    from evals.run import compared
    base, run, said = await asked("auto", answers_like_the_baseline())
    assert said.startswith("72 of 93 cases asked 2 times, the baseline's verdict on every trial; the others 3 times.")
    assert sum(run["trials"].values()) == 72 * 2 + 21 * 3 and compared(base, run).verdict == "no change shown"
    _, three, _ = await asked(3, answers_like_the_baseline())
    assert compared(base, three).verdict == "no change shown" and three["trials"] == 3
    # the totals agree: a steady case's two answers weigh as the baseline's three
    assert "total 247 of 279" in compared(base, run).lines[4] and "total 247 of 279" in compared(base, three).lines[4]


async def test_auto_that_looks_low_becomes_a_three_trial_run():
    """Ten steady cases wrong on their first answer only: at two answers each the total is well below the
    baseline's, so every case gets its third, and the run is the three-trial run, verdict and all."""
    from evals.run import compared
    source = answers_like_the_baseline(**{f"s{i}": [False, True, True, True, True, True] for i in range(10)})
    base, run, said = await asked("auto", source)
    _, three, _ = await asked(3, source)
    assert "asking the 72 steady cases a third time" in said
    assert run["trials"] == three["trials"] == 3 and run["passed"] == three["passed"]
    assert compared(base, run).lines[-1] == compared(base, three).lines[-1]
    assert compared(base, run).lines[-1].startswith("VERDICT: no change shown, but look")


async def test_a_run_of_set_trials_that_looks_low_keeps_its_trials():
    """A one-trial run well below the baseline (another model's, say) stays one trial: only auto asks a third."""
    source = answers_like_the_baseline(**{f"s{i}": [False] * 6 for i in range(30)})
    _, run, said = await asked(1, source, confirm=False)
    assert "a third time" not in said and run["trials"] == 1


async def test_auto_shows_a_rise_only_as_three_trials_would():
    """Twelve of the others one answer better, and ten steady cases wrong on their third answer only: at two
    answers each that reads as a rise, which three trials don't show. So the third answers are asked."""
    from evals.run import compared
    better = {f"f{i}": [True] * 6 for i in range(1, 21, 2)} | dict.fromkeys(("f0", "f2"), [True, True, False] * 2)
    source = answers_like_the_baseline(**better, **{f"s{i}": [True, True, False, True, True, True] for i in range(10)})
    base, run, said = await asked("auto", source)
    _, three, _ = await asked(3, source)
    assert "spreads above the baseline's: asking the 72 steady cases a third time" in said
    assert run["trials"] == three["trials"] == 3 and run["passed"] == three["passed"]
    assert compared(base, run).verdict == compared(base, three).verdict == "no change shown"


async def test_a_case_that_moved_is_asked_to_six_answers_before_it_is_printed():
    from evals.run import compared
    source = answers_like_the_baseline(s0=[False] * 6, s1=[False, False, False, True, True, True])
    for trials in ("auto", 3):  # the same verdict either way
        base, run, said = await asked(trials, source)
        assert "Asking again, up to 6 answers each, the cases that moved beyond noise: s0, s1" in said
        assert run["trials"]["s0"] == run["trials"]["s1"] == 6 and run["trials"]["s2"] == 3  # auto: its third too
        found = compared(base, run)
        assert found.lines[-1] == "VERDICT: a drop shown: stayed down on 6 answers: s0."
        assert "  within noise: s1 3/6 (baseline 3/3)" in found.lines  # three answers more, and it was noise
    base, run, said = await asked(3, source, confirm=False)
    assert "Asking again" not in said and run["trials"] == 3 and compared(base, run).again == ["s0", "s1"]


async def test_an_errored_answer_is_asked_again_once_when_a_case_is_confirmed():
    from evals.run import ask_all, tally_of
    cases = [{**CASE, "id": c} for c in suite()[0]["passed"]]
    base = suite()[0] | {"case_hashes": {c["id"]: case_hash(c) for c in cases}}
    results: dict[str, list[Trial]] = {str(c): [] for c in base["passed"]}

    async def one(case: dict) -> None:
        got = results[case["id"]]
        steady = case["id"].startswith("s") or len(got) % 3 < (1 if int(case["id"][1:]) % 2 == 0 else 2)
        got.append(Trial(errored=True) if case["id"] == "s0" and len(got) == 3
                   else Trial(passed=steady and case["id"] != "s0"))

    def so_far() -> dict:
        return tally_of(cases, results, model="opus", commit="abc", judge=judged_by("sonnet"))
    await ask_all(cases, base, 3, "sonnet", one, so_far)
    run = so_far()
    assert run["trials"]["s0"] == 7 and run["errors"] == {"s0": 1} and run["passed"]["s0"] == 0  # six judged


# ---- a saved run read again, and a run kept as the baseline ----

@pytest.fixture
def suite_on_disk(tmp_path, monkeypatch):
    """The made-up suite as the cases, with its baseline in a file; returns a run's tally with the cases' hashes."""
    from evals import run
    cases = [{**CASE, "id": c, "kind": "wipe"} for c in suite()[0]["passed"]]
    hashes = {c["id"]: case_hash(c) for c in cases}
    monkeypatch.setattr(run, "load_cases", lambda: cases)
    monkeypatch.setattr(run, "baseline", lambda mode: tmp_path / f"baseline-{mode}.json")
    (tmp_path / "baseline-bot.json").write_text(json.dumps(suite()[0] | {"case_hashes": hashes}))

    def a_run(now: dict | None = None, **changed) -> dict:
        return suite(now)[1] | {"case_hashes": hashes, "commit": "def"} | changed
    return tmp_path / "baseline-bot.json", a_run


def test_a_run_like_the_baseline_replaces_it_and_keeps_what_it_replaced(suite_on_disk, capsys):
    from evals.run import save_baseline
    path, a_run = suite_on_disk
    save_baseline(a_run({"s0": 2}), "bot")
    kept = json.loads(path.read_text())
    assert kept["commit"] == "def" and "accepted" not in kept
    assert kept["replaced"]["commit"] == "abc" and kept["replaced"]["passed"] == [247, 279]
    assert kept["replaced"]["like_for_like"].startswith("total 246 of 279, each case weighed as in the baseline")
    assert kept["replaced"]["verdict"].startswith("VERDICT: no change shown")
    assert "Against the baseline it replaces:" in capsys.readouterr().out


def test_a_baseline_is_not_saved_over_a_drop_errors_or_a_filter_without_a_reason(suite_on_disk):
    from evals.run import save_baseline
    path, a_run = suite_on_disk
    before = path.read_text()
    low = a_run({f"s{i}": 2 for i in range(8)})  # one spread below: the next run would be measured against a dip
    with pytest.raises(SystemExit, match="1.4 spreads below the baseline it replaces.*--accept"):
        save_baseline(low, "bot")
    with pytest.raises(SystemExit, match="2 answers errored"):
        save_baseline(a_run(errors={"s0": 2}, trials={c: 5 if c == "s0" else 3 for c in a_run()["passed"]}), "bot")
    partial = a_run()  # (a run of some cases, -k, is merged into the baseline: test_baseline_merge.py)
    partial = partial | {"passed": {c: n for c, n in partial["passed"].items() if c != "s0"},
                         "case_hashes": partial["case_hashes"] | {"s1": "older"}}
    with pytest.raises(SystemExit, match="2 missing or changed since: s0, s1"):
        save_baseline(partial, "bot")
    with pytest.raises(SystemExit, match="not a baseline: 72 cases have fewer than 3 judged answers"):
        save_baseline(a_run(trials={c: 2 if c.startswith("s") else 3 for c in a_run()["passed"]}), "bot",
                      accept="no reason makes two answers three")
    assert path.read_text() == before
    save_baseline(low, "bot", accept="the judge got stricter on naming players")
    kept = json.loads(path.read_text())
    assert kept["accepted"] == {"reason": "the judge got stricter on naming players",
                                "despite": ["it is 1.4 spreads below the baseline it replaces on the cases unchanged "
                                            "since"]}
    assert kept["replaced"]["like_for_like"].startswith("total 239 of 279")


def test_a_saved_run_is_read_by_todays_rule_and_can_become_the_baseline(suite_on_disk, tmp_path, capsys):
    """A file from before 10 November: no named checks, and a crashed tool kept as an error."""
    from evals.run import read_run, save_baseline, show_run
    path, a_run = suite_on_disk
    ok, crash = {"ok": True, "reason": ""}, {"name": "progress", "failed": True,
                                             "result": "progress failed with an unexpected error: TypeError"}
    answer = {"answer": "The enrage.", "passed": True, "errored": False, "expect": ok, "wrong": ok, "calls": []}
    wrong = answer | {"passed": False, "wrong": {"ok": False, "reason": "blames the tank"}}
    crashed = answer | {"passed": False, "errored": True, "error": "progress: failed", "calls": [crash]}
    trials = {c: [answer] * n + [wrong] * (3 - n) for c, n in a_run()["passed"].items()} | {"s0": [crashed] * 3}
    saved = tmp_path / "run.json"
    saved.write_text(json.dumps({"model": "opus", "mode": "bot", "judge_model": "sonnet", "trials": 3,
                                 "commit": "def", "judge": judged_by("sonnet"),
                                 "case_hashes": a_run()["case_hashes"], "cases": trials}))
    run, results, tally = read_run(saved)
    assert tally["errors"] == {} and tally["passed"]["s0"] == 0 and tally["failed"]["s0"] == [["broke"]] * 3
    assert results["f0"][2].checks == {"expect": True, "wrong": False, "calls": True}
    assert tally["broke"] == {"a tool crashed": {"s0": 3}}
    assert show_run(saved) == 1  # a crashed tool is a fault in the code, whatever the totals make of three answers
    out = capsys.readouterr().out
    assert out.startswith("No answer errored.\n3 answers failed whatever the judge said (a tool crashed): s0 (3)\n"
                          "0 of 279 answers asked Warcraft Logs live, for data the frozen file didn't hold.\n"
                          "93 of 93 cases are compared with the baseline\n")
    assert "\n    s0 0/3 (baseline 3/3), by chance 1 in 20\n" in out and "(run.json: opus, bot, commit def)" in out
    assert out.endswith("VERDICT: no change shown: a drop of 10.9 answers (3.9 points) or more would have shown.\n"
                        "Exits non-zero: a tool crashed in 3 answers.\n")
    with pytest.raises(SystemExit, match="not saved as the baseline: a tool crashed in 3 answers"):
        save_baseline(tally, run["mode"], filtered=run.get("filter", ""))
    save_baseline(tally, run["mode"], accept="the tool is fixed in the next commit", filtered=run.get("filter", ""))
    assert json.loads(path.read_text())["passed"]["s0"] == 0


def test_a_runs_file_reads_back_as_the_run_was_tallied(tmp_path):
    """Every kind of answer, kept and read again (--summary, --record, --save-baseline RESULTS): no answer changes
    sides, a capped one least of all."""
    from evals import run
    ok = {"ok": True, "reason": ""}
    crash = {"name": "progress", "failed": True, "result": "progress failed with an unexpected error: TypeError"}
    refused = {"name": "progress", "failed": True, "result": "auth_failed: HTTP 401"}
    case = {**CASE, "id": "made-up", "kind": "wipe"}
    results = {"made-up": [run.settle(t, case, "bot") for t in (
        Trial(expect=ok, wrong=ok), Trial(expect=ok, wrong={"ok": False, "reason": "x"}),
        Trial(error="ModelError: error_max_turns"), Trial(error="ModelError: rate_limit"),
        Trial(expect=ok, wrong=ok, calls=[crash]), Trial(expect=ok, wrong=ok, calls=[refused]))]}
    tally = run.tally_of([case], results, model="opus", commit="abc", judge=judged_by("sonnet")) | {
        "tested": {"answers": ["claude-opus-x"]}}
    assert (tally["passed"], tally["errors"]) == ({"made-up": 1}, {"made-up": 2})
    path = run.keep(tmp_path / "results", results, tally, mode="bot", judge_model="sonnet", filter="", full=True)
    kept, again, read = run.read_run(path)
    assert kept["full"] is True and kept["date"] and read == tally
    assert [t.broke[:17] for t in again["made-up"]] == ["", "", "hit the bot's cap", "", "a tool crashed: p", ""]


def test_a_full_run_is_kept_as_its_modes_last(suite_on_disk, tmp_path, monkeypatch, capsys):
    from evals import run
    _, a_run = suite_on_disk
    monkeypatch.setattr(run, "FULL_RUNS", tmp_path / "full-runs.json")
    (tmp_path / "full-runs.json").write_text(json.dumps({"plugin": {"commit": "old"}}))
    run.record_full_run(a_run(errors={"s0": 1}), "bot", "VERDICT: no change shown.", "2026-11-12")
    kept = json.loads((tmp_path / "full-runs.json").read_text())
    assert kept["plugin"] == {"commit": "old"}  # the other modes' stay
    assert kept["bot"] == {"commit": "def", "date": "2026-11-12", "trials": "3 trials", "passed": 247, "of": 278,
                           "errors": 1, "verdict": "VERDICT: no change shown."}
    # a run most of whose answers errored evaluated little: a release goes on counting from the run before
    down = a_run(errors={f"s{i}": 3 for i in range(1, 60)}, commit="down")
    assert run.record_full_run(down, "bot", "VERDICT: cannot tell.", "2026-11-13") is None
    assert "not recorded as the last full bot run: 177 of its 279 answers errored" in capsys.readouterr().out
    assert json.loads((tmp_path / "full-runs.json").read_text()) == kept
    # a saved run, as the Evals workflow leaves one: recorded without a model, dated by its name
    answer = {"answer": "x", "expect": {"ok": True, "reason": ""}, "wrong": {"ok": True, "reason": ""}}
    saved = tmp_path / "bot-full-20261113.json"
    file = {"model": "opus", "mode": "bot", "commit": "fed", "judge": judged_by("sonnet"),
            "case_hashes": a_run()["case_hashes"], "cases": {c: [answer] * 3 for c in a_run()["passed"]}}
    saved.write_text(json.dumps(file))
    run.record_saved(saved)
    kept = json.loads((tmp_path / "full-runs.json").read_text())["bot"]
    assert (kept["commit"], kept["date"], kept["passed"], kept["of"]) == ("fed", "2026-11-13", 279, 279)
    assert kept["verdict"].startswith("VERDICT: a rise shown")
    saved.write_text(json.dumps(file | {"filter": "wipe"}))
    with pytest.raises(SystemExit, match="not a full run: it asked only the cases matching -k wipe"):
        run.record_saved(saved)
    saved.write_text(json.dumps(file | {"full": False}))
    with pytest.raises(SystemExit, match="not a full run of what the mode ships"):
        run.record_saved(saved)
    # a file from before the filter was kept: a handful of cases is no full run
    saved.write_text(json.dumps(file | {"cases": {c: [answer] for c in list(a_run()["passed"])[:10]}}))
    with pytest.raises(SystemExit, match="not a full run: it asked 10 of the 93 cases its mode has today"):
        run.record_saved(saved)
    assert json.loads((tmp_path / "full-runs.json").read_text())["bot"] == kept


def test_the_tracked_record_of_full_runs_names_modes_the_release_script_knows():
    from evals.run import FULL_RUNS
    from scripts.release import ANSWERS
    runs = json.loads(FULL_RUNS.read_text())
    assert set(runs) <= set(ANSWERS) and all(
        set(r) == {"commit", "date", "trials", "passed", "of", "errors", "verdict"} for r in runs.values())


def test_the_smoke_set_is_cases_every_mode_asks_and_every_baseline_passed_each_time():
    from evals.run import SMOKE, baseline, load_cases
    cases = {c["id"]: c for c in load_cases()}
    assert len(set(SMOKE)) == len(SMOKE) and set(SMOKE) <= set(cases)
    assert not [i for i in SMOKE if cases[i]["kind"] == "heldout" or cases[i].get("bot_only")
                or cases[i].get("client_only")]
    for mode in ("plugin", "connector", "bot"):  # a case a baseline now fails sometimes: put a steady one in its place
        base = json.loads(baseline(mode).read_text())
        assert not [i for i in SMOKE if i in base["failed"] or not base["passed"].get(i)], mode


def test_a_smoke_run_is_kept_as_its_modes_last_unless_it_errored_or_asked_other_cases(tmp_path, monkeypatch):
    from evals import run
    monkeypatch.setattr(run, "SMOKE_RUNS", tmp_path / "smoke-runs.json")
    tally = {"commit": "abc1234", "trials": 1, "passed": dict.fromkeys(run.SMOKE, 1) | {run.SMOKE[0]: 0}}
    assert run.record_smoke_run(tally, "bot", "2026-11-20") == {
        "commit": "abc1234", "date": "2026-11-20", "passed": 11, "of": 12, "errors": 0}
    kept = (tmp_path / "smoke-runs.json").read_text()
    assert run.record_smoke_run(tally | {"errors": {run.SMOKE[1]: 1}}, "bot", "2026-11-21") is None
    assert run.record_smoke_run(tally | {"passed": {"wipe-adds": 1}}, "bot", "2026-11-21") is None
    assert (tmp_path / "smoke-runs.json").read_text() == kept
