"""Who reads what a guild keeps (raid notes, plans, the nights to come in Viserio), where nobody signs in to the
tools themselves: a host app says whose (server.reading_guilds), and the tools answer a stranger as the remote
server answers a user who isn't a member. On real pulls, from the recorded answers."""
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.support import NOTE
from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report.base import JOBS_NOT_LOGGED

KICKS, NOTE_CASE, PLAN_CASE = CASES["nekzali-enemies"], CASES["raid-note"], CASES["raid-plan"]
ELSEWHERE = frozenset({555})  # a guild none of the reports is from


@pytest.fixture
def recorded(monkeypatch):
    serve_from(monkeypatch, Replay())


async def guild_of(report: str) -> int:
    guild = (await server.lib().report(report)).guild_id
    assert guild
    return guild


async def asked(tool: str, **args) -> str:
    """The tool's answer with nothing pasted, or why it gave none, as the model reads either."""
    try:
        return (await server.call(tool, args))[0]
    except ToolError as e:
        return str(e)


async def test_a_stranger_reads_nothing_a_guild_keeps_and_cant_tell_what_it_keeps(recorded):
    """Asked from a place that reads no guild's saved things, or another guild's: the members-only answers, the same
    whether the guild saved a note, a plan or a Viserio key or not."""
    guild, report = await guild_of(NOTE_CASE.args["report"]), NOTE_CASE.args["report"]
    kicks = dict(KICKS.args)
    book = Notebook()
    said = []
    for saved in (False, True):
        if saved:
            book.save_note(guild, 3420, "Sszorak", NOTE.read_text(encoding="utf-8"), "Rowan", at=1000.0)
            book.save_note(await guild_of(kicks["report"]), 1, "Nek'zali", "a note", "Rowan", at=1000.0)
            book.save_plan(guild, "Sszorak", "p1", "Spread", "raidstrats-addon-x", "Halvard", at=1200.0)
            book.save_viserio_key(guild, "key-1", "Lanterns", "Rowan")
        for allowed in (frozenset(), ELSEWHERE):
            with server.reading_guilds(allowed):
                said.append((await asked("roster", upcoming=True, report=report),
                             await asked("raid_note", report=report, boss="Sszorak", pull=42),
                             await asked("raid_note", report=report, boss="Sszorak", upcoming=True),
                             await asked("raid_plan", report=report, pull=42),
                             await asked("pull_map", report=report, pull=42, plan=True),  # the plan, drawn
                             await asked("enemies", **kicks)))
    upcoming, note, next_note, plan, plan_map, jobs = said[0]
    assert all(s == said[0] for s in said)  # with or without anything saved: nothing gives it away
    assert upcoming.endswith("raid nights are for its members only")
    assert "the ones" in note and note == next_note and "saved are for its members only: ask the user to paste the " \
        "raid note." in note
    assert "saved are for its members only: ask the user to paste the raid plan." in plan
    assert "saved are for its members only: ask the user to paste the raid plan." in plan_map
    assert JOBS_NOT_LOGGED in jobs and "raid note saved" not in jobs


async def test_the_guilds_own_place_and_a_single_users_copy_read_what_it_keeps(recorded, monkeypatch):
    guild, report = await guild_of(NOTE_CASE.args["report"]), NOTE_CASE.args["report"]
    kicks = dict(KICKS.args)
    kicks_guild = await guild_of(kicks["report"])

    async def upcoming(key):
        raise server.viserio.ViserioError(f"asked with {key}")
    monkeypatch.setattr(server.viserio, "upcoming", upcoming)
    Notebook().save_viserio_key(guild, "key-1", "Lanterns", "Rowan")
    for allowed in (frozenset({guild, kicks_guild}), None):  # the host said so, or there's no host: one user's own
        with server.reading_guilds(allowed):
            assert "asked with key-1" in await asked("roster", upcoming=True, report=report)  # Viserio was asked
            assert "has none saved for Sszorak that night" in await asked("raid_note", report=report, boss="Sszorak",
                                                                          pull=42)
            assert "has none saved for Sszorak" in await asked("raid_plan", report=report, pull=42)
            assert "has none saved for Sszorak" in await asked("pull_map", report=report, pull=42, plan=True)
            assert "has no raid note saved for Nek'zali the Soulcoiler that night" in await asked("enemies", **kicks)
    assert server._guilds.get() is None  # outside it, as before


def test_a_place_reads_only_the_guilds_it_was_given():
    assert server._a_member(14169)  # nobody said: a single user's own copy
    with server.reading_guilds(frozenset()):
        assert not server._a_member(14169)
        with server.reading_guilds(frozenset({14169})):
            assert server._a_member(14169) and not server._a_member(555)
        assert not server._a_member(14169)


async def test_a_users_guilds_are_asked_of_warcraft_logs_with_their_own_token(monkeypatch):
    """Which guilds a signed-in user has a claimed character in, as Warcraft Logs says now (a host app verifies a
    guild's claim with it); no answer, or a token Warcraft Logs refuses, is an error, never "none"."""
    import httpx

    from wipebot.core import accounts

    user: dict | int = {"id": 1, "name": "Rowan", "characters": [
        {"id": 10, "name": "Torvek", "guilds": [{"id": 14169, "name": "The Copper Lanterns"}]},
        {"id": 11, "name": "Mirelle", "guilds": [{"id": 14169}, {"id": 777}]},
        {"id": 12, "name": "Brightleaf", "guilds": None}]}

    def reply(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == accounts.USER_API and request.headers["Authorization"] == "Bearer rowan's token"
        if isinstance(user, int):
            return httpx.Response(user)
        return httpx.Response(200, json={"data": {"userData": {"currentUser": user}}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw))
    assert await accounts.claimed_guilds("rowan's token") == {14169, 777}
    user = {"id": 1, "name": "Rowan", "characters": []}
    assert await accounts.claimed_guilds("rowan's token") == set()
    user = 401  # revoked on Warcraft Logs
    with pytest.raises(accounts.AccountError):
        await accounts.claimed_guilds("rowan's token")
