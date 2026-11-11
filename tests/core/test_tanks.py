"""The tanks (report/tanks.py): the spells each took a boss's aggro with over the guild's raid nights."""
from wipebot.core.report import Report, tanks


async def test_the_spells_a_tank_took_a_boss_with_count_a_night_logged_twice_once(rep, monkeypatch):
    boss, tank = next(iter(rep.bosses)), next(p.name for p in rep.players.values())
    read: list[tuple[str, int]] = []

    async def tables(self, items, data_type, **_):  # one taunt a pull
        read.extend((self.code, fight) for fight, _ in items)
        return [{"threat": [{"name": tank, "targets": [{"id": boss, "bands": [
            {"startTime": 1000, "startEvent": {"ability": {"name": "Dark Command"}}}]}]}]} for _ in items]
    monkeypatch.setattr(Report, "tables", tables)
    one = await tanks.aggro_history([rep], [tank], 4)
    pulls = len(read)
    assert f"{tank}: Dark Command {pulls} (last " in one
    twin = Report(rep.wcl, "TWIN")  # the other raider's log of the same pulls, its clock seconds off
    await twin.load()
    twin.start_ms = rep.start_ms + 4_000
    del read[:]
    assert await tanks.aggro_history([rep, twin], [tank], 4) == one
    assert {code for code, _ in read} == {rep.code} and len(read) == pulls  # the second log's tables aren't read
