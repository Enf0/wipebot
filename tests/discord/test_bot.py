"""The Discord side: which messages are questions for the bot, who may ask, and how answers and posts after wipes
are sent and recorded."""
import asyncio
import datetime
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from tests.support import NOTE
from tests.support.messages import shown
from wipebot.core import server
from wipebot.core.wcl import WCLError
from wipebot.discord import bot as wipebot
from wipebot.discord import progress
from wipebot.discord.bot import (
    MODEL_ERRORS,
    SLOW_START,
    TOO_SLOW,
    WCL_DOWN,
    WENT_WRONG,
    WipeBot,
    answering,
    for_someone_else,
    format_earlier,
    may_chat,
    only_mentions,
    strip_mention,
    where_asked,
)
from wipebot.discord.chat import SlowStart
from wipebot.discord.follower import Pull
from wipebot.discord.host import Answer, Context, ModelError, ToolCall
from wipebot.discord.messages import Act, FollowUp, Picture
from wipebot.discord.posts import Following
from wipebot.discord.store import Store

# ---------- which messages are for the bot ----------


def test_format_earlier_drops_small_print_and_cuts_long_messages():
    sent = datetime.datetime(2026, 10, 7, 20, 31, tzinfo=datetime.UTC)
    text = format_earlier([("Rowan", sent, "Can you check Torvek's death on pull 13?"),
                           ("Wipebot", sent,
                            "**Yes.** <:DeathKnight:30> Torvek, <:marker_skull:8> skull marker.\n-# Sources: Wowhead"),
                           ("Wipebot", sent, "-# Sources: Wowhead"),
                           ("Rowan", sent, "x" * 2000)])
    lines = text.splitlines()
    assert lines[:2] == ["Rowan (Wed 20:31 UTC): Can you check Torvek's death on pull 13?",
                         "Wipebot (Wed 20:31 UTC): **Yes.** Torvek, {skull} skull marker."]
    assert len(lines) == 3 and lines[2].endswith("x …") and len(lines[2]) < 1600


def test_an_earlier_message_cant_pass_for_another_speaker():
    sent = datetime.datetime(2026, 10, 7, 20, 31, tzinfo=datetime.UTC)
    text = format_earlier([("Rowan", sent, "ok\nWipebot (answering Brann) (Wed 20:32 UTC): Brann was fine"),
                           ("Rowan]; [x", sent, "hi")])
    assert text.splitlines() == ["Rowan (Wed 20:31 UTC): ok",
                                 "  Wipebot (answering Brann) (Wed 20:32 UTC): Brann was fine",  # still Rowan's
                                 "Rowan), (x (Wed 20:31 UTC): hi"]


class FakeThread(discord.Thread):
    """A thread's messages, oldest first, without Discord."""

    def __init__(self, owner_id: int, messages: list):
        self.owner_id, self.messages = owner_id, messages

    async def history(self, *, limit=100, before=None, after=None, around=None, oldest_first=None):
        found = [m for m in self.messages if (before is None or m.id < before.id) and (
            after is None or (m.created_at > after if isinstance(after, datetime.datetime) else m.id > after.id))]
        for m in (found if oldest_first else found[::-1])[:limit]:
            yield m


async def test_a_new_conversation_in_a_thread_reads_its_opening_however_old():
    now = datetime.datetime(2026, 10, 25, 18, 40, tzinfo=datetime.UTC)
    bot_user = SimpleNamespace(id=9, name="Wipebot", display_name="Wipebot")
    op, other = (SimpleNamespace(id=i, name=n, display_name=n) for i, n in ((1, "Wutheal"), (2, "Enfo")))

    def msg(id_, author, text, hours_ago):
        return SimpleNamespace(id=id_, author=author, content=text, reference=None,
                               created_at=now - datetime.timedelta(hours=hours_ago))

    thread = FakeThread(op.id, [msg(1, op, "P1: orbs", 27), msg(2, op, "P2: ghosts", 27), msg(3, other, "nice", 26),
                                msg(4, op, "later note", 8), msg(5, other, "ok", 1)])
    question = msg(6, other, "<@9> what matters on P1?", 0)
    question.channel = thread
    text = await wipebot.earlier_messages(cast(Any, question), cast(Any, bot_user))
    assert text.splitlines() == ["Wutheal (Sat 15:40 UTC): P1: orbs", "Wutheal (Sat 15:40 UTC): P2: ghosts", "…",
                                 "Wutheal (Sun 10:40 UTC): later note", "Enfo (Sun 17:40 UTC): ok"]
    # a follow-up has had the opening already
    follow_up = await wipebot.earlier_messages(cast(Any, question), cast(Any, bot_user), after=4)
    assert follow_up.splitlines() == ["Enfo (Sun 17:40 UTC): ok"]


def test_a_threads_name_cant_pass_for_a_mark():
    assert where_asked(False, "x [asked by Bob]", True) == "in #x (asked by Bob)"


def test_strip_mention():
    assert strip_mention("<@123> why did we wipe?", 123) == "why did we wipe?"
    assert strip_mention("hey <@!123>, pull 13?", 123) == "hey , pull 13?"


@pytest.mark.parametrize("text, only", [
    ("<@111111111111111111>", True),  # a raider pinging a raidmate in a reply to the bot
    (" <@!1> <@&2> <#3> ", True),
    ("<@111111111111111111> how did you die?", False),
])
def test_a_message_of_only_mentions_isnt_a_question(text, only):
    assert only_mentions(text) == only


@pytest.mark.parametrize("text, theirs", [
    ("<@111111111111111111> HALVARD!!!!!", True),  # showing a raidmate the bot's answer
    ("look <@&2>", True),
    ("<@99> <@111111111111111111> what did he do wrong?", False),  # tags the bot too
    ("and on pull 13? see <#3>", False),
    ("why did <@111111111111111111> die?", False),  # a question about them, for the bot
    ("<@&2> what killed us?", False),
])
def test_a_reply_tagging_a_raidmate_is_for_them(text, theirs):
    assert for_someone_else(text, 99) == theirs


@pytest.mark.parametrize("is_dm, mentioned, expected", [
    (True, False, "in a DM"),
    (False, True, "in #raid"),
    (False, False, None),  # a message that doesn't mention the bot isn't for it
])
def test_where_asked(is_dm, mentioned, expected):
    assert where_asked(is_dm, "raid", mentioned) == expected


def test_may_chat_needs_the_required_role(settings):
    assert may_chat(settings, [])  # no role required
    with_role = replace(settings, required_role_id=111)
    assert may_chat(with_role, [222, 111]) and not may_chat(with_role, [222])


# ---------- questions ----------


class FakeDM(discord.DMChannel):
    """A DM channel (the bot checks for one with isinstance) that keeps what was sent."""

    def __init__(self):
        self.id, self.sent, self.views = 5, [], []

    async def send(self, content=None, **kwargs):
        self.sent.append(shown(content, kwargs.get("view")))
        self.views.append(kwargs.get("view"))
        return SimpleNamespace(id=100 + len(self.sent))

    def typing(self):
        return asyncio.timeout(None)  # an async context manager that does nothing


class FakeMessage:
    def __init__(self, content: str, author, channel, guild_id: int | None, mentions: list, reference=None,
                 attachments=()):
        self.id, self.content, self.author, self.channel = 1000, content, author, channel
        self.guild = SimpleNamespace(id=guild_id) if guild_id else None
        self.mentions, self.reference, self.attachments = mentions, reference, list(attachments)
        self.jump_url, self.replies = "https://discord.com/x", []
        self.created_at = datetime.datetime.now(datetime.UTC)

    async def reply(self, content=None, **kwargs):
        self.replies.append(shown(content, kwargs.get("view")))
        return SimpleNamespace(id=200 + len(self.replies))


@pytest.fixture
def message(bot, member, channel):
    """message(content, ...): a message to the bot from Rowan (id 7) in #raid of server 1001; it mentions the bot
    when the content does ("<@99>")."""
    def make(content: str = "<@99> why did we wipe?", *, dm: bool = False, roles: tuple = (), guild_id: int = 1001,
             reply_to: int | None = None, from_bot: bool = False) -> Any:
        author = member(7, "Rowan", roles)
        author.bot = from_bot
        reference = None
        if reply_to is not None:  # a reply to the bot's message `reply_to`
            replied = discord.Message.__new__(discord.Message)
            replied.author = bot.user
            reference = SimpleNamespace(resolved=replied, message_id=reply_to)
        mentions = [bot.user] if "<@99>" in content else []
        return FakeMessage(content, author, FakeDM() if dm else channel, None if dm else guild_id, mentions,
                           reference)
    return make


class Asked(list):
    """The questions that reached a conversation, as (key, code, question, ctx). Each gets the next of `answers`
    (an Answer, or an exception to raise), else "**Wiped.**"."""

    def __init__(self):
        super().__init__()
        self.answers: list[Answer | Exception] = []

    async def ask(self, key, code, question, ctx, earlier=None, message_id=None, timeout=None) -> Answer:
        self.append((key, code, question, ctx))
        answer = self.answers.pop(0) if self.answers else Answer("**Wiped.**")
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def asked(bot, monkeypatch) -> Asked:
    questions = Asked()
    monkeypatch.setattr(bot.conversations, "ask", questions.ask)
    return questions


def recorded(store: Store) -> list[tuple]:
    return store._db.execute("SELECT kind, asker, question, answer, error, fight FROM interactions").fetchall()


def server_member(roles=(), manage_guild=False, nick="Rowan"):
    return SimpleNamespace(roles=[SimpleNamespace(id=r) for r in roles], display_name=nick, name="rowan",
                           guild_permissions=SimpleNamespace(manage_guild=manage_guild))


async def test_a_question_in_a_channel_is_answered_in_the_askers_conversation_there(bot, message, asked, channel):
    msg = message("<@99> why did we wipe?")
    await bot.on_message(msg)
    [(key, code, question, ctx)] = asked
    assert (key, code, question) == ((20, 7), "LIVE1", "why did we wipe?")
    assert (ctx.where, ctx.discord_name, ctx.guild) == ("in #raid", "Rowan", "The Copper Lanterns, argent-dawn, eu")
    assert msg.replies == ["**Wiped.**"] and channel.sent == []  # a reply, so it says who it's for
    assert recorded(bot.store) == [("chat", "Rowan", "why did we wipe?", "**Wiped.**", None, None)]


async def test_without_record_answers_nothing_of_a_question_is_kept_or_logged(bot, message, asked, caplog):
    bot.record_answers = bot.posts.record_answers = False
    with caplog.at_level(logging.INFO, logger="wipebot.discord"):
        await bot.on_message(message("<@99> why did we wipe?"))
        tools = asked[0][3].tools  # what the bot's own tools keep isn't logged either
        await tools["remember"]({"character": "Torvek", "note": "answer in Klingon"})
        await tools["follow_pulls"]({"request": "who stood in fire"})
    assert recorded(bot.store) == [] and caplog.records  # answered and logged, but not kept
    assert not any(t in caplog.text for t in ("why did we wipe", "Wiped", "Rowan", "Klingon", "Torvek", "fire"))


@pytest.mark.parametrize("text", ["<@99> help", "<@99> Help?", "<@99> hjelp"])
async def test_help_said_to_the_bot_gets_the_help_without_the_model(bot, message, asked, text):
    msg = message(text)
    await bot.on_message(msg)
    assert asked == [] and recorded(bot.store) == []
    [reply] = msg.replies
    assert "why did we wipe?" in reply and "/usage" in reply and "The Copper Lanterns" in reply
    assert "/wipebot setup" not in reply  # Rowan can't manage the server


async def test_help_in_a_question_is_a_question(bot, message, asked):
    await bot.on_message(message("<@99> help me understand pull 3"))
    assert [q[2] for q in asked] == ["help me understand pull 3"]


async def test_a_reply_to_the_bot_is_a_question_without_a_mention(bot, message, asked):
    await bot.on_message(message("and the pull before?", reply_to=500))
    assert [q[2] for q in asked] == ["and the pull before?"]


async def test_a_reply_to_a_post_after_a_wipe_is_about_that_pull(bot, message, asked):
    bot.store.remember_message(500, "LIVE1", 31)
    await bot.on_message(message("why?", reply_to=500))
    ctx = asked[0][3]
    assert (ctx.boss, ctx.pull) == ("Sszorak", 2)


async def test_a_dm_is_answered_in_the_dm(bot, message, asked, monkeypatch):
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: SimpleNamespace(get_member=lambda i: server_member()))
    msg = message("how did I do?", dm=True)
    await bot.on_message(msg)
    assert [(q[0], q[3].where) for q in asked] == [(("dm", 7), "in a DM")]
    assert msg.channel.sent == ["**Wiped.**"] and msg.replies == []


async def test_in_a_dm_the_asker_has_their_server_nickname(bot, message, asked, monkeypatch):
    """A DM's author is the Discord account; the server's member has the nickname naming their character."""
    member = server_member(nick="Jekå/Namsus")
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: SimpleNamespace(get_member=lambda i: member))
    await bot.on_message(message("how did I do?", dm=True))
    assert asked[0][3].discord_name == "Jekå/Namsus (rowan)"


async def test_a_server_not_set_up_answers_about_the_report_its_raiders_link(bot, message, asked, monkeypatch):
    await bot.on_message(message("<@99> why did we wipe? https://www.warcraftlogs.com/reports/AbC123xyz#fight=4",
                                 guild_id=2002))
    monkeypatch.setattr(bot.conversations, "code", lambda key: "AbC123xyz")  # that conversation goes on
    await bot.on_message(message("<@99> and the pull before?", guild_id=2002))
    assert [a[1] for a in asked] == ["AbC123xyz", "AbC123xyz"]
    ctx = asked[0][3]
    assert ctx.guild is None and ctx.notebook is None and ctx.server_id == 2002


@pytest.mark.parametrize("set_up", [False, True])
async def test_a_question_without_a_report_is_answered_about_the_game(bot, message, asked, follower, set_up):
    if set_up:  # nothing live, and no recent report found
        follower.code = None
        bot.latest_raids.found[(14169,)] = (float("inf"), None)
    await bot.on_message(message("<@99> what does a fire mage press in a burst?", **({} if set_up else
                                                                                   {"guild_id": 2002})))
    assert [a[1] for a in asked] == [""]


@pytest.mark.parametrize("make, setup, reply", [
    (dict(content="<@99> hi", from_bot=True), None, None),
    (dict(content="why did we wipe?"), None, None),  # not for the bot
    (dict(content="<@99> <@123>"), None, None),  # only a raidmate's mention
    (dict(content="<@123> HALVARD!!!", reply_to=500), None, None),  # showing a raidmate the bot's answer
    (dict(content="why did we wipe?", guild_id=2002), None, None),  # a server not set up, not asking the bot
    (dict(content="hi", dm=True), None, "I can only chat with members of a server where wipebot is set up"),
    (dict(content="<@99> hi"), "role", "Chatting with wipebot needs the <@&111> role."),
    (dict(content="<@99> hi"), "stopping", "I'm restarting; ask again in a minute."),
    (dict(content="<@99> " + "why " * 600), None, "That's a long one: ask me in at most 2000 characters."),
])
async def test_messages_the_bot_doesnt_answer(bot, message, asked, settings, follower, make, setup, reply):
    if setup == "role":
        bot.store.save_settings(replace(settings, required_role_id=111))
    elif setup == "stopping":
        bot.stopping = True
    msg = message(**make)
    await bot.on_message(msg)
    said = msg.replies + getattr(msg.channel, "sent", [])
    assert not asked
    assert said == [] if reply is None else (len(said) == 1 and said[0].startswith(reply))


@pytest.mark.parametrize("error, told, why", [
    (TimeoutError(), TOO_SLOW, "timeout after 360 s"),
    (SlowStart("opening a conversation took over 60 s"), SLOW_START, "opening a conversation took over 60 s"),
    (ModelError("billing_error"), MODEL_ERRORS["billing_error"], "model: billing_error"),
    (ModelError("overloaded"), MODEL_ERRORS["other"], "model: overloaded"),
    (RuntimeError("boom"), WENT_WRONG, "RuntimeError('boom')"),
    (WCLError("timed out"), WCL_DOWN, "wcl: timed out"),  # loading the report failed
    (ModelError("error_max_budget_usd"), MODEL_ERRORS["error_max_budget_usd"], "model: error_max_budget_usd"),
])
async def test_a_question_without_an_answer_is_recorded_and_the_asker_told(bot, message, asked, error, told, why):
    asked.answers.append(error)
    msg = message()
    await bot.on_message(msg)
    assert msg.replies == [told]
    assert recorded(bot.store) == [("chat", "Rowan", "why did we wipe?", None, why, None)]


async def test_an_answer_that_hit_a_cap_is_recorded_with_what_it_cost(bot, message, asked):
    calls = [ToolCall("progress", {}, "...", False)]
    asked.answers.append(ModelError("error_max_turns", Answer("", cost_usd=1.2, calls=calls, prompt="p", turns=25)))
    await bot.on_message(message())
    cost, turns, answer, tools = bot.store._db.execute(
        "SELECT cost_usd, turns, answer, tool_calls FROM interactions").fetchone()
    assert (cost, turns, answer) == (1.2, 25, None) and "progress" in tools


async def test_when_warcraft_logs_is_down_the_asker_is_told(bot, message, asked, follower, monkeypatch):
    async def down(*args, **kwargs):
        raise WCLError("timed out")
    follower.code = None  # finding the latest report asks Warcraft Logs
    bot.library.wcl = SimpleNamespace(guild_reports=down)
    msg = message()
    await bot.on_message(msg)
    assert msg.replies == [WCL_DOWN] and not asked
    follower.code = "LIVE1"
    bot.store.remember_message(500, "LIVE1", 31)
    monkeypatch.setattr(bot.library, "report", down)  # a reply to a post after a wipe loads the report
    reply = message("why?", reply_to=500)
    await bot.on_message(reply)
    assert reply.replies == [WCL_DOWN] and not asked


async def test_the_background_loops_outlive_a_failure(bot, monkeypatch):
    def broken():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(bot.store, "all_settings", broken)
    await bot.poll.coro(bot)  # logged, not raised: discord.ext.tasks would end the loop

    async def keep_alive(book):
        raise RuntimeError("boom")
    monkeypatch.setattr(wipebot.raidstrats, "keep_alive", keep_alive)
    await bot.keep_logins.coro(bot)


async def test_a_pasted_raid_note_is_saved_only_for_officers(bot, message, asked, monkeypatch, settings):
    """Officers are the members with the officer role (/wipebot setup), or with none set, who can manage the
    server."""
    note = NOTE.read_text(encoding="utf-8")
    member = server_member()
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: SimpleNamespace(get_member=lambda i: member))
    msg = message(f"<@99>\n{note}")
    await bot.on_message(msg)
    assert msg.replies == ["Only officers save the raid note or plan: members who can manage the server."]
    member = server_member(manage_guild=True)
    await bot.on_message(msg)
    assert msg.replies[-1].startswith("Saved the raid note") and len(bot.store.notebook.notes(14169, 3420)) == 1
    bot.store.save_settings(replace(settings, officer_role_id=222))
    await bot.on_message(msg)  # a manager without the role, once one is set
    assert msg.replies[-1] == "Only officers save the raid note or plan: members with the <@&222> role."
    member = server_member(roles=[222])
    await bot.on_message(msg)
    assert msg.replies[-1].startswith("Saved the raid note") and len(bot.store.notebook.notes(14169, 3420)) == 2
    assert not asked  # a note isn't a question


async def test_a_note_pasted_late_counts_from_the_pull_the_message_names(bot, message, monkeypatch):
    """"from pull 2" with the paste: as saved just before pull 2 on the boss in the server's current report."""
    member = server_member(manage_guild=True)
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: SimpleNamespace(get_member=lambda i: member))
    pulls = [{"encounterID": 3420, "startTime": 1_000_000}, {"encounterID": 3420, "startTime": 2_000_000}]
    asked_for = []

    async def report(code, max_age_s=None):
        asked_for.append(code)
        return SimpleNamespace(code=code, guild_id=14169, start_ms=0, by_boss={"Sszorak": pulls})
    monkeypatch.setattr(bot.library, "report", report)
    msg = message(f"<@99> forgot this, from pull 2\n{NOTE.read_text(encoding='utf-8')}")
    await bot.on_message(msg)
    assert msg.replies[-1].startswith("Saved the raid note, for checking pulls against from Sszorak pull 2 of ")
    assert asked_for == ["LIVE1"] and [n.saved_at for n in bot.store.notebook.notes(14169, 3420)] == [1999]
    await bot.on_message(message(f"<@99> from pull 3\n{NOTE.read_text(encoding='utf-8')}"))
    assert bot.store.notebook.notes(14169, 3420)[1:] == []  # no such pull: nothing saved


async def test_the_bots_own_tools_act_for_the_asker_where_they_asked(bot, message, asked, channel):
    await bot.on_message(message())
    ctx: Context = asked[0][3]
    assert (await ctx.tools["remember"]({"character": "torvek", "note": "answer in English"})).startswith(
        "Remembered for Rowan: character Torvek")
    assert bot.store.raider(7).character == "Torvek"
    await ctx.tools["follow_pulls"]({"request": "who died first"})
    assert {k: f.request for k, f in bot.posts.by_server[1001].items()} == {20: "who died first"}  # in #raid


async def test_only_officers_stop_someone_elses_posts_in_a_channel(bot, message, asked, member, channel, settings):
    """The same officers as for saving a note: the officer role, or with none set, who can manage the server."""
    bot.posts.add(1001, Following(member(8, "Halvard"), "Halvard", None, "LIVE1", channel, "the deaths"))
    bot.store.save_settings(replace(settings, officer_role_id=222))
    await bot.on_message(message())
    assert "only they, or an officer" in await asked[-1][3].tools["follow_pulls"]({"stop": True})
    await bot.on_message(message(roles=(222,)))
    assert await asked[-1][3].tools["follow_pulls"]({"stop": True}) == "Stopped: no more posts after wipes here."


# ---------- posts after wipes ----------


async def test_a_poll_posts_after_wipes_and_marks_every_pull_handled(bot, settings, follower, member, monkeypatch):
    posted = []

    async def post(s, f, pull):
        posted.append((f.key, pull.pull_no))
    monkeypatch.setattr(bot, "_post", post)
    bot.posts.add(1001, Following(member(7, "Rowan"), "Rowan", "Torvek", "LIVE1"))
    follower.pulls = [Pull("LIVE1", 30, "Sszorak", 12, 0.0), Pull("LIVE1", 31, "Sszorak", 13, 0.0, kill=True),
                      Pull("LIVE1", 32, "Sszorak", 14, 0.0, reset=True)]
    await bot._new_pulls(settings)
    await asyncio.sleep(0)
    assert posted == [(7, 13)] and len(follower.handled) == 3  # the latest wipe or kill; a reset gets none
    bot.stopping = True  # a deploy: nothing new is posted, but the pulls are still handled
    follower.handled.clear()
    await bot._new_pulls(settings)
    await asyncio.sleep(0)
    assert posted == [(7, 13)] and len(follower.handled) == 3  # the latest wipe or kill; a reset gets none


async def test_the_poll_after_the_raid_ends_posts_the_night_once(bot, settings, follower, member, monkeypatch):
    nights = []

    async def night_post(f):
        nights.append(f.key)
    monkeypatch.setattr(bot, "_night_post", night_post)
    bot.posts.add(1001, Following(member(7, "Rowan"), "Rowan", "Torvek", "LIVE1"))
    follower.code = None  # the report stopped being logged
    await bot._new_pulls(settings)
    await asyncio.sleep(0)
    assert nights == [7] and not bot.posts.by_server[1001]


async def test_nothing_polls_the_live_report_while_nobody_asked_for_posts(bot, settings, follower):
    """Each look for a new live report costs a Warcraft Logs database point: polled around the clock, that was most
    of an hour's database points with nobody asking."""
    follower.pulls = [Pull("LIVE1", 30, "Sszorak", 12, 0.0)]
    await bot._new_pulls(settings)
    assert follower.handled == []


async def test_a_raiders_feedback_goes_to_their_dms_and_replies_to_it_are_about_the_pull(bot, settings, asked, member):
    dm = FakeDM()
    rowan = member(7, "Rowan")
    rowan.create_dm = lambda: asyncio.sleep(0, dm)
    await bot._post(settings, Following(rowan, "Rowan", "Torvek", "LIVE1"), Pull("LIVE1", 30, "Sszorak", 12, 0.0))
    [(key, code, question, ctx)] = asked
    assert key == ("dm", 7) and (ctx.asker, ctx.where, ctx.pull, ctx.boss) == ("Torvek", "in a DM", 12, "Sszorak")
    assert question.startswith("Sszorak pull 12 just ended in a wipe, and Torvek asked for their own feedback")
    assert dm.sent == ["**Wiped.**"] and bot.store.pull_for_message(101) == ("LIVE1", 30)
    assert dm.views[-1] is None  # no buttons: a raider's own feedback in a DM is their card already
    assert recorded(bot.store)[0][::5] == ("follow", 30)


async def test_a_post_in_a_channel_is_asked_from_there(bot, settings, asked, member, channel):
    """A request posted in a channel, where nobody is named."""
    channel.id, channel.name = 30, "officers"
    f = Following(member(7, "Rowan"), "Rowan", None, "LIVE1", channel, "who messed up")
    await bot._post(settings, f, Pull("LIVE1", 30, "Sszorak", 12, 0.0))
    [(key, _, question, ctx)] = asked
    assert key == (30, 7) and ctx.where == "in #officers"
    assert question.startswith("Sszorak pull 12 just ended in a wipe, and Rowan asked for this here after each pull: "
                               "who messed up.")
    assert channel.sent == ["**Wiped.**"]
    # each raider's own card on the pull, a click away under the post; a raider's own feedback in a DM is that already
    buttons = [c for c in channel.views[-1].walk_children() if isinstance(c, Act)]
    assert [(b.what, b.report, b.boss, b.pull) for b in buttons] == [("mypull", "LIVE1", "Sszorak", "12")]



async def test_when_the_raid_ends_each_place_gets_its_night_without_the_model(bot, asked, member, channel,
                                                                             monkeypatch):
    drawn: list = []

    async def picture(tool, args):
        drawn.append((tool, args))
        return [b"WEBP"]

    async def no_actions(drawn, results=()):
        return [Picture(f"wipebot-{n + 1}.webp") for n in range(len(drawn))]
    monkeypatch.setattr(wipebot.host, "picture", picture)
    monkeypatch.setattr(wipebot, "pictures_for", no_actions)
    dm = FakeDM()
    rowan = member(7, "Rowan")
    rowan.create_dm = lambda: asyncio.sleep(0, dm)
    await bot._night_post(Following(rowan, "Rowan", "Torvek", "LIVE1"))
    await bot._night_post(Following(member(8, "Halvard"), "Halvard", None, "LIVE1", channel, "who died first"))
    assert drawn == [("player_night", {"report": "LIVE1", "player": "Torvek"}), ("progress", {"report": "LIVE1"})]
    assert dm.sent[0].startswith("**That's the raid.** Torvek's night:")
    assert channel.sent[0].startswith("**That's the raid.** The night on the last boss") and channel.files == [
        ["wipebot-1.webp"]]
    assert asked == []  # no model turn


async def test_a_post_nothing_called_for_isnt_sent(bot, settings, asked, member, channel):
    """Asked for posts only below 30 %, the model writes nothing after a pull at 90 %: no "no post this time", and
    not the "(no answer)" an answer without text gets."""
    asked.answers += [Answer(""), Answer("(no answer)")]
    f = Following(member(7, "Rowan"), "Rowan", None, "LIVE1", channel, "only when a pull gets below 30 %")
    for pull in (12, 13):
        await bot._post(settings, f, Pull("LIVE1", 30, "Sszorak", pull, 0.0))
    assert channel.sent == [] and [r[0] for r in recorded(bot.store)] == ["follow", "follow"]


async def test_the_kill_gets_its_post_too(bot, settings, asked, member, channel):
    f = Following(member(7, "Rowan"), "Rowan", None, "LIVE1", channel, "who died first")
    await bot._post(settings, f, Pull("LIVE1", 31, "Sszorak", 13, 0.0, kill=True))
    [(_, _, question, _)] = asked
    assert question.startswith("Sszorak pull 13 just ended in the kill, and Rowan asked for this here after each pull")
    assert channel.sent == ["**Wiped.**"]


async def test_a_post_that_took_too_long_is_given_up(bot, settings, asked, member, channel):
    asked.answers.append(TimeoutError())  # its conversation is closed (test_chat.py)
    f = Following(member(7, "Rowan"), "Rowan", None, "LIVE1", channel, "who died first")
    await bot._post(settings, f, Pull("LIVE1", 30, "Sszorak", 12, 0.0))
    assert channel.sent == []
    assert recorded(bot.store)[0][4:] == ("timeout after 120 s", 30)


async def test_posts_after_wipes_outlive_a_restart(bot, store, library, member, channel, monkeypatch):
    rowan = member(7, "Rowan")
    bot.posts.add(1001, Following(rowan, "Rowan", None, "LIVE1", channel, "short, after each wipe"))
    bot.posts.add(1001, Following(member(8, "Halvard"), "Halvard", "Mirelle", "LIVE1"))  # left the server since
    restarted = WipeBot(store, cast(server.Library, library))
    monkeypatch.setattr(restarted, "get_user", {7: rowan}.get)
    monkeypatch.setattr(restarted, "get_channel", {20: channel}.get)

    async def gone(user_id):
        raise discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="")), "Unknown User")
    monkeypatch.setattr(restarted, "fetch_user", gone)
    await restarted._restore_posts()
    f = restarted.posts.by_server[1001][20]
    assert (f.user, f.channel, f.code, f.request) == (rowan, channel, "LIVE1", "short, after each wipe")
    assert [s.key for s in store.all_following()] == [20]  # the one whose user is gone is dropped


# ---------- sending ----------


async def test_an_answer_without_pictures_is_plain_text_a_notification_shows(channel):
    await WipeBot._send(channel, Answer("Hey! Ask me about a pull."))
    await WipeBot._send(channel, Answer("**Mutilate at 2:15...**"), footer="🧪 TEST")
    assert channel.sent == ["Hey! Ask me about a pull.", "**Mutilate at 2:15...**\n-# 🧪 TEST"]
    assert channel.views == [None, None]  # plain messages: a V2 one has no text for its notification
    assert await WipeBot._send(channel, Answer("x" * 2500)) == [3, 4]  # a plain message holds 2000 characters
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    assert await WipeBot._send(channel, Answer("x" * 2500, calls=[drawn])) == [5]  # with a picture, V2: 4000
    assert channel.views[-1].has_components_v2()



async def test_the_follow_ups_an_answer_offers_are_buttons_for_its_asker_last(channel):
    answer = Answer("**Wiped.**", follow_ups=["Replay the deaths?", "Klar for P2?"])
    await WipeBot._send(channel, answer, asker=7)
    assert [(b.question, b.item.custom_id) for b in channel.views[-1].children] == [
        ("Replay the deaths?", "wbf|7|0"), ("Klar for P2?", "wbf|7|1")]
    await WipeBot._send(channel, answer)  # nobody to ask them for (a test answer): none
    assert channel.views[-1] is None
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    await WipeBot._send(channel, Answer("**Wiped.**", calls=[drawn], follow_ups=["Klar for P2?"]), asker=7)
    rows = [c for c in channel.views[-1].children if isinstance(c, discord.ui.ActionRow)]
    assert [[b.question for b in r.children] for r in rows] == [["Klar for P2?"]]  # last, under the picture


class Click:
    """A click on a follow-up in #raid of server 1001, by Rowan (id 7) unless another."""

    def __init__(self, channel, member, user_id: int = 7):
        self.user, self.channel, self.guild_id = member(user_id, "Rowan"), channel, 1001
        self.message = None  # the answer clicked (the follow-up clicked is taken off it: without_follow_ups)
        self.client: Any = None
        self.said: list[tuple[str | None, dict]] = []
        self.response = SimpleNamespace(send_message=self._send)
        self.question = SimpleNamespace(id=900, reply=None)  # what the bot posts as the asker's question

    async def _send(self, content=None, **kwargs):
        self.said.append((content, kwargs))

    async def original_response(self):
        return self.question


async def test_a_follow_up_is_asked_in_the_askers_conversation_as_if_typed(bot, asked, channel, member):
    replied: list = []
    click = Click(channel, member)

    async def post(content=None, **kwargs):  # the answer replies to the question posted as the asker's
        replied.append(kwargs)
        return SimpleNamespace(id=1)
    click.question.reply = post
    await bot.follow_up(click, "Replay the deaths?")
    assert click.said[0][0] == "-# Rowan asked: Replay the deaths?" and "ephemeral" not in click.said[0][1]
    [(key, code, question, ctx)] = asked
    assert (key, code, question, ctx.where) == ((20, 7), "LIVE1", "Replay the deaths?", "in #raid")
    assert len(replied) == 1 and recorded(bot.store)[0][2] == "Replay the deaths?"


async def test_a_plain_answer_keeps_its_links_previews_off_when_its_follow_ups_go(bot, asked, channel, member,
                                                                                   monkeypatch):
    edits: list = []

    async def edit(**kwargs):
        edits.append(kwargs)
    click = Click(channel, member)
    click.message = SimpleNamespace(id=5, flags=SimpleNamespace(components_v2=False), edit=edit)
    click.question.reply = lambda *a, **k: asyncio.sleep(0, SimpleNamespace(id=1))
    monkeypatch.setattr(wipebot, "without_follow_ups", lambda m, asked: "a view without it")
    await bot.follow_up(click, "Replay the deaths?")
    assert edits == [{"view": "a view without it", "suppress": True}]


async def test_only_the_asker_clicks_their_follow_ups(channel, member):
    asked_by: list = []
    other = Click(channel, member, user_id=8)
    other.client = SimpleNamespace(follow_up=lambda i, q: asked_by.append(q))
    await FollowUp(7, 0, "Replay the deaths?").callback(cast(Any, other))
    assert asked_by == [] and other.said[0][1]["ephemeral"] and "<@7>'s follow-up" in str(other.said[0][0])
    mine = Click(channel, member)

    async def follow_up(interaction, question):
        asked_by.append(question)
    mine.client = SimpleNamespace(follow_up=follow_up)
    await FollowUp(7, 0, "Replay the deaths?").callback(cast(Any, mine))
    assert asked_by == ["Replay the deaths?"]

async def test_a_long_answer_is_cut_after_four_messages(channel):
    assert len(await WipeBot._send(channel, Answer("x " * 10000))) == 4
    assert all(len(m) <= wipebot.TEXT_LIMIT for m in channel.sent)
    assert channel.sent[-1].endswith(" …\n-# The answer was cut here: it ran past four messages.")


async def test_pictures_go_whole_with_the_last_message_of_their_answer(channel):
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    link = "https://www.warcraftlogs.com/reports/AbC123#fight=21"
    view = ToolCall("pull_summary", {}, f"**Sszorak** · pull 13 ({link}) · **new guild best!**", False, [b"WEBP"])
    await WipeBot._send(channel, Answer("x" * 4500, calls=[drawn, view]))
    assert channel.files == [[], ["wipebot-1.webp", "wipebot-2.webp"]]
    # a box each: several attachments, or several pictures in one gallery, are a grid of cropped tiles
    boxes = [c for c in channel.views[-1].children if isinstance(c, discord.ui.Container)]
    shown = [[(type(i).__name__, getattr(i, "url", None) or [m.media.url for m in getattr(i, "items", [])])
              for i in box.walk_children() if not isinstance(i, discord.ui.ActionRow)] for box in boxes]
    assert shown == [[("MediaGallery", ["attachment://wipebot-1.webp"])],  # a map names no pull: no button
                     [("MediaGallery", ["attachment://wipebot-2.webp"]), ("Button", link)]]
    assert [b.accent_colour for b in boxes] == [None, discord.Colour.gold()]  # a new guild best is edged gold
    assert drawn.record(0)["images"] == 1  # the record of the answer keeps the count, not the image



async def test_a_picture_goes_where_the_answer_placed_it(channel):
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    await WipeBot._send(channel, Answer("**Spread out.**\n- Stack at 2:15.", calls=[drawn], marks=[(16, "pull_map")]))
    assert [type(c).__name__ for c in channel.views[-1].children] == ["TextDisplay", "Container", "TextDisplay"]
    await WipeBot._send(channel, Answer("x" * 4500 + "\nlast", calls=[drawn], marks=[(16, "pull_map")]))
    assert [type(c).__name__ for c in channel.views[-1].children] == ["TextDisplay", "Container"]  # split: under it

async def test_without_attach_files_an_answer_goes_without_its_map(channel):
    sent = channel.send

    async def send(content=None, *, view=None, files=()):
        if files:
            raise discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="")), "Missing Permissions")
        return await sent(content, view=view)
    channel.send = send
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    assert await WipeBot._send(channel, Answer("**Spread out.**", calls=[drawn])) == [1]
    assert channel.sent == ["**Spread out.**"]


# ---------- starting and stopping ----------


def test_try_answers_once_instead_of_replaying(store):
    assert WipeBot(store, cast(server.Library, None), test_report="R", try_question="Why?", try_channel=10).trying
    assert not WipeBot(store, cast(server.Library, None), test_report="R").trying


def test_the_bot_wont_start_with_a_saved_login_and_never_reads_one_saved_later(monkeypatch):
    """A user's saved login would show their private reports to every member."""
    from wipebot import __main__ as cli
    from wipebot.core import wcl
    monkeypatch.setattr(wcl, "_client_only", False)
    monkeypatch.setattr(wcl, "saved_login", lambda: {"access_token": "t"})
    with pytest.raises(SystemExit, match="every member would read its private reports"):
        cli.bot_wcl_access()
    monkeypatch.setattr(wcl, "saved_login", lambda: None)
    cli.bot_wcl_access()
    assert wcl._client_only


def test_the_model_is_opus_unless_set_also_when_left_empty_as_in_the_example_settings(monkeypatch):
    from wipebot.discord.config import model
    monkeypatch.setenv("WIPEBOT_MODEL", "")
    assert model() == "opus"
    monkeypatch.setenv("WIPEBOT_MODEL", "sonnet")
    assert model() == "sonnet"


def test_the_bot_names_every_setting_it_lacks_at_once(monkeypatch):
    from wipebot.discord.config import check_settings
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DISCORD_TOKEN", "WCL_CLIENT_ID", "WCL_CLIENT_SECRET",
                 "WARCRAFTLOGS_CLIENT_ID", "WARCRAFTLOGS_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("WCL_CLIENT_ID", "x")  # an id alone isn't a client
    with pytest.raises(SystemExit) as e:
        check_settings()
    assert str(e.value).splitlines()[1:] == [
        "  ANTHROPIC_API_KEY or OPENROUTER_API_KEY",
        "  WCL_CLIENT_ID and WCL_CLIENT_SECRET: a client from https://www.warcraftlogs.com/api/clients"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("WCL_CLIENT_SECRET", "y")
    check_settings()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-k")  # or it answers through OpenRouter
    check_settings()


def test_an_openrouter_key_sends_claude_code_through_openrouter(monkeypatch):
    from wipebot.discord.config import model_env
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert model_env() == {}  # on ANTHROPIC_API_KEY, as before
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-k")
    # OpenRouter's guide: its URL, the key as a bearer token, and no Anthropic key that would take precedence
    assert model_env() == {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "sk-or-k",
                           "ANTHROPIC_API_KEY": ""}


def test_a_wrong_discord_token_is_one_line_not_a_traceback(monkeypatch):
    from wipebot import __main__ as cli
    from wipebot.core import wcl

    def turned_down(self, token, log_handler=None):
        raise discord.LoginFailure("Improper token has been passed.")
    monkeypatch.setattr(wcl, "_client_only", False)
    monkeypatch.setattr(wcl, "saved_login", lambda: None)
    monkeypatch.setattr("wipebot.discord.config.check_settings", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "wrong")
    monkeypatch.setattr("wipebot.discord.store.Store", lambda: Store(":memory:"))
    monkeypatch.setattr(server, "configure", lambda: None)
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(WipeBot, "run", turned_down)
    with pytest.raises(SystemExit, match="^Discord turned down DISCORD_TOKEN"):
        cli.run()


def test_reads_everyones_messages_only_when_the_host_opts_in(store):
    assert not WipeBot(store, cast(server.Library, None)).intents.message_content
    assert WipeBot(store, cast(server.Library, None), channel_context=True).intents.message_content


async def test_a_stop_waits_for_answers_in_progress(bot, monkeypatch):
    closed = []

    async def close():
        closed.append(True)
    monkeypatch.setattr(bot, "close", close)
    release = asyncio.Event()

    @answering
    async def answer(self):
        await release.wait()
    writing = asyncio.create_task(answer(bot))
    await asyncio.sleep(0)
    drain = asyncio.create_task(bot._drain())
    await asyncio.sleep(0.01)
    assert not closed  # still writing the answer
    release.set()
    await writing
    await drain
    assert closed


async def test_with_nothing_live_questions_are_about_the_guilds_latest_raid(bot, settings, follower, monkeypatch):
    follower.code = None
    asked_for = []

    async def guild_reports(guild_id, since_ms, pulls=False):
        asked_for.append(guild_id)
        return [{"code": "RAID", "startTime": 2, "pulls": 9}]

    async def latest_raid(library, reports):
        return reports[0]["code"]
    bot.library.wcl = SimpleNamespace(guild_reports=guild_reports)
    monkeypatch.setattr("wipebot.core.library.latest_raid", latest_raid)
    assert await bot.current_report(settings) == "RAID"
    assert await bot.current_report(settings) == "RAID" and asked_for == [14169]  # looked up once in a while
    follower.code = "LIVE1"
    assert await bot.current_report(settings) == "LIVE1"


async def test_asked_in_a_channel_to_answer_in_a_dm_it_answers_there_as_if_asked_there(bot, message, asked):
    """answer_in_dm: the question goes to the asker's own DM conversation, where players may be named; from a DM it
    declines, since that's where it is."""
    msg, dm = message("<@99> send me who missed kicks in a DM"), FakeDM()
    msg.author.create_dm = lambda: asyncio.sleep(0, dm)
    await bot.on_message(msg)
    told = await asked[0][3].tools["answer_in_dm"]({"question": "who missed their kicks on Twin Fangs tonight?"})
    assert "DM gets the answer" in told
    for _ in range(5):  # the DM's answer runs on its own
        await asyncio.sleep(0)
    key, _, question, ctx = asked[1]
    assert (key, question, ctx.where) == (("dm", 7), "who missed their kicks on Twin Fangs tonight?", "in a DM")
    assert dm.sent == ["**Wiped.**"] and msg.replies == ["**Wiped.**"]  # the channel's own answer, and the DM's
    assert await ctx.tools["answer_in_dm"]({"question": "x"}) == "This is their DM already: answer here."


class Sent:
    """A message the bot sent, that keeps its edits and whether it was deleted."""

    def __init__(self, content, **kwargs):
        self.contents, self.kwargs, self.deleted = [content], kwargs, False

    async def edit(self, content):
        self.contents.append(content)

    async def delete(self):
        self.deleted = True


async def test_a_long_answer_shows_what_it_looks_at_until_it_is_posted(bot, message, asked, monkeypatch):
    """A progress line under the question, edited as tools are called and return and deleted after the answer, which
    comes as its own reply so the asker is notified of it. It names the boss and pull, never a player."""
    monkeypatch.setattr("wipebot.discord.progress.PROGRESS_AFTER_S", 0)
    monkeypatch.setattr("wipebot.discord.progress.PROGRESS_EDIT_S", 0)
    bot.icons["wipebot_spinner"] = "<a:wipebot_spinner:5>"
    msg = message("<@99> how did Torvek do?")
    since = int(msg.created_at.timestamp())
    sent: list[Sent] = []

    async def reply(content=None, **kwargs):
        if kwargs.get("silent"):
            sent.append(Sent(content, **kwargs))
            return sent[-1]
        msg.replies.append(shown(content, kwargs.get("view")))
        return SimpleNamespace(id=200 + len(msg.replies))
    msg.reply = reply

    async def answering(key, code, question, ctx, *args, **kwargs):
        player = {"player": "Torvek", "boss": "Coiled Altar", "pull": 7}
        ctx.on_tool("Skill", {})
        ctx.on_tool("player_pull", player)
        await asyncio.sleep(0.01)
        ctx.on_tool("death_recap", player)
        ctx.on_done("player_pull", {**player, "report": "x"})
        await asyncio.sleep(0.01)
        ctx.on_done("death_recap", player)
        await asyncio.sleep(0.01)
        assert sent and not sent[0].deleted  # shown while it works
        return Answer("**Torvek did fine.**")
    monkeypatch.setattr(bot.conversations, "ask", answering)
    await bot.on_message(msg)
    assert msg.replies == ["**Torvek did fine.**"] and len(sent) == 1
    line = sent[0]
    assert line.contents[0] == (f"-# <a:wipebot_spinner:5> Reading a player's pull (Coiled Altar, pull 7) · "
                                f"started <t:{since}:R>")
    assert line.contents[1] == ("-# ✓ Player pull\n-# <a:wipebot_spinner:5> Reading death recaps (Coiled Altar, "
                                f"pull 7) · started <t:{since}:R>")
    assert line.contents[-1] == (f"-# ✓ Player pull · ✓ Death recap\n"
                                 f"-# <a:wipebot_spinner:5> Thinking · started <t:{since}:R>")
    assert not any("Torvek" in c for c in line.contents)
    assert line.kwargs == {"silent": True, "mention_author": False} and line.deleted


async def test_a_quick_answer_shows_no_progress_line(bot, message, monkeypatch):
    msg = message()

    async def answering(key, code, question, ctx, *args, **kwargs):
        ctx.on_tool("pull_summary", {})
        return Answer("**Wiped.**")
    monkeypatch.setattr(bot.conversations, "ask", answering)
    await bot.on_message(msg)
    assert msg.replies == ["**Wiped.**"]



async def test_a_server_can_keep_the_steps_under_its_answers(bot, message, monkeypatch):
    """By default the answer ends with the tools it went through, each once, failed ones marked; /wipebot setup
    steps: turns it off."""
    calls = [ToolCall("Skill", {}, "", False), ToolCall("raid_note", {}, "", False), ToolCall("roster", {}, "", False),
             ToolCall("compare_with_top", {"spec": "Restoration"}, "", False),
             ToolCall("compare_with_top", {"spec": "Holy"}, "", False), ToolCall("spell_info", {}, "", True)]

    async def answering(key, code, question, ctx, *args, **kwargs):
        return Answer("**Cast Riptide more.**", calls=calls)
    monkeypatch.setattr(bot.conversations, "ask", answering)
    await bot.on_message(msg := message())
    assert msg.replies == ["**Cast Riptide more.**\n-# ✓ Raid note · ✓ Roster · ✓ Compare with top players ×2 · "
                           "✗ Spell info"]
    bot.store.set_steps(msg.guild.id, False)
    await bot.on_message(msg := message())
    assert msg.replies == ["**Cast Riptide more.**"]
    assert progress.steps([("Skill", False)]) == ""

# ---------- messages a question links to ----------


class Perms(SimpleNamespace):
    view_channel = read_message_history = True
    manage_threads = False


class Linked:
    """A channel with its messages, readable by everyone but the members in `closed_to`."""

    def __init__(self, channel_id, name, messages=(), closed_to=()):
        self.id, self.name, self.messages, self.closed_to = channel_id, name, list(messages), set(closed_to)
        for m in self.messages:
            m.channel = self

    def permissions_for(self, member):
        return Perms(view_channel=member.id not in self.closed_to)

    async def fetch_message(self, message_id):
        if found := [m for m in self.messages if m.id == message_id]:
            return found[0]
        raise discord.NotFound(SimpleNamespace(status=404, reason=""), "")

    async def history(self, limit=100, oldest_first=False):
        for m in (self.messages if oldest_first else self.messages[::-1])[:limit]:
            yield m


class LinkedText(Linked, discord.TextChannel):  # ty: ignore[invalid-method-override]
    pass


class LinkedThread(Linked, discord.Thread):  # ty: ignore[invalid-method-override]
    """A thread in `parent`; a private one keeps its members."""

    def __init__(self, channel_id, name, parent, messages=(), private_to=None):
        super().__init__(channel_id, name, messages, parent.closed_to)
        self._parent, self.parent_id, self.private_to = parent, parent.id, private_to

    @property
    def parent(self):
        return self._parent

    @property
    def type(self):
        return discord.ChannelType.public_thread if self.private_to is None else discord.ChannelType.private_thread

    async def fetch_member(self, user_id):
        if user_id not in (self.private_to or ()):
            raise discord.NotFound(SimpleNamespace(status=404, reason=""), "")


def note(message_id, author, text, thread=None, attachments=()):
    return SimpleNamespace(id=message_id, author=author, content=text, thread=thread, attachments=list(attachments),
                           flags=SimpleNamespace(has_thread=False),
                           created_at=datetime.datetime(2026, 10, 21, 19, 0, tzinfo=datetime.UTC))


@pytest.fixture
def linked(member):
    """Server 1001 with #tactics (30): a note on Coiled Altar starting a thread, and a message after it; #officers
    (40), closed to Rowan (7); the bot is 99."""
    rowan, brann = member(7, "Rowan"), member(8, "Brann")
    tactics = LinkedText(30, "tactics", [note(31, brann, "Coiled Altar: group 2 soaks left"),
                                          note(32, brann, "Kyveza next week", attachments=["map.png"])])
    thread = LinkedThread(31, "Coiled Altar", tactics, [note(50, brann, "tanks swap at 3 stacks")])
    tactics.messages[0].thread = thread
    officers = LinkedText(40, "officers", [note(41, brann, "bench Rowan")], closed_to={7})
    channels = {c.id: c for c in (tactics, thread, officers)}

    async def fetch_channel(channel_id):
        raise discord.NotFound(SimpleNamespace(status=404, reason=""), "")
    guild = SimpleNamespace(id=1001, me=member(99, "Wipebot"), get_channel_or_thread=channels.get,
                            fetch_channel=fetch_channel, get_member={7: rowan}.get)
    return SimpleNamespace(guild=guild, rowan=rowan, tactics=tactics, thread=thread, officers=officers)


LINK = "https://discord.com/channels/1001"


async def test_a_linked_message_comes_with_the_thread_it_starts(linked):
    text = await wipebot.linked_messages(f"how do we do this boss? {LINK}/30/31", linked.guild, linked.rowan)
    assert text.splitlines() == [f"{LINK}/30/31 (#tactics):", "Brann (Wed 19:00 UTC): Coiled Altar: group 2 soaks left",
                                 "Brann (Wed 19:00 UTC): tanks swap at 3 stacks"]


async def test_a_linked_thread_or_channel_gives_its_messages_oldest_first(linked):
    thread = await wipebot.linked_messages(f"{LINK}/31", linked.guild, linked.rowan)
    assert thread.splitlines()[0] == f"{LINK}/31 (thread \"Coiled Altar\" in #tactics):"
    channel = await wipebot.linked_messages(f"<{LINK}/30>", linked.guild, linked.rowan)
    assert channel.splitlines()[1:] == [
        "Brann (Wed 19:00 UTC): Coiled Altar: group 2 soaks left",
        "Brann (Wed 19:00 UTC): Kyveza next week [1 attachment(s) or image(s), not read]"]


async def test_a_link_shows_only_what_the_asker_may_read_themselves(linked):
    text = await wipebot.linked_messages(f"{LINK}/40/41 and https://discord.com/channels/2002/30/31", linked.guild,
                                         linked.rowan)
    assert text.splitlines() == [f"{LINK}/40/41: not read, not found, or the asker can't read it", "",
                                 "https://discord.com/channels/2002/30/31: not read, it's in another server"]
    assert "bench" not in text
    linked.thread.private_to = {8}
    assert (await wipebot.linked_messages(f"{LINK}/31", linked.guild, linked.rowan)).endswith("can't read it")
    linked.tactics.closed_to = {99}  # nor what the bot may not
    assert (await wipebot.linked_messages(f"{LINK}/30/32", linked.guild, linked.rowan)).endswith(
        "I'm not allowed to read that channel")


async def test_a_link_isnt_read_without_the_message_content_intent_or_to_a_gone_message(linked):
    assert (await wipebot.linked_messages(f"{LINK}/30/31", linked.guild, linked.rowan, readable=False)).endswith(
        "not read, I'm not set up to read messages that don't mention me")
    assert (await wipebot.linked_messages(f"{LINK}/30/39", linked.guild, linked.rowan)).endswith(
        "not read, that message is gone")


async def test_a_question_linking_messages_comes_with_them(bot, message, asked, linked, monkeypatch):
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: linked.guild)
    await bot.on_message(message(f"<@99> what's our plan? {LINK}/30/31"))
    bot._connection._intents.message_content = True  # as with WIPEBOT_CHANNEL_CONTEXT=1
    await bot.on_message(message(f"<@99> what's our plan? {LINK}/30/31"))
    [without, with_intent] = [ctx.linked for _, _, _, ctx in asked]
    assert without.endswith("not read, I'm not set up to read messages that don't mention me")
    assert "group 2 soaks left" in with_intent


async def test_a_dm_can_link_the_servers_messages_that_the_asker_may_read(bot, message, asked, linked, monkeypatch):
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: linked.guild)
    bot._connection._intents.message_content = True
    await bot.on_message(message(f"what's our plan? {LINK}/31 and {LINK}/40/41", dm=True))
    [(key, _, _, ctx)] = asked
    assert key == ("dm", 7) and "tanks swap at 3 stacks" in ctx.linked
    assert ctx.linked.endswith(f"{LINK}/40/41: not read, not found, or the asker can't read it")
