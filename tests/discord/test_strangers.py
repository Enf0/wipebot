"""The view from outside, on a bot many servers share: an admin of another Discord server who links a guild that isn't
theirs, and a raider who links another guild's report, tried against what a guild keeps (its raid notes, plans and
Viserio key) and against what another asker reads Warcraft Logs with. Each thing the bot stores gets its strangers
here when it's added. The core's own are in tests/core/test_guilds.py."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE, PLAN
from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server, viewhost, wcl
from wipebot.core.notebook import Notebook
from wipebot.core.report.base import JOBS_NOT_LOGGED
from wipebot.discord import bot as wipebot
from wipebot.discord import commands, host, keys, messages, texts
from wipebot.discord.host import Answer, Context, Session, ToolCall
from wipebot.discord.posts import Following
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


async def test_a_note_pasted_where_nobody_set_the_server_up_asks_for_setup_whoever_pastes_it(shared, paste):
    """A raider signed in with /login has their own guild stand in for a server nobody set up: their paste is saved
    nowhere, and what it's told is to set the server up, not to verify a link that isn't there (on any bot)."""
    shared.store.save_login(8, 43, "Mallory#1", "mallory's token", 4e9,
                            guild=(14169, "The Copper Lanterns", "argent-dawn", "eu"))
    for own_keys in (True, False):
        shared.own_keys = own_keys
        told = await paste(NOT_SET_UP)
        assert "isn't set up yet: run `/wipebot setup` first" in told and "are off here" not in told
    assert shared.store.notebook.latest_notes(14169) == []


async def test_what_the_bot_runs_outside_any_askers_scope_reads_no_guilds_saved_things(bot, monkeypatch):
    """A call the bot makes without saying whose it is (Context.reading) reads nothing a guild keeps, not every
    guild's as a single user's own copy does: from its start on, in every task it starts after."""
    async def nothing(*args) -> None:
        pass
    monkeypatch.setattr(wipebot.emojis, "sync", nothing)
    bot.trying = True  # no polling here

    async def reads() -> bool:
        return server._a_member(14169)

    async def started() -> None:
        assert await reads()  # before the bot starts: nobody said whose
        await bot.setup_hook()
        assert not await reads() and not await asyncio.create_task(reads())  # here, and in a task started after
        with Context(guild_id=14169).reading():  # an asker's own scope still reads their server's guild's
            assert await asyncio.create_task(reads())
        assert not await asyncio.create_task(reads())
    await asyncio.create_task(started())  # in a task of its own, as the bot runs in one: nothing is left behind here
    assert await reads()


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
    the guild's report: the nights to come, the note, the plan, the plan drawn on the map, and whose kick it was."""
    session = Session(server.lib(), REPORT)
    session._ctx = ctx
    tools = {t.name: t for t in await session._tools()}

    async def told(tool: str, **args) -> str:
        return (await tools[tool].handler(args))["content"][0]["text"]
    return {"upcoming": await told("roster", upcoming=True), "note": await told("raid_note", boss="Sszorak", pull=42),
            "next note": await told("raid_note", boss="Sszorak", upcoming=True),
            "plan": await told("raid_plan", pull=42), "plan map": await told("pull_map", pull=42, plan=True),
            "kicks": await told("enemies", **KICKS)}


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
    assert "saved are for its members only: ask the user to paste the raid plan." in after["plan map"]
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
    assert said["plan map"].startswith('The raid plan "') and "saved by Rowan" in said["plan map"]
    assert "has no raid note saved for Nek'zali the Soulcoiler that night" in said["kicks"]


# ---------- one asker's sign-in is never another's ----------

PRIVATE = "PrivateReport001"  # a report Warcraft Logs shows only to some


def reads_as() -> tuple:
    """Who the core reads as right now: their Warcraft Logs token, their private-reports token, the guilds whose
    saved things they read, and whether a report they can't read comes with how they'd let Wipebot read it."""
    return wcl._user.get(), wcl.private_token(), server._guilds.get(), bool(server._unreadable_hint.get())


@pytest.fixture
def signed_in(bot, monkeypatch):
    """Rowan (7) is signed in with Warcraft Logs, for their private reports too; Brann (8) isn't."""
    monkeypatch.setenv("WIPEBOT_WCL_CALLBACK", "https://example.org/signed-in")
    bot.store.save_login(7, 42, "Rowan#1", "rowan's token", 4e9)
    bot.store.save_login(7, 42, "Rowan#1", "rowan's private token", 4e9, private=True)
    return bot


async def test_an_answer_is_read_as_its_asker_from_the_report_it_opens_on_to_its_pictures(signed_in, member, channel,
                                                                                         settings, monkeypatch):
    """A report only the asker may open: the conversation's report, the pictures' links and buttons are all read
    with the asker's own sign-in, so the answer posts; asked by someone who isn't signed in, the same picture's
    extras can't be read, and the answer still goes, without them."""
    bot = signed_in
    read: list[tuple[str, tuple]] = []
    call = ToolCall("pull_summary", {"report": PRIVATE, "pull": 3}, "wiped", False, [b"WEBP"])

    async def ask(key, code, question, ctx, earlier=None, message_id=None, timeout=None):
        read.append(("the conversation", reads_as()))
        return Answer("**Wiped.**", calls=[call])

    async def view_key(tool, args):
        read.append(("the picture's extras", reads_as()))
        if wcl.private_token() != "rowan's private token":
            raise wcl.Unreadable(PRIVATE)
        return None
    monkeypatch.setattr(bot.conversations, "ask", ask)
    monkeypatch.setattr(server, "view_key", view_key)
    for user in (member(7, "Rowan"), member(8, "Brann")):
        ctx = bot._context(settings, user, user.name, PRIVATE, "in #raid", channel)

        async def send(answer):
            return await bot._send(channel, answer)
        assert await bot._answer("chat", (20, user.id), PRIVATE, "why did we wipe?", ctx, user.name, send) is None
    rowan, brann = ("rowan's token", "rowan's private token", frozenset({14169}), False), (
        None, None, frozenset({14169}), True)
    assert read == [("the conversation", rowan), ("the picture's extras", rowan),
                    ("the conversation", brann), ("the picture's extras", brann)]
    assert [said.splitlines()[0] for said in channel.sent] == ["**Wiped.**"] * 2  # both posted, with the picture
    assert channel.files == [["wipebot-1.webp"]] * 2
    assert reads_as() == (None, None, None, False)  # and nothing of either is left behind


async def test_a_report_warcraft_logs_wont_show_is_said_as_that_not_as_an_outage(signed_in, member, channel,
                                                                                monkeypatch):
    """In a server not set up the report linked is the conversation's: a wrong, deleted or private one is no outage
    to try again after, and whoever may open a private one is told how to let the bot read it."""
    bot = signed_in

    async def ask(key, code, question, ctx, earlier=None, message_id=None, timeout=None):
        raise wcl.Unreadable(code)
    monkeypatch.setattr(bot.conversations, "ask", ask)

    async def answered(user) -> str | None:
        ctx = bot._context(ServerSettings.unset(NOT_SET_UP), user, user.name, PRIVATE, "in #raid", channel)
        return await bot._answer("chat", (20, user.id), PRIVATE, "why did we wipe?", ctx, user.name, lambda a: a)
    told = await answered(member(8, "Brann"))
    assert told == ("Warcraft Logs won't show me report PrivateReport001: it's private, was deleted, or the code is "
                    "wrong, so asking again won't help. If it's a private report you can open on Warcraft Logs, "
                    "`/login private:True` lets me read it for your questions; then ask again.")
    assert await answered(member(7, "Rowan")) == texts.UNREADABLE.to("en", code=PRIVATE)  # already lets it: no hint

    async def down(key, code, question, ctx, earlier=None, message_id=None, timeout=None):
        raise wcl.WCLError("502")
    monkeypatch.setattr(bot.conversations, "ask", down)
    assert await answered(member(8, "Brann")) == texts.WCL_DOWN.en


class Click:
    """A click on a picture's button by a Discord user, answered to them alone."""

    def __init__(self, bot, user_id: int):
        self.user, self.client, self.locale, self.said = SimpleNamespace(id=user_id), bot, "en-US", []
        self.response = SimpleNamespace(defer=self._say, send_message=self._say)
        self.followup = SimpleNamespace(send=self._say)

    async def _say(self, content=None, **kwargs):
        self.said.append(content)


async def test_a_button_and_a_nights_post_are_read_as_the_one_they_are_for(signed_in, member, channel, monkeypatch):
    bot = signed_in
    read: list[tuple] = []

    async def picture(tool, args):
        read.append(reads_as())
        return []
    monkeypatch.setattr(host, "picture", picture)
    await messages.act(cast(Any, Click(bot, 7)), "death", PRIVATE, "Sszorak", "3", "Torvek")
    await messages.act(cast(Any, Click(bot, 8)), "death", PRIVATE, "Sszorak", "3", "Torvek")
    await bot._night_post(Following(member(7, "Rowan"), "Rowan", None, PRIVATE, channel, "who died first"))
    await bot._night_post(Following(member(8, "Brann"), "Brann", None, PRIVATE, channel, "who died first"))
    rowan, brann = ("rowan's token", "rowan's private token", frozenset(), False), (None, None, frozenset(), True)
    assert read == [rowan, brann, rowan, brann]


async def test_a_pictures_data_is_read_as_nobody_or_as_its_own_asker_never_as_whoever_asked_first(monkeypatch):
    """The page a picture is drawn from asks for its data through the painter, which answers in the scope it was
    started in: the painter every asker shares is started outside all of theirs, and the picture of a report only
    its asker may read is drawn by a painter of their own."""
    pytest.importorskip("playwright")
    read: list[tuple] = []

    async def view_key(tool, args):
        return {"view": "pull", "report": args["report"], "pull": 1, "boss": "Sszorak"}

    async def answer(name, args):  # the page's call for the view's data
        read.append((*reads_as(), wcl._points.get() is not None))
        return {"isError": True, "content": [{"type": "text", "text": "not recorded"}]}
    async def loaded(code):  # a report anyone may read, already read by the tool call
        return SimpleNamespace(code=code)
    monkeypatch.setattr(server, "lib", lambda: SimpleNamespace(report=loaded))
    monkeypatch.setattr(server, "view_key", view_key)
    monkeypatch.setattr(viewhost, "answer", answer)
    monkeypatch.setattr(wcl, "_private_codes", {PRIVATE})
    monkeypatch.setattr(host, "_no_pictures", "")
    monkeypatch.setattr(host, "_painter", None)
    rowan = Context(wcl_token="rowan's token", wcl_private="rowan's private token", guild_id=14169,
                    private_hint="sign in")
    brann = Context(wcl_token="brann's token", private_hint="sign in")

    async def drawn(asker: Context, report: str) -> set[tuple]:
        read.clear()
        with asker.reading(), wcl.counting_points():  # as a tool call draws its picture
            await host.picture("pull_summary", {"report": report})
        if host._no_pictures:
            pytest.skip("needs Playwright's Chromium (just browser)")
        return set(read)
    try:
        nobody = {(None, None, frozenset(), False, True)}
        assert await drawn(rowan, "PublicReport0001") == nobody  # the first to ask after a start lends nothing
        assert await drawn(brann, "PublicReport0001") == nobody
        assert await drawn(rowan, PRIVATE) == {("rowan's token", "rowan's private token", frozenset({14169}), False,
                                                True)}
        assert await drawn(brann, PRIVATE) == {("brann's token", None, frozenset(), True, True)}
        assert await drawn(brann, "PublicReport0001") == nobody
    finally:
        if host._painter is not None:
            await host._painter.close()


async def test_a_report_not_read_since_the_bot_started_is_found_private_by_reading_it_as_its_asker(monkeypatch):
    """A button on an old answer about a private report, after a restart: nothing has read the report yet, so an
    asker who lets the bot read their private reports has it read as them first; anyone else's picture reads nothing
    more."""
    read: list[tuple] = []

    async def report(code):
        read.append((code, wcl.private_token()))
        wcl._private_codes.add(code)  # as the library finds a report only its asker may open
    monkeypatch.setattr(wcl, "_private_codes", set())
    monkeypatch.setattr(server, "lib", lambda: SimpleNamespace(report=report))
    assert not await host._private({"report": PRIVATE}) and not await host._private({}) and not read
    with Context(wcl_token="brann's token").reading():
        assert not await host._private({"report": PRIVATE}) and not read
    with Context(wcl_private="rowan's private token").reading():
        assert await host._private({"report": f"https://www.warcraftlogs.com/reports/{PRIVATE}#fight=3"})
    assert read == [(PRIVATE, "rowan's private token")]
    assert await host._private({"report": PRIVATE}) and len(read) == 1  # known from then on


def test_a_servers_key_never_shows_where_a_log_line_or_a_traceback_prints_what_holds_it():
    key = keys.ServerKey("openrouter", "sk-or-v1-secret")
    ctx = Context(where="in #raid", env=key.env())
    assert "secret" not in repr(key) + repr(ctx) + f"{key} {ctx}" and "openrouter" in repr(key)
