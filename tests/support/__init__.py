"""Shared by the tests of every layer (tests/README.md says how the suite works):
- paths.py: where the data is; what's copied from real reports sits under fixtures/real.
- night.py: the captured night and a fake Warcraft Logs serving it.
- recorded.py: Warcraft Logs' and the guides' recorded answers, for the tool tests.
- standins.py: what stands in for the guide sites' writing and Warcraft Logs' images in the recordings.
- local.py: the local cases' recordings and snapshots, fetched and pushed (`just local-fixtures`).
- `assert_snapshot`: an output against the file that pins it.
- defensives.py: not a test, a script to run after a patch: which defensives the death recap can't read.
Fixtures are in the conftest.py files."""
import os
from pathlib import Path

from .night import RESTO_SPELLS, FakeWCL, Night
from .paths import CODE, FIXTURES, NOTE, PLAN, REAL

__all__ = ["CODE", "FIXTURES", "NOTE", "PLAN", "REAL", "RESTO_SPELLS", "FakeWCL", "Night", "assert_snapshot"]


def assert_snapshot(path: Path, actual: str) -> None:
    """`actual` is what the snapshot at `path` holds. `just snapshot` (UPDATE_SNAPSHOTS) rewrites the file instead,
    for review in `git diff`; a missing file is written and fails, so a new one is read before it's kept."""
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        new = not path.exists()
        path.write_text(actual, encoding="utf-8")
        assert not new, f"wrote {path.name}, a new snapshot: read it, then run the test again"
    assert actual == path.read_text(encoding="utf-8"), f"{path.name}: after an intended change, `just snapshot`"
