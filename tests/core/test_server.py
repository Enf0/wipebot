"""The tools as MCP clients see them (server.py): their list, schemas and descriptions, the skill, and errors."""
import asyncio
import inspect
import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.support import CODE, NOTE, FakeWCL
from tests.support.recorded import CASES
from wipebot.core import notebook, server
from wipebot.core.report import FactError, Report

# The tools that don't read a report, so take none; every other tool takes `report`.
NO_REPORT = {"spell_info", "spec_guide", "hotfixes", "web_lookup", "analysis_method"}
# The tools without a case in cases.toml, and where they're tested instead.
TESTED_ELSEWHERE = {
    "hotfixes": "knowledge/test_news.py (a snapshot would date: it counts back from today)",
    "analysis_method": "here (test_the_method_is_the_skill_with_its_parts_named_by_part): it is the skill's text",
}


@pytest.mark.parametrize("ref, code", [
    (CODE, CODE),
    (f"https://www.warcraftlogs.com/reports/{CODE}", CODE),
    (f"https://www.warcraftlogs.com/reports/{CODE}#fight=21&type=deaths", CODE),
])
def test_report_code(ref, code):
    assert server.report_code(ref) == code


def test_report_code_rejects_junk():
    with pytest.raises(FactError):
        server.report_code("not a report!")


def test_every_tool_that_reads_a_report_takes_one():
    for name, fn in server._TOOLS:
        assert ("report" in inspect.signature(fn).parameters) == (name not in NO_REPORT), name


def test_every_tool_is_in_the_list_of_where_its_work_is_done():
    listed = (server.__doc__ or "").split("Where each tool's work is done")[1]
    assert [name for name, _ in server._TOOLS if not re.search(rf"\b{name}\b", listed)] == []


def test_every_tool_is_tested():
    """A new tool comes with a case in cases.toml (test_tools.py), or a line in TESTED_ELSEWHERE saying where."""
    untested = {name for name, _ in server._TOOLS} - {c.tool for c in CASES.values()} - set(TESTED_ELSEWHERE)
    assert not untested
    assert not {c.tool for c in CASES.values()} & set(TESTED_ELSEWHERE)  # an entry here outlived its reason


async def test_what_the_model_reads_fits_the_clients():
    """Claude Code cuts server instructions and a tool's description at 4,096 characters, and ChatGPT weighs the
    instructions' first 512. Claude's directory wants a title and a read-only or destructive hint on every tool (a
    read-only one runs without asking; ChatGPT asks before any other)."""
    assert len(server.INSTRUCTIONS) <= 512
    for start in ("pull_summary", "progress", "player_pull", "raid-log-analysis"):
        assert start in server.INSTRUCTIONS, start
    for name, t in {t.name: t for t in await server.mcp.list_tools()}.items():
        assert t.description, name
        assert len(t.description) <= 4096, name
        assert t.title and t.title != name, name
        assert t.annotations and t.annotations.read_only_hint, name


async def test_the_tool_list_is_lean_and_the_starting_tools_load_up_front():
    """Every tool's schema reaches the model on every turn: no pydantic titles or `anyOf [X, null]`. Claude Code
    loads the tools answers start from up front, the rest through its tool search."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name, t in tools.items():
        schema = json.dumps(t.input_schema)
        assert '"title"' not in schema and '{"type": "null"}' not in schema, name
        assert bool((t.meta or {}).get("anthropic/alwaysLoad")) == (name in server.ALWAYS_LOAD), name
    assert tools["breakdown"].input_schema["properties"]["boss"]["type"] == "string"
    with pytest.raises(ToolError):  # the arguments are still checked against the function's types
        await server.call("pull_summary", {"pull": "the last one"})


def test_the_skill_is_a_valid_agent_skill():
    """The Agent Skills rules: a lowercase-hyphen name of at most 64 characters matching its folder, a description
    of at most 1,024 saying what it does and when to use it, the body under 500 lines, and every file it points to
    there (the bot puts them all in its prompt, server.skill)."""
    text = (server.SKILL / "SKILL.md").read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    meta = dict(line.split(": ", 1) for line in front.strip().splitlines())
    assert re.fullmatch(r"[a-z0-9-]{1,64}", meta["name"]) and meta["name"] == server.SKILL.name
    assert 0 < len(meta["description"]) <= 1024 and "Use when" in meta["description"]
    assert len(body.splitlines()) < 500
    linked = re.findall(r"\]\(([\w-]+\.md)\)", body)
    assert linked and all((server.SKILL / f).is_file() for f in linked)
    whole = server.skill()
    assert all((server.SKILL / f).read_text(encoding="utf-8").strip() in whole for f in linked)


def test_the_skill_is_a_core_that_links_each_part_once_by_its_title():
    """SKILL.md is the core, what every answer needs; what one kind of question needs is a part, a file beside it.
    A client with the plugin reads a part when the core sends it there, and the bot gets them all in its prompt, in
    the order of the core's links (server.skill): so a part the core doesn't link reaches nobody, and one linked
    twice would be in the prompt twice. The bot has no file to open and finds a part by its name, so a link's text
    is the title its file opens with."""
    body = (server.SKILL / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    links = re.findall(r"\[([^\]]+)\]\(([\w-]+\.md)\)", body)
    names = [name for _, name in links]
    assert sorted(names) == sorted(f.name for f in server.SKILL.iterdir() if f.name != "SKILL.md")
    assert len(names) == len(set(names)) > 1
    parts = [(server.SKILL / name).read_text(encoding="utf-8").strip() for name in names]
    assert [part.splitlines()[0] for part in parts] == [f"# {title}" for title, _ in links]
    whole = server.skill()
    assert whole.startswith(body.strip())
    assert [whole.count(part) for part in parts] == [1] * len(parts)
    assert [whole.index(part) for part in parts] == sorted(whole.index(part) for part in parts)


def test_the_skill_stays_within_its_size():
    """The core is read with every question and the bot's prompt carries all of it on every answer, so each has a
    limit, in words as str.split counts them: SKILL.md 800 with its front matter, a part 500, all of it 3,400 (it
    was 4,638 as one file with its examples). A rule that comes in takes the place of one that goes, or becomes a
    line of a tool's output."""
    words = {f.name: len(f.read_text(encoding="utf-8").split()) for f in server.SKILL.iterdir()}
    assert words["SKILL.md"] <= 800
    assert {name: n for name, n in words.items() if name != "SKILL.md" and n > 500} == {}
    assert sum(words.values()) <= 3400


async def test_the_skill_and_instructions_name_only_the_cores_tools():
    """The skill and instructions.md are for every host, so the tools and arguments they name are the core's: not
    one a single app adds (the remote server's my_characters, the bot's remember). What a host adds, its own
    prompt says (the bot's host-prompt.md)."""
    tools = await server.mcp.list_tools()
    known = {t.name for t in tools} | {p for t in tools for p in t.input_schema.get("properties", {})}
    assert set(re.findall(r"`([^`]+)`", server.skill())) <= known
    assert set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", server.INSTRUCTIONS)) <= known


async def test_saving_is_a_tool_of_its_own_on_a_server_that_knows_who_asks(tmp_path, monkeypatch):
    """save_note_or_plan isn't read-only, so it's a tool apart from raid_note, which stays read-only: a client asks
    before a call to it. Only a server that knows who's asking has it (the remote server's officers): not the
    in-process and stdio one, nor the bot's."""
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))
    assert "save_note_or_plan" not in {t.name for t in await server.mcp.list_tools()}
    officer: list = [("Rowan", 14169)]

    def who() -> tuple[str, int]:
        if not officer:
            raise FactError("not saved: only the guild's officers save one")
        return officer[0]
    mcp, setup = server.new_server(), server.Setup(library=server.Library())
    server.register(mcp, setup)
    server.allow_saving(mcp, setup, who)
    tools = {t.name: t for t in await mcp.list_tools()}
    save = tools["save_note_or_plan"]
    assert save.title == "Save raid note or plan" and save.annotations and not save.annotations.read_only_hint
    assert not save.annotations.destructive_hint and '"title"' not in json.dumps(save.input_schema)
    assert all(t.annotations and t.annotations.read_only_hint for n, t in tools.items() if n != "save_note_or_plan")
    assert (tools["raid_note"].description or "").endswith(server.SAVING["raid_note"])
    assert not HOSTS.search((save.description or "") + json.dumps(save.input_schema))
    result = await mcp.call_tool("save_note_or_plan", {"pasted": NOTE.read_text(encoding="utf-8")})
    assert server._text(result).startswith("Saved the raid note")
    assert [n.saved_by for n in notebook.Notebook().latest_notes(14169)] == ["Rowan"]
    officer.clear()
    with pytest.raises(ToolError, match="only the guild's officers"):
        await mcp.call_tool("save_note_or_plan", {"pasted": NOTE.read_text(encoding="utf-8")})


HOSTS = re.compile(r"\b(Claude|ChatGPT|OpenAI|Discord|Codex|channels?|DMs?)\b")


async def test_what_every_host_reads_names_no_host():
    """The core's tool descriptions, their arguments' and prompts, the skill and instructions.md reach every host
    alike, so they don't name one or what only one does (Claude's replay of pull_map is the remote server's own
    note, remote.py)."""
    tools = await server.mcp.list_tools()
    texts = {t.name: t.description or "" for t in tools}
    texts |= {f"{t.name}.{p}": d.get("description", "") for t in tools
              for p, d in t.input_schema.get("properties", {}).items()}
    texts |= {p.name: p.description or "" for p in await server.mcp.list_prompts()}
    texts |= {"skill": server.skill(), "instructions.md": server.INSTRUCTIONS}
    assert {name: m.group() for name, text in texts.items() if (m := HOSTS.search(text))} == {}


async def test_missing_report_is_a_readable_tool_error(monkeypatch):
    monkeypatch.setattr(server.DEFAULT, "library", server.Library())  # no default report
    with pytest.raises(ToolError, match="no report given"):
        await server.mcp.call_tool("progress", {})


async def test_an_argument_a_tool_does_not_take_is_refused_by_name():
    """It was dropped without a word, and the call answered another question than the one asked."""
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool("pull_summary", {"report": CODE, "pull": 13, "player": "Enfo", "bogus": 1})
    told = str(e.value)
    assert told.startswith("pull_summary takes no `player` or `bogus`. Its arguments: pull, boss, report. Tools that "
                           "take `player`: ")
    takes = told.split("Tools that take `player`: ")[1].rstrip(".").split(", ")
    assert {"player_pull", "player_night", "death_recap"} <= set(takes) and "pull_summary" not in takes
    assert "bogus`:" not in told  # no tool takes it: nothing to point to
    # An argument left empty is no argument: clients fill in a null for what they don't use.
    with pytest.raises(ToolError, match="no report given"):
        await server.mcp.call_tool("progress", {"player": None})


async def test_an_argument_of_the_wrong_kind_is_told_in_a_sentence():
    """Not as pydantic words it: its model's name, its error codes and a link to its documentation."""
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool("progress", {"report": CODE, "weeks": "many", "each_pull": "sometimes"})
    assert str(e.value) == (
        "progress can't take 'sometimes' as `each_pull`: it should be a valid boolean, unable to interpret input. "
        "`each_pull` is: each pull's row also names its first three deaths and the killing blows until the wipe was "
        "decided, with how many died after it. "
        "progress can't take 'many' as `weeks`: it should be a valid integer, unable to parse string as an integer. "
        "`weeks` is: also the boss on each of the guild's raid nights over this many weeks back (1-12), for \"are "
        "we doing better than last week?\": reads every night, slow.")
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool("player_pull", {"report": CODE, "pull": [1, 2]})
    assert str(e.value) == (
        "player_pull can't take [1, 2] as `pull`: it should be a valid integer or a valid string. `pull` is: pull "
        "number on the boss, or kill, best wipe, last wipe, first or last; omit for the latest. "
        "player_pull needs `player`: character name as in the log (accents optional).")


def _fitting(schema: dict) -> object:
    """A value an argument of this schema takes, for one a test has to give."""
    kind = (schema.get("anyOf") or [schema])[0]
    if "enum" in kind:
        return kind["enum"][0]
    return {"integer": kind.get("minimum", 1), "boolean": True, "array": []}.get(kind.get("type"), "x")


async def test_a_number_given_where_a_tool_takes_text_is_taken_as_its_text(monkeypatch):
    """The tools print a spell's id as a number ("[1287083]") and ask for it back ("spell id (best, from a log
    tool)"), so a model gives it as one: `spell_info {"spell": 1287083}` was refused as no valid string. Every
    tool's text arguments take a number as its text; what a tool takes as a number still refuses text."""
    took: dict[str, dict] = {}
    tools = server.mcp._tool_manager.list_tools()
    for tool in tools:
        async def ran(_name=tool.name, **given):
            took[_name] = given
            return "ran"
        monkeypatch.setattr(tool, "fn", ran)
    texts = {}
    for tool in tools:
        args = tool.parameters.get("properties") or {}
        texts[tool.name] = [name for name, schema in args.items() if schema.get("type") == "string"
                            and "enum" not in schema]
        given = {name: 1287083 if name in texts[tool.name] else _fitting(args[name])
                 for name in {*texts[tool.name], *tool.parameters.get("required", ())}}
        await server.mcp.call_tool(tool.name, given)
        assert {name: took[tool.name][name] for name in texts[tool.name]} == dict.fromkeys(texts[tool.name], "1287083")
    assert texts["spell_info"] == ["spell"] and "report" in texts["progress"] and "player" in texts["player_pull"]
    assert sum(map(len, texts.values())) > 80  # nearly every argument of every tool
    # a whole number that came as a decimal (JSON has one kind of number) reads as the id it is
    await server.mcp.call_tool("spell_info", {"spell": 1287083.0})
    assert took["spell_info"] == {"spell": "1287083"}
    # neither a yes or no nor a list is a number, and a number is no choice of a few words
    with pytest.raises(ToolError, match="spell_info can't take True as `spell`: it should be a valid string"):
        await server.mcp.call_tool("spell_info", {"spell": True})
    with pytest.raises(ToolError, match=r"boss_guide can't take 3 as `topic`: it should be 'strategy', 'tooltips' or"):
        await server.mcp.call_tool("boss_guide", {"boss": "Sszorak", "topic": 3})
    with pytest.raises(ToolError, match="progress can't take 'many' as `weeks`: it should be a valid integer"):
        await server.mcp.call_tool("progress", {"report": 12345678, "weeks": "many"})


async def test_a_boss_name_read_one_of_several_ways_is_said_in_the_results_first_line(night):
    class Mixed(FakeWCL):  # Vashnik pulled once on Heroic before the Mythic pulls
        async def report_fights(self, code, cache_for=None):
            report = await super().report_fights(code)
            heroic = {**report["fights"][0], "id": 900, "difficulty": 4, "startTime": 0, "endTime": 1}
            return {**report, "fights": [heroic, *report["fights"]]}
    rep = Report(Mixed(night).client(), CODE)
    await rep.load()

    async def pulls(boss: str) -> str:
        return f"{rep.boss(boss)}: {len(rep.by_boss[rep.boss(boss)])} pulls"

    assert await server.guarded(pulls)(boss="vashnik") == (
        "Boss 'vashnik' is read as Vashnik the Malignant (Mythic), pulled last; Vashnik the Malignant (Heroic) "
        "matches it too.\nVashnik the Malignant (Mythic): 2 pulls")
    assert await server.guarded(pulls)(boss="vashnik heroic") == "Vashnik the Malignant (Heroic): 1 pulls"


async def test_a_link_to_a_fight_is_noted_only_where_a_pull_is_read_and_trash_is_no_error_about_the_night(
        monkeypatch, night):
    """Fight 21 is Sszorak pull 13, fight 5 trash between the two bosses, and the latest pull is Sszorak's."""
    monkeypatch.setattr(server.DEFAULT, "library", server.Library(CODE, wcl=FakeWCL(night).client()))
    link = f"https://www.warcraftlogs.com/reports/{CODE}"
    trash = ("Fight 5 in the link isn't a boss pull of this report (trash, or a fight it doesn't have), so the link "
             "was read as the report alone.\n")

    async def a_night(boss: str | None = None, report: str | None = None) -> str:
        return f"{(await server.lib().report(report)).boss(boss)}'s night"

    async def a_pull(pull: int | None = None, boss: str | None = None, report: str | None = None) -> str:
        rep = await server.lib().report(report)
        return rep.label(rep.fight(pull, boss))
    a_night, a_pull = server.guarded(a_night), server.guarded(a_pull)
    # about the night: the link chooses the boss, and no line says which pull it was, since none was read
    assert await a_night(boss=None, report=link + "#fight=2") == "Vashnik the Malignant's night"
    assert await a_night(boss=None, report=link + "#fight=21") == "Sszorak's night"
    assert await a_night(boss="Vashnik", report=link + "?fight=21&type=healing") == "Vashnik the Malignant's night"
    # trash: the night is still what was asked for, and the first line says the link named no pull
    assert await a_night(boss=None, report=link + "#fight=5") == trash + "Sszorak's night"
    assert await a_night(boss="Vashnik", report=link + "#fight=5") == trash + "Vashnik the Malignant's night"
    # about a pull: which pull the link's fight is; a call naming its own pull is read, trash or not
    assert await a_pull(pull=None, boss=None, report=link + "#fight=21") == (
        "Fight 21 in the link is Sszorak pull 13.\nSszorak pull 13")
    assert await a_pull(pull=5, boss=None, report=link + "#fight=5") == trash + "Sszorak pull 5"
    assert await a_pull(pull=1, boss="Vashnik", report=link + "#fight=5") == trash + "Vashnik the Malignant pull 1"
    for boss in (None, "Sszorak"):  # the link's pull is what the call reads: the latest would be another fight
        with pytest.raises(ToolError, match="fight 5 in the link isn't a boss pull of report"):
            await a_pull(pull=None, boss=boss, report=link + "#fight=5")


async def test_a_call_over_every_pull_reads_a_links_fight_as_its_boss_and_no_pull(monkeypatch, night):
    """A tool asked for every pull on a boss (`all_pulls`) still looks one pull of it up, for the raid note saved
    for the boss that night: a link to pull 13 led the night's answer with "Fight 21 in the link is Sszorak pull
    13.", and a link to trash ended it in the error of a call that reads the link's pull."""
    monkeypatch.setattr(server.DEFAULT, "library", server.Library(CODE, wcl=FakeWCL(night).client()))
    link = f"https://www.warcraftlogs.com/reports/{CODE}"

    async def hits(pull: int | None = None, boss: str | None = None, all_pulls: bool = False,
                   report: str | None = None) -> str:
        rep = await server.lib().report(report)
        f = rep.fight(pull, boss)
        return f"{rep.boss_key(f)}, " + ("every pull" if all_pulls else f"pull {rep.pull_no(f)}")
    guarded = server.guarded(hits)

    async def asked(report: str, boss: str | None = None, all_pulls: bool = False) -> str:
        return await guarded(pull=None, boss=boss, all_pulls=all_pulls, report=link + report)  # as the SDK calls it
    # the link chooses the boss, as it does for a tool about a boss's night, and no line names a pull
    assert await asked("#fight=21", all_pulls=True) == "Sszorak, every pull"
    assert await asked("#fight=2", all_pulls=True) == "Vashnik the Malignant, every pull"
    assert await asked("?fight=21", boss="Vashnik", all_pulls=True) == "Vashnik the Malignant, every pull"
    assert await asked("#fight=5", all_pulls=True) == (
        "Fight 5 in the link isn't a boss pull of this report (trash, or a fight it doesn't have), so the link was "
        "read as the report alone.\nSszorak, every pull")
    # a call for one pull right after is read as one: the link's pull, and trash is its error
    assert await asked("#fight=21") == "Fight 21 in the link is Sszorak pull 13.\nSszorak, pull 13"
    with pytest.raises(ToolError, match="fight 5 in the link isn't a boss pull of report"):
        await asked("#fight=5")


async def test_a_mechanic_no_guide_names_is_answered_from_its_tooltip(monkeypatch, rep, patch_wcl, fake_knowledge):
    """An ability asked for by its name in the log, which no guide has under this boss or another: what its tooltip
    says, by the ids the report has it under, where the call used to end in an error after a long search."""
    monkeypatch.setattr(server.DEFAULT, "library", server.Library(CODE, wcl=rep.wcl))
    patch_wcl(rep, all_encounters=[], zone_encounters=("The Venomous Abyss", [{"name": "Sszorak"}]))
    spell, name = next((i, n) for i, n in rep.abilities.items() if list(rep.abilities.values()).count(n) == 1)
    asked = []

    async def none_names(boss, raid, difficulty, mechanic, logged):
        assert name in logged  # the log's names, for the abilities listed when no guide names the one asked for
        raise server.KnowledgeError(f"no guide for {boss} names {mechanic!r}")

    async def tooltips(ids):
        asked.append(list(ids))
        return f"Tooltip (Wowhead, spell {spell}): It hurts." if ids else ""
    fake_knowledge(boss_mechanic_text=none_names, boss_naming=None, mechanic_tooltips_text=tooltips)
    told = f"No guide for Sszorak names {name!r}. What its tooltip says:\nTooltip (Wowhead, spell {spell}): It hurts."
    assert await server.call("boss_guide", {"boss": "Sszorak", "mechanic": name.upper()}) == (
        told.replace(repr(name), repr(name.upper())), False)
    assert await server.call("boss_guide", {"boss": "Sszorak", "mechanic": str(spell)}) == (told, False)
    assert asked == [[spell], [spell]]
    with pytest.raises(ToolError, match="no guide for Sszorak names 'Made Up'"):  # nothing of that name in the log
        await server.call("boss_guide", {"boss": "Sszorak", "mechanic": "Made Up"})


async def test_healing_over_a_bosss_pulls_counts_a_point_for_each_graph_window_of_its_long_pulls(monkeypatch, rep,
                                                                                                  patch_wcl):
    # The raid's damage taken is asked in windows of 235 s of pull, a point each: a night of 8-minute pulls costs
    # three a pull, where the guard counted one, the whole pull's graph.
    library = server.Library(CODE, wcl=rep.wcl)
    rep = await library.report()
    monkeypatch.setattr(server.DEFAULT, "library", library)
    asked = []

    async def ensure_points(need, what):
        asked.append(need)
        raise server.WCLError(f"{what} reads about {need} points")
    patch_wcl(rep, ensure_points=ensure_points)
    pulls = rep.by_boss[rep.boss("Sszorak")]
    with pytest.raises(ToolError, match="all_pulls reads about"):
        await server.call("healing", {"boss": "Sszorak", "all_pulls": True})
    windows = [len(server.taken.windows(f)) for f in pulls]
    assert asked == [(server.HEALING_PULL_POINTS - 1) * len(pulls) + sum(windows)]  # these, under 235 s: one each


async def test_a_players_deaths_over_the_weeks_are_refused_when_points_run_low(monkeypatch, rep, patch_wcl):
    library = server.Library(CODE, wcl=rep.wcl)
    rep = await library.report()
    monkeypatch.setattr(server.DEFAULT, "library", library)
    nights = [{"start": rep.start_ms, "codes": [CODE], "players": {"Enfo": 1}}] * 3

    async def raid_nights(*_):
        return nights, 5, {}
    monkeypatch.setattr(server.night, "raid_nights", raid_nights)
    asked = []

    async def ensure_points(need, what):
        asked.append(need)
        raise server.WCLError(f"{what} reads about {need} points: ask again in 5 min")
    patch_wcl(rep, ensure_points=ensure_points)
    text, failed = await server.call("player_history", {"player": "Enfo", "show": ["deaths"]})
    assert asked == [3 * server.DEATHS_NIGHT_POINTS]
    assert text == f"Not available: deaths over weeks reads about {asked[0]} points: ask again in 5 min"


async def test_progress_over_the_weeks_holds_the_reports_own_night_when_attendance_lacks_it(monkeypatch, rep,
                                                                                            patch_wcl):
    library = server.Library(CODE, wcl=rep.wcl)
    rep = await library.report()
    monkeypatch.setattr(server.DEFAULT, "library", library)
    week = 7 * 86400 * 1000
    listed = [{"start": rep.start_ms - week, "codes": ["EARLIER"], "players": {}}]
    left_out: dict[str, str] = {}
    read: list[list[str]] = []  # each night's logs, as they were loaded
    real = library.night

    async def raid_nights(*_):
        return list(listed), 5, left_out

    async def loaded(n, *args):  # another log reads as this one: only which nights and logs are read is under test
        read.append(n["codes"])
        return await real(n, *args) if n["codes"] == [CODE] else [rep]
    monkeypatch.setattr(server.night, "raid_nights", raid_nights)
    monkeypatch.setattr(library, "night", loaded)

    async def starts() -> list[float]:
        return [n.start_ms for n in await server.boss_nights(rep, "sszorak", 2)]
    # attendance lists last week's night and not yet this log's: its night stands after it, as the report dates it
    assert await starts() == [rep.start_ms - week, rep.start_ms] and read == [["EARLIER"], [CODE]]
    text, failed = await server.call("progress", {"boss": "sszorak", "weeks": 2})
    by_night = text[text.index("Sszorak night by night over the last 2 weeks"):].splitlines()
    assert not failed and len(by_night) == 4 and by_night[2].startswith(f"  {server.night.date(rep.start_ms)}: ")
    assert re.fullmatch(r"Best wipe night by night: ([\d.]+) → \1", by_night[3])
    # listed under its own code: as attendance has it
    listed[1:] = [{"start": rep.start_ms + 60_000, "codes": [CODE], "players": {}}]
    assert await starts() == [rep.start_ms - week, rep.start_ms + 60_000]
    # only another raider's log of the night is listed: this one is read beside it, as one night
    listed[1:] = [{"start": rep.start_ms - 3600 * 1000, "codes": ["THEIRS"], "players": {}}]
    assert await starts() == [rep.start_ms - week, rep.start_ms - 3600 * 1000] and read[-1] == ["THEIRS", CODE]
    assert listed[1]["codes"] == ["THEIRS"]  # the listed night itself is left as raid_nights gave it
    # a log left out as not the guild's (a pug logged to its page) stays out of the guild's nights, listed or not yet
    listed[1:], left_out[CODE] = [], "3 of 20 in the guild"
    assert await starts() == [rep.start_ms - week]
    left_out.clear()
    rep.guild_id, rep.guild_name = 14169, "The Cöpper Lanterns"
    patch_wcl(rep, guild_members={"Nobody"})
    assert await starts() == [rep.start_ms - week]
    patch_wcl(rep, guild_members={p.name for p in rep.players.values()})
    assert await starts() == [rep.start_ms - week, rep.start_ms]


async def test_find_reports_takes_the_current_reports_guild_without_a_realm(monkeypatch, rep, patch_wcl):
    library = server.Library(CODE, wcl=rep.wcl)
    rep = await library.report()
    rep.guild_id, rep.guild_name = 14169, "The Cöpper Lanterns"
    monkeypatch.setattr(server.DEFAULT, "library", library)
    asked = []

    async def no_lookup(*_):
        raise AssertionError("looked the guild up by its realm")

    async def reports(guild_id, *_, **__):
        asked.append(guild_id)
        return [{"code": "A", "title": "Night", "startTime": rep.start_ms, "players": [],
                 "pulls_by_difficulty": {5: 9}, "bosses": {"Sszorak": (9, 0)}}]
    patch_wcl(rep, find_guild=no_lookup, guild_members=set(), guild_reports=reports)
    text, failed = await server.call("find_reports", {"guild": "the copper lanterns", "days": 7})
    assert not failed and asked == [14169]
    assert text.startswith("Reports in the last 7 days, The Cöpper Lanterns:\n- A: Night (The Cöpper Lanterns)")
    # "the last 7 days" count back from clock.now(), for frozen data (the evals) when it was recorded: a log
    # started after that isn't listed
    monkeypatch.setattr(server.clock, "now", lambda: rep.start_ms / 1000 + 60)
    seen = []

    async def listed(guild_id, since_ms, **_):
        seen.append(since_ms)
        return [{**(await reports(guild_id))[0], "code": "later", "startTime": rep.start_ms + 120_000},
                *await reports(guild_id)]
    patch_wcl(rep, find_guild=no_lookup, guild_members=set(), guild_reports=listed)
    text, _ = await server.call("find_reports", {"guild": "the copper lanterns", "days": 7})
    assert seen == [rep.start_ms + 60_000 - 7 * 86400 * 1000] and "- A: Night" in text and "later" not in text
    # another guild still needs its realm and region, and the error names the current report's guild
    with pytest.raises(ToolError, match="realm.*the current report's guild, The Cöpper Lanterns, needs neither"):
        await server.call("find_reports", {"guild": "Brightleaf Company"})
    # no guild, and no sign-in or saved login to know the user's guilds (client credentials alone): the report's
    monkeypatch.setattr(server.wcl, "saved_login", lambda: None)
    asked.clear()
    patch_wcl(rep, find_guild=no_lookup, guild_members=set(), guild_reports=reports)
    text, failed = await server.call("find_reports", {"days": 7})
    assert not failed and asked == [14169] and "- A: Night (The Cöpper Lanterns)" in text


async def test_find_reports_marks_an_easier_run_after_the_guilds_raid_night(monkeypatch, rep, patch_wcl):
    library = server.Library(CODE, wcl=rep.wcl)
    rep = await library.report()
    rep.guild_id, rep.guild_name = 14169, "The Cöpper Lanterns"
    monkeypatch.setattr(server.DEFAULT, "library", library)

    async def reports(*_, **__):
        def night(code: str, difficulty: int, pulls: int, at: float, **more) -> dict:
            return {"title": "Night", "players": ["Torvek"], "code": code, "startTime": at,
                    "bosses": {"Sszorak": (pulls, 0)}, "pulls_by_difficulty": {difficulty: pulls},
                    "bosses_by_difficulty": {(difficulty, "Sszorak"): (pulls, 0)}, **more}
        return [night("HEROIC", 4, 13, rep.start_ms), night("MYTHIC", 5, 34, rep.start_ms - 1),
                night("PUG", 5, 9, rep.start_ms - 2, players=["Someone"])]
    patch_wcl(rep, guild_members={"Torvek"}, guild_reports=reports)
    text, failed = await server.call("find_reports", {"guild": "the copper lanterns", "days": 7})
    lines = text.splitlines()
    assert not failed and lines[1].endswith("an easier run than the guild's Mythic raids in these days, not its raid "
                                            "night")
    assert lines[2].endswith("boss pulls Mythic 34: Sszorak 34") and lines[3].endswith("a pug, not a guild raid night")


async def test_each_call_is_kept_with_its_result_or_error(monkeypatch):
    """Serving many users, each call goes to the server's `record`: who called which tool, with what, and what it
    returned."""
    kept = []
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str,
                                                               record=lambda *a: kept.append(a)))

    async def found(pull: int | None = None, boss: str | None = None) -> str:
        return f"pull {pull}"

    async def missing(pull: int) -> str:
        raise FactError("Sszorak has no pull 99")

    assert await server.guarded(found)(pull=13) == "pull 13"
    with pytest.raises(ToolError):
        await server.guarded(missing)(pull=99)
    # (user, client, tool, args, started, duration_s, failed, result); no signed-in user outside a request
    assert [(k[0], k[2], k[3], k[6], k[7]) for k in kept] == [
        (None, "found", {"pull": 13}, False, "pull 13"),  # arguments left out are left out
        (None, "missing", {"pull": 99}, True, "Sszorak has no pull 99")]
    assert all(k[5] >= 0 for k in kept)


async def test_a_long_argument_is_logged_cut_and_kept_whole(monkeypatch, caplog):
    """A pasted raid note is up to 100,000 characters: the log line of a call says who asked what, not all of it."""
    kept = []
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str,
                                                               record=lambda *a: kept.append(a[3])))

    async def save(pasted: str, boss: str | None = None) -> str:
        return "saved"
    with caplog.at_level("INFO", "wipebot.calls"):
        await server.guarded(save)(pasted="EncounterID:3420 " * 1000, boss="Sszorak")
    line = next(r.getMessage() for r in caplog.records if r.name == "wipebot.calls")
    assert len(line) < 500 and "... (17,000 characters)" in line and "'boss': 'Sszorak'" in line
    assert kept == [{"pasted": "EncounterID:3420 " * 1000, "boss": "Sszorak"}]


async def test_a_signed_in_users_calls_read_warcraft_logs_with_their_own_token(monkeypatch):
    """So their own Warcraft Logs points pay first; someone else's call, or one with no user, reads as the server."""
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from wipebot.core import wcl
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(
        guilds=list, user_name=str, wcl_token=lambda subject: {"1": "raider's token"}.get(subject)))

    async def reads_as() -> str | None:
        return wcl._user.get()

    for subject, token in (("1", "raider's token"), ("2", None), (None, None)):
        auth_context_var.set(AuthenticatedUser(AccessToken(token="t", client_id="c", scopes=[], subject=subject))
                             if subject else None)
        assert await server.guarded(reads_as)() == token
    assert wcl._user.get() is None  # and only during the call


async def test_a_call_out_of_time_says_what_it_was_waiting_for(monkeypatch):
    """No tool call had a limit of its own: a source that hung held the answer until the client gave up, with
    nothing said. The model is told which source it was, and that asking again goes on from what was read."""
    from wipebot.core.cache import waiting_for

    async def slow(pull: int) -> str:
        with waiting_for("Warcraft Logs"):
            await asyncio.gather(reads("Wowhead"), reads("Lorrgs"), asyncio.sleep(10))
        return "an answer"

    async def reads(source: str) -> None:
        with waiting_for(source):
            await asyncio.sleep(10)
    monkeypatch.setattr(server, "DEADLINE_S", 0.01)
    with pytest.raises(ToolError) as told:
        await server.guarded(slow)(pull=1)
    assert str(told.value) == (
        "slow gave no answer in 0.01 s, still waiting for Lorrgs, Warcraft Logs and Wowhead. What it had read by "
        "then is kept, so asking again takes less; a call about one pull or one player is quicker than one about a "
        "whole night.")

    async def quick(pull: int) -> str:
        with waiting_for("Warcraft Logs"):
            await asyncio.sleep(0)
        return "an answer"
    assert await server.guarded(quick)(pull=1) == "an answer"


async def test_serving_many_users_a_call_out_of_time_or_left_by_its_client_is_kept_with_what_it_waited_for(
        monkeypatch):
    """Claude and ChatGPT give up on a call at 60 s: the server answers a little before, and the record of a call
    its client left says so, with how long it ran, where it was kept blank."""
    from wipebot.core.cache import waiting_for
    kept = []
    remote = server.Setup(remote=server.Remote(guilds=list, user_name=str, record=lambda *a: kept.append(a[6:8])))

    async def slow(pull: int) -> str:
        with waiting_for("Wowhead"):
            await asyncio.sleep(10)
        return "an answer"
    assert server.REMOTE_DEADLINE_S < 60 < server.DEADLINE_S
    monkeypatch.setattr(server, "REMOTE_DEADLINE_S", 0.01)
    with pytest.raises(ToolError, match="slow gave no answer in 0.01 s, still waiting for Wowhead."):
        await server.guarded(slow, remote)(pull=1)
    monkeypatch.setattr(server, "REMOTE_DEADLINE_S", 50)
    call = asyncio.ensure_future(server.guarded(slow, remote)(pull=1))
    await asyncio.sleep(0.01)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert kept[0][0] and kept[0][1].startswith("slow gave no answer in 0.01 s, still waiting for Wowhead.")
    assert kept[1] == (True, "cancelled after 0 s, still waiting for Wowhead")


async def test_an_unexpected_error_names_itself():
    async def broken(pull: int) -> str:
        raise TimeoutError()

    with pytest.raises(ToolError, match="broken failed with an unexpected error: TimeoutError"):
        await server.guarded(broken)(pull=1)


async def test_serving_many_users_an_unexpected_error_is_told_by_an_id_and_kept_in_full(monkeypatch, caplog):
    kept = []
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str,
                                                               record=lambda *a: kept.append(a)))

    async def broken(pull: int) -> str:
        raise RuntimeError("/srv/secret/path is gone")

    with pytest.raises(ToolError) as told:
        await server.guarded(broken)(pull=1)
    error_id = re.fullmatch(r"broken failed with an unexpected error \(error id (\w+)\)", str(told.value))
    assert error_id and "/srv/secret" not in str(told.value)
    assert f"error id {error_id[1]}" in caplog.text and "/srv/secret/path" in caplog.text
    assert kept[0][6] and "RuntimeError: /srv/secret/path is gone" in kept[0][7]


def test_one_user_is_held_to_their_points_and_calls_in_the_last_hour(monkeypatch):
    """So one user of a server many guilds share can't spend everyone's Warcraft Logs points."""
    now = [1000.0]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    limits = server.UserLimits(points=100, calls=3)
    limits.add("1", {"total": 60, "application": 5})
    assert limits.refusal("1") is None and limits.refusal("2") is None
    now[0] += 600
    limits.add("1", {"total": 50, "application": 5})
    assert limits.refusal("1") == ("not answered: this user has asked for 110 Warcraft Logs points in the last "
                                   "hour, and this server gives one user 100 an hour; ask again in 50 min (a tool that "
                                   "takes no report still answers)")
    assert limits.refusal("2") is None
    now[0] += 3001  # the first call is over an hour old
    assert limits.refusal("1") is None
    limits.add("1", {})
    limits.add("1", {})
    assert limits.refusal("1") == ("not answered: this user has asked for 3 tool calls in the last hour, and this "
                                   "server gives one user 3 an hour; ask again in 10 min")
    # on the new point system, the user's own points are theirs: only what the account pays counts
    own = server.UserLimits(points=100, own_points=True)
    for _ in range(10):
        own.add("1", {"total": 60, "application": 5})
    assert own.refusal("1") is None


def test_the_wait_a_refused_user_is_told_is_until_their_points_are_under_the_limit(monkeypatch):
    """Not until their oldest call is an hour old, whatever it cost: after that wait they were refused again."""
    now = [0.0]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    def told(spent: int, minutes: int) -> str:
        return (f"not answered: this user has asked for {spent} Warcraft Logs points in the last hour, and this "
                f"server gives one user 300 an hour; ask again in {minutes} min (a tool that takes no report still "
                "answers)")
    limits = server.UserLimits(points=300)
    limits.add("1", {"total": 2})
    now[0] = 30 * 60
    limits.add("1", {"total": 400})
    now[0] = 31 * 60
    assert limits.refusal("1") == told(402, 59)
    now[0] = 60 * 60 + 1  # the cheap call is over an hour old: still over
    assert limits.refusal("1") == told(400, 30)
    now[0] = 90 * 60 + 1
    assert limits.refusal("1") is None
    # both limits reached: the later of the two waits
    both = server.UserLimits(points=300, calls=2)
    now[0] = 0
    both.add("1", {"total": 1})
    now[0] = 20 * 60
    both.add("1", {"total": 400})
    assert both.refusal("1") == told(401, 60)


async def test_calls_sent_at_once_are_counted_as_they_start(monkeypatch):
    """Claude and ChatGPT send a turn's tool calls together: counted as each ended, all of them passed the check."""
    from mcp.server.auth.middleware import auth_context
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1", "client_id": "c"})())
    limits = server.UserLimits(points=100, calls=2)
    setup = server.Setup(remote=server.Remote(guilds=list, user_name=str, limits=limits))

    async def tool() -> str:
        await asyncio.sleep(0.01)
        return "ok"
    got = await asyncio.gather(*(server.guarded(tool, setup)() for _ in range(8)), return_exceptions=True)
    assert [r for r in got if r == "ok"] == ["ok", "ok"]
    assert all("asked for 2 tool calls" in str(r) for r in got if r != "ok") and len(limits._spent["1"]) == 2


async def test_past_the_points_limit_a_tool_that_reads_no_log_still_answers(monkeypatch):
    """The points are Warcraft Logs': a tool that takes no report (a spell's tooltip, a spec's guide) spends none."""
    from mcp.server.auth.middleware import auth_context
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1", "client_id": "c"})())
    limits = server.UserLimits(points=100, calls=4)
    limits.add("1", {"total": 150})
    setup = server.Setup(remote=server.Remote(guilds=list, user_name=str, limits=limits))

    async def of_a_log(report: str | None = None) -> str:
        return "ok"

    async def of_the_game(spell: str = "") -> str:
        return "ok"
    with pytest.raises(ToolError, match="asked for 150 Warcraft Logs points"):
        await server.guarded(of_a_log, setup)()
    assert await server.guarded(of_the_game, setup)() == "ok" and await server.guarded(of_the_game, setup)() == "ok"
    assert await server.guarded(of_the_game, setup)() == "ok"
    with pytest.raises(ToolError, match="asked for 4 tool calls"):  # the calls limit holds for every tool
        await server.guarded(of_the_game, setup)()


async def test_a_call_past_the_users_limit_is_refused_and_not_counted(monkeypatch):
    from mcp.server.auth.middleware import auth_context
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1", "client_id": "c"})())
    kept = []
    limits = server.UserLimits(calls=1)
    setup = server.Setup(remote=server.Remote(guilds=list, user_name=str, limits=limits,
                                              record=lambda *a: kept.append(a[6:8])))

    async def tool() -> str:
        return "ok"
    guarded = server.guarded(tool, setup)
    assert await guarded() == "ok"
    with pytest.raises(ToolError, match="asked for 1 tool call in the last hour"):
        await guarded()
    assert len(limits._spent["1"]) == 1 and kept == [(False, "ok"), (True, kept[1][1])]


@pytest.mark.parametrize("part", [None, *server.PARTS])
def test_the_method_is_the_skill_with_its_parts_named_by_part(part):
    """analysis_method gives the skill's steps, the asked part and the examples, and names a part by the `part`
    that gives it, never by a file the client can't open."""
    text = server.method(part)
    files = {p.name for p in server.SKILL.iterdir() if p.name.endswith(".md")}
    assert set(server.PARTS.values()) | {"SKILL.md", "examples.md"} == files  # every part has its word
    assert not re.search(r"\]\([\w-]+\.md\)|\b[\w-]+\.md\b", text)
    assert text.startswith("# Analysing raid logs") and "# Worked examples" in text
    for word, file in server.PARTS.items():
        title = server.SKILL.joinpath(file).read_text(encoding="utf-8").splitlines()[0]
        assert (title in text) == (word == part), word
        assert f"(part `{word}`)" in text or word == part
