"""Checks on an answer that code can make: numbers no tool gave (the model did its own arithmetic, or made one up),
and a site credited for a fact. The evals warn about them, and they can flag real answers."""
import re

# Times (2:32), and amounts written with a decimal, a percent sign, a unit (377k, 2.7M) or from 100 up. Plain whole
# numbers below 100 are pull numbers and counts read off a list: left out. The decimal may be a comma (Norwegian:
# 187,6k, 1,63M, 40 %) when it isn't a thousands separator (1,500).
TIME = re.compile(r"(?<![\d:.])(\d{1,2}):([0-5]\d)(?!\d|:\d)")  # "2:42: on 18" is a time; 1:02:03 isn't
NUMBER = re.compile(r"(?<![\d.,:/=\[A-Za-wyz])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+)|,(\d{1,2})(?!\d))?"
                    r"(\s?%|[kMB](?![a-z])|st|nd|rd|th|x|s)?(?![\w:]|[.,]\d)")
RANGE = re.compile(r"(\d+(?:[.,]\d+)?)(\s*[–-]\s*\d+(?:[.,]\d+)?)([kMB%])")  # 570–710k: both in thousands
LINK = re.compile(r"\]\([^)]*\)|https?://\S+|\[\d+\]")  # link targets and spell ids carry their own numbers
SPELL_ID = re.compile(r"\[(\d+)\]")  # a tool's "Viscous Cyst [1287205]": an answer may give the id bare
# 21.09 is a day, not an amount
DATE = re.compile(r"(?<![\d.])(0?[1-9]|[12]\d|3[01])\.(0[1-9]|1[0-2])(?!\d|\.\d|%|[kMB])")
SCALE = {"k": 1e3, "M": 1e6, "B": 1e9}
CREDIT = re.compile(r"according to|\bguides? (says?|recommends?|warns?|calls?)|"
                    r"\b(wowhead|method|icy veins|lorrgs)('s)?( \w+)? (says|notes|recommends|lists|warns)", re.I)


def times(text: str) -> set[int]:
    return {int(m) * 60 + int(s) for m, s in TIME.findall(LINK.sub(" ", text))}


def _slack(t: int) -> int:
    """How far a time in an answer may be from the source's: a second, or ten for one on a round ten seconds
    ("after 4:30" for 4:34, "a 2:30 pull" for 2:32), as 594k is 594,000 give or take 1,000."""
    return 10 if t % 10 == 0 else 1


def amounts(text: str, every: bool = False) -> list[tuple[str, float, float]]:
    """(as written, value, what its last digit is worth) for each amount worth checking: 594k is 594,000 give or
    take 1,000; 570k in a range is 570,000 give or take 10,000. With `every`, the small whole numbers too (a
    source's 68 answers for an answer's 68%)."""
    out = []
    # Links go first (their paths are full of numbers); then 72/68 (health at a phase, ours and the kill's) is two
    # amounts.
    text = LINK.sub(" ", text).replace("/", " ")
    for m in NUMBER.finditer(RANGE.sub(r"\1\3\2\3", DATE.sub(" ", TIME.sub(" ", text)))):
        whole, frac, unit = m[1].replace(",", ""), m[2] or m[3] or "", (m[4] or "").strip()
        value, scale = float(f"{whole}.{frac}" if frac else whole), SCALE.get(unit, 1)
        if every or frac or unit in SCALE or unit == "%" or value >= 100:
            step = 10 ** -len(frac) if frac else 10 ** (len(whole) - len(whole.rstrip("0")) if whole != "0" else 0)
            out.append((m[0], value * scale, step * scale))
    return out


def unsourced(answer: str, sources: list[str]) -> list[str]:
    """The times and amounts in `answer` that no source has: a time within a second, an amount within what its
    last digit is worth (62.6% for 62.59%, 594k for 594.56k, 0.8M for 879.77k)."""
    sources = [SPELL_ID.sub(r" \1 ", t) for t in sources]
    known_times = set().union(*(times(t) for t in sources))
    known = {value for t in sources for _, value, _ in amounts(t, every=True)} | known_times  # 6:35 is also 395 s
    missing = [f"{t // 60}:{t % 60:02}" for t in sorted(times(answer))
               if not any(abs(t - k) <= _slack(t) for k in known)]  # 4:34 is also 274 s
    missing += [text for text, value, step in amounts(answer) if not any(abs(value - k) < step for k in known)]
    return list(dict.fromkeys(missing))


# Numbers no tool gave stay a warning, not a fail: measured 15 October on the evals' 360 baseline answers
# and a 186-answer run with every turn's tool calls kept, almost every flag was a right number. Of the 18 flagged
# answers whose sources were all kept, none stated a wrong one: 16 held the model's own correct arithmetic on tool
# numbers (a sum, a difference, a share, a per-add average, a projection such as 5:28 at 90% pace = 6:05), one a
# cutoff it chose for a table ("deaths before 9:45"), one a time a tool gave as a list label. A fail would be right
# in about 1 of 18 (6%), against the 90% it would need. The mechanical misses (follow-ups' earlier calls, a time on a
# round ten seconds, a bare spell id, "2:42:" as a label) are fixed; the arithmetic can't be told from a made-up
# number without checking the arithmetic itself.
def warnings(answer: str, sources: list[str]) -> list[str]:
    """What's worth a look but can be right: the model's own sum, or a guide named because it matters."""
    out = []
    if missing := unsourced(answer, sources):
        out.append("numbers no tool gave: " + ", ".join(missing))
    if m := CREDIT.search(answer):
        out.append(f'credits a source: "{m[0]}"')
    return out
