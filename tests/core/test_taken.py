"""The raid's damage taken, worked out of Warcraft Logs' graph windows (taken.py), on made-up damage."""
import itertools
from typing import cast

import pytest

from tests.support import FakeWCL, damage_graph
from wipebot.core.report import fight, taken
from wipebot.core.report.taken import (
    TAKEN_FIRST_MS,
    TAKEN_LEAD_MS,
    TAKEN_STEP_MS,
    TAKEN_WINDOW_MS,
    Taken,
    raid_taken,
    stitch,
    unbox,
    windows,
)


def _truth(t: float) -> float:
    """A made-up raid's damage taken per second at report time `t`: a different number every second."""
    return 1000.0 + (t // TAKEN_STEP_MS) % 37 * 10


def _graphs(f: dict, rate=_truth) -> list[dict]:
    return [damage_graph(rate, a, b, f) for a, b in windows(f)]


def test_a_pull_is_asked_in_windows_of_240_one_second_steps_from_10_s_before_it_and_5_s_before_the_last_ends():
    f = {"id": 1, "startTime": 10_000, "endTime": 10_000 + 494_740}  # The Twin Fangs pull 7's 8:15
    got = windows(f)
    assert len(got) == 3
    assert all(b - a == TAKEN_WINDOW_MS == 240 * TAKEN_STEP_MS for a, b in got)
    assert got[0][0] == f["startTime"] - TAKEN_FIRST_MS == f["startTime"] - 2 * TAKEN_LEAD_MS
    assert all(a == b - TAKEN_LEAD_MS for (_, b), (a, _) in itertools.pairwise(got))  # steps the one before gave
    assert got[-2][1] < f["endTime"] <= got[-1][1]
    assert len(windows({"id": 1, "startTime": 0, "endTime": 230_000})) == 1
    assert len(windows({"id": 1, "startTime": 0, "endTime": 230_001})) == 2
    assert len(windows({"id": 1, "startTime": 0, "endTime": 465_000})) == 2
    assert len(windows({"id": 1, "startTime": 0, "endTime": 465_001})) == 3


@pytest.mark.parametrize("length", [494_740, 230_001, 230_000, 41_650, 3_400])  # windows cut at the end; under one
def test_each_second_reads_the_damage_taken_in_it_exactly_though_each_point_is_10_s_of_it(length):
    # Warcraft Logs' graph averages each point over the 10 steps around it: over a whole 8-minute pull some 20 s,
    # which flattened The Twin Fangs' bursts (1.93M/s over 2 s by the DamageTaken table read 1.44M/s); asked in 60 s
    # windows still 2.5 s, which put a star on Sszorak's 0:35, where the table has 1.40M/s against a 1.47M/s median, for
    # the wipe's burst after 0:40. Each step's own damage is worked back out of the points, exactly.
    f = {"id": 1, "startTime": 10_000, "endTime": 10_000 + length}
    got = stitch(f, _graphs(f))
    assert got["pointStart"] == f["startTime"] and got["pointInterval"] == TAKEN_STEP_MS
    whole = length // TAKEN_STEP_MS
    assert got["data"][:whole] == pytest.approx([_truth(f["startTime"] + i * TAKEN_STEP_MS) for i in range(whole)])
    t = Taken(got["pointStart"], got["pointInterval"], got["data"])
    assert t.damage(f["startTime"], f["endTime"]) == pytest.approx(  # the last second, cut by the pull's end, too
        sum(_truth(f["startTime"] + i * TAKEN_STEP_MS) for i in range(whole))
        + _truth(f["startTime"] + whole * TAKEN_STEP_MS) * (length % TAKEN_STEP_MS) / 1000)


def test_damage_counted_in_the_seconds_before_a_pull_stays_before_it(caplog):
    # Some fights count a hit just before their start: 15,216 in the second before fight 41 of 8TcXMAP2gHQGr9pW's,
    # which a window from 5 s before couldn't tell from the pull's first seconds. Its first window starts 10 s before.
    f = {"id": 1, "startTime": 10_000, "endTime": 10_000 + 300_000}

    def rate(t: float) -> float:
        return 15_216.0 if t < f["startTime"] else _truth(t)
    early = {**f, "startTime": f["startTime"] - 2 * TAKEN_STEP_MS}  # counted from 2 s before it
    got = stitch(f, [damage_graph(rate, a, b, early) for a, b in windows(f)])["data"]
    assert got == pytest.approx([_truth(f["startTime"] + i * TAKEN_STEP_MS) for i in range(300)])
    assert "doesn't unbox" not in caplog.text
    flat = damage_graph(lambda t: 1000.0, *windows(f)[0], early)["series"][0]["data"]
    assert unbox(flat) == pytest.approx([0.0] * 8 + [1000.0] * 232)  # the first window's own 5 steps, worked out


async def test_a_wipes_last_burst_stays_in_its_own_seconds(rep, patch_wcl):
    # Sszorak pull 1 of a 41.65 s wipe: the raid took 18.18M/s over 0:40-0:41.65 and 1.40M/s over 0:35-0:40, which
    # pull_timeline read as 11.12M and 3.64M*, smoothed by Warcraft Logs' graph over 2.5 s.
    f = {**rep.by_boss[rep.boss("Sszorak")][0]}
    t0 = f["startTime"]
    f["endTime"] = t0 + 41_650

    def rate(t: float) -> float:
        return 30e6 if t0 + 40_000 <= t < t0 + 41_000 else 1e6
    patch_wcl(rep, graph_windows=lambda code, items, *_, **__: {name: damage_graph(rate, a, b, f)
                                                                for name, _, a, b in items})
    got = await fight.raid_damage(rep, f, t0, f["endTime"])
    assert got is not None and got[7] == pytest.approx(1e6)  # 0:35-0:40
    assert got[8] == pytest.approx((30e6 + 0.65e6) / 1.65)  # 0:40-0:41.65


def test_a_window_whose_points_arent_boxes_of_its_steps_is_read_as_its_points(caplog):
    f = {"id": 1, "startTime": 10_000, "endTime": 100_000}
    (a, b), = windows(f)
    flat = {"series": [{"name": "Total", "pointStart": a, "pointInterval": TAKEN_STEP_MS, "data": [500.0] * 91}]}
    assert unbox(flat["series"][0]["data"], [0.0] * 5) is None  # its first point isn't the damage of its first 5 s
    assert stitch(f, [flat])["data"][:3] == [500.0] * 3
    assert "doesn't unbox" in caplog.text
    other = damage_graph(_truth, a, a + 2 * TAKEN_WINDOW_MS, f)  # not the steps asked: read as its points say too
    got = stitch(f, [other])["data"]
    assert len(got) == 90 and got[:5] != pytest.approx([_truth(f["startTime"] + i * 1000) for i in range(5)])


def test_a_window_without_damage_reads_none_and_a_pull_without_any_has_none():
    f = {"id": 1, "startTime": 0, "endTime": 600_000}

    def rate(t: float) -> float:
        return 0.0 if 225_000 <= t < 465_000 else _truth(t)
    graphs = _graphs(f, rate)
    graphs[1] = {"series": []}
    got = stitch(f, graphs)["data"]
    assert got == pytest.approx([rate(i * 1000) for i in range(600)])  # the window after it unboxed from its zeros
    assert stitch(f, [{"series": []}] * 3)["data"] == []


def test_damage_over_any_stretch_counts_the_part_of_each_step_inside_it():
    t = Taken(1_000, 250, [400.0, 800.0, 0.0, 1200.0])  # per second, each for its 250 ms
    assert t.damage(1_000, 2_000) == pytest.approx(100 + 200 + 0 + 300)
    assert t.damage(1_125, 1_375) == pytest.approx(50 + 100)
    assert t.damage(0, 5_000) == t.damage(1_000, 2_000)  # nothing outside it
    assert t.rate(1_250, 1_500) == pytest.approx(800) and t.rate(1_500, 1_500) == 0.0
    assert Taken(0, 250, []).damage(0, 1_000) == 0.0


async def test_every_pulls_windows_go_in_one_request_and_are_kept(rep, patch_wcl):
    asked = []

    def graph_windows(code, items, data_type, **filters):
        asked.append((items, data_type, filters))
        return {name: damage_graph(_truth, a, b, f) for name, fid, a, b in items for f in pulls if f["id"] == fid}
    patch_wcl(rep, graph_windows=graph_windows)
    pulls = rep.by_boss[rep.boss("Sszorak")][:3]
    got = await raid_taken(rep, pulls)
    assert len(asked) == 1 and asked[0][1:] == ("DamageTaken", {"hostilityType": "Friendlies"})
    assert [(a, b) for _, _, a, b in asked[0][0]] == [w for f in pulls for w in windows(f)]
    assert all(isinstance(got[f["id"]], Taken) for f in pulls)
    again = await raid_taken(rep, pulls)
    assert len(asked) == 1 and [t and t.data for t in again.values()] == [t and t.data for t in got.values()]


async def test_the_timelines_5_s_averages_are_the_damage_in_each_5_s_within_the_window_asked(rep, patch_wcl):
    f = rep.by_boss[rep.boss("Sszorak")][0]
    t0 = f["startTime"]
    patch_wcl(rep, graph_windows=lambda code, items, *_, **__: {name: damage_graph(_truth, a, b, f)
                                                                for name, _, a, b in items})
    t = (await raid_taken(rep, [f]))[f["id"]]
    assert t is not None
    got = await fight.raid_damage(rep, f, t0 + 12_000, t0 + 23_000)
    assert got is not None and list(got) == [2, 3, 4]  # 0:12-0:15, 0:15-0:20, 0:20-0:23
    assert got[2] == pytest.approx(t.rate(t0 + 12_000, t0 + 15_000))
    assert got[3] == pytest.approx(sum(_truth(t0 + s * 1000) for s in range(15, 20)) / 5)
    assert got[4] == pytest.approx(t.rate(t0 + 20_000, t0 + 23_000))


async def test_a_pull_without_damage_taken_recorded_has_none(rep):
    f = rep.by_boss[rep.boss("Sszorak")][0]
    assert (await raid_taken(rep, [f]))[f["id"]] is None  # FakeWCL: graphs without series
    assert await fight.raid_damage(rep, f, f["startTime"], f["endTime"]) is None
    assert any(q.startswith("graph_windows DamageTaken") for q in cast(FakeWCL, rep.wcl).queries)
    assert taken.TAKEN_LEAD_MS % TAKEN_STEP_MS == 0
