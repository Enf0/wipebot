"""The healing tool's reading of the raid's damage taken, on made-up damage."""
from tests.support import damage_graph
from wipebot.core.report import fight, healing
from wipebot.core.report.taken import Taken


def _taken(per_second: list[float], start: float = 0, step: float = 1000) -> Taken:
    return Taken(start, step, per_second)


def _windows(rep, busy: dict[int, float]):
    """Warcraft Logs' graph windows (taken.py) of a raid taking 100/s, but at the seconds of its pull `busy` gives."""
    fights = {f["id"]: f for f in rep.fights}

    def graph_windows(code, items, *_, **__):
        return {name: damage_graph(lambda t, f=fights[fid]: busy.get(int((t - f["startTime"]) // 1000), 100.0), a, b,
                                   fights[fid]) for name, fid, a, b in items}
    return graph_windows


async def test_a_pull_without_spikes_names_its_peak_by_the_seconds_the_timeline_names(rep, patch_wcl):
    # healing said "highest 1.53M/s at 6:37" of the 5 s that pull_timeline's averages print as "6:35 1.53M": one
    # named them by their middle, the other by where they begin. Both now say where they begin, and read the same
    # damage (taken.py), so those 5 s have one number in both.
    f = rep.by_boss[rep.boss("Sszorak")][0]
    taken = [100.0] * 60
    taken[35:40] = [140.0] * 5
    assert healing._even(f, _taken(taken, f["startTime"]), f["startTime"] + 60_000) == (
        "the raid's damage taken stayed under 1.5x its median of 100/s over any 5 s, highest 140/s over the 5 s from "
        "0:35")
    patch_wcl(rep, graph_windows=_windows(rep, {i: 140.0 for i in range(35, 40)}))
    assert " · 0:35 140 · " in await fight._raid_damage(rep, f, f["startTime"], f["endTime"])


def test_a_peak_where_the_pull_is_cut_says_the_seconds_that_were_averaged():
    # The average around a pull's last seconds (or its first) has only the seconds on one side of them: "over the 5 s
    # from 0:29" stood for the 3 s up to a wipe decided at 0:32, and for the 3 s a pull opens with.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    late = _taken([100.0] * 30 + [140.0] * 30)
    assert healing._even(f, late, 32_000).endswith("highest 127/s over the 3 s from 0:29")
    assert healing._even(f, _taken([140.0] * 2 + [100.0] * 58), 60_000).endswith("highest 127/s over the 3 s from 0:00")
    # Read a second at a time, whatever the steps of what was asked
    fine = _taken([100.0] * 100 + [140.0] * 20 + [100.0] * 120, step=250)
    assert healing._even(f, fine, 60_000).endswith("highest 140/s over the 5 s from 0:25")


def test_a_peak_over_the_bar_for_under_3_s_is_no_spike_and_says_so():
    # A spike lasts 3 s or more: a 5 s average over 1.5x the median for one second isn't one, and "stayed under 1.5x
    # its median, highest 152/s" said of a 100/s median was false.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [100.0] * 60
    taken[28:33] = [120.0, 160.0, 200.0, 160.0, 120.0]
    assert not healing._spikes(f, _taken(taken), f["endTime"])
    assert healing._even(f, _taken(taken), f["endTime"]) == (
        "the raid's damage taken reached 1.5x its median of 100/s for under 3 s at a time, highest 152/s over the 5 s "
        "from 0:28")


def test_a_pulls_spikes_are_its_runs_over_the_bar_joined_when_close_and_ranked_by_their_damage():
    # Runs of 5 s averages at 1.5x the median, runs 3 s apart or less joined, each 3 s or more, biggest first by the
    # damage taken in it: a long spike outranks a sharper short one.
    f = {"id": 1, "startTime": 0, "endTime": 120_000}
    taken = [100.0] * 120
    taken[10:13] = [300.0] * 3  # sharp and short
    taken[30:33] = taken[37:40] = [250.0] * 3  # their averages' runs 3 s apart: one spike
    taken[60:70] = [180.0] * 10  # long
    taken[90:91] = [300.0]  # one hit: its 5 s averages under the bar
    got = healing._spikes(f, _taken(taken), f["endTime"])
    assert [(s.start // 1000, s.end // 1000) for s in got] == [(29, 41), (61, 69), (9, 14)]
    assert all(s.start % 1000 == 0 == s.end % 1000 for s in got)  # whole seconds, for the spike's tables


async def test_a_pull_without_spikes_shows_its_busiest_5_s_and_names_them_so(rep, patch_wcl):
    # Read on Warcraft Logs' whole-pull graph, smoothed over some 20 s, a kill of The Twin Fangs had no spike, and
    # healing showed its "busiest stretches" at a lower bar instead. Read second by second it has four, and a pull
    # without any is rare (4 of 121 that weren't resets, each over the bar for under 3 s): its busiest 5 s are shown
    # as a spike is, and called nothing else.
    busy = {i: 135.0 for i in [*range(20, 30), *range(80, 86)]}
    patch_wcl(rep, graph_windows=_windows(rep, busy))
    text = await healing.healing(rep, 13)
    head, *lines = text.splitlines()
    assert head.startswith("The raid's busiest 5 s of damage taken and the healing into them, ")
    assert head.endswith("A spike: raid damage taken at 1.5x the pull's median or more over 5 s, at least 3 s long.")
    assert lines[0] == "The raid's damage taken stayed under 1.5x its median of 100/s over any 5 s; the busiest 5 s:"
    assert lines[1] == "0:20-0:25 (5 s): 0/s taken (no damage recorded); healing 0/s, - of the damage taken."
    assert lines[2].startswith("  Healers: ")
    assert lines[3].startswith("  Healing cooldowns up in it (pressed in it or up to 5 s before, or earlier and "
                               "lasting through half of it): ")
    assert lines[4].startswith("Each healer in the busiest 5 s against the rest of the time")
    assert "in the busiest 5 s, " in lines[5] and "; casting throughout in those 5 s; 0 healing cooldowns in them, " \
        "0 left ready in them" in lines[5]
    assert "spike" not in "\n".join(lines)


def test_a_cooldown_pressed_before_a_spike_counts_while_it_lasts():
    # On The Twin Fangs pull 7, Avenging Wrath at 3:32 lasted through 3:38-3:44, which read "Healing cooldowns used (or
    # up to 5 s before): none", and answers said no cooldown went into it. Pressed earlier than 5 s before, a
    # cooldown counts while it lasts by Lorrgs' duration through half the spike: an opener's last second isn't the
    # 0:20 spike's. One with no duration (an instant) counts up to 5 s before, and none counts after.
    wrath, rewind = {"duration": 20}, {"duration": 0}
    start, end = 218_000, 224_000
    assert healing._up([212_000], wrath, start, end) == [212_000]
    assert healing._up([197_000, 224_500], wrath, start, end) == []
    assert healing._up([1_000], wrath, 20_000, 27_000) == []
    assert healing._up([213_000], rewind, start, end) == [213_000]
    assert healing._up([212_000], rewind, start, end) == []


async def test_a_pull_without_damage_taken_recorded_says_so_in_a_line(rep):
    text = await healing.healing(rep, 13)  # FakeWCL: graphs without series
    assert text.startswith("The raid's damage taken, ")  # nothing shown: nothing in the title either
    assert text.splitlines()[1:] == ["No raid damage taken recorded."]


def test_a_pull_with_no_damage_most_of_the_time_has_no_median_to_measure_against():
    # "stayed under 1.5x its median of 0/s, highest 140/s" was false: with no damage in half the pull's 5 s or more,
    # nothing is measured against the median.
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    taken = [0.0] * 60
    taken[35:40] = [140.0] * 5
    assert not healing._spikes(f, _taken(taken), f["endTime"])
    assert healing._even(f, _taken(taken), f["endTime"]) == (
        "the raid's damage taken had a median of 0/s over 5 s, against which no stretch is measured, highest 140/s "
        "over the 5 s from 0:35")


async def test_a_night_without_spikes_names_each_pull_in_its_line(rep, patch_wcl):
    # Over a boss's pulls the pulls without a spike are left out; when none has one, each says so in a clause.
    patch_wcl(rep, graph_windows=_windows(rep, {30: 110.0}))
    flat = (await healing.healing(rep, boss="Sszorak", all_pulls=True)).splitlines()
    assert len(flat) == 2 and flat[1].startswith(
        "Pull 1: the raid's damage taken stayed under 1.5x its median of 100/s over any 5 s, highest 102/s over the "
        "5 s from 0:26; pull 2: the raid's damage taken stayed under 1.5x")


async def test_who_was_meant_to_press_a_cooldown_left_ready_is_said_once_and_only_then(rep, patch_wcl, monkeypatch):
    # healing named who left which cooldown ready and unused and never said that who was meant to press it isn't in
    # the log. It says so once, after the spikes (after the night's "most often ready and unused" line), never per
    # spike, and only when a cooldown was left ready.
    busy = {i: 300.0 for i in [*range(20, 26), *range(80, 86)]}
    patch_wcl(rep, graph_windows=_windows(rep, busy))
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
