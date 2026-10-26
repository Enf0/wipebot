"""A meter: a ranked list of players by one amount, as in-game damage meters draw it (the meter view, views.py), from
the rows a tool states (breakdown by player, mechanic_hits for one ability, the damage on adds in enemies)."""
from dataclasses import dataclass, field

MIN_BARS = 3  # fewer players than this aren't a ranking: no meter


@dataclass
class Bar:
    """One player's row: their amount, a few words the tool says of it, and the deaths it set up or dealt."""
    name: str  # the character, as logged
    value: float
    note: str = ""  # "245 ticks", "overheal 23%", "parse 85th"
    died_after: int = 0  # deaths within a few seconds of a hit from it
    killed_by: int = 0  # deaths it dealt the killing blow


@dataclass
class Meter:
    """A ranked list of players by one amount over some pulls."""
    title: str  # "Damage taken", "Axegrinder", "Damage on Venomous Spawn"
    scope: str  # the pulls: "all 13 Sszorak pulls, each until its wipe was decided"
    bars: list[Bar]
    unit: str = "amount"  # "amount" (damage, healing), or what a count counts ("kicks", "dispels", "applications")
    seconds: float = 0  # combat time, for the amount per second; 0 for none
    ability: int | None = None  # the ability's id, for its icon
    note: str = ""  # a line under it, as the tool's text says it
    others: list[str] = field(default_factory=list)  # players in the pulls with no row (not hit, no kicks)
