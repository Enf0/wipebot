"""The reports a process has loaded (library.Library): a finished one read once, a live one refreshed in place, the
pull that just ended waited for, and the guild's latest raid night."""
import asyncio
import time
from types import SimpleNamespace
from typing import cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.support import CODE, FakeWCL, Night
from wipebot.core import library, server
from wipebot.core.wcl import WCLError


async def test_a_finished_report_is_read_once(night):
    wcl = FakeWCL(night)
    lib = library.Library(wcl=wcl.client())
    rep = await lib.report(CODE)
    assert rep.final and rep.settled  # the captured night ended long ago
    await lib.report(CODE, max_age_s=0)
    assert wcl.queries.count("report_fights") == 1


async def test_a_live_report_is_refreshed_in_place_and_keeps_what_it_knows(live_wcl):
    live_wcl.visible -= 1
    lib = library.Library(wcl=live_wcl.client())
    rep = await lib.report(CODE)
    assert not rep.final and len(rep.fights) == 34
    deaths = await rep.deaths(rep.fights[0])

    live_wcl.queries.clear()
    assert await lib.report(CODE, max_age_s=0) is rep  # no new pull: the players and abilities aren't read again
    assert live_wcl.queries == ["report_fights"]

    live_wcl.visible += 1  # the next pull ends
    assert await lib.report(CODE, max_age_s=0) is rep
    assert len(rep.fights) == 35 and "actors" in live_wcl.queries
    live_wcl.queries.clear()
    assert await rep.deaths(rep.fights[0]) is deaths  # a finished pull's deaths are remembered
    assert live_wcl.queries == []

    await lib.report(CODE)  # read moments ago: not again
    assert live_wcl.queries == []


async def test_names_are_read_again_when_a_pull_ends_not_when_one_starts():
    night = Night()  # changed below: not the session's
    wcl = FakeWCL(night, time.time() * 1000)
    wcl.visible -= 1
    lib = library.Library(wcl=wcl.client())
    rep = await lib.report(CODE)
    following = night.fights[wcl.visible]
    night.fights[wcl.visible] = {**following, "inProgress": True}
    wcl.visible += 1  # the next pull starts
    wcl.queries.clear()
    await lib.report(CODE, max_age_s=0)
    assert rep.live == {following["id"]} and "actors" not in wcl.queries
    night.fights[wcl.visible - 1] = following  # and ends
    await lib.report(CODE, max_age_s=0)
    assert not rep.live and "actors" in wcl.queries


async def test_callers_asking_at_once_share_one_load(night):
    wcl = FakeWCL(night)
    lib = library.Library(wcl=wcl.client())
    first, second = await asyncio.gather(lib.report(CODE), lib.report(CODE))
    assert first is second and wcl.queries.count("report_fights") == 1


async def test_a_live_report_that_cant_be_read_again_is_served_as_loaded(live_wcl, monkeypatch):
    lib = library.Library(wcl=live_wcl.client())
    rep = await lib.report(CODE)
    asked = []

    async def down(code, cache_for=None):
        asked.append(code)
        raise WCLError("timed out")
    monkeypatch.setattr(live_wcl, "report_fights", down)
    assert await lib.report(CODE, max_age_s=0) is rep  # the copy from moments ago
    assert await lib.report(CODE) is rep and len(asked) == 1  # not tried again on every call


async def test_the_report_asked_for_longest_ago_is_let_go(night, monkeypatch):
    monkeypatch.setattr(library, "KEPT_REPORTS", 2)
    lib = library.Library(default_report="DEFAULTAAAAAAAAA", wcl=FakeWCL(night).client())
    for code in ("DEFAULTAAAAAAAAA", "AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB", "CCCCCCCCCCCCCCCC"):
        await lib.report(code)
    assert list(lib._reports) == ["DEFAULTAAAAAAAAA", "CCCCCCCCCCCCCCCC"]  # the default is kept


async def test_a_report_loaded_live_and_not_asked_for_in_a_day_is_let_go(live_wcl, monkeypatch):
    monkeypatch.setattr(library, "KEPT_REPORTS", 1)
    lib = library.Library(wcl=live_wcl.client())
    await lib.report("AAAAAAAAAAAAAAAA")
    await lib.report("BBBBBBBBBBBBBBBB")
    assert list(lib._reports) == ["AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB"]  # still being logged: kept
    lib._asked_at["AAAAAAAAAAAAAAAA"] -= library.IDLE_S + 1
    await lib.report("BBBBBBBBBBBBBBBB")
    assert list(lib._reports) == ["BBBBBBBBBBBBBBBB"]


async def test_a_report_quiet_through_a_break_between_bosses_is_still_refreshed(night):
    """A 36-minute break before the next boss: the report looks finished, but the next boss's pulls show up."""
    wcl = FakeWCL(night, time.time() * 1000 - 36 * 60 * 1000)
    wcl.visible -= 1
    lib = library.Library(wcl=wcl.client())
    rep = await lib.report(CODE)
    assert rep.final and not rep.settled and len(rep.fights) == 34

    wcl.visible += 1  # raiding again
    assert await lib.report(CODE, max_age_s=0) is rep and len(rep.fights) == 35


async def test_the_pull_that_just_ended_is_waited_for(live_wcl, monkeypatch):
    """"How did the last pull go?" a few seconds after it ended: the live log uploads it a little later."""
    live_wcl.visible -= 1
    lib = library.Library(wcl=live_wcl.client())
    monkeypatch.setattr(server.DEFAULT, "library", lib)
    monkeypatch.setattr(server, "PULL_WAIT_S", 0)
    rep = await lib.report(CODE)
    boss = rep.boss_key(rep.fights[-1])
    asked = {"report": CODE, "boss": boss, "pull": len(rep.by_boss[boss]) + 1}

    with pytest.raises(ToolError, match="isn't in the log yet .*ask again in a minute"):
        await server.mcp.call_tool("pull_summary", asked)  # it never shows up: say so, after waiting

    refresh = rep.refresh

    async def uploaded(*_):
        live_wcl.visible = len(live_wcl.night.fights)  # the next pull ends
        await refresh()
    monkeypatch.setattr(rep, "refresh", uploaded)
    text, failed = await server.call("pull_summary", asked)
    assert not failed and f"pull {asked['pull']}" in text

    with pytest.raises(ToolError, match="has pulls 1-"):  # further on, there's nothing to wait for
        await server.mcp.call_tool("pull_summary", {**asked, "pull": asked["pull"] + 2})


async def test_the_latest_raid_skips_a_side_log_and_a_pug():
    class Library:
        async def report(self, code):
            async def guild_run_doubt():
                return "only 1 of its 20 players are in the guild" if code == "PUG" else ""
            return SimpleNamespace(guild_run_doubt=guild_run_doubt)

    lib = cast(library.Library, Library())
    reports = [{"code": "OLDER", "startTime": 1, "pulls": 30}, {"code": "SIDE", "startTime": 4, "pulls": 1},
               {"code": "RAID", "startTime": 2, "pulls": 9}, {"code": "PUG", "startTime": 3, "pulls": 12}]
    assert await library.latest_raid(lib, reports) == "RAID"  # newer: a one-pull side log, a raider's pug
    assert await library.latest_raid(lib, reports[1:2]) == "SIDE"  # nothing bigger: the newest
    assert await library.latest_raid(lib, reports[3:]) == "PUG"  # nothing else: it'll do, and the context line says why
    assert await library.latest_raid(lib, []) is None


async def test_the_latest_raid_is_the_newest_on_the_guilds_hardest_difficulty():
    class Library:
        async def report(self, code):
            async def guild_run_doubt():
                return "only 9 of its 20 players are in the guild" if code == "MYTHIC_PUG" else ""
            return SimpleNamespace(guild_run_doubt=guild_run_doubt)

    lib = cast(library.Library, Library())
    reports = [{"code": "OLDER", "startTime": 1, "pulls": 39, "pulls_by_difficulty": {5: 39}},
               {"code": "MYTHIC", "startTime": 2, "pulls": 34, "pulls_by_difficulty": {5: 34}},
               {"code": "HEROIC", "startTime": 3, "pulls": 13, "pulls_by_difficulty": {4: 13}},
               {"code": "ONE_MYTHIC", "startTime": 4, "pulls": 14, "pulls_by_difficulty": {4: 13, 5: 1}}]
    assert await library.latest_raid(lib, reports) == "MYTHIC"  # newer: a Heroic run after it, with one Mythic try
    assert await library.latest_raid(lib, reports[2:]) == "ONE_MYTHIC"  # only Heroic ones: the newest
    pug = {"code": "MYTHIC_PUG", "startTime": 5, "pulls": 9, "pulls_by_difficulty": {5: 9}}
    assert await library.latest_raid(lib, [pug, *reports[2:]]) == "ONE_MYTHIC"  # a raider's Mythic pug isn't it


async def test_the_latest_raid_asked_for_at_once_is_looked_up_once(monkeypatch):
    asked = []

    async def guild_reports(guild_id, since, pulls=False):
        asked.append(guild_id)
        await asyncio.sleep(0.01)
        return [{"code": "RAID", "startTime": 1, "pulls": 9}]

    async def latest_raid(lib, reports):
        return reports[0]["code"]
    monkeypatch.setattr(library, "latest_raid", latest_raid)
    raids = library.LatestRaids(cast(library.Library, SimpleNamespace(wcl=SimpleNamespace(
        guild_reports=guild_reports))), 60)
    assert await asyncio.gather(raids.of([1]), raids.of([1]), raids.of([1])) == ["RAID"] * 3
    assert asked == [1]
