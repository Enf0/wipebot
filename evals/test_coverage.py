"""How the cases are asked (coverage.as_asked), with made-up cases: no model, no Warcraft Logs."""
from evals.coverage import REAL_USE, as_asked


def case(question, **more) -> dict:
    return {"id": "a", "kind": "wipes", "question": question, **more}


def test_the_cases_are_counted_as_they_are_asked_beside_how_raiders_ask():
    cases = [
        case("Why did we wipe on the last pull?", boss="Sszorak"),
        case("hvorfor wipet vi på siste pull? alle bare døde samtidig", where="in a DM", boss="The Coiled Altar"),
        case(["why did we wipe?", "and 18?"], where="in a DM"),  # the second question is judged: one before it
        case("and what killed us on that one?", note="a-note.txt", boss="The Coiled Altar",  # three people wrote before
             earlier=[["Karadim", "why?"], ["Wipebot", "Execution."], ["Pomaa", "the orbs"], ["Karadim", "ok"]]),
        case("In #raid: why did we wipe?", kind="posts"),  # the bot's own text: no language, but a place
    ]
    title, language, turn, place, note, boss = as_asked(cases)
    assert title == "How the cases are asked, beside how raiders ask:"
    assert language.startswith("  language of the question: English 3, Norwegian 1 (real: about five in six")
    assert turn.startswith("  the judged question in its conversation: the first 2, the second or third 1, "
                           "the fourth or later 1 (real: about two in three")
    assert place.startswith("  place: in a channel 3, in a DM 2 (real: more than half in a DM")
    assert note.startswith("  with a saved raid note, as the bot: 1 of 5 cases (real: about one answer in six")
    assert boss.startswith("  the boss named: The Coiled Altar 40%, Sszorak 20% of the cases (real: The Coiled Altar")
    assert all(line.endswith(f"(real: {REAL_USE[key]})")
               for line, key in zip((language, turn, place, note, boss), REAL_USE, strict=True))
