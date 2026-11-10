"""The line under a question while its answer is written: what the answer has looked at, what it's looking at now,
and how long it has taken; and the steps an answer went through, kept under it where a server wants them. In the
question's language, English or Norwegian (texts.py has the words)."""
import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Sequence

import discord

from wipebot.core import server

from . import texts

log = logging.getLogger(__name__)

PROGRESS_AFTER_S = 5.0  # an answer's progress line shows once it has taken this long
PROGRESS_EDIT_S = 2.0  # and is edited at most this often
NOT_SHOWN = ("Skill", "Read")  # tools the progress line leaves out: reading the skill isn't looking at the log
SHOWN_DONE = 8  # tools done the line names, the latest
SHOWN_NOW = 3  # tools under way it names
FALLBACK_SPINNER = "⏳"


def _clean(value: object) -> str:
    """An argument as plain text for the line: no markdown, mentions or links, and short."""
    return " ".join(re.sub(r"[^\w' ,.-]", " ", str(value)).split())[:40]


def doing(name: str, args: dict, lang: str = "en") -> str:
    """What a call does, in `lang`, with the boss, pull and spec it's about: never a player."""
    pull = args.get("pull")
    named = texts.PULLS.get(str(pull).lower())
    pull = (texts.EVERY_PULL.to(lang) if args.get("all_pulls") else None) or (
        (named.to(lang) if named else texts.PULL.to(lang, n=_clean(pull))) if pull not in (None, "") else None)
    about = [s for s in (_clean(args.get("boss") or ""), pull, _clean(args.get("spec") or "")) if s]
    what = texts.DOING[name].to(lang) if name in texts.DOING else step(name, lang)
    return what + (f" ({', '.join(about)})" if about else "")


def step(name: str, lang: str = "en") -> str:
    """A step an answer went through, in `lang`: its tool's title, or what a Norwegian raider would call it."""
    return (texts.STEPS_NO.get(name) if lang == "no" else None) or server.title(name)


def steps(calls: Sequence[tuple[str, bool]], lang: str = "en") -> str:
    """The small line of the steps an answer went through, in `lang`, from its tool calls (name, failed) in order:
    each tool once, with how often it ran when more than once, ✗ when every call of it failed; none without a
    step."""
    runs: Counter[str] = Counter(name for name, _ in calls if name not in NOT_SHOWN)
    worked = {name for name, failed in calls if not failed}
    return ("-# " + " · ".join(f"{'✓' if name in worked else '✗'} {step(name, lang)}"
                               + (f" ×{n}" if n > 1 else "") for name, n in runs.items())) if runs else ""


class Progress:
    """While an answer is written: a small reply under the question, edited as the model calls tools and they
    finish (at most every PROGRESS_EDIT_S, Discord limits edits) and deleted once the answer is posted. One line
    names the tools done, the other what it does now with a spinner (thinking, a tool, or writing the answer once
    what it read is in), and since when (a Discord timestamp, which counts up by itself). It's sent silently and the
    answer as a new message, so the asker is notified of the answer. It names the boss, pull and spec a call is
    about, never a player, and speaks `lang`: the question's language."""

    def __init__(self, message: discord.Message, spinner: str = FALLBACK_SPINNER, lang: str = "en"):
        self._message, self._spinner, self._lang = message, spinner, lang
        self._since = int(message.created_at.timestamp())
        self._calls: dict[tuple[str, str], None] = {}  # (tool, what it does), in the order called
        self._started: Counter[tuple[str, str]] = Counter()
        self._finished: Counter[tuple[str, str]] = Counter()  # may come before its start: the counts still match
        self._shown, self._sent = "", None
        # time.monotonic() of the last edit: as if PROGRESS_AFTER_S from now, so a quick answer shows none
        self._edited = time.monotonic() + PROGRESS_AFTER_S - PROGRESS_EDIT_S
        self._closed = self._sending = False
        self._task = asyncio.create_task(self._show())

    def tool(self, name: str, args: dict) -> None:
        """Context.on_tool: the model called `name`."""
        self._count(self._started, name, args)

    def done(self, name: str, args: dict) -> None:
        """Context.on_done: a call of `name` returned."""
        self._count(self._finished, name, args)

    def _count(self, counts: Counter, name: str, args: dict) -> None:
        if name in NOT_SHOWN:
            return
        key = (name, doing(name, args, self._lang))
        self._calls.setdefault(key)
        counts[key] += 1
        if self._task.done() and not self._closed:
            self._task = asyncio.create_task(self._show())

    def _text(self) -> str:
        now = [k for k in self._calls if self._started[k] > self._finished[k]]
        busy = {name for name, _ in now}
        lang = self._lang
        done = list(dict.fromkeys(step(k[0], lang) for k in self._calls if self._finished[k] and k[0] not in busy))
        lines = []
        if done:
            lines.append("-# " + ("… · " if len(done) > SHOWN_DONE else "")
                         + " · ".join(f"✓ {title}" for title in done[-SHOWN_DONE:]))
        # Nothing under way: thinking before the first read, and after one the answer is being written, the longest
        # stretch of the wait (if it reads more instead, the line says so at its next edit)
        doing_now = [f"{self._spinner} {what}" for _, what in now[:SHOWN_NOW]] or [
            f"{self._spinner} {(texts.WRITING if done else texts.THINKING).to(lang)}"]
        more = f" · {texts.MORE.to(lang, n=len(now) - SHOWN_NOW)}" if len(now) > SHOWN_NOW else ""
        lines.append(f"-# {' · '.join(doing_now)}{more} · {texts.STARTED.to(lang)} <t:{self._since}:R>")
        return "\n".join(lines)

    async def _show(self) -> None:
        while not self._closed and self._text() != self._shown:
            await asyncio.sleep(self._edited + PROGRESS_EDIT_S - time.monotonic())
            if self._closed:
                return
            text = self._text()  # with what came in meanwhile
            self._sending = True
            try:
                if self._sent is None:
                    if isinstance(self._message.channel, discord.DMChannel):
                        self._sent = await self._message.channel.send(text, silent=True)
                    else:
                        self._sent = await self._message.reply(text, silent=True, mention_author=False)
                else:
                    await self._sent.edit(content=text)
            except discord.HTTPException as e:  # the answer matters, not this
                log.info("showing an answer's progress failed: %s", e)
                return
            finally:
                self._sending = False
            self._shown, self._edited = text, time.monotonic()

    async def close(self) -> None:
        """Stop showing progress and delete the line: after a send under way, so none is left behind."""
        self._closed = True
        if not self._sending:  # only waiting for its turn
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        if self._sent is not None:
            try:
                await self._sent.delete()
            except discord.HTTPException as e:
                log.info("deleting an answer's progress line failed: %s", e)
