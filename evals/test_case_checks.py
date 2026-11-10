"""What the free checks say about the cases: the lint's reading of a time, a tool's output with a part missing, and
what the case hash covers."""
import json

from evals.lint import in_output, numbers


def test_a_time_that_ends_a_clause_is_read_and_one_inside_a_longer_clock_is_not():
    """buff_at_casts prints "- 5:27: 5 before": a colon after a time ends the clause, it doesn't make a longer clock."""
    line = "- 5:27: 5 before; gained 5, lost 5"
    assert in_output("5:27", line) and numbers(line)[0] == "5:27"
    assert not in_output("1:05", "the kill took 1:05:27") and not in_output("5:27", "the kill took 1:05:27")
    assert numbers("the kill took 1:05:27") == []


async def test_an_output_with_a_part_missing_for_lack_of_data_leaves_its_case_unchecked(tmp_path, capsys, monkeypatch):
    """compare_with_top prints its build and says its casts are "Not available: auth_failed": a number of the missing
    part is in no output, which says nothing about the case."""
    from evals.lint import lint
    from evals.run import case_hash
    from wipebot.core import server

    def compare_with_top(pull: int = 0) -> str: return ""

    async def call(name: str, args: dict) -> tuple[str, bool]:
        return "## Build\n73 of 73 picks\n## Casts\nNot available: auth_failed: Warcraft Logs returned HTTP 401", False
    monkeypatch.setattr(server, "configure", lambda: None)
    monkeypatch.setattr(server, "_TOOLS", [("compare_with_top", compare_with_top)])
    monkeypatch.setattr(server, "call", call)
    cases = [{"id": "casts", "question": "?", "expect": "Tiger Palm 7.1 a minute", "wrong": ["x"]},
             {"id": "build", "question": "?", "expect": "all 73 picks", "wrong": ["x"]}]
    (tmp_path / "run.json").write_text(json.dumps({
        "case_hashes": {c["id"]: case_hash(c) for c in cases},
        "cases": {c["id"]: [{"calls": [{"name": "compare_with_top", "args": {"pull": 1}, "failed": False}]}]
                  for c in cases}}))
    assert await lint(cases, tmp_path) == (0, 1, 0, 1)  # nothing flagged; "build" checked, "casts" not
    out = capsys.readouterr().out
    assert "casts: not checked: 1 of its 1 calls need data" in out and "build" not in out


def test_the_case_hash_covers_what_is_asked_and_judged_not_what_only_code_reads():
    from evals.run import case_hash
    case = {"id": "x", "question": "why?", "expect": "The enrage at 6:00.", "wrong": ["a reset"], "facts": ["14 alive"]}
    same = case | {"id": "y", "derived": ["9"], "drifts": ["25th"], "tools": ["progress"], "cause": "reset",
                   "numbers": ["6:00"], "form": {"bullets": 3}}
    assert case_hash(same) == case_hash(case)
    for key, value in (("question", "why so?"), ("expect", "The enrage at 6:01."), ("wrong", ["a kill"]),
                       ("facts", ["15 alive"]), ("calls", ["progress"]), ("asker", "Enfo"), ("pull", 3)):
        assert case_hash(case | {key: value}) != case_hash(case), key
