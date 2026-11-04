"""The stopgap for bonus IDs in Wowhead's item tooltip (knowledge/wowhead_bonus.py), and a piece as worn
(knowledge.worn_item)."""
from wipebot.core.knowledge import bosses
from wipebot.core.knowledge.wowhead_bonus import bonus_arg


def test_the_bonus_option_is_taken_out_of_the_command_line():
    argv = ["x", "entity", "item", "251148", "--bonus", "13440:42", "--no-include-comments"]
    assert bonus_arg(argv) == "13440:42"
    assert argv == ["x", "entity", "item", "251148", "--no-include-comments"]
    assert bonus_arg(argv) is None


async def test_a_worn_piece_has_its_item_level_and_stats_from_its_tooltip(monkeypatch):
    # The tooltip of a ring with its bonus IDs, as Wowhead gave it on 4 November 2026
    text = ("Pilfered Precious Band Mythic+ Item Level 321 Upgrade Level: Hero 6/6 Binds when picked up "
            "Unique-Equipped Finger +1,895 Stamina + 150 Critical Strike + 225 Versatility + 64 Speed Prismatic "
            "Socket Requires Level 90")
    asked = []

    async def tooltip(item_id, bonus_ids=None):
        asked.append((item_id, bonus_ids))
        return "Pilfered Precious Band", text
    monkeypatch.setattr(bosses, "_item_tooltip", tooltip)
    assert await bosses.worn_item(251148, [13440, 42]) == (
        "Pilfered Precious Band", 321, [("Critical Strike", 150), ("Versatility", 225), ("Speed", 64)])
    assert asked == [(251148, [13440, 42])]
