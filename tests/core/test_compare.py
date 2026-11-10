"""A pull next to a kill (report/compare.py), on made-up health tracks: the real comparison is pinned by a local
tool case (test_tools.py), since another guild's kill can't be published."""
from typing import cast

from wipebot.core.report import Report, compare


class Rep:
    """What the pace line reads of a report: nothing decided the wipe, no buff at the end, no deaths."""

    async def decided_at(self, f):
        return None

    async def end_buffs(self, f):
        return []

    async def keep(self, key, deps, get, *a):
        return []

    async def deaths(self, f):
        return []


OURS, KILL = {"startTime": 0, "endTime": 120_000}, {"startTime": 0, "endTime": 200_000}


async def pace(ours: list[tuple[float, float]], theirs: list[tuple[float, float]]) -> str:
    return await compare._pace(cast(Report, Rep()), OURS, KILL, "CODE", 1, {"health": ours}, {"health": theirs})


async def test_a_pull_behind_early_and_ahead_at_the_end_is_ahead_not_minus_points_behind():
    """Slow start, fast end: 10 points behind at 0:30, 10 ahead at 2:00. It said "-10 points behind at 2:00"."""
    text = await pace([(0, 100), (30_000, 90), (60_000, 70), (90_000, 40), (120_000, 20)],
                      [(0, 100), (30_000, 80), (60_000, 60), (90_000, 45), (120_000, 30)])
    assert "-" not in text.split("):", 1)[1]
    assert "behind from 0:30 (10 points), no longer by 1:30, 10 points ahead at 2:00 (ours 20%, the kill's 30%)" in text


async def test_a_pull_behind_to_the_end_says_since_when_and_by_how_much():
    text = await pace([(0, 100), (30_000, 90), (60_000, 80), (120_000, 60)],
                      [(0, 100), (30_000, 80), (60_000, 60), (120_000, 30)])
    assert "behind from 0:30 (10 points), 30 points behind at 2:00 (ours 60%, the kill's 30%)" in text


async def test_a_pull_that_kept_the_kills_pace_says_where_it_stood_at_the_end():
    text = await pace([(0, 100), (60_000, 61), (120_000, 20)], [(0, 100), (60_000, 60), (120_000, 30)])
    assert "kept it (within 3 points or ahead), 10 points ahead at 2:00 (ours 20%, the kill's 30%)" in text
    close = await pace([(0, 100), (120_000, 31)], [(0, 100), (120_000, 30)])
    assert "kept it (within 3 points or ahead), 1 point behind at 2:00 (ours 31%, the kill's 30%)" in close
    level = await pace([(0, 100), (120_000, 30.4)], [(0, 100), (120_000, 30)])
    assert "kept it (within 3 points or ahead), level with it at 2:00 (ours 30%, the kill's 30%)" in level
