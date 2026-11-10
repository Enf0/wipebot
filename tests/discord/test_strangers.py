"""The view from outside, on a bot many servers share: an admin of another Discord server who links a guild that isn't
theirs, and a raider who links another guild's report, tried against what a guild keeps (its raid notes, plans and
Viserio key) and against what another asker reads Warcraft Logs with. Each thing the bot stores gets its strangers
here when it's added. The core's own are in tests/core/test_guilds.py."""
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE, PLAN
from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report.base import JOBS_NOT_LOGGED
from wipebot.discord import bot as wipebot
from wipebot.discord import commands
from wipebot.discord.host import Session
from wipebot.discord.store import LoginGuild, ServerSettings

REPORT = CASES["raid-note"].args["report"]  # the guild's night on Sszorak, pull 42 checked against its note
KICKS = dict(CASES["nekzali-enemies"].args)  # another night of the same guild: enemies, said with whose kick it was
OWN, CLAIMS, OTHER, NOT_SET_UP = 1001, 2002, 3003, 4004  # Discord servers
KEY = "viserio-key-1"


@pytest.fixture
def shared(bot, monkeypatch, settings) -> Any:
    """The bot as many servers share it, with sign-in: server 1001 is verified as its guild's own, 2002 linked the
    same guild and nobody verified it, 3003 is verified for another guild, 4004 isn't set up."""
    monkeypatch.setenv("WIPEBOT_WCL_CALLBACK", "https://example.org/signed-in")
    bot.own_keys = True
    bot.store.save_settings(replace(settings, verified_by=7, verified_at=1.0))
    bot.store.save_settings(replace(settings, discord_guild_id=CLAIMS))
    bot.store.save_settings(ServerSettings(OTHER, 555, "Other Guild", "tarren-mill", "eu", verified_by=8,
                                           verified_at=1.0))
    return bot


def saves(bot, guild: int = 14169, at: float = 1000.0) -> None:
    """The guild's own server saves a note and a plan (`at`), and its host gave its Viserio key."""
    book = bot.store.notebook
    server.save_pasted(book, guild, NOTE.read_text(encoding="utf-8"), "Rowan", at)
    server.save_pasted(book, guild, PLAN.read_text(encoding="utf-8"), "Rowan", at)
    book.save_viserio_key(guild, KEY, "Lanterns", "Rowan")


def context(bot, server_id: int, member, channel):
    s = bot.store.settings(server_id) or ServerSettings.unset(server_id)
    return bot._context(s, member(8, "Mallory"), "Mallory", "LIVE1", "in #raid", channel)


def test_a_server_that_links_a_guild_reads_nothing_it_keeps_until_an_admin_in_it_verified(shared, member, channel):
    bot = shared
    saves(bot)
    ctx = context(bot, CLAIMS, member, channel)
    assert ctx.notebook is None and ctx.guild_id == 0 and not ctx.noted and not ctx.planned
    assert "until an admin shows that **The Copper Lanterns** is this server's guild" in ctx.notes_off
    told = commands.overview(bot, CLAIMS)
    assert "Sszorak" not in told and "Rowan" not in told  # none of what the guild saved
    assert "2. Raid notes and plans are off here until an admin shows" in told and "`/login`" in told
    own = context(bot, OWN, member, channel)  # the guild's own server, as before
    assert own.notebook is bot.store.notebook and own.guild_id == 14169
    assert own.noted == ["Sszorak"] and own.planned == ["Sszorak"] and not own.notes_off
    assert "Raid notes saved here (the newest per boss):\n- **Sszorak**, by Rowan" in commands.overview(bot, OWN)
    assert "are off here" not in commands.overview(bot, OWN)


def test_a_raiders_own_guild_standing_in_and_a_server_not_set_up_read_no_notes(shared, member, channel):
    saves(shared)
    for s in (ServerSettings.unset(NOT_SET_UP), LoginGuild(NOT_SET_UP, 14169, "The Copper Lanterns", "argent-dawn",
                                                           "eu", verified_by=7, verified_at=1.0)):
        ctx = shared._context(s, member(8, "Mallory"), "Mallory", "LIVE1", "in #raid", channel)
        assert ctx.notebook is None and ctx.guild_id == 0 and not ctx.noted and not ctx.notes_off


def test_a_hosts_own_bot_trusts_the_servers_it_is_in(shared, member, channel):
    shared.own_keys = False  # one host's bot for its own guilds: no claim to verify
    saves(shared)
    ctx = context(shared, CLAIMS, member, channel)
    assert ctx.notebook is shared.store.notebook and ctx.guild_id == 14169 and ctx.noted == ["Sszorak"]
    assert "Raid notes saved here" in commands.overview(shared, CLAIMS)


class Message:
    """A message to the bot (id 99) in #raid of a server, from Mallory (8), who manages that server."""

    def __init__(self, content: str, server_id: int, channel, user):
        self.id, self.content, self.channel, self.author = 1000, content, channel, user
        self.guild = SimpleNamespace(id=server_id)
        self.mentions, self.reference, self.attachments, self.replies = [SimpleNamespace(id=99)], None, [], []

    async def reply(self, content=None, **kwargs):
        self.replies.append(content)


@pytest.fixture
def paste(shared, monkeypatch, member, channel):
    """paste(server): what the bot says to a note pasted there by a member who manages that server."""
    officer = member(8, "Mallory")
    officer.guild_permissions.manage_guild = True
    monkeypatch.setattr(shared, "get_guild", lambda guild_id: SimpleNamespace(get_member=lambda i: officer))
    monkeypatch.setattr(wipebot.WipeBot, "user", SimpleNamespace(id=99, display_name="Wipebot"))

    async def run(server_id: int) -> str:
        msg = Message(f"<@99>\n{NOTE.read_text(encoding='utf-8')}", server_id, channel, officer)
        msg.mentions = [shared.user]
        await shared.on_message(cast(Any, msg))
        return msg.replies[-1]
    return run


async def test_a_note_pasted_in_a_server_that_only_links_the_guild_is_not_saved(shared, paste):
    """Refused before anything is written, and before the server's AI key is asked for: saving needs no key."""
    book = shared.store.notebook
    told = await paste(CLAIMS)
    assert told.startswith("I didn't save that. Raid notes and plans are off here until an admin shows")
    assert "signs in with `/login`, then runs `/wipebot setup`" in told
    assert book.notes(14169, 3420) == [] and book.latest_notes(14169) == []
    assert (await paste(OWN)).startswith("Saved the raid note")  # the guild's own server saves, as before
    assert len(book.notes(14169, 3420)) == 1
    assert (await paste(CLAIMS)).startswith("I didn't save that.") and len(book.notes(14169, 3420)) == 1
    assert "isn't set up yet" in await paste(NOT_SET_UP)
    shared.own_keys = False  # a host's own bot saves for the servers it is in
    assert (await paste(CLAIMS)).startswith("Saved the raid note") and len(book.notes(14169, 3420)) == 2


# ---------- the tools, asked as each of them ----------


@pytest.fixture
def recorded(monkeypatch, shared):
    """The tools on real pulls, from the recorded answers, and the bot's notebook the one they read."""
    serve_from(monkeypatch, Replay())
    shared.store.notebook = Notebook()

    async def asked(key, *args):
        raise server.viserio.ViserioError(f"asked with {key}")
    monkeypatch.setattr(server.viserio, "upcoming", asked)
    monkeypatch.setattr(server.viserio, "note_changed", asked)


async def answers(ctx) -> dict[str, str]:
    """What the model is told by each tool that reads what a guild keeps, asked in a conversation with `ctx` about
    the guild's report: the nights to come, the note, the plan, and whose kick it was."""
    session = Session(server.lib(), REPORT)
    session._ctx = ctx
    tools = {t.name: t for t in await session._tools()}

    async def told(tool: str, **args) -> str:
        return (await tools[tool].handler(args))["content"][0]["text"]
    return {"upcoming": await told("roster", upcoming=True), "note": await told("raid_note", boss="Sszorak", pull=42),
            "next note": await told("raid_note", boss="Sszorak", upcoming=True),
            "plan": await told("raid_plan", pull=42), "kicks": await told("enemies", **KICKS)}


@pytest.mark.parametrize("server_id", [CLAIMS, OTHER, NOT_SET_UP])
async def test_asked_about_a_guilds_report_from_another_server_the_tools_give_nothing_it_keeps(
        recorded, shared, member, channel, server_id):
    """Linking a guild's public report is all it takes to ask: the answers are the ones for someone who isn't a
    member, the same whether the guild saved a note, a plan and a Viserio key or nothing, so nothing gives it away."""
    guild = (await server.lib().report(REPORT)).guild_id
    assert guild and guild != 555
    shared.store.save_settings(replace(shared.store.settings(OWN), wcl_guild_id=guild))
    shared.store.save_settings(replace(shared.store.settings(CLAIMS), wcl_guild_id=guild))
    before = await answers(context(shared, server_id, member, channel))
    saves(shared, guild)
    after = await answers(context(shared, server_id, member, channel))
    assert before == after
    assert after["upcoming"].endswith("raid nights are for its members only")
    assert JOBS_NOT_LOGGED in after["kicks"] and "raid note saved" not in after["kicks"]
    note, plan = NOTE.read_text(encoding="utf-8"), PLAN.read_text(encoding="utf-8")
    assert not any(KEY in said or note[:40] in said or plan[:40] in said or "Rowan" in said
                   for said in after.values())
    none = {CLAIMS: ("Raid notes and plans are off here until an admin shows",) * 3,
            NOT_SET_UP: ("Raid notes are only kept in Discord",) * 2 + ("Raid plans are only kept in Discord",),
            # its own guild's notebook, which holds none for this boss
            OTHER: ("No raid note is saved for Sszorak that night in this server", "For the next raid, and ",
                    "No raid plan is saved for Sszorak in this server")}[server_id]
    assert all(after[what].startswith(said) for what, said in zip(("note", "next note", "plan"), none, strict=True))


async def test_asked_from_the_guilds_own_server_the_tools_read_what_it_keeps(recorded, shared, member, channel):
    report = await server.lib().report(REPORT)
    guild = report.guild_id or 0
    shared.store.save_settings(replace(shared.store.settings(OWN), wcl_guild_id=guild))
    saves(shared, guild, report.start_ms / 1000 - 3600)  # an hour before the night
    said = await answers(context(shared, OWN, member, channel))
    assert f"asked with {KEY}" in said["upcoming"]  # Viserio was asked with the guild's key
    assert said["note"].startswith("(The note was saved by Rowan")
    assert said["next note"].startswith("For the next raid, so here is the newest")
    assert said["plan"].startswith("(The plan was saved from pastes.)")
    assert "has no raid note saved for Nek'zali the Soulcoiler that night" in said["kicks"]
