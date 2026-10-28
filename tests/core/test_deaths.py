"""Deaths and defensives (report/deaths.py). How they read on real pulls is pinned by the tool cases
(test_tools.py)."""
from tests.support import RESTO_SPELLS
from wipebot.core.report import deaths
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
    assert "  Astral Shift: last used at 0:40 (90 s cooldown), on cooldown" in recap
    table = await deaths.defensives(rep, "Sszorak", player=None)
    mine = next(line for line in table.splitlines() if line.startswith(f"- {p.name} "))
    assert ", Astral Shift 1" in mine and "never cast" not in mine


def test_a_kill_reads_as_the_boss_dead_not_its_last_percent():
    """Warcraft Logs often leaves a kill at 0.01%, which a model reads as a near miss ("finished at 0.01%, so it was
    close"): a kill's end says the boss died."""
    assert boss_left({"kill": True, "bossPercentage": 0.01}) == "the boss dead"
    assert boss_left({"kill": False, "bossPercentage": 62.59}) == "the boss at 62.59% health"


def test_a_wipe_says_its_bosss_health_and_the_wipe_percent_when_phases_make_them_differ():
    # Ula'tek: the summaries say "wipe at 6.4%" (phases weighed in) where the boss had 8.95% health
    assert boss_left({"kill": False, "bossPercentage": 8.95, "fightPercentage": 6.4}) == (
        "the boss at 8.95% health (the wipe at 6.4%, phases weighed in)")


def test_a_wipe_on_a_boss_counted_by_phase_names_the_phase_its_health_is_in():
    # the Coiled Altar: P2's boss had 80.8% health, a wipe Warcraft Logs counts as 74.2% of the whole fight
    assert boss_left({"kill": False, "bossPercentage": 80.8, "fightPercentage": 74.24, "lastPhase": 2}) == (
        "the boss at 80.8% health in P2 (the wipe at 74.2%, phases weighed in)")
