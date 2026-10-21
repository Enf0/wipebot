"""The replay viewer's JavaScript (pull_map_app.html) against the tracks maps.replay packs, run in Node."""
import json
import shutil
import subprocess
from importlib import resources

import pytest

from wipebot.core.report import maps

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def viewer_at(track: str, times_ms: list[float]) -> list:
    """The viewer's `at()` for each time, through its own `decode()`."""
    page = resources.files("wipebot.mcp").joinpath("pull_map_app.html").read_text(encoding="utf-8")
    code = page[page.index("const UNKNOWN"):page.index("\n", page.index("const UNKNOWN"))]
    code += "\n" + page[page.index("function decode(b64)"):page.index("function image(path)")]
    code += "\n" + page[page.index("const DEAD = -2"):page.index("function isDead")]
    script = (f"{code}\nconst R = {{step_ms: {maps.REPLAY_STEP_MS}}};\nconst tr = decode({json.dumps(track)});\n"
              f"console.log(JSON.stringify({json.dumps(times_ms)}.map((ms) => at(tr, ms))));")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


def test_the_viewer_reads_the_tracks_as_they_were_packed():
    samples = [(0.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), (2000.0, 1000.0, 1000.0), (3000.0, 2000.0, 1000.0)]
    facings = [(0.0, 0.17453), (1000.0, 6.10865), (2000.0, 1.5708), (3000.0, 1.5708)]  # 10°, 350°, 90°, 90°
    dead = [(3500.0, 6000.0)]
    track = maps.pack_track(samples, facings, 0.0, 6, dead, origin=(0.0, 0.0))
    got = viewer_at(track, [0, 1000, 500, 750, 4000, 4500, 5900])
    assert got[0] == [0, 0, 10] and got[1] == [20, 0, 350]  # whole steps: as packed (half yards, degrees)
    assert got[2][2] == 0  # 10° to 350° goes through 0°, not round the long way
    assert 0 <= got[3][2] < 360 and abs(got[3][2] - 355) < 1e-9  # and never negative
    assert got[4] == got[5] == got[6] == [40, 20, maps.DEAD_FACING]  # dead where they fell, not moving
