"""Warcraft Logs' rankings: a kill's parse, where a wipe stands against the spec's kills, the top parses."""
import math
from dataclasses import dataclass

from ..cache import DAY
from ..knowledge import spaced
from ..text import ordinal, short
from ..wcl import WCL, WCLError
from .base import FactError, Player, Report, unavailable

BENCHMARK_PERCENTILES = (90, 75, 50, 25)
# An empty rankings list is genuinely empty (a partial reply from Warcraft Logs raises instead).
NO_TOP = "Warcraft Logs returned no ranked {spec} {cls} kills of {boss} on this difficulty: nothing to compare with."


@dataclass
class Spread:
    """How a spec's ranked kills of a boss spread (spec_distribution): how many, whether that's exact, and
    (label, amount per second) points, best first."""
    n: int
    exact: bool
    points: list[tuple[str, float]]


async def kills_spread(rep: Report, f: dict, p: Player) -> Spread | str:
    """The spread of the spec's ranked kills of the pull's boss, or the line saying it's unavailable."""
    metric = metric_of(p)
    total = await _spec_total(rep, f, p, metric)
    try:
        n, exact, points = await rep.keep(
            ("distribution", f["encounterID"], p.class_name, p.spec, f.get("difficulty"), metric, total), [],
            lambda: spec_distribution(rep.wcl, f["encounterID"], p.class_name, p.spec, f.get("difficulty") or 5,
                                      metric, total))
    except WCLError as e:
        return "  " + unavailable("against kills", e)
    return Spread(n, exact, [(label, v) for label, v in points])


def benchmark_line(f: dict, p: Player, per_s: float, spread: Spread | str) -> str | None:
    """Where a wipe's throughput per second alive falls among the spec's ranked kills of the boss (`kills_spread`)."""
    if isinstance(spread, str):
        return spread
    n, exact, points = spread.n, spread.exact, spread.points
    if not points:
        return None
    above = next((label for label, v in points if per_s >= v), None)
    where = f"above the {above}" if above else f"below the {points[-1][0]}"
    if not exact and above:  # ranks, not percentiles: the count is a lower bound, so the percentile is too
        rank = int(above.split("th")[0])
        where += f" (the {ordinal(100 - math.ceil(100 * rank / n))} percentile or better)"
    return (f"  against kills: {short(per_s)}/s alive is {where} of the {n if exact else f'more than {n}'} ranked "
            f"{spaced(p.spec)} {spaced(p.class_name)} kills of {f['name']} on this difficulty (each character's "
            "best; " + ", ".join(f"{label} {short(v)}/s" for label, v in points)
            + "). A short pull runs higher than a full kill: Bloodlust and every cooldown come at the start."
            + (" Healing on a wipe is capped by damage taken: weak evidence on its own." if p.role == "healer"
               else ""))


def spread_line(spread: Spread, label: str, boss: str, metric: str) -> str | None:
    """Where a spec's ranked kills of a boss spread, for a raider to aim at (not the single best)."""
    if not spread.points:
        return None
    return (f"Where the {spread.n if spread.exact else f'more than {spread.n}'} ranked {label} kills of {boss} "
            "spread (each character's best): " + ", ".join(f"{lbl} {short(v)} {metric}" for lbl, v in spread.points)
            + ".")


HEALERS_READING = ("A healer's hps depends on the raid around them: healing is capped by the damage the raid takes "
                   "and shared among the healers, so a parse with fewer healers, or with the others healing less, "
                   "runs high. A share well above an even one is the raid around them as much as their play.")


def healer_share(name: str, healing: dict[str, float], percentiles: dict[str, int] | None = None) -> str | None:
    """How a healer's healing stood among their raid's healers ({name: healing} of everyone healing as a healer
    there): how many there were, their share against an even one, and (with `percentiles`) the others' parses."""
    total = sum(healing.values())
    if name not in healing or not total or len(healing) < 2:
        return None
    n = len(healing)
    out = f"{n} healers, {100 * healing[name] / total:.0f}% of their healing (even: {100 / n:.0f}%)"
    others = [ordinal(v) for v in sorted(v for other, v in (percentiles or {}).items()
                                         if other != name and other in healing)]
    return out + (", the others at the " + ", ".join(others) if others else "")


HEALERS_HEAD = ("Healers in each raid (how many, this healer's share of their healing against an even share, and "
                "the other healers' percentiles against their specs):")


def ranked_healers(ranked: list[dict]) -> tuple[dict[str, float], dict[str, int]]:
    """The healers of a ranked kill (`WCL.rankings`): ({name: hps}, {name: percentile})."""
    healing, percent = {}, {}
    for fight in ranked[:1]:
        for c in ((fight.get("roles") or {}).get("healers") or {}).get("characters") or []:
            healing[c["name"]] = float(c.get("amount") or 0)
            if (rank := _percent(c.get("rankPercent"))) is not None:
                percent[c["name"]] = rank
    return healing, percent


async def top_healer_shares(wcl: WCL, top: list[dict]) -> list[str | None]:
    """`healer_share` of each top parse (WCL's characterRankings rows) in its own raid, in one query; None for one
    that can't be told (Warcraft Logs not answering, an anonymous log)."""
    rows = [r for r in top if r.get("report")]
    try:
        ranked = await wcl.rankings_many([(r["report"]["code"], r["report"]["fightID"]) for r in rows], "hps")
    except WCLError:
        return [None] * len(top)
    by_row = {id(r): healer_share(r.get("name") or "", *ranked_healers(k)) for r, k in zip(rows, ranked, strict=True)}
    return [by_row.get(id(r)) for r in top]


def _percent(x) -> int | None:
    """A kill's percentile from Warcraft Logs, or None for "-" (nothing to rank against yet) or none at all."""
    return round(x) if isinstance(x, int | float) else None


class _NotRankedYet(Exception):
    """Warcraft Logs has no rankings for a kill yet (they come once it has processed the log): not remembered."""


async def _rankings(rep: Report, f: dict, metric: str) -> list[dict]:
    """WCL's rankings of a kill ([] for a wipe, or a kill not ranked yet, which is asked again next time). Not
    ranked yet also comes as every player's rankPercent "-". WCLError when Warcraft Logs can't answer."""
    async def fetch() -> list[dict]:
        ranked = await rep.wcl.rankings(rep.code, f["id"], metric)
        if not any(_percent(c.get("rankPercent")) is not None for fight in ranked
                   for role in (fight.get("roles") or {}).values() for c in role.get("characters") or []):
            raise _NotRankedYet
        return ranked
    try:
        return await rep.keep(("rankings", f["id"], metric), [f["id"]], fetch, DAY)  # more parses come in
    except _NotRankedYet:
        return []


async def _spec_total(rep: Report, f: dict, p: Player, metric: str) -> int | None:
    """How many ranked kills a class and spec has on a boss and difficulty, from a kill of it in this report where
    someone of that spec was ranked (totalParses); None when there's none."""
    for kill in rep.by_boss[rep.boss_key(f)]:
        if not kill["kill"]:
            continue
        try:
            ranked = await _rankings(rep, kill, metric)
        except WCLError:
            continue  # without the count, the benchmark says its percentile is a lower bound
        for fight in ranked:
            for role in (fight.get("roles") or {}).values():
                for c in role.get("characters") or []:
                    if (c.get("class"), c.get("spec")) == (p.class_name, p.spec) and c.get("totalParses"):
                        return int(c["totalParses"])
    return None


async def parse(rep: Report, f: dict, p: Player) -> str:
    """WCL's percentile for a player on a kill, against the same spec (and their item level bracket)."""
    metric = metric_of(p)
    try:
        ranked = await _rankings(rep, f, metric)
    except WCLError as e:
        return unavailable(f"Parse ({metric})", e)
    for fight in ranked:
        for role in (fight.get("roles") or {}).values():
            for c in role.get("characters") or []:
                if c.get("name") == p.name and (rank := _percent(c.get("rankPercent"))) is not None:
                    bracket = _percent(c.get("bracketPercent"))
                    return (f"Parse ({metric}): {ordinal(rank)} percentile of "
                            f"{c.get('totalParses', '?')} {spaced(p.spec)} {spaced(p.class_name)}s on this "
                            "boss and difficulty"
                            + (f", {ordinal(bracket)} in their item level bracket." if bracket is not None
                               else "."))
    return f"Parse ({metric}): not ranked by Warcraft Logs (yet: a kill is ranked once Warcraft Logs has processed it)."


async def kill_parses(rep: Report, f: dict, metric: str) -> dict[str, int]:
    """{player name: WCL's percentile against their spec} on a ranked kill, for `metric` (dps or hps); {} for a
    wipe, a kill not ranked yet, or Warcraft Logs not answering. One query per metric, kept with the kill's
    rankings."""
    try:
        ranked = await _rankings(rep, f, metric)
    except WCLError:
        return {}
    return {c["name"]: rank for fight in ranked for role in (fight.get("roles") or {}).values()
            for c in role.get("characters") or [] if (rank := _percent(c.get("rankPercent"))) is not None}


def metric_of(p: Player) -> str:
    return "hps" if p.role == "healer" else "dps"


def readable(r: dict) -> bool:
    """Whether a ranked parse's own log can be read for its player: an anonymous one (`hidden`, named "Anonymous")
    is in a log that calls every player "Player (N)"."""
    return bool(r.get("report")) and not r.get("hidden")


async def top_parses(rep: Report, f: dict, p: Player) -> list[dict]:
    """The top ranked kills of the player's spec on the boss and difficulty."""
    metric = metric_of(p)
    try:
        return await rep.keep(("top", f["encounterID"], p.class_name, p.spec, f.get("difficulty"), metric), [],
                              lambda: rep.wcl.top_rankings(f["encounterID"], p.class_name, p.spec,
                                                           f.get("difficulty") or 5, metric))
    except WCLError as e:
        raise FactError(f"Warcraft Logs rankings unavailable: {e}") from e


RANKING_PAGE_SIZE = 100
MAX_RANKING_PAGE = 20  # Warcraft Logs gives no page past this (the best 2000); page 21 is an error
CAPPED_RANKS = (100, 500, 1000, 2000)  # the points when a spec has more ranked kills than that and no total


async def spec_distribution(wcl: WCL, encounter: int, class_name: str, spec: str, difficulty: int,
                            metric: str, total: int | None = None) -> tuple[int, bool, list[tuple[str, float]]]:
    """(how many ranked kills, whether that's the exact count, [(label, amount)] best first) for a spec on a boss,
    from WCL's rankings: each character's best kill, best first, 100 a page and at most MAX_RANKING_PAGE pages.
    Up to that many the count is the list's length and the points are percentiles. Past it, `total` (a report's
    totalParses for the spec) places the percentiles that are in the list; without it the count is a lower bound
    and the points are ranks (the 100th best and so on). Every page comes from the partition page 1 came from."""
    pages: dict[int, tuple[list[dict], bool]] = {}
    partition = None  # page 1's, which is read first

    async def page(n: int) -> tuple[list[dict], bool]:
        nonlocal partition
        if n not in pages:
            rows, more, used = await wcl.ranking_page(encounter, class_name, spec, difficulty, metric, n, partition)
            partition = used if n == 1 else partition
            pages[n] = rows, more
        return pages[n]

    async def amount(rank: int) -> float | None:
        rows, _ = await page((rank - 1) // RANKING_PAGE_SIZE + 1)
        i = (rank - 1) % RANKING_PAGE_SIZE
        return float(rows[i].get("amount") or 0) if i < len(rows) else None

    hi = 1
    while (await page(hi))[1] and hi < MAX_RANKING_PAGE:
        hi = min(hi * 2, MAX_RANKING_PAGE)
    if (await page(hi))[1]:  # more than Warcraft Logs lists
        listed = MAX_RANKING_PAGE * RANKING_PAGE_SIZE
    else:
        lo = max(1, hi // 2)
        while lo < hi:  # the last page with rankings on it
            mid = (lo + hi + 1) // 2
            if (await page(mid))[0]:
                lo = mid
            else:
                hi = mid - 1
        if (await page(lo))[1]:  # "more pages follow", then none: a partial answer, not the end of the list
            raise WCLError(f"Warcraft Logs' rankings page {lo} says more follow, but page {lo + 1} is empty")
        listed = (lo - 1) * RANKING_PAGE_SIZE + len((await page(lo))[0])
    if not listed:
        return 0, True, []
    capped = listed == MAX_RANKING_PAGE * RANKING_PAGE_SIZE and (await page(MAX_RANKING_PAGE))[1]
    points = []
    if not capped or total:
        n = max(total or 0, listed) if capped else listed
        for q in BENCHMARK_PERCENTILES:
            rank = max(1, math.ceil((100 - q) / 100 * n))  # 1 is the best
            if rank <= listed and (v := await amount(rank)) is not None:
                points.append((f"{ordinal(q)} percentile", v))
        return n, True, points
    for rank in CAPPED_RANKS:
        if (v := await amount(rank)) is not None:
            points.append((f"{ordinal(rank)} best", v))
    return listed, False, points
