"""The guild's nights (report/night.py): who raided which nights. The night's overview and progress are pinned by
the tool cases (test_tools.py)."""
from wipebot.core.pulls import GuildBest, wipe_pcts
from wipebot.core.report import night, views


async def test_kill_rankings_are_kept_once_every_kill_is_ranked(rep, patch_wcl):
    kills = [f["id"] for f in rep.fights if f["kill"]]
    assert kills
    asked = []
    ranked = {"speed": {"rankPercent": 40}, "execution": {"rankPercent": 60}}

    def rankings(code, fight_id, metric):
        asked.append(fight_id)
        return [{"fightID": i, **(ranked if len(asked) > 1 or i != kills[0] else {})} for i in kills]
    patch_wcl(rep, rankings=rankings)
    every = {i: {"speed": 40, "execution": 60} for i in kills}
    assert await night.kill_rankings(rep) == {i: v for i, v in every.items() if i != kills[0]}  # one not yet
    assert await night.kill_rankings(rep) == every  # asked again
    assert await night.kill_rankings(rep) == every
    assert len(asked) == 2


async def test_attendance_counts_a_night_once_and_leaves_out_other_difficulties(rep, patch_wcl):
    rep.guild_id, rep.zone_id, rep.guild_name = 14169, 53, "The Copper Lanterns"
    hour = 3600 * 1000
    t = rep.end_ms - 2 * 86400 * 1000
    logs = [{"code": "A", "startTime": t,
             "players": [{"name": "Torvek", "presence": 1}, {"name": "Mirelle", "presence": 2}]},
            {"code": "B", "startTime": t + hour, "players": [{"name": "Mirelle", "presence": 1}]},  # same night
            {"code": "ALT", "startTime": t + 26 * hour, "players": [{"name": "Torvek", "presence": 1}]},  # Heroic
            {"code": "C", "startTime": t + 48 * hour, "players": [{"name": "Torvek", "presence": 1},
                                                                   {"name": "Brightleaf", "presence": 2}]},
            # A raider's Mythic pug logged to the guild's page: mostly players from outside the guild.
            {"code": "PUG", "startTime": t + 30 * hour, "players": [{"name": "Torvek", "presence": 1},
                                                                     {"name": "Stranger", "presence": 1},
                                                                     {"name": "Other", "presence": 1}]}]
    reports = ([{"code": c, "pulls_by_difficulty": {5: 9}} for c in ("A", "B", "C", "PUG")]
               + [{"code": "ALT", "pulls_by_difficulty": {4: 9}}])
    patch_wcl(rep, attendance=logs, guild_reports=reports, guild_members={"Torvek", "Mirelle", "Brightleaf"})
    lines = (await night.attendance(rep, 2)).splitlines()
    assert "(2 nights;" in lines[0] and "Mythic raid nights" in lines[0]
    assert lines[1].endswith("2 players (A, B)")  # two logs of one evening; Mirelle there in one, benched in the other
    assert lines[-2].endswith("guild's page): PUG (1 of 3 in the guild)")
    assert lines[-1] == "Nights raided, of 2: Torvek 2, Mirelle 1, Brightleaf 0 (+1 benched)"


async def test_a_boss_night_counts_its_pulls_once_with_the_kill_the_best_wipe_and_the_endings(rep):
    f = rep.by_boss[rep.boss("sszorak")][-1]
    one = await night.boss_night([rep], rep.start_ms, f["encounterID"], f.get("difficulty"))
    pulls = night.boss_pulls(rep, f["encounterID"], f.get("difficulty"))
    assert one and one.pulls == len(pulls) and sum(k for _, k in one.ends) == len(pulls)
    assert one.kill is None and one.best and one.best[0] == min(w for w in wipe_pcts(pulls))
    # the same night logged twice (two raiders' logs) counts each pull once
    again = await night.boss_night([rep, rep], rep.start_ms, f["encounterID"], f.get("difficulty"))
    assert again == one
    assert await night.boss_night([rep], rep.start_ms, 999_999, f.get("difficulty")) is None


def test_weeks_read_night_by_night_with_the_best_wipe_trend():
    nights = [night.BossNight(1_760_040_000_000, 33, None, (34.1, 42), (("the boss gaining X and keeping it to the "
                                                                          "end", 20), ("Mutilate killing many", 8))),
              night.BossNight(1_760_640_000_000, 14, 14, (2.0, 12), (("a kill", 1), ("deaths one after another", 9)))]
    text = night.weeks_text("Sszorak", 2, nights)
    assert text.splitlines()[1:] == [
        "  Thu 2025-10-09: 33 pulls, no kill, best wipe 34.1% (pull 42); ended by the boss gaining X and keeping it "
        "to the end 20, Mutilate killing many 8",
        "  Thu 2025-10-16: 14 pulls, killed on pull 14, best wipe 2.0% (pull 12), further than any night before; ended "
        "by deaths one after another 9",
        "Best wipe night by night: 34.1 → kill on pull 14"]
    assert night.weeks_text("Sszorak", 2, []) == "The guild's raid nights of the last 2 weeks have no pulls on Sszorak."


def test_weeks_name_a_best_wipe_by_its_phase_where_phases_have_their_own_health():
    altar = night.BossNight(1_760_040_000_000, 21, None, (70.5, 16), (("Venom Rupture killing many", 12),), "abc",
                            "P2", 68.3)
    text = night.weeks_text("The Coiled Altar", 1, [altar])
    assert "best wipe P2 68.3% (WCL 70.5%) (pull 16)" in text
    assert text.endswith("Best wipe night by night (WCL %): 70.5")
    # each % comes with how the text writes it, which the card prints (its bars are drawn from the numbers)
    assert views.weeks_row(altar) == {"day": "Thu 9 Oct", "start_ms": 1_760_040_000_000, "report": "abc",
                                      "pulls": 21, "kill": None, "pct": 70.5, "pct_text": "70.5%", "pull": 16,
                                      "phase": "P2", "health": 68.3, "health_text": "68.3%",
                                      "ended": ["Venom Rupture killing many", 12]}


def test_progress_says_whether_the_night_beat_the_guild_best():
    """A lower % got further: "P2 64.0%" against "P2 68.3%" read backwards once, so code says which won."""
    before = GuildBest(62.6, "abc", 21, 13, 1_760_040_000_000)
    pulls = [{"id": 1, "kill": False, "fightPercentage": 70.0, "startTime": 0, "endTime": 120_000},
             {"id": 2, "kill": False, "fightPercentage": 65.0, "startTime": 0, "endTime": 150_000}]
    marks, best = night._marks(pulls, before)
    # A mark is set off from its row (" · "), and the first pull is no "best until then": it always is
    assert marks == {2: " · best of the night until then"}
    assert night._record(before, pulls, best, False) == (
        " Tonight's best wipe, 65.0% on pull 2, didn't get as far: the guild's best stands.")
    marks, best = night._marks(pulls, GuildBest(68.0, "abc", 21, 13, 1_760_040_000_000))
    assert marks == {2: " · best of the night until then, new guild best"}
    marks, best = night._marks(pulls, GuildBest(75.0, "abc", 21, 13, 1_760_040_000_000))
    assert marks[1] == " · new guild best" and best == (2, pulls[1], 65.0)
    kill = [{**pulls[0], "kill": True}, {**pulls[1], "fightPercentage": 100.0, "bossPercentage": 100.0}]
    assert night._marks(kill, None)[0][1] == " · KILL"


async def test_the_nights_killer_splits_off_the_deaths_in_hits_killing_many(rep):
    # Virulence killed most on the tests' night, 57, but 40 of those came in hits that killed many at once: the
    # raid's to stop. The players it killed are named only for the rest, a killer one death behind is named with it,
    # and so is the ability that killed most outside such hits.
    line = str(await night._nights_killer(rep, rep.by_boss["Sszorak"]))
    assert line.startswith(
        "The night's killer (deaths until each wipe was decided): Virulence [1300089, 1312189], 57 deaths over 32 "
        "pulls, 40 of them in hits killing many (the raid's to stop, no one player's), the first death on 8 pulls; "
        "killed most outside those hits: ")
    assert " Close behind: Mutilate [1285999], 55 deaths." in line
    assert (" Outside hits killing many, the most deaths were to Tempest [1287083]: 48 deaths on 22 pulls, against "
            "17 to Virulence; killed most: ") in line


async def test_the_night_at_a_glance_names_a_boss_difficulty_once_and_its_length_in_hours(rep, monkeypatch):
    f = rep.fight(13, "Sszorak")
    text = await night.overview(rep)
    assert ", 2:54:43 long, " in text and "- Sszorak (Mythic): 33 pulls (1 reset), 54:57 in combat" in text
    # A boss pulled on two difficulties in one night carries the difficulty in its name: it printed
    # "Sszorak (Mythic) (Mythic)".
    monkeypatch.setattr(rep, "_mixed", {f["encounterID"]})
    monkeypatch.setattr(rep, "by_boss", {rep.boss_key(f): rep.by_boss["Sszorak"]})
    text = await night.overview(rep)
    assert "- Sszorak (Mythic): 33 pulls" in text and "(Mythic) (Mythic)" not in text


async def test_each_pull_folds_the_post_into_the_row_and_says_the_guild_best_once(rep, monkeypatch):
    # With each_pull every pull's post used to follow the rows: 33 posts, each with the guild's best and its link,
    # the first death and the % its row has. The row now carries what only the post had: the first three deaths, the
    # killing blows until the wipe was decided and how many died after it. The guild's best stands once, above.
    before = GuildBest(40.0, "abcDEF123", 7, 4, 1_700_000_000_000.0)

    async def guild_best(fight: dict) -> GuildBest:
        return before
    monkeypatch.setattr(rep, "guild_best", guild_best)
    plain, each = await night.progress(rep, "Sszorak"), await night.progress(rep, "Sszorak", each_pull=True)
    assert each.count("https://www.warcraftlogs.com/reports/abcDEF123#fight=7") == 1 and "**" not in each
    rows = [line for line in each.splitlines() if line.startswith("  ")]
    assert len(rows) == len(rep.by_boss["Sszorak"]) == 33
    assert rows[12] == (
        "  13: 62.6% P1 (2nd time) 2:42, ended by the boss gaining Unbound Ferocity and keeping it to the end, decided "
        "at 2:32; first deaths Scopidruid (Ula'tek's Presence, 0:48) · Draiman (no killing blow recorded, 0:53) · "
        "Attractîv (Venomous Surge, 1:28); killed by Ula'tek's Presence ×1 · no killing blow recorded ×1 · Venomous "
        "Surge ×1 · Raging Crosswinds ×1 (15 later deaths: fallout); boss Unbound Ferocity from 2:32 · best of the "
        "night until then")
    # what the row says is what the pull's post says, for every pull
    for f, row in zip(rep.by_boss["Sszorak"], rows, strict=True):
        post = (await rep.summary(f)).splitlines()
        first = next((line.removeprefix("First deaths: ") for line in post if line.startswith("First deaths")), None)
        killed = next((line for line in post if line.startswith("Killed by")), None)
        assert first is None or f"; first deaths {first}; killed by " in row
        assert killed is None or killed.split(": ", 1)[1] in row
    # the rest of the answer is the plain one's, but for the legend
    assert each.splitlines()[len(rows) + 2:] == plain.splitlines()[len(rows) + 2:]
    assert "the first 3 deaths, the killing blows of the deaths until the wipe was decided, how many died after it" \
        in each.splitlines()[0]
    assert each.startswith("Sszorak (Mythic): 33 pulls on the night (") and plain.startswith("Sszorak: 33 pulls on")
