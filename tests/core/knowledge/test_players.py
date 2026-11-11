"""A character's experience from Raider.IO (knowledge/players.py), on a made-up profile."""
from wipebot.core.knowledge import fetch, players


async def test_raider_ios_progress_is_named_as_its_own_count(monkeypatch):
    """player_history printed Raider.IO's "6/8 M" under the kills Warcraft Logs ranks, 5 Mythic bosses there, with
    nothing on the line to say whose count the 6 was."""
    async def fake_cli(*args, **kwargs):
        assert args[:2] == ("raiderio", "character")
        return {"character": {"name": "Mirelle", "active_spec_name": "Elemental", "class_name": "Shaman",
                              "realm_name": "Brightwater", "profile_url": "https://raider.io/characters/eu/x/Mirelle"},
                "guild": {"name": "The Copper Lanterns"},
                "raiding": {"progression": [{"raid_slug": "the-old-raid", "summary": "1/1 H"},
                                            {"raid_slug": "the-venomous-abyss", "summary": "6/8 M"}]},
                "mythic_plus": {"current_score": 3729.4, "season": "season-mn-2"}}
    monkeypatch.setattr(fetch, "cli", fake_cli)
    lines = (await players.character_experience_text("Mirelle", "Brightwater", "eu")).splitlines()
    assert lines[0].endswith("guild The Copper Lanterns. Source: Raider.IO https://raider.io/characters/eu/x/Mirelle")
    assert lines[1] == ("Raid progression as Raider.IO counts bosses killed (not Warcraft Logs' ranked kills; most "
                        "recent raids last): the-old-raid 1/1 H; the-venomous-abyss 6/8 M")
    assert lines[2] == "Mythic+ score 3729 (season-mn-2)."
