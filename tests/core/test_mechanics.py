"""Who got hit by what (report/mechanics.py), on made-up hits over the captured night's pulls: the real abilities'
read is pinned by the tool cases (test_tools.py)."""
from collections import Counter

import pytest

from wipebot.core.report import mechanics
from wipebot.core.report.base import JOBS_NOT_LOGGED, FactError

POOL = 904  # a made-up ability


@pytest.fixture
def hits(rep, patch_wcl):
    """hits(damage, debuffs): Warcraft Logs answers with these events of POOL (each its `fight`); the night's
    deaths as they are."""
    rep.abilities[POOL] = "Toxic Pool"
    events = rep.wcl.events

    def answer(damage: list[dict], debuffs: list[dict], killed: int | None = None) -> None:
        """`killed`: a player whose deaths were all to POOL."""
        async def table(code, fight_id, data_type, **filters):
            return {"entries": [{"guid": POOL}]} if data_type == "DamageTaken" else {"entries": []}

        async def fake(code, fight_id, data_type, **filters):
            ids = fight_id if isinstance(fight_id, list) else [fight_id]
            if data_type == "DamageTaken":
                return [e for e in damage if e["fight"] in ids]
            if filters.get("abilityID") == POOL:
                return [e for e in debuffs if e["fight"] in ids]
            found = await events(code, fight_id, data_type, **filters)
            if data_type == "Deaths" and killed is not None:
                return [{**d, "killingAbilityGameID": POOL} if d["targetID"] == killed else d for d in found]
            return found
        patch_wcl(rep, table=table, events=fake)
    return answer


def event(f: dict, who: int, s: float, kind: str, **more) -> dict:
    """An event of POOL on a player `s` seconds into a pull."""
    return {"targetID": who, "timestamp": f["startTime"] + s * 1000, "type": kind, "fight": f["id"],
            "abilityGameID": POOL, "sourceID": -1, **more}


async def test_one_pulls_hits_count_until_the_wipe_was_decided(rep, hits):
    # Pull 13, decided at 2:32 (the enrage): Attractîv is hit three times from 1:25 (twice while the debuff from
    # the first was up: 1.0 s and 1.85 s after the hit before) and dies at 1:28; Sinnatony is hit once; Enfo's hit
    # at 2:40 is fallout, and Enfo's debuff from 0:20 stays to the end. A pet's hit is no player's.
    f = rep.fight(13)
    a, e, s = (rep.player(n).id for n in ("Attractîv", "Enfo", "Sinnatony"))
    hits([event(f, a, 85, "damage", amount=100, absorbed=50), event(f, a, 86, "damage", amount=100),
          event(f, a, 86.5, "damage", amount=10, tick=True), event(f, a, 87.85, "damage", amount=100),
          event(f, s, 30, "damage", amount=100), event(f, e, 160, "damage", amount=100),
          event(f, 99_999, 30, "damage", amount=100)],
         [event(f, a, 85, "applydebuff"), event(f, a, 88, "removedebuff"), event(f, e, 20, "applydebuff")])
    lines = (await mechanics.mechanic_hits(rep, "toxic pool", 13)).splitlines()
    assert lines[0].startswith("Toxic Pool on Sszorak pull 13 until the wipe was decided (the boss's enrage or half "
                               "the raid dead, 2:32; whole_pull for the rest). Per player: hits (and periodic ticks), "
                               "damage incl. absorbed and ticks, times the debuff was applied (applications over the "
                               "whole pull, not stacks)")
    assert lines[1:] == [
        "  Attractîv: 3 hits (+1 tick), 360, 1 application, 1 death soon after, 0 with it as the killing blow; 2 hits "
        "with the debuff up: 1 within 1 s (one hit or two, the log can't tell), 1 later (1.9 s)",
        "  Sinnatony: 1 hit, 100, 0 applications, 0 deaths soon after, 0 with it as the killing blow",
        "  Enfo: 0 hits, 0, 1 application, 0 deaths soon after, 0 with it as the killing blow",
        "Total with Toxic Pool as the killing blow: 0 (1 death within 6 s of a hit: the deaths it set up, by any "
        "killing blow)",
        "Each time Toxic Pool went on a player (pull, when, who, how long they kept it):",
        "  Sszorak pull 13 0:20: Enfo, kept to the end of the pull",
        "  Sszorak pull 13 1:25: Attractîv, kept 3 s",
        JOBS_NOT_LOGGED]  # who was hit isn't whose job it was
    vashnik = rep.fight(1, "vashnik")
    hits([event(vashnik, a, 30, "damage", amount=100)], [])
    assert (await mechanics.mechanic_hits(rep, "Toxic Pool", 1, "vashnik")).startswith(
        "Toxic Pool on Vashnik the Malignant pull 1 until the wipe was decided")


async def test_a_debuff_applied_again_while_up_starts_anew_and_a_refresh_changes_nothing(rep, hits):
    f = rep.fight(13)
    e = rep.player("Enfo").id
    hits([], [event(f, e, 5, "removedebuff"), event(f, e, 20, "applydebuff"), event(f, e, 30, "refreshdebuff"),
              event(f, e, 40, "applydebuff"), event(f, e, 50, "removedebuff")])
    applied: Counter = Counter()
    spans = await mechanics._debuff_spans(rep, [f["id"]], [POOL], lambda e: True, applied)
    t0 = f["startTime"]
    assert spans == {e: [[t0 + 20_000, t0 + 40_000], [t0 + 40_000, t0 + 50_000]]} and applied == {e: 2}


async def test_outliers_sum_minutes_alive_and_deaths_over_the_night(rep, hits):
    # On pulls 12 and 13 Attractîv takes twice what Enfo does, and over the night's pulls is alive 29.7 minutes
    # before each wipe was decided to Enfo's 44.3: 3.0 times Enfo's (the median's) damage per minute alive. 22
    # players were in some pull. A pet's hit on Enfo is friendly fire.
    a, e, s = rep.player("Attractîv"), rep.player("Enfo"), rep.player("Sinnatony")
    rep.actors[5555] = {"id": 5555, "name": "Wolf", "type": "Pet"}
    damage = [event(f, p.id, 5, "damage", amount=amount)
              for f in (rep.fight(12), rep.fight(13)) for p, amount in ((a, 120_000), (e, 60_000), (s, 1_000))]
    damage.append(event(rep.fight(13), e.id, 6, "damage", amount=500_000, sourceID=5555))
    hits(damage, [], killed=a.id)
    text = await mechanics.mechanic_outliers(rep, boss="Sszorak", all_pulls=True)
    assert "all 32 Sszorak pulls (resets left out)" in text.splitlines()[0]
    pool = next(line for line in text.splitlines() if line.startswith("  Toxic Pool"))
    assert pool == ("  Toxic Pool [904]: 3 of 22 took it (few players: avoided or assigned), median 2.71k/min, total "
                    "362.00k; most: Attractîv 3.0x (14 killed)")  # each of Attractîv's 14 deaths before a decision


async def test_outliers_of_a_kill_run_to_its_end(rep, hits):
    kill = rep.fight(2, "vashnik")
    hits([event(kill, rep.player("Enfo").id, (kill["endTime"] - kill["startTime"]) / 1000, "damage", amount=1_000)],
         [])
    text = await mechanics.mechanic_outliers(rep, 2, "vashnik")
    assert text.startswith("Enemy abilities on Vashnik the Malignant pull 2, each pull until the wipe was decided")
    assert "  Toxic Pool [904]: 1 of 20 took it" in text


async def test_a_window_counts_only_that_stretch_of_each_pull(rep, hits):
    # Pull 13: Enfo's debuff from 0:20 and Sinnatony's hit at 0:30 are before the window; Attractîv's from 1:25 in it.
    f = rep.fight(13)
    a, e, s = (rep.player(n).id for n in ("Attractîv", "Enfo", "Sinnatony"))
    hits([event(f, a, 85, "damage", amount=100), event(f, s, 30, "damage", amount=100)],
         [event(f, a, 85, "applydebuff"), event(f, a, 88, "removedebuff"), event(f, e, 20, "applydebuff")])
    text = await mechanics.mechanic_hits(rep, "toxic pool", 13, start="1:00", end="2:00")
    assert "pull 13, from 1:00 to 2:00 of each" in text.splitlines()[0]
    assert "  Attractîv: 1 hit, 100, 1 application" in text and "Sinnatony" not in text
    assert "  Sszorak pull 13 1:25: Attractîv, kept 3 s" in text and "0:20: Enfo" not in text
    with pytest.raises(FactError, match="m:ss from the pull's start"):
        await mechanics.mechanic_hits(rep, "toxic pool", 13, start="2:00", end="1:00")
