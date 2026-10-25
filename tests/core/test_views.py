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
from mcp.types import CallToolResult

from tests.support import assert_snapshot
from tests.support.paths import REAL
from tests.support.recorded import CASES, Replay, serve_from
from wipebot.core import server
from wipebot.core.report import maps, views
from wipebot.core.text import clock

PULL_CASE = CASES["pull-summary"]  # the pull the views are tested on: its summary and timeline are recorded


@pytest.fixture
def recorded(monkeypatch):
    """The tools answer from the recorded answers, serving many users as remote.py does (the views' key)."""
    serve_from(monkeypatch, Replay())
    monkeypatch.setattr(server.DEFAULT, "remote", server.Remote(guilds=list, user_name=str))


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


async def test_pull_summary_gives_the_view_its_key_when_serving_many(recorded, monkeypatch):
    result = await server.mcp.call_tool("pull_summary", PULL_CASE.args)
    assert isinstance(result, CallToolResult)
    assert result.structured_content == {"view": "pull", "report": PULL_CASE.args["report"],
                                         "pull": PULL_CASE.args["pull"], "boss": "Sszorak"}
    monkeypatch.setattr(server.DEFAULT, "remote", None)  # in-process (the bot, `wipebot call`): the text alone
    text_only = await server.mcp.call_tool("pull_summary", PULL_CASE.args)
    assert isinstance(text_only, CallToolResult) and text_only.structured_content is None


async def test_a_view_that_does_not_exist_is_a_readable_error(recorded):
    with pytest.raises(server.FactError, match="no view 'night'"):
        await server.view_data("night", PULL_CASE.args["pull"], None, PULL_CASE.args["report"])


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
            pytest.skip("needs Playwright's Chromium (python -m playwright install chromium-headless-shell)")
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
        drawn = await painter.picture("pull_summary", PULL_CASE.args)
        assert await painter.picture("pull_map", PULL_CASE.args) is None  # its picture is its own map
    except Error as e:
        if "Executable doesn't exist" in str(e):
            pytest.skip("needs Playwright's Chromium (python -m playwright install chromium-headless-shell)")
        raise
    finally:
        await painter.close()
    assert drawn is not None
    img = Image.open(io.BytesIO(drawn))
    assert img.format == "WEBP" and img.width == viewhost.PICTURE_WIDTH * viewhost.PICTURE_SCALE
    assert 300 < img.height < 1000 and len(drawn) < 200_000  # the card alone, without its buttons


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
