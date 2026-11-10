"""Conversations for Discord: one agent session per person and place, so follow-ups keep their context.

A conversation is keyed by place and person (a channel they mentioned the bot in, or their DMs). It is
closed after IDLE_S without a message, when the report changes (once it has answered what it was asked), when its
context has grown too long (the session says it's `worn_out`), when an answer was given up on or failed, or when
a new one opens and, with the questions being answered, more than MAX_SESSIONS would be open: then the least recently
used of those not answering go (with every one answering, one more opens). A question queued behind one whose
conversation was closed starts a new one. Each session runs a Claude Code process, hence the cap, and why at most
MAX_ANSWERING questions are answered at once: the next waits its turn, so there are never more processes than that.
Each question comes with what was said in that place since the conversation's previous question (`earlier`),
everyone's messages, so a conversation knows what the bot told others there; a new conversation gets the
messages before its first one. That is also what a conversation goes on from after a restart (a deploy): none is
kept across one, as a session on a server's own key can't be picked up again.
"""
import asyncio
import logging
import re
import time
import weakref
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field, replace
from typing import Protocol

log = logging.getLogger(__name__)

DISCORD_LIMIT = 2000  # characters per Discord message
IDLE_S = 15 * 60
MAX_SESSIONS = 6
MAX_ANSWERING = 8  # questions being answered at once, each in a session of its own at most; no fewer than MAX_SESSIONS
OPEN_TIMEOUT_S = 60  # opening a session (loading the report, starting Claude Code) gives up after this long


class SlowStart(TimeoutError):
    """Opening a conversation's session took over OPEN_TIMEOUT_S."""


class Asker(Protocol):
    @property
    def worn_out(self) -> bool: ...  # the conversation has grown too long: close it after this answer

    async def ask(self, question: str, ctx) -> object: ...
    async def __aexit__(self, *exc) -> None: ...


@dataclass
class _Conversation:
    session: Asker
    code: str
    env: dict | None = None  # what its session runs on, when not the bot's own key: a server's own (Context.env)
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    busy: int = 0  # questions answering or waiting in it: it isn't closed for being idle or the oldest
    last_question: int | None = None  # its previous question's message id
    seen: set[int] = field(default_factory=set)  # message ids the session already has: its questions and answers
    retired: bool = False  # taken out of _convs while answering (the report changed): closed once it's done
    closed: bool = False


class Conversations:
    def __init__(self, open_session: Callable[..., Awaitable[Asker]],
                 survives: tuple[type[Exception], ...] = ()):
        """`open_session(report_code)` returns a started session for that report, and `open_session(report_code,
        env=env)` one that runs on a server's own key. After an error from a question the conversation is closed
        (its session may be mid-answer or broken), but after one of `survives`."""
        self._open, self._survives = open_session, survives
        self._turns = asyncio.Semaphore(MAX_ANSWERING)  # held by each question from its session's opening to its answer
        self._answering = 0  # the questions holding a turn: each session not idle (opening ones too) has one or more
        self._convs: dict[Hashable, _Conversation] = {}
        # Held only for the bookkeeping of _convs: sessions open and close outside it (loading a report can take
        # most of a minute), so one asker's new conversation never holds up another's question.
        self._lock = asyncio.Lock()
        # Per key while its session opens, so two questions for the same key share one session.
        self._opening: weakref.WeakValueDictionary[Hashable, asyncio.Lock] = weakref.WeakValueDictionary()

    def code(self, key: Hashable) -> str | None:
        """The report the open conversation at `key` is about; None without one."""
        conv = self._convs.get(key)
        return conv.code if conv else None

    async def ask(self, key: Hashable, code: str, question: str, ctx,
                  earlier: Callable[[int | None, set[int]], Awaitable[str]] | None = None,
                  message_id: int | None = None, timeout: float | None = None):
        """`earlier(after, seen)` gives the messages before this question (`message_id`) that the conversation
        hasn't seen: since its previous question `after` (None for a new conversation: from further back), leaving
        out the `seen` ones. `timeout` counts from when the question's turn in its conversation comes, not while it
        waits behind another or its session opens, nor while MAX_ANSWERING others are being answered."""
        async with self._turns:
            self._answering += 1
            try:
                return await self._in_turn(key, code, question, ctx, earlier, message_id, timeout)
            finally:
                self._answering -= 1

    async def _in_turn(self, key: Hashable, code: str, question: str, ctx,
                       earlier: Callable[[int | None, set[int]], Awaitable[str]] | None, message_id: int | None,
                       timeout: float | None):
        conv = await self._conversation(key, code, getattr(ctx, "env", None))
        try:
            return await self._ask(key, conv, code, question, ctx, earlier, message_id, timeout)
        finally:
            conv.busy -= 1
            if conv.retired and not conv.busy:
                await self._shut([(key, conv)])

    async def _conversation(self, key: Hashable, code: str, env: dict | None = None) -> _Conversation:
        """The conversation at `key` about `code`, opened if there's none, counted as busy. `env`: what its session
        runs on, when not the bot's own key; a conversation on another starts over, as for another report."""
        opening = self._opening.get(key)
        if opening is None:
            opening = self._opening[key] = asyncio.Lock()
        async with opening:
            async with self._lock:
                closing = self._take_idle()
                conv = self._convs.get(key)
                if conv and (conv.code != code or conv.env != env):  # a new raid night, or another key: start
                    # over, once the old one has answered
                    del self._convs[key]
                    if conv.busy:
                        conv.retired = True
                    else:
                        closing.append((key, conv))
                    conv = None
                if conv is None:
                    # A new session: the idle ones go, the least recently used first, until they and the questions
                    # being answered (this one too) are MAX_SESSIONS at most. Counted by question, not by what's in
                    # _convs, since a session still opening and one retired are processes too: however many ask at
                    # once, the sessions open, idle ones included, never pass MAX_ANSWERING.
                    idle = sorted((k for k, c in self._convs.items() if not c.busy),
                                  key=lambda k: self._convs[k].last_used)
                    for oldest in idle[:max(0, len(idle) + self._answering - MAX_SESSIONS)]:
                        closing.append((oldest, self._convs.pop(oldest)))
                if conv:
                    conv.last_used = time.time()
                    conv.busy += 1
            await self._shut(closing)
            if conv is None:
                more = {"env": env} if env is not None else {}
                try:
                    async with asyncio.timeout(OPEN_TIMEOUT_S):
                        session = await self._open(code, **more)
                except TimeoutError:
                    raise SlowStart(f"opening a conversation took over {OPEN_TIMEOUT_S} s") from None
                async with self._lock:
                    conv = self._convs[key] = _Conversation(session, code, env)
                    conv.busy += 1
        return conv

    async def _ask(self, key: Hashable, conv: _Conversation, code: str, question: str, ctx,
                   earlier: Callable[[int | None, set[int]], Awaitable[str]] | None, message_id: int | None,
                   timeout: float | None):
        # Closing happens under conv.lock: a question queued behind this one then starts on a new session.
        async with conv.lock:  # one question at a time per conversation
            if conv.closed or conv.session.worn_out:  # by the answer this one queued behind: start over
                await self._close(key, conv)
                return await self._in_turn(key, code, question, ctx, earlier, message_id, timeout)  # its turn still
            try:
                async with asyncio.timeout(timeout):
                    # Read here, after the previous answer was posted: what was said since, by anyone.
                    after, seen = conv.last_question, set(conv.seen)
                    if message_id is not None:
                        conv.last_question = message_id
                        conv.seen.add(message_id)
                    if earlier:
                        ctx = replace(ctx, earlier=await earlier(after, seen), follow_up=after is not None)
                    answer = await conv.session.ask(question, ctx)
            except Exception as e:  # given up on (its session may still be answering), or broken
                if not isinstance(e, self._survives):
                    await self._close(key, conv)
                raise
            conv.last_used = time.time()
            if conv.session.worn_out:  # the next question starts over, with the place's recent messages
                log.info("conversation %s has grown long; closing it", key)
                await self._close(key, conv)
        return answer

    async def _close(self, key: Hashable, conv: _Conversation) -> None:
        """Take a conversation out of _convs, if it's still there, and close it."""
        async with self._lock:
            if self._convs.get(key) is conv:
                del self._convs[key]
        await self._shut([(key, conv)])

    def saw(self, key: Hashable, message_ids: list[int]) -> None:
        """The messages an answer was posted as, which the conversation's session already has."""
        if conv := self._convs.get(key):
            conv.seen.update(message_ids)

    async def drop(self, key: Hashable) -> None:
        """End a conversation, even mid-answer."""
        async with self._lock:
            closing = [(key, self._convs.pop(key))] if key in self._convs else []
        await self._shut(closing)

    async def start_over(self, which: Callable[[Hashable], bool]) -> None:
        """End the conversations whose key is `which`, so their next question starts over (what they read changed);
        one answering is closed once it's done."""
        async with self._lock:
            closing = []
            for key in [k for k in self._convs if which(k)]:
                conv = self._convs.pop(key)
                if conv.busy:
                    conv.retired = True
                else:
                    closing.append((key, conv))
        await self._shut(closing)

    async def close_all(self) -> None:
        """Close every conversation: the bot is stopping."""
        async with self._lock:
            closing = list(self._convs.items())
            self._convs.clear()
        await self._shut(closing)

    def _take_idle(self) -> list[tuple[Hashable, _Conversation]]:
        """The conversations idle for IDLE_S, taken out of _convs (under _lock) to be closed."""
        now = time.time()
        return [(k, self._convs.pop(k)) for k, c in list(self._convs.items())
                if now - c.last_used > IDLE_S and not c.busy]

    async def _shut(self, closing: list[tuple[Hashable, _Conversation]]) -> None:
        for key, conv in closing:
            if conv.closed:
                continue
            conv.closed = True
            try:
                await conv.session.__aexit__(None, None, None)
            except Exception:
                log.exception("closing conversation %s failed", key)


FENCE = re.compile(r"```(?:(\w+)\n)?")  # where a code block opens or closes, with the language it opens in
CLOSE = "\n```"


def _cut(text: str, room: int) -> int:
    """Where to cut `text` to fit `room`: at its last line break, else its last space, else at `room`."""
    cut = text.rfind("\n", 0, room)
    if cut < room // 2:
        cut = text.rfind(" ", 0, room)
    return cut if cut > 0 else room


def _open_block(text: str) -> str | None:
    """How the code block that `text` ends inside opens ("```", "```lua"); None when it ends outside one."""
    marks = FENCE.findall(text)
    return f"```{marks[-1]}" if len(marks) % 2 else None


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split an answer into Discord-sized messages, at line breaks where possible, else at spaces. A code block cut
    in two is closed at the cut and opened again in the next message, so both halves show as code, lined up."""
    chunks = []
    while len(text) > limit:
        cut = _cut(text, limit)
        if _open_block(text[:cut]):  # leave room to close it
            cut = _cut(text, limit - len(CLOSE))
        part, text = text[:cut].rstrip(), text[cut:]
        if block := _open_block(part):
            # the next message opens the block again; the code keeps its indentation
            part, text = part + CLOSE, f"{block}\n{text[1:] if text[:1] in (chr(10), ' ') else text}"
        else:
            text = text.lstrip()
        chunks.append(part)
    if text.strip():
        chunks.append(text)
    return chunks
