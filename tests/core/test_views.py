"""The views (report/views.py, the page in app/): each view's data on a real pull, against its tool's text and its
snapshot; the page drawn in the mock host (viewhost.py, Chromium); the replay's JavaScript against the tracks
maps.replay packs (Node)."""
import asyncio
import contextlib
import hashlib
import io
import itertools
import json
import math
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import cast

import pytest
from mcp.types import CallToolResult, TextContent

from tests.support import assert_snapshot
from tests.support.paths import REAL
from tests.support.recorded import CASES, LOCAL, LOCAL_RECORDED, NOT_LOCAL, Replay, serve_from
from wipebot.core import server
from wipebot.core.classes import CLASS_COLORS, WHITE
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError, maps, views
from wipebot.core.report import note as notes
from wipebot.core.report.ending import EndCast, Ending, HalfDead, KeptBuff, LeadIn, MassDeath, mass_death_of
from wipebot.core.text import clock, plural, short

PULL_CASE = CASES["pull-summary"]  # the pull the views are tested on: its summary and timeline are recorded


def made_up_icon() -> bytes:
    """A spell's icon, made up (Wowhead's art isn't published here): a plain square."""
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (56, 56), (80, 140, 200)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, serving many users as remote.py does (the views' key); spells'
    icons are made up."""
    from wipebot.core import knowledge
    serve_from(monkeypatch, Replay())
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str))

    async def spell_icon(spell_id):
        return made_up_icon()
    monkeypatch.setattr(knowledge, "spell_icon", spell_icon)


async def pull_data() -> dict:
    result = await server.view_data("pull", PULL_CASE.args["pull"], None, PULL_CASE.args["report"])
    assert result.structured_content is not None
    return result.structured_content


def readable(data: dict) -> str:
    """A view's data for its snapshot: a line per field, and a line per item of a list (a death, a point)."""
    def one(v) -> str:
        return json.dumps(v, ensure_ascii=False)
    lines = []
    for k, v in data.items():
        if isinstance(v, list) and v:
            lines += [f"{one(k)}: ["] + [f"  {one(x)}," for x in v] + ["],"]
        elif isinstance(v, dict) and any(isinstance(x, list) for x in v.values()):
            lines += [f"{one(k)}: {{"] + [f"  {one(kk)}: {one(vv)}," for kk, vv in v.items()] + ["},"]
        else:
            lines.append(f"{one(k)}: {one(v)},")
    return "\n".join(lines) + "\n"


async def test_the_pull_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "pull.txt", readable(await pull_data()))


async def test_the_pull_view_states_what_pull_summary_states(recorded):
    data = await pull_data()
    text, failed = await server.call("pull_summary", PULL_CASE.args)
    assert not failed
    deaths = re.findall(r"^\d+\. (\d+:\d\d) (\S+)", text, re.MULTILINE)
    assert [(clock(d["ms"]), d["name"]) for d in data["deaths"]] == deaths
    decided = data["decided"]
    assert decided and f"gained {decided['why'].removeprefix('the boss gained ')} at {clock(decided['ms'])}" in text
    assert [d["fallout"] for d in data["deaths"]] == [d["ms"] > decided["ms"] for d in data["deaths"]]
    assert f"{data['boss_pct']}% health" in text
    assert f"**{data['boss']}** ({data['difficulty']}) · pull {data['pull']}" in text
    # the card's headline is the text's "Ended by", without its ids and what to look up
    headline = data["headline"]["text"]
    assert headline == "The boss gained Unbound Ferocity at 2:32 and kept it to the end, with 16 of 20 alive."
    assert headline.removeprefix("The boss ").removesuffix(".").replace(", with ", ", ") in text
    assert len(json.dumps(data)) < 150_000 // 10  # far under Claude's limit for a tool result


def test_damage_peaks_are_the_runs_pull_timeline_marks_twice_the_median():
    damage = {0: 100.0, 1: 100.0, 2: 250.0, 3: 300.0, 4: 100.0, 5: 100.0, 6: 199.0, 7: 200.0, 8: 100.0}  # median 100
    step = views.DAMAGE_BUCKET_MS
    assert views.damage_peaks(damage) == [{"from_ms": 2 * step, "to_ms": 4 * step, "per_s": 300},
                                          {"from_ms": 7 * step, "to_ms": 8 * step, "per_s": 200}]
    assert views.damage_peaks({}) == []


DEATH_CASE = CASES["death-recap"]  # Scopidruid's death on the same pull


async def death_data() -> dict:
    key = await server.view_key("death_recap", DEATH_CASE.args)
    assert key is not None and key["death"] == 1
    result = await server.view_data(**key)
    assert result.structured_content is not None
    return result.structured_content


async def test_the_death_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "death.txt", readable(await death_data()))


async def test_the_death_view_states_what_death_recap_states(recorded):
    data = await death_data()
    text, failed = await server.call("death_recap", DEATH_CASE.args)
    assert not failed
    assert f"died at {clock(data['at_ms'])}" in text and f"Killing blow: {data['by']} [" in text
    listed = re.findall(r"^  (\d+:\d\d) (.+?) from ", text, re.MULTILINE)
    assert [(clock(h["ms"]), h["by"]) for h in data["hits"]] == listed
    for d in data["defensives"]:
        assert f"  {d['name']}: " in text
    assert ("on cooldown" in text) == any(d["state"] == "cooldown" for d in data["defensives"])
    for saver in data["saved_by"]:
        assert re.search(rf"^  {re.escape(saver)} \(.*: would have kept them alive", text, re.MULTILINE)
    assert f"Healthstones and healing potions before death: {data['stones']}" in text
    assert "Killed outright" not in text and not data["outright"]
    assert data["saved_by"] == ["a healthstone"]  # the text's line of it is checked above: the card's headline
    assert data["headline"] == {"text": "A healthstone, used in time, would have kept them alive.", "kind": "fix"}


async def test_the_death_view_draws_in_a_host_without_errors(recorded, tmp_path):
    taken = await shots(tmp_path, "death_recap", DEATH_CASE.args, themes=("dark", "light"), widths=(320, 760),
                        modes=("inline", "fullscreen"))
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert "Scopidruid" in s.text and "Ula'tek's Presence" in s.text and "would have kept them alive" in s.text
    assert "hits in the 10 s before the death" in taken[-1].text.lower()  # fullscreen: the hits, one per row


NIGHT_OF_PULLS = CASES["progress"]  # the raid's night on Sszorak


async def raid_night_data() -> dict:
    key = await server.view_key("progress", NIGHT_OF_PULLS.args)
    assert key == {"view": "night", "report": NIGHT_OF_PULLS.args["report"], "boss": "Sszorak"}
    result = await server.view_data(**key)
    assert result.structured_content is not None
    return result.structured_content


async def test_the_raid_night_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "night.txt", readable(await raid_night_data()))


async def test_the_raid_night_view_states_what_progress_states(recorded):
    data = await raid_night_data()
    text, failed = await server.call("progress", NIGHT_OF_PULLS.args)
    assert not failed
    assert f"Sszorak: {len(data['pulls'])} pulls on the night" in text
    for r in data["pulls"]:
        d = r["first"]
        first = f" first death {d['name']} ({d['by']}, {clock(d['ms'])})" if d else ""
        assert re.search(rf"^  {r['pull']}: .*{clock(r['duration_ms'])}[^;]*;{re.escape(first)}", text, re.MULTILINE), r
    ends = ", ".join(f"{w} {n}" for w, n in data["ends"])
    assert f"(pull_summary's \"Ended by\"): {ends}." in text
    what, most = data["ends"][0]  # the card's headline: what ended the most pulls, counted over all of them
    assert data["headline"]["text"] == f"{most} of the {len(data['pulls'])} pulls ended with {what}."
    assert "first third {:.1f}, middle {:.1f}, last third {:.1f}".format(*data["thirds"]) in text


async def test_the_raid_night_view_draws_and_a_pulls_bar_opens_that_pull(recorded, tmp_path):
    first, opened = await shots(tmp_path, "progress", NIGHT_OF_PULLS.args, themes=("dark",), widths=(760,),
                                modes=("fullscreen",), clicks=('css:tr[data-pull="13"]',))
    assert not first.errors and "how the 33 pulls ended" in first.text.lower() and "Vashnik" in first.text
    assert "Sszorak pull 13" in opened.text  # that pull's card


# ---- the meter: a ranking of players from breakdown, mechanic_hits and enemies, in the bot only ----

METER_CASES = ["mechanic-hits", "damage-taken-ability", "interrupts", "dispels", "nekzali-enemies-night",
               "vashnik-enemies", "damage-least"]


async def meter_data(case: str) -> dict:
    c = CASES[case]
    key = await server.view_key(c.tool, c.args)
    assert key is not None and key["view"] == "meter" and key["tool"] == c.tool, case
    result = await server.view_data(**key)
    assert result.structured_content is not None
    return result.structured_content


async def test_the_meter_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "meter.txt", readable(await meter_data("mechanic-hits")))


@pytest.mark.parametrize("case", METER_CASES)
async def test_a_meter_ranks_the_players_its_tool_states(recorded, case):
    data = await meter_data(case)
    text, failed = await server.call(CASES[case].tool, CASES[case].args)
    assert not failed
    for m in data["meters"]:
        values = [b["value"] for b in m["bars"]]
        assert len(values) >= 3 and values == sorted(values, reverse=True), m["title"]
        assert m["total"] == sum(values)
        for b in m["bars"] if CASES[case].tool != "enemies" else m["bars"][:1]:  # enemies names the top few
            assert b["name"] in text, (m["title"], b["name"])
            if m["unit"] == "kicks":
                assert re.search(rf"^  {re.escape(b['name'])}: {round(b['value'])}\b", text, re.MULTILINE)
            elif m["unit"] == "amount" and CASES[case].tool != "enemies":
                assert short(b["value"]) in text, (b["name"], short(b["value"]))


async def test_a_meter_shows_the_deaths_a_mechanic_set_up(recorded):
    data = await meter_data("mechanic-hits")
    text, _ = await server.call("mechanic_hits", CASES["mechanic-hits"].args)
    [m] = data["meters"]
    assert m["title"] == "Tempest" and m["icon"]
    for b in m["bars"]:
        assert re.search(rf"^  {re.escape(b['name'])}(?: \(tank\))?: .*, {plural(b['died_after'], 'death')} within "
                         rf"6 s, {plural(b['killed_by'], 'killing blow')}", text, re.MULTILINE), b["name"]
    assert f"{plural(sum(b['killed_by'] for b in m['bars']), 'death')} with it as the killing blow" in m["note"]


async def test_a_mechanics_meter_over_the_night_counts_each_players_hits_pull_by_pull(recorded):
    data = await meter_data("mechanic-hits")
    [m] = data["meters"]
    assert m["per_pull"] == "hits" and len(m["pulls"]) > 1
    for b in m["bars"]:
        assert len(b["per_pull"]) == len(m["pulls"])
        hits = re.match(r"(\d+) hits?", b["note"])
        assert sum(v or 0 for v in b["per_pull"]) == (int(hits.group(1)) if hits else 0), b["name"]
        assert len(b["died_in"]) <= b["died_after"] and all(b["per_pull"][i] is not None for i in b["died_in"])


async def test_a_meter_of_the_least_ranks_the_damage_dealers_and_is_drawn_from_the_bottom(recorded, tmp_path):
    """For "who did the least damage?" (breakdown's least) the meter ranks only the damage dealers, as its text, and
    draws the least first: the bottom players get a bar, the top ones the line."""
    data = await meter_data("damage-least")
    [m] = data["meters"]
    assert m.get("least") and {b["role"] for b in m["bars"]} == {"dps"} and "damage dealers only" in m["scope"]
    [s] = await shots(tmp_path, "breakdown", CASES["damage-least"].args)
    assert not s.errors and "least" in s.text.lower()
    least, top = m["bars"][-1]["name"], m["bars"][0]["name"]
    assert s.text.index(least) < s.text.index(top)


@pytest.mark.parametrize("case", ["damage-taken", "mechanic-outliers", "buffs", "damage-by-pull", "breakdown-casts",
                                  "healing-by-ability"])
async def test_a_call_that_ranks_no_players_has_no_meter(recorded, case):
    assert await server.view_key(CASES[case].tool, CASES[case].args) is None


async def test_the_meter_draws_a_panel_per_meter_without_errors(recorded, tmp_path):
    data = await meter_data("nekzali-enemies-night")
    [s] = await shots(tmp_path, "enemies", CASES["nekzali-enemies-night"].args)
    assert not s.errors, s.errors
    assert s.height > 100 and s.path.name == "discord-dark-680-inline.png"  # as the bot posts it: no card in a host
    for m in data["meters"]:
        assert m["title"] in s.text and m["bars"][0]["name"] in s.text


# ---- the raid note against every pull on a boss: kicks and planned cooldowns pull by pull, in the bot only ----

NOTE_CASES = ["raid-note-order-change", "raid-note-night"]  # The Twin Fangs' kicks; Sszorak's cooldowns


async def note_data(case: str) -> dict:
    c = CASES[case]
    key = await server.view_key(c.tool, c.args)
    assert key is not None and key["view"] == "note" and key["tool"] == "raid_note", case
    result = await server.view_data(**key)
    assert result.structured_content is not None
    return result.structured_content


async def test_the_note_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "note.txt", readable(await note_data("raid-note-order-change")))


async def test_the_note_view_counts_the_kicks_raid_note_counts(recorded):
    data, (text, failed) = await note_data("raid-note-order-change"), await server.call(
        "raid_note", CASES["raid-note-order-change"].args)
    assert not failed
    k = data["kicks"]
    assert f"Kicks are counted for pulls {k['from_pull']}-" in text
    counts = re.findall(r"^  \S+ \(Kick[^)]*\): kicked (\d+) of (\d+)", text, re.MULTILINE)
    made, alive = sum(int(m) for m, _ in counts), sum(int(a) for _, a in counts)
    assert (k["made"], k["alive"]) == (made, alive)
    assert f"went through, {k['lost']} of {k['counted']} (" in text
    first = re.search(r"whose cast went through first while they were alive.*?: (.*?)(?:; no one's|$)", text,
                      re.MULTILINE)
    assert first and [(f["name"], f["count"]) for f in k["first"]] == [
        (n, int(c)) for n, c in re.findall(r"(?:^|; )(\S+) (\d+):", first.group(1))]
    leads = [c for p in k["pulls"] for cell in p["cells"] for c in cell if c["lead"]]
    assert len(leads) == sum(f["count"] for f in k["first"]) and all(c["state"] == "through" for c in leads)


async def test_the_note_view_states_the_planned_cooldowns_raid_note_states(recorded):
    data, (text, _) = await note_data("raid-note-night"), await server.call("raid_note", CASES["raid-note-night"].args)
    c = data["cooldowns"]
    assert c and c["uses"]
    cells = [x for p in c["pulls"] for x in p["cells"] if x]
    totals = re.findall(r"^  \S+: (\d+) planned uses; (.*)$", text, re.MULTILINE)
    assert len(cells) == sum(int(n) for n, _ in totals)
    for state in ("on time", "not cast", "dead"):
        assert sum(x["state"] == state for x in cells) == sum(
            int(m.group(1)) for _, rest in totals if (m := re.search(rf"\b{state} (\d+)", rest))), state
    assert sum(x["state"] in ("early", "late") for x in cells) == sum(
        int(n) for _, rest in totals for n in re.findall(r"\b(?:early|late) (\d+)", rest))


async def test_the_note_view_counts_the_callouts_raid_note_counts(recorded):
    data, (text, _) = await note_data("raid-note-night"), await server.call("raid_note", CASES["raid-note-night"].args)
    c = data["callouts"]
    [personals] = c["callouts"]  # the only one checked: "Trigger Virulence" and the orb popper aren't
    assert personals["text"] == "Big Personals" and personals["to"] == "everyone"
    assert '"Trigger Virulence" to Martin at' in text.split("Callouts not checked", 1)[1]
    block = text.split('"Big Personals" to everyone', 1)[1].split("\n  \"", 1)[0]
    counts = {n: (int(a), int(m))
              for n, a, m in re.findall(r"^    (\S+): alive for (\d+); pressed at (\d+)", block, re.MULTILINE)}
    assert counts and {r["player"]: (r["alive"], r["made"]) for r in personals["rows"]} == counts
    assert {s for r in personals["rows"] for p in r["cells"] for s in p} == {"answered", "none", "dead", "excused"}


@pytest.mark.parametrize("case", ["raid-note", "raid-note-kicks"])
async def test_one_pulls_note_check_has_no_view(recorded, case):
    assert await server.view_key(CASES[case].tool, CASES[case].args) is None


async def test_the_note_view_draws_a_panel_each_without_errors(recorded, tmp_path):
    data = await note_data("raid-note-order-change")
    [s] = await shots(tmp_path, "raid_note", CASES["raid-note-order-change"].args)
    assert not s.errors, s.errors
    assert s.height > 300 and s.path.name.startswith("discord-")
    assert "Kick assignments" in s.text and data["kicks"]["slots"][0]["player"] in s.text


async def test_the_note_for_the_next_raid_is_drawn_as_it_is_player_by_player(recorded, tmp_path, monkeypatch):
    c = CASES["raid-note-order-change"]
    report, note = c.args["report"], c.args["note"]
    pasted = {"report": report, "boss": "Twin Fangs", "upcoming": True, "note": note}
    assert await server.view_key("raid_note", pasted) is None  # a pasted note is checked against the pulls
    key = await server.view_key("raid_note", {"report": report, "boss": "Twin Fangs", "upcoming": True})
    assert key == {"view": "note", "report": report, "boss": "Twin Fangs", "tool": "raid_note",
                   "args": {"boss": "Twin Fangs", "upcoming": True}}
    result = await server.view_data("note", boss="Twin Fangs", report=report, tool="raid_note",
                                    args={**key["args"], "note": note})
    data = result.structured_content
    assert data is not None
    assert_snapshot(REAL / "views" / "note-plan.txt", readable(data))
    parsed, rows = notes.parse(note)[0], data["plan"]["rows"]
    assert sorted((u.player, u.phase, u.at) for u in parsed.cooldowns) == sorted(
        (r["player"], i["phase"], i["at_s"]) for r in rows for i in r["items"] if "spell" in i)
    assert sum(len(o.players) for o in parsed.callouts) == sum("text" in i for r in rows for i in r["items"])
    order = [views.ROLE_ORDER.get(r["role"], 3) for r in rows]
    assert order == sorted(order)  # tanks, healers, then the rest
    # drawn from the note the guild saved
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))
    monkeypatch.setattr(server, "_a_member", lambda guild: True)
    Notebook().save_note(14169, 3421, "Twin Fangs", note, "Rowan", at=1000.0)
    [s] = await shots(tmp_path, "raid_note", key["args"] | {"report": report})
    assert not s.errors, s.errors
    assert "Twin Fangs: who does what" in s.text and rows[0]["player"] in s.text and "saved by Rowan" in s.text


VERSUS_CASE = CASES["compare-raid-mates"]  # two Frost Mages over every Twin Fangs pull both were in


async def test_two_raid_mates_view_states_what_compare_with_top_states(recorded, tmp_path):
    key = await server.view_key(VERSUS_CASE.tool, VERSUS_CASE.args)
    assert key == {"view": "versus", "report": VERSUS_CASE.args["report"], "boss": "The Twin Fangs",
                   "player": "Krøpling", "tool": "compare_with_top",
                   "args": {"against": "Zebinomage", "all_pulls": True}}
    result = await server.view_data(key["view"], boss=key["boss"], report=key["report"], player=key["player"],
                                    tool=key["tool"], args=key["args"])
    data = result.structured_content
    assert data is not None
    assert_snapshot(REAL / "views" / "versus.txt", readable(data))
    text, failed = await server.call(VERSUS_CASE.tool, VERSUS_CASE.args)
    assert not failed
    assert f"on The Twin Fangs, {data['pulls']} pulls both were in" in text
    assert f"per second alive: {short(data['a']['per_s'])} / {short(data['b']['per_s'])}" in text
    assert [s["name"] for s in data["spells"]] == re.findall(r"^  (.+?): ", text, re.MULTILINE)
    assert [bool(s["far"]) for s in data["spells"]] == [
        "far apart" in line for line in text.splitlines() if line.startswith("  ")]
    [s] = await shots(tmp_path, VERSUS_CASE.tool, VERSUS_CASE.args)
    assert not s.errors, s.errors
    assert "Krøpling" in s.text and "Glacial Spike" in s.text and s.path.name.startswith("discord-")


MANA_CASE = CASES["healing-mana"]  # healers low late on a Twin Fangs pull, and the druid's Innervates


async def test_the_mana_view_states_what_healing_with_mana_states(recorded, tmp_path):
    key = await server.view_key(MANA_CASE.tool, MANA_CASE.args)
    assert key == {"view": "mana", "report": MANA_CASE.args["report"], "pull": 25, "boss": "The Twin Fangs"}
    assert await server.view_key("healing", {k: v for k, v in MANA_CASE.args.items() if k != "mana"}) is None
    assert await server.view_key("healing", CASES["healing-mana-night"].args) is None
    result = await server.view_data(**key)
    data = result.structured_content
    assert data is not None
    assert_snapshot(REAL / "views" / "mana.txt", readable(data))
    text, failed = await server.call(MANA_CASE.tool, MANA_CASE.args)
    assert not failed
    for h in data["healers"]:
        assert f"  {h['name']} ({h['spec']}): lowest {h['lowest'][1]}% at {clock(h['lowest'][0])}" in text
    casts = re.findall(r"cast (\d+:\d\d) on (\S+) \(at (\d+)%\)", text)
    assert [(clock(c["ms"]), c["on"], str(c["pct"])) for i in data["innervates"] for c in i["casts"]] == casts
    ready = re.findall(r"ready (\d+:\d\d)-(\d+:\d\d) while", text)
    assert [(clock(r["from_ms"]), clock(r["to_ms"])) for i in data["innervates"] for r in i["ready_low"]] == ready
    assert ready  # the case's point: it sat ready while a healer was low
    [s] = await shots(tmp_path, MANA_CASE.tool, MANA_CASE.args)
    assert not s.errors, s.errors
    assert "Healers' mana" in s.text and data["healers"][0]["name"] in s.text and s.path.name.startswith("discord-")


async def test_two_healers_of_one_class_have_lines_told_apart_and_every_line_its_name(monkeypatch):
    """Made up: three priests and a druid. A line took only its class's colour, so three priests were one line
    three times; the second is now dashed and the third dotted, and each line ends in its healer's name, a priest's
    after a piece of their own line."""
    def healer(name: str, color: str, ends_at: int) -> dict:
        return {"name": name, "spec": "Holy Priest", "color": color, "dead_ms": None, "lowest": [60000, ends_at],
                "low": [], "points": [[0, 100], [30000, 80], [60000, ends_at]]}
    data = {"view": "mana", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "pull": 4, "until_ms": 60000,
            "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678#fight=4", "decided": False, "low": 30,
            "title": "Torvek's Forge pull 4: healers' mana", "cooldown_s": 180.0, "never": [], "innervates": [],
            "healers": [healer("Mirelle", "#FFFFFF", 62), healer("Halvard", "#FFFFFF", 60),
                        healer("Oswin", "#FFFFFF", 58), healer("Brannoch", "#FF7C0A", 40)]}
    made_up_view(monkeypatch, {"view": "mana", "report": data["report"], "pull": 4, "boss": data["boss"]}, data)
    from wipebot.core import viewhost
    setup = viewhost._picture_setup("healing", {}, {"view": "mana", "report": data["report"], "pull": 4})
    async with chromium() as browser:
        errors: list[str] = []
        page = await viewhost._open(browser, setup, errors)
        await viewhost._settled(page)
        view = page.frames[-1]
        lines = await view.locator("svg path.cl[stroke-linejoin]").evaluate_all(
            "els => els.map((e) => e.getAttribute('stroke-dasharray'))")
        assert lines == [None, "7 3", "2 3", None]
        pieces = await view.locator("svg path.cl:not([stroke-linejoin])").evaluate_all(
            "els => els.map((e) => [e.getAttribute('stroke-dasharray'), e.nextElementSibling.textContent])")
        assert pieces == [[None, "Mirelle"], ["7 3", "Halvard"], ["2 3", "Oswin"]]  # the druid's colour is his alone
        names = await view.locator("svg text.cls").evaluate_all(
            "els => els.map((e) => [e.textContent, e.getBoundingClientRect().top, e.getBoundingClientRect().left])")
        assert sorted(n for n, _, _ in names) == ["Brannoch", "Halvard", "Mirelle", "Oswin"]
        tops = sorted(top for _, top, _ in names)
        assert all(b - a >= 11 for a, b in itertools.pairwise(tops))  # none on another, though three end together
        plot_right = await view.locator("svg line").first.evaluate("(e) => e.getBoundingClientRect().right")
        assert all(left >= plot_right for _, _, left in names)  # in the margin, past the lines' ends
        assert not errors


async def test_against_another_night_a_meters_marks_keep_off_its_numbers_and_long_names_are_cut_at_their_end(recorded):
    """The other night's mark sat on the amount in a row whose earlier amount was near the top one; the bars and
    the marks now end where the amounts begin. In the grid by pull a long name ran off the left edge."""
    from wipebot.core import viewhost
    case = CASES["mechanic-hits-against"]
    key = await viewhost.picture_key(case.tool, case.args)
    assert key and key["view"] == "meter"
    async with chromium() as browser:
        errors: list[str] = []
        page = await viewhost._open(browser, viewhost._picture_setup(case.tool, case.args, key), errors)
        await viewhost._settled(page)
        view = page.frames[-1]
        rows = await view.locator(".meter-rows li:has(.was)").evaluate_all("""els => els.map((li) => {
          const right = (sel) => li.querySelector(sel).getBoundingClientRect().right;
          const numbers = [...li.querySelectorAll(".row:not(.on) :is(.dd, .val, .dl)")].filter((e) => e.textContent);
          const first = Math.min(...numbers.map((e) => e.getBoundingClientRect().left));
          return [right(".was"), right(".fill"), first]; })""")
        assert len(rows) >= 5 and all(mark <= numbers and bar <= numbers for mark, bar, numbers in rows), rows
        cut = await view.evaluate("""() => { const s = kit.fit("Averyveryverylongname-Argentdawn", 80, 11, 600);
          return [s, kit.width(s, 11, 600), kit.fit("Mirelle", 80, 11, 600)]; }""")
        assert cut[0].startswith("Averyvery") and cut[0].endswith("…") and cut[1] <= 80 and cut[2] == "Mirelle"
        assert not errors
        # the grid by pull: the names' column is as wide as the longest of them, so the raid's own are whole
        case = CASES["mechanic-hits"]
        key = await viewhost.picture_key(case.tool, case.args)
        assert key
        page = await viewhost._open(browser, viewhost._picture_setup(case.tool, case.args, key), errors)
        await viewhost._settled(page)
        names = await page.frames[-1].locator('[data-panel="by_pull"] svg text.cls').evaluate_all(
            "els => els.map((e) => [e.textContent, e.getBoundingClientRect().left"
            " - e.ownerSVGElement.getBoundingClientRect().left])")
        assert len(names) > 15 and not any("…" in name or left < 0 for name, left in names), names
        assert not errors


async def test_pull_summary_gives_its_views_key_only_to_a_client_that_shows_apps(recorded, monkeypatch):
    # a client without MCP Apps (Claude Code) gives the model structuredContent instead of the text: none for it
    no_apps = await server.mcp.call_tool("pull_summary", PULL_CASE.args)
    assert isinstance(no_apps, CallToolResult) and no_apps.structured_content is None
    first = no_apps.content[0]
    assert isinstance(first, TextContent) and first.text.startswith("**Sszorak**")
    monkeypatch.setattr(server, "_supports_apps", lambda ctx: True)  # claude.ai, ChatGPT
    result = await server.mcp.call_tool("pull_summary", PULL_CASE.args)
    assert isinstance(result, CallToolResult)
    assert result.structured_content == {"view": "pull", "report": PULL_CASE.args["report"],
                                         "pull": PULL_CASE.args["pull"], "boss": "Sszorak"}
    monkeypatch.setattr(server.DEFAULT, "remote", None)  # in-process (the bot, `wipebot call`): the text alone
    text_only = await server.mcp.call_tool("pull_summary", PULL_CASE.args)
    assert isinstance(text_only, CallToolResult) and text_only.structured_content is None


def test_the_tools_with_a_card_are_the_ones_whose_result_carries_its_key():
    """One list in three places, held together here: the tools the remote server gives the views' page to (a host
    draws a card on every call of those), the tools whose result carries the view's key (server._shown, and pull_map
    with its replay's), and the tools the mock host gives a key."""
    from wipebot.core import viewhost
    keyed = set(re.findall(r'await _shown\("(\w+)"', Path(server.__file__).read_text(encoding="utf-8")))
    remote = (Path(server.__file__).parents[1] / "mcp" / "remote.py").read_text(encoding="utf-8")
    listed = re.search(r"server\.register\(mcp, setup, meta=\{(.*?)\}", remote, re.DOTALL)
    assert listed and set(re.findall(r'"(\w+)": shown', listed[1])) == keyed | {"pull_map"}
    assert keyed == viewhost.SHOWN and keyed < set(server.VIEW_TOOLS)


async def test_the_mock_host_draws_what_the_server_sends(recorded, tmp_path):
    """A tool whose drawing only the Discord bot posts gets no key and no card in the mock host, as in Claude and
    ChatGPT (it once got both, and a meter shot there was published as Claude's): it is shot as the picture the bot
    posts, and named so."""
    from wipebot.core import viewhost
    meter = CASES["mechanic-hits"]
    assert await server.view_key(meter.tool, meter.args)  # a drawing there is
    assert "structuredContent" not in await viewhost.tool_result(meter.tool, meter.args)
    assert (await viewhost.picture_key(meter.tool, meter.args) or {}).get("view") == "meter"
    assert (await viewhost.tool_result("pull_summary", PULL_CASE.args))["structuredContent"]["view"] == "pull"
    assert await viewhost.picture_key("pull_summary", PULL_CASE.args) is None
    [s] = await shots(tmp_path, meter.tool, meter.args, themes=("dark", "light"), widths=(320, 760))
    assert s.path.name == "discord-dark-680-inline.png" and (s.theme, s.width) == ("dark", 680) and not s.errors
    host = next(m["msg"]["result"]["hostInfo"]["name"] for m in s.messages if "hostInfo" in m["msg"].get("result", {}))
    assert host == "wipebot-picture"
    from PIL import Image
    assert Image.open(s.path).width == 680 * viewhost.PICTURE_SCALE


async def test_a_view_that_does_not_exist_is_a_readable_error(recorded):
    with pytest.raises(server.FactError, match="no view 'weekly'"):
        await server.view_data("weekly", PULL_CASE.args["pull"], None, PULL_CASE.args["report"])


def test_the_page_is_one_file_with_a_hashed_address():
    page = views.page(pictures=True)
    assert page.startswith("<!doctype html>") and "/*STYLE*/" not in page and "/*SCRIPT*/" not in page
    for name in views.SCRIPTS:  # every script inline: the page loads nothing
        first = resources.files("wipebot.core").joinpath("app", name).read_text(encoding="utf-8").splitlines()[0]
        assert first in page
    assert "<script src" not in page and "<link" not in page
    assert re.fullmatch(r"ui://wipebot/views-[0-9a-f]{10}\.html", views.page_uri())


def test_the_page_claude_and_chatgpt_load_holds_only_the_views_they_show():
    """A meter, a note's board, two raid mates and the healers' mana are drawn only as the Discord bot's pictures: in
    Claude and ChatGPT no tool's result names them and no view opens them. Their script, a quarter of the page's, was
    sent there all the same; it is now in the Painter's page alone, and the address is the page those hosts load."""
    from wipebot.core import viewhost
    hosted, painted = views.page(), views.page(pictures=True)

    def defined(page: str) -> set[str]:
        return set(re.findall(r"^views\.(\w+) = ", page, re.MULTILINE))
    shown = {server.VIEW_TOOLS[tool] for tool in viewhost.SHOWN} | {"replay"}  # pull_map's result is its replay
    assert defined(hosted) == shown and set(re.findall(r'\bopen\("(\w+)"', hosted)) <= shown
    assert defined(painted) - defined(hosted) == {"meter", "note", "versus", "mana"}
    assert all(name in views.SCRIPTS for name in views.PICTURES_ONLY)
    assert len(hosted) < 0.8 * len(painted)
    assert views.page_uri().endswith(f"-{hashlib.sha256(hosted.encode()).hexdigest()[:10]}.html")


# ---- the page in the mock host (Chromium) ----

async def shots(tmp_path, tool: str, args: dict, **kw):
    pytest.importorskip("playwright")
    from playwright.async_api import Error

    from wipebot.core import viewhost
    try:
        return await viewhost.shoot(tool, args, tmp_path, **kw)
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium (just browser)")
        raise


@contextlib.asynccontextmanager
async def chromium():
    """Playwright's Chromium, for the mock host; a test without it is skipped."""
    pytest.importorskip("playwright")
    from playwright.async_api import Error, async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Error as e:
            pytest.skip(f"needs Playwright's Chromium: {e}")
        yield browser
        await browser.close()


def host_setup(tool: str, args: dict, result: dict | None, **setup) -> dict:
    """What the mock host is told (host.html's window.__setup): dark, 760 wide and inline unless `setup` says."""
    from wipebot.core import viewhost
    return {"tool": tool, "args": args, "result": result, "theme": "dark", "width": 760, "mode": "inline",
            "tokens": "none", "bare": True, "fullWidth": viewhost.FULL_WIDTH,
            "fullHeight": viewhost.FULL_HEIGHT} | setup


@contextlib.asynccontextmanager
async def drawn(tool: str, args: dict, touch: bool = False, **setup):
    """The tool's view drawn in the mock host, to ask the page about: its frame, and the errors so far. `touch`:
    as on a phone, where a finger is the pointer."""
    from wipebot.core import viewhost
    setup = host_setup(tool, args, await viewhost.tool_result(tool, args), **setup)
    async with chromium() as browser:
        errors: list[str] = []
        if touch:
            context = await browser.new_context(has_touch=True, is_mobile=True,
                                                viewport={"width": viewhost.FULL_WIDTH + 40, "height": 1000})
            page = await context.new_page()
            await viewhost._wire(page, setup, errors)
        else:
            page = await viewhost._open(browser, setup, errors)
        await viewhost._settled(page)
        yield page.frames[-1], errors


def test_a_charts_text_size_is_texts_own_parameter():
    """kit.text writes the one font-size, from its size parameter (kit.SIZE): given again in its attributes, the
    browser keeps the first and drops the second, which once drew every label at 11 px whatever its caller asked."""
    for name in views.SCRIPTS:
        script = resources.files("wipebot.core").joinpath("app", name).read_text(encoding="utf-8")
        assert all(size.startswith("${") for size in re.findall(r'font-size="([^"]*)"', script)), name


async def test_chart_text_is_drawn_at_the_sizes_asked_and_the_mock_host_refuses_an_attribute_given_twice(recorded):
    from wipebot.core import viewhost
    async with drawn("pull_summary", PULL_CASE.args) as (view, errors):
        sizes = await view.evaluate("""() => {
            const of = (s) => [...document.querySelectorAll("svg text")].find((t) => t.textContent === s);
            const px = (t) => t && [t.getAttribute("font-size"), getComputedStyle(t).fontSize];
            const inMark = document.querySelector('svg text.tb');  // how many died at once
            return { label: px(of("boss health")), value: px(of("decided 2:32")), mark: px(inMark), all: kit.SIZE };
        }""")
        assert sizes["all"] == {"label": 11, "value": 12, "mark": 9}
        assert sizes["label"] == ["11", "11px"] and sizes["value"] == ["12", "12px"] and sizes["mark"] == ["9", "9px"]
        assert await viewhost._markup_errors(view.page) == [] and not errors
        twice = await view.evaluate("""() => {
            const text = kit.text;
            kit.text = (x, y, s, cls, attrs, size) => text(x, y, s, cls, `font-size="9" ${attrs || ""}`, size);
            return true;
        }""")
        assert twice
        [refused] = (await viewhost._markup_errors(view.page))[:1]
        assert "Attribute 'font-size' is redefined" in refused


# ---- the colours, measured: contrast on the card (WCAG 2), and how far apart two are for a red-green colour-blind
# reader (Machado, Oliveira and Fernandes 2009, full protanopia and deuteranopia; the distance in OKLab, times 100) ----

def _linear(rgb: list[int]) -> list[float]:
    return [c / 3294.6 if c <= 10 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]


def contrast(a: list[int], b: list[int]) -> float:
    lighter, darker = sorted((0.2126 * r + 0.7152 * g + 0.0722 * bl + 0.05 for r, g, bl in (_linear(a), _linear(b))),
                             reverse=True)
    return lighter / darker


SEEN_BY = {  # linear RGB as each sees it
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
}


def _oklab(lin: list[float]) -> tuple[float, float, float]:
    r, g, b = lin
    lo, m, s = (x ** (1 / 3) for x in (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                                       0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                                       0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b))
    return (0.2104542553 * lo + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * lo - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * lo + 0.7827717662 * m - 0.8086757660 * s)


def apart(a: list[int], b: list[int], seen_by: str | None = None) -> float:
    """How far apart two colours are (OKLab times 100), as everyone sees them or as `seen_by` does."""
    def lab(rgb):
        lin = _linear(rgb)
        if seen_by:
            lin = [min(1.0, max(0.0, sum(k * c for k, c in zip(row, lin, strict=True)))) for row in SEEN_BY[seen_by]]
        return _oklab(lin)
    return 100 * math.dist(lab(a), lab(b))


def test_the_colour_maths_matches_known_values():
    assert round(contrast([255, 255, 255], [0, 0, 0]), 2) == 21.0
    assert round(contrast([118, 118, 118], [255, 255, 255]), 2) == 4.54  # the grey that just passes AA on white
    # red and green of one lightness: far apart for most, close for a deuteranope
    assert apart([200, 60, 50], [90, 140, 40]) > 25 and apart([200, 60, 50], [90, 140, 40], "deutan") < 12


COLOURS = """(classes) => {
  const c = document.createElement("canvas"); c.width = c.height = 1;
  const g = c.getContext("2d", { willReadFrequently: true });
  const rgb = (color) => {
    g.fillStyle = color; g.fillRect(0, 0, 1, 1);
    return [...g.getImageData(0, 0, 1, 1).data].slice(0, 3);
  };
  const probe = document.body.appendChild(document.createElement("span"));
  const of = (name) => { probe.style.color = `var(--${name})`; return rgb(getComputedStyle(probe).color); };
  const all = (names) => Object.fromEntries(names.map((n) => [n, of(n)]));
  const made = (cls, hex) => {
    probe.className = cls; probe.style.cssText = `--c:${hex}`;
    return getComputedStyle(probe);
  };
  const out = { card: all(["bg", "panel"]),
                text: all(["ink", "muted", "gold-ink", "dead-ink", "decided-ink", "good-ink"]),
                mark: all(["gold", "dead", "decided", "green", "blue", "p2"]),
                hue: all(["cat-1", "cat-2", "cat-3", "cat-4"]), name: {}, bar: {} };
  for (const [name, hex] of Object.entries(classes)) {
    out.name[name] = rgb(made("cls", hex).color);
    const bar = made("cb", hex);
    out.bar[name] = [rgb(bar.backgroundColor), rgb(bar.stroke)];
  }
  const line = document.querySelector('svg line[stroke="var(--decided)"]');
  const says = [...document.querySelectorAll("svg text.to")].some((t) => t.textContent.startsWith("decided "));
  out.decided = { dashed: !!line && !!line.getAttribute("stroke-dasharray"), named: says };
  return out;
}"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("tokens", ["none", "claude"])
async def test_every_colour_reads_on_the_card_and_stays_apart_for_the_colour_blind(recorded, theme, tokens):
    """On our own card and on a host's (the mock host's Claude-like tokens), its panel too: text at 4.5:1 (a class's
    name in its ink, the states' inks, the host's own text), marks at 3:1; and what colour tells apart stays 8 apart
    for a red-green colour-blind reader, the moment a wipe was decided dashed and named besides."""
    classes = {name: "#" + rgb for name, rgb in CLASS_COLORS.items()} | {"a new class": "#" + WHITE}
    async with drawn("pull_summary", PULL_CASE.args, theme=theme, tokens=tokens) as (view, errors):
        seen = await view.evaluate(COLOURS, classes)
    assert not errors
    for where, card in seen["card"].items():
        for name, rgb in (seen["text"] | seen["name"]).items():
            assert contrast(rgb, card) >= 4.5, (name, where, round(contrast(rgb, card), 2))
        for name, rgb in (seen["mark"] | seen["hue"]).items():
            assert contrast(rgb, card) >= 3, (name, where, round(contrast(rgb, card), 2))
    # a bar keeps the class's own colour, its edge in the ink: a priest's white bar ends where its outline does
    for name, (fill, edge) in seen["bar"].items():
        assert fill == [int(classes[name][i:i + 2], 16) for i in (1, 3, 5)] and edge == seen["name"][name], name
    states = {name: seen["mark"][name] for name in ("gold", "decided", "dead", "green")}
    for group in (states, seen["hue"]):
        for (a, x), (b, y) in itertools.combinations(group.items(), 2):
            for eyes in SEEN_BY:
                assert apart(x, y, eyes) >= 8, (a, b, eyes, round(apart(x, y, eyes), 1))
    for (a, x), (b, y) in itertools.combinations(seen["hue"].items(), 2):
        assert apart(x, y) >= 15, (a, b, round(apart(x, y), 1))
    assert seen["decided"] == {"dashed": True, "named": True}


def test_the_views_colours_are_the_pages_own():
    """No view carries a palette of its own: every colour is one of page.css's, so it follows the theme and the host
    and is measured above. The replay's stage is dark in either theme, with its own."""
    for name in views.SCRIPTS:
        script = resources.files("wipebot.core").joinpath("app", name).read_text(encoding="utf-8")
        assert name == "replay.js" or not re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\brgba?\(", script), name


# every text of a drawn view with what is behind it: the card or panel it stands on with any tint of its own, or for a
# chart's text its own halo, else the marks drawn under its middle; whatever is drawn faint counts as faint
TEXTS = """() => {
  const c = document.createElement("canvas"); c.width = c.height = 1;
  const g = c.getContext("2d", { willReadFrequently: true });
  const rgba = (color) => {
    g.clearRect(0, 0, 1, 1); g.fillStyle = color; g.fillRect(0, 0, 1, 1);
    const d = g.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, under) => under.map((v, i) => top[i] * top[3] + v * (1 - top[3]));
  const shows = (el) => {
    let a = 1;
    for (let e = el; e; e = e.parentElement) a *= +getComputedStyle(e).opacity;
    return a;
  };
  const ground = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const b = rgba(getComputedStyle(e).backgroundColor);
      if (b[3] > 0) layers.push(b);
      if (b[3] === 1) break;
    }
    return layers.reverse().reduce((under, b) => over(b, under), [255, 255, 255]);
  };
  const out = [], seen = new Set();
  const walker = document.createTreeWalker(document.getElementById("app"), NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement, said = n.textContent.trim();
    if (!said || seen.has(el) || el.closest(":disabled")) continue;
    seen.add(el);
    const cs = getComputedStyle(el), box = el.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility === "hidden" || !box.width || !box.height) continue;
    let on = ground(el instanceof SVGElement ? el.ownerSVGElement.parentElement : el), ink = rgba(cs.color);
    if (el instanceof SVGElement) {
      const svg = el.ownerSVGElement;
      ink = rgba(cs.fill);
      if (cs.paintOrder.startsWith("stroke") && cs.stroke !== "none") on = over(rgba(cs.stroke), on);
      else {
        const middle = new DOMPoint(box.x + box.width / 2, box.y + box.height / 2);
        const at = middle.matrixTransform(svg.getScreenCTM().inverse());
        for (const m of svg.querySelectorAll("rect, circle, path, ellipse, polygon")) {
          if (!(m.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;  // drawn over it
          const ms = getComputedStyle(m), fill = rgba(ms.fill);
          if (ms.fill === "none" || !fill[3] || !m.isPointInFill(at)) continue;
          fill[3] *= shows(m) * ms.fillOpacity;
          on = over(fill, on);
        }
      }
    }
    ink[3] *= shows(el);
    out.push({ said, cls: [...el.classList].join(" ") || el.tagName.toLowerCase(),
               ink: over(ink, on).map(Math.round), on: on.map(Math.round) });
  }
  return out;
}"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("tokens", ["none", "claude"])
async def test_every_text_a_view_draws_reads_against_what_is_behind_it(recorded, monkeypatch, theme, tokens):
    """The colours above are measured one at a time; this measures every text where it is drawn, in the five cards
    at their fullest: 4.5:1 against the card or the panel it stands on with any tint of its own, or the mark it is
    written in. A text drawn faint is measured faint: a death after the wipe was decided was a row at 55%, its name
    2.2:1 on a light panel, and a pull's chip a number at 70% or on a tint its colour did not read on."""
    from wipebot.core import viewhost
    night = made_up_night()  # a chip of each kind: would have lived, nothing ready, can't tell, killed outright
    night["killers"][0]["pulls"] += [{"pull": 5, "ms": 150000, "place": 9, "saved": None},
                                     {"pull": 6, "ms": 180000, "place": 2, "saved": False, "outright": True}]
    key = {"report": night["report"], "boss": night["boss"], "player": "Mirelle"}
    seen: dict[str, list[dict]] = {}
    async with chromium() as browser:
        async def texts(tool: str, args: dict) -> None:
            setup = host_setup(tool, args, await viewhost.tool_result(tool, args), theme=theme, tokens=tokens,
                               mode="fullscreen", width=viewhost.FULL_WIDTH)
            errors: list[str] = []
            page = await viewhost._open(browser, setup, errors)
            await viewhost._settled(page)
            seen[tool] = await page.frames[-1].evaluate(TEXTS)
            assert not errors
            await page.close()
        for case in (PULL_CASE, DEATH_CASE, NIGHT_OF_PULLS):
            await texts(case.tool, case.args)
        for tool, data in (("player_pull", made_up_player(True)), ("player_night", night)):
            made_up_view(monkeypatch, {"view": data["view"], "pull": 4} | key, data)
            await texts(tool, {"player": "Mirelle"})
    low = [(tool, t["said"], t["cls"], round(contrast(t["ink"], t["on"]), 2))
           for tool, texts in seen.items() for t in texts if contrast(t["ink"], t["on"]) < 4.5]
    # known, and still to put right: the name of a phase the fight went back to, on its gold band in the dark theme
    # (3.5:1 on a host's card; pull.js's phaseBands says how strong the band is)
    again = [t for t in low if t[0] == "pull_summary" and t[1].startswith("P1 (2nd time)")]
    assert [t for t in low if t not in again] == [] and (theme == "dark" or not again)
    # and it looked at what it says: a chip of each kind, and how many died at once written in three marks
    assert {t["cls"] for t in seen["player_night"] if t["cls"].startswith("chip")} == {
        "chip", "chip fix", "chip unknown", "chip outright", "chip fix sample"}
    assert sum(t["cls"] == "tb" for t in seen["pull_summary"]) == 3


async def test_the_pull_view_draws_in_a_host_without_errors(recorded, tmp_path):
    data = await pull_data()
    taken = await shots(tmp_path, "pull_summary", PULL_CASE.args, themes=("dark", "light"), widths=(320, 760),
                        modes=("inline", "fullscreen"))
    assert len(taken) == 6
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 100 and s.path.stat().st_size > 10_000
        assert "Scopidruid 0:48" in s.text and f"{data['boss_pct']}%" in s.text
        assert data["headline"]["text"] in s.text  # how it ended, over the numbers
    inline, full = taken[0], taken[-1]
    assert "Open pull" in inline.text and "deaths (19)" not in inline.text.lower()
    assert "deaths (19)" in full.text.lower() and "Open pull" not in full.text


CHART = """() => {
  const texts = [...document.querySelectorAll("#pv-chart svg text")].map((t) => {
    const r = t.getBoundingClientRect();
    return { said: t.textContent, cls: t.getAttribute("class"), y: +t.getAttribute("y"),
             box: [r.left, r.right, r.top, r.bottom] };
  });
  const over = [];  // two texts on one another: more than a pixel across and three down (a line's own leading)
  texts.forEach((a, i) => texts.slice(i + 1).forEach((b) => {
    const across = a.box[0] < b.box[1] - 1 && b.box[0] < a.box[1] - 1;
    if (across && a.box[2] < b.box[3] - 3 && b.box[2] < a.box[3] - 3) over.push([a.said, b.said]);
  }));
  const frame = document.querySelector("#pv-chart svg").getBoundingClientRect();
  const cut = texts.filter((t) => t.box[0] < frame.left - 0.5 || t.box[1] > frame.right + 0.5).map((t) => t.said);
  const count = (sel) => document.querySelectorAll(`#pv-chart svg ${sel}`).length;
  return { texts, over, cut, raid: count('circle[fill="var(--blue)"]'), healers: count('path[stroke="var(--green)"]') };
}"""


@pytest.mark.parametrize("case", ["pull-summary", "vashnik-summary", "altar-summary"])
@pytest.mark.parametrize("width", [320, 760])
async def test_nothing_written_in_the_pull_chart_runs_into_anything_else(recorded, case, width):
    """At a phone's width too: the moment the wipe was decided, a damage peak's height and the two axes' labels each
    keep their own place (they once ran across each other and the health line on a narrow card), and none is cut at
    the chart's edge (a damage scale of "5.0M/s" once lost its first figure there)."""
    async with drawn("pull_summary", CASES[case].args, width=width) as (view, errors):
        chart = await view.evaluate(CHART)
    assert not errors and chart["over"] == [] and chart["cut"] == []


async def test_the_pull_chart_names_both_axes_and_draws_the_cooldowns_its_tool_promises(recorded):
    """Boss health runs 0 to 100% on every pull, named at both ends in the line's colour (it once ran from an
    unnamed floor: 50% on this pull); the inline card has the raid's and the healers' cooldowns, which the remote
    server's description of pull_summary says the user sees; on a narrow card the plot says when the wipe was
    decided, and the headline over it why."""
    data = await pull_data()
    raid = sum(c["kind"] == "raid" for c in data["cooldowns"]) + len(data["lusts"])
    healers = sum(c["kind"] == "healing" for c in data["cooldowns"])
    assert raid and healers
    for width, why in ((760, True), (320, False)):
        async with drawn("pull_summary", PULL_CASE.args, width=width) as (view, errors):
            chart = await view.evaluate(CHART)
        said = {t["said"]: t for t in chart["texts"]}
        assert not errors and said["100%"]["cls"] == said["0%"]["cls"] == "tg" and said["0"]["cls"] == "tm"
        assert said["0%"]["y"] == said["0"]["y"]
        assert said["100%"]["y"] < said[f"{data['boss_pct']}%"]["y"] < said["0%"]["y"]
        assert (chart["raid"], chart["healers"]) == (raid + 1, healers + 1)  # each mark, and its key in the legend
        assert "raid cooldown" in said and "healer cooldown" in said
        assert f"decided {clock(data['decided']['ms'])}" in said and (data["decided"]["why"] in said) == why


TALL = "Math.ceil(document.body.getBoundingClientRect().height)"


@pytest.mark.parametrize("width", [380, 760])
async def test_the_placeholder_has_the_shape_of_the_view_that_is_loading(recorded, monkeypatch, width):
    """While a view's data is read its placeholder is about as tall as the view that replaces it, so the answer
    around it doesn't jump (every view once had the replay's 3:2 stage: 214 px too tall for a pull's card at 760),
    and has no spinner. Every card has one shape, so a night's placeholder is as near as a pull's (its lists once
    made its height the night's own)."""
    from wipebot.core import viewhost
    answer, read = viewhost.answer, asyncio.Event()

    async def held(name: str, args: dict) -> dict:
        if name != "viewer_log":
            await read.wait()
        return await answer(name, args)
    monkeypatch.setattr(viewhost, "answer", held)
    async with chromium() as browser:
        for case, shape, within in (("pull-summary", "pull", 40), ("death-recap", "death", 40),
                                    ("pull-map", "replay", 40), ("progress", "night", 40)):
            read.clear()
            errors: list[str] = []
            tool, args = CASES[case].tool, CASES[case].args
            setup = host_setup(tool, args, await viewhost.tool_result(tool, args), width=width)
            page = await viewhost._open(browser, setup, errors)
            view = page.frames[-1]
            await view.locator(f'#status[data-shape="{shape}"]').wait_for()
            assert await view.locator("#status .spinner").count() == 0
            assert await view.locator("#status").inner_text() == "Loading…"
            waiting = await view.evaluate(TALL)
            read.set()
            await viewhost._settled(page)
            assert abs(await view.evaluate(TALL) - waiting) <= within, (case, waiting)
            assert not errors
            await page.close()


async def test_the_placeholder_is_the_running_tools_before_there_is_a_result(recorded):
    """While the tool still runs the host has named it: the placeholder is already that tool's view's."""
    from wipebot.core import viewhost
    case = CASES["pull-map"]
    async with chromium() as browser:
        errors: list[str] = []
        page = await viewhost._open(browser, host_setup(case.tool, case.args, None, running=True), errors)
        await page.frames[-1].locator('#status[data-shape="replay"]').wait_for()
        assert not errors


async def test_a_view_that_cannot_be_read_says_so_in_one_line_and_reads_again_when_asked(recorded, monkeypatch):
    """Warcraft Logs not answering in time is the usual cause, and it passes: the card says what went wrong in one
    line, with none of the placeholder's blocks left around it (they looked like dead controls), and "Try again"
    reads the view's data again."""
    view_data, calls = server.view_data, []

    async def at_the_second_try(*args, **kw):
        calls.append(args)
        if len(calls) == 1:
            raise FactError("Warcraft Logs didn't answer in time.")
        return await view_data(*args, **kw)
    monkeypatch.setattr(server, "view_data", at_the_second_try)
    async with drawn("pull_summary", PULL_CASE.args) as (view, errors):
        failed = view.locator("#status.failed")
        assert await failed.inner_text() == "Couldn't load this view: Warcraft Logs didn't answer in time.\nTry again"
        assert await failed.locator("[class^=sk-]").count() == 0 and await view.evaluate(TALL) <= 64
        assert await view.locator("button").count() == 1
        await view.get_by_role("button", name="Try again").click()
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert len(calls) == 2 and await view.locator("#status").count() == 0


async def test_a_tool_that_failed_has_nothing_to_read_again(recorded):
    from wipebot.core import viewhost
    result = {"isError": True, "content": [{"type": "text", "text": "No such pull."}]}
    async with chromium() as browser:
        page = await viewhost._open(browser, host_setup("pull_summary", {}, result), [])
        failed = page.frames[-1].locator("#status.failed")
        assert await failed.inner_text() == "The tool failed: nothing to show."
        assert await page.frames[-1].locator("button").count() == 0


AT = """() => {  // where the keyboard is, and what the tooltip says
  const a = document.activeElement, tip = document.getElementById("tooltip");
  return { tag: a.tagName.toLowerCase(), role: a.getAttribute("role"), label: a.getAttribute("aria-label"),
           pull: a.dataset.pullCol || a.dataset.pull || null,
           tip: getComputedStyle(tip).display === "none" ? null : tip.textContent };
}"""


async def test_the_keyboard_steps_through_a_charts_marks_and_opens_what_a_click_opens(recorded):
    """A chart is one stop of the Tab key: the arrow keys step through its marks, each named by what its tooltip
    says and shown with it, and Enter opens the pull a click opens (its marks were once the mouse's alone)."""
    async with drawn("progress", NIGHT_OF_PULLS.args) as (view, errors):
        keys = view.page.keyboard
        assert await view.locator("svg [data-tip]").count() == 33
        assert await view.locator('svg [tabindex="0"]').count() == 1
        assert await view.locator('svg [data-tip]:not([aria-label], [role="button"])').count() == 0
        await keys.press("Tab")
        at = await view.evaluate(AT)
        assert (at["tag"], at["role"], at["pull"]) == ("rect", "button", "1")
        assert at["label"].startswith("Pull 1: ") and at["tip"] == at["label"] + "\nEnter for this pull"
        for key, pull in (("ArrowRight", "2"), ("End", "33"), ("ArrowRight", "33"), ("ArrowLeft", "32"), ("Home", "1")):
            await keys.press(key)
            at = await view.evaluate(AT)
            assert at["pull"] == pull and at["tip"].startswith(f"Pull {pull}: "), key
        assert await view.locator('svg [tabindex="0"]').count() == 1  # still one stop: the next Tab leaves the chart
        for _ in range(12):
            await keys.press("ArrowRight")
        await keys.press("Enter")
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert not errors


LONG_NIGHT = {"report": CASES["first-hits"].args["report"], "boss": "Sszorak"}  # 42 pulls on the boss
NUMBERS = """() => [...document.querySelectorAll('#app svg text.tm[text-anchor="middle"]')].map((t) => {
  const r = t.getBoundingClientRect();
  return { said: t.textContent, left: r.left, right: r.right };
})"""


async def test_the_pull_numbers_under_a_long_nights_bars_keep_apart(recorded):
    """A night's bars are numbered as many as fit, the last pull's always: the number before it gives way where the
    two would touch (on a phone "41" and "42" once ran into one another under a night of 42 pulls)."""
    async with drawn("progress", LONG_NIGHT, width=360) as (view, errors):
        numbers = await view.evaluate(NUMBERS)
    assert not errors and numbers[0]["said"] == "1" and numbers[-1]["said"] == "42" and len(numbers) > 8
    assert all(a["right"] + 3 <= b["left"] for a, b in itertools.pairwise(numbers)), [n["said"] for n in numbers]


async def test_the_keyboard_reaches_a_tables_rows_and_a_marks_tooltip_where_nothing_opens(recorded):
    """The table of pulls is one stop too, its rows stepped through with the arrows and opened with Enter; a mark
    that opens nothing (a boss's cast in the pull's chart) is reached the same way and read out as a picture."""
    from wipebot.core import viewhost
    async with drawn("progress", NIGHT_OF_PULLS.args, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        keys = view.page.keyboard
        assert await view.locator('tbody tr[tabindex="0"]').count() == 1
        for _ in range(12):  # past the bosses' chips and the chart
            await keys.press("Tab")
            if (await view.evaluate(AT))["tag"] == "tr":
                break
        assert (await view.evaluate(AT))["pull"] == "1"
        for _ in range(12):
            await keys.press("ArrowDown")
        assert (await view.evaluate(AT))["pull"] == "13"
        await keys.press("Enter")
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert not errors
    async with drawn("pull_summary", PULL_CASE.args) as (view, errors):
        await view.page.keyboard.press("Tab")
        at = await view.evaluate(AT)
        assert at["role"] == "img" and at["label"] and at["tip"] == at["label"]  # no "Enter for": it opens nothing
        assert await view.locator('svg [tabindex="0"]').count() == 1 and not errors


async def test_on_a_touch_screen_a_bar_opens_on_its_second_tap_and_a_button_is_a_fingers_size(recorded):
    """A night's bars are thinner than a finger: the first tap says which pull was hit and how to open it, and the
    tooltip stays once the finger is lifted (it once went with it); the second tap opens the pull. Buttons are
    44 px high there."""
    async with drawn("progress", NIGHT_OF_PULLS.args, touch=True, width=360) as (view, errors):
        bar, tip = view.locator('[data-pull-col="13"]'), view.locator("#tooltip")
        assert (await bar.bounding_box())["width"] < 24
        await bar.tap()
        said = await tip.inner_text()
        assert await tip.is_visible() and said.startswith("Pull 13: ") and said.endswith("\nTap again for this pull")
        assert await view.locator("h1").inner_text() == "Sszorak" and await view.locator("[data-armed]").count() == 1
        high = await view.locator("button").evaluate_all("els => els.map((b) => b.getBoundingClientRect().height)")
        assert high and min(high) >= 44
        await bar.tap()
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert not errors


async def test_the_replays_timeline_tells_a_screen_reader_where_it_is(recorded):
    case = CASES["pull-map"]
    async with drawn(case.tool, case.args) as (view, errors):
        at = await view.locator("#rp-timeline").evaluate(
            "(e) => ['role', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext']"
            ".map((a) => e.getAttribute(a))")
        assert at[:2] == ["slider", "0"] and 0 <= int(at[3]) <= int(at[2]) and int(at[2]) > 60
        assert at[4] == f"{clock(int(at[3]) * 1000)} of {clock(int(at[2]) * 1000)}"
        await view.locator("#rp-timeline").press("ArrowRight")
        assert int(await view.locator("#rp-timeline").get_attribute("aria-valuenow")) == int(at[3]) + 1
        assert not errors


async def test_the_replay_opens_in_fullscreen_from_its_own_button_and_keeps_its_controls_in_sight(recorded):
    """pull_map's replay had no way to fullscreen; and there its 3:2 stage at the frame's full width put play and
    the timeline under the fold (a page of 808 px in a frame of 760). The stage now takes the height that is left."""
    from wipebot.core import viewhost
    case = CASES["pull-map"]
    async with drawn(case.tool, case.args) as (view, errors):
        full = view.get_by_role("button", name="Open the replay in fullscreen")
        assert await full.is_visible()
        await full.click()
        await view.locator(":root[data-mode=fullscreen]").wait_for()
        await full.wait_for(state="hidden")
        wide, high = await view.locator("#rp-stage").evaluate(
            "(e) => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }")
        assert abs(wide / high - 1.5) < 0.01 and high > 500
        assert await view.locator(".controls").evaluate("(e) => e.getBoundingClientRect().bottom") <= 760
        assert await view.evaluate(TALL) <= viewhost.FULL_HEIGHT == 760 and not errors


async def test_what_the_host_says_its_own_controls_cover_is_kept_clear(recorded):
    """A host's safe-area insets (a phone's edges, a composer over the foot of the page) are padding around every
    view, and the replay's controls in fullscreen end above the bottom one."""
    from wipebot.core import viewhost
    case, cover = CASES["pull-map"], {"top": 12, "right": 0, "bottom": 60, "left": 0}
    async with drawn(case.tool, case.args, mode="fullscreen", width=viewhost.FULL_WIDTH, safeArea=cover) as (view, _):
        pad = await view.locator("#app").evaluate("(e) => [getComputedStyle(e).paddingTop, "
                                                  "getComputedStyle(e).paddingBottom]")
        assert pad == ["30px", "80px"]
        foot = await view.locator(".controls").evaluate("(e) => e.getBoundingClientRect().bottom")
        assert foot <= viewhost.FULL_HEIGHT - cover["bottom"]
    async with drawn("pull_summary", PULL_CASE.args, safeArea=cover) as (view, _):
        pad = await view.locator("#app").evaluate("(e) => getComputedStyle(e).padding")
        assert pad == "22px 0px 64px"


async def test_the_page_speaks_the_protocol_in_order(recorded, tmp_path):
    (s,) = await shots(tmp_path, "pull_summary", PULL_CASE.args, themes=("dark",), widths=(430,))
    said = [(m["dir"], m["msg"].get("method") or "reply") for m in s.messages]
    assert said[0] == ("←", "ui/initialize")  # the page speaks first, and asks before anything else
    order = [said.index(x) for x in [("←", "ui/notifications/initialized"), ("→", "ui/notifications/tool-input"),
                                     ("→", "ui/notifications/tool-result"), ("←", "tools/call")]]
    assert order == sorted(order)
    assert ("←", "ui/notifications/size-changed") in said


async def test_the_replay_opens_from_the_pull_view(recorded, tmp_path):
    _, replay = await shots(tmp_path, "pull_summary", PULL_CASE.args, themes=("dark",), widths=(430,),
                            clicks=("Replay",))
    assert not replay.errors and "← Sszorak pull 13" in replay.text and "0:45 / 2:42" in replay.text
    calls = [m["msg"]["params"]["name"] for m in replay.messages if m["msg"].get("method") == "tools/call"]
    assert calls[:2] == ["view_data", "pull_replay"]


async def test_the_painter_draws_the_pull_view_as_a_picture_for_the_bot(recorded, monkeypatch):
    pytest.importorskip("playwright")
    from PIL import Image
    from playwright.async_api import Error

    from wipebot.core import viewhost
    monkeypatch.setattr(server.DEFAULT, "remote", None)  # as the bot runs the tools: in-process
    painter = viewhost.Painter()
    try:
        [drawn] = await painter.pictures("pull_summary", PULL_CASE.args)
        assert await painter.pictures("pull_map", PULL_CASE.args) == []  # its picture is its own map
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium (just browser)")
        raise
    finally:
        await painter.close()
    assert drawn is not None
    img = Image.open(io.BytesIO(drawn))
    assert img.format == "WEBP" and img.width == viewhost.PICTURE_WIDTH * viewhost.PICTURE_SCALE
    assert 300 < img.height < 1000 and len(drawn) < 200_000  # the card alone, without its buttons


async def test_the_painter_gives_no_picture_of_a_view_that_could_not_load(recorded, monkeypatch):
    """A view whose data can't be read shows why in place of its card: that is no picture for an answer."""
    pytest.importorskip("playwright")
    from playwright.async_api import Error

    from wipebot.core import viewhost
    from wipebot.core.knowledge import KnowledgeError
    monkeypatch.setattr(server.DEFAULT, "remote", None)

    async def unread(**_: object) -> CallToolResult:
        raise KnowledgeError("the report can't be read right now")
    monkeypatch.setattr(server, "view_data", unread)
    painter = viewhost.Painter()
    try:
        with pytest.raises(RuntimeError, match="not drawn: Couldn't load this view: the report can't be read"):
            await painter.pictures("pull_summary", PULL_CASE.args)
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium (just browser)")
        raise
    finally:
        await painter.close()


async def test_the_painter_names_each_panel_so_an_answer_can_post_the_one_its_about(recorded):
    pytest.importorskip("playwright")
    from playwright.async_api import Error

    from wipebot.core import viewhost
    painter = viewhost.Painter()
    try:
        drawn = await painter.pictures("raid_note", CASES["raid-note-night"].args)
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium (just browser)")
        raise
    finally:
        await painter.close()
    assert [getattr(d, "name", None) for d in drawn] == ["cooldowns", "callouts"]


# ---- the player view: real data reads rankings and other guilds' parses (local only); the page draws made-up data ----

PLAYER_CASE, TOP_CASE = CASES["player-pull"], CASES["compare-with-top"]  # Draiman on pull 13, and against the top
NIGHT_CASE = CASES["player-night"]  # Enfo's night on Sszorak


async def player_data(top: bool = False) -> dict:
    a = PLAYER_CASE.args
    result = await server.view_data("player", a["pull"], None, a["report"], a["player"], top)
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def local(recorded):
    if not LOCAL_RECORDED.exists():
        pytest.skip(NOT_LOCAL)


async def night_data() -> dict:
    a = NIGHT_CASE.args
    result = await server.view_data("player_night", None, a.get("boss"), a["report"], a["player"])
    assert result.structured_content is not None
    return result.structured_content


async def test_the_player_night_view_matches_its_snapshot(local):
    assert_snapshot(LOCAL / "views" / "player-night.txt", readable(await night_data()))


async def test_the_player_night_view_states_what_player_night_states(local):
    data = await night_data()
    text, failed = await server.call("player_night", NIGHT_CASE.args)
    assert not failed
    n = sum(not r["reset"] for r in data["pulls"])  # a reset is no pull of the night's, in the text and the view
    assert f"Died before the wipe was decided on {data['died']} of {n} pulls" in text
    assert data["headline"]["text"].startswith(f"Died before the wipe was decided on {data['died']} of {n} pulls; ")
    assert data["headline"]["text"].endswith(f" alive at {data['lived']['saved']} of those {data['died']} deaths.")
    assert f"dead for {data['dead_s']} s of the {data['counted_s']} s" in text and f"({data['dead_pct']}%" in text
    best = f"pull {data['best']['pull']} ({kit_short(data['best']['per_s_alive'])}/s"
    assert f"Best pull by {data['what']} per second alive: {best}" in text
    lived = data["lived"]
    assert f"would have kept them alive at {lived['saved']} of the {lived['of']} deaths that count" in text
    assert f"can't tell at {lived['unknown']} " in text if lived["unknown"] else "can't tell" not in text
    if data["thirds"]:
        died = " / ".join(f"{t['died']} of {len(t['pulls'])}" for t in data["thirds"])
        assert f"deaths that count (as above) on {died};" in text
    for c in data["cooldowns"]:
        if c["possible"] is not None and c["used"]:
            # The text counts a cooldown held for a moment of the fight on the pulls that got to it; the view
            # still shows every cooldown's uses against possible uses.
            assert f"{c['name']} {c['used']}/{c['possible']}" in text or f"{c['name']} used on " in text


async def test_the_player_night_view_never_says_they_died_to_no_killing_blow(local):
    """Enfo's night has deaths the log names no killing blow for: the data marks them, and no tooltip of the page
    reads "died ... to no killing blow recorded"."""
    data = await night_data()
    deaths = [r["death"] for r in data["pulls"] if r["death"]]
    assert any(d["no_blow"] for d in deaths) and not all(d["no_blow"] for d in deaths)
    assert all(d["no_blow"] == (d["by"] == views.NO_KILLING_BLOW) for d in deaths + data["killers"])
    async with drawn("player_night", NIGHT_CASE.args) as (view, errors):
        tips = await view.evaluate('() => [...document.querySelectorAll("[data-tip]")].map((e) => e.dataset.tip)')
    assert not errors and any(", no killing blow recorded" in t for t in tips)
    assert not [t for t in tips if "to no killing blow" in t]


async def test_the_player_view_matches_its_snapshot(local):
    assert_snapshot(LOCAL / "views" / "player.txt", readable(await player_data(top=True)))


async def test_the_player_view_states_what_player_pull_and_compare_with_top_state(local):
    data = await player_data(top=True)
    text, failed = await server.call("player_pull", PLAYER_CASE.args)
    assert not failed
    out, lost = data["output"], data["lost"]
    per_s = f"{kit_short(out['per_s_alive'])}/s over the {data['alive_s']} s alive)"
    assert f"{per_s}, rank {out['rank']} of {out['of']} {out['role']}" in text
    assert f"dead (no output at all) {lost['dead_s']:.0f} s" in text and f"casting gaps (no cast begun) " \
                                                                       f"{lost['gaps_s']:.0f} s" in text
    assert f"Died: {clock(data['died'][0]['ms'])} to {data['died'][0]['by']}" in text
    # alive 53 s of the pull: an opener is placed among no kills, on the card as in the text
    assert data["standing"] is None and "against kills: not compared" in text
    assert data["headline"] == {"text": data["notes"]["fix"][0] + ".", "kind": "fix"}  # the weightiest thing to fix
    assert not [n for n in data["notes"]["good"] + data["notes"]["fix"] if "percentile" in n or "used" in n]
    for cd in data["cooldowns"]:  # as player_pull lists them: uses / possible at their times
        uses = f"{len(cd['uses'])}/{cd['possible']}" + (" at " + ", ".join(clock(t) for t in cd["uses"])
                                                         if cd["uses"] else "")
        assert f"{cd['name']} [" in text and uses in text
    top, failed = await server.call("compare_with_top", TOP_CASE.args)
    assert not failed
    for r in data["top"]["rates"]:
        if r["median"] is not None:
            assert f"{r['mine']:.1f} / {r['median']:.1f} ({r['low']:.1f}-{r['high']:.1f})" in top
    # one spell, one rate: the card's own list of casts says what the comparison says of every spell in both (a
    # rogue's Mutilate, logged under an id for each hand, was 53.0 a minute in the list and 27.1 in the comparison)
    mine = {r["name"]: r["mine"] for r in data["top"]["rates"]}
    both = {c["name"]: (c["per_min"], mine[c["name"]]) for c in data["casts"] if c["name"] in mine}
    assert len(both) >= 8 and both["Mutilate"] == (27.1, 27.1) and all(a == b for a, b in both.values()), both
    # alive for 53 s: Ambush (3.1 a minute at the top's median) is a spell to have pressed, Sprint and Vanish aren't
    flags = {r["name"]: r["flag"] for r in data["top"]["rates"]}
    assert [n for n, flag in flags.items() if flag == "never cast"] == ["Ambush"]
    assert flags["Sprint"] == flags["Vanish"] == "not due: under one cast in their time alive at the top's median"
    assert all(f"{n}: " in top and f"<- {flag}" in top for n, flag in flags.items() if flag)


async def test_a_cooldowns_moment_on_the_card_is_the_moment_player_pull_says_it_came_back(local, monkeypatch):
    """On the recorded healer's pull, with their casts cut to the first use of each cooldown: player_pull says when
    each came back unused, and the card names that moment for the damage cooldown alone (Divine Toll: Avenging Wrath
    is a raid cooldown, whose use is someone's call). The healer's standing carries both of the text's caveats."""
    from wipebot.core.report import cooldowns
    use = cooldowns.cooldown_use

    async def used_once(*args, **kw):
        rows, untalented = await use(*args, **kw)
        return [(sp, times[:1], possible) for sp, times, possible in rows], untalented
    monkeypatch.setattr(cooldowns, "cooldown_use", used_once)
    monkeypatch.setattr(views, "cooldown_use", used_once)
    a = CASES["player-pull-holy-power"].args
    data = (await server.view_data("player", a["pull"], None, a["report"], a["player"])).structured_content
    assert data is not None
    text, failed = await server.call("player_pull", a)
    assert not failed
    back = {c["name"]: c["ready_ms"] for c in data["cooldowns"]}
    assert set(back) == {"Divine Toll", "Avenging Wrath", "Aura Mastery"}
    assert back["Divine Toll"] and back["Avenging Wrath"] and back["Aura Mastery"] is None  # 1 of 1: none missed
    for name, ready in back.items():  # the text's moment, for every cooldown it lists
        line = re.search(rf"^  {name} \[\d+\] \(.*\): 1/\d at [\d:]+(.*)$", text, re.MULTILINE)
        assert line, name
        if ready is None:
            assert line[1] == "", name
        else:
            assert re.fullmatch(rf" \(ready again at {clock(ready)}\b.*, unused\)", line[1]), (name, line[1])
    assert data["notes"]["fix"][0] == f"Divine Toll ready again at {clock(back['Divine Toll'])}, not used"
    assert data["headline"] == {"text": data["notes"]["fix"][0] + ".", "kind": "fix"}
    assert not [n for n in data["notes"]["fix"] if "Avenging Wrath" in n or "Aura Mastery" in n]
    assert data["standing"]["caveat"] == f"{views.SHORT_PULL} {views.CAPPED_HEALING}"
    assert data["standing"]["caveat"] in text


def kit_short(v: float) -> str:
    """An amount as the tools' text writes it (text.short)."""
    from wipebot.core.text import short
    return short(v)


async def test_view_keys_name_the_player_as_logged_and_ask_for_the_comparison(rep, monkeypatch):
    lib = server.Library()

    async def report(code=None):
        return rep
    monkeypatch.setattr(lib, "report", report)
    monkeypatch.setattr(server, "lib", lambda: lib)
    name = next(iter(rep.players.values())).name
    asked = {"report": rep.code, "pull": 13, "player": name.lower()}
    assert await server.view_key("player_pull", asked) == {"view": "player", "report": rep.code, "pull": 13,
                                                           "boss": "Sszorak", "player": name}
    against = await server.view_key("compare_with_top", asked)
    assert against is not None and against["top"] is True
    assert await server.view_key("compare_with_top", {**asked, "aspects": ["build"]}) is None  # no casts: no view
    assert await server.view_key("compare_with_top", {"spec": "holy priest", "boss": "Sszorak"}) is None
    assert await server.view_key("pull_map", asked) is None  # its picture is its own
    assert await server.view_key("player_night", {"report": rep.code, "player": name.lower()}) == {
        "view": "player_night", "report": rep.code, "boss": "Sszorak", "player": name}  # a night: no pull


def made_up_player(top: bool) -> dict:
    """A player view's data, made up (Warcraft Logs asks for synthetic data in public tests): a healer who died after
    the wipe was decided, with a casting gap, two cooldowns and, with `top`, casts against top players."""
    made = {
        "view": "player", "report": "AbCdEfGh12345678", "pull": 4, "boss": "Torvek's Forge",
        "title": "Torvek's Forge pull 4",
        "difficulty": "Mythic", "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678#fight=9", "kill": False,
        "duration_ms": 150000,
        "player": {"name": "Mirelle", "spec": "Restoration", "class": "Shaman", "color": "#0070DD", "role": "healer",
                   "hero": "Totemic", "ilvl": 320},
        "decided": {"ms": 140000, "why": "the boss gained Molten Fury"},
        "output": {"what": "healing", "total": 40_000_000, "per_s_alive": 290_000, "rank": 2, "of": 4,
                   "role": "healers", "top": [{"name": "Chain Heal", "total": 12_000_000}]},
        "standing": {"kind": "kills", "n": 900, "exact": False, "mine": 290_000,
                     "points": [["100th best", 360_000], ["500th best", 320_000], ["900th best", 250_000]],
                     "caveat": f"{views.SHORT_PULL} {views.CAPPED_HEALING}"},
        "lost": {"until_ms": 140000, "dead_s": 0.0, "gaps_s": 4.0, "capped_s": 0.0, "resource": ""},
        "alive_s": 143, "alive_until_ms": 143000, "gaps": [[100000, 104000]], "capped": [], "dead": [[143000, 150000]],
        "died": [{"ms": 143000, "by": "Molten Fury", "fallout": True}],
        "cooldowns": [{"name": "Ascendance", "uses": [60000], "possible": 1, "icon": "spells/114052.jpg"},
                      {"name": "Spirit Link Totem", "uses": [], "possible": 1, "icon": "spells/98008.jpg"}],
        "casts": [{"name": "Chain Heal", "n": 30, "per_min": 12.6}, {"name": "Riptide", "n": 25, "per_min": 10.5}],
        "phases": [{"ms": 0, "id": 1}],
        "tracked": [{"ms": 30000, "name": "Slag Wave"}, {"ms": 90000, "name": "Slag Wave"}],
        "top": {"hero": "Totemic", "same_hero": True, "parses": 5, "total": [40, 46],
                "rates": [{"name": "Chain Heal", "mine": 12.6, "median": 17.0, "low": 15.0, "high": 19.0, "flag": ""},
                          {"name": "Astral Shift", "mine": 0.0, "median": 0.4, "low": 0.0, "high": 0.6,
                           "flag": "never cast"}]} if top else None,
    }
    made["notes"] = views.player_notes(made)
    return made | {"headline": views.player_headline(made)}


def made_up_night() -> dict:
    """A player's night, made up: six pulls, a reset, two deaths that counted and one after the wipe was decided."""
    pulls = []
    for n, (pct, rate, death) in enumerate([(100.0, 150_000, None), (80.5, 280_000, (1, False)),
                                            (70.2, 300_000, None), (65.0, 310_000, (4, False)),
                                            (60.1, 320_000, (9, True)), (55.0, 330_000, None)], start=1):
        pulls.append({"pull": n, "kill": False, "reset": n == 1, "wipe_pct": pct, "duration_ms": 60000 * n,
                      "role": "healer", "spec": "Restoration", "per_s_alive": rate, "rank": 2, "of": 4,
                      "death": {"place": death[0], "ms": 30000 * n, "by": "Slag Wave", "icon": "spells/1.jpg",
                                "fallout": death[1]} if death else None,
                      "dead_s": 5.0 if death and not death[1] else 0.0, "counted_s": 55.0 * n, "cooldowns": [1, 2]})
    return {"view": "player_night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge",
            "title": "Mirelle on Torvek's Forge", "headline": views.player_night_headline(2, 6, 1),
            "player": {"name": "Mirelle", "spec": "Restoration", "class": "Shaman", "color": "#0070DD",
                       "role": "healer"},
            "what": "healing", "role_name": "healers", "pulls": pulls, "total_pulls": 6, "died": 2, "first": 1,
            "early": 1, "early_n": 3, "dead_s": 10, "counted_s": 1155, "dead_pct": 1, "cooldowns_total": [6, 12],
            "best": {"pull": 6, "per_s_alive": 330_000},
            "killers": [{"by": "Slag Wave", "icon": "spells/1.jpg",
                         "pulls": [{"pull": 2, "ms": 60000, "place": 1, "saved": True},
                                   {"pull": 4, "ms": 120000, "place": 4, "saved": False}]}],
            "lived": {"saved": 1, "of": 2, "unknown": 0, "by": [["Astral Shift", 1]]},
            "cooldowns": [{"name": "Ascendance", "used": 4, "possible": 6, "icon": "spells/114052.jpg"}],
            "cooldowns_unavailable": "", "thirds": []}


@pytest.fixture
def made_up(monkeypatch):
    """The player view drawn from made-up data: its key and its data answered without a report, its icons made up."""
    from wipebot.core import knowledge, viewhost

    async def spell_icon(spell_id):
        return made_up_icon()
    monkeypatch.setattr(knowledge, "spell_icon", spell_icon)
    key = {"view": "player", "report": "AbCdEfGh12345678", "pull": 4, "boss": "Torvek's Forge", "player": "Mirelle"}

    async def tool_result(tool, args):
        return {"content": [{"type": "text", "text": "Mirelle's pull"}], "structuredContent": key}

    async def view_data(view, pull=None, boss=None, report=None, player=None, top=False):
        return CallToolResult(content=[], structured_content=made_up_night() if view == "player_night"
                              else made_up_player(top))

    async def view_key(tool, args):
        return {**key, "top": tool == "compare_with_top"}
    monkeypatch.setattr(viewhost, "tool_result", tool_result)
    monkeypatch.setattr(server, "view_data", view_data)
    monkeypatch.setattr(server, "view_key", view_key)


async def test_the_player_view_draws_and_asks_for_the_top_on_a_click(made_up, tmp_path):
    taken = await shots(tmp_path, "player_pull", {"player": "Mirelle"}, themes=("dark", "light"), widths=(320, 760),
                        clicks=("Against top players",))
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 100 and "Mirelle" in s.text and "290k" in s.text and "rank 2 of 4 healers" in s.text
        assert "4 s casting gaps" in s.text and "1 of 2" in s.text and "after the wipe was decided" in s.text
    first, clicked = taken[0], taken[1]
    assert "Against top players" in first.text and "Astral Shift" not in first.text
    calls = [m["msg"]["params"]["arguments"] for m in clicked.messages if m["msg"].get("method") == "tools/call"
             and m["msg"]["params"]["name"] == "view_data"]
    assert [c.get("top") for c in calls] == [False, True]  # the comparison read only when asked for
    assert "Astral Shift" in clicked.text and "0.0 / 0.4" in clicked.text


async def test_the_player_night_view_draws_and_a_pulls_row_opens_that_pull(made_up, tmp_path, monkeypatch):
    from wipebot.core import viewhost

    async def tool_result(tool, args):  # player_night's key: a boss's night, no pull
        return {"content": [], "structuredContent": {"view": "player_night", "report": "AbCdEfGh12345678",
                                                     "boss": "Torvek's Forge", "player": "Mirelle"}}
    monkeypatch.setattr(viewhost, "tool_result", tool_result)
    first, opened = await shots(tmp_path, "player_night", {"player": "Mirelle"}, themes=("dark",), widths=(760,),
                                modes=("fullscreen",), clicks=('css:tr[data-pull="4"]',))
    # 2 of 5: the sixth of the made-up night's pulls is a reset, no pull of the player's night
    assert not first.errors and "2 of 5" in first.text and "first to die once" in first.text
    assert "could have lived" in first.text.lower() and "1 of 2" in first.text
    assert "Astral Shift on 1 death" in first.text
    assert "what killed mirelle" in first.text.lower() and "Slag Wave" in first.text
    assert "6: 330k" in first.text and "pull by pull" in first.text.lower()
    calls = [m["msg"]["params"]["arguments"] for m in opened.messages if m["msg"].get("method") == "tools/call"
             and m["msg"]["params"]["name"] == "view_data"]
    assert calls[-1] == {"view": "player", "report": "AbCdEfGh12345678", "pull": 4, "boss": "Torvek's Forge",
                         "player": "Mirelle", "top": False}
    assert "← Mirelle" in opened.text and "rank 2 of 4 healers" in opened.text  # that pull's card, and Back


def made_up_view(monkeypatch, key: dict, data: dict) -> None:
    """One view drawn from made-up data: its key and its data answered without a report."""
    from wipebot.core import viewhost

    async def tool_result(tool, args):
        return {"content": [], "structuredContent": key}

    async def view_data(view, **asked):
        return CallToolResult(content=[], structured_content=data)
    monkeypatch.setattr(viewhost, "tool_result", tool_result)
    monkeypatch.setattr(server, "view_data", view_data)


async def test_a_pulls_chip_is_a_fingers_target_on_a_touch_screen_and_a_small_one_with_a_mouse(monkeypatch):
    """A chip is 20 px high where a mouse points at it; under a finger it grows and reaches into the gaps around
    it, 44 px each way, without reaching over the chip beside it."""
    night = made_up_night()
    made_up_view(monkeypatch, {"view": "player_night", "report": "AbCdEfGh12345678", "player": "Mirelle"}, night)
    reach = """els => els.map((b) => { const r = b.getBoundingClientRect(), a = getComputedStyle(b, "::after");
      const past = a.content === "none" ? 0 : -parseFloat(a.top);
      return { high: r.height + 2 * past, wide: r.width + 2 * past, past, left: r.left, right: r.right,
               top: r.top }; })"""
    async with drawn("player_night", {}, touch=True, width=360) as (view, errors):
        chips = await view.locator("button.chip").evaluate_all(reach)
        assert not errors and len(chips) >= 2 and all(c["high"] >= 44 and c["wide"] >= 44 for c in chips)
        for a, b in itertools.pairwise(chips):
            if a["top"] == b["top"]:  # side by side: each one's reach ends before the other begins
                assert a["right"] + a["past"] <= b["left"] - b["past"] + 0.01
    async with drawn("player_night", {}, width=360) as (view, errors):
        assert {c["high"] for c in await view.locator("button.chip").evaluate_all(reach)} == {20}


async def test_a_night_of_one_pull_counts_one_pull(monkeypatch):
    """A boss killed on the first pull, made up, as on any farm night: "1 pull", no heading over a list that isn't
    there and no key for a line that isn't drawn."""
    night = {"view": "night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "difficulty": "Heroic",
             "title": "Torvek's Forge, Mon 1 Jan", "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678",
             "headline": {"text": "Killed on the first pull.", "kind": ""},
             "combat_ms": 240000, "guild_best": None, "phased": False, "ends": [["a kill", 1]], "firsts": [],
             "first_n": 3, "thirds": None,
             "bosses": [{"boss": "Torvek's Forge", "pulls": 1, "kill": True, "best": None}],
             "pulls": [{"pull": 1, "pct": 0.0, "kill": True, "reset": False, "live": False, "duration_ms": 240000,
                        "phase": "", "health": None, "ended": "a kill", "best": False, "first": None}]}
    from wipebot.core import viewhost
    made_up_view(monkeypatch, {"view": "night", "report": night["report"], "boss": night["boss"]}, night)
    async with drawn("progress", {}) as (view, errors):
        card = (await view.locator("body").inner_text()).lower()
    async with drawn("progress", {}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, more):
        text = (await view.locator("body").inner_text()).lower()
    assert not errors and not more and "1 pull ·" in text and "the only pull" in text and "how the pull ended" in text
    assert "killed on the first pull." in card and "1 pull ·" in card and "how the pull ended" not in card
    for said in (card, text):
        assert "pulls" not in said and "what killed the first" not in said and "best so far" not in said


async def test_counts_and_ranks_are_written_as_the_tools_write_them(monkeypatch):
    """A parse is "81st percentile", never "81th"; a count of one has no plural: the page's ordinal and plural
    against the tools' own (core/text.py). A rank under output per second alive says it is by the total, as
    player_night's text does: the two stand on different bases."""
    from wipebot.core.text import ordinal
    parse = made_up_player(False) | {"kill": True, "standing": {"kind": "parse", "percentile": 81}}
    made_up_view(monkeypatch, {"view": "player", "report": parse["report"], "pull": 4, "boss": parse["boss"],
                               "player": "Mirelle"}, parse)
    numbers = [0, 1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 81, 93, 100, 101, 111, 112]
    async with drawn("player_pull", {"player": "Mirelle"}) as (view, errors):
        text = await view.locator("body").inner_text()
        said = await view.evaluate("(ns) => ns.map((n) => [kit.ordinal(n), kit.plural(n, 'pull')])", numbers)
    assert not errors and "81st percentile" in text and "81th" not in text
    assert "rank 2 of 4 healers by total" in text
    assert said == [[ordinal(n), plural(n, "pull")] for n in numbers]


async def test_a_missed_save_is_what_to_do_differently_and_a_death_with_no_killing_blow_says_only_that(recorded):
    """The death view's line on what would have kept them alive is its headline, marked as a fix (it once had the
    plus of what went well), the thing first; a death the log names no killing blow for isn't "killed by no killing
    blow", in its headline or its chart."""
    lead = '() => [...document.querySelectorAll(".lead")].map((p) => [p.className, p.textContent])'
    async with drawn("death_recap", DEATH_CASE.args) as (view, errors):
        assert await view.evaluate(lead) == [["lead fix", "−A healthstone, used in time, would have kept them alive."]]
        assert not errors
    async with drawn("death_recap", CASES["death-recap-thrown"].args) as (view, errors):
        labels = await view.evaluate('() => [...document.querySelectorAll("svg text.td")].map((t) => t.textContent)')
        assert labels == ["no killing blow recorded"] and not errors
        assert await view.evaluate(lead) == [["lead ", "Died at 0:53, no killing blow recorded."]]


async def test_a_night_says_a_death_with_no_killing_blow_as_the_log_does(monkeypatch):
    """A death the log names no killing blow for, on a player's night: its pull's bar and its chip say "died 2:00
    (#4), no killing blow recorded", never "died ... to no killing blow recorded"; a named killer is still "to" it."""
    night = made_up_night()
    night["pulls"][3]["death"] |= {"by": "no killing blow recorded", "no_blow": True, "icon": None}
    night["killers"] = [{"by": "Slag Wave", "icon": "spells/1.jpg",
                         "pulls": [{"pull": 2, "ms": 60000, "place": 1, "saved": True}]},
                        {"by": "no killing blow recorded", "no_blow": True, "icon": None,
                         "pulls": [{"pull": 4, "ms": 120000, "place": 4, "saved": None}]}]
    from wipebot.core import viewhost
    made_up_view(monkeypatch, {"view": "player_night", "report": night["report"], "player": "Mirelle"}, night)
    async with drawn("player_night", {}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        tip = lambda at: view.locator(at).get_attribute("data-tip")  # noqa: E731
        chips = [await tip(f'[data-pull-chip="{n}"]') for n in (2, 4)]
        bars = [await tip(f'[data-pull-col="{n}"]') for n in (2, 4)]
    assert not errors
    assert chips[0].startswith("Pull 2: died 1:00 (#1) to Slag Wave; ")
    assert chips[1].startswith("Pull 4: died 2:00 (#4), no killing blow recorded; can't tell")
    assert bars[0].split("\n")[1:3] == ["280k/s alive, rank 2 of 4 healers by total", "died 1:00 (#1) to Slag Wave"]
    assert "died 2:00 (#4), no killing blow recorded" in bars[1].split("\n")


async def test_a_mark_shows_its_tooltip_on_hover_and_icons_draw(made_up):
    pytest.importorskip("playwright")
    from playwright.async_api import Error, async_playwright

    from wipebot.core import viewhost
    result = await viewhost.tool_result("player_pull", {"player": "Mirelle"})
    setup = {"tool": "player_pull", "args": {}, "result": result, "theme": "dark", "width": 760, "mode": "inline",
             "tokens": "none", "bare": True, "fullWidth": viewhost.FULL_WIDTH, "fullHeight": viewhost.FULL_HEIGHT}
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Error as e:
            pytest.skip(f"needs Playwright's Chromium: {e}")
        errors: list[str] = []
        page = await viewhost._open(browser, setup, errors)
        await viewhost._settled(page)
        view = page.frame_locator("iframe")
        # Ascendance at its use, and in the legend by its name
        assert await view.locator("image[href$='spells/114052.jpg']").count() == 2
        assert await view.locator("image").evaluate_all("els => els.every((e) => e.getBBox().width > 0)")
        await view.locator("image[href$='spells/114052.jpg'][data-tip]").hover()
        tip = view.locator("#tooltip")
        assert await tip.is_visible() and await tip.inner_text() == "Ascendance 1:00"
        await view.locator("h1").hover()
        assert not await tip.is_visible()
        assert not errors
        await browser.close()


async def test_the_painter_draws_compare_with_tops_player_view_with_the_biggest_differences(made_up):
    pytest.importorskip("playwright")
    from PIL import Image
    from playwright.async_api import Error

    from wipebot.core import viewhost
    painter = viewhost.Painter()
    try:
        [alone] = await painter.pictures("player_pull", {"player": "Mirelle"})
        [against] = await painter.pictures("compare_with_top", {"player": "Mirelle"})
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium")
        raise
    finally:
        await painter.close()
    assert alone and against
    small, big = Image.open(io.BytesIO(alone)), Image.open(io.BytesIO(against))
    assert small.width == big.width == viewhost.PICTURE_WIDTH * viewhost.PICTURE_SCALE
    assert big.height > small.height + 100  # the comparison's rows under the card


# ---- the replay's JavaScript (Node) ----

def viewer_at(track: str, times_ms: list[float]) -> list:
    """The replay's `at()` for each time, through its own `decode()`."""
    page = resources.files("wipebot.core").joinpath("app/replay.js").read_text(encoding="utf-8")
    code = page[page.index("const UNKNOWN"):page.index("\n", page.index("const UNKNOWN"))]
    code += "\n" + page[page.index("function decode(b64)"):page.index("function image(path)")]
    code += "\n" + page[page.index("const DEAD = -2"):page.index("function isDead")]
    script = (f"{code}\nconst R = {{step_ms: {maps.REPLAY_STEP_MS}}};\nconst tr = decode({json.dumps(track)});\n"
              f"console.log(JSON.stringify({json.dumps(times_ms)}.map((ms) => at(tr, ms))));")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_replay_reads_the_tracks_as_they_were_packed():
    samples = [(0.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), (2000.0, 1000.0, 1000.0), (3000.0, 2000.0, 1000.0)]
    facings = [(0.0, 0.17453), (1000.0, 6.10865), (2000.0, 1.5708), (3000.0, 1.5708)]  # 10°, 350°, 90°, 90°
    dead = [(3500.0, 6000.0)]
    track = maps.pack_track(samples, facings, 0.0, 6, dead, origin=(0.0, 0.0))
    got = viewer_at(track, [0, 1000, 500, 750, 4000, 4500, 5900])
    assert got[0] == [0, 0, 10] and got[1] == [20, 0, 350]  # whole steps: as packed (half yards, degrees)
    assert got[2][2] == 0  # 10° to 350° goes through 0°, not round the long way
    assert 0 <= got[3][2] < 360 and abs(got[3][2] - 355) < 1e-9  # and never negative
    assert got[4] == got[5] == got[6] == [40, 20, maps.DEAD_FACING]  # dead where they fell, not moving


def _facts(**over) -> dict:
    """A player view's facts that player_notes reads: a steady 3:00 until the wipe was decided, nothing wrong."""
    facts = {"lost": {"until_ms": 180000, "dead_s": 0, "gaps_s": 2, "capped_s": 0, "resource": "Energy"},
             "output": {"what": "damage", "rank": 4, "of": 14, "role": "damage dealers"}, "standing": None,
             "died": [], "cooldowns": [{"name": "Deathmark", "uses": [1000, 121000], "possible": 2, "raid": False}]}
    return facts | over


def test_a_players_notes_put_the_weightiest_first_and_judge_no_cooldown_by_its_count():
    """Every cooldown used as often as it could be is no praise, and one used less no fault: a count can't tell a
    cooldown held for a burst window from one forgotten (the card once said "Vanish: used 0 of the 1 times it was
    ready" of a rogue who began the pull in Stealth)."""
    assert views.player_notes(_facts()) == {"good": ["Kept casting: 2 s of gaps in 3:00"], "fix": []}
    worse = _facts(died=[{"ms": 95000, "by": "Toxic Fumes", "fallout": False}],
                   lost={"until_ms": 180000, "dead_s": 85, "gaps_s": 20, "capped_s": 40, "resource": "Energy"},
                   cooldowns=[{"name": "Darkness", "uses": [], "possible": 3, "raid": True},
                              {"name": "Deathmark", "uses": [], "possible": 2, "raid": False, "dps": True,
                               "ready_ms": None}])
    assert views.player_notes(worse) == {"good": [], "fix": ["Died at 1:35 to Toxic Fumes, before the wipe was decided",
                                                             "Energy capped for 40 s (22%)"]}
    after = _facts(died=[{"ms": 185000, "by": "Toxic Fumes", "fallout": True}],  # fallout isn't theirs to fix
                   output={"what": "healing", "rank": 1, "of": 4, "role": "healers"},
                   standing={"kind": "parse", "percentile": 81})
    assert views.player_notes(after)["good"] == ["81st percentile parse", "Most healing of the 4 healers"]
    wiped = _facts(died=[{"ms": 95000, "by": "Visceral Burst", "fallout": False, "with": 4}])  # one hit killing many
    assert views.player_notes(wiped)["fix"] == []


def test_time_at_the_cap_is_a_fix_only_beyond_top_players():
    capped = _facts(lost={"until_ms": 180000, "dead_s": 0, "gaps_s": 2, "capped_s": 36, "resource": "Fury"})
    assert views.player_notes(capped)["fix"] == ["Fury capped for 36 s (20%)"]  # without top players: their share
    assert views.player_notes(capped | {"top": {"capped": 0.18}})["fix"] == []  # the top cap as much: the fight's
    less = capped | {"top": {"capped": 0.05}}
    assert views.player_notes(less)["fix"] == ["Fury capped for 36 s (20%; top players 5%)"]


def test_a_death_in_a_hit_killing_many_is_one_of_that_hit():
    burst = MassDeath(412_000, "Visceral Burst", (1308386,), 4, 2)
    ended = Ending("moments", 20, 422_000, (burst,))
    assert mass_death_of(ended, 413_500, "Visceral Burst") == burst
    assert mass_death_of(ended, 413_500, "Eternal Venom") is None  # another ability, the same moment
    assert mass_death_of(ended, 407_000, "Visceral Burst") is None  # an earlier one, on its own
    assert mass_death_of(None, 413_500, "Visceral Burst") is None  # a kill


async def test_a_view_in_panels_is_painted_a_picture_per_panel(made_up, monkeypatch):
    pytest.importorskip("playwright")
    from playwright.async_api import Error

    from wipebot.core import viewhost

    async def view_key(tool, args):
        return {"view": "player_night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "player": "Mirelle"}
    monkeypatch.setattr(server, "view_key", view_key)
    painter = viewhost.Painter()
    try:
        drawn = await painter.pictures("player_night", {"player": "Mirelle"})
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium")
        raise
    finally:
        await painter.close()
    assert len(drawn) == 2  # what killed them; the night pull by pull: each whole in Discord
    from PIL import Image
    for panel in drawn:  # each as wide as a whole view's picture, with its margin (a panel was once shot bare)
        img = Image.open(io.BytesIO(panel)).convert("RGB")
        assert img.width == viewhost.PICTURE_WIDTH * viewhost.PICTURE_SCALE
        # the margin: the card's colour, nothing drawn in it
        edge = cast(tuple[tuple[int, int], ...], img.crop((0, 0, 24, img.height)).getextrema())
        assert all(most - least <= 3 for least, most in edge), edge


# ---- what a card must not judge: the rule is the tool's, so its text and the card say the same ----

def test_a_spell_never_cast_is_flagged_only_where_the_top_would_have_cast_it_in_the_time_alive():
    """A rogue alive for 53 s of a wipe was told they never cast Sprint, Vanish, Shiv and Thistle Tea, each under one
    cast in 53 s at the top players' median: only Ambush, 2.7 casts in that time, was a spell to have pressed. The
    rule is compare_with_top's own (top.cast_rates), which the card draws from."""
    from wipebot.core.report import top
    median = {"Ambush": 3.1, "Shiv": 1.0, "Thistle Tea": 0.8, "Sprint": 0.7, "Vanish": 0.5}
    found = [top.Reference({}, dict(median), top.Casting([], {}, 0.0)) for _ in range(3)]
    mine: dict[str, float | None] = dict.fromkeys(median, 0.0)

    def flags(**alive) -> dict[str, str]:
        return {r.name: r.flag for r in top.cast_rates(top.SpellNames([]), mine, found, set(), **alive)}
    short_life = flags(minutes=53 / 60)
    assert short_life.pop("Ambush") == "never cast" and set(short_life.values()) == {top.NOT_DUE}
    assert set(flags(minutes=5).values()) == {"never cast"}  # over a whole pull each is a spell to have pressed
    assert set(flags().values()) == {"never cast"}  # the time alive not given: as before
    # the flag says whose rate it is, as "not worn" and "cast before the pull" say why: read alone, "under one cast
    # in their time alive" was also true of the player's own casts, which is what "never cast" says
    assert top.NOT_DUE == "not due: under one cast in their time alive at the top's median"


def test_a_damage_cooldown_never_pressed_is_never_cast_however_short_the_life():
    """A damage cooldown is pressed from the pull, which is how its possible uses are counted (cooldown_use: 0/1 for
    someone alive a minute). Its rate over a whole kill says nothing of a short life: three casts in a kill of 5:30
    are 0.5 a minute, under one cast in 1:50, yet every top player had pressed it by then. Unpressed, it is "never
    cast" in the comparison too; a spell Lorrgs doesn't tag so (Vanish) isn't due in that time."""
    from wipebot.core.report import top
    names = top.SpellNames([{"name": "Deathmark", "spell_id": 360194, "cooldown": 120, "tags": ["dps"]},
                            {"name": "Vanish", "spell_id": 1856, "cooldown": 120, "tags": []},
                            {"name": "Tricks of the Trade", "spell_id": 57934, "cooldown": 30, "tags": ["raid_cd"]}])
    median = {"Deathmark": 0.5, "Vanish": 0.5, "Tricks of the Trade": 0.5}
    found = [top.Reference({}, dict(median), top.Casting([], {}, 0.0)) for _ in range(3)]
    rates = top.cast_rates(names, dict.fromkeys(median, 0.0), found, set(), minutes=110 / 60)
    assert {r.name: r.flag for r in rates} == {"Deathmark": "never cast", "Vanish": top.NOT_DUE,
                                               "Tricks of the Trade": top.NOT_DUE}


def test_a_cooldown_is_noted_as_the_moment_it_came_back_unused_and_only_a_damage_cooldown():
    """What the tool's text says of a cooldown used less than it could be is when it came back ("ready again at
    2:07, unused"): the card says that moment, for a cooldown Lorrgs tags a damage cooldown, and nothing of one never
    used, an untagged spell or a raid cooldown."""
    back = {"name": "Deathmark", "uses": [7000], "possible": 2, "raid": False, "dps": True, "ready_ms": 127000}
    assert views.player_notes(_facts(cooldowns=[back]))["fix"] == ["Deathmark ready again at 2:07, not used"]
    for quiet in (back | {"uses": [], "ready_ms": None}, back | {"dps": False}, back | {"raid": True}):
        assert views.player_notes(_facts(cooldowns=[quiet]))["fix"] == []


def test_a_cooldown_came_back_unused_only_while_alive_and_before_the_wipe_was_decided():
    from wipebot.core.report import cooldowns
    f = {"startTime": 1_000_000, "endTime": 1_180_000}
    deathmark, end = {"name": "Deathmark", "cooldown": 120.0}, 1_180_000
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, end, end) == (127000.0, True)
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, 1_053_000, end) == (127000.0, False)  # died before it
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, end, 1_120_000) is None  # after the wipe was decided
    assert cooldowns.came_back(f, deathmark, [], 1, end, end) is None  # never used: nothing came back
    assert cooldowns.came_back(f, deathmark, [7000.0, 130000.0], 2, end, end) is None  # as often as it could be


def test_where_a_wipe_stands_among_kills_is_no_praise_and_no_fault_of_a_healers():
    """A rogue dead for 100 of 152 s was told "Above the 90th percentile of ranked kills", and a healer on the same
    wipe "Below the 25th": the text says under both that a short pull runs high, and of the healer that healing on a
    wipe is capped by the damage taken. The card now praises no wipe by it and faults no healer; a damage dealer
    below the lowest of the kills still is."""
    points = [["90th percentile", 234_760], ["75th percentile", 232_040], ["50th percentile", 214_900],
              ["25th percentile", 199_770]]
    above = {"kind": "kills", "n": 42, "exact": True, "mine": 247_820, "points": points}
    assert views.player_notes(_facts(standing=above))["good"] == ["Kept casting: 2 s of gaps in 3:00"]
    below = above | {"mine": 150_000}
    assert views.player_notes(_facts(standing=below))["fix"] == ["Below the 25th percentile of ranked kills"]
    healer = _facts(standing=below, output={"what": "healing", "rank": 3, "of": 4, "role": "healers"})
    assert views.player_notes(healer) == {"good": ["Kept casting: 2 s of gaps in 3:00"], "fix": []}


# ---- the card: one shape in every view, led by a sentence code wrote ----

def test_a_pulls_headline_is_how_it_ended_in_one_sentence():
    """pull_summary's "Ended by", led by its first moment, without the ids and what to look up: one sentence for
    each way a pull ends."""
    f = {"startTime": 0, "endTime": 312_000, "kill": True}
    assert views.pull_headline(f, None, 3) == {"text": "Killed in 5:12 with 3 deaths.", "kind": ""}
    assert views.pull_headline(f, None, 1)["text"] == "Killed in 5:12 with 1 death."
    assert views.pull_headline(f, None, 0)["text"] == "Killed in 5:12 without a death."
    said = lambda *ending, **more: views.pull_headline(f, Ending(*ending, **more), 9)["text"]  # noqa: E731
    assert said("live", 20, 130_000) == "Still going: 2:10 logged so far."
    assert said("reset", 20, 24_000) == "A reset: the boss untouched."
    assert said("alive", 20, 190_000, alive_at_end=14) == "Ended at 3:10 with 14 of 20 alive: no death decided it."
    march = EndCast(118_000, "March", (1300001,), 16, 18, "March", 115_000)
    assert said("cast", 20, 120_000, alive_at_end=18, cast=march) == (
        "The boss cast March at 1:55 and it landed on 16 of the 18 alive at 1:58, as the pull ended with 18 of 20 "
        "alive.")
    assert said("cascade", 20, 162_000, first_death_ms=31_000, half_dead_ms=139_000) == (
        "Deaths one after another from 0:31 until half the raid was dead at 2:19, no single hit killing many.")
    burst = MassDeath(55_000, "Mutilate", (1287001,), 4, 1)
    assert said("moments", 20, 88_000, (burst,)) == "Mutilate killed 4 within 1 s at 0:55."
    assert said("moments", 20, 88_000, (burst, HalfDead(70_000)), lead_in=LeadIn(42_000, 3, 1)) == (
        "Mutilate killed 4 within 1 s at 0:55, after 3 deaths one after another from 0:42.")
    # as many deaths as a hit killing many takes lead, as they do in "Ended by"
    assert said("moments", 20, 88_000, (burst, HalfDead(70_000)), lead_in=LeadIn(42_000, 5, 1)) == (
        "5 deaths one after another from 0:42, then Mutilate killed 4 within 1 s at 0:55.")
    pulses = MassDeath(93_000, "Tempest", (1287083,), 4, 5, waves=True)
    assert said("moments", 20, 110_000, (pulses,)) == "Tempest killed 4 in pulses within 5 s from 1:33."
    unnamed = MassDeath(107_000, "", (), 13, 2)
    assert said("moments", 20, 123_000, (unnamed,)) == "13 died at once within 2 s at 1:47, no killing blow logged."
    assert said("moments", 20, 162_000, (KeptBuff(152_000, "Unbound Ferocity", 16),)) == (
        "The boss gained Unbound Ferocity at 2:32 and kept it to the end, with 16 of 20 alive.")
    assert said("moments", 20, 162_000, (HalfDead(100_000),)) == "Half the raid was dead at 1:40."


def test_a_deaths_headline_is_what_it_comes_down_to():
    """What killed outright is no defensive's to stop; a death after the wipe was decided is nobody's to fix, with
    whatever was ready; before it, what was ready and would have kept them alive is what to do differently."""
    head = "If pressed at the start of the lethal burst (2:00):"
    short = "  Barkskin (20% less damage taken for 8 s): 30k less of the 150k it covers: not enough on its own"
    stone = "  a healthstone (if they had one; none used this pull) (heals 25%): 120k: would have kept them alive"
    shift = "  Astral Shift (40% less damage taken for 12 s): 60k less of the 150k: would have kept them alive"
    said = lambda *facts: views.death_headline("Slag Wave", 121_000, *facts)  # noqa: E731
    assert said(False, 64, [head, short, stone], False) == {
        "text": "A healthstone, used in time, would have kept them alive.", "kind": "fix"}
    assert said(False, 64, [head, shift, stone], False)["text"] == (
        "Astral Shift or a healthstone, used in time, would have kept them alive.")
    assert said(False, 64, [head, shift, stone], True) == {
        "text": "Killed by Slag Wave at 2:01, after the wipe was decided.", "kind": ""}
    assert said(True, 97, [], False) == {
        "text": "Slag Wave killed them outright from 97% health: it wasn't damage that outpaced their healing.",
        "kind": "mind"}
    assert said(False, 64, [head, short], False)["text"] == (
        "Killed by Slag Wave at 2:01: nothing ready would have been enough on its own.")
    assert said(False, 64, [], False) == {"text": "Killed by Slag Wave at 2:01.", "kind": ""}  # can't tell: no more
    assert views.death_headline(views.NO_KILLING_BLOW, 53_000, False, None, [], False)["text"] == (
        "Died at 0:53, no killing blow recorded.")


async def test_a_deaths_headline_reads_the_replays_own_lines(recorded):
    """The headline reads a death's replay by its words (deaths.if_pressed: "would have kept them alive", "not
    enough on its own"), so it is checked on recorded deaths: one where the only thing ready, a healthstone, wasn't
    enough, and one where Barkskin wasn't and a healthstone was."""
    from wipebot.core.report import deaths
    rep = await server.lib().report(PULL_CASE.args["report"])
    druid = rep.player("Scopidruid")
    replayed = {rep.pull_no(r.f): r for r in await deaths.replay_deaths(rep, druid, rep.by_boss["Sszorak"])}

    def said(pull: int) -> dict:
        r = replayed[pull]
        return views.death_headline(rep.ability(r.d["killingAbilityGameID"]), r.d["timestamp"] - r.f["startTime"],
                                    r.outright, None, r.more, False)
    assert [line.rsplit(": ", 1)[1] for line in replayed[29].more[1:]] == [views.NOT_ENOUGH]
    assert said(29) == {"text": "Killed by Mutilate at 1:11: nothing ready would have been enough on its own.",
                        "kind": ""}
    assert [line.rsplit(": ", 1)[1] for line in replayed[32].more[1:]] == [views.NOT_ENOUGH, deaths.SAVED]
    assert said(32) == {"text": "A healthstone, used in time, would have kept them alive.", "kind": "fix"}


def test_a_nights_headline_counts_what_ended_the_most_pulls_over_all_of_them():
    """One count on the card: every pull that ended, resets among them, as the list of endings counts them (the card
    once said "10 of 32" over "How the 33 pulls ended")."""
    row = lambda n, **over: {"pull": n, "kill": False, "live": False, "reset": False} | over  # noqa: E731
    night = [row(1), row(2, reset=True), row(3), row(4)]
    ends = [("Mutilate killing many", 2), ("a reset", 1), ("deaths one after another", 1)]
    assert views.night_headline(night, ends) == {"text": "2 of the 4 pulls ended with Mutilate killing many.",
                                                 "kind": ""}
    going = [*night, row(5, live=True)]  # a pull still going hasn't ended
    assert views.night_headline(going, ends)["text"] == "2 of the 4 pulls ended with Mutilate killing many."
    assert views.night_headline([*night, row(5, kill=True)], ends)["text"] == "Killed on pull 5 of 5."
    assert views.night_headline([row(1, kill=True)], [("a kill", 1)])["text"] == "Killed on the first pull."
    assert views.night_headline([row(1, reset=True), row(2, reset=True)], [("a reset", 2)])["text"] == (
        "2 pulls, none past a reset.")


def test_a_nights_headline_reads_as_a_sentence_for_every_way_a_pull_ends():
    """What ended the pulls is said in progress's own words for it (ending.kind_of_end), which are not all a
    sentence's subject: "Many dying at once, no killing blow ended 3 of the 12 pulls" read as if no killing blow
    had ended them, and the pinned night as "keeping it to the end ended 10". One pull is not "1 of the 1 pull",
    and a pull that ended with the raid alive went past a reset."""
    row = lambda n, **over: {"pull": n, "kill": False, "live": False, "reset": False} | over  # noqa: E731
    said = lambda rows, *ends: views.night_headline(rows, list(ends))["text"]  # noqa: E731
    dozen = [row(n) for n in range(1, 13)]
    assert said(dozen, ("many dying at once, no killing blow", 3), ("half the raid dead", 2)) == (
        "3 of the 12 pulls ended with many dying at once, no killing blow.")
    assert said(dozen, ("the boss gaining Unbound Ferocity and keeping it to the end", 10)) == (
        "10 of the 12 pulls ended with the boss gaining Unbound Ferocity and keeping it to the end.")
    assert said(dozen, ("half the raid dead", 5)) == "5 of the 12 pulls ended with half the raid dead."
    assert said(dozen, ("deaths one after another", 7)) == "7 of the 12 pulls ended with deaths one after another."
    assert said([row(7)], ("Mutilate killing many", 1)) == "Pull 7 ended with Mutilate killing many."
    assert said([row(7), row(8, live=True)], ("Mutilate killing many", 1)) == (
        "Pull 7 ended with Mutilate killing many.")
    # no mechanic ended a pull: the counts as progress lists them, and "none past a reset" only where that is so
    assert said([row(1), row(2), row(3, reset=True)], ("no death, the raid alive", 2), ("a reset", 1)) == (
        "3 pulls: no death, the raid alive 2; a reset 1.")
    assert said([row(1, reset=True), row(2, live=True)], ("a reset", 1)) == "2 pulls: a reset 1; still going 1."
    assert said(dozen, ("the boss casting March", 4), ("no death, the raid alive", 3)) == (
        "4 of the 12 pulls ended with the boss casting March.")
    assert said([row(4, live=True)]) == "Pull 4 is still going."


def test_a_players_headline_is_the_weightiest_note_and_their_nights_what_would_have_kept_them_alive():
    died = _facts(died=[{"ms": 95000, "by": "Toxic Fumes", "fallout": False}])
    died["notes"] = views.player_notes(died)
    assert views.player_headline(died) == {"text": "Died at 1:35 to Toxic Fumes, before the wipe was decided.",
                                           "kind": "fix"}
    steady = _facts()
    steady["notes"] = views.player_notes(steady)
    assert views.player_headline(steady) == {"text": "Kept casting: 2 s of gaps in 3:00.", "kind": "good"}
    plain = _facts(output={"what": "damage", "rank": 4, "of": 14, "role": "damage dealers", "per_s_alive": 248_000},
                   alive_s=160, duration_ms=180_000) | {"notes": {"good": [], "fix": []}}
    assert views.player_headline(plain) == {
        "text": "248.00k damage per second over the 160 s alive, rank 4 of 14 damage dealers by total.", "kind": ""}
    assert views.player_night_headline(0, 12, 0) == {
        "text": "Never died before a wipe was decided in 12 pulls.", "kind": "good"}
    assert views.player_night_headline(8, 33, 0) == {
        "text": "Died before the wipe was decided on 8 of 33 pulls.", "kind": ""}
    assert views.player_night_headline(8, 33, 3) == {
        "text": "Died before the wipe was decided on 8 of 33 pulls; a ready defensive or a healthstone would have "
                "kept them alive at 3 of those 8 deaths.", "kind": "fix"}
    assert views.player_night_headline(1, 1, 1)["text"] == (
        "Died before the wipe was decided on 1 of 1 pull; a ready defensive or a healthstone would have kept them "
        "alive.")


CARD = """() => {
  const all = (sel) => [...document.querySelectorAll(sel)];
  return { lead: all(".lead").map((p) => p.textContent), stats: all(".stats .stat").length,
           buttons: all(".actions button").map((b) => b.textContent), charts: all("#app svg:not(.out svg)").length,
           lists: all("ul.ranked").length, tables: all("table").length,
           tall: Math.ceil(document.body.getBoundingClientRect().height),
           order: all("table, ul.ranked").map((e) => e.tagName.toLowerCase()) };
}"""


@pytest.mark.parametrize("width", [380, 760])
async def test_every_card_is_a_headline_four_stats_one_chart_and_two_buttons(recorded, width):
    """The inline card has one shape in every view (kit.card): a sentence code wrote, at most four numbers, one chart
    and at most two buttons, "Open ..." first. A night's is that too: it once ran to 684 px at 760 wide and 836 on a
    phone, with two lists the answer's text has already; they and the pulls' table are in fullscreen, the table
    first."""
    from wipebot.core import viewhost
    for case, opens, most in (("pull-summary", "Open pull", 520), ("death-recap", "Open death", 500),
                              ("progress", "Open night", 420)):
        async with drawn(CASES[case].tool, CASES[case].args, width=width) as (view, errors):
            card = await view.evaluate(CARD)
            want = await view.evaluate("kit.CARD")
        assert not errors and want == {"stats": 4, "chart": 220, "buttons": 2}
        assert len(card["lead"]) == 1 and card["lead"][0].endswith("."), case
        assert 1 <= card["stats"] <= 4 and card["charts"] == 1 and card["lists"] == card["tables"] == 0, case
        assert card["buttons"][0] == opens and len(card["buttons"]) <= 2 and card["tall"] <= most, (case, card)
    night = CASES["progress"]
    async with drawn(night.tool, night.args, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        full = await view.evaluate(CARD)
    assert not errors and full["order"] == ["table", "ul", "ul"] and "Open night" not in full["buttons"]


async def test_the_player_cards_are_the_same_card_and_a_wipes_standing_is_fullscreens_with_why(made_up, monkeypatch):
    """A player's pull and night are the card too. Where a wipe's output stands among ranked kills isn't on the card
    or in a picture: it is drawn in fullscreen only, with why it proves little by itself, in the tool's words."""
    from wipebot.core import viewhost
    async with drawn("player_pull", {"player": "Mirelle"}) as (view, errors):
        card, text = await view.evaluate(CARD), await view.locator("body").inner_text()
    assert not errors and card["lead"] == ["290.00k healing per second over the 143 s alive, rank 2 of 4 healers "
                                           "by total."]
    assert card["stats"] == 4 and card["charts"] == 1 and card["buttons"] == ["Open player", "Against top players"]
    assert "ranked" not in text and "100th best" not in text and views.SHORT_PULL not in text
    async with drawn("player_pull", {"player": "Mirelle"}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, _):
        text = await view.locator("body").inner_text()
    assert "ranked Restoration Shaman kills" in text and "100th best" in text
    assert f"{views.SHORT_PULL} {views.CAPPED_HEALING}" in text
    night = made_up_night()
    made_up_view(monkeypatch, {"view": "player_night", "report": night["report"], "player": "Mirelle"}, night)
    async with drawn("player_night", {}) as (view, errors):
        card = await view.evaluate(CARD)
    assert not errors and card["stats"] == 4 and card["charts"] == 0 and card["buttons"] == ["Open night"]
    assert card["lead"] == ["−Died before the wipe was decided on 2 of 6 pulls; a ready defensive or a healthstone "
                            "would have kept them alive at 1 of those 2 deaths."]


async def test_asked_about_the_whole_night_the_card_leads_with_its_bosses(monkeypatch):
    """progress without a boss answers for the whole night, and its text leads with every boss: so does its card,
    each with its pulls and its kill or best wipe (it once drew the last boss alone, as if asked about it). Asked
    about one boss, the others are in fullscreen."""
    night = {"view": "night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "difficulty": "Heroic",
             "title": "Torvek's Forge, Mon 1 Jan", "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678",
             "combat_ms": 600000, "guild_best": None, "phased": False, "first_n": 3, "thirds": None,
             "ends": [["Slag Wave killing many", 2]], "firsts": [["Slag Wave", 4]],
             "bosses": [{"boss": "Ashen Warden", "pulls": 1, "kill": True, "best": None},
                        {"boss": "Torvek's Forge", "pulls": 2, "kill": False, "best": 41.5}],
             "pulls": [{"pull": n, "pct": pct, "kill": False, "reset": False, "live": False, "duration_ms": 300000,
                        "phase": "", "health": None, "ended": "Slag Wave killing many", "best": n == 1,
                        "first": None} for n, pct in ((1, 41.5), (2, 60.0))]}
    night["headline"] = views.night_headline(night["pulls"], [("Slag Wave killing many", 2)])
    made_up_view(monkeypatch, {"view": "night", "report": night["report"], "boss": night["boss"]}, night)
    chips = '() => [...document.querySelectorAll(".chips.bosses .chip")].map((b) => [b.textContent, b.className])'
    async with drawn("progress", {"report": night["report"]}) as (view, errors):
        assert await view.evaluate(chips) == [["Ashen Warden · 1 pull, killed", "chip"],
                                              ["Torvek's Forge · 2 pulls, best 42%", "chip on"]]
        under = "document.querySelector('.chips.bosses + [data-panel] h1').textContent"  # then that boss's card
        assert await view.evaluate(under) == "Torvek's Forge"
        assert (await view.evaluate(CARD))["lead"] == ["2 of the 2 pulls ended with Slag Wave killing many."]
        assert not errors
    async with drawn("progress", {"report": night["report"], "boss": "Torvek"}) as (view, errors):
        assert await view.evaluate(chips) == [] and not errors


HEARD = """(method) => window.__log.filter((m) => m.dir === "←" && m.msg.method === method).map((m) => m.msg.params)"""
REPORT = "KBYT4xXn7VNjDgh8"


async def test_the_model_is_told_what_the_user_opened_and_where_a_replay_stopped(recorded):
    """A click on a pull in the night's card, then "why did this one go wrong?": the model reads which pull is on
    screen with that message (ui/update-model-context: one line, each replacing the last), on the way back what is
    there again, and in a replay the moment the user stopped at. The tool's own view, as it first shows, is what the
    model asked for: nothing is sent."""
    async with drawn("progress", NIGHT_OF_PULLS.args) as (view, errors):
        async def told() -> list[str]:
            said = await view.page.evaluate(HEARD, "ui/update-model-context")
            assert all(list(p) == ["content"] and len(p["content"]) == 1 for p in said)
            return [p["content"][0]["text"] for p in said]
        assert await told() == []
        await view.locator('[data-pull-col="13"]').click()
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert await told() == [f"The user is now looking at Sszorak pull 13 (report {REPORT})."]
        await view.get_by_role("button", name="Replay").click()
        await view.locator("#rp-map").wait_for()
        replay = "The user is now looking at the replay of Sszorak · pull 13 at"
        assert (await told())[-1] == f"{replay} 0:45 (report {REPORT})."
        for _ in range(4):  # scrubbing says the moment once, where it stopped
            await view.locator("#rp-timeline").press("ArrowRight")
        await view.page.wait_for_timeout(900)
        assert (await told())[2:] == [f"{replay} 0:49: Scopidruid died (Ula'tek's Presence) (report {REPORT})."]
        await view.locator("button.back").click()
        await view.locator("#pv-chart").wait_for()
        await view.locator("button.back").click()
        await view.locator("[data-pull-col]").first.wait_for()
        assert (await told())[3:] == [f"The user is now looking at Sszorak pull 13 (report {REPORT}).",
                                      "The user is now looking at the night's pulls on Sszorak, Mon 5 Oct "
                                      f"(report {REPORT})."]
        assert not errors


async def test_a_host_that_takes_no_model_context_is_sent_none(recorded):
    async with drawn("progress", NIGHT_OF_PULLS.args, capabilities={"openLinks": {}}) as (view, errors):
        await view.locator('[data-pull-col="13"]').click()
        await view.locator("h1", has_text="Sszorak pull 13").wait_for()
        assert await view.page.evaluate(HEARD, "ui/update-model-context") == [] and not errors


CLICK_OUT = """(a) => new Promise((done) => {
  window.addEventListener("click", (e) => { done(e.defaultPrevented); e.preventDefault(); }, { once: true });
  a.click();
})"""


async def test_every_card_links_to_its_pull_on_warcraft_logs_and_the_host_opens_it(recorded):
    """The link is the server's (the pull's, or the night's report): inline at the end of the card's buttons, in
    fullscreen at the end of its head's line. A host that says it opens links is asked to (ui/open-link: a view's
    frame may not open a page itself); in another the click is the browser's. A picture has none."""
    from wipebot.core import viewhost
    report = f"https://www.warcraftlogs.com/reports/{REPORT}"
    for case, url in (("pull-summary", f"{report}#fight=21"), ("death-recap", f"{report}#fight=21"),
                      ("progress", report)):
        async with drawn(CASES[case].tool, CASES[case].args) as (view, errors):
            out = view.locator(".actions a.out")
            assert await out.inner_text() == "Warcraft Logs" and await out.get_attribute("href") == url, case
            assert await out.evaluate(CLICK_OUT) is True
            await view.page.wait_for_timeout(100)
            assert await view.page.evaluate(HEARD, "ui/open-link") == [{"url": url}] and not errors, case
    pull = CASES["pull-summary"]
    async with drawn(pull.tool, pull.args, capabilities={}) as (view, errors):
        assert await view.locator(".actions a.out").evaluate(CLICK_OUT) is False
        assert await view.page.evaluate(HEARD, "ui/open-link") == [] and not errors
    async with drawn(pull.tool, pull.args, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        assert await view.locator("a.out").count() == 1
        assert await view.locator(".head .sub").inner_text() == "Mythic · 2:42 · Warcraft Logs" and not errors
    async with drawn(pull.tool, pull.args, picture=True) as (view, errors):
        assert await view.locator("a").count() == 0 and not errors
