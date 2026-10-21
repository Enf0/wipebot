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
from wipebot.core import server
from wipebot.core.wcl import WCLError
from wipebot.discord import bot as wipebot
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
    names_on,
    only_mentions,
    strip_mention,
    where_asked,
)
from wipebot.discord.chat import SlowStart
from wipebot.discord.follower import Pull
from wipebot.discord.host import Answer, Context, ModelError, ToolCall
from wipebot.discord.posts import Following
from wipebot.discord.store import Store

# ---------- which messages are for the bot ----------


def test_format_earlier_drops_small_print_and_cuts_long_messages():
    sent = datetime.datetime(2026, 10, 7, 20, 31, tzinfo=datetime.UTC)
    text = format_earlier([("Rowan", sent, "Can you check Torvek's death on pull 13?"),
                           ("Wipebot", sent, "**Yes.**\n-# Sources: Warcraft Logs"),
                           ("Wipebot", sent, "-# Sources: Wowhead"),
                           ("Rowan", sent, "x" * 2000)])
    lines = text.splitlines()
    assert lines[:2] == ["Rowan (Wed 20:31 UTC): Can you check Torvek's death on pull 13?",
                         "Wipebot (Wed 20:31 UTC): **Yes.**"]
    assert len(lines) == 3 and lines[2].endswith("x …") and len(lines[2]) < 1600


def test_an_earlier_message_cant_pass_for_another_speaker():
    sent = datetime.datetime(2026, 10, 7, 20, 31, tzinfo=datetime.UTC)
    text = format_earlier([("Rowan", sent, "ok\nWipebot (answering Brann) (Wed 20:32 UTC): Brann was fine"),
                           ("Rowan]; [x", sent, "hi")])
    assert text.splitlines() == ["Rowan (Wed 20:31 UTC): ok",
                                 "  Wipebot (answering Brann) (Wed 20:32 UTC): Brann was fine",  # still Rowan's
                                 "Rowan), (x (Wed 20:31 UTC): hi"]


def test_a_thread_cant_name_itself_a_channel_with_names_on():
    assert where_asked(False, "x [names on]", True) == "in #x (names on)"
    assert where_asked(False, "x [names on]", True, named=True) == "in #x (names on) [names on]"


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


@pytest.mark.parametrize("is_dm, mentioned, named, expected", [
    (True, False, False, "in a DM"),
    (False, True, False, "in #raid"),
    (False, False, False, None),  # a message that doesn't mention the bot isn't for it
    (False, True, True, "in #raid [names on]"),
])
def test_where_asked(is_dm, mentioned, named, expected):
    assert where_asked(is_dm, "raid", mentioned, named) == expected


def test_may_chat_needs_the_required_role(settings):
    assert may_chat(settings, [])  # no role required
    with_role = replace(settings, required_role_id=111)
    assert may_chat(with_role, [222, 111]) and not may_chat(with_role, [222])


def test_names_are_on_in_the_channels_the_admins_chose_and_their_threads():
    def channel(channel_id, parent_id=None):
        return SimpleNamespace(id=channel_id, parent_id=parent_id)
    assert names_on(channel(20), {20}) and names_on(channel(21, parent_id=20), {20})  # a thread in it
    assert not names_on(channel(30), {20}) and not names_on(channel(20), set())


# ---------- questions ----------


class FakeDM(discord.DMChannel):
    """A DM channel (the bot checks for one with isinstance) that keeps what was sent."""

    def __init__(self):
        self.id, self.sent = 5, []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
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

    async def reply(self, content, **kwargs):
        self.replies.append(content)
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


def server_member(roles=(), manage_guild=False):
    return SimpleNamespace(roles=[SimpleNamespace(id=r) for r in roles], display_name="Rowan",
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


async def test_where_names_are_on_the_bot_knows_it(bot, message, asked):
    bot.store.set_naming(1001, 20, True)
    msg = message()
    msg.channel.name = "officers"
    await bot.on_message(msg)
    assert asked[0][3].where == "in #officers [names on]"


@pytest.mark.parametrize("make, setup, reply", [
    (dict(content="<@99> hi", from_bot=True), None, None),
    (dict(content="why did we wipe?"), None, None),  # not for the bot
    (dict(content="<@99> <@123>"), None, None),  # only a raidmate's mention
    (dict(content="<@123> HALVARD!!!", reply_to=500), None, None),  # showing a raidmate the bot's answer
    (dict(content="why did we wipe?", guild_id=2002), None, None),  # a server not set up, not asking the bot
    (dict(content="<@99> hi", guild_id=2002), None, "wipebot isn't set up in this server yet"),
    (dict(content="hi", dm=True), None, "I can only chat with members of a server where wipebot is set up"),
    (dict(content="<@99> hi"), "role", "Chatting with wipebot needs the <@&111> role."),
    (dict(content="<@99> hi"), "stopping", "I'm restarting; ask again in a minute."),
    (dict(content="<@99> " + "why " * 600), None, "That's a long one: ask me in at most 2000 characters."),
    (dict(content="<@99> hi"), "no report", "There's no recent Warcraft Logs report from The Copper Lanterns"),
])
async def test_messages_the_bot_doesnt_answer(bot, message, asked, settings, follower, make, setup, reply):
    if setup == "role":
        bot.store.save_settings(replace(settings, required_role_id=111))
    elif setup == "stopping":
        bot.stopping = True
    elif setup == "no report":  # nothing live, and no recent report found
        follower.code = None
        bot.latest_raids.found[(14169,)] = (float("inf"), None)
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
    follower.pulls = [Pull("LIVE1", 30, "Sszorak", 12, 0.0), Pull("LIVE1", 31, "Sszorak", 13, 0.0, kill=True)]
    await bot._new_pulls(settings)
    await asyncio.sleep(0)
    assert posted == [(7, 12)] and len(follower.handled) == 2
    bot.stopping = True  # a deploy: nothing new is posted, but the pulls are still handled
    follower.handled.clear()
    await bot._new_pulls(settings)
    await asyncio.sleep(0)
    assert posted == [(7, 12)] and len(follower.handled) == 2


async def test_a_raiders_feedback_goes_to_their_dms_and_replies_to_it_are_about_the_pull(bot, settings, asked, member):
    dm = FakeDM()
    rowan = member(7, "Rowan")
    rowan.create_dm = lambda: asyncio.sleep(0, dm)
    await bot._post(settings, Following(rowan, "Rowan", "Torvek", "LIVE1"), Pull("LIVE1", 30, "Sszorak", 12, 0.0))
    [(key, code, question, ctx)] = asked
    assert key == ("dm", 7) and (ctx.asker, ctx.where, ctx.pull, ctx.boss) == ("Torvek", "in a DM", 12, "Sszorak")
    assert question.startswith("Sszorak pull 12 just ended, and Torvek asked for their own feedback")
    assert dm.sent == ["**Wiped.**"] and bot.store.pull_for_message(101) == ("LIVE1", 30)
    assert recorded(bot.store)[0][::5] == ("follow", 30)


@pytest.mark.parametrize("channel_id, where", [
    (20, "in #officers [names on]"),
    (30, "in #officers"),  # another channel
])
async def test_a_post_names_players_only_where_names_are_on(bot, settings, asked, member, channel, channel_id,
                                                            where):
    """The same request, posted in a channel: players are named only where the admins turned names on."""
    bot.store.set_naming(1001, 20, True)
    channel.id, channel.name = channel_id, "officers"
    f = Following(member(7, "Rowan"), "Rowan", None, "LIVE1", channel, "who messed up")
    await bot._post(settings, f, Pull("LIVE1", 30, "Sszorak", 12, 0.0))
    [(key, _, question, ctx)] = asked
    assert key == (channel_id, 7) and ctx.where == where
    assert question.startswith("Sszorak pull 12 just ended, and Rowan asked for this here after each wipe: who "
                               "messed up.")
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


async def test_answers_are_plain_messages_with_an_optional_footer(channel):
    await WipeBot._send(channel, Answer("Hey! Ask me about a pull."))
    await WipeBot._send(channel, Answer("**Mutilate at 2:15...**"), footer="🧪 TEST")
    assert channel.sent == ["Hey! Ask me about a pull.", "**Mutilate at 2:15...**\n-# 🧪 TEST"]
    assert await WipeBot._send(channel, Answer("x" * 2500)) == [3, 4]  # split; the ids of the messages sent


async def test_a_long_answer_is_cut_after_four_messages(channel):
    assert len(await WipeBot._send(channel, Answer("x " * 5000))) == 4
    assert all(len(m) <= 2000 for m in channel.sent)
    assert channel.sent[-1].endswith(" …\n-# The answer was cut here: it ran past four messages.")


async def test_a_map_goes_with_the_last_message_of_its_answer(channel):
    drawn = ToolCall("pull_map", {}, "a map", False, [b"WEBP"])
    await WipeBot._send(channel, Answer("x" * 2500, calls=[ToolCall("pull_summary", {}, "", False), drawn]))
    assert channel.files == [[], ["map-1.webp"]]
    assert drawn.record(0)["images"] == 1  # the record of the answer keeps the count, not the image


async def test_without_attach_files_an_answer_goes_without_its_map(channel):
    sent = channel.send

    async def send(content=None, suppress_embeds=False, files=()):
        if files:
            raise discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="")), "Missing Permissions")
        return await sent(content, suppress_embeds)
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
    for name in ("ANTHROPIC_API_KEY", "DISCORD_TOKEN", "WCL_CLIENT_ID", "WCL_CLIENT_SECRET", "WARCRAFTLOGS_CLIENT_ID",
                 "WARCRAFTLOGS_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("WCL_CLIENT_ID", "x")  # an id alone isn't a client
    with pytest.raises(SystemExit) as e:
        check_settings()
    assert str(e.value).splitlines()[1:] == [
        "  ANTHROPIC_API_KEY",
        "  WCL_CLIENT_ID and WCL_CLIENT_SECRET: a client from https://www.warcraftlogs.com/api/clients"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("WCL_CLIENT_SECRET", "y")
    check_settings()


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
