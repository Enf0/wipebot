"""The cases' numbers against what the tools print now (`just evals --lint`), with no model and no judge.

Each case's expect and facts state times, counts and amounts the tools gave when it was written. A change to a tool
can move one (a boss cast a second later, a count that was wrong) and leave the case behind, failing right answers.
So every number in them is looked for in the output of the calls its answers made in any run (evals/results), replayed
with today's code on the frozen data. A number no output has is
either stale (fix the case) or worked out by the case's writer (a gap between two times, a sum): such a number goes
in the case's `derived`, which says so and keeps it from being flagged.
"""
import asyncio
import json
import re
from pathlib import Path

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


async def lint(cases: list[dict], results: Path) -> tuple[int, int]:
    """Print each case's numbers its tools no longer state, and the tools that fail now; (numbers flagged, cases
    checked: those with a recorded call to replay)."""
    import inspect

    from wipebot.core import server
    server.configure()
    takes = {name: set(inspect.signature(fn).parameters) for name, fn in server._TOOLS}
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results.glob("*.json"))]
    outputs: dict[str, str | None] = {}
    jobs = asyncio.Semaphore(4)

    async def run(name: str, args: dict) -> str | None:
        """The tool's output now; None for arguments it no longer takes (an older run's)."""
        key = json.dumps([name, args], sort_keys=True)
        if key not in outputs:
            async with jobs:
                try:
                    outputs[key] = (await server.call(name, args))[0]
                except Exception as e:  # noqa: BLE001  a tool that fails now is a finding, not a crash
                    # arguments an older version of the case or the tool took (another report, since private)
                    old = any(w in str(e) for w in ("validation error", "Unknown tool", "won't show report"))
                    outputs[key] = None if old else f"<{name} failed: {type(e).__name__}: {str(e)[:200]}>"
        return outputs[key]

    from evals.run import case_hash
    flagged = checked = asked_wrong = 0
    for case in cases:
        # a call with an argument its tool no longer takes ran another way then (compare_pulls' old pull_a)
        calls = [(n, a) for n, a in recorded_calls(runs, case["id"]) if set(a) <= takes.get(n, set(a))]
        # a run on another version of the case may name players or pulls this one doesn't (made-up names, once)
        now = {json.dumps([n, a], sort_keys=True) for n, a in recorded_calls(
            [r for r in runs if (r.get("case_hashes") or {}).get(case["id"]) == case_hash(case)], case["id"])}
        found = await asyncio.gather(*(run(n, a) for n, a in calls))
        texts = [t for t in found if t is not None and not (t.startswith("<") and " failed: " in t)]
        if not texts:
            if runs:
                print(f"{case['id']}: no recorded call to replay (run the case once)")
            continue
        checked += 1
        for (n, a), t in zip(calls, found, strict=True):
            if t and t.startswith("<") and " failed: " in t and json.dumps([n, a], sort_keys=True) in now:
                if any(w in t for w in ASKED_WRONG):
                    asked_wrong += 1
                else:
                    print(f"{case['id']}: {t}")
        for where, n in stale(case, "\n".join(texts)):
            print(f"{case['id']}: {where} states {n}, which no call of its answers prints now")
            flagged += 1
        if moved := drifted(case, "\n".join(texts)):
            print(f"{case['id']}: rankings moved, not counted: {', '.join(moved)} (in facts: they count, never fail)")
    if asked_wrong:
        print(f"{asked_wrong} replayed calls failed on what the answer asked (a player not in the report, "
              "find_reports without a guild): the model's slip, not the tools'")
    return flagged, checked
