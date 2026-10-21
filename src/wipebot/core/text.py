"""How the tools write times, dates, numbers and lists: one way each, for every tool."""
import functools
import re
import unicodedata
from datetime import UTC, datetime


def clock(ms: float) -> str:
    """Milliseconds -> 'm:ss', the time format of every tool."""
    s = int(max(ms, 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def parse_clock(text: str) -> float | None:
    """'2:31' or '151' (seconds) -> milliseconds; None for anything else."""
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*", text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) * 1000 if m else None


def day(ms: float, with_time: bool = False) -> str:
    """'Mon 5 Oct', or 'Mon 5 Oct 19:30 UTC': a moment this season."""
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return f"{d:%a} {d.day} {d:%b}" + (f" {d:%H:%M} UTC" if with_time else "")


def date(ms: float, with_time: bool = False) -> str:
    """'Mon 2026-10-12', or 'Mon 2026-10-12 19:30 UTC': a raid night in a list of them."""
    return f"{datetime.fromtimestamp(ms / 1000, UTC):%a %Y-%m-%d}" + (
        f" {datetime.fromtimestamp(ms / 1000, UTC):%H:%M} UTC" if with_time else "")


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
