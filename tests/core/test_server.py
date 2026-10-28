"""The tools as MCP clients see them (server.py): their list, schemas and descriptions, the skill, and errors."""
import inspect
import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.support import CODE, NOTE
from tests.support.recorded import CASES
from wipebot.core import notebook, server
from wipebot.core.report import FactError

# The tools that don't read a report, so take none; every other tool takes `report`.
NO_REPORT = {"spell_info", "spec_guide", "hotfixes", "web_lookup"}
# The tools without a case in cases.toml, and where they're tested instead.
TESTED_ELSEWHERE = {
    "hotfixes": "knowledge/test_news.py (a snapshot would date: it counts back from today)",
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
        night = {"title": "Night", "players": ["Torvek"], "bosses": {"Sszorak": (9, 0)}}
        return [{**night, "code": "HEROIC", "startTime": rep.start_ms, "pulls_by_difficulty": {4: 13}},
                {**night, "code": "MYTHIC", "startTime": rep.start_ms - 1, "pulls_by_difficulty": {5: 34}},
                {**night, "code": "PUG", "startTime": rep.start_ms - 2, "players": ["Someone"],
                 "pulls_by_difficulty": {5: 9}}]
    patch_wcl(rep, guild_members={"Torvek"}, guild_reports=reports)
    text, failed = await server.call("find_reports", {"guild": "the copper lanterns", "days": 7})
    lines = text.splitlines()
    assert not failed and lines[1].endswith("an easier run than the guild's Mythic raids in these days, not its raid "
                                            "night")
    assert lines[2].endswith("Sszorak 9") and lines[3].endswith("a pug, not a guild raid night")


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
                                   "hour, the most this server gives one user; ask again in 50 min")
    assert limits.refusal("2") is None
    now[0] += 3001  # the first call is over an hour old
    assert limits.refusal("1") is None
    limits.add("1", {})
    limits.add("1", {})
    assert "asked for 3 tool calls" in (limits.refusal("1") or "")
    # on the new point system, the user's own points are theirs: only what the account pays counts
    own = server.UserLimits(points=100, own_points=True)
    for _ in range(10):
        own.add("1", {"total": 60, "application": 5})
    assert own.refusal("1") is None


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
