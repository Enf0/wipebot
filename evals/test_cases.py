from evals.run import load_cases


def test_the_cases_load():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert all(c["expect"].strip() and c["wrong"] for c in cases)
    assert all(isinstance(f, str) and f.strip() for c in cases for f in c.get("facts", []))
    assert {c["id"] for c in cases if c.get("client_only")} == {"officer-saves-note"}  # connector.toml's


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


def test_channel_only_wrong_items_are_on_channel_cases_and_left_out_with_the_connector():
    from evals.run import IN_CLAUDE, asked, wrongs
    cases = load_cases()
    assert all("where" not in c for c in cases if c.get("wrong_in_channel"))  # only the server channel has the rule
    case = next(c for c in cases if c["id"] == "night-raid-prep")
    assert wrongs(case) == case["wrong"] + case["wrong_in_channel"] and wrongs(case, True) == case["wrong"]
    assert "#raid" in asked(case) and IN_CLAUDE in asked(case, True)


def test_every_tool_has_an_eval_case():
    """A new tool comes with an eval case tagged with it (`tools`), or a line in NO_CASE_YET saying why not, as it
    comes with a snapshot case (tests/core/test_server.py). A tagged tool exists."""
    from evals.coverage import NO_CASE_YET, app_tools, core_tools
    tagged = {t for c in load_cases() for t in c.get("tools", [])}
    assert tagged <= core_tools() | app_tools()
    assert not core_tools() - tagged - set(NO_CASE_YET)
    assert not tagged & set(NO_CASE_YET)  # an entry there outlived its reason
