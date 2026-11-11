"""The views' own words in another language than English: the labels a card draws and the sentence over it, never
a tool's text, which the model reads in English and answers from in the question's language by itself. One
catalogue (app/words.json), read here for what the server words (a card's headline, a ranking's title) and by the
page for what it words (kit.t): English is the key, so a line without a translation stays English.

The game's words stay English, as raiders write them (pull, wipe, kill, healing, tank, parse), and so do names of
bosses, abilities, specs and players, which are the log's.
"""
import contextvars
import functools
import json
from importlib import resources

LANGUAGES = {"no": ("no", "nb", "nn")}  # a catalogue's language -> the codes and names' starts that mean it
_NAMES = {"no": ("norw", "nors", "bokm", "nyn")}
lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default="en")


@functools.cache
def catalogue() -> dict[str, dict[str, str]]:
    """Each language's words: {language: {English: its words}}."""
    return json.loads(resources.files("wipebot.core").joinpath("app", "words.json").read_text(encoding="utf-8"))


def language(given: object) -> str:
    """The language the views have for what a caller named ('no', 'nb-NO', 'Norwegian', 'norsk'): one with a
    catalogue, else 'en'."""
    name = str(given or "").strip().lower()
    for code, codes in LANGUAGES.items():
        if name.split("-")[0].split("_")[0] in codes or name.startswith(_NAMES[code]):
            return code
    return "en"


def say(en: str, **fields: object) -> str:
    """A sentence or label in the view's language, with its {fields} filled in."""
    return catalogue().get(lang.get(), {}).get(en, en).format(**fields)
