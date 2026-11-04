"""The build comparison's gear by slot (report/build.py)."""
from wipebot.core import knowledge
from wipebot.core.knowledge import KnowledgeError
from wipebot.core.report import build


async def test_each_piece_has_its_item_level_from_the_log_and_its_stats_from_its_tooltip(monkeypatch):
    # #1247 of the bot's answers: a raider asked for each piece's stats, which the totals alone don't give.
    async def worn(item_id, bonus_ids):
        if item_id == 2:
            raise KnowledgeError("no answer")
        return {1: ("Stare", 321, [("Critical Strike", 128), ("Mastery", 62), ("Leech", 82)]),
                3: ("Band", 315, [("Versatility", 225)])}[item_id]
    monkeypatch.setattr(knowledge, "worn_item", worn)
    det = {"combatantInfo": {"gear": [
        {"id": 3, "slot": 11, "name": "Band", "itemLevel": 321, "bonusIDs": [1]},
        {"id": 1, "slot": 0, "name": "Stare", "itemLevel": 321, "bonusIDs": [13440]},
        {"id": 2, "slot": 1, "name": "Reliquary", "itemLevel": 334},
        {"id": 0, "slot": 3}]}}
    assert await build.gear_by_slot(det) == (
        "Gear by slot at the pull (item level from the log; secondary and minor stats from Wowhead's tooltip with "
        "the piece's bonus IDs, before gems and enchants): head Stare (321): 128 Crit, 62 Mastery, 82 Leech; neck "
        "Reliquary (334): stats unknown (Wowhead didn't answer); ring Band (321): 225 Versatility (Wowhead's tooltip "
        "is item level 315: its stats are at that level).")
    assert await build.gear_by_slot({}) == "Gear by slot: not in the log (needs advanced combat logging)."
