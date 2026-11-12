"""Deaths: one in detail (`death_recap`), what a ready defensive would have changed, and the raid's
healthstones, potions and defensives over a boss (`defensives`)."""
import asyncio
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import inf

from .. import knowledge
from ..knowledge import KnowledgeError, spaced
from ..text import clock, ids_line, plural, short
from . import healing as heals
from . import positions
from .base import DEATH_BURST_MS, FactError, Player, PullRef, Report, unavailable
from .ending import BEFORE_NOTE, FALLOUT_NOTE, MASS_HIT, MASS_NOTE, MassDeath, mass_death

RECAP_WINDOW_MS = 10_000  # damage shown before a death
OUTRIGHT_FROM_PCT = 50  # a killing blow with no hit logged, after health at least this high: it killed outright
HEALTHSTONE_SPELL = 6262  # the Healthstone item's spell: its tooltip says what it heals
A_HEALTHSTONE = "a healthstone"  # in a death's replay, with what it takes as given: the log doesn't say who carries one
HEALTHSTONE_GIVEN = "if they had one; none used this pull"
CASCADE_MS = 15_000       # deaths this close together are probably linked
DEATH_STRIP_MS = 250  # a dying player's auras are removed just before the death event


async def death_recap(rep: Report, player: str, pull: PullRef = None, boss: str | None = None,
                      death: int = 1) -> str:
    f = rep.fight(pull, boss)
    p = rep.player(player)
    rep.require_in(f, p)
    deaths = await rep.deaths(f)
    own = [d for d in deaths if d["targetID"] == p.id]
    if not own:
        return f"{p.name} did not die on {rep.label(f)}."
    p = rep.in_pull(f, p)  # defensives of the spec played in this pull
    if not 1 <= death <= len(own):
        raise FactError(f"{p.name} died {len(own)} time(s) on {rep.label(f)}")
    d = own[death - 1]
    t0, at = f["startTime"], d["timestamp"]
    (hits, what, hit_lines), healing, healers, auras, defensive_lines, was = await asyncio.gather(
        _hits(rep, f, p, d), _healing_received(rep, f, p, at), heals.at_death(rep, f, p, at),
        _aura_lines(rep, f, p, at), _defensives(rep, f, p, at), standing(rep, f, d))
    others = [x for x in deaths if x is not d and abs(x["timestamp"] - at) <= CASCADE_MS]
    burst = [x for x in others if abs(x["timestamp"] - at) <= BURST_MS]
    (verdict, pressed), raid_cds = await asyncio.gather(_if_pressed_lines(rep, f, p, d, hits, was),
                                                        _raid_cooldowns_lines(rep, f, [d, *burst]))
    # What killed them and whether anything ready would have changed it lead; the hits and the rest are the detail.
    lines = [f"{p.label} died at {clock(at - t0)} on {rep.label(f)} ({rep.url(f)})"
             + (f", {was.note}" if was.note else "")
             + f". Killing blow: {rep.ability_ref(d.get('killingAbilityGameID'))}.", *what, *verdict, *hit_lines,
             *healing, *healers, *auras, *defensive_lines]
    if others:
        lines.append(f"Other deaths within {CASCADE_MS // 1000} s: " + ", ".join(
            f"{rep.names[x['targetID']]} ({rep.ability(x.get('killingAbilityGameID'))}, "
            f"{clock(x['timestamp'] - t0)})" for x in others))
    return "\n".join(lines + pressed + raid_cds)


def _verdict(replay: Replay) -> list[str]:
    """The replay's answer in one line, for the top of a death's recap: what would have kept them alive, of what
    was ready (`if_pressed`'s rows, which stay below as the working). None when the death couldn't be replayed.
    It claims no more than the working does: a healthstone only if they had one, and nothing ruled out that the
    replay couldn't tell."""
    if not replay:
        return []
    if saved := saving(replay):
        return [f"Would have kept them alive, if pressed (replayed below; secondary to what killed them): "
                f"{', '.join(r.label for r in saved)}."]
    if lived(replay) is None:
        return [f"Nothing that was ready is sure to have kept them alive on its own (replayed below): "
                f"{untold(replay)} of {len(replay.rows)} can't be told."]
    return ["Nothing that was ready would have kept them alive on its own (replayed below)."]


@dataclass(frozen=True)
class Standing:
    """What a death was to its wipe: `note` says it in the words player_pull's "Died" line notes a death with
    (before the wipe was decided; after it, fallout; one of a hit killing many, ending.mass_death; "" on a kill or
    a wipe never decided). Fallout and a death in a hit killing many are no one player's to fix."""
    note: str
    fallout: bool = False
    mass: MassDeath | None = None

    @property
    def theirs(self) -> bool:
        return not self.fallout and self.mass is None


# Why death_recap leaves the replay with what was ready out of a death that isn't the player's to fix: the same two
# kinds of death `defensives` and player_night leave out
NOT_REPLAYED = "Not replayed with what was ready: "
NOT_REPLAYED_FALLOUT = NOT_REPLAYED + "the wipe was decided by then, so nothing pressed would have changed the pull."
NOT_REPLAYED_MASS = NOT_REPLAYED + ("a hit killing many is the raid's to stop, and a defensive would only have bought "
                                    "seconds of a pull it lost.")


async def standing(rep: Report, f: dict, death: dict) -> Standing:
    """What a death was to its wipe (`Standing`), for death_recap's first line and its picture."""
    decided = await rep.decided_at(f)
    if decided and death["timestamp"] - f["startTime"] > decided.at_ms:
        return Standing(FALLOUT_NOTE.format(at=clock(decided.at_ms)), fallout=True)
    if m := await mass_death(rep, f, death):
        return Standing(MASS_NOTE.format(n=m.killed, at=clock(m.at_ms)), mass=m)
    return Standing(BEFORE_NOTE if decided else "")


def damage_by_ability(rep: Report, f: dict, hits: list[dict]) -> str:
    """What did the damage before a death, added up by ability, most first, with the biggest hit and the health it
    left, and the killing blow's own hit: a death's killing blow is often a small tick after the hit that did it."""
    by: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        by[rep.ability(h["abilityGameID"])].append(h)
    total = sum(h.get("amount", 0) for h in hits)
    parts = [f"{name} {short(sum(h.get('amount', 0) for h in own))} in {plural(len(own), 'hit')}"
             for name, own in sorted(by.items(), key=lambda kv: -sum(h.get("amount", 0) for h in kv[1]))]
    line = (f"Damage in the {RECAP_WINDOW_MS // 1000} s before death, by ability: {', '.join(parts)}; "
            f"{short(total)} in all.")
    big = max(hits, key=lambda h: h.get("amount", 0))
    last = next((h for h in reversed(hits) if h.get("overkill")), None)
    hp = (f", to {100 * big['hitPoints'] / big['maxHitPoints']:.0f}% health"
          if big.get("resourceActor") == 2 and big.get("maxHitPoints") else "")
    line += (f" The biggest hit{' was the killing blow' if big is last else ''}: {rep.ability(big['abilityGameID'])} "
             f"{short(big.get('amount', 0))} at {clock(big['timestamp'] - f['startTime'])}{hp}.")
    if last is not None and last is not big:
        line += (f" The killing blow: {rep.ability(last['abilityGameID'])} {short(last.get('amount', 0))} at "
                 f"{clock(last['timestamp'] - f['startTime'])}.")
    return line


async def _hits(rep: Report, f: dict, p: Player, d: dict) -> tuple[list[dict], list[str], list[str]]:
    """The damage a player took in the window before a death; what it adds up to, for the top of the recap (by
    ability, and where they were last when nothing is named as the killing blow); and its lines, each hit with their
    health after it."""
    t0, at = f["startTime"], d["timestamp"]
    # For DamageTaken, WCL's sourceID filter selects the player who took the damage.
    hits = await rep.events(f["id"], "DamageTaken", sourceID=p.id, hostilityType="Friendlies",
                            startTime=at - RECAP_WINDOW_MS, endTime=at + 1, includeResources=True)
    lines = [f"Damage taken in the {RECAP_WINDOW_MS // 1000} s before death (time, ability, source, amount, "
             "health after):"]
    for h in hits:
        hp = ""
        if h.get("resourceActor") == 2 and h.get("maxHitPoints"):
            hp = f"{100 * h['hitPoints'] / h['maxHitPoints']:.0f}% hp"
        extra = []
        if h.get("absorbed"):
            extra.append(f"{short(h['absorbed'])} absorbed")
        if h.get("overkill"):
            extra.append(f"overkill {short(h['overkill'])}")
        lines.append(f"  {clock(h['timestamp'] - t0)} {rep.ability(h['abilityGameID'])} from "
                     f"{rep.actor(h['sourceID'])}: {short(h.get('amount', 0))}"
                     + (f" ({', '.join(extra)})" if extra else "") + (f" -> {hp}" if hp else ""))
    if not hits:
        lines.append("  (no damage events recorded)")
    else:
        lines.append(ids_line({rep.ability(h["abilityGameID"]): h["abilityGameID"] for h in hits}))
    what = [damage_by_ability(rep, f, hits)] if hits else []
    if moved := await last_seen(rep, f, p, d):
        what.append(moved)  # the first line has said no killing blow was recorded
    elif d.get("killingAbilityGameID") and (line := killed_outright(rep, f, d, hits)):
        what.append(line)
    return hits, what, lines


async def last_seen(rep: Report, f: dict, p: Player, d: dict) -> str:
    """Where a player was last logged before a death the log names no killing blow for, and what that fits
    (positions.last_moves): death_recap's line and its picture's. '' for a death with a killing blow, or without
    positions."""
    if d.get("killingAbilityGameID"):
        return ""
    return await positions.last_moves(rep, f, p.id, d["timestamp"]) or ""


def killed_outright(rep: Report, f: dict, d: dict, hits: list[dict]) -> str:
    """'' unless the killing blow left no hit and their last logged health was OUTRIGHT_FROM_PCT or more: then the
    line saying it killed outright, not damage that outpaced healing."""
    if any(h.get("overkill") or h["abilityGameID"] == d["killingAbilityGameID"] for h in hits):
        return ""
    last = next((h for h in reversed(hits) if h.get("resourceActor") == 2 and h.get("maxHitPoints")), None)
    if last is None or (pct := 100 * last["hitPoints"] / last["maxHitPoints"]) < OUTRIGHT_FROM_PCT:
        return ""
    return (f"Killed outright: no hit of {rep.ability(d['killingAbilityGameID'])} is logged, and they were at "
            f"{pct:.0f}% health at {clock(last['timestamp'] - f['startTime'])}, so it wasn't damage that outpaced "
            "their healing.")


async def _aura_lines(rep: Report, f: dict, p: Player, at: float) -> list[str]:
    t0 = f["startTime"]
    auras = await _auras_at(rep, f, p, at)
    given = [(n, src, since) for n, (src, since, _) in auras["buffs"].items() if src != p.id and src in rep.names]
    return ["Buffs from others on them at death (gained during the pull): " + (", ".join(
            f"{n} ({rep.names[src]}, from {clock(since - t0)})" for n, src, since in sorted(given, key=lambda x: x[2]))
            or "none"),
            "Enemy debuffs on them at death: " + (", ".join(
                f"{n}{f' ×{stack}' if stack > 1 else ''} (from {clock(since - t0)})"
                for n, (_, since, stack) in sorted(auras["debuffs"].items(), key=lambda kv: kv[1][1])) or "none")]


async def _if_pressed_lines(rep: Report, f: dict, p: Player, d: dict, hits: list[dict], was: Standing
                            ) -> tuple[list[str], list[str]]:
    """death_recap's replay with what was ready: its answer in one line for the top of the recap (`_verdict`; for a
    death that wasn't theirs, the line saying why it has none), and its working for the end."""
    if not was.theirs:
        return [NOT_REPLAYED_FALLOUT if was.fallout else NOT_REPLAYED_MASS], []
    try:
        replay = await if_pressed(rep, f, p, d, hits)
    except KnowledgeError as e:
        return [], [unavailable("If pressed (what a ready defensive would have changed)", e)]
    return _verdict(replay), replay.lines()


async def _raid_cooldowns_lines(rep: Report, f: dict, deaths: list[dict]) -> list[str]:
    """When several died at once, the raid cooldowns ready then."""
    if len(deaths) < BURST_DEATHS:
        return []
    try:
        return await _raid_cooldowns_at(rep, f, deaths)
    except KnowledgeError as e:
        return [unavailable(f"Raid cooldowns ready when {plural(len(deaths), 'player')} died at once", e)]


BURST_FROM_PCT = 60  # the lethal burst: the hits since the player was last at this much health or more
REACT_MS = 1_000  # a heal counts only if pressed after a hit at least this long before the killing hit: time to see it


BURST_DEATHS, BURST_MS = 3, 3_000  # this many deaths this close together: a moment for raid cooldowns


async def _used_ever(rep: Report, p: Player, spells: list[dict]) -> set[str]:
    """Which of these spells the player cast in any pull of the report: a defensive they never press may be a
    talent they don't have or a passive."""
    mine = [c for c in await rep.defensive_casts() if c.get("sourceID") == p.id]
    return {sp["name"] for sp in spells for c in mine if rep.is_spell(c, sp)}


async def _their_casts(rep: Report, f: dict, p: Player, until: float) -> list[dict]:
    """The player's defensive and raid cooldown casts in one pull up to a moment."""
    return [c for c in await rep.defensive_casts()
            if c.get("fight") == f["id"] and c.get("sourceID") == p.id and c["timestamp"] <= until]


def _ready(rep: Report, casts: list[dict], sp: dict, at: float) -> bool:
    last = max((c["timestamp"] for c in casts if rep.is_spell(c, sp) and c["timestamp"] <= at), default=None)
    return last is None or (at - last) / 1000 >= (sp.get("cooldown") or 0)


SAVED = "would have kept them alive"  # if_pressed's verdict for a spell that would have
NOT_ENOUGH = "not enough on its own"  # and for one that wouldn't have
UNTOLD = "a ready spell's effect can't be told"  # why a replayed death has no answer (`lived` is None)
# How if_pressed replays, in short, where its verdicts are counted with no replay's own head beside them.
REPLAYED = (f"a defensive pressed as they last fell under {BURST_FROM_PCT}% health, at most "
            f"{RECAP_WINDOW_MS // 1000} s before the death; a heal or a healthstone {REACT_MS / 1000:g} s or more "
            "before the killing hit; healing as it was")


@dataclass(frozen=True)
class Pressed:
    """One thing that was ready at a death, replayed (a row of if_pressed): its name ("Barkskin", "a healthstone"),
    what the row takes as given that the log can't show (the healthstone's "if they had one; none used this pull"),
    its effect from the tooltip, the arithmetic, and the verdict in words. `saves` is what the verdict comes to:
    True (SAVED), False (NOT_ENOUGH), None where the replay can't tell or can't be sure."""
    name: str
    verdict: str
    saves: bool | None = None
    given: str = ""
    effect: str = ""
    how: str = ""

    @property
    def label(self) -> str:
        """Its name with what the row takes as given: "a healthstone (if they had one; none used this pull)"."""
        return self.name + (f" ({self.given})" if self.given else "")

    def line(self) -> str:
        head = self.label + (f" ({self.effect})" if self.effect else "")
        return f"  {head}: " + (f"{self.how}: " if self.how else "") + self.verdict


@dataclass(frozen=True)
class Replay:
    """A death replayed with what was ready (if_pressed): the line saying how, and a row per thing ready. Empty,
    and false, when the replay can't tell: no overkill or health logged, or nothing was ready."""
    head: str = ""
    rows: tuple[Pressed, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rows)

    def lines(self) -> list[str]:
        return [self.head, *(r.line() for r in self.rows)] if self.rows else []


def _lethal_burst(hits: list[dict]) -> tuple[list[dict], str]:
    """The lethal burst of the hits read before a death, and where it begins, in words: the hits since the player
    was last at BURST_FROM_PCT of their health or more, or all of them when no hit left them that high."""
    above = [i for i, h in enumerate(hits) if h.get("resourceActor") == 2 and h.get("maxHitPoints")
             and 100 * h["hitPoints"] / h["maxHitPoints"] >= BURST_FROM_PCT]
    if above and above[-1] + 1 < len(hits):
        return hits[above[-1] + 1:], f"the first hit after they were last at {BURST_FROM_PCT}%+ health"
    return hits, (f"the first hit read in the {RECAP_WINDOW_MS // 1000} s before the death (none left them at "
                  f"{BURST_FROM_PCT}%+ health)")


def _unstopped(hits: list[dict], killing: dict) -> float:
    """What some hits did, and would have done to health the player no longer had: the killing hit's amount stops at
    the health left and its overkill is the rest of it, so what a spell takes off that hit, it takes off both."""
    return sum(h.get("amount", 0) for h in hits) + (killing["overkill"] if any(h is killing for h in hits) else 0)


async def if_pressed(rep: Report, f: dict, p: Player, d: dict, hits: list[dict]) -> Replay:
    """Whether a ready defensive, a healthstone or a healing spell would have kept them alive: the lethal burst
    replayed with the effect from the spell's tooltip. Secondary to why they died; the arithmetic, not a verdict on
    the player."""
    hp_hits = [h for h in hits if h.get("resourceActor") == 2 and h.get("maxHitPoints")]
    killing = next((h for h in reversed(hits) if h.get("overkill")), None)
    overkill = killing["overkill"] if killing else 0
    if not killing or not hp_hits or not overkill:
        return Replay()

    def unstopped(some: list[dict]) -> float:
        return _unstopped(some, killing)
    max_hp = hp_hits[-1]["maxHitPoints"]
    burst, began = _lethal_burst(hits)
    start = burst[0]["timestamp"]
    t0 = f["startTime"]
    head = (f"If pressed ({short(overkill)} overkill to make up, healing as it was; a defensive at the start of the "
            f"lethal burst, {clock(start - t0)}, {began}; a heal after the last hit {REACT_MS / 1000:g} s or more "
            "before the killing hit, time to see it):")
    casts = await _their_casts(rep, f, p, d["timestamp"])
    spells = await rep.defensive_spells(p)
    pressed = await _used_ever(rep, p, spells)
    stones, _ = await rep.potions(f, p, until=d["timestamp"])
    candidates = [(sp, sp["spell_id"], "") for sp in spells
                  if sp["name"] in pressed and _ready(rep, casts, sp, start)]
    if not stones:
        candidates.append(({"name": A_HEALTHSTONE}, HEALTHSTONE_SPELL, HEALTHSTONE_GIVEN))
    rows = []
    for sp, spell_id, given in candidates:
        effect = await knowledge.defensive_effect(spell_id)
        if effect is None:
            rows.append(Pressed(sp["name"], "its tooltip doesn't say how much it does; can't tell", given=given))
            continue
        dur_ms = 1000 * (effect.duration_s or sp.get("duration") or 0)
        covered = [h for h in burst if not dur_ms or h["timestamp"] <= start + dur_ms]
        if effect.only == "aoe":
            rows.append(Pressed(sp["name"], "the log doesn't say which hits were area damage; can't tell",
                                given=given, effect=str(effect)))
            continue
        if effect.only == "magic":
            schools = await rep.schools()
            covered = [h for h in covered if schools.get(h["abilityGameID"], 1) != 1]
        if effect.kind == "heal":
            saved, how = _heal_in_time(hp_hits, max_hp, (effect.pct or 0) / 100 * max_hp)
        elif effect.kind == "absorb":  # a shield or temporary health: on top of their health, not capped by it
            saved = min((effect.pct or 0) / 100 * max_hp, unstopped(covered))  # of the hits it is up for
            how = f"absorbs {short(saved)} of the burst"
        else:
            taken = unstopped(covered)
            saved = taken if effect.kind == "immunity" else (effect.pct or 0) / 100 * taken
            how = f"{short(saved)} less of the {short(taken)} it covers"
        saves: bool | None = saved > overkill
        verdict = SAVED if saves else NOT_ENOUGH
        if saves and effect.sure_s:  # only a moment of it is sure (Cloak of Shadows' tooltip)
            sure = unstopped([h for h in covered if h["timestamp"] <= start + 1000 * effect.sure_s])
            if sure <= overkill:
                saves = None  # not a save anyone may count: the row says so
                verdict = (f"{SAVED} only if it held for all {dur_ms // 1000:g} s: its tooltip "
                           "promises the immunity for a moment, so can't be sure")
        rows.append(Pressed(sp["name"], verdict, saves, given, str(effect), how))
    return Replay(head, tuple(rows))


def _heal_in_time(hp_hits: list[dict], max_hp: float, heal: float) -> tuple[float, str]:
    """What a heal pressed in time adds at the killing hit: pressed after the last hit at least REACT_MS before it,
    and only the part they had room for until then (a heal at full health is overhealing)."""
    kill = hp_hits[-1]["timestamp"]
    early = [i for i, h in enumerate(hp_hits[:-1]) if kill - h["timestamp"] >= REACT_MS]
    if not early:
        return 0, f"no hit came {REACT_MS / 1000:g} s or more before the killing hit, so no time to press it"
    t = hp_hits[early[-1]]["timestamp"]
    peak = max(h["hitPoints"] for h in hp_hits[early[-1]:-1])
    saved = min(heal, max_hp - peak)
    gap = (kill - t) / 1000
    if saved < heal:
        return saved, (f"heals only {short(saved)} pressed {gap:.1f} s before the killing hit: they were at "
                       f"{round(100 * peak / max_hp)}% health from then on")
    return saved, f"heals {short(saved)} pressed {gap:.1f} s before the killing hit"


async def _raid_cooldowns_at(rep: Report, f: dict, deaths: list[dict]) -> list[str]:
    """Several players died at once: the raid cooldowns that cut damage or add health and were ready, their reach
    from the tooltip, and how far apart the dead stood (a short-range one needs them stacked)."""
    at = min(x["timestamp"] for x in deaths)
    here = rep.pull_players(f)
    pos = await positions.pull_positions(rep, f)
    spots = [s for x in deaths if (s := positions.where_fell(pos.samples.get(x["targetID"], []), x["timestamp"]))]
    spread = max((positions.distance_yd(a, b) for a in spots for b in spots), default=None)
    found = []
    for q in here.values():
        raid = [sp for sp in await rep.spells(q) if "raid_cd" in (sp.get("tags") or []) and sp.get("cooldown")]
        if not raid:
            continue
        pressed = await _used_ever(rep, q, raid)
        casts = await _their_casts(rep, f, q, at)
        for sp in raid:
            if sp["name"] in pressed and _ready(rep, casts, sp, at):
                effect = await knowledge.defensive_effect(sp["spell_id"])
                if effect and effect.kind in ("reduction", "immunity", "absorb"):
                    found.append(f"{sp['name']} ({q.name}; {effect})")
    t0 = f["startTime"]
    head = (f"{plural(len(deaths), 'player')} died within {BURST_MS // 1000} s of {clock(at - t0)}"
            + (f", the farthest {spread:.0f} yd apart" if spread is not None else ""))
    if not found:
        return [f"{head}. No raid cooldown that cuts damage or adds health was ready then."]
    return [f"{head}. Raid cooldowns that cut damage or add health and were ready (from those who use them this "
            "report): " + "; ".join(found)]


async def _healing_received(rep: Report, f: dict, p: Player, at: float) -> list[str]:
    """Healing a player received in the recap window before a death: total, from whom, per second."""
    t0 = f["startTime"]
    heals = [e for e in await rep.events(f["id"], "Healing", targetID=p.id, startTime=at - RECAP_WINDOW_MS,
                                           endTime=at + 1) if e["type"] == "heal"]
    if not heals:
        return ["Healing received in the window: none."]
    total = sum(e.get("amount", 0) for e in heals)
    over = sum(e.get("overheal", 0) for e in heals)
    by_source: Counter = Counter()
    per_second: Counter = Counter()
    for e in heals:
        by_source[rep.actor(rep.owner(e["sourceID"]))] += e.get("amount", 0)
        per_second[(e["timestamp"] - t0) // 1000] += e.get("amount", 0)
    start = (at - RECAP_WINDOW_MS - t0) // 1000
    seconds = range(int(max(start, 0)), int((at - t0) // 1000) + 1)
    return [f"Healing received in the window: {short(total)} effective (+{short(over)} overhealing), most from "
            + ", ".join(f"{who} {short(n)}" for who, n in by_source.most_common(4)) + ".",
            "  per second: " + " · ".join(f"{clock(sec * 1000)} +{short(per_second[sec])}" for sec in seconds)]


async def _auras_at(rep: Report, f: dict, p: Player, at: float) -> dict[str, dict[str, tuple[int, float, int]]]:
    """The buffs and the enemies' debuffs a player had at a moment: per kind, name -> (source actor, since,
    stacks). Only auras gained during the pull (pre-pull raid buffs and consumables aren't events)."""
    out: dict[str, dict[str, tuple[int, float, int]]] = {"buffs": {}, "debuffs": {}}
    for kind, data_type in (("buffs", "Buffs"), ("debuffs", "Debuffs")):
        # For auras, WCL's sourceID filter selects the player who has the aura; the events keep their source.
        filters = {"hostilityType": "Friendlies"} if kind == "debuffs" else {}
        events = await rep.events(f["id"], data_type, sourceID=p.id, endTime=at + 1, **filters)
        active = out[kind]
        for e in sorted(events, key=lambda e: e["timestamp"]):
            if e.get("targetID") != p.id or (kind == "debuffs" and e.get("sourceID") in rep.names):
                continue
            name = rep.ability(e["abilityGameID"])
            if e["type"] in ("applybuff", "applydebuff"):
                active[name] = (e.get("sourceID", 0), e["timestamp"], 1)
            elif e["type"] in ("applybuffstack", "applydebuffstack") and name in active:
                source, since, _ = active[name]
                active[name] = (source, since, e.get("stack", 1))
            elif e["type"] in ("removebuff", "removedebuff") and e["timestamp"] < at - DEATH_STRIP_MS:
                active.pop(name, None)
    return out


async def _defensives(rep: Report, f: dict, p: Player, at: float) -> list[str]:
    """Which of the player's defensives (Lorrgs' list for the spec), healthstone and potions were used before
    a death, whether each was ready at the time, and which defensive was up (a buff of their own)."""
    casts, auras = await asyncio.gather(rep.casts(f, p, until=at), _auras_at(rep, f, p, at))
    active = {name for name, (source, _, _) in auras["buffs"].items() if source == p.id}
    t0 = f["startTime"]
    lines = []
    try:
        defs = await rep.defensive_spells(p)
    except KnowledgeError as e:
        defs = None
        lines.append(unavailable("Personal defensives", e))
    if defs == []:
        lines.append(f"Lorrgs lists no personal defensives for {p.spec} {p.class_name}.")
    elif defs:
        lines.append("Personal defensives (list and cooldowns from Lorrgs; use times from the log):")
        for sp in defs:
            kind, last = defensive_state(rep, casts, sp, at)
            if last is None:
                state = "not used this pull, so ready if talented"
            else:
                state = (f"last used at {clock(last - t0)} ({sp['cooldown']:.0f} s cooldown), "
                         + ("ready again" if kind == "ready"
                            else f"on cooldown until {clock(last - t0 + 1000 * sp['cooldown'])}"))
            if sp["name"] in active:
                state += ", active at death"
            lines.append(f"  {sp['name']}: {state}")
    stones, potions = await rep.potions(f, p, until=at)
    lines.append(f"Healthstones and healing potions before death: {stones}"
                 + (f"; combat potions (they don't heal): {potions}" if potions else ""))
    return lines


def defensive_state(rep: Report, casts: list[dict], sp: dict, at: float) -> tuple[str, float | None]:
    """A defensive at a moment, from the player's casts before it: "unused" this pull (ready if talented), "ready"
    again or on "cooldown", and when it was last cast (None if never)."""
    times = [c["timestamp"] for c in casts if rep.is_spell(c, sp)]
    if not times:
        return "unused", None
    return ("ready" if (at - times[-1]) / 1000 >= sp["cooldown"] else "cooldown"), times[-1]


async def defensives(rep: Report, boss: str | None = None, whole_pull: bool = False,  # noqa: C901
                     player: str | None = None) -> str:
    """Every player's healthstones and healing potions and personal defensives over a boss's pulls, until each
    wipe was decided: uses, and at each death whether a defensive was off cooldown but not used. With `player`,
    each of their deaths instead, replayed with what was ready (the death recap's arithmetic)."""
    pulls = rep.by_boss[rep.boss(boss)]
    going = rep.still_going(pulls)
    if player:
        return going + await _deaths_replayed(rep, rep.player(player), rep.ended(pulls), whole_pull)
    fights, resets = rep.attempts(pulls)  # a reset's deaths are nobody's, and it is no pull to use a defensive on
    await rep.all_deaths(fights)
    decided = {} if whole_pull else await rep.decided_cuts(fights)
    cut = {f["id"]: decided.get(f["id"], f["endTime"]) for f in fights}
    played = {f["id"]: rep.pull_players(f) for f in fights}
    kits, casts, missing = await _kits(rep, played)
    await rep.tables([(f["id"], {"endTime": cut[f["id"]]}) for f in fights], "Summary")  # potions, in one query
    used: dict[tuple[int, int], list[tuple[float, dict]]] = defaultdict(list)  # (pull, player) -> (time, spell)
    for c in casts:
        q = played.get(c.get("fight"), {}).get(c.get("sourceID"))
        if q and c["timestamp"] <= cut[c["fight"]]:
            sp = next((sp for sp in kits[(q.class_name, q.spec)] if rep.is_spell(c, sp)), None)
            if sp:
                used[(c["fight"], q.id)].append((c["timestamp"], sp))
    stones: Counter = Counter()
    uses: dict[int, Counter] = defaultdict(Counter)
    pulls_in: Counter = Counter()
    deaths: Counter = Counter()
    in_mass: Counter = Counter()  # player -> deaths in a hit killing many
    ready_at_death: dict[int, Counter] = defaultdict(Counter)  # player -> defensive -> deaths it was ready at
    for f in fights:
        for pid, q in played[f["id"]].items():
            pulls_in[pid] += 1
            stones[pid] += (await rep.potions(f, q, until=cut[f["id"]]))[0]
            for _, sp in used[(f["id"], pid)]:
                uses[pid][sp["name"]] += 1
        for d in await rep.deaths(f):
            q = played[f["id"]].get(d["targetID"])
            if not q or d["timestamp"] > cut[f["id"]]:
                continue
            if await mass_death(rep, f, d):  # the raid's to stop: no defensive ready there was a miss
                in_mass[q.id] += 1
                continue
            deaths[q.id] += 1
            for sp in kits[(q.class_name, q.spec)]:
                cd_ms = (sp.get("cooldown") or 0) * 1000
                if not any(sp is s and d["timestamp"] - cd_ms < t <= d["timestamp"] for t, s in used[(f["id"], q.id)]):
                    ready_at_death[q.id][sp["name"]] += 1
    scope = "until each wipe was decided (whole_pull for the rest)" if not whole_pull else "whole pulls"
    lines = [going + f"Healthstones and healing potions, and personal defensives (Lorrgs' defensive tag), on "
             f"{rep.boss_key(fights[0])}: {plural(len(fights), 'pull')}{resets}, {scope}. Per player: pulls, "
             "healthstones and potions, each defensive's uses, deaths with how many of them were saveable (replayed, "
             "a ready defensive or a healthstone would have kept them alive through the hits that killed them; "
             "`player` has each death's), and at how many deaths each defensive they used on this boss was off "
             "cooldown but unused (ignoring charges and cooldown reduction; deaths in a hit killing many apart, the "
             "raid's to stop); defensives never cast are a talent choice or passive, not a miss:"]
    who = {pid: next(here[pid] for here in played.values() if pid in here) for pid in pulls_in}
    died = [pid for pid in pulls_in if deaths[pid]]
    # each of their own deaths replayed with what was ready, as `player` gives them one by one: the count here
    replays = dict(zip(died, await asyncio.gather(*(replay_deaths(rep, who[pid], fights, whole_pull)
                                                    for pid in died)), strict=True))
    saved = {pid: sum(lived(r.more, r.outright) is True for r in rs if not r.mass) for pid, rs in replays.items()}
    for pid in sorted(pulls_in, key=lambda i: (-deaths[i], rep.names.get(i, ""))):
        kit = {sp["name"] for here in played.values() if pid in here
               for sp in kits[(here[pid].class_name, here[pid].spec)]}
        lines.append(_kit_line(who[pid], pulls_in[pid], stones[pid], uses[pid], (deaths[pid], in_mass[pid]),
                               ready_at_death[pid], kit, saved.get(pid)))
    if missing:
        lines.append(missing)
    elif not any(kits.values()):
        lines.append("Lorrgs lists no defensives for these specs: only healthstones and potions are counted.")
    return "\n".join(lines)


def _kit_line(q: Player, pulls: int, stones: int, uses: Counter, died: tuple[int, int], ready_at: Counter,
              kit: set[str], saved: int | None = None) -> str:
    """defensives' line for a player: pulls, healthstones and potions, each defensive's uses, their deaths (`died`:
    their own, and in hits killing many) with the defensives ready but unused at their own, and those never cast."""
    own, mass = died
    used = ", ".join(f"{n} {k}" for n, k in uses.most_common()) or "no defensive used"
    line = (f"- {q.name} ({spaced(q.spec)} {spaced(q.class_name)}): {plural(pulls, 'pull')}, "
            f"{stones} healthstone{'' if stones == 1 else 's'}/potion{'' if stones == 1 else 's'}, {used}")
    if own:
        ready = ", ".join(f"{n} at {k}" for n, k in ready_at.most_common() if uses[n])
        line += (f"; died {own}" + (f" ({saved} saveable)" if saved is not None else "")
                 + (f", ready but unused: {ready}" if ready else ""))
    if mass:
        line += f"; {'and ' if own else ''}died {mass} in hits killing many"
    if never := sorted(kit - set(uses)):
        line += f"; never cast: {', '.join(never)}"
    return line


async def _kits(rep: Report, played: dict[int, dict[int, Player]]
                ) -> tuple[dict[tuple[str, str], list[dict]], list[dict], str]:
    """Each spec's defensives from Lorrgs ((class, spec) -> spells), everyone's casts of them, and the line saying
    they're unavailable (then none of either)."""
    specs = {(q.class_name, q.spec): q for here in played.values() for q in here.values()}
    try:
        kits = dict(zip(specs, await asyncio.gather(*(rep.defensive_spells(q) for q in specs.values())), strict=True))
        return kits, await rep.defensive_casts(), ""
    except KnowledgeError as e:
        return ({spec: [] for spec in specs}, [],
                unavailable("Defensive lists", e) + " Only healthstones and potions are counted.")


def saving(replay: Replay) -> list[Pressed]:
    """The rows of a death's replay (if_pressed) that would have kept them alive. Only a row whose verdict is that
    one: an immunity that would have "only if it held for all 5 s ... so can't be sure" is none of them, in a
    count as in a death's recap."""
    return [r for r in replay.rows if r.saves]


def savers(replay: Replay) -> list[str]:
    """What would have kept them alive, from a death's replay: each spell's name ("a healthstone")."""
    return [r.name for r in saving(replay)]


def untold(replay: Replay) -> int:
    """How many rows of a death's replay can't say what their spell would have done: a tooltip without an amount,
    area damage the log doesn't mark, an immunity sure for a moment only."""
    return sum(r.saves is None for r in replay.rows)


def lived(replay: Replay, outright: bool = False) -> bool | None:
    """Whether something ready would have kept them alive, the one reading of a death's replay that death_recap's
    verdict line and its card, defensives and player_night give. True: a spell would have (`savers`). False: nothing
    ready would have, each row saying so, or the death killed `outright`. None: the log can't tell, the death not
    replayed (no overkill or health logged) or no spell sure to have and one whose effect can't be told."""
    if savers(replay):
        return True
    if outright:
        return False
    return None if not replay or untold(replay) else False


@dataclass(frozen=True)
class Replayed:
    """One death replayed: its pull, the death, its replay (if_pressed: empty when it can't tell, no overkill or
    health logged), and whether it killed outright (killed_outright), which nothing ready would have stopped."""
    f: dict
    d: dict
    more: Replay
    outright: bool
    mass: MassDeath | None = None  # the hit killing many it was one of (mass_death): no one player's to fix


def in_mass_hits(replayed: list[Replayed]) -> list[Replayed]:
    """A player's deaths in a hit killing many, of their replayed deaths (replay_deaths): every one of them, a second
    death in a pull too. The one count defensives and player_night both state."""
    return [r for r in replayed if r.mass]


async def replay_deaths(rep: Report, p: Player, fights: list[dict], whole_pull: bool = False) -> list[Replayed]:
    """Each of a player's deaths on `fights` (before each wipe was decided, unless `whole_pull`), replayed with
    what was ready."""
    fights = rep.attempts(rep.pulls_with(p, fights))[0]  # a reset's death isn't theirs to replay
    await rep.all_deaths(fights)
    decided = {} if whole_pull else await rep.decided_cuts(fights)
    todo = [(f, d) for f in fights for d in await rep.deaths(f)
            if d["targetID"] == p.id and d["timestamp"] <= decided.get(f["id"], f["endTime"])]
    # The hits before each death, and each pull's potions up to it (if_pressed), in one query each.
    windows = [(f["id"], {"startTime": d["timestamp"] - RECAP_WINDOW_MS, "endTime": d["timestamp"] + 1})
               for f, d in todo]
    hits, _ = await asyncio.gather(
        rep.events_for(windows, "DamageTaken", sourceID=p.id, hostilityType="Friendlies", includeResources=True),
        rep.tables([(f["id"], {"endTime": d["timestamp"]}) for f, d in todo], "Summary"))

    async def one(f: dict, d: dict, hits: list[dict]) -> Replayed:
        more = await if_pressed(rep, f, rep.in_pull(f, p), d, hits)
        return Replayed(f, d, more, not more and bool(d.get("killingAbilityGameID"))
                        and bool(killed_outright(rep, f, d, hits)), await mass_death(rep, f, d))
    return list(await asyncio.gather(*(one(f, d, h) for (f, d), h in zip(todo, hits, strict=True))))


async def _went_on(rep: Report, saved: list[Replayed]) -> str:
    """How long the pulls went on after the deaths something ready would have saved, until each wipe was decided
    or the pull ended: what living through the burst was worth to the pull, which the replay can't say. The deaths
    a wipe was decided within DEATH_BURST_MS of (a moment of deaths) are counted apart: the time after them isn't
    what living was worth, when with them alive the wipe may not have been decided then."""
    if not saved:
        return ""
    decided = await rep.decided_cuts([r.f for r in saved])
    after = sorted(decided.get(r.f["id"], r.f["endTime"]) - r.d["timestamp"] for r in saved
                   if decided.get(r.f["id"], inf) - r.d["timestamp"] > DEATH_BURST_MS)
    those = "that death" if len(saved) == 1 else f"those {len(saved)}"
    then = f"the wipe was decided as they died (within {DEATH_BURST_MS // 1000} s)"
    if not after:
        return f" At {those} {then}."
    went = (f"the pull went on for {clock(after[0])}" if len(after) == 1 else
            f"the pulls went on for a median of {clock(statistics.median(after))} ({clock(after[0])} to "
            f"{clock(after[-1])})") + " until the wipe was decided or the pull ended."
    if len(after) == len(saved):
        return f" After {those} {went}"
    return (f" At {len(saved) - len(after)} of {those} {then}; after the other "
            f"{'one' if len(after) == 1 else len(after)} {went}")


async def _deaths_replayed(rep: Report, p: Player, fights: list[dict], whole_pull: bool) -> str:
    """Each of a player's deaths on a boss before its wipe was decided, with what a ready defensive or a
    healthstone would have changed (if_pressed, the death recap's replay)."""
    fights = rep.pulls_with(p, fights)
    replayed = await replay_deaths(rep, p, fights, whole_pull)
    done = [r for r in replayed if not r.mass]
    saved = sum(lived(r.more, r.outright) is True for r in done)
    unsure = sum(bool(r.more) and lived(r.more, r.outright) is None for r in done)  # replayed, and it can't tell
    scope = "before each wipe was decided" if not whole_pull else "in the whole pulls"
    attempts, resets = rep.attempts(fights)
    lines = [f"{p.name}'s {plural(len(done), 'death')} on {rep.boss_key(fights[0])} {scope}, over "
             f"{plural(len(attempts), 'pull')}{resets}, each replayed with what was ready ({REPLAYED}; secondary to "
             f"what killed them): a ready defensive or a healthstone would have kept them alive through the hits "
             f"that killed them at {saved} of them"
             + (f" (can't tell at {unsure} more: {UNTOLD})" if unsure else "") + "."
             + ("" if whole_pull else await _went_on(rep, [r for r in done if lived(r.more, r.outright) is True]))]
    if on_reset := [f"pull {rep.pull_no(f)} {clock(d['timestamp'] - f['startTime'])}"
                    for f in fights if f not in attempts for d in await rep.deaths(f) if d["targetID"] == p.id]:
        lines.append(f"Not counted, {plural(len(on_reset), 'death')} on a reset (the boss untouched, no attempt): "
                     f"{', '.join(on_reset)}.")
    if mass := [(r, m) for r in in_mass_hits(replayed) if (m := r.mass)]:
        lines.append(f"Not replayed, {plural(len(mass), 'death')} in a hit killing many (the raid's to stop; a "
                     f"defensive would only have bought seconds of a pull it lost): "
                     + ", ".join(f"pull {rep.pull_no(r.f)} {clock(r.d['timestamp'] - r.f['startTime'])} "
                                 f"({rep.ability(r.d.get('killingAbilityGameID'))}, "
                                 + MASS_HIT.format(n=m.killed, at=clock(m.at_ms)) + ")"
                                 for r, m in mass))
    unknown, outright = [], []
    for r in done:
        at = f"pull {rep.pull_no(r.f)} {clock(r.d['timestamp'] - r.f['startTime'])}"
        if r.outright:
            outright.append(f"{at} ({rep.ability(r.d['killingAbilityGameID'])})")
        elif not r.more:
            unknown.append(at)
        else:
            lines.append(f"- {at}, {rep.ability(r.d.get('killingAbilityGameID'))}: "
                         + "; ".join(row.line().strip() for row in r.more.rows))
    if outright:
        lines.append(f"Killed outright at {len(outright)} (no hit of the killing blow logged, after high health: "
                     f"nothing ready would have stopped it): {', '.join(outright)}")
    if unknown:
        lines.append(f"Can't tell at {len(unknown)} (no overkill or health logged): {', '.join(unknown)}")
    return "\n".join(lines)
