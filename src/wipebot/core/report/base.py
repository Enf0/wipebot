"""One Warcraft Logs report: its pulls, players and abilities (its WCL answers remembered: fetch.py), and the facts
several tools share (deaths, the boss's buffs, when a wipe was decided, a player's casts and cooldowns).

The log tools themselves are functions over a Report, one module per subject (a night, one pull, deaths, adds, tanks,
one player, against the top, history, ...). Each returns plain text. Pulls are named the way raiders name them: by
boss and pull number on that boss ("Sszorak pull 13"), not by WCL fight id. Ability ids are given in [brackets] so
knowledge tools can look the spell up.
"""
import asyncio
import difflib
import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .. import knowledge
from ..cache import DAY, FOREVER
from ..knowledge import KnowledgeError, spaced
from ..pulls import (
    DIFFICULTIES,
    NO_KILLING_BLOW,
    Decided,
    GuildBest,
    end_buffs,
    first_by_name,
    format_summary,
    lost_buffs,
    summarize,
)
from ..text import clock, fight_url, name_key, plural, ranges
from ..wcl import WCL, WCLError, boss_ids
from .fetch import Fetched

SAME_DEATH_MS = 1_000  # a player's deaths this close together are one death listed twice
# One hit's deaths are logged 0-40 ms apart, a fall's or a knockback's up to a few hundred: deaths this close one after
# another, from the one that decided a wipe and within DEATH_BURST_MS of it, are one moment and count with it. A
# cascade of separate deaths seconds long isn't.
DEATH_GAP_MS = 250
DEATH_BURST_MS = 1_000
PARTNER_DEATH_MS = 2000  # a buff gained this soon after another boss died came from that death
PARTNER_SLACK_MS = 500  # or this soon before it: the buff and the death are logged in either order
REPORT_QUIET_S = 20 * 60  # a report with no new data for this long is no longer being logged
# A report's pull list is read anew on every load, but kept on disk: when Warcraft Logs doesn't answer, the last copy
# loads the report, and what's on disk about its finished pulls answers.
PULLS_FRESH_S = 1
# Warcraft Logs' advice: a report without a pull in the last couple of hours almost never gets new ones, so its
# actors, abilities and players are kept on disk for good (docs/reference/wcl-api.md)
SETTLED_S = 2 * 3600
LUST_GROUP_MS = 10_000  # Sated & co. landing this close together are one Bloodlust (players come in range late)
# A guild's raid nights have most of their players in the guild (86-100% of one guild's); a raider's
# pug or another group logged to the guild's page has few (2-48%).
GUILD_RUN_SHARE = 2 / 3
GUILD_BEST_WEEKS = 16  # "new best" counts the guild's logs this far back: a raid tier's progression
ROLES = {"tanks": "tank", "healers": "healer"}  # WCL's playerDetails keys; the rest ("dps") are damage dealers
ROLE_NAMES = {"tank": "tanks", "healer": "healers", "dps": "damage dealers"}
REPEAT_CAST_MS = 10_000  # the same player's same cast again this soon is one use (a channel logs each tick)


def dead_since(deaths: list[dict], backs: list[float], who: int, at: float) -> float | None:
    """When a player died and stayed dead up to `at` (their last death before it, with no battle res after it in
    `backs`), or None for one alive then."""
    died = max((d["timestamp"] for d in deaths if d["targetID"] == who and d["timestamp"] <= at), default=None)
    return died if died is not None and not any(died < b <= at for b in backs) else None


def alive_ms(deaths: list[dict], backs: list[float], who: int, start: float, end: float) -> float:
    """How long a player was alive from `start` to `end` (report ms): dead from each death until a battle res in
    `backs` brought them back."""
    dead, down = 0.0, None
    for at, back in sorted([(d["timestamp"], False) for d in deaths if d["targetID"] == who]
                           + [(b, True) for b in backs]):
        at = min(max(at, start), end)
        if not back and down is None:
            down = at
        elif back and down is not None:
            dead, down = dead + at - down, None
    return end - start - dead - (end - down if down is not None else 0)


def alive_after(deaths: list[dict], backs: dict[int, list[float]], raid: int) -> list[int]:
    """Players alive right after each death, in order: a battle res (`backs`, per player) brings one back."""
    dead: dict[int, float] = {}
    out = []
    for d in deaths:
        dead = {who: at for who, at in dead.items() if not any(at < b <= d["timestamp"] for b in backs.get(who, []))}
        dead[d["targetID"]] = d["timestamp"]
        out.append(raid - len(dead))
    return out


def alive_at(deaths: list[dict], backs: dict[int, list[float]], raid: int, at: float) -> int:
    """Players alive at a moment (report ms), a battle res counted."""
    return raid - sum(dead_since(deaths, backs.get(who, []), who, at) is not None
                      for who in {d["targetID"] for d in deaths})


def half_dead(deaths: list[dict], backs: dict[int, list[float]], raid: int) -> float | None:
    """The report time half the raid was dead (⌈raid/2⌉ players at once, battle res counted), or None."""
    return next((d["timestamp"] for d, alive in zip(deaths, alive_after(deaths, backs, raid), strict=True)
                 if raid - alive >= math.ceil(raid / 2)), None)


def unavailable(what: str, e: Exception) -> str:
    """The line for a part of an answer whose source couldn't answer, so it never reads as none."""
    return f"{what}: unavailable ({str(e).rstrip('.')})."


# After a tool's facts about who was hit, carried a debuff or had a kick ready: what the log can't show.
JOBS_NOT_LOGGED = ("Whose job it was (a soak, a kick, a spot) isn't in the log: the raid note holds the jobs, and who "
                   "did it on other pulls isn't an assignment (raids swap spots and keep reserves).")


class FactError(ValueError):
    """A bad tool argument (unknown player, pull out of range); the message is shown to the model."""


class PullNotLogged(FactError):
    """The pull after the last one of a report that's still being logged: it has ended in the game but isn't
    uploaded yet. Worth waiting a little for."""

    def __init__(self, message: str, report: Report):
        super().__init__(message)
        self.report = report


@dataclass
class Player:
    id: int
    name: str
    class_name: str  # WCL's form: "DeathKnight"
    spec: str
    role: str  # tank, healer, dps
    server: str = ""
    region: str = ""
    also: tuple[str, ...] = ()  # other specs played in the report; none for a player as they were in one pull

    @property
    def label(self) -> str:
        also = f"; also {', '.join(spaced(s) for s in self.also)} in some pulls" if self.also else ""
        return f"{self.name} ({spaced(self.spec)} {spaced(self.class_name)}{also})"


CLOSE_NAME = 0.8  # difflib ratio for a typo to still match a name


def match_player(name: str, players: Iterable[Player]) -> Player | None:
    """The one player a typed name means: the same name, else the same without accents (Mírëlle is hard to type),
    else the one name it starts (a raid note's "Torv"), else the one name close to it (a typo: "torvk" for Torvek).
    None for none or several."""
    players = list(players)
    typed = name_key(name)
    if not typed:
        return None
    for found in ([p for p in players if p.name.lower() == name.strip().lower()],
                  [p for p in players if name_key(p.name) == typed],
                  [p for p in players if name_key(p.name).startswith(typed)]):
        if len(found) == 1:
            return found[0]
        if found:
            return None
    close = difflib.get_close_matches(typed, [name_key(p.name) for p in players], n=2, cutoff=CLOSE_NAME)
    return next(p for p in players if name_key(p.name) == close[0]) if len(close) == 1 else None


class Report(Fetched):
    def __init__(self, wcl: WCL, code: str, settled: bool = False):
        super().__init__(wcl, code)
        # A past raid night that won't change any more: what loading it asks is kept on disk for good
        self._cache_for = FOREVER if settled else None
        self._settled = settled
        # Per finished pull (a pull still going changes): player deaths, the bosses' applybuff/removebuff
        # events, player details.
        self._deaths: dict[int, list[dict]] = {}
        self._buffs: dict[int, list[dict]] = {}
        self._details: dict[int, dict] = {}
        self._backs: dict[int, dict[int, list[float]]] = {}  # battle res and self-res
        self._self_res: dict[int, list[dict]] = {}  # the casts a player came back by themselves with (any pull)
        self._refreshing = asyncio.Lock()

    async def load(self) -> None:
        """Read the report: its pulls, players and abilities."""
        report = await self._fetch()
        self._update(report, await self._fetch_names(report))

    async def refresh(self, max_age_s: float = 0) -> None:
        """Read a report that's still being logged again, unless it was read in the last `max_age_s` (by a call
        that was refreshing it meanwhile). Players and abilities are read again only when a pull ended (not when
        one starts: whoever joined for it is read at its end), and what's remembered about finished pulls stays."""
        async with self._refreshing:
            if time.time() - self.loaded_at < max_age_s:
                return
            report = await self._fetch()
            ended = {f["id"] for f in report["fights"] if not f.get("inProgress")}
            changed = ended != {f["id"] for f in self.fights if not f.get("inProgress")}
            self._update(report, await self._fetch_names(report) if changed else self._names)

    async def _fetch(self) -> dict:
        try:
            report = await self.wcl.report_fights(self.code, self._cache_for or PULLS_FRESH_S)
        except WCLError as e:
            raise FactError(str(e)) from e
        if not report["fights"]:
            raise FactError(f"report {self.code} has no boss pulls")
        return report

    async def _fetch_names(self, report: dict) -> tuple[dict, dict, dict]:
        """(actors, ability table, player details for the whole report); kept on disk for a report that's settled
        (SETTLED_S), as its pull list shows."""
        settled = time.time() * 1000 - report["endTime"] > SETTLED_S * 1000
        cache_for = self._cache_for or (FOREVER if settled else None)
        actors, table, details = await asyncio.gather(
            self.wcl.actors(self.code, cache_for), self.wcl.ability_table(self.code, cache_for),
            self.wcl.player_details(self.code, 0, report["endTime"] - report["startTime"], cache_for=cache_for))
        return actors, table, details

    def _update(self, report: dict, names: tuple[dict, dict, dict]) -> None:
        """Take a fresh read of the report. Every attribute is set without awaiting in between, so a tool running
        meanwhile never sees half of one read and half of another."""
        actors, table, details = self._names = names
        self.phase_names = {(enc["encounterID"], ph["id"]): ph["name"]
                            for enc in report.get("phases") or [] for ph in enc.get("phases") or []}
        self.title = report["title"]
        self.guild_name = (report.get("guild") or {}).get("name") or ""  # empty for personal logs
        self.guild_id = (report.get("guild") or {}).get("id")
        self.start_ms, self.end_ms = report["startTime"], report["endTime"]
        self.zone = (report.get("zone") or {}).get("name") or ""
        self.zone_id = (report.get("zone") or {}).get("id")
        self.fights = sorted(report["fights"], key=lambda f: f["startTime"])
        self.live = {f["id"] for f in self.fights if f.get("inProgress")}
        self.actors = actors
        self.bosses = boss_ids(actors)
        self.abilities = {i: name for i, (name, _) in table.items()}
        self.names = {i: a["name"] for i, a in actors.items() if a["type"] == "Player"}
        # A player who changed role during the night is listed once per role, and the order of the roles changes
        # from report to report: the spec played most is their spec for the report, the others are `also`. Tools
        # about one pull use the spec of that pull (`pull_players`).
        played: dict[str, list[tuple[int, str, str, dict]]] = defaultdict(list)
        for role, members in details.items():
            for m in members:
                for s in m["specs"]:
                    played[m["name"].lower()].append((s["count"], s["spec"], ROLES.get(role, "dps"), m))
        self.players: dict[str, Player] = {}
        # The role of each spec a player played: Warcraft Logs lists the player under it.
        self.spec_roles = {(m["id"], spec): role for specs in played.values() for _, spec, role, m in specs}
        for key, specs in played.items():
            _, spec, role, m = max(specs, key=lambda x: x[:2])
            also = tuple(dict.fromkeys(s for _, s, _, _ in specs if s != spec))
            self.players[key] = Player(m["id"], m["name"], m["type"], spec, role, m.get("server") or "",
                                       m.get("region") or "", also)
        # A boss pulled on two difficulties in one report counts its pulls per difficulty, as the posts do.
        difficulties: dict[int, set] = defaultdict(set)
        for f in self.fights:
            difficulties[f["encounterID"]].add(f.get("difficulty"))
        self._mixed = {enc for enc, ds in difficulties.items() if len(ds) > 1}
        self.by_boss: dict[str, list[dict]] = defaultdict(list)
        for f in self.fights:
            self.by_boss[self.boss_key(f)].append(f)
        # A report that's still being logged grows; one that's been quiet for a while is final.
        self.loaded_at = time.time()
        quiet_ms = self.loaded_at * 1000 - self.end_ms
        self.final = quiet_ms > REPORT_QUIET_S * 1000 and not self.live
        # Final for now, but a raid's break between bosses is often longer than REPORT_QUIET_S: until SETTLED_S
        # it's still read again, so the next boss's pulls show up.
        self.settled = self._settled or (quiet_ms > SETTLED_S * 1000 and not self.live)

    def boss_key(self, fight: dict) -> str:
        """How the tools name a fight's boss: 'Sszorak', or 'Sszorak (Heroic)' when the report has the boss on
        more than one difficulty."""
        if fight["encounterID"] in self._mixed:
            return f"{fight['name']} ({DIFFICULTIES.get(fight.get('difficulty'), fight.get('difficulty'))})"
        return fight["name"]

    def boss(self, name: str | None) -> str:
        """The boss matching a name or part of it (knowledge.matches_boss); of several, the one pulled last."""
        if not name:
            return self.boss_key(self.fights[-1])
        matches = [b for b in self.by_boss if knowledge.matches_boss(name, b)]
        if not matches:
            raise FactError(f"no boss matching {name!r}; bosses in this report: {', '.join(self.by_boss)}")
        return max(matches, key=lambda b: self.by_boss[b][-1]["startTime"])

    def pull_no(self, fight: dict) -> int:
        # By id: a refresh replaces the fight dicts while a tool may still hold an old one.
        return [f["id"] for f in self.by_boss[self.boss_key(fight)]].index(fight["id"]) + 1

    def fight(self, pull: int | None = None, boss: str | None = None) -> dict:
        """The fight for 'pull N on boss'. No pull means the latest finished pull on that boss (one still being
        logged only when it's the boss's first)."""
        pulls = self.by_boss[self.boss(boss)]
        if pull is None:
            return next((f for f in reversed(pulls) if f["id"] not in self.live), pulls[-1])
        if pull == len(pulls) + 1 and not self.final:
            raise PullNotLogged(f"{pulls[0]['name']} pull {pull} isn't in the log yet (pulls 1-{len(pulls)} so far): "
                                "the live log is still uploading it", self)
        if not 1 <= pull <= len(pulls):
            if not boss and len(self.by_boss) > 1:
                # No boss named and the one pulled last doesn't have that pull: the one boss that does, or ask.
                having = [b for b, fs in self.by_boss.items() if 1 <= pull <= len(fs)]
                if len(having) == 1:
                    return self.by_boss[having[0]][pull - 1]
                counts = ", ".join(f"{b} {len(fs)}" for b, fs in self.by_boss.items())
                if having:
                    raise FactError(f"pull {pull} of which boss? Several have one (pulls in this report: {counts}); "
                                    "ask, or name the boss")
                raise FactError(f"no boss has a pull {pull} in this report (pulls: {counts})")
            raise FactError(f"{pulls[0]['name']} has pulls 1-{len(pulls)} in this report")
        return pulls[pull - 1]

    def label(self, fight: dict) -> str:
        return f"{self.boss_key(fight)} pull {self.pull_no(fight)}"

    def player(self, name: str) -> Player:
        """The player a tool was given (see find_player); none or several is an error."""
        if p := self.find_player(name):
            return p
        raise FactError(f"no single player matching {name!r} in this report (find_reports with a player finds "
                         f"the reports a character is in)")

    def find_player(self, name: str, among: Iterable[Player] | None = None) -> Player | None:
        """The one player of the report's, or of `among` (a pull's), a typed name means (match_player)."""
        return match_player(name, self.players.values() if among is None else among)

    def ability_ids(self, name: str) -> list[int]:
        """The ids of the ability a tool was given, lowest first: a number is that id; a name, every id of that name
        in the report (an ability often logs under several), case and accents optional. None of that name is an
        error naming the closest."""
        if name.strip().isdigit():
            return [int(name)]
        if ids := sorted(self.ids_named([name])):
            return ids
        names = sorted(set(self.abilities.values()), key=str.lower)
        close = difflib.get_close_matches(name.strip(), names, n=3, cutoff=0.6)
        close += [n for n in names if name_key(name) in name_key(n) and n not in close][:3]
        raise FactError(f"nothing named {name.strip()!r} was cast, taken or applied in this report (not talented, or "
                        "named differently)" + (f"; closest names: {', '.join(close)}" if close else ""))

    def ids_named(self, names: Iterable[str]) -> set[int]:
        """The ids of every ability with one of these names, case and accents optional."""
        wanted = {name_key(n) for n in names}
        return {i for i, n in self.abilities.items() if name_key(n) in wanted}

    def spell_ids(self, spells: Iterable[dict]) -> set[int]:
        """Every id a cast of these Lorrgs spells can log under (is_spell): theirs, their variations', and the
        report's ids of their names."""
        spells = list(spells)
        return ({i for sp in spells for i in [sp["spell_id"], *(sp.get("variations") or [])]}
                | self.ids_named(sp["name"] for sp in spells))

    def is_spell(self, cast: dict, spell: dict) -> bool:
        """Whether a cast is this Lorrgs spell: its id, its name (ids can differ per rank), or one of the ids Lorrgs
        lists as its variations (a replacing talent: Incarnation: Chosen of Elune for Celestial Alignment, Crusade
        for Avenging Wrath)."""
        gid = cast["abilityGameID"]
        return (gid == spell["spell_id"] or gid in (spell.get("variations") or [])
                or name_key(self.ability(gid)) == name_key(spell["name"]))

    def tank_tag(self, name: str) -> str:
        """' (tank)' after a tank's name in a list of players hit by mechanics: tanks stand at the boss and take
        what's aimed at them, so they top those lists by design."""
        p = self.players.get(name.lower())
        return " (tank)" if p and p.role == "tank" else ""

    def was_in(self, fight: dict, p: Player) -> bool:
        """Whether a player was in a pull. A report lists everyone who joined at some point in the night, so a
        player who left after the earlier bosses would otherwise read as 'no healing, no deaths, no casts'."""
        return p.id in (fight.get("friendlyPlayers") or [p.id])

    def require_in(self, fight: dict, p: Player) -> None:
        if not self.was_in(fight, p):
            raise FactError(f"{p.name} was not in {self.label(fight)}; {self.pulls_in(p)}")

    def ended(self, fights: list[dict]) -> list[dict]:
        """A boss's pulls for an answer over the night: those still being logged left out, as what they logged so
        far isn't a pull's (`still_going` says so). None ended yet is an error."""
        kept = [f for f in fights if f["id"] not in self.live]
        if fights and not kept:
            raise FactError(f"the only {self.boss_key(fights[0])} pull is still going: ask again once it has ended")
        return kept

    def still_going(self, fights: list[dict]) -> str:
        """What an answer over the night opens with when `ended` left pulls out ('' when none)."""
        going = [str(self.pull_no(f)) for f in fights if f["id"] in self.live]
        if not going:
            return ""
        return (f"Pull {going[0]} is still going and left out until it ends.\n" if len(going) == 1
                else f"Pulls {', '.join(going)} are still going and left out until they end.\n")

    def pulls_with(self, p: Player, fights: list[dict]) -> list[dict]:
        """The pulls a player was in; none at all is an error, not a night of zeros."""
        kept = [f for f in fights if self.was_in(f, p)]
        if not kept:
            raise FactError(f"{p.name} was not in any pull on {self.boss_key(fights[0])} in this report; "
                            + self.pulls_in(p))
        return kept

    def pulls_in(self, p: Player) -> str:
        """Where a player was, for an error about where they weren't: "in the report: Sszorak pulls 1-14, 16"."""
        parts = []
        for boss, fights in self.by_boss.items():
            nos = [n for n, f in enumerate(fights, 1) if self.was_in(f, p)]
            if nos == list(range(1, len(fights) + 1)):
                parts.append(f"{boss} (all {len(nos)} pulls)" if len(nos) > 1 else f"{boss} (its one pull)")
            elif nos:
                parts.append(f"{boss} pulls {ranges(nos)}")
        return ("in the report: " + "; ".join(parts)) if parts else "not in any pull of the report"

    def ability(self, game_id: int | None) -> str:
        if not game_id:
            return NO_KILLING_BLOW
        return self.abilities.get(game_id, f"spell {game_id}")

    def url(self, fight: dict) -> str:
        """The pull on Warcraft Logs."""
        return fight_url(self.code, fight["id"])

    async def name_spells(self, ids: set[int]) -> None:
        """Name spells the report hasn't seen yet (a raid note's cooldown nobody has cast yet tonight) from Wowhead,
        so they read as names, not "spell 196718". One Wowhead can't name now stays a number."""
        for i in ids - set(self.abilities):
            try:
                if name := await knowledge.spell_name(i):
                    self.abilities[i] = name
            except KnowledgeError:
                continue

    def ability_ref(self, game_id: int | None) -> str:
        """'Mutilated Gash [1285998]': the name plus the id knowledge tools can look up."""
        return f"{self.ability(game_id)} [{game_id}]" if game_id else NO_KILLING_BLOW

    def ability_refs(self, name: str, ids: Iterable[int]) -> str:
        """'Virulence [1300089, 1312189]': an ability logged under several ids, named once."""
        ids = sorted(i for i in ids if i)
        return f"{name} [{', '.join(map(str, ids))}]" if ids else name

    def actor(self, actor_id: int) -> str:
        if actor_id == -1:
            return "Environment"
        return self.actors.get(actor_id, {}).get("name", f"#{actor_id}")

    def _player_deaths(self, events: list[dict]) -> list[dict]:
        """Player deaths in order, each once (WCL sometimes lists a death twice, milliseconds apart)."""
        last: dict[int, float] = {}
        out = []
        for e in sorted((e for e in events if e["targetID"] in self.names), key=lambda e: e["timestamp"]):
            if e["timestamp"] - last.get(e["targetID"], -math.inf) > SAME_DEATH_MS:
                out.append(e)
            last[e["targetID"]] = e["timestamp"]
        return out

    async def deaths(self, fight: dict) -> list[dict]:
        """Player deaths in a fight, in order."""
        if fight["id"] in self._deaths:
            return self._deaths[fight["id"]]
        finished = self._finished([fight["id"]])  # before the fetch: a pull ending meanwhile was read half done
        deaths = self._player_deaths(await self.events(fight["id"], "Deaths"))
        if finished:
            self._deaths[fight["id"]] = deaths
        return deaths

    async def all_deaths(self, fights: list[dict]) -> None:
        """Deaths of several pulls, fetched in one query."""
        missing = [f["id"] for f in fights if f["id"] not in self._deaths]
        if len(missing) > 1:
            finished = [i for i in missing if self._finished([i])]
            events = await self.events(missing, "Deaths")
            if all("fight" in e for e in events):
                by_fight: dict[int, list[dict]] = defaultdict(list)
                for e in events:
                    by_fight[e["fight"]].append(e)
                for i in finished:
                    self._deaths[i] = self._player_deaths(by_fight[i])
        await asyncio.gather(*(self.deaths(f) for f in fights))

    async def resurrects(self, fights: list[dict]) -> dict[int, dict[int, list[float]]]:
        """Per pull, when each player was brought back, in order: by a battle res (Warcraft Logs' resurrect events)
        or by themselves (`self_res`: a Reincarnation is only a cast). The missing pulls' in a query or two."""
        out = {f["id"]: self._backs[f["id"]] for f in fights if f["id"] in self._backs}
        if missing := [f for f in fights if f["id"] not in out]:
            ids = [f["id"] for f in missing]
            finished = {i for i in ids if self._finished([i])}  # before the fetch, as in `keep`
            found: dict[int, dict[int, list[float]]] = {i: defaultdict(list) for i in ids}
            for e in await self.events(ids, "All", filterExpression='type = "resurrect"'):
                if (back := found.get(e.get("fight", ids[0]))) is not None:
                    back[e.get("targetID", -1)].append(e["timestamp"])
            came = await self._came_back(missing, found)
            for i in ids:
                self._self_res[i] = came.get(i, [])
                for e in self._self_res[i]:
                    found[i][e["sourceID"]].append(e["timestamp"])
                # WCL sometimes lists a battle res twice, a millisecond apart: one player brought back once
                for at in found[i].values():
                    at.sort()
                    at[:] = [t for n, t in enumerate(at) if n == 0 or t - at[n - 1] > SAME_DEATH_MS]
                out[i] = found[i]
                if i in finished:
                    self._backs[i] = found[i]
        return out

    async def self_res(self, fight: dict) -> list[dict]:
        """The casts players came back to life by themselves with in a pull, in order (`_came_back`)."""
        await self.resurrects([fight])
        return sorted(self._self_res.get(fight["id"], []), key=lambda e: e["timestamp"])

    async def _came_back(self, fights: list[dict], backs: dict[int, dict[int, list[float]]]
                         ) -> dict[int, list[dict]]:
        """Per pull, the players who came back by themselves (a shaman's Reincarnation, logged only as a cast): each
        one's first cast after a death that no battle res in `backs` answered, before their next death; a dead
        player casts nothing else (a fake cast, which Warcraft Logs derives, aside). The pulls in one query."""
        await self.all_deaths(fights)
        items, asked = [], []
        for f in fights:
            deaths, t0 = await self.deaths(f), f["startTime"]
            terms = []
            for n, d in enumerate(deaths):
                who, at = d["targetID"], d["timestamp"]
                again = next((e["timestamp"] for e in deaths[n + 1:] if e["targetID"] == who), None)
                if any(at < b <= (again or math.inf) for b in backs.get(f["id"], {}).get(who, [])):
                    continue  # a battle res brought them back
                # a filter's timestamp counts from the pull (verified live; docs/reference/wcl-api.md)
                until = f" and timestamp < {again - t0:.0f}" if again is not None else ""
                name = json.dumps(self.names[who], ensure_ascii=False)  # source.id matches nothing
                terms.append(f"(source.name = {name} and timestamp > {at - t0:.0f}{until})")
                asked.append((f["id"], who, at, again))
            if terms:
                items.append((f["id"], {"filterExpression": f'type = "cast" and ({" or ".join(terms)})'}))
        got = dict(zip([i for i, _ in items], await self.events_for(items, "Casts"), strict=True)) if items else {}
        out: dict[int, list[dict]] = defaultdict(list)
        for fid, who, at, again in asked:
            cast = [e for e in got[fid] if e.get("sourceID") == who and not e.get("fake")
                    and at < e["timestamp"] < (again or math.inf)]
            if cast:
                out[fid].append(min(cast, key=lambda e: e["timestamp"]))
        return out

    async def boss_buff_events(self, fights: list[dict]) -> dict[int, list[dict]]:
        """Per pull, the bosses' buffs gained and lost (applybuff and removebuff), the missing pulls fetched in one
        query."""
        out = {f["id"]: self._buffs[f["id"]] for f in fights if f["id"] in self._buffs}
        missing = [f["id"] for f in fights if f["id"] not in out]
        if missing:
            finished = {i for i in missing if self._finished([i])}
            by_fight: dict[int, list[dict]] = defaultdict(list)
            for e in await self.events(missing, "Buffs", hostilityType="Enemies"):
                if e.get("targetID") in self.bosses and e["type"] in ("applybuff", "removebuff"):
                    by_fight[e.get("fight", missing[0])].append(e)
            for i in missing:
                out[i] = by_fight[i]
                if i in finished:
                    self._buffs[i] = by_fight[i]
        return out

    async def boss_buffs(self, fight: dict) -> list[dict]:
        return (await self.boss_buff_events([fight]))[fight["id"]]

    async def routine_buffs(self, fight: dict) -> set[int]:
        """Buffs the boss lost during the pulls before this one: routine, not an enrage."""
        earlier = [g for g in self.by_boss[self.boss_key(fight)] if g["startTime"] < fight["startTime"]]
        buffs = await self.boss_buff_events(earlier)
        return {a for g in earlier for a in lost_buffs(g, buffs[g["id"]])}

    async def end_buffs(self, fight: dict) -> list[tuple[str, float]]:
        """(name, ms from pull start) of the buffs the boss gained and kept to the end of a wipe, e.g. an enrage. A
        buff several bosses gain (each Entombed Sentinel) is one, from the first to gain it. None for a pull still
        going: its end isn't the end."""
        if fight["kill"] or fight.get("inProgress"):
            return []
        kept = end_buffs(fight, await self.boss_buffs(fight), await self.routine_buffs(fight))
        return first_by_name((self.ability(a), at) for a, at in kept)

    async def enrages(self, fight: dict) -> list[tuple[str, float]]:
        """The end_buffs that can decide a wipe: not one a council boss gains as its partner dies, which killing
        that boss brings."""
        return [(name, at) for name, at in await self.end_buffs(fight) if not await self._partner_died(fight, at)]

    async def decided_at(self, fight: dict) -> Decided | None:
        """When a wipe was decided, and by what: the boss gaining a buff it kept to the end (an
        enrage), or half the raid dead (a battle res brings one back), whichever came first, moved to the last death
        of the moment it falls in (deaths DEATH_GAP_MS apart, within DEATH_BURST_MS): one hit's deaths are never
        split into counted and fallout. Deaths up to the moment count; later ones are fallout. None for a kill or a
        wipe with neither."""
        if fight["kill"]:
            return None
        found = [(at, name) for name, at in await self.enrages(fight)]
        deaths, raid = await self.deaths(fight), await self.raid_size(fight)
        backs = (await self.resurrects([fight]))[fight["id"]]
        if raid and (half := half_dead(deaths, backs, raid)) is not None:
            found.append((half - fight["startTime"], ""))
        if not found:
            return None
        at, enrage = min(found)
        end = fight["startTime"] + at
        if any(end - DEATH_GAP_MS <= d["timestamp"] <= end for d in deaths):
            cap = end + DEATH_BURST_MS
            for d in deaths:
                if end < d["timestamp"] <= min(end + DEATH_GAP_MS, cap):
                    end = d["timestamp"]
        return Decided(end - fight["startTime"], enrage)

    async def _partner_died(self, fight: dict, gained: float) -> bool:
        """Whether a buff gained at `gained` (ms from the pull) came as another boss of a council died: what
        killing that boss brings, not what decided the wipe."""
        if not self._council(fight):
            return False
        return any(gained - PARTNER_DEATH_MS <= e["timestamp"] - fight["startTime"] <= gained + PARTNER_SLACK_MS
                   for e in await self.events(fight["id"], "Deaths", hostilityType="Enemies")
                   if e.get("targetID") in self.bosses)

    def _council(self, fight: dict) -> bool:
        """Whether a pull is on several bosses at once, each one of its kind (as adds.boss_units)."""
        return sum(n["id"] in self.bosses and (n.get("instanceCount") or 1) == 1
                   for n in fight.get("enemyNPCs") or []) >= 2

    async def decided_cuts(self, fights: list[dict]) -> dict[int, float]:
        """Per pull, the report time its wipe was decided (decided_at; pulls without one left out). Every pull's
        deaths, boss buffs and battle res are read in one query each, and the bosses' deaths of the council pulls
        whose boss kept a buff."""
        await self.all_deaths(fights)
        await self.boss_buff_events(fights)
        await self.resurrects(fights)
        if council := [f for f in fights if self._council(f) and await self.end_buffs(f)]:
            await self.events_for([(f["id"], {}) for f in council], "Deaths", hostilityType="Enemies")
        return {f["id"]: f["startTime"] + d.at_ms for f in fights if (d := await self.decided_at(f))}

    async def raid_size(self, fight: dict) -> int:
        """Players in a pull."""
        return len(fight.get("friendlyPlayers") or []) or len(await self.details(fight))

    async def until_decided(self, fight: dict, whole_pull: bool) -> tuple[dict, str]:
        """WCL filters that stop at the moment a wipe was decided, and a note saying so ({} and '' if none)."""
        decided = None if whole_pull else await self.decided_at(fight)
        if not decided:
            return {}, ""
        return ({"endTime": fight["startTime"] + decided.at_ms},
                f" until {clock(decided.at_ms)}, when the wipe was decided ({decided.why}; whole_pull for the rest)")

    async def details(self, fight: dict) -> dict[int, dict]:
        """A pull's player details by player id (item level, combatant info: talents, gear)."""
        return (await self.details_many([fight]))[fight["id"]]

    async def details_many(self, fights: list[dict]) -> dict[int, dict[int, dict]]:
        """`details` of several pulls, by fight id, the ones not remembered fetched in one query."""
        wanted = {f["id"]: (("details", f["id"]), f["id"]) for f in fights if f["id"] not in self._details}
        by_id = {f["id"]: f for f in fights}
        finished = {i for i in wanted if self._finished([i])}
        fetched = await self.keep_many(wanted, lambda ids: self.wcl.player_details_many(
            self.code, [by_id[i] for i in ids], combatant=True), FOREVER)
        out = {}
        for f in fights:
            if f["id"] in self._details:
                out[f["id"]] = self._details[f["id"]]
                continue
            details = {m["id"]: {**m, "role": role} for role, members in fetched[f["id"]].items() for m in members}
            if f["id"] in finished:
                self._details[f["id"]] = details
            out[f["id"]] = details
        return out

    def pull_players(self, fight: dict) -> dict[int, Player]:
        """The players in a pull as they played it, by id: spec (the fight's friendlySpecs) and its role, since a
        player can change spec between pulls."""
        players = {p.id: p for p in self.players.values()}
        ids = fight.get("friendlyPlayers") or []
        specs = fight.get("friendlySpecs") or [None] * len(ids)
        out = {}
        for pid, spec in zip(ids, specs, strict=False):
            if p := players.get(pid):
                spec = spec or p.spec
                out[pid] = replace(p, spec=spec, role=self.spec_roles.get((pid, spec), p.role), also=())
        return out

    def in_pull(self, fight: dict, p: Player) -> Player:
        """A player as they played a pull (see pull_players)."""
        return self.pull_players(fight).get(p.id, p)

    async def guild_share(self) -> tuple[int, int] | None:
        """(players who are in the report's guild, players), or None for a personal log or when Warcraft Logs
        doesn't say."""
        if not (self.guild_id and self.players):
            return None
        try:
            members = await self.wcl.guild_members(self.guild_id)
        except WCLError:
            return None
        if not members:
            return None
        return sum(p.name in members for p in self.players.values()), len(self.players)

    async def raids(self) -> list[tuple[str, list[str]]]:
        """The report's bosses (as `by_boss` names them) by the raid each is in, in that raid's boss order, the
        raids in the order first pulled: one log can hold several raids, as a single-boss raid next to the tier's,
        which Warcraft Logs files under the tier. Empty when the raids can't be read; a boss in none goes under
        the report's own raid."""
        try:
            listed = await knowledge.raids()
        except KnowledgeError:
            return []
        raids: dict[str, list[tuple[int, str]]] = {}
        for boss, fights in self.by_boss.items():
            raid, place = next(((raid, i) for raid, bosses in listed for i, b in enumerate(bosses)
                                if knowledge.names_boss(b, fights[0]["name"])), (self.zone, len(fights) + 99))
            raids.setdefault(raid, []).append((place, boss))
        return [(raid, [b for _, b in sorted(bosses)]) for raid, bosses in raids.items()]

    async def guild_run_doubt(self) -> str:
        """'' for what looks like the guild's own run; else why it may not be: a raider's pug or another group
        logged to the guild's page."""
        share = await self.guild_share()
        if share is None or share[0] >= GUILD_RUN_SHARE * share[1]:
            return ""
        return (f"only {share[0]} of its {plural(share[1], 'player')} are in {self.guild_name}: it may be a pug or "
                f"another group logged to the guild's page rather than a guild raid night")

    async def summary(self, fight: dict) -> str:
        same = self.by_boss[self.boss_key(fight)]
        await self.all_deaths(same)
        await self.boss_buff_events(same)
        await self.resurrects(same)
        decided = await self.decided_at(fight)
        return format_summary(summarize(fight, same, await self.deaths(fight), self.names, self.abilities,
                                     await self.boss_buffs(fight), await self.routine_buffs(fight),
                                     await self.guild_best(fight), decided))

    async def guild_best(self, fight: dict) -> GuildBest | None:
        """The guild's furthest wipe on the fight's boss and difficulty in its logs of the weeks before this one
        (not counting this log, or another log of the same night): what "new best" is measured against. None
        when there is none, or Warcraft Logs can't tell."""
        guild, zone = self.guild_id, self.zone_id
        if not (guild and zone):
            return None
        since = self.start_ms - GUILD_BEST_WEEKS * 7 * 86400 * 1000
        try:  # the logs before this one hardly change: kept a day, and for good once this one is settled
            reports = await self.keep(("guild wipes", since), [], lambda: self.wcl.guild_wipes(
                guild, zone, since, FOREVER if self.settled else DAY))
        except WCLError:
            return None
        key = (fight["encounterID"], fight.get("difficulty") or 0)
        found = [GuildBest(pct, r["code"], fight_id, pull_no, r["startTime"])
                 for r in reports if r["code"] != self.code and r["endTime"] <= self.start_ms
                 and (b := (r.get("best_wipes") or {}).get(key)) for pct, fight_id, pull_no in [b]]
        return min(found, key=lambda g: g.pct) if found else None

    def phases(self, fight: dict, encounter: int | None = None) -> str:
        """'P1 Sszorak 0:00 → P2 Howling Maelstrom 1:40 → P1 Sszorak 2:05', or '' for a boss without phases. A fight
        from another report gives its `encounter`, and is named by this report's phase names."""
        out = []
        for t in fight.get("phaseTransitions") or []:
            name = self.phase_names.get((encounter or fight["encounterID"], t["id"]))
            out.append(f"P{t['id']}{' ' + name if name else ''} {clock(t['startTime'] - fight['startTime'])}")
        return " → ".join(out)

    async def lusts(self, f: dict) -> list[tuple[float, str, int]]:
        """(timestamp, debuff, players) of each Bloodlust in a pull, from the debuff it leaves (Sated & co.), whose
        source is each player, so it doesn't say who cast it."""
        lust = set(await knowledge.lust_debuffs())
        runs: list[list[dict]] = []
        debuffs = await self.events(f["id"], "Debuffs", hostilityType="Friendlies")
        for e in sorted(debuffs, key=lambda e: e["timestamp"]):
            if e["type"] != "applydebuff" or e.get("targetID") not in self.names or e["abilityGameID"] not in lust:
                continue
            if runs and e["timestamp"] - runs[-1][-1]["timestamp"] <= LUST_GROUP_MS:
                runs[-1].append(e)
            else:
                runs.append([e])
        return [(run[0]["timestamp"], self.ability(run[0]["abilityGameID"]), len({e["targetID"] for e in run}))
                for run in runs]

    async def spells(self, p: Player) -> list[dict]:
        """The player's spec cooldowns from Lorrgs; KnowledgeError when Lorrgs can't answer."""
        return await knowledge.spec_spells(p.class_name, p.spec)

    async def schools(self) -> dict[int, int]:
        """Each ability's school (1 physical, else magic): for defensives that cover magic damage only."""
        return await self.wcl.ability_schools(self.code)

    async def defensive_spells(self, p: Player) -> list[dict]:
        """The spec's personal defensives with a cooldown (Lorrgs' `defensive` tag), for the death recap and the
        raid's defensives table."""
        return [s for s in await self.spells(p) if "defensive" in (s.get("tags") or []) and s.get("cooldown")]

    async def defensive_casts(self) -> list[dict]:
        """Every cast of a defensive or a raid cooldown (Lorrgs' tags, for every spec played in the report), by
        everyone, over the whole report, in one query: who presses what and when, for the death recap's replay
        and the defensives table."""
        kits: dict[tuple[str, str], Player] = {}
        for f in self.fights:
            for q in self.pull_players(f).values():
                kits.setdefault((q.class_name, q.spec), q)
        spells = [sp for got in await asyncio.gather(*(self.spells(q) for q in kits.values())) for sp in got
                  if {"defensive", "raid_cd"} & set(sp.get("tags") or [])]
        ids = sorted(self.spell_ids(spells))
        if not ids:
            return []
        fights = [f["id"] for f in self.fights]
        expr = f"ability.id in ({', '.join(map(str, ids))})"
        casts = await self.events(fights, "Casts", filterExpression=expr)
        return [c for c in casts if c.get("type") == "cast"]

    async def tracked_casts(self, fights: list[dict]) -> dict[int, list[dict]]:
        """Per pull, every cast of a spell Lorrgs tracks for the specs in it (cooldowns of every tag, and the
        untagged ones), by everyone, every pull's in one query: who pressed what when a raid note called for it."""
        found = await asyncio.gather(*(self._tracked_filter(f) for f in fights))
        exprs = {f["id"]: expr for f, expr in zip(fights, found, strict=True) if expr}
        found = await self.events_for([(i, {"filterExpression": e}) for i, e in exprs.items()], "Casts") \
            if exprs else []
        casts = dict(zip(exprs, found, strict=True))
        return {f["id"]: [c for c in casts.get(f["id"], []) if c.get("type") == "cast"] for f in fights}

    async def _tracked_filter(self, f: dict) -> str | None:
        """The filter for the casts of the spells Lorrgs tracks for the specs in a pull."""
        kits: dict[tuple[str, str], Player] = {}
        for q in self.pull_players(f).values():
            kits.setdefault((q.class_name, q.spec), q)
        got = await asyncio.gather(*(self.spells(q) for q in kits.values()))
        ids = sorted(self.spell_ids([sp for spells in got for sp in spells]))
        return f"ability.id in ({', '.join(map(str, ids))})" if ids else None

    def owner(self, actor_id: int) -> int:
        """A pet's or totem's owner, else the actor itself."""
        return self.actors.get(actor_id, {}).get("petOwner") or actor_id

    async def potions(self, f: dict, p: Player, until: float | None = None) -> tuple[int, int]:
        """(healthstones and healing potions, combat potions) a player used in a pull, or until a moment: Warcraft
        Logs' own counts (its Summary table; playerDetails' counts stay 0)."""
        t = await self.table(f["id"], "Summary", endTime=until)
        for members in ((t.get("playerDetails") or {}).values()):
            for m in members if isinstance(members, list) else []:
                if m.get("id") == p.id:
                    return m.get("healthstoneUse", 0), m.get("potionUse", 0)
        return 0, 0

    async def cast_events(self, f: dict, p: Player, until: float | None = None) -> list[dict]:
        """Every cast event of a player: casts, begun casts (`begincast`) and empowers; `casts` keeps the casts."""
        return await self.events(f["id"], "Casts", sourceID=p.id, endTime=until, includeResources=True)

    async def casts(self, f: dict, p: Player, until: float | None = None) -> list[dict]:
        return [c for c in await self.cast_events(f, p, until) if c["type"] == "cast"]
