"""The cases' numbers against what the tools print now (`just evals --lint`), with no model and no judge.

Each case's expect and facts state times, counts and amounts the tools gave when it was written. A change to a tool
can move one (a boss cast a second later, a count that was wrong) and leave the case behind, failing right answers.
So every number in them is looked for in the output of the calls its answers made in any run (evals/results), replayed
with today's code on the frozen data. A number no output has is
either stale (fix the case) or worked out by the case's writer (a gap between two times, a sum): such a number goes
in the case's `derived`, which says so and keeps it from being flagged.

A replayed call that fails now is a failure of the lint, like a stale number. One that fails for lack of data (the
frozen file doesn't hold what the call reads today and Warcraft Logs didn't give it) says nothing about the case:
unless the outputs that did replay hold every number, its case is left unchecked and said so, with none of its
numbers flagged, and the lint doesn't pass.
"""
import asyncio
import json
import re
from pathlib import Path
from typing import NamedTuple

TIME = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])")
AMOUNT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?([kKM])\b")
PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?%")
SHARE = re.compile(r"(?<![\d.:])(\d+\.\d+|\d+(?=/\d)|(?<=\d/)\d+)(?![\d:])")  # 68.9, or either side of 38/24
NUMBER = re.compile(r"(?<![\d.:/\[])(\d+(?:\.\d+)?)(?![\d:%\]])")
SCALE = {"k": 1e3, "K": 1e3, "M": 1e6}
SAVED_ARGS = {"raid_note": "note", "raid_plan": "plan"}  # the bot fills these from what the server saved
# A replayed call failing on what the model asked (a player not in the report, find_reports without a guild): the
# answer's own slip, counted, not a finding about the tools.
ASKED_WRONG = ("no single player matching", "give a guild, with its server and region")
# A replay that needed data the frozen file lacks and this machine couldn't fetch (refused keys, Warcraft Logs down)
NO_DATA = ("auth_failed", "upstream_error")
AMOUNT_TOLERANCE = 0.03  # "about 210k" for 210.05k, "164k" for 163.64k
PERCENT_TOLERANCE = 0.51  # "34%" for 34.1%


def numbers(text: str) -> list[str]:
    """The times, amounts, percentages and plain numbers a text states, as written."""
    found = TIME.findall(text)
    rest = TIME.sub(" ", text)
    found += [m.group(0) for m in AMOUNT.finditer(rest)]
    rest = AMOUNT.sub(" ", rest)
    found += [m.group(0) for m in PERCENT.finditer(rest)]
    rest = PERCENT.sub(" ", rest)
    return found + NUMBER.findall(rest)


def in_output(token: str, output: str) -> bool:
    """Whether the output states the number, a time exactly and an amount or a percentage as rounded. Anywhere in
    it: a stale time that is also another event's passes (matching on the fact's words flagged more right facts
    than stale ones, as tools word a line otherwise)."""
    if TIME.fullmatch(token):
        return re.search(rf"(?<![\d:]){re.escape(token)}(?![\d:])", output) is not None
    if m := AMOUNT.fullmatch(token):
        want = float(m.group(1)) * SCALE[m.group(2)]
        return any(abs(float(v) * SCALE[s] - want) <= AMOUNT_TOLERANCE * want for v, s in AMOUNT.findall(output))
    if m := PERCENT.fullmatch(token):  # tools print one as "34.1%", "health 38/24" or "first third 68.9"
        want = float(m.group(1))
        return any(abs(float(v) - want) <= PERCENT_TOLERANCE for v in PERCENT.findall(output) + SHARE.findall(output))
    return re.search(rf"(?<![\d.]){re.escape(token)}(?![\d])", output) is not None


def recorded_calls(runs: list[dict], case_id: str) -> list[tuple[str, dict]]:
    """Every call the runs' answers to a case made that didn't fail, once each, but the bot's own tools' calls that
    the core can't take (a saved note's)."""
    calls: dict[str, tuple[str, dict]] = {}
    for run in runs:
        for t in (run.get("cases") or {}).get(case_id, []):
            for c in t.get("calls") or []:
                saved = SAVED_ARGS.get(c["name"])
                if not c.get("failed") and not (saved and saved not in c["args"]):
                    calls.setdefault(json.dumps([c["name"], c["args"]], sort_keys=True), (c["name"], c["args"]))
    return list(calls.values())


def stale(case: dict, output: str) -> list[tuple[str, str]]:
    """(where, number) for each number of a case's expect and facts that no output states and isn't `derived` nor
    `drifts` (rankings and the top parses move: `drifted` reports those apart)."""
    derived = {n for d in case.get("derived", []) + case.get("drifts", []) for n in numbers(d)}  # "25th" is 25
    texts = [("expect", case["expect"])] + [("facts", f) for f in case.get("facts", [])]
    return [(where, n) for where, text in texts for n in numbers(text) if n not in derived and not in_output(n, output)]


def drifted(case: dict, output: str) -> list[str]:
    """A case's `drifts` numbers (from rankings or the top parses, which move) that the output no longer states."""
    derived = {n for d in case.get("derived", []) for n in numbers(d)}  # never printed as such: not a move
    return [n for d in case.get("drifts", []) for n in numbers(d) if n not in derived and not in_output(n, output)]


class Linted(NamedTuple):
    flagged: int  # numbers no tool prints now
    checked: int  # cases whose numbers were looked for: those with a recorded call that replays
    failed: int = 0  # replayed calls that fail now, though they worked in a run of today's case
    no_data: int = 0  # cases left unchecked: a replay needed data this machine couldn't read


def failure(output: str | None) -> bool:
    return output is not None and output.startswith("<") and " failed: " in output


async def lint(cases: list[dict], results: Path) -> Linted:
    """Print each case's numbers its tools no longer state, the tools that fail now, and the cases that couldn't be
    checked for lack of data; what it counted."""
    import inspect

    from wipebot.core import server
    server.configure()
    takes = {name: set(inspect.signature(fn).parameters) for name, fn in server._TOOLS}
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results.glob("*.json"))]
    outputs: dict[str, str | None] = {}
    jobs = asyncio.Semaphore(4)

    async def run(name: str, args: dict) -> str | None:
        """The tool's output now, "<tool failed: why>" when it fails, None for arguments it no longer takes (an
        older run's)."""
        key = json.dumps([name, args], sort_keys=True)
        if key not in outputs:
            async with jobs:
                try:
                    text, failed = await server.call(name, args)
                except Exception as e:  # noqa: BLE001  a tool that fails now is a finding, not a crash
                    text, failed = f"{type(e).__name__}: {e}", True
                # arguments an older version of the case or the tool took (another report, since private)
                old = failed and any(w in text for w in ("validation error", "Unknown tool", "won't show report"))
                outputs[key] = None if old else f"<{name} failed: {text[:200]}>" if failed else text
        return outputs[key]

    from evals.run import case_hash
    flagged = checked = failed = no_data = asked_wrong = 0
    for case in cases:
        # a call with an argument its tool no longer takes ran another way then (compare_pulls' old pull_a)
        calls = [(n, a) for n, a in recorded_calls(runs, case["id"]) if set(a) <= takes.get(n, set(a))]
        # a run on another version of the case may name players or pulls this one doesn't (made-up names, once)
        now = {json.dumps([n, a], sort_keys=True) for n, a in recorded_calls(
            [r for r in runs if (r.get("case_hashes") or {}).get(case["id"]) == case_hash(case)], case["id"])}
        found = await asyncio.gather(*(run(n, a) for n, a in calls))
        replayed = "\n".join(t for t in found if t is not None and not failure(t))
        lacking = [t for t in found if failure(t) and any(w in str(t) for w in NO_DATA)]
        for (n, a), t in zip(calls, found, strict=True):  # first: a call that fails now, whatever else the case lacks
            if failure(t) and t not in lacking and json.dumps([n, a], sort_keys=True) in now:
                if any(w in str(t) for w in ASKED_WRONG):
                    asked_wrong += 1
                else:
                    print(f"{case['id']}: {t}")
                    failed += 1
        if lacking and (not replayed or stale(case, replayed)):  # a number not found may be in the output not read
            print(f"{case['id']}: not checked: {len(lacking)} of its {len(calls)} calls need data the frozen file "
                  f"lacks and Warcraft Logs didn't give ({next(w for w in NO_DATA if w in str(lacking[0]))})")
            no_data += 1
            continue
        texts = [t for t in found if t is not None and not failure(t)]
        if not texts:
            if any(failure(t) for t in found):
                print(f"{case['id']}: every call of its answers fails now: its numbers aren't checked")
            elif runs:
                print(f"{case['id']}: no recorded call to replay (run the case once)")
            continue
        checked += 1
        for where, n in stale(case, "\n".join(texts)):
            print(f"{case['id']}: {where} states {n}, which no call of its answers prints now")
            flagged += 1
        if moved := drifted(case, "\n".join(texts)):
            print(f"{case['id']}: rankings moved, not counted: {', '.join(moved)} (in facts: they count, never fail)")
    if asked_wrong:
        print(f"{asked_wrong} replayed calls failed on what the answer asked (a player not in the report, "
              "find_reports without a guild): the model's slip, not the tools'")
    return Linted(flagged, checked, failed, no_data)
