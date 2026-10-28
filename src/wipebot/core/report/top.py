"""Against the top parses: a player's casts (`compare_with_top` casts), and what the top players of a spec
play (`compare_with_top` without a player)."""
import asyncio
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .. import knowledge
from ..cache import FOREVER
from ..knowledge import KnowledgeError, spaced
from ..pulls import DIFFICULTIES
from ..text import clock, fight_url, name_key, plural, short
from ..wcl import KILLS_PAGE, KILLS_PAGES, WCL, WCLError
from .base import FactError, Player, PullRef, Report, unavailable
from .build import MAX_BUILD_TALENTS, SAME_HERO_MIN, TOP_PARSES, hero_of
from .casting import GAP_MS, GCD_MAX_MS, Casting, alive, cast_rows, casting
from .coaching import cap_stretches, resource_of, resource_points, resource_use
from .ranks import (
    HEALERS_HEAD,
    HEALERS_READING,
    NO_TOP,
    Spread,
    healer_share,
    metric_of,
    readable,
    spec_distribution,
    spread_line,
    top_healer_shares,
    top_parses,
)

REFERENCE_PARSES = 5  # top parses a player's casts are compared with
FAR_MORE_PER_MIN = 2.0  # casts per minute above the top median that mean something (one cast on a short pull doesn't)
LUST_CAST_MS = 2000  # a cast this close before a Bloodlust landed is the Bloodlust (or its kin) itself


async def _encounter(wcl: WCL, boss: str) -> tuple[int, int, str]:
    """A boss's Warcraft Logs zone and encounter (zone id, encounter id, name) by its name, in the newest raid that
    has it, not a test realm's."""
    found = [(zone, eid, name) for zone, zone_name, eid, name in await wcl.all_encounters()
             if knowledge.names_boss(name, boss) and not re.search(r"\((ptr|beta)\)", zone_name, re.I)]
    if not found:
        raise FactError(f"Warcraft Logs ranks no boss named {boss}")
    return max(found)


async def boss_name(wcl: WCL, boss: str) -> str:
    """A boss's name as Warcraft Logs spells it ("Twin Fangs" -> "The Twin Fangs"), which the guide sites' URLs and
    titles follow; the name as given when no boss matches it (a raid, a class) or the list can't be read."""
    try:
        return (await _encounter(wcl, boss))[2]
    except (FactError, WCLError):
        return boss


KILLS_LISTED = 5  # the fastest kills linked one by one
KILLS_MAX = 500  # the most kills top_kills reads (ten pages of the rankings)
KILLS_STRETCH = 100  # with more kills than this, how many healers they brought, stretch by stretch of the list
Ranking = Literal["fastest", "first", "latest"]


async def top_kills(wcl: WCL, boss: str, difficulty: str | None = None, ranking: Ranking = "fastest",
                    count: int = KILLS_PAGE, regions: Sequence[str] | None = None) -> str:
    """What kills of a boss brought: how many tanks, healers, melee and ranged, as Warcraft Logs' rankings count
    them, with their kill times and item levels ("is it a 4-heal fight?"): the `count` fastest, the first (each
    guild's first kill, earliest first: the progress ranking) or the latest first kills, on some regions' servers
    or all, each region's in one ranking read and merged."""
    zone, eid, boss_name = await _encounter(wcl, boss)
    ids = {name: i for i, name in DIFFICULTIES.items()}
    tried = [difficulty.title()] if difficulty else ["Mythic", "Heroic", "Normal"]
    count = max(1, min(count, KILLS_MAX))
    metric = "speed" if ranking == "fastest" else "progress"
    kills: list[dict] = []
    cut = False
    for diff in tried:
        try:
            got = await asyncio.gather(*(wcl.ranked_kills(eid, ids[diff], metric, count, r, ranking == "latest")
                                         for r in [x.upper() for x in regions or []] or [None]))
        except WCLError as e:
            raise FactError(f"Warcraft Logs rankings unavailable: {e}") from e
        kills, cut = [k for ks, _ in got for k in ks], any(c for _, c in got)
        if kills:
            break
    where = f" on {'/'.join(r.upper() for r in regions)} servers" if regions else ""
    if not kills:
        return f"Warcraft Logs has no ranked kills of {boss_name}{where} on " + " or ".join(tried) + " yet."
    if ranking == "fastest":
        kills = sorted(kills, key=lambda k: k.get("duration") or math.inf)[:count]
    else:
        kills = sorted(kills, key=lambda k: k.get("startTime") or 0)
        kills = kills[-count:] if ranking == "latest" else kills[:count]

    def spread(role: str, these: list[dict]) -> str:
        counts = Counter(k.get(role) for k in these if isinstance(k.get(role), int))
        return ", ".join(f"{n} in {plural(c, 'kill')}" for n, c in sorted(counts.items(), key=lambda x: -x[1]))
    durations = [k["duration"] for k in kills if k.get("duration")]
    levels = [k["bracketData"] for k in kills if k.get("bracketData")]
    which = {"fastest": f"{len(kills)} fastest kills",
             "first": f"first {len(kills)} kills (each guild's first, earliest first)",
             "latest": f"latest {len(kills)} first kills (each guild's first, the newest)"}[ranking]
    days = sorted(k["startTime"] for k in kills if k.get("startTime"))
    when = f", killed {_day(days[0])} to {_day(days[-1])}" if days and ranking != "fastest" else ""
    lines = [f"The {which} of {boss_name} ({diff}){where} on Warcraft Logs{when}: median kill "
             + (clock(statistics.median(durations)) if durations else "?") + ", median item level "
             + (f"{statistics.median(levels):.0f}" if levels else "?") + ". What they brought:",
             f"  healers: {spread('healers', kills)}", f"  tanks: {spread('tanks', kills)}",
             f"  melee: {spread('melee', kills)}; ranged: {spread('ranged', kills)}"]
    if cut:
        lines.append(f"Warcraft Logs serves only the first {KILLS_PAGES * KILLS_PAGE} kills of this ranking, and "
                     "newer ones lie past them: these are the newest it serves (a region of servers holds fewer).")
    by_healers = defaultdict(list)
    for k in kills:
        if k.get("duration") and k.get("bracketData"):
            by_healers[k.get("healers")].append(k)
    if len(by_healers) > 1:
        lines.append("By healers brought (median kill, item level): " + "; ".join(
            f"{n}: {clock(statistics.median(k['duration'] for k in ks))}, "
            f"{statistics.median(k['bracketData'] for k in ks):.0f}" for n, ks in sorted(by_healers.items())))
    if len(kills) > KILLS_STRETCH:
        lines.append("Healers down the list, " + ("fastest first" if ranking == "fastest" else "earliest first")
                     + ": " + "; ".join(f"kills {i + 1}-{i + len(part)}: {spread('healers', part)}"
                                        for i in range(0, len(kills), KILLS_STRETCH)
                                        if (part := kills[i:i + KILLS_STRETCH])))
    shown = kills[:KILLS_LISTED] if ranking != "latest" else kills[-KILLS_LISTED:][::-1]
    lines.append(("The fastest: " if ranking == "fastest" else "The first: " if ranking == "first" else "The newest: ")
                 + "; ".join(
        f"{(k.get('guild') or {}).get('name') or 'a raid'} ({(k.get('server') or {}).get('region') or '?'}) "
        f"{clock(k.get('duration') or 0)}" + (f" on {_day(k['startTime'])}" if ranking != "fastest" and
                                               k.get("startTime") else "")
        + f", item level {k.get('bracketData') or 0:.0f}, {k.get('tanks')} tanks {k.get('healers')} healers"
        + (", " + fight_url(k["report"]["code"], k["report"]["fightID"]) if (k.get("report") or {}).get("code")
           else "")
        for k in shown))
    lines.append(("The fastest kills are the best geared, most practised raids: what they bring works at their gear "
                  "and kill time, not proven for a progression pull." if ranking == "fastest" else
                  "A guild's first kill is a progression kill, but only guilds that log are ranked.")
                 + f" Source: Warcraft Logs {metric} rankings "
                 f"https://www.warcraftlogs.com/zone/rankings/{zone}?boss={eid}&metric={metric}")
    return "\n".join(lines)


def _day(ms: float) -> str:
    return time.strftime("%a %Y-%m-%d", time.gmtime(ms / 1000))


async def top_builds(wcl: WCL, spec: str, boss: str, difficulty: str | None = None) -> str:
    """What the top parses of a spec on a boss play, with no pull of the asker's needed: hero tree, the talents
    where they differ, trinkets, item level and kill time, the top logs, and a typical build's import string."""
    class_name, spec_name = await knowledge.class_and_spec(spec)
    label = f"{spaced(spec_name)} {spaced(class_name)}"
    zone, eid, boss_name = await _encounter(wcl, boss)
    metric = "hps" if await knowledge.heals(class_name, spec_name) else "dps"
    ids = {name: i for i, name in DIFFICULTIES.items()}
    tried = [difficulty.title()] if difficulty else ["Mythic", "Heroic", "Normal"]
    top: list[dict] = []
    for diff in tried:
        try:
            top = (await wcl.top_rankings(eid, class_name, spec_name, ids[diff], metric))[:TOP_PARSES]
        except WCLError as e:
            raise FactError(f"Warcraft Logs rankings unavailable: {e}") from e
        if top:
            break
    if not top:
        return (f"Warcraft Logs has no ranked {label} kills of {boss_name} on "
                + " or ".join(tried) + " yet.")
    n = len(top)
    half = n // 2
    tree = await knowledge.tree_for(class_name, spec_name)
    names = await knowledge.talent_names()

    def named(i: int) -> str:
        return names.get(i, f"talent {i}")

    talents = [{t["talentID"] for t in r.get("talents") or [] if t.get("points", 1) > 0} for r in top]
    heroes = [knowledge.build_hero(tree, t) if tree else None for t in talents]
    skipped = tried[:tried.index(diff)]
    lines = [f"The top {plural(n, metric + ' parse')} of {label} on {boss_name} ({diff}"
             + (f"; none ranked on {' or '.join(skipped)} yet" if skipped else "") + "): "
             f"median kill {clock(sorted(float(r.get('duration') or 0) for r in top)[half])}, median item level "
             f"{sorted(float(r.get('bracketData') or 0) for r in top)[half]:.0f}, best "
             f"{short(float(top[0].get('amount') or 0))} {metric}."]
    tree_counts = Counter(h for h in heroes if h)
    if tree_counts:
        lines.append("Hero tree: " + ", ".join(f"{h} {c} of {n}" for h, c in tree_counts.most_common()))
    # Talents per hero tree when enough play it: the other tree's build differs by design.
    groups = [(h, [t for t, th in zip(talents, heroes, strict=True) if th == h])
              for h, c in tree_counts.most_common() if c >= SAME_HERO_MIN] or [(None, talents)]
    for hero, pool in groups:
        taken: Counter = Counter()
        for t in pool:
            taken.update(t)
        m = len(pool)
        core = [i for i in taken if taken[i] / m >= 0.9]
        split = sorted((i for i in taken if 0.1 <= taken[i] / m < 0.9), key=lambda i: -taken[i])
        lines.append(f"Talents of the {m}" + (f" playing {hero}" if hero else "") + f": {len(core)} taken by "
                     "nearly all (90%+, the import string has them); where they differ: "
                     + (", ".join(f"{named(i)} ({100 * taken[i] // m}%)" for i in split[:16]) or "nowhere"))
        try:
            lines += _guides_against_top(await _guide_builds(class_name, spec_name), hero, taken, m, tree, named)
        except KnowledgeError as e:
            lines.append("  " + unavailable("Against the guides' raid builds", e))
    trinkets: Counter = Counter()
    for r in top:
        trinkets.update({g["name"] for slot, g in enumerate(r.get("gear") or []) if slot in (12, 13) and g.get("name")})
    lines.append("Trinkets: " + ", ".join(f"{t} ({100 * c // n}%)" for t, c in sorted(
        trinkets.items(), key=lambda tc: (-tc[1], tc[0]))[:6]))
    lines += await _top_logs(wcl, top, eid, (class_name, spec_name, label), ids[diff], boss_name, metric)
    if code := await _typical_code(wcl, top, talents, groups[0][1]):
        lines.append(code)
    lines.append("Top parses are the highest-damage kills, mostly by the first guilds to kill it; what they take is "
                 "popular, not proven best for a progression pull. Sources: Warcraft Logs rankings "
                 f"https://www.warcraftlogs.com/zone/rankings/{zone}?boss={eid}, talent names from Raidbots "
                 "https://www.raidbots.com")
    return "\n".join(lines)


async def _top_logs(wcl: WCL, top: list[dict], eid: int, spec: tuple[str, str, str], difficulty: int, boss: str,
                    metric: str) -> list[str]:
    """The top three logs linked (a healer's with their raids' healers), and where the spec's ranked kills spread;
    `spec` is (class, spec, label)."""
    class_name, spec_name, label = spec
    listed = [r for r in top[:3] if r.get("report")]
    spread, shares = await asyncio.gather(
        _spread(wcl, eid, class_name, spec_name, difficulty, metric),
        top_healer_shares(wcl, listed) if metric == "hps" else asyncio.sleep(0, [None] * len(listed)))
    lines = ["The top logs: " + "; ".join(
        f"{r['name']} {short(float(r.get('amount') or 0))} {metric}, {clock(float(r.get('duration') or 0))}, "
        f"item level {float(r.get('bracketData') or 0):.0f}"
        + (f", {r['guild']['name']}" if r.get("guild") else "")
        + " " + fight_url(r["report"]["code"], r["report"]["fightID"]) for r in listed)]
    if any(shares):
        lines += [HEALERS_HEAD, *(f"  {r['name']}: {share}" for r, share in zip(listed, shares, strict=True) if share),
                  HEALERS_READING]
    if line := spread_line(spread, label, boss, metric) if isinstance(spread, Spread) else spread:
        lines.append(line)
    return lines


async def _spread(wcl: WCL, eid: int, class_name: str, spec: str, difficulty: int, metric: str) -> Spread | str:
    """How the spec's ranked kills of the boss spread, or the line saying it's unavailable."""
    try:
        return Spread(*await spec_distribution(wcl, eid, class_name, spec, difficulty, metric))
    except WCLError as e:
        return unavailable("Where the spec's ranked kills spread", e)


async def _guide_builds(class_name: str, spec: str) -> list[dict]:
    builds = await knowledge.guide_builds(class_name, spec)
    return [b for b in builds if len(b["talents"]) <= MAX_BUILD_TALENTS]  # more: a decode gone wrong


def _guides_against_top(builds: list[dict], hero: str | None, taken: Counter, n: int, tree: dict | None,
                        named: Callable[[int], str]) -> list[str]:
    """The guides' raid builds for a hero tree against what the top parses take: talents most top players take
    that a build lacks, and a build's picks few of them take."""
    builds = [b for b in builds if not hero or b["hero"] == hero]
    if not builds:
        return []
    free = {e["id"] for key in ("classNodes", "specNodes", "heroNodes", "subTreeNodes")
            for node in (tree or {}).get(key) or [] if node.get("freeNode") or key == "subTreeNodes"
            for e in node.get("entries") or []}
    by_name: Counter = Counter()  # by name: a talent can have an entry id per rank
    for i, c in taken.items():
        if i not in free:
            by_name[named(i)] = max(by_name[named(i)], c)
    lines = ["  Against the guides' raid builds (a guide build is a starting point, often not for this boss):"]
    for b in builds[:3]:
        guide = {named(i) for i in b["talents"] if i not in free}
        lacks = sorted((t for t in by_name if t not in guide and by_name[t] / n >= 0.6), key=lambda t: (-by_name[t], t))
        odd = sorted((t for t in guide if by_name[t] / n <= 0.25), key=lambda t: (by_name[t], t))
        lines.append(f"    {b['source']} \"{b['label']}\" ({b['url']}): lacks "
                     + (", ".join(f"{t} ({100 * by_name[t] // n}%)" for t in lacks) or "nothing most take")
                     + "; takes what few top players do: "
                     + (", ".join(f"{t} ({100 * by_name[t] // n}%)" for t in odd) or "nothing"))
    return lines


async def _typical_code(wcl: WCL, top: list[dict], talents: list[set[int]], pool: list[set[int]]) -> str | None:
    """The import string of the most typical build among the best parses of the main hero tree: the one that
    takes the most of what most of them take and the least of what few do."""
    taken: Counter = Counter()
    for t in pool:
        taken.update(t)
    m = len(pool)

    def typical(t: set[int]) -> int:
        return sum(1 if taken[i] / m >= 0.5 else -1 for i in t)

    candidates = [(r, t) for r, t in zip(top[:10], talents[:10], strict=False) if t in pool and readable(r)]
    if not candidates:
        return None
    r, _ = max(candidates, key=lambda rt: typical(rt[1]))
    try:
        actor = (await wcl.player_ids(r["report"]["code"])).get(r["name"])
        code = actor and await wcl.talent_code(r["report"]["code"], r["report"]["fightID"], actor)
    except WCLError:
        return None
    if not code:
        return None
    return (f"The most typical build of the best ten, {r['name']}'s, as an import string (paste into the game's "
            f"talent window): {code}")


@dataclass
class SpellNames:
    """A spec's spells from Lorrgs, to count a spell once: it can log under two ids (Eternity Surge with and without
    Font of Magic, a Mutilate per hand) or under a replacing talent's name (Incarnation for Celestial Alignment), so
    casts are merged by name, and by Lorrgs' spell for the ids it lists as one spell's variations."""
    spells: list[dict]
    by_id: dict[int, dict] = field(init=False)
    by_name: dict[str, dict] = field(init=False)

    def __post_init__(self):
        self.by_id = {i: sp for sp in self.spells for i in [sp["spell_id"], *(sp.get("variations") or [])]}
        self.by_name = {name_key(sp["name"]): sp for sp in self.spells}

    def canonical(self, a: dict) -> str:
        """The name a Casts table row is counted under."""
        sp = self.by_id.get(a.get("guid")) or self.named(a["name"])  # type: ignore[arg-type]
        return sp["name"] if sp else a["name"]

    def named(self, name: str) -> dict | None:
        return self.by_name.get(name_key(name))

    def per_minute(self, entries: list[dict], minutes: float) -> dict[str, float | None]:
        """Casts per minute by spell; None for a spell the table lists without a single cast (an empowered spell
        under its other id leaves begincasts only): its count isn't known, which isn't 0."""
        cpm: dict[str, float | None] = {}
        for a in entries:
            n = self.canonical(a)
            if a.get("total"):
                cpm[n] = (cpm.get(n) or 0) + a["total"] / minutes
            else:
                cpm.setdefault(n, None)
        return cpm


@dataclass
class Reference:
    """A top parse's casts: per minute by spell, and how their casting flowed."""
    ranking: dict
    cpm: dict[str, float | None]
    flow: Casting
    capped: float | None = None  # the share of the kill their resource sat at its cap (None: not read)
    ids: dict[str, int] = field(default_factory=dict)  # a spell's id by the name it's counted under, for its icon


@dataclass
class CastRate:
    """One spell's casts per minute alive, the player's against the top parses': the median and range of those whose
    logs count it (None when none does), and `flag` where the player's is far off ("never cast", "far fewer", "far
    more")."""
    name: str
    mine: float
    median: float | None
    low: float | None
    high: float | None
    flag: str = ""


def _worn(det: dict) -> set[str] | None:
    """The names of a player's items at the pull, None when the log doesn't have their gear."""
    gear = (det.get("combatantInfo") or {}).get("gear") or []
    return {name_key(g["name"]) for g in gear if g.get("name")} or None


def cast_rates(names: SpellNames, my_cpm: dict[str, float | None], found: list[Reference],
               lust_casts: set[str], worn: set[str] | None = None) -> list[CastRate]:
    """Each spell's casts per minute, theirs against the top parses', most cast by the top first. With `worn`, the
    names of the player's items: an item of the top's they don't wear is "not worn", not "never cast"."""
    items = {name_key(g["name"]) for x in found for g in x.ranking.get("gear") or [] if g.get("name")}
    def known(n: str) -> list[float]:
        """The references' counts of a spell, those that aren't known left out."""
        return sorted(c for c in (x.cpm.get(n, 0) for x in found) if c is not None)

    out = []
    for n in sorted({n for x in found for n in x.cpm} | set(my_cpm), key=lambda n: (-_median(known(n)), n))[:25]:
        others, me = known(n), my_cpm.get(n) or 0
        med = _median(others)
        if med < 0.05 and me < 0.05:
            continue
        if not others:  # every reference used it in a way the table doesn't count
            out.append(CastRate(n, me, None, None, None))
            continue
        flag = ""
        sp = names.named(n)
        # A cooldown of a minute or more, an item, a racial or Bloodlust is cast once on a short pull: no fault.
        once = bool(sp) and ((sp.get("cooldown") or 0) >= 60 or str(sp.get("spell_type") or "").startswith("other"))
        if me == 0 and worn is not None and name_key(n) in items - worn:
            flag = "not worn: an item of theirs the player doesn't have on"
        elif me == 0 and sum(1 for x in others if x > 0) > len(others) / 2:
            flag = "never cast"
        elif med and me < 0.6 * med:
            flag = "far fewer"
        elif me > 1.6 * med and me - med >= FAR_MORE_PER_MIN and not once and n not in lust_casts:
            flag = "far more"
        out.append(CastRate(n, me, med, others[0], others[-1], flag))
    return out


@dataclass
class CastComparison:
    """A player's casts on a pull against top parses of their spec (`cast_comparison`)."""
    p: Player
    f: dict
    hero: str | None
    same_hero: bool  # the top parses are of the player's hero tree
    alive_s: float
    ilvl: float
    found: list[Reference]
    names: SpellNames
    merged: str  # a note when Lorrgs' spell list couldn't be read
    mine: list[dict]  # the player's Casts table rows
    my_cpm: dict[str, float | None]
    rates: list[CastRate]
    flow: Casting
    resource: str  # the resource they spend, "" for none
    my_capped: list[tuple[float, float]]  # its stretches at the cap


async def cast_comparison(rep: Report, player: str, pull: PullRef = None, boss: str | None = None
                          ) -> CastComparison | str:
    """A player's casts per minute of each spell on a pull, next to top parses of their spec and hero tree on the
    same boss, the closest in item level; or why there's nothing to compare."""
    f = rep.fight(pull, boss)
    p = rep.player(player)
    rep.require_in(f, p)
    p = rep.in_pull(f, p)
    # A player's own Casts table: the all-players one keeps only each player's top five spells.
    details, deaths, events, table = await asyncio.gather(
        rep.details(f), rep.deaths(f), rep.cast_events(f, p), rep.table(f["id"], "Casts", sourceID=p.id))
    det = details.get(p.id) or {}
    own = [d for d in deaths if d["targetID"] == p.id]
    alive_s, alive_until = alive(rep, f, p, own, [c for c in events if c["type"] == "cast"])
    if not (mine := cast_rows(table.get("entries") or [])):
        return f"No casts recorded for {p.name} on {rep.label(f)}."
    top = [r for r in await top_parses(rep, f, p) if readable(r)]
    if not top:
        return NO_TOP.format(spec=spaced(p.spec), cls=spaced(p.class_name), boss=f["name"])
    hero, tree = await asyncio.gather(hero_of(rep, p, det), knowledge.tree_for(p.class_name, p.spec))
    same_hero = [r for r in top if tree and hero
                 and knowledge.build_hero(tree, {t.get("talentID") for t in r.get("talents") or []}) == hero]
    ilvl = float(det.get("maxItemLevel") or 0)
    refs = sorted(same_hero or top, key=lambda r: abs(float(r.get("bracketData") or 0) - ilvl))[:REFERENCE_PARSES]
    try:
        names, merged = SpellNames(await rep.spells(p)), ""
    except KnowledgeError as e:
        names, merged = SpellNames([]), " " + unavailable("Lorrgs' spell list, which merges a spell's ids", e)
    landed = [c for c in events if c["type"] == "cast"]
    kind, most = await resource_of(p, landed)
    found, lust_casts, (_, resource, my_capped) = await asyncio.gather(
        asyncio.gather(*(_reference(rep, r, names, (kind, most) if kind else None) for r in refs)),
        _lust_casts(rep, f, p),
        resource_use(rep, f, p, landed, alive_s, alive_until) if kind else asyncio.sleep(0, ([], "", [])))
    found = [x for x in found if x]
    if not found:
        return "Couldn't read the casts of any top parse."
    my_cpm = names.per_minute(mine, max(alive_s / 60, 1 / 60))
    return CastComparison(p, f, hero, bool(same_hero), alive_s, ilvl, found, names, merged, mine, my_cpm,
                          cast_rates(names, my_cpm, found, lust_casts, _worn(det)),
                          casting(events, alive_until, [d["timestamp"] for d in own]), resource, my_capped)


async def compare_casts(rep: Report, player: str, pull: PullRef = None, boss: str | None = None) -> str:
    """Casts per minute of each spell for a player on a pull, next to top parses of their spec and hero tree on
    the same boss, the closest in item level."""
    c = await cast_comparison(rep, player, pull, boss)
    if isinstance(c, str):
        return c
    p, f, found, alive_s = c.p, c.f, c.found, c.alive_s
    lines = [f"{p.label}{f', {c.hero}' if c.hero else ''}, casts per minute alive on {rep.label(f)} ({alive_s:.0f} s "
             f"alive) next to {plural(len(found), 'top ' + metric_of(p) + ' parse')} of their spec"
             + (f" and hero tree ({c.hero})" if c.same_hero else "") + f" on {f['name']}, the closest to their item "
             f"level {c.ilvl:.0f}: " + "; ".join(
                 f"{x.ranking['name']} ({float(x.ranking.get('bracketData') or 0):.0f}, kill "
                 f"{clock(x.ranking.get('duration') or 0)}, "
                 f"{fight_url(x.ranking['report']['code'], x.ranking['report']['fightID'])})" for x in found),
             "Spell: theirs / top median (top range), per minute:" + c.merged]
    if p.role == "healer":
        lines[1:1] = await _healers_lines(rep, f, p, await rep.details(f), found)
    lines += _rate_lines(c.names, c.mine, c.rates)
    lines.append(f"Total: {sum(v or 0 for v in c.my_cpm.values()):.0f} / "
                 f"{_median([sum(v or 0 for v in x.cpm.values()) for x in found]):.0f} per "
                 "minute. Every cast event counts (procs and items too); a spell logged under several ids or a "
                 "replacing talent's name counts as one. Cooldowns of a minute or more depend on the pull's "
                 "length, and a pull with Bloodlust at the start is busier than a whole kill.")
    lines.append(_gaps_line(c.flow, [x.flow for x in found]))
    theirs = sorted(100 * x.capped for x in found if x.capped is not None)
    if c.resource and theirs:
        mine = 100 * sum(b - a for a, b in c.my_capped) / 1000 / max(alive_s, 1)
        lines.append(f"{c.resource} at its cap, share of the time alive: {mine:.0f}% / {_median(theirs):.0f}% "
                     f"({theirs[0]:.0f}-{theirs[-1]:.0f}%). The top parses meet the same fight: as much time at the "
                     "cap as theirs is the fight's (movement, knockbacks, an intermission); more is gain lost.")
    return "\n".join(lines)


async def _healers_lines(rep: Report, f: dict, p: Player, details: dict[int, dict],
                         found: list[Reference]) -> list[str]:
    """For a healer: how their healing stood among their raid's healers on the pull, and each top parse's in its
    own raid."""
    table, shares = await asyncio.gather(rep.table(f["id"], "Healing"),
                                         top_healer_shares(rep.wcl, [x.ranking for x in found]))
    healers = {d["name"] for d in details.values() if d.get("role") == "healers"}
    own = healer_share(p.name, {e["name"]: float(e.get("total") or 0) for e in table.get("entries") or []
                                if e.get("name") in healers})
    told = [f"  {x.ranking['name']}: {share}" for x, share in zip(found, shares, strict=True) if share]
    if not own and not told:
        return []
    return [HEALERS_HEAD, f"  {p.name} on this pull: {own or 'unknown'}", *told, HEALERS_READING]


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


async def _reference(rep: Report, r: dict, names: SpellNames,
                     resource: tuple[int, float] | None = None) -> Reference | None:
    """A top parse's casts from its own log, or None when they can't be read; with a resource (power type, the
    player's maximum of it), also the share of the kill it sat at its cap."""
    code, fight_id = r["report"]["code"], r["report"]["fightID"]
    try:
        # A ranked kill's report is finished: what's read from it never changes.
        ids = await rep.keep(("players", code), [], lambda: rep.wcl.player_ids(code), FOREVER)
        if r.get("name") not in ids:
            return None
        pid = ids[r["name"]]
        t, events = await asyncio.gather(
            rep.keep(("casts", code, fight_id, pid), [],
                     lambda: rep.wcl.table(code, [fight_id], "Casts", sourceID=pid), FOREVER),
            rep.keep(("cast_events", code, fight_id, pid), [],
                     lambda: rep.wcl.events(code, [fight_id], "Casts", sourceID=pid), FOREVER))
    except WCLError:
        return None
    minutes = (r.get("duration") or 0) / 60000
    if not t.get("entries") or not minutes:
        return None
    # The kill's end isn't in the ranking: the last cast is close enough (the kill's deaths aren't read).
    last = max((e["timestamp"] for e in events), default=0)
    rows = cast_rows(t["entries"])
    return Reference(r, names.per_minute(rows, minutes), casting(events, last + GCD_MAX_MS),
                     await _capped(rep, code, fight_id, pid, events, *resource) if resource else None,
                     {names.canonical(a): a["guid"] for a in rows if a.get("guid")})


async def _capped(rep: Report, code: str, fight_id: int, pid: int, events: list[dict], kind: int, most: float
                  ) -> float | None:
    """The share of a top parse's kill, from its first cast to its last, its resource sat at its cap (as
    resource_use reads the player's). Their cast events don't carry their maximum: the player's, or the highest
    the graph shows when that's more (a talent that raises it)."""
    stamps = [e["timestamp"] for e in events]
    if not stamps:
        return None
    span = {"id": fight_id, "startTime": min(stamps), "endTime": max(stamps) + GCD_MAX_MS}
    try:
        graph = await rep.keep(("graph", code, fight_id, "Resources", pid, kind), [], lambda: rep.wcl.graph(
            code, span, "Resources", sourceID=pid, abilityID=100 + kind), FOREVER)
    except WCLError:
        return None
    points = resource_points(graph, pid, span["endTime"])
    top = max(most, *(v for _, v in points)) if points else 0
    if not top:
        return None
    capped = sum(b - a for a, b in cap_stretches(points, top, span["endTime"]))
    return capped / (span["endTime"] - span["startTime"])


def _rate_lines(names: SpellNames, mine: list[dict], rates: list[CastRate]) -> list[str]:
    """Each spell's casts per minute, theirs against the top parses', flagged where theirs is far off."""
    ids: dict[str, list[str]] = defaultdict(list)  # the player's ids of each spell, and its names when they differ
    own_names: dict[str, set[str]] = defaultdict(set)
    for a in mine:
        n = names.canonical(a)
        if a.get("guid"):
            ids[n].append(str(a["guid"]))
        if a["name"] != n:
            own_names[n].add(a["name"])
    lines = []
    for r in rates:
        if r.median is None:
            lines.append(f"  {r.name}{_ref(r.name, ids, own_names)}: {r.mine:.1f} / not counted in the top parses' "
                         "logs")
        else:
            lines.append(f"  {r.name}{_ref(r.name, ids, own_names)}: {r.mine:.1f} / {r.median:.1f} ({r.low:.1f}-"
                         f"{r.high:.1f})" + (f"  <- {r.flag}" if r.flag else ""))
    return lines


def _gaps_line(my: Casting, theirs: list[Casting]) -> str:
    shares = sorted(100 * c.gap_share for c in theirs)
    drops = sorted(60000 * sum(len(v) for v in c.cancelled.values()) / max(c.span_ms, 1) for c in theirs)
    return (f"Casting gaps of {GAP_MS // 1000} s or more, share of the time: {100 * my.gap_share:.0f}% / "
            f"{_median(shares):.0f}% ({shares[0]:.0f}-{shares[-1]:.0f}%); cancelled casts per minute: "
            f"{60000 * sum(len(v) for v in my.cancelled.values()) / max(my.span_ms, 1):.1f} / "
            f"{_median(drops):.1f} ({drops[0]:.1f}-{drops[-1]:.1f}). A gap in a top parse is the fight's "
            "forced movement or a long channel; more than that is time to win back.")


def _ref(n: str, ids: dict[str, list[str]], own_names: dict[str, set[str]]) -> str:
    """' [382411, 359073] (cast as Incarnation: Chosen of Elune)': the player's ids of a spell, and their names for
    it when they differ from the name it's listed under."""
    ref = f" [{', '.join(ids[n])}]" if ids.get(n) else ""
    if own_names.get(n):
        ref += f" (cast as {', '.join(sorted(own_names[n]))})"
    return ref


async def _lust_casts(rep: Report, f: dict, p: Player) -> set[str]:
    """The names of the player's casts that were a Bloodlust (or its kin): a spell cast once in the pull, just
    before one landed."""
    try:
        lusts = [at for at, _, _ in await rep.lusts(f)]
    except KnowledgeError:  # unknown: a Bloodlust may read as cast far more often than the top
        return set()
    casts = await rep.casts(f, p)
    counts = Counter(c["abilityGameID"] for c in casts)
    return {rep.ability(c["abilityGameID"]) for c in casts if counts[c["abilityGameID"]] == 1
            and any(0 <= at - c["timestamp"] <= LUST_CAST_MS for at in lusts)}
