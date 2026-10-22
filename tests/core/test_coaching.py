"""One player's pull (report/coaching.py): the resource their spec spends, and where their time went."""
import time

from tests.support import CODE, RESTO_SPELLS, FakeWCL, Night
from wipebot.core import server
from wipebot.core.pulls import Decided
from wipebot.core.report import Player, adds, coaching, deaths, healing, mechanics, tables


async def test_the_resource_judged_is_the_one_the_spec_spends(rep, patch_wcl, fake_knowledge):
    f = rep.fight(13)
    p = rep.player("Enfo")
    t0 = f["startTime"]
    # A cast carries the resource it spends: Lava Burst mana, Elemental Blast Maelstrom.
    casts = [{"abilityGameID": 51505, "timestamp": t0 + 1000, "classResources": [{"type": 0, "max": 260000}]},
             {"abilityGameID": 117014, "timestamp": t0 + 2000, "classResources": [{"type": 11, "max": 125}]}]
    asked = []

    async def graph(code, fight, data_type, **filters):
        asked.append(filters["abilityID"])
        return {"series": [{"id": p.id, "data": [[t0, 100], [t0 + 10_000, 125], [t0 + 20_000, 10]]}]}
    patch_wcl(rep, graph=graph)
    fake_knowledge(heals=lambda class_name, spec: spec == "Restoration")
    line, _, _ = await coaching.resource_use(rep, f, p, casts, 100, f["endTime"])
    assert asked == [100] and line and line.startswith("Mana over the pull: ")  # a healer: mana, whatever else is spent
    elemental = Player(p.id, p.name, p.class_name, "Elemental", "dps")
    line, name, capped = await coaching.resource_use(rep, f, elemental, casts, 100, f["endTime"])
    assert asked == [100, 111]
    assert line == ("Maelstrom at its cap (125) for 10% of their time alive (what it gained then was lost); longest "
                    "stretches: 0:10-0:20 (10 s).")
    assert (name, capped) == ("Maelstrom", [(t0 + 10_000, t0 + 20_000)])
    essence = Player(p.id, p.name, "Evoker", "Devastation", "dps")
    casts[1]["classResources"] = [{"type": 19, "max": 6}]
    line, _, _ = await coaching.resource_use(rep, f, essence, casts, 100, f["endTime"])
    assert line and line.startswith("Essence at its cap (6)")


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
