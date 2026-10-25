"""What must hold for every pull of every report the tool cases recorded (support/recorded.py), whatever its
snapshot says: a snapshot pins an output right or wrong, these hold or the output is wrong. Each is checked on the
values the tools print from (Report, pulls.summarize, the mechanic hits, a player's night), not on their text. A
pull whose answers no case asked for isn't recorded and is left out; MIN_PULLS keeps a check from passing on none."""
import contextlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import pytest

from tests.support.recorded import CASES, LOCAL_RECORDED, NOT_LOCAL, Replay, serve_from
from wipebot.core import server
from wipebot.core.pulls import (
    BUFF_GRACE_MS,
    NO_KILLING_BLOW,
    Decided,
    PullSummary,
    attempt,
    format_summary,
    summarize,
)
from wipebot.core.report import Report, coaching, mechanics, night, ranks
from wipebot.core.report.base import DEATH_BURST_MS, DEATH_GAP_MS, alive_after, alive_at, alive_ms

REPORTS = sorted({c.args["report"] for c in CASES.values() if "report" in c.args})
MIN_PULLS = 100  # about half the reports' pulls: those some case read every death and battle res of


@dataclass
class Pull:
    code: str
    label: str
    f: dict
    deaths: list[dict]
    backs: dict[int, list[float]]  # battle res and self-res, per player
    raid: int
    decided: Decided | None
    summary: PullSummary

    @property
    def wipe(self) -> bool:
        return not self.f["kill"] and not self.f.get("inProgress")

    def at(self, d: dict) -> float:
        return d["timestamp"] - self.f["startTime"]


@dataclass
class Recorded:
    pulls: list[Pull] = field(default_factory=list)
    killers: list[tuple[list[Pull], str]] = field(default_factory=list)  # a night's pulls on a boss, its killer line
    hits: list[tuple[str, list[Pull], mechanics.Hits, set[int]]] = field(default_factory=list)  # a case's
    night: list[coaching.NightPull] = field(default_factory=list)  # Enfo's Sszorak night (local)
    percentiles: list[float] = field(default_factory=list)  # Warcraft Logs' percentiles of the night's kills (local)
    spread: ranks.Spread | None = None  # Draiman's spec's kills of Sszorak (local)


async def _pulls(rep: Report, fights: list[dict]) -> list[Pull]:
    with contextlib.suppress(LookupError):  # a night's pulls in one query each, as progress asks
        await rep.decided_cuts(fights)
    out = []
    for f in fights:
        try:
            deaths, backs, raid = await rep.deaths(f), (await rep.resurrects([f]))[f["id"]], await rep.raid_size(f)
            decided = await rep.decided_at(f)
            buffs, routine = await rep.boss_buffs(f), await rep.routine_buffs(f)
        except LookupError:
            continue  # no case read this pull
        s = summarize(f, rep.by_boss[rep.boss_key(f)], deaths, rep.names, rep.abilities, buffs, routine, None,
                      decided)
        out.append(Pull(rep.code, f"{rep.code} {rep.label(f)}", f, deaths, backs, raid, decided, s))
    return out


async def _load() -> Recorded:
    out = Recorded()
    for code in REPORTS:
        rep = await server.lib().report(code)
        for fights in rep.by_boss.values():
            pulls = await _pulls(rep, fights)
            out.pulls += pulls
            if len(pulls) == len(fights):
                with contextlib.suppress(LookupError):
                    if line := await night._nights_killer(rep, fights):
                        out.killers.append(([p for p in pulls if attempt(p.f)], line))
    for case in CASES.values():
        if case.tool == "mechanic_hits" and "ability" in case.args:
            a = case.args
            rep = await server.lib().report(a["report"])
            fights = rep.by_boss[rep.boss(a.get("boss"))] if a.get("all_pulls") else [rep.fight(a.get("pull"))]
            cut = {} if a.get("whole_pull") else await rep.decided_cuts(fights)
            ids = rep.ability_ids(a["ability"])
            found = await mechanics._hits(rep, fights, ids, mechanics._counting(fights, cut, None))
            out.hits.append((case.name, [p for p in out.pulls if p.f in fights], found, set(ids)))
    if LOCAL_RECORDED.exists():
        a = CASES["player-night"].args
        rep = await server.lib().report(a["report"])
        p = rep.player(a["player"])
        out.night = (await coaching.night_pulls(rep, p, rep.by_boss[rep.boss(None)]))[0]
        out.percentiles = [x for k in (await night._kill_rankings(rep)).values() for x in k.values()]
        a = CASES["player-pull"].args
        f = rep.fight(a["pull"])
        spread = await ranks.kills_spread(rep, f, rep.in_pull(f, rep.player(a["player"])))
        out.spread = spread if isinstance(spread, ranks.Spread) else None
    return out


_LOADED: list[Recorded] = []


@pytest.fixture
async def recorded(monkeypatch) -> Recorded:
    """Every recorded pull's facts, read once for the module."""
    if not _LOADED:
        serve_from(monkeypatch, Replay())
        _LOADED.append(await _load())
    return _LOADED[0]


@pytest.fixture
async def local(recorded) -> Recorded:
    if not LOCAL_RECORDED.exists():
        pytest.skip(NOT_LOCAL)
    return recorded


async def test_enough_pulls_are_recorded(recorded):
    assert len(recorded.pulls) >= MIN_PULLS
    assert sum(p.decided is not None for p in recorded.pulls) >= MIN_PULLS // 2
    assert len(recorded.killers) >= 3 and len(recorded.hits) >= 2


async def test_a_kill_has_no_decided_moment_and_no_fallout(recorded):
    kills = [p for p in recorded.pulls if p.f["kill"]]
    assert kills
    for p in kills:
        assert p.decided is None and not any(d.fallout for d in p.summary.deaths), p.label


async def test_the_decided_moment_is_within_the_pull(recorded):
    for p in recorded.pulls:
        if p.decided:
            assert 0 <= p.decided.at_ms <= p.f["endTime"] - p.f["startTime"], p.label


async def test_a_death_is_fallout_exactly_when_it_came_after_the_decided_moment(recorded):
    """Counted and fallout split every death of the pull, by the moment alone."""
    for p in recorded.pulls:
        assert len(p.summary.deaths) == len(p.deaths), p.label
        at = p.decided.at_ms if p.decided else math.inf
        assert [d.fallout for d in p.summary.deaths] == [d.at_ms > at for d in p.summary.deaths], p.label


async def test_one_moments_deaths_are_never_split_into_counted_and_fallout(recorded):
    """A burst of deaths logged ms apart is one moment: the first fallout death comes more than DEATH_GAP_MS after
    the last counted one, unless the burst ran past DEATH_BURST_MS."""
    for p in recorded.pulls:
        counted = [p.at(d) for d in p.deaths if p.decided and p.at(d) <= p.decided.at_ms]
        later = [p.at(d) for d in p.deaths if p.decided and p.at(d) > p.decided.at_ms]
        if counted and later and later[0] - counted[-1] <= DEATH_GAP_MS:
            burst = [t for t in counted if counted[-1] - t <= DEATH_BURST_MS]
            assert counted[-1] - burst[0] >= DEATH_BURST_MS - DEATH_GAP_MS, p.label


def _first_half_dead(p: Pull) -> float | None:
    """When ⌈n/2⌉ of the pull's n players were first dead at once, a battle res bringing one back."""
    dead: dict[int, float] = {}  # who: when they died
    for d in p.deaths:
        dead = {who: at for who, at in dead.items() if not any(at < t <= d["timestamp"] for t in p.backs.get(who, []))}
        dead[d["targetID"]] = d["timestamp"]
        if len(dead) >= math.ceil(p.raid / 2):
            return d["timestamp"]
    return None


async def test_half_the_raid_dead_is_a_death_with_half_the_raid_dead(recorded):
    """A wipe decided by half the raid dead is decided at a death, with at least ⌈n/2⌉ of the n players dead (a
    battle res counted), at the first moment that was so or within the burst of deaths it fell in."""
    halves = [p for p in recorded.pulls if p.decided and not p.decided.enrage]
    assert halves
    for p in halves:
        t0, at = p.f["startTime"], p.decided.at_ms
        assert any(p.at(d) == at for d in p.deaths), p.label
        assert p.raid - alive_at(p.deaths, p.backs, p.raid, t0 + at) >= math.ceil(p.raid / 2), p.label
        first = _first_half_dead(p)
        assert first is not None and 0 <= t0 + at - first <= DEATH_BURST_MS, p.label


async def test_every_death_is_of_a_player_in_the_raid(recorded):
    """The raid size the alive counts start from holds everyone who died."""
    for p in recorded.pulls:
        assert {d["targetID"] for d in p.deaths} <= set(p.f.get("friendlyPlayers") or []), p.label


def _steps(p: Pull):
    """(alive before a death, alive after it, battle res since the death before) for each death of a pull."""
    alive = alive_after(p.deaths, p.backs, p.raid)
    since = [-math.inf] + [d["timestamp"] for d in p.deaths]
    for d, before, now, prev in zip(p.deaths, [p.raid, *alive], alive, since, strict=False):
        yield before, now, sum(prev < t <= d["timestamp"] for ts in p.backs.values() for t in ts)


async def test_players_alive_stay_within_the_raid_and_go_up_only_with_a_battle_res(recorded):
    for p in recorded.pulls:
        for before, now, back in _steps(p):
            assert 0 <= now < p.raid and now <= before + back, p.label


async def test_time_alive_runs_to_a_players_first_death_and_on_after_a_battle_res(recorded):
    for p in recorded.pulls:
        start = p.f["startTime"]
        end = start + p.decided.at_ms if p.decided else p.f["endTime"]
        for who in {d["targetID"] for d in p.deaths}:
            first = min(next(d["timestamp"] for d in p.deaths if d["targetID"] == who), end)
            alive = alive_ms(p.deaths, p.backs.get(who, []), who, start, end)
            assert first - start <= alive <= end - start, p.label
            if any(first < t < end for t in p.backs.get(who, [])):
                assert alive > first - start, p.label


async def test_each_death_takes_one_player_from_the_alive_and_each_battle_res_gives_one_back(recorded):
    """A self-res (a shaman's Reincarnation, only a cast in the log) counts as a battle res: without it, a shaman
    dead twice is taken from the alive twice."""
    for p in recorded.pulls:
        for before, now, back in _steps(p):
            assert now == before - 1 + back, p.label


async def test_a_summary_stays_within_the_pull_and_its_killers_are_the_counted_deaths(recorded):
    for p in recorded.pulls:
        s, dur = p.summary, p.f["endTime"] - p.f["startTime"]
        assert s.duration_ms == dur > 0, p.label
        assert all(0 <= d.at_ms <= dur for d in s.deaths), p.label
        assert [d.at_ms for d in s.deaths] == sorted(d.at_ms for d in s.deaths), p.label
        assert s.boss_pct is None or 0 <= s.boss_pct <= 100, p.label
        assert all(0 <= x <= 100 for x in [*s.trend, *([s.best_pct] if s.best_pct is not None else [])]), p.label
        assert all(BUFF_GRACE_MS <= at <= dur for _, at in s.boss_buffs), p.label
        if p.wipe and not s.reset and s.boss_pct is not None:
            assert s.trend[-1] == s.boss_pct, p.label
        killed_by = next((line for line in format_summary(s).splitlines() if line.startswith("Killed by")), "")
        counted = sum(map(int, re.findall(r"×(\d+)", killed_by)))
        assert s.kill or counted == sum(not d.fallout for d in s.deaths), p.label


async def test_the_nights_killer_counts_the_deaths_the_summaries_count(recorded):
    """The night's killer and each pull's "Killed by" count the same deaths: every death on a kill, a wipe's until
    it was decided, resets left out."""
    for pulls, line in recorded.killers:
        counted = Counter(d.ability for p in pulls for d in p.summary.deaths
                          if not d.fallout and d.ability != NO_KILLING_BLOW)
        top = counted.most_common(1)[0][1]
        assert any(f"): {name} [" in line and f", {n} deaths over {len(pulls)} pulls" in line
                   for name, n in counted.items() if n == top), (pulls[0].label, line)


async def test_a_mechanics_deaths_are_its_hits_deaths(recorded):
    """mechanic_hits counts the deaths a pull's summary puts on it (until each wipe was decided, unless whole_pull),
    each once whatever id it logged under, and each to a player it hit, with a hit just before."""
    for case, pulls, h, ids in recorded.hits:
        whole = CASES[case].args.get("whole_pull", False)
        died = Counter(d["targetID"] for p in pulls for d in p.deaths if d.get("killingAbilityGameID") in ids
                       and (whole or not p.decided or p.at(d) <= p.decided.at_ms))
        assert h.killed_by == died, case
        for who, n in h.killed_by.items():
            assert n <= h.hits[who] + h.ticks[who] and n <= h.died_after[who], case


async def test_a_players_night_counts_within_each_pull(local):
    """Time dead within the time counted, that within the pull; a death called fallout as the pull's summary calls
    it; major cooldowns used within the time counted, possible at least once, and more than possible only when two
    uses came closer than the base cooldown (a talent shortened it)."""
    pulls = {(p.code, p.f["id"]): p for p in local.pulls}
    code = CASES["player-night"].args["report"]
    assert len(local.night) >= 30
    for n in local.night:
        f, label = n.f, f"{n.f['id']}"
        dur = (f["endTime"] - f["startTime"]) / 1000
        assert 0 <= n.dead_s <= n.counted_s <= dur and 0 <= n.alive_s <= dur, label
        if n.death:
            s = pulls[code, f["id"]].summary
            first = next(d for d in s.deaths if d.at_ms == n.death[1]["timestamp"] - f["startTime"])
            assert n.fallout == first.fallout, label
        for sp, times, possible in n.cooldowns:
            assert all(0 <= t <= n.counted_s * 1000 for t in times), (label, sp["name"])
            if possible is None:
                continue
            assert possible >= 1, (label, sp["name"])
            if len(times) > possible:
                assert min(b - a for a, b in zip(times, times[1:], strict=False)) < sp["cooldown"] * 1000, label


async def test_percentiles_are_within_0_and_100(local):
    assert local.percentiles and all(0 <= x <= 100 for x in local.percentiles)
    s = local.spread
    assert s is not None and s.n >= len(s.points) > 0
    amounts = [v for _, v in s.points]
    assert amounts == sorted(amounts, reverse=True)  # best first
