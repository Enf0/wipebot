"""What a raid saves for a boss, in versions (report/saved.py): each pull is checked against the one it began with."""
from wipebot.core.report import saved


def test_a_paste_splits_into_its_versions_oldest_first():
    assert saved.split("no marks") == [("no marks", None, "")]
    found = saved.split("[saved 2000 by Mirelle] two\n[saved 1000.5 by  Torvek ] one")
    assert found == [(" one", 1000.5, "Torvek"), (" two\n", 2000.0, "Mirelle")]


def test_a_mark_reads_back_as_written():
    assert saved.split(saved.mark(1000.4, "Ha]lvard") + " one") == [(" one", 1000.0, "Ha)lvard")]


def test_each_pull_gets_the_version_saved_last_before_it_began():
    versions = [(1000.0, "Torvek"), (2000.0, "Mirelle")]

    def pick(start_s: float) -> str:
        return saved.pick(versions, start_s, lambda v: v[0])[1]
    assert (pick(1500), pick(2500), pick(500)) == ("Torvek", "Mirelle", "Torvek")  # saved after: the first one
    assert saved.pick([(None, "pasted")], 9e9, lambda v: v[0])[1] == "pasted"
