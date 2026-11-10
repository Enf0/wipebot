"""The checks code makes of an answer for the cases that ask (a case's `numbers` and `form`): named parts of a pass
beside the judge's two and the required calls, with no model."""
import pytest

from evals.checks import coded, coded_keys, states
from evals.run import FIRST_PARTS, Trial, case_hash, compare, judged_by, settle, tally_of

CASE = {"id": "a", "report": "R", "kind": "wipe", "question": "Why did we wipe?", "wrong": ["a reset"],
        "expect": "The boss enraged at 2:32 with 16 of 20 alive, after 163.64k of healing."}
OK = {"ok": True, "reason": ""}
SHORT = ("**The boss enraged at 2:32.**\n- **Pop the stack.** It needs 14 players.\n- **Stay alive.** Two died early.\n"
         "Follow-up: Who missed the stack at 1:17?")
LONG = SHORT.replace("It needs 14 players.", "It needs 14 players. Sixteen were alive. So it was not the deaths.")


def test_a_number_is_stated_as_the_lint_reads_one():
    assert states("2:32", "enraged at 02:32") and states("2:32", "enraged at **2:32**: fallout after")
    assert not states("2:32", "enraged at 2:33") and not states("2:32", "the kill took 1:02:32")
    assert states("164k", "healed 163.64k") and states("34%", "left at 34.1%") and not states("164k", "healed 150k")
    assert states("16", "16 of 20 alive") and not states("16", "died at 2:16") and not states("16", "took 16.4k")
    assert states("16 of 20", "16 alive of the 20") and not states("16 of 20", "16 alive")  # every number of it
    # what a raider reads: not a link's address, not the question a follow-up button offers
    assert not states("39", "[pull 4](https://www.warcraftlogs.com/reports/R#fight=39) was lost")
    assert not states("1:17", SHORT) and states("2:32", SHORT)


def test_numbers_fail_an_answer_that_leaves_one_out_and_take_either_way_a_moment_is_printed():
    case = CASE | {"numbers": ["2:32", "16 or 17"]}
    assert coded(case, "Enraged at 2:32 with 17 alive.", "plugin") == {"numbers": ""}
    assert coded(case, "Enraged at 2:33 with 15 alive.", "bot") == {"numbers": "doesn't state 2:32, 16 or 17"}
    assert coded(CASE, "Enraged.", "bot") == {}  # a case that doesn't ask is not checked


def test_form_holds_the_bots_answer_to_what_the_case_asks_and_no_clients():
    case = CASE | {"form": {"bullets": 3, "bullet_sentences": 1}}
    assert coded(case, SHORT, "bot") == {"form": ""}
    assert coded(case, LONG, "bot") == {"form": "1 of 2 bullets over 1 sentence after the bold point"}
    assert coded(case, LONG, "plugin") == {}  # in a client the form is the client's own
    post = CASE | {"form": {"lines": 4, "chars": 60}}  # about 60: a quarter over is still about
    assert coded(post, "Enraged at 2:32.\n" + "x" * 55, "bot") == {"form": ""}
    assert coded(post, "\n".join("line" for _ in range(5)), "bot")["form"] == "5 lines (at most 4)"
    assert coded(post, "y" * 90, "bot")["form"] == "90 characters to read (about 60)"


def test_a_case_asks_only_for_numbers_its_expect_states_and_limits_the_form_measures():
    assert coded_keys(CASE | {"numbers": ["2:32", "16 or 17"], "form": {"bullets": 3}}) == ""
    assert coded_keys(CASE | {"numbers": ["2:33"]}) == "numbers: its expect doesn't state 2:33"
    assert coded_keys(CASE | {"numbers": ["the enrage"]}) == 'numbers: "the enrage" holds no number'
    assert coded_keys(CASE | {"form": {"paragraphs": 2}}).startswith("form: unknown limits paragraphs")
    assert coded_keys(CASE | {"form": {}}).startswith("form: each limit is a whole number above 0")


def test_the_loader_refuses_a_case_whose_code_check_cannot_be_made(tmp_path):
    from evals.run import load_cases
    (tmp_path / "wipes.toml").write_text(
        '[defaults]\nreport = "R"\n[[case]]\nid = "a"\nquestion = "why?"\nexpect = "The enrage at 2:32."\n'
        'wrong = ["a reset"]\nnumbers = ["2:32"]\nform = {bullets = 3}\n')
    assert load_cases(tmp_path)[0]["numbers"] == ["2:32"]
    (tmp_path / "wipes.toml").write_text((tmp_path / "wipes.toml").read_text().replace('["2:32"]', '["2:15"]'))
    with pytest.raises(SystemExit, match="case a \\(wipes.toml\\): numbers: its expect doesn't state 2:15"):
        load_cases(tmp_path)


def test_a_code_check_is_a_named_part_of_a_pass(capsys):
    from evals.run import show
    case = CASE | {"numbers": ["2:32"]}
    right = settle(Trial(answer="It enraged at 2:32.", expect=OK, wrong=OK), case, "plugin")
    off = settle(Trial(answer="It enraged at 2:35.", expect=OK, wrong=OK), case, "plugin")  # the judge let it through
    assert right.passed and right.checks == {"expect": True, "wrong": True, "calls": True, "numbers": True}
    assert not off.passed and off.checks["numbers"] is False and off.coded == {"numbers": "doesn't state 2:32"}
    show("a", off)
    assert "\n    numbers: doesn't state 2:32" in capsys.readouterr().out
    unjudged = settle(Trial(answer="It enraged at 2:35.", error="RuntimeError: a usage limit"), case, "plugin")
    assert unjudged.errored and unjudged.checks == {}  # an answer the judge never saw is in no count
    now = tally_of([case], {"a": [right, off]})
    assert now["parts"] == [*FIRST_PARTS, "numbers"] and now["passed"] == {"a": 1} and now["failed"] == {
        "a": [["numbers"]]}
    assert tally_of([CASE], {"a": [right]})["parts"] == list(FIRST_PARTS)  # no case asks: no such check was made
    # against a baseline from before the check, the answer counts as it would have then
    old = {"model": "opus", "commit": "abc", "trials": 2, "judge": judged_by("sonnet"), "errors": {},
           "case_hashes": {"a": case_hash(case)}, "passed": {"a": 2}}
    text = "\n".join(compare(old, now | {"model": "opus", "commit": "def", "judge": judged_by("sonnet")}))
    assert "scored by the baseline's checks (expect, wrong, calls): this run's numbers is left out" in text
    assert "total 2 of 2" in text
