"""Fakes for the bot's tests: a server's settings, the store, Discord's users and channels, the core's tools and
reports, the live report's follower, and the bot itself. Names (the guild, players, Discord users) are made up.

Tests can't import from a conftest (the root one has the same module name), so helpers are fixtures too:
`member(...)` makes one."""
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from tests.support.messages import shown
from wipebot.core.report import FactError
from wipebot.discord.follower import Pull
from wipebot.discord.store import ServerSettings, Store


@pytest.fixture
def settings() -> ServerSettings:
    """A set-up server: Discord server 1001, following guild 14169 on Warcraft Logs, anyone may chat."""
    return ServerSettings(1001, 14169, "The Copper Lanterns", "argent-dawn", "eu")


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def member() -> Callable[..., discord.Member]:
    """member(id, name="", roles=()): a Discord member with these role ids, whose display name is their username."""
    def make(user_id: int, name: str = "", roles: tuple[int, ...] = ()) -> discord.Member:
        return cast(discord.Member, SimpleNamespace(id=user_id, display_name=name, name=name, bot=False,
                                                    roles=[SimpleNamespace(id=r) for r in roles],
                                                    guild_permissions=SimpleNamespace(manage_guild=False)))
    return make


class FakeChannel:
    """A channel (or a DM) that keeps what was sent to it."""

    def __init__(self, channel_id: int = 20, name: str = "raid"):
        self.id, self.name, self.mention = channel_id, name, f"#{name}"
        self.parent_id: int | None = None  # the channel of a thread
        self.guild: Any = None
        self.permissions_for: Any = None
        self.sent: list[str | None] = []
        self.files: list[list[str]] = []
        self.views: list[Any] = []

    async def send(self, content=None, *, view=None, files=(), **kwargs):
        self.sent.append(shown(content, view))
        self.files.append([f.filename for f in files])
        self.views.append(view)
        return SimpleNamespace(id=len(self.sent))

    def typing(self):
        return contextlib.nullcontext()


@pytest.fixture
def channel() -> Any:
    """#raid (id 20)."""
    return FakeChannel()


@dataclass
class FakeFollower:
    """The live report's follower: `code` is the live report (None before the raid logs), `pulls` what each poll
    finds; `done` keeps the pulls handled."""
    code: str | None = None
    pulls: list[Pull] = field(default_factory=list)
    handled: list[Pull] = field(default_factory=list)

    async def live(self) -> str | None:
        return self.code

    async def poll(self) -> list[Pull]:
        return self.pulls

    def done(self, pull: Pull) -> None:
        self.handled.append(pull)


@pytest.fixture
def follower() -> FakeFollower:
    return FakeFollower()


@pytest.fixture(autouse=True)
def no_pictures(monkeypatch):
    """No views drawn as pictures (a browser; test_host.py's picture tests turn them on)."""
    monkeypatch.setattr("wipebot.discord.host._no_pictures", "off in the tests")


@pytest.fixture
def core_calls(monkeypatch) -> list[tuple[str, dict]]:
    """The core's tools, faked: each call is kept as (name, arguments) and returns "checked"."""
    calls: list[tuple[str, dict]] = []

    async def call_with_images(name, args):
        calls.append((name, args))
        return "checked", False, []
    monkeypatch.setattr("wipebot.core.server.call_with_images", call_with_images)
    return calls


ROSTER = ["Torvek", "Mirelle", "Brightleaf"]


class FakeLibrary:
    """The core's reports, faked: every report has the players of ROSTER and two Sszorak pulls (fights 30, 31)."""

    async def report(self, code, max_age_s=None):
        def player(name):
            found = [p for p in ROSTER if p.lower() == name.lower()]
            if not found:
                raise FactError(f"no single player matching {name!r} in this report")
            return SimpleNamespace(name=found[0])
        fights = [{"id": 30, "name": "Sszorak"}, {"id": 31, "name": "Sszorak"}]
        return SimpleNamespace(code=code, player=player, fights=fights, boss_key=lambda f: f["name"],
                               pull_no=lambda f: fights.index(f) + 1)


@pytest.fixture
def library() -> Any:
    return FakeLibrary()


@pytest.fixture
def bot(store, library, settings, follower) -> Any:
    """The bot, logged in as Wipebot (id 99), in one set-up server whose live report is LIVE1."""
    from wipebot.core import server
    from wipebot.discord.bot import WipeBot

    store.save_settings(settings)
    bot = WipeBot(store, cast(server.Library, library), record_answers=True)  # as a host who reviews answers
    bot._connection.user = cast(discord.ClientUser, SimpleNamespace(id=99, display_name="Wipebot", name="wipebot"))
    follower.code = "LIVE1"
    bot.followers[settings.discord_guild_id] = follower
    return bot
