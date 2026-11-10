"""The raid note (an NSRT note exported from Viserio Cooldowns): who was given which job, checked against a pull.

A note holds, per boss under an `EncounterID:3420;Difficulty:Mythic;Name:Sszorak` line:
- timed cooldowns, `time:72;ph:1;tag:Lirenna;spellid:98008;` (with `bossSpell:<id>`, the boss ability it's for);
- timed callouts to players or everyone, `time:11;ph:1;tag:Zuvyg Gutafaend ...;text:Soak Left;...`, a callout to one
  player at the moment an add begins a cast being a kick assignment (`text:Kick #1 left`);
- timed reminders, a spell line to everyone (`time:7;ph:2;tag:everyone;spellid:1285643;text:Go to diamond;`): the
  boss's cast or a move shown with an icon, which no one presses;
- the assignments as text: a heading naming the mechanic ("Mutilate Soaks"), then `Label: players` lines;
- the interrupt order, the lines of names between `intstart` and `intend`;
- an `invitelist:`.
A tag is a player's name (NSRT also takes a nickname, a role, a group, melee or ranged, a class or spec id), with or
without the realm. What isn't one of these lines is kept as not understood (`Note.unread`) and said.
"""
import itertools
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Collection, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from typing import Any

from ..knowledge import KnowledgeError
from ..pulls import DIFFICULTIES, Decided
from ..text import clock, name_key, plural, ranges, short
from . import saved
from .adds import interruptible_casts, kicked_tonight
from .base import REPEAT_CAST_MS, FactError, Player, PullRef, Report, dead_since, unavailable
from .cooldowns import spell_at

MATCH_S = 30  # a planned cooldown is matched to the player's nearest cast of it this close to its time
NEVER_CAST_MIN = 3  # pulls a planned spell was missed on, never cast at all, before the night says so
# Hits of an ability this close together are one hit of it (one soak). One landing's hits come within 50 ms in the
# log, and soaks that go group after group (The Twin Fangs' Ravenous Feast) land 1.5 s apart or more.
BURST_MS = 500
# A hit is a soak the note assigned when the note calls one of the section's groups to soak this close to it: on the
# recorded nights every soak landed within 15 s of a callout to its group, and the hits that were no group's soak (the
# ability's other use, in another stage) 31 s or more from any.
SOAK_CALL_MS = 20_000
# This few of a group's players in a hit, and under half the group, single no group out: a player or two standing in
# another group's soak look the same.
SOAK_FEW = 2
OUTSIDE, UNCLEAR = "outside the note's soak times", "can't tell whose soak"  # why a hit is given to no group
NO_GROUP = {OUTSIDE: f"outside the note's soak times (no group called within {SOAK_CALL_MS // 1000} s of them)",
            UNCLEAR: "where the log can't tell whose soak it was (a player or two of a group hit, or as many of two)"}
SOAKS_ARE = f"given to the group called to soak within {SOAK_CALL_MS // 1000} s of it with most of its players hit"
CALL_BEFORE_MS, CALL_AFTER_MS = 3_000, 5_000  # a callout is answered this close to its time
# A planned cooldown not cast this close before the wipe was decided is said to be: the raid was already falling
# apart under it. It still counts: a cooldown not pressed then may be why the pull was lost.
NEAR_END_MS = 10_000
PLANNED_NEAR_MS = 10_000  # a cooldown the note plans for a player this close to a callout is their answer to it
HELD_MS = 15_000  # a tank's cooldown pressed this soon after a callout's window was kept for what came next
EVERYONE = "everyone"
# A callout whose words ask for personal defensives ("Personals", "Big defs") is answered only by one: a spell Lorrgs
# tags defensive, or a tank's tank cooldown. It's the only kind of callout the log can check: another asks for
# something that isn't a tracked spell (grab an orb, CC a mind-controlled player), so it's listed, not checked.
PERSONALS = re.compile(r"\b(?:personals?|defensives?|defs?)\b", re.I)
PERSONAL_TAGS = frozenset({"defensive", "tank"})
PERSONALS_ARE = "a defensive or tank cooldown"
RUNNING = "already running"  # how a callout is answered by a defensive pressed before its window and still up
READY_ARE = ("off cooldown by Lorrgs' base cooldown, ignoring charges and cooldown reduction; one they never cast "
             "may be a talent they don't have or a passive")
UNCHECKED = ("Callouts not checked (what they ask, such as grabbing an orb or a CC, isn't a spell the log can match "
             "to them; only callouts for personals are)")
# A callout to one player whose words say kick ("Kick #1 left") this close to an add beginning a cast someone kicked
# on the boss tonight is a kick assignment, and its cast is one begun this close to it: which side's add begins first
# varies from wave to wave, so a note timed for one side is a second off for the other. Another job timed with a
# cast ("CC Mindcontrol") isn't one.
KICK_SLOT_MS = 1_500
KICK_WORDS = re.compile(r"\b(?:kicks?|interrupts?)\b", re.I)
# A player hit by fewer than this share of their group's soaks while alive, over at least FEW_SOAKS_MIN of them,
# most likely wasn't soaking with that group: a reserve, a spot changed since the note was written.
FEW_SOAKS = 0.2
FEW_SOAKS_MIN = 3
KICK_WAVE_GAP_MS = 5_000  # kick assignments this close together are one wave of adds
# The note's kick order came in at a pull when, in the pulls before, an assignment's casts were kicked by one other
# player (at least ORDER_KICKS of them, ORDER_SHARE of its casts) and by its player at most ASSIGNEE_SHARE, and from
# that pull on by its player (ORDER_SHARE): another order before, not one player missing. A tank kicking another's
# cast is a backup and doesn't count, nor does a backup who takes a few of a slot's casts all night. And it's another
# order only when the change is whole: the one who kicked the casts has no kick in the note, or their own was kicked
# by another before too, or the assignment's player wasn't in those pulls. Otherwise one player kicked a cast not
# theirs, pull after pull: a mistake the kicks show.
ORDER_KICKS, ORDER_SHARE, ASSIGNEE_SHARE = 4, 0.5, 0.25
# A kick assignment's cast that went through this close before the wipe was decided is named with the wipe: what
# it did (the add's spell) is for the boss's guide and the pull's ending to say.
LOST_AFTER_MS = 10_000
KICKS_ARE = ("Kick assignments (a callout to one player when an add begins a cast someone kicked on this boss "
             "that night). Which side's add begins first varies, so a note timed for one side is a second off for the "
             "other: each wave is read as noted or with each pair's casts swapped, whichever has more players kicking "
             f"their own, and a kick on another cast begun within {KICK_SLOT_MS / 1000:g} s of the callout counts too")


@dataclass
class Cooldown:
    at: float  # seconds from the start of phase `phase`
    phase: int
    player: str
    spell: int
    boss_spell: int | None = None  # the boss ability it's for


@dataclass
class Callout:
    at: float
    phase: int
    players: tuple[str, ...]  # () for everyone
    text: str


@dataclass
class Reminder:
    """A timed spell line to everyone: the boss's cast or a move shown with an icon, nothing a player presses."""
    at: float
    phase: int
    spell: int
    text: str = ""


@dataclass
class Note:
    encounter_id: int
    difficulty: str
    boss: str
    cooldowns: list[Cooldown] = field(default_factory=list)
    callouts: list[Callout] = field(default_factory=list)
    groups: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # heading -> label -> players
    text: str = ""  # this boss's part of the note, as pasted
    reminders: list[Reminder] = field(default_factory=list)
    interrupts: list[list[str]] = field(default_factory=list)  # the intstart block: each line's players, in order
    invited: list[str] = field(default_factory=list)  # the invitelist's names, without their realms
    unread: list[str] = field(default_factory=list)  # the lines that weren't understood, as written


HEADER = re.compile(r"^\s*EncounterID:\s*(\d+)\s*;", re.I | re.M)
TIMED = re.compile(r"(?:^|;)\s*time:", re.I)  # a timed line: NSRT reads a line's fields wherever they stand
INT_START, INT_END = "intstart", "intend"
# The tags NSRT takes that are no player's name (its Reminders.lua: a role, the raid group, melee or ranged, a class
# or spec id): the log can't say whom the addon showed such a line to.
NOT_A_NAME = re.compile(r"tank|healer|damager|melee|ranged|group\d|\d+", re.I)


def parse(text: str) -> list[Note]:
    """Every boss's note in a paste, in order. A paste without an EncounterID line is an error: it can't be tied to
    a boss."""
    starts = [m.start() for m in HEADER.finditer(text)]
    if not starts:
        raise FactError("the note has no EncounterID line: paste the whole note as exported, header included")
    return [_parse_one(text[a:b].strip()) for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]


def _fields(line: str) -> dict[str, str]:
    return {k.strip().lower(): v.strip() for k, _, v in (p.partition(":") for p in line.split(";")) if k.strip()}


def _bare(name: str) -> str:
    """A name without its realm ("Torvek-Kazzak"): a character's name has no hyphen."""
    return name.partition("-")[0] or name


def _numbered(name: str, taken: Collection[str]) -> str:
    """`name`, or "name (2)", "name (3)" when the note has one already: of a heading or a label written twice, both
    are kept."""
    return name if name not in taken else next(
        numbered for k in itertools.count(2) if (numbered := f"{name} ({k})") not in taken)


def _parse_one(text: str) -> Note:
    lines = text.splitlines()
    head = _fields(lines[0])
    note = Note(int(head["encounterid"]), head.get("difficulty", ""), head.get("name", ""), text=text)
    heading: str | None = None
    kicks = False  # inside the intstart block
    ends = max((i for i, line in enumerate(lines) if line.strip().lower() == INT_END), default=0)

    def close() -> None:
        """A heading with no line under it is words of the note that nothing reads."""
        if heading is not None and heading not in note.groups:
            note.unread.append(heading)
    for i, raw in enumerate(lines[1:], 1):
        line = raw.strip()
        if kicks:  # as NSRT reads it: every line up to intend is a line of names, an empty one skipped
            if line.lower() == INT_END:
                kicks = False
            elif line:
                note.interrupts.append([_bare(n) for n in line.split()])
        elif not line:
            close()
            heading = None
        elif line.lower() == INT_START and i < ends:  # one never closed isn't a block: its lines stay what they are
            close()
            heading, kicks = None, True
        elif TIMED.search(line):
            if not _timed(note, _fields(line)):
                note.unread.append(line)
        elif line.lower().startswith("invitelist:"):
            note.invited += [_bare(n.strip()) for n in line.partition(":")[2].strip(" ;").split(",") if n.strip()]
        elif ": " in line or line.endswith(":"):
            label, _, names = line.partition(":")
            section = note.groups.setdefault(heading or label.strip(), {})
            section[_numbered(label.strip(), section)] = names.split()
        else:
            close()
            heading = _numbered(line, note.groups)
    close()
    return note


def _timed(note: Note, f: dict[str, str]) -> bool:
    """A timed line's fields read into the note, as NSRT takes them: a time in seconds, a tag, and a spell or a
    text. False for a line it can't read: its time is no number of seconds (1:03 isn't), its phase no whole number,
    or it has no tag or neither a spell nor a text."""
    try:
        at, phase = float(f["time"]), int(f.get("ph") or 1)
    except (KeyError, ValueError):
        return False
    tags = [_bare(t) for t in f.get("tag", "").split()]
    everyone = [t.lower() for t in tags] == [EVERYONE]
    if not tags:
        return False
    if f.get("spellid", "").isdigit():
        if everyone:
            note.reminders.append(Reminder(at, phase, int(f["spellid"]), f.get("text", "")))
            return True
        boss_spell = int(f["bossspell"]) if f.get("bossspell", "").isdigit() else None
        note.cooldowns += [Cooldown(at, phase, t, int(f["spellid"]), boss_spell) for t in tags]
    elif f.get("text"):
        note.callouts.append(Callout(at, phase, () if everyone else tuple(tags), f["text"]))
    else:
        return False
    return True


def for_boss(notes: list[Note], encounter_id: int) -> Note | None:
    return next((n for n in reversed(notes) if n.encounter_id == encounter_id), None)


@dataclass
class Version:
    notes: list[Note]
    saved_at: float | None = None  # epoch seconds
    saved_by: str = ""

    def source(self) -> str:
        return saved.source(self.saved_at, self.saved_by) or "as pasted"


def versions(text: str) -> list[Version]:
    """The versions of a note in a paste, oldest first (saved.py)."""
    return [Version(parse(chunk), at, by) for chunk, at, by in saved.split(text)]


def _version(found: list[Version], start_s: float) -> Version:
    return saved.pick(found, start_s, lambda v: v.saved_at)


@dataclass
class SoakHit:
    at: float  # ms from the pull's start
    label: str  # the group it was given to; "" for a hit given to none (`why`), which counts for no one
    group: list[str]
    hit: set[str]
    size: float  # the median damage per player hit
    absent: list[tuple[str, str]]  # (player of the group not hit, their state)
    others: list[tuple[str, str | None]]  # (player hit from outside the group, their groups if any)
    why: str = ""  # OUTSIDE or UNCLEAR
    near: dict[str, int] = field(default_factory=dict)  # of an UNCLEAR hit: the groups called then, players hit


@dataclass
class Soaks:
    heading: str
    groups: dict[str, list[str]]  # label -> players, as in the report
    ability: int | None  # None: no ability of the heading's name hit players
    hits: list[SoakHit] = field(default_factory=list)
    total: int = 0  # every hit, counting those after the wipe was decided
    later: int = 0
    untimed: list[str] = field(default_factory=list)  # the groups the note never calls to soak (`_soak_calls`)


@dataclass
class PlannedUse:
    at: float
    cooldown: Cooldown
    cast_at: float | None  # the cast matched to it
    state: str  # the player's, when it was planned
    casts: list[float]  # all their casts of it in the pull
    # not cast, and it couldn't be: (their earlier cast it was on cooldown from, the cooldown in s that says so,
    # Lorrgs' base cooldown in s); the two differ when their own casts on the night show a shorter one
    cooling: tuple[float, float, float] | None = None


@dataclass
class Planned:
    uses: list[PlannedUse]
    later: list[tuple[float, Cooldown]]  # planned after `until`, so not checked against the pull
    skipped: int  # planned in phases the pull never reached
    twice: list[tuple[float, float, Cooldown]] = field(default_factory=list)  # (first, again, it): checked as one


@dataclass
class CalloutCheck:
    at: float
    callout: Callout
    in_pull: bool  # a callout to players: whether any of them was in the pull
    pressed: list[tuple[str, list[tuple[float, dict]]]]  # (player who answered it, [(ms, spell)])
    idle: list[tuple[str, list[dict]]]  # (player who didn't, the spells that answer it they had ready)
    dead: list[str]
    others: dict[str, list[tuple[float, dict]]] = field(default_factory=dict)  # of the idle, what they pressed instead
    # who pressed none but isn't counted as missing it: (player, why) for one already running (RUNNING), a cooldown
    # the note plans for them then, or a tank's cooldown kept for just after
    excused: list[tuple[str, str]] = field(default_factory=list)
    fell: dict[str, float] = field(default_factory=dict)  # of the idle, who died before its window closed: when


def answers(sp: dict) -> bool:
    """Whether pressing the spell answers a callout for personals: a defensive or a tank cooldown."""
    return bool(PERSONAL_TAGS & set(_tags(sp)))


@dataclass
class KickCheck:
    at: float  # the callout's time, ms from the pull's start
    callout: Callout
    player: str
    cast: tuple[float, str, str | None]  # the cast that was theirs: (ms it began, how it ended, who kicked it)
    kicked: bool
    state: str  # the player's, at the callout
    other: float | None = None  # their cast wasn't kicked and they kicked another of the wave: the ms that one began


@dataclass
class OrderChange:
    start: int  # the first pull (an index into the night's checks) the note's kick order fits
    slots: list[tuple[str, str, int, int]]  # (assignment, who kicked its casts before, how many, of how many)
    absent: list[str]  # named in the note, in none of the pulls before and in some after


@dataclass
class PullCheck:
    f: dict
    note: Note
    decided: Decided | None
    until: float
    soaks: list[Soaks]
    planned: Planned | None
    callouts: list[CalloutCheck]
    kicks: list[KickCheck]
    missing: list[str]  # named in the note, not in the pull
    unavailable: str = ""  # why the callouts couldn't be checked
    unchecked: list[tuple[float, Callout]] = field(default_factory=list)  # callouts the log can't check (PERSONALS)
    stray: dict[str, tuple[int, int]] = field(default_factory=dict)  # tags that are no player (`_stray`)


async def note_check(rep: Report, text: str, pull: PullRef = None, boss: str | None = None,
                     source: str = "", all_pulls: bool = False) -> str:
    """The note's part for the pull's boss, checked against the pull: the assignments, each soak group's hits
    (who of the group was hit, who wasn't and why, who else was), each planned cooldown against the player's casts,
    and the callouts to one player or to everyone. With `all_pulls`, every pull on the boss (each against the
    note's version for it), counted per player."""
    f = rep.fight(pull, boss)
    found = versions(text)
    if not any(for_boss(v.notes, f["encounterID"]) for v in found):
        raise FactError(f"the note has no part for {f['name']} (it has: "
                        + ", ".join(dict.fromkeys(n.boss or str(n.encounter_id) for v in found for n in v.notes)) + ")")
    if all_pulls:
        pulls = rep.by_boss[rep.boss_key(f)]
        return rep.still_going(pulls) + await _night(rep, found, rep.ended(pulls), source)
    v = _version(found, (rep.start_ms + f["startTime"]) / 1000)
    note = for_boss(v.notes, f["encounterID"]) or next(n for v in found if (n := for_boss(v.notes, f["encounterID"])))
    return _pull_text(rep, await _check(rep, f, note), source or (v.source() if v.saved_at else ""))


async def assigns_kicks(rep: Report, f: dict, text: str) -> bool:
    """Whether the note's version for pull `f` assigns kicks on its boss: a callout to one player as an add begins
    a cast someone kicked on the boss that night (`_kicks`). A note may assign none: its jobs are what the raid
    leader wrote in it."""
    found = versions(text)
    note = for_boss(_version(found, (rep.start_ms + f["startTime"]) / 1000).notes, f["encounterID"])
    if note is None:
        return False
    starts = _phase_starts(f)
    players = _players(rep, note)
    return bool(await _kicks(rep, f, note, players, lambda *_: "",
                             lambda c: None if c.phase not in starts else starts[c.phase] + c.at * 1000,
                             f["endTime"] - f["startTime"]))


def _phase_starts(f: dict) -> dict[int, float]:
    """Each phase's first start, in ms from the pull's start."""
    starts: dict[int, float] = {1: 0.0}
    for ph in f.get("phaseTransitions") or []:
        starts.setdefault(ph["id"], ph["startTime"] - f["startTime"])
    return starts


async def _check(rep: Report, f: dict, note: Note, night: _Night | None = None) -> PullCheck:
    """One pull against the note. `night`: the casts of the night's pulls on the boss, shared by a night's checks
    (read for this pull alone otherwise, and only when a callout or a planned cooldown needs them)."""
    night = night or _Night(rep, rep.by_boss[rep.boss_key(f)])
    t0, end = f["startTime"], f["endTime"] - f["startTime"]
    starts = _phase_starts(f)
    in_pull = {p.name for p in rep.pull_players(f).values()}
    players = _players(rep, note)
    stray = _stray(note, players)
    full, note = note, replace(  # what's checked: a line whose tags are no player is listed, not checked
        note, cooldowns=[c for c in note.cooldowns if c.player not in stray],
        callouts=[c for c in note.callouts if not c.players or not set(c.players) <= stray.keys()])
    deaths = await rep.deaths(f)
    backs = (await rep.resurrects([f]))[f["id"]]
    ids = {rep.actor(d["targetID"]): d["targetID"] for d in deaths}

    def died(name: str, at: float) -> float | None:
        """When a player died and stayed dead to `at`, in ms from the pull's start (a battle res brings them back);
        None for one alive then."""
        if (pid := ids.get(name)) is None:
            return None
        since = dead_since(deaths, backs.get(pid, []), pid, t0 + at)
        return None if since is None else since - t0
    decided = await rep.decided_at(f)
    await rep.name_spells({c.spell for c in full.cooldowns} | {c.boss_spell for c in full.cooldowns if c.boss_spell})

    def when(c: Cooldown | Callout) -> float | None:
        """Ms from the pull's start; None in a phase the pull never reached."""
        return None if c.phase not in starts else starts[c.phase] + c.at * 1000

    def state(name: str, at: float) -> str:
        p = players.get(name)
        if p is None or p.name not in in_pull:
            return "not in this pull"
        if (since := died(p.name, at)) is not None:
            return f"dead since {clock(since)}"
        return "alive"

    until = decided.at_ms if decided else end  # what came after the wipe was decided is only counted
    kicks = await _kicks(rep, f, note, players, state, when, until)
    calls, unchecked = _callouts_to_check(note, when, until, {id(k.callout) for k in kicks})
    try:
        callouts, why = await _callouts(rep, f, note, players, died, when, calls, night), ""
    except KnowledgeError as e:
        callouts, why = [], unavailable("Callouts (Lorrgs' spells for each spec)", e)
    return PullCheck(f, full, decided, until, await _soaks(rep, f, note, players, state, when, until),
                     await _cooldowns(rep, f, note, players, state, when, until, full, night), callouts, kicks,
                     sorted(n for n in _people(note, players) - stray.keys()
                            if n not in players or players[n].name not in in_pull), why, unchecked, stray)


def _named(note: Note) -> set[str]:
    return ({c.player for c in note.cooldowns} | {n for c in note.callouts for n in c.players}
            | {n for g in note.groups.values() for ns in g.values() for n in ns}
            | {n for line in note.interrupts for n in line})


def _players(rep: Report, note: Note) -> dict[str, Player]:
    """The report's player each name in the note means, by the name as written: its realm left off, and a tag that
    is a role or a group (NOT_A_NAME) no one's name."""
    return {n: p for n in _named(note) if not NOT_A_NAME.fullmatch(n) and (p := rep.find_player(_bare(n)))}


def _known(note: Note, players: Mapping[str, Player]) -> Callable[[str], bool]:
    """Whether a word of the note is someone's name: a player of the report, or on the note's invite list."""
    invited = {name_key(n) for n in note.invited}
    return lambda n: n in players or name_key(_bare(n)) in invited


def _stray(note: Note, players: Mapping[str, Player]) -> dict[str, tuple[int, int]]:
    """The tags on timed lines that are no one's name (`_known`): a role, a group, a nickname, which the addon shows
    to players the log can't name. Per tag, how many cooldown lines and callouts carry it: listed, not checked, and
    never a player who "wasn't in the pull"."""
    known = _known(note, players)
    out: dict[str, tuple[int, int]] = {}
    for n in (c.player for c in note.cooldowns):
        if not known(n):
            out[n] = (out.get(n, (0, 0))[0] + 1, 0)
    for n in (n for c in note.callouts for n in c.players):
        if not known(n):
            out[n] = (out.get(n, (0, 0))[0], out.get(n, (0, 0))[1] + 1)
    return out


def _people(note: Note, players: Mapping[str, Player]) -> set[str]:
    """The names the note gives a job: the tags of its timed lines, and the words of its assignment lines and
    interrupt order, of a line at least half of whose words are someone's name (`_known`). Another line is the
    note's words ("Swap: at 2 stacks, Torvek first"), not a list of players."""
    known = _known(note, players)
    listed = [ns for g in note.groups.values() for ns in g.values()] + note.interrupts
    return ({c.player for c in note.cooldowns} | {n for c in note.callouts for n in c.players}
            | {n for ns in listed if 2 * sum(map(known, ns)) >= len(ns) for n in ns})


def _head(note: Note) -> str:
    return f"The raid note for {note.boss or 'the boss'}" + (f" ({note.difficulty})" if note.difficulty else "")


def _other_difficulty(note: Note, fights: list[dict]) -> str:
    """' The note is for Mythic and this pull is Heroic: ...' when the note names another difficulty than the pulls
    it's checked against have (a note is found by its boss alone), else ''."""
    theirs = {DIFFICULTIES.get(f.get("difficulty") or 0, "") for f in fights} - {""}
    if not note.difficulty or not theirs or note.difficulty.lower() in {d.lower() for d in theirs}:
        return ""
    what = "this pull is" if len(fights) == 1 else "these pulls are"
    return (f" The note is for {note.difficulty} and {what} {' and '.join(sorted(theirs))}: its times and jobs may "
            "not fit them.")


def _assignments(rep: Report, note: Note) -> list[str]:
    """What the note says besides its checked lines, as written: the assignments, the interrupt order and the
    reminders to everyone."""
    lines = []
    if note.groups:
        lines = ["Assignments in the note's text:"] + [
            f"  {heading}: " + "; ".join(f"{label}: {' '.join(ns)}" for label, ns in groups.items())
            for heading, groups in note.groups.items()]
    if note.interrupts:  # which add a line is for is the addon's rule for the boss: the lines are numbered, no more
        lines.append("Interrupt order (the note's intstart block, each line in the addon's order; not checked against "
                     "the log): " + "; ".join(f"line {i}: {' '.join(names)}"
                                              for i, names in enumerate(note.interrupts, 1)))
    if note.reminders:
        said = Counter(f'"{r.text}"' if r.text else rep.ability_ref(r.spell) for r in note.reminders)
        lines.append(f"Reminders to everyone ({plural(len(note.reminders), 'timed line')} with a spell and no player "
                     "to press it: the boss's cast or a move, shown with an icon), not checked: "
                     + ", ".join(what + (f" {n} times" if n > 1 else "") for what, n in said.items()))
    return lines


UNREAD_SHOWN = 100  # characters of the first line not understood that are shown


def receipt(note: Note) -> str:
    """What was read from a boss's note, for the officer who saved it: '59 timed cooldowns, 28 callouts'."""
    return (f"{plural(len(note.cooldowns), 'timed cooldown')}, {plural(len(note.callouts), 'callout')}"
            + (f", {plural(len(note.reminders), 'reminder')} to everyone" if note.reminders else "")
            + (f", an interrupt order of {plural(len(note.interrupts), 'line')}" if note.interrupts else ""))


def not_understood(note: Note) -> str:
    """'not understood: 3 lines, the first "time:1:03;ph:1;..."', or '' for a note read whole."""
    if not note.unread:
        return ""
    first = note.unread[0]
    return (f"not understood: {plural(len(note.unread), 'line')}, the first \"{first[:UNREAD_SHOWN]}"
            + ("..." if len(first) > UNREAD_SHOWN else "") + "\"")


def _not_checked(note: Note, stray: Mapping[str, tuple[int, int]]) -> list[str]:
    """What of the note no check could take up, each said once: the tags that are no player, and the lines that
    weren't understood. Nothing for a note read whole."""
    lines = []
    if stray:
        lines.append("Tags that are no player of the report and no name on the note's invite list (a role, a group or "
                     "a nickname, which the log can't match to players), their lines not checked: " + ", ".join(
                         f"{tag} (" + ", ".join(plural(n, what) for n, what in ((cds, "cooldown"), (calls, "callout"))
                                                 if n) + ")" for tag, (cds, calls) in sorted(stray.items())))
    if unread := not_understood(note):
        lines.append(f"Lines of the note {unread} (a timed line has a time in seconds, a whole phase, a tag, and a "
                     "spell or a text; an assignment is a heading with \"Label: players\" lines under it)")
    return lines


def _pull_text(rep: Report, c: PullCheck, source: str) -> str:  # noqa: C901, PLR0915
    f, end = c.f, c.f["endTime"] - c.f["startTime"]
    lines = [_head(c.note).replace("the boss", f["name"]) + (f", {source}" if source else "")
             + f", checked against {rep.label(f)} ({clock(end)}, " + ("KILL" if f["kill"] else "wipe")
             + f", {rep.url(f)})" + (f"; the wipe was decided at {clock(c.decided.at_ms)} ({c.decided.why})"
                                    if c.decided else "")
             + ". Note times count from the pull's start (ph:1) or from the phase's first start."
             + _other_difficulty(c.note, [f])]
    lines += _assignments(rep, c.note)
    lines += _pull_soaks(rep, c.soaks, c.until)
    if c.planned:
        lines.append("Planned cooldowns (time in the note: what happened):")
        for u in c.planned.uses:
            cd = u.cooldown
            line = f"  {clock(u.at)} {cd.player} {rep.ability_ref(cd.spell)}"
            if cd.boss_spell:
                line += f" for {rep.ability_ref(cd.boss_spell)}"
            if u.cast_at is not None:
                d = (u.cast_at - u.at) / 1000
                line += f": cast at {clock(u.cast_at)}, " + ("on time" if abs(d) < 1 else
                                                              f"{abs(d):.0f} s {'late' if d > 0 else 'early'}")
            else:
                line += f": not cast within {MATCH_S} s ({u.state}"
                if u.casts:
                    line += "; cast at " + ", ".join(clock(x) for x in u.casts)
                if u.cooling:
                    since, cooldown, base = u.cooling
                    line += f"; on cooldown from the {clock(since)} cast, " + (
                        f"{base:g} s by Lorrgs' base cooldown" if cooldown == base else
                        f"{cooldown:.0f} s by their closest two casts of it on the night (Lorrgs' base cooldown: "
                        f"{base:g} s)")
                if u.state == "alive" and c.until - u.at <= NEAR_END_MS:
                    line += f"; {(c.until - u.at) / 1000:.0f} s before " + (
                        "the wipe was decided" if c.until < end else "the pull ended")
                line += ")"
            lines.append(line)
        if c.planned.later:  # still the note's plan: listed, so a review of the note sees all of it
            lines.append("  Planned after " + (
                f"the wipe was decided at {clock(c.until)}" if c.until < end else f"the pull ended at {clock(end)}")
                + ", so not checked against this pull:")
            lines += [f"  {clock(at)} {cd.player} {rep.ability_ref(cd.spell)}"
                      + (f" for {rep.ability_ref(cd.boss_spell)}" if cd.boss_spell else "")
                      for at, cd in c.planned.later]
        if c.planned.skipped:
            lines.append(f"  {c.planned.skipped} planned in phases the pull never reached")
        lines += [f"  {_twice(rep, t)}" for t in c.planned.twice]
    if c.callouts:
        lines.append(f"Callouts for personals, with what each player it names pressed from {CALL_BEFORE_MS // 1000} "
                     f"s before to {CALL_AFTER_MS // 1000} s after it (the spells Lorrgs tracks for their spec, with "
                     f"its tags; only {PERSONALS_ARE} answers it), who isn't counted as missing it (one was {RUNNING}, "
                     "pressed earlier and still up by Lorrgs' duration, which talents change; or as the line says), "
                     "and for those who pressed none, which of the ones that answer it they had ready, of those they "
                     f"cast at least once on the night's pulls ({READY_ARE}):")
    for k in c.callouts:
        line = f"  {clock(k.at)} {' '.join(k.callout.players) or 'everyone'}: \"{k.callout.text}\""
        if not k.in_pull:
            lines.append(line + ": not in this pull")
            continue
        if not k.pressed and not k.idle and not k.excused:
            lines.append(line + ": dead by then (" + ", ".join(k.dead) + ")")
            continue
        running = sum(why.startswith(RUNNING) for _, why in k.excused)
        lines.append(line + f": of {len(k.pressed) + len(k.idle) + len(k.excused)} alive, {len(k.pressed)} pressed "
                     f"{PERSONALS_ARE}, {len(k.idle)} pressed none"
                     + (f" ({len(k.others)} of them only other spells)" if k.others else "")
                     + (f", {len(k.excused)} not counted" + (f" ({running} with one {RUNNING})" if running else "")
                        if k.excused else ""))
        if k.pressed:
            lines.append("    pressed: " + "; ".join(
                f"{n} " + ", ".join(f"{_spell(sp)} {clock(t)}" for t, sp in mine) for n, mine in k.pressed))
        if k.idle:
            lines.append("    pressed none: " + "; ".join(
                f"{n} (" + (f"died at {clock(k.fell[n])}; " if n in k.fell else "")
                + (f"pressed only {', '.join(f'{_spell(sp)} {clock(t)}' for t, sp in k.others[n])}; "
                   if n in k.others else "")
                + f"ready: {', '.join(_spell(sp) for sp in ready) or 'none'})" for n, ready in k.idle))
        if k.excused:
            lines.append("    pressed none, not counted against them: " + "; ".join(f"{n} ({why})"
                                                                              for n, why in k.excused))
        if k.dead:
            lines.append("    dead: " + ", ".join(k.dead))
    if c.unchecked:
        lines.append(UNCHECKED + ": " + "; ".join(
            f"{clock(at)} {' '.join(co.players) or EVERYONE} \"{co.text}\"" for at, co in c.unchecked))
    if c.kicks:
        lines.append(KICKS_ARE + ", each with the cast that was theirs and what became of it (covered: another "
                     "player kicked it first, so it was handled and was no one's miss):")
    for k in c.kicks:
        lines.append(f"  {clock(k.at)} {k.player} \"{k.callout.text}\": {_kick_line(k)}")
    if before := through_before(c):
        lines.append(f"  Went through in the {LOST_AFTER_MS // 1000} s before the wipe was decided at "
                     f"{clock(c.until)}: " + ", ".join(_through(k) for k in before))
    if c.unavailable:
        lines.append(c.unavailable)
    lines += _not_checked(c.note, c.stray)
    if c.missing:
        lines.append("Named in the note but not in this pull: " + ", ".join(c.missing))
    return "\n".join(lines)


def _pull_soaks(rep: Report, soaks: list[Soaks], until: float) -> list[str]:
    """Each soak section's hits on one pull up to `until`, in order: the group a hit was given to with who of it
    wasn't hit and who else was, or why it was given to none."""
    lines = []
    for s in soaks:
        if s.ability is None:
            lines.append(f"{s.heading}: no ability of that name hit players on this pull")
            continue
        if s.later and not s.hits:  # nothing to check, so no rule to state
            lines.append(f"{s.heading}: {plural(s.total, 'hit')} of {rep.ability_ref(s.ability)}, after the wipe was "
                         f"decided at {clock(until)}: not checked")
            continue
        lines.append(f"{s.heading}: each hit of {rep.ability_ref(s.ability)}, {SOAKS_ARE} ({plural(s.total, 'hit')}):")
        took: Counter = Counter()
        alive: Counter = Counter()
        for h in s.hits:
            if not h.label:
                lines.append(f"  {clock(h.at)} {h.why}: {plural(len(h.hit), 'player')} hit"
                             + (" (" + ", ".join(f"{label} {n}" for label, n in h.near.items()) + ")" if h.near else "")
                             + f", {short(h.size)} each; counted for no one")
                continue
            for n in h.group:
                took[(n, h.label)] += n in h.hit
                alive[(n, h.label)] += n in h.hit or (n, "alive") in h.absent
            line = f"  {clock(h.at)} {h.label}: {len(h.hit & set(h.group))} of {len(h.group)} hit, {short(h.size)} each"
            if h.absent:
                line += "; not hit: " + ", ".join(f"{n} ({st})" for n, st in h.absent)
            if h.others:
                line += "; also hit: " + ", ".join(f"{n} ({g})" if g else n for n, g in h.others)
            lines.append(line)
        lines += _untimed([s])
        if few := _few_soaks(took, alive):
            lines.append(few)
        if s.later:
            lines.append(f"  {s.later} more after the wipe was decided at {clock(until)}")
    return lines


def _by_count(counts: Counter) -> list[tuple[str, int]]:
    """Most first, ties by name: the same order on every run (tags come from sets)."""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _tags(sp: dict) -> list[str]:
    return sp.get("tags") or ["untagged"]


def _spell(sp: dict) -> str:
    return f"{sp['name']} [{', '.join(sp.get('tags') or []) or 'untagged'}]"


def _ability_of(rep: Report, heading: str, hit_ids: set[int]) -> list[int]:
    """The ids of the ability a heading names ("Mutilate Soaks" -> Mutilate), among those that hit players: the
    longest run of the heading's first words that is an ability's name."""
    words = heading.split()
    for k in range(len(words), 0, -1):
        if ids := sorted(rep.ids_named([" ".join(words[:k])]) & hit_ids):
            return ids
    return []


def _soak_sections(note: Note) -> dict[str, dict[str, list[str]]]:
    """The note's sections of groups that get timed callouts: soak groups, not a spread list."""
    called = {frozenset(c.players) for c in note.callouts if len(c.players) > 1}
    return {h: gs for h, gs in note.groups.items() if any(frozenset(ns) in called for ns in gs.values() if len(ns) > 1)}


DAMAGE_BY_ABILITY: dict[str, Any] = {"hostilityType": "Friendlies", "viewBy": "Ability"}


def _soak_calls(note: Note, groups: dict[str, list[str]], when) -> dict[str, list[float] | None]:
    """When the note calls each group of a soak section to soak on a pull, in ms from its start: the times of the
    callouts to exactly the group's players. None for a group the note has no such callout for: the note doesn't
    time its soaks, so a hit at any time may be its. A label with no players has no soaks: no times, and not None."""
    out: dict[str, list[float] | None] = {}
    for label, names in groups.items():
        mine = [c for c in note.callouts if names and frozenset(c.players) == frozenset(names)]
        out[label] = [at for c in mine if (at := when(c)) is not None] if mine or not names else None
    return out


def _whose(hit: AbstractSet[str], at: float, members: dict[str, list[str]],
           calls: dict[str, list[float] | None]) -> tuple[str, str, dict[str, int]]:
    """The group a hit of a soak section's ability is given to, or "" with why it's given to none, and how many
    players of each group it could be given to were hit. Those are the groups the note calls to soak within
    SOAK_CALL_MS of the hit, and the ones it never times (`_soak_calls`); of them, the one with most of its players
    hit. With none called then, and no player hit of a group it never times, the hit is OUTSIDE the note's soak
    times. UNCLEAR is a hit that singles none out: no player of theirs hit, as many of two groups', or of the one
    with most only SOAK_FEW or fewer and under half of it. Two labels for the same players (the first soak and the
    third) are one group, under the first."""
    near = {label: len(hit & set(members[label])) for label, times in calls.items()
            if times is None or any(abs(t - at) <= SOAK_CALL_MS for t in times)}
    if not any(n or calls[label] is not None for label, n in near.items()):
        return "", OUTSIDE, {}
    label, most = max(near.items(), key=lambda kv: kv[1])
    tied = len({frozenset(members[g]) for g, n in near.items() if n == most}) > 1
    if not most or tied or (most <= SOAK_FEW and 2 * most < len(members[label])):
        return "", UNCLEAR, near
    return label, "", near


async def _soaks(rep: Report, f: dict, note: Note, players: dict[str, Player], state, when,
                 until: float) -> list[Soaks]:
    """Per section of groups that get timed callouts (soak groups, not a spread list): each hit of the section's
    ability, given to the group it was the soak of (`_whose`) or to none."""
    sections = _soak_sections(note)
    if not sections:
        return []
    t0 = f["startTime"]
    t = await rep.table(f["id"], "DamageTaken", **DAMAGE_BY_ABILITY)
    hit_ids = {e["guid"] for e in t.get("entries") or []}
    out = []
    for heading, groups in sections.items():
        members = {label: [players[n].name if n in players else n for n in ns] for label, ns in groups.items()}
        ids = _ability_of(rep, heading, hit_ids)
        if not ids:
            out.append(Soaks(heading, members, None))
            continue
        hits = [e for i in ids for e in await rep.events(f["id"], "DamageTaken", hostilityType="Friendlies",
                                                          abilityID=i)
                if e.get("type") in ("damage", "absorbed") and e.get("targetID") in rep.names and not e.get("tick")]
        bursts: list[list[dict]] = []
        for e in sorted(hits, key=lambda e: e["timestamp"]):
            if bursts and e["timestamp"] - bursts[-1][-1]["timestamp"] <= BURST_MS:
                bursts[-1].append(e)
            else:
                bursts.append([e])
        calls = _soak_calls(note, groups, when)
        section = Soaks(heading, members, ids[0], total=len(bursts),
                        untimed=[label for label, times in calls.items() if times is None])
        if later := sum(b[0]["timestamp"] - t0 > until for b in bursts):
            bursts = bursts[:-later]
        section.later = later
        owner: dict[str, list[str]] = defaultdict(list)  # a player may be in several groups (a soak in each stage)
        for label, ns in members.items():
            for n in ns:
                owner[n].append(label)
        for burst in bursts:
            at = burst[0]["timestamp"] - t0
            hit = {rep.actor(e["targetID"]) for e in burst}
            size = sorted(e.get("amount", 0) + e.get("absorbed", 0) for e in burst)[len(burst) // 2]
            label, why, near = _whose(hit, at, members, calls)
            group = members[label] if label else []
            section.hits.append(SoakHit(
                at, label, group, hit, size, [(n, state(n, at)) for n in group if n not in hit],
                [(n, "/".join(owner[n]) or None) for n in sorted(hit - set(group))] if label else [], why, near))
        out.append(section)
    return out


async def _cooldowns(rep: Report, f: dict, note: Note, players: dict[str, Player], state, when,  # noqa: PLR0913
                     until: float, whole: Note | None = None,
                     night: Callable[[], Awaitable[Tonight]] | None = None) -> Planned | None:
    """Each planned cooldown against the player's casts of it on the pull: the nearest within MATCH_S, each cast
    used for one line only. A channel's casts in a row (Divine Hymn logs each tick) are one use. The casts are
    asked for by the `whole` note's spells (the note before the lines to no player were left out), as the night's
    one query asks for them (`_prefetch`)."""
    if not note.cooldowns:
        return None
    t0 = f["startTime"]
    planners = {c.player for c in note.cooldowns}
    here = {n: p for n, p in players.items() if n in planners and rep.was_in(f, p)}
    by_source: dict[int, list[dict]] = defaultdict(list)
    for e in await _planned_casts(rep, f, whole or note):
        by_source[e.get("sourceID", -1)].append(e)
    casts: dict[tuple[str, int], list[float]] = defaultdict(list)  # (note name, spell) -> ms from the pull's start
    for name, p in here.items():
        wanted = {c.spell: rep.ids_named([rep.ability(c.spell)]) | {c.spell} for c in note.cooldowns
                  if c.player == name}
        for e in by_source[p.id]:
            for spell, ids in wanted.items():
                if e["abilityGameID"] in ids:
                    at = e["timestamp"] - t0
                    if not (seen := casts[(name, spell)]) or at - seen[-1] > REPEAT_CAST_MS:
                        seen.append(at)
    planned, twice = [], []
    for c in sorted((c for c in note.cooldowns if when(c) is not None), key=when):
        first = next((p for p in planned if (p.player, p.spell, p.boss_spell) == (c.player, c.spell, c.boss_spell)
                      and when(c) - when(p) <= REPEAT_CAST_MS), None)
        if first is None:
            planned.append(c)
        else:  # one use written twice (0:42 and 0:43): one cast can't cover both, so one would read as not cast
            twice.append((when(first), when(c), c))
    pairs = sorted((abs(at - when(c)), i, j, at) for i, c in enumerate(planned)
                   for j, at in enumerate(casts[(c.player, c.spell)]) if abs(at - when(c)) <= MATCH_S * 1000)
    matched: dict[int, float] = {}
    used: set[tuple[str, int, int]] = set()
    for _, i, j, at in pairs:
        key = (planned[i].player, planned[i].spell, j)
        if i not in matched and key not in used:
            matched[i] = at
            used.add(key)
    uses = [PlannedUse(when(c), c, matched.get(i), state(c.player, when(c)), casts[(c.player, c.spell)])
            for i, c in enumerate(planned) if when(c) <= until]
    await _cooling(rep, f, here, uses, night)
    return Planned(uses, [(when(c), c) for c in planned if when(c) > until],
                   len(note.cooldowns) - len(planned) - len(twice), twice)


async def _cooling(rep: Report, f: dict, here: dict[str, Player], uses: list[PlannedUse],
                   night: Callable[[], Awaitable[Tonight]] | None) -> None:
    """Marks the planned uses a living player didn't cast while the spell was on cooldown from their own earlier
    cast of it in the pull (`spell_at`): the note plans it again too soon, or they pressed it off the plan. The
    cooldown is Lorrgs' base one, or the time between their closest two casts of it on a pull of the night when
    that is shorter (`Tonight.gap`: a talent that shortens it, or a second charge, shows there). None is marked when
    Lorrgs can't be read: the uses are then as they were."""
    try:
        for u in uses:
            if u.cast_at is not None or u.state != "alive" or not any(t < u.at for t in u.casts):
                continue
            q = rep.in_pull(f, here[u.cooldown.player])
            sp = next((sp for sp in await rep.spells(q) if rep.is_spell({"abilityGameID": u.cooldown.spell}, sp)), None)
            if sp is None or not sp.get("cooldown"):
                continue
            own = (await night()).gap.get((q.id, sp["name"])) if night else None
            cooldown = min(sp["cooldown"], own) if own else sp["cooldown"]
            if (since := spell_at(sp, u.casts, u.at, cooldown).cooling) is not None:
                u.cooling = (since, cooldown, sp["cooldown"])
    except KnowledgeError:
        return


def _planned_filter(rep: Report, note: Note) -> str:
    """The filter for the casts of the note's planned cooldowns: their ids, and other ids of the same names."""
    ids = {c.spell for c in note.cooldowns} | rep.ids_named(rep.ability(c.spell) for c in note.cooldowns)
    return f"ability.id in ({', '.join(map(str, sorted(ids)))})"


async def _planned_casts(rep: Report, f: dict, note: Note) -> list[dict]:
    """Everyone's casts of the note's planned cooldowns in a pull, in one query."""
    casts = await rep.events(f["id"], "Casts", filterExpression=_planned_filter(rep, note))
    return [c for c in casts if c.get("type") == "cast"]


def _callouts_to_check(note: Note, when, until: float,
                       kicks: set[int]) -> tuple[list[tuple[float, Callout]], list[tuple[float, Callout]]]:
    """The callouts up to `until` that aren't a soak group's or a kick assignment (`kicks`, their ids), in order:
    those asking for personals, which the log can check, and the rest, which it can't (PERSONALS)."""
    soak = {frozenset(ns) for gs in _soak_sections(note).values() for ns in gs.values()}
    calls = sorted(((at, c) for c in note.callouts if frozenset(c.players) not in soak and id(c) not in kicks
                    and (at := when(c)) is not None and at <= until),
                   key=lambda x: x[0])
    return [x for x in calls if PERSONALS.search(x[1].text)], [x for x in calls if not PERSONALS.search(x[1].text)]


@dataclass
class Tonight:
    """What the players of a boss's pulls pressed of the spells Lorrgs tracks for their specs, read once for the
    night (`_tonight`): per pull for its callouts, and over the pulls for which spells a player has at all."""
    kits: dict[int, dict[int, list[dict]]] = field(default_factory=dict)  # pull -> player -> their spec's spells
    # pull -> player -> their casts of those: (ms from the pull's start, spell), a channel's ticks once
    pressed: dict[int, dict[int, list[tuple[float, dict]]]] = field(default_factory=dict)
    ever: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))  # player -> the spells they cast
    # (player, spell) -> the seconds between their closest two casts of it on one pull: a cooldown shorter than
    # Lorrgs' base one (a talent, a second charge) shows here
    gap: dict[tuple[int, str], float] = field(default_factory=dict)


class _Night:
    """The night's `Tonight`, read when first asked for and once: a pull's callouts and its planned cooldowns both
    read it, and the pulls of a night share it. Lorrgs failing is kept too, so every pull says so without asking
    again."""

    def __init__(self, rep: Report, fights: list[dict]):
        self.rep, self.fights = rep, fights
        self.got: Tonight | None = None
        self.failed: KnowledgeError | None = None

    async def __call__(self) -> Tonight:
        if self.failed:
            raise self.failed
        if self.got is None:
            try:
                self.got = await _tonight(self.rep, self.fights)
            except KnowledgeError as e:
                self.failed = e
                raise
        return self.got


async def _tonight(rep: Report, fights: list[dict]) -> Tonight:
    """`Tonight` over these pulls, their casts fetched in one query (Report.tracked_casts keeps each pull's)."""
    out = Tonight()
    casts = await rep.tracked_casts(fights)
    which: dict[tuple[int, int], dict | None] = {}  # (a spec's spells, an ability) -> the spell it is a cast of
    for f in fights:
        here = rep.pull_players(f)
        kits = out.kits[f["id"]] = {q.id: await rep.spells(q) for q in here.values()}
        pressed = out.pressed[f["id"]] = defaultdict(list)
        for c in casts[f["id"]]:
            if (q := here.get(c.get("sourceID"))) is None:
                continue
            key = (id(kits[q.id]), c["abilityGameID"])
            if key not in which:
                which[key] = next((sp for sp in kits[q.id] if rep.is_spell(c, sp)), None)
            if (sp := which[key]) is None:
                continue
            out.ever[q.id].add(sp["name"])
            seen, at = pressed[q.id], c["timestamp"] - f["startTime"]
            if not any(used is sp and at - t <= REPEAT_CAST_MS for t, used in seen[-3:]):  # a channel's ticks: once
                if (before := next((t for t, used in reversed(seen) if used is sp), None)) is not None:
                    key = (q.id, sp["name"])
                    out.gap[key] = min(out.gap.get(key, math.inf), (at - before) / 1000)
                seen.append((at, sp))
    return out


async def _callouts(rep: Report, f: dict, note: Note, players: dict[str, Player], died: Callable, when,
                    calls: list[tuple[float, Callout]],
                    night: Callable[[], Awaitable[Tonight]] | None = None) -> list[CalloutCheck]:
    """Each callout for personals (a call to everyone such as "Big Personals", or to some players), with what the
    players it names pressed around it: their casts of the spells Lorrgs tracks for their spec, with the spell's
    tags, and whether one answers it (`answers`). A cast after they died inside its window isn't one (a shaman's
    Reincarnation). One who pressed none but had one running from before the window (`spell_at`) isn't counted as
    missing it. For the others: which of the spells that answer it were ready, of those they cast at least once on
    the night's pulls on the boss (`night`), and what they pressed instead."""
    if not calls:
        return []
    here = rep.pull_players(f)
    tonight = await (night or _Night(rep, rep.by_boss[rep.boss_key(f)]))()
    kits, pressed_at = tonight.kits[f["id"]], tonight.pressed[f["id"]]
    fell: dict[int, list[float]] = defaultdict(list)  # player -> when they died, ms from the pull's start
    for d in await rep.deaths(f):
        fell[d["targetID"]].append(d["timestamp"] - f["startTime"])
    out = []
    for at, c in calls:
        named_here = [players[n] for n in c.players if n in players and players[n].id in here]
        targets = named_here if c.players else sorted(here.values(), key=lambda q: q.name)
        check = CalloutCheck(at, c, bool(named_here) or not c.players, [], [], [])
        for q in targets:
            if died(q.name, at) is not None:
                check.dead.append(q.name)
                continue
            fell_at = next((t for t in fell[q.id] if at < t <= at + CALL_AFTER_MS), None)
            mine = [(t, sp) for t, sp in pressed_at[q.id] if at - CALL_BEFORE_MS <= t <= at + CALL_AFTER_MS
                    and (fell_at is None or t < fell_at)]
            if any(answers(sp) for _, sp in mine):
                check.pressed.append((q.name, mine))
                continue
            stood = [(sp, spell_at(sp, [t for t, used in pressed_at[q.id] if used is sp], at))
                     for sp in kits[q.id] if answers(sp)]
            if up := [(sp, x.running) for sp, x in stood if x.running is not None]:
                check.excused.append((q.name, f"{RUNNING}: " + ", ".join(
                    f"{_spell(sp)} pressed at {clock(t)}, lasting {sp['duration']:g} s" for sp, t in up)))
                continue
            if why := _excused(rep, note, players, q, at, when, pressed_at[q.id]):
                check.excused.append((q.name, why))
                continue
            if mine:
                check.others[q.name] = mine
            if fell_at is not None:
                check.fell[q.name] = fell_at
            check.idle.append((q.name, [sp for sp, x in stood if sp.get("cooldown") and x.ready
                                        and sp["name"] in tonight.ever[q.id]]))
        out.append(check)
    return out


def _excused(rep: Report, note: Note, players: dict[str, Player], q: Player, at: float, when,
             pressed: list[tuple[float, dict]]) -> str:
    """Why a player who pressed no personal at a callout isn't counted as missing it: they pressed a cooldown the note
    plans for them within PLANNED_NEAR_MS of it (what it asks of them then), or they tank and pressed a tank cooldown
    just after the callout's window (kept for what came next); "" for neither."""
    for cd in note.cooldowns:
        if (o := players.get(cd.player)) and o.id == q.id and (t := when(cd)) is not None \
                and abs(t - at) <= PLANNED_NEAR_MS:
            for t, sp in pressed:
                if abs(t - at) <= PLANNED_NEAR_MS and rep.is_spell({"abilityGameID": cd.spell}, sp):
                    return f"pressed {sp['name']} at {clock(t)}, the note's plan for them then"
    if q.role == "tank":
        for t, sp in pressed:
            if at + CALL_AFTER_MS < t <= at + HELD_MS and "tank" in _tags(sp):
                return f"a tank, kept {sp['name']} for {clock(t)}"
    return ""


def _kick_call(c: Callout) -> bool:
    """Whether a callout may be a kick assignment: to one player, its words saying kick (KICK_WORDS)."""
    return len(c.players) == 1 and bool(KICK_WORDS.search(c.text))


async def _kicks(rep: Report, f: dict, note: Note, players: dict[str, Player], state, when,
                 until: float) -> list[KickCheck]:
    """Each kick assignment up to `until`: a callout to one player within KICK_SLOT_MS of an add beginning a cast
    someone kicked on the boss tonight ("Kick #1 left"), with the cast that was theirs and whether they kicked it.
    Which side's add begins first varies from wave to wave, so a note timed for one side is a second off for the
    other: of each wave's assignments, taken in pairs (left and right), the casts are read as noted or with each
    pair's swapped, whichever has more players kicking their own. A player who kicked another cast begun within
    KICK_SLOT_MS still kicked theirs; one who didn't kick gets their cast in that reading, so a cast that went
    through is on them and not on their pair; if they kicked another cast of the wave (from KICK_SLOT_MS before its
    first callout to KICK_WAVE_GAP_MS after its last) that isn't an assignment of their own, the check says which
    (`other`): their cast still wasn't kicked, but they didn't stand idle."""
    calls = [(at, c) for c in note.callouts if _kick_call(c) and (at := when(c)) is not None and at <= until]
    if not calls:
        return []
    kickable = await kicked_tonight(rep, f)
    t0 = f["startTime"]
    casts = sorted((at - t0, how, by) for these in (await interruptible_casts(rep, f)).values()
                   for at, how, by, spell in these if spell in kickable)

    def nearest(at: float) -> list[int]:
        return sorted((i for i, x in enumerate(casts) if abs(x[0] - at) <= KICK_SLOT_MS),
                      key=lambda i: abs(casts[i][0] - at))
    slots = []  # (callout time, callout, player, the casts begun near it, nearest first)
    for at, c in sorted(calls, key=lambda x: x[0]):
        if near := nearest(at):
            slots.append((at, c, players[c.players[0]].name if c.players[0] in players else c.players[0], near))
    waves: list[list[int]] = []
    for s, (at, *_) in enumerate(slots):
        if waves and at - slots[waves[-1][-1]][0] <= KICK_WAVE_GAP_MS:
            waves[-1].append(s)
        else:
            waves.append([s])
    expected: dict[int, list[int]] = {}  # slot -> its cast in the wave's reading, nearest first
    for wave in waves:
        pairs = [(wave[k], wave[k + 1]) for k in range(0, len(wave) - 1, 2)]
        partner = {a: b for x, y in pairs for a, b in ((x, y), (y, x))}
        readings = [{s: slots[s][3] for s in wave},
                    {s: nearest(slots[partner[s]][0]) if s in partner else slots[s][3] for s in wave}]
        expected |= max(readings, key=lambda r: sum(
            bool(r[s]) and casts[r[s][0]][1:] == ("kicked", slots[s][2]) for s in wave))
    taken: dict[int, int] = {}  # slot -> its cast
    for s, (_, _, name, near) in enumerate(slots):
        if (i := next((i for i in [*expected[s], *near] if casts[i][1:] == ("kicked", name)
                       and i not in taken.values()), None)) is not None:
            taken[s] = i
    for s, (_, _, _, near) in enumerate(slots):
        if s not in taken:
            taken[s] = next((i for i in [*expected[s], *near] if i not in taken.values()), near[0])
    own: dict[str, set[int]] = defaultdict(set)  # player -> the casts of their assignments
    for s, i in taken.items():
        own[slots[s][2]].add(i)
    span = {s: (slots[w[0]][0] - KICK_SLOT_MS, slots[w[-1]][0] + KICK_WAVE_GAP_MS) for w in waves for s in w}

    def other(s: int) -> float | None:
        name, theirs, (since, to) = slots[s][2], casts[taken[s]], span[s]
        if theirs[1] == "kicked":
            return None
        return min((x[0] for i, x in enumerate(casts) if x[1:] == ("kicked", name) and since <= x[0] <= to
                    and i not in own[name]), key=lambda at: abs(at - theirs[0]), default=None)
    return [KickCheck(at, c, name, casts[taken[s]], casts[taken[s]][1:] == ("kicked", name),
                      state(c.players[0], at), other(s)) for s, (at, c, name, _) in enumerate(slots)]


def _cast_end(cast: tuple[float, str, str | None]) -> str:
    return f"kicked by {cast[2]}" if cast[1] == "kicked" else cast[1]


def _kick_line(k: KickCheck) -> str:
    """What became of a kick assignment on one pull, after its time, player and words. A player dead at their callout
    made no kick, whatever they kicked earlier in the wave."""
    if k.kicked:
        return f"kicked the cast begun at {clock(k.cast[0])}"
    if k.cast[1] == "kicked":
        return f"covered: their cast, begun at {clock(k.cast[0])}, was kicked by {k.cast[2]} ({k.state})"
    theirs = f"their cast, begun at {clock(k.cast[0])}, {_cast_end(k.cast)}"
    if k.other is not None and k.state == "alive":
        return f"{theirs}; they kicked the cast begun at {clock(k.other)}"
    return f"no kick ({k.state}); {theirs}"


def _not_kicked(rep: Report, missed: list[tuple[dict, KickCheck]]) -> str:
    """'not kicked 3, kicked another cast on 1: p18 1:44 (kicked the 1:46 cast), p21 1:44, p24 3:18 (ended otherwise)':
    a player's casts that weren't kicked over the night, by their callout's time; one that went through says so by
    saying nothing more (the header's words), and one whose player kicked another cast of the wave says which."""
    instead = sum(k.other is not None for _, k in missed)

    def one(f: dict, k: KickCheck) -> str:
        said = ([] if k.cast[1] == "went through" else [_cast_end(k.cast)]) + (
            [f"kicked the {clock(k.other)} cast"] if k.other is not None else [])
        return f"p{rep.pull_no(f)} {clock(k.at)}" + (f" ({'; '.join(said)})" if said else "")
    return (f"not kicked {len(missed)}" + (f", kicked another cast on {instead}" if instead else "") + ": "
            + ", ".join(one(f, k) for f, k in missed[:LISTED])
            + (f" and {len(missed) - LISTED} more" if len(missed) > LISTED else ""))


# ---------- every pull on the boss ----------

@dataclass
class Night:
    """Every pull on a boss checked against its version of the note."""
    fights: list[dict]
    checks: list[PullCheck]
    usable: list[Version]  # the versions with a part for the boss
    by_pull: dict[int, Version]  # each pull's version, by its fight id
    used: list[int]  # the versions the pulls used (their ids), in order
    change: OrderChange | None  # the pull the note's kick order came in at, with one version for every pull


async def check_night(rep: Report, text: str, boss: str | None = None) -> Night:
    """Every ended pull on the boss checked against the note's version saved before it began (`_check`), with
    everything the pulls need fetched first in a few queries (`_prefetch`): what the night's text counts and the
    raid note's view draws."""
    f = rep.fight(None, boss)
    found = versions(text)
    if not any(for_boss(v.notes, f["encounterID"]) for v in found):
        raise FactError(f"the note has no part for {f['name']}")
    return await _check_night(rep, found, rep.ended(rep.by_boss[rep.boss_key(f)]))


async def _check_night(rep: Report, found: list[Version], fights: list[dict]) -> Night:
    encounter = fights[0]["encounterID"]
    usable = [v for v in found if for_boss(v.notes, encounter)]
    by_pull = {f["id"]: _version(usable, (rep.start_ms + f["startTime"]) / 1000) for f in fights}
    notes = {f["id"]: n for f in fights if (n := for_boss(by_pull[f["id"]].notes, encounter))}  # every pull's
    await _prefetch(rep, fights, notes)
    night = _Night(rep, fights)
    checks = [await _check(rep, f, notes[f["id"]], night) for f in fights]
    used = list(dict.fromkeys(id(by_pull[f["id"]]) for f in fights))
    tanks = {p.name for f in fights for p in rep.pull_players(f).values() if p.role == "tank"}
    change = _order_change(checks, tanks) if len(used) == 1 else None
    return Night(fights, checks, usable, by_pull, used, change)


async def _night(rep: Report, found: list[Version], fights: list[dict], source: str) -> str:
    """Each pull checked against its version of the note, then counted per player: soaks, planned cooldowns,
    callouts."""
    night = await _check_night(rep, found, fights)
    checks, by_pull, usable, used, change = night.checks, night.by_pull, night.usable, night.used, night.change
    latest = checks[-1].note
    if len(used) == 1 and by_pull[fights[0]["id"]].saved_at is not None:
        source = by_pull[fights[0]["id"]].source() + (f"; {source}" if source else "")
    lines = [_head(latest).replace("the boss", rep.boss_key(fights[0])) + (f", {source}" if source else "")
             + f", checked against every {rep.boss_key(fights[0])} pull ({len(fights)}), each until its wipe was "
             "decided (the boss's enrage or half the raid dead) and against the note as it was saved before it "
             "began. \"p3 1:15\" is pull 3 at 1:15." + _other_difficulty(latest, fights)]
    if len(used) > 1:
        lines.append("Versions of the note: " + "; ".join(
            f"{v.source()}: pulls " + ", ".join(str(rep.pull_no(f)) for f in fights if by_pull[f["id"]] is v)
            for v in usable if id(v) in used))
    lines += _saved_late(rep, fights, by_pull)
    if change:
        lines.append(_change_text(rep, fights, change))
    lines += _assignments(rep, latest)
    lines += _night_soaks(rep, checks)
    lines += _night_cooldowns(rep, checks)
    lines += _night_kicks(rep, checks[change.start:] if change else checks)
    lines += _night_callouts(rep, checks)
    lines += list(dict.fromkeys(c.unavailable for c in checks if c.unavailable))
    lines += _not_checked(latest, {tag: n for c in checks for tag, n in c.stray.items()})
    return "\n".join(lines)


async def _prefetch(rep: Report, fights: list[dict], notes: dict[int, Note]) -> None:
    """What `_check` reads for each pull, fetched in a few queries for all of them and kept where `_check` finds it."""
    await rep.all_deaths(fights)
    await rep.boss_buff_events(fights)
    soaking = [f for f in fights if _soak_sections(notes[f["id"]])]
    tables = dict(zip([f["id"] for f in soaking], await rep.tables(
        [(f["id"], {}) for f in soaking], "DamageTaken", **DAMAGE_BY_ABILITY), strict=True))
    wanted = []
    for f in soaking:
        hit_ids = {e["guid"] for e in tables[f["id"]].get("entries") or []}
        for heading in _soak_sections(notes[f["id"]]):
            wanted += [(f["id"], {"abilityID": i}) for i in _ability_of(rep, heading, hit_ids)]
    if wanted:
        await rep.events_for(wanted, "DamageTaken", hostilityType="Friendlies")
    planning = [f for f in fights if notes[f["id"]].cooldowns]
    await rep.name_spells({c.spell for f in planning for c in notes[f["id"]].cooldowns})
    if planning:
        await rep.events_for([(f["id"], {"filterExpression": _planned_filter(rep, notes[f["id"]])})
                              for f in planning], "Casts")
    kicking = [(f["id"], {}) for f in fights if any(_kick_call(c) for c in notes[f["id"]].callouts)]
    if kicking:  # what `_kicks` reads: the adds' casts and the kicks, each pull's and the night's
        await kicked_tonight(rep, fights[0])
        await rep.events_for(kicking, "Casts", hostilityType="Enemies")
        await rep.events_for(kicking, "Interrupts")


LISTED = 12  # moments listed per player; the rest are counted


def _pulls(rep: Report, at: list[tuple[dict, float]]) -> str:
    text = ", ".join(f"p{rep.pull_no(f)} {clock(t)}" for f, t in at[:LISTED])
    if len(at) > LISTED:
        text += f" and {len(at) - LISTED} more, the last p{rep.pull_no(at[-1][0])} {clock(at[-1][1])}"
    return text


def _night_soaks(rep: Report, checks: list[PullCheck]) -> list[str]:  # noqa: C901
    out = []
    for heading in dict.fromkeys(s.heading for c in checks for s in c.soaks):
        sections = [(c, s) for c in checks for s in c.soaks if s.heading == heading and s.ability is not None]
        if not sections:
            out.append(f"{heading}: no ability of that name hit players on any pull")
            continue
        alive: Counter = Counter()  # these four per (player, group): a player may be in several groups
        took: Counter = Counter()
        missed: dict[tuple[str, str], list[tuple[dict, float]]] = defaultdict(list)
        dead: Counter = Counter()
        other: Counter = Counter()
        stray: Counter = Counter()
        no_group: dict[str, list[tuple[dict, float]]] = defaultdict(list)  # why -> the hits given to no group
        for c, s in sections:
            for h in s.hits:
                if not h.label:
                    no_group[h.why].append((c.f, h.at))
                for n in h.hit & set(h.group):
                    alive[(n, h.label)] += 1
                    took[(n, h.label)] += 1
                for n, st in h.absent:
                    if st == "alive":
                        alive[(n, h.label)] += 1
                        missed[(n, h.label)].append((c.f, h.at))
                    elif st.startswith("dead"):
                        dead[(n, h.label)] += 1
                for n, g in h.others:
                    if g:
                        other[n] += 1
                    else:
                        stray[n] += 1
        hits = sum(len(s.hits) for _, s in sections)
        later = sum(s.later for _, s in sections)
        head = (f"{heading}: {plural(hits, 'hit')} of {rep.ability_ref(sections[0][1].ability)} over "
                f"{plural(len(sections), 'pull')}" + (f" ({later} more after the wipes were decided)" if later else ""))
        if no_soaks := _no_soaks([h for _, s in sections for h in s.hits]):
            out.append(f"{head}, {no_soaks}")
            continue
        out.append(f"{head}, each {SOAKS_ARE}. Per player of a group, most missed first: of their group's hits while "
                   "alive, how many hit them and which they missed:")
        few = _few(took, alive)  # named apart below, as likely not soaking with the group: no row of misses
        none = []
        for k in sorted(alive, key=lambda k: (-len(missed[k]), k)):
            row = f"{k[0]} ({k[1]})"
            also = f"; dead for {dead[k]} more" if dead[k] else ""
            if missed[k] and k not in few:
                out.append(f"  {row}: hit by {took[k]} of {alive[k]}; missed {len(missed[k])}: {_pulls(rep, missed[k])}"
                           + also)
            elif k not in few:
                none.append(f"{row} {took[k]} of {alive[k]}{also.replace(';', ',')}")
        if none:
            out.append("  Missed none while alive: " + "; ".join(none))
        if other:
            out.append("  Hit by another group's soak too: " + ", ".join(f"{n} {k}" for n, k in _by_count(other)))
        out += [f"  Counted for no one: {plural(len(at), 'hit')} {NO_GROUP[why]}: {_pulls(rep, at)}"
                for why, at in no_group.items()]
        out += _untimed([s for _, s in sections])
        if line := _few_soaks(took, alive):
            out.append(line)
        if stray:
            out.append("  Hit, in no group: " + ", ".join(f"{n} {k}" for n, k in _by_count(stray)))
    return out


def _untimed(sections: list[Soaks]) -> list[str]:
    """The line naming the groups the note never calls to soak, whose hits are found by the players hit alone."""
    labels = list(dict.fromkeys(label for s in sections for label in s.untimed))
    if not labels:
        return []
    return [f"  The note has no timed callout to {', '.join(labels)}: a hit at any time may be "
            f"{'its' if len(labels) == 1 else 'theirs'}, by the players hit alone"]


def _no_soaks(hits: list[SoakHit]) -> str:
    """What the night says of a section whose groups aren't soaking its ability (a CC list, an ability to avoid): of
    the hits in the note's soak times, at least FEW_SOAKS_MIN, under FEW_SOAKS could be given to a group, the rest
    landing on a player or two of each. No one is counted as missing those; '' for a section that soaks."""
    timed = [h for h in hits if h.why != OUTSIDE]
    given = sum(bool(h.label) for h in timed)
    if len(timed) < FEW_SOAKS_MIN or given >= FEW_SOAKS * len(timed):
        return ""
    return (f"but only {given} of the {len(timed)} in the note's soak times landed on a group (under a fifth; the "
            "rest on a player or two of each): likely not soak groups (an ability to avoid, or groups for another "
            "job), so no one is counted as missing it")


def _few(took: Counter, alive: Counter) -> list:
    """Who was hit by almost none of their group's soaks while alive (FEW_SOAKS), of the counts' keys."""
    return [k for k in alive if alive[k] >= FEW_SOAKS_MIN and took[k] < FEW_SOAKS * alive[k]]


def _few_soaks(took: Counter, alive: Counter) -> str:
    """The line naming the players hit by almost none of their group's soaks while alive, or ''. The counts are per
    player or per (player, group); a player in several groups is named with the group."""
    few = _few(took, alive)
    if not few:
        return ""
    rows = Counter(k[0] for k in alive if isinstance(k, tuple))

    def name(k: str | tuple[str, str]) -> str:
        return k if isinstance(k, str) else k[0] if rows[k[0]] == 1 else f"{k[0]} in {k[1]}"
    return ("  Hit by almost none of their group's soaks while alive (under a fifth), so likely not soaking with "
            "this group (a reserve, a spot changed since the note was written): "
            + ", ".join(f"{name(k)} ({took[k]} of {alive[k]})"
                        for k in sorted(few, key=lambda k: (took[k] / alive[k], name(k)))))


def _night_cooldowns(rep: Report, checks: list[PullCheck]) -> list[str]:
    uses = [(c, u) for c in checks if c.planned for u in c.planned.uses]
    if not uses:
        return []
    out = ["Planned cooldowns over the pulls, per player and per planned use (note time and spell), counting the "
           f"pulls that reached it before the wipe was decided: on time (within 1 s), early or late (the median), not "
           f"cast within {MATCH_S} s while alive (with the pulls; of them, how many were planned in the last "
           f"{NEAR_END_MS // 1000} s before the wipe was decided or the pull ended, and at how many the spell was on "
           "cooldown from their own earlier cast, by Lorrgs' base cooldown or by their closest two casts of it on "
           "these pulls where that is shorter: the note plans it again too soon, or it was pressed off the plan), "
           "dead by then:"]
    per_player: dict[str, dict[tuple[float, int, int | None], list[tuple[PullCheck, PlannedUse]]]] = defaultdict(
        lambda: defaultdict(list))
    for c, u in uses:
        per_player[u.cooldown.player][(u.cooldown.at + 1000 * u.cooldown.phase, u.cooldown.spell,
                                       u.cooldown.boss_spell)].append((c, u))

    def tally(rows: list[tuple[PullCheck, PlannedUse]], which: bool = False) -> str:
        on = [r for r in rows if r[1].cast_at is not None and abs(r[1].cast_at - r[1].at) < 1000]
        off = [(u.cast_at - u.at) / 1000 for _, u in rows if u.cast_at is not None]
        early, late = [-d for d in off if d <= -1], [d for d in off if d >= 1]
        left = [r for r in rows if r[1].cast_at is None and r[1].state == "alive"]
        not_cast = [(c.f, u.at) for c, u in left]
        dead = sum(r[1].cast_at is None and r[1].state.startswith("dead") for r in rows)
        parts = [f"on time {len(on)}"] if on else []
        if early:
            parts.append(f"early {len(early)} ({statistics.median(early):.0f} s)")
        if late:
            parts.append(f"late {len(late)} ({statistics.median(late):.0f} s)")
        if not_cast:
            near = sum(c.until - u.at <= NEAR_END_MS for c, u in left)
            cooling = sum(bool(u.cooling) for _, u in left)
            parts.append(f"not cast {len(not_cast)}" + (
                " (" + ", ".join(f"p{rep.pull_no(f)}" for f, _ in not_cast[:LISTED])
                + (f" and {len(not_cast) - LISTED} more" if len(not_cast) > LISTED else "") + ")" if which else "")
                + (f", {near} of them in the pull's last {NEAR_END_MS // 1000} s" if near else "")
                + (f", {cooling} of them on cooldown" if cooling else ""))
        if dead:
            parts.append(f"dead {dead}")
        return ", ".join(parts)

    def missed(rows: list[tuple[PullCheck, PlannedUse]]) -> int:
        return sum(r[1].cast_at is None and r[1].state == "alive" for r in rows)
    never: dict[str, set[int]] = {}  # per player, the planned spells they cast on none of the pulls
    for name in sorted(per_player, key=lambda n: (-sum(missed(r) for r in per_player[n].values()), n)):
        entries = per_player[name]
        everything = [r for rows in entries.values() for r in rows]
        spells = {r[1].cooldown.spell for r in everything}
        never[name] = {sp for sp in spells if sum(missed([r]) for r in everything if r[1].cooldown.spell == sp)
                       >= NEVER_CAST_MIN and not any(r[1].casts for r in everything if r[1].cooldown.spell == sp)}
        out.append(f"  {name}: {len(everything)} planned uses; {tally(everything)}")
        for rows in sorted(entries.values(), key=lambda rows: rows[0][1].at):
            u = rows[0][1]
            line = f"    {clock(u.at)} {rep.ability_ref(u.cooldown.spell)}"
            if u.cooldown.boss_spell:
                line += f" for {rep.ability_ref(u.cooldown.boss_spell)}"
            out.append(line + f", {plural(len(rows), 'pull')}: {tally(rows, which=True)}"
                       + ("; never cast on any of these pulls: a talent may replace it, or another spell do its job "
                          "(breakdown of their casts)" if u.cooldown.spell in never[name] else ""))
    twice = {(cd.player, cd.spell, first, again): (first, again, cd)
             for c in checks if c.planned for first, again, cd in c.planned.twice}
    return out + [f"  {_twice(rep, t)}" for t in twice.values()]


def _twice(rep: Report, twice: tuple[float, float, Cooldown]) -> str:
    first, again, cd = twice
    return (f"The note plans {cd.player}'s {rep.ability_ref(cd.spell)} at both {clock(first)} and {clock(again)}: one "
            "use written twice, checked as the first")


def _night_callouts(rep: Report, checks: list[PullCheck]) -> list[str]:
    calls = [(c, k) for c in checks for k in c.callouts if k.in_pull]
    if not calls:
        return _night_unchecked(checks)
    out = [f"Callouts for personals over the pulls, per callout and per player it names, most often pressing none "
           f"first: how many of the callouts they were alive and counted for, at how many they pressed "
           f"{PERSONALS_ARE} from {CALL_BEFORE_MS // 1000} s before to {CALL_AFTER_MS // 1000} s after it "
           "(callouts by the tag of what they pressed), the ones they pressed none at, with at how many of those "
           f"they had one ready (one they cast at least once on these pulls, {READY_ARE}) and the tags of what they "
           "pressed instead (untagged: a defensive Lorrgs doesn't tag, such as Anti-Magic Shell, may be one), and "
           f"those not counted against them (one was {RUNNING}, pressed earlier and still up by Lorrgs' duration, "
           "which talents change; they pressed the cooldown the note plans for them then; or a tank kept a tank "
           "cooldown for just after):"]
    groups: dict[tuple[str, tuple[str, ...]], list[tuple[PullCheck, CalloutCheck]]] = defaultdict(list)
    for c, k in calls:
        groups[(k.callout.text, k.callout.players)].append((c, k))
    for (text, named), rows in groups.items():
        times = sorted({clock(k.at) for _, k in rows})
        out.append(f"  \"{text}\" to {' '.join(named) or 'everyone'} ({plural(len(rows), 'callout')} over "
                   f"{plural(len({id(c) for c, _ in rows}), 'pull')}, at {', '.join(times)}):")
        alive: Counter = Counter()
        pressed: dict[str, Counter] = defaultdict(Counter)
        pressed_n: Counter = Counter()
        none: dict[str, list[tuple[dict, float]]] = defaultdict(list)
        ready: Counter = Counter()  # per player, the callouts they pressed none at with one ready
        instead: dict[str, Counter] = defaultdict(Counter)
        excused: dict[str, list[str]] = defaultdict(list)  # pressed none, not counted against them: why
        for c, k in rows:
            for n, mine in k.pressed:
                alive[n] += 1
                pressed_n[n] += 1
                pressed[n].update({t for _, sp in mine for t in _tags(sp)})
            for n, spells in k.idle:
                alive[n] += 1
                none[n].append((c.f, k.at))
                ready[n] += bool(spells)
                if n in k.others:
                    instead[n][None] += 1
                    instead[n].update({t for _, sp in k.others[n] for t in _tags(sp)})
            for n, why in k.excused:
                alive[n] += 0  # listed, not counted
                excused[n].append(why.split(" for ")[0] if why.startswith("a tank") else f"one {RUNNING}"
                                  if why.startswith(RUNNING) else "pressed the note's plan for them")
        for n in sorted(alive, key=lambda n: (-len(none[n]), n)):
            line = f"    {n}: alive for {alive[n]}; pressed at {pressed_n[n]}"
            if pressed[n]:
                line += " (" + ", ".join(f"{t} {k}" for t, k in _by_count(pressed[n])) + ")"
            if none[n]:
                line += f"; none at {len(none[n])}: {_pulls(rep, none[n])} (one ready at {ready[n]})"
            if only := instead[n].pop(None, 0):
                line += f"; of those, pressed only others at {only} (" + ", ".join(
                    f"{t} {k}" for t, k in _by_count(instead[n])) + ")"
            if excused[n]:
                line += f"; not counted at {len(excused[n])} (" + ", ".join(
                    f"{why} {k}" for why, k in Counter(excused[n]).most_common()) + ")"
            out.append(line)
    return out + _night_unchecked(checks)


def _night_unchecked(checks: list[PullCheck]) -> list[str]:
    """The callouts the log can't check, each once with its times and how many pulls reached it."""
    seen: dict[tuple[str, tuple[str, ...]], tuple[set[str], set[int]]] = {}
    for c in checks:
        for at, co in c.unchecked:
            times, pulls = seen.setdefault((co.text, co.players), (set(), set()))
            times.add(clock(at))
            pulls.add(c.f["id"])
    if not seen:
        return []
    return [UNCHECKED + ": " + "; ".join(
        f"\"{text}\" to {' '.join(named) or EVERYONE} at {', '.join(sorted(times))} ({plural(len(pulls), 'pull')})"
        for (text, named), (times, pulls) in seen.items())]


def _saved_late(rep: Report, fights: list[dict], by_pull: dict[int, Version]) -> list[str]:
    """The pulls checked against a note saved after they began, there being none saved before (saved.pick)."""
    late = [rep.pull_no(f) for f in fights
            if (by_pull[f["id"]].saved_at or 0) > (rep.start_ms + f["startTime"]) / 1000]
    if not late:
        return []
    return [f"The note was saved after pulls {ranges(late)} began, with none saved before them: the plan for them may "
            "have been different."]


def _order_change(checks: list[PullCheck], tanks: AbstractSet[str] = frozenset()) -> OrderChange | None:
    """The pull the note's kick order came in at, when the pulls before it had another (ORDER_KICKS): the split
    where most kicks fit, the note's order from it on and the other before it (each assignment's player kicks the
    same either way, so a split counts the other order's kicks over its player's, before it). None when every pull
    fits the note's. `tanks` kicking an assignment not theirs are left out (ORDER_KICKS)."""
    def tally(cs: list[PullCheck]) -> dict[tuple[str, str], Counter]:
        by: dict[tuple[str, str], Counter] = defaultdict(Counter)  # (assignment, its player) -> who kicked its casts
        for c in cs:
            for k in c.kicks:
                by_ = k.cast[2] if k.cast[1] == "kicked" else None
                if by_ not in tanks or by_ == k.player:
                    by[(k.callout.text, k.player)][by_] += 1
        return by
    kickers = {k.player for c in checks for k in c.kicks}  # the players the note gives a kick
    best: tuple[int, int, list[tuple[str, str, int, int]]] | None = None
    for s in range(1, len(checks)):
        before, after = tally(checks[:s]), tally(checks[s:])
        away = set.intersection(*(set(c.missing) for c in checks[:s]))
        moved = []  # (assignment, its player, who kicked its casts instead, how many, of how many, its player's)
        for (text, who), kicked in before.items():
            total, later = sum(kicked.values()), after.get((text, who), Counter())
            other, n = next(((p, n) for p, n in kicked.most_common() if p not in (who, None)), ("", 0))
            if (n >= ORDER_KICKS and n >= ORDER_SHARE * total and kicked[who] <= ASSIGNEE_SHARE * total
                    and later[who] >= ORDER_SHARE * sum(later.values())):
                moved.append((text, who, other, n, total, kicked[who]))
        whole = [m for m in moved if m[2] not in kickers or m[2] in {m[1] for m in moved} or m[1] in away]
        score = sum(n - own for *_, n, _, own in whole)
        if whole and (best is None or score > best[0]):
            best = (score, s, [(text, other, n, total) for text, _, other, n, total, _ in whole])
    if best is None:
        return None
    _, s, slots = best
    absent = set.intersection(*(set(c.missing) for c in checks[:s])) - set.intersection(
        *(set(c.missing) for c in checks[s:]))
    return OrderChange(s, sorted(slots), sorted(absent))


def _change_text(rep: Report, fights: list[dict], change: OrderChange) -> str:
    fits, before = ([rep.pull_no(f) for f in part] for part in (fights[change.start:], fights[:change.start]))
    slots = ", ".join(f"{text} was kicked by {who} ({n} of its {total} casts)" for text, who, n, total in change.slots)
    return (f"The note's kick order fits pulls {ranges(fits)}, not {ranges(before)}, which had another: there, {slots}"
            + (f", and {', '.join(change.absent)}, named in the note, {'was' if len(change.absent) == 1 else 'were'} "
               "in none of them" if change.absent else "")
            + f" (tanks' kicks on others' assignments left out as backup). Kicks are counted for pulls {ranges(fits)} "
            "only; soaks and cooldowns for every pull.")


def _night_kicks(rep: Report, checks: list[PullCheck]) -> list[str]:
    rows = [(c, k) for c in checks for k in c.kicks]
    if not rows:
        return []
    out = [KICKS_ARE + ". Over the pulls, per player, most casts that went through first: of their kicks while alive, "
           "how many they made, how many another player covered (kicked their cast first: handled, no one's miss; by "
           "whom), and the casts of theirs that weren't kicked (they went through unless it says otherwise; with the "
           "cast of the wave they kicked instead, if any); dead for how many more:"]
    alive: Counter = Counter()
    made: Counter = Counter()
    covered: dict[str, Counter] = defaultdict(Counter)
    missed: dict[str, list[tuple[dict, KickCheck]]] = defaultdict(list)
    dead: Counter = Counter()
    texts: dict[str, list[str]] = defaultdict(list)
    for c, k in rows:
        if k.callout.text not in texts[k.player]:
            texts[k.player].append(k.callout.text)
        if k.state.startswith("dead"):
            dead[k.player] += 1
        elif k.state == "alive":
            alive[k.player] += 1
            if k.kicked:
                made[k.player] += 1
            elif k.cast[1] == "kicked":
                covered[k.player][k.cast[2]] += 1
            else:
                missed[k.player].append((c.f, k))
    for n in sorted(alive.keys() | dead.keys(), key=lambda n: (-len(missed[n]), -sum(covered[n].values()), n)):
        line = f"  {n} ({', '.join(texts[n])}): kicked {made[n]} of {alive[n]}"
        if covered[n]:
            line += f"; covered {sum(covered[n].values())} (" + ", ".join(
                f"{by} {k}" for by, k in _by_count(covered[n])) + ")"
        if missed[n]:
            line += "; " + _not_kicked(rep, missed[n])
        if dead[n]:
            line += f"; dead for {dead[n]} more"
        out.append(line)
    through = Counter(k.callout.text for _, k in rows if k.cast[1] == "went through")
    if through:
        out.append("  Their casts that went through, per assignment: " + ", ".join(
            f"\"{t}\" {n}" for t, n in _by_count(through)))
    lost = [(c, before) for c in checks if (before := through_before(c))]
    if lost:
        out.append(f"  Pulls whose wipe was decided within {LOST_AFTER_MS // 1000} s after casts on assignments went "
                   f"through, {len(lost)} of {len(checks)} (when it was decided, then the casts, first one first): "
                   + "; ".join(f"p{rep.pull_no(c.f)} {clock(c.until)} (" + ", ".join(_through(k) for k in before) + ")"
                               for c, before in lost))
        out.append(_first_through(rep, lost))
    return out


def _first_through(rep: Report, lost: list[tuple[PullCheck, list[KickCheck]]]) -> str:
    """Of those pulls, whose cast went through first: the miss that let the wipe in; the casts after it are left out."""
    first: dict[str, list[tuple[dict, KickCheck]]] = defaultdict(list)
    no_one = []
    for c, before in lost:
        if before[0].state == "alive":
            first[before[0].player].append((c.f, before[0]))
        else:
            no_one.append(f"p{rep.pull_no(c.f)} ({before[0].player} {before[0].state})")
    texts = {n: {k.callout.text for _, k in got} for n, got in first.items()}
    return ("  On those pulls, per player, whose cast went through first while they were alive (the casts after it "
            "aren't counted against anyone): " + "; ".join(
                f"{n} {len(got)}: " + ", ".join(
                    f"p{rep.pull_no(f)} {clock(k.cast[0])}" + (f" \"{k.callout.text}\"" if len(texts[n]) > 1 else "")
                    for f, k in got)
                for n, got in sorted(first.items(), key=lambda i: (-len(i[1]), i[0])))
            + (f"; no one's, the first cast's player dead or not in the pull: {', '.join(no_one)}" if no_one else ""))


def through_before(c: PullCheck) -> list[KickCheck]:
    """The kick assignments whose casts went through in the LOST_AFTER_MS before the pull's wipe was decided."""
    if c.decided is None:
        return []
    return sorted((k for k in c.kicks
                   if k.cast[1] == "went through" and c.until - LOST_AFTER_MS <= k.cast[0] <= c.until),
                  key=lambda k: k.cast[0])


def _through(k: KickCheck) -> str:
    return f"\"{k.callout.text}\" {k.player} {clock(k.cast[0])} ({k.state})"
