"""How the tools write times, numbers and lists (text.py)."""
from wipebot.core import text


def test_times_are_read_and_written_as_minutes_and_seconds():
    assert text.clock(151_999) == "2:31" and text.clock(-5) == "0:00"
    assert text.parse_clock("2:31") == 151_000
    assert text.parse_clock("45") == 45_000
    assert text.parse_clock("soon") is None


def test_lists_of_numbers_and_moments_are_grouped():
    assert text.ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8"
    assert text.clusters([39_000, 152_000, 40_000, 77_000]) == "0:39-0:40 (2 pulls), 1:17 (1 pull), 2:32 (1 pull)"
    assert [text.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "112th"]
    assert (text.short(1_234_567), text.short(999)) == ("1.23M", "999")
