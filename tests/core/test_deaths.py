"""Deaths and defensives (report/deaths.py). How they read on real pulls is pinned by the tool cases
(test_tools.py)."""
from typing import cast

from tests.support import RESTO_SPELLS
from wipebot.core.report import coaching, deaths
from wipebot.core.report.fight import boss_left


async def test_a_defensive_cast_as_its_replacing_talent_counts_as_used(rep, patch_wcl, fake_knowledge):
    """Lorrgs lists a talent that replaces a spell as its variation (Crusade for Avenging Wrath): the death recap and
    the defensives table count it as the spell, not as never pressed."""
    fake_knowledge(spec_spells=[{**RESTO_SPELLS[0], "variations": [999001]}])  # Astral Shift
    f, p = rep.fight(13), rep.player("Scopidruid")
    events = rep.wcl.events

    async def casts(code, fight_id, data_type, **filters):  # one cast on pull 13 at 0:40, under the talent's id
        if data_type != "Casts":
            return await events(code, fight_id, data_type, **filters)
        return [{"timestamp": f["startTime"] + 40_000, "type": "cast", "sourceID": p.id, "abilityGameID": 999001,
                 "fight": f["id"]}]
    patch_wcl(rep, events=casts)
    recap = await deaths.death_recap(rep, p.name, 13)
    assert "  Astral Shift: last used at 0:40 (90 s cooldown), on cooldown until 2:10\n" in recap
    table = await deaths.defensives(rep, "Sszorak", player=None)
    mine = next(line for line in table.splitlines() if line.startswith(f"- {p.name} "))
    assert ", Astral Shift 1" in mine and "never cast" not in mine


def test_a_kill_reads_as_the_boss_dead_not_its_last_percent():
    """Warcraft Logs often leaves a kill at 0.01%, which a model reads as a near miss ("finished at 0.01%, so it was
    close"): a kill's end says the boss died."""
    assert boss_left({"kill": True, "bossPercentage": 0.01}) == "the boss dead"
    # one precision: pull_summary opened with "wipe at 62.6%" and closed with "the boss at 62.59% health"
    assert boss_left({"kill": False, "bossPercentage": 62.59}) == "the boss at 62.6% health"
    assert boss_left({"kill": False, "bossPercentage": 62.56, "fightPercentage": 62.61}) == "the boss at 62.6% health"
    # ... and where the two print apart, however close they are, the last line says which the first line gave: it
    # read "the boss at 62.5% health" alone under "wipe at 62.6%".
    assert boss_left({"kill": False, "bossPercentage": 62.55, "fightPercentage": 62.59}) == (
        "the boss at 62.5% health (the wipe at 62.6%, phases weighed in)")


def test_a_wipe_says_its_bosss_health_and_the_wipe_percent_when_phases_make_them_differ():
    # Ula'tek: the summaries say "wipe at 6.4%" (phases weighed in) where the boss had 8.95% health
    assert boss_left({"kill": False, "bossPercentage": 8.95, "fightPercentage": 6.4}) == (
        "the boss at 8.9% health (the wipe at 6.4%, phases weighed in)")


def test_a_wipe_on_a_boss_counted_by_phase_names_the_phase_its_health_is_in():
    # the Coiled Altar: P2's boss had 80.8% health, a wipe Warcraft Logs counts as 74.2% of the whole fight
    assert boss_left({"kill": False, "bossPercentage": 80.8, "fightPercentage": 74.24, "lastPhase": 2}) == (
        "the boss at 80.8% health in P2 (the wipe at 74.2%, phases weighed in)")


def test_a_deaths_damage_is_added_up_by_ability_with_the_hit_that_did_it(rep):
    # A killing blow is often a small tick after the hit that did the damage: "killed by" the tick is the easy wrong
    # answer. The recap adds the window up by ability and names the biggest hit with the health it left.
    f = rep.fight(13)

    def hit(s: int, ability: int, amount: int, hp: int | None = None, overkill: int = 0) -> dict:
        h = {"timestamp": f["startTime"] + s * 1000, "abilityGameID": ability, "amount": amount}
        if hp is not None:
            h |= {"resourceActor": 2, "hitPoints": hp, "maxHitPoints": 100}
        return h | ({"overkill": overkill} if overkill else {})
    tempest, mutilate = 1287083, 1285999
    hits = [hit(40, tempest, 10_000, 90), hit(41, mutilate, 300_000, 6), hit(42, tempest, 5_000, 0, overkill=1_000)]
    assert deaths.damage_by_ability(rep, f, hits) == (
        "Damage in the 10 s before death, by ability: Mutilate 300.00k in 1 hit, Tempest 15.00k in 2 hits; 315.00k in "
        "all. The biggest hit: Mutilate 300.00k at 0:41, to 6% health. The killing blow: Tempest 5.00k at 0:42.")
    one = [hit(40, tempest, 10_000, 90), hit(41, mutilate, 300_000, 0, overkill=50_000)]
    assert deaths.damage_by_ability(rep, f, one).endswith(
        "310.00k in all. The biggest hit was the killing blow: Mutilate 300.00k at 0:41, to 0% health.")
    # no killing hit logged (a fall): the biggest hit, and no killing blow claimed
    assert deaths.damage_by_ability(rep, f, hits[:2]).endswith(
        "The biggest hit: Mutilate 300.00k at 0:41, to 6% health.")


HEAD = "If pressed (12.72k overkill to make up, healing as it was; ...):"
# The rows of a replay (deaths.if_pressed) as the tests make them up, each beside the line it prints
STONE = deaths.Pressed("a healthstone", deaths.SAVED, True, "if they had one; none used this pull",
                       "heals 25% of maximum health", "heals 215.34k pressed 1.1 s before the killing hit")
SHORT = deaths.Pressed("Barkskin", deaths.NOT_ENOUGH, False, effect="20% less damage for 8 s",
                       how="30.00k less of the 150.00k it covers")
BLIND = deaths.Pressed("Evasion", "its tooltip doesn't say how much it does; can't tell")
AREA = deaths.Pressed("Feint", "the log doesn't say which hits were area damage; can't tell",
                      effect="40% less area damage")
CLOAK = deaths.Pressed("Cloak of Shadows", f"{deaths.SAVED} only if it held for all 5 s: its tooltip promises the "
                       "immunity for a moment, so can't be sure", None, effect="immune to magic damage for 5 s",
                       how="80.00k less of the 80.00k it covers")


def replay(*rows: deaths.Pressed) -> deaths.Replay:
    return deaths.Replay(HEAD, rows)


def test_a_replays_rows_print_the_lines_the_recap_and_defensives_show():
    """The replay is typed rows (deaths.Pressed) that its readers count from; what they print is these lines."""
    assert replay(STONE, SHORT, BLIND, AREA, CLOAK).lines() == [
        HEAD,
        "  a healthstone (if they had one; none used this pull) (heals 25% of maximum health): heals 215.34k pressed "
        "1.1 s before the killing hit: would have kept them alive",
        "  Barkskin (20% less damage for 8 s): 30.00k less of the 150.00k it covers: not enough on its own",
        "  Evasion: its tooltip doesn't say how much it does; can't tell",
        "  Feint (40% less area damage): the log doesn't say which hits were area damage; can't tell",
        "  Cloak of Shadows (immune to magic damage for 5 s): 80.00k less of the 80.00k it covers: would have kept "
        "them alive only if it held for all 5 s: its tooltip promises the immunity for a moment, so can't be sure"]
    assert deaths.Replay().lines() == [] and not deaths.Replay() and not deaths.Replay(HEAD)  # nothing was ready


async def test_a_replay_takes_a_spells_share_off_the_whole_killing_hit(monkeypatch):
    """A killing hit's amount stops at the health the player had left, and its overkill is the rest of it. The
    replay took an immunity or a reduction off the amounts alone and asked whether that made up the overkill: an
    immunity to a one-shot was "not enough on its own" (873.52k less against 8.56M overkill on a real night), and a
    reduction came out short by its share of the overkill. Made-up hits on a player with 1000 health."""
    from types import SimpleNamespace

    from wipebot.core import knowledge
    from wipebot.core.knowledge.spells import Effect

    class Rep:
        async def defensive_casts(self):  # pressed on another pull: a spell they have, ready on this one
            return [{"fight": 2, "sourceID": 7, "timestamp": 500_000, "abilityGameID": 1}]

        async def defensive_spells(self, p):
            return [{"name": "A Spell", "spell_id": 1, "cooldown": 120}]

        def is_spell(self, c, sp):
            return c["abilityGameID"] == sp["spell_id"]

        async def potions(self, f, p, until):  # a healthstone used: no row for one
            return 1, 0

        async def schools(self):
            return {9: 1, 8: 32}  # 9 physical, 8 magic

    async def replayed(effect: Effect, hits: list[dict]) -> deaths.Replay:
        async def its(spell_id):
            return effect
        monkeypatch.setattr(knowledge, "defensive_effect", its)
        died = {"timestamp": hits[-1]["timestamp"]}
        return await deaths.if_pressed(cast("deaths.Report", Rep()), {"id": 1, "startTime": 0},
                                       cast("deaths.Player", SimpleNamespace(id=7)), died, hits)

    def hit(at: int, amount: int, left: int, ability: int = 9, **more) -> dict:
        return {"timestamp": at, "abilityGameID": ability, "amount": amount, "hitPoints": left, "maxHitPoints": 1000,
                "resourceActor": 2} | more

    one_shot = [hit(10_000, 50, 950), hit(12_000, 950, 0, overkill=8000)]
    immune = await replayed(Effect("immunity", duration_s=8), one_shot)
    assert immune.rows[0].line().strip() == (
        "A Spell (immune to all damage for 8 s): 8.95k less of the 8.95k it covers: would have kept them alive")
    assert immune.head == (
        "If pressed (8.00k overkill to make up, healing as it was; a defensive at the start of the lethal burst, 0:12, "
        "the first hit after they were last at 60%+ health; a heal after the last hit 1 s or more before the killing "
        "hit, time to see it):")
    assert (await replayed(Effect("reduction", pct=20, duration_s=8), one_shot)).rows[0].line().strip() == (
        "A Spell (20% less damage for 8 s): 1.79k less of the 8.95k it covers: not enough on its own")
    # a moment of immunity that is sure holds the whole one-shot, not only the health it took
    assert (await replayed(Effect("immunity", duration_s=8, sure_s=1), one_shot)).rows[0].saves is True
    # 20% of the 900 they took alone is 180, short of the 220 overkill; of all 1120 it is 224: at 580 health, not
    # 500, the last hit does 576 of its 720 and they live
    worn_down = [hit(10_000, 100, 900), hit(11_000, 400, 500), hit(12_000, 500, 0, overkill=220)]
    assert (await replayed(Effect("reduction", pct=20, duration_s=8), worn_down)).rows[0].line().strip() == (
        "A Spell (20% less damage for 8 s): 224 less of the 1.12k it covers: would have kept them alive")
    # a spell that ran out before the killing hit takes nothing off it: the 400 it stopped must make up the overkill
    assert (await replayed(Effect("immunity", duration_s=0.5), worn_down)).rows[0].saves is True
    assert (await replayed(Effect("reduction", pct=30, duration_s=0.5), worn_down)).rows[0].line().strip() == (
        "A Spell (30% less damage for 0.5 s): 120 less of the 400 it covers: not enough on its own")
    # nor does a shield that ran out before it: 500 of shield took in the 350 it was up for, short of the 400 overkill
    shield_out = [hit(10_000, 100, 900), hit(11_000, 350, 550), hit(12_000, 550, 0, overkill=400)]
    assert (await replayed(Effect("absorb", pct=50, duration_s=0.5), shield_out)).rows[0].line().strip() == (
        "A Spell (absorbs 50% of maximum health for 0.5 s): absorbs 350 of the burst: not enough on its own")
    # a shield absorbs no more than the hits it is up for, and one against magic none of a physical burst
    assert (await replayed(Effect("absorb", pct=30, duration_s=0.5), worn_down)).rows[0].saves is True  # 300 of 400
    assert (await replayed(Effect("absorb", pct=30, only="magic", duration_s=8), worn_down)).rows[0].line().strip() == (
        "A Spell (absorbs 30% of maximum health for 8 s): absorbs 0 of the burst: not enough on its own")


async def test_a_recaps_verdict_is_the_replays_answer_in_one_line(rep, monkeypatch):
    assert deaths._verdict(replay(SHORT)) == [
        "Nothing that was ready would have kept them alive on its own (replayed below)."]
    assert deaths._verdict(deaths.Replay()) == []

    # a guide out of reach: no verdict up top, and the line saying so where the working would be
    async def out_of_reach(*_):
        raise deaths.KnowledgeError("Wowhead timed out")
    monkeypatch.setattr(deaths, "if_pressed", out_of_reach)
    nobody = cast("deaths.Player", None)
    assert await deaths._if_pressed_lines(rep, {}, nobody, {}, [], deaths.Standing("")) == (
        [], ["If pressed (what a ready defensive would have changed): unavailable (Wowhead timed out)."])
    # It says no more than the working below it. The log doesn't show who carries a healthstone, so the one that
    # would have done it keeps its "if they had one"; and what the replay couldn't tell isn't ruled out with the
    # rest: an immunity sure only for a moment is no "would have", and "nothing would have" isn't said of the unknown.
    assert deaths._verdict(replay(SHORT, STONE)) == [
        "Would have kept them alive, if pressed (replayed below; secondary to what killed them): a healthstone (if "
        "they had one; none used this pull)."]
    assert deaths._verdict(replay(SHORT, AREA)) == [
        "Nothing that was ready is sure to have kept them alive on its own (replayed below): 1 of 2 can't be told."]
    assert deaths._verdict(replay(AREA, CLOAK)) == [
        "Nothing that was ready is sure to have kept them alive on its own (replayed below): 2 of 2 can't be told."]
    feint = deaths.Pressed("Feint", deaths.SAVED, True, effect="20% less damage for 8 s",
                           how="30.00k less of the 150.00k it covers")
    assert deaths._verdict(replay(CLOAK, feint)) == [
        "Would have kept them alive, if pressed (replayed below; secondary to what killed them): Feint."]


def test_a_spell_that_would_only_if_it_held_is_no_saver_in_any_count():
    """savers() took any line with "would have kept them alive" in it, so Cloak of Shadows' "only if it held for all
    5 s ... so can't be sure" counted as a save in defensives, in player_night and on the cards, where the death's
    own recap said nothing was sure to have."""
    assert deaths.savers(replay(CLOAK)) == []
    assert deaths.savers(replay(CLOAK, SHORT, STONE)) == ["a healthstone"]
    # One reading for every count: something would have, nothing would have, or the log can't tell.
    assert deaths.lived(replay(CLOAK, STONE)) is True and deaths.lived(replay(SHORT)) is False
    assert deaths.lived(replay(CLOAK)) is None and deaths.lived(replay(SHORT, BLIND)) is None
    assert deaths.lived(deaths.Replay()) is None and deaths.lived(deaths.Replay(), outright=True) is False
    # ... and the recap's first line is that reading.
    assert deaths._verdict(replay(CLOAK))[0].startswith("Nothing that was ready is sure to have kept them alive")
    assert deaths._verdict(replay(SHORT))[0].startswith("Nothing that was ready would have kept them alive")


async def test_a_death_the_replay_cant_tell_is_counted_apart_in_defensives_and_the_players_night(rep, monkeypatch):
    """Four of Draiman's deaths on the tests' night, with made-up replays: a healthstone would have at one, nothing
    would have at one, Cloak of Shadows "only if it held" at one (it counted as kept alive), and one wasn't replayed."""
    p = rep.player("Draiman")
    pulls, *_ = await coaching.night_pulls(rep, p, rep.by_boss["Sszorak"])
    died = [(x, x.death[1]) for x in pulls if x.counted and x.death][:4]
    counted = [x for x, _ in died]
    replays = [replay(CLOAK, STONE), replay(SHORT), replay(CLOAK, SHORT), deaths.Replay()]
    made = [deaths.Replayed(x.f, d, more, False) for (x, d), more in zip(died, replays, strict=True)]
    first, _, unsure, _ = (rep.pull_no(x.f) for x in counted)
    assert coaching._night_lived(rep, counted, made) == (
        "A ready defensive or a healthstone would have kept them alive at 1 of the 4 deaths that count (death_recap "
        f"replays each): pull {first}; a healthstone 1; can't tell at 2 (no overkill or health logged at 1, a ready "
        "spell's effect can't be told at 1).")

    async def replay_deaths(*_):
        return made
    monkeypatch.setattr(deaths, "replay_deaths", replay_deaths)
    lines = (await deaths.defensives(rep, "Sszorak", player="Draiman")).splitlines()
    assert lines[0].endswith("a ready defensive or a healthstone would have kept them alive at 1 of them (can't tell "
                             "at 1 more: a ready spell's effect can't be told).")
    assert any(line.startswith(f"- pull {unsure} ") and "so can't be sure" in line for line in lines)
    assert lines[-1].startswith("Can't tell at 1 (no overkill or health logged): ")
