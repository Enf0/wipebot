from tests.support import CODE
from wipebot.core.server import Library
from wipebot.discord.follower import BACKLOG_S, Follower
from wipebot.discord.store import Store


def make(live_wcl, test_report=None):
    library = Library(wcl=live_wcl.client())
    return live_wcl, Follower(library, Store(":memory:"), 14169, test_report)


async def test_backlog_is_skipped_on_lock(night, live_wcl):
    wcl, follower = make(live_wcl)
    pulls = await follower.poll()
    # Only pulls that ended within BACKLOG_S of now are answered when the bot picks up a report.
    recent = [f["id"] for f in night.fights if wcl.now_ms - (wcl.start + f["endTime"]) <= BACKLOG_S * 1000]
    assert [p.fight for p in pulls] == recent


async def test_pulls_not_done_come_again(night, live_wcl):
    wcl, follower = make(live_wcl)
    first = await follower.poll()
    assert first
    again = await follower.poll()  # nothing was marked done
    assert [p.fight for p in again] == [p.fight for p in first]
    for p in again:
        follower.done(p)
    assert await follower.poll() == []


async def test_new_pull_comes_once(night, live_wcl):
    wcl, follower = make(live_wcl)
    wcl.visible -= 1
    for p in await follower.poll():
        follower.done(p)
    wcl.visible += 1
    pulls = await follower.poll()
    assert [p.fight for p in pulls] == [night.fights[-1]["id"]]


async def test_in_progress_pull_waits(night, live_wcl):
    wcl, follower = make(live_wcl)
    for p in await follower.poll():
        follower.done(p)
    night_fights = night.fights
    live = {**night_fights[-1], "id": 999, "inProgress": True}
    night.fights = [*night_fights, live]
    wcl.visible += 1
    try:
        assert await follower.poll() == []
    finally:
        night.fights = night_fights


async def test_keeps_current_report_while_live(night, live_wcl):
    wcl, follower = make(live_wcl)
    await follower.poll()
    # A second raider starts logging the same night; their report is newer but we stay put.
    wcl.other_reports = [{"code": "OTHER", "startTime": wcl.start, "endTime": wcl.now_ms}]
    follower._last_report_check = 0
    await follower.poll()
    assert follower.code == CODE


async def test_only_wipes_are_answered(night, live_wcl):
    wcl, follower = make(live_wcl, test_report=CODE)
    pulls = {}
    for _ in night.fights:
        for p in await follower.poll():
            pulls[p.fight] = p
            follower.done(p)
    assert pulls[21].wipe and (pulls[21].boss, pulls[21].pull_no) == ("Sszorak", 13)
    assert pulls[17].reset and not pulls[17].wipe  # Sszorak pull 9, the boss untouched
    kills = [p for p in pulls.values() if p.kill]
    assert kills and not any(p.wipe for p in kills)


async def test_test_mode_gives_one_pull_per_poll_from_the_start(night, live_wcl):
    wcl, follower = make(live_wcl, test_report=CODE)
    for expected in night.fights[:3]:
        pulls = await follower.poll()
        assert [p.fight for p in pulls] == [expected["id"]]
        follower.done(pulls[0])


async def test_a_pug_logged_to_the_guilds_page_is_not_followed(night, live_wcl, monkeypatch):
    wcl, follower = make(live_wcl)
    fights = wcl.report_fights

    async def report_fights(code, cache_for=None):  # the guild's report, by id
        return {**await fights(code), "guild": {"id": 14169, "name": "The Copper Lanterns"}}

    async def guild_members(guild_id):  # a raider's pug: one guild member among strangers
        return {next(iter(night.players.values()))}
    monkeypatch.setattr(wcl, "report_fights", report_fights)
    monkeypatch.setattr(wcl, "guild_members", guild_members)
    assert await follower.poll() == [] and follower.code is None

    async def everyone(guild_id):
        return set(night.players.values())
    monkeypatch.setattr(wcl, "guild_members", everyone)
    follower._last_report_check = 0  # the next check: now it's the guild's
    assert await follower.poll() and follower.code == CODE


async def test_with_nothing_live_a_look_is_not_repeated_within_the_check_interval(night, live_wcl, monkeypatch):
    """Questions and requests ask for the live report too: each look costs a Warcraft Logs database point."""
    wcl, follower = make(live_wcl)
    looks = []

    async def pick(now_ms):
        looks.append(now_ms)
    monkeypatch.setattr(follower, "_pick_report", pick)
    assert await follower.live() is None and await follower.live() is None and len(looks) == 1
    follower._last_report_check = 0  # REPORT_CHECK_S later
    await follower.live()
    assert len(looks) == 2


async def test_two_servers_following_one_report_each_get_its_pulls(night, live_wcl):
    """A guild's raid server and its officers' server, or two guilds' servers on a shared log: a pull handled for one
    is still new to the other."""
    library, store = Library(wcl=live_wcl.client()), Store(":memory:")
    raid, officers = Follower(library, store, 14169, server_id=1001), Follower(library, store, 14169, server_id=2002)
    first = await raid.poll()
    assert first
    for p in first:
        raid.done(p)
    assert await raid.poll() == []
    assert [p.fight for p in await officers.poll()] == [p.fight for p in first]  # not handled for this one yet
    for p in first:
        officers.done(p)
    assert await officers.poll() == []


def test_pulls_handled_before_servers_had_their_own_stay_handled_for_the_servers_following_them(tmp_path):
    """A database from before each server had its own kept each pull once: after the move, a restart during a raid
    posts none of them again where the report is followed, and a server that starts following it later has its own."""
    import sqlite3
    path = tmp_path / "wipebot.db"
    Store(path)._db.close()
    old = sqlite3.connect(path)
    old.executescript("""DROP TABLE posted;
        CREATE TABLE posted (report TEXT NOT NULL, fight INTEGER NOT NULL, posted_at REAL NOT NULL, latency_s REAL,
                             PRIMARY KEY (report, fight));
        INSERT INTO posted VALUES ('LIVE1', 30, 1000.0, 45.0), ('LASTWEEK', 12, 500.0, NULL);
        INSERT INTO following VALUES (1001, 20, 7, 'Rowan', NULL, 'LIVE1', 20, 'who died first', 900.0),
                                     (1001, 7, 7, 'Rowan', 'Torvek', 'LIVE1', NULL, '', 950.0);""")
    old.commit()
    old.close()
    store = Store(path)
    assert store.is_posted("LIVE1", 30, 1001) and not store.is_posted("LIVE1", 30, 2002)
    assert not store.is_posted("LIVE1", 31, 1001) and store.is_posted("LASTWEEK", 12)  # kept, for no server
    assert store._db.execute("SELECT discord_guild_id, report, fight, latency_s FROM posted ORDER BY posted_at"
                             ).fetchall() == [(0, "LASTWEEK", 12, None), (1001, "LIVE1", 30, 45.0)]
    assert Store(path).is_posted("LIVE1", 30, 1001)  # opened again: nothing left to move
