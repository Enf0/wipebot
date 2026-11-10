"""The cache on disk (cache.py): fresh answers served, stale ones served while a new one is fetched."""
import asyncio
import time

import pytest

from wipebot.core.cache import Cache, Gone


@pytest.fixture
def cache():
    return Cache(":memory:")


async def test_fresh_values_come_from_the_cache(cache):
    calls = []

    async def get():
        calls.append(1)
        return {"x": len(calls)}
    assert await cache.fetch("k", 60, get) == {"x": 1}
    assert await cache.fetch("k", 60, get) == {"x": 1}
    assert len(calls) == 1


def aged(cache, key, value, age_s):
    cache.put(key, value)
    cache._db.execute("UPDATE cache SET stored_at = ? WHERE key = ?", (time.time() - age_s, key))


async def test_a_stale_value_is_served_and_replaced_in_the_background(cache):
    aged(cache, "k", "old", 100)

    async def get():
        return "new"
    assert await cache.fetch("k", 60, get) == "old"  # older than its max age, not twice it: served at once
    await cache.drain()  # the refresh runs
    assert cache.get("k")[0] == "new"


async def test_a_value_past_twice_its_max_age_is_fetched_again(cache):
    aged(cache, "k", "weeks old", 200)

    async def get():
        return "new"
    assert await cache.fetch("k", 60, get) == "new"


async def test_a_failing_source_keeps_the_old_value(cache):
    aged(cache, "k", "old", 100)
    aged(cache, "ancient", "older", 1000)

    async def get():
        raise RuntimeError("down")
    assert await cache.fetch("k", 60, get) == "old"
    await asyncio.sleep(0)
    assert cache.get("k")[0] == "old"
    assert await cache.fetch("ancient", 60, get) == "older"  # too old, but the source can't give a newer one


async def test_callers_asking_at_once_share_one_fetch(cache):
    calls = []

    async def get():
        calls.append(1)
        await asyncio.sleep(0.01)
        return "v"
    assert await asyncio.gather(*(cache.fetch("k", 60, get) for _ in range(5))) == ["v"] * 5
    assert len(calls) == 1


async def test_a_fetch_asked_to_stop_with_its_callers_stops_when_the_last_one_leaves(cache):
    started, stopped = asyncio.Event(), []

    async def get():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            stopped.append(1)
            raise
        return "v"
    first, second = (asyncio.ensure_future(cache.fetch("k", 60, get, cancel=True)) for _ in range(2))
    await started.wait()
    first.cancel()
    await asyncio.sleep(0)
    assert not stopped and "k" in cache._fetching  # the other caller still waits for it
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    await asyncio.sleep(0)
    assert stopped == [1] and "k" not in cache._fetching


async def test_a_fetch_goes_on_when_its_callers_leave_unless_asked_to_stop(cache):
    async def get():
        await asyncio.sleep(0.01)
        return "v"
    caller = asyncio.ensure_future(cache.fetch("k", 60, get))
    await asyncio.sleep(0)
    caller.cancel()
    await cache.drain()
    assert cache.get("k")[0] == "v"  # a Warcraft Logs query is paid for once sent: kept


def test_the_purge_drops_the_oldest_past_the_cap(cache, monkeypatch):
    for i in range(5):
        aged(cache, f"k{i}", str(i) * 1000, 100 - i)  # k4 the newest
    size = cache._db.execute("SELECT MAX(LENGTH(value)) FROM cache").fetchone()[0]
    monkeypatch.setenv("WIPEBOT_CACHE_MAX_MB", str(2.5 * size / 1e6))  # two entries' worth
    cache.purge()
    assert {k for (k,) in cache._db.execute("SELECT key FROM cache")} == {"k3", "k4"}


async def test_failures_are_not_cached(cache):
    async def get():
        raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        await cache.fetch("k", 60, get)
    assert cache.get("k") is None


async def test_a_frozen_cache_keeps_old_values(tmp_path):
    path = tmp_path / "cache.sqlite"
    Cache(path).put("k", "old")
    frozen = Cache(path, frozen=True)
    frozen._db.execute("UPDATE cache SET stored_at = 0")  # older than any max age, and than KEEP_DAYS
    frozen = Cache(path, frozen=True)

    async def get():
        return "new"
    assert await frozen.fetch("k", -1, get) == "old"
    await asyncio.sleep(0)
    assert (frozen.get("k") or [None])[0] == "old"
    assert await frozen.fetch("missing", 60, get) == "new"  # what's missing is fetched once and kept
    assert (frozen.get("missing") or [None])[0] == "new"


async def test_drain_lets_refreshes_finish_then_cancels_the_rest(cache):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(60)
        return "new"

    cache.put("k", "old")
    cache._db.execute("UPDATE cache SET stored_at = stored_at - 100")
    assert await cache.fetch("k", 60, slow) == "old"
    await started.wait()
    assert cache._tasks
    await cache.drain(timeout_s=0.1)
    assert not cache._tasks


async def test_what_a_source_says_is_gone_is_not_served_from_an_old_copy_and_is_dropped(cache):
    """A report made private or deleted: the source's refusal is its answer, not an outage to bridge."""
    aged(cache, '["report", "GONE000000000000", "deaths"]', "its deaths", 1000)
    aged(cache, '["wcl", "query", {"code": "GONE000000000000"}]', "its pulls", 1000)
    aged(cache, '["report", "STAYS00000000000", "deaths"]', "another report's", 1000)

    async def get():
        raise Gone("GONE000000000000")
    with pytest.raises(Gone):
        await cache.fetch('["report", "GONE000000000000", "deaths"]', 60, get)
    assert cache.get('["report", "GONE000000000000", "deaths"]') is None
    assert cache.get('["wcl", "query", {"code": "GONE000000000000"}]') is None  # everything naming it
    assert cache.get('["report", "STAYS00000000000", "deaths"]')[0] == "another report's"


async def test_a_refresh_in_the_background_that_finds_it_gone_drops_the_old_copy_too(cache):
    aged(cache, '["report", "GONE000000000000", "deaths"]', "its deaths", 100)

    async def get():
        raise Gone("GONE000000000000")
    assert await cache.fetch('["report", "GONE000000000000", "deaths"]', 60, get) == "its deaths"  # not known yet
    await asyncio.sleep(0)
    with pytest.raises(Gone):
        await cache.fetch('["report", "GONE000000000000", "deaths"]', 60, get)


async def test_a_large_value_is_decoded_and_encoded_off_the_event_loop(cache, monkeypatch):
    """One pull's events are 12 MB of JSON, 0.14 s to decode, and a night's pulls came back to back: one tool held
    the loop for a second, and every other call waited. A large value is unpacked in a thread, a small one where it
    is (a thread costs more than it saves)."""
    import threading

    from wipebot.core import cache as cache_module
    threads = []
    decode, encode = cache_module._decode, cache_module._encode
    monkeypatch.setattr(cache_module, "_decode", lambda stored: threads.append(threading.current_thread()) or
                        decode(stored))
    monkeypatch.setattr(cache_module, "_encode", lambda value: threads.append(threading.current_thread()) or
                        encode(value))
    monkeypatch.setattr(cache_module, "THREAD_BYTES", 100)
    large = [f"event {i}" for i in range(1000)]

    async def get():
        return large
    here = threading.current_thread()
    assert await cache.fetch("large", 60, get) == large and threads.pop() is not here  # stored
    assert await cache.fetch("large", 60, get) == large and threads.pop() is not here  # read back
    cache.put("small", "v")
    threads.clear()
    assert await cache.fetch("small", 60, get) == "v" and threads == [here]
    assert (await cache.read("large"))[0] == large and cache.get("large")[0] == large
