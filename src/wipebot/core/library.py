"""The reports a process has loaded (`Library`), shared by every tool call, and the guild's latest raid night: the
report a tool reads when given none."""
import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable

from . import clock, wcl
from .cache import cache
from .pulls import DIFFICULTIES
from .report import FactError, Report
from .report.base import link_fights
from .report.night import RAID_PULLS
from .wcl import WCL

LIVE_RELOAD_S = 30  # a report still being logged is read again when its last read is older than this
KEPT_REPORTS = 20  # reports kept loaded (a night's events take tens of MB); the least recently asked go first
IDLE_S = 86400  # a report not asked for this long may be let go, even one that was still being logged then
VISIBLE_FOR_S = 86400  # a loaded report is asked of Warcraft Logs again, for whether it still shows it, this often
VISIBLE_RETRY_S = 300  # ...and when Warcraft Logs didn't answer that, this much later
_log = logging.getLogger(__name__)


class Library:
    """Loaded reports, shared by every tool call. A settled report (no new data for two hours) loads once per
    process; one that may still be logged, through a long break between bosses too, is refreshed when its last
    read is older than `max_age_s`, so new pulls show up and what's known about finished pulls is kept. A tool given
    no report reads `default_report`, else the one `latest()` finds (the guild's latest raid night, serving many
    users). Past KEPT_REPORTS, the one asked for longest ago is let go, but never the default or latest one, nor
    one still being logged that was asked for in the last IDLE_S (one loaded live and never asked for again
    would otherwise stay for good).
    A report the client credentials can't read, that the asker's private-reports token (wcl.reading_as) reads, is
    kept under that token too: another user asking for it reads it with their own, so Warcraft Logs decides who sees
    it, and nothing of it is cached on disk (wcl.mark_private). A call given one is marked as having read a report
    that isn't public (wcl.read_private), however it reads it.
    Warcraft Logs decides who sees a report later too: with each load, and for a loaded report every VISIBLE_FOR_S,
    it's asked whether it still shows the report to the asker. One made private or deleted since is let go, with
    everything kept of it on disk (`_forget`), and is told as any report Warcraft Logs won't show; a private one
    made public is read and kept as any other again."""

    def __init__(self, default_report: str | None = None, wcl: WCL | None = None,
                 latest: Callable[[], Awaitable[str | None]] | None = None):
        self.default_report = default_report
        self.latest = latest
        self._own_wcl = wcl is None  # with our own client, a missing login is offered before the first query
        self.wcl = wcl or WCL()
        # in the order last asked for, by code, or a private report's by (its reader's token hashed, code)
        self._reports: dict[str | tuple[str, str], Report] = {}
        self._asked_at: dict[str | tuple[str, str], float] = {}
        self._loading: dict[str | tuple[str, str], asyncio.Future] = {}  # being loaded now: callers share one load
        self._latest: str | None = None
        self._failed_at: dict[str, float] = {}  # when refreshing a report last failed
        # when Warcraft Logs last showed each report to its asker (`_still_shown`), and the asking going on now
        self._shown_at: dict[str | tuple[str, str], float] = {}
        self._asking: dict[str | tuple[str, str], asyncio.Future] = {}

    async def report(self, report: str | None = None, max_age_s: float = LIVE_RELOAD_S,
                     settled: bool = False) -> Report:
        """A report, loaded once per process. `settled`: a past night that won't change (raid_nights older than a
        day), whose loading is kept on disk for good. When reading a live report again fails, the copy already
        loaded is served, and it's tried again `max_age_s` later. wcl.Unreadable for one Warcraft Logs won't show
        the asker, one it showed before too."""
        code = self._linked(report) if report else self.default_report
        if not code and self.latest:
            code = self._latest = await self.latest()
        if not code:
            raise FactError("no report given: pass a Warcraft Logs report code or URL (find_reports lists recent ones)")
        try:
            found = await self._report(_key(code), code, max_age_s, settled)
        except wcl.Unreadable:
            if wcl.is_private(code) or not await self.wcl.private_report(code):
                raise
            wcl.mark_private(code)  # read from now on with each asker's own token, and kept per token
            self._reports.pop(code, None)
            found = await self._report(_key(code), code, max_age_s, settled)
        if wcl.is_private(code):  # however this call reads it (from memory, by another call's query): not kept
            wcl.read_private()
        return found

    @staticmethod
    def _linked(report: str) -> str:
        """The code of a report given by code or link, the fight a link names noted as the call's default pull
        (report.base.link_fights)."""
        code, fight = report_link(report)
        if fight is not None:
            if (named := link_fights.get()) is None:
                link_fights.set(named := {})
            named[code] = fight
        return code

    async def _report(self, key: str | tuple[str, str], code: str, max_age_s: float, settled: bool) -> Report:
        cached = self._reports.get(key)
        if cached is None:
            # what's kept on disk may answer the load without asking Warcraft Logs: it's asked alongside
            loaded, shown = await asyncio.gather(self._load(key, code, settled), self._still_shown(key, code),
                                                 return_exceptions=True)
            if isinstance(shown, BaseException):
                raise shown
            if isinstance(loaded, BaseException):
                raise loaded
            cached = loaded
        else:
            await self._still_shown(key, code)
            if key not in self._reports:  # a private report made public: kept under its code from now on
                return await self._report(_key(code), code, max_age_s, settled)
            if not cached.settled and time.time() - max(cached.loaded_at, self._failed_at.get(code, 0)) >= max_age_s:
                await self._read_again(cached, key, code, max_age_s)
        self._reports.pop(key, None)
        self._reports[key] = cached  # the most recently asked for last
        now = self._asked_at[key] = time.time()
        while len(self._reports) > KEPT_REPORTS and (old := next(
                (c for c, r in self._reports.items() if c not in (key, self.default_report, self._latest)
                 and (r.final or now - self._asked_at.get(c, 0) > IDLE_S)), None)):
            self._let_go(old)
        return cached

    async def _read_again(self, cached: Report, key: str | tuple[str, str], code: str, max_age_s: float) -> None:
        """Read a report that may still be logged again. When that fails, the copy loaded is served, unless Warcraft
        Logs answered that it won't show the report any more."""
        try:
            await cached.refresh(max_age_s)
        except wcl.Unreadable:
            self._forget(key, code)
            raise
        except Exception as e:
            _log.warning("reading report %s again failed, serving the copy read %.0f s ago: %s", code,
                         time.time() - cached.loaded_at, e)
            self._failed_at[code] = time.time()

    async def _still_shown(self, key: str | tuple[str, str], code: str) -> None:
        """Ask Warcraft Logs whether it still shows the report to the asker, unless it did in the last
        VISIBLE_FOR_S (callers asking at once share one question; a frozen cache, the evals', asks nothing).
        wcl.Unreadable when it doesn't: the report is then forgotten."""
        if cache().frozen or time.time() - self._shown_at.get(key, 0) < VISIBLE_FOR_S:
            return
        task = self._asking.get(key)
        if task is None:
            task = self._asking[key] = asyncio.ensure_future(self._ask_shown(key, code))
            task.add_done_callback(lambda _: self._asking.pop(key, None))
        await asyncio.shield(task)

    async def _ask_shown(self, key: str | tuple[str, str], code: str) -> None:
        try:
            visibility = await self.wcl.visibility(code)
        except wcl.Unreadable:
            self._forget(key, code)
            raise
        except wcl.WCLError as e:  # an outage says nothing about the report: served as loaded, asked again soon
            _log.warning("asking whether Warcraft Logs still shows report %s failed: %s", code, e)
            self._shown_at[key] = time.time() - VISIBLE_FOR_S + VISIBLE_RETRY_S
            return
        self._shown_at[key] = time.time()
        if visibility == "public" and wcl.is_private(code):  # its owner published it: read and kept as any other
            wcl.public_again(code)
            for kept in [k for k in self._reports if isinstance(k, tuple) and k[1] == code]:
                self._let_go(kept)

    def _let_go(self, key: str | tuple[str, str]) -> None:
        for kept in (self._reports, self._asked_at, self._shown_at):
            kept.pop(key, None)

    def _forget(self, key: str | tuple[str, str], code: str) -> None:
        """Warcraft Logs no longer shows report `code` to the asker (made private since it was read, or deleted):
        the copy loaded for them is let go, and of one read as a public report everything kept on disk that names
        it, so nobody is answered from what was read before."""
        self._let_go(key)
        if key == code:
            cache().forget(code)

    async def night(self, night_: dict, max_age_s: float = LIVE_RELOAD_S) -> list[Report]:
        """The reports of one of the guild's raid nights (night.raid_nights), loaded side by side."""
        return list(await asyncio.gather(*(self.report(code, max_age_s, settled=settled(night_))
                                           for code in night_["codes"])))

    async def _load(self, key: str | tuple[str, str], code: str, settled: bool) -> Report:
        task = self._loading.get(key)
        if task is None:
            async def load() -> Report:
                if self._own_wcl:
                    ensure_wcl_access()
                r = Report(self.wcl, code, settled)
                await r.load()
                return r
            task = self._loading[key] = asyncio.ensure_future(load())
            task.add_done_callback(lambda _: self._loading.pop(key, None))
        return await asyncio.shield(task)


def _key(code: str) -> str | tuple[str, str]:
    """Where a report is kept: under its code, shared by everyone; one that isn't public (wcl.mark_private) under the
    asker's private-reports token too, which reads it, or for an asker without one, nowhere."""
    if not wcl.is_private(code):
        return code
    if not (token := wcl.private_token()):
        raise wcl.Unreadable(code)
    return hashlib.sha256(token.encode()).hexdigest(), code


def settled(night_: dict) -> bool:
    """Whether a raid night (night.raid_nights) is a day or more past, so its logs won't change any more."""
    return time.time() * 1000 - night_["start"] > 86400 * 1000


RAID_REPORTS_CHECKED = 5  # the newest reports looked at for the guild's latest raid night
LATEST_RAID_DAYS = 7  # how far back the guild's latest raid night is looked for


async def latest_raid(library: Library, reports: list[dict]) -> str | None:
    """The guild's latest raid night: of the newest few reports with at least RAID_PULLS boss pulls (`pulls`), the
    newest on the hardest difficulty one has that many pulls on (`pulls_by_difficulty`), since raiders log their
    Heroic or alt runs after a Mythic night, whose players are mostly in the guild, since they log their pugs to
    the guild's page too; else the first of those; else the newest."""
    newest = sorted(reports, key=lambda r: -r["startTime"])
    raids = sorted((r for r in newest[:RAID_REPORTS_CHECKED] if r.get("pulls", 0) >= RAID_PULLS),
                   key=lambda r: (-_hardest(r), -r["startTime"]))
    for r in raids:
        try:
            if not (doubt := await (await library.report(r["code"])).guild_run_doubt()):
                return r["code"]
            _log.info("skipping report %s as the guild's raid night: %s", r["code"], doubt)
        except Exception:
            _log.exception("loading report %s failed", r["code"])
    return raids[0]["code"] if raids else newest[0]["code"] if newest else None


def _hardest(report: dict) -> int:
    """The hardest raid difficulty a report (wcl.guild_reports with `pulls`) has RAID_PULLS boss pulls on, or 0."""
    return max((d for d, n in (report.get("pulls_by_difficulty") or {}).items()
                if d in DIFFICULTIES and n >= RAID_PULLS), default=0)


class LatestRaids:
    """The latest raid night (`latest_raid`) of the last LATEST_RAID_DAYS among some guilds' reports, kept
    `max_age_s` before it's looked up again. Callers asking at once share one look-up."""

    def __init__(self, library: Library, max_age_s: float):
        self.library, self.max_age_s = library, max_age_s
        self.found: dict[tuple[int, ...], tuple[float, str | None]] = {}  # guild ids -> (when looked up, report)
        self._looking: dict[tuple[int, ...], asyncio.Future] = {}

    async def of(self, guild_ids: Iterable[int]) -> str | None:
        ids = tuple(sorted(guild_ids))
        checked, code = self.found.get(ids, (0.0, None))
        if time.time() - checked < self.max_age_s:
            return code
        task = self._looking.get(ids)
        if task is None:
            task = self._looking[ids] = asyncio.ensure_future(self._look_up(ids))
            task.add_done_callback(lambda _: self._looking.pop(ids, None))
        return await asyncio.shield(task)

    async def _look_up(self, ids: tuple[int, ...]) -> str | None:
        since = (clock.now() - LATEST_RAID_DAYS * 86400) * 1000
        reports = [r for gid in ids for r in await self.library.wcl.guild_reports(gid, since, pulls=True)]
        code = await latest_raid(self.library, reports)
        self.found[ids] = (time.time(), code)
        return code


def report_code(report: str) -> str:
    """'https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21' or the bare code -> the code."""
    m = re.search(r"reports/([A-Za-z0-9]+)", report)
    code = m.group(1) if m else report.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8,24}", code):
        raise FactError(f"not a Warcraft Logs report code or URL: {report!r}")
    return code


_LINK_FIGHT = re.compile(r"[?#&]fight=([^&#\s]*)")


def report_link(report: str) -> tuple[str, int | None]:
    """A report given by code or link: (its code, the fight the link names). Warcraft Logs' links name a fight as
    '#fight=21' or '?fight=21&type=damage-done&source=6' (what the address bar holds); 'fight=last' and a link
    without one name none, which reads the latest pull. A link to several fights is an error that says so, as one
    comparing reports is (it has no one code)."""
    code = report_code(report)
    named = _LINK_FIGHT.search(report)
    if not named or named[1] in ("", "last"):
        return code, None
    if not named[1].isdigit():
        raise FactError(f"the link names more than one fight (fight={named[1]}): give a link to one fight, or the "
                        "report's code and the pull")
    return code, int(named[1])


def ensure_wcl_access() -> None:
    """WCL needs a token even for public reports: client credentials (or a login saved in warcraft_cli)."""
    if wcl.client_credentials() or wcl.saved_login():
        return
    raise FactError("Warcraft Logs isn't set up: set WCL_CLIENT_ID and WCL_CLIENT_SECRET (a client from "
                    "https://www.warcraftlogs.com/api/clients)")
