"""Raiders over the season (report/history.py): the guild's ranking per role, and one player's ranked kills."""
import pytest

from wipebot.core.report import FactError, history

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
    # Each kill by the spec played: healing for Restoration, damage for Elemental.
    resto = lines.index("Vashnik the Malignant, Mythic, as Restoration (hps): 2 kills, best 27%, median 16%")
    assert " 6% " in lines[resto + 1] and "R3#fight=2" in lines[resto + 1]  # newest first
    assert "Vashnik the Malignant, Mythic, as Elemental (dps): 1 kill, best 86%, median 86%" in lines
    # Warcraft Logs' own summary, per difficulty and spec played, each by its spec's metric
    assert sorted(asked["summaries"]) == [(4, "Elemental", "dps"), (5, "Elemental", "dps"), (5, "Restoration", "hps")]
    assert ("Warcraft Logs' summary, Mythic as Elemental (dps; as on the character page): average of each boss's "
            "best kill 86.8 over 1 boss, of each boss's median kill 70.1; All Stars 502 points as Elemental (top 13% "
            "of the spec)") in lines
    assert any("88%" in line and "pug" in line for line in lines)
    assert lines[-1] == "No ranked kill (not killed, or not logged): Sszorak"
    assert "argent-dawn" in lines[0]

    await history.ranked_kills(rep, p.name, boss="vashnik")
    assert asked["rankings"][1] == [3455]
    with pytest.raises(FactError, match="no boss matching"):
        await history.ranked_kills(rep, p.name, boss="Ragnaros")
