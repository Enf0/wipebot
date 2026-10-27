"""A shared copy's free answers per server: a share of the OpenRouter key's monthly limit."""
import datetime

import pytest

from wipebot.discord import allowance, budget
from wipebot.discord.allowance import Allowance


@pytest.fixture(autouse=True)
def _notes(tmp_path, monkeypatch):
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))


def pool(monkeypatch, limit, remaining):
    async def fetch(key):
        return budget.KeyBudget(limit=limit, remaining=remaining, month=0, total=0)
    monkeypatch.setattr(budget, "fetch", fetch)


async def test_a_server_answers_until_it_has_spent_its_share(store, monkeypatch):
    pool(monkeypatch, 100.0, 80.0)
    a = Allowance(store, "sk-or-x", 0.1)
    store.add_spend(1001, 7, "chat", 9.5, 0)
    store.add_spend(2002, 8, "chat", 50.0, 0)  # another server's spend isn't this one's
    assert await a.refusal(1001) is None
    store.add_spend(1001, 7, "chat", 0.5, 0)
    refused = await a.refusal(1001)
    assert refused and "$10.00 of its $10.00" in refused


async def test_nobody_answers_once_the_pool_is_spent_or_set_to_nothing(store, monkeypatch):
    for limit, remaining in ((100.0, 0.0), (0.0, 0.0)):
        pool(monkeypatch, limit, remaining)
        refused = await Allowance(store, "sk-or-x", 0.1).refusal(1001)
        assert refused and "every server shares" in refused


async def test_no_limit_on_the_key_or_no_pool_read_means_no_cap(store, monkeypatch):
    pool(monkeypatch, None, None)
    store.add_spend(1001, 7, "chat", 500.0, 0)
    assert await Allowance(store, "sk-or-x", 0.1).refusal(1001) is None

    async def down(key):
        raise budget.httpx.ConnectError("down")
    monkeypatch.setattr(budget, "fetch", down)
    assert await Allowance(store, "sk-or-x", 0.1).refusal(1001) is None  # OpenRouter still refuses past its limit


def test_the_share_and_the_month(monkeypatch):
    monkeypatch.delenv("WIPEBOT_SERVER_SHARE", raising=False)
    assert allowance.server_share() is None
    monkeypatch.setenv("WIPEBOT_SERVER_SHARE", "0.1")
    assert allowance.server_share() == 0.1
    monkeypatch.setenv("WIPEBOT_SERVER_SHARE", "2")
    with pytest.raises(ValueError):
        allowance.server_share()
    dec = datetime.datetime(2026, 12, 31, 23, 0, tzinfo=datetime.UTC)
    assert allowance.month_start(dec).day == 1 and allowance.next_month(dec) == datetime.datetime(
        2027, 1, 1, tzinfo=datetime.UTC)


async def test_the_footer_says_the_cost_points_and_the_servers_share_left(store, monkeypatch):
    pool(monkeypatch, 100.0, 80.0)
    store.add_spend(1001, 7, "chat", 2.5, 0)
    left = await Allowance(store, "sk-or-x", 0.1).left(1001)
    assert left == (10.0, 7.5)
    points = {"total": 92.4, "application": 92.4, "user": 60, "queries": 3}  # not added up: total is the sum
    rate = {"limitPerHour": 3600, "pointsSpentThisHour": 480}
    assert allowance.footer(0.18, points, left, rate) == (
        "-# $0.18 · Warcraft Logs points: 92 (3,120/3,600 left this hour) · $7.50 of this server's $10.00 left this "
        "month")
    # on the new point system, a signed-in asker's own points pay first
    assert allowance.footer(0.18, points, None, rate, user_points=True) == (
        "-# $0.18 · Warcraft Logs points: 60 yours, 32 the bot's (3,120/3,600 left this hour)")
    assert allowance.footer(None, {}, None) == "-# $0.00 · Warcraft Logs points: 0"


def test_an_admins_footer_choice_is_kept(store):
    assert store.footer(1001) is None
    store.set_footer(1001, False)
    assert store.footer(1001) is False
    store.set_footer(1001, True)
    assert store.footer(1001) is True
    store.forget_server(1001)
    assert store.footer(1001) is None
