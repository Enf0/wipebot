"""How a pull ended (report/ending.py and pull_summary), on made-up deaths: the real pulls' read is pinned
by the tool cases (test_tools.py) and the summaries (test_pulls.py)."""
import itertools
import time
from collections import Counter

import pytest

from tests.support import CODE, FakeWCL, Night
from wipebot.core import server
from wipebot.core.pulls import Decided
from wipebot.core.report import fight
from wipebot.core.report.ending import ending, ending_ability, ending_text, lead

T0 = 1_000_000  # a pull late in the log: times in the text are from its start
PULL = {"id": 1, "kill": False, "fightPercentage": 40.0, "startTime": T0, "endTime": T0 + 120_000}
VENOM, SWIPE, CRASH = 901, 902, 903


@pytest.fixture
def deaths(rep):
    """deaths((seconds, ability), ...): one player each, killed by a made-up ability."""
    rep.abilities |= {VENOM: "Venom Burst", SWIPE: "Tail Swipe", CRASH: "Shadow Crash"}
    ids = itertools.count(1)
    return lambda *at: [{"targetID": next(ids), "timestamp": T0 + s * 1000, "killingAbilityGameID": a}
                        for s, a in at]


def ended_by(*args):
    return ending_text(ending(*args))


def test_one_ability_killing_many_is_found_once_and_with_its_span(rep, deaths):
    # 20 players: 4 deaths to one ability within 2 s (the edge included) are one hit killing many; the 2 to it just
    # after that hit are a hit of their own, too few; the boss's buff after the wipe was decided isn't an event.
    died = deaths((10.0, SWIPE), (10.1, VENOM), (11.0, VENOM), (11.4, VENOM), (12.1, VENOM), (12.6, VENOM),
                  (13.0, VENOM))
    text = ended_by(rep, PULL, died, {}, 20, [("Frenzy", 50_000), ("Rage", 60_000)],
                          Decided(55_000, "Frenzy"))
    assert text == ("Ended by (from the log, earliest first): Venom Burst [901] killed 4 within 2 s at 0:10 (one "
                    "ability killing many at once: find which mechanic failed); then the boss gained Frenzy at 0:50 "
                    "and kept it to the end, 13 of 20 alive (look it up: an enrage makes every later death fallout).")


def test_in_a_small_raid_three_dying_at_once_is_a_hit_killing_many(rep, deaths):
    died = deaths((20.0, CRASH), (20.3, CRASH), (20.5, CRASH), (30.0, SWIPE), (35.0, SWIPE))
    text = ended_by(rep, PULL, died, {}, 10, [], Decided(35_000))
    assert text == ("Ended by (from the log, earliest first): Shadow Crash [903] killed 3 within 1 s at 0:20 (one "
                    "ability killing many at once: find which mechanic failed); then half the raid was dead at 0:35.")
    assert "killed 3" not in str(ended_by(rep, PULL, died, {}, 20, [], None))  # 20 players: 4 at least


def test_a_wipe_nothing_decided_was_called_off_and_one_still_going_has_no_end(rep, deaths):
    died = deaths((30.0, SWIPE), (40.0, CRASH))
    assert ended_by(rep, PULL, died, {}, 20, [], None) == "Ended by: called off at 2:00 with 18 of 20 alive."
    assert ended_by(rep, {**PULL, "inProgress": True}, died, {}, 20, [], None) == (
        "Ended by: not yet, the pull is still going (2:00 logged so far).")
    assert ended_by(rep, {**PULL, "kill": True}, died, {}, 20, [], None) is None



def died_as(*at: tuple[float, int, int]) -> list[dict]:
    """Deaths (seconds, ability, player id), for players the night doesn't have (no role) unless the id is one."""
    return [{"targetID": who, "timestamp": T0 + s * 1000, "killingAbilityGameID": a} for s, a, who in at]


def test_one_ability_killing_in_pulses_over_a_few_seconds_is_one_moment(rep, deaths):
    # A set of three hits about 3 s apart, two deaths each: never four within 2 s, six within 6 s.
    died = deaths((40.0, VENOM), (40.2, VENOM), (43.0, VENOM), (43.1, VENOM), (45.5, VENOM), (45.8, VENOM))
    assert ended_by(rep, PULL, died, {}, 20, [], Decided(47_000)).startswith(
        "Ended by (from the log, earliest first): Venom Burst [901] killed 6 in pulses within 6 s from 0:40 (one "
        "ability killing many in a few seconds: find which mechanic failed)")
    # an unnamed killing blow isn't one ability: no pulses
    unnamed = [{**d, "killingAbilityGameID": None} for d in died]
    assert "in pulses" not in str(ended_by(rep, PULL, unnamed, {}, 20, [], Decided(47_000)))


def test_the_deaths_leading_into_a_moment_are_named_and_lead_when_they_are_many(rep, deaths):
    # Four die one after another from 1:10 (another brought back, one long before), then one hit kills four at 1:40.
    rep.abilities |= {VENOM: "Venom Burst", SWIPE: "Tail Swipe", CRASH: "Shadow Crash"}
    died = died_as((5.0, SWIPE, 901), (70.0, SWIPE, 902), (80.0, SWIPE, 903), (85.0, SWIPE, 904), (90.0, CRASH, 905),
                   (95.0, SWIPE, 906), (100.0, VENOM, 907), (100.2, VENOM, 908), (100.4, VENOM, 909),
                   (100.6, VENOM, 910))
    backs: dict[int, list[float]] = {903: [T0 + 81_000.0]}  # 903 battle-ressed: not one of the deaths that stayed
    e = ending(rep, PULL, died, backs, 20, [], None)
    assert e and e.lead_in and (e.lead_in.first_ms, e.lead_in.deaths, e.lead_in.tanks) == (70_000, 4, 0)
    assert str(ending_text(e)).endswith("Before it, 4 deaths one after another from 1:10, none brought back: what each "
                                        "left short may be what lost it.")
    assert lead(e) == "deaths from 1:10"  # four is a fifth of 20
    fewer = ending(rep, PULL, died[4:], backs, 20, [], None)  # two deaths before it: named, but don't lead
    assert fewer and fewer.lead_in and fewer.lead_in.deaths == 2 and lead(fewer) == "ability: Venom Burst"
    # the mechanic a post reads up on first: the one ending it, none when the deaths before it lead
    assert ending_ability(fewer) == "Venom Burst" and ending_ability(e) is None


def test_a_cascade_starts_at_the_first_death_that_stayed(rep, deaths):
    died = died_as((10.0, SWIPE, 901), (50.0, SWIPE, 902), (55.0, CRASH, 903), (60.0, SWIPE, 904))
    e = ending(rep, PULL, died, {901: [T0 + 15_000.0]}, 6, [], Decided(60_000))
    assert e and e.kind == "cascade" and e.first_death_ms == 50_000
    assert ending_ability(e) is None

async def test_a_pull_still_going_has_no_buffs_at_the_end_and_is_logged_so_far():
    night = Night()
    last = night.fights[-1]
    live = {**last, "id": 999, "inProgress": True, "startTime": last["endTime"] + 60_000,
            "endTime": last["endTime"] + 90_000}
    night.fights.append(live)
    night.deaths[999] = []
    night.boss_buffs[999] = [{"timestamp": live["endTime"] - 1_000, "type": "applybuff", "abilityGameID": 1,
                              "targetID": next(iter(night.bosses))}]
    rep = await server.Library(wcl=FakeWCL(night, time.time() * 1000).client()).report(CODE)
    pull = len(rep.by_boss["Sszorak"])
    assert await rep.end_buffs(rep.fight(pull)) == []
    text = await fight.pull_summary(rep, pull)
    assert "Ended by: not yet, the pull is still going (0:30 logged so far)." in text
    assert "counted):\nnone\n" in text  # no deaths yet
    assert text.endswith("\nLogged so far at 0:30 with the boss at 63.1% health.")


async def test_a_death_of_a_player_warcraft_logs_gave_no_details_for_is_named(rep):
    del rep.players["scopidruid"]  # the first to die on pull 13
    assert "\n1. 0:48 Scopidruid - " in await fight.pull_summary(rep, 13)


def test_the_boss_health_follows_the_boss_of_the_next_phase():
    """The Coiled Altar: Zul'jan (121) dies at the end of P1 and Hex Lord Malacrass (124) is the boss of P2, from
    its own 100%; an enemy at full health the whole pull (130) never takes over."""
    series: dict[int, list[tuple[float, float]]] = {
        121: [(0, 100), (160_000, 50), (167_000, 0)], 124: [(175_000, 100), (230_000, 81)],
        130: [(0, 100), (240_000, 100)]}
    track = fight._taken_over(series, 121, Counter({121: 20, 124: 5, 130: 9}))
    assert track == [(0, 100), (160_000, 50), (167_000, 0), (175_000, 100), (230_000, 81)]
    assert fight._taken_over({121: [(0, 100), (240_000, 40)], 124: [(0, 100), (240_000, 60)]}, 121,
                             Counter({121: 9})) == [(0, 100), (240_000, 40)]  # a boss alive to the end stays
