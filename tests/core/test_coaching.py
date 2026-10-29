"""One player's pull (report/coaching.py): the resource their spec spends, and where their time went."""
import time
from types import SimpleNamespace
from typing import cast

from tests.support import CODE, RESTO_SPELLS, FakeWCL, Night
from wipebot.core import server
from wipebot.core.pulls import Decided
from wipebot.core.report import Player, adds, build, coaching, deaths, healing, mechanics, tables


async def test_the_resource_judged_is_the_one_the_spec_spends(rep, patch_wcl, fake_knowledge):
    f = rep.fight(13)
    p = rep.player("Enfo")
    t0 = f["startTime"]
    # A cast carries the resource it spends: Lava Burst mana, Elemental Blast Maelstrom.
    casts = [{"abilityGameID": 51505, "timestamp": t0 + 1000, "classResources": [{"type": 0, "max": 260000}]},
             {"abilityGameID": 117014, "timestamp": t0 + 2000, "classResources": [{"type": 11, "max": 125}]}]
    asked, tables = [], []

    async def graph(code, fight, data_type, **filters):
        asked.append(filters["abilityID"])
        return {"series": [{"id": p.id, "data": [[t0, 100], [t0 + 10_000, 125], [t0 + 20_000, 10]]}]}

    async def table(code, fight_id, data_type, **filters):
        tables.append((data_type, filters["abilityID"]))
        return {"resources": [{"name": "Elemental Blast", "gains": 40, "wasted": 0},
                              {"name": "Lava Burst", "gains": 200, "wasted": 30}]}
    patch_wcl(rep, graph=graph, table=table)
    fake_knowledge(heals=lambda class_name, spec: spec == "Restoration")
    lines, _, _ = await coaching.resource_use(rep, f, p, casts, 100, f["endTime"])
    # A healer: mana; Maelstrom isn't built in units as a healer's Holy Power is, so it isn't theirs
    assert asked == [100] and len(lines) == 1 and lines[0].startswith("Mana over the pull: ")
    elemental = Player(p.id, p.name, p.class_name, "Elemental", "dps")
    lines, name, capped = await coaching.resource_use(rep, f, elemental, casts, 100, f["endTime"])
    assert asked == [100, 111] and tables == [("Resources", 111)]
    assert lines == ["Maelstrom at its cap (125) for 10% of their time alive (what it gained then was lost); longest "
                     "stretches: 0:10-0:20 (10 s).",
                     "Maelstrom gained 240, of it lost at the cap 30 (12%); by source (Warcraft Logs' names), lost "
                     "of gained: Lava Burst 30 of 200, Elemental Blast 0 of 40" + coaching.CAP_READING]
    assert (name, capped) == ("Maelstrom", [(t0 + 10_000, t0 + 20_000)])
    essence = Player(p.id, p.name, "Evoker", "Devastation", "dps")
    casts[1]["classResources"] = [{"type": 19, "max": 6}]
    lines, _, _ = await coaching.resource_use(rep, f, essence, casts, 100, f["endTime"])
    assert lines[0].startswith("Essence at its cap (6)")


async def test_a_healer_who_spends_holy_power_is_judged_on_mana_and_on_holy_power(rep, patch_wcl, fake_knowledge):
    f = rep.fight(13)
    p = rep.player("Enfo")
    holy = Player(p.id, p.name, "Paladin", "Holy", "healer")
    t0 = f["startTime"]
    # Holy Shock spends mana, Eternal Flame Holy Power; a few Holy Power spenders among many mana casts
    casts = ([{"abilityGameID": 20473, "timestamp": t0 + i, "classResources": [{"type": 0, "max": 250000}]}
              for i in range(10)]
             + [{"abilityGameID": 156322, "timestamp": t0 + 20, "classResources": [{"type": 9, "max": 5}]}])

    async def graph(code, fight, data_type, **filters):
        if filters["abilityID"] == 100:
            return {"series": [{"id": p.id, "data": [[t0, 100], [t0 + 50_000, 60]]}]}
        return {"series": [{"id": p.id, "data": [[t0, 2], [t0 + 10_000, 5], [t0 + 40_000, 1]]}]}
    patch_wcl(rep, graph=graph, table={"resources": [{"name": "Holy Shock", "gains": 150, "wasted": 52},
                                                      {"name": "Beacon of Light", "gains": 68, "wasted": 24}]})
    fake_knowledge(heals=lambda class_name, spec: spec in ("Holy", "Restoration"))
    lines, name, capped = await coaching.resource_use(rep, f, holy, casts, 100, f["endTime"])
    assert lines[0].startswith("Mana over the pull: 0:00 100%")
    assert lines[1].startswith("Holy Power at its cap (5) for 30% of their time alive")
    assert lines[2] == ("Holy Power gained 218, of it lost at the cap 76 (35%); by source (Warcraft Logs' names), "
                        "lost of gained: Holy Shock 52 of 150, Beacon of Light 24 of 68" + coaching.CAP_READING)
    assert (name, capped) == ("Holy Power", [(t0 + 10_000, t0 + 40_000)])
    # A cat-weaving druid's Energy isn't the healer's resource: only mana is judged
    casts[-1]["classResources"] = [{"type": 3, "max": 100}]
    lines, name, _ = await coaching.resource_use(rep, f, Player(p.id, p.name, "Druid", "Restoration", "healer"),
                                                 casts, 100, f["endTime"])
    assert len(lines) == 1 and lines[0].startswith("Mana") and name == ""



async def test_a_source_that_drains_the_resource_is_not_counted_as_gained(rep, patch_wcl):
    # A Devourer's Void Metamorphosis drains Fury: Warcraft Logs gives it a gain below zero, net of the drain
    f, p = rep.fight(13), rep.player("Enfo")
    patch_wcl(rep, table={"resources": [{"name": "Devour", "gains": 1674, "wasted": 202},
                                        {"name": "Void Metamorphosis", "gains": -14216, "wasted": 480},
                                        {"name": "Consume Soul", "gains": 3156, "wasted": 928}]})
    assert await coaching.lost_at_cap(rep, [f["id"]], p, 17) == (
        "Fury gained 4830, of it lost at the cap 1130 (23%); by source (Warcraft Logs' names), lost of gained: "
        "Consume Soul 928 of 3156, Devour 202 of 1674; drained by Void Metamorphosis 14216 (net)"
        + coaching.CAP_READING)


def test_a_stat_below_zero_is_said_and_left_out_of_the_shares():
    det = {"combatantInfo": {"stats": {"Mastery": {"max": 2901}, "Haste": {"max": 1255}, "Crit": {"max": 626},
                                       "Versatility": {"max": -213}, "Intellect": {"max": 3535}}}}
    assert build.gear_stats(det) == (
        "Stats from their gear at the pull (ratings, before buffs such as a flask or a trinket's proc): Mastery 2901 · "
        "Haste 1255 · Crit 626 · Versatility -213 (Versatility below zero as the log gives it; the log doesn't say "
        "why); Intellect 3535.")
    assert build._secondary(det["combatantInfo"]["stats"]) == {}

def test_time_lost_weighs_deaths_gaps_and_the_cap_in_seconds_until_the_wipe_was_decided():
    f = {"startTime": 0, "endTime": 200_000}
    deaths = [{"timestamp": 50_000}, {"timestamp": 150_000}]  # battle-ressed at 80 s; the second is fallout
    casts = [{"timestamp": 10_000}, {"timestamp": 80_000}]
    lost = coaching.time_lost(f, deaths, casts, [(20_000, 25_000)], "Energy", [(85_000, 130_000)],
                              Decided(120_000))
    assert (lost.dead_s, lost.gaps_s, lost.capped_s) == (30, 5, 35)
    assert coaching.time_lost_text(lost) == (
        "Time lost until 2:00, when the wipe was decided, biggest first: Energy at its cap (only its gain lost) 35 s "
        "(29%), dead (no output at all) 30 s (25%), casting gaps (no cast begun) 5 s (4%).")
    assert coaching.time_lost_text(coaching.time_lost(f, [], casts, [], "", [], None)) == (
        "Time lost over the 3:20 pull, biggest first: none.")


async def test_a_pull_still_going_is_left_out_of_the_night(fake_knowledge):
    fake_knowledge(spec_spells=RESTO_SPELLS, lust_debuffs=[57723], boss_spells=[])
    night = Night()  # changed below: not the session's
    last = night.fights[-1]
    night.fights.append({**last, "id": 999, "inProgress": True, "bossPercentage": 100.0, "fightPercentage": 100.0,
                         "startTime": last["endTime"] + 60_000, "endTime": last["endTime"] + 90_000})
    night.deaths[999] = []
    wcl = FakeWCL(night, time.time() * 1000)
    rep = await server.Library(wcl=wcl.client()).report(CODE)
    n = len(rep.by_boss["Sszorak"])
    wcl.queries.clear()
    text = await coaching.player_night(rep, "Enfo", "Sszorak")
    assert text.startswith(f"Enfo (Restoration Shaman) on Sszorak, all {n - 1} pulls; pull {n} is still going and "
                           "left out until it ends.")
    text = await mechanics.mechanic_outliers(rep, boss="Sszorak", all_pulls=True)
    assert f"all {n - 2} Sszorak pulls (resets and the pull still going left out)" in text  # one reset
    # The other answers over the night leave it out too, and say so first.
    going = f"Pull {n} is still going and left out until it ends.\n"
    for text in [await mechanics.mechanic_hits(rep, "Mutilate", boss="Sszorak", all_pulls=True),
                 await tables.breakdown(rep, "DamageTaken", boss="Sszorak"),
                 await deaths.defensives(rep, "Sszorak"),
                 await adds.enemies_night(rep, "Sszorak"),
                 await healing.healing(rep, boss="Sszorak", all_pulls=True)]:
        assert text.startswith(going) and f"{n} Sszorak pulls" not in text
    assert not any("999" in q for q in wcl.queries)


def test_a_resource_is_named_as_raiders_write_it_and_mana_is_not_one():
    assert coaching.power_type("Holy Power") == coaching.power_type("holy power") == 9
    assert coaching.power_type("combo point") == coaching.power_type("Combo Points") == 4
    assert coaching.power_type("Mana") is None and coaching.power_type("Starlord") is None


def test_a_nights_best_pull_is_one_long_enough_to_judge():
    def pull(counted_s: float, role: str = "dps", pct: float = 50.0) -> coaching.NightPull:
        f = {"kill": False, "fightPercentage": pct, "bossPercentage": pct}
        return cast(coaching.NightPull, SimpleNamespace(f=f, mine={"total": 1}, counted_s=counted_s,
                                                       played=SimpleNamespace(role=role)))
    short, long, healing, reset = pull(25), pull(140), pull(200, "healer"), pull(300, pct=100)
    assert coaching.best_pulls([short, long, healing, reset], "dps") == [long]
    assert coaching.best_pulls([short], "dps") == [short]  # none long enough: the short ones
