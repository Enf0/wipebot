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
                               "the raid dead, 2:32; whole_pull for the rest). Per player: hits on them (and periodic "
                               "ticks), damage incl. absorbed and ticks, times the debuff was applied (applications "
                               "over the whole pull, not stacks)")
    # The caveat on a hit with the debuff up is said once, above the rows, and a death soon after a hit is of any
    # cause: "the deaths it set up" claimed more than the log shows.
    assert lines[0].endswith("; then, if any, hits while the debuff from an earlier hit was still up: those within "
                             "1 s of the hit before (one hit or two, the log can't tell) and those later, with how "
                             "long after:")
    assert lines[1:] == [
        "  Attractîv: 3 hits (+1 tick), 360, 1 application, 1 death within 6 s, 0 killing blows; 2 hits with the "
        "debuff up: 1 within 1 s, 1 later (1.9 s)",
        "  Sinnatony: 1 hit, 100, 0 applications, 0 deaths within 6 s, 0 killing blows",
        "  Enfo: 0 hits, 0, 1 application, 0 deaths within 6 s, 0 killing blows",
        "Total with Toxic Pool as the killing blow: 0; deaths of any cause within 6 s of a hit: 1",
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
    spans = await mechanics._debuff_spans(rep, [f["id"]], [POOL], lambda e: True, applied, Counter())
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


async def test_a_night_ranks_by_hits_per_minute_of_the_pulls_each_was_in(rep, hits):
    # Algmor was in 10 of the night's 32 pulls and Enfo in all of them. Algmor is hit four times in theirs, Enfo
    # six times: by raw hits Enfo led, though Algmor was hit more than twice as often while there.
    algmor, enfo = rep.player("Algmor"), rep.player("Enfo")
    theirs = [f for f in rep.attempts(rep.by_boss["Sszorak"])[0] if algmor.id in rep.pull_players(f)]
    assert len(theirs) == 10
    hits([event(f, algmor.id, 5 + i, "damage", amount=100) for f in theirs[:2] for i in range(2)]
         + [event(f, enfo.id, 5, "damage", amount=100) for f in rep.by_boss["Sszorak"][:6]], [])
    lines = (await mechanics.mechanic_hits(rep, "toxic pool", boss="Sszorak", all_pulls=True)).splitlines()
    assert ". Per player, most hits per minute of the pulls they were in first (each until its wipe was decided; a " \
           "player who sat out pulls is ranked by the ones they were in): hits on them (and periodic ticks)" in lines[0]
    assert lines[1:3] == ["  Algmor: 4 hits in 10 pulls (0.29 per minute), 400, 0 applications, 0 deaths within 6 s, "
                          "0 killing blows",
                          "  Enfo: 6 hits in 32 pulls (0.12 per minute), 600, 0 applications, 0 deaths within 6 s, 0 "
                          "killing blows"]
    assert lines[3] == "Total with Toxic Pool as the killing blow: 0"


async def test_hits_at_one_interval_with_the_debuff_up_are_a_pulse_and_not_counted_apart(rep, hits):
    # An aura logged as hits, not ticks: with the debuff up it hits every second. "11 hits with the debuff up: 6
    # within 1 s, 5 later (1.0-1.0 s)" on every row said nothing; one line says it pulses.
    f = rep.fight(13)
    a, e = (rep.player(n).id for n in ("Attractîv", "Enfo"))
    damage = [event(f, who, at + i * 1.0 + (0.004 if i % 2 else 0), "damage", amount=100)
              for who, at in ((a, 20), (e, 40)) for i in range(8)]
    hits(damage, [event(f, a, 20, "applydebuff"), event(f, a, 28, "removedebuff"),
                  event(f, e, 40, "applydebuff"), event(f, e, 48, "removedebuff")])
    lines = (await mechanics.mechanic_hits(rep, "toxic pool", 13)).splitlines()
    assert "the debuff from an earlier hit" not in lines[0] and "with the debuff up" not in "".join(lines[1:3])
    assert "  Attractîv: 8 hits, 800, 1 application, 0 deaths within 6 s, 0 killing blows" in lines[1:3]
    assert lines[3] == ("Hits with the debuff up: 14, 14 of them 1.0 s after the hit before. It hits again every 1.0 "
                        "s while the debuff is up, so the rows don't count them apart.")
    # Hits at any time after the one before are no pulse, nor are a few.
    h = mechanics.Hits()
    h.rehit_gaps[a] = [40, 120, 300, 310, 450, 900, 1000, 1200, 2500, 3900, 4500]
    assert mechanics._pulse(h) is None
    h.rehit_gaps[a] = [1000, 1002, 998]
    assert mechanics._pulse(h) is None


async def test_more_players_hit_than_rows_are_counted_and_the_total_stays(rep, hits, monkeypatch):
    # With 30 or more players hit over a night, the cut that kept the output short took the total line with the
    # players, and said nothing. Three rows here for four players hit.
    monkeypatch.setattr(mechanics, "PLAYERS_LISTED", 3)
    f = rep.fight(13)
    who = [rep.player(n).id for n in ("Attractîv", "Enfo", "Sinnatony", "Pomaa")]
    hits([event(f, w, 10 + i, "damage", amount=100) for k, w in enumerate(who) for i in range(4 - k)], [])
    for night in (False, True):
        lines = (await mechanics.mechanic_hits(rep, "toxic pool", None if night else 13, boss="Sszorak",
                                               all_pulls=night)).splitlines()
        assert [line.split(":")[0].strip() for line in lines[1:4]] == ["Attractîv", "Enfo", "Sinnatony"]
        assert lines[4] == "  and 1 more player below them in this order, left out here"
        assert lines[5] == "Total with Toxic Pool as the killing blow: 0"
        assert lines[6].startswith("The raid's per pull") == night
