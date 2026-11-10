"""The bot's own words (texts.py): each in English and Norwegian, and which language a text is in."""
import re

import discord
import pytest

from wipebot.discord import texts
from wipebot.discord.texts import Text


def every_text() -> list[tuple[str, Text]]:
    found = []
    for name, value in vars(texts).items():
        if isinstance(value, Text):
            found.append((name, value))
        elif isinstance(value, dict):
            found += [(f"{name}[{key}]", v) for key, v in value.items() if isinstance(v, Text)]
    return found


def test_every_text_is_in_both_languages_with_the_same_fields():
    found = every_text()
    assert len(found) > 60
    for name, text in found:
        assert text.en.strip() and text.no.strip(), name
        assert sorted(re.findall(r"\{(\w+)\}", text.en)) == sorted(re.findall(r"\{(\w+)\}", text.no)), name
        assert "—" not in text.en + text.no, name


def test_the_norwegian_keeps_the_games_words_as_raiders_write_them():
    norwegian = " ".join(text.no for _, text in every_text()) + " " + " ".join(texts.STEPS_NO.values())
    for translated in ("pullen", "skade", "dødsfall", "helse", "kl."):
        assert translated not in norwegian.lower(), translated
    assert texts.BUTTONS["ppull"].to("no", player="Torvek") == "Pullet til Torvek"  # "pull" is neuter
    assert texts.MENUS["player"].no == "Åpne en spillers kort på dette pullet…"


@pytest.mark.parametrize("text, lang", [
    ("**Cooldownene dine sitter der toppene har dem, bortsett fra Vanish.** Du brukte den 1 gang.", "no"),
    ("**Your cooldowns line up with the top players' but for Vanish.**", "en"),
    ("hvorfor wipet vi?", "no"),
    ("hvem døde først?", "no"),
    ("why did we wipe?", "en"),
    ("INGEN som brukte slows på siste pullet??", "no"),
    # a link's address is in no language: "fight=last" and "of-the" aren't the asker's English
    ("hva skjedde? https://www.warcraftlogs.com/reports/AbC123?fight=last&type=healing", "no"),
    ("kan du sjekke [denne](https://www.wowhead.com/guide/the-art-of-the-pull)?", "no"),
])
def test_which_language_a_text_is_in(text, lang):
    assert texts.language(text) == lang


def test_a_text_that_doesnt_show_its_language_takes_the_servers():
    link = "https://www.warcraftlogs.com/reports/AbC123#fight=4"
    assert texts.language(link) == "en" and texts.language(link, "no") == "no"
    assert texts.language("https://www.warcraftlogs.com/reports/AbC123?fight=last", "no") == "no"
    assert [texts.named(name) for name in ("Norwegian", "norsk", "Norsk bokmål", "Nynorsk", "no")] == ["no"] * 5
    assert [texts.named(name) for name in (None, "", "English", "Swedish", "Northern Sami")] == ["en"] * 5
    assert texts.locale_language(discord.Locale.norwegian) == "no"
    assert texts.locale_language(discord.Locale.american_english) == "en"
