"""A player's cooldowns over a pull (report/cooldowns.py): used against how often they could have been, as Lorrgs
lists the spec's spells and the player's talents allow."""
import pytest

from tests.support import RESTO_SPELLS
from wipebot.core import knowledge
from wipebot.core.report import cooldowns
from wipebot.core.text import clock

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


async def test_a_pvp_talent_lorrgs_still_lists_is_not_a_missed_cooldown(rep, pull, fake_knowledge, monkeypatch):
    f, p, cast = pull
    # Outside the tree: a baseline cooldown (judged) and a PvP talent (Emerald Communion for a Preservation Evoker)
    spells = [{"spell_id": 2825, "name": "Bloodlust", "cooldown": 300.0, "tags": ["raid_cd"]},
              {"spell_id": 370960, "name": "Emerald Communion", "cooldown": 180.0, "tags": ["raid_cd"]}]
    talents = {"talentTree": [{"id": 101912, "nodeID": 81032}]}

    async def details(fight):
        return {p.id: {"combatantInfo": talents}}

    async def spec_spells(player):
        return spells
    fake_knowledge(tree_for=TREE, pvp_talent=lambda spell_id: spell_id == 370960)
    monkeypatch.setattr(rep, "details", details)
    monkeypatch.setattr(rep, "spells", spec_spells)
    lines = "\n".join(await cooldowns.cooldown_lines(rep, f, p, []))
    assert "  Bloodlust [2825] (raid_cd): 0/1" in lines
    assert "Emerald Communion [" not in lines
    assert "missed cooldown): Emerald Communion" in lines


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
    assert cooldowns.aura_end(auras, 0, 99000) == 41000
    assert cooldowns.aura_end(auras, 5, 99000) == 99000


def test_a_press_lasts_as_long_as_the_buff_it_put_on_its_caster():
    # Lorrgs follows a Restoration Shaman's Ascendance by its buff and gives it no duration, so how long a press
    # lasted is the log's to say. On The Twin Fangs kill (dTQMvNbBcPh6mDrA fight 23) the press 1:52.990 into the pull
    # has its buff from a millisecond before the cast to 2:10.990, 18 s.
    def aura(kind: str, ms: int) -> dict:
        return {"type": kind, "abilityGameID": 114052, "timestamp": ms}
    pull_end = 480_000
    assert cooldowns.buff_end(112_990, [aura("applybuff", 112_989), aura("removebuff", 130_990)], pull_end) == 130_990
    # A proc (Deeply Rooted Elements) puts the same buff up for 6 s with no cast. No press near it: nobody's press.
    # A press while one is up shows as a refresh, and lasts until the buff is removed.
    procs = [aura("applybuff", 40_000), aura("removebuff", 46_000),
             aura("applybuff", 100_000), aura("refreshbuff", 103_000), aura("removebuff", 121_000)]
    assert cooldowns.buff_end(103_000, procs, pull_end) == 121_000
    assert cooldowns.buff_end(60_000, procs, pull_end) is None  # the proc at 0:40 isn't the buff of a press at 1:00
    # Its buff is the one gained nearest the press: not the refresh of one that ran out just before it
    after = [aura("applybuff", 94_000), aura("refreshbuff", 99_200), aura("removebuff", 99_600),
             aura("applybuff", 100_000), aura("removebuff", 118_000)]
    assert cooldowns.buff_end(100_000, after, pull_end) == 118_000
    # Removed and gained again at once (within REFRESH_MS), it runs on; still up when the pull ends, it ends with it
    again = [aura("applybuff", 10_000), aura("removebuff", 28_000), aura("applybuff", 28_020),
             aura("removebuff", 34_000)]
    assert cooldowns.buff_end(10_000, again, pull_end) == 34_000
    assert cooldowns.buff_end(470_000, [aura("applybuff", 470_000)], pull_end) == pull_end
    # No buff came with the press (an instant, a totem, whose buff is the totem's): nothing measured
    assert cooldowns.buff_end(10_000, [], pull_end) is None
    assert cooldowns.buff_end(10_000, [aura("removebuff", 10_000)], pull_end) is None


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


async def test_the_top_comparisons_phase_is_named_as_every_tool_names_it(rep, monkeypatch):
    """Pull 33 of the tests' night goes P1, P2 and back to P1 at 2:05. warcraft_cli numbers the phases in the order
    the pull went through them, so compare_with_top with phase=3 said "phase P3 (2:05-2:50)" for the stretch
    player_pull calls "P1 Sszorak (2nd time)"."""
    f = rep.fight(33)
    back = f["phaseTransitions"][2]["startTime"] - f["startTime"]
    assert cooldowns.phase_at(rep, f, back) == "P1 Sszorak (2nd time)"
    asked = []

    async def packet(code, fight_id, player, phase):
        asked.append(phase)
        return {"phase": {"selected": {"phase": 3, "label": "P3", "start_ms": back, "end_ms": 170_000}},
                "player": {"name": "Draiman", "spec_slug": "rogue-assassination"}, "comparison": {"status": "none"}}
    monkeypatch.setattr(knowledge.top_parses, "cooldown_comparison", packet)
    text = await knowledge.cooldown_comparison_text(rep.code, f["id"], "Draiman", 3,
                                                    lambda at_ms: cooldowns.phase_at(rep, f, at_ms))
    assert asked == [3]
    assert text.startswith(f"Draiman (rogue-assassination), phase P1 Sszorak (2nd time) ({clock(back)}-2:50) of this "
                           "pull.")
    assert "P3" not in text
    # Without the pull's phases to name it by, the stretch is its times alone: never warcraft_cli's own number.
    bare = await knowledge.cooldown_comparison_text(rep.code, f["id"], "Draiman", 3)
    assert bare.startswith(f"Draiman (rogue-assassination), {clock(back)}-2:50 of this pull.")


async def test_the_top_comparisons_moment_spells_are_counted_apart_from_its_cooldowns(rep, monkeypatch):
    """me-healer-kill: compare_with_top counted Earth Elemental (a defensive) among a Restoration Shaman's
    cooldowns as "3/5", and an answer advised casting it because most top players had. Defensive, tank and movement
    spells answer a moment, so they get their own line; each top player's timeline still lists them."""
    def spell(name: str, *tags: str) -> dict:
        return {"spell": {"name": name, "tags": list(tags)}}

    async def packet(code, fight_id, player, phase):
        return {"phase": {"selected": {"start_ms": 0}},
                "player": {"name": "Enfo", "spec_slug": "shaman-restoration"},
                "comparison": {"status": "ready", "phase_sample_count": 2, "selected_phase_spell_frequency": [
                    {**spell("Ascendance", "raid_cd"), "sample_count": 2, "total_casts": 4},
                    {**spell("Spiritwalker's Grace", "move"), "sample_count": 2, "total_casts": 5},
                    {**spell("Potion of Recklessness"), "sample_count": 1, "total_casts": 1},
                    {**spell("Earth Elemental", "defensive"), "sample_count": 1, "total_casts": 1}],
                    "samples": [{"player": {"name": "Torvek"}, "region": "EU", "report_id": "a", "duration_ms": 300_000,
                                 "selected_phase_casts": [{**spell("Earth Elemental", "defensive"),
                                                           "timestamp_ms": 30_000}]}]}}
    monkeypatch.setattr(knowledge.top_parses, "cooldown_comparison", packet)
    lines = (await knowledge.cooldown_comparison_text(rep.code, 2, "Enfo")).split("\n")
    assert lines[3:6] == [
        "How many of them cast each cooldown in this phase, and total casts: Ascendance 2/2 (4 casts); Potion of "
        "Recklessness 1/2 (1 cast)",
        "Defensive, tank and movement spells, which answer a moment (a big hit, a death, movement), so how often "
        "they cast them isn't a fault: Spiritwalker's Grace 2/2 (5 casts); Earth Elemental 1/2 (1 cast)",
        "  Torvek (EU, report a, kill 5:00): Earth Elemental at 0:30"]
