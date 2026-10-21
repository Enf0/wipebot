"""Recorded answers: what Warcraft Logs, warcraft_cli and Raidbots answered, kept so the tool tests run offline.

fixtures/real/tools/ holds the cases (cases.toml: a name, a tool and its arguments), each case's output pinned in
<name>.txt, and recorded.sqlite: the core's cache table holding every answer the cases need, by cache key. A test
serves the tools from it read-only (`Replay`); a question not in it fails with "not recorded" instead of going to
the network.

A case marked `local` reads what may not be published: Warcraft Logs' rankings and other guilds' reports (the top
parses, the fastest kills), which Warcraft Logs asks to keep out of public tests, and Raidbots' data files, whose terms
forbid reproducing them. Its answers and its snapshot are in local/ instead, untracked; without them it's skipped.
`just record-tools` with your own Warcraft Logs keys records them afresh; the maintainers' copy comes from their
private store (`just local-fixtures`, which pushes it there too after recording).

`just record-tools` (`python -m tests.support.recorded evals/cache.sqlite`) records again: it runs every case on a
copy of the evals' frozen cache plus what was recorded before (which wins, so a snapshot never moves because the
evals hold a newer answer or a report has turned private since), fetching only what neither has, then keeps exactly
the answers the cases asked for. Without the evals' cache (a fresh clone) it starts from what was recorded before
alone. The guide sites' writing and Warcraft Logs' images get stand-ins after (standins.py): made afresh from the
source's page, or kept as recorded when the source hasn't the page. What only local cases ask goes to local/;
recording stops if a published case asks for or is answered with rankings, another guild's report or a Raidbots file
(`_not_ours`; tests/core/test_recorded.py applies it to every published answer)."""
import asyncio
import json
import re
import sqlite3
import sys
import time
import tomllib
import zlib
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from wipebot.core import cache, clock, server
from wipebot.core.report import maps
from wipebot.core.wcl import WCL

from . import standins
from .paths import REAL

TOOLS = REAL / "tools"
RECORDED = TOOLS / "recorded.sqlite"
LOCAL = TOOLS / "local"  # untracked: the local cases' answers and snapshots
LOCAL_RECORDED = LOCAL / "recorded.sqlite"
NOT_LOCAL = ("the local cases' recordings aren't here (they can't be published; tests/README.md): `just record-tools` "
             "with your own Warcraft Logs keys in .env records them afresh, then `just snapshot` makes your baseline "
             "to compare a change against; maintainers: `just local-fixtures`")
# The clock the cases run at, so "the last N days" counts back from when they were recorded (find-reports' guild list).
NOW = SimpleNamespace(time=lambda: datetime(2026, 10, 13, 15, 16, tzinfo=UTC).timestamp(), monotonic=time.monotonic)
REPLAY_CASE = "pull-map"  # the replay viewer's data (maps.replay) is tested on this case's pull and player
REPLAY_AT_MS = 120_000


@dataclass(frozen=True)
class Case:
    name: str
    tool: str
    args: dict
    local: bool = False  # reads rankings or other guilds' reports: kept in LOCAL, not published

    @property
    def snapshot(self) -> Path:
        return (LOCAL if self.local else TOOLS) / f"{self.name}.txt"


def _arg(value: object) -> object:
    """An argument of cases.toml: {file = "<path under real/>"} is that file's text."""
    if isinstance(value, dict) and set(value) == {"file"}:
        return (REAL / value["file"]).read_text(encoding="utf-8")
    return value


def load_cases() -> dict[str, Case]:
    raw = tomllib.loads((TOOLS / "cases.toml").read_text(encoding="utf-8"))
    return {name: Case(name, spec.pop("tool"), {k: _arg(v) for k, v in spec.items() if k != "local"},
                       spec.get("local", False))
            for name, spec in raw.items()}


CASES = load_cases()


class Replay(cache.Cache):
    """The recorded answers, read only: the published ones, and the local ones when they're here."""
    frozen = True

    def __init__(self):
        self._dbs = [sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, check_same_thread=False)
                     for p in (RECORDED, LOCAL_RECORDED) if p.exists()]

    def get(self, key):
        for db in self._dbs:
            if row := db.execute("SELECT value, stored_at FROM cache WHERE key = ?", (key,)).fetchone():
                return json.loads(zlib.decompress(row[0])), time.time() - row[1]
        return None

    async def fetch(self, key, max_age_s, get, *, cancel=False):
        if (found := self.get(key)) is None:
            raise LookupError(f"not recorded (run `just record-tools`): {key[:300]}")
        return found[0]

    async def drain(self, timeout_s: float = 15) -> None:  # nothing refreshes in the background
        pass


class Recorder(cache.Cache):
    """A frozen cache that notes every key asked for, published or local by the case asking."""

    def __init__(self, path: Path):
        super().__init__(path, frozen=True)
        self.keys: set[str] = set()
        self.local_keys: set[str] = set()
        self.local = False  # whether the case running now is local

    def _note(self, key: str) -> None:
        (self.local_keys if self.local else self.keys).add(key)

    def get(self, key):  # a look-up without a fetch (Report.keep_many) is asked for too
        self._note(key)
        return super().get(key)

    async def fetch(self, key, max_age_s, get, *, cancel=False):
        self._note(key)
        return await super().fetch(key, max_age_s, get, cancel=cancel)


_RANKINGS = re.compile(r"[Rr]ankings?\(|zoneRanking|rankedCharacters|characterData")
_CODE = re.compile(r'"code": "(\w{16})"|code: \\"(\w{16})\\"')
# warcraft_cli commands that read rankings, top parses (other guilds' reports) or character pages, whatever they're
# asked for
_RANKED_CLI = {("warcraft", "cooldown-packet"), ("lorrgs", "spec-ranking"), ("raiderio", "character")}
# In an answer: a ranking's fields, and a report named (by code or link)
_RANKED_FIELD = re.compile(r'"(?:rankings|rankPercent|historicalPercent|todayPercent|bracketPercent|medianPercent|'
                           r'zoneRankings?|encounterRankings|characterRankings|fightRankings|rankedCharacters|'
                           r'allStars|worldRank|regionRank|serverRank|\w*spec_ranking)":')
_NAMED = re.compile(r'"(?:code|report_id|report_code|reportCode)": "(\w{16})"|warcraftlogs\.com/reports/(\w{16})')


def _codes(pattern: re.Pattern, text: str) -> set[str]:
    return {c for m in pattern.findall(text) for c in m if c}


def _guilds(rows: list[tuple[str, str]]) -> dict[str, int | None]:
    """Each recorded report's guild id, from its fights query (Report.load). `rows`: (key, value as JSON text)."""
    out = {}
    for k, v in rows:
        if "code title startTime" in k and (code := json.loads(k)[2].get("code")):
            report = ((json.loads(v) or {}).get("reportData") or {}).get("report") or {}
            out[code] = (report.get("guild") or {}).get("id")
    return out


def _listed(rows: list[tuple[str, str]], ours: int | None) -> set[str]:
    """The reports our guild's own listings name (its reports, its attendance): ours by who logged them."""
    return {c for k, v in rows if (p := json.loads(k))[0] == "wcl" and isinstance(p[-1], dict)
            and p[-1].get("guild") == ours for c in _codes(_NAMED, v)}


def _not_ours(key: str, value: str, guilds: dict[str, int | None], ours: int | None, listed: set[str]) -> bool:
    """Whether an answer (`value`: its JSON text) is Warcraft Logs' rankings, from or naming another guild's report,
    or a Raidbots data file (whose terms forbid reproducing it): never published. A report is ours when its fights
    query says so (`guilds`) or our guild's listings name it (`listed`)."""
    parts = json.loads(key)
    if (parts[0] in ("parse stats", "wcl-character", "raidbots") or _RANKINGS.search(key)
            or (parts[0] == "cli" and tuple(parts[1:3]) in _RANKED_CLI) or _RANKED_FIELD.search(value)):
        return True
    codes = {parts[1]} if parts[0] == "report" else _codes(_CODE, key)
    return any(c not in listed and guilds.get(c) != ours for c in codes | _codes(_NAMED, value))


def serve_from(mp: pytest.MonkeyPatch, answers: cache.Cache) -> None:
    """The tools answer from `answers`, at NOW, with no report loaded yet."""
    mp.setattr(cache, "_cache", answers)
    mp.setattr(server.DEFAULT, "library", server.Library(wcl=WCL()))  # given a client: no login check
    mp.setattr(server, "time", NOW)
    mp.setattr(clock, "now", NOW.time)


async def run(case: Case) -> str:
    """The case's tool output, as the snapshot holds it."""
    text, failed = await server.call(case.tool, case.args)
    return ("(failed) " if failed else "") + text + "\n"


async def replay_data(args: dict | None = None) -> dict:
    """The replay viewer's data for REPLAY_CASE's pull (or `args`), its player in focus."""
    args = args or CASES[REPLAY_CASE].args
    rep = await server.lib().report(args["report"])
    f = rep.fight(args["pull"], args.get("boss"))
    return await maps.replay(rep, f, REPLAY_AT_MS, {rep.player(p).id for p in args["players"]})


def _scrub_rows(db: sqlite3.Connection, kept: Collection[str] = ()) -> None:
    """Every answer with what isn't ours made up (standins.py), but `kept`: stand-ins already."""
    rows = [(k, json.loads(zlib.decompress(v))) for k, _, v in db.execute("SELECT key, stored_at, value FROM cache")]
    seen = standins.Seen(rows)
    db.executemany("UPDATE cache SET value = ? WHERE key = ?",
                   [(zlib.compress(json.dumps(seen.standin(k, v)).encode(), 1), k) for k, v in rows if k not in kept])
    db.commit()


def _write(path: Path, rows: list) -> None:
    """`rows` as the file at `path`, left as it is when it holds them already (a binary diff otherwise)."""
    if path.exists():
        before = sqlite3.connect(path)
        same = set(before.execute("SELECT key, stored_at, value FROM cache")) == set(rows)
        before.close()
        if same:
            return
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
    out.executemany("INSERT INTO cache VALUES (?, ?, ?)", rows)
    out.commit()
    out.execute("VACUUM")
    out.close()


async def record(source: Path) -> None:
    """Record every case's answers in RECORDED and LOCAL_RECORDED (the module docstring says how)."""
    work = TOOLS / "recording.sqlite"
    for f in TOOLS.glob("recording.sqlite*"):
        f.unlink()
    dst = sqlite3.connect(work)
    if source.exists():
        src = sqlite3.connect(source)
        src.backup(dst)
        src.close()  # `with` only commits; Windows can't delete a file still open
    else:  # a fresh clone: what's recorded, and what Warcraft Logs and the sites answer now
        print(f"no {source}: starting from the recorded answers, fetching what they lack", flush=True)
        dst.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
    kept: set[str] = set()  # stand-ins read back: the source hasn't the page to make them afresh from
    for path in (RECORDED, LOCAL_RECORDED):
        if path.exists():
            before = sqlite3.connect(":memory:")
            sqlite3.connect(path).backup(before)
            rows = before.execute("SELECT key, stored_at, value FROM cache").fetchall()
            afresh = {r[0] for r in rows if standins.replaced(r[0])
                      and dst.execute("SELECT 1 FROM cache WHERE key = ?", (r[0],)).fetchone()}
            kept |= {r[0] for r in rows if standins.replaced(r[0])} - afresh
            dst.executemany("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", [r for r in rows if r[0] not in afresh])
            dst.commit()
    dst.close()
    recorder = Recorder(work)
    with pytest.MonkeyPatch.context() as mp:
        for case in CASES.values():
            print(case.name, "(local)" if case.local else "", flush=True)
            serve_from(mp, recorder)  # each case starts with nothing loaded, as each test does
            recorder.local = case.local
            await run(case)
        print("replay", REPLAY_CASE, flush=True)
        serve_from(mp, recorder)
        recorder.local = False
        await replay_data()
    every = recorder.keys | recorder.local_keys
    rows = recorder._db.execute(f"SELECT key, stored_at, value FROM cache WHERE key IN ({', '.join('?' * len(every))})",
                                sorted(every)).fetchall()
    recorder._db.close()
    scrubbed = sqlite3.connect(":memory:")  # stand-ins over every answer at once: they go by the raiders' builds
    scrubbed.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, stored_at REAL NOT NULL, value BLOB NOT NULL)")
    scrubbed.executemany("INSERT INTO cache VALUES (?, ?, ?)", rows)
    _scrub_rows(scrubbed, kept)
    rows = scrubbed.execute("SELECT key, stored_at, value FROM cache").fetchall()
    published = [r for r in rows if r[0] in recorder.keys]
    if found := published_leaks(published):
        raise SystemExit("a published case asks for rankings, another guild's report or a Raidbots file; mark it "
                         "`local = true` in cases.toml:\n" + "\n".join(k[:200] for k in found))
    LOCAL.mkdir(exist_ok=True)
    _write(RECORDED, published)
    _write(LOCAL_RECORDED, [r for r in rows if r[0] not in recorder.keys])
    for f in TOOLS.glob("recording.sqlite*"):
        f.unlink()
    print(f"recorded {len(rows)} answers: {RECORDED.stat().st_size // 1024} KB published, "
          f"{LOCAL_RECORDED.stat().st_size // 1024} KB local")


def published_leaks(rows: list[tuple[str, float, bytes]]) -> list[str]:
    """The keys of recorded rows that may not be published (_not_ours), our guild being the one of REPLAY_CASE's
    report."""
    text = [(k, zlib.decompress(v).decode()) for k, _, v in rows]
    guilds = _guilds(text)
    ours = guilds[CASES[REPLAY_CASE].args["report"]]
    listed = _listed(text, ours)
    return sorted(k for k, v in text if _not_ours(k, v, guilds, ours, listed))


if __name__ == "__main__":
    asyncio.run(record(Path(sys.argv[1])))
