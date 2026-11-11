"""The build comparison's gear by slot and stats (report/build.py)."""
from types import SimpleNamespace
from typing import cast

from wipebot.core import knowledge
from wipebot.core.knowledge import KnowledgeError
from wipebot.core.report import build
from wipebot.core.report.base import Report


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
    assert await build.gear_by_slot(det) == (  # the sum, which the comparison below it reads, before the pieces
        "Gear at the pull, summed over the pieces read (before gems and enchants): Versatility 225 (54%), Crit 128 "
        "(31%), Mastery 62 (15%), Haste 0 (0%). By slot (item level from the log; secondary and minor stats from "
        "Wowhead's tooltip with the piece's bonus IDs, before gems and enchants): head Stare (321): 128 Crit, 62 "
        "Mastery, 82 Leech; neck Reliquary (334): stats unknown (Wowhead didn't answer); ring Band (321): 225 "
        "Versatility (Wowhead's tooltip is item level 315: its stats are at that level).")
    assert await build.gear_by_slot({}) == "Gear by slot: not in the log (needs advanced combat logging)."


def test_the_log_s_stats_are_not_said_to_be_the_gear_s():
    # #1362 of the bot's answers: a Balance Druid's log gave 2925 Mastery where their pieces sum to 969, and the bot
    # called their stats skewed from it (#1368, where the raider showed it wrong).
    det = {"combatantInfo": {"stats": {"Mastery": {"max": 2925}, "Haste": {"max": 1067}, "Crit": {"max": 522}}}}
    assert "can differ a lot from what the gear gives" in build.gear_stats(det)


async def test_the_top_players_stats_are_no_reason_to_take_a_stat_over_one_the_guide_ranks_higher():
    # me-stat-priority: the top players' gear had more Crit than a Farseer's, and an answer told them to take Crit
    # over Mastery, which their spec guide ranks first.
    def stats(crit: int, haste: int, mastery: int, vers: int) -> dict:
        return {"Crit": {"max": crit}, "Haste": {"max": haste}, "Mastery": {"max": mastery},
                "Versatility": {"max": vers}}

    async def parse_stats(asked):
        return [stats(400, 300, 250, 50), stats(450, 250, 250, 50)]
    rep = cast(Report, SimpleNamespace(wcl=SimpleNamespace(parse_stats=parse_stats)))
    top = [{"name": n, "report": {"code": c, "fightID": 1}} for n, c in (("Torvek", "a"), ("Mirelle", "b"))]
    gaps = {"top": top, "det": {"combatantInfo": {"stats": stats(250, 250, 400, 100)}}}
    assert await build._top_stats(rep, gaps) == (
        "Stats of the top 2 at their pulls, from their logs (each secondary's share of their secondary ratings, the "
        "median and the range; shares compare across item levels): Crit 42% (40-45), Haste 28% (25-30), Mastery 25% "
        "(25-25), Versatility 5% (5-5); theirs: Crit 25%, Haste 25%, Mastery 40%, Versatility 10%. These are what "
        "the top players' gear gave them, not a stat priority: the spec guide gives that (spec_guide), and a gap from "
        "them is a note beside it, not a new priority: it is no reason to take a stat over one the guide ranks higher.")
