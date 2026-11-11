"""Deaths and defensives (report/deaths.py). How they read on real pulls is pinned by the tool cases
(test_tools.py)."""
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


def test_a_recaps_verdict_is_the_replays_answer_in_one_line():
    head = "If pressed (12.72k overkill to make up, healing as it was; ...):"
    stone = "  a healthstone (if they had one; none used this pull) (heals 25% of maximum health): heals 215.34k " \
            "pressed 1.1 s before the killing hit: would have kept them alive"
    short = "  Barkskin (20% less damage for 8 s): 30.00k less of the 150.00k it covers: not enough on its own"
    blind = "  Feint (40% less area damage): the log doesn't say which hits were area damage; can't tell"
    assert deaths._verdict([head, short]) == [
        "Nothing that was ready would have kept them alive on its own (replayed below)."]
    assert deaths._verdict([]) == [] and deaths._verdict(["If pressed: unavailable (Wowhead timed out)."]) == []
    # It says no more than the working below it. The log doesn't show who carries a healthstone, so the one that
    # would have done it keeps its "if they had one"; and what the replay couldn't tell isn't ruled out with the
    # rest: an immunity sure only for a moment is no "would have", and "nothing would have" isn't said of the unknown.
    assert deaths._verdict([head, short, stone]) == [
        "Would have kept them alive, if pressed (replayed below; secondary to what killed them): a healthstone (if "
        "they had one; none used this pull)."]
    cloak = "  Cloak of Shadows (immune to magic for 5 s): 80.00k less of the 80.00k it covers: would have kept them " \
            "alive only if it held for all 5 s: its tooltip promises the immunity for a moment, so can't be sure"
    assert deaths._verdict([head, short, blind]) == [
        "Nothing that was ready is sure to have kept them alive on its own (replayed below): 1 of 2 can't be told."]
    assert deaths._verdict([head, blind, cloak]) == [
        "Nothing that was ready is sure to have kept them alive on its own (replayed below): 2 of 2 can't be told."]
    assert deaths._verdict([head, cloak, short.replace("Barkskin", "Feint").replace(
        "not enough on its own", deaths.SAVED)]) == [
        "Would have kept them alive, if pressed (replayed below; secondary to what killed them): Feint."]


HEAD = "If pressed (12.72k overkill to make up, healing as it was; ...):"
STONE = ("  a healthstone (if they had one; none used this pull) (heals 25% of maximum health): heals 215.34k pressed "
         "1.1 s before the killing hit: would have kept them alive")
SHORT = "  Barkskin (20% less damage for 8 s): 30.00k less of the 150.00k it covers: not enough on its own"
BLIND = "  Evasion: its tooltip doesn't say how much it does; can't tell"
CLOAK = ("  Cloak of Shadows (immune to magic damage for 5 s): 80.00k less of the 80.00k it covers: would have kept "
         "them alive only if it held for all 5 s: its tooltip promises the immunity for a moment, so can't be sure")


def test_a_spell_that_would_only_if_it_held_is_no_saver_in_any_count():
    """savers() took any line with "would have kept them alive" in it, so Cloak of Shadows' "only if it held for all
    5 s ... so can't be sure" counted as a save in defensives, in player_night and on the cards, where the death's
    own recap said nothing was sure to have."""
    assert deaths.savers([HEAD, CLOAK]) == []
    assert deaths.savers([HEAD, CLOAK, SHORT, STONE]) == ["a healthstone"]
    # One reading for every count: something would have, nothing would have, or the log can't tell.
    assert deaths.lived([HEAD, CLOAK, STONE]) is True and deaths.lived([HEAD, SHORT]) is False
    assert deaths.lived([HEAD, CLOAK]) is None and deaths.lived([HEAD, SHORT, BLIND]) is None
    assert deaths.lived([]) is None and deaths.lived([], outright=True) is False
    # ... and the recap's first line is that reading.
    assert deaths._verdict([HEAD, CLOAK])[0].startswith("Nothing that was ready is sure to have kept them alive")
    assert deaths._verdict([HEAD, SHORT])[0].startswith("Nothing that was ready would have kept them alive")


async def test_a_death_the_replay_cant_tell_is_counted_apart_in_defensives_and_the_players_night(rep, monkeypatch):
    """Four of Draiman's deaths on the tests' night, with made-up replays: a healthstone would have at one, nothing
    would have at one, Cloak of Shadows "only if it held" at one (it counted as kept alive), and one wasn't replayed."""
    p = rep.player("Draiman")
    pulls, *_ = await coaching.night_pulls(rep, p, rep.by_boss["Sszorak"])
    died = [(x, x.death[1]) for x in pulls if x.counted and x.death][:4]
    counted = [x for x, _ in died]
    replays = [[HEAD, CLOAK, STONE], [HEAD, SHORT], [HEAD, CLOAK, SHORT], []]
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
