"""clock.now(): the real time, or for frozen data (the evals) the time it was recorded, which never moves."""
import time

from wipebot.core import cache, clock
from wipebot.core.cache import Cache


def test_frozen_data_is_as_of_the_time_given_first_else_its_oldest_entry_else_now_for_good(tmp_path):
    path = tmp_path / "cache.sqlite"
    frozen = Cache(path, frozen=True)
    frozen._db.execute("INSERT INTO cache VALUES ('old', 1000.0, x'00'), ('newer', 2000.0, x'00')")
    assert frozen.recorded_at() == 1000.0
    frozen.put("added later", 1)  # data missing from the frozen file is fetched and kept: "now" stays
    frozen._db.execute("DELETE FROM cache WHERE key = 'old'")
    assert Cache(path, frozen=True).recorded_at(first=9000.0) == 1000.0  # nor does a later run's time move it

    given = Cache(tmp_path / "given.sqlite", frozen=True)
    given._db.execute("INSERT INTO cache VALUES ('old', 1000.0, x'00')")
    assert given.recorded_at(first=5000.0) == 5000.0
    assert abs(Cache(":memory:", frozen=True).recorded_at() - time.time()) < 5  # new: as of now


def test_now_is_pinned_only_when_frozen(monkeypatch):
    frozen = Cache(":memory:", frozen=True)
    frozen._db.execute("INSERT INTO cache VALUES ('k', 1000.0, x'00')")
    monkeypatch.setattr(cache, "_cache", frozen)
    assert clock.now() == 1000.0
    monkeypatch.setattr(cache, "_cache", Cache(":memory:"))
    assert abs(clock.now() - time.time()) < 5
    monkeypatch.setattr(cache, "_cache", cache.NoCache())  # the tests' WIPEBOT_CACHE=off
    assert abs(clock.now() - time.time()) < 5
