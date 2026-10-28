"""A cache on disk for data that's slow to fetch and changes slowly or never: guides and tooltips (through
warcraft_cli), Warcraft Logs data about finished pulls, rankings. It survives restarts, so the bot after a deploy,
`wipebot call` and the evals don't fetch it all again.

One SQLite file (~/.cache/wipebot/cache.sqlite; WIPEBOT_CACHE sets another path, or `off`). Values are JSON,
compressed (a night's events shrink about tenfold). An
entry is fresh for the max age its caller gives; `fetch` serves one up to twice that old at once and gets a new one
in the background (stale-while-revalidate), and an older one only when the source fails. Callers asking for the
same key at once share one fetch; it goes on when they leave (a Warcraft Logs query is paid for once sent), unless
asked to stop with the last of them (a warcraft_cli command, a process worth killing). Entries older than KEEP_DAYS
are dropped once a day, and the oldest when the file outgrows MAX_MB (WIPEBOT_CACHE_MAX_MB).

A key naming a report that isn't public (`never_keep`, wcl.mark_private) is never kept, nor its fetch shared by callers
asking at once: only the users Warcraft Logs lets read that report may see its data.

The evals freeze theirs (WIPEBOT_CACHE_FROZEN=1): nothing is refreshed or dropped, so every run reads the same
Warcraft Logs data and guides; what's missing is fetched once and kept. Its `recorded_at` is "now" for them
(clock.py).
"""
import asyncio
import json
import logging
import os
import sqlite3
import time
import zlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".cache" / "wipebot" / "cache.sqlite"
KEEP_DAYS = 30  # entries older than this are dropped, when the cache opens and then daily
HOUR, DAY = 3600, 86400
FOREVER = KEEP_DAYS * DAY  # for what never changes, such as a finished pull
STALE_SERVED = 2  # an entry up to this many times its max age is served while a new one is fetched
MAX_MB = 2000  # past this, the oldest entries are dropped at the daily purge
_unkept: set[str] = set()  # words a kept key may not hold: the codes of reports that aren't public


def never_keep(word: str) -> None:
    """From now on, keep nothing whose key holds `word` (a private report's code), in this process."""
    _unkept.add(word)


def keeps(key: str) -> bool:
    """Whether what's under `key` may be kept and shared: it names no report that isn't public."""
    return not any(word in key for word in _unkept)


class Cache:
    frozen = False

    def __init__(self, path: Path | str, frozen: bool = False):
        self.frozen = frozen
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, "
                         "value BLOB NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._purged_at = 0.0
        if not frozen:
            self.purge()
        self._fetching: dict[str, asyncio.Future] = {}  # what's being fetched now, by key
        self._tasks: set[asyncio.Future] = set()
        self._waiting: dict[asyncio.Future, int] = {}  # callers awaiting each fetch
        self._background: set[asyncio.Future] = set()  # refreshes started for nobody waiting
        self._recorded_at: float | None = None

    def purge(self) -> None:
        """Drop the entries older than KEEP_DAYS, then the oldest past MAX_MB of values."""
        self._purged_at = time.time()
        self._db.execute("DELETE FROM cache WHERE stored_at < ?", (time.time() - KEEP_DAYS * DAY,))
        cap = float(os.environ.get("WIPEBOT_CACHE_MAX_MB") or MAX_MB) * 1e6
        self._db.execute("DELETE FROM cache WHERE key IN (SELECT key FROM (SELECT key, SUM(LENGTH(value)) OVER "
                         "(ORDER BY stored_at DESC) AS total FROM cache) WHERE total > ?)", (cap,))

    def recorded_at(self, first: float | None = None) -> float:
        """When the data was recorded (clock.now() for a frozen cache), kept in the file the first time it's asked:
        `first`, else the oldest entry's time, or now for a new file. It never moves after, though missing data is
        added."""
        if self._recorded_at is None:
            row = self._db.execute("SELECT value FROM meta WHERE key = 'recorded_at'").fetchone()
            if row is None:
                oldest = self._db.execute("SELECT MIN(stored_at) FROM cache").fetchone()[0]
                self._db.execute("INSERT OR IGNORE INTO meta VALUES ('recorded_at', ?)",
                                 (first or oldest or time.time(),))
                row = self._db.execute("SELECT value FROM meta WHERE key = 'recorded_at'").fetchone()
            self._recorded_at = float(row[0])
        return self._recorded_at

    def get(self, key: str) -> tuple[Any, float] | None:
        """(value, age in seconds), or None."""
        if not keeps(key):
            return None
        row = self._db.execute("SELECT value, stored_at FROM cache WHERE key = ?", (key,)).fetchone()
        return (json.loads(zlib.decompress(row[0])), time.time() - row[1]) if row else None

    def put(self, key: str, value: Any) -> None:
        if not keeps(key):  # checked again once fetched: the report may have turned out private meanwhile
            return
        if not self.frozen and time.time() - self._purged_at > DAY:
            self.purge()
        self._db.execute("INSERT OR REPLACE INTO cache (key, stored_at, value) VALUES (?, ?, ?)",
                         (key, time.time(), zlib.compress(json.dumps(value).encode(), 1)))

    async def fetch(self, key: str, max_age_s: float, get: Callable[[], Awaitable[Any]], *,
                    cancel: bool = False) -> Any:
        """The value under `key`: fresh from the cache, else from `get()`. One older than `max_age_s` but at most
        STALE_SERVED times that is served at once while `get()` replaces it in the background; an older one is
        served only when `get()` fails. With `cancel`, `get()` is cancelled when the last caller waiting for it is
        (a background refresh goes on)."""
        if not keeps(key):
            return await get()
        found = self.get(key)
        if found is not None:
            value, age = found
            if self.frozen or age <= max_age_s:
                return value
            if age <= STALE_SERVED * max_age_s:
                if key not in self._fetching:
                    self._background.add(self._start(key, get))
                return value
        task = self._fetching.get(key) or self._start(key, get)
        self._waiting[task] = self._waiting.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancel and self._waiting[task] == 1 and task not in self._background:
                task.cancel()
            raise
        except Exception as e:
            if found is None:
                raise
            log.info("fetching %s failed, serving the copy from %.0f s ago: %s", key[:80], found[1], e)
            return found[0]
        finally:
            self._waiting[task] -= 1
            if not self._waiting[task]:
                del self._waiting[task]

    def _start(self, key: str, get: Callable[[], Awaitable[Any]]) -> asyncio.Future:
        """Fetch `key` and keep its value, shared by everyone asking for it meanwhile."""
        async def run() -> Any:
            value = await get()
            self.put(key, value)
            return value

        def done(task: asyncio.Future) -> None:
            if self._fetching.get(key) is task:
                del self._fetching[key]
            self._tasks.discard(task)
            self._background.discard(task)
            if not task.cancelled() and (e := task.exception()):  # a refresh nobody waits for: the old value stays
                log.info("fetching %s failed: %s", key[:80], e)
        task = self._fetching[key] = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(done)
        return task

    async def drain(self, timeout_s: float = 15) -> None:
        """Let the background refreshes finish, up to `timeout_s`, then cancel the rest. A process about to exit
        (`wipebot call`, `wipebot ask`, the evals) calls it: `asyncio.run` cancelling them at shutdown hangs."""
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(list(self._tasks), timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=5)


class NoCache(Cache):
    """WIPEBOT_CACHE=off (the tests): everything is fetched."""

    def __init__(self):
        self._tasks = set()

    def get(self, key: str) -> tuple[Any, float] | None:
        return None

    def put(self, key: str, value: Any) -> None:
        pass

    async def fetch(self, key: str, max_age_s: float, get: Callable[[], Awaitable[Any]], *,
                    cancel: bool = False) -> Any:
        return await get()


_cache: Cache | None = None


def cache() -> Cache:
    global _cache
    if _cache is None:
        where = os.environ.get("WIPEBOT_CACHE") or str(DEFAULT_PATH)
        _cache = NoCache() if where == "off" else Cache(where, frozen=os.environ.get("WIPEBOT_CACHE_FROZEN") == "1")
    return _cache


def cache_key(*parts: Any) -> str:
    return json.dumps(parts, sort_keys=True, default=str)
