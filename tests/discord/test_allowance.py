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
