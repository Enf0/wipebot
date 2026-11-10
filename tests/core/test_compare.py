"""A pull next to a kill (report/compare.py), on made-up health tracks: the real comparison is pinned by a local
tool case (test_tools.py), since another guild's kill can't be published."""
import json
from typing import cast

from wipebot.core.report import Report, compare


class Rep:
    """What the pace line reads of a report: nothing decided the wipe, no buff at the end, no deaths."""

    async def decided_at(self, f):
        return None

    async def end_buffs(self, f):
        return []

    async def keep(self, key, deps, get, *a):
        return []

    async def deaths(self, f):
        return []

    def phase(self, f, phase_id):
        return f"P{phase_id}"


OURS, KILL = {"startTime": 0, "endTime": 120_000}, {"startTime": 0, "endTime": 200_000}


async def pace(ours: list[tuple[float, float]], theirs: list[tuple[float, float]]) -> str:
    return await compare._pace(cast(Report, Rep()), OURS, KILL, "CODE", 1, {"health": ours}, {"health": theirs},
                               compare.spans(OURS, KILL))


async def test_a_pull_behind_early_and_ahead_at_the_end_is_ahead_not_minus_points_behind():
    """Slow start, fast end: 10 points behind at 0:30, 10 ahead at 2:00. It said "-10 points behind at 2:00"."""
    text = await pace([(0, 100), (30_000, 90), (60_000, 70), (90_000, 40), (120_000, 20)],
                      [(0, 100), (30_000, 80), (60_000, 60), (90_000, 45), (120_000, 30)])
    assert "-" not in text.split("):", 1)[1]
    assert "behind from 0:30 (10 points), no longer by 1:30, 10 points ahead at 2:00 (ours 20%, the kill's 30%)" in text


async def test_a_pull_behind_to_the_end_says_since_when_and_by_how_much():
    text = await pace([(0, 100), (30_000, 90), (60_000, 80), (120_000, 60)],
                      [(0, 100), (30_000, 80), (60_000, 60), (120_000, 30)])
    assert "behind from 0:30 (10 points), 30 points behind at 2:00 (ours 60%, the kill's 30%)" in text


async def test_a_pull_that_kept_the_kills_pace_says_where_it_stood_at_the_end():
    text = await pace([(0, 100), (60_000, 61), (120_000, 20)], [(0, 100), (60_000, 60), (120_000, 30)])
    assert "kept it (within 3 points or ahead), 10 points ahead at 2:00 (ours 20%, the kill's 30%)" in text
    close = await pace([(0, 100), (120_000, 31)], [(0, 100), (120_000, 30)])
    assert "kept it (within 3 points or ahead), 1 point behind at 2:00 (ours 31%, the kill's 30%)" in close
    level = await pace([(0, 100), (120_000, 30.4)], [(0, 100), (120_000, 30)])
    assert "kept it (within 3 points or ahead), level with it at 2:00 (ours 30%, the kill's 30%)" in level


# The Coiled Altar, made up: Zul'jan (game id 501) is the boss of P1 and dies as it ends; Hex Lord Malacrass (502)
# stands at full health until P2 and is the boss from there.
ZULJAN, MALACRASS = 501, 502


def boss(game_id: int, health: list[tuple[float, float]], power: list[tuple[float, float]] | None = None) -> dict:
    return {"game_id": game_id, "name": {ZULJAN: "Zul'jan", MALACRASS: "Hex Lord Malacrass"}[game_id],
            "health": health, "power": power or []}


def test_both_pulls_follow_the_same_boss_through_a_fight_with_several():
    """Ours wiped in P2, the kill went on for minutes after it: whichever boss cast most in each, the health
    compared is Zul'jan's while he lived, then Malacrass's, on both sides, and so is the power."""
    ours = [boss(ZULJAN, [(0, 100), (60_000, 50), (137_000, 0)], [(0, 0), (60_000, 80), (130_000, 10)]),
            boss(MALACRASS, [(0, 100), (140_000, 100), (200_000, 60), (360_000, 3)], [(150_000, 20), (300_000, 90)])]
    kill = [boss(MALACRASS, [(0, 100), (130_000, 100), (190_000, 50), (270_000, 0)], [(140_000, 30)]),
            boss(ZULJAN, [(0, 100), (60_000, 40), (128_000, 0)], [(0, 0), (60_000, 85)])]
    mine, theirs, who = compare.same_boss(ours, kill)
    assert mine["health"] == [(0, 100), (60_000, 50), (137_000, 0), (140_000, 100), (200_000, 60), (360_000, 3)]
    assert theirs["health"] == [(0, 100), (60_000, 40), (128_000, 0), (130_000, 100), (190_000, 50), (270_000, 0)]
    assert mine["power"] == [(0, 0), (60_000, 80), (130_000, 10), (150_000, 20), (300_000, 90)]
    assert theirs["power"] == [(0, 0), (60_000, 85), (140_000, 30)]
    assert who == "Zul'jan, then Hex Lord Malacrass from 2:17 (the kill 2:08)"
    assert compare._at(theirs["health"], 60_000) == "40"  # the kill's Zul'jan in P1, not Malacrass at 100


def test_a_boss_only_the_kill_reached_is_followed_by_the_kill_alone():
    ours = [boss(ZULJAN, [(0, 100), (60_000, 50)]), boss(MALACRASS, [(0, 100), (60_000, 100)])]
    kill = [boss(ZULJAN, [(0, 100), (128_000, 0)]), boss(MALACRASS, [(0, 100), (130_000, 100), (270_000, 0)])]
    mine, theirs, who = compare.same_boss(ours, kill)
    assert mine["health"] == [(0, 100), (60_000, 50)]
    assert theirs["health"] == [(0, 100), (128_000, 0), (130_000, 100), (270_000, 0)]
    assert who == "Zul'jan (the kill then Hex Lord Malacrass from 2:08)"


def test_two_bosses_hit_from_the_start_are_followed_in_one_order_on_both_sides():
    """A council fought side by side: no boss is first by the log, so both sides take the same one first."""
    ours = [boss(MALACRASS, [(1_000, 99), (100_000, 0)]), boss(ZULJAN, [(1_000, 98), (200_000, 30)])]
    kill = [boss(ZULJAN, [(2_000, 99), (150_000, 0)]), boss(MALACRASS, [(1_000, 99), (90_000, 0)])]
    mine, theirs, _ = compare.same_boss(ours, kill)
    assert mine["health"][0] == (1_000, 98) and theirs["health"][0] == (2_000, 99)  # Zul'jan on both


OURS_PHASES = {"startTime": 0, "endTime": 360_000, "kill": False, "encounterID": 1,
               "phaseTransitions": [{"id": 1, "startTime": 0}, {"id": 2, "startTime": 137_000}]}
KILL_PHASES = {"startTime": 1_000_000, "endTime": 1_500_000, "kill": True, "encounterID": 1,
               "phaseTransitions": [{"id": 1, "startTime": 1_000_000}, {"id": 2, "startTime": 1_128_000},
                                    {"id": 3, "startTime": 1_269_000}]}


def test_moments_are_compared_inside_a_phase_counted_from_its_start():
    """P2 began at 2:17 for us and 2:08 for the kill: 30 s into it is 2:47 of ours and 2:38 of the kill's, and the
    kill's later phases, which ours never reached, are compared with nothing."""
    spans = compare.spans(OURS_PHASES, KILL_PHASES)
    assert spans == [compare.Span(1, 0, 0, 137_000, 128_000), compare.Span(2, 137_000, 128_000, 223_000, 141_000)]
    moments = compare.moments(spans, 30_000, 360_000)
    assert [(m.phase, m.into, m.ours, m.kill) for m in moments][:5] == [
        (1, 30_000, 30_000, 30_000), (1, 60_000, 60_000, 60_000), (1, 90_000, 90_000, 90_000),
        (1, 120_000, 120_000, 120_000), (2, 30_000, 167_000, 158_000)]
    assert moments[-1].phase == 2 and moments[-1].into == 120_000  # the kill left P2 after 2:21
    assert all(m.ours <= 150_000 for m in compare.moments(spans, 30_000, 150_000))  # up to a moment of ours


def test_a_fight_without_phases_is_compared_on_one_clock_to_the_end_of_ours():
    f, k = {"startTime": 0, "endTime": 100_000}, {"startTime": 50, "endTime": 60_050}
    spans = compare.spans(f, k)
    assert spans == [compare.Span(None, 0, 0, 100_000, 60_000)]
    assert [m.ours for m in compare.moments(spans, 30_000, 100_000, past_kill=True)] == [30_000, 60_000, 90_000]
    assert [m.ours for m in compare.moments(spans, 30_000, 100_000)] == [30_000, 60_000]


async def test_the_pace_on_a_fight_with_phases_is_read_inside_each_phase():
    """Ours entered P2 nine seconds after the kill: its P2 is compared with the kill's P2 from each one's start,
    not with whatever phase the kill was in at the same time of the pull."""
    ours = {"health": [(0, 100), (137_000, 0), (140_000, 100), (197_000, 80), (257_000, 60)]}
    kill = {"health": [(0, 100), (128_000, 0), (130_000, 100), (188_000, 70), (248_000, 40), (269_000, 0)]}
    text = await compare._pace(cast(Report, Rep()), OURS_PHASES, KILL_PHASES, "CODE", 1, ours, kill,
                               compare.spans(OURS_PHASES, KILL_PHASES))
    assert text.startswith("Pace against the kill (boss health at the same moment of each phase, counted from its "
                           "start, while both were in it, until our pull ended at 6:00): behind from P2 +1:00 (10 "
                           "points), 20 points behind at P2 +2:20 (ours 60%, the kill's 40%). Deaths by P2 +1:00")


class Graphs:
    """Warcraft Logs for a made-up fight with two bosses, whose actor ids differ between the two reports: the
    enemies' casts with their power, and the Resources graph of health (every enemy) or one boss's power."""

    def __init__(self, zuljan: int, malacrass: int, casts_most: int, p2_at: float):
        self.ids, self.casts_most, self.p2_at = (zuljan, malacrass), casts_most, p2_at

    async def events(self, code, fight_id, data_type, **filters):
        return [{"sourceID": i, "classResources": [{"type": 3}]} for i in self.ids
                for _ in range(9 if i == self.casts_most else 2)] + [{"sourceID": 77, "classResources": [{"type": 0}]}]

    async def graph(self, code, fight, data_type, **filters):
        t0, (zuljan, malacrass) = fight["startTime"], self.ids
        if filters["abilityID"] == 1000:
            return {"series": [
                {"id": zuljan, "data": [[t0, 100], [t0 + 60_000, 50], [t0 + self.p2_at - 2_000, 0]]},
                {"id": malacrass, "data": [[t0, 100], [t0 + self.p2_at, 100], [t0 + self.p2_at + 60_000, 70]]},
                {"id": 77, "data": [[t0, 100], [t0 + 5_000, 0]]}]}  # an add
        return {"series": [{"id": filters["sourceID"], "data": [[t0 + 10_000, 40]]}]}


class AltarRep(Rep):
    code, bosses = "OURS", {21, 24}

    def __init__(self, ours: Graphs, kill: Graphs):
        self.graphs = {"OURS": ours, "KILL": kill}

    @property
    def wcl(self):
        rep = self

        class Either:
            async def events(self, code, *a, **kw):
                return await rep.graphs[code].events(code, *a, **kw)

            async def graph(self, code, *a, **kw):
                return await rep.graphs[code].graph(code, *a, **kw)
        return Either()

    def actor(self, actor_id):
        return {21: "Zul'jan", 24: "Hex Lord Malacrass"}[actor_id]

    async def keep(self, key, deps, get, *a):
        return json.loads(json.dumps(await get()))  # as the cache on disk gives it back


async def test_a_wipe_and_a_kill_of_a_fight_with_several_bosses_follow_the_same_boss():
    """Ours wiped in P2 with Zul'jan having cast most; in the kill Malacrass cast most. Each pull used to follow its
    own busiest boss, so the kill's column read Malacrass at 100% through P1 beside our Zul'jan."""
    f = {**OURS_PHASES, "id": 3, "enemyNPCs": [{"id": 21, "gameID": ZULJAN, "instanceCount": 1},
                                              {"id": 24, "gameID": MALACRASS, "instanceCount": 1},
                                              {"id": 77, "gameID": 9, "instanceCount": 4}]}
    k = {**KILL_PHASES, "id": 5, "bosses": [{"id": 8, "name": "Hex Lord Malacrass", "gameID": MALACRASS},
                                            {"id": 9, "name": "Zul'jan", "gameID": ZULJAN},
                                            {"id": 12, "name": "Another night's boss", "gameID": 600}]}
    rep = AltarRep(Graphs(21, 24, casts_most=21, p2_at=137_000), Graphs(9, 8, casts_most=8, p2_at=128_000))
    ours, theirs, who = await compare._boss_tracks(cast(Report, rep), f, k, "KILL", 5)
    assert who == "Zul'jan, then Hex Lord Malacrass from 2:15 (the kill 2:06)"
    assert (compare._at(ours["health"], 60_000), compare._at(theirs["health"], 60_000)) == ("50", "50")
    assert (compare._at(ours["health"], 200_000), compare._at(theirs["health"], 190_000)) == ("70", "70")
    assert ours["power"] == theirs["power"] == [(10_000, 40)]  # Zul'jan's own bar on both


async def test_two_pulls_side_by_side_state_each_pulls_killers_once(rep):
    """The post names a wipe's killers until it was decided, up to five: the full list comes only where the post
    cut it or has none (a kill), and the fallout's killers always."""
    pulls = rep.by_boss["Sszorak"]
    posts = [await rep.summary(f) for f in pulls]
    cut = next(n for n, post in enumerate(posts, 1) if "other abilit" in post)
    first, second, third = (await compare.compare_pulls(rep, 13, cut) + "\n\n"
                            + await compare.compare_pulls(rep, 2, 2, "Vashnik")).split("\n\n")[:3]  # 2: the kill
    assert "All killers until" not in first and first.count("Ula'tek's Presence ×1") == 2  # the post, the fallout
    assert "\nAll killers after the wipe was decided (fallout): " in first
    assert "\nAll killers until the wipe was decided (" in second
    assert "\nAll killers: " in third and "fallout" not in third
