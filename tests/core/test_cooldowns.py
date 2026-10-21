"""A player's cooldowns over a pull (report/cooldowns.py): used against how often they could have been, as Lorrgs
lists the spec's spells and the player's talents allow."""
import pytest

from tests.support import RESTO_SPELLS
from wipebot.core.report import cooldowns

# Two Restoration Shaman cooldowns on one choice node, and one on its own node
TREE = {"specNodes": [
    {"id": 81032, "type": "choice", "entries": [{"id": 101912, "name": "Ascendance", "spellId": 114052},
                                                 {"id": 135486, "name": "Healing Tide Totem", "spellId": 108280}]},
    {"id": 81041, "type": "single", "entries": [{"id": 101924, "name": "Spirit Link Totem", "spellId": 98008}]},
]}


@pytest.fixture
def pull(rep):
    """Pull 13 (2:42; P2 from 1:39, P1 again from 2:05), a player in it, and a cast at a second into it."""
    f = rep.fight(13)
    def cast(spell: int, s: float) -> dict:
        return {"abilityGameID": spell, "timestamp": f["startTime"] + s * 1000}
    return f, rep.player("Enfo"), cast


async def test_defensives_are_listed_by_use_not_judged_by_count(rep, pull):
    f, p, cast = pull
    rep.abilities |= {108271: "Astral Shift", 98008: "Spirit Link Totem"}
    lines = "\n".join(await cooldowns.cooldown_lines(rep, f, p, [cast(108271, 30), cast(98008, 60)]))
    assert "  Spirit Link Totem [98008] (raid_cd): 1/1 at 1:00" in lines
    assert "Astral Shift [108271] (" not in lines and "Astral Shift [108271] at 0:30" in lines


async def test_cooldowns_left_out_by_the_talents_are_not_judged(rep, pull, fake_knowledge, monkeypatch):
    f, p, cast = pull
    spells = RESTO_SPELLS + [
        {"spell_id": 114052, "name": "Ascendance", "cooldown": 180.0, "tags": ["raid_cd"]},
        {"spell_id": 108280, "name": "Healing Tide Totem", "cooldown": 180.0, "tags": ["raid_cd"]}]
    talents = {"talentTree": [{"id": 101912, "nodeID": 81032}, {"id": 101924, "nodeID": 81041}]}

    async def details(fight):
        return {p.id: {"combatantInfo": talents}}

    async def spec_spells(player):
        return spells
    fake_knowledge(tree_for=TREE)
    monkeypatch.setattr(rep, "details", details)
    monkeypatch.setattr(rep, "spells", spec_spells)
    lines = "\n".join(await cooldowns.cooldown_lines(rep, f, p, [cast(114052, 30)]))
    assert "  Ascendance [114052] (raid_cd): 1/1 at 0:30" in lines
    assert "  Spirit Link Totem [98008] (raid_cd): 0/1" in lines
    assert "Healing Tide Totem [" not in lines
    assert "Not talented (their talents leave these out, e.g. the other side of a choice node; not a missed " \
           "cooldown): Healing Tide Totem" in lines


async def test_cooldowns_count_a_replacing_talent_an_unknown_cooldown_and_when_one_came_back(rep, pull, monkeypatch):
    f, p, cast = pull
    spells = RESTO_SPELLS + [
        # Lorrgs gives no cooldown for a spell a talent turns into a cooldown, and lists a replacing talent's id as
        # a variation (Incarnation for Celestial Alignment).
        {"spell_id": 114050, "name": "Ascendance", "cooldown": 0.0, "tags": ["dps"]},
        {"spell_id": 383410, "name": "Celestial Alignment", "cooldown": 120.0, "tags": ["dps"],
         "variations": [102560]},
        {"spell_id": 191634, "name": "Stormkeeper", "cooldown": 60.0, "tags": []}]

    async def spec_spells(player):
        return spells
    monkeypatch.setattr(rep, "spells", spec_spells)
    rep.abilities |= {114050: "Ascendance", 102560: "Incarnation: Chosen of Elune", 191634: "Stormkeeper"}
    casts = [cast(114050, 5), cast(114050, 100), cast(102560, 10)] + [cast(191634, s) for s in (1, 40, 80, 120)]
    lines = "\n".join(await cooldowns.cooldown_lines(rep, f, p, casts))
    assert "  Ascendance [114050] (dps): 2 (cooldown unknown to Lorrgs) at 0:05, 1:40" in lines
    assert ("  Celestial Alignment [383410] (dps; cast as Incarnation: Chosen of Elune [102560]): 1/2 at 0:10 "
            "(ready again at 2:10 in P1 Sszorak (2nd time), unused)") in lines
    assert ("  Stormkeeper [191634] (untagged): 4 (more than its base cooldown allows: a talent or effect shortens "
            "it) at 0:01, 0:40, 1:20, 2:00") in lines
    assert "  Spirit Link Totem [98008] (raid_cd): 0/1\n" in lines  # never cast: nothing came back


def test_a_channelled_cooldown_counts_once_however_many_ticks_it_logs():
    # Divine Hymn logs a cast per tick of its channel: six in 4 s are one use (it read 248 uses of 43 possible)
    ticks = [16_000, 16_400, 17_000, 18_000, 19_000, 19_400]
    assert cooldowns.one_per_use([*ticks, 200_000]) == [16_000, 200_000]


async def test_cooldowns_count_until_the_wipe_was_decided(rep, pull, monkeypatch):
    # Pull 13 was decided at 2:32 (the enrage) and lasted 2:42: an 80 s cooldown fits twice, not three times, and
    # a press in the fallout doesn't count.
    f, p, cast = pull
    spells = RESTO_SPELLS + [{"spell_id": 1, "name": "Burst", "cooldown": 80.0, "tags": ["dps"]}]

    async def spec_spells(player):
        return spells
    monkeypatch.setattr(rep, "spells", spec_spells)
    rep.abilities[1] = "Burst"
    lines = "\n".join(await cooldowns.cooldown_lines(rep, f, p, [cast(1, 10), cast(1, 100), cast(1, 158)]))
    assert "  Burst [1] (dps): 2/2 at 0:10, 1:40" in lines


@pytest.fixture
def burst(rep, monkeypatch):
    """burst(cooldown s): the spec has one more cooldown, Burst [1]."""
    def add(cooldown: float) -> None:
        async def spec_spells(player):
            return RESTO_SPELLS + [{"spell_id": 1, "name": "Burst", "cooldown": cooldown, "tags": ["dps"]}]
        monkeypatch.setattr(rep, "spells", spec_spells)
        rep.abilities[1] = "Burst"
    return add


async def test_a_cooldown_up_at_the_pull_counts_as_used_at_its_start(rep, pull, burst, monkeypatch):
    f, p, cast = pull
    burst(80)

    async def prepull(rep, fights, p):
        return {f["id"]: ["Burst"]}
    monkeypatch.setattr(cooldowns, "prepull", prepull)
    lines = await cooldowns.cooldown_lines(rep, f, p, [cast(1, 90)])
    assert lines[0].endswith(": Burst")
    assert "  Burst [1] (dps): 2/2 at 0:00, 1:30 (and once before the pull)" in lines
    assert any(line.endswith("; talents not in the log, so 0 uses can mean not talented):") for line in lines)


async def test_a_cooldown_used_less_than_it_could_says_when_it_came_back(rep, pull, burst):
    f, p, cast = pull  # pull 13: Enfo died at 2:35, Attractîv at 1:28; the pull ended at 2:42
    burst(60)
    attractiv = rep.player("Attractîv")
    lines = await cooldowns.cooldown_lines(rep, f, attractiv, [cast(1, 40)])
    assert "  Burst [1] (dps): 1/2 at 0:40 (died before it was ready again)" in lines
    burst(80)
    assert "  Burst [1] (dps): 1/2 at 1:30" in await cooldowns.cooldown_lines(rep, f, p, [cast(1, 90)])  # after the end
    no_phases = {**f, "phaseTransitions": []}
    assert "  Burst [1] (dps): 1/2 at 0:10 (ready again at 1:30, unused)" in await cooldowns.cooldown_lines(
        rep, no_phases, p, [cast(1, 10)])
    assert cooldowns.one_per_use([0, 10_000]) == [0, 10_000]  # a cooldown of 10 s and more, pressed again on time


async def test_a_cooldown_back_after_the_wipe_was_decided_was_no_missed_use(rep, pull, burst):
    f, p, cast = pull  # pull 13: decided at 2:32 (the enrage); Enfo died at 2:35
    burst(60)
    assert "  Burst [1] (dps): 1/3 at 1:35" in await cooldowns.cooldown_lines(rep, f, p, [cast(1, 95)])  # back at 2:35


async def test_on_a_kill_cooldowns_count_to_its_end_and_a_cast_of_the_same_name_is_the_spell(rep, burst):
    kill = rep.fight(2, "vashnik")  # 6:56
    p = rep.player("Enfo")
    burst(60)
    rep.abilities[2] = "Burst"  # the same spell under another id (another rank)
    lines = await cooldowns.cooldown_lines(rep, kill, p, [{"abilityGameID": 2, "timestamp": kill["startTime"] + 1000}])
    assert lines[0].endswith(": none")  # nothing up at the pull
    assert "  Burst [1] (dps): 1/7 at 0:01 (ready again at 1:01, unused)" in lines
