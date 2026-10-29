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


@dataclass(frozen=True)
class Decided:
    """When a wipe was decided, and what decided it: the boss gaining a buff it kept to the end (`enrage`, its name),
    or half the raid dead. Deaths up to it count; later ones are its fallout."""
    at_ms: float  # from the pull's start
    enrage: str = ""  # the buff that decided it; "" when half the raid dead did

    @property
    def why(self) -> str:
        return f"the boss gained {self.enrage}" if self.enrage else "half the raid was dead"


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
    ended: dict = field(default_factory=dict)  # the fight's phase fields (wcl.WIPE_FIELDS): where it got to

    @property
    def phase(self) -> str | None:
        return phase_label(self.ended)

    @property
    def health(self) -> float | None:
        """The boss's health in the phase it ended in, on a boss counted by phase (phase_health)."""
        return phase_health(self.ended) if self.ended else None

    def text(self, wcl: str = "") -> str:
        """'P2 64.0% (WCL 69.2%) on ...' where its phase is known on a boss counted by phase, else its % ('62.6%'),
        after `wcl` ("WCL ") on such a boss."""
        how = wipe_text(self.ended) if self.health is not None else f"{wcl}{self.pct:.1f}%"
        return f"{how} on {day(self.start_ms)} (pull {self.pull_no}, {fight_url(self.code, self.fight)})"


@dataclass
class PullSummary:
    boss: str
    difficulty: str
    pull_no: int
    kill: bool
    boss_pct: float | None  # how far the wipe got (wipe_pct)
    phase: str | None
    duration_ms: float
    deaths: list[Death]
    best_pct: float | None  # best wipe on this boss before this pull, in this log
    trend: list[float]  # boss % of the last few wipes, oldest first, including this one
    reset: bool = False  # a wipe with the boss untouched: a reset, not an attempt
    boss_buffs: list[tuple[str, float]] = field(default_factory=list)  # (buff, gained at ms): kept to the end
    guild_best: GuildBest | None = None  # the best wipe in the guild's earlier logs
    decided: Decided | None = None  # when the wipe was decided, and by what, if it was
    live: bool = False  # still being logged: neither a kill nor a wipe yet
    phase_health: float | None = None  # on a boss with phases, its health in the phase the pull ended in


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


def phase_health(fight: dict) -> float | None:
    """The boss's health in the phase a pull ended in, where it isn't Warcraft Logs' measure of the whole fight (a
    boss whose phases each start a new health bar, such as the Coiled Altar): how raiders count such a wipe ("P2 at
    68%"). None for a boss without phases or where the two agree."""
    health, pct = fight.get("bossPercentage"), wipe_pct(fight)
    if not phase_label(fight) or health is None or pct is None or abs(health - pct) < 0.05:
        return None
    return health


def by_phase(fights: Iterable[dict]) -> bool:
    """Whether wipes on this boss are counted by phase (phase_health): its % then needs saying it's WCL's."""
    return any(phase_health(f) is not None for f in fights)


def wipe_text(fight: dict, wcl: bool = True) -> str:
    """How far a wipe got as raiders say it: on a boss counted by phase, the phase and the boss's health in it, then
    (with `wcl`) Warcraft Logs' measure of the whole fight, which ranks wipes across phases ('P2 68.3% (WCL 70.5%)');
    otherwise the boss's % ('62.6%')."""
    if (health := phase_health(fight)) is None:
        return pct_text(wipe_pct(fight))
    return f"{phase_label(fight)} {health:.1f}%" + (f" (WCL {wipe_pct(fight):.1f}%)" if wcl else "")


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
    decided: Decided | None = None,
) -> PullSummary:
    """Build the summary for `fight`, using earlier pulls in `report_fights` for history. `boss_buff_events` are
    the bosses' buff events in this fight, for the buffs the boss still had at the end (e.g. an enrage);
    `routine_buffs` are buffs the boss lost during earlier pulls (see `end_buffs`); `guild_best` is the best wipe in
    the guild's earlier logs; `decided` when the wipe was decided, later deaths its fallout."""
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
            fallout=decided is not None and e["timestamp"] - fight["startTime"] > decided.at_ms,
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
        decided=decided,
        live=bool(fight.get("inProgress")),
        phase_health=phase_health(fight),
    )


def first_by_name(buffs: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """A buff several bosses gain (both of the Coiled Altar's, each Entombed Sentinel) once, from the first to gain
    it."""
    first: dict[str, float] = {}
    for name, at in buffs:
        first[name] = min(at, first.get(name, at))
    return list(first.items())


def best_marks(pct: float, tonight: float | None, before: GuildBest | None, wcl: str = "") -> list[str]:
    """How a wipe at `pct` compares with the log's earlier wipes (`tonight`) and the guild's earlier logs
    (`before`, None when it has none or they weren't read): "new guild best" only when it beats both. `wcl` names
    the % as Warcraft Logs' ("WCL ") on a boss with phases, where raiders count the phase's health instead."""
    marks = []
    if tonight is not None:
        marks.append("best of this log so far" if pct < tonight else f"best of this log {wcl}{tonight:.1f}%")
    if before is not None:
        if pct < min(before.pct, tonight if tonight is not None else before.pct):
            marks.append(f"**new guild best!** (before this log: {before.text(wcl)})")
        else:
            marks.append(f"guild best before this log {before.text(wcl)}")
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

    phased = s.phase is not None and s.phase_health is not None  # as raiders count it: the phase, its health
    wcl = "WCL " if phased else ""  # the % that ranks wipes, then, is Warcraft Logs' own
    if s.live:
        pct = f", the boss at {s.boss_pct:.1f}% so far" if s.boss_pct is not None else ""
        if phased:
            pct = f", {s.phase} at {s.phase_health:.1f}% so far"
        parts = [head, f"**in progress**{pct}"]
    else:
        pct = f" at **{s.boss_pct:.1f}%**" if s.boss_pct is not None else " (boss health not logged)"
        if phased:
            pct = f" in **{s.phase} at {s.phase_health:.1f}%**" + (
                f" (WCL {s.boss_pct:.1f}%)" if s.boss_pct is not None else "")
        parts = [head, f"{'reset' if s.reset else 'wipe'}{pct}"]
    if s.phase and not phased:
        parts.append(s.phase)
    parts.append(clock(s.duration_ms))
    if s.boss_pct is not None and not s.reset and not s.live:
        parts += best_marks(s.boss_pct, s.best_pct, s.guild_best, wcl)
    lines = [" · ".join(parts)]
    if s.boss_buffs:
        lines.append("Boss buffs at the end: " + " · ".join(f"**{name}** (from {clock(at)})"
                                                            for name, at in s.boss_buffs))

    if s.deaths:
        first = " · ".join(f"{d.player} ({d.ability}, {clock(d.at_ms)})" for d in s.deaths[:FIRST_DEATHS])
        lines.append(f"First deaths: {first}")
        counted = [d for d in s.deaths if not d.fallout]
        if s.decided is None or len(counted) == len(s.deaths):
            lines.append("Killed by: " + killers_text(counted))
        else:
            later = plural(len(s.deaths) - len(counted), "later death")
            lines.append(f"Killed by, until the wipe was decided at {clock(s.decided.at_ms)} ({s.decided.why}): "
                         + (killers_text(counted) or "none") + f" ({later}: fallout)")
    if len(s.trend) > 1 and not s.reset and not s.live:
        lines.append(f"Last {len(s.trend)}{' (WCL %)' if phased else ''}: " + " → ".join(f"{p:.1f}" for p in s.trend))
    return "\n".join(lines)
