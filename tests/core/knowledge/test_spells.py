import pytest

from tests.support import FIXTURES
from wipebot.core.knowledge import fetch, spells
from wipebot.core.knowledge.fetch import KnowledgeError, Memo, NotFound


def test_a_tooltip_keeps_what_applies_to_one_spec():
    """warcraft_cli's tooltip text drops Death Grip's taunt, which applies to Blood only (Wowhead, 15 Oct 2026)."""
    markup = (FIXTURES / "knowledge" / "wowhead-death-grip-tooltip.html").read_text(encoding="utf-8")
    assert spells.tooltip_text(markup) == (
        "Death Grip 30 yd range Draws the target toward you [Blood: and forcing the enemy to attack you]")


# What a defensive does, from its tooltip: the clause that says it, worded as on Wowhead on 2026-10-13
TOOLTIPS = {
    "Astral Shift": ("Taking 40 % less damage for 12 sec.",
                     ("reduction", 40, None, 12, None)),
    "Barkskin": ("Reducing all damage you take by 20%. Lasts 8 sec.", ("reduction", 20, None, 8, None)),
    "Blur": ("Reduces all damage taken by 25% for 10 sec.", ("reduction", 25, None, 10, None)),
    "Obsidian Scales": ("Reducing damage taken by 30 %. Lasts 12 sec.",
                        ("reduction", 30, None, 12, None)),
    "Spell Reflection": ("Reducing magic damage you take by 20% for 5 sec.", ("reduction", 20, "magic", 5, None)),
    "Feint": ("Reducing damage taken from area-of-effect attacks by 40% for 6 sec.",
              ("reduction", 40, "aoe", 6, None)),
    "Divine Shield": ("Grants immunity to all damage for 8 sec.", ("immunity", None, None, 8, None)),
    "Ice Block": ("Protecting you from all attacks and damage for 10 sec.", ("immunity", None, None, 10, None)),
    "Cloak of Shadows": ("Provides a moment of magic immunity.",
                         ("immunity", None, "magic", None, None)),
    "Crimson Vial": ("Heals you for 20 % of your maximum health over 4 sec.",
                     ("heal", 20, None, None, None)),
    "Healthstone": ("Instantly restores 25% health", ("heal", 25, None, None, None)),
    "Spirit Link Totem": ("A totem for 6 sec reduces damage taken by all party and raid members within 10 yards by "
                          "10%.", ("reduction", 10, None, 6, 10)),
    "Anti-Magic Zone": ("For 6 sec, reducing the magic damage taken by party or raid members by 15%.",
                        ("reduction", 15, "magic", 6, None)),
    "Aura Mastery": ("For 8 sec. Devotion Aura : Damage reduction increased to 12%.",
                     ("reduction", 12, None, 8, None)),
    "Desperate Prayer (older wording)": ("Increases maximum health by % for 10 sec, and instantly heals you for that "
                                         "amount.", None),
    "Evasion": ("Increases your dodge chance by 100% for 10 sec.", None),
    # 17 Oct 2026: the replay's most frequent "can't tell"s in the bot's answers
    "Exhilaration": ("Heals you for 30% and your pet for 100% of maximum health. [Rejuvenating Wind: Exhilaration "
                     "heals you for an additional 12.0% of your maximum health over 8 sec]",
                     ("heal", 30, None, None, None)),
    "Desperate Prayer": ("Increases maximum health by [Light's Inspiration: 35 / 25]% for 10 sec, and instantly "
                         "heals you for that amount.", ("heal", 25, None, 10, None)),
    "Survival of the Fittest": ("Reduces all damage you and your pet take by 30% for 6 sec.",
                                ("reduction", 30, None, 6, None)),
    "Ice Barrier": ("Shields you with ice, absorbing [35 / 100 * Total Health * (1 + Versatility)] damage for 1 min.",
                    ("absorb", 35, None, None, None)),
    "Rallying Cry": ("Granting all party or raid members within 40 yards 10% temporary and maximum health for 10 sec.",
                     ("absorb", 10, None, 10, 40)),
    "Dark Pact": ("Sacrifices 20% of your current health to shield you for 200% of the sacrificed health plus an "
                  "additional (Spell Power * 2.5) for 20 sec.", None),
    "Fade": ("Fade out, removing all your threat and reducing enemies' detection range against you for 10 sec.",
             None),
}


@pytest.mark.parametrize("name", TOOLTIPS)
def test_a_defensive_is_read_from_its_tooltip_or_not_at_all(name):
    text, expected = TOOLTIPS[name]
    effect = spells.parse_effect(text)
    got = effect and (effect.kind, effect.pct, effect.only, effect.duration_s, effect.radius_yd)
    assert got == expected


def test_an_effect_says_what_it_does():
    assert str(spells.parse_effect("Grants immunity to all damage and harmful effects for 8 sec.")) == \
        "immune to all damage for 8 s"
    assert str(spells.parse_effect(TOOLTIPS["Cloak of Shadows"][0])) == "immune to magic damage"
    assert str(spells.parse_effect(TOOLTIPS["Spirit Link Totem"][0])) == "10% less damage for 6 s, within 10 yd"
    assert str(spells.parse_effect(TOOLTIPS["Healthstone"][0])) == "heals 25% of maximum health"


@pytest.mark.parametrize("text, cooldown", [
    ("Kick Melee Range Instant 15 sec cooldown Requires Rogue", 15.0),
    ("Silence 40 yd range Instant 30 sec cooldown Requires Priest", 30.0),
    ("Bloodlust 10% of base mana Instant 5 min cooldown Requires Shaman", 300.0),
    ("Frostbolt 40 yd range 2 sec cast Requires Mage", None),
])
async def test_a_spells_cooldown_is_read_from_its_tooltip(monkeypatch, text, cooldown):
    async def tooltip(spell_id):
        return spells.Spell(spell_id, "a spell", text, "")
    monkeypatch.setattr(spells, "tooltip", tooltip)
    assert await spells.spell_cooldown(1) == cooldown


async def test_a_spell_wowhead_lacks_has_no_name_but_wowhead_down_is_unavailable(monkeypatch):
    async def cli(tool, *args):
        raise NotFound("wowhead entity: not_found") if args[2] == "1" else KnowledgeError("wowhead entity: timeout")
    monkeypatch.setattr(fetch, "cli", cli)
    monkeypatch.setattr(spells, "_spell_names", Memo())
    assert await spells.spell_name(1) is None
    with pytest.raises(KnowledgeError):
        await spells.spell_name(2)
    assert 2 not in spells._spell_names  # asked again next time


async def test_bloodlusts_debuffs_are_unavailable_without_lorrgs(monkeypatch):
    async def cli(tool, *args):
        raise KnowledgeError("lorrgs spell: timeout")
    monkeypatch.setattr(fetch, "cli", cli)
    monkeypatch.setattr(spells, "_lust", Memo())
    with pytest.raises(KnowledgeError):
        await spells.lust_debuffs()


def test_a_moment_of_immunity_is_sure_only_for_a_moment():
    """Cloak of Shadows' tooltip gives 5 s, but of resisting: the immunity is "a moment" (guides: the first second)."""
    effect = spells.parse_effect("Provides a moment of magic immunity, instantly removing all harmful spell effects. "
                                 "The cloak lingers, causing you to resist harmful spells for 5 sec.")
    assert effect and (effect.kind, effect.only, effect.duration_s, effect.sure_s) == ("immunity", "magic", 5, 1)
