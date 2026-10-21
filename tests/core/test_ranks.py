"""Rankings (report/ranks.py): a kill's parse, and where an amount stands among a spec's ranked kills."""
from typing import cast

import pytest

from wipebot.core.report import Player, ranks
from wipebot.core.wcl import WCL, WCLError


async def test_a_kill_not_ranked_yet_is_asked_again(rep, patch_wcl):
    # Right after a kill Warcraft Logs may have no rankings for it yet, or every player's "-"; neither may stick.
    f = next(f for f in rep.fights if f["kill"])
    p = next(iter(rep.players.values()))
    dash = {"name": p.name, "rankPercent": "-", "bracketPercent": 0, "totalParses": 0}
    answers = [[], [{"roles": {"healers": {"characters": [dash]}}}],
               [{"roles": {"healers": {"characters": [{"name": p.name, "rankPercent": 42, "totalParses": 900}]}}}]]
    patch_wcl(rep, rankings=lambda code, fight_id, metric: answers.pop(0))
    assert "not ranked by Warcraft Logs (yet" in await ranks.parse(rep, f, p)
    assert "not ranked by Warcraft Logs (yet" in await ranks.parse(rep, f, p)
    assert "42nd percentile of 900" in await ranks.parse(rep, f, p)
    assert "42nd percentile of 900" in await ranks.parse(rep, f, p)  # now remembered: no third question


def ranked(total: int, partition: int | None = None) -> WCL:
    """A spec with `total` ranked kills, listed as Warcraft Logs lists them: 100 a page, no page past 20; with
    `partition`, in that partition, page 1 falling back to it from the empty default."""
    class Ranked:
        async def ranking_page(self, encounter, class_name, spec, difficulty, metric, page, asked=None):
            assert page <= ranks.MAX_RANKING_PAGE
            if asked != partition and not (page == 1 and asked is None):
                return [], False, asked
            rows = [{"amount": 10000 - r} for r in range((page - 1) * 100, min(page * 100, total))]
            return rows, page * 100 < total, partition
    return cast(WCL, Ranked())


async def test_spec_distribution_finds_the_last_page_and_the_percentiles():
    n, exact, points = await ranks.spec_distribution(ranked(1234), 1, "Rogue", "Assassination", 5, "dps")
    assert (n, exact) == (1234, True)
    assert points == [("90th percentile", 10000 - 123), ("75th percentile", 10000 - 308),
                      ("50th percentile", 10000 - 616), ("25th percentile", 10000 - 925)]


async def test_spec_distribution_reads_every_page_from_the_partition_page_1_came_from():
    # A new patch's default partition is empty: page 1 comes from the one before, and so must page 2 and on.
    n, exact, _ = await ranks.spec_distribution(ranked(1269, partition=3), 1, "Rogue", "Assassination", 5, "dps")
    assert (n, exact) == (1269, True)


async def test_spec_distribution_refuses_an_empty_page_after_more_pages_follow():
    class Partial:
        async def ranking_page(self, encounter, class_name, spec, difficulty, metric, page, partition=None):
            return ([{"amount": 1}] * 100, True, None) if page == 1 else ([], False, None)
    with pytest.raises(WCLError, match="says more follow"):
        await ranks.spec_distribution(cast(WCL, Partial()), 1, "Rogue", "Assassination", 5, "dps")


async def test_spec_distribution_past_the_2000_warcraft_logs_lists():
    # With the real count from a report's rankings, the percentiles that fall in the list.
    n, exact, points = await ranks.spec_distribution(ranked(5458), 1, "Shaman", "Restoration", 5, "hps", 5458)
    assert (n, exact) == (5458, True)
    assert points == [("90th percentile", 10000 - 545), ("75th percentile", 10000 - 1364)]  # 50th: rank 2729
    # Without it, ranks: not "the 50th percentile of 2000".
    n, exact, points = await ranks.spec_distribution(ranked(5458), 1, "Shaman", "Restoration", 5, "hps")
    assert (n, exact) == (2000, False)
    assert [label for label, _ in points] == ["100th best", "500th best", "1000th best", "2000th best"]


async def test_a_wipe_benchmark_says_the_percentile_it_means_and_a_healers_caveat(rep):
    f = rep.fight(13)
    p = rep.player("Enfo")
    spread = ranks.Spread(2000, False, [("100th best", 500.0), ("1000th best", 300.0), ("2000th best", 200.0)])
    line = ranks.benchmark_line(f, p, 400.0, spread)
    assert line and line.startswith("  against kills: 400/s alive is above the 1000th best (the 50th percentile or "
                                    "better) of the more than 2000 ranked Restoration Shaman kills of Sszorak")
    assert line and line.endswith("Healing on a wipe is capped by damage taken: weak evidence on its own.")
    dps = Player(p.id, p.name, p.class_name, "Elemental", "dps")
    line = ranks.benchmark_line(f, dps, 100.0, spread)
    assert line and "below the 2000th best of the more than 2000" in line and "capped by damage taken" not in line
