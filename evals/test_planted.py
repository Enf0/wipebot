"""The planted answers (evals/planted.py, evals/planted.toml): how the file is read, what makes a plant stale, and
that the plants in the file fit their cases. No model: the judge is faked."""
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
    assert planted.lint_line(plants, []) == "1 plants, none stale"
    assert planted.lint_line(plants, ["wipe-a: ..."]) == "1 plants, 1 stale: rewrite them (evals/planted.toml)"


async def test_the_lint_fails_on_a_stale_plant_and_names_it(tmp_path, capsys):
    from evals.run import _lint_then_drain
    path = tmp_path / "planted.toml"
    path.write_text(WRONG.replace("wipe-a", "wipe-was-a-reset"), encoding="utf-8")
    assert await _lint_then_drain("", tmp_path, path) == 1  # no run to replay: the plants are checked all the same
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("wipe-was-a-reset: its wrong plant is stale, the case no longer lists \"calls it an ")
    assert out[2] == "1 plants, 1 stale: rewrite them (evals/planted.toml)"
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
