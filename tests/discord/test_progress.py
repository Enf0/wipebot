from wipebot.discord import progress
from wipebot.discord.host import sdk_tools
from wipebot.discord.progress import doing
from wipebot.discord.prompt import BOT_TOOLS
from wipebot.discord.texts import DOING, STEPS_NO


async def test_every_tool_says_what_it_does_in_both_languages():
    tools = {t.name for t in await sdk_tools()} | {t.name for t in BOT_TOOLS}
    assert tools <= set(DOING) and tools <= set(STEPS_NO)


def test_a_call_names_its_boss_pull_and_spec_never_a_player():
    assert doing("pull_summary", {"boss": "Coiled Altar", "pull": 7}) == "Summing up the pull (Coiled Altar, pull 7)"
    assert doing("player_pull", {"player": "Torvek", "pull": "best wipe"}) == (
        "Reading a player's pull (the best wipe)")
    assert doing("mechanic_hits", {"boss": "Sszorak", "all_pulls": True}) == (
        "Counting mechanic hits (Sszorak, every pull)")
    assert doing("spec_guide", {"spec": "Holy Paladin"}) == "Reading the spec guide (Holy Paladin)"
    assert doing("raid_note", {"boss": "**<@1> Sszorak**"}) == "Reading the raid note (1 Sszorak)"  # no markdown
    assert doing("save_note_or_plan", {}) == "Save raid note or plan"  # not in DOING: its title


def test_the_line_and_the_steps_speak_norwegian_with_the_games_words_in_english():
    assert doing("pull_summary", {"boss": "Coiled Altar", "pull": 7}, "no") == (
        "Oppsummerer pullet (Coiled Altar, pull 7)")
    assert doing("player_pull", {"player": "Torvek", "pull": "best wipe"}, "no") == (
        "Leser en spillers pull (beste wipe)")
    assert doing("mechanic_hits", {"boss": "Sszorak", "all_pulls": True}, "no") == (
        "Teller treff av mechanics (Sszorak, alle pulls)")
    calls = [("roster", False), ("death_recap", False), ("death_recap", False), ("pull_timeline", True)]
    assert progress.steps(calls) == "-# ✓ Roster · ✓ Death recap ×2 · ✗ Pull timeline"
    assert progress.steps(calls, "no") == "-# ✓ Roster · ✓ Death recap ×2 · ✗ Tidslinja"
    assert progress.step("save_note_or_plan", "no") == "Save raid note or plan"  # no Norwegian for it: its title
