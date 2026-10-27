"""A pull's deterministic summary (pulls.py): every pull of the captured night pinned in
fixtures/real/<code>.summaries.md, and the rules behind it."""
from tests.support import CODE, REAL, FakeWCL, assert_snapshot
from wipebot.core.pulls import (
    Decided,
    GuildBest,
    best_marks,
    end_buffs,
    format_summary,
    is_reset,
    lost_buffs,
    pct_text,
    phase_label,
    summarize,
    wipe_pct,
    wipe_pcts,
    wipe_text,
)
from wipebot.core.report import Report


def routine(night, fight) -> set[int]:
    """Buffs the boss lost during earlier pulls on it, as the follower has seen them by this pull."""
    return {a for f in night.fights if f["encounterID"] == fight["encounterID"] and f["id"] < fight["id"]
            for a in lost_buffs(f, night.boss_buffs[f["id"]])}


async def summaries(night) -> str:
    """Every pull's summary, as the tools give it: through Report.summary."""
    rep = Report(FakeWCL(night).client(), CODE)
    await rep.load()
    return "\n\n".join([await rep.summary(f) for f in sorted(rep.fights, key=lambda f: f["id"])]) + "\n"


async def test_summaries_match_snapshot(night):
    assert_snapshot(REAL / f"{CODE}.summaries.md", await summaries(night))


def summary(night, fight_id):
    f = next(f for f in night.fights if f["id"] == fight_id)
    return summarize(f, night.fights, night.deaths[fight_id], night.players, night.abilities,
                     night.boss_buffs[fight_id], routine(night, f))


def test_pull_numbers_count_per_boss(night):
    numbers = {}
    for f in night.fights:
        numbers.setdefault(f["name"], []).append(summary(night, f["id"]).pull_no)
    for boss, nos in numbers.items():
        assert nos == list(range(1, len(nos) + 1)), boss


def test_best_is_lowest_earlier_wipe(night):
    for f in night.fights:
        s = summary(night, f["id"])
        earlier = [g["bossPercentage"] for g in night.fights
                   if g["name"] == f["name"] and g["startTime"] < f["startTime"] and not g["kill"] and not is_reset(g)]
        assert s.best_pct == (min(earlier) if earlier else None)


def test_deaths_are_in_order_and_named(night):
    for f in night.fights:
        s = summary(night, f["id"])
        assert [d.at_ms for d in s.deaths] == sorted(d.at_ms for d in s.deaths)
        assert all(not d.player.startswith("#") for d in s.deaths)


def test_kill_summary(night):
    kill = next(f for f in night.fights if f["kill"])
    text = format_summary(summary(night, kill["id"]))
    assert "**KILL**" in text and "\n" not in text


def test_a_pull_without_a_boss_percentage_is_summarized():
    fight = {"id": 1, "name": "Boss", "encounterID": 1, "difficulty": 5, "kill": False,
             "bossPercentage": None, "lastPhase": 0, "startTime": 0, "endTime": 60_000}
    earlier = {**fight, "id": 0, "bossPercentage": 40.0, "startTime": -100_000, "endTime": -50_000}
    text = format_summary(summarize(fight, [earlier, fight], [], {}, {}))
    assert text == "**Boss** (Mythic) · pull 2 · wipe (boss health not logged) · 1:00"


def test_an_unnamed_killing_blow_shows_its_id():
    fight = {"id": 1, "name": "Boss", "encounterID": 1, "difficulty": 5, "kill": False,
             "bossPercentage": 40.0, "lastPhase": 0, "startTime": 0, "endTime": 60_000}
    deaths = [{"timestamp": 30_000, "targetID": 7, "killingAbilityGameID": 123}]
    s = summarize(fight, [fight], deaths, {7: "Torvek"}, {})
    assert s.deaths[0].ability == "spell 123"


def test_missing_killing_blow_is_labelled():
    fight = {"id": 1, "name": "Boss", "encounterID": 1, "difficulty": 5, "kill": False,
             "bossPercentage": 40.0, "lastPhase": 2, "startTime": 0, "endTime": 60_000}
    deaths = [{"timestamp": 30_000, "targetID": 7}]
    s = summarize(fight, [fight], deaths, {7: "Torvek"}, {})
    assert s.deaths[0].ability == "no killing blow recorded"
    assert "Torvek (no killing blow recorded, 0:30)" in format_summary(s)


def test_a_pull_still_going_is_no_wipe_and_no_best():
    fight = {"id": 2, "name": "Boss", "encounterID": 1, "difficulty": 5, "kill": False, "inProgress": True,
             "fightPercentage": 30.0, "lastPhase": 0, "startTime": 0, "endTime": 60_000}
    earlier = {**fight, "id": 1, "inProgress": False, "fightPercentage": 50.0, "startTime": -100_000,
               "endTime": -50_000}
    text = format_summary(summarize(fight, [earlier, fight], [], {}, {}))
    assert text == "**Boss** (Mythic) · pull 2 · **in progress**, the boss at 30.0% so far · 1:00"
    assert wipe_pcts([earlier, fight]) == [50.0] and not is_reset({**fight, "fightPercentage": 100.0})


def test_end_buffs_are_kept_to_the_end_and_not_routine():
    fight = {"startTime": 0, "endTime": 100_000}
    events = [
        {"timestamp": 1_000, "type": "applybuff", "abilityGameID": 1, "targetID": 9},  # setup, too early
        {"timestamp": 20_000, "type": "applybuff", "abilityGameID": 2, "targetID": 9},  # came and went
        {"timestamp": 30_000, "type": "removebuff", "abilityGameID": 2, "targetID": 9},
        {"timestamp": 90_000, "type": "applybuff", "abilityGameID": 3, "targetID": 9},  # enrage
        {"timestamp": 99_500, "type": "removebuff", "abilityGameID": 3, "targetID": 9},  # the boss resetting
        {"timestamp": 95_000, "type": "applybuff", "abilityGameID": 4, "targetID": 9},  # lost on an earlier pull
    ]
    assert end_buffs(fight, events, routine={4}) == [(3, 90_000)]
    assert lost_buffs(fight, events) == {2}


def test_phase_label_counts_returns():
    assert phase_label({"lastPhase": 0}) is None
    assert phase_label({"lastPhase": 2, "phaseTransitions": [{"id": 1}, {"id": 2}]}) == "P2"
    assert phase_label({"lastPhase": 1, "phaseTransitions": [{"id": 1}, {"id": 2}, {"id": 1}]}) == "P1 (2nd time)"
    assert phase_label({"lastPhase": 2, "lastPhaseIsIntermission": True}) == "I2"


def test_a_boss_whose_phases_start_a_new_health_bar_is_counted_by_phase():
    p2 = {"lastPhase": 2, "phaseTransitions": [{"id": 1}, {"id": 2}], "bossPercentage": 68.34, "fightPercentage": 70.5}
    assert wipe_text(p2) == "P2 68.3% (WCL 70.5%)" and wipe_text(p2, wcl=False) == "P2 68.3%"
    one_bar = {"lastPhase": 1, "phaseTransitions": [{"id": 1}], "bossPercentage": 62.6, "fightPercentage": 62.6}
    assert wipe_text(one_bar) == "62.6%"  # phases on one health bar: the boss's % is the wipe's
    assert wipe_text({"lastPhase": 0, "bossPercentage": 40.0, "fightPercentage": 40.0}) == "40.0%"


def test_resets_are_left_out_of_best_and_trend(night):
    reset = next(f for f in night.fights if is_reset(f))
    later = next(f for f in night.fights if f["startTime"] > reset["startTime"] and not f["kill"])
    s = summary(night, later["id"])
    assert reset["bossPercentage"] not in s.trend and s.best_pct != reset["bossPercentage"]
    assert summary(night, reset["id"]).reset


WIPE = {"id": 1, "name": "Boss", "encounterID": 1, "difficulty": 5, "kill": False, "fightPercentage": 40.0,
        "lastPhase": 0, "startTime": 0, "endTime": 60_000}


def buff(at: float, ability: int, kind: str = "applybuff") -> dict:
    return {"timestamp": at, "type": kind, "abilityGameID": ability, "targetID": 9}


def test_how_far_a_wipe_got_is_wcls_fight_percentage_else_the_bosss_health():
    assert wipe_pct({"fightPercentage": 83.3, "bossPercentage": 76.6}) == 83.3
    assert wipe_pct({"bossPercentage": 76.6}) == 76.6
    assert pct_text(None) == "boss % unknown" and pct_text(62.63) == "62.6%"
    assert is_reset({**WIPE, "fightPercentage": 99.0}) and not is_reset({**WIPE, "fightPercentage": 98.9})
    assert not is_reset({**WIPE, "fightPercentage": None})  # no % logged: an attempt


def test_end_buffs_count_from_the_pull_and_its_edges():
    # A pull late in the log: grace and window are measured from its start and end; a buff gained twice counts
    # from its first gain; the buffs come in the order gained.
    t0 = 1_000_000
    fight = {"startTime": t0, "endTime": t0 + 100_000}
    events = [buff(t0 + 5_000, 1), buff(t0 + 75_000, 2), buff(t0 + 70_000, 3), buff(t0 + 80_000, 2),
              buff(t0 + 69_999, 4)]
    assert end_buffs(fight, events) == [(3, 70_000), (2, 75_000)]
    assert end_buffs({**fight, "startTime": t0 - 65_000}, [buff(t0 + 70_000, 1)]) == [(1, 135_000)]
    assert end_buffs({"startTime": t0, "endTime": t0 + 20_000}, [buff(t0 + 4_999, 1), buff(t0 + 5_000, 2)]) == [
        (2, 5_000)]
    assert lost_buffs(fight, [buff(t0 + 99_000, 1, "removebuff"), buff(t0 + 98_999, 2, "removebuff")]) == {2}


def test_a_wipe_with_no_death_after_the_decision_or_none_says_plainly_what_killed():
    deaths = [{"timestamp": 30_000, "targetID": 7, "killingAbilityGameID": 1}]
    for decided in (None, 45_000):
        s = summarize(WIPE, [WIPE], deaths, {7: "Torvek"}, {1: "Tempest"},
                      decided=Decided(decided) if decided else None)
        assert format_summary(s).splitlines()[-1] == "Killed by: Tempest ×1"


def test_a_difficulty_or_buff_the_tables_dont_name_reads_as_its_number():
    s = summarize({**WIPE, "difficulty": 8}, [WIPE], [], {}, {}, [buff(50_000, 77)])
    assert (s.difficulty, s.boss_buffs) == ("difficulty 8", [("spell 77", 50_000)])


def test_a_kill_or_a_pull_still_going_has_no_buffs_at_the_end():
    assert summarize({**WIPE, "kill": True}, [WIPE], [], {}, {}, [buff(50_000, 77)]).boss_buffs == []
    assert summarize({**WIPE, "inProgress": True}, [WIPE], [], {}, {}, [buff(50_000, 77)]).boss_buffs == []


def test_a_pull_still_going_without_a_percentage_says_only_that_it_is_going():
    s = summarize({**WIPE, "inProgress": True, "fightPercentage": None}, [WIPE], [], {}, {})
    assert format_summary(s) == "**Boss** (Mythic) · pull 1 · **in progress** · 1:00"


def test_a_new_best_must_beat_both_tonight_and_the_guilds_earlier_logs():
    before = GuildBest(30.0, "abc", 4, 9, 0)
    assert best_marks(40.0, 40.0, None) == ["best of this log 40.0%"]  # a tie isn't a new best
    assert best_marks(25.0, 20.0, before)[1].startswith("guild best before this log 30.0%")  # tonight was better
    assert best_marks(30.0, None, before)[0].startswith("guild best before this log")  # a tie with the guild's
    assert best_marks(29.0, None, before)[0].startswith("**new guild best!**")


def test_buffs_at_the_end_are_listed_in_the_order_gained():
    s = summarize(WIPE, [WIPE], [], {}, {1: "Frenzy", 2: "Enrage"}, [buff(55_000, 1), buff(50_000, 2)])
    assert format_summary(s).splitlines()[1] == "Boss buffs at the end: **Enrage** (from 0:50) · **Frenzy** (from 0:55)"
