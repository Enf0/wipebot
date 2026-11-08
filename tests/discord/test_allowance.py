"""What a server's answers cost: the month they're counted in, and the small line under an answer."""
import datetime

from wipebot.discord import allowance


def test_the_month_starts_on_the_first():
    dec = datetime.datetime(2026, 12, 31, 23, 0, tzinfo=datetime.UTC)
    assert allowance.month_start(dec) == datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC)


def test_the_footer_says_the_cost_and_points():
    points = {"total": 92.4, "application": 92.4, "user": 60, "queries": 3}  # not added up: total is the sum
    rate = {"limitPerHour": 3600, "pointsSpentThisHour": 480}
    assert allowance.footer(0.18, points, rate) == "-# $0.18 · Warcraft Logs points: 92 (3,120 left this hour)"
    # on the new point system, a signed-in asker's own points pay first
    assert allowance.footer(0.18, points, rate, user_points=True) == (
        "-# $0.18 · Warcraft Logs points: 60 yours, 32 the bot's (3,120 left this hour)")
    assert allowance.footer(None, {}) == "-# $0.00 · Warcraft Logs points: 0"


def test_an_admins_footer_choice_is_kept(store):
    assert store.footer(1001) is None
    store.set_footer(1001, False)
    assert store.footer(1001) is False
    store.set_footer(1001, True)
    assert store.footer(1001) is True
    store.forget_server(1001)
    assert store.footer(1001) is None
