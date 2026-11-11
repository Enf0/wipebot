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
