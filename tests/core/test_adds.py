"""The adds of a pull (report/adds.py): how many came and died, and how their casts ended."""
from wipebot.core.pulls import first_by_name
from wipebot.core.report.adds import _finish_orphans, came_of


def test_as_many_came_as_died_when_more_die_than_the_listed_actor_counts():
    # Vashnik's Malignant Totems die under two actors; the one the pull lists counted 36 where 46 died
    totems = {"id": 141, "instanceCount": 36}
    assert came_of(totems, {141: [60_000.0] * 46}) == 46
    assert came_of(totems, {141: [60_000.0] * 30}) == 36


def test_a_cast_whose_begin_is_logged_on_another_instance_finishes_that_begin():
    # A Drowned Echo began Soulcoiler's Curse as one instance and the log has the cast under another: Warcraft
    # Logs counts one cast that went through, not one ended otherwise and another gone through
    def key(e: dict) -> tuple[str, str]:
        return "Drowned Echo", "Soulcoiler's Curse"
    out = {("Drowned Echo", "Soulcoiler's Curse"): [(300_000, "kicked", "Pxl", 1),
                                                    (316_200, "ended otherwise", None, 1)]}
    _finish_orphans(out, [{"timestamp": 331_000, "sourceID": 348, "abilityGameID": 1}], key)
    assert [how for _, how, _, _ in out[("Drowned Echo", "Soulcoiler's Curse")]] == ["kicked", "went through"]
    # with nothing open it's a later stage of a cast already counted (Hungering Pyre's second cast event)
    _finish_orphans(out, [{"timestamp": 400_000, "sourceID": 348, "abilityGameID": 1}], key)
    assert len(out[("Drowned Echo", "Soulcoiler's Curse")]) == 2


def test_a_buff_both_bosses_gain_is_listed_once_from_the_first():
    assert first_by_name([("Soulbound", 428_000.0), ("Soulbound", 428_100.0), ("Enrage", 500_000.0)]) == [
        ("Soulbound", 428_000.0), ("Enrage", 500_000.0)]


async def test_a_cast_an_add_logs_as_it_is_kicked_is_the_kick_and_not_one_gone_through(rep, patch_wcl):
    """Spiteful Soulcoiler logs a cast of a Wail of Terror as it's kicked, at the kick or a few ms before it: the
    cast was kicked (Warcraft Logs' table counts 483 of 673 kicked; read as gone through, the tool said 6). A cast
    with no kick near it still went through."""
    from wipebot.core.report.adds import interruptible_casts
    f = rep.fight(13)
    t0, kicker = f["startTime"], f["friendlyPlayers"][0]
    add, wail = 9_001, next(iter(rep.abilities))
    rep.actors[add] = {"id": add, "name": "Spiteful Soulcoiler", "type": "NPC"}
    events = rep.wcl.events

    def cast(t: int, kind: str = "cast") -> dict:
        return {"timestamp": t0 + t, "type": kind, "sourceID": add, "sourceInstance": 1, "abilityGameID": wail}

    async def answer(code, fight_id, data_type, **filters):
        if data_type == "Casts":
            return [cast(1_000, "begincast"), cast(2_000),  # logged with the kick
                    cast(3_000, "begincast"), cast(4_998),  # logged 2 ms before the kick
                    cast(6_000, "begincast"), cast(8_000)]  # gone through
        if data_type == "Interrupts":
            return [{"timestamp": t0 + t, "type": "interrupt", "sourceID": kicker, "targetID": add,
                     "targetInstance": 1, "abilityGameID": 1766, "extraAbilityGameID": wail} for t in (2_000, 5_000)]
        return await events(code, fight_id, data_type, **filters)
    patch_wcl(rep, events=answer)
    casts = (await interruptible_casts(rep, f))[(rep.actor(add), rep.ability(wail))]
    assert [how for _, how, _, _ in casts] == ["kicked", "kicked", "went through"]
