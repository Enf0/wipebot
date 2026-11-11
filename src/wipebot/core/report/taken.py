"""The raid's damage taken over a pull, second by second as the DamageTaken table gives it (`raid_taken`): what
healing's spikes, pull_timeline's raid damage line and the pull card's chart read, so one moment has one number in
each."""
import itertools
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..cache import FOREVER
from .base import Report

log = logging.getLogger(__name__)

# Warcraft Logs' graph has 240 steps over whatever window it is asked for, and each of its points is the damage over
# the BOX steps around it (BOX_BEFORE before it, its own and 4 after) divided by their length, none counted outside
# the window: over the whole of an 8-minute pull, some 20 s, which flattened its bursts. So a point less the one
# before it is the step entering its box less the one leaving, and with the damage of a window's first BOX_BEFORE
# steps known each step's own damage follows exactly (`unbox`; verified to the unit on over a hundred graphs,
# docs/reference/wcl-api.md). A pull is asked in windows of 240 steps of a second, each from BOX_BEFORE steps before
# the end of the window before it (known), the first from twice as long before the pull's start (no damage of the
# pull yet; some fights count a hit in the seconds before it, which is unboxed too): exact to the second, a point for
# each 235 s of pull.
GRAPH_STEPS = 240
TAKEN_STEP_MS = 1_000
TAKEN_WINDOW_MS = GRAPH_STEPS * TAKEN_STEP_MS
BOX, BOX_BEFORE = 10, 5
TAKEN_LEAD_MS = BOX_BEFORE * TAKEN_STEP_MS
TAKEN_FIRST_MS = 2 * TAKEN_LEAD_MS


@dataclass
class Taken:
    """The raid's damage taken over a pull, per second: one value a `step` ms from `start` (report ms), each for its
    step."""
    start: float
    step: float
    data: list[float]
    _before: list[float] = field(init=False, repr=False)  # the damage taken before each step

    def __post_init__(self):
        self._before = [0.0, *itertools.accumulate(v * self.step / 1000 for v in self.data)]

    def damage(self, a: float, b: float) -> float:
        """The damage taken from `a` to `b` (report ms); a step either crosses counts for the time inside."""
        return self._until(b) - self._until(a)

    def rate(self, a: float, b: float) -> float:
        """The damage taken per second from `a` to `b`."""
        return self.damage(a, b) * 1000 / (b - a) if b > a else 0.0

    def _until(self, t: float) -> float:
        if not self.data:
            return 0.0
        x = min(max((t - self.start) / self.step, 0.0), len(self.data))
        i = min(int(x), len(self.data) - 1)
        return self._before[i] + (x - i) * self.data[i] * self.step / 1000


def windows(f: dict) -> list[tuple[float, float]]:
    """A pull's graph windows as (start, end), report ms: each TAKEN_WINDOW_MS long, the first from TAKEN_FIRST_MS
    before the pull's start and each next from TAKEN_LEAD_MS before the end of the one before, the last reaching its
    end."""
    out, a = [], f["startTime"] - TAKEN_FIRST_MS
    while True:
        out.append((a, a + TAKEN_WINDOW_MS))
        if a + TAKEN_WINDOW_MS >= f["endTime"]:
            return out
        a += TAKEN_WINDOW_MS - TAKEN_LEAD_MS


def unbox(points: Sequence[float], first: Sequence[float] | None = None) -> list[float] | None:
    """A graph window's damage in each of its steps (TAKEN_STEP_MS), from its points and the damage in its first
    BOX_BEFORE steps (`first`; without it, a pull's first window: none, but where its points say otherwise). None when
    the points aren't such boxes: those steps or the ones past its last point (nothing after the window or the pull's
    end, where Warcraft Logs stops a window) don't come out as they are, or a step comes out below none."""
    box = [v * BOX * TAKEN_STEP_MS / 1000 for v in points]  # the damage in each point's box
    n, after = len(box) - 1, BOX - BOX_BEFORE - 1
    d = [*(first or [0.0] * BOX_BEFORE), *[0.0] * (n + BOX)]
    for j in range(1, n + 1):  # point j's box gains step j + after and loses step j - BOX_BEFORE - 1
        d[j + after] = box[j] - box[j - 1] + (d[j - BOX_BEFORE - 1] if j > BOX_BEFORE else 0.0)
    off = 1 + 1e-6 * max(box, default=0.0)  # what the points' rounding leaves
    if first is None:
        # Each of the first steps begins every BOX-th step after it, and the steps past the last point have none:
        # those that reach there come out, and the first point's box holds the rest (one, or none of them).
        u = {k % BOX: -d[k] for k in range(n, n + after + 1) if k % BOX < BOX_BEFORE}
        rest, left = [r for r in range(BOX_BEFORE) if r not in u], box[0] - sum(u.values())
        if len(rest) == 1:
            u[rest[0]] = left
        elif abs(left) > off:
            return None
        for k in range(n + BOX):
            d[k] += u.get(k % BOX, 0.0)
    if (abs(sum(d[:BOX_BEFORE]) - box[0]) > off or any(abs(v) > off for v in d[n:n + after + 1])
            or min(d[:n], default=0.0) < -off):
        return None
    return [max(v, 0.0) for v in d[:n]]


def stitch(f: dict, graphs: Sequence[dict | None]) -> dict:
    """A pull's windows' graphs (in `windows`' order) as its damage taken per second in each TAKEN_STEP_MS from its
    start (the last, cut short by the pull's end, per second of its part in the pull), each window unboxed from the
    steps before it: {"pointStart", "pointInterval", "data"}, with no data when no window had a Total series. A window
    that isn't of TAKEN_STEP_MS steps from where it was asked, or whose points aren't boxes, is read as its points
    say."""
    t0, end = f["startTime"], f["endTime"]
    steps: list[float] = []  # the damage in each step from the first window's start
    found = False
    for (a, b), graph in zip(windows(f), graphs, strict=True):
        at = round((a - t0 + TAKEN_FIRST_MS) / TAKEN_STEP_MS)
        total = next((s for s in (graph or {}).get("series") or [] if s.get("name") == "Total"), None)
        got: list[float] | None = []  # no points: nothing taken in it
        if total and total.get("data"):
            found = True
            if total.get("pointStart") == a and total.get("pointInterval") == TAKEN_STEP_MS:
                got = unbox(total["data"], steps[at:at + BOX_BEFORE] if at else None)
            else:
                got = None
            if got is None:
                log.warning("fight %s's damage taken graph over %s-%s doesn't unbox: read as its points", f["id"], a,
                            b)
                points = Taken(total["pointStart"], total["pointInterval"], total["data"])
                got = [points.damage(a + i * TAKEN_STEP_MS, a + (i + 1) * TAKEN_STEP_MS) for i in range(GRAPH_STEPS)]
        steps[at:] = (got + [0.0] * GRAPH_STEPS)[:GRAPH_STEPS]
    first, last = TAKEN_FIRST_MS // TAKEN_STEP_MS, math.ceil((end - t0) / TAKEN_STEP_MS)
    data = [v * 1000 / TAKEN_STEP_MS for v in steps[first:first + last]]
    if data and (part := end - t0 - (last - 1) * TAKEN_STEP_MS) < TAKEN_STEP_MS:
        data[-1] *= TAKEN_STEP_MS / part  # nothing is taken after the pull's end: its last step is shorter
    return {"pointStart": t0, "pointInterval": TAKEN_STEP_MS, "data": [round(v, 1) for v in data] if found else []}


async def raid_taken(rep: Report, fights: list[dict]) -> dict[int, Taken | None]:
    """Each pull's raid damage taken, by fight id (None where Warcraft Logs has none), kept forever: the pulls not
    kept are asked together, every window an alias. Raises WCLError."""
    async def fetch(missing: list[int]) -> dict[int, dict]:
        pulls = [f for f in fights if f["id"] in missing]
        asked = {f["id"]: [(f"f{f['id']}w{k}", f["id"], a, b) for k, (a, b) in enumerate(windows(f))] for f in pulls}
        got = await rep.wcl.graph_windows(rep.code, [item for f in pulls for item in asked[f["id"]]], "DamageTaken",
                                          hostilityType="Friendlies")
        return {f["id"]: stitch(f, [got[name] for name, *_ in asked[f["id"]]]) for f in pulls}
    kept = await rep.keep_many({f["id"]: (("taken", f["id"], "unboxed", TAKEN_STEP_MS), f["id"]) for f in fights},
                               fetch, FOREVER)
    return {fid: Taken(s["pointStart"], s["pointInterval"], s["data"]) if s.get("data") else None
            for fid, s in kept.items()}
