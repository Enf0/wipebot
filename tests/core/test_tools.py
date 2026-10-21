"""The tools on real pulls: each case of fixtures/real/tools/cases.toml against its snapshot, answered from the
recorded answers (support/recorded.py); and what a snapshot can't hold, the pictures and the replay's data."""
import base64
import io
import json
import struct

import pytest
from mcp.types import CallToolResult
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
)
from wipebot.core import server
from wipebot.core.report import maps


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, with nothing loaded yet."""
    serve_from(monkeypatch, Replay())


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
async def test_tool_output_matches_snapshot(recorded, case: Case):
    if case.local and not LOCAL_RECORDED.exists():
        pytest.skip(NOT_LOCAL)
    assert_snapshot(case.snapshot, await run(case))


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
