"""The captured night: a report's fights, players, deaths and the bosses' buffs
(fixtures/real/<code>.json), and a fake Warcraft Logs that serves it, finished or playing live. Tests use it where
recorded answers can't serve: a live report growing pull by pull, and counting the queries a report asks.

`uv run python -m tests.support.night CODE` captures a report."""
import asyncio
import json
import sys
from typing import cast

from wipebot.core.wcl import WCL, boss_ids

from .paths import CODE, REAL

# What Lorrgs lists for a Restoration Shaman, as every FakeWCL player is: a defensive and a raid cooldown.
RESTO_SPELLS = [
    {"spell_id": 108271, "name": "Astral Shift", "cooldown": 90.0, "tags": ["defensive"], "spell_type": "shaman"},
    {"spell_id": 98008, "name": "Spirit Link Totem", "cooldown": 180.0, "tags": ["raid_cd"],
     "spell_type": "shaman-restoration"},
]


class Night:
    """A captured report (`capture`), with JSON's string keys turned back into ints."""

    def __init__(self, code: str = CODE):
        raw = json.loads((REAL / f"{code}.json").read_text(encoding="utf-8"))
        self.report = raw["report"]
        self.fights = sorted(self.report["fights"], key=lambda f: f["id"])
        self.players = {int(k): v for k, v in raw["players"].items()}
        self.abilities = {int(k): v for k, v in raw["abilities"].items()}
        self.deaths = {int(k): v for k, v in raw["deaths"].items()}
        self.bosses = set(raw["bosses"])
        self.boss_buffs = {int(k): v for k, v in raw["boss_buffs"].items()}


class FakeWCL:
    """Serves what the captured night has: fights, players, deaths and the bosses' buffs. Every player is a
    Restoration Shaman; tables, graphs, rankings and other events are empty. `queries` lists what was asked.

    With `now_ms`, the night plays live: the report is shifted so that `now_ms` falls just after pull `visible`
    ends, and only the pulls up to `visible` are in it."""

    def __init__(self, night: Night, now_ms: float | None = None):
        self.night = night
        self.now_ms = now_ms
        self.start = now_ms - night.fights[-1]["endTime"] - 1000 if now_ms else night.report["startTime"]
        self.visible = len(night.fights)
        self.other_reports: list[dict] = []
        self.queries: list[str] = []  # what was asked for, by method name

    def _fights(self) -> list[dict]:
        return self.night.fights[: self.visible]

    def client(self) -> WCL:
        return cast(WCL, self)

    async def guild_reports(self, guild_id, since_ms, pulls=False, cache_for=None):
        end = self.start + self._fights()[-1]["endTime"]
        return [{"code": CODE, "startTime": self.start, "endTime": end, "pulls": len(self._fights())},
                *self.other_reports]

    async def rankings(self, code, fight_id, metric):
        return []  # not captured: no parses

    async def guild_progress(self, guild_id, zone_id):
        return {}

    async def ensure_points(self, need, what):
        pass  # never short of points

    async def talent_code(self, code, fight_id, actor_id):
        return ""

    async def guild_members(self, guild_id):
        return set(self.night.players.values())  # everyone in the captured night is in the guild

    async def report_fights(self, code, cache_for=None):
        self.queries.append("report_fights")
        fights = [dict(f) for f in self._fights()]
        end = self.start + fights[-1]["endTime"] if self.now_ms else self.night.report["endTime"]
        return dict(self.night.report, startTime=self.start, endTime=end, fights=fights,
                    zone={"name": "The Venomous Abyss"})

    async def actors(self, code, cache_for=None):
        self.queries.append("actors")
        players = {i: {"id": i, "name": n, "type": "Player"} for i, n in self.night.players.items()}
        return players | {i: {"id": i, "name": "Boss", "type": "NPC", "subType": "Boss"} for i in self.night.bosses}

    async def ability_table(self, code, cache_for=None):
        self.queries.append("ability_table")
        return {i: (n, "ability_icon.jpg") for i, n in self.night.abilities.items()}

    async def player_details(self, code, start, end, combatant=False, cache_for=None):
        """The whole night: everyone. One pull: the players who died in it (19 of 20 on pull 13)."""
        self.queries.append("player_details")
        ids = set(self.night.players)
        fight = next((f for f in self.night.fights if f["startTime"] == start), None)
        if fight:
            ids = {d["targetID"] for d in self.night.deaths[fight["id"]]}
        return {"healers": [{"id": i, "name": n, "type": "Shaman", "specs": [{"spec": "Restoration", "count": 1}]}
                            for i, n in self.night.players.items() if i in ids]}

    async def events(self, code, fight_id, data_type, **filters):
        self.queries.append(f"events {data_type}")
        ids = fight_id if isinstance(fight_id, list) else [fight_id]
        if data_type == "Buffs" and filters.get("hostilityType") == "Enemies":  # only the bosses' were captured
            return [e for i in ids for e in self.night.boss_buffs.get(i, [])]
        if data_type != "Deaths" or filters.get("hostilityType") == "Enemies":
            return []
        return [d for i in ids for d in self.night.deaths.get(i, [])]

    async def boss_buffs(self, code, fight_id, bosses):
        self.queries.append("boss_buffs")
        ids = fight_id if isinstance(fight_id, list) else [fight_id]
        return [e for i in ids for e in self.night.boss_buffs.get(i, [])]

    async def graph(self, code, fight, data_type, **filters):
        return {"series": []}

    async def table(self, code, fight_id, data_type, **filters):
        self.queries.append(f"table {data_type}")
        return {"entries": []}

    async def tables(self, code, items, data_type, **filters):
        self.queries.append(f"tables {data_type}")
        return {name: await self.table(code, fid, data_type, **filters, **own) for name, fid, own in items}

    async def events_many(self, code, fights, data_type, **filters):
        self.queries.append(f"events_many {data_type}")
        return [await self.events(code, fid, data_type, **filters, **own) for fid, own in fights]

    async def player_details_many(self, code, fights, combatant=True):
        self.queries.append("player_details_many")
        return {f["id"]: await self.player_details(code, f["startTime"], f["endTime"], combatant) for f in fights}


async def capture(code: str) -> None:
    """Save a real report's fights, players, deaths and boss buffs as fixtures/real/<code>.json."""
    wcl = WCL()
    report = await wcl.report_fights(code)
    actors = await wcl.actors(code)
    raiders = {i for f in report["fights"] for i in f["friendlyPlayers"]}  # not everyone the log happened to see
    players = {i: a["name"] for i, a in actors.items() if a["type"] == "Player" and i in raiders}
    abilities = {i: name for i, (name, _icon) in (await wcl.ability_table(code)).items()}
    bosses = boss_ids(actors)
    deaths = {f["id"]: await wcl.events(code, f["id"], "Deaths") for f in report["fights"]}
    buffs = {f["id"]: await wcl.boss_buffs(code, f["id"], bosses) for f in report["fights"]}

    # The full ability table is ~1 MB; keep only the names the deaths and boss buffs refer to.
    used = ({e.get("killingAbilityGameID") for events in deaths.values() for e in events}
            | {e["abilityGameID"] for events in buffs.values() for e in events})
    data = {"report": report, "players": players, "bosses": sorted(bosses),
            "abilities": {k: v for k, v in abilities.items() if k in used}, "deaths": deaths, "boss_buffs": buffs}
    path = REAL / f"{code}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(capture(sys.argv[1]))
