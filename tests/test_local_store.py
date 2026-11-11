"""Pushing the local cases' files to their store (tests/support/local.py): only what a checkout changed since its
fetch goes there, so one fetched a while ago doesn't put its older files over another's newer ones."""
import pytest

from tests.support import local


def copy(folder, **files):
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    return folder


def texts(folder):
    return {f: (folder / f).read_text() for f in local.hashes(folder)}


@pytest.fixture
def mine(tmp_path):
    return tmp_path / "mine"


@pytest.fixture
def store(tmp_path):
    return tmp_path / "store"


def test_a_file_another_checkout_changed_since_the_fetch_is_taken_not_overwritten(mine, store):
    was = local.hashes(copy(mine, **{"a.txt": "a", "b.txt": "b", "views/c.txt": "c"}))
    copy(store, **{"a.txt": "a", "b.txt": "theirs, newer", "views/c.txt": "c"})
    copy(mine, **{"views/c.txt": "mine, newer"})

    assert local.merge(mine, store, was) == (["views/c.txt"], ["b.txt"])
    assert texts(mine) == texts(store) == {"a.txt": "a", "b.txt": "theirs, newer", "views/c.txt": "mine, newer"}


def test_a_file_changed_on_both_sides_stops_it_before_anything_moves(mine, store):
    was = local.hashes(copy(mine, **{"a.txt": "a", "b.txt": "b"}))
    copy(store, **{"a.txt": "a", "b.txt": "theirs"})
    copy(mine, **{"a.txt": "mine", "b.txt": "mine too"})

    with pytest.raises(SystemExit, match=r"changed both here and in .* since the fetch: b\.txt\. Fetch again"):
        local.merge(mine, store, was)
    assert texts(store) == {"a.txt": "a", "b.txt": "theirs"}


def test_a_new_file_goes_or_comes_and_a_removed_one_is_removed(mine, store):
    was = local.hashes(copy(mine, **{"a.txt": "a", "gone-here.txt": "x", "gone-there.txt": "y"}))
    copy(store, **{"a.txt": "a", "gone-here.txt": "x", "new-there.txt": "theirs"})
    (mine / "gone-here.txt").unlink()
    copy(mine, **{"new-here.txt": "mine"})

    assert local.merge(mine, store, was) == (["gone-here.txt", "new-here.txt"], ["gone-there.txt", "new-there.txt"])
    assert texts(mine) == texts(store) == {"a.txt": "a", "new-here.txt": "mine", "new-there.txt": "theirs"}


def test_the_same_change_on_both_sides_is_no_conflict(mine, store):
    was = local.hashes(copy(mine, **{"a.txt": "a"}))
    copy(store, **{"a.txt": "the same new text"})
    copy(mine, **{"a.txt": "the same new text"})

    assert local.merge(mine, store, was) == ([], [])


def test_a_copy_recorded_afresh_replaces_the_store(mine, store):
    copy(mine, **{"a.txt": "mine"})
    copy(store, **{"a.txt": "theirs", "old.txt": "x"})

    assert local.merge(mine, store, None) == (["a.txt", "old.txt"], [])
    assert texts(store) == {"a.txt": "mine"}


def test_what_the_fetch_wrote_down_and_compiled_files_are_no_files_of_the_store(mine):
    copy(mine, **{"a.txt": "a", local.FETCHED: "{}", "__pycache__/x.pyc": "x"})

    assert list(local.hashes(mine)) == ["a.txt"]
