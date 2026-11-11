"""The views (report/views.py, the page in app/): each view's data on a real pull, against its tool's text and its
snapshot; the page drawn in the mock host (viewhost.py, Chromium); the replay's JavaScript against the tracks
maps.replay packs (Node)."""
import asyncio
import contextlib
import dataclasses
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
from tests.support.recorded import CASES, LOCAL, LOCAL_RECORDED, NOT_LOCAL, Replay, asked, serve_from
from wipebot.core import server
from wipebot.core.classes import CLASS_COLORS, WHITE
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError, maps, tables, views
from wipebot.core.report import note as notes
from wipebot.core.report.coaching import TimeLost, time_lost
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
    # the deaths until the wipe was decided are the text's rows, the fallout its one line, in the card's order
    rows = re.findall(r"^\d+\. (\d+:\d\d) (\S+)", text, re.MULTILINE)
    assert [(clock(d["ms"]), d["name"]) for d in data["deaths"] if not d["fallout"]] == rows
    (fallout,) = re.findall(r"^Fallout, (\d+) deaths after the wipe was decided at (\S+) \((.+?)\), by .+?: (.+);",
                            text, re.MULTILINE)
    after = [d for d in data["deaths"] if d["fallout"]]
    decided = data["decided"]
    assert decided and fallout[:3] == (str(len(after)), clock(decided["ms"]), decided["why"])
    assert all(d["name"] in fallout[3] and f"{clock(d['ms'])} " in fallout[3] for d in after)
    assert f"gained {decided['why'].removeprefix('the boss gained ')} at {clock(decided['ms'])}" in text
    assert [d["fallout"] for d in data["deaths"]] == [d["ms"] > decided["ms"] for d in data["deaths"]]
    assert f"wipe at {data['got']}" in text and data["health_end"] == data["got"] == "62.6%"
    assert text.startswith(f"{data['boss']} ({data['difficulty']}) pull {data['pull']}: ")
    # the card's headline is the text's "Ended by", without its ids and what to look up
    headline = data["headline"]["text"]
    assert headline == "The boss gained Unbound Ferocity at 2:32 and kept it to the end, with 16 of 20 alive."
    assert headline.removeprefix("The boss ").removesuffix(".").replace(", with ", ", ") in text
    assert len(json.dumps(data)) < 150_000 // 10  # far under Claude's limit for a tool result


async def test_the_pull_cards_best_wipes_are_written_as_the_text_writes_a_percentage(recorded, monkeypatch):
    """The log's best wipe before this pull and the guild's best before the night come to the card written, by the
    function the text writes a boss % with. The page rounded them itself, and JavaScript rounds 63.25 to 63.3
    where the text says 63.2%: about one pull in a hundred, beside the number the answer quotes."""
    from wipebot.core.pulls import pct_text
    data = await pull_data()
    assert data["best"]["tonight"] == pct_text(66.79) == "66.8%"
    made_up_view(monkeypatch, {"view": "pull", "report": data["report"], "pull": data["pull"]}, data | {
        "best": {"tonight": pct_text(63.25), "new_tonight": False, "new_guild": False,
                 "guild": {"pct": pct_text(41.25), "day": "Thu 9 Oct"}}})
    async with drawn("pull_summary", {}, mode="fullscreen", width=1100) as (view, errors):
        said = await view.locator(".stats").first.inner_text()
    assert not errors and "best of this log 63.2%" in said and "41.2%\nThu 9 Oct" in said


async def test_a_damage_peak_names_the_healer_cooldowns_as_long_before_it_as_healing_counts_one(recorded, monkeypatch):
    """How long before a raid damage peak a healer's cooldown is named with it is the band healing's text has for a
    cooldown used for a spike, handed to the page: the chart kept 10 s of its own beside the answer's 5 s."""
    from wipebot.core.report.healing import COOLDOWN_LEAD_MS
    src = resources.files("wipebot.core").joinpath("app", "pull.js").read_text(encoding="utf-8")
    assert "PEAK_BEFORE_MS" not in src and "10000" not in src
    data = await pull_data()
    assert data["damage"]["lead_ms"] == COOLDOWN_LEAD_MS == 5_000
    peak = {"from_ms": 150_000, "to_ms": 160_000, "per_s": 5_000_000}
    pressed = [{"ms": peak["from_ms"] - COOLDOWN_LEAD_MS - 1, "kind": "healing", "name": "Too Soon", "player": "Rowan"},
               {"ms": peak["from_ms"] - COOLDOWN_LEAD_MS, "kind": "healing", "name": "In Time", "player": "Ash"},
               {"ms": peak["to_ms"], "kind": "healing", "name": "In It", "player": "Wren"},
               {"ms": peak["to_ms"] - 1, "kind": "raid", "name": "The Raid's", "player": "Sage"}]
    tips = []
    for cooldowns in (pressed, pressed[:1]):
        made_up_view(monkeypatch, {"view": "pull", "report": data["report"], "pull": data["pull"]}, data | {
            "damage": data["damage"] | {"peaks": [peak]}, "cooldowns": cooldowns})
        async with drawn("pull_summary", {}, mode="fullscreen", width=1100) as (view, errors):
            tips.append(await view.locator('svg rect[data-tip^="Raid damage peak"]').first.get_attribute("data-tip"))
        assert not errors, errors
    assert tips[0].endswith("\nHealer cooldowns from 5 s before: In Time (Ash) 2:25, In It (Wren) 2:40")
    assert tips[1].endswith("\nNo healer cooldown from 5 s before")


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
    assert data["saved_by"] == ["a healthstone"]  # the text's line of it is checked above: the card's headline,
    assert data["headline"] == {"kind": "fix", "text": (  # with what the text's line takes as given
        "A healthstone (if they had one; none used this pull), used in time, would have kept them alive.")}
    # what the death was to its wipe is the text's first line's; the other deaths are the text's list of them
    assert data["note"] == "before the wipe was decided" and f"pull 13 ({data['url']}), {data['note']}. " in text
    assert not data["fallout"] and data["mass"] is None
    others = re.search(rf"^Other deaths within {data['with_ms'] // 1000} s: (.+)$", text, re.MULTILINE)
    assert others and others[1] == ", ".join(f"{w['name']} ({w['by']}, {clock(w['ms'])})" for w in data["with"])


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
    # a pull is starred where the text marks it the best of the night until then: the night's first wipe is that by
    # being first, and the text leaves it unmarked (the table once starred it)
    for r in data["pulls"]:
        row = next(x for x in text.splitlines() if x.startswith(f"  {r['pull']}: "))
        assert r["best"] == ("best of the night until then" in row), r["pull"]
    assert [r["pull"] for r in data["pulls"] if r["best"]] == [2, 3, 7, 13]


async def test_the_night_card_prints_each_wipes_percent_as_progress_writes_it(recorded, monkeypatch):
    """The night's card rounded a wipe's % itself, to a whole number from 10% up: "63%" in pull 13's row, on the
    best wipe's tile and bar and on the boss's chip, under a text that says 62.6%, and the thirds as "78% → 75% →
    76%" for the text's 77.7, 75.4 and 76.1. Each % is written where the text's is (pulls.pct_text) and printed."""
    from wipebot.core import viewhost
    data = await raid_night_data()
    text, _ = await server.call("progress", NIGHT_OF_PULLS.args)
    for r in data["pulls"]:
        assert re.search(rf"^  {r['pull']}: {re.escape(r['pct_text'])} ", text, re.MULTILINE), r["pull"]
    assert "first third {}, middle {}, last third {}".format(*data["thirds_text"]) in text
    async with drawn("progress", NIGHT_OF_PULLS.args, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        tiles = await view.evaluate(STAT_TILES)
        cells = await view.locator("table.pulls tbody tr").evaluate_all(
            "trs => trs.map((tr) => tr.children[1].textContent.replace(' ★', ''))")
        chips = await view.locator(".chips.bosses .chip").all_inner_texts()
        marks = await view.evaluate("() => [...document.querySelectorAll('svg text')].map((t) => t.textContent)")
        tip = await view.locator('[data-pull-col="13"]').first.get_attribute("data-tip")
    assert not errors and [t.upper() for t in tiles] == [
        "BEST WIPE\n62.6%\nPULL 13", "AVERAGE WIPE %\n77.7 → 75.4 → 76.1\nFIRST, MIDDLE, LAST THIRD"]
    assert cells == ["reset" if r["reset"] else r["pct_text"] for r in data["pulls"]] and cells[12] == "62.6%"
    assert chips == ["Vashnik the Malignant · 2 pulls, killed", "Sszorak · 33 pulls, best 62.6%"]
    assert "62.6%" in marks and tip.startswith("Pull 13: 62.6% left (P1 (2nd time)), 2:42")
    # A boss counted by phase, made up, with halves that round two ways: 68.25 is "68.2%" as the text writes it
    # and was "68%" here; a page that rounds for itself writes "68.3%"
    night = {"view": "night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "difficulty": "Heroic",
             "title": "Torvek's Forge, Mon 1 Jan", "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678",
             "combat_ms": 600000, "phased": True, "first_n": 3, "thirds": [70.25, 61.0, 55.55],
             "guild_best": {"pct": 41.25, "day": "Thu 9 Oct", "pull": 7, "phase": "P3", "health": 12.25},
             "ends": [["Slag Wave killing many", 2]], "firsts": [["Slag Wave", 4]],
             "bosses": [{"boss": "Torvek's Forge", "pulls": 2, "kill": False, "best": 55.25}],
             "pulls": [{"pull": n, "pct": pct, "kill": False, "reset": False, "live": False, "duration_ms": 300000,
                        "phase": "P2 (2nd time)", "health": health, "ended": "Slag Wave killing many",
                        "best": False, "first": None} for n, pct, health in ((1, 60.0, 80.0), (2, 55.25, 68.25))]}
    night["headline"] = views.night_headline(night["pulls"], [("Slag Wave killing many", 2)])
    made_up_view(monkeypatch, {"view": "night", "report": night["report"], "boss": night["boss"]}, written(night))
    async with drawn("progress", {}, width=380) as (view, errors):
        tiles = [t.split("\n")[:2] for t in await view.evaluate(STAT_TILES)]
        marks = await view.evaluate("() => [...document.querySelectorAll('svg text')].map((t) => t.textContent)")
        tip = await view.locator('[data-pull-col="2"]').first.get_attribute("data-tip")
    assert not errors and [[a.upper(), b] for a, b in tiles] == [
        ["BEST WIPE", "P2 68.2%"], ["GUILD BEST BEFORE", "P3 12.2%"], ["AVERAGE WIPE %", "70.2 → 61.0 → 55.5"]]
    assert "P2 68.2%" in marks and "guild best before: P3 12.2%" in marks
    assert tip.startswith("Pull 2: P2 (2nd time) at 68.2% (WCL 55.2%), 5:00")


@pytest.mark.parametrize("case", ["pull-summary", "vashnik-summary", "altar-summary"])
async def test_the_pull_cards_boss_health_is_what_the_texts_first_line_states(recorded, case):
    """The card's "Boss" number was the log's raw boss health ("62.59%") under a text that leads with "wipe at
    62.6%": it is the text's own words for how far the wipe got, on a boss counted by phase the phase and its health
    with Warcraft Logs' % beside it. The end of the health line is named with the boss's health written the same
    way, so one card has one number."""
    tool, args = CASES[case].tool, CASES[case].args
    key = await server.view_key(tool, args)
    assert key
    data = (await server.view_data(**asked(key))).structured_content
    text, failed = await server.call(tool, args)
    assert data and not failed
    head = text.splitlines()[0]
    if case == "altar-summary":  # counted by phase
        phase, health = data["got"].split(" ")
        assert f": wipe in {phase} at {health} (WCL {data['got_wcl']}), " in head and phase == data["phase"]
    else:
        assert f": wipe at {data['got']}, " in head and data["got_wcl"] is None
    assert re.fullmatch(r"(P\d+ )?\d+\.\d%", data["got"])
    assert data["got"].endswith(data["health_end"])  # the health line's end: the same number
    async with drawn(tool, args) as (view, errors):
        stats = await view.evaluate('() => [...document.querySelectorAll(".stat")].map((e) => e.innerText)')
        ends = await view.evaluate("() => [...document.querySelectorAll('svg text')].map((t) => t.textContent)")
    assert not errors and stats[0].upper().startswith(f"BOSS\n{data['got']}".upper()), stats[0]
    assert data["health_end"] in ends and not [t for t in ends if re.fullmatch(r"\d+\.\d\d%", t)]


async def test_the_raid_night_view_draws_and_a_pulls_bar_opens_that_pull(recorded, tmp_path):
    first, opened = await shots(tmp_path, "progress", NIGHT_OF_PULLS.args, themes=("dark",), widths=(760,),
                                modes=("fullscreen",), clicks=('css:tr[data-pull="13"]',))
    assert not first.errors and "how the 33 pulls ended" in first.text.lower() and "Vashnik" in first.text
    assert "Sszorak pull 13" in opened.text  # that pull's card


# ---- the meter: a ranking of players from breakdown and mechanic_hits (a card in Claude and ChatGPT, a picture in
# the bot) and from enemies (the bot's picture only) ----

METER_CASES = ["mechanic-hits", "damage-taken-ability", "interrupts", "dispels", "nekzali-enemies-night",
               "vashnik-enemies", "damage-least"]


async def meter_data(case: str) -> dict:
    c = CASES[case]
    key = await server.view_key(c.tool, c.args)
    assert key is not None and key["view"] == "meter" and key["tool"] == c.tool, case
    result = await server.view_data(**asked(key))
    assert result.structured_content is not None
    return result.structured_content


async def test_the_meter_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "meter.txt", readable(await meter_data("mechanic-hits")))


def test_the_night_cards_quiet_endings_are_the_cores():
    """The endings the night's card draws quiet (a kill, a reset, a pull still going, a raid alive at the end) are
    the ones the core names (ending.QUIET_ENDS), which the card's headline leaves out too: one list."""
    from wipebot.core.report import ending

    quiet = re.search(r"const QUIET = new Set\(\[(.*?)\]\)", views.APP.joinpath("raidnight.js").read_text())
    assert quiet and json.loads(f"[{quiet.group(1)}]") == list(ending.QUIET_ENDS)
    assert views.QUIET_ENDS is ending.QUIET_ENDS


@pytest.mark.parametrize("case", METER_CASES)
async def test_a_meter_ranks_the_players_its_tool_states(recorded, case):
    """A meter is its tool's own rows: every bar a player its text names with the amount it states there, and
    around them only what the text states (its total and shares, which no table of kicks has; of adds the damage
    the kind took, and no shares). Dispels are the one sum left: the text lists who dispelled each debuff, and a
    bar is a player's dispels over those lists, a count and an order of players the text itself doesn't state."""
    data = await meter_data(case)
    text, failed = await server.call(CASES[case].tool, CASES[case].args)
    assert not failed
    dispels = [dict((name, int(n)) for name, n in re.findall(r"(\S+) (\d+)", who))
               for who in re.findall(r"^  .+?: .*?; dispels by (.*?)(?:;|$)", text, re.MULTILINE)]
    for m in data["meters"]:
        values = [b["value"] for b in m["bars"]]
        assert len(values) >= 3 and values == sorted(values, reverse=True), m["title"]
        assert "total" not in m and (m["stated"] is None) == (m["unit"] != "amount")
        for b in m["bars"]:
            assert b["name"] in text, (m["title"], b["name"])
            assert bool(b["share"]) == (bool(m["stated"]) and CASES[case].tool != "enemies")
            if m["unit"] == "kicks":
                assert re.search(rf"^  {re.escape(b['name'])}: {round(b['value'])}\b", text, re.MULTILINE)
            elif m["unit"] == "dispels":
                assert len(dispels) == 2 and b["value"] == sum(by.get(b["name"], 0) for by in dispels), b["name"]
            elif CASES[case].tool == "enemies":  # everyone who damaged the most damaged kind, as its line lists them
                assert f"{b['name']} {short(b['value'])}" in text, (m["title"], b["name"])
            elif m["unit"] == "amount":
                assert short(b["value"]) in text, (b["name"], short(b["value"]))


@pytest.mark.parametrize("case", ["damage-least", "damage-taken-ability"])
async def test_a_meters_total_rate_time_and_shares_are_its_texts_own(recorded, case):
    """breakdown's heading states the table's total, its rate and the combat time, and each row its share of that
    total: the meter hands those on and sums nothing over its bars. With `least` the bars are the damage dealers
    alone and the total is still the raid's, so a sum over them gave another total (13.39M under a text saying
    14.83M) and another share on 9 of the 13 bars; a clock from seconds rounded up read a second past the text's."""
    data = await meter_data(case)
    text, _ = await server.call(CASES[case].tool, CASES[case].args)
    [m] = data["meters"]
    said = m["stated"]
    assert f"({said['over']} of combat; total {said['total']}, {said['per_s']})" in text.splitlines()[0]
    for b in m["bars"]:
        assert re.search(rf"^  \d+\. {re.escape(b['name'])} \([^)]*\)(?: \(tank\))?: {short(b['value'])} \([^)]*/s\), "
                         rf"{b['share']} of total", text, re.MULTILINE), (b["name"], b["share"])
    if case == "damage-least":
        assert short(sum(b["value"] for b in m["bars"])) != said["total"]  # the bars are not the whole table


async def test_a_meter_says_who_was_in_only_some_of_its_pulls_as_the_text_does(recorded, monkeypatch):
    """Over several pulls the text says of a player who sat some out in how many they were and their rate over
    those; the bar says it in the same words, and so does the headline of the one it names, so that a ranking from
    the least doesn't lead with a player who wasn't there without saying so. No recorded ranking over several
    pulls has such a player, so one is taken out of the first pull's roster here."""
    from wipebot.core.report.base import Report
    args = {"report": REPORT, "data_type": "DamageDone", "pulls": [12, 13], "least": True}
    whole = (await server.view_data("meter", report=REPORT, tool="breakdown", args=args)).structured_content
    last = whole["meters"][0]["bars"][-1]["name"]
    assert not any("pulls" in b["note"] for b in whole["meters"][0]["bars"])
    rep = await server.lib().report(REPORT)
    first, in_pull = rep.fight(12, None), Report.pull_players
    monkeypatch.setattr(Report, "pull_players", lambda self, f: {
        k: v for k, v in in_pull(self, f).items() if f is not first or v.name != last})
    text, _ = await server.call("breakdown", args)
    [m] = (await server.view_data("meter", report=REPORT, tool="breakdown", args=args)).structured_content["meters"]
    there = re.search(rf"\. {re.escape(last)} \(.*, (in 1 of 2 pulls \(\S+/s over them\))", text)
    assert there and m["bars"][-1]["note"] == there.group(1)
    assert m["headline"]["text"].startswith(f"{last} is last with {short(m['bars'][-1]['value'])}, {there.group(1)}, ")
    assert sum("pulls" in b["note"] for b in m["bars"]) == 1


async def test_a_mechanics_meter_is_in_its_texts_order_by_what_the_text_ranks_by(recorded):
    """mechanic_hits lists its players by hits, over a night per minute of the pulls each was in; the meter ranked
    them by damage, so the answer said Krøpling was second and the picture under it showed him tenth. The bars are
    the text's rows in its order, each with the rate its row states and the row's other numbers in its words."""
    data = await meter_data("mechanic-hits")
    text, _ = await server.call("mechanic_hits", CASES["mechanic-hits"].args)
    [m] = data["meters"]
    rows = re.findall(r"^  (\S+)(?: \(tank\))?: (\d+ hits? \(\+\d+ ticks?\) in \d+ pulls?) \((\d+\.\d\d) per minute\), "
                      r"(\S+), ", text, re.MULTILINE)
    assert len(rows) == 22 and "most hits per minute of the pulls they were in first" in text
    assert m["unit"] == "hits per minute of the pulls they were in"
    assert [(b["name"], b["text"], b["note"]) for b in m["bars"]] == [
        (name, rate, f"{counts}, {damage}") for name, counts, rate, damage in rows]
    assert [b["name"] for b in m["bars"]][:3] == ["Deathadim", "Krøpling", "Sveini"]
    assert m["headline"]["text"] == ("Deathadim is first with 2.14 hits per minute of the pulls they were in, 2.5 "
                                     "times the median of the 22 players ranked.")
    assert all(b["share"] == "" for b in m["bars"]) and m["stated"] is None  # its text states no total of hits


def test_a_mechanics_meter_on_one_pull_is_ranked_by_hits_and_one_that_only_ticks_by_its_ticks():
    """On one pull the text lists most hits first, so a bar's amount is the hits and its words the ticks and the
    damage; a debuff that lands more often than it deals damage is ranked by its applications; and for an ability
    that only ticks the text's order falls to the ticks, which are the bar's amount then."""
    from collections import Counter

    from wipebot.core.report.mechanics import Hits
    from wipebot.core.report.meter import Bar, Meter

    names = {1: "Mirelle", 2: "Rowan", 3: "Tamsin"}
    drawn = Meter("Slag Wave", "Torvek pull 3", [Bar("Rowan", 900.0), Bar("Mirelle", 500.0), Bar("Tamsin", 100.0)])
    h = Hits(hits=Counter({1: 4, 2: 2, 3: 1}), ticks=Counter({2: 6}), damage=Counter({1: 500, 2: 900, 3: 100}))
    m = views._as_its_text_ranks(names, drawn, h, [(1, 4, None), (2, 2, None), (3, 1, None)])
    assert m.unit == "hits" and [(b.name, b.value, b.note) for b in m.bars] == [
        ("Mirelle", 4, "500"), ("Rowan", 2, "6 ticks, 900"), ("Tamsin", 1, "100")]
    assert views.meter_headline(m)["text"] == (
        "Mirelle is first with 4 hits, 2.0 times the median of the 3 players ranked.")
    stacks = Hits(hits=Counter({1: 1}), debuffs=Counter({1: 3, 2: 5, 3: 4}), damage=Counter({1: 500}))
    m = views._as_its_text_ranks(names, drawn, stacks, [(2, 5, None), (3, 4, None), (1, 3, None)])
    assert m.unit == "applications" and [(b.name, b.value, b.note) for b in m.bars] == [
        ("Rowan", 5, ""), ("Tamsin", 4, ""), ("Mirelle", 3, "1 hit, 500")]
    over = Hits(ticks=Counter({1: 3, 2: 9, 3: 5}), damage=Counter({1: 300, 2: 900, 3: 500}))
    night = dataclasses.replace(drawn, pulls=[1, 2])
    m = views._as_its_text_ranks(names, night, over, [(2, 0, 2), (3, 0, 2), (1, 0, 1)])
    assert m.unit == "ticks" and [(b.name, b.value, b.note) for b in m.bars] == [
        ("Rowan", 9, "in 2 pulls, 900"), ("Tamsin", 5, "in 2 pulls, 500"), ("Mirelle", 3, "in 1 pull, 300")]


async def test_a_mechanics_meter_states_its_deaths_as_its_text_does(recorded, tmp_path):
    """The deaths on a mechanic's meter are its text's "Total" line and each row's own two counts, in the text's
    words: deaths of any cause within 6 s of a hit, and those with it as the killing blow. Nothing says the
    mechanic set a death up: the text dropped that claim, and the page said it in six places."""
    data = await meter_data("mechanic-hits")
    text, _ = await server.call("mechanic_hits", CASES["mechanic-hits"].args)
    [m] = data["meters"]
    assert m["title"] == "Tempest" and m["icon"]
    for b in m["bars"]:
        assert re.search(rf"^  {re.escape(b['name'])}(?: \(tank\))?: .*, {plural(b['died_after'], 'death')} within "
                         rf"{m['within_s']} s, {plural(b['killed_by'], 'killing blow')}", text, re.MULTILINE), b["name"]
    assert (f"Total with Tempest as the killing blow: {m['deaths']['killed_by']}; deaths of any cause within "
            f"{m['within_s']} s of a hit: {m['deaths']['after']}") in text
    assert "set up" not in views.APP.joinpath("meter.js").read_text()
    [s] = await shots(tmp_path, "mechanic_hits", CASES["mechanic-hits"].args, as_picture=True)
    assert not s.errors and "set up" not in s.text
    stats = s.text.upper()
    assert "KILLING BLOWS\n48\n" in stats and "DEATHS WITHIN 6 S\n95\n" in stats and "TOTAL\n" not in stats
    # the hits pull by pull under it are in the meter's order, the text's, not sorted again
    grid = s.text.split("Tempest by pull")[1]
    names = [b["name"] for b in m["bars"]]
    assert sorted(names, key=grid.index) == names


async def test_against_another_night_the_changes_are_the_ones_the_text_writes(recorded):
    """mechanic_hits' against writes the raid's change, the deaths soon after a hit per minute both nights and each
    player's change: the meter hands them on as written, where the page worked each out again."""
    data = await meter_data("mechanic-hits-against")
    text, _ = await server.call("mechanic_hits", CASES["mechanic-hits-against"].args)
    [m] = data["meters"]
    t = m["then"]
    assert (f"per minute against {short(t['raid_was'])} ({t['change']}); deaths of any cause within {m['within_s']} s "
            f"of a hit {t['deaths_per_min'][1]} per minute against {t['deaths_per_min'][0]} ({t['deaths']} against "
            f"{t['deaths_was']})") in text
    assert m["headline"]["text"].endswith(f" on {t['day']} ({t['change']}).")  # the sentence states the same change
    both = [b for b in m["bars"] if "was" in b]
    assert len(both) >= 15
    for b in both:
        assert re.search(rf"^  {re.escape(b['name'])}(?: \(tank\))?: {short(b['value'])} \(\d+\w\w\) against "
                         rf"{short(b['was'])} \(\d+\w\w\), {re.escape(b['change'])};", text, re.MULTILINE), b["name"]


@pytest.mark.parametrize("case", ["twin-fangs-enemies-waves", "vashnik-enemies", "nekzali-enemies-night"])
async def test_enemies_meter_is_the_one_list_its_text_states_and_says_when_that_is_not_by_wave(recorded, case):
    """enemies lists who damaged the most damaged kind of add; its meter is that list. Each player's damage on
    every kind summed was a meter too, a number the text states nowhere, and with by_wave the picture showed the
    whole pull's sums under an answer about waves without saying so. The damage that kind took is on its line in
    the text ("took 175.13M") and was nowhere on the meter: it is the meter's total, as written there, with no
    rate, time or shares, which the text doesn't state of it."""
    from wipebot.core.report import adds
    data = await meter_data(case)
    text, _ = await server.call("enemies", CASES[case].args)
    [m] = data["meters"]
    assert m["title"] != adds.EVERY_KIND and m["title"].startswith("Damage on ")
    kind = m["title"].removeprefix("Damage on ")
    took = re.search(rf"^  {re.escape(kind)}: .*?, took ([\d.]+[kMB]?)(?:;|$)", text, re.MULTILINE)
    assert took and m["stated"] == {"total": took.group(1), "over": "", "per_s": ""}
    assert not any(b["share"] for b in m["bars"])
    for b in m["bars"]:
        assert f"{b['name']} {short(b['value'])}" in text, b["name"]
    by_wave = bool(CASES[case].args.get("by_wave"))
    assert m["scope"].endswith(", the whole pull, not by wave") == by_wave
    assert ("by wave, by player" in text) == by_wave


def test_every_argument_of_a_tool_with_a_meter_is_read_for_its_meter():
    """A meter is drawn from its tool's arguments: one the meter never read (enemies' by_wave was) gives a picture
    of another question than the answer's. Each is read by name where the meters are made, or said here to be
    read elsewhere."""
    import inspect
    elsewhere = {"against": "the other night, handed in as its report (`other`)"}
    made = inspect.getsource(views._ranked)
    read = set(re.findall(r'a(?:\.get\(|\[)"(\w+)"', made))
    for tool in views.METER_TOOLS:
        takes = set(inspect.signature(inspect.unwrap(getattr(server, tool))).parameters) - {"ctx", "report"}
        assert takes and takes <= read | set(elsewhere), (tool, takes - read - set(elsewhere))


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
    [s] = await shots(tmp_path, "breakdown", CASES["damage-least"].args, as_picture=True)
    least, top = m["bars"][-1]["name"], m["bars"][0]["name"]
    said = m["headline"]["text"]
    assert not s.errors and said.startswith(f"{least} is last with ") and said in s.text
    rows = s.text.split(said)[1]
    assert rows.index(least) < rows.index(top)


@pytest.mark.parametrize("case", ["damage-taken", "mechanic-outliers", "buffs", "debuffs-by-pull", "interrupts-by-pull",
                                  "breakdown-casts", "healing-by-ability"])
async def test_a_call_that_ranks_no_players_has_no_meter(recorded, case):
    """Nor a trend: by pull only an amount is one (damage, healing, damage taken, casts), not an aura's uptimes or
    the kicks' counts by cast."""
    assert await server.view_key(CASES[case].tool, CASES[case].args) is None


def test_a_meters_headline_says_who_is_first_and_how_that_stands_against_the_median():
    """The ranking in one sentence, from the meter's own rows: who is first with how much and how many times the
    median that is; with `least`, who is last and at what share of it; a count in its unit; against another night,
    the raid's per minute both nights and the change."""
    from wipebot.core.report.meter import Bar, Meter, Then
    bars = [Bar("Mirelle", 54_000_000), Bar("Rowan", 27_000_000), Bar("Tamsin", 20_000_000), Bar("Corin", 5_000_000)]
    assert views.meter_headline(Meter("Damage taken", "Torvek pull 3", bars)) == {
        "text": "Mirelle is first with 54.00M, 2.3 times the median of the 4 players ranked.", "kind": ""}
    assert views.meter_headline(Meter("Damage done", "Torvek pull 3, damage dealers only", bars, least=True))[
        "text"] == "Corin is last with 5.00M, 21% of the median of the 4 players ranked."
    kicks = [Bar("Mirelle", 6), Bar("Rowan", 2), Bar("Tamsin", 1)]
    assert views.meter_headline(Meter("Kicks", "Torvek pull 3", kicks, "kicks"))["text"] == (
        "Mirelle is first with 6 kicks, 3.0 times the median of the 3 players ranked.")
    none = [Bar("Mirelle", 2), Bar("Rowan", 0), Bar("Tamsin", 0)]
    assert views.meter_headline(Meter("Dispels", "Torvek pull 3", none, "dispels"))["text"] == (
        "Mirelle is first of the 3 players ranked with 2 dispels.")
    then = Then("Mon 1 Jan", "Thu 4 Jan", 1_200_000, 1_500_000, 3, 5, 40, 50, 20)
    against = Meter("Slag Wave", "damage taken per minute in the pulls, 9 and 12 Torvek pulls, each until its wipe "
                    "was decided", bars, "per minute", then=then)
    assert views.meter_headline(against)["text"] == (
        "The raid's damage taken from Slag Wave per minute of its pulls: 1.50M on Mon 1 Jan, 1.20M on Thu 4 Jan "
        "(-20%).")


async def test_a_median_between_two_counts_is_written_as_the_headline_compares_with_it(recorded, monkeypatch, tmp_path):
    """Kicks of 6, 3, 2 and 1 have a median of 2.5, which the headline compares the first with ("2.4 times"). The
    page rounds a count, so its tile said "3" under that sentence: a median that falls between two counts comes
    written, and a whole one stays the page's."""
    from wipebot.core.report.meter import Bar, Meter
    case = CASES["interrupts"]
    kicks = Meter("Kicks", "Torvek pull 3", [Bar("Mirelle", 6), Bar("Rowan", 3), Bar("Tamsin", 2), Bar("Corin", 1)],
                  "kicks")

    async def ranked(rep, tool, args, other=None):
        return [(kicks, views.Stated())]
    monkeypatch.setattr(views, "_ranked", ranked)
    [m] = (await meter_data("interrupts"))["meters"]
    assert m["median"] == 2.5 and m["median_text"] == "2.5" and all("text" not in b for b in m["bars"])
    assert m["headline"]["text"] == "Mirelle is first with 6 kicks, 2.4 times the median of the 4 players ranked."
    [s] = await shots(tmp_path, case.tool, case.args, as_picture=True)
    assert not s.errors and "MEDIAN\n2.5\n" in s.text.upper()
    kicks.bars.pop()
    [m] = (await meter_data("interrupts"))["meters"]
    assert m["median"] == 3 and "median_text" not in m


async def test_the_meter_draws_a_panel_per_meter_without_errors(recorded, tmp_path):
    data = await meter_data("nekzali-enemies-night")
    [s] = await shots(tmp_path, "enemies", CASES["nekzali-enemies-night"].args)
    assert not s.errors, s.errors
    assert s.height > 100 and s.path.name == "discord-dark-680-inline.png"  # as the bot posts it: no card in a host
    for m in data["meters"]:
        assert m["title"] in s.text and m["bars"][0]["name"] in s.text
        assert f"TOTAL\n{m['stated']['total']}\nMEDIAN\n" in s.text.upper()  # what the kind took, and nothing under it


# ---- the trend: an amount pull by pull, from breakdown grouped by pull and mechanic_hits over every pull ----

async def trend_data(case: str, mark: str | None = None) -> dict:
    c = CASES[case]
    result = await server.view_data("trend", report=c.args["report"], tool=c.tool, player=mark,
                                    args={k: v for k, v in c.args.items() if k != "report"})
    assert result.structured_content is not None
    return result.structured_content


async def test_the_trend_view_matches_its_snapshot(recorded):
    assert_snapshot(REAL / "views" / "trend.txt", readable(await trend_data("mechanic-hits")))


async def test_a_trend_states_each_pulls_amount_as_breakdown_by_pull_does(recorded):
    """The trend reads breakdown's tables itself (tables' own helpers): each pull's total, its amount per second,
    its counted time and its top three are the ones the tool's line for that pull states."""
    case = CASES["damage-by-pull"]
    key = await server.view_key(case.tool, case.args)
    assert key == {"view": "trend", "report": case.args["report"], "boss": "Sszorak", "tool": "breakdown",
                   "args": {"data_type": "DamageDone", "group_by": "pull", "pulls": [12, 13]},
                   "label": "Damage done by pull, Sszorak pulls 12, 13, each until its wipe was decided"}
    data = await trend_data("damage-by-pull")
    text, failed = await server.call(case.tool, case.args)
    assert not failed and [r["pull"] for r in data["pulls"]] == [12, 13] and data["per"] == "second"
    for r in data["pulls"]:
        top = ", ".join(f"{name} {short(v)} ({short(v / r['seconds'])}/s)" for name, v in r["top"])
        line = (f"  Sszorak pull {r['pull']} ({clock(r['seconds'] * 1000)}): {short(r['amount'])} "
                f"({short(r['rate'])}/s); top {top}")
        assert line in text, line
    assert data["thirds"] is None and data["mine"] is None and data["who"] is None
    assert data["headline"] == {"kind": "", "text": "The raid's damage per second: " + ", ".join(
        f"{short(r['rate'])} on pull {r['pull']}" for r in data["pulls"]) + "."}


async def test_a_mechanics_trend_counts_the_raids_hits_per_pull_as_its_tool_does(recorded):
    """mechanic_hits over every pull states the raid's hits pull by pull; the trend has the same counts over the same
    pulls (the night's reset is none of them, as the tool's text says), over each pull's minutes until its wipe was
    decided, the medians of the night's thirds, and the asker's own hits in the pulls they were in."""
    data = await trend_data("mechanic-hits", mark="enfo")
    text, _ = await server.call("mechanic_hits", CASES["mechanic-hits"].args)
    per_pull = dict(re.findall(r"(?:: |; )(\d+): (\d+)(?: \(\+\d+\))?, ", text.split("The raid's per pull")[1]))
    assert len(per_pull) == len(data["pulls"]) == 32
    assert data["scope"] == "all 32 Sszorak pulls (1 reset left out), each until its wipe was decided"
    for r in data["pulls"]:
        assert r["amount"] == int(per_pull[str(r["pull"])]), r["pull"]
        assert r["rate"] == round(60 * r["amount"] / r["seconds"], 1)  # to the one decimal a count is written with
    assert data["count"] and data["per"] == "minute" and data["subject"] == "Tempest hits" and data["icon"]
    thirds = data["thirds"]
    assert not any(r["reset"] for r in data["pulls"]) and [len(t["pulls"]) for t in thirds] == [10, 12, 10]
    rates = {r["pull"]: r["rate"] for r in data["pulls"]}
    for t in thirds:
        found = sorted(rates[p] for p in t["pulls"])
        assert t["rate"] == found[len(found) // 2]
    a, b, c = (f"{t['rate']:.1f}" for t in thirds)
    assert data["headline"]["text"] == (f"The raid's Tempest hits per minute: {a} in the first third of the 32 "
                                        f"pulls, {b} in the middle and {c} in the last (each third's median pull).")
    assert data["mine"]["name"] == "Enfo" and len(data["mine"]["thirds"]) == 3
    own = [r["mine"] for r in data["pulls"]]
    assert any(v == 0 for v in own) and any(v for v in own)  # in the pull and never hit, hit
    sat_out = [r["mine"] for r in (await trend_data("mechanic-hits", mark="Miralupa"))["pulls"]]
    assert any(v is None for v in sat_out) and any(v for v in sat_out)  # not in every pull


async def test_the_askers_row_has_none_for_a_pull_they_were_in_and_no_row_for_a_pull_they_sat_out(recorded):
    """breakdown's table for a pull lists only the players with some of the amount, so a player in the pull without
    any had no row there, and the asker's row said "not in it" of a pull they were in (and left it out of their
    thirds): Miralupa cast no Flash Heal in pull 26, and Namsus, a healer, did no damage in pull 12 before its wipe
    was decided. Such a pull is theirs with none; only a pull they sat out has no amount. One with none of it all
    night, or not in the report, has no row at all."""
    rep = await server.lib().report(REPORT)
    in_pull = {rep.pull_no(f): {p.name for p in rep.pull_players(f).values()} for f in rep.by_boss["Sszorak"]}
    args = {"data_type": "Casts", "ability": "Flash Heal", "boss": "Sszorak", "group_by": "pull"}

    async def own(mark: str) -> dict:
        result = await server.view_data("trend", report=REPORT, tool="breakdown", args=args, player=mark)
        assert result.structured_content is not None
        return result.structured_content
    data = await own("Miralupa")
    for r in data["pulls"]:
        assert (r["mine"] is None) == ("Miralupa" not in in_pull[r["pull"]]), r["pull"]
    assert next(r for r in data["pulls"] if r["pull"] == 26)["mine"] == 0
    assert 26 in {n for t in data["mine"]["thirds"] for n in t["pulls"]}
    assert (await own("Enfo"))["mine"] is None and "Enfo" in in_pull[26]  # a shaman: no Flash Heal all night
    assert (await own("Nobodyhere"))["mine"] is None
    # the raid's own numbers are as they were: a player with none adds nothing and is not among a pull's top
    assert all(v for r in data["pulls"] for _, v in r["top"])
    two = await trend_data("damage-by-pull", mark="namsus")
    assert two["mine"]["name"] == "Namsus" and {"Namsus"} <= in_pull[12] & in_pull[13]
    assert [r["mine"] == 0 for r in two["pulls"]] == [True, False] and two["pulls"][1]["mine"] > 0


async def test_a_counts_rate_is_handed_on_to_the_one_decimal_its_headline_writes(recorded, monkeypatch):
    """A count per minute is written to one decimal. The page rounds what it is given, and was given two: 20.249
    hits a minute went as 20.25, which the page wrote as 20.3 on a pull whose headline said 20.2. An amount keeps
    two (1.53M: the page writes those too)."""
    rep = await server.lib().report(REPORT)
    fights = rep.by_boss["Sszorak"][:2]
    made = views.Trend("Slag Wave by pull", "Sszorak pulls 1, 2", "Slag Wave hits", "minute", True, fights,
                       [20.249, 7.0], [60.0, 60.0], [{"Enfo": 20.249}, {"Enfo": 7.0}])

    async def trend_of(rep, tool, args):
        return made
    monkeypatch.setattr(views, "trend_of", trend_of)
    data = (await server.view_data("trend", report=REPORT, tool="mechanic_hits", args={}, player="Enfo")
            ).structured_content
    assert data and [(r["rate"], r["mine"]) for r in data["pulls"]] == [(20.2, 20.2), (7.0, 7.0)]
    assert data["median"] == 20.2
    assert data["headline"]["text"] == "The raid's Slag Wave hits per minute: 20.2 on pull 1, 7.0 on pull 2."
    made.count, made.per, made.amounts = False, "second", [1_534_567.891 * 60, 7.0]
    data = (await server.view_data("trend", report=REPORT, tool="breakdown", args={})).structured_content
    assert data and data["pulls"][0]["rate"] == 1_534_567.89 and "1.53M on pull 1" in data["headline"]["text"]


def test_a_trends_thirds_leave_resets_out_and_need_six_counted_pulls():
    """As player_night's trend (coaching.night_trend): the pulls in thirds, the middle one taking the rest, each
    third's median the upper of two middle pulls, resets not counted, and no thirds for fewer than six. One
    function (tables.thirds) for the card and for breakdown by pull's text, and one wording of what it found."""
    rates = [10.0, 30.0, 0.0, 20.0, 40.0, 50.0, 70.0, 60.0]
    resets = [False, False, True, False, False, False, False, False]
    assert views.trend_thirds(rates, resets) == [([0, 1], 30.0), ([3, 4, 5], 40.0), ([6, 7], 70.0)]
    assert views.trend_thirds(rates[:6], resets[:6]) is None  # five counted
    assert views.trend_thirds(rates[:5] + [1.0, 2.0], resets[:5] + [False, False]) is not None
    assert views.trend_thirds is not tables.thirds and views.trend_thirds(rates, resets) == tables.thirds(rates, resets)
    assert tables.thirds_text(tables.thirds(rates, resets) or [], lambda rate: f"{rate:.1f}") == (
        "30.0 in the first third of the 7 pulls, 40.0 in the middle and 70.0 in the last (each third's median pull)")


async def test_breakdown_by_pull_states_the_thirds_medians_its_trends_card_does(recorded):
    """The card's headline gave the median pull of each third of the night, which its tool's text didn't have: a
    number on the card with nothing in the answer's text behind it. The text now states them under its heading,
    in the card's words, and says which pull the count leaves out. Two pulls have no thirds, nor has an aura."""
    args = {"data_type": "Casts", "ability": "Flash Heal", "boss": "Sszorak", "group_by": "pull"}
    text, failed = await server.call("breakdown", {"report": REPORT} | args)
    data = (await server.view_data("trend", report=REPORT, tool="breakdown", args=args)).structured_content
    assert not failed and data and len(data["pulls"]) == 33 and sum(r["reset"] for r in data["pulls"]) == 1
    said = data["headline"]["text"].removeprefix("The raid's Flash Heal casts per minute: ").removesuffix(".")
    a, b, c = (f"{t['rate']:.1f}" for t in data["thirds"])
    assert said == f"{a} in the first third of the 32 pulls, {b} in the middle and {c} in the last (each third's " \
                   "median pull)"
    lines = text.splitlines()
    assert lines[1] == f"  Per minute: {said}; 1 reset left out." and len(lines) == 2 + 33
    # each third's median is one of its own pulls' rates, as that pull's row writes it
    for third in data["thirds"]:
        assert any(f"  Sszorak pull {no} (" in line and f"({third['rate']:.1f}/min)" in line
                   for no in third["pulls"] for line in lines[2:])
    few, _ = await server.call(CASES["damage-by-pull"].tool, CASES["damage-by-pull"].args)
    aura, _ = await server.call(CASES["debuffs-by-pull"].tool, CASES["debuffs-by-pull"].args)
    assert "third" not in few + aura


async def test_only_an_amount_by_pull_is_a_trend(recorded):
    rep = await server.lib().report(REPORT)
    assert await views.trend_of(rep, "breakdown", {"data_type": "Debuffs", "group_by": "pull"}) is None
    assert await views.trend_of(rep, "breakdown", {"data_type": "DamageDone", "pulls": [13]}) is None  # by player
    assert await views.trend_of(rep, "breakdown", {"data_type": "DamageDone", "group_by": "pull",
                                                   "pulls": [13]}) is None  # one pull
    assert await views.trend_of(rep, "mechanic_hits", {"ability": "Tempest", "pull": 13}) is None
    with pytest.raises(server.FactError, match="states nothing pull by pull"):
        await server.view_data("trend", report=REPORT, tool="breakdown", args={"data_type": "DamageDone"})


async def test_a_ranking_over_several_pulls_names_the_call_whose_trend_shows_it_pull_by_pull(recorded):
    """The meter's second button: mechanic_hits over every pull as it was called; breakdown by player over several
    pulls grouped by pull instead (without `least`, which ranks players). None for one pull, or a table no trend
    draws."""
    assert (await meter_data("mechanic-hits"))["by_pull"] == {
        "tool": "mechanic_hits", "args": {"ability": "Tempest", "all_pulls": True}}
    assert "by_pull" not in await meter_data("damage-taken-ability")  # one pull
    assert "by_pull" not in await meter_data("damage-least")  # two pulls, one of which got to its window
    assert "by_pull" not in await meter_data("interrupts")  # kicks: no amount by pull
    rep = await server.lib().report(CASES["damage-by-pull"].args["report"])
    ranked = {"data_type": "DamageDone", "pulls": [12, 13], "least": True}
    assert await views._by_pull_call(rep, "breakdown", ranked) == {
        "tool": "breakdown", "args": {"data_type": "DamageDone", "pulls": [12, 13], "group_by": "pull"}}


async def test_the_whole_nights_healing_by_pull_is_a_trend_in_thirds(local):
    """A night of 33 pulls, the call the log shows most: the thirds' medians in the headline, and the asker's own
    healing pull by pull beside the raid's."""
    args = {"data_type": "Healing", "group_by": "pull", "whole_pull": True}
    result = await server.view_data("trend", report=REPORT, tool="breakdown", args=args, player="Enfo")
    data = result.structured_content
    assert data and len(data["pulls"]) == 33 and data["title"] == "Healing by pull" and data["scope"] == (
        "all 33 Sszorak pulls")
    a, b, c = (short(t["rate"]) for t in data["thirds"])
    assert data["headline"]["text"] == (f"The raid's healing per second: {a} in the first third of the 32 pulls, {b} "
                                        f"in the middle and {c} in the last (each third's median pull).")
    assert data["mine"]["name"] == "Enfo" and all(r["mine"] is not None for r in data["pulls"])
    text, _ = await server.call("breakdown", {"report": REPORT} | args)
    for r in data["pulls"]:
        assert f"({clock(r['seconds'] * 1000)}): {short(r['amount'])} ({short(r['rate'])}/s)" in text, r["pull"]
    assert text.splitlines()[1] == (f"  Per second: {a} in the first third of the 32 pulls, {b} in the middle and {c} "
                                    "in the last (each third's median pull); 1 reset left out.")


async def test_breakdown_gives_a_views_key_only_for_a_ranking_or_a_trend(recorded, monkeypatch):
    """breakdown and mechanic_hits carry a key only on the calls that rank players or state an amount pull by pull,
    and only for a client that shows MCP Apps: every other call is its text alone, and the page draws nothing."""
    monkeypatch.setattr(server, "_supports_apps", lambda ctx: True)
    monkeypatch.setattr(server, "_last_ranking", {})

    async def key_of(case: str) -> dict | None:
        result = await server.mcp.call_tool(CASES[case].tool, CASES[case].args)
        assert isinstance(result, CallToolResult) and not result.is_error
        return result.structured_content
    meter = await key_of("mechanic-hits")
    assert meter and meter["view"] == "meter" and "folded" not in meter
    assert meter["label"] == "Tempest, all 32 Sszorak pulls (1 reset left out), each until its wipe was decided"
    assert await key_of("damage-taken") is None and await key_of("mechanic-outliers") is None
    trend = await key_of("damage-by-pull")
    assert trend and trend["view"] == "trend" and trend["folded"] is True  # the second of the same answer
    monkeypatch.setattr(server.DEFAULT, "remote", None)  # in-process (the bot, `wipebot call`): the text alone
    assert await key_of("damage-by-pull") is None


async def test_a_rankings_arguments_from_the_page_are_held_to_its_tools_own(recorded, monkeypatch):
    """A meter's and a trend's data are asked for with the arguments their tool was called with, which the page
    sends back from its result's key. The page's tools are anyone's to call who is signed in, and those arguments go
    on to the tables' queries: one the tool itself would refuse (a list for a number, text for a list of pulls) is
    refused here in words, before any report is read. Text for the pulls once ranked pulls 1 and 3 for "13", and a
    list for the cutoff got as far as the query's wording."""
    async def no_report(code):
        raise AssertionError("read a report for arguments its tool would refuse")
    ask = {k: v for k, v in CASES["damage-taken-ability"].args.items() if k != "report"}  # Tempest on pull 13
    with monkeypatch.context() as stranger:
        stranger.setattr(server.lib(), "report", no_report)
        for bad in ({"cutoff": ["1]) }"]}, {"pulls": "13"}, {"start": [90]}, {"data_type": ["DamageTaken"]},
                    {"target": {"name": "x"}}):
            for view in ("meter", "trend"):
                with pytest.raises(FactError, match="not breakdown's arguments: " + next(iter(bad))):
                    await server.view_data(view, report=REPORT, tool="breakdown", args=ask | bad)
        with pytest.raises(FactError, match="not mechanic_hits' arguments: ability"):
            await server.view_data("trend", report=REPORT, tool="mechanic_hits", args={"ability": ["Tempest"]})
    # what the tool takes goes through as it would there: typed, a number for text as that text (the tool's own
    # call reads one so: Server.call_tool), and without what it doesn't have
    assert server._as_called("breakdown", ask | {"cutoff": "3", "nope": 1}) == ask | {"cutoff": 3}
    assert server._as_called("breakdown", ask | {"start": 90, "ability": 1287083.0}) == ask | {
        "start": "90", "ability": "1287083"}
    result = await server.view_data("meter", report=REPORT, tool="breakdown", args=ask | {"nope": 1})
    assert result.structured_content and result.structured_content["args"] == ask
    # the keys the server hands out are such arguments
    for case in ("mechanic-hits", "damage-by-pull", "damage-least", "interrupts"):
        key = await server.view_key(CASES[case].tool, CASES[case].args)
        assert key and server._as_called(key["tool"], key["args"]) == key["args"], case


async def test_an_ability_given_by_its_id_as_a_number_is_drawn_as_its_tool_ranked_it(recorded):
    """The tools print an ability's id as a number and a model gives it back as one, which the tool takes as its
    text (test_server). Its picture is drawn from the same arguments, as the model gave them: the key was an error
    ("'int' object has no attribute 'strip'"), so the bot's answer went without its meter, and a page asking with
    the host's copy of the arguments was told they aren't mechanic_hits'."""
    args = CASES["mechanic-hits"].args  # Tempest over the night; 1287083 is one of the ids the log has it under
    key = await server.view_key("mechanic_hits", args | {"ability": 1287083})
    assert key and key == await server.view_key("mechanic_hits", args | {"ability": "1287083"})
    assert key["args"] == {"ability": "1287083", "all_pulls": True} and key["label"].startswith("Tempest, all 32 ")
    by_id = (await server.view_data(**asked(key))).structured_content
    assert by_id and by_id["meters"] == (await meter_data("mechanic-hits"))["meters"]
    # the page's own call with the arguments as the host had them, for the meter and for its trend by pull
    for view, held in (("meter", "meters"), ("trend", "pulls")):
        page = await server.view_data(view, report=REPORT, tool="mechanic_hits",
                                      args={"ability": 1287083.0, "all_pulls": True})
        named = await server.view_data(view, report=REPORT, tool="mechanic_hits",
                                       args={"ability": "Tempest", "all_pulls": True})
        assert page.structured_content and named.structured_content
        assert page.structured_content[held] == named.structured_content[held], view


def test_only_the_first_ranking_of_an_answer_comes_open():
    """A host draws a card for every call of a tool with a view, and an answer ranks several things: the first
    ranking or trend handed to a user comes open, the ones within a minute of the last folded (one line that opens),
    and a minute later the next is open again. The other views are never folded."""
    server._last_ranking.clear()
    try:
        meter, trend = {"view": "meter", "label": "Healing"}, {"view": "trend", "label": "Healing by pull"}
        assert server._fold(dict(meter), now=100.0) == meter
        assert server._fold(dict(trend), now=130.0) == trend | {"folded": True}
        assert server._fold(dict(meter), now=185.0) == meter | {"folded": True}  # a minute from the last, not the first
        assert server._fold(dict(meter), now=185.0 + server.FOLD_S) == meter
        assert server._fold({"view": "pull"}, now=186.0 + server.FOLD_S) == {"view": "pull"}
    finally:
        server._last_ranking.clear()


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
    assert {s for r in personals["rows"] for p in r["cells"] for s in p} == {"answered", "none", "dead", "excused",
                                                                             "running"}


async def test_the_note_view_sets_a_defensive_already_running_apart_as_raid_note_does(recorded):
    """A defensive pressed before the callout's window and still up is not counted against a raider, and raid_note
    says so by name ("one already running 10"). The board drew it as the blue of "the note's cooldown for them",
    which it isn't: it has its own square, as many as the text counts."""
    data, (text, _) = await note_data("raid-note-night"), await server.call("raid_note", CASES["raid-note-night"].args)
    [personals] = data["callouts"]["callouts"]
    block = text.split('"Big Personals" to everyone', 1)[1].split("\n  \"", 1)[0]
    said = {n: int(k) for n, k in re.findall(rf"^    (\S+): .*one {notes.RUNNING} (\d+)", block, re.MULTILINE)}
    drawn_as = {r["player"]: k for r in personals["rows"]
                if (k := sum(s == "running" for p in r["cells"] for s in p))}
    assert said and drawn_as == said
    not_counted = {n: int(k) for n, k in re.findall(r"^    (\S+): .*not counted at (\d+) \(", block, re.MULTILINE)}
    assert {r["player"]: k for r in personals["rows"]  # with the note's plan for them and a tank's kept for after
            if (k := sum(s in ("running", "excused") for p in r["cells"] for s in p))} == not_counted


async def test_the_note_board_for_one_raider_is_their_part(recorded):
    """raid_note with `player` prints that raider's rows; its board drew every raider's all the same. It is their
    kick assignment with their count, their planned cooldowns, and their row of each callout that names them or
    everyone, with the pulls lost after a cast went through as the text counts them for scale."""
    case = CASES["raid-note-kicks-player"]
    data, (text, _) = await note_data("raid-note-kicks-player"), await server.call("raid_note", case.args)
    assert data["player"] == "Enfo" and data["title"] == "The Twin Fangs: Enfo's part of the raid note"
    k = data["kicks"]
    assert [s["player"] for s in k["slots"]] == ["Enfo"]
    assert {c["player"] for p in k["pulls"] for cell in p["cells"] for c in cell} == {"Enfo"}
    [(made, alive)] = re.findall(r"^  Enfo \(Kick[^)]*\): kicked (\d+) of (\d+)", text, re.MULTILINE)
    assert (k["made"], k["alive"]) == (int(made), int(alive))
    [(lost, counted, first)] = re.findall(r"went through: (\d+) of (\d+); Enfo's cast was the first to go through "
                                          r"on (\d+)", text)
    assert (k["lost"], k["counted"]) == (int(lost), int(counted))
    assert sum(f["count"] for f in k["first"]) == int(first) and all(f["name"] == "Enfo" for f in k["first"])
    assert {u["player"] for u in data["cooldowns"]["uses"]} == {"Enfo"}
    [uses] = re.findall(r"^  Enfo: (\d+) planned uses", text, re.MULTILINE)
    assert sum(bool(c) for p in data["cooldowns"]["pulls"] for c in p["cells"]) == int(uses)
    case = CASES["raid-note-night-player"]
    data, (text, _) = await note_data("raid-note-night-player"), await server.call("raid_note", case.args)
    assert data["kicks"] is None and data["cooldowns"] is None  # "the note plans none for Zebinomage"
    [personals] = data["callouts"]["callouts"]
    [row] = personals["rows"]
    [(alive, made)] = re.findall(r"^    Zebinomage: alive for (\d+); pressed at (\d+)", text, re.MULTILINE)
    assert (row["player"], row["alive"], row["made"]) == ("Zebinomage", int(alive), int(made))


async def test_the_note_boards_bands_are_the_note_checks_own_and_one_raiders_board_says_whose(recorded, tmp_path):
    """What counts as on time, as answering a callout and as a pull lost after a cast is said on the board in the
    note check's own numbers, not in copies of them, and a defensive already running has its word in the legend.
    One raider's board names them under each panel's title and counts their casts."""
    src = resources.files("wipebot.core").joinpath("app", "note.js").read_text(encoding="utf-8")
    assert "within 1 s" not in src and "3 s before to 5 s after" not in src and "within 10 s" not in src
    assert "const FEW = 3" not in src
    [s] = await shots(tmp_path / "night", "raid_note", CASES["raid-note-night"].args)
    assert not s.errors, s.errors
    # on time in the words of the text's own key: "within 3 s" took in a press 3.0 s off, which the text calls late
    text, _ = await server.call("raid_note", CASES["raid-note-night"].args)
    band = f"on time (under {notes.ON_TIME_S} s from the note's time)"
    assert band in text and band in s.text and f"a defensive {notes.RUNNING}" in s.text
    assert (f"pressed from {notes.CALL_BEFORE_MS // 1000} s before to {notes.CALL_AFTER_MS // 1000} s after the "
            "callout answers it") in s.text
    assert f"which fewer than {views.NOTE_FEW} pulls reached" in s.text
    [s] = await shots(tmp_path / "theirs", "raid_note", CASES["raid-note-kicks-player"].args)
    assert not s.errors, s.errors
    said = s.text.upper()
    assert "ENFO · THE TWIN FANGS · 39 PULLS" in said and "THEIRS WENT THROUGH FIRST\n0 OF 11" in said
    assert f"THE WIPE DECIDED WITHIN {notes.LOST_AFTER_MS // 1000} S, ANY PLAYER'S CAST" in said
    assert "WHOSE CAST WENT THROUGH FIRST" not in said and "KARADIM" not in said


async def test_the_note_boards_planned_cooldowns_have_the_texts_one_band(recorded):
    """The board kept a paler gold for a cooldown "early or late by 3 s at most" from when on time was under 1 s.
    raid_note's text has one band (on time under ON_TIME_S, early or late from there on) and so has the board: a
    square is on time, early or late, not cast, dead or absent, by the state the note check gave it."""
    src = resources.files("wipebot.core").joinpath("app", "note.js").read_text(encoding="utf-8")
    assert "NEAR_S" not in src and "at most" not in src
    data = await note_data("raid-note-night")
    cast = [c for p in data["cooldowns"]["pulls"] for c in p["cells"] if c and "off_s" in c]
    assert data["cooldowns"]["on_time_s"] == notes.ON_TIME_S
    # the state is the note check's, of the unrounded time: the tenth of a second the cell carries is at the band's
    # edge for six of the night's 537 presses (2.96 s is on time and reads 3.0), which the paler tier went by
    on, off = ([abs(c["off_s"]) for c in cast if (c["state"] == "on time") == yes] for yes in (True, False))
    assert max(on) <= notes.ON_TIME_S <= min(off) and len(on) == 279 and len(off) == 258
    assert (on + off).count(notes.ON_TIME_S) == 6
    from wipebot.core import viewhost
    panel, args = '[data-panel="cooldowns"]', CASES["raid-note-night"].args
    squares = f'{panel} svg rect[width="10"]'
    key = await viewhost.picture_key("raid_note", args)  # the board is a picture only the Discord bot posts
    assert key is not None
    board = {"content": [], "structuredContent": key}
    async with drawn("raid_note", args, result=board, picture=True) as (view, errors):
        keys = await view.evaluate(f"() => [...document.querySelectorAll('{panel} .legend span')]"
                                   ".map((s) => s.textContent)")
        fills = await view.evaluate(f"() => [...new Set([...document.querySelectorAll('{squares}')]"
                                    ".map((r) => r.getAttribute('fill')))]")
        assert not errors, errors
    assert keys == [f"on time (under {notes.ON_TIME_S} s from the note's time)", "early or late", "not cast, alive",
                    "dead by then"]
    assert set(fills) <= {"var(--green)", "var(--gold)", "var(--dead)", "var(--muted)", "var(--line)"}
    assert {"var(--green)", "var(--gold)"} <= set(fills)


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
    # asked for one raider (raid_note's player), their row alone under what everyone is told: it drew every raider's
    key = await server.view_key("raid_note", {"report": report, "boss": "Twin Fangs", "upcoming": True,
                                              "player": "enfo"})
    assert key is not None and key["args"]["player"] == "enfo"
    theirs = (await server.view_data(**key)).structured_content
    assert theirs is not None and theirs["player"] == "Enfo"
    assert theirs["title"] == "Twin Fangs: Enfo's part of the raid note"
    assert theirs["plan"]["rows"] == [r for r in rows if r["player"] == "Enfo"] != []
    assert theirs["plan"]["everyone"] == data["plan"]["everyone"]
    [s] = await shots(tmp_path / "theirs", "raid_note", key["args"] | {"report": report})
    assert not s.errors and "Twin Fangs: Enfo's part" in s.text and rows[0]["player"] not in s.text


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
    key = await server.view_key(case.tool, case.args)
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
        key = await server.view_key(case.tool, case.args)
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
    assert isinstance(first, TextContent) and first.text.startswith("Sszorak (Mythic) pull 13: ")
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
    # and in the page: the tools only some of whose calls have a view (it draws nothing for the others), and the
    # rest, whose view it can draw again from the tool's arguments
    main = resources.files("wipebot.core").joinpath("app", "main.js").read_text(encoding="utf-8")
    sometimes = re.search(r"const SOMETIMES = new Set\(\[(.*?)\]\)", main)
    always = re.search(r"const BY_TOOL = \{(.*?)\}", main)
    assert sometimes and always
    ranked = {tool for tool in keyed if server.VIEW_TOOLS[tool] == "meter"}
    assert set(re.findall(r'"(\w+)"', sometimes[1])) == ranked == {"breakdown", "mechanic_hits"}
    assert dict(re.findall(r'(\w+): "(\w+)"', always[1])) == {t: server.VIEW_TOOLS[t] for t in keyed - ranked}


async def test_the_mock_host_draws_what_the_server_sends(recorded, tmp_path):
    """A tool whose drawing only the Discord bot posts gets no key and no card in the mock host, as in Claude and
    ChatGPT (it once got both, and a meter shot there was published as Claude's): it is shot as the picture the bot
    posts, and named so. A ranking by breakdown or mechanic_hits is a card there now, as the server sends it."""
    from wipebot.core import viewhost
    adds = CASES["nekzali-enemies-night"]
    assert await server.view_key(adds.tool, adds.args)  # a drawing there is
    assert "structuredContent" not in await viewhost.tool_result(adds.tool, adds.args)
    assert (await viewhost.picture_key(adds.tool, adds.args) or {}).get("view") == "meter"
    assert (await viewhost.tool_result("pull_summary", PULL_CASE.args))["structuredContent"]["view"] == "pull"
    assert await viewhost.picture_key("pull_summary", PULL_CASE.args) is None
    meter = CASES["mechanic-hits"]
    assert (await viewhost.tool_result(meter.tool, meter.args))["structuredContent"]["view"] == "meter"
    assert await viewhost.picture_key(meter.tool, meter.args) is None
    [s] = await shots(tmp_path, adds.tool, adds.args, themes=("dark", "light"), widths=(320, 760))
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
    """A note's board, two raid mates and the healers' mana are drawn only as the Discord bot's pictures: in Claude
    and ChatGPT no tool's result names them and no view opens them. Their script, a fifth of the page's, was sent
    there all the same; it is in the Painter's page alone, and the address is the page those hosts load. The meter
    and the trend are in both: breakdown's and mechanic_hits' results name them."""
    from wipebot.core import viewhost
    hosted, painted = views.page(), views.page(pictures=True)

    def defined(page: str) -> set[str]:
        return set(re.findall(r"^views\.(\w+) = ", page, re.MULTILINE))
    # pull_map's result is its replay; breakdown grouped by pull is a trend (server.view_key)
    shown = {server.VIEW_TOOLS[tool] for tool in viewhost.SHOWN} | {"replay", "trend"}
    assert defined(hosted) == shown and set(re.findall(r'\bopen\("(\w+)"', hosted)) <= shown
    assert defined(painted) - defined(hosted) == {"note", "versus", "mana"}
    assert all(name in views.SCRIPTS for name in views.PICTURES_ONLY)
    assert len(hosted) < 0.87 * len(painted)
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
    as on a phone, where a finger is the pointer; `result` in `setup`: handed to the page in place of the tool's."""
    from wipebot.core import viewhost
    setup = host_setup(tool, args, setup.pop("result", None) or await viewhost.tool_result(tool, args), **setup)
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
    night["killers"][0]["pulls"] += [{"pull": 5, "ms": 150000, "place": 9, "saved": None, "why": views.NOT_REPLAYED},
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
    assert low == []
    # and it looked at what it says: a chip of each kind, and how many died at once written in three marks
    assert {t["cls"] for t in seen["player_night"] if t["cls"].startswith("chip")} == {
        "chip", "chip fix", "chip unknown", "chip outright", "chip fix sample", "chip unknown sample"}
    assert sum(t["cls"] == "tb" for t in seen["pull_summary"]) == 3


async def test_the_pull_view_draws_in_a_host_without_errors(recorded, tmp_path):
    data = await pull_data()
    taken = await shots(tmp_path, "pull_summary", PULL_CASE.args, themes=("dark", "light"), widths=(320, 760),
                        modes=("inline", "fullscreen"))
    assert len(taken) == 6
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 100 and s.path.stat().st_size > 10_000
        assert "Scopidruid 0:48" in s.text and f"BOSS\n{data['got']}\n" in s.text.upper()
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
        assert said["100%"]["y"] < said[data["health_end"]]["y"] < said["0%"]["y"]
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
    # each cooldown is counted as the text counts it: Enfo's two are held for a moment of the fight, so each is the
    # pulls that got there and on how many it was used, never uses of possible uses, and no total over them (the card
    # once said "37 of 64 uses" beside a list of 16/32 and 22/32, where the text has 16 of 18 and 18 of 18 pulls)
    line = next(x for x in text.splitlines() if x.startswith("Major cooldowns over the night"))
    assert len(data["cooldowns"]) == 2 and "cooldowns_total" not in data
    for c in data["cooldowns"]:
        said, _, more = c["said"].partition(" (")
        assert f"{said} (first pressed about then pull after pull" in line and (not more or f"; {more}" in line), c
        assert re.fullmatch(r"\d+ of \d+", c["count"]) and c["of"].startswith("pulls that got to ") and "/" not in said
    # and each pull's are the words of its row in the text: the times, or "unused" on a pull that got to the moment
    for r in data["pulls"]:
        row = next(x for x in text.splitlines() if x.startswith(f"  {r['pull']} ("))
        assert row.endswith("; cooldowns " + ", ".join(r["cooldowns"])) if r["cooldowns"] else "; cooldowns " not in row
    assert any("unused" in c for r in data["pulls"] for c in r["cooldowns"])
    # a reset's death is no death of the night's: not in the count, the killers' chips or the thirds
    resets = [r["pull"] for r in data["pulls"] if r["reset"] and r["death"]]
    chips = [x["pull"] for k in data["killers"] for x in k["pulls"]]
    assert resets == [9] and not set(resets) & set(chips) and "(1 reset left out" in text
    assert data["died"] == len(chips) == sum(t["died"] for t in data["thirds"])


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
    # the time lost is the "Time lost" line's own: its costs in its order, seconds and shares, its caveats left out
    line = next(x for x in text.splitlines() if x.startswith("Time lost"))
    assert re.findall(r"(?:: |, )(\D+?) \([^)]*\) (\d+) s \((\d+%)\)", line) == [
        (what, sec, share) for sec, what, share in lost["costs"]] and len(lost["costs"]) == 3
    head = re.search(r"^Time lost until ([\d:]+), when the wipe was decided, .* of those (\d+) s:", line)
    assert head
    until, window = head.groups()
    died = data["died"][0]
    assert f"Died: {clock(died['ms'])} to {died['by']} ({died['note']})" in text
    # alive 53 s of the pull: an opener is placed among no kills, on the card as in the text
    assert data["standing"] is None and "against kills: not compared" in text
    # the card leads with the death the text counts as theirs, in the text's words and numbers, and judges nothing
    assert died["note"] == "before the wipe was decided" and lost["costs"][0][1] == "dead"
    assert data["headline"] == {"kind": "", "text": f"Died at {clock(died['ms'])}, {died['note']} at {until}: dead "
                                f"for {lost['costs'][0][0]} of those {window} s ({lost['costs'][0][2]})."}
    assert "notes" not in data
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
    assert data["casts"] == []  # with the comparison its own list isn't drawn, and isn't sent
    both = {c["name"]: (c["per_min"], mine[c["name"]]) for c in (await player_data())["casts"] if c["name"] in mine}
    assert len(both) >= 8 and both["Mutilate"] == (27.1, 27.1) and all(a == b for a, b in both.values()), both
    # alive for 53 s: Ambush (3.1 a minute at the top's median) is a spell to have pressed, Sprint and Vanish aren't
    flags = {r["name"]: r["flag"] for r in data["top"]["rates"]}
    assert [n for n, flag in flags.items() if flag == "never cast"] == ["Ambush"]
    assert flags["Sprint"] == flags["Vanish"] == "not due: under one cast in their time alive at the top's median"
    assert all(f"{n}: " in top and f"<- {flag}" in top for n, flag in flags.items() if flag)


async def test_a_cooldowns_moment_on_the_card_is_the_moment_player_pull_says_it_came_back(local, monkeypatch):
    """On the recorded healer's pull, with their casts cut to the first use of each cooldown: player_pull says when
    each came back unused, and the card lists that moment for each as the text does. No cooldown leads the card,
    whenever it came back: the card weighs none, as the text weighs none. The healer's standing carries both of the
    text's caveats."""
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
    out = data["output"]
    assert "notes" not in data and data["headline"] == {"kind": "", "text": (
        f"{kit_short(out['per_s_alive'])} healing per second over the {data['alive_s']} s alive, rank {out['rank']} of "
        f"{out['of']} healers by total.")}
    assert f"{kit_short(out['per_s_alive'])}/s over the {data['alive_s']} s alive), rank {out['rank']} of" in text
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
        "lost": {"until_ms": 140000, "resource": "", "costs": [["4", "casting gaps", "3%"]]},
        "alive_s": 143, "alive_until_ms": 143000, "gaps": [[100000, 104000]], "capped": [], "dead": [[143000, 150000]],
        "died": [{"ms": 143000, "by": "Molten Fury", "note": "after the wipe was decided at 2:20: fallout",
                  "fallout": True, "mass": False}],
        "cooldowns": [{"name": "Ascendance", "uses": [60000], "possible": 1, "icon": "spells/114052.jpg"},
                      {"name": "Spirit Link Totem", "uses": [], "possible": 1, "icon": "spells/98008.jpg"}],
        "casts": [{"name": "Chain Heal", "n": 30, "per_min": 12.6}, {"name": "Riptide", "n": 25, "per_min": 10.5}],
        "phases": [{"ms": 0, "id": 1}],
        "tracked": [{"ms": 30000, "name": "Slag Wave"}, {"ms": 90000, "name": "Slag Wave"}],
        "top": {"hero": "Totemic", "same_hero": True, "parses": 5, "total": [40, 46],
                "rates": [{"name": "Riptide", "mine": 10.5, "median": 11.0, "low": 9.0, "high": 13.0, "flag": ""},
                          {"name": "Chain Heal", "mine": 12.6, "median": 17.0, "low": 15.0, "high": 19.0, "flag": ""},
                          {"name": "Astral Shift", "mine": 0.0, "median": 0.4, "low": 0.0, "high": 0.6,
                           "flag": "never cast"}]} if top else None,
    }
    return made | {"headline": views.player_headline(made, TimeLost(140000, True, 0.0, 4.0, 0.0, ""))}


def made_up_night() -> dict:
    """A player's night, made up: six pulls, a reset, two deaths that counted and one after the wipe was decided."""
    pulls = []
    for n, (pct, rate, death) in enumerate([(100.0, 150_000, None), (80.5, 280_000, (1, False)),
                                            (70.2, 300_000, None), (65.0, 310_000, (4, False)),
                                            (60.1, 320_000, (9, True)), (55.0, 330_000, None)], start=1):
        pulls.append({"pull": n, "kill": False, "reset": n == 1, "wipe_pct": pct, "got": f"{pct}%",
                      "got_wcl": f"{pct}%", "duration_ms": 60000 * n,
                      "role": "healer", "spec": "Restoration", "per_s_alive": rate, "rank": 2, "of": 4,
                      "death": {"place": death[0], "ms": 30000 * n, "by": "Slag Wave", "icon": "spells/1.jpg",
                                "fallout": death[1]} if death else None,
                      "dead_s": 5.0 if death and not death[1] else 0.0, "counted_s": 55.0 * n,
                      "cooldowns": ["Ascendance 1/2 at 0:30"]})
    return {"view": "player_night", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge",
            "title": "Mirelle on Torvek's Forge", "headline": views.player_night_headline(2, 6, 1),
            "player": {"name": "Mirelle", "spec": "Restoration", "class": "Shaman", "color": "#0070DD",
                       "role": "healer"},
            "what": "healing", "role_name": "healers", "pulls": pulls, "total_pulls": 6, "died": 2, "first": 1,
            "early": 1, "early_n": 3, "dead_s": 10, "counted_s": 1155, "dead_pct": 1,
            "best": {"pull": 6, "per_s_alive": 330_000},
            "killers": [{"by": "Slag Wave", "icon": "spells/1.jpg",
                         "pulls": [{"pull": 2, "ms": 60000, "place": 1, "saved": True},
                                   {"pull": 4, "ms": 120000, "place": 4, "saved": False}]}],
            "lived": {"saved": 1, "of": 2, "unknown": 0, "by": [["Astral Shift", 1]]},
            "cooldowns": [{"name": "Ascendance", "count": "4/6", "of": "uses of possible", "said": "Ascendance 4/6",
                           "icon": "spells/114052.jpg"}],
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
        assert "4 s casting gaps" in s.text and "after the wipe was decided" in s.text
    first, clicked = taken[0], taken[1]
    assert "Against top players" in first.text and "Astral Shift" not in first.text
    calls = [m["msg"]["params"]["arguments"] for m in clicked.messages if m["msg"].get("method") == "tools/call"
             and m["msg"]["params"]["name"] == "view_data"]
    assert [c.get("top") for c in calls] == [False, True]  # the comparison read only when asked for
    assert "Astral Shift" in clicked.text and "0.0 / 0.4" in clicked.text


async def test_the_comparison_on_the_card_marks_only_what_its_text_flags_in_the_texts_order(made_up):
    """compare_with_top flags a spell never cast, cast far fewer or far more, and refuses "far more" for one cast
    once in a short life. The card once ranked its rows by a difference of its own (Chain Heal, under the top's
    range, above Riptide) and drew such a dot in orange as "outside their range", a mark the text never makes: the
    flagged spells come first, the others in the text's order, in the player's colour."""
    key = {"view": "player", "report": "AbCdEfGh12345678", "pull": 4, "boss": "Torvek's Forge", "player": "Mirelle",
           "top": True}
    result = {"content": [{"type": "text", "text": "Mirelle against the top"}], "structuredContent": key}
    async with drawn("compare_with_top", {"player": "Mirelle"}, result=result) as (view, errors):
        words = await view.evaluate("() => [...document.querySelectorAll('svg text')].map((t) => t.textContent)")
        text = "|".join(words)
        marked = await view.evaluate("() => document.querySelectorAll('circle[fill=\"var(--decided)\"]').length")
    assert not errors and not marked and "outside their range" not in text
    assert text.index("Astral Shift") < text.index("Riptide") < text.index("Chain Heal")
    assert "flagged spells first" in text and "never cast or far off" in text and "biggest differences" not in text


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


async def test_a_death_on_a_reset_is_not_drawn_as_one_of_the_nights(made_up, monkeypatch):
    """A reset is no pull of the night's, and player_night counts no death on one: the table and the chart once
    drew it in red, as a death before the wipe was decided, beside a count that left it out."""
    from wipebot.core import viewhost
    night = made_up_night()
    assert night["pulls"][0]["reset"] and night["died"] == 2
    night["pulls"][0]["death"] = {"place": 2, "ms": 9000, "by": "Slag Wave", "icon": "spells/1.jpg", "fallout": False}
    made_up_view(monkeypatch, {"view": "player_night", "report": night["report"], "player": "Mirelle"}, night)
    async with drawn("player_night", {}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        rows = '() => [...document.querySelectorAll("tr.counted")].map((tr) => +tr.dataset.pull)'
        dots = "() => document.querySelectorAll('svg circle[fill=\"var(--dead)\"][opacity=\"1\"]').length"
        counted, red = await view.evaluate(rows), await view.evaluate(dots)
        tip = await view.evaluate("() => document.querySelector('tr[data-pull=\"1\"]').dataset.tip")
    assert not errors and counted == [2, 4] and red == 2 and tip == "Slag Wave, on a reset: not counted"


def made_up_view(monkeypatch, key: dict, data: dict) -> None:
    """One view drawn from made-up data: its key and its data answered without a report."""
    from wipebot.core import viewhost

    async def tool_result(tool, args):
        return {"content": [], "structuredContent": key}

    async def view_data(view, **asked):
        return CallToolResult(content=[], structured_content=data)
    monkeypatch.setattr(viewhost, "tool_result", tool_result)
    monkeypatch.setattr(server, "view_data", view_data)


def written(night: dict) -> dict:
    """A made-up night with every % written beside its number, as views.night_view hands them to the page."""
    for r in night["pulls"]:
        r |= {"pct_text": views.pct_text(r["pct"]), "health_text": views._pct(r["health"])}
    for b in night["bosses"]:
        b["best_text"] = views._pct(b["best"])
    if best := night.get("guild_best"):
        best |= {"pct_text": views.pct_text(best["pct"]), "health_text": views._pct(best["health"])}
    night["thirds_text"] = [f"{t:.1f}" for t in night["thirds"]] if night["thirds"] else None
    return night


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
    made_up_view(monkeypatch, {"view": "night", "report": night["report"], "boss": night["boss"]}, written(night))
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
        assert await view.evaluate(lead) == [["lead fix", "−A healthstone (if they had one; none used this pull), "
                                              "used in time, would have kept them alive."]]
        assert not errors
    thrown = CASES["death-recap-thrown"].args
    async with drawn("death_recap", thrown) as (view, errors):
        labels = await view.evaluate('() => [...document.querySelectorAll("svg text.td")].map((t) => t.textContent)')
        assert labels == ["no killing blow recorded"] and not errors
        assert await view.evaluate(lead) == [["lead mind", "!Died at 0:53, no killing blow recorded."]]
        # under it, the text's own line on where they were last logged and what that fits: without it the card
        # listed five defensives "ready, not used" for a player the log last saw 113 yd from the raid
        more = await view.locator(".lead-more").inner_text()
    text, failed = await server.call("death_recap", thrown)
    assert not failed and text.splitlines()[2] == more  # under what did the damage, where the verdict would stand
    assert "113 yd from the middle of the rest of the raid" in more and "thrown or knocked off" in more


async def test_a_night_says_a_death_with_no_killing_blow_as_the_log_does(monkeypatch):
    """A death the log names no killing blow for, on a player's night: its pull's bar and its chip say "died 2:00
    (#4), no killing blow recorded", never "died ... to no killing blow recorded"; a named killer is still "to" it."""
    night = made_up_night()
    night["pulls"][3]["death"] |= {"by": "no killing blow recorded", "no_blow": True, "icon": None}
    night["killers"] = [{"by": "Slag Wave", "icon": "spells/1.jpg",
                         "pulls": [{"pull": 2, "ms": 60000, "place": 1, "saved": True}]},
                        {"by": "no killing blow recorded", "no_blow": True, "icon": None,
                         "pulls": [{"pull": 4, "ms": 120000, "place": 4, "saved": None,
                                    "why": views.NOT_REPLAYED}]}]
    from wipebot.core import viewhost
    made_up_view(monkeypatch, {"view": "player_night", "report": night["report"], "player": "Mirelle"}, night)
    async with drawn("player_night", {}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        tip = lambda at: view.locator(at).get_attribute("data-tip")  # noqa: E731
        chips = [await tip(f'[data-pull-chip="{n}"]') for n in (2, 4)]
        bars = [await tip(f'[data-pull-col="{n}"]') for n in (2, 4)]
    assert not errors
    assert chips[0].startswith("Pull 2: died 1:00 (#1) to Slag Wave; ")
    assert chips[1] == f"Pull 4: died 2:00 (#4), no killing blow recorded; can't tell: {views.NOT_REPLAYED}"
    assert bars[0].split("\n")[1:3] == ["280k/s alive, rank 2 of 4 healers by total", "died 1:00 (#1) to Slag Wave"]
    assert "died 2:00 (#4), no killing blow recorded" in bars[1].split("\n")


async def test_a_nights_death_is_read_on_its_card_as_player_nights_text_reads_it(rep, monkeypatch):
    """Whether something ready would have kept them alive is one reading (deaths.lived, through coaching.night_lived)
    with three answers. The card had its own with two for a death that was replayed: one whose only ready spell
    "would have only if it held ... so can't be sure" was drawn as a death nothing ready would have stopped, beside
    a text that says it can't tell. Four of Draiman's deaths on the tests' night, with made-up replays."""
    from wipebot.core.report import coaching, deaths
    stone = deaths.Pressed("a healthstone", deaths.SAVED, True, "if they had one; none used this pull")
    short_of = deaths.Pressed("Barkskin", deaths.NOT_ENOUGH, False)
    unsure = deaths.Pressed("Cloak of Shadows", f"{deaths.SAVED} only if it held for all 5 s, so can't be sure", None)
    replays = [deaths.Replay("If pressed:", rows) for rows in ((unsure, stone), (short_of,), (unsure, short_of))]
    p = rep.player("Draiman")
    pulls, *_ = await coaching.night_pulls(rep, p, rep.by_boss["Sszorak"])
    died = [(x, x.death[1]) for x in pulls if x.counted and x.death]
    made = [deaths.Replayed(x.f, d, more, False)
            for (x, d), more in zip(died[:4], [*replays, deaths.Replay()], strict=True)]
    made.append(deaths.Replayed(died[4][0].f, died[4][1], deaths.Replay(), True))  # one that killed outright

    async def replay_deaths(*_):
        return made
    monkeypatch.setattr(views, "replay_deaths", replay_deaths)
    data = await views.player_night_view(rep, "Sszorak", "Draiman")
    chips = {x["pull"]: x for k in data["killers"] for x in k["pulls"]}
    would, wouldnt, cant, unlogged, outright = (chips[rep.pull_no(x.f)] for x, _ in died[:5])
    assert (would["saved"], wouldnt["saved"], outright["saved"]) == (True, False, False)
    assert "why" not in would and "why" not in wouldnt and outright["outright"] and not wouldnt["outright"]
    assert cant["saved"] is None and cant["why"] == deaths.UNTOLD  # was False: "nothing ready would have"
    assert unlogged["saved"] is None and unlogged["why"] == views.NOT_REPLAYED
    # and the counts over them are the text's own line
    lived, text = data["lived"], coaching._night_lived(rep, pulls, made)
    assert text and f"alive at {lived['saved']} of the {lived['of']} deaths that count" in text
    assert f"; can't tell at {lived['unknown']} ({views.NOT_REPLAYED} at {lived['unknown'] - 1}, " in text
    assert text.endswith(f"{deaths.UNTOLD} at 1).") and lived["by"] == [("a healthstone", 1)]
    assert [lived["saved"], lived["outright"], lived["of"]] == [1, 1, len(died)]


KEYS = """() => [...document.querySelectorAll(".note.keys .key")].map((k) =>
    [k.querySelector(".chip").textContent, k.querySelector(".chip").className, k.textContent])"""


async def test_the_night_cards_key_shows_a_pull_that_is_one_and_names_no_kind_the_night_lacks(monkeypatch):
    """The key under the chips once showed a chip "12" in the colour of a death a defensive would have stopped,
    whatever the night: on the recorded night pull 12 is a death the text can't tell for, and the key was drawn on
    a night with no such death at all. Its sample is the first pull that is one, of each kind the rows have."""
    def night_with(*chips: tuple[int, bool | None]) -> dict:
        night = made_up_night()
        night["killers"] = [{"by": "Slag Wave", "icon": "spells/1.jpg", "pulls": [
            {"pull": n, "ms": 60000, "place": 1, "saved": saved}
            | ({"why": views.NOT_REPLAYED} if saved is None else {}) for n, saved in chips]}]
        made_up_view(monkeypatch, {"view": "player_night", "report": night["report"], "player": "Mirelle"}, night)
        return night

    lived, cant = "a pull where a ready defensive or a healthstone would have kept them alive", "the log can't tell"
    night_with((2, False), (4, None), (5, True), (6, True), (7, None))
    async with drawn("player_night", {}) as (view, errors):
        assert await view.evaluate(KEYS) == [["5", "chip fix sample", f"5{lived}"],
                                             ["4", "chip unknown sample", f"4a pull where {cant}"]]
        tip = await view.locator('[data-pull-chip="4"]').get_attribute("data-tip")
        assert tip.endswith(f"; can't tell: {views.NOT_REPLAYED}") and not errors
    night_with((2, False), (4, None))  # no death a defensive would have stopped: that kind is not in the key
    async with drawn("player_night", {}) as (view, errors):
        assert await view.evaluate(KEYS) == [["4", "chip unknown sample", f"4a pull where {cant}"]] and not errors
    night_with((2, False), (4, False))  # neither kind: no key
    async with drawn("player_night", {}) as (view, errors):
        assert await view.evaluate(KEYS) == [] and await view.locator(".note.keys").count() == 0 and not errors


async def test_a_result_for_the_mock_host_leaves_the_in_process_tools_as_they_were(monkeypatch):
    """The mock host runs a tool as a server for many users does. It set that for the rest of the process, so every
    in-process call after it ran that way too: kept as a remote call, with a remote server's time for one."""
    from mcp.types import CallToolResult

    from wipebot.core import viewhost
    monkeypatch.setattr(server.DEFAULT, "remote", None)
    serving_many = []

    async def call_tool(tool, args):
        serving_many.append(server.DEFAULT.remote is not None)
        return CallToolResult(content=[], structured_content={"view": "pull"})
    monkeypatch.setattr(server.mcp, "call_tool", call_tool)
    assert (await viewhost.tool_result("pull_summary", {}))["structuredContent"] == {"view": "pull"}
    assert serving_many == [True] and server.DEFAULT.remote is None


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


def made_up_scene(*dots: maps.Dot) -> maps.Scene:
    """A map's scene on made-up positions (hundredths of a yard), in a room 1,000 yd across."""
    return maps.Scene("Made-up Boss · pull 1", "0:10", 1, (-50_000.0, 50_000.0, -50_000.0, 50_000.0), dots=list(dots))


def made_up_dot(name: str, x_yd: float, y_yd: float = 0, **more) -> maps.Dot:
    return maps.Dot(name, (200, 200, 200), "", "dps", x_yd * 100, y_yd * 100, **more)


def made_up_room() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 60, 40)).save(out, "PNG")
    return out.getvalue()


def test_a_map_far_wider_than_any_room_still_has_its_scale_bar():
    """The scale bar takes the first of its lengths that is long enough to read. With two living players 1,800 yd
    apart none was, and the drawing failed (StopIteration) though the answer's text was written: it now takes the
    longest."""
    from wipebot.core.report import draw
    apart = made_up_scene(made_up_dot("Near", 0), made_up_dot("Far", 1_800))
    picture = draw.render(apart, made_up_room(), {})
    assert picture[:4] == b"RIFF" and picture[8:12] == b"WEBP"
    close = made_up_scene(made_up_dot("Near", 0), made_up_dot("Far", 30))  # an ordinary room: as before
    assert draw.render(close, made_up_room(), {})[:4] == b"RIFF"


async def test_a_map_is_drawn_without_the_room_when_its_image_cannot_be_read(monkeypatch):
    """A spec's icon that can't be read leaves a plain marker; the room's map image failed the whole tool, text and
    all. The positions are now drawn on a plain ground, and the picture's title says the room could not be read."""
    from wipebot.core import knowledge
    from wipebot.core.report import draw

    async def unreadable(*_):
        raise knowledge.KnowledgeError("Warcraft Logs' images did not answer")
    monkeypatch.setattr(knowledge, "map_image", unreadable)
    monkeypatch.setattr(knowledge, "wcl_image", unreadable)
    titles = []
    text = draw._text
    monkeypatch.setattr(draw, "_text", lambda d, xy, words, *rest: (titles.append(words), text(d, xy, words, *rest)))
    picture = await maps.drawn(made_up_scene(made_up_dot("Near", 0), made_up_dot("Far", 30)))
    assert picture[:4] == b"RIFF" and picture[8:12] == b"WEBP"
    assert "Made-up Boss · pull 1 · the room's map could not be read" in titles


def test_one_player_far_from_the_raid_does_not_shrink_the_map_to_a_dot():
    """The view was framed on everyone alive, however far apart, so one player a long way off (walked out, on
    another floor) left the raid a dot. A view is now no wider than draw.MAX_VIEW_YD for the living: past that,
    those farthest from the middle of the rest are left out of the frame and named in the text as off the map's
    edge. One asked about is always framed, and a player thrown 100 yd, as on the recorded pull, still is."""
    from types import SimpleNamespace

    from wipebot.core.report import draw
    raid = [made_up_dot(f"R{i}", 5 * (i % 4), 5 * (i // 4)) for i in range(12)]  # within 15 yd of each other
    alone = made_up_scene(*raid)
    gone = made_up_scene(*raid, made_up_dot("Stray", 900))
    assert draw.strays(gone) == {"Stray"} and draw.strays(alone) == set()
    assert draw._view(gone) == draw._view(alone) and not draw.in_view(gone, 90_000, 0)
    width_yd = lambda sc: draw.W * draw.SS / draw._view(sc)[2] / 100  # noqa: E731
    assert width_yd(gone) == draw.MIN_VIEW_YD
    nobody = cast("maps.Report", SimpleNamespace(pull_players=lambda f: {}))
    assert maps._around(nobody, {}, gone)[-1] == "Off the map's edge (alive, far from the rest of the raid): Stray"
    assert not any("Off the map" in line for line in maps._around(nobody, {}, alone))
    asked = made_up_scene(*raid, made_up_dot("Stray", 900, focus=True))  # the one asked about: in the frame
    assert draw.strays(asked) == set() and width_yd(asked) > 900 and draw.in_view(asked, 90_000, 0)
    two = made_up_scene(*raid, made_up_dot("Left", 900), made_up_dot("Right", -700))  # both, the farthest first
    assert draw.strays(two) == {"Left", "Right"} and draw._view(two) == draw._view(alone)
    thrown = made_up_scene(*raid, made_up_dot("Thrown", 108))  # far, and the view still shows the raid: framed
    assert draw.strays(thrown) == set() and 108 < width_yd(thrown) < draw.MAX_VIEW_YD
    assert draw.in_view(thrown, 10_800, 0)


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


def test_the_players_card_has_no_judge_of_its_own():
    """The card once picked "what went well" and "to fix" by thresholds no tool's text has (gaps over 5% of the
    time, a cap 10% beyond top players', a parse over the 75th percentile), so it could fault what the answer above
    it did not. It states player_pull's facts and weighs none."""
    for gone in ("player_notes", "GAPS_SHARE", "STEADY_SHARE", "CAPPED_SHARE", "NOTES"):
        assert not hasattr(views, gone), gone
    page = resources.files("wipebot.core").joinpath("app/player.js").read_text(encoding="utf-8")
    assert "D.notes" not in page and "outside their range" not in page


def test_a_nights_cooldown_on_the_card_is_counted_as_player_nights_text_counts_it():
    """One held for a moment of the fight has no uses against possible uses in the text: the card once gave it as
    16/33 under the answer's "used on 16 of the 19 pulls that got to 1:11"."""
    from collections import Counter

    from wipebot.core.report import coaching

    class Rep:  # the text names a cooldown's latest pull
        def pull_no(self, f):
            return f["no"]
    used = {"Spirit Link Totem": 16, "Ascendance": 22, "Deathmark": 20, "Signet": 5, "Vanish": 0}
    cds = coaching.NightCooldowns(
        used=Counter(used),
        possible=Counter({"Spirit Link Totem": 33, "Ascendance": 33, "Deathmark": 31, "Vanish": 30}),
        held={"Spirit Link Totem": coaching.Held(71_000, 16, 19), "Ascendance": coaching.Held(67_000, 18, 18)},
        unknown={"Signet"})
    for name in used:
        cds.first_use[name] = [({"no": 22}, 77_000.0)]
    card = {n: views._night_cooldown(cds, n) for n in used}
    assert card["Spirit Link Totem"] == {
        "name": "Spirit Link Totem", "count": "16 of 19", "of": "pulls that got to 1:11",
        "said": "Spirit Link Totem used on 16 of the 19 pulls that got to 1:11"}
    assert card["Ascendance"]["of"] == "pulls that got to 1:07; 22 uses over the night"
    assert card["Ascendance"]["said"] == (
        "Ascendance used on 18 of the 18 pulls that got to 1:07 (22 uses over the night)")
    assert (card["Deathmark"]["count"], card["Deathmark"]["of"]) == ("20/31", "uses of possible")
    assert (card["Signet"]["count"], card["Signet"]["of"]) == ("5", "uses")
    assert card["Vanish"] == {"name": "Vanish", "count": "0", "of": "never cast on the night",
                              "said": "Vanish never cast"}
    for name in ("Spirit Link Totem", "Ascendance", "Deathmark", "Signet"):  # the text's own words for each
        text = coaching._cooldown_text(cast("coaching.Report", Rep()), cds, name)
        said, _, more = card[name]["said"].partition(" (")
        assert text.startswith(f"{said} (") and (not more or f"; {more}" in text), text


def _died(ms: int, by: str = "Toxic Fumes", note: str = "", fallout: bool = False, mass: bool = False) -> dict:
    return {"ms": ms, "by": by, "note": note, "fallout": fallout, "mass": mass}


def test_a_players_headline_is_the_death_the_text_counts_as_theirs_else_their_output():
    """ "Before the wipe was decided" is said only where player_pull's "Died" line says it: the card once said it of
    a death on a kill, and of one on a wipe that was never decided."""
    out = {"output": {"what": "damage", "rank": 4, "of": 14, "role": "damage dealers", "per_s_alive": 248_000},
           "alive_s": 160, "duration_ms": 300_000}
    said = lambda died, lost: views.player_headline(out | {"died": died}, lost)["text"]  # noqa: E731
    wipe = TimeLost(180_000, True, 85.0, 20.0, 40.0, "Energy")
    assert said([_died(95_000, note="before the wipe was decided")], wipe) == (
        "Died at 1:35 to Toxic Fumes, before the wipe was decided at 3:00: dead for 85 of those 180 s (47%).")
    kill = TimeLost(300_000, False, 39.0, 3.0, 0.0, "Energy")
    assert said([_died(261_000, "Mutilate")], kill) == (
        "Died at 4:21 to Mutilate: dead for 39 of the pull's 300 s (13%).")
    assert said([_died(299_500, "Mutilate")], TimeLost(300_000, False, 0.5, 3.0, 0.0, "Energy")) == (
        "Died at 4:59 to Mutilate.")
    assert said([_died(95_000, views.NO_KILLING_BLOW, "before the wipe was decided")], wipe).startswith(
        "Died at 1:35, before the wipe was decided at 3:00: ")
    # fallout and a hit killing many aren't theirs: neither leads, and no gap, cap or cooldown does in their place
    plain = "248.00k damage per second over the 160 s alive, rank 4 of 14 damage dealers by total."
    assert said([_died(185_000, note="after the wipe was decided at 3:00: fallout", fallout=True)], wipe) == plain
    assert said([_died(95_000, "Visceral Burst", "one of 4 killed at once from 1:35, a hit killing many: the raid's "
                       "to stop, not counted as theirs", mass=True)], wipe) == plain
    assert said([], wipe) == plain
    assert views.player_headline({"died": [], "output": None, "duration_ms": 45_000}, wipe)["text"] == (
        "No output logged in 0:45.")
    assert all(views.player_headline(out | {"died": d}, wipe)["kind"] == "" for d in ([], [_died(95_000)]))


async def test_a_death_on_a_recorded_kill_is_not_said_to_be_before_a_wipe_was_decided(recorded):
    """Nymrissa's kill, on which two players died: player_pull's "Died" line notes nothing of a death on a kill, so
    the card's deaths carry no note and its headline speaks of the pull, not of a wipe."""
    rep = await server.lib().report("GM8XdBHaqpFgxK9Q")
    f = next(f for f in rep.by_boss["Nymrissa Wavecaller"] if f["kill"])
    deaths = await rep.deaths(f)
    assert len(deaths) == 2
    for d in deaths:
        own = [d]
        died = await views._died(rep, f, own)
        assert died == [{"ms": round(d["timestamp"] - f["startTime"]), "by": rep.ability(d["killingAbilityGameID"]),
                         "note": "", "fallout": False, "mass": False}]
        lost = time_lost(f, own, [], [], "", [], await rep.decided_at(f))  # as player_pull counts the time dead
        head = views.player_headline({"died": died, "output": None, "duration_ms": 0}, lost)["text"]
        length = int((f["endTime"] - f["startTime"]) / 1000)
        assert head == (f"Died at {clock(died[0]['ms'])} to {died[0]['by']}: dead for {lost.dead_s:.0f} of the pull's "
                        f"{length} s ({100 * lost.dead_s * 1000 / (f['endTime'] - f['startTime']):.0f}%).")
        assert "wipe" not in head and not lost.decided


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


def test_a_cooldown_came_back_unused_only_while_alive_and_before_the_wipe_was_decided():
    from wipebot.core.report import cooldowns
    f = {"startTime": 1_000_000, "endTime": 1_180_000}
    deathmark, end = {"name": "Deathmark", "cooldown": 120.0}, 1_180_000
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, end, end) == (127000.0, True)
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, 1_053_000, end) == (127000.0, False)  # died before it
    assert cooldowns.came_back(f, deathmark, [7000.0], 2, end, 1_120_000) is None  # after the wipe was decided
    assert cooldowns.came_back(f, deathmark, [], 1, end, end) is None  # never used: nothing came back
    assert cooldowns.came_back(f, deathmark, [7000.0, 130000.0], 2, end, end) is None  # as often as it could be


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
    """A death after the wipe was decided, or one of a hit killing many, is nobody's to fix, whatever was ready:
    the headline says which, in death_recap's own words for it; what killed outright is no defensive's to stop;
    before the wipe was decided, what was ready and would have kept them alive is what to do differently, a
    healthstone with what the replay takes as given."""
    from wipebot.core.report.deaths import NOT_ENOUGH, SAVED, Pressed, Replay, Standing
    short = Pressed("Barkskin", NOT_ENOUGH, False, effect="20% less damage for 8 s", how="30k less of the 150k")
    stone = Pressed("a healthstone", SAVED, True, "if they had one; none used this pull", "heals 25%", "heals 120k")
    shift = Pressed("Astral Shift", SAVED, True, effect="40% less damage for 12 s", how="60k less of the 150k")
    theirs = Standing("before the wipe was decided")
    said = lambda *facts: views.death_headline("Slag Wave", 121_000, *facts)  # noqa: E731
    assert said(False, 64, Replay("If pressed:", (short, stone)), theirs) == {"kind": "fix", "text": (
        "A healthstone (if they had one; none used this pull), used in time, would have kept them alive.")}
    assert said(False, 64, Replay("If pressed:", (shift, stone)), theirs)["text"] == (
        "Astral Shift or a healthstone (if they had one; none used this pull), used in time, would have kept them "
        "alive.")
    assert said(False, 64, Replay("If pressed:", (shift,)), Standing("")) == {  # on a kill: no note, the same fix
        "text": "Astral Shift, used in time, would have kept them alive.", "kind": "fix"}
    late = Standing("after the wipe was decided at 1:50: fallout", fallout=True)
    assert said(False, 64, Replay(), late) == {
        "text": "Killed by Slag Wave at 2:01, after the wipe was decided at 1:50: fallout.", "kind": ""}
    burst = Standing("one of 5 killed at once from 2:00, a hit killing many: the raid's to stop, not counted as "
                     "theirs", mass=MassDeath(120_000, "Slag Wave", (1,), 5, 1))
    for outright in (False, True):  # the hit that wiped the raid leads, however it killed this one of them
        assert said(outright, 97, Replay(), burst) == {"kind": "", "text": (
            "Killed by Slag Wave at 2:01, one of 5 killed at once from 2:00, a hit killing many: the raid's to stop, "
            "not counted as theirs.")}
    assert said(True, 97, Replay(), theirs) == {
        "text": "Slag Wave killed them outright from 97% health: it wasn't damage that outpaced their healing.",
        "kind": "mind"}
    assert said(False, 64, Replay("If pressed:", (short,)), theirs)["text"] == (
        "Killed by Slag Wave at 2:01: nothing ready would have been enough on its own.")
    assert said(False, 64, Replay(), theirs) == {"text": "Killed by Slag Wave at 2:01.", "kind": ""}  # can't tell
    nowhere = views.death_headline(views.NO_KILLING_BLOW, 53_000, False, None, Replay(), theirs)
    assert nowhere == {"text": "Died at 0:53, no killing blow recorded.", "kind": ""}
    moved = "Last logged position at 0:51: 113 yd from the middle of the rest of the raid."
    assert views.death_headline(views.NO_KILLING_BLOW, 53_000, False, None, Replay(), theirs, moved) == {
        "text": "Died at 0:53, no killing blow recorded.", "kind": "mind", "more": moved}


async def test_a_death_after_the_wipe_was_decided_says_so_once_on_its_card(recorded, monkeypatch):
    """A fallout death's card says what it was in its headline, with when the wipe was decided. The line under the
    player's name said "after the wipe was decided" again."""
    data = await death_data()
    note = "after the wipe was decided at 0:40: fallout"
    late = data | {"fallout": True, "note": note, "saved_by": [],
                   "headline": {"text": f"Killed by {data['by']} at {clock(data['at_ms'])}, {note}.", "kind": ""}}
    key = await server.view_key("death_recap", DEATH_CASE.args)
    assert key is not None
    made_up_view(monkeypatch, key, late)
    for width in (360, 720):
        async with drawn("death_recap", {}, width=width) as (view, errors):
            said = await view.locator("body").inner_text()
        assert not errors, errors
        assert said.count("after the wipe was decided") == 1 and f"died {clock(data['at_ms'])}" in said, width


async def test_a_save_the_replay_cant_be_sure_of_is_not_counted_as_one(monkeypatch):
    """Cloak of Shadows' tooltip promises its immunity for a moment. Where it covers the lethal burst only if it
    held for all its seconds, the replay's row says "would have kept them alive only if it held ... can't be
    sure": it was counted as a save all the same, by those first words, in the card's headline and in every count
    of deaths a ready defensive would have saved. Made-up hits: 500 inside the sure second, 500 after it, 600
    overkill to make up."""
    from types import SimpleNamespace

    from wipebot.core import knowledge
    from wipebot.core.knowledge.spells import Effect
    from wipebot.core.report import deaths

    cloak = {"name": "Cloak of Shadows", "spell_id": 31224, "cooldown": 120}

    class Rep:
        async def defensive_casts(self):  # pressed on another pull: a spell they have, ready on this one
            return [{"fight": 2, "sourceID": 7, "timestamp": 500_000, "abilityGameID": 31224}]

        async def defensive_spells(self, p):
            return [cloak]

        def is_spell(self, c, sp):
            return c["abilityGameID"] == sp["spell_id"]

        async def potions(self, f, p, until):  # a healthstone used: no row for one
            return 1, 0

        async def schools(self):
            return {9: 32}

    async def effect(spell_id):
        return Effect("immunity", only="magic", duration_s=5, sure_s=sure_s)
    monkeypatch.setattr(knowledge, "defensive_effect", effect)
    hit = lambda at, amount, left, **more: {"timestamp": at, "abilityGameID": 9, "amount": amount,  # noqa: E731
                                            "hitPoints": left, "maxHitPoints": 1000, "resourceActor": 2} | more
    hits = [hit(10_000, 100, 900), hit(12_500, 500, 400), hit(14_000, 500, 0, overkill=600)]
    replay = lambda: deaths.if_pressed(cast("deaths.Report", Rep()), {"id": 1, "startTime": 0},  # noqa: E731
                                       cast("deaths.Player", SimpleNamespace(id=7)), {"timestamp": 14_000}, hits)
    sure_s = 1
    unsure = await replay()
    assert unsure.lines()[1] == (
        "  Cloak of Shadows (immune to magic damage for 5 s): 1.00k less of the 1.00k it covers: would have kept them "
        "alive only if it held for all 5 s: its tooltip promises the immunity for a moment, so can't be sure")
    assert unsure.rows[0].saves is None and deaths.savers(unsure) == []
    theirs = deaths.Standing("before the wipe was decided")
    assert views.death_headline("Slag Wave", 14_000, False, 0, unsure, theirs) == {
        "text": "Killed by Slag Wave at 0:14.", "kind": ""}  # neither a fix nor "nothing would have been enough"
    sure_s = None  # an immunity that holds for all its seconds, by its tooltip: a save
    sure = await replay()
    assert sure.lines()[1].endswith(": would have kept them alive") and deaths.savers(sure) == ["Cloak of Shadows"]
    assert views.death_headline("Slag Wave", 14_000, False, 0, sure, theirs)["kind"] == "fix"


async def test_a_deaths_headline_reads_the_replays_own_rows(recorded):
    """The headline reads a death's replay by its rows (deaths.if_pressed), so it is checked on recorded deaths: one
    where the only thing ready, a healthstone, wasn't enough, one where Barkskin wasn't and a healthstone was, and
    one of a hit killing many, which is not replayed as theirs whatever was ready."""
    from wipebot.core.report import deaths
    rep = await server.lib().report(PULL_CASE.args["report"])
    druid = rep.player("Scopidruid")
    replayed = {rep.pull_no(r.f): r for r in await deaths.replay_deaths(rep, druid, rep.by_boss["Sszorak"])}

    async def said(pull: int) -> dict:
        r = replayed[pull]
        was = await deaths.standing(rep, r.f, r.d)
        by = rep.ability(k) if (k := r.d.get("killingAbilityGameID")) else views.NO_KILLING_BLOW
        return views.death_headline(by, r.d["timestamp"] - r.f["startTime"], r.outright, None,
                                    r.more if was.theirs else deaths.Replay(), was)
    assert [(row.saves, row.verdict) for row in replayed[29].more.rows] == [(False, deaths.NOT_ENOUGH)]
    assert await said(29) == {"text": "Killed by Mutilate at 1:11: nothing ready would have been enough on its own.",
                              "kind": ""}
    assert [(row.name, row.saves) for row in replayed[32].more.rows] == [("Barkskin", False), ("a healthstone", True)]
    assert [line.rsplit(": ", 1)[1] for line in replayed[32].more.lines()[1:]] == [deaths.NOT_ENOUGH, deaths.SAVED]
    assert await said(32) == {"kind": "fix", "text": (
        "A healthstone (if they had one; none used this pull), used in time, would have kept them alive.")}
    # pull 10: Virulence killed four at once from 0:30. Barkskin and a healthstone were ready and the arithmetic
    # says either would have kept them alive, so the card once told them to press one
    assert deaths.savers(replayed[10].more) == ["Barkskin", "a healthstone"]
    assert await said(10) == {"kind": "", "text": (
        "Killed by Virulence at 0:30, one of 4 killed at once from 0:30, a hit killing many: the raid's to stop, not "
        "counted as theirs.")}
    # and it is the text's verdict line (deaths._verdict) for every recorded death that was theirs: the same things
    # named with what they take as given, "nothing would have" of the same deaths, and neither where the text has none
    kinds = {"fix": 0, "nothing": 0, "neither": 0}
    for pull, r in replayed.items():
        if r.mass:
            continue
        line, head = "".join(deaths._verdict(r.more)), await said(pull)
        if head["kind"] == "fix":
            named = line.removeprefix("Would have kept them alive, if pressed (replayed below; secondary to what "
                                      "killed them): ").removesuffix(".").split(", ")
            ready = " or ".join(named)
            assert head["text"] == f"{ready[0].upper()}{ready[1:]}, used in time, would have kept them alive."
        elif head["text"].endswith(": nothing ready would have been enough on its own."):
            assert line == "Nothing that was ready would have kept them alive on its own (replayed below)."
        else:
            assert not line or line.startswith("Nothing that was ready is sure to have kept them alive")
        kinds["fix" if head["kind"] == "fix" else "nothing" if "nothing ready" in head["text"] else "neither"] += 1
    assert kinds["fix"] and kinds["nothing"] and sum(kinds.values()) == sum(not r.mass for r in replayed.values())


async def test_death_recap_notes_a_death_as_player_pull_does_and_replays_only_their_own(recorded, monkeypatch):
    """What a death was to its wipe is one reading: death_recap's first line has the note player_pull's "Died" line
    has, for every death of the recorded night. A death after the wipe was decided or in a hit killing many gets no
    "If pressed" replay in the text, a line saying why instead, and no "would have kept them alive" on its card."""
    from wipebot.core.report import coaching, deaths
    rep = await server.lib().report(PULL_CASE.args["report"])
    fights = rep.by_boss["Sszorak"]
    await rep.decided_cuts(fights)
    kinds = {"before": 0, "fallout": 0, "mass": 0}
    for f in fights:
        for d in await rep.deaths(f):
            was = await deaths.standing(rep, f, d)
            assert was.note == await coaching.death_note(rep, f, d)
            assert was.theirs == (not was.fallout and was.mass is None)
            kinds["fallout" if was.fallout else "mass" if was.mass else "before"] += 1
    assert kinds == {"before": 183, "fallout": 310, "mass": 129}  # every kind is there to compare
    # the recorded death, as if it had been each kind: only what the death was to its wipe is made up
    f = rep.fight(DEATH_CASE.args["pull"])
    hit = MassDeath(47_000, "Ula'tek's Presence", (1285965,), 4, 2)
    for was, why in ((deaths.Standing("after the wipe was decided at 0:40: fallout", fallout=True),
                      deaths.NOT_REPLAYED_FALLOUT),
                     (deaths.Standing("one of 4 killed at once from 0:47, a hit killing many: the raid's to stop, "
                                      "not counted as theirs", mass=hit), deaths.NOT_REPLAYED_MASS)):
        async def made_up(rep, f, death, was=was):
            return was
        monkeypatch.setattr(deaths, "standing", made_up)
        monkeypatch.setattr(views, "standing", made_up)
        text, failed = await server.call("death_recap", DEATH_CASE.args)
        assert not failed and f"({rep.url(f)}), {was.note}. Killing blow: " in text.splitlines()[0]
        # the line stands where the replay's verdict would, under what did the damage, and nothing is replayed below
        assert text.splitlines()[1].startswith("Damage in the 10 s before death, by ability: ")
        assert text.splitlines()[2] == why and text.count(deaths.NOT_REPLAYED) == 1
        assert "If pressed" not in text and deaths.SAVED not in text and "replayed below" not in text
        data = await death_data()
        assert data["headline"] == {"text": f"Killed by Ula'tek's Presence at 0:48, {was.note}.", "kind": ""}
        assert data["saved_by"] == [] and data["note"] == was.note
        assert data["fallout"] == was.fallout
        assert data["mass"] == ("one of 4 killed at once from 0:47" if was.mass else None)


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


def made_up_nights(*bests: tuple, kill: int | None = None) -> list:
    """Raid nights on a boss a week apart from Thu 9 Oct 2025, each with its best wipe ((WCL %, pull) or with the
    phase and the boss's health in it too; None for a night of resets); the last one killed it on pull `kill`."""
    from wipebot.core.report.night import BossNight
    week = 7 * 86400 * 1000
    return [BossNight(1_760_040_000_000 + i * week, 12, kill if i == len(bests) - 1 else None,
                      (best[0], best[1]) if best else None, (), f"night{i}", *(best[2:] if best else ()))
            for i, best in enumerate(bests)]


def test_night_by_night_the_headline_is_the_kill_else_the_furthest_wipe_in_the_texts_words():
    """progress's weeks as a card once led with nothing. Its sentence names the best wipe as the text does, by its
    phase and the boss's health in it on a boss counted by phase, and says "further than any night before" of the
    night the text says it of."""
    said = lambda *bests, **more: views.weeks_headline(made_up_nights(*bests, **more))  # noqa: E731
    assert said((40.2, 7), (34.1, 42)) == {
        "text": "Thu 16 Oct got further than any night before: best wipe 34.1%, on pull 42.", "kind": ""}
    assert said((34.1, 42), (40.2, 7))["text"] == "Thu 9 Oct is still the furthest: best wipe 34.1%, on pull 42."
    assert said((40.2, 7), (34.1, 3), (34.1, 9))["text"] == (  # as far is not further
        "Thu 16 Oct is still the furthest: best wipe 34.1%, on pull 3.")
    assert said((70.5, 16, "P2", 68.3))["text"] == "Best wipe P2 68.3% (WCL 70.5%), on pull 16 of Thu 9 Oct."
    assert said((80.0, 2), (70.5, 16, "P2", 68.3))["text"] == (
        "Thu 16 Oct got further than any night before: best wipe P2 68.3% (WCL 70.5%), on pull 16.")
    assert said(None, (34.1, 42))["text"] == "Best wipe 34.1%, on pull 42 of Thu 16 Oct."
    assert said((34.1, 42), (2.0, 12), kill=14)["text"] == "Killed on Thu 16 Oct, on pull 14 of that night."
    assert said(None)["text"] == "No wipe past a reset that night."
    assert said(None, None)["text"] == "No wipe past a reset on these nights."


@pytest.mark.parametrize("bests", [((40.2, 7), (34.1, 42)), ((34.1, 42), (40.2, 7), (36.0, 3)), ((34.1, 4),),
                                   ((50.0, 1), (40.0, 2), (30.0, 3)), ((40.0, 1), (40.0, 2)), (None, (40.0, 2)),
                                   ((40.0, 1), (30.0, 2), None), ((60.0, 5, "P1", 20.0), (70.5, 16, "P2", 68.3))])
def test_night_by_night_the_headlines_night_is_the_last_the_text_marks_further(bests):
    """The furthest night is worked out twice, in weeks_text's marks and for the card: held together here. The card's
    night is the text's last "further than any night before" (its first night with a wipe where none is marked), and
    the card says those words only of the last night, where the text's last line says them."""
    from wipebot.core.report import night
    nights = made_up_nights(*bests)
    lines = night.weeks_text("Sszorak", len(nights), nights).splitlines()[1:-1]
    marked = [i for i, line in enumerate(lines) if "further than any night before" in line]
    far = nights[marked[-1] if marked else next(i for i, n in enumerate(nights) if n.best)]
    said = views.weeks_headline(nights)["text"]
    assert f"best wipe {far.best_text()}, on pull {far.best[1]}" in said[0].lower() + said[1:]
    assert views.day(far.start_ms) in said
    assert ("got further than any night before" in said) == ("further than any night before" in lines[-1])


async def test_the_night_by_night_card_leads_with_its_headline(monkeypatch):
    """Asked night by night, the card is the nights' best pulls: it leads with their sentence as every card does
    with its own, and tonight's pulls keep theirs a click away."""
    from wipebot.core import viewhost
    nights = made_up_nights((70.5, 16, "P2", 68.3), (62.6, 21, "P2", 60.1), (55.0, 9, "P3", 91.0))
    tonight = {"view": "night", "report": "night2", "boss": "Torvek's Forge", "difficulty": "Heroic",
               "title": "Torvek's Forge, Thu 23 Oct", "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678",
               "combat_ms": 600000, "guild_best": None, "phased": True, "first_n": 3, "thirds": None,
               "ends": [["Slag Wave killing many", 2]], "firsts": [["Slag Wave", 4]],
               "bosses": [{"boss": "Torvek's Forge", "pulls": 2, "kill": False, "best": 55.0}],
               "pulls": [{"pull": n, "pct": pct, "kill": False, "reset": False, "live": False, "duration_ms": 300000,
                          "phase": "P3", "health": 91.0, "ended": "Slag Wave killing many", "best": False,
                          "first": None} for n, pct in ((8, 60.0), (9, 55.0))],
               "weeks": [views.weeks_row(n) for n in nights], "weeks_headline": views.weeks_headline(nights)}
    tonight["headline"] = views.night_headline(tonight["pulls"], [("Slag Wave killing many", 2)])
    made_up_view(monkeypatch, {"view": "night", "report": "night2", "boss": tonight["boss"], "weeks": 3},
                 written(tonight))
    further = "Thu 23 Oct got further than any night before: best wipe P3 91.0% (WCL 55.0%), on pull 9."
    async with drawn("progress", {}, width=380) as (view, errors):
        card = await view.evaluate(CARD)
        assert not errors and card["lead"] == [further] and card["buttons"][0] == "Open night" and card["tall"] <= 520
    async with drawn("progress", {}, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        assert not errors and (await view.evaluate(CARD))["lead"] == [
            further, "2 of the 2 pulls ended with Slag Wave killing many."]


def test_a_players_nights_headline_is_their_deaths_and_what_would_have_kept_them_alive():
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


STAT_TILES = '() => [...document.querySelectorAll(".stats .stat")].map((e) => e.innerText)'
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


@pytest.mark.parametrize("width", [380, 760])
async def test_a_ranking_and_a_trend_are_the_card_too(recorded, width):
    """breakdown's and mechanic_hits' ranking in Claude and ChatGPT: the card, with the top players' bars for its
    chart and the rest a click away ("Open ranking": everyone's, and a mechanic's hits per player per pull). "By
    pull" opens the trend of a ranking over several pulls: the card again, a bar per pull."""
    from wipebot.core import viewhost
    case = CASES["mechanic-hits"]
    rows = "document.querySelectorAll('.meter-rows li:not(.gap)').length"
    async with drawn(case.tool, case.args, width=width) as (view, errors):
        card = await view.evaluate(CARD)
        assert card["lead"] == ["Deathadim is first with 2.14 hits per minute of the pulls they were in, 2.5 times "
                                "the median of the 22 players ranked."]
        assert 1 <= card["stats"] <= 4 and card["buttons"] == ["Open ranking", "By pull"] and card["tables"] == 0
        assert await view.evaluate(rows) == 8 and "14 more players" in await view.locator("body").inner_text()
        assert await view.locator('[data-panel="by_pull"]').count() == 0
        assert card["tall"] <= (570 if width < 500 else 450)
        await view.get_by_role("button", name="By pull").click()
        await view.locator("[data-pull-col]").first.wait_for()
        trend = await view.evaluate(CARD)
        assert trend["lead"][0].startswith("The raid's Tempest hits per minute: ") and trend["charts"] == 1
        assert 1 <= trend["stats"] <= 4 and trend["buttons"] == ["Open trend"] and trend["tables"] == 0
        assert await view.locator("[data-pull-col]").count() == 32 and trend["tall"] <= (560 if width < 500 else 450)
        assert await view.locator("button.back").inner_text() == "← Tempest"
        assert not errors
    async with drawn(case.tool, case.args, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        assert await view.evaluate(rows) == 22 and await view.locator('[data-panel="by_pull"] svg').count() == 1
        assert await view.get_by_role("button", name="Open ranking").count() == 0 and not errors
    two = CASES["damage-by-pull"]
    async with drawn(two.tool, two.args, width=width) as (view, errors):
        card = await view.evaluate(CARD)
        assert card["lead"] == ["The raid's damage per second: 2.57M on pull 12, 2.55M on pull 13."]
        assert card["charts"] == 1 and card["buttons"] == ["Open trend"]
        assert card["tall"] <= (480 if width < 500 else 380)
        await view.locator('[data-pull-col="13"]').click()  # a pull's column opens that pull
        await view.get_by_role("button", name="Open pull").wait_for()
        assert await view.locator("button.back").inner_text() == "← Damage done by pull" and not errors


def made_up_trend() -> dict:
    """A trend's data, made up (no recorded night's trend holds a kill): seven pulls, the second a reset and the
    last the kill, with the row of the one asking, who sat out pull 3."""
    rates = [1_200_000, 200_000, 1_310_000, 1_280_000, 1_400_000, 1_520_000, 1_610_000]
    own = [210_000, 30_000, None, 228_000, 240_000, 251_000, 262_000]
    pulls = [{"pull": n, "kill": n == 7, "reset": n == 2, "pct": 0.0 if n == 7 else 90.0 - 10 * n,
              "pct_text": None if n == 7 else f"{90.0 - 10 * n:.1f}%", "seconds": 60.0 * n,
              "amount": rate * 60.0 * n, "rate": rate, "mine": mine,
              "top": [["Mirelle", rate * 20.0 * n], ["Torvald", rate * 15.0 * n]]}
             for n, (rate, mine) in enumerate(zip(rates, own, strict=True), 1)]
    return {"view": "trend", "report": "AbCdEfGh12345678", "boss": "Torvek's Forge", "tool": "breakdown",
            "args": {"data_type": "Healing", "group_by": "pull"}, "title": "Healing by pull",
            "scope": "all 7 Torvek's Forge pulls", "subject": "healing", "per": "second", "count": False,
            "who": None, "icon": None, "url": "https://www.warcraftlogs.com/reports/AbCdEfGh12345678",
            "headline": {"text": "The raid's healing per second: 1.20M in the first third of the 6 pulls, 1.28M in "
                                 "the middle and 1.52M in the last (each third's median pull).", "kind": ""},
            "pulls": pulls, "median": 1_400_000,
            "thirds": [{"pulls": [1, 3], "rate": 1_310_000}, {"pulls": [4, 5], "rate": 1_400_000},
                       {"pulls": [6, 7], "rate": 1_610_000}],
            "mine": {"name": "Mirelle", "color": "#33937f", "spec": "Mistweaver", "thirds": None}}


async def test_a_trend_tells_a_kill_and_a_reset_from_a_wipe_and_draws_the_askers_row(monkeypatch):
    """A kill's bar is capped in gold and a reset's is hollow, each with its key under the chart and its word in
    the pull's tooltip and row; a wipe says how far it got as the tools write it (it once read "86.13%", and
    "null%" for a log without one); the one asking has their own row, which says where they weren't in a pull."""
    from wipebot.core import viewhost
    trend = made_up_trend()
    trend["pulls"][4] |= {"pct": None, "pct_text": None}  # Warcraft Logs gave pull 5 no percent
    key = {k: trend[k] for k in ("view", "report", "boss", "tool", "args")} | {"mark": "Mirelle"}
    made_up_view(monkeypatch, key, trend)
    tip = "(n) => document.querySelector(`[data-pull-col=\"${n}\"]`).dataset.tip"
    async with drawn("breakdown", trend["args"]) as (view, errors):
        chart = view.locator(".trend-chart")
        assert await chart.locator('rect[fill="var(--gold)"]').count() == 2  # on the kill's bar, and in the key
        assert await chart.locator('rect[stroke-dasharray="2 2"]').count() == 2  # the reset's bar, and its key
        said = await chart.inner_text()
        assert "kill" in said and "reset, not counted" in said and "Mirelle, up to 262.00k/s" in said
        assert (await view.evaluate(tip, 7)).startswith("Pull 7: kill\n1.61M/s: 676M over 7:00\n")
        assert "Mirelle: 262.00k/s" in await view.evaluate(tip, 7)
        assert (await view.evaluate(tip, 2)).startswith("Pull 2: a reset, not counted\n")
        assert (await view.evaluate(tip, 4)).startswith("Pull 4: wipe at 50.0%\n")  # as the tools write it
        assert (await view.evaluate(tip, 5)).startswith("Pull 5: wipe\n")  # a log without how far it got
        assert "Mirelle: not in it" in await view.evaluate(tip, 3)
        card = (await view.locator("body").inner_text()).lower()
        assert "of 6 pulls, 1 reset left out" in card and "pull 7" in card and "1.61m/s, the kill" in card
        assert not errors
    async with drawn("breakdown", trend["args"], mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, errors):
        rows = await view.locator("table.pulls tbody tr").evaluate_all(
            "trs => trs.map((tr) => [...tr.children].map((td) => td.textContent))")
        assert [r[1] for r in rows] == ["80.0%", "reset", "60.0%", "50.0%", "–", "30.0%", "kill"]
        assert rows[2][5] == "–" and rows[6][5] == "262.00k" and not errors


async def test_a_reset_doesnt_set_a_trends_scale(monkeypatch):
    """A reset is left out of the medians, and of the chart's scale too: a raid that dies on purpose takes a night's
    most damage per second in ten seconds, and its hollow bar once set the axis (10M/s for pulls of 1.2M to 1.6M,
    each a sixth of the plot's height). The counted pulls set it, in the asker's row too; the reset's bar stops at
    the plot's top, and its tooltip still says its amount."""
    trend = made_up_trend()
    trend["pulls"][1] |= {"rate": 6_000_000, "mine": 900_000}
    key = {k: trend[k] for k in ("view", "report", "boss", "tool", "args")} | {"mark": "Mirelle"}
    made_up_view(monkeypatch, key, trend)
    box = """(sel) => { const b = document.querySelector(sel).getBBox(); return [b.y, b.height]; }"""
    async with drawn("breakdown", trend["args"]) as (view, errors):
        said = await view.locator(".trend-chart").inner_text()
        assert said.startswith("2.0M/s\n") and "Mirelle, up to 262.00k/s" in said  # the axis, and the row's most
        plot_top, plot = 14, 132  # trend.js: the plot's top and its height in a card
        top, tall = await view.evaluate(box, '.trend-chart rect[stroke-dasharray="2 2"]')  # the reset's bar
        assert top == plot_top and tall == plot
        tall_of = "rects => rects.map((r) => r.getBBox().height)"
        heights = await view.locator('.trend-chart rect[rx="2"][opacity=".8"]').evaluate_all(tall_of)
        assert max(heights) == pytest.approx(plot * 1_610_000 / 2_000_000, abs=0.5), heights  # the kill's bar
        own = sorted(await view.locator('.trend-chart rect[rx="1.5"]').evaluate_all(tall_of))
        assert own[-2:] == [24, 24], own  # the asker's row: their most in a counted pull fills it, the reset's no more
        assert (await view.evaluate("document.querySelector('[data-pull-col=\"2\"]').dataset.tip")).startswith(
            "Pull 2: a reset, not counted\n6.00M/s: ")
        assert not errors


async def test_a_call_without_a_view_takes_no_room(recorded):
    """A host draws the page on every call of a tool with a view, and most calls of breakdown and mechanic_hits rank
    nobody: for those the page stays empty at no height, while the tool runs too. It once would have shown the
    placeholder and then "Nothing to show here", 585 px of it."""
    from wipebot.core import viewhost
    case = CASES["damage-taken"]  # by ability: a table, no ranking of players
    result = await viewhost.tool_result(case.tool, case.args)
    assert "structuredContent" not in result
    frame = "document.querySelector('iframe').getBoundingClientRect().height"
    async with chromium() as browser:
        for setup in (host_setup(case.tool, case.args, None, running=True), host_setup(case.tool, case.args, result),
                      host_setup(case.tool, case.args, {"isError": True, "content": []})):
            errors: list[str] = []
            page = await viewhost._open(browser, setup, errors)
            view = page.frames[-1]
            await view.locator(":root[data-empty]").wait_for(state="attached")
            await page.wait_for_function(f"{frame} === 0")
            assert await view.evaluate(TALL) == 0 and await view.locator("body").inner_text() == ""
            assert (await page.evaluate(HEARD, "ui/notifications/size-changed"))[-1]["height"] == 0
            assert not errors
            await page.close()
        # the running tool's result then names a view: the card, as for any other
        meter = CASES["mechanic-hits"]
        page = await viewhost._open(browser, host_setup(meter.tool, meter.args, None, running=True), errors)
        await page.wait_for_function(f"{frame} === 0")
        await page.evaluate("""(result) => { S.running = false;
            send({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }); }""",
                            await viewhost.tool_result(meter.tool, meter.args))
        await page.frames[-1].locator(".meter-rows").wait_for()
        assert await page.evaluate(frame) > 300 and not errors


async def test_a_call_without_a_view_takes_no_room_in_a_host_that_names_no_tool_either(recorded):
    """Which tool a page is for is the host's to say or not (hostContext.toolInfo is optional), and the page knew
    breakdown and mechanic_hits only by it: in a host that left it out, a call that ranks nobody got the card's
    placeholder and then "Nothing to show here: ask again to see it", and a failed one "The tool failed". A page
    that isn't told its tool now draws nothing until a result names a view, and then draws it as anywhere."""
    from wipebot.core import viewhost
    case = CASES["damage-taken"]  # by ability: a table, no ranking of players
    result = await viewhost.tool_result(case.tool, case.args)
    assert "structuredContent" not in result
    frame = "document.querySelector('iframe').getBoundingClientRect().height"
    async with chromium() as browser:
        for setup in (host_setup(case.tool, case.args, None, running=True, unnamed=True),
                      host_setup(case.tool, case.args, result, unnamed=True),
                      host_setup(case.tool, case.args, {"isError": True, "content": []}, unnamed=True)):
            errors: list[str] = []
            page = await viewhost._open(browser, setup, errors)
            view = page.frames[-1]
            assert "toolInfo" not in next(m["msg"]["result"]["hostContext"] for m in await page.evaluate("window.__log")
                                          if "hostContext" in m["msg"].get("result", {}))
            await view.locator(":root[data-empty]").wait_for(state="attached")
            await page.wait_for_function(f"{frame} === 0")
            assert await view.evaluate(TALL) == 0 and await view.locator("body").inner_text() == ""
            assert not errors
            await page.close()
        # a ranking there is drawn from its result's key, and a pull's card too
        meter, pull = CASES["mechanic-hits"], PULL_CASE
        for tool, args, drawn_as in ((meter.tool, meter.args, ".meter-rows"), (pull.tool, pull.args, "#card-open")):
            page = await viewhost._open(browser, host_setup(tool, args, await viewhost.tool_result(tool, args),
                                                            unnamed=True), errors)
            await page.frames[-1].locator(drawn_as).first.wait_for()
            assert await page.evaluate(frame) > 300 and not errors
            await page.close()


async def test_a_later_ranking_of_an_answer_is_one_line_that_opens(recorded):
    """The second ranking or trend of an answer comes folded (server._fold): one line saying what it ranks, which
    opens the card. A still picture and fullscreen draw it whole."""
    from wipebot.core import viewhost
    case = CASES["mechanic-hits"]
    result = await viewhost.tool_result(case.tool, case.args)
    result["structuredContent"]["folded"] = True
    async with drawn(case.tool, case.args, result=result) as (view, errors):
        line = view.locator(".folded")
        assert await line.inner_text() == ("Tempest, all 32 Sszorak pulls (1 reset left out), each until its wipe was "
                                           "decided\nShow")
        assert await view.evaluate(TALL) <= 56 and await view.locator(".meter-rows").count() == 0
        await view.get_by_role("button", name="Show").click()
        await view.locator(".meter-rows").wait_for()
        assert await view.locator(".folded").count() == 0 and not errors
    async with drawn(case.tool, case.args, result=result, mode="fullscreen", width=viewhost.FULL_WIDTH) as (view, _):
        assert await view.locator(".folded").count() == 0 and await view.locator(".meter-rows").count() == 1


async def test_the_player_cards_are_the_same_card_and_a_wipes_standing_is_fullscreens_with_why(made_up, monkeypatch):
    """A player's pull and night are the card too. Where a wipe's output stands among ranked kills isn't on the card
    or in a picture: it is drawn in fullscreen only, with why it proves little by itself, in the tool's words."""
    from wipebot.core import viewhost
    async with drawn("player_pull", {"player": "Mirelle"}) as (view, errors):
        card, text = await view.evaluate(CARD), await view.locator("body").inner_text()
        tiles = [t.splitlines()[0] for t in await view.evaluate(STAT_TILES)]
    assert not errors and card["lead"] == ["290.00k healing per second over the 143 s alive, rank 2 of 4 healers "
                                           "by total."]
    assert card["stats"] == 3 and card["charts"] == 1 and card["buttons"] == ["Open player", "Against top players"]
    assert "ranked" not in text and "100th best" not in text and views.SHORT_PULL not in text
    # No number over the cooldowns: player_pull states each one's uses of possible uses (Ascendance 1/1, Spirit Link
    # Totem 0/1) and no total, and the card once said "Cooldowns 1 of 2", a sum the page made of them
    assert tiles == ["HEALING PER S ALIVE", "TIME LOST BEFORE 2:20", "DIED"] and "1 of 2" not in text
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
                        "phase": "", "health": None, "ended": "Slag Wave killing many", "best": False,
                        "first": None} for n, pct in ((1, 41.5), (2, 60.0))]}
    night["headline"] = views.night_headline(night["pulls"], [("Slag Wave killing many", 2)])
    made_up_view(monkeypatch, {"view": "night", "report": night["report"], "boss": night["boss"]}, written(night))
    chips = '() => [...document.querySelectorAll(".chips.bosses .chip")].map((b) => [b.textContent, b.className])'
    async with drawn("progress", {"report": night["report"]}) as (view, errors):
        assert await view.evaluate(chips) == [["Ashen Warden · 1 pull, killed", "chip"],  # the best once read "42%"
                                              ["Torvek's Forge · 2 pulls, best 41.5%", "chip on"]]
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
        assert await view.locator("#rp-title").inner_text() == "Sszorak pull 13"  # titled as the card it opened from
        replay = "The user is now looking at the replay of Sszorak pull 13 at"
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
