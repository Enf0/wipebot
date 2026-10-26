"""The session: the core's tools as the model calls them, the bot's own tools, and what's made of an answer."""
import json
import os
from types import SimpleNamespace
from typing import cast

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError, Report
from wipebot.discord import host
from wipebot.discord.host import Answer, Context, ModelError, Session, ToolCall, context_size, sdk_tools
from wipebot.discord.prompt import FOLLOW_PULLS, REMEMBER

GUILD = "The Copper Lanterns, argent-dawn, eu"


async def test_tools_get_the_sessions_report_unless_named(core_calls):
    tools = {t.name: t for t in await sdk_tools("ReportOfServerA1")}
    await tools["pull_summary"].handler({"pull": 13})
    await tools["pull_summary"].handler({"pull": 2, "report": "OtherReport00001"})
    await tools["spell_info"].handler({"spell": "1277027"})
    assert core_calls == [("pull_summary", {"pull": 13, "report": "ReportOfServerA1"}),
                             ("pull_summary", {"pull": 2, "report": "OtherReport00001"}),
                             ("spell_info", {"spell": "1277027"})]


@pytest.mark.parametrize("asked, called", [
    ({"guild": "the copper lanterns", "days": 10},
     {"guild": "the copper lanterns", "days": 10, "server": "argent-dawn", "region": "eu"}),
    ({"days": 10},  # no guild: "the user's guilds", which Discord has none of
     {"days": 10, "guild": "The Copper Lanterns", "server": "argent-dawn", "region": "eu"}),
    ({"guild": "The Copper Lanterns", "server": "Argent Dawn", "region": "EU"},
     {"guild": "The Copper Lanterns", "server": "Argent Dawn", "region": "EU"}),  # as asked
    ({"guild": "Echo"}, {"guild": "Echo"}),  # another guild
])
async def test_find_reports_for_the_servers_own_guild_gets_its_realm_and_region(core_calls, asked, called):
    tools = {t.name: t for t in await sdk_tools(None, guild=lambda: GUILD)}
    await tools["find_reports"].handler(asked)
    assert core_calls == [("find_reports", called)]


async def test_a_tool_error_goes_to_the_model_as_a_failed_call(monkeypatch):
    from wipebot.core.report import FactError

    async def failing(name, args):
        raise FactError("no pull 99 of Sszorak")
    monkeypatch.setattr("wipebot.core.server.call_with_images", failing)
    recorded: list[ToolCall] = []
    tools = {t.name: t for t in await sdk_tools("R", recorded.append)}
    result = await tools["pull_summary"].handler({"pull": 99})
    assert result["is_error"] and result["content"][0]["text"] == "no pull 99 of Sszorak"
    assert [(c.name, c.failed) for c in recorded] == [("pull_summary", True)]


async def test_a_tools_view_goes_with_the_answer_as_a_picture(core_calls, monkeypatch):
    drawn = []

    async def picture(name, args):
        drawn.append((name, args))
        return [b"WEBP"]
    monkeypatch.setattr(host, "picture", picture)
    recorded: list[ToolCall] = []
    monkeypatch.setattr(host, "_no_pictures", "")
    tools = {t.name: t for t in await sdk_tools("R", recorded.append)}
    await tools["pull_summary"].handler({"pull": 13})  # returns at once: the model goes on while it's drawn
    await tools["spell_info"].handler({"spell": "1277027"})  # no view
    assert [c.drawing is not None for c in recorded] == [True, False]
    await host.pictures_drawn(recorded)  # what an answer waits for before it's sent
    assert drawn == [("pull_summary", {"pull": 13, "report": "R"})]
    assert [c.images for c in recorded] == [[b"WEBP"], []]
    assert Answer("", calls=recorded).images == [b"WEBP"]


def test_an_answer_sends_its_maps_and_its_first_view_alone():
    def call(name, *images):
        return ToolCall(name, {}, "", False, list(images))
    calls = [call("spell_info"), call("player_night", b"N1", b"N2"), call("pull_map", b"M"), call("player_pull", b"P")]
    assert Answer("", calls=calls).images == [b"N1", b"N2", b"M"]  # the pull's card is a click away from the night's


async def test_a_picture_that_cannot_be_drawn_leaves_the_answer_without_it(monkeypatch):
    class Painter:
        def __init__(self, error):
            self.error, self.asked = error, 0

        async def pictures(self, name, args):
            self.asked += 1
            raise self.error
    monkeypatch.setattr(host, "_no_pictures", "")
    broken = Painter(RuntimeError("not drawn: Timeout 60000ms exceeded"))
    monkeypatch.setattr(host, "_painter", broken)
    assert await host.picture("pull_summary", {}) == [] and await host.picture("pull_summary", {}) == []
    assert broken.asked == 2  # one view that didn't draw: the next is tried
    missing = Painter(Exception("BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium"))
    monkeypatch.setattr(host, "_painter", missing)
    assert await host.picture("pull_summary", {}) == [] and await host.picture("pull_summary", {}) == []
    assert missing.asked == 1 and "Chromium isn't installed" in host._no_pictures  # said once, then not tried


async def test_each_tool_call_keeps_what_its_warcraft_logs_queries_cost(monkeypatch):
    from wipebot.core import wcl

    async def costing(name, args):
        points = wcl._points.get()
        assert points is not None  # counted during the call
        points.add({"total": 3, "database": 1})  # as a query run during the call adds its cost
        return "Sszorak pull 13", False, []
    monkeypatch.setattr("wipebot.core.server.call_with_images", costing)
    recorded: list[ToolCall] = []
    tools = {t.name: t for t in await sdk_tools("R", recorded.append)}
    await tools["pull_summary"].handler({"pull": 13})
    await tools["pull_summary"].handler({"pull": 14})
    assert recorded[0].record(0)["wcl_points"] == {"total": 3, "database": 1, "queries": 1}
    assert Answer("", calls=recorded).wcl_points() == {"total": 6, "database": 2, "queries": 2}


async def test_raid_note_in_discord_gets_the_saved_note_not_one_from_the_model(core_calls):
    recorded: list[ToolCall] = []

    async def saved(args):
        return ("EncounterID:3420;...", "saved by Rowan") if args.get("pull") != 2 else "No raid note is saved."
    tools = {t.name: t for t in await sdk_tools("ReportOfServerA1", recorded.append, note=saved)}
    assert "note" not in tools["raid_note"].input_schema["properties"]
    result = await tools["raid_note"].handler({"pull": 13})
    assert core_calls == [("raid_note", {"pull": 13, "report": "ReportOfServerA1", "note": "EncounterID:3420;..."})]
    assert result["content"][0]["text"] == "(The note was saved by Rowan.)\nchecked"
    assert "note" not in recorded[0].args  # the same in every call: not kept with each answer
    result = await tools["raid_note"].handler({"pull": 2})
    assert len(core_calls) == 1 and result["content"][0]["text"] == "No raid note is saved."


async def test_raid_plan_in_discord_gets_the_saved_versions_not_a_plan_from_the_model(core_calls):
    async def saved(args):
        return "[saved 1000 by Rowan] raidstrats-addon-A\n[saved 2000 by Rowan] raidstrats-addon-B", ""
    tools = {t.name: t for t in await sdk_tools("ReportOfServerA1", plan=saved)}
    assert "plan" not in tools["raid_plan"].input_schema["properties"]
    assert "note" in tools["raid_note"].input_schema["properties"]  # untouched without saved notes
    result = await tools["raid_plan"].handler({"all_pulls": True})
    assert core_calls[0][1]["plan"].count("raidstrats-addon-") == 2
    assert result["content"][0]["text"] == "checked"  # where each version is from is in the tool's own text


async def test_in_discord_raid_note_and_raid_plan_say_they_check_the_servers_saved_ones():
    async def saved(args):
        return "none"
    plain = {t.name: t.description for t in await sdk_tools("R")}
    tools = {t.name: t.description for t in await sdk_tools("R", note=saved, plan=saved)}
    assert "saved in this server checked against" in tools["raid_note"] and tools["raid_note"] != plain["raid_note"]
    assert "plan from raidstrats.gg saved in this server" in tools["raid_plan"]


def session_on(report: object, code: str = "ReportOfServerA1") -> Session:
    async def get(code, max_age_s=0):
        return report
    return Session(cast(server.Library, SimpleNamespace(report=get)), code)


async def test_raid_note_gets_the_servers_saved_note_and_over_all_pulls_every_version():
    session = session_on(SimpleNamespace(fight=lambda pull, boss: {"encounterID": 3420, "startTime": 0,
                                                                   "name": "Sszorak"}, start_ms=1_500_000,
                                    end_ms=2_500_000))
    book = Notebook(":memory:")
    book.save_note(14169, 3420, "Sszorak", "EncounterID:3420;a", "Rowan", at=1000.0)
    book.save_note(14169, 3420, "Sszorak", "EncounterID:3420;b", "Ha]lvard", at=2000.0)
    session._ctx = Context(notebook=book, guild_id=14169)
    assert await session._saved_note({"all_pulls": True}) == (
        "[saved 1000 by Rowan]\nEncounterID:3420;a\n[saved 2000 by Ha)lvard]\nEncounterID:3420;b", "")
    assert await session._saved_note({"pull": 3}) == ("EncounterID:3420;a", "saved by Rowan on Thu 01 Jan 00:16 UTC")
    session._ctx = Context(notebook=book, guild_id=1)  # another server's guild
    told = await session._saved_note({"pull": 3})
    assert isinstance(told, str) and told.startswith("No raid note is saved for Sszorak that night in this server.")
    session._ctx = Context()  # not in Discord
    assert await session._saved_note({}) == "Raid notes are only kept in Discord: there's none here."


async def test_raid_plan_gets_every_version_the_server_saved_for_the_pulls_boss(monkeypatch):
    for name in ("RAIDSTRATS_CLIENT_ID", "RAIDSTRATS_CLIENT_SECRET"):  # a developer's .env may set them
        monkeypatch.delenv(name, raising=False)
    def boss(name):
        if name != "Sszorak":
            raise FactError(f"no boss matching {name!r}")
        return name
    session = session_on(SimpleNamespace(fight=lambda pull, boss: {"id": 1}, boss_key=lambda f: "Sszorak", boss=boss))
    book = Notebook(":memory:")
    book.save_plan(14169, "Sszorak", "p1", "Spread", "raidstrats-addon-A", "Rowan", at=1000.0)
    book.save_plan(14169, "Vashnik", "p2", "Soak", "raidstrats-addon-V", "Rowan", at=1500.0)
    book.save_plan(14169, "Sszorak", "p1", "Spread", "raidstrats-addon-B", "Ha]lvard", at=2000.0)
    session._ctx = Context(notebook=book, guild_id=14169)
    plans, source = await session._saved_plan({})
    assert plans == "[saved 1000 by Rowan] raidstrats-addon-A\n[saved 2000 by Ha)lvard] raidstrats-addon-B"
    assert source == "saved from pastes"  # no raidstrats.gg app: /wipebot raidstrats isn't there to name
    session._ctx = Context(notebook=book, guild_id=1)
    told = await session._saved_plan({})
    assert told == ("No raid plan is saved for Sszorak in this server. Officers save one by pasting the plan's "
                    "export from raidstrats.gg (the planner's Export button) to the bot." + server.NO_JOBS)
    monkeypatch.setenv("RAIDSTRATS_CLIENT_ID", "app")
    monkeypatch.setenv("RAIDSTRATS_CLIENT_SECRET", "secret")
    told = await session._saved_plan({})
    assert isinstance(told, str) and "or have the plans fetched from raidstrats.gg with /wipebot raidstrats" in told
    assert "no raidstrats.gg login is connected" in told
    session._ctx = Context(notebook=book, guild_id=14169)
    every = ("[saved 1000 by Rowan] raidstrats-addon-A\n[saved 1500 by Rowan] raidstrats-addon-V\n"
             "[saved 2000 by Ha)lvard] raidstrats-addon-B")
    # the plan as it is needs no pull: every plan, and raid_plan picks the boss's
    assert (await session._saved_plan({"boss": "Vashnik"}))[0] == every  # not pulled in the report
    assert (await session._saved_plan({"show": True}))[0] == every


async def test_raid_note_for_a_boss_not_pulled_yet_gets_the_newest_one_saved():
    def boss(name):
        if name != "Sszorak":
            raise FactError(f"no boss matching {name!r}")
        return name
    async def name_spells(ids):
        pass
    session = session_on(SimpleNamespace(boss=boss, guild_name="The Copper Lanterns", name_spells=name_spells,
                                         ability_ref=lambda i: f"spell [{i}]"))
    book = Notebook(":memory:")
    book.save_note(14169, 3418, "Vashnik", "EncounterID:3418;old", "Rowan", at=1000.0)
    book.save_note(14169, 3418, "Vashnik", "EncounterID:3418;new", "Rowan", at=2000.0)
    session._ctx = Context(notebook=book, guild_id=14169)
    told = await session._saved_note({"boss": "Vashnik"})
    assert isinstance(told, str) and told.startswith("Vashnik has no pull in this report, so here is the newest")
    assert told.endswith("EncounterID:3418;new")
    told = await session._saved_note({"boss": "Coiled Altar"})
    assert isinstance(told, str) and "has no raid note saved for it" in told


async def test_the_bots_own_tools_run_the_questions_handler():
    session = session_on(None)
    remember, follow = session._bot_tool(REMEMBER), session._bot_tool(FOLLOW_PULLS)
    remembered = []

    async def keep(args):
        remembered.append(args)
        return "Remembered."
    returned = []
    session._ctx = Context(tools={"remember": keep}, on_done=lambda name, args: returned.append((name, args)))
    assert (await remember.handler({"note": "answer in English"}))["content"][0]["text"] == "Remembered."
    assert remembered == [{"note": "answer in English"}]
    text = (await follow.handler({"stop": True}))["content"][0]["text"]
    assert text == FOLLOW_PULLS.elsewhere  # no handler for this question: not in Discord
    assert [(c.name, c.result) for c in session._calls] == [("remember", "Remembered."),
                                                             ("follow_pulls", FOLLOW_PULLS.elsewhere)]
    assert returned == [("remember", {"note": "answer in English"}), ("follow_pulls", {"stop": True})]


def test_context_size_is_what_the_last_api_call_read_and_wrote():
    usage = {"input_tokens": 8, "cache_creation_input_tokens": 24717, "cache_read_input_tokens": 150815,
             "output_tokens": 3499, "iterations": [{"input_tokens": 2, "output_tokens": 2212,
                                                    "cache_read_input_tokens": 49451,
                                                    "cache_creation_input_tokens": 6236}]}
    assert context_size(usage) == 2 + 2212 + 49451 + 6236  # the last call, not the answer's total
    assert context_size({"input_tokens": 5, "output_tokens": 7}) == 12  # no iterations: the totals
    assert context_size(None) == 0


class FakeClient:
    """The SDK's client, answering each question with the messages given for it."""

    def __init__(self, *answers: list):
        self.answers, self.queries = list(answers), []

    async def query(self, text):
        self.queries.append(text)

    async def receive_response(self):
        for msg in self.answers.pop(0):
            yield msg


def result(cost: float, tokens: int) -> ResultMessage:
    return ResultMessage("success", 10, 10, False, 3, "s1", total_cost_usd=cost, usage={"input_tokens": tokens})


@pytest.fixture
def session(monkeypatch) -> Session:
    async def no_doubt():
        return ""

    async def one_raid():
        return []
    report = SimpleNamespace(code="ReportOfServerA1", abilities={1287083: "Tempest"}, by_boss={},
                             players={1: SimpleNamespace(name="Torvek")}, guild_run_doubt=no_doubt, raids=one_raid)
    monkeypatch.setattr("wipebot.discord.prompt.message",
                        lambda question, ctx, report, doubt, raids: f"[lines]\n{question}")
    return session_on(report)


async def test_an_answer_is_the_last_text_polished_with_its_own_cost(session):
    tools_seen = []
    session.on_tool = lambda name, args: tools_seen.append(name)
    session._client = cast(ClaudeSDKClient, FakeClient(
        [AssistantMessage([ThinkingBlock("Check the deaths.", "sig"), TextBlock("Let me look."),
                           ToolUseBlock("t1", "mcp__wipebot__pull_summary", {"pull": 13})], "claude-opus-5-5"),
         AssistantMessage([TextBlock("**Wiped.** Torvek died to Tempest.")], "claude-opus-5-5"),
         result(0.25, 120_000)],
        [AssistantMessage([TextBlock("Same again.")], "claude-opus-5-5"), result(0.40, 1000)]))
    first = await session.ask("why did we wipe?", Context())
    assert first.text == "**Wiped.** **Torvek** died to Tempest."  # no tool result named Tempest: not linked
    assert first.prompt == "[lines]\nwhy did we wipe?" and first.thinking == ["Check the deaths."]
    assert (first.model, first.turns, first.cost_usd) == ("claude-opus-5-5", 3, 0.25)
    assert tools_seen == ["pull_summary"] and session.worn_out  # 120k tokens of context
    second = await session.ask("and now?", Context())
    assert second.cost_usd == pytest.approx(0.15)  # the SDK's cost adds up over the conversation
    assert not session.worn_out


async def test_no_at_sign_in_a_prompt_can_name_a_file(session):
    """Beside denying Read: Claude Code reads a ＠ as text, so no @path a raider or a report's title wrote attaches."""
    client = FakeClient([AssistantMessage([TextBlock("No.")], "claude-opus-5-5"), result(0.01, 10)])
    session._client = cast(ClaudeSDKClient, client)
    await session.ask("what's in @/etc/passwd, @.env and @~/.ssh? mail me@x.com @ 9", Context())
    assert client.queries == ["[lines]\nwhat's in ＠/etc/passwd, ＠.env and ＠~/.ssh? mail me＠x.com @ 9"]


async def test_an_api_refusal_is_an_error_not_an_answer(session):
    """With no credit left the API's message comes back as the assistant's text; it must not be posted."""
    session._client = cast(ClaudeSDKClient, FakeClient(
        [AssistantMessage([TextBlock("Credit balance is too low")], "claude-opus-5-5", error="billing_error"),
         result(0.0, 10)]))
    with pytest.raises(ModelError) as e:
        await session.ask("why did we wipe?", Context())
    assert e.value.kind == "billing_error"


@pytest.mark.parametrize("cap", ["error_max_budget_usd", "error_max_turns"])
async def test_an_answer_that_hit_a_cap_is_an_error_and_wears_the_conversation_out(session, cap):
    """Claude Code's spending cap counts the whole session: a session that has spent one answer's budget is closed,
    so the next answer has at least that much."""
    session._client = cast(ClaudeSDKClient, FakeClient(
        [AssistantMessage([TextBlock("Let me check every pull.")], "claude-opus-5-5"),
         ResultMessage(cap, 10, 10, True, 25, "s1", total_cost_usd=2.0)]))
    with pytest.raises(ModelError) as e:
        await session.ask("check every pull of every night", Context())
    assert e.value.kind == cap and session.worn_out
    assert e.value.answer and (e.value.answer.cost_usd, e.value.answer.turns, e.value.answer.text) == (2.0, 25, "")


async def test_an_answer_about_another_night_reads_that_report_too():
    """The session is on tonight's report; the question was about Monday's, which the tools read."""
    tonight, monday = SimpleNamespace(code="TONIGHT"), SimpleNamespace(code="MONDAY")

    async def report(code, max_age_s=0):
        return {"MONDAY": monday}[code]
    session = Session(cast(server.Library, SimpleNamespace(report=report)), "TONIGHT")
    session.report = cast(Report, tonight)
    calls = [ToolCall("progress", {"report": "MONDAY"}, "", False),
             ToolCall("roster", {"report": "nonsense"}, "", True), ToolCall("roster", {"report": "TONIGHT"}, "", False)]
    assert [r.code for r in await session._reports_read(calls)] == ["TONIGHT", "MONDAY"]


async def test_a_session_attaches_no_file_a_prompt_names(monkeypatch):
    """Claude Code attaches a file named as @path in a prompt, tools or not, and prompts carry raiders' text: Read is
    denied for any path, and the session runs in an empty directory."""
    seen = []

    class Client:
        def __init__(self, options):
            seen.append(options)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    async def no_tools(*args):
        return []

    async def report(code, max_age_s=0):
        return SimpleNamespace(code=code)
    monkeypatch.setattr(host, "ClaudeSDKClient", Client)
    monkeypatch.setattr(host, "sdk_tools", no_tools)
    async with Session(cast(server.Library, SimpleNamespace(report=report)), "R1", system_prompt="x"):
        pass
    options = seen[0]
    assert json.loads(options.settings)["permissions"]["deny"] == ["Read"]
    assert options.tools == [] and options.setting_sources == []
    assert options.max_budget_usd == 2 * host.ANSWER_BUDGET_USD
    assert options.thinking == {"type": "adaptive", "display": "summarized"}  # for the reviews
    assert os.listdir(options.cwd) == []


async def test_with_only_the_connector_raid_notes_are_the_cores(monkeypatch):
    """The evals' connector-only answers: raid_note and raid_plan take a pasted note, or read the saved one, as the
    connector's do, not the bot's "only kept in Discord"."""
    finders = []

    class Client:
        def __init__(self, options):
            pass

        async def connect(self):
            pass

    async def tools(code, on_result, guild, note, plan):
        finders.append((note, plan))
        return []

    async def report(code, max_age_s=0):
        return SimpleNamespace(code=code)
    monkeypatch.setattr(host, "ClaudeSDKClient", Client)
    monkeypatch.setattr(host, "sdk_tools", tools)
    library = cast(server.Library, SimpleNamespace(report=report))
    await Session(library, "R1", system_prompt="connector only").__aenter__()
    await Session(library, "R1").__aenter__()
    assert finders[0] == (None, None) and None not in finders[1]


async def test_with_a_plugin_the_model_loads_its_skills_and_reads_only_its_files(monkeypatch, tmp_path):
    """The evals' plugin mode: Claude loads the plugin's skill when a question calls for it, as Claude does with the
    plugin, and may read the skill's files, nothing else."""
    seen = []

    class Client:
        def __init__(self, options):
            seen.append(options)

        async def connect(self):
            pass

    async def no_tools(*args):
        return []

    async def report(code, max_age_s=0):
        return SimpleNamespace(code=code)
    monkeypatch.setattr(host, "ClaudeSDKClient", Client)
    monkeypatch.setattr(host, "sdk_tools", no_tools)
    (tmp_path / "skills").mkdir()
    session = Session(cast(server.Library, SimpleNamespace(report=report)), "R1", system_prompt="x",
                      plugin=str(tmp_path))
    await session.__aenter__()
    options = seen[0]
    assert options.tools == ["Skill", "Read"] and options.skills == "all" and options.setting_sources == []
    assert options.plugins == [{"type": "local", "path": str(tmp_path)}]
    allowed = await session._in_plugin("Read", {"file_path": str(tmp_path / "skills" / "SKILL.md")}, None)
    assert allowed.behavior == "allow"
    for name, args in (("Read", {"file_path": "/etc/passwd"}), ("Read", {"file_path": str(tmp_path) + "-other/x"}),
                       ("Bash", {"command": "ls"})):
        assert (await session._in_plugin(name, args, None)).behavior == "deny"


async def test_an_answer_says_which_skill_it_loaded_and_which_files_it_read(session):
    session._client = cast(ClaudeSDKClient, FakeClient(
        [AssistantMessage([ToolUseBlock("t1", "Skill", {"skill": "wipebot:raid-log-analysis"}),
                           ToolUseBlock("t2", "Read", {"file_path": "/p/skills/s/examples.md"})],
                          "claude-opus-5-5"),
         AssistantMessage([TextBlock("Wiped to the enrage.")], "claude-opus-5-5"), result(0.1, 10)]))
    answer = await session.ask("why did we wipe?", Context())
    assert answer.loaded == ["skill:wipebot:raid-log-analysis", "read:examples.md"]


def test_follow_ups_come_off_the_answers_end_as_buttons():
    text = ("**Wiped to Tempest.**\n- Spread at 2:15.\n\nFollow-up: Replay Rowan's death on pull 12?\n"
            "- Follow-up: **Klar for P2?**\nFollow-up: Replay Rowan's death on pull 12?\nFollow-up: " + "x" * 81
            + "\n-# Nerfed Oct 13.")
    shown, offered = host.follow_ups(text)
    assert shown == "**Wiped to Tempest.**\n- Spread at 2:15.\n\n-# Nerfed Oct 13."
    assert offered == ["Replay Rowan's death on pull 12?", "Klar for P2?"]  # repeated and too long left out
    assert host.follow_ups("No follow-up here: follow-ups are their own lines.")[1] == []
    assert Answer("**Done.**", follow_ups=["Klar for P2?"]).full == "**Done.**\nFollow-up: Klar for P2?"


async def test_a_picture_the_answer_places_goes_where_it_says(session):
    session._client = cast(ClaudeSDKClient, FakeClient(
        [AssistantMessage([TextBlock("**Strong night.**\n1. Coiled Altar: top 3.\n[picture: player_night]\n"
                                     "2. Twin Fangs: Torvek never died.\n[Picture: nothing_drawn]")],
                          "claude-opus-5-5"), result(0.25, 1000)]))
    answer = await session.ask("how did I do?", Context())
    assert answer.text == "**Strong night.**\n1. Coiled Altar: top 3.\n2. Twin Fangs: **Torvek** never died."
    at = dict((tool, n) for n, tool in answer.marks)
    assert answer.text[:at["player_night"]].endswith("top 3.\n") and set(at) == {"player_night", "nothing_drawn"}
    night = ToolCall("player_night", {}, "", False, [b"1", b"2"])
    pull = ToolCall("player_pull", {}, "", False, [b"3"])
    answer.calls = [pull, night]  # the view placed is the one shown, though drawn second
    assert [c.name for _, c in answer.pictures] == ["player_night", "player_night"]
    assert answer.place(night) == at["player_night"] and answer.place(pull) is None
