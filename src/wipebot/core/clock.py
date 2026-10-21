""""Now" for questions about how far back to look ("the last 14 days"): the real time, or, when the cache is frozen
(the evals), the time its data was recorded, so a log uploaded since never shows up and every run asks Warcraft
Logs the same (a list of reports is cached by its start time).

Most look-backs count from the report (`Report.start_ms`, `end_ms`) and need none of this; this is for those that
count from today.
"""
import time

from .cache import cache


def now() -> float:
    """Seconds since the epoch: the time the frozen data was recorded, or the real time."""
    c = cache()
    return c.recorded_at() if c.frozen else time.time()
