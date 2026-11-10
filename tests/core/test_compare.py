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


OURS_PHASES: dict = {"startTime": 0, "endTime": 360_000, "kill": False, "encounterID": 1,
                     "phaseTransitions": [{"id": 1, "startTime": 0}, {"id": 2, "startTime": 137_000}]}
KILL_PHASES: dict = {"startTime": 1_000_000, "endTime": 1_500_000, "kill": True, "encounterID": 1,
                     "phaseTransitions": [{"id": 1, "startTime": 1_000_000}, {"id": 2, "startTime": 1_128_000},
                                          {"id": 3, "startTime": 1_269_000}]}


def test_both_pulls_follow_the_same_boss_through_a_fight_with_several():
    """Ours wiped in P2, the kill went on for minutes after it: whichever boss cast most in each, the health
    compared is Zul'jan's in P1 and Malacrass's in P2, on both sides, and so is the power."""
    ours = [boss(ZULJAN, [(0, 100), (60_000, 50), (137_000, 0)], [(0, 0), (60_000, 80), (130_000, 10)]),
            boss(MALACRASS, [(0, 100), (140_000, 100), (200_000, 60), (360_000, 3)], [(150_000, 20), (300_000, 90)])]
    kill = [boss(MALACRASS, [(0, 100), (130_000, 100), (190_000, 50), (270_000, 0)], [(140_000, 30)]),
            boss(ZULJAN, [(0, 100), (60_000, 40), (128_000, 0)], [(0, 0), (60_000, 85)])]
    mine, theirs, who = compare.same_boss(ours, kill, compare.spans(OURS_PHASES, KILL_PHASES))
    assert who == "Zul'jan, then Hex Lord Malacrass from 2:17 (the kill 2:08)"
    # each boss's stretch begins with its own value then: Malacrass at 100 as P2 begins, not Zul'jan's 0
    assert mine["health"] == [(0, 100), (60_000, 50), (137_000, 0),
                              (137_000, 100), (140_000, 100), (200_000, 60), (360_000, 3), (360_000, 3)]
    assert theirs["health"] == [(0, 100), (60_000, 40), (128_000, 0),
                                (128_000, 100), (130_000, 100), (190_000, 50), (269_000, 50), (270_000, 0)]
    assert compare._at(theirs["health"], 60_000) == "40"  # the kill's Zul'jan in P1, not Malacrass at 100
    assert (compare._at(mine["health"], 137_000), compare._at(theirs["health"], 128_000)) == ("100", "100")
    assert mine["power"] == [(0, 0), (60_000, 80), (130_000, 10), (137_000, 20), (150_000, 20), (300_000, 90),
                             (360_000, 90)]
    assert theirs["power"] == [(0, 0), (60_000, 85), (128_000, 30), (140_000, 30), (269_000, 30)]
    # Zul'jan's bar was emptied at 2:10; his 85 before Malacrass's 30 in the kill is no reset, they are two bars
    assert (compare._reset_times(mine), compare._reset_times(theirs)) == ([130_000], [])


def test_a_boss_only_the_kill_reached_is_followed_by_the_kill_alone():
    ended_in_p1 = {**OURS_PHASES, "endTime": 60_000, "phaseTransitions": OURS_PHASES["phaseTransitions"][:1]}
    ours = [boss(ZULJAN, [(0, 100), (60_000, 50)]), boss(MALACRASS, [(0, 100), (60_000, 100)])]
    kill = [boss(ZULJAN, [(0, 100), (128_000, 0)]), boss(MALACRASS, [(0, 100), (130_000, 100), (270_000, 0)])]
    mine, theirs, who = compare.same_boss(ours, kill, compare.spans(ended_in_p1, KILL_PHASES))
    assert mine["health"] == [(0, 100), (60_000, 50), (60_000, 50)]
    assert theirs["health"] == [(0, 100), (128_000, 0), (128_000, 100), (130_000, 100), (270_000, 0)]
    assert who == "Zul'jan (the kill then Hex Lord Malacrass from 2:08)"


COUNCIL, COUNCIL_KILL = {"startTime": 0, "endTime": 300_000}, {"startTime": 0, "endTime": 240_000}


def test_two_bosses_hit_from_the_start_are_followed_in_one_order_on_both_sides():
    """A council fought side by side: no boss is first by the log, so the lower game id is, on both sides."""
    ours = [boss(MALACRASS, [(1_000, 99), (100_000, 0)]), boss(ZULJAN, [(1_000, 98), (200_000, 30)])]
    kill = [boss(ZULJAN, [(2_000, 97), (150_000, 0)]), boss(MALACRASS, [(1_000, 99), (90_000, 0)])]
    mine, theirs, who = compare.same_boss(ours, kill, compare.spans(COUNCIL, COUNCIL_KILL))
    assert who.startswith("Zul'jan") and (mine["health"][0], theirs["health"][0]) == ((0, 98), (0, 97))


async def test_a_council_is_compared_boss_by_boss_at_the_same_moment_whatever_the_kill_was_on():
    """A council without phases: ours killed Zul'jan at 2:30 and went on to Malacrass, the kill had Zul'jan down
    at 2:00. Each side followed its own bosses in turn, so from 2:00 to 2:30 our Zul'jan at 20% stood beside the
    kill's Malacrass at 92%, under "the same boss on both sides", and one boss's power bar at 90 before the other's
    at 5 read as a reset on each side."""
    ours = [boss(ZULJAN, [(0, 100), (60_000, 60), (120_000, 20), (150_000, 0)], [(0, 0), (100_000, 90)]),
            boss(MALACRASS, [(0, 100), (150_000, 100), (200_000, 80), (300_000, 40)], [(0, 0), (155_000, 5)])]
    kill = [boss(ZULJAN, [(0, 100), (60_000, 50), (120_000, 0)], [(0, 0), (100_000, 90)]),
            boss(MALACRASS, [(0, 100), (120_000, 100), (130_000, 92), (180_000, 50), (240_000, 0)],
                 [(0, 0), (125_000, 5)])]
    in_phases = compare.spans(COUNCIL, COUNCIL_KILL)
    mine, theirs, who = compare.same_boss(ours, kill, in_phases)
    assert who == "Zul'jan, then Hex Lord Malacrass from 2:30"
    at = {t: (compare._at(mine["health"], t), compare._at(theirs["health"], t)) for t in (60_000, 140_000, 200_000)}
    assert at == {60_000: ("60", "50"), 140_000: ("20", "0"), 200_000: ("80", "50")}  # one boss in each pair
    assert (compare._reset_times(mine), compare._reset_times(theirs)) == ([], [])
    text = await compare._pace(cast(Report, Rep()), COUNCIL, COUNCIL_KILL, "CODE", 1, mine, theirs, in_phases)
    assert "behind from 1:00 (10 points), 80 points behind at 4:00 (ours 80%, the kill's 0%)" in text


def test_a_boss_that_is_healed_or_stands_untouched_is_not_the_one_followed():
    """In a phase where one boss is fought and the other only stands there (its health logged with its casts), or
    is healed, the one whose health falls is followed; in an intermission where none falls, the one before it."""
    phases = {"startTime": 0, "endTime": 300_000, "encounterID": 1, "phaseTransitions": [
        {"id": 1, "startTime": 0}, {"id": 2, "startTime": 100_000}, {"id": 3, "startTime": 200_000}]}
    ours = [boss(ZULJAN, [(0, 100), (50_000, 60), (150_000, 60), (160_000, 70), (250_000, 70)]),
            boss(MALACRASS, [(0, 100), (90_000, 100), (120_000, 100), (180_000, 100), (220_000, 80), (290_000, 10)])]
    _, _, who = compare.same_boss(ours, [dict(b) for b in ours], compare.spans(phases, phases))
    assert who == "Zul'jan, then Hex Lord Malacrass from 3:20"  # P2, where no health fell, stays on Zul'jan


def test_bosses_fought_side_by_side_read_as_one_boss_throughout():
    """Two bosses brought down together through three phases, their health logged a second apart: the boss
    followed first stays the one followed, with no change of boss at each phase or in the last second of the wipe."""
    phases = {"startTime": 0, "endTime": 300_000, "encounterID": 1, "phaseTransitions": [
        {"id": 1, "startTime": 0}, {"id": 2, "startTime": 100_000}, {"id": 3, "startTime": 200_000}]}
    first: list[tuple[float, float]] = [(t, 100 - t / 4_000) for t in range(2_000, 300_000, 10_000)]
    beside = [(t + 1_000, v - 0.5) for t, v in first[1:]]  # hit from 0:13 on, so the other boss was first
    ours = [boss(ZULJAN, beside), boss(MALACRASS, first)]
    mine, _, who = compare.same_boss(ours, [dict(b) for b in ours], compare.spans(phases, phases))
    assert who == "Hex Lord Malacrass"
    assert {v for _, v in mine["health"]} <= {v for _, v in first}  # nothing of the other boss, to the end


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
    enemies' casts with their power, and the Resources graph of health (every enemy) or one boss's power. As in the
    real log, the second boss's health is first logged when he comes, as P2 begins, and a pull that ended before
    it has nothing of him."""

    def __init__(self, zuljan: int, malacrass: int | None, casts_most: int, p2_at: float):
        self.zuljan, self.malacrass, self.casts_most, self.p2_at = zuljan, malacrass, casts_most, p2_at

    async def events(self, code, fight_id, data_type, **filters):
        return [{"sourceID": i, "classResources": [{"type": 3}]} for i in (self.zuljan, self.malacrass) if i
                for _ in range(9 if i == self.casts_most else 2)] + [{"sourceID": 77, "classResources": [{"type": 0}]}]

    async def graph(self, code, fight, data_type, **filters):
        t0, end = fight["startTime"], fight["endTime"]
        if filters["abilityID"] != 1000:
            return {"series": [{"id": filters["sourceID"], "data": [[t0 + 10_000, 40]]}]}
        series = {self.zuljan: [[t0, 100], [t0 + 60_000, 50], [t0 + self.p2_at - 2_000, 0]],
                  self.malacrass: [[t0 + self.p2_at, 100], [t0 + self.p2_at + 60_000, 70]],
                  77: [[t0, 100], [t0 + 5_000, 0]]}  # an add
        return {"series": [{"id": i, "data": [p for p in points if p[0] <= end]} for i, points in series.items() if i]}


class AltarRep(Rep):
    """A report whose pulls on the fight are `fights`, read from the made-up Warcraft Logs of ours and of the
    kill's report; `asked` is what was kept, by its key."""
    code, bosses = "OURS", {21, 24}

    def __init__(self, ours: Graphs, kill: Graphs, fights: list[dict]):
        self.graphs, self.fights, self.asked = {"OURS": ours, "KILL": kill}, fights, []

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
        self.asked.append(key)
        return json.loads(json.dumps(await get()))  # as the cache on disk gives it back


BOTH = [{"id": 21, "gameID": ZULJAN, "instanceCount": 1}, {"id": 24, "gameID": MALACRASS, "instanceCount": 1},
        {"id": 77, "gameID": 9, "instanceCount": 4}]
IN_P2: dict = {**OURS_PHASES, "id": 3, "enemyNPCs": BOTH}
IN_P1: dict = {**OURS_PHASES, "id": 2, "endTime": 90_000, "phaseTransitions": OURS_PHASES["phaseTransitions"][:1],
               "enemyNPCs": [BOTH[0], BOTH[2]]}  # a wipe before P2 has only the first boss in its log
THE_KILL: dict = {**KILL_PHASES, "id": 5, "bosses": [{"id": 8, "name": "Hex Lord Malacrass", "gameID": MALACRASS},
                                                     {"id": 9, "name": "Zul'jan", "gameID": ZULJAN},
                                                     {"id": 12, "name": "Another night's boss", "gameID": 600}]}


async def altar(f: dict, fights: list[dict]) -> tuple[dict, dict, str, AltarRep]:
    """Our pull `f` of a report with `fights` beside the kill, in which Malacrass cast most."""
    ours = Graphs(21, 24 if f is IN_P2 else None, casts_most=21, p2_at=137_000)
    rep = AltarRep(ours, Graphs(9, 8, casts_most=8, p2_at=128_000), fights)
    return *await compare._boss_tracks(cast(Report, rep), f, THE_KILL, "KILL", 5, compare.spans(f, THE_KILL)), rep


async def test_a_wipe_and_a_kill_of_a_fight_with_several_bosses_follow_the_same_boss():
    """Ours wiped in P2 with Zul'jan having cast most; in the kill Malacrass cast most. Each pull used to follow its
    own busiest boss, so the kill's column read Malacrass at 100% through P1 beside our Zul'jan."""
    ours, theirs, who, _ = await altar(IN_P2, [IN_P1, IN_P2])
    assert who == "Zul'jan, then Hex Lord Malacrass from 2:17 (the kill 2:08)"
    assert (compare._at(ours["health"], 60_000), compare._at(theirs["health"], 60_000)) == ("50", "50")
    assert (compare._at(ours["health"], 200_000), compare._at(theirs["health"], 190_000)) == ("70", "70")
    assert {v for _, v in ours["power"] + theirs["power"]} == {40}  # each boss's own bar, the same on both sides
    assert (compare._reset_times(ours), compare._reset_times(theirs)) == ([], [])


async def test_a_wipe_on_the_first_boss_is_set_beside_the_kills_first_boss():
    """A wipe before P2 has only Zul'jan in its log. That one boss sent it down the path of a fight with one boss,
    each pull by its busiest: the kill's column was Malacrass, at 100% while our Zul'jan fell, "50 points ahead"."""
    ours, theirs, who, rep = await altar(IN_P1, [IN_P1, IN_P2])  # another pull of the night saw both bosses
    assert who == "Zul'jan (the kill then Hex Lord Malacrass from 2:08)"
    assert (compare._at(ours["health"], 60_000), compare._at(theirs["health"], 60_000)) == ("50", "50")
    assert rep.asked == [("boss series", 2), ("boss series", "KILL", 5, ZULJAN, MALACRASS)]


async def test_a_night_that_never_saw_the_second_boss_is_still_set_beside_the_kills_first_boss():
    """No pull of the report got to P2, so nothing in it says the fight has two bosses: the kill's busiest boss,
    whose health is first logged two minutes in, shows it, and the kill is read again as our boss."""
    ours, theirs, who, rep = await altar(IN_P1, [IN_P1])
    assert who == "Zul'jan"
    assert (compare._at(ours["health"], 60_000), compare._at(theirs["health"], 60_000)) == ("50", "50")
    assert rep.asked[-1] == ("boss series", "KILL", 5, ZULJAN)  # kept apart from the kill's two bosses' above


async def test_a_fight_with_one_boss_reads_each_pulls_boss_as_before():
    """One boss on both sides: the tracks kept for the pull and for the kill, and no line about several bosses."""
    one = {**IN_P1, "enemyNPCs": IN_P1["enemyNPCs"][:1]}
    kill = {**THE_KILL, "bosses": THE_KILL["bosses"][1:]}
    rep = AltarRep(Graphs(21, None, casts_most=21, p2_at=137_000), Graphs(9, None, casts_most=9, p2_at=128_000), [one])
    ours, theirs, who = await compare._boss_tracks(cast(Report, rep), one, kill, "KILL", 5, compare.spans(one, kill))
    assert who == "" and rep.asked == [("boss tracks", 2), ("boss tracks", "KILL", 5)]
    assert (compare._at(ours["health"], 60_000), compare._at(theirs["health"], 60_000)) == ("50", "50")


def test_a_phase_the_fight_went_back_to_is_named_as_its_second_time():
    """P1, P2, then P1 again: a moment of the second P1 read "P1 +0:30", which is a moment of the first."""
    f = {"startTime": 0, "endTime": 260_000, "phaseTransitions": [
        {"id": 1, "startTime": 0}, {"id": 2, "startTime": 100_000}, {"id": 1, "startTime": 160_000}]}
    k = {"startTime": 0, "endTime": 300_000, "phaseTransitions": [
        {"id": 1, "startTime": 0}, {"id": 2, "startTime": 90_000}, {"id": 1, "startTime": 150_000}]}
    in_phases = compare.spans(f, k)
    assert [(s.phase, s.visit) for s in in_phases] == [(1, 1), (2, 1), (1, 2)]
    named = [compare._when(cast(Report, Rep()), f, m) for m in compare.moments(in_phases, 30_000, 260_000)]
    assert named == ["P1 +0:30", "P1 +1:00", "P1 +1:30", "P2 +0:30", "P2 +1:00",
                     "P1 (2nd time) +0:30", "P1 (2nd time) +1:00", "P1 (2nd time) +1:30"]
    assert compare._phase(cast(Report, Rep()), f, in_phases[2]) == "P1 (2nd time)"


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
