"""The healing tool's reading of the raid's damage taken, on made-up graphs."""
from wipebot.core.report import fight, healing


def _graph(per_second: list[float]) -> dict:
    return {"series": [{"name": "Total", "pointStart": 0, "pointInterval": 1000, "data": per_second}]}


async def test_a_pull_without_spikes_names_its_peak_by_the_seconds_the_timeline_names(rep, monkeypatch):
    # healing said "highest 1.53M/s at 6:37" of the 5 s that pull_timeline's averages print as "6:35 1.53M": one
    # named them by their middle, the other by where they begin. Both now say where they begin.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [100.0] * 60
    taken[35:40] = [140.0] * 5
    assert healing._even(f, _graph(taken), f["endTime"]) == (
        "the raid's damage taken stayed under 1.5x its median of 100/s, highest 140/s over the 5 s from 0:35")

    async def keep(*_):
        return _graph(taken)
    monkeypatch.setattr(rep, "keep", keep)
    assert " · 0:35 140 · " in await fight._raid_damage(rep, f, 0, f["endTime"])


def test_a_peak_where_the_pull_is_cut_says_the_seconds_that_were_averaged():
    # The average around a pull's last points (or its first) has only the seconds on one side of them: "over the 5 s
    # from 0:29" stood for the 3 s up to a wipe decided at 0:32, and for the 3 s a pull opens with.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    late = _graph([100.0] * 30 + [140.0] * 30)
    assert healing._even(f, late, 32_000).endswith("highest 127/s over the 3 s from 0:29")
    assert healing._even(f, _graph([140.0] * 2 + [100.0] * 58), 60_000).endswith("highest 127/s over the 3 s from 0:00")
    # Warcraft Logs' graph has 240 points whatever the pull's length: the points 5 s hold are not always five.
    fine = {"series": [{"name": "Total", "pointStart": 0, "pointInterval": 250,
                        "data": [100.0] * 100 + [140.0] * 20 + [100.0] * 120}]}
    assert healing._even(f, fine, 60_000).endswith("highest 138/s over the 5 s from 0:24")
