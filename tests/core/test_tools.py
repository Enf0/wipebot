"""The tools on real pulls: each case of fixtures/real/tools/cases.toml against its snapshot, answered from the
recorded answers (support/recorded.py); and what a snapshot can't hold, the pictures and the replay's data."""
import base64
import io
import json
import os
import re
import struct

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from PIL import Image

from tests.support import assert_snapshot
from tests.support.recorded import (
    CASES,
    LOCAL,
    LOCAL_RECORDED,
    NOT_LOCAL,
    REPLAY_AT_MS,
    REPLAY_CASE,
    TOOLS,
    Case,
    Replay,
    replay_data,
    run,
    serve_from,
    view,
)
from wipebot.core import server
from wipebot.core.report import fight, maps
from wipebot.core.report import note as notes
from wipebot.core.text import clock
from wipebot.core.wcl import Unreadable, WCLError


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
async def test_tool_output_matches_snapshot(recorded, case: Case):
    if case.local and not LOCAL_RECORDED.exists():
        pytest.skip(NOT_LOCAL)
    if case.stale:  # asks Warcraft Logs what its recording doesn't hold yet (cases.toml says why)
        with pytest.raises(Exception, match="not recorded"):
            await run(case)
        pytest.skip(f"recorded before {case.stale}: `just record-tools`, then `just snapshot`")
    assert_snapshot(case.snapshot, await run(case))


# An output may outgrow its budget by a tenth, or by this many characters where that is more (a short output's
# added clause), before the test asks for the budget to be raised by hand.
SIZE_MARGIN, SIZE_SLACK = 0.10, 200
SIZES_HEAD = """\
# The size budget of each tool case's pinned output, in characters (test_no_tool_output_outgrew_its_size_budget in
# tests/core/test_tools.py). The model reads a tool's whole output on every call, at about 2.7 characters a token
# for these outputs, so an output that grows costs every answer that reads it, and a snapshot's diff shows changed
# text, not that it is a fifth longer.
# `just snapshot` lowers a line when its output shrank and adds a new case's. It never raises one: an output that
# outgrew its budget by more than a tenth (or 200 characters, where that is more) fails the test until its line is
# raised here by hand, with the reason in the commit.
"""


def _sizes(folder) -> dict[str, int]:
    path = folder / "sizes.toml"
    return {name: int(n) for name, n in (line.split(" = ") for line in path.read_text(encoding="utf-8").splitlines()
                                         if line and not line.startswith("#"))} if path.exists() else {}


@pytest.mark.parametrize("folder", [TOOLS, LOCAL], ids=["published", "local"])
def test_no_tool_output_outgrew_its_size_budget(folder):
    """Size is a reviewed quantity: each case's output has a budget in characters beside its snapshot (sizes.toml;
    the local cases' in local/, with their snapshots), and one that outgrew it by more than the margin fails until
    the budget is raised by hand. Runs after the snapshot tests above, so `just snapshot` measures the new outputs."""
    cases = [case for case in CASES.values() if case.snapshot.parent == folder and case.snapshot.exists()]
    updating = bool(os.environ.get("UPDATE_SNAPSHOTS"))
    budget = _sizes(folder)
    if folder == LOCAL and (not cases or not (budget or updating)):
        pytest.skip(NOT_LOCAL if not cases else "the local cases have no size budget yet: `just snapshot` writes it")
    now = {case.name: len(case.snapshot.read_text(encoding="utf-8")) for case in cases}
    if updating:  # down with an output that shrank, a line for a new case; never up
        kept = {name: min(now.get(name, n), n) for name, n in budget.items() if name in CASES}
        kept |= {name: n for name, n in now.items() if name not in kept}
        if kept != budget:
            (folder / "sizes.toml").write_text(SIZES_HEAD + "".join(
                f"{name} = {kept[name]}\n" for name in CASES if name in kept), encoding="utf-8")
        budget = kept
    assert not (new := sorted(set(now) - set(budget))), f"no size budget for {new}: `just snapshot` adds its line"
    grown = _outgrown(budget, now)
    assert not grown, ("grew past its size budget: " + "; ".join(grown) + ". The model reads all of it on every call: "
                       "cut what repeats, or, if the growth is meant, raise the line in "
                       f"{(folder / 'sizes.toml').relative_to(TOOLS.parent.parent.parent)} by hand")


def _outgrown(budget: dict[str, int], now: dict[str, int]) -> list[str]:
    """The outputs past their budget by more than the margin, each with both sizes."""
    return [f"{name}: {budget[name]} to {n} characters (+{100 * (n / budget[name] - 1):.0f}%)"
            for name, n in now.items() if n > budget[name] + max(SIZE_MARGIN * budget[name], SIZE_SLACK)]


def test_the_size_budget_allows_a_tenth_or_a_clause_and_no_more():
    budget = {"night": 7000, "roster": 750}
    assert _outgrown(budget, {"night": 7700, "roster": 950}) == []  # a tenth of a long one, a clause on a short one
    assert _outgrown(budget, {"night": 7701, "roster": 951}) == [
        "night: 7000 to 7701 characters (+10%)", "roster: 750 to 951 characters (+27%)"]
    assert _outgrown(budget, {"night": 3000}) == []  # shrinking is free; `just snapshot` then lowers the line


async def test_an_abilitys_damage_on_a_pull_is_one_number_in_every_tool(recorded):
    """compare_pulls' most damage taken and breakdown's damage taken by ability read one table the same way: an
    ability logged under several ids (Virulence, Venomous Surge) is one row with their sum in both, in one order."""
    compared, taken = await run(CASES["compare-pulls"]), await run(CASES["damage-taken"])
    pull_13 = compared.split("\n\n")[1].splitlines()[-1]
    assert pull_13.startswith("Most damage taken until 2:32")
    listed = [tuple(row.rsplit(" ", 1)) for row in pull_13.split("): ", 1)[1].split(", ")]
    rows = re.findall(r"^  \d+\. (.+?): (\S+) \(", taken, re.M)
    assert len(listed) == 8 and listed == rows[:8]
    assert ("Virulence", "17.87M") in listed and ("Venomous Surge", "13.04M") in listed


@pytest.mark.parametrize("mine, top", [("player-pull", "compare-with-top"),
                                       ("player-pull-holy-power", "compare-with-top-healer")])
async def test_a_players_casts_per_minute_are_one_number_in_both_tools(recorded, mine, top):
    """player_pull and compare_with_top count a player's casts from Warcraft Logs' Casts table alike: a Rogue's
    Mutilate once per press, not once per hand, and a Paladin's Reclamation procs not at all."""
    if not LOCAL_RECORDED.exists():
        pytest.skip(NOT_LOCAL)
    pull, against = await run(CASES[mine]), await run(CASES[top])
    per_minute = re.search(r"^Casts: \d+ \((\d+) per minute alive", pull, re.M)
    total = re.search(r"^Total: (\d+) / ", against, re.M)
    assert per_minute and total and per_minute[1] == total[1]
    for name, mine_cpm in re.findall(r"^  (.+? \[\d+\]): ([\d.]+) / ", against, re.M):
        alive_s = int(re.search(r"\((\d+) s alive\)", against)[1])  # ty: ignore[not-subscriptable]
        n = re.search(re.escape(name) + r" (\d+)[,\n]", pull)
        assert n and abs(60 * int(n[1]) / alive_s - float(mine_cpm)) < 0.2, name


async def test_a_note_assigns_kicks_when_it_calls_one_player_as_an_add_begins_a_cast(recorded):
    """The Twin Fangs note's "Kick #1 left" callouts come as the Broodlings begin Visceral Burst; its cooldowns and
    callouts to everyone are no kicks."""
    note = (TOOLS.parent / "notes" / "twin-fangs-2026-10-19.txt").read_text(encoding="utf-8")
    rep = await server.lib().report("fvkH4hGZwDRVnyXJ")
    f = rep.fight(14, "Twin Fangs")
    assert await notes.assigns_kicks(rep, f, note)
    assert not await notes.assigns_kicks(rep, f, "\n".join(line for line in note.splitlines() if "Kick #" not in line))


def test_no_output_names_a_player_by_a_spec_alone():
    """'Providence (Holy)' is a Paladin or a Priest, 'Enfo (Restoration)' a Druid or a Shaman: a player is named
    with spec and class (Player.label) in every pinned output. A stale case's snapshot waits for its recording."""
    alone = re.compile(r"\w \((?:Holy|Restoration|Protection|Frost|Discipline|Preservation|Mistweaver)\)")
    for case in CASES.values():
        if not case.stale and case.snapshot.exists():
            found = alone.findall(case.snapshot.read_text(encoding="utf-8"))
            assert not found, f"{case.name}: {found[:3]}"


@pytest.mark.parametrize("name", ["mechanic-hits", "damage-by-pull", "pull-summary"])
async def test_a_recording_reads_each_cases_view_as_the_page_asks_for_it(recorded, name):
    """Recording stopped at the first ranking: its view's key carries the folded card's label, which the page keeps
    to itself and view_data doesn't take."""
    await view(CASES[name])


def test_every_snapshot_has_a_case():
    assert {p.stem for p in TOOLS.glob("*.txt")} == {c.name for c in CASES.values() if not c.local}
    if LOCAL.exists():
        assert {p.stem for p in LOCAL.glob("*.txt")} == {c.name for c in CASES.values() if c.local}


@pytest.mark.parametrize("name", ["pull-map", "raid-plan", "raid-plan-player"])
async def test_maps_are_drawn(recorded, name):
    case = CASES[name]
    text, failed, images = await server.call_with_images(case.tool, case.args)
    assert not failed and len(images) == 1
    img = Image.open(io.BytesIO(images[0]))
    assert (img.format, img.size) == ("WEBP", (1200, 800))


async def test_pull_map_serving_many_users_tells_the_viewer_its_moment(recorded, monkeypatch):
    """claude.ai and ChatGPT get the replay viewer, told what was asked and the moment to open at."""
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str))
    case = CASES["pull-map"]
    result = await server.mcp.call_tool(case.tool, case.args)
    assert isinstance(result, CallToolResult) and result.structured_content is not None
    asked = result.structured_content
    assert {k: asked[k] for k in ("report", "pull", "players")} == {k: case.args[k] for k in ("report", "pull",
                                                                                              "players")}
    rep = await server.lib().report(case.args["report"])
    f, p = rep.fight(case.args["pull"]), rep.player(case.args["players"][0])
    died = next(d for d in await rep.deaths(f) if d["targetID"] == p.id)
    assert 0 < died["timestamp"] - f["startTime"] - asked["at_ms"] < 1000  # just before the player asked about died
    assert [c.type for c in result.content] == ["text", "image"]  # no MCP Apps client in-process: the picture too


async def test_pull_map_with_the_plan_serving_many_users_gives_the_viewer_the_plan_at_each_moment(recorded,
                                                                                               monkeypatch):
    """claude.ai and ChatGPT get the replay viewer with the guild's plan: per moment its spots in the log's
    coordinates, whose each is and who stood off theirs, as the text says; it opens at the moment the picture would
    show. raid_plan gives no viewer anything: a host shows a tool's viewer on every call."""
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str))
    case = CASES["raid-plan"]
    plan = case.args["plan"]

    async def saved(rep, pull, boss):  # the guild's saved plan, as the notebook would give it
        return plan
    monkeypatch.setattr(server, "_saved_plans", saved)
    checked = await server.mcp.call_tool("raid_plan", case.args)
    assert isinstance(checked, CallToolResult) and checked.structured_content is None
    result = await server.mcp.call_tool("pull_map", {"report": case.args["report"], "pull": case.args["pull"],
                                                     "plan": True})
    assert isinstance(result, CallToolResult) and result.structured_content is not None
    asked, first = result.structured_content, result.content[0]
    assert isinstance(first, TextContent)
    text = first.text
    assert (asked["report"], asked["pull"], asked["boss"]) == (case.args["report"], case.args["pull"], "Sszorak")
    moments = asked["plan"]
    assert [clock(m["ms"]) for m in moments] == ["0:31", "1:20", "2:37", "3:28"]  # as the text lists them
    assert asked["at_ms"] == moments[2]["ms"]  # the one with most players off their spot
    for m in moments:
        assert len(m["spots"]) == 20 and {s["player"] for s in m["spots"] if s["off"]} == {o["name"] for o in m["off"]}
        assert f"{clock(m['ms'])} (plan" in text and all(f"{o['name']} {o['yd']} yd" in text for o in m["off"])


def _ints(track: str) -> tuple[int, ...]:
    raw = base64.b64decode(track)
    return struct.unpack(f"<{len(raw) // 2}h", raw)


async def test_the_replay_of_a_pull_fits_a_tool_result(recorded):
    data = await replay_data()
    steps = data["duration_ms"] // data["step_ms"] + 1
    assert len(json.dumps(data)) < 150_000 // 2  # Claude's limit for a tool result, with room for long pulls
    players = {p["name"]: _ints(p["track"]) for p in data["players"]}
    bosses = [_ints(b["track"]) for b in data["bosses"]]
    assert len(bosses) == 1 and all(len(t) == 3 * steps for t in [*players.values(), *bosses])
    known = [sum(t[i] != maps.UNKNOWN for i in range(0, len(t), 3)) / steps for t in players.values()]
    assert len(players) >= 15 and sorted(known)[len(known) // 2] > 0.9  # most players, most of the pull
    args = CASES[REPLAY_CASE].args
    rep = await server.lib().report(args["report"])
    f = rep.fight(args["pull"], args.get("boss"))
    assert [d["name"] for d in data["deaths"]] == [rep.names[d["targetID"]] for d in await rep.deaths(f)]
    assert [p["name"] for p in data["players"] if p["focus"]] == args["players"]
    assert data["map"]["image"].startswith("maps/") and data["at_ms"] == REPLAY_AT_MS
    # The map's corner in the log's coordinates, from which the viewer places raid_plan's spots.
    ox, oy = data["map"]["origin"]
    w, h = (v * maps.REPLAY_UNIT for v in data["map"]["size"])
    assert 0 < w and 0 < h and isinstance(ox, int | float) and isinstance(oy, int | float)


LINK = "https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8"


@pytest.mark.parametrize("fight", ["#fight=21", "?fight=21&type=damage-done&source=6"])
async def test_a_link_to_a_fight_reads_that_pull_and_says_which_pull_it_is(recorded, fight):
    """A raider points at a pull with the link Warcraft Logs gives them, in either form. Fight 21 is Sszorak pull
    13, not pull 21, and not the latest pull (33), which the link was read as."""
    note = "Fight 21 in the link is Sszorak pull 13.\n"
    text, failed = await server.call("pull_summary", {"report": LINK + fight})
    assert not failed and text + "\n" == note + await run(CASES["pull-summary"])
    taken, failed = await server.call("breakdown", {"report": LINK + fight, "data_type": "DamageTaken",
                                                     "group_by": "ability"})  # no pulls: the link's, not all 33
    assert not failed and taken + "\n" == note + await run(CASES["damage-taken"])


async def test_a_pull_named_beside_a_link_to_another_fight_is_read_and_the_first_line_says_so(recorded):
    text, failed = await server.call("compare_pulls", {"report": LINK + "#fight=21", "pull": 12, "other": 13})
    assert not failed and text + "\n" == (
        "Fight 21 in the link is Sszorak pull 13; this call named another pull or boss, which was read instead.\n"
        + await run(CASES["compare-pulls"]))
    plain, failed = await server.call("compare_pulls", {"report": LINK + "#fight=last", "pull": 12, "other": 13})
    assert not failed and plain + "\n" == await run(CASES["compare-pulls"])  # the latest: nothing to say


async def test_a_link_to_a_fight_that_is_no_boss_pull_says_so(recorded):
    with pytest.raises(ToolError, match="fight 5 in the link isn't a boss pull of report KBYT4xXn7VNjDgh8"):
        await server.call("pull_summary", {"report": LINK + "#fight=5"})


TRASH = ("Fight 5 in the link isn't a boss pull of this report (trash, or a fight it doesn't have), so the link was "
         "read as the report alone.\n")


async def test_a_tool_about_the_night_says_nothing_of_the_pull_a_link_names(recorded):
    """ "Fight 21 in the link is Sszorak pull 13." led the boss's night and the roster, which read no pull."""
    night, failed = await server.call("progress", {"report": LINK + "#fight=21", "boss": "Sszorak"})
    assert not failed and night + "\n" == await run(CASES["progress"])
    there, failed = await server.call("roster", {"report": LINK + "?fight=21&type=damage-done"})
    assert not failed and there + "\n" == await run(CASES["roster"])


async def test_a_call_naming_its_pulls_takes_a_link_to_trash_and_says_it_named_none(recorded):
    """It was refused as a call reading the link's pull is, though only when it named no boss."""
    text, failed = await server.call("compare_pulls", {"report": LINK + "#fight=5", "pull": 12, "other": 13})
    assert not failed and text + "\n" == TRASH + await run(CASES["compare-pulls"])


async def test_a_call_over_every_pull_says_nothing_of_the_pull_a_link_names_and_takes_a_link_to_trash(recorded):
    """mechanic_hits over the night read no single pull, yet led with "Fight 21 in the link is Sszorak pull 13.";
    given a link to trash it was refused, as a call reading the link's pull is."""
    args, night = CASES["mechanic-hits"].args, await run(CASES["mechanic-hits"])
    text, failed = await server.call("mechanic_hits", args | {"report": LINK + "#fight=21"})
    assert not failed and text + "\n" == night
    text, failed = await server.call("mechanic_hits", args | {"report": LINK + "#fight=5"})
    assert not failed and text + "\n" == TRASH + night


async def test_breakdown_takes_a_pull_by_name_as_the_other_tools_do(recorded):
    text, failed = await server.call("breakdown", {"report": "KBYT4xXn7VNjDgh8", "data_type": "DamageTaken",
                                                    "group_by": "ability", "pulls": ["best wipe"]})
    assert not failed and text.startswith("Damage taken on Sszorak pull 13")


async def test_a_timeline_of_one_ability_lists_what_the_log_has_of_it_to_the_millisecond(recorded, monkeypatch):
    """When an ability was cast, whom it hit and for how much, which the model wrote raw queries for and converted
    by hand. A pull's hits of an ability are recorded for the night's pulls at once (the mechanic-hits case), so
    this pull's are read out of those."""
    rep = await server.lib().report("KBYT4xXn7VNjDgh8")
    night = [f["id"] for f in rep.by_boss["Sszorak"]]
    table = await rep.table(21, "DamageTaken", hostilityType="Friendlies", viewBy="Ability")
    damaging = {e["guid"] for e in table["entries"]}
    asked = []

    async def events_for(items, data_type, **filters):  # the hits of every id of the ability, in one request
        asked.append((data_type, [extra for _, extra in items]))
        assert data_type == "DamageTaken" and all(set(extra) == {"abilityID"} for _, extra in items)
        return [[e for e in await rep.events(night, data_type, **filters, **extra) if e["fight"] == fight_id]
                if extra["abilityID"] in damaging else []  # the id of the cast: the pull's table has no damage under it
                for fight_id, extra in items]
    monkeypatch.setattr(rep, "events_for", events_for)
    lines = (await fight.pull_timeline(rep, 13, "Sszorak", end="0:40", ability="tempest")).splitlines()
    assert lines[0] == ("Sszorak pull 13 timeline, 0:00-0:40, only Tempest (https://www.warcraftlogs.com/reports/"
                        "KBYT4xXn7VNjDgh8#fight=21). Ended at 2:42 with the boss at 62.6% health.")
    assert lines[1] == ("What the log has of Tempest (its casts, its hits on players and their pets, its debuff going "
                        "on and off players, its buff on the boss, the deaths it dealt): 26 events in Sszorak pull 13, "
                        "times from the pull's start.")
    # the 14 hits' amounts in the log add up to 1,862,408, and what shields took of them to 1,997,423
    assert lines[2] == ("Counted: 14 damage (1.86M together, 2.00M more absorbed), 4 applydebuff, 4 removedebuff, "
                        "2 applydebuffstack, 1 begincast, 1 cast; by ability: Tempest 26; from: Sszorak 26; on: "
                        "Sveini 8, Krøpling 6, Cptola 5, Dragepie 3, Deathadim 2.")
    # the cast and its damage are logged under two ids of the one name, so each row says which
    assert lines[3:6] == ["0:13.004 begincast: Sszorak → Tempest [1287072]",
                          "0:15.341 applydebuff: Sszorak → Tempest [1287083] → Dragepie",
                          "0:15.342 damage: Sszorak → Tempest [1287083] → Dragepie; 0 (334.76k absorbed)"]
    assert "0:16.980 damage: Sszorak → Tempest [1287083] → Sveini; 258.01k (35.23k absorbed, a tick)" in lines
    # Tempest is one of the boss's own casts: its rows have them, so no line of the boss's casts follows, but the
    # raid's answer to each cast does ("did we have cooldowns for it?"), from 10 s before the cast to 5 s after
    assert lines[-3] == "Ability ids: Tempest [1287072, 1287083]" and len(lines) == 3 + 26 + 1 + 2
    assert lines[-2:] == [
        "Raid and healing cooldowns pressed around each of the boss's Tempest casts, from 10 s before it to 5 s after; "
        "one pressed earlier isn't listed, also when it was still running (pull_timeline without ability has every "
        "cooldown, with the boss's other casts, the deaths and the raid's damage taken):",
        "  0:16: healing cooldowns Apotheosis (Namsus) 0:15"]
    assert asked == [("DamageTaken", [{"abilityID": 1287072}, {"abilityID": 1287083}])]
    # by or on one player; and a window with nothing of it
    on_one = (await fight.pull_timeline(rep, 13, "Sszorak", end="0:40", ability="Tempest",
                                        player="sveini")).splitlines()
    assert on_one[0].startswith("Sszorak pull 13 timeline, 0:00-0:40, only Tempest by or on Sveini (")
    assert ": 8 events in Sszorak pull 13" in on_one[1] and all(" → Sveini" in row for row in on_one[3:-1])
    assert on_one[-1].startswith("Ability ids: ")  # one player's rows: the raid's cooldowns aren't what was asked
    # fewer than ten events, but several hits: their amounts are added up in code
    assert on_one[2].startswith("Counted: 5 damage (1.02M together, 520.87k more absorbed), 1 applydebuff, ")
    nothing = await fight.pull_timeline(rep, 13, "Sszorak", start="0:40", end="0:45", ability="Tempest")
    assert nothing.splitlines()[1:] == ["Nothing of Tempest is logged in this window."]


async def test_the_cooldowns_around_a_boss_s_own_casts_say_none_and_skip_one_cast_too_often(recorded, monkeypatch):
    """A healing answer said four Imbibes went "without a cooldown": the ability's rows showed only the boss, and the
    cooldowns pressed around each cast were in the plain timeline it didn't read. A line per cast now follows the
    rows, and its header says what the lines leave out, as a fact and not a maybe: a cooldown pressed earlier isn't
    listed, also when it was still running (Apotheosis at 1:30 through the 1:48 Imbibe), and the plain timeline has
    the boss's other casts, which the same cooldowns stand near (Ravage 1:07, Mutilate 1:11 and Venomous Surge 1:16
    on Sszorak pull 33)."""
    t0 = 1_000_000

    async def pressed(rep, f):
        return [(t0 + at, kind, name, who) for at, kind, name, who in (
            (49_900, "raid cooldown", "Darkness", "Dh"), (50_000, "healing cooldown", "Tranquility", "Dru"),
            (58_000, "raid cooldown", "Rallying Cry", "War"), (65_900, "healing cooldown", "Revival", "Monk"),
            (66_100, "healing cooldown", "Divine Hymn", "Pri"))]
    monkeypatch.setattr(fight, "raid_cooldown_casts", pressed)
    rep, f = await server.lib().report("KBYT4xXn7VNjDgh8"), {"id": 1, "startTime": t0}

    def roars(*seconds):
        return [fight.BossCast(t0 + s * 1000, "Roar", True, 1, 1) for s in seconds]
    lines = await fight._covered(rep, f, [*roars(60, 120), fight.BossCast(t0 + 90_000, "Bite", True, 1, 1)],
                                 {"Roar"}, t0, t0 + 200_000)
    assert lines[0].startswith("Raid and healing cooldowns pressed around each of the boss's Roar casts, from 10 s "
                               "before it to 5 s after; one pressed earlier isn't listed, also when it was still ")
    # from 10 s before the cast's second to 5 s after it; healing cooldowns first; a cast with none says so
    assert lines[1:] == ["  1:00: healing cooldowns Tranquility (Dru) 0:50, Revival (Monk) 1:05; raid cooldowns "
                         "Rallying Cry (War) 0:58", "  2:00: none pressed then"]
    assert len(await fight._covered(rep, f, roars(60, 120), {"Roar"}, t0 + 100_000, t0 + 200_000)) == 2  # a window
    assert await fight._covered(rep, f, roars(60, 120), {"Roar"}, t0, t0 + 30_000) == []  # its casts come later
    # a window on the ability's hits, after its cast: the cast is named, so the cooldowns around it can be asked for
    assert await fight._covered(rep, f, roars(60, 120), {"Roar"}, t0 + 70_000, t0 + 80_000) == [
        "The boss cast Roar at 1:00, before this window: the raid and healing cooldowns pressed around that cast come "
        "with a window that holds it."]
    # more casts than a line each is worth reading: said, with how to get them (a Vashnik kill's 20 Malignant
    # Catalysts got nothing, unsaid, where the wipe's 6 got their lines)
    assert await fight._covered(rep, f, roars(*range(10, 140, 10)), {"Roar"}, t0, t0 + 200_000) == [
        "The boss cast Roar at 13 separate moments here (casts up to 2 s apart are one moment): the raid and healing "
        "cooldowns pressed around each come with 12 or fewer (a window: start, end); pull_timeline without ability "
        "has every cooldown."]
    assert len(await fight._covered(rep, f, roars(*range(10, 130, 10)), {"Roar"}, t0, t0 + 200_000)) == 1 + 12
    # a cast a second (the Lost Explorers' Mighty Thud printed the same cooldowns six times over) is one cast, from
    # 10 s before its first second to 5 s after its last
    pulsed = await fight._covered(rep, f, roars(60, 61, 62, 64, 120), {"Roar"}, t0, t0 + 200_000)
    assert pulsed[1:] == ["  1:00-1:04: healing cooldowns Tranquility (Dru) 0:50, Revival (Monk) 1:05, Divine Hymn "
                          "(Pri) 1:06; raid cooldowns Rallying Cry (War) 0:58", "  2:00: none pressed then"]
    # a start inside the cast's second (copied from a row's m:ss.mmm) still has the cast
    assert len(await fight._covered(rep, f, roars(60), {"Roar"}, t0 + 60_005, t0 + 70_000)) == 2

    async def down(rep, f):
        raise WCLError("Warcraft Logs timed out")
    monkeypatch.setattr(fight, "raid_cooldown_casts", down)  # the rows in hand still come, with what is missing
    assert (await fight._covered(rep, f, roars(60), {"Roar"}, t0, t0 + 200_000))[0].startswith(
        "The raid and healing cooldowns around each cast: unavailable")

    async def refused(rep, f):
        raise Unreadable(rep.code)
    monkeypatch.setattr(fight, "raid_cooldown_casts", refused)  # a report made private since: the call fails with that
    with pytest.raises(Unreadable):
        await fight._covered(rep, f, roars(60), {"Roar"}, t0, t0 + 200_000)


async def test_a_timeline_of_an_ability_not_the_boss_s_own_ends_with_its_tracked_casts(recorded, monkeypatch):
    """A totem's Malignance alone, without the boss's Imbibe it went off at: the model guessed the Imbibe's time. The
    boss's ◆ casts in the window follow the rows, from the enemies' casts already read."""
    rep = await server.lib().report("KBYT4xXn7VNjDgh8")

    async def events_for(items, data_type, **filters):  # Rallying Cry hits no one
        return [[] for _ in items]
    monkeypatch.setattr(rep, "events_for", events_for)
    lines = (await fight.pull_timeline(rep, 13, "Sszorak", ability="Rallying Cry")).splitlines()
    assert lines[2:4] == ["1:11.818 cast: Pomaa → Rallying Cry", "2:28.953 cast: Sinnatony → Rallying Cry"]
    assert lines[-1] == ("The boss's ◆ casts (those Lorrgs tracks) in this pull, to read the rows against "
                         "(pull_timeline without ability has all its casts, with the cooldowns and deaths): "
                         "Venomous Surge 0:29, 1:16, 2:36 · Raging Crosswinds 0:39, 0:40, 1:26, 1:27 · Dig In 1:39")
    # a window has only its own, and none when it has none of them
    window = (await fight.pull_timeline(rep, 13, "Sszorak", end="1:30", ability="Rallying Cry")).splitlines()
    assert window[-1].endswith(" in this window, to read the rows against (pull_timeline without ability has all its "
                               "casts, with the cooldowns and deaths): Venomous Surge 0:29, 1:16 · Raging Crosswinds "
                               "0:39, 0:40, 1:26, 1:27")
    none = await fight.pull_timeline(rep, 13, "Sszorak", start="1:40", end="2:30", ability="Rallying Cry")
    assert none.splitlines()[-1] == "Ability ids: Rallying Cry [97462]" and "◆" not in none


async def test_a_timeline_of_one_players_casts_is_cut_to_its_first_rows_and_counted_in_full(recorded):
    text, failed = await server.call("pull_timeline", {"report": "KBYT4xXn7VNjDgh8", "pull": 13, "player": "Sveini"})
    assert not failed
    lines = text.splitlines()
    events = re.search(r": (\d+) events in Sszorak pull 13, times from the pull's start\.$", lines[1])
    total = int(events[1]) if events else 0
    assert total > 150 and sum(" → " in line and line[0].isdigit() for line in lines) == 150
    # one of the events is their death, which has no source: the rest are theirs
    assert lines[2].startswith("Counted: ") and ", 1 death; " in lines[2] and f"; from: Sveini {total - 1}" in lines[2]
    assert "by ability: Hand of Gul'dan 70 (33 cast, 37 begincast), " in lines[2]  # 33 casts, not 70
    assert re.fullmatch(rf"… {total - 150} more events left out, the last at \d:\d\d\.\d{{3}}: the counts above are of "
                        rf"all {total}\.", lines[-3])
    assert lines[-2].startswith("Ability ids: ")
    assert lines[-1] == "For the rows left out, ask for a narrower window with start and end."
    # a player who wasn't in the pull, and an ability the report doesn't have, are refused with what there is
    for args, error in (({"player": "Nobodyhere"}, "no single player matching"),
                        ({"ability": "Tempst"}, "closest names: Tempest")):
        with pytest.raises(ToolError, match=error):
            await server.call("pull_timeline", {"report": "KBYT4xXn7VNjDgh8", "pull": 13, **args})


async def test_who_pulled_leads_the_answer_that_was_asked_for_it(recorded):
    # "Who pulls early?" is answered by the tally. It closed 42 rows that followed the whole night; it now opens the
    # answer, its rows a pull each under one legend, and the night comes after.
    text, failed = await server.call("progress", CASES["first-hits"].args)
    assert not failed
    lines = text.splitlines()
    assert lines[0].startswith("First on the boss over 42 pulls: Zebinomage 13, Krøpling 9, ")
    assert lines[0].endswith("; at least 1 s ahead of everyone else: Martin 1.")
    # "First hits on", not the boss's own: "Entombed Sentinels's first hits" reads as theirs, and badly
    assert lines[1].startswith("First hits on Sszorak, a pull a row (pull: the first player to hit the boss, ")
    assert lines[2] == "  1: Krøpling (Arcane Surge) at 0.0 s, 0.2 s before Pomaa"
    assert [line.split(":")[0].strip() for line in lines[2:44]] == [str(n) for n in range(1, 43)]
    assert lines[44] == "" and lines[45].startswith("Sszorak: 42 pulls on the night (")


async def test_a_death_recap_leads_with_what_did_the_damage_and_whether_they_could_have_lived(recorded):
    # The answer to "what killed X, could they have lived?" closed the recap, after twenty rows of hits. It now
    # follows the first line: the window added up by ability (a 50k tick was the killing blow of a death a 378k hit
    # caused), then the replay's answer; the rows and the replay's working stay below.
    text, failed = await server.call("death_recap", CASES["death-recap"].args)
    assert not failed
    lines = text.splitlines()
    assert lines[0].endswith("Killing blow: Ula'tek's Presence [1285965].")
    assert lines[1] == (
        "Damage in the 10 s before death, by ability: Raging Crosswinds 503.30k in 9 hits, Venomous Surge 282.79k in 2 "
        "hits, Ula'tek's Presence 270.07k in 5 hits; 1.06M in all. The biggest hit: Raging Crosswinds 377.69k at 0:47, "
        "to 6% health. The killing blow: Ula'tek's Presence 50.23k at 0:48.")
    assert lines[2] == ("Would have kept them alive, if pressed (replayed below; secondary to what killed them): a "
                        "healthstone (if they had one; none used this pull).")
    assert lines[3].startswith("Damage taken in the 10 s before death (")
    # the line's sums are the rows' own: each ability's hits and the total
    rows = re.findall(r"^  \d+:\d\d (.+?) from .+?: ([\d.]+)k", text, re.MULTILINE)
    assert len(rows) == 16 and [name for name, _ in rows].count("Raging Crosswinds") == 9
    assert abs(sum(float(k) for name, k in rows if name == "Raging Crosswinds") - 503.30) < 0.05
    assert "  Barkskin: last used at 0:30 (60 s cooldown), on cooldown until 1:30" in lines
    assert sum(line.startswith("If pressed (12.72k overkill to make up") for line in lines) == 1
    assert "Total 1.06M damage in the window." not in lines  # said once, in the line that adds it up


async def test_a_death_with_no_killing_blow_says_so_once(recorded):
    # "Killing blow: no killing blow recorded." closed the first line and "No killing blow recorded." opened the
    # third, before where they were last. The third line now starts with that.
    text, failed = await server.call("death_recap", CASES["death-recap-thrown"].args)
    assert not failed
    lines = text.splitlines()
    assert lines[0].endswith("Killing blow: no killing blow recorded.")
    assert lines[2].startswith("Last logged position at 0:51: 113 yd from the middle of the rest of the raid")
    assert text.lower().count("no killing blow recorded") == 1


async def test_a_timeline_window_lists_the_ids_of_what_it_names(recorded, monkeypatch):
    # A 15 s window closed with the ids of all 19 abilities of the pull, a third of its text. It lists the ones its
    # lines name; the whole pull still lists them all.
    window, _ = await server.call("pull_timeline", CASES["timeline-window"].args)
    whole, _ = await server.call("pull_timeline", CASES["timeline"].args)
    ids, every = window.splitlines()[-1], whole.splitlines()[-1]
    assert ids.startswith("Ability ids: ") and every.startswith("Ability ids: ")
    named = re.findall(r"([^,\[]+?)(?: \(\d\w\w ability\))? \[\d+\]", ids.removeprefix("Ability ids: "))
    events = "\n".join(window.splitlines()[1:-1])
    assert named and all(name.strip() in events for name in named)
    assert len(named) == 8 and every.count("[") == 19 and "Viscous Cyst" not in ids
    assert set(ids.removeprefix("Ability ids: ").split(", ")) < set(every.removeprefix("Ability ids: ").split(", "))
    # a debuff left out of the lines as too frequent is named in the note under them: its id is listed too
    monkeypatch.setattr("wipebot.core.report.fight.TIMELINE_DEBUFF_GROUPS", 2)
    window, _ = await server.call("pull_timeline", CASES["timeline-window"].args)
    assert "Viscous Cyst [1287205]" not in ids
    assert ", Viscous Cyst (64 applications)" in window and "Viscous Cyst [1287205]" in window.splitlines()[-1]
    assert "Dig In" not in window  # named by neither: still left out



def test_players_whose_amounts_print_the_same_share_a_place():
    """mechanic_hits' night against night: equal numbers get one place, marked, and the next place is skipped."""
    from wipebot.core.report.mechanics import _places
    places = _places({"A": 3.0, "B": 2.004, "C": 2.001, "D": 1.0}, lambda v: f"{v:.2f}")
    assert places == {"A": (1, "1st"), "B": (2, "2nd, tied"), "C": (2, "2nd, tied"), "D": (4, "4th")}
