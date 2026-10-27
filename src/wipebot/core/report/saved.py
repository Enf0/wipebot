"""What a raid saves for a boss (a raid note, a raid plan): its versions.

The saved versions (server.saved_note, saved_plans) come as one text, each after a `[saved <epoch seconds> by <name>]`
mark (`mark`). A pull is checked against the version saved last before it began, else the first one saved after it.
"""
import re
from collections.abc import Callable

from ..text import day

FETCHED = "raidstrats.gg"  # who a plan fetched from raidstrats.gg is saved by; any other saver pasted it
MARK = re.compile(r"\[saved (\d+(?:\.\d+)?) by ([^\]]*)\]")


def mark(saved_at: float, saved_by: str) -> str:
    """The mark before a version (MARK): a `]` in the name would end it, so it's written `)`."""
    return f"[saved {saved_at:.0f} by {saved_by.replace(']', ')')}]"


def split(text: str) -> list[tuple[str, float | None, str]]:
    """(text, saved at, saved by) of each version in a paste, oldest first; without a mark, the paste as one."""
    marks = list(MARK.finditer(text))
    if not marks:
        return [(text, None, "")]
    ends = [m.start() for m in marks[1:]] + [len(text)]
    found = [(text[m.end():end], float(m[1]), m[2].strip()) for m, end in zip(marks, ends, strict=True)]
    return sorted(found, key=lambda v: v[1] or 0)


def pick[T](versions: list[T], start_s: float, saved_at: Callable[[T], float | None]) -> T:
    """The version a pull that began at `start_s` (epoch seconds) was planned with."""
    return next((v for v in reversed(versions) if (saved_at(v) or 0) <= start_s), versions[0])


def source(saved_at: float | None, saved_by: str) -> str:
    if saved_at is None:
        return ""
    return f"saved by {saved_by} {day(saved_at * 1000, with_time=True)}"
