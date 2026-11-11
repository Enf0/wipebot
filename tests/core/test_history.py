"""Raiders over the season (report/history.py): the guild's ranking per role, and one player's ranked kills."""
import re

import pytest

from wipebot.core.report import FactError, Report, history

GUILD = "The Copper Lanterns"


def heals(class_name: str, spec: str) -> bool:
    return spec == "Restoration"


async def test_guild_rankings_rank_a_roles_raiders_by_their_average_best_kill(rep, patch_wcl):
    rep.zone_id = 53
    first, second, third = (rep.player(n) for n in sorted(p.name for p in rep.players.values())[:3])
    asked = {}

    def zone_rankings(character_ids, zone_id, difficulty, metric, spec=None, role="Any"):
        asked["args"] = (character_ids, zone_id, difficulty, metric, role)
        return {1: {"bestPerformanceAverage": 61.0, "medianPerformanceAverage": 50.0,
                    "rankings": [{"encounter": {"name": "Sszorak"}, "rankPercent": 61.0, "spec": "Restoration"}]},
                2: {"bestPerformanceAverage": 92.3, "medianPerformanceAverage": 88.0,
                    "rankings": [{"encounter": {"name": "Sszorak"}, "rankPercent": 92.3, "spec": "Elemental"},
                                 {"encounter": {"name": "Vashnik the Malignant"}, "rankPercent": None}]},
                3: {"rankings": []}}
    patch_wcl(rep, ranked_characters={first.name: 1, second.name: 2, third.name: 3}, zone_rankings=zone_rankings,
              zone_encounters=("The Venomous Abyss", []))
    lines = (await history.guild_rankings(rep, "heroic", "healer")).splitlines()
    assert asked["args"] == ([1, 2, 3], 53, 4, "hps", "Healer")  # Warcraft Logs keeps each role's own specs
    assert lines[1].startswith(f"1. {second.name} (Restoration Shaman): average of each boss's best kill 92.3")
    assert lines[1].endswith("Best per boss: Sszorak 92 as Elemental")
    assert lines[2].startswith(f"2. {first.name} ")
    assert lines[-1] == f"No ranked Heroic kill as a healer: {third.name}"
    with pytest.raises(FactError, match="no role"):
        await history.guild_rankings(rep, "Mythic", "dog")


async def test_player_history_lists_every_ranked_kill_newest_first(rep, patch_wcl, fake_knowledge):
    p = next(iter(rep.players.values()))
    p.server, p.region = "ArgentDawn", "EU"
    rep.zone_id, rep.guild_name = 53, GUILD
    asked = {}

    def find_character(name, realm, region):
        asked["character"] = (name, realm, region)
        return {"id": 7, "server": {"name": "Argent Dawn", "slug": "argent-dawn"}}

    def kill(day, pct, guild=GUILD, spec="Restoration"):
        return {"startTime": 1791000000000 + day * 86400000, "rankPercent": pct, "amount": 200000, "duration": 392629,
                "bracketData": 326, "spec": spec, "guild": {"name": guild},
                "report": {"code": f"R{day}", "fightID": 2}}

    def encounter_rankings(character_id, encounters, difficulties, metric, zone_id=None):
        asked.setdefault("metrics", set()).add(metric)
        asked["rankings"] = (character_id, encounters)
        # Warcraft Logs ranks every kill by the metric asked for, whatever the spec.
        if metric == "hps":
            return {(3455, 5): {"ranks": [kill(1, 27.0), kill(3, 6.0), kill(2, 16.0, spec="Elemental")]},
                    (3455, 4): {"ranks": [kill(0, 3.0, guild=None, spec="Elemental")]}}
        return {(3455, 5): {"ranks": [kill(1, 78.0), kill(3, 70.0), kill(2, 86.0, spec="Elemental")]},
                (3455, 4): {"ranks": [kill(0, 88.0, guild=None, spec="Elemental")]}}

    def zone_rankings(character_ids, zone_id, difficulty, metric, spec=None, role="Any"):
        asked.setdefault("summaries", []).append((difficulty, spec, metric))
        return {7: {"bestPerformanceAverage": 86.8, "medianPerformanceAverage": 70.1,
                    "rankings": [{"encounter": {"name": "Vashnik the Malignant"}, "rankPercent": 89.0, "spec": spec}],
                    "allStars": [{"spec": spec, "points": 501.7, "rankPercent": 87.0}]}}

    patch_wcl(rep, find_character=find_character, encounter_rankings=encounter_rankings, zone_rankings=zone_rankings,
              zone_encounters=("The Venomous Abyss", [{"id": 3455, "name": "Vashnik the Malignant"},
                                                      {"id": 3420, "name": "Sszorak"}]))
    fake_knowledge(heals=heals)
    lines = (await history.ranked_kills(rep, p.name)).splitlines()
    assert asked["character"] == (p.name, "ArgentDawn", "eu")
    assert asked["rankings"] == (7, [3455, 3420]) and asked["metrics"] == {"dps", "hps"}
    # Each kill by the spec played: healing for Restoration, damage for Elemental. The rows run newest first, so the
    # line over them gives the first kill's and the newest: read upwards, a boss that fell was said to have risen.
    resto = lines.index("Vashnik the Malignant, Mythic, as Restoration (hps): 2 kills, best 27%, median 16%, first "
                        "27%, newest 6%")
    assert " 6% " in lines[resto + 1] and "R3#fight=2" in lines[resto + 1]  # newest first
    assert "Vashnik the Malignant, Mythic, as Elemental (dps): 1 kill, best 86%, median 86%" in lines
    # Warcraft Logs' own summary, per difficulty and spec played, each by its spec's metric
    assert sorted(asked["summaries"]) == [(4, "Elemental", "dps"), (5, "Elemental", "dps"), (5, "Restoration", "hps")]
    assert ("Warcraft Logs' summary, Mythic as Elemental (dps; as on the character page): average of each boss's "
            "best kill 86.8 over 1 boss, of each boss's median kill 70.1; All Stars 502 points as Elemental (87th "
            "percentile of the spec)") in lines
    # Over the whole raid a boss's kills are listed on the hardest difficulty it was killed on, and the easier ones
    # counted: Normal and Heroic farm kills listed like the Mythic ones made 60 rows of a page.
    heroic = lines.index("Vashnik the Malignant, Heroic, as Elemental (dps): 1 kill, best 88%, median 88%")
    assert not lines[heroic + 1].startswith("  ") and not any("in a pug" in line for line in lines)
    assert "the easier ones are counted" in lines[0]
    assert lines[-1] == "No ranked kill (not killed, or not logged): Sszorak"
    assert "argent-dawn" in lines[0]

    lines = (await history.ranked_kills(rep, p.name, boss="vashnik")).splitlines()
    assert asked["rankings"][1] == [3455]
    assert any("88%" in line and "in a pug" in line for line in lines) and "the easier ones" not in lines[0]
    with pytest.raises(FactError, match="no boss matching"):
        await history.ranked_kills(rep, p.name, boss="Ragnaros")


def test_all_stars_name_their_partition_and_a_low_standing_is_a_percentile():
    # Two All Stars entries "as Restoration" stood side by side, one per patch's rankings, with nothing to tell
    # which is current; and "top 82% of the spec" is the bottom fifth, phrased as praise.
    z = {"bestPerformanceAverage": 87.2, "medianPerformanceAverage": 79.4,
         "rankings": [{"rankPercent": 90.0}, {"rankPercent": 80.0}],
         "allStars": [{"partition": 1, "spec": "Restoration", "points": 926.4, "rankPercent": 98.4},
                      {"partition": 2, "spec": "Restoration", "points": 60.2, "rankPercent": 17.6}]}
    summary = history._zone_summary(z)
    assert summary == (
        "average of each boss's best kill 87.2 over 2 bosses, of each boss's median kill 79.4; All Stars 60 points "
        "as Restoration (18th percentile of the spec; partition 2, the newest), 926 points as Restoration (98th "
        "percentile of the spec; partition 1)")
    # What a partition is, is said once for a list of summaries, and only where one is named
    assert history._partitions_note([summary, summary]) == [history.PARTITIONS]
    assert history._partitions_note([history._zone_summary({**z, "allStars": z["allStars"][:1]})]) == []


async def test_a_players_deaths_count_a_night_logged_twice_once(rep):
    name = next(p.name for p in rep.players.values())
    one = await history.deaths_by_night([(rep.start_ms, [rep])], name, 5)
    pulls = int(re.findall(r": (\d+) pulls on ", one)[0])
    assert pulls > 1 and len(one.splitlines()) == 2
    twin = Report(rep.wcl, "TWIN")  # the other raider's log of the same pulls, its clock seconds off
    await twin.load()
    twin.start_ms = rep.start_ms + 4_000
    assert await history.deaths_by_night([(rep.start_ms, [rep, twin])], name, 5) == one
    assert await history.deaths_by_night([(rep.start_ms, [rep, rep])], name, 5) == one  # the log listed twice
    twin.start_ms = rep.end_ms + 600_000  # a second log of the evening, of pulls after the first's: each counts
    both = await history.deaths_by_night([(rep.start_ms, [rep, twin])], name, 5)
    assert re.findall(r": (\d+) pulls on ", both) == [str(2 * pulls)]
    # the night's first log doesn't have the player (it began before they joined): the second log's pulls count
    twin.start_ms = rep.start_ms - 4_000
    twin.players = {i: p for i, p in twin.players.items() if p.name != name}
    with pytest.raises(FactError):
        twin.player(name)
    assert await history.deaths_by_night([(rep.start_ms, [twin, rep])], name, 5) == one
