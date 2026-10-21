"""How a pull ended (report/fight.py's ended_by and pull_summary), on made-up deaths: the real pulls' read is pinned
by the tool cases (test_tools.py) and the summaries (test_pulls.py)."""
import itertools
import time

import pytest

from tests.support import CODE, FakeWCL, Night
from wipebot.core import server
from wipebot.core.report import fight

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


def test_one_ability_killing_many_is_found_once_and_with_its_span(rep, deaths):
    # 20 players: 4 deaths to one ability within 2 s (the edge included) are one hit killing many; the 2 to it just
    # after that hit are a hit of their own, too few; the boss's buff after the wipe was decided isn't an event.
    died = deaths((10.0, SWIPE), (10.1, VENOM), (11.0, VENOM), (11.4, VENOM), (12.1, VENOM), (12.6, VENOM),
                  (13.0, VENOM))
    text = fight.ended_by(rep, PULL, died, {}, 20, [("Frenzy", 50_000), ("Rage", 60_000)],
                          (55_000, "the boss gained Frenzy"))
    assert text == ("Ended by (from the log, earliest first): Venom Burst [901] killed 4 within 2 s at 0:10 (one "
                    "ability killing many at once: find which mechanic failed); then the boss gained Frenzy at 0:50 "
                    "and kept it to the end, 13 of 20 alive (look it up: an enrage makes every later death fallout).")


def test_in_a_small_raid_three_dying_at_once_is_a_hit_killing_many(rep, deaths):
    died = deaths((20.0, CRASH), (20.3, CRASH), (20.5, CRASH), (30.0, SWIPE), (35.0, SWIPE))
    text = fight.ended_by(rep, PULL, died, {}, 10, [], (35_000, "half the raid was dead"))
    assert text == ("Ended by (from the log, earliest first): Shadow Crash [903] killed 3 within 1 s at 0:20 (one "
                    "ability killing many at once: find which mechanic failed); then half the raid was dead at 0:35.")
    assert "killed 3" not in str(fight.ended_by(rep, PULL, died, {}, 20, [], None))  # 20 players: 4 at least


def test_a_wipe_nothing_decided_was_called_off_and_one_still_going_has_no_end(rep, deaths):
    died = deaths((30.0, SWIPE), (40.0, CRASH))
    assert fight.ended_by(rep, PULL, died, {}, 20, [], None) == "Ended by: called off at 2:00 with 18 of 20 alive."
    assert fight.ended_by(rep, {**PULL, "inProgress": True}, died, {}, 20, [], None) == (
        "Ended by: not yet, the pull is still going (2:00 logged so far).")
    assert fight.ended_by(rep, {**PULL, "kill": True}, died, {}, 20, [], None) is None


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
