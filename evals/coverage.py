"""What the eval cases cover (`just evals --coverage`), with no model: evals measure answers, not code, so there's no
line coverage, but every part of Wipebot can be asked about. Along four axes:

- tools: each case's `tools` (what its right answers call), against every tool the apps have;
- kinds of question: one cases file each (wipes, nights, players, ...);
- where it's asked: a case's `where` in Discord, and each mode (plugin, connector only, bot);
- bosses: a case's `boss`.

From the latest full run of each mode (evals/results), also the cases whose answers never called a tool they're
tagged with: the tag is stale, or the answers miss a step.
"""
import json
from collections import Counter
from pathlib import Path

from wipebot.core import server
from wipebot.discord import prompt

# The connector's own tools (src/wipebot/mcp/remote.py), which the in-process server doesn't register.
CONNECTOR_TOOLS = {"my_characters", "report_answer", "save_note_or_plan"}
# The core's tools with no eval case yet, and why: a new tool comes with a case.
NO_CASE_YET = {
    "spell_info": "the answers to the cases that could use it call it in fewer than two of three",
    "web_lookup": "no case needs a page no other tool reads yet",
}


def core_tools() -> set[str]:
    return {name for name, _ in server._TOOLS}


def app_tools() -> set[str]:
    """The tools of the Discord bot and the connector on top of the core's."""
    return {t.name for t in prompt.BOT_TOOLS} | CONNECTOR_TOOLS


def latest_runs(results: Path, cases: list[dict]) -> dict[str, dict]:
    """The newest run of each mode that asked at least half its cases."""
    runs: dict[str, dict] = {}
    for path in sorted(results.glob("*.json"), reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        mode = run.get("mode")
        if not mode or mode in runs:
            continue
        asked = [c for c in cases if (mode == "bot" or not c.get("bot_only")) and (mode != "bot" or not
                                                                                   c.get("client_only"))]
        if len(run.get("cases") or {}) * 2 >= len(asked):
            runs[mode] = run | {"file": path.name}
    return runs


def report(cases: list[dict], results: Path) -> list[str]:
    tagged = Counter(t for c in cases for t in c.get("tools", []))
    lines = [f"{len(cases)} cases."]
    every = core_tools() | app_tools()
    none = sorted(every - set(tagged))
    lines.append("Tools with no case: " + (", ".join(f"{t} ({NO_CASE_YET[t]})" if t in NO_CASE_YET else t
                                                    for t in none) or "none"))
    lines.append("Tools in one case only: " + (", ".join(sorted(t for t, n in tagged.items() if n == 1)) or "none"))
    lines.append("Cases per tool: " + ", ".join(f"{t} {n}" for t, n in tagged.most_common()))
    kinds = Counter(c["kind"] for c in cases)
    lines.append("Kinds of question (a cases file each): " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    places = Counter(c.get("where", "in a server channel") for c in cases)
    lines.append("Where (in Discord; with the plugin or the connector, each in the asker's own chat): "
                 + ", ".join(f"{w} {n}" for w, n in places.most_common()))
    bosses = Counter(c.get("boss") or "(none named)" for c in cases)
    lines.append("Bosses: " + ", ".join(f"{b} {n}" for b, n in bosses.most_common()))
    for mode, run in sorted(latest_runs(results, cases).items()):
        missed = []
        for c in cases:
            trials = run["cases"].get(c["id"]) or []
            if not trials:
                continue
            called = {x["name"] for t in trials for x in t.get("calls") or []}
            if gone := sorted(set(c.get("tools", [])) - called):
                missed.append(f"{c['id']} ({', '.join(gone)})")
        lines.append(f"Tagged tools no answer called, {mode} ({run['file']}): " + ("; ".join(missed) or "none"))
    return lines
