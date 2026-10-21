"""warcraft_cli commands, cached on disk, and the errors every knowledge source raises.

Each command runs warcraft_cli's module in a Python subprocess (not its launchers, so it works wherever Wipebot
does) and returns its JSON `data`. At most CLI_CONCURRENCY commands run at once in a process, whoever asks.

The contract of every data accessor in knowledge/: it returns what the source says, and raises KnowledgeError
when the source can't answer (down, slow, refusing, or answering something unreadable), so a caller never takes a
failure for "none". An empty answer (None, [], {}) means the source answered and has nothing: no name for that
spell id, no cooldown in its tooltip, no talent tree for that spec. A source answering that the thing asked for
doesn't exist raises NotFound, a KnowledgeError; an accessor for which that is an ordinary answer returns the
empty value instead, and its docstring says so.
"""
import asyncio
import json
import logging
import sys
import time
import weakref
from typing import Any

from ..cache import DAY, HOUR, cache, cache_key
from ..wcl import use_wcl_env_names

log = logging.getLogger(__name__)


class KnowledgeError(RuntimeError):
    """A source can't answer; the message says which and why, for the model or an "unavailable" line."""


class NotFound(KnowledgeError):
    """The source answered that the thing asked for doesn't exist (warcraft_cli's not_found)."""


MODULES = {
    "warcraft": "warcraft_cli", "wowhead": "wowhead_cli", "method": "method_cli",
    "lorrgs": "lorrgs_cli", "icy-veins": "icy_veins_cli", "warcraftlogs": "warcraftlogs_cli",
    "raiderio": "raiderio_cli", "warcraft-wiki": "warcraft_wiki_cli",
}
CLI_CONCURRENCY = 6
CLI_TIMEOUT_S = 60  # a hung command (a site that stops answering) gives up rather than holding an answer forever

# How long a command's answer is fresh. Older answers are still served at once while a new one is fetched in the
# background (cache.Cache.fetch): guides change a few times a week, Blizzard's running hotfix post daily.
MAX_AGE_S = {
    ("wowhead", "blue-tracker"): HOUR, ("wowhead", "blue-topic"): HOUR, ("raiderio", "character"): HOUR,
    ("warcraft", "cooldown-packet"): 6 * HOUR,  # top parses
    ("method", "guide-full"): 12 * HOUR, ("icy-veins", "guide-full"): 12 * HOUR, ("wowhead", "guide-full"): 12 * HOUR,
}
DEFAULT_MAX_AGE_S = DAY  # searches, tooltips, spec cooldowns

MEMO_S = DAY  # game data read into memory is read again after this: it changes with patches, a process runs weeks


class Memo:
    """Game data a process keeps in memory, by key, MEMO_S each (or the `for_s` it was kept for)."""

    def __init__(self):
        self._kept: dict[Any, tuple[float, Any]] = {}

    def __contains__(self, key: Any) -> bool:
        found = self._kept.get(key)
        return found is not None and time.monotonic() < found[0]

    def __getitem__(self, key: Any) -> Any:
        return self._kept[key][1]

    def get(self, key: Any, default: Any = None) -> Any:
        return self[key] if key in self else default

    def keep(self, key: Any, value: Any, for_s: float = MEMO_S) -> Any:
        self._kept[key] = (time.monotonic() + for_s, value)
        return value


_limits: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


async def cli(tool: str, *args: str) -> dict:
    """Run one warcraft_cli command and return its `data`, or raise KnowledgeError with its error. Answers are
    kept in the cache on disk; a failure isn't. A command nobody waits for any more is killed. Arguments a model's
    words fill (a search, a page's address) come last, after `--`, so they're never read as an option."""
    return await cache().fetch(cache_key("cli", tool, *_key_args(args)),
                               MAX_AGE_S.get((tool, args[0]), DEFAULT_MAX_AGE_S), lambda: _run(tool, *args),
                               cancel=True)


def _key_args(args: tuple[str, ...]) -> tuple[str, ...]:
    """A command as its cache key has it: what follows `--` before the options, where it stood before `--` was
    added, so the answers kept since stay found."""
    if "--" not in args:
        return args
    end = args.index("--")
    options = next((i for i, a in enumerate(args[:end]) if a.startswith("-")), end)
    return (*args[:options], *args[end + 1:], *args[options:end])


def _limit() -> asyncio.Semaphore:
    """The running event loop's limit on commands at once (a semaphore can't be shared between loops)."""
    loop = asyncio.get_running_loop()
    if loop not in _limits:
        _limits[loop] = asyncio.Semaphore(CLI_CONCURRENCY)
    return _limits[loop]


async def _run(tool: str, *args: str) -> dict:
    use_wcl_env_names()
    async with _limit():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", f"from {MODULES[tool]}.main import run; run()", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), CLI_TIMEOUT_S)
        except TimeoutError:
            raise KnowledgeError(f"{tool} {args[0]} gave no answer in {CLI_TIMEOUT_S} s") from None
        finally:
            if proc.returncode is None:  # timed out, or the answer it was for was cancelled
                proc.kill()
                await asyncio.shield(proc.wait())
    text = (out or err).decode("utf-8", errors="replace")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:  # a crash, not an answer: its output (a traceback) is for the log, not the model
        log.warning("%s %s failed (exit %s): %s", tool, " ".join(args), proc.returncode, text[-2000:])
        raise KnowledgeError(f"{tool} {args[0]} failed with no readable answer") from None
    if not envelope.get("ok"):
        e = envelope.get("error") or {}
        error = NotFound if e.get("code") == "not_found" else KnowledgeError
        raise error(f"{tool} {args[0]}: {e.get('code')}: {e.get('message')}")
    return envelope.get("data") or {}
