import pytest

from wipebot.core.knowledge.names import boss_words, lorrgs_slug, matches_boss, names_boss, slug


@pytest.mark.parametrize("cls, spec, expected", [
    ("Shaman", "Restoration", "shaman-restoration"),
    ("DeathKnight", "Blood", "deathknight-blood"),
    ("Hunter", "BeastMastery", "hunter-beastmastery"),
])
def test_lorrgs_slug(cls, spec, expected):
    assert lorrgs_slug(cls, spec) == expected


@pytest.mark.parametrize("name, expected", [
    ("Sszorak", "sszorak"), ("Nek'zali the Soulcoiler", "nekzali-the-soulcoiler"),
    ("The Venomous Abyss", "the-venomous-abyss"), ("Ula'tek", "ulatek"),
])
def test_slug_matches_method_urls(name, expected):
    assert slug(name) == expected


def test_a_title_names_a_boss_by_all_its_words():
    assert names_boss("The Unbinding of Kith'ix Raid Guide - Midnight Season 2: Boss Cheat Sheet", "Kith’ix")
    assert names_boss("Nek'zali the Soulcoiler Raid Boss Guide", "Nek'zali the Soulcoiler")
    assert not names_boss("Sszorak Raid Boss Guide", "Ula'tek")
    assert boss_words("The Twin Fangs") == ["twin", "fangs"]


def test_a_name_run_together_names_the_boss():
    # A raider's "Twinfangs" found no guide and no boss (answer #483).
    assert names_boss("The Twin Fangs", "Twinfangs")
    assert names_boss("The Twin Fangs Raid Boss Guide", "twinfangs")
    assert not names_boss("Twinfang Harrower", "Twin Fangs")
    assert not names_boss("The Twin Fangs", "Twinfang")


@pytest.mark.parametrize("typed, boss", [
    ("Sszorak", "Sszorak"), ("sszo", "Sszorak"), ("fang", "The Twin Fangs"), ("twin fangs", "The Twin Fangs"),
    ("twinfangs", "The Twin Fangs"), ("Nekzali", "Nek'zali the Soulcoiler"),
    ("nek’zali soul", "Nek'zali the Soulcoiler"), ("Mirelle", "Mírëlle"),
])
def test_a_typed_boss_matches_its_name_or_part_of_it(typed, boss):
    assert matches_boss(typed, boss)


def test_a_typed_boss_doesnt_match_another():
    assert not matches_boss("Ula'tek", "Sszorak")
    assert not matches_boss("twin vashnik", "The Twin Fangs")
