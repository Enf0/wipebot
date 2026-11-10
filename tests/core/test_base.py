"""A report (report/base.py): its pulls per boss, its players, when a wipe was decided, whether it's the guild's
run, and a source that can't answer."""
import time
from typing import cast

import pytest

from tests.support import CODE, FakeWCL, Night
from wipebot.core import knowledge, server
from wipebot.core.pulls import Decided, carried_buffs, is_reset, wipe_pct
from wipebot.core.report import FactError, Report, base, casting, cooldowns, deaths, fight, ranks
from wipebot.core.report.base import alive_after, alive_at, half_dead
from wipebot.core.wcl import WCL, WCLError


async def test_pulls_are_numbered_per_boss(rep):
    assert rep.boss(None) == "Sszorak"
    assert rep.fight(13)["id"] == 21
    assert rep.fight(None)["id"] == 41
    assert rep.fight(1, "vashnik")["id"] == 1
    assert rep.label(rep.fight(13)) == "Sszorak pull 13"
    with pytest.raises(FactError, match="Sszorak 33"):
        rep.fight(34)


class MixedWCL(FakeWCL):
    """The captured night, with Vashnik pulled once more on Heroic before the Mythic pulls."""

    async def report_fights(self, code, cache_for=None):
        report = await super().report_fights(code)
        heroic = {**report["fights"][0], "id": 900, "difficulty": 4, "startTime": 0, "endTime": 1}
        return {**report, "fights": [heroic, *report["fights"]]}


async def test_a_boss_on_two_difficulties_counts_pulls_per_difficulty(night):
    r = Report(MixedWCL(night).client(), CODE)
    await r.load()
    assert set(r.by_boss) == {"Vashnik the Malignant (Heroic)", "Vashnik the Malignant (Mythic)", "Sszorak"}
    assert r.fight(1, "vashnik mythic")["id"] == 1
    assert r.fight(1, "Vashnik the Malignant (Heroic)")["id"] == 900
    assert r.fight(None, "vashnik")["id"] == 2  # the difficulty pulled last
    assert r.label(r.fight(2, "vashnik")) == "Vashnik the Malignant (Mythic) pull 2"
    assert r.fight(13)["id"] == 21


async def test_a_boss_name_that_fits_several_takes_the_one_it_names_or_says_which_it_took(night):
    r = Report(MixedWCL(night).client(), CODE)
    await r.load()
    said = base.read_as.set([])
    try:
        assert r.boss("Vashnik the Malignant (Heroic)") == "Vashnik the Malignant (Heroic)"  # named whole: nothing
        assert r.boss("vashnik heroic") == "Vashnik the Malignant (Heroic)" and r.boss("ssz") == "Sszorak"
        assert base.read_as.get() == []
        assert r.boss("vashnik") == r.boss("Vashnik the Malignant") == "Vashnik the Malignant (Mythic)"
        assert base.read_as.get() == [
            "Boss 'vashnik' is read as Vashnik the Malignant (Mythic), pulled last; Vashnik the Malignant (Heroic) "
            "matches it too.",
            "Boss 'Vashnik the Malignant' is read as Vashnik the Malignant (Mythic), pulled last; Vashnik the "
            "Malignant (Heroic) matches it too."]
        r.boss("vashnik")  # said once in a call
        assert len(base.read_as.get() or []) == 2
    finally:
        base.read_as.reset(said)
    assert r.boss("vashnik") == "Vashnik the Malignant (Mythic)"  # outside a call: nothing to say it in


async def test_a_boss_named_whole_is_taken_before_one_the_name_is_part_of(night):
    end = night.fights[-1]["endTime"]
    echo = {**night.fights[0], "id": 900, "name": "Echo of Sszorak", "encounterID": 1, "startTime": end + 60_000,
            "endTime": end + 90_000}

    class Later(FakeWCL):
        async def report_fights(self, code, cache_for=None):
            report = await super().report_fights(code)
            return {**report, "fights": [*report["fights"], echo]}
    r = Report(Later(night).client(), CODE)
    await r.load()
    said = base.read_as.set([])
    try:
        assert r.boss("sszorak") == "Sszorak" and r.boss("echo") == "Echo of Sszorak"
        assert base.read_as.get() == []
        assert r.boss("szor") == "Echo of Sszorak"  # part of both names: the one pulled last, and said
        assert base.read_as.get() == ["Boss 'szor' is read as Echo of Sszorak, pulled last; Sszorak matches it too."]
    finally:
        base.read_as.reset(said)


async def test_a_boss_not_in_the_report_says_where_to_look(rep):
    with pytest.raises(FactError) as e:
        rep.boss("Twin Fangs")
    assert str(e.value) == ("no boss matching 'Twin Fangs' was pulled in this report, whose bosses are Vashnik the "
                            "Malignant and Sszorak; find_reports finds the report of the night it was pulled")
    # An enemy of a boss's fight is no boss: whose pulls it is in, and the tools that read it.
    rep.actors[500] = {"id": 500, "name": "Shrouded Venom", "type": "NPC", "subType": "NPC"}
    for f in rep.by_boss["Sszorak"]:
        f["enemyNPCs"] = [{"id": 500, "gameID": 260895, "instanceCount": 4}]
    with pytest.raises(FactError) as e:
        rep.boss("shrouded venom")
    assert str(e.value) == ("no boss matching 'shrouded venom' in this report: Shrouded Venom is an enemy in "
                            "Sszorak's pulls. With `boss` Sszorak, breakdown's `target` has the damage done to one "
                            "enemy and enemies has the adds")


async def test_a_name_no_player_has_comes_with_the_close_names_and_the_reports_players(rep):
    names = ", ".join(sorted((p.name for p in rep.players.values()), key=str.lower))
    assert "Scopidruid, " in names and len(rep.players) == 24
    for typed, close in (("Dscopi", "; closest names: Scopidruid"), ("Zebinolock", "; closest names: Zebinomage"),
                         ("Keithpop", "")):
        with pytest.raises(FactError) as e:
            rep.player(typed)
        assert str(e.value) == (f"no single player matching {typed!r} in this report{close}. Its players: {names}. "
                                "find_reports with a player finds the reports a character is in.")


async def test_player_names_match_without_accents_or_with_a_typo(rep):
    assert rep.player("enfo").name == "Enfo"
    assert rep.player("attractiv").name == "Attractîv"
    assert rep.player("kropling").name == "Krøpling"
    assert rep.player("scopidriud").name == "Scopidruid"  # letters swapped
    with pytest.raises(FactError):
        rep.player("nobody")
    assert rep.pulls_in(rep.player("enfo")).startswith("in the report: ")  # where they were, for an error


@pytest.mark.parametrize("order", [("healers", "dps"), ("dps", "healers")])
async def test_a_player_who_changed_role_is_their_main_spec_but_each_pull_has_its_own(night, monkeypatch, order):
    # Warcraft Logs lists such a player once per role for the whole report, in an order that changes per report;
    # each pull's spec comes with the pull (friendlySpecs, lined up with friendlyPlayers).
    wcl = FakeWCL(night)
    pid = next(i for i in night.fights[0]["friendlyPlayers"] if i in night.players)
    name = night.players[pid]
    entries = {"healers": [{"id": pid, "name": name, "type": "Shaman", "specs": [{"spec": "Restoration", "count": 2}]}],
               "dps": [{"id": pid, "name": name, "type": "Shaman", "specs": [{"spec": "Elemental", "count": 18}]}]}

    async def player_details(code, start, end, combatant=False, cache_for=None):
        return {role: entries[role] for role in order}

    monkeypatch.setattr(wcl, "player_details", player_details)
    r = Report(cast(WCL, wcl), CODE)
    await r.load()
    p = r.player(name)
    assert (p.spec, p.role, p.also) == ("Elemental", "dps", ("Restoration",))
    assert p.label == f"{name} (Elemental Shaman; also Restoration in some pulls)"
    f = next(f for f in r.fights if pid in f["friendlyPlayers"])
    f["friendlySpecs"] = ["Restoration" if i == pid else "Elemental" for i in f["friendlyPlayers"]]
    q = r.in_pull(f, p)
    assert (q.spec, q.role, q.also) == ("Restoration", "healer", ())


async def test_a_wipe_is_decided_by_the_enrage_or_half_the_raid_dead(rep):
    assert await rep.decided_at(rep.fight(13)) == Decided(152_966, "Unbound Ferocity")  # 2:32
    decided = await rep.decided_at(rep.fight(12))  # no enrage: decided when the 10th of 20 died
    assert decided and decided.why == "half the raid was dead"
    assert decided.at_ms == (await rep.deaths(rep.fight(12)))[9]["timestamp"] - rep.fight(12)["startTime"]
    assert await rep.decided_at(rep.fight(2, "vashnik")) is None  # a kill


async def test_one_burst_of_deaths_is_never_split_into_counted_and_fallout(rep):
    # Pull 16: 17 players die to one Virulence within a second at 0:28, the 10th of them half the raid; the last
    # player standing dies 3 s later.
    f = rep.fight(16)
    at = (await rep.decided_at(f)).at_ms
    deaths = await rep.deaths(f)
    assert at == deaths[-2]["timestamp"] - f["startTime"]
    assert deaths[-1]["timestamp"] - f["startTime"] > at + 1000


async def test_a_hit_killing_many_under_way_when_half_the_raid_died_counts_whole(rep):
    # Pull 28: the 10th of 20 dies at 1:38, the sixth of the seven one Viscous Cyst kills within 1.4 s. The hit's last
    # death, a second later, is the moment; the five who die over the 2 s after it are fallout.
    f = rep.fight(28)
    at = (await rep.decided_at(f)).at_ms
    deaths = await rep.deaths(f)
    assert at == deaths[11]["timestamp"] - f["startTime"]
    text = await fight.pull_summary(rep, 28)
    assert "Viscous Cyst [1287205] killed 7 within 1 s at 1:38" in text and "(8 alive)" in text
    assert "Half the raid was dead at 1:38, in one moment's deaths that ran to 1:39; later deaths" in text
    assert text.count("after half the raid was dead") == 5
    # Pull 19: half the raid dead at 1:43, in the middle of the ten one Viscous Cyst kills within 2 s.
    text = await fight.pull_summary(rep, 19)
    assert "Half the raid was dead at 1:43, in one moment's deaths that ran to 1:45;" in text


async def test_a_buff_the_boss_carried_through_an_earlier_wipe_is_the_fights_not_an_enrage(rep, monkeypatch):
    # A stage's aura, gained 7 s into the stage (past the grace a phase's own buffs get): on the pull that carried
    # it for a minute it is no end buff; on the next, which wiped 20 s after it, it would look like an enrage.
    aura = 990_001
    rep.abilities[aura] = "Dread Aura"
    first, second = rep.fight(3), rep.fight(4)

    def gained(f: dict, before_end: int) -> list[dict]:
        return [{"type": "applybuff", "abilityGameID": aura, "timestamp": f["endTime"] - before_end,
                 "targetID": next(iter(rep.bosses))}]

    carried = [first]

    async def events(fights):
        return {f["id"]: gained(f, 60_000) if f in carried else gained(f, 20_000) if f is second else []
                for f in fights}
    monkeypatch.setattr(rep, "boss_buff_events", events)
    assert await rep.end_buffs(first) == []
    assert aura in await rep.routine_buffs(second) and await rep.end_buffs(second) == []
    # with no earlier pull that carried it, the same 20 s are an end buff: the first pull to reach the stage
    carried.clear()
    assert [name for name, _ in await rep.end_buffs(second)] == ["Dread Aura"]
    assert carried_buffs(first, gained(first, 60_000)) == {aura}
    lost = [*gained(first, 60_000),
            {"type": "removebuff", "abilityGameID": aura, "timestamp": first["endTime"] - 45_000}]
    assert carried_buffs(first, lost) == set()  # gone within the window: a buff that comes and goes


async def test_a_councils_boss_deaths_are_read_for_every_pull_in_one_query(rep, monkeypatch):
    wcl = cast(FakeWCL, rep.wcl)
    fights = rep.by_boss["Sszorak"]
    monkeypatch.setattr(rep, "_council", lambda f: True)  # as if every pull were on several bosses
    await rep.decided_cuts(fights[:1])
    wcl.queries.clear()
    cuts = await rep.decided_cuts(fights)
    assert wcl.queries.count("events_many Deaths") == 1  # the pulls whose boss kept a buff, together
    wcl.queries.clear()
    assert cuts == {f["id"]: f["startTime"] + d.at_ms for f in fights if (d := await rep.decided_at(f))}
    assert wcl.queries == []


async def test_many_dying_at_once_without_a_killing_blow_is_one_moment_not_a_cascade(rep):
    # Pull 11: 13 players die within 1.4 s at 1:47 and the log names no killing blow for any of them.
    text = await fight.pull_summary(rep, 11)
    assert "13 died at once, no killing blow logged" in text and "no single hit killing many" not in text


async def test_a_kill_has_no_fallout(rep):
    text = await fight.pull_summary(rep, 2, "vashnik")
    assert "fallout" not in text and "after half the raid was dead" not in text


async def test_the_latest_pull_is_the_latest_finished_one():
    night = Night()
    live = {**night.fights[-1], "id": 999, "inProgress": True, "startTime": night.fights[-1]["endTime"] + 60_000,
            "endTime": night.fights[-1]["endTime"] + 90_000}
    night.fights.append(live)
    night.deaths[999] = []
    rep = await server.Library(wcl=FakeWCL(night, time.time() * 1000).client()).report(CODE)
    assert rep.fight()["id"] == night.fights[-2]["id"]
    assert rep.fight(len(rep.by_boss["Sszorak"]))["id"] == 999  # asked for by number: that one
    assert "**in progress**" in await rep.summary(live)


async def test_a_pull_number_the_boss_pulled_last_lacks_is_the_one_boss_that_has_it(night):
    # Vashnik (2 pulls) pulled a third time after Sszorak's 33, then a new boss's first pull, still going.
    end = night.fights[-1]["endTime"]
    vashnik = {**night.fights[0], "id": 900, "startTime": end + 60_000, "endTime": end + 90_000}
    first = {**night.fights[0], "id": 901, "name": "Nek'zali", "encounterID": 1, "inProgress": True,
             "startTime": end + 120_000, "endTime": end + 150_000}
    class Later(FakeWCL):
        async def report_fights(self, code, cache_for=None):
            report = await super().report_fights(code)
            return {**report, "fights": [*report["fights"], vashnik, first]}
    r = Report(Later(night).client(), CODE)
    await r.load()
    assert r.fight()["id"] == 901  # the boss's only pull: the one still going
    assert r.fight(33)["id"] == r.by_boss["Sszorak"][-1]["id"]  # only Sszorak has a pull 33
    with pytest.raises(FactError, match="pull 3 of which boss"):
        r.fight(3)  # Vashnik and Sszorak both have one
    assert r.fight(None, "vashnik")["id"] == 900


def test_half_the_raid_dead_counts_a_battle_res():
    deaths = [{"targetID": i, "timestamp": t} for i, t in [(1, 1000), (2, 2000), (3, 5000)]]
    assert alive_after(deaths, {}, 4) == [3, 2, 1]
    assert half_dead(deaths, {}, 4) == 2000
    backs = {1: [1500.0]}  # brought back before the second death
    assert alive_after(deaths, backs, 4) == [3, 3, 2]
    assert half_dead(deaths, backs, 4) == 5000
    assert alive_at(deaths, backs, 4, 1200) == 3 and alive_at(deaths, backs, 4, 1600) == 4


async def test_a_report_mostly_of_players_outside_the_guild_is_doubted(rep, patch_wcl):
    """A raider's pug logged to the guild's page isn't the guild's raid night."""
    rep.guild_id, rep.guild_name = 14169, "The Copper Lanterns"
    assert await rep.guild_run_doubt() == ""  # FakeWCL: everyone in the night is in the guild
    patch_wcl(rep, guild_members={next(iter(rep.players.values())).name})
    assert await rep.guild_share() == (1, len(rep.players))
    assert (await rep.guild_run_doubt()).startswith(f"only 1 of its {len(rep.players)} players are in The Copper "
                                                    "Lanterns: it may be a pug")
    patch_wcl(rep, guild_members=set())  # Warcraft Logs doesn't say: no doubt raised
    assert await rep.guild_run_doubt() == ""


async def test_a_source_that_cant_answer_reads_as_unavailable_not_none(rep, fake_knowledge, patch_wcl):
    """Lorrgs or Warcraft Logs down: the part of the answer that needs it says so in one line, and the rest of the
    answer still comes; it never reads as "no defensives", "no cooldowns" or "not ranked"."""
    async def down(*args, **kwargs):
        raise knowledge.KnowledgeError("Lorrgs timed out")
    fake_knowledge(spec_spells=down, lust_debuffs=down)
    f, p = rep.fight(13), rep.player("Enfo")
    recap = await deaths.death_recap(rep, "Scopidruid", 13)
    assert "Personal defensives: unavailable (Lorrgs timed out)." in recap and "Lorrgs lists no" not in recap
    assert "Healthstones and healing potions before death: 0" in recap  # the rest is still there
    table = await deaths.defensives(rep, "Sszorak")
    assert "Defensive lists: unavailable (Lorrgs timed out). Only healthstones and potions are counted." in table
    lines = await cooldowns.cooldown_lines(rep, f, p, [])
    assert lines[-1] == "Major cooldowns: unavailable (Lorrgs timed out)."

    async def wcl_down(code, fight_id, metric):
        raise WCLError("timeout: Warcraft Logs didn't answer")
    patch_wcl(rep, rankings=wcl_down)
    kill = next(f for f in rep.fights if f["kill"])
    assert await ranks.parse(rep, kill, p) == "Parse (hps): unavailable (timeout: Warcraft Logs didn't answer)."


async def test_bosses_are_grouped_by_the_raid_the_game_puts_them_in(rep, fake_knowledge):
    # Warcraft Logs files a single-boss raid's boss under the tier, so "the first boss" was read as one pulled
    # first from another raid (answer #473). The raids come in the game's boss order.
    assert list(rep.by_boss) == ["Vashnik the Malignant", "Sszorak"]
    fake_knowledge(raids=[("The Unbinding of Vashnik", ["Vashnik the Malignant"]),
                          ("The Venomous Abyss", ["Nek'zali the Soulcoiler", "Sszorak"])])
    assert await rep.raids() == [("The Unbinding of Vashnik", ["Vashnik the Malignant"]),
                                 ("The Venomous Abyss", ["Sszorak"])]
    fake_knowledge(raids=[("The Venomous Abyss", ["Sszorak"])])  # a boss in no raid: the report's own, last
    assert rep.zone == "The Venomous Abyss"
    assert await rep.raids() == [("The Venomous Abyss", ["Sszorak", "Vashnik the Malignant"])]


def test_a_player_brought_back_by_a_battle_res_is_alive_until_they_die_again():
    from wipebot.core.report.base import dead_since
    deaths = [{"targetID": 7, "timestamp": 171_000}, {"targetID": 7, "timestamp": 420_000}]  # 2:51, then 7:00
    backs = [176_000.0]  # a battle res at 2:56
    assert dead_since(deaths, backs, 7, 175_000) == 171_000
    assert dead_since(deaths, backs, 7, 263_000) is None  # 4:23: brought back
    assert dead_since(deaths, backs, 7, 430_000) == 420_000
    assert dead_since(deaths, [], 8, 430_000) is None  # never died


async def test_a_player_casting_after_their_death_came_back_by_themselves(rep, patch_wcl):
    """A Reincarnation is only a cast: a player's first cast after a death no battle res answered brings them back,
    as a battle res would. A fake cast (Warcraft Logs derives one from a dead Demon Hunter) doesn't, a death a battle
    res answered isn't asked about, and every pull's casts come in one query. A battle res is logged when it is
    cast: it brings a player back once they took it, which a cast of theirs after it shows (one more query, for the
    pulls with a res no later death proves); one nobody took brings nobody back."""
    pulls = rep.by_boss["Vashnik the Malignant"]
    f, t0 = pulls[1], pulls[1]["startTime"]
    deaths = await rep.deaths(f)  # 1:38, 2:24 and 3:50 the first three
    shaman, hunter, revived = deaths[:3]
    came = {"type": "cast", "sourceID": shaman["targetID"], "abilityGameID": 21169, "fight": f["id"],
            "timestamp": shaman["timestamp"] + 4000}
    fake = {"type": "cast", "sourceID": hunter["targetID"], "abilityGameID": 209693, "fight": f["id"], "fake": True,
            "timestamp": hunter["timestamp"] + 1000}
    res = {"type": "resurrect", "sourceID": shaman["targetID"], "targetID": revived["targetID"], "fight": f["id"],
           "abilityGameID": 20484, "timestamp": revived["timestamp"] + 9000}
    took = {"type": "cast", "sourceID": revived["targetID"], "abilityGameID": 133, "fight": f["id"],
            "timestamp": res["timestamp"] + 3000}
    first = {d["targetID"] for d in deaths[:3]}
    left = next(d for d in deaths[3:] if d["targetID"] not in first  # ressed 5 s after their only death: never took it
                and sum(x["targetID"] == d["targetID"] for x in deaths) == 1)
    unused = {**res, "targetID": left["targetID"], "timestamp": left["timestamp"] + 5000}
    asked = []
    real_events, real_many = rep.wcl.events, rep.wcl.events_many

    async def events(code, fight_id, data_type, **filters):
        return [res, unused] if data_type == "All" else await real_events(code, fight_id, data_type, **filters)

    async def events_many(code, items, data_type, **filters):
        if data_type != "Casts":
            return await real_many(code, items, data_type, **filters)
        asked.append({fid: own["filterExpression"] for fid, own in items})
        return [[came, fake, took] if fid == f["id"] else [] for fid, _ in items]
    patch_wcl(rep, events=events, events_many=events_many)

    backs = await rep.resurrects(pulls)
    assert len(asked) == 2 and list(asked[0]) == [g["id"] for g in pulls]
    after = asked[1][f["id"]]  # the res no later death proves: did the player cast after it?
    assert list(asked[1]) == [f["id"]] and all(
        f'source.name = "{rep.names[e["targetID"]]}" and timestamp > {e["timestamp"] - t0}' in after
        for e in (res, unused))
    assert left["targetID"] not in backs[f["id"]]  # nobody took it
    asks = asked[0][f["id"]]
    assert f'source.name = "{rep.names[shaman["targetID"]]}" and timestamp > {shaman["timestamp"] - t0}' in asks
    assert f"timestamp > {revived['timestamp'] - t0}" not in asks
    assert backs[f["id"]][shaman["targetID"]] == [came["timestamp"]]
    assert backs[f["id"]][revived["targetID"]] == [res["timestamp"]]
    assert hunter["targetID"] not in backs[f["id"]]
    assert await rep.self_res(f) == [came]
    timeline = dict((await fight._battle_res(rep, f)).events)
    assert sorted(timeline) == sorted([res["timestamp"], unused["timestamp"], came["timestamp"]])
    assert f"{rep.names[shaman['targetID']]} back to life by themselves" in timeline[came["timestamp"]]
    assert "not taken" not in timeline[res["timestamp"]]
    assert timeline[unused["timestamp"]].endswith(
        f"on {rep.names[left['targetID']]} (not taken: they did nothing after it, and stay dead in the counts)")
    raid = await rep.raid_size(f)
    counts = alive_after(deaths, backs[f["id"]], raid)
    assert counts[:2] == [raid - 1, raid - 1]  # one died, the shaman came back
    # two readings of the pull agree on who was alive at its end: the one battle res taken, the other not
    assert counts[-1] == alive_at(deaths, backs[f["id"]], raid, f["endTime"])
    assert f"({counts[-1]} alive" in (await fight.pull_summary(rep, rep.pull_no(f), "Vashnik")).splitlines()[-3]
    assert await rep.resurrects(pulls) == backs and len(asked) == 2  # remembered


async def test_the_raids_pauses_are_of_the_living(rep):
    # Pull 16: 19 of 20 die at 0:28-0:31. Dead players begin no cast, and aren't a raid-wide pause.
    assert await casting.raid_pauses(rep, rep.fight(16)) == []


def test_a_death_listed_twice_counts_once_and_two_players_dying_together_twice(rep):
    a, b = list(rep.names)[:2]
    events = [{"targetID": a, "timestamp": 5_000}, {"targetID": b, "timestamp": 5_010},
              {"targetID": a, "timestamp": 5_020}, {"targetID": a, "timestamp": 90_000}]
    assert [(e["targetID"], e["timestamp"]) for e in rep._player_deaths(events)] == [(a, 5_000), (b, 5_010),
                                                                                      (a, 90_000)]


@pytest.mark.parametrize(("raid", "enrage", "deaths", "decided"), [
    # deaths to different abilities: 250 ms apart are one moment, 251 aren't
    (4, None, [10_000, 10_500, 10_750, 11_000, 11_251], 11_000),
    (4, None, [10_000, 10_500, 10_700, 10_900, 11_100, 11_300, 11_500, 11_700], 11_500),  # a moment is 1 s at most
    (20, 20_000, [19_750, 20_200], 20_200),  # the enrage in a moment of deaths: from 250 ms before it
    # a hit killing many under way when half the raid died counts whole, however its deaths are spread within it:
    # the 10th of 20 dies at 30.6 s, the ability's last victim at 31.9 s, and the next, 2.2 s after its first, is
    # fallout; so is a death to something else half a second after the hit
    (20, None, [(5_000, 1), (6_000, 2), (7_000, 3), (8_000, 4), (9_000, 5), *[(at, 77) for at in (
        30_000, 30_100, 30_200, 30_400, 30_600, 31_000, 31_500, 31_900, 32_200)], (32_400, 6)], 31_900),
    # another ability's hit under way then counts whole too, and a death right behind its last one with it
    (20, None, [*[(5_000 + 1000 * n, n) for n in range(7)], (20_000, 88), (20_100, 88), (20_500, 99), (20_600, 88),
                (21_800, 88), (21_900, 7), (24_000, 88)], 21_900),
    # deaths in pulses to one named ability are one hit as well: the moment is its last pulse
    (20, None, [*[(5_000 + 1000 * n, n) for n in range(8)], (20_000, 88), (22_500, 88), (24_500, 88), (25_900, 88),
                (26_500, 88)], 25_900),
])
async def test_the_moment_a_wipe_was_decided_takes_in_the_deaths_of_that_moment(rep, monkeypatch, raid, enrage,
                                                                                deaths, decided):
    t0 = 1_000_000
    f = {"id": 1, "kill": False, "startTime": t0, "endTime": t0 + 60_000}

    async def answer(value):
        return value
    died = [d if isinstance(d, tuple) else (d, 1000 + i) for i, d in enumerate(deaths)]
    monkeypatch.setattr(rep, "enrages", lambda f: answer([("Frenzy", enrage)] if enrage else []))
    died = [{"targetID": i, "timestamp": t0 + at, "killingAbilityGameID": a} for i, (at, a) in enumerate(died)]
    monkeypatch.setattr(rep, "deaths", lambda f: answer(died))
    monkeypatch.setattr(rep, "raid_size", lambda f: answer(raid))
    monkeypatch.setattr(rep, "resurrects", lambda fs: answer({1: {}}))
    assert (await rep.decided_at(f)).at_ms == decided
    assert await rep.until_decided(f, whole_pull=True) == ({}, "")


async def test_the_guilds_best_is_its_furthest_wipe_in_earlier_logs_on_that_difficulty(rep, patch_wcl):
    f = rep.fight(13)
    key = (f["encounterID"], f["difficulty"])
    rep.guild_id, rep.zone_id = 14169, 38

    def log(code: str, ends: float, best: dict) -> dict:
        return {"code": code, "startTime": ends - 3_600_000, "endTime": ends, "best_wipes": best}
    ended = {"fightPercentage": 35.0, "bossPercentage": 64.0, "lastPhase": 2, "kill": False}
    patch_wcl(rep, guild_wipes=[
        log(CODE, rep.end_ms, {key: (5.0, 30, 30, {})}),  # this log
        log("sameNight", rep.start_ms + 1, {key: (10.0, 2, 2, {})}),  # another log of the same night
        log("heroic", rep.start_ms - 86_400_000, {(key[0], 4): (1.0, 9, 9, {})}),
        log("older", rep.start_ms - 86_400_000, {key: (40.0, 7, 3, {})}),
        log("oldest", rep.start_ms - 2 * 86_400_000, {key: (35.0, 12, 8, ended)}),
        log("none", rep.start_ms - 86_400_000, {})])
    best = await rep.guild_best(f)
    assert best is not None and (best.pct, best.code, best.fight, best.pull_no) == (35.0, "oldest", 12, 8)
    assert best.text("WCL ").startswith("P2 64.0% (WCL 35.0%) on ")  # as raiders count it, its phase's health
    rep.guild_id = None  # a personal log
    assert await rep.guild_best(f) is None


def test_a_council_is_several_bosses_each_one_of_its_kind(rep):
    boss, other = 9001, 9002
    rep.bosses = {boss, other}
    assert rep._council({"enemyNPCs": [{"id": boss}, {"id": other, "instanceCount": 1}]})
    assert not rep._council({"enemyNPCs": [{"id": boss}, {"id": 5}]})  # one boss and an add
    assert not rep._council({"enemyNPCs": [{"id": boss}, {"id": other, "instanceCount": 2}]})  # a pack of one kind


def test_a_pull_can_be_named_kill_best_wipe_first_or_last(rep):
    sszorak = rep.by_boss[rep.boss("sszorak")]
    assert rep.fight("first", "sszorak") is sszorak[0] and rep.fight("last", "sszorak") is rep.fight(None, "sszorak")
    assert rep.fight("13", "sszorak") is rep.fight(13, "sszorak")
    best = rep.fight("best wipe", "sszorak")
    pcts = [pct for f in sszorak if not f["kill"] and not is_reset(f) and (pct := wipe_pct(f)) is not None]
    assert wipe_pct(best) == min(pcts) and rep.fight(" Best  Wipe ", "sszorak") is best
    assert rep.fight("kill", "vashnik")["kill"]
    with pytest.raises(FactError, match="has no kill in this report"):
        rep.fight("kill", "sszorak")
    with pytest.raises(FactError, match="give a pull number or one of kill, best wipe"):
        rep.fight("the good one", "sszorak")


def test_a_heal_counts_only_with_time_to_press_it_and_room_to_heal():
    def hit(ms, hp):
        return {"timestamp": ms, "hitPoints": hp, "maxHitPoints": 1000}
    # a big hit from full health, then the killing tick 22 ms later: nobody could have pressed anything
    assert deaths._heal_in_time([hit(0, 990), hit(10_000, 60), hit(10_022, 0)], 1000, 250)[0] == 10
    assert deaths._heal_in_time([hit(9_500, 60), hit(10_022, 0)], 1000, 250)[0] == 0
    # left at 40% two seconds before the kill: the whole healthstone counts
    assert deaths._heal_in_time([hit(8_000, 400), hit(9_500, 300), hit(10_000, 0)], 1000, 250)[0] == 250


def test_a_player_over_some_pulls_has_the_spec_played_most_in_them_and_only_their_others_also():
    from types import SimpleNamespace
    from typing import cast

    from wipebot.core.report.base import Player, Report

    holy = Player(1, "Cptola", "Paladin", "Holy", "healer", also=("Protection",))
    fights = [{"id": i, "friendlyPlayers": [1], "friendlySpecs": [spec]}
              for i, spec in enumerate(["Protection", "Holy", "Holy"])]
    ns = SimpleNamespace(players={"cptola": holy}, spec_roles={(1, "Holy"): "healer", (1, "Protection"): "tank"})
    ns.pull_players = lambda f: Report.pull_players(cast(Report, ns), f)
    over = Report.over.__get__(cast(Report, ns))
    assert over(holy, fights[1:]).label == "Cptola (Holy Paladin)"
    assert over(holy, fights).also == ("Protection",)
    assert over(holy, fights[:1]).role == "tank"
