"""Where the tests' data is. Everything copied from Warcraft Logs or the maintainers' guild (reports, notes, plans,
recorded answers and the tool outputs over them) is under REAL; the rest of FIXTURES is guide-page excerpts."""
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures"
REAL = FIXTURES / "real"
CODE = "KBYT4xXn7VNjDgh8"  # the captured night (night.py): Vashnik killed, then 33 Sszorak pulls
NOTE = REAL / "notes" / "sszorak-2026-10-12.txt"  # the raid's Sszorak note on 12 October (NSRT's format)
PLAN = REAL / "plans" / "sszorak-2026-10-14.txt"  # its Sszorak spread plan from raidstrats.gg, as of 14 October
