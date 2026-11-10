"""What a report asks Warcraft Logs (report/fetch.py): batched into one query where it can be, and remembered
when it's about finished pulls."""
import asyncio
import threading
import time
from typing import cast

import pytest

from tests.support import CODE, FakeWCL, Night
from wipebot.core import cache as cache_module
from wipebot.core import server
from wipebot.core.cache import FOREVER, Cache, cache_key
from wipebot.core.report import coaching
from wipebot.core.report import fetch as fetch_module
from wipebot.core.report.fetch import Fetched
from wipebot.core.wcl import WCL, WCLError


async def test_events_for_several_items_come_in_one_query_a_pull_twice_included(rep):
    wcl = cast(FakeWCL, rep.wcl)
    f13, f14 = rep.fight(13, "Sszorak"), rep.fight(14, "Sszorak")
    wcl.queries.clear()
    got = await rep.events_for([(f13["id"], {"endTime": 1}), (f14["id"], {}), (f13["id"], {"endTime": 2})], "Deaths")
    assert wcl.queries[0] == "events_many Deaths" and "events_many" not in wcl.queries[1:]  # one query
    assert [len(g) for g in got] == [len(await rep.deaths(f13)), len(await rep.deaths(f14)), len(await rep.deaths(f13))]
    wcl.queries.clear()
    assert await rep.events(f14["id"], "Deaths") == got[1]  # remembered under the single-pull key
    assert wcl.queries == []


async def test_tools_side_by_side_share_a_batched_fetch_already_going():
    """Two tools asking for the same pulls' data at once (players on one pull): one query, not one each."""
    fetched = Fetched(cast(WCL, None), CODE)
    asked: list[list[int]] = []

    async def fetch_many(items: list[int]) -> dict[int, str]:
        asked.append(items)
        await asyncio.sleep(0.01)
        return {i: f"v{i}" for i in items}

    async def fetch_one() -> str:
        asked.append([-1])
        return "one"
    wanted = {1: (("k", 1), 1), 2: (("k", 2), 2), 3: (("k", 1), 1)}  # item 3 is item 1's key again
    a, b, c = await asyncio.gather(fetched.keep_many(wanted, fetch_many), fetched.keep_many(wanted, fetch_many),
                                   fetched.keep(("k", 2), [2], fetch_one))
    assert asked == [[1, 2]] and a == b == {1: "v1", 2: "v2", 3: "v1"} and c == "v2"


async def test_a_read_cut_off_in_one_call_ends_no_other_call_waiting_for_it():
    """A call out of time, or left by its client, is cancelled in the middle of a batched read that another call
    waits for too: that one is told to ask again. It was cancelled with it, and ended with no answer at all."""
    fetched = Fetched(cast(WCL, None), CODE)

    async def fetch_many(items: list[int]) -> dict[int, str]:
        await asyncio.sleep(10)
        return {i: f"v{i}" for i in items}
    wanted = {1: (("k", 1), 1)}
    first = asyncio.ensure_future(fetched.keep_many(wanted, fetch_many))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(fetched.keep_many(wanted, fetch_many))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(WCLError, match="was cut off: ask again"):
        await second
    assert first.cancelled() or isinstance(first.exception(), asyncio.CancelledError)

    async def at_once(items: list[int]) -> dict[int, str]:
        return {i: f"v{i}" for i in items}
    assert await fetched.keep_many(wanted, at_once) == {1: "v1"}  # and asking again reads it


async def test_a_call_cut_off_reading_a_kept_value_leaves_nobody_waiting_for_what_it_set_out_to_fetch(monkeypatch):
    """A call is cut off while a pull's large value comes off disk, after it set out to fetch an earlier pull's:
    the next call for that pull fetches it. It waited for the call that was gone, and so did every call after."""
    unpacked, decode = threading.Event(), cache_module._decode
    monkeypatch.setattr(cache_module, "_decode", lambda stored: unpacked.wait(5) and decode(stored))
    monkeypatch.setattr(cache_module, "THREAD_BYTES", 100)
    kept = Cache(":memory:")
    kept.put(cache_key("report", CODE, "k", 2), [f"event {i}" for i in range(1000)])  # large: unpacked in a thread
    monkeypatch.setattr(fetch_module, "cache", lambda: kept)
    fetched = Fetched(cast(WCL, None), CODE)
    asked: list[list[int]] = []

    async def fetch_many(items: list[int]) -> dict[int, str]:
        asked.append(items)
        return {i: f"v{i}" for i in items}
    wanted = {1: (("k", 1), 1), 2: (("k", 2), 2)}
    first = asyncio.ensure_future(fetched.keep_many(wanted, fetch_many, FOREVER))
    await asyncio.sleep(0)  # pull 1 is to be fetched, pull 2's value is in the cache's thread
    first.cancel()
    unpacked.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert asked == []  # cut off before it fetched anything
    assert await asyncio.wait_for(fetched.keep_many({1: wanted[1]}, fetch_many, FOREVER), 1) == {1: "v1"}
    await kept.drain()


async def test_a_call_cut_off_keeping_what_it_fetched_hands_it_to_the_call_waiting_for_it(monkeypatch):
    """A call is cut off between fetching a night's pulls and keeping the first of them on disk: a call waiting for
    a later pull of that fetch gets its value. Nothing was handed to it, and it waited for ever."""
    kept = Cache(":memory:")
    monkeypatch.setattr(fetch_module, "cache", lambda: kept)
    fetched = Fetched(cast(WCL, None), CODE)
    asked: list[list[int]] = []

    async def fetch_many(items: list[int]) -> dict[int, str]:
        asked.append(items)
        return {i: f"v{i}" for i in items}
    wanted = {1: (("k", 1), 1), 2: (("k", 2), 2)}
    first = asyncio.ensure_future(fetched.keep_many(wanted, fetch_many, FOREVER))
    second = asyncio.ensure_future(fetched.keep_many({2: wanted[2]}, fetch_many, FOREVER))
    await asyncio.sleep(0)  # the first has fetched both and is keeping pull 1; the second waits for pull 2
    first.cancel()
    assert await asyncio.wait_for(second, 1) == {2: "v2"} and asked == [[1, 2]]
    with pytest.raises(asyncio.CancelledError):
        await first
    await kept.drain()


async def test_a_nights_tables_and_details_come_in_one_query_each_and_are_remembered(rep):
    wcl = cast(FakeWCL, rep.wcl)
    wcl.queries.clear()
    await coaching.player_night(rep, "Enfo")
    assert wcl.queries.count("tables Healing") == 1 and wcl.queries.count("player_details_many") == 1
    assert "tables DamageDone" not in wcl.queries  # a healer: no damage tables
    wcl.queries.clear()
    fight = rep.fight(13, "Sszorak")
    await rep.table(fight["id"], "Healing")  # one pull of it: remembered from the night's query
    await rep.details(fight)
    await coaching.player_night(rep, "Enfo")
    assert wcl.queries == []


async def test_a_pull_in_progress_is_not_remembered():
    night = Night()  # changed below: not the session's
    live = {**night.fights[-1], "id": 999, "inProgress": True}
    night.fights.append(live)
    night.deaths[999] = []
    rep = await server.Library(wcl=FakeWCL(night, time.time() * 1000).client()).report(CODE)
    assert await rep.deaths(live) == []
    night.deaths[999] = night.deaths[night.fights[-2]["id"]]
    assert await rep.deaths(live) == night.deaths[999]  # read again: the pull is still going


async def test_a_pull_that_ends_during_its_fetch_is_not_remembered():
    fetched = Fetched(cast(WCL, None), CODE)
    fetched.live = {7}
    calls = []

    async def fetch():
        calls.append(1)
        fetched.live = set()  # the pull ended meanwhile: the report was read again
        return "half done"
    assert await fetched.keep(("k",), [7], fetch) == "half done"
    assert await fetched.keep(("k",), [7], fetch) == "half done"
    assert len(calls) == 2  # fetched again, now that the pull is finished
