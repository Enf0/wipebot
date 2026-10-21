"""Fetching the evals' frozen data from the maintainers' store (evals/data.py), with `gh` faked."""
import sqlite3
import subprocess
from pathlib import Path

import pytest

from evals import data


def _cache(path: Path, answers: int) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
    db.executemany("INSERT INTO cache VALUES (?, 0, x'00')", [(str(i),) for i in range(answers)])
    db.commit()
    db.close()
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The private store holding a packed cache of 3 answers, the evals' cache at a path of the test's own."""
    packed = tmp_path / "store" / data.ASSET
    packed.parent.mkdir()
    data.pack(_cache(tmp_path / "theirs.sqlite", 3), packed)
    asked = []

    def gh(*args):
        asked.append(args)
        if args[:2] == ("release", "download"):
            to = Path(args[args.index("--dir") + 1])
            (to / data.ASSET).write_bytes(packed.read_bytes())
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(data, "_gh", gh)
    monkeypatch.setattr(data, "CACHE", tmp_path / "cache.sqlite")
    return asked


def test_fetch_puts_the_stored_cache_in_place(store, capsys):
    data.fetch()
    assert sqlite3.connect(data.CACHE).execute("SELECT COUNT(*) FROM cache").fetchone() == (3,)
    assert store[0][:3] == ("release", "download", data.TAG) and "fetched 3 answers" in capsys.readouterr().out


def test_fetch_leaves_a_cache_already_here_unless_told_to_replace_it(store):
    _cache(data.CACHE, 1)
    with pytest.raises(SystemExit, match="--replace"):
        data.fetch()
    assert sqlite3.connect(data.CACHE).execute("SELECT COUNT(*) FROM cache").fetchone() == (1,)
    data.fetch(replace=True)
    assert sqlite3.connect(data.CACHE).execute("SELECT COUNT(*) FROM cache").fetchone() == (3,)


def test_without_access_fetch_says_how_a_run_gets_the_data_instead(store, monkeypatch):
    monkeypatch.setattr(data, "_gh", lambda *a: subprocess.CompletedProcess(a, 1, "", "HTTP 404: Not Found\n"))
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
