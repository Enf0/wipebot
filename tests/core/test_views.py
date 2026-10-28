"""The views (report/views.py, the page in app/): each view's data on a real pull, against its tool's text and its
snapshot; the page drawn in the mock host (viewhost.py, Chromium); the replay's JavaScript against the tracks
maps.replay packs (Node)."""
import io
import json
import re
import shutil
import subprocess
from importlib import resources

import pytest
from mcp.types import CallToolResult, TextContent

from tests.support import assert_snapshot
from tests.support.paths import REAL
from tests.support.recorded import CASES, LOCAL, LOCAL_RECORDED, NOT_LOCAL, Replay, serve_from
from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report import maps, views
from wipebot.core.report import note as notes
from wipebot.core.report.ending import Ending, MassDeath, mass_death_of
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
        assert re.search(rf"^  {r['pull']}: .*{clock(r['duration_ms'])}{re.escape(first)}", text, re.MULTILINE), r
    ends = ", ".join(f"{w} {n}" for w, n in data["ends"])
    assert f"(pull_summary's \"Ended by\"): {ends}." in text
    assert "first third {:.1f}, middle {:.1f}, last third {:.1f}".format(*data["thirds"]) in text


async def test_the_raid_night_view_draws_and_a_pulls_bar_opens_that_pull(recorded, tmp_path):
    first, opened = await shots(tmp_path, "progress", NIGHT_OF_PULLS.args, themes=("dark",), widths=(760,),
                                modes=("fullscreen",), clicks=('css:tr[data-pull="13"]',))
    assert not first.errors and "how the 33 pulls ended" in first.text.lower() and "Vashnik" in first.text
    assert "Sszorak pull 13" in opened.text  # that pull's card


# ---- the meter: a ranking of players from breakdown, mechanic_hits and enemies, in the bot only ----

METER_CASES = ["mechanic-hits", "damage-taken-ability", "interrupts", "dispels", "nekzali-enemies-night",
               "vashnik-enemies"]


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
        assert re.search(rf"^  {re.escape(b['name'])}(?: \(tank\))?: .*, {plural(b['died_after'], 'death')} soon "
                         rf"after, {b['killed_by']} with it as the killing blow", text, re.MULTILINE), b["name"]
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


@pytest.mark.parametrize("case", ["damage-taken", "mechanic-outliers", "buffs", "damage-by-pull", "breakdown-casts",
                                  "healing-by-ability"])
async def test_a_call_that_ranks_no_players_has_no_meter(recorded, case):
    assert await server.view_key(CASES[case].tool, CASES[case].args) is None


async def test_the_meter_draws_a_panel_per_meter_without_errors(recorded, tmp_path):
    data = await meter_data("nekzali-enemies-night")
    taken = await shots(tmp_path, "enemies", CASES["nekzali-enemies-night"].args, themes=("dark", "light"),
                        widths=(320, 760))
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 100
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
    [personals] = [g for g in c["callouts"] if g["text"] == "Big Personals"]
    assert personals["personals"] and personals["to"] == "everyone"
    block = text.split('"Big Personals" to everyone', 1)[1].split("\n  \"", 1)[0]
    counts = {n: (int(a), int(m))
              for n, a, m in re.findall(r"^    (\S+): alive for (\d+); pressed at (\d+)", block, re.MULTILINE)}
    assert counts and {r["player"]: (r["alive"], r["made"]) for r in personals["rows"]} == counts
    assert any(s == "other" for r in personals["rows"] for p in r["cells"] for s in p)


@pytest.mark.parametrize("case", ["raid-note", "raid-note-kicks"])
async def test_one_pulls_note_check_has_no_view(recorded, case):
    assert await server.view_key(CASES[case].tool, CASES[case].args) is None


async def test_the_note_view_draws_a_panel_each_without_errors(recorded, tmp_path):
    data = await note_data("raid-note-order-change")
    taken = await shots(tmp_path, "raid_note", CASES["raid-note-order-change"].args, themes=("dark", "light"),
                        widths=(320, 760))
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 300
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
    taken = await shots(tmp_path, "raid_note", key["args"] | {"report": report}, themes=("dark", "light"),
                        widths=(320, 760))
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
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
    for s in await shots(tmp_path, VERSUS_CASE.tool, VERSUS_CASE.args, themes=("dark", "light"), widths=(320, 760)):
        assert not s.errors, (s.path.name, s.errors)
        assert "Krøpling" in s.text and "Glacial Spike" in s.text


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
    for s in await shots(tmp_path, MANA_CASE.tool, MANA_CASE.args, themes=("dark", "light"), widths=(320, 760)):
        assert not s.errors, (s.path.name, s.errors)
        assert "Healers' mana" in s.text and data["healers"][0]["name"] in s.text


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


async def test_a_view_that_does_not_exist_is_a_readable_error(recorded):
    with pytest.raises(server.FactError, match="no view 'weekly'"):
        await server.view_data("weekly", PULL_CASE.args["pull"], None, PULL_CASE.args["report"])


def test_the_page_is_one_file_with_a_hashed_address():
    page = views.page()
    assert page.startswith("<!doctype html>") and "/*STYLE*/" not in page and "/*SCRIPT*/" not in page
    for name in views.SCRIPTS:  # every script inline: the page loads nothing
        first = resources.files("wipebot.core").joinpath("app", name).read_text(encoding="utf-8").splitlines()[0]
        assert first in page
    assert "<script src" not in page and "<link" not in page
    assert re.fullmatch(r"ui://wipebot/views-[0-9a-f]{10}\.html", views.page_uri())


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


async def test_the_pull_view_draws_in_a_host_without_errors(recorded, tmp_path):
    data = await pull_data()
    taken = await shots(tmp_path, "pull_summary", PULL_CASE.args, themes=("dark", "light"), widths=(320, 760),
                        modes=("inline", "fullscreen"))
    assert len(taken) == 6
    for s in taken:
        assert not s.errors, (s.path.name, s.errors)
        assert s.height > 100 and s.path.stat().st_size > 10_000
        assert "Scopidruid 0:48" in s.text and f"{data['boss_pct']}%" in s.text
        assert f"the boss gaining Unbound Ferocity {clock(data['decided']['ms'])}" in s.text
    inline, full = taken[0], taken[-1]
    assert "Open pull" in inline.text and "deaths (19)" not in inline.text.lower()
    assert "deaths (19)" in full.text.lower() and "Open pull" not in full.text


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
    n = len(data["pulls"])
    assert f"Died before the wipe was decided on {data['died']} of {n} pulls" in text
    assert f"dead for {data['dead_s']} s of the {data['counted_s']} s" in text and f"({data['dead_pct']}%" in text
    best = f"pull {data['best']['pull']} ({kit_short(data['best']['per_s_alive'])}/s)"
    assert f"Best pull by {data['what']} per second alive: {best}" in text
    if data["thirds"]:
        died = " / ".join(f"{t['died']} of {len(t['pulls'])}" for t in data["thirds"])
        assert f"deaths that count (as above) on {died};" in text
    for c in data["cooldowns"]:
        if c["possible"] is not None and c["used"]:
            assert f"{c['name']} {c['used']}/{c['possible']}" in text


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
    for cd in data["cooldowns"]:  # as player_pull lists them: uses / possible at their times
        uses = f"{len(cd['uses'])}/{cd['possible']}" + (" at " + ", ".join(clock(t) for t in cd["uses"])
                                                         if cd["uses"] else "")
        assert f"{cd['name']} [" in text and uses in text
    top, failed = await server.call("compare_with_top", TOP_CASE.args)
    assert not failed
    for r in data["top"]["rates"]:
        if r["median"] is not None:
            assert f"{r['mine']:.1f} / {r['median']:.1f} ({r['low']:.1f}-{r['high']:.1f})" in top


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
    return {
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
                     "points": [["100th best", 360_000], ["500th best", 320_000], ["900th best", 250_000]]},
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
            "title": "Mirelle on Torvek's Forge",
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
    assert not first.errors and "2 of 6" in first.text and "first to die once" in first.text
    assert "could have lived" in first.text.lower() and "1 of 2" in first.text and "Astral Shift at 1" in first.text
    assert "what killed mirelle" in first.text.lower() and "Slag Wave" in first.text
    assert "6: 330k" in first.text and "pull by pull" in first.text.lower()
    calls = [m["msg"]["params"]["arguments"] for m in opened.messages if m["msg"].get("method") == "tools/call"
             and m["msg"]["params"]["name"] == "view_data"]
    assert calls[-1] == {"view": "player", "report": "AbCdEfGh12345678", "pull": 4, "boss": "Torvek's Forge",
                         "player": "Mirelle", "top": False}
    assert "← Mirelle" in opened.text and "rank 2 of 4 healers" in opened.text  # that pull's card, and Back


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
        assert await view.locator("image[href$='spells/114052.jpg']").count() == 1  # Ascendance at its use
        assert await view.locator("image").evaluate_all("els => els.every((e) => e.getBBox().width > 0)")
        await view.locator("image[href$='spells/114052.jpg']").hover()
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


def test_a_players_notes_put_the_weightiest_first_and_leave_raid_cooldowns_out_of_the_fixes():
    assert views.player_notes(_facts()) == {"good": ["Kept casting: 2 s of gaps in 3:00",
                                                      "Used every major cooldown each time it was ready"], "fix": []}
    worse = _facts(died=[{"ms": 95000, "by": "Toxic Fumes", "fallout": False}],
                   lost={"until_ms": 180000, "dead_s": 85, "gaps_s": 20, "capped_s": 40, "resource": "Energy"},
                   cooldowns=[{"name": "Darkness", "uses": [], "possible": 3, "raid": True},
                              {"name": "Deathmark", "uses": [], "possible": 2, "raid": False}])
    assert views.player_notes(worse) == {"good": [], "fix": ["Died at 1:35 to Toxic Fumes, before the wipe was decided",
                                                             "Deathmark: used 0 of the 2 times it was ready"]}
    after = _facts(died=[{"ms": 185000, "by": "Toxic Fumes", "fallout": True}],  # fallout isn't theirs to fix
                   output={"what": "healing", "rank": 1, "of": 4, "role": "healers"},
                   standing={"kind": "parse", "percentile": 81})
    assert views.player_notes(after)["good"] == ["81st percentile parse", "Most healing of the 4 healers"]
    wiped = _facts(died=[{"ms": 95000, "by": "Visceral Burst", "fallout": False, "with": 4}])  # one hit killing many
    assert views.player_notes(wiped)["fix"] == []


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
