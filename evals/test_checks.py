"""How the judge's replies are read (evals/checks.py), with canned replies in place of the model."""
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
    queue.append("Can't tell from this.\nUNKNOWN")
    assert (await checks.judge("p", "sonnet")).ok is None


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


async def test_a_fail_is_judged_twice_more_and_the_majority_kept(replies):
    queue, asked = replies
    queue.extend(["Misses the add.\nFAIL", "It's there.\nPASS", "Names the adds.\nPASS"])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is True and len(asked) == checks.FAIL_VOTES
    assert v.reason == "It's there (1 of 3 judges failed it)" and v.cost_usd == pytest.approx(0.03)
    queue.extend(["Misses the add.\nFAIL", "It's there.\nPASS", "Wrong boss.\nFAIL"])
    v = await checks.judge_majority("p", "sonnet")
    assert v.ok is False and v.reason == "Misses the add (2 of 3 judges failed it)"


async def test_undecided_votes_leave_a_fail_standing(replies):
    queue, _ = replies
    queue.extend(["Misses it.\nFAIL", "Hmm.\nUNKNOWN", "Hmm.\nUNKNOWN"])
    assert (await checks.judge_majority("p", "sonnet")).ok is False


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


async def test_grounding_lists_contradicted_and_unsupported_claims_and_shows_the_judge_the_outputs(replies):
    queue, asked = replies
    queue.append("CONTRADICTED: \"pull 3 ended at 3:50\" – it ended at 4:14\n"
                 "UNSUPPORTED: \"the healers caused it\" – nothing shows that\nNOT GROUNDED")
    calls = [{"name": "pull_summary", "args": {"pull": 3}, "result": "Pull ended at 4:14."}]
    g = await checks.grounded("Why?", "It ended at 3:50; the healers caused it.", calls, "sonnet")
    assert g.ok is False and g.contradicted == ["\"pull 3 ended at 3:50\" – it ended at 4:14"]
    assert g.unsupported == ["\"the healers caused it\" – nothing shows that"]
    assert '[pull_summary {"pull": 3}]\nPull ended at 4:14.' in asked[0]
    queue.append("Every claim is in the outputs.\nGROUNDED")
    assert (await checks.grounded("Why?", "An enrage.", calls, "sonnet")).ok is True


async def test_each_rubric_rule_is_its_own_question_and_n_a_counts_as_none(replies):
    queue, asked = replies
    queue.extend(["It opens with the enrage.\nYES", "No change is given.\nN/A", "Nothing to count.\nN/A"])
    scores, cost = await checks.rubric("Why?", "The boss enraged.", "sonnet")
    assert scores == {"verdict_first": True, "concrete_fix": None, "few_changes": None}
    assert len(asked) == len(checks.RUBRIC) and cost == pytest.approx(0.03)
