"""/login: a raider signs in with Warcraft Logs, and /usage says whether their own points are left."""
from typing import cast
from urllib.parse import parse_qs, urlparse

import discord
import pytest

from wipebot.core import accounts, wcl
from wipebot.discord import logincmd

CALLBACK = "https://wipebot.example/signed-in"


@pytest.fixture(autouse=True)
def callback(monkeypatch):
    monkeypatch.setenv("WIPEBOT_WCL_CALLBACK", CALLBACK)


async def test_the_link_signs_in_to_the_bots_client_and_comes_back_to_the_page(store):
    choices = logincmd.Choices(store, CALLBACK, "client-id", "secret")
    link = cast(discord.ui.Button, choices.children[0])
    query = parse_qs(urlparse(link.url or "").query)
    assert query["client_id"] == ["client-id"] and query["redirect_uri"] == [CALLBACK]
    assert query["state"] == [choices.state] and query["code_challenge"] == [choices.challenge]
    assert [getattr(item, "label", None) for item in choices.children] == ["1. Sign in with Warcraft Logs",
                                                                           "2. Paste the address"]


async def test_the_pasted_address_signs_the_raider_in(store, monkeypatch):
    choices = logincmd.Choices(store, CALLBACK, "client-id", "secret")
    asked = {}

    async def login(client_id, secret, code, verifier, redirect_uri):
        asked.update(code=code, verifier=verifier, redirect_uri=redirect_uri)
        return accounts.Account(101, "Kaelnor", [], "token-abc", 2e9)
    monkeypatch.setattr(accounts, "login", login)
    said = await logincmd.sign_in(choices, 7, f"{CALLBACK}?code=abc123DEF_-x&state={choices.state}")
    assert "Signed in as **Kaelnor**" in said
    assert asked == {"code": "abc123DEF_-x", "verifier": choices.verifier, "redirect_uri": CALLBACK}
    assert store.login(7) == ("Kaelnor", "token-abc")


async def test_the_private_reports_sign_in_asks_for_them_and_keeps_its_token_apart(store, monkeypatch):
    choices = logincmd.Choices(store, CALLBACK, "client-id", "secret", private=True)
    link = cast(discord.ui.Button, choices.children[0])
    assert parse_qs(urlparse(link.url or "").query)["scope"] == [accounts.PRIVATE_SCOPES]

    async def login(*args):
        return accounts.Account(101, "Kaelnor", [], "private-abc", 2e9)
    monkeypatch.setattr(accounts, "login", login)
    said = await logincmd.sign_in(choices, 7, f"{CALLBACK}?code=abc123DEF_-x&state={choices.state}")
    assert "now reads the private reports **Kaelnor**" in said
    assert store.login(7) is None and store.private_login(7) == ("Kaelnor", "private-abc")
    assert "reads the private reports **Kaelnor** may open" in logincmd.status(store, 7)


async def test_an_address_from_another_sign_in_or_without_a_code_saves_nothing(store, monkeypatch):
    choices = logincmd.Choices(store, CALLBACK, "client-id", "secret")
    assert "another sign-in" in await logincmd.sign_in(choices, 7, f"{CALLBACK}?code=abc123DEF_-x&state=other")
    assert "no code" in await logincmd.sign_in(choices, 7, CALLBACK)

    async def refused(*args):
        raise accounts.AccountError("Warcraft Logs token: 400")
    monkeypatch.setattr(accounts, "login", refused)
    assert "didn't sign you in" in await logincmd.sign_in(choices, 7, f"{CALLBACK}?code=abc123DEF_-x")
    assert store.login(7) is None


class Reader:  # stands in for wcl.WCL
    def __init__(self, rate: dict):
        self.rate, self.token = rate, None

    async def query(self, query: str) -> dict:
        self.token = wcl._user.get()
        return {"rateLimitData": self.rate}


async def test_usage_asks_warcraft_logs_with_the_raiders_own_token(store, monkeypatch):
    async def line(rate: dict) -> str:
        reader = Reader(rate)
        said = await logincmd.points_line(store, cast(wcl.WCL, reader), 7)
        assert said is not None and reader.token == (store.login(7) or (None, None))[1]  # asked with theirs
        return said
    assert "not signed in" in await line({})
    store.save_login(7, 101, "Kaelnor", "token-abc", 2e9)
    said = await line({"userHasRemainingPoints": False, "userPointsResetIn": 1500})
    assert "signed in as Kaelnor" in said and "used up, back in 26 min" in said
    assert "some left" in await line({"userHasRemainingPoints": True})
    assert "new point system" in await line({"userHasRemainingPoints": None})  # before the new point system
    monkeypatch.delenv("WIPEBOT_WCL_CALLBACK")
    assert await logincmd.points_line(store, cast(wcl.WCL, Reader({})), 7) is None  # a bot without sign-in
