"""The guild's nights (report/night.py): who raided which nights. The night's overview and progress are pinned by
the tool cases (test_tools.py)."""
from wipebot.core.report import night


async def test_kill_rankings_are_kept_once_every_kill_is_ranked(rep, patch_wcl):
    kills = [f["id"] for f in rep.fights if f["kill"]]
    assert kills
    asked = []
    ranked = {"speed": {"rankPercent": 40}, "execution": {"rankPercent": 60}}

    def rankings(code, fight_id, metric):
        asked.append(fight_id)
        return [{"fightID": i, **(ranked if len(asked) > 1 or i != kills[0] else {})} for i in kills]
    patch_wcl(rep, rankings=rankings)
    every = {i: {"speed": 40, "execution": 60} for i in kills}
    assert await night._kill_rankings(rep) == {i: v for i, v in every.items() if i != kills[0]}  # one not yet
    assert await night._kill_rankings(rep) == every  # asked again
    assert await night._kill_rankings(rep) == every
    assert len(asked) == 2


async def test_attendance_counts_a_night_once_and_leaves_out_other_difficulties(rep, patch_wcl):
    rep.guild_id, rep.zone_id, rep.guild_name = 14169, 53, "The Copper Lanterns"
    hour = 3600 * 1000
    t = rep.end_ms - 2 * 86400 * 1000
    logs = [{"code": "A", "startTime": t,
             "players": [{"name": "Torvek", "presence": 1}, {"name": "Mirelle", "presence": 2}]},
            {"code": "B", "startTime": t + hour, "players": [{"name": "Mirelle", "presence": 1}]},  # same night
            {"code": "ALT", "startTime": t + 26 * hour, "players": [{"name": "Torvek", "presence": 1}]},  # Heroic
            {"code": "C", "startTime": t + 48 * hour, "players": [{"name": "Torvek", "presence": 1},
                                                                   {"name": "Brightleaf", "presence": 2}]},
            # A raider's Mythic pug logged to the guild's page: mostly players from outside the guild.
            {"code": "PUG", "startTime": t + 30 * hour, "players": [{"name": "Torvek", "presence": 1},
                                                                     {"name": "Stranger", "presence": 1},
                                                                     {"name": "Other", "presence": 1}]}]
    reports = ([{"code": c, "pulls_by_difficulty": {5: 9}} for c in ("A", "B", "C", "PUG")]
               + [{"code": "ALT", "pulls_by_difficulty": {4: 9}}])
    patch_wcl(rep, attendance=logs, guild_reports=reports, guild_members={"Torvek", "Mirelle", "Brightleaf"})
    lines = (await night.attendance(rep, 2)).splitlines()
    assert "(2 nights;" in lines[0] and "Mythic raid nights" in lines[0]
    assert lines[1].endswith("2 players (A, B)")  # two logs of one evening; Mirelle there in one, benched in the other
    assert lines[-2].endswith("guild's page): PUG (1 of 3 in the guild)")
    assert lines[-1] == "Nights raided, of 2: Torvek 2, Mirelle 1, Brightleaf 0 (+1 benched)"
