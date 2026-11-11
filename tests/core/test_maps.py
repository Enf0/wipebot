"""Positions over a pull and the replay viewer's tracks (report/positions.py, report/maps.py)."""
import base64
import struct
from typing import cast

from wipebot.core.report import Report, maps, positions


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


def test_an_ability_s_id_in_at_is_the_ability_not_seconds():
    # #1351 of the bot's answers: at="1283832" (Axegrinder's id) was read as 1283832 seconds into the pull
    f = {"startTime": 0, "endTime": 209_000}
    assert maps.at_ms_of("1283832", f) is None
    assert maps.at_ms_of("151", f) == 151_000
    assert maps.at_ms_of("2:31", f) == 151_000
    assert maps.at_ms_of(None, f) is None


def test_a_cast_begun_and_never_finished_counts_from_when_it_began():
    # #1378: Eternal Nightfall, a channel the raid ends by breaking the boss's shield, logs only its begincast, so
    # the map said it didn't happen
    casts = [{"timestamp": 1_000, "type": "begincast", "sourceID": 9}, {"timestamp": 3_000, "type": "cast",
                                                                       "sourceID": 9},
             {"timestamp": 40_000, "type": "begincast", "sourceID": 9},
             {"timestamp": 90_000, "type": "cast", "sourceID": 8}]
    assert positions._cast_times(casts) == [3_000, 40_000, 90_000]


def test_a_mechanics_count_on_the_map_is_of_moments_and_says_so():
    """Twelve hits on players in two volleys: pull_map said "hitting players: 2 times" where mechanic_hits counts each
    player's hits, with no word on what either counted. A volley is one moment, and the other kinds of event are
    counted as the log has them."""
    f = {"id": 1, "startTime": 0, "endTime": 60_000}
    hits = [{"timestamp": 10_000 + 400 * n, "targetID": 1 + n % 3} for n in range(6)]
    hits += [{"timestamp": 40_000 + 400 * n, "targetID": 1 + n % 3} for n in range(6)]
    moments = positions._moments(f, "hit", {}, [], hits, set())
    assert [len(m.on) for m in moments] == [3, 3]
    mech = positions.Mechanic("Splinters", [1312853], "hit", {1: moments}, {"ends": 9, "cast": 0, "hit": len(hits)})
    assert maps._what(cast(Report, None), mech, "The Lost Explorers pull 1") == [
        "Splinters [1312853] hitting players on The Lost Explorers pull 1: 2 moments, what came within 2 s of each "
        "other counted as one (also logged, in events: ends 9; `event` picks another)."]
