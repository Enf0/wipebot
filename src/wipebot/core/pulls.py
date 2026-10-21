"""Turn a finished boss pull into its deterministic summary (pull_summary, progress with each_pull and compare_pulls
start from it)."""
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field

from .text import clock, day, fight_url, ordinal, plural

DIFFICULTIES = {1: "LFR", 3: "Normal", 4: "Heroic", 5: "Mythic"}
NO_KILLING_BLOW = "no killing blow recorded"
FIRST_DEATHS = 3  # the deaths that usually start a wipe; later ones are mostly fallout
KILLERS_SHOWN = 5
TREND_LENGTH = 5
RESET_PCT = 99.0  # a wipe with the boss still above this was a reset, not an attempt
BUFF_GRACE_MS = 5_000  # boss buffs gained this early are part of the pull's setup
END_SLACK_MS = 1_000  # a buff removed this close to the end was still up when the pull ended
# An enrage ends a pull within seconds (Sszorak's within 18 s); a buff the boss carries for minutes to the end
# (Vashnik's Flame Infusion from 0:24, Nymrissa's Wavecaller's Might) is part of the fight, not what ended it.
END_BUFF_WINDOW_MS = 30_000


@dataclass
class Death:
    player: str
    ability: str
    at_ms: float
    fallout: bool = False  # after the wipe was decided


@dataclass
class GuildBest:
    """The guild's furthest wipe on a boss and difficulty in its logs before the one at hand."""
    pct: float
    code: str
    fight: int
    pull_no: int
    start_ms: float  # the log's start, UNIX ms

    def text(self) -> str:
        return (f"{self.pct:.1f}% on {day(self.start_ms)} (pull {self.pull_no}, "
                f"{fight_url(self.code, self.fight)})")


@dataclass
class PullSummary:
    boss: str
    difficulty: str
    pull_no: int
    kill: bool
    boss_pct: float | None
    phase: str | None
    duration_ms: float
    deaths: list[Death]
    best_pct: float | None  # best wipe on this boss before this pull, in this log
    trend: list[float]  # boss % of the last few wipes, oldest first, including this one
    reset: bool = False  # a wipe with the boss untouched: a reset, not an attempt
    boss_buffs: list[tuple[str, float]] = field(default_factory=list)  # (buff, gained at ms): kept to the end
    guild_best: GuildBest | None = None  # the best wipe in the guild's earlier logs
    decided_ms: float | None = None  # when the wipe was decided, if it was
    decided_why: str = ""  # what decided it: "half the raid was dead", "the boss gained <its enrage>"
    live: bool = False  # still being logged: neither a kill nor a wipe yet


def wipe_pct(fight: dict) -> float | None:
    """How much of the fight was left when a pull ended, in %: Warcraft Logs' fightPercentage, its measure of how
    far a wipe got, which for a boss with phases weighs them in (an Ula'tek wipe in phase 1: 83.3 where the boss's
    health was 76.6); the boss's health when a log lacks it."""
    pct = fight.get("fightPercentage")
    return pct if pct is not None else fight.get("bossPercentage")


def is_reset(fight: dict) -> bool:
    """A wipe with the boss still (nearly) at full health: the raid reset, it didn't attempt the boss."""
    return not fight["kill"] and not fight.get("inProgress") and (wipe_pct(fight) or 0) >= RESET_PCT


def attempt(fight: dict) -> bool:
    """A pull to count in figures over the night: ended (a pull still going has no end yet), not a reset."""
    return not fight.get("inProgress") and not is_reset(fight)


def wipe_pcts(fights: list[dict]) -> list[float]:
    """How far each wipe got (wipe_pct), in order: kills, resets, pulls still going and pulls WCL gave no % for
    left out."""
    return [pct for f in fights if not f["kill"] and not f.get("inProgress") and (pct := wipe_pct(f)) is not None
            and not is_reset(f)]


def pct_text(pct: float | None) -> str:
    """'62.6%', or 'boss % unknown' when WCL gave none."""
    return f"{pct:.1f}%" if pct is not None else "boss % unknown"


def lost_buffs(fight: dict, buff_events: list[dict]) -> set[int]:
    """Buffs a boss lost before the end of a pull: routine buffs that come and go (a cast, an intermission)."""
    return {e["abilityGameID"] for e in buff_events
            if e["type"] == "removebuff" and e["timestamp"] < fight["endTime"] - END_SLACK_MS}


def end_buffs(fight: dict, buff_events: list[dict], routine: Collection[int] = ()) -> list[tuple[int, float]]:
    """(ability id, ms from pull start) of each buff a boss gained shortly before the end of a pull (within
    END_BUFF_WINDOW_MS) and still had at the end, such as an enrage. `buff_events` are the bosses'
    applybuff/removebuff events of this fight; `routine` are buffs the boss was seen to lose during earlier pulls,
    which are left out (a pull that ends during a routine buff says nothing)."""
    routine = set(routine) | lost_buffs(fight, buff_events)
    t0 = fight["startTime"]
    found: dict[int, float] = {}
    for e in buff_events:
        if e["type"] == "applybuff" and e["abilityGameID"] not in routine and e["timestamp"] - t0 >= BUFF_GRACE_MS \
                and fight["endTime"] - e["timestamp"] <= END_BUFF_WINDOW_MS:
            found[e["abilityGameID"]] = min(found.get(e["abilityGameID"], e["timestamp"]), e["timestamp"])
    return sorted(((a, at - t0) for a, at in found.items()), key=lambda x: x[1])


def phase_label(fight: dict) -> str | None:
    """'P2', 'I1', or 'P1 (2nd time)' when the fight went back to a phase (e.g. after an intermission)."""
    n = fight.get("lastPhase")
    if not n:
        return None
    label = f"I{n}" if fight.get("lastPhaseIsIntermission") else f"P{n}"
    times = sum(1 for t in fight.get("phaseTransitions") or [] if t.get("id") == n)
    return f"{label} ({ordinal(times)} time)" if times > 1 else label


def summarize(
    fight: dict,
    report_fights: list[dict],
    death_events: list[dict],
    players: dict[int, str],
    abilities: dict[int, str],
    boss_buff_events: Sequence[dict] = (),
    routine_buffs: Collection[int] = (),
    guild_best: GuildBest | None = None,
    decided_ms: float | None = None,
    decided_why: str = "",
) -> PullSummary:
    """Build the summary for `fight`, using earlier pulls in `report_fights` for history. `boss_buff_events` are
    the bosses' buff events in this fight, for the buffs the boss still had at the end (e.g. an enrage);
    `routine_buffs` are buffs the boss lost during earlier pulls (see `end_buffs`); `guild_best` is the best wipe in
    the guild's earlier logs; `decided_ms` when the wipe was decided (ms from the pull), later deaths its fallout, and
    `decided_why` what decided it."""
    same_boss = sorted(
        (f for f in report_fights
         if f["encounterID"] == fight["encounterID"] and f.get("difficulty") == fight.get("difficulty")
         and f["startTime"] <= fight["startTime"]),
        key=lambda f: f["startTime"],
    )
    earlier_wipes = wipe_pcts(same_boss[:-1])
    trend = earlier_wipes[-(TREND_LENGTH - 1):] + wipe_pcts([fight])

    deaths = [
        Death(
            player=players.get(e["targetID"], f"#{e['targetID']}"),
            ability=abilities.get(e["killingAbilityGameID"], f"spell {e['killingAbilityGameID']}")
            if e.get("killingAbilityGameID") else NO_KILLING_BLOW,
            at_ms=e["timestamp"] - fight["startTime"],
            fallout=decided_ms is not None and e["timestamp"] - fight["startTime"] > decided_ms,
        )
        for e in sorted(death_events, key=lambda e: e["timestamp"])
        if e["targetID"] in players
    ]

    return PullSummary(
        boss=fight["name"],
        difficulty=DIFFICULTIES.get(fight.get("difficulty"), f"difficulty {fight.get('difficulty')}"),
        pull_no=len(same_boss),
        kill=bool(fight["kill"]),
        boss_pct=wipe_pct(fight),
        phase=phase_label(fight),
        duration_ms=fight["endTime"] - fight["startTime"],
        deaths=deaths,
        best_pct=min(earlier_wipes) if earlier_wipes else None,
        trend=trend,
        reset=is_reset(fight),
        boss_buffs=[] if fight["kill"] or fight.get("inProgress") else first_by_name(
            (abilities.get(a, f"spell {a}"), at) for a, at in end_buffs(fight, list(boss_buff_events), routine_buffs)),
        guild_best=guild_best,
        decided_ms=decided_ms,
        decided_why=decided_why,
        live=bool(fight.get("inProgress")),
    )


def first_by_name(buffs: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """A buff several bosses gain (both of the Coiled Altar's, each Entombed Sentinel) once, from the first to gain
    it."""
    first: dict[str, float] = {}
    for name, at in buffs:
        first[name] = min(at, first.get(name, at))
    return list(first.items())


def best_marks(pct: float, tonight: float | None, before: GuildBest | None) -> list[str]:
    """How a wipe at `pct` compares with the log's earlier wipes (`tonight`) and the guild's earlier logs
    (`before`, None when it has none or they weren't read): "new guild best" only when it beats both."""
    marks = []
    if tonight is not None:
        marks.append("best of this log so far" if pct < tonight else f"best of this log {tonight:.1f}%")
    if before is not None:
        if pct < min(before.pct, tonight if tonight is not None else before.pct):
            marks.append(f"**new guild best!** (before this log: {before.text()})")
        else:
            marks.append(f"guild best before this log {before.text()}")
    return marks


def killers_text(deaths: list[Death]) -> str:
    """'Virulence ×17 · Tempest ×1', the most deaths first; past KILLERS_SHOWN, the rest summed."""
    killers = Counter(d.ability for d in deaths).most_common()
    shown, rest = killers[:KILLERS_SHOWN], killers[KILLERS_SHOWN:]
    others = f"{len(rest)} other {'ability' if len(rest) == 1 else 'abilities'} ×{sum(n for _, n in rest)}"
    return " · ".join([f"{name} ×{n}" for name, n in shown] + ([others] if rest else []))


def format_summary(s: PullSummary) -> str:
    head = f"**{s.boss}** ({s.difficulty}) · pull {s.pull_no}"
    if s.kill:
        return f"{head} · **KILL** · {clock(s.duration_ms)} · {plural(len(s.deaths), 'death')}"

    if s.live:
        pct = f", the boss at {s.boss_pct:.1f}% so far" if s.boss_pct is not None else ""
        parts = [head, f"**in progress**{pct}"]
    else:
        pct = f" at **{s.boss_pct:.1f}%**" if s.boss_pct is not None else " (boss health not logged)"
        parts = [head, f"{'reset' if s.reset else 'wipe'}{pct}"]
    if s.phase:
        parts.append(s.phase)
    parts.append(clock(s.duration_ms))
    if s.boss_pct is not None and not s.reset and not s.live:
        parts += best_marks(s.boss_pct, s.best_pct, s.guild_best)
    lines = [" · ".join(parts)]
    if s.boss_buffs:
        lines.append("Boss buffs at the end: " + " · ".join(f"**{name}** (from {clock(at)})"
                                                            for name, at in s.boss_buffs))

    if s.deaths:
        first = " · ".join(f"{d.player} ({d.ability}, {clock(d.at_ms)})" for d in s.deaths[:FIRST_DEATHS])
        lines.append(f"First deaths: {first}")
        counted = [d for d in s.deaths if not d.fallout]
        if s.decided_ms is None or len(counted) == len(s.deaths):
            lines.append("Killed by: " + killers_text(counted))
        else:
            later = plural(len(s.deaths) - len(counted), "later death")
            why = f" ({s.decided_why})" if s.decided_why else ""
            lines.append(f"Killed by, until the wipe was decided at {clock(s.decided_ms)}{why}: "
                         + (killers_text(counted) or "none") + f" ({later}: fallout)")
    if len(s.trend) > 1 and not s.reset and not s.live:
        lines.append(f"Last {len(s.trend)}: " + " → ".join(f"{p:.1f}" for p in s.trend))
    return "\n".join(lines)
