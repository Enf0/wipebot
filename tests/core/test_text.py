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


def test_a_date_that_is_the_evening_before_in_the_americas_carries_its_time_in_utc():
    from datetime import UTC, datetime
    evening = datetime(2026, 10, 14, 18, 56, tzinfo=UTC).timestamp() * 1000  # a European raid night's start
    night = datetime(2026, 10, 14, 4, 5, tzinfo=UTC).timestamp() * 1000  # Tuesday 21:05 Pacific, Wednesday in UTC
    # No report says where its raiders are (Warcraft Logs' US region holds the Oceanic realms too), so dates stay
    # UTC. "Wed 14 Oct" alone read a day late for a Tuesday raid in the Americas: there the time and "UTC" go with it.
    assert text.day(evening) == "Wed 14 Oct" and text.date(evening) == "Wed 2026-10-14"
    assert text.day(night) == "Wed 14 Oct 04:05 UTC" and text.date(night) == "Wed 2026-10-14 04:05 UTC"
    assert text.day(evening, with_time=True) == "Wed 14 Oct 18:56 UTC"
    # A date to count nights by stays a date.
    assert text.day(night, with_time=False) == "Wed 14 Oct" and text.date(night, with_time=False) == "Wed 2026-10-14"


def test_a_nights_length_over_an_hour_is_written_with_its_hours():
    # "174:43 long" for a night of 2 h 55 min read as a time in a pull; under an hour it is a pull's clock.
    assert text.span(10_483_000) == "2:54:43" and text.span(3_600_000) == "1:00:00"
    assert text.span(3_599_999) == "59:59" and text.span(626_000) == "10:26"


def test_one_events_moment_is_written_to_the_millisecond():
    # Two hits in one second, or a cast and the death it dealt, are told apart and ordered only by the thousandths.
    assert text.exact(15_250) == "0:15.250" and text.exact(162_854.6) == "2:42.854" and text.exact(-3) == "0:00.000"
    assert text.exact(3_723_004) == "1:02:03.004"
