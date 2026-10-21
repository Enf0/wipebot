"""What a report asks Warcraft Logs (report/fetch.py): batched into one query where it can be, and remembered
when it's about finished pulls."""
import time
from typing import cast

from tests.support import CODE, FakeWCL, Night
from wipebot.core import server
from wipebot.core.report import coaching
from wipebot.core.report.fetch import Fetched
from wipebot.core.wcl import WCL


async def test_events_for_several_items_come_in_one_query_a_pull_twice_included(rep):
    wcl = cast(FakeWCL, rep.wcl)
    f13, f14 = rep.fight(13, "Sszorak"), rep.fight(14, "Sszorak")
    wcl.queries.clear()
    got = await rep.events_for([(f13["id"], {"endTime": 1}), (f14["id"], {}), (f13["id"], {"endTime": 2})], "Deaths")
    assert wcl.queries[0] == "events_many Deaths" and "events_many" not in wcl.queries[1:]  # one query
    assert [len(g) for g in got] == [len(await rep.deaths(f13)), len(await rep.deaths(f14)), len(await rep.deaths(f13))]
    wcl.queries.clear()
    assert await rep.events(f14["id"], "Deaths") == got[1]  # remembered under the single-pull key
    assert wcl.queries == []


async def test_a_nights_tables_and_details_come_in_one_query_each_and_are_remembered(rep):
    wcl = cast(FakeWCL, rep.wcl)
    wcl.queries.clear()
    await coaching.player_night(rep, "Enfo")
    assert wcl.queries.count("tables Healing") == 1 and wcl.queries.count("player_details_many") == 1
    assert "tables DamageDone" not in wcl.queries  # a healer: no damage tables
    wcl.queries.clear()
    fight = rep.fight(13, "Sszorak")
    await rep.table(fight["id"], "Healing")  # one pull of it: remembered from the night's query
    await rep.details(fight)
    await coaching.player_night(rep, "Enfo")
    assert wcl.queries == []


async def test_a_pull_in_progress_is_not_remembered():
    night = Night()  # changed below: not the session's
    live = {**night.fights[-1], "id": 999, "inProgress": True}
    night.fights.append(live)
    night.deaths[999] = []
    rep = await server.Library(wcl=FakeWCL(night, time.time() * 1000).client()).report(CODE)
    assert await rep.deaths(live) == []
    night.deaths[999] = night.deaths[night.fights[-2]["id"]]
    assert await rep.deaths(live) == night.deaths[999]  # read again: the pull is still going


async def test_a_pull_that_ends_during_its_fetch_is_not_remembered():
    fetched = Fetched(cast(WCL, None), CODE)
    fetched.live = {7}
    calls = []

    async def fetch():
        calls.append(1)
        fetched.live = set()  # the pull ended meanwhile: the report was read again
        return "half done"
    assert await fetched.keep(("k",), [7], fetch) == "half done"
    assert await fetched.keep(("k",), [7], fetch) == "half done"
    assert len(calls) == 2  # fetched again, now that the pull is finished
