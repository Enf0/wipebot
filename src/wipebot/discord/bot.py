"""Discord client for any number of servers, each set up with /wipebot setup.

Per server: answers to members who @mention it (in any channel it can see) or DM it, and, for whoever asks, a
post after each wipe of the guild's live report where they asked (posts.py). It names players in DMs, and in the
channels where the server's admins turned names on (/wipebot names).
"""
import asyncio
import datetime
import functools
import io
import logging
import re
import signal
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import cast

import discord
from discord import app_commands
from discord.ext import tasks

from wipebot.core import raidstrats, server
from wipebot.core.library import LatestRaids
from wipebot.core.report import FactError
from wipebot.core.wcl import WCLError

from . import commands
from .chat import DISCORD_LIMIT, Conversations, SlowStart, split_message
from .follower import Follower, Pull
from .host import CAPPED, Answer, Context, ModelError, Session
from .posts import Following, Posts
from .prompt import plain, post_question
from .store import INTERACTION_DAYS, ServerSettings, Store

log = logging.getLogger(__name__)

POLL_S = 20
CHAT_TIMEOUT_S = 360  # a chat answer gives up after this long (whole-night questions take a few minutes)
POST_TIMEOUT_S = 120  # a post after a wipe gives up after this long (the next pull is coming)
# On SIGTERM (a deploy's restart) the bot finishes the answers it's writing, up to this long, before it stops;
# compose.yml's stop_grace_period gives it a little more.
DRAIN_S = CHAT_TIMEOUT_S + 30
# With nothing live, questions are about the guild's latest raid night, looked up again after this many seconds
RECENT_REPORT_CHECK_S = 300
EARLIER_MESSAGES = 15  # a question comes with up to this many messages its conversation hasn't seen,
EARLIER_HOURS = 12  # for a new conversation from this many hours back,
EARLIER_CHARS = 1500  # each cut to this many characters
EARLIER_SCANNED = 100  # messages looked through for them
QUESTION_CHARS = 2000  # a longer question isn't answered (Discord's limit for a message, without Nitro)
# What a raider is told when there's no answer: Claude's API refused the question (host.ModelError's kind), it took
# too long, or something else went wrong.
MODEL_ERRORS = {
    "billing_error": "I'm out of credit for answering this month, so I can't answer for now. An officer can tell "
                     "you when I'm back.",
    "rate_limit": "Too many questions at once; try again in a minute.",
    "other": "I can't reach Claude right now; try again in a few minutes.",
    **dict.fromkeys(CAPPED, "That needed more digging than I do for one answer, sorry. Try asking something "
                            "narrower."),
}
TOO_SLOW = "That took too long, sorry. Try asking something narrower."
SLOW_START = "Loading the report took too long; try again in a minute."
WENT_WRONG = "Something went wrong while answering that; try again in a moment."
WCL_DOWN = "Warcraft Logs isn't answering, so I can't look at the logs; try again in a few minutes."
MENTION = re.compile(r"<@[!&]?\d+>|<#\d+>")
# Replies need Read Message History; threads, for when someone mentions the bot inside one; maps, Attach Files.
INVITE_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True,
                                         send_messages_in_threads=True, attach_files=True)
MAX_MESSAGES = 4  # an answer is cut after this many Discord messages
CUT = "-# The answer was cut here: it ran past four messages."


def strip_mention(text: str, bot_id: int) -> str:
    """The question without the @mention of the bot."""
    return re.sub(rf"<@!?{bot_id}>", "", text).strip()


def only_mentions(text: str) -> bool:
    """Whether a message is nothing but mentions of others (a raider pinging a raidmate in a reply to the bot)."""
    return not MENTION.sub("", text).strip()


def for_someone_else(text: str, bot_id: int) -> bool:
    """Whether a message tags someone but not the bot and asks nothing: in a reply to the bot, a raider showing
    its answer to a raidmate ("@Bob BOB!!!"). A question mark ("why did @Bob die?") or tagging the bot too
    makes it a question for the bot."""
    return (bool(re.search(r"<@[!&]?\d+>", text)) and not re.search(rf"<@!?{bot_id}>", text)
            and "?" not in MENTION.sub("", text))


NAMES_ON = " [names on]"  # the context line's mark of a channel where the admins turned names on


def where_asked(is_dm: bool, channel_name: str, mentioned: bool, named: bool = False) -> str | None:
    """Where a message was asked, for the context line, or None when it isn't for the bot: the bot answers DMs
    and messages that mention it or reply to it (`mentioned`). `named`: in a channel with names on."""
    if is_dm:
        return "in a DM"
    if mentioned:
        return in_channel(channel_name, named)
    return None


def in_channel(channel_name: str, named: bool) -> str:
    """Where, for the context line: a thread's name is anyone's to write, and plain() takes the marker's
    brackets out of it, so no name can say names are on."""
    return f"in #{plain(channel_name)}" + (NAMES_ON if named else "")


def names_on(channel: object, channels: set[int]) -> bool:
    """Whether a channel, or the channel a thread is in, is one where the admins turned names on (`channels`)."""
    return bool(channels & {getattr(channel, "id", None), getattr(channel, "parent_id", None)})


def format_earlier(messages: list[tuple[str, datetime.datetime, str]]) -> str:
    """Earlier messages, oldest first, as (author, sent, text) lines; the bot's small print (`-#` lines) dropped.
    A message's further lines are indented, so only a message's first line starts with who wrote it."""
    lines = []
    for author, sent, text in messages:
        text = "\n".join(line for line in text.splitlines() if not line.startswith("-# ")).strip()
        if len(text) > EARLIER_CHARS:
            text = text[:EARLIER_CHARS].rstrip() + " …"
        if text:
            lines.append(f"{plain(author)} ({sent:%a %H:%M} UTC): " + text.replace("\n", "\n  "))
    return "\n".join(lines)


async def earlier_messages(message: discord.Message, bot_user: discord.abc.User, after: int | None = None,
                           seen: set[int] | None = None) -> str:
    """What was said before `message` in its place that its conversation hasn't seen (`seen`): since the
    conversation's previous question `after`, or else from the last EARLIER_HOURS. Everyone's messages, the bot's
    answers to other people included, so one person's correction reaches the next answer."""
    start = discord.Object(id=after) if after else message.created_at - datetime.timedelta(hours=EARLIER_HOURS)
    found = []
    async for m in message.channel.history(limit=EARLIER_SCANNED, before=message, after=start, oldest_first=False):
        if m.id in (seen or ()):
            continue
        from_bot = m.author.id == bot_user.id
        text = strip_mention(m.content, bot_user.id)
        if not text:
            continue
        author = bot_user.display_name if from_bot else display(m.author)  # with a username: no one is the bot
        replied = m.reference.resolved if m.reference else None
        if from_bot and isinstance(replied, discord.Message):
            author += f" (answering {display(replied.author)})"
        found.append((author, m.created_at, text))
        if len(found) == EARLIER_MESSAGES:
            break
    return format_earlier(found[::-1])


async def respond(message: discord.Message, text: str) -> None:
    """A short notice: a reply in a server channel, where it says who it's for; just a message in a DM."""
    if isinstance(message.channel, discord.DMChannel):
        await message.channel.send(text)
    else:
        await message.reply(text)


async def pasted_note(message: discord.Message) -> str | None:
    """A raid note or raid plan in a message to the bot: its text, or a text file (Discord turns a long paste into
    message.txt)."""
    if commands.looks_like_note(message.content):
        return message.content
    for a in message.attachments:
        if a.size <= commands.MAX_NOTE_BYTES and (a.filename.endswith(".txt")
                                                   or (a.content_type or "").startswith("text/")):
            text = (await a.read()).decode("utf-8", errors="replace")
            if commands.looks_like_note(text):
                return text
    return None


def log_tool(name: str, args: dict) -> None:
    """Log each tool call as it starts, so the console shows what an answer is working on."""
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "report")
    log.info("  · %s(%s)", name, shown[:200])


def preview(text: str, chars: int = 120) -> str:
    """The start of an answer on one line, for the log."""
    line = " ".join(text.split())
    return line if len(line) <= chars else line[:chars].rstrip() + " …"


def may_chat(settings: ServerSettings, role_ids: list[int]) -> bool:
    """Whether a member with these roles may chat in a server with these settings."""
    return settings.required_role_id is None or settings.required_role_id in role_ids


def is_officer(settings: ServerSettings, member: discord.Member) -> bool:
    """Whether a member is an officer, who saves the raid note or plan and changes or stops anyone's posts after
    wipes in a channel: one with the officer role (/wipebot setup), or without one set, who can manage the server."""
    if settings.officer_role_id is None:
        return member.guild_permissions.manage_guild
    return settings.officer_role_id in [r.id for r in member.roles]


def officers(settings: ServerSettings) -> str:
    """Who the officers are, for a member who isn't one."""
    return (f"members with the <@&{settings.officer_role_id}> role" if settings.officer_role_id
            else "members who can manage the server")


def display(user: discord.abc.User | discord.Member) -> str:
    """A Discord user as the model and the record name them: "Display (username)", or the one name when they're the
    same."""
    return plain(user.display_name if user.display_name == user.name else f"{user.display_name} ({user.name})")


def answering(method):
    """A WipeBot method that writes and posts an answer: a stop waits for it to finish."""
    @functools.wraps(method)
    async def wrapper(self: WipeBot, *args, **kwargs):
        self._answering += 1
        self._idle.clear()
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._answering -= 1
            if not self._answering:
                self._idle.set()
    return wrapper


class WipeBot(discord.Client):
    def __init__(self, store: Store, library: server.Library, model: str = "",
                 test_report: str | None = None, test_server: int | None = None,
                 try_question: str | None = None, try_channel: int | None = None, channel_context: bool = False,
                 record_answers: bool = False):
        # Without the Message Content intent Discord gives the text of DMs, of messages that mention the bot and of
        # replies to it that ping it: earlier messages then hold only the bot's answers and the questions to it.
        # With it (`channel_context`, also switched on in the developer portal): everyone's messages before a
        # question, and replies to the bot that don't ping it.
        intents = discord.Intents.default()
        intents.message_content = channel_context
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.store, self.library, self.model = store, library, model
        # Keep each question and answer (Store.interactions) and print them in the log: off unless the host asks.
        self.record_answers = record_answers
        # `wipebot try`: answer once in `try_channel` and exit, never follow or replay a report
        self.trying = try_question is not None
        self.try_channel = try_channel
        # `wipebot replay` replays an old report into one server as if live: feedback after each of its pulls goes to
        # whoever asks for it there, and its pulls aren't recorded as handled.
        self.test_report, self.test_server, self.try_question = test_report, test_server, try_question
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(commands.build(self))
        self.followers: dict[int, Follower] = {}  # per server: its live report
        self.posts = Posts(store, library, record_answers)  # who asked for posts after wipes
        # After Claude's API refused a question or Warcraft Logs didn't answer, the conversation goes on.
        self.conversations = Conversations(self._open_session, survives=(ModelError, WCLError))
        self.latest_raids = LatestRaids(library, RECENT_REPORT_CHECK_S)
        self._started = False
        self.stopping = False  # after SIGTERM: no new questions or posts, finish the answers in progress
        self._answering = 0
        self._idle = asyncio.Event()
        self._idle.set()

    # ---------- starting and stopping ----------

    async def setup_hook(self) -> None:
        if not self.trying:
            self.poll.start()
            self.keep_logins.start()
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self._on_sigterm)
        except NotImplementedError:  # Windows: SIGTERM stops the bot at once, as before
            pass

    def _on_sigterm(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.poll.cancel()  # unhandled pulls stay unhandled: the next start picks them up
        self.keep_logins.cancel()
        log.info("stopping: finishing %d answers in progress first (up to %d s)", self._answering, DRAIN_S)
        self._drain_task = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        try:
            await asyncio.wait_for(self._idle.wait(), DRAIN_S)
        except TimeoutError:
            log.warning("stopping with %d answers still in progress after %d s", self._answering, DRAIN_S)
        await self.close()

    async def on_ready(self) -> None:
        if self._started:  # on_ready comes again after a reconnect
            return
        self._started = True
        if pruned := self.store.prune_interactions():
            log.info("forgot %d answers older than %d days", pruned, INTERACTION_DAYS)
        if self.trying:
            try:
                await self._try(self.try_question or "")
            finally:
                await self.close()
            return
        for guild in self.guilds:
            await self._sync_commands(guild)
        invite = discord.utils.oauth_url(self.application_id or 0, permissions=INVITE_PERMISSIONS,
                                         scopes=("bot", "applications.commands"))
        log.info("logged in as %s in %d servers (%d set up). Invite link: %s", self.user, len(self.guilds),
                 len(self.store.all_settings()), invite)
        await self._restore_posts()

    async def _restore_posts(self) -> None:
        """The posts after wipes asked for before a restart, so a deploy during a raid doesn't end them. One whose
        user or channel can't be found any more is dropped; one for a report that has ended is dropped at the
        next poll, as before the restart."""
        for saved in self.store.all_following():
            try:
                user = self.get_user(saved.user_id) or await self.fetch_user(saved.user_id)
                channel = None
                if saved.channel_id is not None:
                    channel = self.get_channel(saved.channel_id) or await self.fetch_channel(saved.channel_id)
            except discord.HTTPException:
                log.info("dropped posts after wipes for %s: their user or channel is gone", self._shown(saved.name))
                self.posts.end(saved.discord_guild_id, saved.key)
                continue
            self.posts.add(saved.discord_guild_id, Following(
                user, saved.name, saved.character, saved.code, cast(discord.abc.Messageable | None, channel),
                saved.request, saved.asked), save=False)
            log.info("posts after wipes for %s restored (report %s)", self._shown(saved.name),
                     saved.code or "not started")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._sync_commands(guild)

    async def _sync_commands(self, guild: discord.Guild) -> None:
        """Register the slash commands in one server; per server, they show up at once."""
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException:
            log.exception("registering commands in %s failed (was the bot invited with applications.commands?)",
                          guild)

    def _shown(self, name: str) -> str:
        """Who the log names: only with record_answers, as the record does."""
        return name if self.record_answers else "a raider"

    @property
    def name(self) -> str:
        """The bot's name in Discord, which it introduces itself by."""
        return self.user.display_name if self.user else "wipebot"

    async def close(self) -> None:
        await self.conversations.close_all()
        await super().close()

    def settings_changed(self, discord_guild_id: int) -> None:
        """After /wipebot setup: follow the (maybe new) guild from the next poll."""
        self.followers.pop(discord_guild_id, None)
        self.posts.clear(discord_guild_id)
        self.latest_raids.found.clear()

    # ---------- the live report: posts after wipes ----------

    def _follower(self, s: ServerSettings) -> Follower:
        if s.discord_guild_id not in self.followers:
            test = self.test_report if s.discord_guild_id == self.test_server else None
            store = Store(":memory:") if test else self.store  # a replay doesn't mark the real pulls handled
            self.followers[s.discord_guild_id] = Follower(self.library, store, s.wcl_guild_id, test)
        return self.followers[s.discord_guild_id]

    @tasks.loop(seconds=POLL_S)
    async def poll(self) -> None:
        # A failure is logged and retried next poll (an exception would end the loop for good); unhandled pulls stay
        # unhandled, so nothing is lost.
        try:
            servers = self.store.all_settings()
        except Exception:
            log.exception("reading the set-up servers failed")
            return
        if self.test_report:
            servers = [s for s in servers if s.discord_guild_id == self.test_server]
        for s in servers:
            try:
                await self._new_pulls(s)
            except Exception:
                log.exception("following the live report of server %s failed", s.discord_guild_id)

    @poll.before_loop
    async def _wait_ready(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=6)
    async def keep_logins(self) -> None:
        """The guilds' raidstrats.gg logins refreshed weekly, so one nobody used for 30 days doesn't lapse."""
        try:  # an exception would end the loop for good
            await raidstrats.keep_alive(self.store.notebook)
        except Exception:
            log.exception("refreshing the raidstrats.gg logins failed")

    async def _new_pulls(self, s: ServerSettings) -> None:
        """Every pull that finished since the last poll; after a wipe, the post each person asked for."""
        follower = self._follower(s)
        pulls = await follower.poll()
        due = await self.posts.due(s.discord_guild_id, follower.code, pulls)
        for pull in pulls:
            follower.done(pull)
        if not self.stopping:
            for f, pull in due:
                asyncio.create_task(self._post(s, f, pull))

    @answering
    async def _post(self, s: ServerSettings, f: Following, pull: Pull) -> None:
        """The post someone asked for after one wipe, where they asked for it (their DMs or a channel), in their
        conversation there. In a channel, players are named only where names are on."""
        if f.channel is None:
            key: Hashable = ("dm", f.user.id)
            where = "in a DM"
        else:
            key = (f.key, f.user.id)
            where = in_channel(getattr(f.channel, "name", ""),
                               names_on(f.channel, self.store.naming_channels(s.discord_guild_id)))
        question = post_question(pull.boss, pull.pull_no, f.name, f.character, f.request)
        ctx = self._context(s, f.user, f.name, pull.report, where, f.channel, asker=f.character, pull=pull.pull_no,
                            boss=pull.boss)

        async def send(answer: Answer) -> list[int]:
            sent = await self._send(f.channel or await f.user.create_dm(), answer)
            for message_id in sent:  # a reply to it is about this pull
                self.store.remember_message(message_id, pull.report, pull.fight)
            return sent
        log.info("post after %s pull %s for %s, %.0f s after it ended", pull.boss, pull.pull_no, self._shown(f.name),
                 time.time() - pull.ended_ms / 1000)
        await self._answer("follow", key, pull.report, question, ctx, f.name, send, fight=pull.fight,
                           channel_id=getattr(f.channel, "id", None))

    # ---------- questions ----------

    @answering
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.user:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        replied = message.reference.resolved if message.reference else None
        # A reply to the bot is for it too, with the ping turned off.
        mentioned = self.user in message.mentions or (
            isinstance(replied, discord.Message) and replied.author.id == self.user.id)
        settings = await self._settings_for(message, is_dm, mentioned)
        if settings is None:
            return
        named = not is_dm and names_on(message.channel, self.store.naming_channels(settings.discord_guild_id))
        where = where_asked(is_dm, getattr(message.channel, "name", ""), mentioned, named)
        if where is None:
            return
        if pasted := await pasted_note(message):
            await self._save_note(message, settings, pasted)
            return
        question = strip_mention(message.content, self.user.id)
        if only_mentions(question) or (not is_dm and for_someone_else(message.content, self.user.id)):
            return
        if len(question) > QUESTION_CHARS:
            await respond(message, f"That's a long one: ask me in at most {QUESTION_CHARS} characters.")
            return
        if refusal := await self._refusal(message, settings, is_dm):
            await respond(message, refusal)
            return
        try:
            code = await self.current_report(settings)
        except Exception:
            log.exception("finding the report to answer about failed")
            await respond(message, WCL_DOWN)
            return
        if not code:
            await respond(message, f"There's no recent Warcraft Logs report from {settings.wcl_guild} to look at.")
            return
        await self._chat(message, settings, code, question, where, is_dm)

    async def _settings_for(self, message: discord.Message, is_dm: bool, mentioned: bool) -> ServerSettings | None:
        """The settings of the server a message is for; None, after telling the asker why when it's for the bot,
        when there's none."""
        if is_dm:
            if (settings := await self._settings_for_dm(message.author)) is None:
                await respond(message, "I can only chat with members of a server where wipebot is set up "
                                       "(with the role it asks for, if any).")
            return settings
        settings = self.store.settings(message.guild.id) if message.guild else None
        if settings is None and mentioned:
            await respond(message, "wipebot isn't set up in this server yet: an admin can run `/wipebot setup`.")
        return settings

    async def _refusal(self, message: discord.Message, s: ServerSettings, is_dm: bool) -> str | None:
        """Why a question for the bot won't be answered, if it won't: the asker lacks the chat role, or the bot is
        restarting."""
        if not is_dm and not may_chat(s, [r.id for r in getattr(message.author, "roles", [])]):
            return f"Chatting with wipebot needs the <@&{s.required_role_id}> role."
        if self.stopping:
            return "I'm restarting; ask again in a minute."
        return None

    async def _chat(self, message: discord.Message, s: ServerSettings, code: str, question: str, where: str,
                    is_dm: bool) -> None:
        """Answer a question in its conversation: the asker's DMs, or the asker in that channel."""
        author = message.author
        # In a DM the author is the Discord account: the server's member has the nickname a raider names their
        # character in ("Jekå/Namsus"), as a channel's message does.
        guild = self.get_guild(s.discord_guild_id) if is_dm else None
        name = display((guild.get_member(author.id) if guild else None) or author)
        ctx = self._context(s, author, name, code, where, None if is_dm else message.channel)
        if message.reference and (posted := self.store.pull_for_message(message.reference.message_id)):
            try:
                await self._about_pull(ctx, code, *posted)
            except Exception:
                log.exception("loading report %s failed", code)
                await respond(message, WCL_DOWN)
                return
        key = ("dm", author.id) if is_dm else (message.channel.id, author.id)
        if self.record_answers:
            log.info("question from %s %s (report %s): %s", name, where, code, question)
        else:
            log.info("question %s (report %s)", where, code)

        async def send(answer: Answer) -> list[int]:
            return await self._send(message.channel, answer, reply_to=None if is_dm else message)
        async with message.channel.typing():
            failed = await self._answer("chat", key, code, question, ctx, name, send, message)
        if failed:
            await respond(message, failed)

    def _context(self, s: ServerSettings, user: discord.User | discord.Member, name: str, code: str, where: str,
                 channel: discord.abc.Messageable | None, asker: str | None = None, pull: int | None = None,
                 boss: str | None = None) -> Context:
        """A question's Context in server `s`, asked by `user` (`name`) in `channel` (None for their DMs) about
        report `code`: their remembered character and notes, the bot's own tools for them there, and the server's
        raid notes and plans."""
        raider = self.store.raider(user.id)

        async def follow_pulls(args: dict) -> str:
            follower = self.followers.get(s.discord_guild_id)
            live = follower.code if follower else None  # None: nothing logged yet tonight
            officer = channel is not None and is_officer(s, cast(discord.Member, user))  # a member, in a channel
            return await self.posts.follow_pulls(s.discord_guild_id, user, name, args, live, code, channel, officer)

        async def remember(args: dict) -> str:
            return await self._remember(user.id, name, code, args)

        async def answer_in_dm(args: dict) -> str:
            if channel is None:
                return "This is their DM already: answer here."
            if not (question := " ".join(str(args.get("question") or "").split())):
                return "Give the question to answer in their DM."
            asyncio.create_task(self._in_dm(s, user, name, code, question))
            return ("Their DM gets the answer now, as if they had asked it there. Say so here in a line, and don't "
                    "answer it here too.")
        return Context(asker=asker or raider.character, discord_name=name, where=where, pull=pull, boss=boss,
                       notes=raider.notes, guild=f"{s.wcl_guild}, {s.wcl_server}, {s.wcl_region}",
                       tools={"follow_pulls": follow_pulls, "remember": remember, "answer_in_dm": answer_in_dm},
                       **self._raid_notes(s))

    @answering
    async def _in_dm(self, s: ServerSettings, user: discord.User | discord.Member, name: str, code: str,
                     question: str) -> None:
        """A question asked in a channel to be answered in the asker's DMs (answer_in_dm): in their DM conversation,
        as if asked there."""
        dm = await user.create_dm()

        async def send(answer: Answer) -> list[int]:
            return await self._send(dm, answer)
        ctx = self._context(s, user, name, code, "in a DM", None)
        if failed := await self._answer("chat", ("dm", user.id), code, question, ctx, name, send):
            await dm.send(failed)

    async def _answer(self, kind: str, key: Hashable, code: str, question: str, ctx: Context, who: str,
                      send: Callable[[Answer], Awaitable[list[int]]], message: discord.Message | None = None,
                      fight: int | None = None, channel_id: int | None = None) -> str | None:
        """Ask in the conversation at `key`, post the answer with `send`, and record it. When there's no answer
        (it took too long, Claude's API refused, something failed), that's recorded and logged instead, and the
        return value is what to tell the asker. `message` is the question's, None for a post after a wipe."""
        started = time.monotonic()
        timeout = CHAT_TIMEOUT_S if kind == "chat" else POST_TIMEOUT_S

        def record(**row) -> int:
            return self._record(kind, code, question, started, message, asker=who, fight=fight,
                                channel_id=channel_id, **row)
        shown = self._shown(who)
        earlier = (lambda after, seen: self._earlier(message, after, seen)) if message else None
        try:  # the timeout counts from the question's turn in its conversation, not while it waits behind another
            answer = await self.conversations.ask(key, code, question, ctx, earlier, message.id if message else None,
                                                  timeout)
            sent = await send(answer)
        except SlowStart as e:
            log.warning("%s answer to %s failed: %s", kind, shown, e)
            record(error=str(e))
            return SLOW_START
        except TimeoutError:  # its conversation was closed: its session may still be answering
            log.warning("%s answer to %s took over %d s; gave up", kind, shown, timeout)
            record(error=f"timeout after {timeout} s")
            return TOO_SLOW
        except ModelError as e:
            log.error("%s answer to %s failed: Claude's API refused (%s)", kind, shown, e.kind)
            record(error=f"model: {e.kind}", answer=e.answer)  # what it cost, when it hit a cap
            return MODEL_ERRORS.get(e.kind, MODEL_ERRORS["other"])
        except WCLError as e:  # loading the report, before the model got the question
            log.error("%s answer to %s failed: Warcraft Logs didn't answer (%s)", kind, shown, e)
            record(error=f"wcl: {e}")
            return WCL_DOWN
        except Exception as e:
            log.exception("%s answer to %s failed", kind, shown)
            record(error=repr(e))  # a broken session's conversation was closed: the next question starts over
            return WENT_WRONG
        self.conversations.saw(key, sent)
        interaction = record(answer=answer, sent=sent)
        log.info("%s answer to %s in %.0f s with %d tool calls, $%.3f (#%d)%s", kind, shown,
                 time.monotonic() - started, len(answer.calls), answer.cost_usd or 0, interaction,
                 f": {preview(answer.text)}" if self.record_answers else "")
        return None

    def _record(self, kind: str, report: str, question: str, started: float, message: discord.Message | None,
                asker: str | None = None, answer: Answer | None = None, error: str | None = None,
                sent: list[int] | None = None, fight: int | None = None, channel_id: int | None = None) -> int:
        """Keep a question and its answer (or why there was none) for improving the bot; returns its id. `message`
        is the question (None for a post after a wipe and for `wipebot try`); `channel_id` where an answer
        without one was posted: a channel's posts after wipes. Recording never stops an answer; without
        `record_answers` nothing is kept (0)."""
        if not self.record_answers:
            return 0
        row: dict = dict(kind=kind, report=report, question=question, asker=asker, error=error,
                         discord_guild_id=message.guild.id if message and message.guild else None,
                         channel_id=message.channel.id if message else channel_id,
                         message_id=message.id if message else None,
                         duration_s=round(time.monotonic() - started, 1), fight=fight)
        if answer:
            row.update(prompt=answer.prompt, answer=answer.text or None, model=answer.model,
                       session_id=answer.session_id, cost_usd=answer.cost_usd, turns=answer.turns, usage=answer.usage,
                       thinking=answer.thinking, tool_calls=answer.records())
        try:
            return self.store.record_interaction(messages=sent or [], **row)
        except Exception:
            log.exception("recording the answer to %r failed", question)
            return 0

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.user and payload.user_id != self.user.id:
            if interaction := self.store.add_reaction(payload.message_id, payload.user_id, str(payload.emoji)):
                log.info("reaction %s to answer #%d", payload.emoji, interaction)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        self.store.remove_reaction(payload.message_id, payload.user_id, str(payload.emoji))

    async def _earlier(self, message: discord.Message, after: int | None, seen: set[int]) -> str:
        """The messages before this one that its conversation hasn't seen; none when Discord won't give them."""
        assert self.user
        try:
            return await earlier_messages(message, self.user, after, seen)
        except discord.HTTPException:
            log.warning("couldn't read the messages before %s", message.jump_url)
            return ""

    async def _about_pull(self, ctx: Context, code: str, report_code: str, fight_id: int) -> None:
        """A reply to the post after a wipe is about that pull."""
        if report_code != code:
            return
        report = await self.library.report(code)
        fight = next((f for f in report.fights if f["id"] == fight_id), None)
        if fight:
            ctx.boss, ctx.pull = report.boss_key(fight), report.pull_no(fight)

    async def _remember(self, user_id: int, name: str, code: str, args: dict) -> str:
        """The remember tool for one raider: keep or forget their character and notes; what to tell the model."""
        if character := str(args.get("character") or "").strip():
            try:  # as the log spells it, when they're in this report; else as found in another one
                args = {**args, "character": (await self.library.report(code)).player(character).name}
            except FactError:
                pass
        raider = self.store.raider(user_id)
        if problem := raider.apply(args):
            return problem
        self.store.save_raider(user_id, raider)
        raider = self.store.raider(user_id)
        log.info("remembering %s", f"{name}: {raider}" if self.record_answers else "a raider's character or notes")
        return f"Remembered for {name}: character {raider.character or 'unknown'}; notes: {raider.notes or 'none'}."

    async def _save_note(self, message: discord.Message, s: ServerSettings, text: str) -> None:
        """A raid note or plan pasted to the bot: saved for the server's guild, if the sender is an officer."""
        guild = self.get_guild(s.discord_guild_id)
        member = None
        if guild is not None:
            try:
                member = guild.get_member(message.author.id) or await guild.fetch_member(message.author.id)
            except discord.NotFound:
                pass
        if not member or not is_officer(s, member):
            await respond(message, f"Only officers save the raid note or plan: {officers(s)}.")
            return
        try:
            reply = commands.save_pasted(self.store, s.discord_guild_id, text, member.display_name)
        except FactError as e:
            reply = f"Couldn't read that as a raid note or plan: {e}."
        log.info("raid note or plan from %s: %s", self._shown(member.display_name), reply.splitlines()[-1])
        await respond(message, reply)

    def _raid_notes(self, s: ServerSettings) -> dict:
        """Context's raid note and plan fields for a server: what its guild saved (an officer's paste)."""
        book, guild = self.store.notebook, s.wcl_guild_id
        return {"notebook": book, "guild_id": guild, "noted": [n.boss for n in book.latest_notes(guild)],
                "planned": list(dict.fromkeys(p.boss for p in book.latest_plans(guild)))}

    async def current_report(self, s: ServerSettings) -> str | None:
        """The live report the server's follower is on, or else its guild's latest raid night (`latest_raid`)."""
        follower = self.followers.get(s.discord_guild_id)
        if follower and follower.code:
            return follower.code
        return await self.latest_raids.of([s.wcl_guild_id])

    async def _settings_for_dm(self, user: discord.abc.User) -> ServerSettings | None:
        """For a DM: the first set-up server the user is in and may chat in."""
        for s in self.store.all_settings():
            guild = self.get_guild(s.discord_guild_id)
            if guild is None:
                continue
            try:
                member = guild.get_member(user.id) or await guild.fetch_member(user.id)
            except discord.NotFound:
                continue
            if may_chat(s, [r.id for r in member.roles]):
                return s
        return None

    async def _text_channel(self, channel_id: int) -> discord.abc.Messageable:
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError(f"channel {channel_id} is not a text channel")
        return channel

    async def _open_session(self, code: str) -> Session:
        session = Session(self.library, code, self.model, log_tool, self.name)
        try:
            return await session.__aenter__()
        except BaseException:  # failed or given up on (chat.OPEN_TIMEOUT_S): no Claude Code process left behind
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                log.exception("closing a session that didn't open failed")
            raise

    async def _try(self, question: str) -> None:
        """`wipebot try`: post the answer to a question in a channel (`try_channel`), marked as a test; the
        question goes to the log."""
        servers = {s.discord_guild_id: s for s in self.store.all_settings()}
        s = servers.get(self.test_server) if self.test_server else next(iter(servers.values()), None)
        if s is None:
            log.error("no set-up server to try in: run the bot and /wipebot setup first")
            return
        if self.try_channel is None:
            log.error("no channel to post in: pass --channel")
            return
        code = self.test_report or await self.current_report(s)
        if not code:
            log.error("no report to ask about: pass --report")
            return
        channel = await self._text_channel(self.try_channel)
        log.info("test question about report %s: %s", code, question)
        started = time.monotonic()
        async with Session(self.library, code, self.model, log_tool, self.name) as session:
            answer = await session.ask(question, Context(where="a test question from the developer"))
        sent = await self._send(channel, answer, footer="🧪 TEST (wipebot try)")
        self._record("try", code, question, started, None, answer=answer, sent=sent)
        log.info("answered with %d tool calls (%s), $%.3f", len(answer.calls),
                 ", ".join(c.name for c in answer.calls), answer.cost_usd or 0)

    @staticmethod
    async def _send(channel: discord.abc.Messageable, answer: Answer, reply_to: discord.Message | None = None,
                    footer: str | None = None) -> list[int]:
        """An answer, as a reply to `reply_to` or else a plain send, without link previews, in at most MAX_MESSAGES
        messages, with `footer` in a small line at the end and the images its tools drew (maps) on its last message.
        Returns the ids of the messages sent."""
        chunks = split_message(answer.text, DISCORD_LIMIT)
        if len(chunks) > MAX_MESSAGES:
            chunks = chunks[:MAX_MESSAGES]
            chunks[-1] = chunks[-1][:DISCORD_LIMIT - len(CUT) - 3].rstrip() + " …\n" + CUT
        if footer:
            tail = f"-# {footer}"
            if chunks and len(chunks[-1]) + len(tail) + 1 <= DISCORD_LIMIT:
                chunks[-1] += "\n" + tail
            else:
                chunks.append(tail)
        sent: list[discord.Message] = []
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            files = [discord.File(io.BytesIO(image), filename=f"map-{n + 1}.webp")
                     for n, image in enumerate(answer.images)] if last else []
            post = reply_to.reply if i == 0 and reply_to else channel.send
            try:
                sent.append(await post(chunk, suppress_embeds=True, files=files))
            except discord.Forbidden:  # invited before the bot asked for Attach Files: the answer without its maps
                if not files:
                    raise
                log.warning("couldn't attach the answer's maps: the bot lacks Attach Files there")
                sent.append(await post(chunk, suppress_embeds=True))
        return [m.id for m in sent]
