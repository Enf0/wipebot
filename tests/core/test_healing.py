"""The healing tool's reading of the raid's damage taken, on made-up graphs."""
from wipebot.core.report import fight, healing


def _graph(per_second: list[float], start: float = 0) -> dict:
    return {"series": [{"name": "Total", "pointStart": start, "pointInterval": 1000, "data": per_second}]}


async def test_a_pull_without_spikes_names_its_peak_by_the_seconds_the_timeline_names(rep, monkeypatch):
    # healing said "highest 1.53M/s at 6:37" of the 5 s that pull_timeline's averages print as "6:35 1.53M": one
    # named them by their middle, the other by where they begin. Both now say where they begin.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [100.0] * 60
    taken[35:40] = [140.0] * 5
    assert healing._even(f, _graph(taken), f["endTime"]) == (
        "the raid's damage taken stayed under 1.5x its median of 100/s over any 5 s, highest 140/s over the 5 s from "
        "0:35")

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


def test_a_peak_over_the_bar_for_under_3_s_is_no_spike_and_says_so():
    # A spike lasts 3 s or more: a 5 s average over 1.5x the median for one second isn't one, and "stayed under 1.5x
    # its median, highest 152/s" said of a 100/s median was false.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [100.0] * 60
    taken[28:33] = [120.0, 160.0, 200.0, 160.0, 120.0]
    assert not healing._spikes(f, _graph(taken), f["endTime"])
    assert healing._even(f, _graph(taken), f["endTime"]) == (
        "the raid's damage taken reached 1.5x its median of 100/s for under 3 s at a time, highest 152/s over the 5 s "
        "from 0:28")


def test_a_pulls_busiest_stretches_are_built_as_its_spikes_are():
    # The biggest few runs of 5 s averages at 1.25x the median, runs this close together joined as spikes are, none
    # overlapping, shown in time order.
    f = {"id": 1, "startTime": 0, "endTime": 120_000}
    taken = [100.0] * 120
    taken[10:15] = [135.0] * 5  # the smallest of four: left out
    taken[30:33] = taken[37:40] = [170.0] * 3  # their averages' runs 3 s apart: one stretch
    taken[60:70] = [140.0] * 10
    taken[90:96] = [135.0] * 6
    graph = _graph(taken)
    assert not healing._spikes(f, graph, f["endTime"])
    busiest = healing._biggest([f], {1: graph}, {1: f["endTime"]}, healing.BUSY_TIMES, healing.BUSIEST_SHOWN)
    assert [(s.start // 1000, s.end // 1000) for s in busiest] == [(29, 41), (61, 69), (91, 95)]


def test_a_stretch_is_ranked_by_its_own_points_not_the_one_after_it():
    # Warcraft Logs' graph points are 2.06 s apart, not whole milliseconds: a stretch's end, a step after its last
    # point, can be a hair past the next point, and that point, outside it, counted toward its rank. On The Twin
    # Fangs pull 7, 3:46-3:52 (4.26M on its own points) was shown as one of the three busiest before 3:38-3:44 (4.30M).
    step = 2061.4166666666665
    taken = [100.0] * 60
    taken[7:11] = [150.0, 150.0, 150.0, 120.0]  # a stretch on points 7-9, its end a hair past point 10
    taken[20:23] = [170.0] * 3
    f = {"id": 1, "startTime": 0, "endTime": 124_000}
    graph = {"series": [{"name": "Total", "pointStart": 0, "pointInterval": step, "data": taken}]}
    assert [round(s.start / step) for s in healing._spikes(f, graph, f["endTime"], healing.BUSY_TIMES)] == [20, 7]


def _flat_but(f: dict, busy: dict[int, float]) -> dict:
    """A graph of the raid's damage taken at 100/s over pull `f`, but at the seconds `busy` gives."""
    taken = [busy.get(i, 100.0) for i in range(int((f["endTime"] - f["startTime"]) // 1000))]
    return _graph(taken, f["startTime"])


async def test_a_pull_without_spikes_shows_its_busiest_stretches_and_names_them_so(rep, patch_wcl):
    # On a kill whose damage was high all through, healing printed only "No spikes: the raid's damage taken stayed
    # under 1.5x its median of 1.11M/s, highest 1.53M/s over the 5 s from 6:35", and answers took "no spikes" for
    # their verdict on the healers though no healing cooldown went into that worst stretch. It now says what is so,
    # then shows the pull's busiest stretches as it shows spikes, and calls them nothing else.
    busy = {i: 135.0 for i in [*range(20, 30), *range(80, 86)]}
    patch_wcl(rep, graphs=lambda code, fights, *_, **__: {f["id"]: _flat_but(f, busy) for f in fights})
    text = await healing.healing(rep, 13)
    head, *lines = text.splitlines()
    assert head.startswith("The raid's busiest stretches of damage taken and the healing into them, ")
    assert head.endswith("A spike: raid damage taken at 1.5x the pull's median or more over 5 s, at least 3 s long; "
                         "where a pull has none, its busiest stretches: at 1.25x or more, as long.")
    assert lines[0] == ("The raid's damage taken stayed under 1.5x its median of 100/s over any 5 s, highest 135/s "
                        "over the 5 s from 0:20; its 2 busiest stretches:")
    assert lines[1] == "0:21-0:29 (8 s): 0/s taken (no damage recorded); healing 0/s, - of the damage taken."
    assert lines[2].startswith("  Healers: ")
    assert lines[3].startswith("  Healing cooldowns used (or up to 5 s before): ")
    assert lines[4].startswith("1:21-1:25 (4 s): ")
    assert "Each healer in the busiest stretches against the rest of the time" in text
    assert "in the busiest stretches, " in text and "healing cooldowns in a busiest stretch" in text
    assert "spike" not in "\n".join(lines)


async def test_a_pull_under_the_lower_bar_says_so_in_a_line(rep, patch_wcl):
    patch_wcl(rep, graphs=lambda code, fights, *_, **__: {f["id"]: _flat_but(f, {40: 120.0}) for f in fights})
    text = await healing.healing(rep, 13)
    assert text.startswith("The raid's damage taken, ")  # no stretch shown: none in the title either
    assert text.splitlines()[1] == ("The raid's damage taken stayed under 1.25x its median of 100/s over any 5 s, "
                                    "highest 104/s over the 5 s from 0:36.")
    assert len(text.splitlines()) == 2


def test_a_pull_with_no_damage_most_of_the_time_has_no_median_to_measure_against():
    # "stayed under 1.5x its median of 0/s, highest 140/s" was false: with no damage in half the pull's 5 s or more,
    # nothing is measured against the median.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [0.0] * 60
    taken[35:40] = [140.0] * 5
    assert not healing._spikes(f, _graph(taken), f["endTime"], healing.BUSY_TIMES)
    assert healing._even(f, _graph(taken), f["endTime"]) == (
        "the raid's damage taken had a median of 0/s over 5 s, against which no stretch is measured, highest 140/s "
        "over the 5 s from 0:35")


async def test_a_night_without_spikes_shows_each_pulls_busiest_stretches(rep, patch_wcl):
    busy = {i: 135.0 for i in range(10, 18)}
    patch_wcl(rep, graphs=lambda code, fights, *_, **__: {f["id"]: _flat_but(f, busy) for f in fights})
    text = await healing.healing(rep, boss="Sszorak", all_pulls=True)
    head, *lines = text.splitlines()
    assert lines[0] == "No pull's raid damage taken was at 1.5x its median over 5 s for 3 s or more."
    assert lines[1].startswith("Each pull's 3 busiest stretches (time, length, ")
    assert any(line.startswith("The busiest stretches that came back pull after pull") for line in lines)
    assert "spike" not in "\n".join(lines)


async def test_a_night_under_the_lower_bar_names_each_pull_in_its_line(rep, patch_wcl):
    patch_wcl(rep, graphs=lambda code, fights, *_, **__: {f["id"]: _flat_but(f, {30: 110.0}) for f in fights})
    flat = (await healing.healing(rep, boss="Sszorak", all_pulls=True)).splitlines()
    assert len(flat) == 2 and flat[1].startswith(
        "Pull 1: the raid's damage taken stayed under 1.25x its median of 100/s over any 5 s, highest 102/s over the "
        "5 s from 0:26; pull 2: the raid's damage taken stayed under 1.25x")


async def test_who_was_meant_to_press_a_cooldown_left_ready_is_said_once_and_only_then(rep, patch_wcl, monkeypatch):
    # healing named who left which cooldown ready and unused and never said that who was meant to press it isn't in
    # the log. It says so once, after the spikes (after the night's "most often ready and unused" line), never per
    # spike, and only when a cooldown was left ready.
    busy = {i: 300.0 for i in [*range(20, 26), *range(80, 86)]}
    patch_wcl(rep, graphs=lambda code, fights, *_, **__: {f["id"]: _flat_but(f, busy) for f in fights})
    fill = healing._fill

    async def ready_in_each(rep, fights, spikes, ends):
        whole = await fill(rep, fights, spikes, ends)
        for s in spikes:
            s.ready.append("Spirit Link Totem (Enfo)")
        return whole
    monkeypatch.setattr(healing, "_fill", ready_in_each)
    lines = (await healing.healing(rep, 13, jobs="JOBS")).splitlines()
    assert lines.count("JOBS") == 1 and lines[lines.index("JOBS") + 1].startswith("Each healer in the spikes")
    night = (await healing.healing(rep, boss="Sszorak", all_pulls=True, jobs="JOBS")).splitlines()
    assert night.count("JOBS") == 1 and night[night.index("JOBS") - 1].startswith(
        "Healing cooldowns most often ready and unused in a spike: Spirit Link Totem (Enfo) ")
    monkeypatch.setattr(healing, "_fill", fill)
    assert "JOBS" not in await healing.healing(rep, 13, jobs="JOBS")  # none left ready: nothing to say it of
