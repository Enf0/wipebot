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
