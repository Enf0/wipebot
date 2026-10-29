"""Positions over a pull and the replay viewer's tracks (report/positions.py, report/maps.py)."""
import base64
import struct

from wipebot.core.report import maps, positions


def _ints(track: str) -> tuple[int, ...]:
    raw = base64.b64decode(track)
    return struct.unpack(f"<{len(raw) // 2}h", raw)


def test_a_track_marks_the_dead_where_they_fell_until_a_res_and_what_isnt_known():
    samples = [(1000.0, 500.0, 700.0), (2000.0, 600.0, 700.0), (6000.0, 900.0, 700.0)]  # (ms, x, y), 1/100 yd
    dead = positions.dead_spans(samples, [2500.0], end=9000.0)  # died at 2.5 s, brought back: a position at 6 s
    assert dead == [(2500.0, 6000.0)]
    track = _ints(maps.pack_track(samples, [(1000.0, 0.0)], 0.0, 9, dead, origin=(0.0, 0.0)))
    x, y, face = track[0::3], track[1::3], track[2::3]
    assert (x[1], y[1], face[1]) == (10, 14, 0)  # half yards; facing 0° (east)
    assert [(x[i], face[i]) for i in (3, 4, 5)] == [(12, maps.DEAD_FACING)] * 3  # where they fell, dead
    assert x[6] == 18 and face[6] != maps.DEAD_FACING  # back on their feet
    assert x[8] != maps.UNKNOWN and positions.at(samples, 9000.0) is None  # 3 s after the last sample: not known
    never_back = positions.dead_spans(samples[:2], [2500.0], end=9000.0)
    assert never_back == [(2500.0, 9000.0)]


def test_a_damage_over_time_ticking_as_it_lands_is_the_players_own_not_from_another_whose_ended():
    # The Coiled Altar pull 5 (4N9JMwBFPAjXyV3Z): Mutagenic Venom ticks on whoever it's put on as it lands, and two
    # players got theirs 0.1-0.3 s before the first carrier's ended, 23 and 75 yd from them
    stints: positions.Stints = {1: [(7_171.0, 9_595.0)], 2: [(9_340.0, 9_589.0)], 3: [(9_457.0, 11_614.0)]}
    carried = {1: (7_171.0, 9_595.0)}
    for target, t in ((2, 9_361), (3, 9_457)):
        hit = {"timestamp": t, "targetID": target, "abilityGameID": 1310691}
        assert not positions._from_another(hit, carried, stints, burst=set())
    # someone without it, hit while another had it, is still theirs
    hit = {"timestamp": 8_500, "targetID": 4, "abilityGameID": 1310691}
    assert positions._from_another(hit, carried, stints, burst=set())
    # a burst that puts the debuff on whom it hits as another's ends stays the other's
    hit = {"timestamp": 9_460, "targetID": 3, "abilityGameID": 1310691}
    assert positions._from_another(hit, carried, stints, burst={1310691})
