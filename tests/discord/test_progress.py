from wipebot.discord.host import sdk_tools
from wipebot.discord.progress import DOING, doing
from wipebot.discord.prompt import BOT_TOOLS


async def test_every_tool_says_what_it_does():
    assert {t.name for t in await sdk_tools()} | {t.name for t in BOT_TOOLS} <= set(DOING)


def test_a_call_names_its_boss_pull_and_spec_never_a_player():
    assert doing("pull_summary", {"boss": "Coiled Altar", "pull": 7}) == "Summing up the pull (Coiled Altar, pull 7)"
    assert doing("player_pull", {"player": "Torvek", "pull": "best wipe"}) == (
        "Reading a player's pull (the best wipe)")
    assert doing("mechanic_hits", {"boss": "Sszorak", "all_pulls": True}) == (
        "Counting mechanic hits (Sszorak, every pull)")
    assert doing("spec_guide", {"spec": "Holy Paladin"}) == "Reading the spec guide (Holy Paladin)"
    assert doing("raid_note", {"boss": "**<@1> Sszorak**"}) == "Reading the raid note (1 Sszorak)"  # no markdown
    assert doing("save_note_or_plan", {}) == "Save raid note or plan"  # not in DOING: its title
