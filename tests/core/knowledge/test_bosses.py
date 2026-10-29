import datetime
import json

from tests.support import FIXTURES
from wipebot.core.knowledge import bosses, fetch, news
from wipebot.core.knowledge.fetch import KnowledgeError
from wipebot.core.knowledge.guides import Guide

HERE = FIXTURES / "knowledge"


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


async def test_boss_guide_reads_method_wowhead_and_icy_veins(monkeypatch):
    seen = []
    wowhead = load("wowhead-sszorak-guide.json")

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        seen.append((tool, args))
        if tool == "method":
            return {"guide": {"last_updated": "2026-10-02"}, "pages": [{"guide": {"section_title": "Sszorak"},
                    "article": {"sections": [{"title": "The Two Things that Wipe you", "text": "Tornadoes."}]}}]}
        if tool == "wowhead" and args[0] == "guides":
            return {"results": [{"title": "Sszorak Raid Boss Guide - Midnight Season 2", "url": "https://wh/sszorak",
                                 "last_updated": "2026-08-26T15:49:22-05:00"}]}
        if tool == "wowhead":
            return wowhead
        if args[0] == "search":
            return {"results": [{"id": "sszorak-raid-guide", "metadata": {"content_family": "raid_guide"}}]}
        return {"guide": {"page_url": "https://www.icy-veins.com/wow/sszorak-raid-guide"}, "pages": [
            {"guide": {"section_title": "4 Lost Explorers"}, "article": {"sections": [{"title": "x", "text": "no"}]}},
            {"guide": {"section_title": "5 Sszorak", "last_updated": "2026-08-13T12:30:00+00:00"},
             "article": {"sections": [
                 {"title": "Mechanic Breakdown (Normal/Heroic)", "text": "In this section ..."},
                 {"title": "Intermission", "text": "Dig In: Sszorak takes 30% increased damage."},
                 {"title": "Notable Changes on Mythic Difficulty", "text": "Below ..."},
                 {"title": "Main Phase", "text": "Serpent's Fury: 14 players."},
                 {"title": "Changelog", "text": "13 Aug. 2026: Guide created."}]}}]}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = await bosses.boss_guide_text("Sszorak", "The Venomous Abyss", "heroic")
    assert ("method", ("guide-full", "https://www.method.gg/guides/the-venomous-abyss/sszorak-heroic")) in seen
    assert "## The Two Things that Wipe you\nTornadoes." in text
    assert "## Mechanic Breakdown (Normal/Heroic): Intermission\nDig In: Sszorak takes 30% increased damage." in text
    assert "Serpent's Fury" not in text and "Changelog" not in text  # Heroic: no Mythic changes
    assert "Icy Veins' guide for Sszorak (every difficulty; without the Mythic changes)" in text
    assert "last updated 2026-08-13" in text
    # Wowhead's cheat sheet, with the linked names the markup carries, and no loot
    assert "# Wowhead's guide for Sszorak" in text and "last updated 2026-08-26" in text
    assert "Save cooldowns for Dig In.\n\n### Boss Cheat Sheet\nBloodlust Timing: Pull\n\n- Dig In - The boss takes " \
           "30% increased damage." in text
    assert "builds to a massive Howling Maelstrom that pushes players." in text and "Loot" not in text
    mythic = await bosses.boss_guide_text("Sszorak", "The Venomous Abyss", "mythic")
    assert "## Notable Changes on Mythic Difficulty: Main Phase\nSerpent's Fury: 14 players." in mythic
    assert "Mythic changes at the end" in mythic


async def test_boss_guide_finds_icy_veins_page_by_a_loose_title(monkeypatch):
    """Icy Veins' search doesn't know "The Twin Fangs" or "Vashnik the Malignant"; the raid's family of pages
    titles them "7 Twin Fangs" and "6 Vashnik"."""
    searches = []

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        if tool != "icy-veins":
            raise KnowledgeError("no")
        if args[0] == "search":
            searches.append(args[-1])
            if args[-1] == "venomous abyss":
                return {"results": [{"id": "venomous-abyss-raid-guide", "metadata": {"content_family": "raid_guide"}}]}
            return {"results": []}
        return {"pages": [{"guide": {"section_title": "6 Vashnik", "page_url": "https://iv/vashnik"},
                           "article": {"sections": [{"title": "Quick Guide", "text": ""},
                                                    {"title": "Fountains", "text": "Soak."}]}},
                          {"guide": {"section_title": "7 Twin Fangs"},
                           "article": {"sections": [{"title": "Main Phase", "text": "Swap."}]}}]}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = await bosses.boss_guide_text("Vashnik the Malignant", "The Venomous Abyss")
    assert searches == ["Vashnik the Malignant", "vashnik", "venomous abyss"]
    assert "## Quick Guide: Fountains\nSoak." in text and "the page has no Mythic section" in text
    assert "(Not available: no Method guide" in text and "(Not available: no Wowhead guide for Vashnik" in text
    assert "## Main Phase\nSwap." in await bosses.boss_guide_text("The Twin Fangs", "The Venomous Abyss")


async def test_method_heroic_guide_stands_in_for_a_missing_mythic_one(monkeypatch):
    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        if args[-1].endswith("/ulatek"):
            raise KnowledgeError("not_found")
        return {"guide": {"last_updated": "2026-08-30"}, "pages": [{"guide": {"section_title": "Ulatek Heroic"},
                "article": {"sections": [{"title": "Phase 1", "text": "Soak the coils."}]}}]}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = bosses.strategy_text(await bosses.method_boss_guide("Ula'tek", "The Venomous Abyss", "mythic"))
    assert text.startswith("# Method has no Mythic guide for Ula'tek (The Venomous Abyss); its Heroic guide, "
                           "last updated 2026-08-30")
    assert "/the-venomous-abyss/ulatek-heroic" in text and "## Phase 1\nSoak the coils." in text


async def test_boss_guide_survives_one_missing_guide(monkeypatch):
    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        if tool == "method":
            raise KnowledgeError("not_found")
        if args[0] == "search":
            return {"results": [{"id": "x-raid-guide", "metadata": {"content_family": "raid_guide"}}]}
        return {"pages": [{"guide": {"section_title": "2 Nek'zali the Soulcoiler"},
                           "article": {"sections": [{"title": "Phase 1", "text": "Soak."}]}}]}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = await bosses.boss_guide_text("Nek’zali the Soulcoiler", "The Venomous Abyss")
    assert "## Phase 1\nSoak." in text and "(Not available: no Method guide" in text


async def test_boss_loot_lists_the_gear_and_what_the_spec_guides_name(monkeypatch):
    guide, tips = load("wowhead-kithix-guide.json"), load("wowhead-kithix-item-tooltips.json")

    async def fake_cli(tool, *args):
        args = fetch._key_args(args)  # the command as it means, wherever `--` stands
        if tool == "wowhead" and args[0] == "guides" and args[1] == "raids":
            return {"results": [{"title": "The Unbinding of Kith'ix Raid Guide", "url": "https://wh/kithix",
                                 "last_updated": "2026-10-13"}]}
        if tool == "wowhead" and args[:2] == ("entity", "item"):
            tip = tips[args[2]]
            return {"entity": {"name": tip["name"]}, "tooltip": {"html": tip["html"]}}
        if args[0] == "resolve":
            return {"resolved": True, "next_command": f"{tool} guide-full https://{tool}/resto"}
        if tool == "icy-veins":
            raise KnowledgeError("not_found")
        if tool == "method":
            return {"pages": [
                {"guide": {"section_title": "Overview", "last_updated": "2026-08-01"},
                 "article": {"sections": [{"title": "Overview", "text": "Twisted Horror’s Tendril is bad."}]}},
                {"guide": {"section_title": "Gearing", "page_url": "https://method/resto/gearing",
                           "last_updated": "2026-10-12"},
                 "article": {"sections": [
                     {"title": "Trinkets", "text": "Twisted Horror’s Tendril is fine."},
                     {"title": "Best Gear from Mythic+", "text": "Chestwrap of Palpable Terror"}]}}]}
        if args[1] == "https://wowhead/resto":
            return {"navigation": {"raw_markup": "[url=guide/resto/bis-gear]BiS Gear[/url]"}}
        if args[1] == "https://www.wowhead.com/guide/resto/bis-gear":
            return {"guide": {"page_url": args[1], "last_updated": "2026-10-11"},
                    "body": {"section_chunks": [{"title": "Best in Slot", "content_text": "Trinket: Twisted "
                                                 "Horror's Tendril (Kith'ix)"}]}}
        return guide
    monkeypatch.setattr(fetch, "cli", fake_cli)
    text = await bosses.boss_loot_text("Kith'ix", "restoration shaman")
    assert "Item level of the drops: LFR 285 (Veteran 3/6), Mythic 324 (Myth 3/6)" in text
    assert "Not in Wowhead's table: Normal, Heroic; its item level" in text
    # the primary stat in brackets kept; name, item level, selling and durability left out
    assert ("- Twisted Horror's Tendril (item 1), Trinket: Trinket +190 [Agility or Intellect] Equip: Grants "
            "Devastating Power. [tooltip at item level 340]\n  Restoration Shaman: named in Method's trinket advice, "
            "Wowhead's best-in-slot list") in text
    # a Mythic+ list isn't the raid's
    assert "Chestwrap of Palpable Terror (item 2), Leather Chest: Chest Leather +189 Intellect +143 Haste" in text
    assert "Restoration Shaman: not named in their gear pages" in text
    assert "a tier token; the gear pages name the set piece" in text
    assert "Method https://method/resto/gearing (last updated 2026-10-12" in text and "not available: Icy Veins" in text
    assert "Also drops: Shadeweaver's Hearthstone" in text and "item 9" not in text


def test_a_mechanic_is_read_from_the_guide_sections_and_paragraphs_naming_it():
    """A section titled for the mechanic comes whole; of the rest only the paragraphs naming it, each once (the
    guides repeat an ability per difficulty), and a long one only its sentences naming it with the next few."""
    filler = " ".join(f"Sentence {i} about the tanks." for i in range(60))
    g = Guide("icy-veins", "A guide", "https://guide", None, [
        ("Searing Brand (Soak)", "Stand in it. Then move."),
        ("Abilities", "- Searing Brand: burns.\nMore on it.\n\n- Tail Swipe: knocks.\n\n"
                      "- Searing Brand: burns.\nMore on it."),
        ("Mythic", f"{filler} The marked player runs out with Searing Brand. Spread. Heal up. Never stack. {filler}"),
        ("Tanks", "Swap at 6 stacks of Searing Brandish."),
    ])
    found = dict(bosses.mechanic_sections(g, "searing brand"))
    assert found["Searing Brand (Soak)"] == "Stand in it. Then move."
    assert found["Abilities"] == "- Searing Brand: burns.\nMore on it."
    assert found["Mythic"].startswith("The marked player runs out with Searing Brand. Spread. Heal up.")
    assert "Never stack" not in found["Mythic"] and "Tanks" not in found  # another ability's name isn't this one's


def test_an_enrage_is_found_under_either_name():
    """Guides say enrage or berserk for the same timer, and the model asks with either."""
    g = Guide("method", "A guide", "https://guide", None, [
        ("Overview", "There's no DPS enrage to race against.\n\nBring a fifth healer."),
        ("Mythic", "The boss goes berserk at 8:00."),
    ])
    for asked in ("Berserk", "enrage"):
        assert dict(bosses.mechanic_sections(g, asked)) == {
            "Overview": "There's no DPS enrage to race against.", "Mythic": "The boss goes berserk at 8:00."}


def test_the_journal_keeps_the_entries_on_the_difficulty_asked():
    """Wowhead's journal lists an ability once per difficulty it differs on, its abilities in the list under it; an
    entry on no difficulty is on all of them."""
    raw = ("<ul><li><div>Fangs: all players.</div></li>"
           "<li><div>Deluge: spews venom. (Normal, Raid Finder)</div></li>"
           "<ul><li><div>Venom: pulses.</div></li><ul><li><div>Rupture: explodes.</div></li></ul></ul>"
           "<li><div>Deluge: spews venom. (Heroic, Mythic)</div></li>"
           "<ul><li><div>Mutation: on Mythic. (Mythic)</div></li></ul></ul>")
    mythic = bosses.journal_on(raw, "mythic")
    assert "Fangs" in mythic and "(Heroic, Mythic)" in mythic and "Mutation" in mythic
    assert "Normal" not in mythic and "Venom:" not in mythic and "Rupture" not in mythic
    heroic = bosses.journal_on(raw, "heroic")
    assert "(Heroic, Mythic)" in heroic and "Mutation" not in heroic and "Rupture" not in heroic


def test_the_strategy_guides_come_whole_and_wowhead_gets_the_rest():
    def guide(source):
        return Guide(source, source, f"https://{source}", None, [])
    guides = [guide("method"), guide("wowhead"), guide("icy-veins")]
    assert bosses._budgets(guides, ["m" * 20000, "w" * 39000, "i" * 11000]) == [20000, 9000, 11000]
    # strategy guides too long together share what Wowhead's least leaves, by length
    room = bosses.GUIDES_CHARS - bosses.WOWHEAD_MIN_CHARS
    method, wowhead, icy = bosses._budgets(guides, ["m" * 30000, "w" * 30000, "i" * 30000])
    assert method == icy == room // 2 and wowhead == bosses.WOWHEAD_MIN_CHARS
    assert bosses._budgets([guide("method")], ["m" * 50000]) == [bosses.GUIDES_CHARS]


def test_a_cut_guide_names_what_was_cut_and_a_repeated_ability_keeps_its_name():
    text = bosses._repeats_out("# Guide\n\n## Phase 1\n" + "x" * 300 + "\n\n- Deluge: " + "venom " * 20
                               + "\n\n## Phase 3\n- Stage Three: Union\n\n- Deluge: " + "venom " * 20)
    assert text.endswith("- Stage Three: Union\n\n- Deluge: as above")
    cut = bosses._cut(Guide("method", "M", "https://m", None, []), text, 200)
    assert cut.endswith("(Cut here: Phase 3, Stage Three: Union and the rest; web_lookup with https://m "
                        "reads the whole guide.)")


def test_hotfixes_are_listed_against_the_guides_written_before_them():
    """By day, as the posts give no time: a hotfix on the day of a guide's update may be newer than it."""
    def fix(day, text):
        return news.Hotfix(f"October {day}, 2026", "The Venomous Abyss › The Coiled Altar", text,
                           datetime.datetime(2026, 10, day, tzinfo=datetime.UTC).timestamp())
    guides = [Guide("method", "M", "https://m", "2026-10-13", []),
              Guide("icy-veins", "I", "https://i", "2026-08-13T12:30:00+00:00", [])]
    text = bosses.hotfixes_after("The Coiled Altar", guides, (["https://post"], [fix(13, "Orbs reduced to 4."),
                                                                                  fix(2, "Old.")]))
    assert text.splitlines()[1:] == [
        "- October 13, 2026 (Method, Icy Veins): The Venomous Abyss › The Coiled Altar: Orbs reduced to 4.",
        "- October 2, 2026 (Icy Veins): The Venomous Abyss › The Coiled Altar: Old."]
    newest = [Guide("method", "M", "https://m", "2026-10-14", [])]
    assert bosses.hotfixes_after("The Coiled Altar", newest, (["https://post"], [fix(13, "x")])).startswith(
        "# No hotfix to The Coiled Altar since 2026-10-14.")
    assert "unknown" in bosses.hotfixes_after("The Coiled Altar", newest, KnowledgeError("down"))


async def test_a_mechanic_on_the_wrong_boss_is_found_on_its_own(monkeypatch):
    async def raids():
        return [("The Venomous Abyss", ["The Lost Explorers", "The Coiled Altar"]), ("Sporefall", ["Rotmire"])]

    async def boss_spells(boss):
        return [{"name": "Axegrinder"}] if boss == "The Coiled Altar" else [{"name": "Shell Spin"}]

    async def no_abilities(boss):
        return []
    monkeypatch.setattr(bosses, "raids", raids)
    monkeypatch.setattr(bosses, "boss_spells", boss_spells)
    monkeypatch.setattr(bosses, "_abilities", no_abilities)
    assert await bosses.boss_naming("axegrinder", "The Lost Explorers") == ("The Venomous Abyss", "The Coiled Altar")
    assert await bosses.boss_naming("Axegrinder", "The Coiled Altar") is None
    assert await bosses.boss_naming("Shell Spin", "Rotmire") == ("The Venomous Abyss", "The Lost Explorers")
    read = []

    async def counted(boss):
        read.append(boss)
        return await boss_spells(boss)
    monkeypatch.setattr(bosses, "boss_spells", counted)
    assert await bosses.boss_naming("Axegrinder", "The Lost Explorers", "The Venomous Abyss") == (
        "The Venomous Abyss", "The Coiled Altar")
    assert read == ["The Coiled Altar"]  # found in the report's raid: the other raids aren't read
