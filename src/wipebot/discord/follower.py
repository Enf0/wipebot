"""Follow the guild's live report and find the boss pulls that finished since the last poll."""
import logging
import time
from dataclasses import dataclass

from wipebot.core.pulls import is_reset
from wipebot.core.report import REPORT_QUIET_S, FactError, Report
from wipebot.core.server import Library

from .store import Store

log = logging.getLogger(__name__)

REPORT_CHECK_S = 120      # how often to look for a new guild report
BACKLOG_S = 10 * 60       # on locking onto a report, older pulls are skipped instead of answered
LOOKBACK_S = 12 * 3600    # only consider reports started this recently


@dataclass
class Pull:
    """A boss pull that just finished."""
    report: str
    fight: int
    boss: str
    pull_no: int  # pull number on the boss
    ended_ms: float  # UNIX ms
    kill: bool = False
    reset: bool = False  # the boss was untouched: nothing to explain

    @property
    def wipe(self) -> bool:
        """A wipe worth explaining: not a kill, not a reset."""
        return not self.kill and not self.reset


def pull_of(report: Report, fight: dict) -> Pull:
    return Pull(report.code, fight["id"], fight["name"], report.pull_no(fight), report.start_ms + fight["endTime"],
                bool(fight["kill"]), is_reset(fight))


class Follower:
    """Reads the live report through the core's Report, the same one the tools read, so the answers about a pull
    share what's been fetched."""

    def __init__(self, library: Library, store: Store, guild_id: int, test_report: str | None = None):
        self.library, self.store = library, store
        self.guild_id = guild_id  # the guild on Warcraft Logs
        # `wipebot replay`: follow this (old) report as if it were live, one pull per poll.
        self.test_report = test_report
        self.code: str | None = test_report
        self._last_report_check = 0.0
        self._skipped: set[str] = set()  # live reports not followed (not the guild's), logged once each

    async def _pick_report(self, now_ms: float) -> None:
        """Lock onto one live report. Several raiders may log the same night; switching
        between their reports would answer every wipe twice, so keep the current one while it is live. A report
        whose players are mostly not in the guild (a raider's pug logged to the guild's page) isn't followed: its
        wipes would be answered in the guild's channel."""
        reports = await self.library.wcl.guild_reports(self.guild_id, now_ms - LOOKBACK_S * 1000)
        live = [r for r in reports if now_ms - r["endTime"] < REPORT_QUIET_S * 1000]
        if self.code and any(r["code"] == self.code for r in live):
            return
        new, report = None, None
        for r in sorted(live, key=lambda r: -r["endTime"]):
            try:
                candidate = await self.library.report(r["code"], max_age_s=0)
            except FactError:  # no boss pull yet
                continue
            if doubt := await candidate.guild_run_doubt():
                if r["code"] not in self._skipped:
                    log.info("not following report %s: %s", r["code"], doubt)
                    self._skipped.add(r["code"])
                continue
            new, report = r["code"], candidate
            break
        if new != self.code:
            log.info("following report %s", new)
            self.code = new
            if report:
                self._skip_backlog(report, now_ms)

    def _skip_backlog(self, report: Report, now_ms: float) -> None:
        for f in report.fights:
            if now_ms - (report.start_ms + f["endTime"]) > BACKLOG_S * 1000:
                self.store.mark_posted(report.code, f["id"], latency_s=None)

    async def poll(self) -> list[Pull]:
        """The pulls that finished since the last poll. Call `done` once each is handled."""
        now_ms = time.time() * 1000
        stale_check = self.code is None or now_ms / 1000 - self._last_report_check > REPORT_CHECK_S
        if stale_check and not self.test_report:
            self._last_report_check = now_ms / 1000
            await self._pick_report(now_ms)
        if self.code is None:
            return []

        report = await self.library.report(self.code, max_age_s=0)
        pulls = [pull_of(report, f) for f in sorted(report.fights, key=lambda f: f["id"])
                 if not f.get("inProgress") and not self.store.is_posted(report.code, f["id"])]
        return pulls[:1] if self.test_report else pulls

    def done(self, pull: Pull) -> None:
        latency = None if self.test_report else time.time() - pull.ended_ms / 1000
        self.store.mark_posted(pull.report, pull.fight, latency_s=latency)
        if latency is not None:
            log.info("pull %s (%s) handled %.0f s after it ended", pull.fight, pull.boss, latency)
