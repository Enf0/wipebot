"""How a pull ended (report/ending.py and pull_summary), on made-up deaths: the real pulls' read is pinned
by the tool cases (test_tools.py) and the summaries (test_pulls.py)."""
import itertools
import time
from collections import Counter

import pytest

from tests.support import CODE, FakeWCL, Night
from wipebot.core import server
from wipebot.core.pulls import Decided, GuildBest, mass_hits
from wipebot.core.report import fight, night
from wipebot.core.report.ending import (
    Ending,
    LeadIn,
    MassDeath,
    deaths_from,
    ending,
    ending_ability,
    ending_text,
    kind_of_end,
    lead,
    pull_ending,
    pull_endings,
    ruled_out,
)
from wipebot.core.text import clock

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


def test_a_wipe_no_death_decided_ended_with_the_raid_alive_and_one_still_going_has_no_end(rep, deaths):
    # The log has no word for why a raid stopped: the ending says what it shows, and claims no call.
    died = deaths((30.0, SWIPE), (40.0, CRASH))
    alive = ending(rep, PULL, died, {}, 20, [], None)
    assert ending_text(alive) == (
        "Ended by: no death. The pull ended at 2:00 with 18 of 20 alive and nothing in the log decided it: called off "
        "or reset, the log doesn't say which.")
    assert (lead(alive), kind_of_end(alive), ending_ability(alive)) == ("alive", "no death, the raid alive", None)
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


def test_a_hit_killing_many_seconds_after_a_buff_no_earlier_pull_shows_is_named_with_it(rep, deaths):
    # The first pull to show a buff can't tell an enrage from a stage's aura (a Coiled Altar pull that was the night's
    # first to reach Stage Two): a hit killing many within seconds of it is named beside it, as what the log leaves
    # open. A hit well after the buff is its fallout, and so is any hit after a buff an earlier pull has shown: the
    # deaths behind an enrage the raid has met are no mechanic to look for.
    died = deaths((20.0, SWIPE), (53.0, VENOM), (53.1, VENOM), (53.2, VENOM), (53.4, VENOM),
                  *[(70.0 + n / 10, CRASH) for n in range(5)])
    buffs: list[tuple[str, float]] = [("Frenzy", 50_000)]
    decided = Decided(50_000, "Frenzy")
    enrage = ("Ended by (from the log, earliest first): the boss gained Frenzy at 0:50 and kept it to the end, "
              "19 of 20 alive (look it up: an enrage makes every later death fallout)")
    assert ended_by(rep, PULL, died, {}, 20, buffs, decided) == enrage + "."
    assert ending_text(ending(rep, PULL, died, {}, 20, buffs, decided, unseen={"Frenzy"})) == enrage + (
        "; then Venom Burst [901] killed 4 within 1 s at 0:53 (seconds after Frenzy, which the boss had on no earlier "
        "pull in this log: fallout if that buff is an enrage, what ended the pull if it is part of the stage).")


async def test_the_deaths_behind_an_enrage_the_raid_has_met_are_no_mechanic_to_find(rep):
    # Pull 13 of the tests' night: the boss enraged at 2:32, as on earlier pulls, and Mutilated Gash killed seven a
    # second later. The enrage ended it (the experts' reading, evals/cases/wipes.toml); the hit behind it is fallout
    # and isn't named. On pull 1, the night's first sight of the buff, the hit a second after it is named with it.
    def ended(text: str) -> str:
        return next(line for line in text.splitlines() if line.startswith("Ended by"))
    assert ended(await fight.pull_summary(rep, 13, "Sszorak")) == (
        "Ended by (from the log, earliest first): the boss gained Unbound Ferocity at 2:32 and kept it to the end, "
        "16 of 20 alive (look it up: an enrage makes every later death fallout).")
    assert ended(await fight.pull_summary(rep, 1, "Sszorak")).endswith(
        "20 of 20 alive (look it up: an enrage makes every later death fallout); then Venomous Surge [1306120] killed "
        "15 within 1 s at 0:40 (seconds after Unbound Ferocity, which the boss had on no earlier pull in this log: "
        "fallout if that buff is an enrage, what ended the pull if it is part of the stage).")


async def test_the_fallout_names_only_what_decided_the_wipe(rep):
    # Pull 13: the boss's enrage at 2:32 decided the wipe, and the deaths after it say so. Pull 5: the same buff came
    # at 1:17, after half the raid was dead at 1:14: it has its own line, once, and no death is told by it.
    text = await fight.pull_summary(rep, 13)
    assert "Fallout, 15 deaths after the wipe was decided at 2:32 (the boss gained Unbound Ferocity), by" in text
    late = await fight.pull_summary(rep, 5)
    assert "Fallout, 9 deaths after the wipe was decided at 1:14 (half the raid was dead), by" in late
    assert late.count("the boss gained Unbound Ferocity at 1:17") == 1 and "Not an enrage buff: the boss gained" in late
    assert "The boss gained Unbound Ferocity" not in late  # nor a second line about a buff that decided nothing


async def test_every_buff_gained_after_the_wipe_was_decided_is_named_once_as_fallout(rep, monkeypatch):
    # Pull 5: half the raid was dead at 1:14, and the boss gained Unbound Ferocity at 1:17. Were it two buffs, the
    # line on what the wipe wasn't names both, so neither has, or needs, a line of its own further down.
    f = rep.fight(5)
    ((name, at),) = await rep.end_buffs(f)
    kept = [(name, at), ("Frenzy", at + 2_000)]
    assert ruled_out(await pull_ending(rep, f), kept, await rep.decided_at(f)) == (
        "Not an enrage buff: the boss gained Unbound Ferocity at 1:17 and Frenzy at 1:19, after the wipe was decided "
        "at 1:14, so they're fallout. Unless the guide names a cast that ends the fight on a timer, deaths lost it, "
        "not a damage check.")

    async def end_buffs(fight: dict) -> list[tuple[str, float]]:
        return kept
    monkeypatch.setattr(rep, "end_buffs", end_buffs)
    text = await fight.pull_summary(rep, 5)
    assert text.count("Unbound Ferocity at 1:17") == 1 and text.count("Frenzy at 1:19") == 1
    assert "The boss gained" not in text and "so they're fallout." in text


async def test_pull_summary_leads_with_the_pull_and_how_it_ended(rep):
    # What the model reads first is the answer: the pull in a plain sentence with its own link, then "Ended by". The
    # post's lines that the rows below restate (first deaths, the buffs at the end) and its bold are the post's alone.
    lines = (await fight.pull_summary(rep, 13)).splitlines()
    assert lines[0] == ("Sszorak (Mythic) pull 13: wipe at 62.6%, P1 (2nd time), 2:42 long "
                        "(https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21); best of this log so far.")
    assert lines[1].startswith("Ended by (from the log, earliest first): the boss gained Unbound Ferocity at 2:32")
    assert not [line for line in lines if line.startswith(("First deaths", "Boss buffs at the end")) or "**" in line]
    assert lines.count("Last 5: 68.1 → 79.5 → 70.8 → 70.4 → 62.6") == 1
    # every buff the boss kept is said once: the one that decided the wipe by "Ended by", with no line repeating it
    assert sum("gained Unbound Ferocity at 2:32" in line for line in lines) == 1
    kill = (await fight.pull_summary(rep, 2, "vashnik")).splitlines()
    assert kill[0] == ("Vashnik the Malignant (Mythic) pull 2: kill in 6:56, 4 deaths "
                       "(https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=2).")
    assert "Raid size 20. Deaths in order (time from pull start" in kill[3] and kill[4].startswith("1. 1:38 ")


async def test_the_deaths_after_the_wipe_was_decided_are_one_line_with_every_death_in_it(rep):
    # Pull 13: four deaths until the enrage, a row each; the fifteen after it by killing blow, each with its second
    # and its player, in the order the first of each came, and the players left after the last.
    f = rep.fight(13)
    deaths = await rep.deaths(f)
    lines = (await fight.pull_summary(rep, 13)).splitlines()
    rows = [line for line in lines if line[:1].isdigit() and ". " in line[:4]]
    assert len(rows) == 4 and rows[-1].endswith("(16 alive)")
    assert "(15 later deaths: fallout)" in lines[lines.index("") + 1]
    assert lines[lines.index("") + 2].startswith("Raid size 20. Deaths until then, in order (")
    (fallout,) = [line for line in lines if line.startswith("Fallout")]
    assert fallout == (
        "Fallout, 15 deaths after the wipe was decided at 2:32 (the boss gained Unbound Ferocity), by killing blow "
        "(when, who): Mutilated Gash [1285998] ×9 (2:33 Sinnatony; 2:34 Lïzårðmâñ, Peggerino, Pomaa, Krøpling, Sveini, "
        "Fappachino; 2:35 Enfo, Sonyosh) · Ula'tek's Presence [1285965] ×1 (2:34 Cptola) · Melee [1] ×1 (2:36 "
        "Deathadim) · Venomous Surge [1306120] ×4 (2:42 Martin, Nexsozz, Dragepie, Wutroll); 1 of 20 alive after the "
        "last.")
    for d in deaths[4:]:  # each of them by name and second
        assert f"{rep.names[d['targetID']]}" in fallout and clock(d["timestamp"] - f["startTime"]) in fallout
    # a first pull that the enrage decided with everyone alive: no row, and all of them in the line
    first = (await fight.pull_summary(rep, 1)).splitlines()
    at = first.index("Raid size 20. Deaths until then, in order (time from pull start, players alive after it, a "
                     "battle res or self-res counted):")
    assert first[at + 1] == "none" and first[at + 2].startswith("Fallout, 19 deaths after the wipe was decided at 0:39")


async def test_the_post_keeps_its_text_and_the_bot_finds_its_marks_in_pull_summary(rep, monkeypatch):
    # The post (compare_pulls prints it) is written from the same facts and reads as it did. Wipebot in Discord
    # looks in pull_summary's answer for the post's mark of a new guild best and takes the first pull link in it
    # for the picture: the mark is kept as the post writes it, and the pull's own link comes first.
    f = rep.fight(13)
    assert (await rep.summary(f)).splitlines() == [
        "**Sszorak** (Mythic) · pull 13 · wipe at **62.6%** · P1 (2nd time) · 2:42 · best of this log so far",
        "Boss buffs at the end: **Unbound Ferocity** (from 2:32)",
        "First deaths: Scopidruid (Ula'tek's Presence, 0:48) · Draiman (no killing blow recorded, 0:53) · Attractîv "
        "(Venomous Surge, 1:28)",
        "Killed by, until the wipe was decided at 2:32 (the boss gained Unbound Ferocity): Ula'tek's Presence ×1 · no "
        "killing blow recorded ×1 · Venomous Surge ×1 · Raging Crosswinds ×1 (15 later deaths: fallout)",
        "Last 5: 68.1 → 79.5 → 70.8 → 70.4 → 62.6"]
    before = GuildBest(70.0, "abcDEF123", 7, 4, 1_700_000_000_000.0)

    async def guild_best(fight: dict) -> GuildBest:
        return before
    monkeypatch.setattr(rep, "guild_best", guild_best)
    first = (await fight.pull_summary(rep, 13)).splitlines()[0]
    assert "**new guild best!** (before this log: 70.0% on " in first and first.count("**") == 2
    assert first.index(rep.url(f)) < first.index("https://www.warcraftlogs.com/reports/abcDEF123#fight=7")
    assert "**new guild best!**" in await rep.summary(f)


def test_a_hit_killing_many_counts_whole_whatever_else_kills_meanwhile(rep, deaths):
    # Two abilities kill four each in the same two seconds: both are found, each with all its deaths, also the ones
    # logged after half the raid was dead (the moment the caller cut at falls inside both).
    died = deaths(*[(10.0 * n, SWIPE) for n in range(1, 6)], (60.0, VENOM), (60.1, CRASH), (60.4, VENOM),
                  (60.5, CRASH), (60.9, VENOM), (61.2, CRASH), (61.6, VENOM), (61.9, CRASH))
    text = ended_by(rep, PULL, died, {}, 20, [], Decided(60_900))
    assert "Venom Burst [901] killed 4 within 2 s at 1:00" in text
    assert "then Shadow Crash [903] killed 4 within 2 s at 1:00" in text
    assert [(h.ability, len(h.deaths)) for h in mass_hits(died, 20, rep.ability)] == [("Venom Burst", 4),
                                                                                       ("Shadow Crash", 4)]


def test_a_death_just_ahead_of_a_hit_killing_many_is_a_death_of_its_own(rep, deaths):
    # No killing blow logged: one dies 2.05 s ahead of four who die together. The four are the hit; the one isn't
    # its first victim, and the two a little after it are too few to be another.
    died = [{**d, "killingAbilityGameID": None} for d in deaths(
        (30.0, 0), (32.05, 0), (32.3, 0), (32.6, 0), (33.0, 0), (34.2, 0), (34.3, 0))]
    hits = mass_hits(died, 20, rep.ability)
    assert [[d["timestamp"] - T0 for d in h.deaths] for h in hits] == [[32_050, 32_300, 32_600, 33_000]]
    assert not hits[0].waves


def test_deaths_in_pulses_that_another_abilitys_hit_follows_led_into_it(rep, deaths):
    # Four die to one ability over 5 s, then another kills seven at once: the four led into that hit, they aren't
    # a hit killing many of their own (pull 28 of the tests' night). With no such hit behind them, they are.
    pulses = [(33.0, SWIPE), (36.8, SWIPE), (37.3, SWIPE), (37.5, SWIPE)]
    died = deaths(*pulses, *[(38.2 + n / 10, VENOM) for n in range(7)])
    assert [h.ability for h in mass_hits(died, 20, rep.ability)] == ["Venom Burst"]
    assert lead(ending(rep, PULL, died, {}, 20, [], Decided(38_800))) == "deaths from 0:33"
    alone = mass_hits(deaths(*pulses, (50.0, VENOM)), 20, rep.ability)
    assert [(h.ability, h.waves) for h in alone] == [("Tail Swipe", True)]


def test_the_deaths_leading_into_a_moment_are_named_and_lead_when_they_are_many(rep, deaths):
    # Four die one after another from 1:10 (another brought back, one long before), then one hit kills four at 1:40.
    rep.abilities |= {VENOM: "Venom Burst", SWIPE: "Tail Swipe", CRASH: "Shadow Crash"}
    died = died_as((5.0, SWIPE, 901), (70.0, SWIPE, 902), (80.0, SWIPE, 903), (85.0, SWIPE, 904), (90.0, CRASH, 905),
                   (95.0, SWIPE, 906), (100.0, VENOM, 907), (100.2, VENOM, 908), (100.4, VENOM, 909),
                   (100.6, VENOM, 910))
    backs: dict[int, list[float]] = {903: [T0 + 81_000.0]}  # 903 battle-ressed: not one of the deaths that stayed
    e = ending(rep, PULL, died, backs, 20, [], None)
    assert e and e.lead_in and (e.lead_in.first_ms, e.lead_in.deaths, e.lead_in.tanks) == (70_000, 4, 0)
    # four is a fifth of 20, as many as a hit killing many takes: they lead, in the text, the label, the card and
    # what the evals check alike
    assert ending_text(e) == (
        "Ended by (from the log, earliest first): 4 deaths one after another from 1:10, none brought back (what each "
        "left short may be what lost the rest); then Venom Burst [901] killed 4 within 1 s at 1:40 (one ability "
        "killing many at once: find which mechanic failed).")
    assert lead(e) == "deaths from 1:10"
    assert kind_of_end(e) == "deaths leading into Venom Burst killing many"
    fewer = ending(rep, PULL, died[4:], backs, 20, [], None)  # two deaths before it: named, but don't lead
    assert fewer and fewer.lead_in and fewer.lead_in.deaths == 2 and lead(fewer) == "ability: Venom Burst"
    assert str(ending_text(fewer)).endswith("Before it, 2 deaths one after another from 1:30, none brought back: what "
                                            "each left short may be what lost it.")
    assert kind_of_end(fewer) == "Venom Burst killing many"
    # the mechanic a post reads up on first: the one ending it, none when the deaths before it lead
    assert ending_ability(fewer) == "Venom Burst" and ending_ability(e) is None


def _landing(name_id: int, at_s: float, targets: list[int], source: int = 9001) -> list[dict]:
    return [{"type": "applydebuff", "timestamp": T0 + at_s * 1000 + n, "sourceID": source, "targetID": who,
             "abilityGameID": name_id} for n, who in enumerate(targets)]


@pytest.fixture
def alive_pull(rep, monkeypatch):
    """A pull of the night's first boss that ended at 2:00 with 18 of 20 alive and no death deciding it, answered
    from made-up events: alive_pull(debuffs, casts, earlier) sets the enemies' debuffs on players, the enemies'
    casts, and the debuff's applications on the pull before it; returns the pull and the queries asked."""
    first = rep.by_boss[next(iter(rep.by_boss))][0]
    f = {**first, "id": 990, "kill": False, "fightPercentage": 40.0, "startTime": first["endTime"] + T0,
         "endTime": first["endTime"] + T0 + 120_000}
    players: list[int] = first["friendlyPlayers"][:20]
    died = [{"targetID": players[0], "timestamp": f["startTime"] + 30_000, "killingAbilityGameID": SWIPE},
            {"targetID": players[1], "timestamp": f["startTime"] + 40_000, "killingAbilityGameID": CRASH}]
    asked: list[tuple] = []

    def answer(debuffs: list[dict], casts: list[dict], earlier: list[dict] | None = None):
        shift = f["startTime"] - T0

        def moved(events):
            return [{**e, "timestamp": e["timestamp"] + shift} for e in events]

        async def events(fid, kind, **filters):
            asked.append((fid, kind, tuple(sorted(filters))))
            return moved(debuffs if kind == "Debuffs" else casts)

        async def events_for(items, kind, **filters):
            asked.append(("for", kind, tuple(i for i, _ in items)))
            return [[{**e, "timestamp": first["startTime"] + e["timestamp"] - T0} for e in earlier or []]
                    if "filterExpression" in extra else moved(debuffs if kind == "Debuffs" else casts)
                    for _, extra in items]

        async def value(v):
            return v
        monkeypatch.setattr(rep, "events", events)
        monkeypatch.setattr(rep, "events_for", events_for)
        monkeypatch.setattr(rep, "deaths", lambda g: value(died if g["id"] == 990 else []))
        monkeypatch.setattr(rep, "resurrects", lambda fs: value({g["id"]: {} for g in fs}))
        monkeypatch.setattr(rep, "enrages", lambda g: value([]))
        monkeypatch.setattr(rep, "decided_at", lambda g: value(None))
        monkeypatch.setattr(rep, "raid_size", lambda g: value(20))
        monkeypatch.setattr(rep, "decided_cuts", lambda fs: value({}))
        return f, players, asked
    return answer


async def test_a_cast_of_the_boss_that_lands_on_the_living_as_the_pull_ends_is_what_ended_it(rep, alive_pull):
    # 18 of 20 alive at 2:00: the boss cast March at 1:57 and Dread landed on all 18 a second later, as it had at
    # no earlier moment of the pull and on no earlier pull. The ending says what the log shows, and names the cast.
    rep.abilities |= {SWIPE: "Tail Swipe", CRASH: "Shadow Crash", 801: "March", 802: "Dread", 803: "Sting"}
    boss = next(iter(rep.bosses))
    cast = [{"type": "cast", "timestamp": T0 + 117_000, "sourceID": boss, "abilityGameID": 801}]
    f, players, asked = alive_pull([], cast)
    living = players[2:]
    f, players, asked = alive_pull(_landing(803, 50, living[:3]) + _landing(802, 118, living), cast)
    e = await pull_ending(rep, f)
    assert ending_text(e) == (
        "Ended by: the boss cast March at 1:57 and Dread [802] landed on 18 of the 18 alive at 1:58, 2 s before the "
        "pull ended at 2:00 with 18 of 20 alive. No death decided it, and it landed on that many at no earlier "
        "moment of this pull or of the pulls before it: look the cast up, one that ends the fight is what ended this "
        "pull.")
    assert (lead(e), kind_of_end(e), ending_ability(e)) == ("cast: March", "the boss casting March", "March")
    # a night's pulls: the ones that ended with the raid alive are read in one query per kind
    asked.clear()
    assert [kind_of_end(x) for x in await pull_endings(rep, [f, {**f, "id": 991}])] == ["the boss casting March"] * 2
    assert asked[:2] == [("for", "Debuffs", (990, 991)), ("for", "Casts", (990, 991))]


async def test_a_debuff_the_raid_carries_through_the_fight_is_not_what_ended_a_pull(rep, alive_pull):
    rep.abilities |= {SWIPE: "Tail Swipe", CRASH: "Shadow Crash", 801: "March", 802: "Dread"}
    alive = "Ended by: no death. The pull ended at 2:00 with 18 of 20 alive and nothing in the log decided it"
    f, players, _ = alive_pull([], [])
    living = players[2:]
    # it landed on the raid a minute earlier in this pull as well: part of the fight
    f, *_ = alive_pull(_landing(802, 58, living) + _landing(802, 118, living), [])
    assert str(ending_text(await pull_ending(rep, f))).startswith(alive)
    # or on the pull before, which went on: part of the fight too
    f, *_ = alive_pull(_landing(802, 118, living), [], earlier=_landing(802, 20, living))
    assert str(ending_text(await pull_ending(rep, f))).startswith(alive)
    # on a few players only, or well before the end: nothing the pull ended on
    f, *_ = alive_pull(_landing(802, 118, living[:5]), [])
    assert str(ending_text(await pull_ending(rep, f))).startswith(alive)
    f, *_ = alive_pull(_landing(802, 100, living), [])
    assert str(ending_text(await pull_ending(rep, f))).startswith(alive)
    # with no cast of the boss's logged before it, the debuff is what the log names
    f, *_ = alive_pull(_landing(802, 118, living), [])
    assert str(ending_text(await pull_ending(rep, f))).startswith(
        "Ended by: the boss's Dread [802] landed on 18 of the 18 alive at 1:58, 2 s before the pull ended")


async def test_a_debuff_of_the_fight_landing_late_does_not_hide_the_cast_that_ended_the_pull(rep, alive_pull):
    # Sting lands on the raid at 1:52 for the first time in this pull, as it did on the pull before, which went on:
    # part of the fight. Dread lands at 1:58, as on no earlier pull: the ending names it. A debuff from a player's
    # pet (a hunter's pet's Bloodlust) is nobody's cast to look up.
    rep.abilities |= {SWIPE: "Tail Swipe", CRASH: "Shadow Crash", 802: "Dread", 803: "Sting", 804: "Fatigued"}
    f, players, _ = alive_pull([], [])
    living = players[2:]
    f, *_ = alive_pull(_landing(803, 112, living) + _landing(802, 118, living), [], earlier=_landing(803, 20, living))
    assert str(ending_text(await pull_ending(rep, f))).startswith(
        "Ended by: the boss's Dread [802] landed on 18 of the 18 alive at 1:58, 2 s before the pull ended")
    rep.actors[9100] = {"id": 9100, "name": "Wolf", "type": "Pet", "petOwner": living[0]}
    f, *_ = alive_pull(_landing(804, 118, living, source=9100), [])
    assert kind_of_end(await pull_ending(rep, f)) == "no death, the raid alive"


async def test_what_lands_on_the_raid_as_a_pull_begins_did_not_end_a_pull_seconds_long(rep, alive_pull):
    # The raid stops 8 s into the night's first pull on the boss. The boss's opening debuff landed on everyone at
    # 0:02: a pull this short has no earlier moment, and no earlier pull, to tell it from what ended the pull.
    rep.abilities |= {SWIPE: "Tail Swipe", CRASH: "Shadow Crash", 802: "Dread"}
    f, players, _ = alive_pull([], [])
    f, *_ = alive_pull(_landing(802, 2, players), [])
    short = {**f, "endTime": f["startTime"] + 8_000}
    e = await pull_ending(rep, short)
    assert (kind_of_end(e), lead(e)) == ("no death, the raid alive", "alive")
    assert ending_text(e) == ("Ended by: no death. The pull ended at 0:08 with 20 of 20 alive and nothing in the log "
                              "decided it: called off or reset, the log doesn't say which.")


async def test_a_pull_gets_past_what_ended_earlier_pulls_not_past_pulls_nothing_ended(rep, monkeypatch):
    # Three of the four pulls before this one ended with the raid alive: nothing in the log ended them, so a wipe
    # to a hit didn't "get past" them. The same ending is still said of a pull that ended alive too.
    f = rep.fight(5, "Sszorak")
    alive = Ending("alive", 20, 120_000, alive_at_end=18)
    earlier = [alive, alive, Ending("cascade", 20, 120_000, first_death_ms=30_000, half_dead_ms=90_000), alive]

    async def endings(_, fights):
        return earlier[:len(fights)]
    monkeypatch.setattr(fight, "pull_endings", endings)
    hit = Ending("moments", 20, 120_000, (MassDeath(60_000, "Venom Burst", (901,), 4, 1),))
    assert await fight._same_ending(rep, f, hit) is None
    assert await fight._same_ending(rep, f, alive) == (
        "This pull ended the way 3 earlier pulls on Sszorak in this log did (pulls 1-2, 4): no death, the raid alive.")
    # What ended the most earlier pulls is named first, then how many of them: "most earlier pulls, 3 of 4: ..."
    # read as a list of pulls.
    earlier[:] = [hit, hit, hit, alive]
    cascade = Ending("cascade", 20, 120_000, first_death_ms=30_000, half_dead_ms=90_000)
    assert await fight._same_ending(rep, f, cascade) == (
        "This pull got past what ended the most earlier pulls: Venom Burst killing many (3 of 4).")


async def test_a_pull_did_not_get_past_what_came_on_it_too(rep, monkeypatch):
    # Pulls 1-3 of the tests' night ended as the boss gained Unbound Ferocity. On pull 5 it gained it at 1:17, after
    # the wipe was decided at 1:14: the summary said "the boss gained Unbound Ferocity at 1:17 ... so it's fallout"
    # and, a line on, that the pull "got past" the boss gaining it. Pull 4, where the boss never gained it, did.
    # The time is the line above's to say: it is given here only for a buff no line above names.
    late, clear = rep.fight(5, "Sszorak"), rep.fight(4, "Sszorak")
    assert await fight._same_ending(rep, late, await pull_ending(rep, late)) == (
        "This pull ended the way 1 earlier pull on Sszorak in this log did (pull 4): deaths leading into Mutilate "
        "killing many. What ended the most earlier pulls (3 of 4), the boss gaining Unbound Ferocity and keeping it to "
        "the end, came on this pull too, after the wipe was decided.")
    assert await fight._same_ending(rep, clear, await pull_ending(rep, clear)) == (
        "This pull got past what ended the most earlier pulls: the boss gaining Unbound Ferocity and keeping it to the "
        "end (3 of 3).")
    # A hit that ended most earlier pulls and came here behind another, or after deaths that led into it, came too.
    hit = Ending("moments", 20, 120_000, (MassDeath(60_000, "Venom Burst", (901,), 4, 1),))

    async def endings(_, fights):
        return [hit, hit, hit, None][:len(fights)]
    monkeypatch.setattr(fight, "pull_endings", endings)
    behind = Ending("moments", 20, 120_000, (MassDeath(30_000, "Tail Swipe", (902,), 4, 1), *hit.moments))
    assert await fight._same_ending(rep, rep.fight(5, "Sszorak"), behind) == (
        "What ended the most earlier pulls (3 of 4), Venom Burst killing many, came on this pull too, after what "
        "ended it.")
    # Deaths led into the hit on those pulls and not on this one: the hit is what came here too.
    led = Ending("moments", 20, 120_000, hit.moments, lead_in=LeadIn(30_000, 4, 0))
    earlier = [led, led, led, None]

    async def led_into(_, fights):
        return earlier[:len(fights)]
    monkeypatch.setattr(fight, "pull_endings", led_into)
    assert kind_of_end(led) == "deaths leading into Venom Burst killing many"
    assert await fight._same_ending(rep, rep.fight(5, "Sszorak"), hit) == (
        "What ended the most earlier pulls (3 of 4), Venom Burst killing many, came on this pull too.")


async def test_a_fight_on_no_raid_difficulty_says_so_and_is_no_difficulty_10(rep, monkeypatch):
    # A Mythic+ run reaches Warcraft Logs as one fight with a difficulty that is no raid's: "(difficulty 10)" with
    # "raid size 5" and "the boss dead" read as a bug. The raid's own pulls carry no such line.
    assert fight.NOT_A_RAID not in await fight.pull_summary(rep, 12, "Sszorak")
    monkeypatch.setitem(rep.fight(13, "Sszorak"), "difficulty", 10)
    lines = (await fight.pull_summary(rep, 13, "Sszorak")).splitlines()
    assert lines[0].startswith("Sszorak (difficulty 10, not a raid's) pull ")
    assert fight.NOT_A_RAID in lines and "a key's level, its timer or its trash" in fight.NOT_A_RAID


def test_a_cascade_starts_at_the_first_death_that_stayed(rep, deaths):
    died = died_as((10.0, SWIPE, 901), (50.0, SWIPE, 902), (55.0, CRASH, 903), (60.0, SWIPE, 904))
    e = ending(rep, PULL, died, {901: [T0 + 15_000.0]}, 6, [], Decided(60_000))
    assert e and e.kind == "cascade" and e.first_death_ms == 50_000
    assert ending_ability(e) is None


def test_a_nights_row_starts_a_pulls_deaths_where_its_summary_does(rep, deaths):
    """The pull's first death at 0:10 was brought back, so its "Ended by" has the deaths from 0:50. The night's row
    named only that first death, and a pull with "first death (Plague Wave, 3:48)" read as deaths from 3:48 where its
    summary said from 4:36."""
    died = died_as((10.0, SWIPE, 901), (50.0, SWIPE, 902), (55.0, CRASH, 903), (60.0, SWIPE, 904))
    e = ending(rep, PULL, died, {901: [T0 + 15_000.0]}, 6, [], Decided(60_000))
    assert str(ending_text(e)).startswith("Ended by: deaths from 0:50 until half the raid was dead at 1:00")
    assert lead(e) == "deaths from 0:50" and deaths_from(e) == 50_000
    assert night._ended(e, Decided(60_000)) == ", ended by deaths from 0:50 one after another, decided at 1:00"
    # Deaths that led into a hit killing many: from the first of them, a death long before left out as in the summary.
    rep.abilities |= {VENOM: "Venom Burst", SWIPE: "Tail Swipe"}
    led = died_as((5.0, SWIPE, 901), (70.0, SWIPE, 902), (85.0, SWIPE, 904), (90.0, SWIPE, 905), (95.0, SWIPE, 906),
                  (100.0, VENOM, 907), (100.2, VENOM, 908), (100.4, VENOM, 909), (100.6, VENOM, 910))
    e = ending(rep, PULL, led, {}, 20, [], None)
    assert night._ended(e, None) == ", ended by deaths from 1:10 leading into Venom Burst killing many"
    # An ending that leads with something else has no such start, and its row none.
    hit = ending(rep, PULL, led[5:], {}, 20, [], None)
    assert deaths_from(hit) is None and night._ended(hit, None) == ", ended by Venom Burst killing many"

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


async def test_a_stack_two_reach_in_one_moment_names_both(rep, monkeypatch):
    """The timeline named whoever's event came first: "Helical Toxins reaches 14 stacks on Fappachino" where
    Dragepie reached 14 in the same millisecond. One who reaches it seconds later is not who it "reaches" it on."""
    f = rep.fight(13, "Sszorak")
    a, b, c = (rep.player(name).id for name in ("Fappachino", "Dragepie", "Pomaa"))
    rep.abilities[901] = "Helical Toxins"

    async def debuffs(*_):
        return [{"timestamp": f["startTime"] + at, "type": "applydebuffstack", "targetID": who, "abilityGameID": 901,
                 "stack": n} for at, who, n in ((160_000, a, 13), (168_278, a, 14), (168_278, b, 14), (172_000, c, 14))]
    monkeypatch.setattr(fight, "_boss_debuffs", debuffs)
    assert [text for _, text in (await fight._debuff_stacks(rep, f)).events] == [
        "Helical Toxins reaches 13 stacks on Fappachino", "Helical Toxins reaches 14 stacks on Fappachino, Dragepie"]


async def test_the_deaths_an_ability_dealt_are_among_its_events_in_a_timeline_of_it(rep):
    """The captured night has its deaths and no casts or hits: of an ability, the deaths it dealt are what is left."""
    text = await fight.pull_timeline(rep, 13, "Sszorak", end="1:00", ability="Ula'tek's Presence")
    lines = text.splitlines()
    assert lines[0].startswith("Sszorak pull 13 timeline, 0:00-1:00, only Ula'tek's Presence (")
    assert ": 1 event in Sszorak pull 13, times from the pull's start." in lines[1]
    assert lines[2:] == ["0:48.031 death: Scopidruid; killing blow Ula'tek's Presence",
                         "Ability ids: Ula'tek's Presence [1285965]"]
    with pytest.raises(fight.FactError, match="the window's end must be after its start"):
        await fight.pull_timeline(rep, 13, "Sszorak", start="1:00", end="0:30", ability="Tempest")


async def test_a_timeline_of_a_players_casts_has_their_deaths_and_says_when_they_died_before_an_empty_window(
        rep, patch_wcl):
    """Rows that stop, or a window with none, read as a player who stopped pressing: the log knows they had died."""
    f = rep.fight(13, "Sszorak")
    druid = rep.player("Scopidruid")
    rep.abilities[190984] = "Wrath"  # the captured night has only the abilities of its deaths
    casts = [{"timestamp": f["startTime"] + at, "type": kind, "sourceID": druid.id, "abilityGameID": 190984,
              "fight": f["id"], **target} for at, kind, target in ((46_602, "begincast", {}),
                                                                  (47_434, "cast", {"targetID": 21}))]
    logged = rep.wcl.events

    async def events(code, fight_id, data_type, **filters):
        return casts if data_type == "Casts" else await logged(code, fight_id, data_type, **filters)
    patch_wcl(rep, events=events)
    lines = (await fight.pull_timeline(rep, 13, "Sszorak", start="0:40", player="Scopidruid")).splitlines()
    assert lines[1] == ("Every cast Scopidruid and their pets began or finished, and their deaths: 3 events in Sszorak "
                        "pull 13, times from the pull's start.")
    assert lines[2:5] == ["0:46.602 begincast: Scopidruid → Wrath", "0:47.434 cast: Scopidruid → Wrath → Boss",
                          "0:48.031 death: Scopidruid; killing blow Ula'tek's Presence"]
    after = await fight.pull_timeline(rep, 13, "Sszorak", start="0:50", end="1:10", player="Scopidruid")
    assert after.splitlines()[1:] == ["No cast of Scopidruid's is logged in this window: their last death before it "
                                      "was at 0:48.031."]
