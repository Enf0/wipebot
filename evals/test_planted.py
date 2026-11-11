"""The planted answers (evals/planted.py, evals/planted.toml): how the file is read, what makes a plant stale, and
that the plants in the file fit their cases. No model: the judge is faked."""
import json

import pytest

from evals import checks, planted
from evals.planted import Plant, load_plants, stale, unplanted
from evals.run import load_cases

WRONG = """[[plant]]
case = "wipe-a"
kind = "wrong"
item = "calls it an enrage, or says the raid needs more damage"
answer = \"\"\"
The boss enraged, and that ended the pull.
\"\"\"
"""
VAGUE = """[[plant]]
case = "wipe-a"
kind = "vague"
answer = "The raid took too much avoidable damage. Tighten up the mechanics."
"""
CASES = [{"id": "wipe-a", "kind": "wipes", "wrong": ["calls it an enrage, or says the raid needs more damage",
                                                     "names a healer"]},
         {"id": "night-b", "kind": "nights", "wrong": ["says the night got worse"]},
         {"id": "held-c", "kind": "heldout", "wrong": ["says the tank caused it"]}]


def written(tmp_path, text: str) -> list[Plant]:
    path = tmp_path / "planted.toml"
    path.write_text(text, encoding="utf-8")
    return load_plants(path)


def test_a_plant_is_read_with_its_case_kind_item_and_answer(tmp_path):
    wrong, vague = written(tmp_path, WRONG + VAGUE)
    assert wrong == Plant("wipe-a", "wrong", "The boss enraged, and that ended the pull.",
                          "calls it an enrage, or says the raid needs more damage")
    assert vague == Plant("wipe-a", "vague", "The raid took too much avoidable damage. Tighten up the mechanics.")
    assert written(tmp_path, "plant = []") == []


@pytest.mark.parametrize(("text", "why"), [
    ("[[plants]]\ncase = 'a'", "[[plant]] tables and nothing else"),
    (WRONG + "[defaults]\nreport = 'x'", "[[plant]] tables and nothing else"),
    (WRONG.replace('"wrong"', '"soft"'), "plant 1 (wipe-a): kind is wrong or vague"),
    (WRONG.replace("item =", "wrong ="), "a wrong plant has the keys answer, case, item, kind"),
    (WRONG + 'note = "why"', "a wrong plant has the keys answer, case, item, kind"),
    (VAGUE + 'item = "names a healer"', "a vague plant has the keys answer, case, kind"),
    (VAGUE.replace('"The raid took too much avoidable damage. Tighten up the mechanics."', '" "'),
     "every key is a text that says something"),
    (WRONG.replace('"wipe-a"', "13"), "plant 1: every key is a text that says something"),
    (VAGUE.replace("too much", "310k of"), "a vague plant states no number (310k)"),
    (VAGUE.replace("The raid", "At 2:32 the raid"), "a vague plant states no number (2:32)"),
    (WRONG + WRONG, "plant 2 (wipe-a): the case has this plant twice"),
    (VAGUE + VAGUE.replace("Tighten up", "Sharpen"), "plant 2 (wipe-a): the case has this plant twice"),
])
def test_a_malformed_file_is_refused(tmp_path, text, why):
    with pytest.raises(SystemExit) as refused:
        written(tmp_path, text)
    assert why in str(refused.value) and str(refused.value).startswith("planted.toml: ")


def test_a_case_may_have_a_wrong_plant_for_more_than_one_item(tmp_path):
    second = WRONG.replace("calls it an enrage, or says the raid needs more damage", "names a healer")
    assert [p.item for p in written(tmp_path, WRONG + second)] == [
        "calls it an enrage, or says the raid needs more damage", "names a healer"]


def test_an_item_wrapped_over_lines_is_its_cases_item_all_the_same(tmp_path):
    wrapped = WRONG.replace('"calls it an enrage, or says the raid needs more damage"',
                            '"""calls it an enrage,\n    or says the raid   needs more damage"""')
    plants = written(tmp_path, wrapped)
    cases = [CASES[0] | {"wrong": ["calls it an enrage, or says the\n         raid needs more damage"]}]
    assert stale(plants, cases) == [] and stale(plants, CASES) == []


def test_a_plant_whose_case_is_gone_or_no_longer_lists_its_item_is_stale(tmp_path):
    plants = written(tmp_path, WRONG + VAGUE)
    assert stale(plants, CASES) == []
    gone = stale(plants, CASES[1:])
    assert len(gone) == 2 and gone[0].startswith("wipe-a: its wrong plant is stale, the case is gone: drop the plant")
    assert gone[1].startswith("wipe-a: its vague plant is stale, the case is gone")
    reworded = [CASES[0] | {"wrong": ["calls it an enrage", "names a healer"]}]
    assert stale(plants, reworded) == [  # the vague plant states no item: it stands
        "wipe-a: its wrong plant is stale, the case no longer lists \"calls it an enrage, or says the raid needs more "
        "damage\": rewrite the plant to state an item of the case's wrong list as it is now, and name that item"]


def test_a_held_out_case_gets_no_plant(tmp_path):
    plants = written(tmp_path, VAGUE.replace("wipe-a", "held-c"))
    assert stale(plants, CASES) == ["held-c: a held-out case gets no plant (what it expects stands in heldout.toml "
                                    "alone): drop the plant"]


def test_the_regular_cases_without_a_wrong_plant_are_named(tmp_path):
    assert unplanted(written(tmp_path, WRONG), CASES) == ["night-b"]  # not the held-out one
    assert unplanted(written(tmp_path, VAGUE), CASES) == ["wipe-a", "night-b"]  # a vague plant is not its wrong one


def test_the_lint_counts_the_plants_and_says_what_to_do_with_a_stale_one():
    plants = [Plant("wipe-a", "wrong", "x", "names a healer")]
    assert planted.lint_line(plants, []) == "1 plant, none stale"
    assert planted.lint_line(plants * 2, ["wipe-a: ..."]) == "2 plants, 1 stale: rewrite them (evals/planted.toml)"


async def test_the_lint_fails_on_a_stale_plant_and_names_it(tmp_path, capsys):
    from evals.run import _lint_then_drain
    path = tmp_path / "planted.toml"
    path.write_text(WRONG.replace("wipe-a", "wipe-was-a-reset"), encoding="utf-8")
    assert await _lint_then_drain("", tmp_path, path) == 1  # no run to replay: the plants are checked all the same
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("wipe-was-a-reset: its wrong plant is stale, the case no longer lists \"calls it an ")
    assert out[2] == "1 plant, 1 stale: rewrite them (evals/planted.toml)"
    assert await _lint_then_drain("night", tmp_path, path) == 1  # -k picks the plants as it picks the cases
    assert capsys.readouterr().out.splitlines()[1] == "0 plants, none stale"


# ---- the plants in the file ----

def test_the_files_plants_load_and_none_is_stale():
    assert stale(load_plants(), load_cases()) == []


def test_each_plant_is_in_the_language_of_its_question():
    """A plant answers as the bot would: a Norwegian question gets a Norwegian plant (checks.languages, as a run
    holds an answer to it). Where a language doesn't show, nothing is said."""
    cases = {c["id"]: c for c in load_cases()}
    other = []
    for p in load_plants():
        case = cases[p.case]
        found = checks.languages(case, p.answer, "plugin" if case.get("client_only") else "bot")
        if found and found[0] != found[1]:
            other.append(f"{p.case} ({p.kind}): asked in {found[0]}, written in {found[1]}")
    assert other == []


# ---- --planted: the judge asked about the plants, faked here ----

# Two plants on real cases, so the question and the prompts are the cases' own.
TWO = """[[plant]]
case = "wipe-was-a-reset"
kind = "wrong"
item = "analyses pull 9's deaths as if it were a real attempt"
answer = "Pull 9 was lost to the opening Mutilate."

[[plant]]
case = "wipe-adds"
kind = "vague"
answer = "The raid took too much avoidable damage. Tighten up the mechanics."
"""


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """`--planted` on TWO with `judge` in place of the majority rule: (its exit code, what it printed, the saved
    file or None, the prompts the judge got)."""
    async def run(judge, text: str = TWO, only: str = ""):
        prompts: list[str] = []

        async def majority(prompt: str, model: str) -> checks.Verdict:
            prompts.append(prompt)
            print("the judge is asked")
            return judge(prompt)
        monkeypatch.setattr(checks, "judge_majority", majority)
        path = tmp_path / "planted.toml"
        path.write_text(text, encoding="utf-8")
        code = await planted.planted("sonnet", only, path, tmp_path / "results", "your Claude Code login")
        saved = sorted((tmp_path / "results").glob("planted-*.json"))
        return code, capsys.readouterr().out, json.loads(saved[-1].read_text("utf-8")) if saved else None, prompts
    return run


def fails(prompt: str) -> checks.Verdict:
    return checks.Verdict(False, "It states the item (2 of 2 judges failed it)", 0.02, [False, False])


def passes(prompt: str) -> checks.Verdict:
    return checks.Verdict(True, "It states none of them", 0.01, [True])


async def test_a_judge_that_fails_every_plant_catches_them_all(run):
    code, out, saved, _ = await run(fails)
    assert code == 0 and "let through" not in out and "could not be judged" not in out
    assert "wrong: the judge failed 1 of 1 plant\nvague: the judge failed 1 of 1 plant\n" in out
    assert out.rstrip().endswith("(judge sonnet, 4 votes, $0.04)")
    assert saved["planted"] == {"wrong": {"caught": 1, "let_through": 0, "not_judged": 0},
                                "vague": {"caught": 1, "let_through": 0, "not_judged": 0}}
    assert (saved["votes"], saved["cost_usd"], saved["filter"]) == (4, 0.04, "")
    assert saved["judge"]["model"] == "sonnet" and saved["billed_to"] == "your Claude Code login"
    wrong, vague = saved["plants"]  # in the file's order
    assert wrong == {"case": "wipe-was-a-reset", "kind": "wrong", "check": "wrong",
                     "item": "analyses pull 9's deaths as if it were a real attempt",
                     "plant": "Pull 9 was lost to the opening Mutilate.",
                     "question": "Asked in #raid (a server channel): Why did we wipe on pull 9?",
                     "case_hash": wrong["case_hash"], "verdict": "caught", "votes": [False, False],
                     "reason": "It states the item (2 of 2 judges failed it)", "cost_usd": 0.02}
    assert (vague["case"], vague["kind"], vague["check"], vague["item"]) == ("wipe-adds", "vague", "expect", "")


async def test_a_judge_that_passes_every_plant_lets_them_through_and_the_run_still_exits_zero(run):
    code, out, saved, _ = await run(passes)
    assert code == 0  # a plant let through is a finding to read, not a gate
    assert "wrong: the judge failed 0 of 1 plant\nvague: the judge failed 0 of 1 plant\n" in out
    assert "\n2 let through: the judge passed an answer written to fail. Read each one" in out
    assert ("  wipe-was-a-reset, wrong: \"analyses pull 9's deaths as if it were a real attempt\"\n"
            "    the plant: Pull 9 was lost to the opening Mutilate.\n"
            "    the judge: It states none of them\n"
            "  wipe-adds, vague\n"
            "    the plant: The raid took too much avoidable damage. Tighten up the mechanics.\n") in out
    assert [p["verdict"] for p in saved["plants"]] == ["let through", "let through"]
    assert saved["planted"]["wrong"] == {"caught": 0, "let_through": 1, "not_judged": 0}


async def test_a_judge_that_cannot_tell_leaves_its_plant_unjudged_and_the_run_exits_non_zero(run):
    code, out, saved, _ = await run(lambda prompt: checks.Verdict(None, "no verdict: Hmm", 0.03, [None])
                                    if "They are wrong." in prompt else fails(prompt))
    assert code == 1 and out.rstrip().endswith("Exits non-zero: 1 plant could not be judged.")
    assert "wrong: the judge failed 0 of 0 plants (1 more could not be judged)\n" in out  # in no count
    assert "vague: the judge failed 1 of 1 plant\n" in out
    assert "\n1 could not be judged (in no count):\n  wipe-was-a-reset, wrong: " in out
    assert "    the judge: no verdict: Hmm\n" in out
    assert [p["verdict"] for p in saved["plants"]] == ["not judged", "caught"]


async def test_a_judge_that_errors_leaves_its_plant_unjudged_and_the_others_are_judged_and_saved(run):
    def judge(prompt: str) -> checks.Verdict:
        if "They are wrong." in prompt:
            raise RuntimeError("usage limit")
        return fails(prompt)
    code, out, saved, _ = await run(judge)
    assert code == 1 and "Exits non-zero: 1 plant could not be judged." in out
    wrong, vague = saved["plants"]
    assert (wrong["verdict"], wrong["reason"], wrong["votes"], wrong["cost_usd"]) == (
        "not judged", "the judge failed: RuntimeError: usage limit", [], 0.0)
    assert vague["verdict"] == "caught"


async def test_the_judge_is_asked_as_a_run_asks_it_with_the_cases_own_question_and_lists(run):
    from evals.run import asked
    cases = {c["id"]: c for c in load_cases()}
    _, _, saved, prompts = await run(fails)
    reset, adds = cases["wipe-was-a-reset"], cases["wipe-adds"]
    assert sorted(prompts) == sorted([
        checks.wrong_prompt(asked(reset), reset["wrong"], "Pull 9 was lost to the opening Mutilate."),
        checks.expect_prompt(asked(adds), adds["expect"],
                             "The raid took too much avoidable damage. Tighten up the mechanics.")])
    assert [p["question"] for p in saved["plants"]] == [asked(reset), asked(adds)]


async def test_a_case_only_a_client_asks_is_asked_about_as_in_a_client(run):
    plant = TWO.split("\n\n")[1].replace("wipe-adds", "officer-saves-note")
    _, _, saved, prompts = await run(fails, plant)
    assert "in their own chat on claude.ai: " in saved["plants"][0]["question"] and "in #raid" not in prompts[0]


async def test_the_real_majority_rule_decides_and_its_votes_and_cost_are_kept(tmp_path, monkeypatch, capsys):
    replies = ["States it.\nFAIL", "States none.\nPASS", "States it as the cause.\nFAIL"]

    async def ask(prompt: str, model: str) -> tuple[str, float]:
        return replies.pop(0), 0.01
    monkeypatch.setattr(checks, "_ask_judge", ask)
    path = tmp_path / "planted.toml"
    path.write_text(TWO.split("\n\n")[0], encoding="utf-8")
    assert await planted.planted("sonnet", "", path, tmp_path) == 0 and not replies
    saved = json.loads(next(tmp_path.glob("planted-*.json")).read_text("utf-8"))
    assert saved["plants"][0]["votes"] == [False, True, False] and saved["plants"][0]["verdict"] == "caught"
    assert saved["plants"][0]["reason"] == "States it (2 of 3 judges failed it)"
    assert (saved["votes"], saved["cost_usd"]) == (3, 0.03)


async def test_the_cost_is_said_before_the_judge_is_asked(run):
    _, out, _, _ = await run(fails)
    said = ("2 plants (1 wrong, 1 vague), judged by sonnet: 2 to 6 judge calls, 4 if it fails every plant twice "
            "running. About $0.04 at 1 cent a call, at most $0.06. Billed to your Claude Code login.\n")
    assert out.startswith(said) and out.index(said) < out.index("the judge is asked")


def test_the_estimate_counts_three_votes_a_plant_at_most():
    plants = [Plant("a", "wrong", "x", "y")] * 110 + [Plant("a", "vague", "x")] * 20
    assert planted.estimate(plants, "sonnet") == (
        "130 plants (110 wrong, 20 vague), judged by sonnet: 130 to 390 judge calls, 260 if it fails every plant "
        "twice running. About $2.60 at 1 cent a call, at most $3.90.")


async def test_a_stale_plant_stops_the_run_before_the_judge_is_asked(run):
    code, out, saved, prompts = await run(fails, TWO.replace("analyses pull 9's deaths", "counts pull 9's deaths"))
    assert code == 1 and prompts == [] and saved is None
    assert out.splitlines()[0].startswith("wipe-was-a-reset: its wrong plant is stale, the case no longer lists ")
    assert out.splitlines()[1] == ("Exits non-zero: stale plants (1 of the 2 plants picked), so none was asked. "
                                   "Rewrite them first (planted.toml).")


async def test_k_picks_the_plants_by_their_case_and_a_run_that_picks_none_fails(run):
    code, out, saved, prompts = await run(fails, only="wipe-adds")
    assert code == 0 and len(prompts) == 1 and [p["case"] for p in saved["plants"]] == ["wipe-adds"]
    assert saved["filter"] == "wipe-adds" and out.startswith("1 plant (1 vague), judged by sonnet: 1 to 3 judge")
    code, out, _, prompts = await run(fails, only="no-such-case")
    assert code == 1 and prompts == [] and out == "Exits non-zero: no plant is picked by -k no-such-case " \
                                                  "(planted.toml).\n"


async def test_the_saved_file_is_read_past_by_the_lint_and_the_coverage_report(run, tmp_path):
    from evals.coverage import latest_runs
    from evals.lint import recorded_calls
    _, _, saved, _ = await run(fails)
    assert recorded_calls([saved], "wipe-adds") == []
    assert latest_runs(tmp_path / "results", load_cases()) == {}


def test_the_flag_asks_with_the_judges_model_and_the_cases_k_picks(monkeypatch):
    import sys

    import evals.run as runner
    got = {}

    async def asked(judge_model: str, only: str = "", **more) -> int:
        got.update(judge_model=judge_model, only=only, **more)
        return 1
    monkeypatch.setattr(planted, "planted", asked)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["evals.run", "--planted", "-k", "wipe,night", "--judge-model", "haiku"])
    with pytest.raises(SystemExit) as done:
        runner.main()
    assert done.value.code == 1  # the run's own exit code
    assert got == {"judge_model": "haiku", "only": "wipe,night", "billed_to": "your Claude Code login"}
