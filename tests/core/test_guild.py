"""A guild's reports (report/guild.py, find_reports), on a made-up night: the real listing is pinned by the tool
cases (test_tools.py)."""
import time

from wipebot.core import wcl as wcl_module
from wipebot.core.report import guild

NOW = time.time() * 1000


def fight(name: str, difficulty: int, kill: bool = False) -> dict:
    return {"name": name, "difficulty": difficulty, "kill": kill, "friendlyPlayers": [1]}


class Logs(wcl_module.WCL):
    """Warcraft Logs with one report of The Copper Lanterns, holding `fights`."""

    def __init__(self, fights: list[dict]):
        self.fights = fights

    async def query(self, q, *a, **k):
        return {"reportData": {"reports": {"has_more_pages": False, "data": [{
            "code": "AbCdEfGh12345678", "title": "raid night", "startTime": NOW - 3_600_000, "endTime": NOW,
            "masterData": {"actors": [{"id": 1, "name": "Torvek"}]}, "fights": list(self.fights)}]}}}

    async def find_guild(self, name, realm, region):
        return {"id": 1, "name": "The Copper Lanterns", "server": {"name": "Kazzak"}}

    async def guild_members(self, guild_id):
        return {"Torvek"}


async def listed(fights: list[dict]) -> str:
    text = await guild.guild_reports(Logs(fights), 7, NOW - 7 * 86_400_000, "The Copper Lanterns", "Kazzak", "eu")
    return text.splitlines()[1].split(" UTC: ", 1)[1]


async def test_a_boss_killed_on_heroic_is_not_killed_on_mythic():
    """A Heroic kill and 31 Mythic wipes of one boss were added up as "33 (killed)" under "Heroic 2, Mythic 32"."""
    night = ([fight("Torvek's Forge", 4), fight("Torvek's Forge", 4, kill=True)]
             + [fight("Torvek's Forge", 5) for _ in range(31)] + [fight("The Slag Pit", 5)])
    assert await listed(night) == ("boss pulls Heroic 2: Torvek's Forge 2 (1 kill); Mythic 32: Torvek's Forge 31, "
                                   "The Slag Pit 1")


async def test_a_dungeon_in_a_raid_log_is_not_a_boss_of_the_raid():
    """A dungeon's fights have no raid difficulty: they aren't counted in the pulls, so they aren't listed as one."""
    night = [fight("Torvek's Forge", 4, kill=True), fight("The Ember Halls", 10, kill=True),
             fight("The Ember Halls", 10)]
    assert await listed(night) == "boss pulls Heroic 1: Torvek's Forge 1 (1 kill); other content: The Ember Halls"
    assert await listed([fight("The Ember Halls", 10)]) == "no boss pulls; other content: The Ember Halls"


async def test_a_boss_killed_twice_says_so():
    twice = [fight("Torvek's Forge", 3, kill=True)] * 2
    assert await listed(twice) == "boss pulls Normal 2: Torvek's Forge 2 (2 kills)"
