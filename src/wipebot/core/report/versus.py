"""Two raid mates side by side (compare_with_top's `against`): output per second alive, item level, deaths, and per
spell casts per minute alive, average hit and crit share, on a pull or every pull on the boss both were in."""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from ..knowledge import KnowledgeError, spaced
from ..text import plural, short
from .base import FactError, Player, PullRef, Report
from .casting import alive, cast_rows
from .top import SpellNames

SPELLS_SHOWN = 15  # the spells listed, most output first: the rest are a share too small to matter
FAR_OFF = 0.25  # casts per minute or average hit this far apart (of the higher) is marked
MATTERS = 0.01  # ... on a spell with this share of either's output or more


@dataclass
class Spell:
    """One spell of one player over the pulls: casts per minute alive, its output, hits and crits."""
    cpm: float | None = None  # None: in the Casts table without a count (an empowered spell's other id)
    total: float = 0
    hits: int = 0
    crits: int = 0
    icon: str = ""

    @property
    def per_hit(self) -> float | None:
        return self.total / self.hits if self.hits else None

    @property
    def crit_share(self) -> float | None:
        return self.crits / self.hits if self.hits else None


@dataclass
class Side:
    p: Player
    alive_s: float = 0
    deaths: int = 0
    ilvl: float = 0
    output: float = 0
    spells: dict[str, Spell] = field(default_factory=dict)

    @property
    def per_s(self) -> float:
        return self.output / self.alive_s if self.alive_s else 0


@dataclass
class Versus:
    fights: list[dict]
    metric: str  # "damage" or "healing"
    a: Side
    b: Side
    order: list[str]  # the spells, most output of the two first

    def far_off(self, name: str) -> list[str]:
        """What of a spell is far apart between the two: "casts", "per hit"."""
        out = []
        sa, sb = self.a.spells.get(name, Spell()), self.b.spells.get(name, Spell())
        if max((s.spells[name].total / s.output if s.output and name in s.spells else 0)
               for s in (self.a, self.b)) < MATTERS:
            return out
        for what, x, y in (("casts", sa.cpm, sb.cpm), ("per hit", sa.per_hit, sb.per_hit)):
            if x is not None and y is not None and max(x, y) and abs(x - y) / max(x, y) >= FAR_OFF:
                out.append(what)
        return out


def _fights(rep: Report, a: Player, b: Player, pull: PullRef, boss: str | None, all_pulls: bool) -> list[dict]:
    if not all_pulls:
        f = rep.fight(pull, boss)
        rep.require_in(f, a)
        rep.require_in(f, b)
        return [f]
    fights = [f for f in rep.ended(rep.by_boss[rep.boss(boss)]) if {a.id, b.id} <= set(rep.pull_players(f))]
    if not fights:
        raise FactError(f"{a.name} and {b.name} were in no {rep.boss(boss)} pull together")
    return fights


async def _side(rep: Report, p: Player, fights: list[dict], metric: str) -> Side:
    ids = [f["id"] for f in fights]
    output_type = "Healing" if metric == "healing" else "DamageDone"
    casts, out, events, details = await asyncio.gather(
        rep.table(ids, "Casts", sourceID=p.id), rep.table(ids, output_type, sourceID=p.id),
        rep.events(ids, "Casts", sourceID=p.id), rep.details_many(fights))
    side = Side(p)
    by_fight: dict[int, list[dict]] = defaultdict(list)
    for c in events:
        if c["type"] == "cast":
            by_fight[c["fight"]].append(c)
    for f in fights:
        own = [d for d in await rep.deaths(f) if d["targetID"] == p.id]
        side.alive_s += alive(rep, f, p, own, by_fight[f["id"]])[0]
        side.deaths += len(own)
    levels = [float(d[p.id].get("maxItemLevel") or 0) for d in details.values() if p.id in d]
    side.ilvl = max(levels, default=0)
    try:
        names = SpellNames(await rep.spells(rep.in_pull(fights[0], p)))
    except KnowledgeError:
        names = SpellNames([])
    for n, cpm in names.per_minute(cast_rows(casts.get("entries") or []), max(side.alive_s / 60, 1 / 60)).items():
        side.spells.setdefault(n, Spell()).cpm = cpm
    for e in out.get("entries") or []:
        s = side.spells.setdefault(names.canonical(e), Spell())
        s.total += e.get("total") or 0
        s.hits += (e.get("hitCount") or 0) + (e.get("tickCount") or 0)
        s.crits += (e.get("critHitCount") or 0) + (e.get("critTickCount") or 0)
        s.icon = s.icon or (f"spells/{e['guid']}.jpg" if e.get("guid") else "")
        side.output += e.get("total") or 0
    return side


async def versus(rep: Report, player: str, other: str, pull: PullRef = None, boss: str | None = None,
                 all_pulls: bool = False) -> Versus:
    """`player` and `other` side by side on a pull, or with `all_pulls` every pull on the boss both were in;
    healing for a healer (as `player` played the first of them), else damage."""
    a, b = rep.player(player), rep.player(other)
    if a.id == b.id:
        raise FactError("give two different players")
    fights = _fights(rep, a, b, pull, boss, all_pulls)
    await rep.decided_cuts(fights)  # every pull's deaths in a query
    metric = "healing" if rep.in_pull(fights[0], a).role == "healer" else "damage"
    sa, sb = await asyncio.gather(_side(rep, rep.in_pull(fights[0], a), fights, metric),
                                  _side(rep, rep.in_pull(fights[0], b), fights, metric))
    def weight(n: str) -> float:
        return max((s.spells[n].total / s.output if s.output and n in s.spells else 0) for s in (sa, sb))
    names = {n for s in (sa, sb) for n, sp in s.spells.items() if sp.total or sp.cpm}
    order = sorted(names, key=lambda n: (-weight(n), -max((s.spells.get(n) or Spell()).cpm or 0 for s in (sa, sb)),
                                         n))
    return Versus(fights, metric, sa, sb, order[:SPELLS_SHOWN])


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _amount(x: float | None) -> str:
    return short(x) if x else "-"


def _num(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def versus_text(rep: Report, v: Versus) -> str:
    a, b = v.a, v.b
    where = (rep.label(v.fights[0]) if len(v.fights) == 1
             else f"{v.fights[0]['name']}, {plural(len(v.fights), 'pull')} both were in")
    def who(s: Side) -> str:
        return f"{s.p.name} ({spaced(s.p.spec)} {spaced(s.p.class_name)})"
    lines = [f"{who(a)} against {who(b)} on {where}, {a.p.name} / {b.p.name}:",
             f"{v.metric.capitalize()} per second alive: {short(a.per_s)} / {short(b.per_s)}"
             + (f" ({100 * (a.per_s / b.per_s - 1):+.0f}%)" if b.per_s else ""),
             f"Alive: {a.alive_s:.0f} s / {b.alive_s:.0f} s; deaths: {a.deaths} / {b.deaths}; item level: "
             f"{a.ilvl:.0f} / {b.ilvl:.0f}",
             f"Per spell, most {v.metric} first: casts per minute alive; average hit (ticks too); crit share; share of "
             f"their {v.metric}:"]
    for n in v.order:
        sa, sb = a.spells.get(n, Spell()), b.spells.get(n, Spell())
        def share(s: Side, sp: Spell) -> str:
            return ("<1%" if 0 < sp.total / s.output < 0.005 else _pct(sp.total / s.output)) if s.output else "-"
        off = v.far_off(n)
        lines.append(f"  {n}: {_num(sa.cpm)} / {_num(sb.cpm)} per min; "
                     f"{_amount(sa.per_hit)} / {_amount(sb.per_hit)} per hit; "
                     f"crit {_pct(sa.crit_share)} / {_pct(sb.crit_share)}; {share(a, sa)} / {share(b, sb)}"
                     + (f"  <- {' and '.join(off)} far apart" if off else ""))
    lines.append("-: none (not cast, or no hits). A cooldown of a minute or more is cast as often as the pulls are "
                 "long; the average hit moves with item level, crit, buffs lined up with it and how many targets it "
                 "hit.")
    return "\n".join(lines)
