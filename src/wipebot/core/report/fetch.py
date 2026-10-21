"""What a report asks Warcraft Logs, remembered: an answer about finished pulls never changes, so each is asked
once per report (and kept on disk), and tools running side by side share one fetch."""
import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar, cast

from ..cache import FOREVER, cache, cache_key
from ..wcl import WCL

KEPT_MAX = 500  # WCL answers remembered per report (finished pulls only)
T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


class Fetched:
    """A report's WCL answers remembered: `keep` any fetch, or read its events and tables through it. What's about
    a pull still going (`live`) isn't remembered, as it changes."""

    def __init__(self, wcl: WCL, code: str):
        self.wcl, self.code = wcl, code
        self.live: set[int] = set()  # the fight ids of pulls still going
        self._kept: dict[tuple, object] = {}  # WCL answers about finished pulls, which never change
        self._fetching: dict[tuple, asyncio.Future] = {}  # what's being fetched now, by `keep` key

    def _finished(self, fight_ids: list[int]) -> bool:
        return not self.live.intersection(fight_ids)

    async def keep(self, key: tuple, fight_ids: list[int], fetch: Callable[[], Awaitable[T]],
                   persist_s: float | None = None) -> T:
        """`fetch()`, remembered for this report when every pull it covers is finished. Follow-up questions
        and whole-night tools then reuse what's been fetched. With `persist_s`, it's also kept in the cache on disk,
        fresh for that long (FOREVER for what can't change); only plain JSON values can be."""
        if key in self._kept:
            return cast(T, self._kept[key])
        # Tools run side by side, and several often need the same data: they share one fetch.
        task = self._fetching.get(key)
        if task is None:
            task = self._fetching[key] = asyncio.ensure_future(self._fetch_kept(key, fight_ids, fetch, persist_s))
            task.add_done_callback(lambda _: self._fetching.pop(key, None))
        return cast(T, await asyncio.shield(task))

    async def _fetch_kept(self, key: tuple, fight_ids: list[int], fetch: Callable[[], Awaitable[T]],
                          persist_s: float | None) -> T:
        # Whether the pulls were finished before the fetch: one that ends meanwhile was fetched half done.
        finished = self._finished(fight_ids)
        if persist_s is not None and finished:
            value = await cache().fetch(cache_key("report", self.code, *key), persist_s, fetch)
        else:
            value = await fetch()
        if finished:
            if len(self._kept) >= KEPT_MAX:
                self._kept.pop(next(iter(self._kept)))  # the oldest
            self._kept[key] = value
        return value

    async def keep_many(self, wanted: dict[K, tuple[tuple, int]],
                        fetch_many: Callable[[list[K]], Awaitable[dict[K, T]]],
                        persist_s: float | None = None) -> dict[K, T]:
        """`keep` for several values at once: {item: (its key, the pull it's about)}. The ones remembered (in
        this report, or on disk with `persist_s`) are served from there; the rest come from one
        `fetch_many(items)`, and each finished pull's value is then remembered under its key, where `keep` finds
        it too."""
        out: dict[K, T] = {}
        missing: list[K] = []
        for item, (key, fid) in wanted.items():
            if key in self._kept:
                out[item] = cast(T, self._kept[key])
            elif (persist_s is not None and self._finished([fid])
                  and (found := cache().get(cache_key("report", self.code, *key)))):
                out[item] = self._kept[key] = found[0]  # an old entry serves too, as `keep` does (it's FOREVER)
            else:
                missing.append(item)
        if missing:
            finished = {item for item in missing if self._finished([wanted[item][1]])}  # before, as in `keep`
            got = await fetch_many(missing)
            for item in missing:
                key, fid = wanted[item]
                out[item] = got[item]
                if item in finished:
                    await self._remember(key, got[item], persist_s)
        return out

    async def _remember(self, key: tuple, value: object, persist_s: float | None) -> None:
        """Keep a finished pull's value under its key, on disk too with `persist_s` (through the cache's fetch,
        so a recording or replaying cache sees the key)."""
        if persist_s is not None:
            async def ready() -> object:
                return value
            await cache().fetch(cache_key("report", self.code, *key), persist_s, ready)
        if len(self._kept) >= KEPT_MAX:
            self._kept.pop(next(iter(self._kept)))  # the oldest
        self._kept[key] = value

    @staticmethod
    def _events_key(ids: list[int], data_type: str, filters: dict) -> tuple:
        # "events2": events cached before 2026-10-13 stopped at WCL's first page (10,000 events) and are dropped
        return ("events2", tuple(ids), data_type, tuple(sorted((k, repr(v)) for k, v in filters.items())))

    async def events(self, fight_id: int | list[int], data_type: str, **filters) -> list[dict]:
        """WCL events for one pull or several in one query (each event has its `fight`)."""
        ids = fight_id if isinstance(fight_id, list) else [fight_id]
        key = self._events_key(ids, data_type, filters)
        return await self.keep(key, ids, lambda: self.wcl.events(self.code, ids, data_type, **filters), FOREVER)

    async def events_for(self, items: list[tuple[int, dict]], data_type: str, **filters) -> list[list[dict]]:
        """`events` for each of several (pull, its own filters) items, in order (a pull may come up more than
        once, e.g. a window around each death), the ones not remembered fetched in one query."""
        own = [{**filters, **extra} for _, extra in items]
        wanted = {i: (self._events_key([fid], data_type, own[i]), fid) for i, (fid, _) in enumerate(items)}

        async def fetch(missing: list[int]) -> dict[int, list[dict]]:
            got = await self.wcl.events_many(self.code, [(items[i][0], items[i][1]) for i in missing], data_type,
                                             **filters)
            return dict(zip(missing, got, strict=True))
        found = await self.keep_many(wanted, fetch, FOREVER)
        return [found[i] for i in range(len(items))]

    @staticmethod
    def _table_key(ids: list[int], data_type: str, filters: dict) -> tuple:
        return ("table", tuple(ids), data_type, tuple(sorted((k, repr(v)) for k, v in filters.items())))

    async def table(self, fight_id: int | list[int], data_type: str, **filters) -> dict:
        """A WCL table for one pull, or summed over several."""
        ids = fight_id if isinstance(fight_id, list) else [fight_id]
        key = self._table_key(ids, data_type, filters)
        return await self.keep(key, ids, lambda: self.wcl.table(self.code, ids, data_type, **filters), FOREVER)

    async def tables(self, items: list[tuple[int, dict]], data_type: str, **filters) -> list[dict]:
        """`table` for each of several (pull, its own filters) items, in order, the ones not remembered fetched
        in one query (a whole-night tool's forty tables in a second rather than one at a time)."""
        own = [{**filters, **extra} for _, extra in items]
        wanted = {i: (self._table_key([fid], data_type, own[i]), fid) for i, (fid, _) in enumerate(items)}

        async def fetch(missing: list[int]) -> dict[int, dict]:
            got = await self.wcl.tables(self.code, [(f"i{i}", items[i][0], items[i][1]) for i in missing],
                                        data_type, **filters)
            return {i: got[f"i{i}"] for i in missing}
        found = await self.keep_many(wanted, fetch, FOREVER)
        return [found[i] for i in range(len(items))]
