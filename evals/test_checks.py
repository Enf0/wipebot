"""How the judge's replies are read (evals/checks.py), with canned replies in place of the model."""
from itertools import product

import pytest

from evals import checks


@pytest.fixture
def replies(monkeypatch):
    """The judge answers with each of `replies` in turn (an exception is raised), at 0.01 $ each; no waiting."""
    queue: list = []
    asked: list[str] = []

    async def ask(prompt: str, model: str) -> tuple[str, float]:
        asked.append(prompt)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, 0.01
    monkeypatch.setattr(checks, "_ask_judge", ask)
    monkeypatch.setattr(checks, "JUDGE_WAIT_S", 0)
    return queue, asked


async def test_the_verdict_is_the_last_one_the_reasoning_may_quote_others(replies):
    queue, _ = replies
    queue.append("It would FAIL if it blamed the tank, but it names the enrage.\nPASS")
    v = await checks.judge("p", "sonnet")
    assert v.ok is True and "FAIL" not in v.reason and v.reason.startswith("It would")
    queue.append("The point is missing.\n**FAIL**")
    assert (await checks.judge("p", "sonnet")).ok is False


async def test_an_undecided_judge_is_asked_again_at_once(replies):
    queue, asked = replies
    queue.extend(["Can't tell from this.\nUNKNOWN", "It names the enrage.\nPASS"])
    assert (await checks.judge("p", "sonnet")).ok is True and len(asked) == 2
    queue.extend(["Can't tell from this.\nUNKNOWN"] * checks.JUDGE_TRIES)
    v = await checks.judge("p", "sonnet")
    assert v.ok is None and v.reason.startswith("Can't tell") and not queue  # undecided to the end: an error


async def test_a_reply_without_a_verdict_is_asked_again_then_left_undecided(replies):
    queue, asked = replies
    queue.extend([RuntimeError("usage limit"), "I think it's fine", "Still no word for it"])
    v = await checks.judge("p", "sonnet")
    assert len(asked) == checks.JUDGE_TRIES and v.ok is None and v.reason.startswith("no verdict: Still no word")
    assert v.cost_usd == pytest.approx(0.02)  # the failed call cost nothing
    queue.extend([RuntimeError("overloaded"), "Fine.\nPASS"])
    assert (await checks.judge("p", "sonnet")).ok is True


async def test_a_pass_is_kept_without_asking_again(replies):
    queue, asked = replies
    queue.append("Gets it across.\nPASS")
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is True and len(asked) == 1


async def test_a_fail_is_judged_again_and_the_majority_of_three_kept(replies):
    queue, asked = replies
    queue.extend(["Misses the add.\nFAIL", "It's there.\nPASS", "Names the adds.\nPASS"])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is True and len(asked) == checks.FAIL_VOTES and v.votes == [False, True, True]
    assert v.reason == "It's there (1 of 3 judges failed it)" and v.cost_usd == pytest.approx(0.03)
    queue.extend(["Misses the add.\nFAIL", "It's there.\nPASS", "Wrong boss.\nFAIL"])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is False and v.reason == "Misses the add (2 of 3 judges failed it)"


async def test_a_second_fail_settles_it_without_a_third_vote(replies):
    queue, asked = replies
    queue.extend(["Misses the add.\nFAIL", "Wrong boss.\nFAIL"])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is False and len(asked) == 2 and v.votes == [False, False]
    assert v.reason == "Misses the add (2 of 2 judges failed it)" and v.cost_usd == pytest.approx(0.02)


async def test_an_undecided_second_vote_leaves_the_fail_standing_unasked_further(replies):
    queue, asked = replies
    queue.extend(["Misses it.\nFAIL", *["Hmm.\nUNKNOWN"] * checks.JUDGE_TRIES])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is False and v.votes == [False, None] and len(asked) == 1 + checks.JUDGE_TRIES and not queue


# Each row: the votes the judge would give if asked three times (F fail, P pass, U couldn't tell), the verdict, and
# how many of them the rule asks. The verdict is the one of asking all three, as the rule did until 10 November: a
# pass or an undecided first vote is kept, whatever the other two would say; after a fail, the answer passes only
# when both other votes pass. All 27 combinations.
OTHER_TWO = ["".join(two) for two in product("PFU", repeat=2)]
VOTES = [*((f"P{two}", True, 1) for two in OTHER_TWO), *((f"U{two}", None, 1) for two in OTHER_TWO),
         ("FFF", False, 2), ("FFP", False, 2), ("FFU", False, 2),
         ("FPF", False, 3), ("FPP", True, 3), ("FPU", False, 3),
         ("FUF", False, 2), ("FUP", False, 2), ("FUU", False, 2)]


@pytest.mark.parametrize(("votes", "verdict", "asked"), VOTES)
async def test_the_majority_asks_only_the_votes_it_needs_and_rules_as_three_would(monkeypatch, votes, verdict,
                                                                                  asked):
    given = [{"P": True, "F": False, "U": None}[v] for v in votes]
    queue = [checks.Verdict(ok, f"vote {n}", 0.01) for n, ok in enumerate(given, 1)]

    async def judge(prompt: str, model: str) -> checks.Verdict:
        return queue.pop(0)
    monkeypatch.setattr(checks, "judge", judge)
    v = await checks.judge_majority("p", "sonnet")
    # all three asked at once, as before: the first vote unless it failed, else failed unless more passed than failed
    fails, passes = given.count(False), given.count(True)
    of_three = given[0] if given[0] is not False else fails < passes
    assert v.ok is verdict is of_three
    assert v.votes == given[:asked] and len(queue) == 3 - asked and v.cost_usd == pytest.approx(0.01 * asked)


def test_the_table_of_votes_holds_every_combination_once():
    assert sorted(votes for votes, _, _ in VOTES) == sorted("".join(three) for three in product("PFU", repeat=3))


async def test_facts_are_read_one_line_each_in_any_wording(replies):
    queue, asked = replies
    queue.append("1 YES\n2. no\n3: Yes")
    f = await checks.facts("why?", ["the enrage at 6:00", "Kraburn died first", "3 deaths"], "answer", "sonnet")
    assert f is not None and (f.stated, f.of, f.missing) == (2, 3, ["Kraburn died first"])
    assert "1. the enrage at 6:00\n2. Kraburn died first" in asked[0]


async def test_a_facts_reply_missing_a_line_is_asked_again_then_none(replies):
    queue, asked = replies
    queue.extend(["1 YES", "1 YES\n2 NO"])
    f = await checks.facts("why?", ["a", "b"], "answer", "sonnet")
    assert f is not None and (f.stated, f.missing) == (1, ["b"]) and len(asked) == 2
    queue.extend(["1 YES"] * checks.JUDGE_TRIES)
    assert await checks.facts("why?", ["a", "b"], "answer", "sonnet") is None


async def test_grounding_lists_misleading_claims_and_shows_the_judge_the_outputs(replies):
    queue, asked = replies
    queue.append("MISLEADING: \"pull 3 ended at 3:50\" – it ended at 4:14\n"
                 "MISLEADING: \"the healers caused it\" – nothing shows that\nNOT GROUNDED")
    calls = [{"name": "pull_summary", "args": {"pull": 3}, "result": "Pull ended at 4:14."}]
    g = await checks.grounded("Why?", "It ended at 3:50; the healers caused it.", calls, "sonnet")
    assert g.ok is False and g.misleading == ["\"pull 3 ended at 3:50\" – it ended at 4:14",
                                              "\"the healers caused it\" – nothing shows that"]
    assert '[pull_summary {"pull": 3}]\nPull ended at 4:14.' in asked[0]
    queue.append("Every claim is in the outputs.\nGROUNDED")
    assert (await checks.grounded("Why?", "An enrage.", calls, "sonnet")).ok is True


async def test_each_rubric_rule_is_its_own_question_and_n_a_counts_as_none(replies):
    queue, asked = replies
    queue.extend(["It opens with the enrage.\nYES", "No change is given.\nN/A", "Nothing to count.\nN/A"])
    scores, cost = await checks.rubric("Why?", "The boss enraged.", "sonnet")
    assert scores == {"verdict_first": True, "concrete_fix": None, "few_changes": None}
    assert len(asked) == len(checks.RUBRIC) and cost == pytest.approx(0.03)


def test_an_answers_form_is_held_to_what_its_case_is_and_only_as_the_bot():
    answer = "**The enrage ended it.**\n" + "\n".join(f"- point {n}" for n in range(4))
    measured, slips = checks.built({"kind": "wipes"}, answer, "bot")
    assert measured["bullet_words"] == [2, 2, 2, 2] and measured["verdict_words"] == 4 and slips == {}
    assert checks.built({"kind": "bot", "live": True}, answer, "bot")[1] == {"many bullets": "4 bullets (at most 3)"}
    assert checks.built({"kind": "posts"}, answer, "bot")[1] == {"many lines": "5 lines (at most 4)"}
    assert checks.built({"kind": "bot", "live": True}, answer, "plugin") == (measured, None)


def test_a_runs_forms_are_summed_up_by_the_middle_answer_and_the_kinds_of_slip():
    short = ({"words": 100, "bullet_words": [20, 30], "verdict_words": 10, "follow_ups": 2}, {})
    long = ({"words": 300, "bullet_words": [40, 50, 60], "verdict_words": 24, "follow_ups": 0},
            {"a long bullet": "3 of 3 bullets over 30 words (40, 50, 60)", "a long verdict": "x"})
    plain = ({"words": 12, "bullet_words": [], "verdict_words": 0, "follow_ups": 0}, {})
    assert checks.built_summary([short, long, long, plain]) == [
        "how the answers are built (the middle one of 4): 200 words, 2 bullets of 45 words, a verdict of 24 words, "
        "0 follow-ups",
        "2 of 4 leave the form the bot's prompt asks for: a long bullet 2, a long verdict 2"]
    assert checks.built_summary([(short[0], None)]) == [
        "how the answers are built (the middle one of 1): 100 words, 2 bullets of 25 words, a verdict of 10 words, "
        "2 follow-ups"]
    assert checks.built_summary([({}, None)]) == [] and checks.built_summary([]) == []
