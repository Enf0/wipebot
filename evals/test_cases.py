from evals.run import load_cases


def test_the_cases_load():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert all(c["expect"].strip() and c["wrong"] for c in cases)
    assert all(isinstance(f, str) and f.strip() for c in cases for f in c.get("facts", []))
    assert {c["id"] for c in cases if c.get("client_only")} == {"officer-saves-note", "plan-spots-at-virulence",
                                                                  "plan-where-do-i-stand", "plan-who-stays-out",
                                                                  "night-kicks-order-changed", "report-wrong-answer"}


def test_a_broken_trial_is_an_error_not_a_wrong_answer():
    from evals.run import Trial, broken
    ok = {"ok": True, "reason": ""}
    assert broken(Trial(expect=ok, wrong=ok, calls=[{"name": "pull_summary", "failed": True,
                                                     "result": "give the boss's name"}])) == ""  # the model's doing
    assert "won't show report" in broken(Trial(expect=ok, wrong=ok, calls=[
        {"name": "pull_summary", "failed": True, "result": "Warcraft Logs won't show report X: it's private now"}]))
    assert "unexpected error" in broken(Trial(expect=ok, wrong=ok, calls=[
        {"name": "progress", "failed": True, "result": "progress failed with an unexpected error: TypeError"}]))
    assert "no verdict on wrong" in broken(Trial(expect=ok, wrong={"ok": None, "reason": "no verdict: "}))
    assert broken(Trial(error="ProcessError: limit")) == "ProcessError: limit"
    down = {"name": "pull_map", "failed": True, "result": "upstream_error: Warcraft Logs returned HTTP 503 for ..."}
    assert "HTTP 503" in broken(Trial(expect=ok, wrong={"ok": False, "reason": ""}, calls=[down]))
    assert broken(Trial(expect=ok, wrong=ok, passed=True, calls=[down])) == ""  # it got by without the call


def test_every_tool_has_an_eval_case():
    """A new tool comes with an eval case tagged with it (`tools`), or a line in NO_CASE_YET saying why not, as it
    comes with a snapshot case (tests/core/test_server.py). A tagged tool exists."""
    from evals.coverage import NO_CASE_YET, app_tools, core_tools
    tagged = {t for c in load_cases() for t in c.get("tools", [])}
    assert tagged <= core_tools() | app_tools()
    assert not core_tools() - tagged - set(NO_CASE_YET)
    assert not tagged & set(NO_CASE_YET)  # an entry there outlived its reason
