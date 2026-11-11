"""A player's casting over a pull (report/casting.py): gaps with nothing cast, and casts dropped."""
from wipebot.core.report import casting


def ev(t: float, kind: str, ability: int) -> dict:
    return {"timestamp": t, "type": kind, "abilityGameID": ability}


def test_casting_finds_gaps_and_dropped_casts():
    """A begun cast lands as a cast of the same spell; another begun before it, or an on-GCD instant instead of
    it, means it was dropped. A stretch with nothing cast is a gap; time dead isn't."""
    events = [
        ev(1000, "begincast", 1), ev(2500, "cast", 1),  # lands
        ev(2500, "begincast", 1), ev(4000, "cast", 1),  # begins the millisecond the last one lands
        ev(4000, "begincast", 2), ev(4800, "cast", 9), ev(6000, "cast", 2),  # an off-GCD spell during it
        ev(6000, "begincast", 1), ev(7000, "begincast", 1), ev(8500, "cast", 1),  # dropped and begun again
        ev(8500, "begincast", 1), ev(15000, "cast", 3),  # dropped (nothing lands in time), then nothing: a gap
        ev(30000, "cast", 3), ev(33200, "cast", 3),  # dead 20.0-30.0 is not a gap; 3.2 s after a GCD isn't either
        ev(40000, "cast", 3),  # from 34.7 to 40.0: a gap; from 41.5 to the end at 50.0: another
    ]
    c = casting.casting(events, 50000, deaths=[20000])
    assert c.cancelled == {1: [6000, 8500]}
    # 16.5-20.0: nothing cast before dying; the gap is cut at the death.
    assert c.gaps == [(8500, 15000), (16500, 20000), (34700, 40000), (41500, 50000)]
    assert c.span_ms == 49000
    assert round(c.gap_share, 2) == round(23800 / 49000, 2)
    # Stopping before dying: the gap ends at the death.
    c = casting.casting(events[:2], 50000, deaths=[10000])
    assert c.gaps == [(2500, 10000)]
    assert casting.casting([], 50000).span_ms == 0


def test_a_death_within_the_last_casts_gcd_leaves_no_gap():
    """An instant at 10.0 is taken as busy until 11.5; dying at 11.0 makes the rest time dead, though the stretch
    without a cast begins after the death: no gap, whether they lie dead to the end or are brought back and cast
    again at 25.0."""
    events = [ev(4000, "cast", 3), ev(5500, "cast", 3), ev(10000, "cast", 3)]
    assert casting.casting(events, 30000, deaths=[11000]).gaps == [(7000, 10000)]
    assert casting.casting([*events, ev(25000, "cast", 3)], 30000, deaths=[11000]).gaps == [(7000, 10000),
                                                                                           (26500, 30000)]


def test_a_raid_wide_pause_is_cut_out_of_a_players_gaps():
    from wipebot.core.report.casting import without
    # a gap from 3:10 to 3:30, the raid stunned 3:17-3:23: what's left either side, if still a gap
    assert without([(190_000, 210_000)], [(197_000, 203_000)]) == [(190_000, 197_000), (203_000, 210_000)]
    assert without([(195_000, 205_000)], [(197_000, 203_000)]) == []  # 2 s either side: no gap left
