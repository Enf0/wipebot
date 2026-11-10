"""What a Discord message carries besides its text, where a case sets it, and the Context its questions are asked
with then.

A raider's question reaches the bot with the conversation before it, the server's saved raid note, what the bot
remembers of the asker, the server's language and today's date. A case sets them with these keys (cases/README.md):

    set_up    asked from the guild's own Discord server, as `/wipebot setup` leaves it
    earlier   what was said in the place before the first question: [who, text] pairs, oldest first ("Wipebot" is
              the bot itself)
    note      a raid note an officer pasted before the night's first pull: a file under tests/fixtures/real/notes,
              or under evals/cases/notes when a case needs a note no night had
    notes     what the bot remembers of the asker (the remember tool's notes)
    language  the server's language, as its admin set it
    today     the day the question is asked ("2026-10-22"): the date in its first line and the tools' clock

As the bot, a `set_up` case's Context is made by the bot's own builder (WipeBot._context) from a server set up with
the report's guild, so the question's first lines hold what production's do: the guild, the server's language, the
remembered notes, the bosses with a saved note; raid_note finds the note in the server's notebook, the core's tools
see that one is saved, and remember and answer_in_dm are the bot's own. Not carried yet: linked messages, pictures,
raid plans, a report being logged, and follow_pulls, which stays the stand-in every case has. In a client (the plugin,
the connector) only `today` is used: the rest is Discord's, and a case that needs `earlier`, `note` or `notes` to be
answerable is asked only as the bot (`discord_only`).

A case with none of these keys is asked as before: run.trial's own Context, untouched.
"""
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import discord

from wipebot.core import clock, notebook, server
from wipebot.core.notebook import Notebook
from wipebot.discord import commands
from wipebot.discord.bot import WipeBot, format_earlier, where_asked
from wipebot.discord.host import Context
from wipebot.discord.store import Raider, ServerSettings, Store

MESSAGE = ("set_up", "earlier", "note", "notes", "language", "today")
DEFAULTS = ("report", "set_up", "language", "today", "where")  # what a file's [defaults] may hold for its cases
ROOT = Path(__file__).resolve().parent.parent
# Where a case's `note` is looked for: the notes the guild's officers wrote, then the ones written for a case
NOTES = (ROOT / "tests" / "fixtures" / "real" / "notes", ROOT / "evals" / "cases" / "notes")
# The stand-in server and asker of a `set_up` case, and the realm of the one guild whose reports are cases
# (.claude/rules/test-data.md), as `/wipebot setup` keeps it
SERVER_ID, USER_ID, REALM = 1, 2, ("kazzak", "eu")
BOT = "Wipebot"  # an `earlier` message's author when the bot wrote it
JUDGE_CHARS = 300  # of each earlier message, in the question as the judge sees it
# A case's own day and its server's notebook, for the tools that read them (per_case), within its Context's reading
TODAY: ContextVar[float | None] = ContextVar("a case's today", default=None)
BOOK: ContextVar[Path | None] = ContextVar("a case's notebook", default=None)


def note_file(name: object) -> Path | None:
    """The file of a case's `note`, None when no folder of notes has it."""
    if not (isinstance(name, str) and name.strip()):
        return None
    return next((folder / name for folder in NOTES if (folder / name).is_file()), None)


def problem(case: dict) -> str:
    """What is wrong with a case's message keys, "" when nothing is."""
    earlier = case.get("earlier")
    if "set_up" in case and not isinstance(case["set_up"], bool):
        return "set_up is true or false"
    if needs := [k for k in ("earlier", "note", "notes", "language") if k in case and not case.get("set_up")]:
        return f"{', '.join(needs)} is the server's or the asker's in Discord: the case needs set_up = true"
    if earlier is not None and not (isinstance(earlier, list) and earlier and all(
            isinstance(m, list) and len(m) == 2 and all(isinstance(x, str) and x.strip() for x in m)
            for m in earlier)):
        return "earlier is a list of [who, text] pairs"
    if earlier and earlier[0][0] == BOT:
        return f"earlier starts with what someone asked: {BOT} answers a message before it"
    if "note" in case and not note_file(case["note"]):
        return f"note is a file under {' or '.join(str(folder.relative_to(ROOT)) for folder in NOTES)}"
    if "notes" in case and not (isinstance(case["notes"], list) and all(isinstance(n, str) for n in case["notes"])):
        return "notes is a list of lines"
    if "language" in case and not isinstance(case["language"], str):
        return "language is the server's, as an admin writes it: \"Norwegian\""
    try:
        if "today" in case:
            date.fromisoformat(case["today"])
    except (TypeError, ValueError):
        return "today is a day in quotes: \"2026-10-22\""
    return ""


def discord_only(case: dict) -> bool:
    """Whether the case can only be answered with what Discord carries: a client has no earlier messages, no saved
    note and no remembered notes, so there the question would be another one."""
    return any(k in case for k in ("earlier", "note", "notes"))


def today(case: dict) -> float | None:
    """The case's own "now": noon UTC of its `today`, None for a case on the recorded data's day. Noon, so a list
    of the guild's reports is asked from another hour than the recorded day's lists (wcl.guild_reports keeps one by
    the hour it starts from) and never reads one that was fetched before the case's night existed."""
    if not case.get("today"):
        return None
    return datetime.combine(date.fromisoformat(case["today"]), datetime.min.time(), UTC).timestamp() + 12 * 3600


@dataclass
class Asked(Context):
    """A question's Context with its case's day and notebook: within `reading`, where the session runs every tool,
    the tools' clock is the case's `today` and what the guild saved is read from the case's own notebook, never
    another case's."""
    today: float | None = None
    book: Path | None = None

    @contextmanager
    def reading(self) -> Generator[None]:
        day, book = TODAY.set(self.today), BOOK.set(self.book)
        try:
            with super().reading():
                yield
        finally:
            TODAY.reset(day)
            BOOK.reset(book)


def per_case() -> None:
    """The tools' clock and the notebook's file as a case sets them: clock.now is the case's `today` and
    notebook.default_path its own file while a tool runs for it (Asked.reading), and what they were everywhere
    else. Both are read through their modules at each call, so replacing the two functions reaches every tool."""
    if clock.now.__module__ == __name__:  # done already
        return
    real_now, real_path = clock.now, notebook.default_path

    def now() -> float:
        return TODAY.get() or real_now()

    def default_path() -> Path:
        return BOOK.get() or real_path()
    setattr(clock, "now", now)  # noqa: B010  the modules' own functions, replaced for this process
    setattr(notebook, "default_path", default_path)  # noqa: B010


def said(case: dict, now: float) -> str:
    """The case's `earlier` as the bot hands a place's earlier messages to the model (bot.format_earlier): one a
    minute, the last a minute before the question; the bot's own as an answer to whoever wrote before it."""
    messages, before = [], ""
    start = datetime.fromtimestamp(now, UTC) - timedelta(minutes=len(case.get("earlier", [])))
    for n, (who, text) in enumerate(case.get("earlier", [])):
        author = f"{BOT} (answering {before})" if who == BOT else who
        before = before if who == BOT else who
        messages.append((author, start + timedelta(minutes=n), text))
    return format_earlier(messages)


def said_before(case: dict) -> str:
    """The case's `earlier` for the judge and the labeller, before the question: who said what, each message's
    start. "" for a case without one."""
    if not case.get("earlier"):
        return ""
    lines = [f"{who}: {' '.join(text.split())[:JUDGE_CHARS]}" for who, text in case["earlier"]]
    return "Said in the same place before the question, oldest first:\n" + "\n".join(lines) + "\n"


async def contexts(case: dict, plain: Context, library: server.Library, mode: str,
                   recorded_at: float) -> Callable[[int], Context]:
    """The Context of each of a case's questions, by its turn (0 the first). `plain` is the one run.trial made, and
    for a case without message keys it is every turn's, as it always was. With `today` alone, or in a client, it is
    `plain` on the case's day. As the bot, a `set_up` case's is made by the bot for every question, as the bot makes
    one for every message: the first with the `earlier` messages, the later ones as follow-ups."""
    now = today(case)
    if not (case.get("set_up") and mode == "bot"):
        if now is None:
            return lambda turn: plain
        dated = Asked(**{f.name: getattr(plain, f.name) for f in fields(Context)}, today=now)
        return lambda turn: dated
    report = await library.report(case["report"])
    book = Path(tempfile.mkdtemp(prefix="wipebot-evals-")) / "notes.sqlite"  # this case's alone
    store = Store(":memory:", notebook=Notebook(book))
    bot = WipeBot(store, library)
    bot.own_keys = False  # a host's own bot: a set-up server reads and saves its guild's notes

    async def no_dm(*args, **kwargs) -> None:
        """answer_in_dm starts an answer in the asker's DMs: none is written here, the tool says what it says."""
    setattr(bot, "_in_dm", no_dm)  # noqa: B010
    settings = ServerSettings(SERVER_ID, report.guild_id or 0, report.guild_name, *REALM)
    store.save_settings(settings)
    store.set_language(SERVER_ID, case.get("language"))
    if case.get("asker") or case.get("notes"):  # what the bot remembers of the asker: their character, their notes
        store.save_raider(USER_ID, Raider(case.get("asker"), list(case.get("notes", []))))
    if pasted := note_file(case.get("note")):  # as an officer's paste to the bot, saved as of the night's first pull
        commands.save_pasted(store, SERVER_ID, pasted.read_text(encoding="utf-8"), "an officer",
                             from_pull=(report, 1))
    place = plain.where
    in_dm = not place.startswith("in #")
    where = where_asked(in_dm, place.removeprefix("in #"), True) or place
    user = cast(discord.User, SimpleNamespace(id=USER_ID))
    channel = None if in_dm else cast(discord.abc.Messageable, SimpleNamespace(id=SERVER_ID))
    earlier = said(case, now or recorded_at)

    name = case.get("discord_name") or case.get("asker") or "a raider"  # as Discord shows them

    def turn_context(turn: int) -> Context:
        made = bot._context(settings, user, name, case["report"], where, channel, pull=case.get("pull"),
                            boss=case.get("boss"))
        made.tools.pop("follow_pulls")  # it would ask Warcraft Logs for tonight's log: the stand-in answers, as ever
        made = replace(made, discord_name=case.get("discord_name"), live=plain.live, prefetch=plain.prefetch,
                       earlier="" if turn else earlier, follow_up=turn > 0)
        return Asked(**{f.name: getattr(made, f.name) for f in fields(Context)}, today=now, book=book)
    return turn_context
