"""One answer from the question to its verdict (evals.run.trial), with a made-up session and judge: no model."""
import asyncio
import time
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

import pytest

from evals import run
from evals.checks import Verdict
from wipebot.core import wcl
from wipebot.discord.host import Answer, ModelError, ToolCall

CASE = {"id": "a", "report": "R", "question": "Why did we wipe?", "expect": "An enrage.", "wrong": ["deaths"],
        "kind": "wipe"}


def stand_in(**has) -> Any:
    """An object with only what the code under test reads of a Library or a WCL."""
    return SimpleNamespace(**has)


@pytest.fixture
def asking(monkeypatch):
    """`trial()` with a session that answers from `answers` in turn (an exception is raised) and a judge whose
    verdicts are `verdicts` (expect, wrong)."""
    answers: list = []
    verdicts = {"expect": True, "wrong": True}

    class Session:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

        async def ask(self, question, ctx):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    async def judge(prompt: str, model: str) -> Verdict:
        return Verdict(verdicts["wrong" if "They are wrong." in prompt else "expect"], "because", 0.01)

    async def report(code):
        return SimpleNamespace(guild_id=1)
    monkeypatch.setattr(run, "Session", Session)
    monkeypatch.setattr(run, "judge_majority", judge)

    async def ask(case=CASE, mode="bot"):
        return await run.trial(case, stand_in(report=report), "opus", "sonnet", asyncio.Semaphore(1), mode)
    return answers, verdicts, ask


def call(result="Ended by the enrage at 6:00.", failed=False, **more) -> ToolCall:
    return ToolCall("pull_summary", {"pull": 3}, result, failed, **more)


async def test_an_answer_passes_when_the_judge_passes_both_checks(asking):
    answers, verdicts, ask = asking
    answers.append(Answer("The boss enraged.", 0.10, [call()]))
    t = await ask()
    assert t.passed and not t.errored and not t.broke and t.answer == "The boss enraged."
    assert t.cost_usd == pytest.approx(0.12) and [c["name"] for c in t.calls] == ["pull_summary"]
    answers.append(Answer("The tank died.", 0.10, [call()]))
    verdicts["wrong"] = False
    t = await ask()
    assert not t.passed and not t.errored and not t.broke  # a wrong answer


async def test_a_follow_up_keeps_its_earlier_answers_for_the_reader_and_no_judge_sees_them(asking, monkeypatch):
    answers, _, ask = asking
    judged = []

    async def judge(prompt: str, model: str) -> Verdict:
        judged.append(prompt)
        return Verdict(True, "because", 0.01)
    monkeypatch.setattr(run, "judge_majority", judge)
    case = CASE | {"question": ["Why did we wipe?", "But would a Barkskin have saved them?"]}
    answers.extend([Answer("15 of 21 died to the enrage.", 0.10, [call()]), Answer("No: it was the enrage.", 0.10)])
    t = await ask(case)
    assert t.passed and t.answer == "No: it was the enrage." and t.earlier == ["15 of 21 died to the enrage."]
    assert len(judged) == 2 and not any("15 of 21" in prompt for prompt in judged)
    assert "15 of 21" not in run.asked(case)
    assert run.Trial(**asdict(t)).earlier == t.earlier  # in the run's file, beside the answer
    answers.append(Answer("The boss enraged.", 0.10, [call()]))
    assert (await ask()).earlier == []  # one question: nothing before it


async def test_a_crashed_tool_fails_the_answer_the_judge_passed(asking):
    answers, _, ask = asking
    answers.append(Answer("The boss enraged.", 0.10, [
        call(), call("pull_summary failed with an unexpected error: TypeError", failed=True)]))
    t = await ask()
    assert not t.passed and not t.errored and t.broke.startswith("a tool crashed: pull_summary")
    assert t.expect["ok"] is True  # the judge's verdict is kept beside it


async def test_an_answer_that_hit_the_bots_cap_fails_and_keeps_what_it_got_to(asking):
    answers, _, ask = asking
    answers.append(ModelError("error_max_turns", Answer("", 0.90, [call()])))
    t = await ask()
    assert not t.passed and not t.errored and "error_max_turns" in t.broke
    assert t.cost_usd == pytest.approx(0.90) and len(t.calls) == 1 and not t.expect  # never judged


async def test_a_usage_limit_and_refused_keys_are_errors(asking):
    answers, _, ask = asking
    answers.append(ModelError("rate_limit"))
    t = await ask()
    assert t.errored and t.error == "ModelError: rate_limit" and not t.broke and not t.passed
    answers.append(Answer("I couldn't read the log.", 0.10, [call("auth_failed: HTTP 401", failed=True)]))
    t = await ask()
    assert t.errored and "auth_failed" in t.error and not t.passed  # judged, but this machine's keys, not the answer


async def test_the_wait_for_the_next_hours_points_is_timed_apart(asking, monkeypatch):
    answers, _, ask = asking
    now = time.monotonic()
    monkeypatch.setattr(run, "WAITS", [(now + 10, now + 1510), (now + 5000, now + 6000)])
    answers.append(Answer("The boss enraged.", 0.10, [call(started=now, duration_s=1520),
                                                      call(started=now + 20, duration_s=5)]))
    assert (await ask()).waited_s == 1500  # the first wait, as the call that sat through it; the second was no call's
    monkeypatch.setattr(run, "WAITS", None)
    answers.append(Answer("The boss enraged.", 0.10, [call()]))
    assert (await ask()).waited_s is None  # not timed: no answer fails on its seconds


def test_the_wait_the_evals_time_is_still_where_wcl_makes_it(monkeypatch):
    """time_waits wraps wcl.WCL._wait_for_points, the wait before each query (keep_for_others); keys_work asks
    through WCL._run. Renamed, an answer's wait would go untimed without a word."""
    assert callable(getattr(wcl.WCL, "_wait_for_points", None)) and callable(getattr(wcl.WCL, "_run", None))
    waited = []
    monkeypatch.setattr(wcl.WCL, "_wait_for_points", lambda self: waited.append(time.sleep(0)))
    monkeypatch.setattr(run, "WAITS", None)
    monkeypatch.setattr(time, "monotonic", iter([100.0, 1600.0, 1600.0]).__next__)
    run.time_waits()
    wcl.WCL._wait_for_points(stand_in())
    assert waited and run.WAITS == [(100.0, 1600.0)]


async def test_a_run_stops_at_its_start_when_warcraft_logs_refuses_the_keys(capsys):
    def refused(query, variables):
        raise wcl.WCLError("auth_failed: HTTP 401")

    def down(query, variables):
        raise wcl.WCLError("upstream_error: HTTP 503")
    with pytest.raises(SystemExit, match="refused this machine's keys"):
        await run.keys_work(stand_in(wcl=stand_in(_run=refused)))
    await run.keys_work(stand_in(wcl=stand_in(_run=down)))  # down for now: said, and the run goes on
    assert "didn't answer" in capsys.readouterr().out
    await run.keys_work(stand_in(wcl=stand_in(_run=lambda q, v: {})))
    assert capsys.readouterr().out == ""


def test_a_question_is_dated_as_the_recorded_data_is(monkeypatch):
    """The bot's prompt takes today's date; a run's questions carry the frozen clock's, whenever it is run."""
    from wipebot.discord import prompt
    from wipebot.discord.host import Context
    code = prompt.message.__code__
    assert "now" in code.co_varnames[:code.co_argcount]  # what freeze_today sets
    monkeypatch.setattr(prompt, "message", prompt.message)  # put back after
    run.freeze_today()
    run.freeze_today()  # once is enough
    assert "today is Tuesday 2026-10-13]" in prompt.message("How do I gem?", Context(where="in #raid"), None)


def test_a_run_records_what_it_tested_and_a_comparison_says_when_that_differs(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(run, "MODELS", {"claude-sonnet-x"})
    db = tmp_path / "cache.sqlite"
    with sqlite3.connect(db) as made:
        made.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
        made.executemany("INSERT INTO cache VALUES (?, ?, '')",
                         [("a", run.RECORDED_AT), ("b", run.RECORDED_AT + 90000)])
    monkeypatch.setattr(run, "CACHE", db)
    monkeypatch.delenv("WIPEBOT_EFFORT", raising=False)
    t = run.tested({"a": [run.Trial(model="claude-opus-x"), run.Trial(model="claude-opus-x"), run.Trial()]})
    assert (t["answers"], t["judge"], t["effort"], t["data"]) == (["claude-opus-x"], ["claude-sonnet-x"], "",
                                                                 "2026-10-14 16:16")
    assert t["sdk"][0].isdigit() and t["cli"][0].isdigit() and isinstance(t["uncommitted"], bool)
    now = {"commit": "abc", "tested": t}
    assert run.tested_line(now).startswith("Tested: answers by claude-opus-x; judged by claude-sonnet-x; Agent SDK ")
    assert run.tested_alike({"tested": t}, now) == "" and run.tested_alike({}, {"commit": "abc"}) == ""
    assert run.tested_alike({}, now).startswith("  note: the baseline is older than the record of what a run tested")
    older = t | {"answers": ["claude-opus-w"], "cli": "2.0.0", "data": "2026-10-13 15:16"}
    assert run.tested_alike({"tested": older}, now) == (
        "  WARNING: not tested alike: the answers' model claude-opus-w -> claude-opus-x; Claude Code 2.0.0 -> "
        f"{t['cli']}; frozen data last added to 2026-10-13 15:16 -> 2026-10-14 16:16")


async def test_a_report_that_cannot_be_read_is_an_error_not_a_crash_of_the_run(asking, monkeypatch):
    """What fails before the session starts (the report's read, a post's prefetch) is caught like the rest."""
    _, _, ask = asking

    async def unread(code):
        raise wcl.WCLError("upstream_error: Warcraft Logs returned HTTP 503")

    async def trial_of(report):
        return await run.trial(CASE, stand_in(report=report), "opus", "sonnet", asyncio.Semaphore(1), "bot")
    t = await trial_of(unread)
    assert t.errored and "HTTP 503" in t.error and not t.passed and t.calls == [] and not t.broke


def test_a_saved_trial_reads_the_same_a_second_time():
    """--summary, --record and --save-baseline RESULTS settle a saved run again: a capped answer stays a failed
    answer, and nothing else changes sides either."""
    ok, case = {"ok": True, "reason": ""}, {"id": "a", "question": "why?", "kind": "wipe"}
    crash = {"name": "progress", "failed": True, "result": "progress failed with an unexpected error: TypeError"}
    refused = {"name": "progress", "failed": True, "result": "auth_failed: HTTP 401"}
    for trial in (run.Trial(expect=ok, wrong=ok), run.Trial(expect=ok, wrong={"ok": False, "reason": "x"}),
                  run.Trial(error="ModelError: error_max_turns"), run.Trial(error="ModelError: error_max_budget_usd"),
                  run.Trial(error="ModelError: rate_limit"), run.Trial(expect=ok, wrong=ok, calls=[crash]),
                  run.Trial(expect=ok, wrong=ok, seconds=4000, waited_s=0.0),
                  run.Trial(expect=ok, wrong=ok, calls=[refused]),
                  run.Trial(expect=ok, wrong={"ok": None, "reason": "no verdict: "})):
        first = asdict(run.settle(trial, case, "bot"))
        assert asdict(run.settle(run.Trial(**first), case, "bot")) == first
