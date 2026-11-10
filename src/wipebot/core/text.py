"""How the tools write times, dates, numbers and lists: one way each, for every tool."""
import functools
import re
import unicodedata
from datetime import UTC, datetime


def clock(ms: float) -> str:
    """Milliseconds -> 'm:ss', the time format of every tool."""
    s = int(max(ms, 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def span(ms: float) -> str:
    """How long a night or a boss's pulls took together: 'm:ss' as a pull's clock under an hour, 'h:mm:ss' from an
    hour ('2:54:43', not '174:43', which reads as a time in a pull)."""
    s = int(max(ms, 0) // 1000)
    return clock(ms) if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_clock(text: str) -> float | None:
    """'2:31' or '151' (seconds) -> milliseconds; None for anything else."""
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*", text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) * 1000 if m else None


EVENING_BEFORE_H = 10  # before this hour UTC it is still the evening before somewhere in the Americas


def _time_utc(d: datetime, with_time: bool | None) -> str:
    """' 19:30 UTC' for a moment, when asked for or, left open (None), when its date alone can mislead. Dates are
    UTC: no report says where its raiders are (Warcraft Logs' US region holds the Oceanic realms too, so a region
    is no time zone). An evening raid in the Americas starts after midnight UTC, and its UTC weekday alone would read
    a day late there; with the time and "UTC" beside it the reader can tell."""
    if with_time is None:
        with_time = d.hour < EVENING_BEFORE_H
    return f" {d:%H:%M} UTC" if with_time else ""


def day(ms: float, with_time: bool | None = None) -> str:
    """'Mon 5 Oct', or 'Mon 5 Oct 19:30 UTC': a moment this season. The time is there when asked for, and unasked
    when the date alone can mislead (`_time_utc`); never with `with_time` False."""
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return f"{d:%a} {d.day} {d:%b}" + _time_utc(d, with_time)


def date(ms: float, with_time: bool | None = None) -> str:
    """'Mon 2026-10-12', or 'Mon 2026-10-12 19:30 UTC': a raid night in a list of them. The time as in `day`."""
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return f"{d:%a %Y-%m-%d}" + _time_utc(d, with_time)


def fight_url(code: str, fight_id: int) -> str:
    """A pull on Warcraft Logs, by its report's code and its fight id."""
    return f"https://www.warcraftlogs.com/reports/{code}#fight={fight_id}"


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def short(n: float) -> str:
    """1234567 -> '1.23M'."""
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            return f"{n / div:.2f}{unit}"
    return f"{n:.0f}"


def plural(k: int, what: str) -> str:
    """'1 pull', '3 pulls'."""
    return f"{k} {what}" + ("" if k == 1 else "s")


def ranges(nos: list[int]) -> str:
    """[1, 2, 3, 5] -> "1-3, 5"."""
    out: list[str] = []
    start = prev = nos[0]
    for n in nos[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if prev > start else str(start))
        if n is not None:
            start = prev = n
    return ", ".join(out)


def few(names: list[str], shown: int = 4) -> str:
    """'A, B, C, D +2', or 'nobody'."""
    if not names:
        return "nobody"
    return ", ".join(names[:shown]) + (f" +{len(names) - shown}" if len(names) > shown else "")


def ids_line(ids: dict[str, int]) -> str:
    """'Ability ids: Tempest [1287083], ...', for spell lookups."""
    return "Ability ids: " + ", ".join(f"{n} [{i}]" for n, i in ids.items() if i and i > 10)


def clusters(times: list[float], gap_ms: float = 10_000) -> str:
    """Pull times grouped where they bunch up: '0:39-0:40 (3 pulls), 1:17-1:25 (11 pulls)'."""
    groups: list[list[float]] = []
    for t in sorted(times):
        if groups and t - groups[-1][-1] <= gap_ms:
            groups[-1].append(t)
        else:
            groups.append([t])
    return ", ".join(f"{clock(g[0])}{'-' + clock(g[-1]) if clock(g[-1]) != clock(g[0]) else ''} "
                     f"({plural(len(g), 'pull')})" for g in groups)


def fold(s: str) -> str:
    """A name without its accents, for matching: 'Mírëlle' -> 'Mirelle'."""
    s = s.replace("ð", "d").replace("ø", "o").replace("æ", "ae")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


@functools.lru_cache(maxsize=65536)
def name_key(name: str) -> str:
    """How a typed name is matched against the log's: no case, no accents, no outer spaces."""
    return fold(name.strip().lower())
