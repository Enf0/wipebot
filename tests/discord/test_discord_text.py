"""What's made of the model's answer before it's posted: links, bold names, pull links, lists."""
from types import SimpleNamespace
from typing import cast

import pytest

from tests.support import CODE, FakeWCL
from wipebot.core import server
from wipebot.core.report import Report
from wipebot.discord.discord_text import (
    add_icons,
    bold_players,
    fix_pull_links,
    link_abilities,
    link_items,
    link_scenes,
    planned_classes,
    polish,
    specs_in,
    tight_lists,
    unlink_broken,
    unlink_elsewhere,
    unlink_wrong,
    verdict_bold,
)


def spell(name: str, spell_id: int) -> str:
    return f"[{name}](https://www.wowhead.com/spell={spell_id})"


ABILITIES = {1287083: "Tempest", 462: "Tempest", 51490: "Thunderstorm", 1285998: "Mutilated Gash", 1: "Melee"}
DEATHS = ["1. 0:48 Brightleaf - Tempest [1287083] (19 alive)", "Mutilated Gash [1285998] x9; Melee [1]"]


@pytest.mark.parametrize("abilities, results, text, expected", [
    pytest.param(ABILITIES, DEATHS, "**Tempest killed Brightleaf.** More Tempest later.",
                 f"**{spell('Tempest', 1287083)} killed Brightleaf.** More Tempest later.",
                 id="the first mention, by the id a tool gave (Tempest has two in the report)"),
    pytest.param(ABILITIES, DEATHS, f"{spell('Mutilated Gash', 1285998)}, then Mutilated Gash.",
                 f"{spell('Mutilated Gash', 1285998)}, then Mutilated Gash.", id="already linked"),
    pytest.param(ABILITIES, DEATHS, "Melee hurt.", "Melee hurt.", id="not Melee and the like"),
    pytest.param(ABILITIES, DEATHS, "Thunderstorm helps.", "Thunderstorm helps.", id="not in the results"),
    pytest.param(ABILITIES, [*DEATHS, "Casts: Thunderstorm 3, Tempest 2"], "Use Thunderstorm and Tempest.",
                 f"Use {spell('Thunderstorm', 51490)} and {spell('Tempest', 1287083)}.",
                 id="named without an id: the report's only id for it"),
    pytest.param({1300235: "Soul Exhaustion", 1300236: "Soul Exhaustion", 57723: "Exhaustion", 5176: "Wrath",
                  31884: "Avenging Wrath", 31885: "Avenging Wrath"},
                 ["Soul Exhaustion on 4 players", "Bloodlust (Exhaustion on 20 players)", "Avenging Wrath; Wrath"],
                 "Soul Exhaustion stacks; Exhaustion too. Avenging Wrath, then Wrath.",
                 f"Soul Exhaustion stacks; {spell('Exhaustion', 57723)} too. Avenging Wrath, then "
                 f"{spell('Wrath', 5176)}.",
                 id="not inside a longer ability of the report"),
    pytest.param({390677: "Inspiration", 106898: "Stampeding Roar", 61391: "Typhoon"},
                 ["where they differ: Lycara's Inspiration (86%), Typhoon (82%), Improved Stampeding Roar (62%)",
                  "Stampeding Roar [106898] at 1:02"],
                 "Lycara's Inspiration, Typhoon, Improved Stampeding Roar. Roar: Stampeding Roar at 1:02.",
                 f"Lycara's Inspiration, {spell('Typhoon', 61391)}, Improved Stampeding Roar. Roar: "
                 f"{spell('Stampeding Roar', 106898)} at 1:02.",
                 id="not inside a talent the results name"),
    pytest.param(ABILITIES, DEATHS, "```\nTempest  you ███ 3\n```\nDodge `Tempest`, then Tempest.",
                 f"```\nTempest  you ███ 3\n```\nDodge `Tempest`, then {spell('Tempest', 1287083)}.",
                 id="not in code: a link there shows as its markdown"),
])
def test_abilities_link_to_wowhead(abilities, results, text, expected):
    assert link_abilities(text, results, abilities) == expected


def test_items_link_to_wowhead_by_the_ids_boss_guide_gave():
    results = ["## Gear\n- Twisted Horror's Tendril (item 281215), Trinket: +190 ...\n"
               "- Band of the Swarmcaller (item 281029), Finger: ..."]
    band = "[Band of the Swarmcaller](https://www.wowhead.com/item=281029)"
    text = f"**Twisted Horror's Tendril:** take it. Twisted Horror's Tendril again; {band}. Chestwrap of Terror."
    assert link_items(text, results) == (
        f"**[Twisted Horror's Tendril](https://www.wowhead.com/item=281215):** take it. Twisted Horror's Tendril "
        f"again; {band}. Chestwrap of Terror.")


def test_a_spell_link_without_an_id_is_undone():
    text = f"Save cooldowns for [Dig In](https://www.wowhead.com/spell=Dig In); {spell('Tempest', 1287083)} stays."
    assert unlink_broken(text) == f"Save cooldowns for Dig In; {spell('Tempest', 1287083)} stays."


def test_a_spell_link_to_another_spells_id_is_undone():
    abilities = {1290662: "Ravenous Feast", 192077: "Wind Rush Totem"}
    text = (f"Kick {spell('Visceral Burst', 1290662)}, soak {spell('Ravenous Feast', 1290662)}, "
            f"{spell('Wind Rush', 192077)} and {spell('Ascendance', 114052)}.")
    assert unlink_wrong(text, abilities) == (
        f"Kick Visceral Burst, soak {spell('Ravenous Feast', 1290662)}, {spell('Wind Rush', 192077)} and "
        f"{spell('Ascendance', 114052)}.")  # an id the report doesn't have: left alone


ROSTER = ["Brightleaf", "Torvek", "Mirelle", "Quillon", "Halvard", "Al"]


@pytest.mark.parametrize("text, expected", [
    ("Torvek and Mirelle (7 each).", "**Torvek** and **Mirelle** (7 each)."),
    ("Halvard's taunt", "**Halvard**'s taunt"),
    ("- **Tempest:** Torvek", "- **Tempest:** **Torvek**"),  # after a bold lead
    ("[Quillon's pull](https://x/Quillon)", "[Quillon's pull](https://x/Quillon)"),  # in a link
    ("`Halvard` stays", "`Halvard` stays"),  # code
    ("## Torvek", "## Torvek"),  # a heading
    ("Brightleafalt isn't one; Al is too short.", "Brightleafalt isn't one; Al is too short."),
    ("Torvek died. Mirelle healed Torvek.", "**Torvek** died. **Mirelle** healed Torvek."),  # only the first
    ("- **Torvek died first.** Torvek stood in it.", "- **Torvek died first.** Torvek stood in it."),  # bold lead
    ("**Torvek** died. **Torvek** again.", "**Torvek** died. Torvek again."),  # bolded by the model
])
def test_player_names_are_bold(text, expected):
    assert bold_players(text, ROSTER) == expected


def test_no_blank_line_between_a_line_and_its_list():
    text = ("**Two things:**\n\n- **Pools:** move.\n- Mana.\n\n**Deaths:**\n- Pull 34.\n\nAnd one more.\n\n"
            "1. First\n\n-# small grey text")
    assert tight_lists(text) == ("**Two things:**\n- **Pools:** move.\n- Mana.\n\n**Deaths:**\n- Pull 34.\n\n"
                                 "And one more.\n1. First\n\n-# small grey text")


async def test_pull_links_get_the_pulls_fight(night):
    report = await server.Library(wcl=FakeWCL(night).client()).report(CODE)
    base = f"https://www.warcraftlogs.com/reports/{CODE}"
    right = report.url(report.fight(13, "Sszorak"))
    assert right != f"{base}#fight=13"  # resets and other bosses in between
    other = "[pull 2](https://www.warcraftlogs.com/reports/OtherReport00001#fight=2)"
    made_up = f"On [Sszorak pull 13]({base}#fight=13) and [pull 13]({base}), see {other}."
    assert fix_pull_links(made_up, report) == (
        f"On [Sszorak pull 13]({right}) and [pull 13]({base}), see {other}.")  # no boss named, two in the report;
    # another report: left alone


def test_polish_turns_a_broken_link_into_a_proper_one():
    """The model's own link without an id is undone before the abilities are linked, so it gets the right one."""
    report = SimpleNamespace(code="X", abilities={1287083: "Tempest"}, by_boss={},
                             players={1: SimpleNamespace(name="Brightleaf")})
    text = "Brightleaf died to [Tempest](https://www.wowhead.com/spell=Tempest).\n\n- Move out."
    assert polish(text, ["Tempest [1287083]"], [cast(Report, report)]) == (
        f"**Brightleaf** died to {spell('Tempest', 1287083)}.\n- Move out.")


def test_polish_links_another_nights_abilities_and_names_too():
    """A question about Monday's report, asked in a session on tonight's: Monday's players and abilities count."""
    tonight = SimpleNamespace(code="TONIGHT", abilities={}, by_boss={}, players={})
    monday = SimpleNamespace(code="MONDAY", abilities={1287083: "Tempest"}, by_boss={},
                             players={1: SimpleNamespace(name="Torvek")})
    text = polish("Torvek and Tempest.", ["Tempest [1287083] x30"], [cast(Report, tonight), cast(Report, monday)])
    assert text == f"**Torvek** and {spell('Tempest', 1287083)}."


def test_a_masked_link_to_a_site_the_tools_dont_link_to_keeps_only_its_text():
    """Its hidden address could carry what the answer read to someone's server."""
    text = ("[Sszorak pull 13](https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21), "
            "[the guide](https://www.method.gg/guides/x), [wiki](<https://warcraft.wiki.gg/wiki/Tempest>), "
            "[see here](https://evil.example/?q=Torvek+died), [x](https://warcraftlogs.com.evil.example/a \"t\"), "
            "[y](javascript:alert(1)) and https://bare.example/stays")
    assert unlink_elsewhere(text) == (
        "[Sszorak pull 13](https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21), "
        "[the guide](https://www.method.gg/guides/x), [wiki](<https://warcraft.wiki.gg/wiki/Tempest>), "
        "see here, x, y and https://bare.example/stays")  # a bare address shows where it goes


@pytest.mark.parametrize("link, text", [
    ("[a [b] c](https://evil.example/?q=Torvek)", "a [b] c"),  # brackets in its text
    ("[a]b](https://evil.example/)", "a]b"),
    ("[[x](https://evil.example/)](https://www.wowhead.com/spell=1)", "[x](https://www.wowhead.com/spell=1)"),
    ("[x]( https://evil.example/ )", "x"),  # spaces around the address
    ("[x](https://evil.example/ 'title')", "x"),
    ("[x](<https://evil.example/> \"t\")", "x"),
    ("[x](https://evil.example\\@www.wowhead.com/)", "x"),  # browsers read the backslash as a slash
    ("[x](https://www.wowhead.com@evil.example/)", "x"),  # a login, then the real host
    ("[x](https://evil.example/(a)b)", "x"),  # parentheses in the address
    ("[x](https://www.wowhead.com/spell=1 'Tempest')", "[x](https://www.wowhead.com/spell=1 'Tempest')"),
])
def test_no_way_of_writing_a_masked_link_gets_past(link, text):
    assert unlink_elsewhere(f"see {link} now") == f"see {text} now"


ICONS = {"raid_skull": "<:raid_skull:8>", "raid_star": "<:raid_star:1>", "Paladin": "<:Paladin:20>",
         "Paladin_Holy": "<:Paladin_Holy:21>", "DeathKnight": "<:DeathKnight:30>",
         "DeathKnight_Blood": "<:DeathKnight_Blood:31>", "wipebot_spinner": "<a:wipebot_spinner:9>",
         "spell_1290001": "<:spell_1290001:40>"}
SPECS = {"Brightleaf": ("Paladin", "Holy"), "Torvek": ("DeathKnight", "Frost"), "Al": ("Paladin", "Holy")}


@pytest.mark.parametrize("text, expected", [
    pytest.param("Stack on {skull}, then {rt1} and {RT8}.",
                 "Stack on <:raid_skull:8>, then <:raid_star:1> and <:raid_skull:8>.",
                 id="a raid note's markers, every one"),
    pytest.param("Go to the skull marker, then the Star marker; skull.",
                 "Go to the <:raid_skull:8> skull marker, then the <:raid_star:1> Star marker; skull.",
                 id="before a marker named as one"),
    pytest.param("**Brightleaf** heals; Brightleaf again.",
                 "**<:Paladin_Holy:21> Brightleaf** heals; Brightleaf again.",
                 id="a player's spec, the first time"),
    pytest.param("Torvek died.", "<:DeathKnight:30> Torvek died.", id="no icon for the spec: the class's"),
    pytest.param("Holy Paladins and the shaman cooldowns.", "Holy Paladins and the shaman cooldowns.",
                 id="none for a spec or class named as such"),
    pytest.param(f"Kick {spell('Visceral Burst', 1290001)}, use {spell('Ascendance', 114052)}.",
                 f"Kick <:spell_1290001:40> {spell('Visceral Burst', 1290001)}, use {spell('Ascendance', 114052)}.",
                 id="a boss ability's before its link; none for a spell without one"),
    pytest.param("[Brightleaf](https://x) and `{skull}` stay.", "[Brightleaf](https://x) and `{skull}` stay.",
                 id="not in links or code"),
    pytest.param("Al is short; {moon} has no emoji.", "Al is short; {moon} has no emoji.", id="left alone"),
])
def test_icons_go_where_the_bot_has_them(text, expected):
    assert add_icons(text, SPECS, ICONS) == expected
    assert add_icons(text, SPECS, {}) == text


PLAN = "https://raidstrats.gg/planner?view=plan-1"
PLAN_RESULT = (f'The raid plan "Coiled Altar Plan" ({PLAN}/9): 9 scenes.\n'
               f"Scene 2 ({PLAN}/2):\n  Asked: #11 Scopi (Dscopi) (demonhunter): below right of the boss\n"
               f"  #9 Martin (hunter): right of the boss, between the cross and triangle markers\n"
               f"Scene 3 ({PLAN}/3):\n  #5 Ape Goalkeeper (evoker): far below the boss")


@pytest.mark.parametrize("text, expected", [
    pytest.param("**Grønne orbs (scene 2):** du har spot #11. I scene 3 går du ut. Scene 2 igjen.",
                 f"**[Grønne orbs]({PLAN}/2):** du har spot #11. I [scene 3]({PLAN}/3) går du ut. Scene 2 igjen.",
                 id="a lead naming the scene's subject, else the scene, once each"),
    pytest.param("- Orb-bevegelse (Scene 3): ut til grønn 1.", f"- [Orb-bevegelse]({PLAN}/3): ut til grønn 1.",
                 id="a bullet's lead"),
    pytest.param("Du står der (scene 2), sammen med Martin.", f"[Du står der]({PLAN}/2), sammen med Martin.",
                 id="a sentence's clause"),
    pytest.param("**Cptola:** lilla orb i P1, pil inn til orbene i P1 (scene 3), soaker spirits.",
                 f"**Cptola:** lilla orb i P1, [pil inn til orbene i P1]({PLAN}/3), soaker spirits.",
                 id="a clause mid-sentence"),
    pytest.param(f"**[Scene 2]({PLAN}/2)** og scene 2.", f"**[Scene 2]({PLAN}/2)** og scene 2.", id="linked already"),
    pytest.param("Scene 7 og `scene 3`.", "Scene 7 og `scene 3`.", id="a scene the results have no link for, code"),
])
def test_a_raid_plans_scenes_are_linked(text, expected):
    assert link_scenes(text, [PLAN_RESULT]) == expected


def test_two_plans_with_the_same_scene_are_left_alone():
    other = PLAN_RESULT.replace("plan-1", "plan-2")
    assert link_scenes("I scene 2.", [PLAN_RESULT, other]) == "I scene 2."


def test_a_raid_plans_raiders_have_their_class():
    classes = {"demonhunter": "DemonHunter", "hunter": "Hunter", "evoker": "Evoker"}
    assert planned_classes([PLAN_RESULT], classes) == {
        "Scopi": ("DemonHunter", ""), "Dscopi": ("DemonHunter", ""), "Martin": ("Hunter", ""),
        "Ape Goalkeeper": ("Evoker", "")}
    assert add_icons("Med Martin.", planned_classes([PLAN_RESULT], classes), {"Hunter": "<:Hunter:5>"}) == (
        "Med <:Hunter:5> Martin.")


def test_tool_results_give_the_specs_of_the_players_they_name():
    results = ["Out: Grusarn (Fury Warrior), Lïzårðmâñ (Preservation Evoker, Ape)", "Tanks: Deathadim (Blood Death "
               "Knight)", "Rowan (Paladin) and Torvek (the hunter)"]
    icons = {"Warrior_Fury": "<:a:1>", "Evoker_Preservation": "<:b:2>", "DeathKnight_Blood": "<:c:3>",
             "Paladin_Holy": "<:d:4>"}
    assert specs_in(results, icons) == {"Grusarn": ("Warrior", "Fury"), "Lïzårðmâñ": ("Evoker", "Preservation"),
                                        "Deathadim": ("DeathKnight", "Blood"), "Rowan": ("Paladin", "")}


def test_a_players_spec_is_the_reports_then_the_tools_then_the_rosters():
    report = cast(Report, SimpleNamespace(players={1: SimpleNamespace(name="Brightleaf", class_name="Paladin",
                                                                      spec="Holy")}, abilities={}, code="x"))
    icons = {"Paladin_Holy": "<:Paladin_Holy:1>", "Paladin_Retribution": "<:Paladin_Retribution:2>",
             "Warrior_Fury": "<:Warrior_Fury:3>", "Warrior_Arms": "<:Warrior_Arms:4>",
             "Priest_Holy": "<:Priest_Holy:5>"}
    roster = {"Brightleaf": ("Paladin", "Retribution"), "Grusarn": ("Warrior", "Arms"), "Miralupa": ("Priest", "Holy")}
    text = polish("Brightleaf, Grusarn and Miralupa.", ["Grusarn (Fury Warrior)"], [report], icons, roster)
    assert text == "<:Paladin_Holy:1> Brightleaf, <:Warrior_Fury:3> Grusarn and <:Priest_Holy:5> Miralupa."  # no bold


def test_a_marker_in_a_links_text_goes_after_the_link_and_a_class_named_spot_gets_no_icon():
    icons = {"raid_skull": "<:raid_skull:8>", "raid_star": "<:raid_star:1>", "Priest": "<:Priest:6>",
             "Priest_Holy": "<:Priest_Holy:5>"}
    text = "2. [en ny gateway til {skull}](https://raidstrats.gg/p/4), så [til {star}](https://raidstrats.gg/p/6)."
    assert add_icons(text, {}, icons) == ("2. [en ny gateway til](https://raidstrats.gg/p/4) <:raid_skull:8>, så "
                                          "[til](https://raidstrats.gg/p/6) <:raid_star:1>.")
    assert add_icons("en «Priest» uten navn, ikke Namsus", {"Priest": ("Priest", ""), "Namsus": ("Priest", "Holy")},
                     icons) == "en «Priest» uten navn, ikke <:Priest_Holy:5> Namsus"


def test_no_blank_line_inside_a_list_where_discord_would_end_its_nesting():
    text = "- **Healers:**\n  - Cptola: orb.\n  - Enfo: totem.\n\n  - Lizard: goalkeeper.\n- **Melee:**"
    assert tight_lists(text) == ("- **Healers:**\n  - Cptola: orb.\n  - Enfo: totem.\n  - Lizard: goalkeeper.\n"
                                 "- **Melee:**")


def test_a_boss_ability_linked_by_another_of_its_ids_gets_its_icon_by_name():
    icons = {"spell_1283489": "<:spell_1283489:7>", "ability:guillotine": "<:spell_1283489:7>"}
    text = f"Får du {spell('Guillotine', 1283485)}, si fra. {spell('Sever', 1299684)}."
    assert add_icons(text, {}, icons) == f"Får du <:spell_1283489:7> {spell('Guillotine', 1283485)}, si fra. " \
                                         f"{spell('Sever', 1299684)}."


def test_a_boss_ability_gets_its_icon_on_its_first_mention_linked_or_not():
    icons = {"ability:toxic deluge": "<:spell_1:1>", "ability:sever": "<:spell_2:2>", "ability:guillotine": "<:g:3>"}
    text = (f"- **Toxic Deluge:** ikke stå i dem. Toxic Deluge igjen.\n- **Sever:** tank-frontalen; ikke sever noe.\n"
            f"- Får du {spell('Guillotine', 1283485)}, si fra. Guillotine treffer igjen.")
    assert add_icons(text, {}, icons) == (
        "- **<:spell_1:1> Toxic Deluge:** ikke stå i dem. Toxic Deluge igjen.\n- **<:spell_2:2> Sever:** "
        f"tank-frontalen; ikke sever noe.\n- Får du <:g:3> {spell('Guillotine', 1283485)}, si fra. Guillotine "
        "treffer igjen.")  # never the lowercase word
    assert add_icons(f"Guillotine først, så {spell('Guillotine', 1283485)}.", {}, icons) == (
        f"<:g:3> Guillotine først, så {spell('Guillotine', 1283485)}.")  # the first mention, linked or not


@pytest.mark.parametrize("text, expected", [
    pytest.param("**Ja, samme bakke. Og du har rett: f.eks. nr. 2 var feil.**\n- a",
                 "**Ja, samme bakke.** Og du har rett: f.eks. nr. 2 var feil.\n- a", id="the first sentence"),
    pytest.param("**Vanish på [pull 3. Two](https://x) var sen.**", "**Vanish på [pull 3. Two](https://x) var sen.**",
                 id="not in a link"),
    pytest.param("**One sentence, f.eks. nr. 2.**\n- a", "**One sentence, f.eks. nr. 2.**\n- a", id="one"),
    pytest.param("**Bold** lead. Then text.", "**Bold** lead. Then text.", id="not a bold line"),
])
def test_only_the_verdicts_first_sentence_is_bold(text, expected):
    assert verdict_bold(text) == expected
