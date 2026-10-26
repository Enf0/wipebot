"""Fetching the evals' frozen data from the maintainers' store (evals/data.py), with the download faked."""
import sqlite3
from pathlib import Path

import pytest

from evals import data


def _cache(path: Path, answers: int, stored_at: float = 0) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
    db.executemany("INSERT OR REPLACE INTO cache VALUES (?, ?, x'00')", [(str(i), stored_at) for i in range(answers)])
    db.commit()
    db.close()
    return path


def _answers(path: Path) -> int:
    """How many answers the cache at `path` holds, the file closed after (Windows can't replace an open one)."""
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    finally:
        db.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The private store holding a packed cache of 3 answers, the evals' cache at a path of the test's own."""
    packed = tmp_path / "store" / data.ASSET
    packed.parent.mkdir()
    data.pack(_cache(tmp_path / "theirs.sqlite", 3), packed)
    asked = []

    def download(repo, tag, asset, dest):
        asked.append((repo, tag, asset))
        dest.write_bytes(packed.read_bytes())
        return ""
    monkeypatch.setattr(data, "download", download)
    monkeypatch.setattr(data, "CACHE", tmp_path / "cache.sqlite")
    return asked


def test_fetch_puts_the_stored_cache_in_place(store, capsys):
    data.fetch()
    assert _answers(data.CACHE) == 3
    assert store == [(data.REPO, data.TAG, data.ASSET)] and "fetched 3 answers" in capsys.readouterr().out


def test_fetch_leaves_a_cache_already_here_unless_told_to_replace_it(store):
    _cache(data.CACHE, 1)
    with pytest.raises(SystemExit, match="--replace"):
        data.fetch()
    assert _answers(data.CACHE) == 1
    data.fetch(replace=True)
    assert _answers(data.CACHE) == 3


def test_without_access_fetch_says_how_a_run_gets_the_data_instead(store, monkeypatch):
    monkeypatch.setattr(data, "download", lambda *a: "HTTP 404: Not Found\n")
    with pytest.raises(SystemExit, match="HTTP 404(.|\n)*first run"):
        data.fetch()
    assert not data.CACHE.exists()


def test_an_asset_that_isnt_the_cache_is_not_put_in_place(tmp_path):
    bad = tmp_path / data.ASSET
    from compression import zstd
    bad.write_bytes(zstd.compress(b"not a database at all" * 100))
    with pytest.raises(SystemExit, match="isn't the evals' cache"):
        data.unpack(bad, tmp_path / "cache.sqlite")
    assert list(tmp_path.iterdir()) == [bad]


def test_a_fetched_copy_pushes_only_what_a_run_added_since(store, tmp_path, monkeypatch):
    data.fetch()
    pushed = []

    def commit(folder, put, message):
        put(tmp_path / "repo" / folder)
        pushed.append((folder, message))
        return True
    monkeypatch.setattr(data, "commit", commit)
    data.push()
    assert pushed == []  # nothing new since the fetch
    _cache(data.CACHE, 5, stored_at=1e10)  # a run fetched 5 answers, 2 of them new keys (3, 4)
    data.push()
    assert pushed == [(data.DELTAS, "chore: 5 new eval answers, for the workflow to merge into the eval-data release")]
    [archive] = (tmp_path / "repo" / data.DELTAS).iterdir()
    assert data.unpack(archive, tmp_path / "delta.sqlite") == 5
    data.push()
    assert len(pushed) == 1  # pushed once, marked as in the store
