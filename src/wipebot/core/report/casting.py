"""How a player's time on a pull went: time alive, casting gaps and cancelled casts."""
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ..text import clock
from .base import Player, Report

# Casting gaps: the log has when a cast begins and lands, not the global cooldown's length or when a channel ends,
# so an instant is taken as the longest a GCD can be, and a stretch without a cast is a gap only when it's clearly
# longer than that. A long channel still looks like a short gap; top parses get the same reading, so the
# comparison holds.
GCD_MAX_MS = 1500
GAP_MS = 3000
CAST_MAX_MS = 6000  # a begun cast that hasn't landed within this was dropped (no raid cast takes longer)
GAPS_NAMED = 3  # the longest gaps named with their times
PAUSED_SHARE = 0.8  # this share of the raid alive and beginning no cast at once is a raid-wide pause (a stun)
CANCELS_NAMED = 3  # cancelled casts' times named per spell


def cast_rows(entries: list[dict]) -> list[dict]:
    """A Casts table's rows with each composite row split into its spells. A one-player table nests some spells in
    one row whose total is only its own id's (docs/reference/wcl-api.md): a part with another name is another spell
    (Infernal Bolt under Shadow Bolt), and parts with the same name are one cast logged under several ids, counted
    once."""
    rows = []
    for e in entries:
        if not e.get("composite"):
            rows.append(e)
            continue
        parts: dict[str, dict] = {}
        for s in e.get("subentries") or []:
            if s["name"] not in parts or (s.get("total") or 0) > (parts[s["name"]].get("total") or 0):
                parts[s["name"]] = s
        rows += parts.values() or [e]
    return rows


def alive(rep: Report, f: dict, p: Player, deaths: list[dict], casts: list[dict]) -> tuple[float, float]:
    """(seconds alive, the last moment alive) of a player in a pull. A player brought back (a battle res,
    Reincarnation) is alive again from their next cast after a death, not a fake one (Warcraft Logs derives one
    from a dead Demon Hunter)."""
    dead_ms, until = 0.0, f["endTime"]
    for d in deaths:
        back = next((c["timestamp"] for c in casts if c["timestamp"] > d["timestamp"] and not c.get("fake")), None)
        dead_ms += (back or f["endTime"]) - d["timestamp"]
        until = f["endTime"] if back else d["timestamp"]
    return (f["endTime"] - f["startTime"] - dead_ms) / 1000, until


@dataclass
class Casting:
    """How a player's casting flowed: the stretches without a cast and the casts begun and dropped."""
    gaps: list[tuple[float, float]]  # each stretch of GAP_MS or more without a cast: (from, to) timestamps
    cancelled: dict[int, list[float]]  # casts begun, then dropped for another: ability id -> when
    span_ms: float  # from the first cast to the end looked at, the time the gaps are a share of

    @property
    def gap_ms(self) -> float:
        return sum(b - a for a, b in self.gaps)

    @property
    def gap_share(self) -> float:
        return self.gap_ms / self.span_ms if self.span_ms > 0 else 0.0


def casting(events: list[dict], until: float, deaths: Collection[float] = ()) -> Casting:
    """The casting gaps and cancelled casts in a player's cast events (casts, begincasts, empowers), up to
    `until`. A begun cast lands as a cast of the same spell; another cast begun before that, or no landing within
    CAST_MAX_MS, means it was dropped (an instant in between can be an off-GCD spell during the cast, so it isn't
    a drop by itself). A gap that holds a death is time dead, not a gap, and the last gap ends at the death."""
    active: list[tuple[float, float]] = []  # when the player was casting or on the GCD, as far as the log says
    cancelled: dict[int, list[float]] = defaultdict(list)
    pending: tuple[int, float] | None = None  # the begun cast not yet landed: (ability, when)
    # A cast landing and the next one beginning share a millisecond: the landing first.
    for e in sorted(events, key=lambda e: (e["timestamp"], e["type"] in ("begincast", "empowerstart"))):
        t, kind, ability = e["timestamp"], e["type"], e.get("abilityGameID") or 0
        if t > until:
            break
        if pending and t - pending[1] > CAST_MAX_MS:
            cancelled[pending[0]].append(pending[1])
            pending = None
        if kind in ("begincast", "empowerstart"):
            if pending:
                cancelled[pending[0]].append(pending[1])
            pending = (ability, t)
            active.append((t, t))
        elif kind in ("cast", "empowerend"):
            if pending and pending[0] == ability:
                active.append((pending[1], t))
                pending = None
            else:
                active.append((t, t + GCD_MAX_MS))
    if not active:
        return Casting([], {}, 0.0)
    active.sort()
    gaps: list[tuple[float, float]] = []
    end = active[0][1]
    for a, b in active[1:] + [(until, until)]:
        if a - end >= GAP_MS:
            gaps.append((end, a))
        end = max(end, b)
    # A gap holding a death is cut there: what follows is time dead.
    gaps = [(a, min(b, next((d for d in sorted(deaths) if a < d < b), b))) for a, b in gaps]
    gaps = [(a, b) for a, b in gaps if b - a >= GAP_MS]
    return Casting(gaps, dict(cancelled), until - active[0][0])


async def raid_pauses(rep: Report, f: dict) -> list[tuple[float, float]]:
    """Stretches of GAP_MS or more when PAUSED_SHARE of the raid's living players began no cast at once: a raid-wide
    stun or knockback, or nothing to hit. A player's gap then isn't their own. Every player's casts in one query."""
    events, deaths = await rep.events(f["id"], "Casts"), await rep.deaths(f)
    ids = set(rep.pull_players(f))
    by_player: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("sourceID") in ids:
            by_player[e["sourceID"]].append(e)
    edges: list[tuple[float, int, int]] = []  # (when, players idle, players alive): a gap or a death begins or ends
    for pid in ids:
        died = sorted(d["timestamp"] for d in deaths if d["targetID"] == pid)
        flow = casting(by_player.get(pid, []), f["endTime"], died)  # a gap ends at a death
        edges += [x for a, b in flow.gaps for x in ((a, 1, 0), (b, -1, 0))]
        casts = sorted(e["timestamp"] for e in by_player.get(pid, []) if not e.get("fake"))
        for d in died:  # alive again from their next cast (a battle res), as `alive` reads it
            edges.append((d, 0, -1))
            if (back := next((t for t in casts if t > d), None)) is not None:
                edges.append((back, 0, 1))
    out, idle_now, alive_now, since = [], 0, len(ids), None
    for t, idle, back in sorted(edges, key=lambda e: (e[0], e[1] + e[2])):
        idle_now, alive_now = idle_now + idle, alive_now + back
        # Of the living, and while half the raid lives: the last few standing aren't a raid
        paused = alive_now >= len(ids) / 2 and idle_now >= PAUSED_SHARE * alive_now
        if since is None and paused:
            since = t
        elif since is not None and not paused:
            if t - since >= GAP_MS:
                out.append((since, t))
            since = None
    return out


def without(gaps: list[tuple[float, float]], pauses: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The gaps with the raid's pauses cut out, what's left still GAP_MS or more."""
    for pa, pb in pauses:
        gaps = [part for a, b in gaps for part in ((a, min(b, pa)), (max(a, pb), b)) if part[1] > part[0]]
    return [(a, b) for a, b in gaps if b - a >= GAP_MS]


def casting_lines(rep: Report, f: dict, c: Casting, alive_s: float,
                  pauses: Sequence[tuple[float, float]] = ()) -> list[str]:
    """The two lines of `player_pull` about a player's casting flow."""
    t0 = f["startTime"]
    gaps = f"Casting gaps of {GAP_MS // 1000} s or more (no cast begun; a long channel can look like one): "
    if c.gaps:
        longest = sorted(c.gaps, key=lambda g: g[0] - g[1])[:GAPS_NAMED]
        gaps += (f"{len(c.gaps)}, {c.gap_ms / 1000:.0f} s in all ({100 * c.gap_ms / 1000 / max(alive_s, 1):.0f}% "
                 f"of the {alive_s:.0f} s alive); longest "
                 + ", ".join(f"{clock(a - t0)}-{clock(b - t0)} ({(b - a) / 1000:.0f} s)" for a, b in sorted(longest)))
    else:
        gaps += "none"
    if pauses:
        gaps += ("; left out, the raid's pauses (most of the raid began no cast either: a stun, a knockback, nothing "
                 "to hit): " + ", ".join(f"{clock(a - t0)}-{clock(b - t0)}" for a, b in pauses))
    drops = "Cancelled casts (begun, then dropped for another): "
    if c.cancelled:
        drops += ", ".join(
            f"{rep.ability_ref(ability)} {len(times)} ({', '.join(clock(t - t0) for t in times[:CANCELS_NAMED])}"
            f"{', ...' if len(times) > CANCELS_NAMED else ''})"
            for ability, times in sorted(c.cancelled.items(), key=lambda x: -len(x[1])))
    else:
        drops += "none"
    return [gaps, drops]
