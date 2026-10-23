"""Warcraft Logs access: warcraft_cli's WCL client (auth, retries, saved login) behind a small async API.

warcraft_cli's client is synchronous, so each query runs in a worker thread with that thread's own
client. It reads with client credentials from WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET, which Wipebot
fills from WCL_CLIENT_ID / WCL_CLIENT_SECRET, unless warcraft_cli has a user login saved (`saved_login`), which it
uses instead; serving many users, never (`client_credentials_only`), but a signed-in user's own token for what they
ask (`reading_as`).
"""
import asyncio
import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from warcraft_core.auth import load_provider_auth_state
from warcraftlogs_cli.client import GRAPHQL_WARNINGS_KEY, WarcraftLogsClient, WarcraftLogsClientError

from .cache import DAY, FOREVER, HOUR, cache, cache_key

log = logging.getLogger(__name__)

GUILD_REPORTS = """
query($guild: Int!, $since: Float!, $page: Int!) {
  reportData { reports(guildID: $guild, startTime: $since, limit: 100, page: $page) {
    data { code title startTime endTime }
    has_more_pages
  } }
}
"""
# Each boss pull of a guild's logs of one raid, for its furthest wipe on each boss before a night; 50 a page keeps the
# query under WCL's complexity limit (100 is over it).
GUILD_WIPES = """
query($guild: Int!, $zone: Int!, $since: Float!, $page: Int!) {
  reportData { reports(guildID: $guild, zoneID: $zone, startTime: $since, limit: 50, page: $page) {
    data { code startTime endTime fights(killType: Encounters) { id encounterID difficulty kill fightPercentage } }
    has_more_pages
  } }
}
"""
GUILD_WIPE_PAGES = 6  # 300 logs of one raid
GUILD_PROGRESS = """
query($guild: Int!, $zone: Int!) {
  guildData { guild(id: $guild) { zoneRanking(zoneId: $zone) {
    progress { worldRank { number } regionRank { number } serverRank { number } }
  } } }
}
"""

GUILD_MEMBERS = """
query($guild: Int!, $page: Int!) {
  guildData { guild(id: $guild) { members(limit: 100, page: $page) { has_more_pages data { name } } } }
}
"""
GUILD_MEMBER_PAGES = 20  # 2000 characters, alts included (a big guild has about 800)

TALENT_CODE = """
query($code: String!, $fight: Int!, $actor: Int!) {
  reportData { report(code: $code) { fights(fightIDs: [$fight]) { talentImportCode(actorID: $actor) } } }
}
"""

ATTENDANCE = """
query($guild: Int!, $zone: Int!, $page: Int!) {
  guildData { guild(id: $guild) { attendance(zoneID: $zone, limit: 25, page: $page) {
    has_more_pages data { code startTime players { name type presence } }
  } } }
}
"""
ATTENDANCE_PAGES = 8  # 200 logs

# With each report's boss pulls (boss, difficulty, kill) in the same query (`pulls`).
GUILD_REPORTS_WITH_PULLS = GUILD_REPORTS.replace(
    "startTime endTime }", "startTime endTime fights(killType: Encounters) { name difficulty kill } }")
GUILD_REPORTS_WITH_PLAYERS = GUILD_REPORTS_WITH_PULLS.replace(  # 25 a page: 100 is over WCL's query complexity
    "{ name difficulty kill } }",
    '{ name difficulty kill friendlyPlayers } masterData { actors(type: "Player") { id name } } }').replace(
    "limit: 100", "limit: 25")
GUILD_REPORT_PAGES = 10  # 1000 reports: years of a guild's raids, Mythic+ and personal logs
CHARACTER_REPORTS = """
query($id: Int!, $page: Int!) {
  characterData { character(id: $id) { recentReports(limit: 25, page: $page) {
    data { code title startTime endTime visibility guild { name } zone { name }
           fights(killType: Encounters) { name difficulty kill } }
    has_more_pages
  } } }
}
"""
CHARACTER_REPORT_PAGES = 8  # 200 reports: months of a raider's raids, Mythic+ and pugs
CHARACTER_REPORTS_S = 30 * 60  # a character's reports are kept this long: a page takes several seconds to read
GUILD_REPORTS_S = 5 * 60  # a guild's reports, for questions (find_reports, raid nights); the follower asks fresh

REPORT_FIGHTS = """
query($code: String!) {
  reportData { report(code: $code) {
    code title startTime endTime
    guild { id name }
    zone { id name partitions { name default } }
    phases { encounterID phases { id name isIntermission } }
    fights(killType: Encounters) {
      id name encounterID difficulty inProgress kill bossPercentage fightPercentage averageItemLevel friendlyPlayers
      friendlySpecs
      lastPhase lastPhaseIsIntermission lastPhaseAsAbsoluteIndex phaseTransitions { id startTime }
      startTime endTime enemyNPCs { id gameID instanceCount }
    }
  } }
}
"""

ACTORS = """
query($code: String!) {
  reportData { report(code: $code) { masterData { actors { id name type subType petOwner gameID } } } }
}
"""

PLAYERS = """
query($code: String!) {
  reportData { report(code: $code) { masterData { actors(type: "Player") { id name } } } }
}
"""

ABILITIES = """
query($code: String!) {
  reportData { report(code: $code) { masterData { abilities { gameID name icon } } } }
}
"""
ABILITY_SCHOOLS = """
query($code: String!) {
  reportData { report(code: $code) { masterData { abilities { gameID type } } } }
}
"""

RANKED_CHARACTERS = """
query($code: String!) { reportData { report(code: $code) { rankedCharacters { id name } } } }
"""

PLAYER_DETAILS = """
query($code: String!, $start: Float!, $end: Float!, $combatant: Boolean!) {
  reportData { report(code: $code) {
    playerDetails(startTime: $start, endTime: $end, includeCombatantInfo: $combatant)
  } }
}
"""

# Historical: each kill's percentile as of when it was logged, as on character pages (encounterRankings and
# zoneRankings use it too); the default, Today, gives the same kill another number.
RANKINGS = """
query($code: String!, $fight: Int!, $metric: ReportRankingMetricType!) {
  reportData { report(code: $code) { rankings(fightIDs: [$fight], playerMetric: $metric, timeframe: Historical) } }
}
"""

# Every kill of a report (fightIDs left out; a null fightIDs gives nothing).
ALL_RANKINGS = """
query($code: String!, $metric: ReportRankingMetricType!) {
  reportData { report(code: $code) { rankings(playerMetric: $metric, timeframe: Historical) } }
}
"""

# Rankings live in partitions (12.1, 12.1.5: a patch's rankings start afresh); the default is the newest, empty
# for a while after it opens, so each ranking query takes a partition and falls back to the one before.
CHARACTER_RANKINGS = """
query($encounter: Int!, $class: String!, $spec: String!, $difficulty: Int!, $metric: CharacterRankingMetricType!,
      $partition: Int) {
  worldData { encounter(id: $encounter) {
    characterRankings(className: $class, specName: $spec, difficulty: $difficulty, metric: $metric,
                      includeCombatantInfo: true, partition: $partition)
  } }
}
"""

RANKING_PAGE = """
query($encounter: Int!, $class: String!, $spec: String!, $difficulty: Int!, $metric: CharacterRankingMetricType!,
      $page: Int!, $partition: Int) {
  worldData { encounter(id: $encounter) {
    characterRankings(className: $class, specName: $spec, difficulty: $difficulty, metric: $metric, page: $page,
                      partition: $partition)
  } }
}
"""

ENCOUNTER_PARTITIONS = """
query($encounter: Int!) {
  worldData { encounter(id: $encounter) { zone { id partitions { id name compactName default } } } }
}
"""

ZONE_PARTITIONS = """
query($id: Int!) { worldData { zone(id: $id) { partitions { id name compactName default } } } }
"""

GUILD_FIELDS = "id name server { name slug region { slug } }"
GUILD_BY_ID = f"""
query($id: Int!) {{ guildData {{ guild(id: $id) {{ {GUILD_FIELDS} }} }} }}
"""

RATE = "query { rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }"
RATE_S = 60  # the hour's points are read at most this often
RESERVE_SHARE = 1 / 6  # of the hour's points, kept for ordinary questions: an opt-in heavy read is refused past it

MY_REPORTS = """
query { userData { currentUser { id name guilds { id name server { slug region { slug } } } } } }
"""


def saved_login() -> dict | None:
    """A user login saved in warcraft_cli's store (unexpired), which warcraft_cli reads with instead of the client
    credentials: it sees that user's private reports."""
    state = load_provider_auth_state("warcraftlogs")
    if not isinstance(state, dict) or state.get("auth_mode") not in ("pkce", "authorization_code"):
        return None
    expires_at = state.get("expires_at")
    if isinstance(expires_at, (int, float)) and time.time() >= expires_at:
        return None
    return state if state.get("access_token") else None


ZONE = """
query($id: Int!) { worldData { zone(id: $id) { name encounters { id name } } } }
"""

ALL_ENCOUNTERS = """
query { worldData { expansions { zones { id name encounters { id name } } } } }
"""

FIGHT_RANKINGS = """
query($encounter: Int!, $difficulty: Int!, $partition: Int) {
  worldData { encounter(id: $encounter) {
    fightRankings(difficulty: $difficulty, metric: speed, partition: $partition)
  } }
}
"""

KILL = """
query($code: String!, $fight: Int!) {
  reportData { report(code: $code) {
    code guild { name }
    fights(fightIDs: [$fight]) {
      id name encounterID difficulty kill averageItemLevel startTime endTime phaseTransitions { id startTime }
    }
    masterData { actors(type: "NPC", subType: "Boss") { id name gameID } }
  } }
}
"""


END_OF_REPORT = 2**40  # an endTime past any report's end, in ms
# Report fields asked for in one query, each under its own alias ("f46: table(...)"): Warcraft Logs answers a
# client's requests one after another, so forty pulls' tables in one query come back in under a second where
# forty queries take ten or more. It caps a query's complexity; a night's pulls fit.
ALIASES_PER_QUERY = 40


UNREADABLE = ("Warcraft Logs won't show report {code}: it's private now, was deleted, or the code is wrong. Say so; "
              "asking again won't help.")
PARTIAL = "Warcraft Logs answered only part of the question ({said}); ask again"


def best_wipes(fights: list[dict]) -> dict[tuple[int, int], tuple[float, int, int]]:
    """A log's furthest wipe on each boss, from its boss pulls in order: {(encounter id, difficulty): (fight %,
    fight id, pull number on that boss and difficulty)}."""
    best: dict[tuple[int, int], tuple[float, int, int]] = {}
    pulls: Counter = Counter()
    for f in fights:
        key = (f.get("encounterID") or 0, f.get("difficulty") or 0)
        pulls[key] += 1
        pct = f.get("fightPercentage")
        if not f.get("kill") and pct is not None and (key not in best or pct < best[key][0]):
            best[key] = (pct, f.get("id") or 0, pulls[key])
    return best


class WCLError(RuntimeError):
    pass


def client_credentials() -> tuple[str, str] | None:
    """The Warcraft Logs client Wipebot reads with, (id, secret): WCL_CLIENT_ID and WCL_CLIENT_SECRET, else
    warcraft_cli's WARCRAFTLOGS_CLIENT_ID and WARCRAFTLOGS_CLIENT_SECRET; None without both of either."""
    for prefix in ("WCL", "WARCRAFTLOGS"):
        if (client := os.environ.get(f"{prefix}_CLIENT_ID")) and (secret := os.environ.get(f"{prefix}_CLIENT_SECRET")):
            return client, secret
    return None


def use_wcl_env_names() -> None:
    """Let WCL_CLIENT_ID / WCL_CLIENT_SECRET stand in for warcraft_cli's variable names."""
    pairs = (("WCL_CLIENT_ID", "WARCRAFTLOGS_CLIENT_ID"), ("WCL_CLIENT_SECRET", "WARCRAFTLOGS_CLIENT_SECRET"))
    for ours, theirs in pairs:
        if os.environ.get(ours) and not os.environ.get(theirs):
            os.environ[theirs] = os.environ[ours]


def _args(**filters) -> str:
    """GraphQL arguments from keyword filters, leaving out the ones that are None."""
    out = []
    for k, v in filters.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, list):
            out.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif isinstance(v, str) and k in ("dataType", "hostilityType", "viewBy", "killType"):
            out.append(f"{k}: {v}")  # enums
        else:
            out.append(f"{k}: {json.dumps(v)}")
    return ", ".join(out)


# Each Warcraft Logs request waits this long for an answer, and is tried this often. On 14 Oct 2026 WCL hung and
# warcraft_cli's default three tries made two calls take 62 s each, an answer 144 s; a query that needs over 20 s
# never succeeded anyway, so a third try only adds to the wait.
WCL_TIMEOUT_S, WCL_ATTEMPTS = 20.0, 2


_client_only = False


def client_credentials_only() -> None:
    """From now on, read Warcraft Logs with the client credentials only, in this process: warcraft_cli reads with a
    user login saved in its store, looked for on every call, which serving many users would show its private
    reports to all of them."""
    global _client_only
    _client_only = True


_user: ContextVar[str | None] = ContextVar("wcl_user", default=None)  # the token `reading_as` gave


@contextmanager
def reading_as(token: str | None) -> Generator[None]:
    """Within it, a server reading with the client credentials only reads with this user's Warcraft Logs token
    instead, at the user endpoint: on Warcraft Logs' new point system the user's own points pay first, and the
    client's once they run out. Its answers are cached for everyone, so the token's scope must read nothing the
    client credentials can't (`view-user-profile`; docs/reference/wcl-api.md, "Point types"). A token Warcraft Logs
    refuses (expired, revoked) falls back to the client credentials."""
    reset = _user.set(token or None)
    try:
        yield
    finally:
        _user.reset(reset)


COST_ALIAS = "wipebotCost"  # the query's own cost and the hour's points, asked alongside every query (free)
HOUR_FIELDS = ("limitPerHour pointsSpentThisHour pointsResetIn databaseLimitPerHour databasePointsSpentThisHour "
               "currentRequest")
_keep_share = 0.0  # of the hour's points, left to others: past it, this process waits (keep_for_others)


@dataclass(frozen=True)
class Budget:
    """How much of the account's Warcraft Logs points this process may spend in an hour, for a server sharing the
    account with others (a public instance beside a guild's own): past it, its queries are refused until the hour
    ends, and what it read before still answers from disk. Read from the environment (`from_env`)."""
    share: float = 1.0  # of the hour's points, and of its database points, this process's at most
    leave: float = 0.0  # of the account's hour, left to the others: past it, this process reads nothing more
    user_points: bool = False  # the account is on the new point system: users' own points aren't this process's

    @classmethod
    def from_env(cls) -> Budget:
        """WIPEBOT_WCL_SHARE and WIPEBOT_WCL_LEAVE, each a share from 0 to 1 (unset: no limit), and
        WIPEBOT_WCL_USER_POINTS, set once the account is opted into the new point system; ValueError says what's
        wrong with them."""
        def share(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                value = -1.0
            if not 0 <= value <= 1:
                raise ValueError(f"{name}: a share of the hour's Warcraft Logs points from 0 to 1, e.g. 0.1, "
                                 f"not {raw!r}")
            return value
        user_points = os.environ.get("WIPEBOT_WCL_USER_POINTS", "").strip().lower() in ("1", "true", "yes", "on")
        return cls(share("WIPEBOT_WCL_SHARE", 1.0), share("WIPEBOT_WCL_LEAVE", 0.0), user_points)

    def refusal(self, rate: dict | None, fresh: bool, own: Counter, minutes: int) -> str | None:
        """Why the next query isn't sent, or None: `rate` is the account's hour as last read (`fresh` if within
        RATE_S), `own` this process's points this hour by kind."""
        if not rate:
            return None
        later = f"ask again in {minutes} min; what was read before still answers"
        limit, db_limit = rate.get("limitPerHour"), rate.get("databaseLimitPerHour")
        if self.share < 1 and isinstance(limit, int | float):
            # the application points include the database points; on the old system every point is the app's
            spent = own["application"] if self.user_points else own["total"]
            if spent >= limit * self.share:
                return (f"not read from Warcraft Logs: this server has spent its share of this hour's points "
                        f"({spent:,.0f} of {limit * self.share:,.0f}); {later}")
            if isinstance(db_limit, int | float) and own["database"] >= db_limit * self.share:
                return (f"not read from Warcraft Logs: this server has spent its share of this hour's database "
                        f"points, which rankings and report lists cost ({own['database']:,.0f} of "
                        f"{db_limit * self.share:,.0f}); {later}")
        if self.leave and fresh and isinstance(limit, int | float) \
                and limit - rate.get("pointsSpentThisHour", 0) < limit * self.leave:
            return (f"not read from Warcraft Logs: {rate.get('pointsSpentThisHour', 0):,.0f} of the account's "
                    f"{limit:,} points this hour are spent, and the rest is kept for others sharing it; {later}")
        return None


def keep_for_others(share: float) -> None:
    """From now on, this process waits for the next hour rather than spend the account's points past (1 - share)
    of the hour: for the evals, which share the account's points with the bot and the remote server."""
    global _keep_share
    _keep_share = share


class Hour:
    """This hour's Warcraft Logs points: the account's, as the latest answer gave them, and this process's own,
    added up from each query's cost. When an hour ends, both are logged, so a host sees how much of the account's
    points went elsewhere (the other app, the evals, a developer's calls)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.rate: dict | None = None  # limitPerHour, pointsSpentThisHour, pointsResetIn, ...
        self._read_at = 0.0  # time.monotonic() of `rate`
        self._ends: float | None = None  # time.time() the hour ends
        self._own: Counter = Counter()  # this process's points this hour, by kind

    def saw(self, answer: dict) -> None:
        """Take a query's COST_ALIAS answer."""
        now = time.time()
        with self._lock:
            if self._ends is not None and now > self._ends + 1:
                self._log(self._ends)
                self._own.clear()
                self.rate = None
            if isinstance(answer.get("pointsResetIn"), int | float):
                self._ends = now + answer["pointsResetIn"]
                self.rate, self._read_at = {k: v for k, v in answer.items() if k != "currentRequest"}, time.monotonic()
            for kind, points in (answer.get("currentRequest") or {}).items():
                if isinstance(points, int | float):
                    self._own[kind] += points

    def fresh(self, max_age_s: float) -> dict | None:
        """The account's points as last read, if read in the last `max_age_s`."""
        with self._lock:
            return self.rate if self.rate and time.monotonic() - self._read_at < max_age_s else None

    def now(self) -> tuple[dict | None, bool, Counter, int]:
        """The hour so far, for a Budget: the account's points as last read (None once that hour has ended, which a
        process refusing every query wouldn't otherwise see), whether read in the last RATE_S, this process's own
        points by kind, and the minutes until the hour ends."""
        with self._lock:
            now = time.time()
            if self._ends is None or now > self._ends:
                return None, False, Counter(), 0
            return (self.rate, time.monotonic() - self._read_at < RATE_S, Counter(self._own),
                    math.ceil((self._ends - now) / 60))

    def _log(self, ended: float) -> None:
        rate = self.rate or {}
        account = (f"the account had spent at least {rate['pointsSpentThisHour']:,.0f} of {rate['limitPerHour']:,} "
                   f"({rate.get('databasePointsSpentThisHour', 0):,.0f} database)" if rate else "")
        log.info("Warcraft Logs points, the hour to %s: %s by this process (%s database)%s",
                 time.strftime("%H:%M", time.localtime(ended)), f"{self._own['total']:,.0f}",
                 f"{self._own['database']:,.0f}", f"; {account}" if account else "")


class Points:
    """The Warcraft Logs points a tool call's queries cost, by kind (`application`, `database`, `user`, `total`, as
    `rateLimitData.currentRequest` gives them; docs/reference/wcl-api.md, "Point types"). Queries run in threads
    side by side, so adding up takes a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.spent: Counter = Counter()
        self.queries = 0

    def add(self, cost: dict) -> None:
        with self._lock:
            self.queries += 1
            for kind, points in cost.items():
                if isinstance(points, int | float):
                    self.spent[kind] += points

    def record(self) -> dict:
        """{"total": ..., "database": ..., "queries": n} for a record, rounded; {} when nothing was asked."""
        with self._lock:
            if not self.queries:
                return {}
            return {**{k: round(v, 2) for k, v in self.spent.items() if v}, "queries": self.queries}


_points: ContextVar[Points | None] = ContextVar("wcl_points", default=None)


@contextmanager
def counting_points() -> Generator[Points]:
    """Within it, each query also asks what it cost and adds it to the Points it yields: a tool call's Warcraft Logs
    points, for the records. warcraft_cli's own guild and character lookups aren't counted."""
    points = Points()
    reset = _points.set(points)
    try:
        yield points
    finally:
        _points.reset(reset)


def _with_cost(query: str) -> str:
    """The query with its own cost and the hour's points asked in its first selection (it costs nothing more), or as
    it is when that can't be placed (a query with fragments, as a raw query may have)."""
    if "fragment " in query:
        return query
    depth = 0
    for i, ch in enumerate(query):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "{" and depth == 0:
            return f"{query[:i + 1]} {COST_ALIAS}: rateLimitData {{ {HOUR_FIELDS} }} {query[i + 1:]}"
    return query


class _ClientOnly(WarcraftLogsClient):
    """warcraft_cli's client that never sees a saved user login: its own requests (`guild`, `character`) and
    `raw_graphql` all ask the client endpoint then, or the user endpoint with the token `reading_as` gave."""

    def _has_user_token(self) -> bool:
        return bool(_user.get())

    def _user_token(self) -> str:
        if not (token := _user.get()):
            raise WarcraftLogsClientError("missing_user_auth", "no user token for this call")
        return token


class WCL:
    def __init__(self):
        use_wcl_env_names()
        self._local = threading.local()
        self._hour = Hour()
        self._budget = Budget.from_env()

    def _client(self) -> WarcraftLogsClient:
        if not hasattr(self._local, "client"):
            self._local.client = (_ClientOnly if _client_only else WarcraftLogsClient)(
                timeout_seconds=WCL_TIMEOUT_S, retry_attempts=WCL_ATTEMPTS)
        return self._local.client

    def _run(self, query: str, variables: dict, keep: bool = False) -> dict:
        """One query. Warcraft Logs answers some queries only in part (an error next to the data, e.g. a fight it
        couldn't rank right then): an answer meant for the cache, or whole (`keep`), is then refused rather than
        kept, so an empty ranking never sticks for a day and gets told to raiders as "no ranked kills"."""
        self._wait_for_points()
        if refused := self._budget.refusal(*self._hour.now()):
            raise WCLError(refused)
        query, points = _with_cost(query), _points.get()
        try:
            try:
                data, _endpoint = self._client().raw_graphql(operation_name=None, query=query, variables=variables)
            except WarcraftLogsClientError as e:
                if e.code != "auth_failed" or not _user.get():
                    raise
                log.info("a user's Warcraft Logs token was refused (%s): read with the client credentials", e)
                with reading_as(None):
                    data, _endpoint = self._client().raw_graphql(operation_name=None, query=query,
                                                                 variables=variables)
        except WarcraftLogsClientError as e:
            raise WCLError(f"{e.code}: {e}") from e
        data = dict(data or {})
        if cost := data.pop(COST_ALIAS, None):
            self._hour.saw(cost)
            if points is not None:
                points.add(cost.get("currentRequest") or {})
        if warnings := data.pop(GRAPHQL_WARNINGS_KEY, None):
            if "permission" in str(warnings).lower() and "code" in variables:
                raise WCLError(UNREADABLE.format(code=variables["code"]))
            if keep:
                said = "; ".join(str(w.get("message", w)) if isinstance(w, dict) else str(w) for w in warnings)
                raise WCLError(PARTIAL.format(said=said))
            log.warning("partial WCL response: %s", warnings)
        return data

    async def query(self, query: str, cache_for: float | None = None, whole: bool = False, **variables) -> dict:
        """Run a query. With `cache_for`, its answer is kept in the cache on disk and fresh for that many seconds:
        for data that changes slowly (rankings) or never (another guild's kill). A frozen cache (the evals) keeps
        every answer, so a run reads the same data as the last. Such an answer, or one asked `whole`, raises
        WCLError when Warcraft Logs answers only part of it."""
        if cache_for is None and not cache().frozen:
            data = await asyncio.to_thread(self._run, query, variables, whole)
        else:
            data = await cache().fetch(cache_key("wcl", query, variables), cache_for or FOREVER,
                                       lambda: asyncio.to_thread(self._run, query, variables, True))
        found = data.get("reportData")
        if "code" in variables and isinstance(found, dict) and "report" in found and found["report"] is None:
            raise WCLError(UNREADABLE.format(code=variables["code"]))
        return data

    async def points(self) -> dict | None:
        """This hour's points (limitPerHour, pointsSpentThisHour, pointsResetIn), read at most every RATE_S; None
        when Warcraft Logs doesn't say."""
        if rate := self._hour.fresh(RATE_S):  # every query's answer brings them
            return rate
        try:
            rate = (await self.query(RATE))["rateLimitData"]
        except (WCLError, KeyError) as e:
            log.info("reading the hour's points failed: %s", e)
            return None
        self._hour.saw(rate)
        return rate

    def _wait_for_points(self) -> None:
        """With points kept for others (keep_for_others), wait for the next hour once this hour has less left."""
        if not _keep_share or not (rate := self._hour.fresh(RATE_S)):
            return
        limit = rate["limitPerHour"]
        if limit - rate["pointsSpentThisHour"] < limit * _keep_share:
            log.warning("%.0f of this hour's %d Warcraft Logs points are spent, the rest kept for the bot and the "
                        "server: waiting %d min for the next hour", rate["pointsSpentThisHour"], limit,
                        rate["pointsResetIn"] // 60 + 1)
            time.sleep(rate["pointsResetIn"] + 5)

    async def ensure_points(self, need: float, what: str) -> None:
        """Refuse an opt-in heavy read (`what`, about `need` points) that would leave less than RESERVE_SHARE of the
        hour's points for ordinary questions (a frozen cache, the evals', spends none)."""
        if cache().frozen or not (rate := await self.points()):
            return
        limit = rate["limitPerHour"]
        left, reserve = limit - rate["pointsSpentThisHour"], limit * RESERVE_SHARE
        if left - need < reserve:
            raise WCLError(f"{what} reads about {need:.0f} Warcraft Logs points, and {max(left, 0):.0f} of this "
                           f"hour's {limit} are left, {reserve:.0f} of them kept for other questions: answer "
                           f"without it, or ask again in {rate['pointsResetIn'] // 60 + 1} min")

    # ---- reports ----

    async def guild_reports(self, guild_id: int, since_ms: float, pulls: bool = False,
                            players: bool = False, cache_for: float | None = None) -> list[dict]:
        """Every report (code, title, start and end) of a guild since a time, by WCL guild id, newest first. A
        guild uploads several a week (raids, Mythic+, personal logs), so this follows the pages. With `pulls`,
        each also has `pulls`, how many boss pulls it has, `pulls_by_difficulty` ({difficulty: pulls}) and `bosses`
        ({boss name: (pulls, kills)}, in the order pulled); with `players` too, `players`, the names of the
        players in its boss pulls (a log also has everyone who passed by).
        With `cache_for`, the list is kept that long and asked from the hour before `since_ms`, so the next
        question asks the same; the follower, which looks for a log just started, asks fresh."""
        query = GUILD_REPORTS_WITH_PLAYERS if players else GUILD_REPORTS_WITH_PULLS if pulls else GUILD_REPORTS
        asked = since_ms - since_ms % (HOUR * 1000) if cache_for else since_ms
        out: list[dict] = []
        for page in range(1, GUILD_REPORT_PAGES * (4 if players else 1) + 1):  # the same 1000 reports
            data = await self.query(query, cache_for, guild=guild_id, since=asked, page=page)
            reports = data["reportData"]["reports"]
            for r in reports["data"] or []:
                if asked != since_ms and r.get("startTime", since_ms) < since_ms:  # asked only for the cache
                    continue
                if players:
                    names = {a["id"]: a["name"] for a in ((r.pop("masterData", None) or {}).get("actors") or [])}
                    in_pulls = {i for f in r.get("fights") or [] for i in f.get("friendlyPlayers") or []}
                    r["players"] = sorted({names[i] for i in in_pulls if i in names})
                if pulls or players:
                    fights = r.pop("fights", None) or []
                    r["pulls"] = len(fights)
                    r["pulls_by_difficulty"] = dict(Counter(f.get("difficulty") for f in fights))
                    bosses: dict[str, list[int]] = {}
                    for f in fights:
                        b = bosses.setdefault(f.get("name") or "?", [0, 0])
                        b[0] += 1
                        b[1] += 1 if f.get("kill") else 0
                    r["bosses"] = {name: (pulls, kills) for name, (pulls, kills) in bosses.items()}
                out.append(r)
            if not reports.get("has_more_pages"):
                break
        return out

    async def guild_wipes(self, guild_id: int, zone_id: int, since_ms: float, cache_for: float) -> list[dict]:
        """A guild's logs of one raid since a time, newest first: code, start, end and `best_wipes` (see
        best_wipes). Asked from the hour before `since_ms` and kept `cache_for`, like guild_reports."""
        asked = since_ms - since_ms % (HOUR * 1000)
        out: list[dict] = []
        for page in range(1, GUILD_WIPE_PAGES + 1):
            reports = (await self.query(GUILD_WIPES, cache_for, guild=guild_id, zone=zone_id, since=asked,
                                        page=page))["reportData"]["reports"]
            for r in reports["data"] or []:
                if r.get("startTime", since_ms) >= since_ms:
                    out.append({"code": r["code"], "startTime": r["startTime"], "endTime": r["endTime"],
                                "best_wipes": best_wipes(r.get("fights") or [])})
            if not reports.get("has_more_pages"):
                break
        return out

    async def guild_members(self, guild_id: int) -> set[str]:
        """The names of a guild's characters on Warcraft Logs (from the game's guild roster, alts included)."""
        names: set[str] = set()
        for page in range(1, GUILD_MEMBER_PAGES + 1):
            got = ((await self.query(GUILD_MEMBERS, DAY, guild=guild_id, page=page))["guildData"]["guild"] or {}
                   ).get("members") or {}
            names |= {m["name"] for m in got.get("data") or []}
            if not got.get("has_more_pages"):
                break
        return names

    async def parse_stats(self, parses: list[tuple[str, int, str]]) -> list[dict | None]:
        """Each ranked parse's (report code, fight id, character name) stats at the pull, as playerDetails'
        combatantInfo gives them ({"Crit": {"min": .., "max": ..}, ...}), None where the log has none. Kept on disk
        for good per parse (a ranked kill never changes); the ones not kept come from one query over their reports,
        of which only the stats are kept (each report's answer is ~180 KB)."""
        keys = [cache_key("parse stats", code, fight, name) for code, fight, name in parses]
        out: list[dict | None] = [found[0] if (found := cache().get(k)) else None for k in keys]
        missing = [i for i, v in enumerate(out) if v is None]
        if not missing:
            return out
        body = " ".join(f"r{i}: report(code: {json.dumps(parses[i][0])}) {{ playerDetails(fightIDs: "
                        f"[{int(parses[i][1])}], includeCombatantInfo: true) }}" for i in missing)
        data = (await asyncio.to_thread(self._run, f"query {{ reportData {{ {body} }} }}", {}))["reportData"]
        for i in missing:
            details = (((data.get(f"r{i}") or {}).get("playerDetails") or {}).get("data") or {}).get(
                "playerDetails") or {}
            stats = next(((p.get("combatantInfo") or {}).get("stats") for role in details.values()
                          for p in role if p.get("name") == parses[i][2]), None)
            if stats:
                async def ready(stats: dict = stats) -> dict:
                    return stats
                out[i] = await cache().fetch(keys[i], FOREVER, ready)
        return out

    async def talent_code(self, code: str, fight_id: int, actor_id: int) -> str:
        """A player's talents on a pull as the game's import string ('' when the log lacks them)."""
        data = await self.query(TALENT_CODE, code=code, fight=fight_id, actor=actor_id)
        fights = (data["reportData"]["report"] or {}).get("fights") or [{}]
        return fights[0].get("talentImportCode") or ""

    async def attendance(self, guild_id: int, zone_id: int, since_ms: float) -> list[dict]:
        """The guild's logs of a raid since a time, newest first, each with `code`, `startTime` and `players`
        (`name`, `type` = class, `presence`: 1 there, 2 benched). Two people logging one night are two logs."""
        out: list[dict] = []
        for page in range(1, ATTENDANCE_PAGES + 1):
            data = (await self.query(ATTENDANCE, HOUR, guild=guild_id, zone=zone_id, page=page))["guildData"]
            got = ((data["guild"] or {}).get("attendance") or {})
            out += [n for n in got.get("data") or [] if n["startTime"] >= since_ms]
            if not got.get("has_more_pages") or any(n["startTime"] < since_ms for n in got.get("data") or []):
                break
        return out

    async def guild_progress(self, guild_id: int, zone_id: int) -> dict:
        """A guild's progress rank in a raid on Warcraft Logs: {'world': n, 'region': n, 'realm': n}, the ones it
        has."""
        data = await self.query(GUILD_PROGRESS, HOUR, guild=guild_id, zone=zone_id)
        progress = (((data["guildData"]["guild"] or {}).get("zoneRanking") or {}).get("progress") or {})
        return {ours: (progress.get(theirs) or {}).get("number") for ours, theirs in
                (("world", "worldRank"), ("region", "regionRank"), ("realm", "serverRank"))
                if (progress.get(theirs) or {}).get("number")}

    async def find_guild(self, name: str, realm: str, region: str) -> dict | None:
        """A guild on Warcraft Logs by name, realm and region: id, name, server name and slug, region. The realm
        can be spelled any way: Warcraft Logs keeps some realms' word breaks (tarren-mill) and runs others
        together (azjolnerub), and slugs some Russian realms in English, so warcraft_cli tries each spelling. Kept
        a day: a guild's id and names hardly change. A guild not found isn't kept: it's asked again next time (a
        guild just made, a name fixed in the settings)."""
        class NotFound(Exception):
            pass

        def find() -> dict:
            try:
                return self._client().guild(region=region, realm=realm, name=name)
            except WarcraftLogsClientError as e:
                if e.code == "not_found":
                    raise NotFound from e
                raise WCLError(f"{e.code}: {e}") from e
        try:
            return await cache().fetch(cache_key("wcl-guild", name.lower(), realm.lower(), region.lower()), DAY,
                                       lambda: asyncio.to_thread(find))
        except NotFound:
            return None

    async def find_character(self, name: str, realm: str, region: str) -> dict | None:
        """A character on Warcraft Logs (id, name, server name and slug), the realm spelled any way, a log's own
        spelling ('TarrenMill') too; None when there's none."""
        realm = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", realm)  # a log's 'TarrenMill' -> 'Tarren Mill'

        def find() -> dict | None:
            try:
                return self._client().character(region=region, realm=realm, name=name)
            except WarcraftLogsClientError as e:
                if e.code == "not_found":
                    return None
                raise WCLError(f"{e.code}: {e}") from e
        return await cache().fetch(cache_key("wcl-character", name.lower(), realm.lower(), region), DAY,
                                   lambda: asyncio.to_thread(find))

    async def character_reports(self, character_id: int, since_ms: float) -> list[dict]:
        """Every report a character is in that started since a time, whoever logged it (pugs and other guilds'
        too), newest first: code, title, start and end, visibility, guild, zone, and the boss pulls (name,
        difficulty, kill). Warcraft Logs lists them by upload, not by start, so this reads pages until one has
        none that recent."""
        out: list[dict] = []
        for page in range(1, CHARACTER_REPORT_PAGES + 1):
            data = await self.query(CHARACTER_REPORTS, CHARACTER_REPORTS_S, id=character_id, page=page)
            reports = (data["characterData"]["character"] or {}).get("recentReports") or {}
            recent = [r for r in reports.get("data") or [] if r["startTime"] >= since_ms]
            out += recent
            if not recent or not reports.get("has_more_pages"):
                break
        return sorted(out, key=lambda r: -r["startTime"])

    async def zone_encounters(self, zone_id: int) -> tuple[str, list[dict]]:
        """A raid's name and its bosses (id, name), in the raid's order."""
        zone = (await self.query(ZONE, DAY, id=zone_id))["worldData"]["zone"] or {}
        return zone.get("name") or "", zone.get("encounters") or []

    async def all_encounters(self) -> list[tuple[int, str, int, str]]:
        """Every boss Warcraft Logs ranks: (zone id, zone name, encounter id, boss name). A test realm's copy of a
        raid is a zone of its own, "(PTR)" or "(Beta)" in its name."""
        data = await self.query(ALL_ENCOUNTERS, DAY)
        return [(z["id"], z["name"], e["id"], e["name"]) for x in data["worldData"]["expansions"] or []
                for z in x.get("zones") or [] for e in z.get("encounters") or []]

    async def older_partitions(self, zone_id: int | None = None, encounter_id: int | None = None) -> list[int]:
        """A zone's ranking partitions other than the default one, newest first: a patch's rankings start afresh
        in a new partition, empty for a while, and the season's kills sit in the one before."""
        if zone_id is None:
            data = await self.query(ENCOUNTER_PARTITIONS, DAY, encounter=encounter_id)
            zone = (data["worldData"]["encounter"] or {}).get("zone") or {}
        else:
            data = await self.query(ZONE_PARTITIONS, DAY, id=zone_id)
            zone = data["worldData"]["zone"] or {}
        parts = [p for p in zone.get("partitions") or [] if p.get("id")]
        return [p["id"] for p in sorted(parts, key=lambda p: -p["id"]) if not p.get("default")]

    async def encounter_rankings(self, character_id: int, encounters: list[int], difficulties: list[int],
                                 metric: str, zone_id: int | None = None) -> dict[tuple[int, int], dict]:
        """Every ranked kill of a character this season, per (boss, difficulty): `ranks` (one per kill: report
        code and fight, start time, rankPercent, amount, duration, item level as bracketData, spec, guild),
        totalKills, medianPerformance. One query; a boss and difficulty with no kills has no ranks. With
        `zone_id`, an empty default partition falls back to the one before."""
        async def ask(partition: int | None) -> dict:
            part = f", partition: {partition}" if partition else ""
            fields = " ".join(f"e{e}_{d}: encounterRankings(encounterID: {e}, difficulty: {d}, metric: {metric}{part})"
                              for e in encounters for d in difficulties)
            q = f"query($id: Int!) {{ characterData {{ character(id: $id) {{ {fields} }} }} }}"
            return (await self.query(q, HOUR, id=character_id))["characterData"]["character"] or {}
        found = await ask(None)
        if zone_id and not any((v or {}).get("ranks") for v in found.values()):
            for partition in await self.older_partitions(zone_id):
                found = await ask(partition)
                if any((v or {}).get("ranks") for v in found.values()):
                    break
        return {(e, d): found.get(f"e{e}_{d}") or {} for e in encounters for d in difficulties}

    async def ranked_characters(self, code: str) -> dict[str, int]:
        """The Warcraft Logs character id of every player ranked in a report (everyone in a kill), by name."""
        data = await self.query(RANKED_CHARACTERS, DAY, code=code)
        report = data["reportData"]["report"] or {}
        return {c["name"]: c["id"] for c in report.get("rankedCharacters") or []}

    async def zone_rankings(self, character_ids: list[int], zone_id: int, difficulty: int, metric: str,
                            spec: str | None = None, role: str = "Any") -> dict[int, dict]:
        """Each character's summary of a raid as their Warcraft Logs page shows it, by character id: per boss the
        best kill (`rankings`: encounter, rankPercent, medianPercent, spec, totalKills), bestPerformanceAverage,
        medianPerformanceAverage, allStars per spec. Without `spec`, each boss's best kill of any spec of `role`
        (Any, DPS, Healer, Tank), ranked by `metric`: with Any, a healing spec's kill is ranked on dps too. One
        query."""
        if not character_ids:
            return {}
        character_ids = sorted(set(character_ids))  # one query for the same characters, however they were listed
        fields = " ".join(f"c{i}: character(id: {i}) {{ zoneRankings(zoneID: $zone, difficulty: $difficulty, "
                          f"metric: $metric, specName: $spec, role: $role, partition: $partition) }}"
                          for i in character_ids)
        q = (f"query($zone: Int!, $difficulty: Int!, $metric: CharacterPageRankingMetricType!, $spec: String, "
             f"$role: RoleType!, $partition: Int) "
             f"{{ characterData {{ {fields} }} }}")

        async def ask(partition: int | None) -> dict[int, dict]:
            found = (await self.query(q, HOUR, zone=zone_id, difficulty=difficulty, metric=metric, spec=spec,
                                      role=role, partition=partition))["characterData"]
            return {i: (found.get(f"c{i}") or {}).get("zoneRankings") or {} for i in character_ids}
        out = await ask(None)
        if not any(z.get("rankings") for z in out.values()):  # a new partition, empty so far
            for partition in await self.older_partitions(zone_id):
                out = await ask(partition)
                if any(z.get("rankings") for z in out.values()):
                    break
        return out

    async def guild_by_id(self, guild_id: int) -> dict | None:
        """A guild on Warcraft Logs by id, with the same fields as find_guild."""
        data = await self.query(GUILD_BY_ID, id=guild_id)
        return (data.get("guildData") or {}).get("guild")

    async def my_guilds(self) -> tuple[str, list[dict]]:
        """The logged-in user's name and guilds. Needs the browser login."""
        user = (await self.query(MY_REPORTS))["userData"]["currentUser"]
        return user["name"], user.get("guilds") or []

    async def report_fights(self, code: str, cache_for: float | None = None) -> dict:
        report = (await self.query(REPORT_FIGHTS, cache_for, code=code))["reportData"]["report"]
        if report is None:
            raise WCLError(f"report {code} not found, or not visible to this login")
        return report

    async def actors(self, code: str, cache_for: float | None = None) -> dict[int, dict]:
        """Every actor in a report (players, pets, NPCs) by id."""
        data = await self.query(ACTORS, cache_for, code=code)
        return {a["id"]: a for a in data["reportData"]["report"]["masterData"]["actors"]}

    async def player_ids(self, code: str) -> dict[str, int]:
        """Player name -> actor id in a report (e.g. another guild's kill)."""
        data = await self.query(PLAYERS, code=code)
        return {a["name"]: a["id"] for a in data["reportData"]["report"]["masterData"]["actors"]}

    async def ability_table(self, code: str, cache_for: float | None = None) -> dict[int, tuple[str, str]]:
        """Ability game id -> (name, icon file such as 'spell_nature_riptide.jpg') for a report (about 900 KB)."""
        data = await self.query(ABILITIES, cache_for, code=code)
        return {a["gameID"]: (a["name"], a.get("icon") or "")
                for a in data["reportData"]["report"]["masterData"]["abilities"]}

    async def ability_schools(self, code: str) -> dict[int, int]:
        """Ability game id -> school (1 physical, else a bitmask of magic schools), for a report; cached."""
        data = await self.query(ABILITY_SCHOOLS, cache_for=FOREVER, code=code)
        return {a["gameID"]: int(a["type"]) for a in data["reportData"]["report"]["masterData"]["abilities"]
                if str(a.get("type") or "").isdigit()}

    async def player_details(self, code: str, start: float, end: float, combatant: bool = False,
                             cache_for: float | None = None) -> dict:
        """Class, spec, role and item level for a time range, by role; with `combatant`, also each player's
        combatantInfo (stats, talentTree, gear with enchants)."""
        data = await self.query(PLAYER_DETAILS, cache_for, code=code, start=start, end=end, combatant=combatant)
        return data["reportData"]["report"]["playerDetails"]["data"]["playerDetails"]

    async def rankings(self, code: str, fight_id: int | None, metric: str) -> list[dict]:
        """WCL's rankings of one kill, or of every kill in the report with `fight_id` None (`metric` dps or hps):
        per role each player's percentile (`rankPercent`, `bracketPercent`, `totalParses` of their class and spec),
        and per kill `speed` and `execution` percentiles, `deaths`, `duration`. A wipe gives [], and so does a kill
        Warcraft Logs hasn't ranked yet."""
        if fight_id is None:
            data = await self.query(ALL_RANKINGS, code=code, metric=metric)
        else:
            data = await self.query(RANKINGS, code=code, fight=fight_id, metric=metric)
        return (data["reportData"]["report"]["rankings"] or {}).get("data") or []

    async def rankings_many(self, kills: list[tuple[str, int]], metric: str) -> list[list[dict]]:
        """`rankings` of several kills of any reports (report code, fight id), in order, in one query. Kept: a
        ranked kill's historical rankings don't change."""
        if not kills:
            return []
        if bad := [code for code, _ in kills if not re.fullmatch(r"\w+", code)]:
            raise WCLError(f"not a report code: {bad[0]!r}")
        body = " ".join(f'k{i}: report(code: "{code}") {{ rankings(fightIDs: [{int(fight)}], playerMetric: $metric, '
                        "timeframe: Historical) }" for i, (code, fight) in enumerate(kills))
        data = await self.query(f"query($metric: ReportRankingMetricType!) {{ reportData {{ {body} }} }}", FOREVER,
                                metric=metric)
        return [((data["reportData"].get(f"k{i}") or {}).get("rankings") or {}).get("data") or []
                for i in range(len(kills))]

    async def top_rankings(self, encounter_id: int, class_name: str, spec: str, difficulty: int,
                           metric: str) -> list[dict]:
        """The top 100 parses of one spec on one boss, with their talents and gear; from the partition before
        the default when the default is empty (a new patch's)."""
        async def ask(partition: int | None) -> list[dict]:
            data = await self.query(CHARACTER_RANKINGS, DAY, encounter=encounter_id, **{"class": class_name},
                                    spec=spec, difficulty=difficulty, metric=metric, partition=partition)
            return ((data["worldData"]["encounter"] or {}).get("characterRankings") or {}).get("rankings") or []
        ranked = await ask(None)
        for partition in [] if ranked else await self.older_partitions(encounter_id=encounter_id):
            if ranked := await ask(partition):
                break
        return ranked

    async def ranking_page(self, encounter_id: int, class_name: str, spec: str, difficulty: int, metric: str,
                           page: int, partition: int | None = None) -> tuple[list[dict], bool, int | None]:
        """One page (100, best first) of a spec's ranked kills of a boss, each character's best, from `partition`
        (None: the default): (rankings with amount, duration and item level, whether more pages follow, the
        partition read). Page 1 of an empty default (a new patch's) comes from the partition before it, so the
        later pages are to be read from the partition page 1 came from: the default's are empty too."""
        async def ask(partition: int | None) -> dict:
            data = await self.query(RANKING_PAGE, DAY, encounter=encounter_id, **{"class": class_name}, spec=spec,
                                    difficulty=difficulty, metric=metric, page=page, partition=partition)
            return (data["worldData"]["encounter"] or {}).get("characterRankings") or {}
        ranked = await ask(partition)
        for older in [] if ranked.get("rankings") or page > 1 or partition is not None else \
                await self.older_partitions(encounter_id=encounter_id):
            if (found := await ask(older)).get("rankings"):
                ranked, partition = found, older
                break
        if ranked.get("error"):  # e.g. past page 20: "The maximum page value supported by the API is 20."
            raise WCLError(ranked["error"])
        return ranked.get("rankings") or [], bool(ranked.get("hasMorePages")), partition

    async def fastest_kills(self, encounter_id: int, difficulty: int) -> list[dict]:
        """The 50 fastest kills of a boss: report code and fight, duration, deaths, average item level."""
        async def ask(partition: int | None) -> list[dict]:
            data = await self.query(FIGHT_RANKINGS, DAY, encounter=encounter_id, difficulty=difficulty,
                                    partition=partition)
            return ((data["worldData"]["encounter"] or {}).get("fightRankings") or {}).get("rankings") or []
        ranked = await ask(None)
        for partition in [] if ranked else await self.older_partitions(encounter_id=encounter_id):
            if ranked := await ask(partition):
                break
        return ranked

    async def kill(self, code: str, fight_id: int) -> dict | None:
        """One fight of any report (e.g. another guild's kill) with its phases and the report's bosses."""
        report = (await self.query(KILL, FOREVER, code=code, fight=fight_id))["reportData"]["report"]
        if not report or not report.get("fights"):
            return None
        return {**report["fights"][0], "code": report["code"], "guild": (report.get("guild") or {}).get("name"),
                "bosses": report["masterData"]["actors"]}

    async def graph(self, code: str, fight: dict, data_type: str, **filters) -> dict:
        """A WCL graph over one fight: `series` of rates, one point every `pointInterval` ms from the fight
        start. The fight's time range has to be given, or WCL draws the whole report."""
        args = _args(fightIDs=[fight["id"]], startTime=fight["startTime"], endTime=fight["endTime"],
                     dataType=data_type, **filters)
        q = f"query($code: String!) {{ reportData {{ report(code: $code) {{ graph({args}) }} }} }}"
        return (await self.query(q, code=code))["reportData"]["report"]["graph"]["data"]

    async def graphs(self, code: str, fights: list[dict], data_type: str, **filters) -> dict[int, dict]:
        """`graph` for each of several fights, by fight id, in as few queries as possible."""
        fields = {f"f{f['id']}": "graph(" + _args(fightIDs=[f["id"]], startTime=f["startTime"], endTime=f["endTime"],
                                                  dataType=data_type, **filters) + ")" for f in fights}
        got = await self._aliased(code, fields)
        return {f["id"]: got[f"f{f['id']}"]["data"] for f in fights}

    async def table(self, code: str, fight_id: int | list[int], data_type: str, **filters) -> dict:
        """A WCL table (DamageDone, Healing, DamageTaken, Casts, ...) for one fight, or summed over several."""
        ids = fight_id if isinstance(fight_id, list) else [fight_id]
        args = _args(fightIDs=ids, dataType=data_type, **filters)
        q = f"query($code: String!) {{ reportData {{ report(code: $code) {{ table({args}) }} }} }}"
        return (await self.query(q, code=code))["reportData"]["report"]["table"]["data"]

    async def _aliased(self, code: str, fields: dict[str, str]) -> dict[str, Any]:
        """Several of a report's fields in one query (ALIASES_PER_QUERY at a time), each under its alias:
        {alias: "table(...)"} -> {alias: its answer}. An answer missing one (Warcraft Logs answered part of the
        query) is a WCLError."""
        out: dict[str, Any] = {}
        names = list(fields)
        for i in range(0, len(names), ALIASES_PER_QUERY):
            body = " ".join(f"{n}: {fields[n]}" for n in names[i:i + ALIASES_PER_QUERY])
            q = f"query($code: String!) {{ reportData {{ report(code: $code) {{ {body} }} }} }}"
            got = (await self.query(q, whole=True, code=code))["reportData"]["report"]
            if missing := [n for n in names[i:i + ALIASES_PER_QUERY] if not isinstance(got.get(n), dict)]:
                raise WCLError(PARTIAL.format(said=f"no answer for {', '.join(missing[:5])}"))
            out.update(got)
        return out

    async def tables(self, code: str, items: list[tuple[str, int, dict]], data_type: str,
                     **filters) -> dict[str, dict]:
        """A WCL table for each of several (name, fight id, the fight's own filters such as an endTime) items,
        by name, in as few queries as possible."""
        fields = {name: f"table({_args(fightIDs=[fid], dataType=data_type, **{**filters, **own})})"
                  for name, fid, own in items}
        got = await self._aliased(code, fields)
        return {name: got[name]["data"] for name, _, _ in items}

    async def player_details_many(self, code: str, fights: list[dict], combatant: bool = True) -> dict[int, dict]:
        """player_details for each of several fights, by fight id, in as few queries as possible."""
        fields = {f"f{f['id']}": f"playerDetails(startTime: {f['startTime']}, endTime: {f['endTime']}, "
                                 f"includeCombatantInfo: {str(combatant).lower()})" for f in fights}
        got = await self._aliased(code, fields)
        return {f["id"]: got[f"f{f['id']}"]["data"]["playerDetails"] for f in fights}

    async def events(self, code: str, fight_id: int | list[int], data_type: str, **filters) -> list[dict]:
        """All events of one type in one fight or several (one query; each event has its `fight`)."""
        return (await self.events_many(code, [(fight_id, {})], data_type, **filters))[0]

    async def events_many(self, code: str, fights: list[tuple[int | list[int], dict]], data_type: str,
                          **filters) -> list[list[dict]]:
        """`events` for each of several items (a fight or several, and their own filters, e.g. a time window; a
        fight may come up more than once), in order: the first page of each in as few queries as possible, the
        rare later pages (10,000 events each) one by one. An item of several fights is asked a fight an alias:
        Warcraft Logs pages events over several fights by time, a page every fight or so when filtered to a player,
        so a night's casts came in forty queries one after another."""
        specs, parts = [], []  # each alias's (ids, filters, start, end); each item's aliases
        for fid, own in fights:
            f = {**filters, **own}
            ids = fid if isinstance(fid, list) else [fid]
            # Without an end, WCL's next page comes back empty.
            start, end = f.pop("startTime", None), f.pop("endTime", None) or END_OF_REPORT
            parts.append(range(len(specs), len(specs) + len(ids)))
            specs += [([i], f, start, end) for i in ids]

        def field(ids: list[int], f: dict, start: float | None, end: float) -> str:
            return ("events(" + _args(fightIDs=ids, dataType=data_type, startTime=start, endTime=end, limit=10000,
                                      **f) + ") { data nextPageTimestamp }")
        got = await self._aliased(code, {f"f{i}": field(*spec) for i, spec in enumerate(specs)})
        each = []
        for i, (ids, f, _start, end) in enumerate(specs):
            page = got[f"f{i}"]
            data = list(page["data"] or [])
            while page.get("nextPageTimestamp"):
                page = (await self._aliased(code, {"f0": field(ids, f, page["nextPageTimestamp"], end)}))["f0"]
                data += page["data"] or []
            each.append(data)
        # An item's fights back in time order, as one query over them gave them
        return [each[part[0]] if len(part) == 1 else sorted((e for i in part for e in each[i]),
                                                            key=lambda e: e.get("timestamp", 0)) for part in parts]

    async def boss_buffs(self, code: str, fight_id: int | list[int], bosses: set[int]) -> list[dict]:
        """Buffs gained and lost by the bosses (applybuff and removebuff events) in one fight or several."""
        events = await self.events(code, fight_id, "Buffs", hostilityType="Enemies")
        return [e for e in events if e.get("targetID") in bosses and e["type"] in ("applybuff", "removebuff")]


def boss_ids(actors: dict[int, dict]) -> set[int]:
    """The actor ids of a report's bosses."""
    return {i for i, a in actors.items() if a.get("subType") == "Boss"}
