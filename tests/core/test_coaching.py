"""One player's pull (report/coaching.py): the resource their spec spends, and where their time went."""
import time
from types import SimpleNamespace
from typing import cast

from tests.support import CODE, RESTO_SPELLS, FakeWCL, Night
from wipebot.core import server
from wipebot.core.pulls import Decided
from wipebot.core.report import Player, Report, adds, build, coaching, deaths, healing, mechanics, ranks, tables


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
        "Stats at the pull as the log gives them (ratings, before buffs such as a flask or a trinket's proc; they can "
        "differ a lot from what the gear gives, and the log doesn't say why; compare_with_top build sums the pieces): "
        "Mastery 2901 · Haste 1255 · Crit 626 · Versatility -213 (Versatility below zero as the log gives it); "
        "Intellect 3535.")
    assert build._secondary(det["combatantInfo"]["stats"]) == {}

def test_time_lost_weighs_deaths_gaps_and_the_cap_in_seconds_until_the_wipe_was_decided():
    f = {"startTime": 0, "endTime": 200_000}
    deaths = [{"timestamp": 50_000}, {"timestamp": 150_000}]  # battle-ressed at 80 s; the second is fallout
    casts = [{"timestamp": 10_000}, {"timestamp": 80_000}]
    lost = coaching.time_lost(f, deaths, casts, [(20_000, 25_000)], "Energy", [(85_000, 130_000)],
                              Decided(120_000))
    assert (lost.dead_s, lost.gaps_s, lost.capped_s) == (30, 5, 35)
    # The shares name what they are of once: lower down, the same gaps and cap are shares of the time alive.
    assert coaching.time_lost_text(lost) == (
        "Time lost until 2:00, when the wipe was decided, biggest first, each a share of those 120 s: Energy at its "
        "cap (only its gain lost) 35 s (29%), dead (no output at all) 30 s (25%), casting gaps (no cast begun) 5 s "
        "(4%).")
    assert coaching.time_lost_text(coaching.time_lost(f, [], casts, [], "", [], None)) == (
        "Time lost over the 3:20 pull, biggest first: none.")
    # The seconds are the clock's: a wipe decided at 152.6 s read "until 2:32 ... a share of those 153 s".
    late = coaching.time_lost_text(coaching.time_lost(f, deaths, casts, [], "", [], Decided(152_600)))
    assert late.startswith("Time lost until 2:32, when the wipe was decided, biggest first, each a share of those "
                           "152 s: ")


async def test_a_reset_is_no_pull_of_a_players_night_in_any_count(rep):
    # Pull 9 of the tests' night is a reset: twelve die with no killing blow at 0:11, the boss untouched. Its row
    # stays, marked; the headline, the deaths that count, the trend and the defensives leave it out alike.
    p = rep.player("Enfo")
    pulls, *_ = await coaching.night_pulls(rep, p, rep.by_boss["Sszorak"])
    reset = pulls[8]
    assert reset.reset and reset.death and not reset.counted
    counted = sum(x.counted for x in pulls)
    assert counted == sum(t.died for t in coaching.night_trend(pulls, "healer") or [])
    text = await coaching.player_night(rep, "Enfo", "Sszorak")
    assert f"Died before the wipe was decided on {counted} of 32 pulls (1 reset left out; later deaths" in text
    assert f"of the {counted} deaths that count" in text
    assert "  9 (100.0%, 0:24): a reset, not counted; " in text
    table = await deaths.defensives(rep, "Sszorak")
    assert "on Sszorak: 32 pulls (1 reset left out), until each wipe was decided" in table
    assert ": 33 pulls" not in table and ": 32 pulls, " in table
    own = await deaths.defensives(rep, "Sszorak", player="Enfo")
    assert own.startswith(f"Enfo's {counted} deaths on Sszorak before each wipe was decided, over 32 pulls (1 reset "
                          "left out), each replayed")
    assert "Not counted, 1 death on a reset (the boss untouched, no attempt): pull 9 0:11." in own
    hits = await mechanics.mechanic_hits(rep, "Mutilate", boss="Sszorak", all_pulls=True)
    assert "on all 32 Sszorak pulls (1 reset left out)" in hits


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


def test_a_buff_refreshed_by_a_removal_and_a_gain_at_once_runs_on():
    # Fury of the Aspects, an Evoker's Bloodlust, is removed and gained again every few seconds, as in
    # BVxPYCk6XQwWmgLd fight 11: one 40 s buff, not one of 2 s, or buff_at_casts said Bloodlust was never up.
    fury, other = 390386, 1
    auras = [{"type": "applybuff", "abilityGameID": fury, "timestamp": 1000},
             {"type": "removebuff", "abilityGameID": fury, "timestamp": 3000},
             {"type": "applybuff", "abilityGameID": other, "timestamp": 3000},
             {"type": "applybuff", "abilityGameID": fury, "timestamp": 3000},
             {"type": "removebuff", "abilityGameID": fury, "timestamp": 41000},
             {"type": "applybuff", "abilityGameID": fury, "timestamp": 60000}]
    assert coaching._aura_end(auras, 0, 99000) == 41000
    assert coaching._aura_end(auras, 5, 99000) == 99000


def _night_pull(no: int, until_s: float, uses: dict[str, list[float]], reset: bool = False) -> coaching.NightPull:
    """A made-up pull of a night for its cooldowns: how long the player had (alive, the wipe not decided) and when
    they used each, in seconds."""
    spells = {"Spirit Link Totem": 180, "Ascendance": 180, "Heroism": 300}
    rows = [({"name": n, "spell_id": k, "cooldown": cd, "tags": ["raid_cd"]}, [t * 1000 for t in uses.get(n, [])],
             int(until_s // cd) + 1) for k, (n, cd) in enumerate(spells.items(), 1)]
    return cast(coaching.NightPull, SimpleNamespace(f={"id": no}, reset=reset, until_s=until_s, cooldowns=rows))


def test_a_cooldown_held_for_a_moment_is_counted_on_the_pulls_that_got_to_it():
    # Spirit Link Totem is first used at about 1:11 on every pull that gets there: held for a boss cast, not pressed
    # at the pull. "3/7 possible" over the night read as four uses wasted; the pulls that ended before 1:11 had none.
    pulls = [_night_pull(1, 40, {"Heroism": [2]}), _night_pull(2, 150, {"Spirit Link Totem": [71], "Heroism": [3]}),
             _night_pull(3, 45, {"Heroism": [2]}), _night_pull(4, 160, {"Spirit Link Totem": [69], "Heroism": [2]}),
             _night_pull(5, 30, {"Heroism": [1]}), _night_pull(6, 155, {"Spirit Link Totem": [77], "Heroism": [4]}),
             _night_pull(7, 90, {"Heroism": [2]}), _night_pull(8, 20, {"Spirit Link Totem": [5]}, reset=True)]
    cds = coaching.night_cooldowns(pulls, set(), "")
    assert cds.held == {"Spirit Link Totem": coaching.Held(71_000, 3, 4)}  # pull 7 got to 1:11 without a use
    assert (cds.used["Spirit Link Totem"], cds.possible["Spirit Link Totem"]) == (3, 7)  # as pressed from the pull
    rep = cast(coaching.Report, SimpleNamespace(pull_no=lambda f: f["id"]))
    assert coaching._cooldown_text(rep, cds, "Spirit Link Totem") == (
        "Spirit Link Totem used on 3 of the 4 pulls that got to 1:11 (held for then: its median first use, latest "
        "pull 6 at 1:17)")
    # One pressed at the pull stays uses against possible uses, and so does one used too rarely to tell.
    assert coaching._cooldown_text(rep, cds, "Heroism") == (
        "Heroism 7/7 (first use on 7 pulls: median 0:02, latest pull 6 at 0:04)")
    assert coaching.held_at([70_000, 72_000]) is None
    # Steady is most first uses near their median: a few at the pull (the buff already up) don't hide it,
    assert coaching.held_at([0, 66_000, 67_000, 67_000, 68_000, 70_000, 71_000, 72_000]) == 67_500
    # ... but first uses all over the pull are no moment.
    assert coaching.held_at([35_000, 60_000, 95_000, 130_000, 170_000]) is None
    # A pull's row leaves a held cooldown out when the pull ended before its moment, and says so when it got there.
    assert coaching._row_cooldowns(pulls[0], cds) == ["Heroism 1/1 at 0:02"]
    assert coaching._row_cooldowns(pulls[1], cds) == ["Spirit Link Totem at 1:11", "Heroism 1/1 at 0:03"]
    assert coaching._row_cooldowns(pulls[6], cds) == ["Spirit Link Totem unused", "Heroism 1/1 at 0:02"]


def test_a_player_who_died_early_gets_no_active_time_and_no_percentile_against_kills():
    # A rogue died 53 s into a 2:42 pull. "active 41% of the pull" is Warcraft Logs' active time over the whole
    # pull and read as idling; 245k/s alive stood "above the 90th percentile" of full kills, for an opener with
    # Bloodlust and every cooldown in it.
    p = Player(7, "Torvek", "Rogue", "Assassination", "dps")
    f = {"id": 1, "name": "Sszorak", "startTime": 0, "endTime": 162_000}
    rep = cast(Report, SimpleNamespace(pull_players=lambda f: {7: p}))
    table = {"entries": [{"id": 7, "name": "Torvek", "total": 13_000_000, "activeTime": 66_000,
                          "abilities": [{"name": "Envenom", "total": 2_000_000}],
                          "targets": [{"name": "Sszorak", "total": 12_990_000}, {"name": "Mirelle", "total": 10_000}]}]}
    spread = ranks.Spread(42, True, [("90th percentile", 234_000.0), ("50th percentile", 214_000.0)])
    lines = coaching._throughput_lines(rep, f, p, "damage", table, True, 53, spread)
    assert lines[0] == ("Damage: 13.00M (80.25k/s over the pull, 245.28k/s over the 53 s alive), rank 1 of 1 damage "
                        "dealers (all specs)")
    assert lines[2] == "  by target: Sszorak 100%"  # a target that rounds to 0% is left out
    assert lines[3] == ("  against kills: not compared. They were alive 53 s of the 2:42 pull, and an opener "
                        "(Bloodlust and every cooldown at the start) runs above any full kill.")
    # Alive to the end: Warcraft Logs' active time and the place among the kills stand
    lines = coaching._throughput_lines(rep, f, p, "damage", table, False, 162, spread)
    assert lines[0].endswith("(all specs), active 41% of the pull")
    # ... as for one who died in the wipe's fallout, after it was decided
    assert coaching._throughput_lines(rep, f, p, "damage", table, True, 154, spread, False)[0].endswith(
        "over the 154 s alive), rank 1 of 1 damage dealers (all specs), active 41% of the pull")
    assert lines[3].startswith("  against kills: 80.25k/s alive is below the 50th percentile of the 42 ranked")
    # A pull that was over within a minute is an opener too, whoever lived
    short_pull = {**f, "endTime": 45_000}
    assert coaching._throughput_lines(rep, short_pull, p, "damage", table, False, 45, spread)[3].startswith(
        "  against kills: not compared. They were alive 45 s of the 0:45 pull")


async def test_time_at_the_cap_with_no_logged_loss_says_what_the_log_leaves_out(rep, patch_wcl):
    # "Energy at its cap for 3% of their time alive (what it gained then was lost)" above "lost at the cap 0": the
    # log counts what spells and procs gained, not what a resource regenerates by itself.
    f, p = rep.fight(13), rep.player("Enfo")
    patch_wcl(rep, table={"resources": [{"name": "Venomous Vim", "gains": 261, "wasted": 0}]})
    assert await coaching.lost_at_cap(rep, [f["id"]], p, 3, capped_ms=2_000) == (
        "Energy gained 261, of it lost at the cap 0 (0%); by source (Warcraft Logs' names), lost of gained: Venomous "
        "Vim 0 of 261. Its 2 s at the cap can have lost more: these are the gains of spells and procs, and what a "
        "resource regenerates by itself isn't logged.")
    assert (await coaching.lost_at_cap(rep, [f["id"]], p, 3) or "").endswith("Venomous Vim 0 of 261")


async def test_a_nights_rows_are_short_under_one_legend(rep):
    # Every row repeated what its numbers were ("healing ... while alive, rank 3 of 4 healers by total") and what its
    # death's mark meant, 46 words of it on a death in a hit killing many. One line above the rows says it once; a
    # row keeps its numbers and a mark: "counts", "fallout (decided m:ss)" or "one of N killed at once from m:ss".
    p = rep.player("Enfo")
    pulls, *_ = await coaching.night_pulls(rep, p, rep.by_boss["Sszorak"])
    lines = (await coaching.player_night(rep, "Enfo", "Sszorak")).splitlines()
    rows = [line for line in lines if line.startswith("  ")]
    legend = lines[lines.index(rows[0]) - 1]
    assert legend == coaching.NIGHT_ROWS.format(what="healing", role="healers") and len(rows) == len(pulls) == 33
    assert sum("the raid's to stop" in line for line in lines[lines.index(legend):]) == 1
    assert sum("by total" in line for line in lines) == 1
    for pull, row in zip(pulls, rows, strict=True):
        assert row.startswith(f"  {rep.pull_no(pull.f)} (")
        assert ("; survived" in row) == (pull.death is None)
        if pull.death:  # each death's mark is the one its count goes by
            assert (", counts" in row) == pull.counted
            assert (", fallout (decided " in row) == pull.fallout
            assert (", one of " in row and " killed at once from " in row) == (pull.mass and not pull.fallout)
    assert sum(", counts" in row for row in rows) == sum(x.counted for x in pulls)
    assert rows[5].endswith("died #8 at 1:07 to Mutilate [1285999], one of 4 killed at once from 1:07")
    assert rows[7].split("; ")[1] == "died #6 at 1:33 to Mutilated Gash [1285998], counts"
