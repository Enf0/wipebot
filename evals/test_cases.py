from evals.run import load_cases


def test_the_cases_load():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert all(c["expect"].strip() and c["wrong"] for c in cases)
    assert all(isinstance(f, str) and f.strip() for c in cases for f in c.get("facts", []))
    assert {c["id"] for c in cases if c.get("client_only")} == {"officer-saves-note", "plan-spots-at-virulence",
                                                                  "plan-where-do-i-stand", "plan-who-stays-out",
                                                                  "night-kicks-order-changed", "report-wrong-answer"}


def test_what_the_raider_would_have_met_fails_and_what_says_nothing_is_an_error():
    from evals.run import Trial, settle
    ok, case = {"ok": True, "reason": ""}, {"id": "a", "question": "why?", "kind": "wipe"}

    def settled(**trial) -> Trial:
        return settle(Trial(**trial), case, "bot")
    bad_argument = {"name": "pull_summary", "failed": True, "result": "give the boss's name"}  # the model's doing
    assert settled(expect=ok, wrong=ok, calls=[bad_argument]).passed
    crashed = settled(expect=ok, wrong=ok, calls=[
        {"name": "progress", "failed": True, "result": "progress failed with an unexpected error: TypeError"}])
    assert "a tool crashed: progress" in crashed.broke and not crashed.passed and not crashed.errored  # a failed answer
    capped = settled(error="ModelError: error_max_turns")
    assert "error_max_turns" in capped.broke and not capped.passed and not capped.errored
    limit = settled(error="ProcessError: limit")
    assert limit.errored and limit.error == "ProcessError: limit" and not limit.broke
    for unread in ("Warcraft Logs won't show report X: it's private now", "auth_failed: HTTP 401"):
        t = settled(expect=ok, wrong=ok, calls=[{"name": "pull_summary", "failed": True, "result": unread}])
        assert t.errored and unread[:20] in t.error and not t.passed  # this machine's data, not the answer
    undecided = settled(expect=ok, wrong={"ok": None, "reason": "no verdict: "})
    assert undecided.errored and "no verdict on wrong" in undecided.error
    down = {"name": "pull_map", "failed": True, "result": "upstream_error: Warcraft Logs returned HTTP 503 for ..."}
    assert "HTTP 503" in settled(expect=ok, wrong={"ok": False, "reason": ""}, calls=[down]).error
    assert settled(expect=ok, wrong=ok, calls=[down]).passed  # it got by without the call


def test_an_answer_slower_than_the_bot_waits_fails_on_its_own_time_only():
    from evals.run import CHAT_TIMEOUT_S, POST_TIMEOUT_S, Trial, settle
    ok, case = {"ok": True, "reason": ""}, {"id": "a", "question": ["why?", "and then?"], "kind": "wipe"}
    slow = 2 * CHAT_TIMEOUT_S + 10
    assert "the bot gives up after" in settle(Trial(expect=ok, wrong=ok, seconds=slow, waited_s=0.0), case, "bot").broke
    # the wait for the next hour's Warcraft Logs points is the eval's own, and left out
    assert settle(Trial(expect=ok, wrong=ok, seconds=slow + 1500, waited_s=1600.0), case, "bot").passed
    assert settle(Trial(expect=ok, wrong=ok, seconds=slow), case, "bot").passed  # not timed (an older run): no verdict
    assert settle(Trial(expect=ok, wrong=ok, seconds=slow, waited_s=0.0), case, "plugin").passed  # a client waits
    post = {"id": "p", "question": "what happened?", "kind": "posts"}
    assert settle(Trial(expect=ok, wrong=ok, seconds=POST_TIMEOUT_S + 1, waited_s=0.0), post, "bot").broke


def test_every_tool_has_an_eval_case():
    """A new tool comes with an eval case tagged with it (`tools`), or a line in NO_CASE_YET saying why not, as it
    comes with a snapshot case (tests/core/test_server.py). A tagged tool exists."""
    from evals.coverage import NO_CASE_YET, app_tools, core_tools
    tagged = {t for c in load_cases() for t in c.get("tools", [])}
    assert tagged <= core_tools() | app_tools()
    assert not core_tools() - tagged - set(NO_CASE_YET)
    assert not tagged & set(NO_CASE_YET)  # an entry there outlived its reason
