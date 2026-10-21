"""The reports a process has loaded (`Library`), shared by every tool call, and the guild's latest raid night: the
report a tool reads when given none."""
import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable

from . import clock, wcl
from .report import FactError, Report
from .report.night import RAID_PULLS
from .wcl import WCL

LIVE_RELOAD_S = 30  # a report still being logged is read again when its last read is older than this
KEPT_REPORTS = 20  # reports kept loaded (a night's events take tens of MB); the least recently asked go first
IDLE_S = 86400  # a report not asked for this long may be let go, even one that was still being logged then
_log = logging.getLogger(__name__)


class Library:
    """Loaded reports, shared by every tool call. A settled report (no new data for two hours) loads once per
    process; one that may still be logged, through a long break between bosses too, is refreshed when its last
    read is older than `max_age_s`, so new pulls show up and what's known about finished pulls is kept. A tool given
    no report reads `default_report`, else the one `latest()` finds (the guild's latest raid night, serving many
    users). Past KEPT_REPORTS, the one asked for longest ago is let go, but never the default or latest one, nor
    one still being logged that was asked for in the last IDLE_S (one loaded live and never asked for again
    would otherwise stay for good)."""

    def __init__(self, default_report: str | None = None, wcl: WCL | None = None,
                 latest: Callable[[], Awaitable[str | None]] | None = None):
        self.default_report = default_report
        self.latest = latest
        self._own_wcl = wcl is None  # with our own client, a missing login is offered before the first query
        self.wcl = wcl or WCL()
        self._reports: dict[str, Report] = {}  # in the order last asked for
        self._asked_at: dict[str, float] = {}
        self._loading: dict[str, asyncio.Future] = {}  # reports being loaded now: callers at once share one load
        self._latest: str | None = None
        self._failed_at: dict[str, float] = {}  # when refreshing a report last failed

    async def report(self, report: str | None = None, max_age_s: float = LIVE_RELOAD_S,
                     settled: bool = False) -> Report:
        """A report, loaded once per process. `settled`: a past night that won't change (raid_nights older than a
        day), whose loading is kept on disk for good. When reading a live report again fails, the copy already
        loaded is served, and it's tried again `max_age_s` later."""
        code = report_code(report) if report else self.default_report
        if not code and self.latest:
            code = self._latest = await self.latest()
        if not code:
            raise FactError("no report given: pass a Warcraft Logs report code or URL (find_reports lists recent ones)")
        cached = self._reports.get(code)
        if cached is None:
            cached = await self._load(code, settled)
        elif not cached.settled and time.time() - max(cached.loaded_at, self._failed_at.get(code, 0)) >= max_age_s:
            try:
                await cached.refresh(max_age_s)
            except Exception as e:
                _log.warning("reading report %s again failed, serving the copy read %.0f s ago: %s", code,
                             time.time() - cached.loaded_at, e)
                self._failed_at[code] = time.time()
        self._reports.pop(code, None)
        self._reports[code] = cached  # the most recently asked for last
        now = self._asked_at[code] = time.time()
        while len(self._reports) > KEPT_REPORTS and (old := next(
                (c for c, r in self._reports.items() if c not in (code, self.default_report, self._latest)
                 and (r.final or now - self._asked_at.get(c, 0) > IDLE_S)), None)):
            del self._reports[old]
            self._asked_at.pop(old, None)
        return cached

    async def night(self, night_: dict, max_age_s: float = LIVE_RELOAD_S) -> list[Report]:
        """The reports of one of the guild's raid nights (night.raid_nights), loaded side by side."""
        return list(await asyncio.gather(*(self.report(code, max_age_s, settled=settled(night_))
                                           for code in night_["codes"])))

    async def _load(self, code: str, settled: bool) -> Report:
        task = self._loading.get(code)
        if task is None:
            async def load() -> Report:
                if self._own_wcl:
                    ensure_wcl_access()
                r = Report(self.wcl, code, settled)
                await r.load()
                return r
            task = self._loading[code] = asyncio.ensure_future(load())
            task.add_done_callback(lambda _: self._loading.pop(code, None))
        return await asyncio.shield(task)


def settled(night_: dict) -> bool:
    """Whether a raid night (night.raid_nights) is a day or more past, so its logs won't change any more."""
    return time.time() * 1000 - night_["start"] > 86400 * 1000


RAID_REPORTS_CHECKED = 5  # the newest reports looked at for the guild's latest raid night
LATEST_RAID_DAYS = 7  # how far back the guild's latest raid night is looked for


async def latest_raid(library: Library, reports: list[dict]) -> str | None:
    """The guild's latest raid night: of the newest few reports, the newest with at least RAID_PULLS boss pulls
    (`pulls`) whose players are mostly in the guild, since raiders log their pugs to the guild's page too; else
    the newest with enough pulls; else the newest."""
    newest = sorted(reports, key=lambda r: -r["startTime"])
    raids = [r for r in newest[:RAID_REPORTS_CHECKED] if r.get("pulls", 0) >= RAID_PULLS]
    for r in raids:
        try:
            if not (doubt := await (await library.report(r["code"])).guild_run_doubt()):
                return r["code"]
            _log.info("skipping report %s as the guild's raid night: %s", r["code"], doubt)
        except Exception:
            _log.exception("loading report %s failed", r["code"])
    return raids[0]["code"] if raids else newest[0]["code"] if newest else None


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


def ensure_wcl_access() -> None:
    """WCL needs a token even for public reports: client credentials (or a login saved in warcraft_cli)."""
    if wcl.client_credentials() or wcl.saved_login():
        return
    raise FactError("Warcraft Logs isn't set up: set WCL_CLIENT_ID and WCL_CLIENT_SECRET (a client from "
                    "https://www.warcraftlogs.com/api/clients)")
