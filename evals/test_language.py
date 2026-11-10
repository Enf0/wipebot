"""The language check (checks.in_language) on answers that quote the other language: a check that fails right
answers is worse than none. No model, no Warcraft Logs."""
import pytest

from evals.checks import CLEAR, answer_language, in_language
from wipebot.discord import texts

NORSK_CASE = {"id": "a", "kind": "norsk", "question": "hvorfor wipet vi på siste pull?", "language": "Norwegian"}
ENGLISH_CASE = {"id": "b", "kind": "wipes", "question": "why did we wipe on the last pull?"}

# Right answers in Norwegian, with the game's names in English: "of" and "the" are the names', not the answer's
NORSK_WITH_NAMES = [
    "Dere døde til Call of the Void og Eye of the Storm.",
    "Hvilken boss mener du: The Coiled Altar eller The Twin Fangs?",
    "Seks kills på onsdag: The Coiled Altar, Ula'tek, Nek'zali the Soulcoiler, Entombed Sentinels, The Lost Explorers "
    "og Vashnik the Malignant.",
    "**Blood of the Old Ones og Call of the Void tok dere på 2:13.** Dere mistet 6 til Eye of the Storm og 4 til "
    "Breath of the Deep, og tanken døde til Bite of the Serpent. Stå i Circle of the Moon når Call of the Void kommer.",
]
# Right answers in English that quote Norwegian: lines of the guild's note, and minutes written "min"
ENGLISH_WITH_NORSK = [
    'The note says "Enfo tar soak 2 med Namsus og Zeb på venstre", and you were on the right.',
    'Your line in the note is "Hymn på 2:10, etter soak 2, ikke før", which you did.',
    "Kicks at 3 min, 5 min and 7 min; you missed the one at 2 min.",
    "Kicks: 3 min, 5 min, 7 min. Missed: 2 min.",
]
NORSK = ("**Execution drepte 14 spillere på 2:13.** Guillotine traff færre enn 5, så øksa gjorde en Execution på "
         "hele raidet. Det er soaken som må fikses, ikke mer damage: dere var 4 i sirkelen, og den femte sto for "
         "langt unna.")
ENGLISH = ("**Execution killed 14 players at 2:13.** Guillotine hit fewer than 5, so the axe did an Execution on the "
           "whole raid. The soak is what to fix, not more damage: 4 of you were in the circle and the fifth was too "
           "far.")


@pytest.mark.parametrize("answer", NORSK_WITH_NAMES)
def test_a_norwegian_answer_that_names_the_games_english_names_is_not_taken_for_english(answer):
    assert texts.language(answer, "") == "en"  # by the bare count it would be, and a right answer would fail
    assert answer_language(answer) != "en"
    assert in_language(NORSK_CASE, answer, "bot") is not False


@pytest.mark.parametrize("answer", ENGLISH_WITH_NORSK)
def test_an_english_answer_that_quotes_norwegian_is_not_taken_for_norwegian(answer):
    assert answer_language(answer) != "no"
    assert in_language(ENGLISH_CASE, answer) is not False


def test_an_answer_plainly_in_the_other_language_still_fails():
    assert (answer_language(NORSK), answer_language(ENGLISH)) == ("no", "en")
    assert in_language(NORSK_CASE, ENGLISH, "bot") is False and in_language(NORSK_CASE, NORSK, "bot") is True
    assert in_language(ENGLISH_CASE, NORSK) is False and in_language(ENGLISH_CASE, ENGLISH) is True
    # with the other language quoted in it too
    assert in_language(NORSK_CASE, ENGLISH + " You asked: hvorfor wipet vi på siste pull?", "bot") is False
    assert in_language(ENGLISH_CASE, NORSK + " The Coiled Altar, pull 21 of the night.") is False


def test_a_two_line_answer_is_held_to_its_language_only_when_it_shows():
    assert in_language(NORSK_CASE, "Which pull do you mean?", "bot") is None  # too few common words to tell
    assert in_language(NORSK_CASE, "Hvilket pull mener du?", "bot") is None
    english = "**Execution killed 14 of you at 2:13.**\nThe soak was short, so the axe did it."
    norsk = "**Execution drepte 14 av dere på 2:13.**\nSoaken var for liten, så øksa gjorde det."
    assert in_language(NORSK_CASE, english, "bot") is False and in_language(NORSK_CASE, norsk, "bot") is True


def test_the_lead_an_answers_language_needs():
    def words(no: int, en: int) -> str:
        return " ".join(["og"] * no + ["and"] * en + ["Krøpling"])
    assert CLEAR == 5
    assert [answer_language(words(no, 0)) for no in (4, 5)] == ["", "no"]
    assert [answer_language(words(0, en)) for en in (4, 5)] == ["", "en"]
    assert [answer_language(words(no, 5)) for no in (9, 10)] == ["", "no"]  # twice the other's
    assert [answer_language(words(5, en)) for en in (9, 10)] == ["", "en"]
    assert answer_language("https://www.warcraftlogs.com/reports/R?fight=last&the=and&you=did") == ""  # no language
