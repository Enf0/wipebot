"""Ask the eval cases through wipebot's agent session and check the answers.

    just evals                        # every case once, as Claude with the plugin: the connector and the skill
    just evals -k wipe --trials 3     # some cases, three times each
    just evals --bot --trials auto    # twice where the baseline was sure, three times elsewhere (the full suite)
    just evals --model opus           # another model; the cases that differ from the baseline are listed
    just evals --save-baseline        # keep this run as its mode's baseline, to compare later runs with
    just evals --save-baseline evals/results/X.json   # a saved three-trial run as the baseline (no model)
    just evals --summary evals/results/X.json         # a saved run's summary and comparison again (no model)
    just evals --record evals/results/X.json          # a saved full run as its mode's last in evals/full-runs.json
    just evals --connector-only       # as a client with only the connector: no skill, no instructions (own baseline)
    just evals --bot                  # as the Discord bot: its prompt with the skill (own baseline)
    just evals --label evals/results/X.json   # mark a run's answers pass or fail yourself (evals/labels.jsonl)
    just evals --label-failed 30      # mark 30 answers the judge failed, over the cases
    just evals --agreement            # how often the judge agrees with your marks
    just evals --lint                 # the cases' numbers against today's tool output (no model; after `just snapshot`)
    just evals --coverage             # what the cases cover: tools, kinds of question, places, bosses (no model)
    just evals --endings              # the engine's "Ended by" against each case's `cause` (no model)
    just evals --api-key              # bill the answers and the judge to ANTHROPIC_API_KEY (default: your Claude login)
    just evals --held-out --trials 3  # the held-out cases alone, at a milestone (never while changing the method)
    just evals --recheck evals/results/X.json  # a run's answers again: claims its tools don't back, and the rubric
    just evals --form evals/results/X.json     # how a run's answers are built, against the bot's prompt (no model)

Warcraft Logs data and the guides come from evals/cache.sqlite (untracked), frozen: what a run asks that isn't there
is fetched once and kept, never refreshed, so every run on a machine reads the same data, as of the time it was
first fetched (wipebot.core.clock). A fresh clone fetches it on its first run, which needs Warcraft Logs keys and
takes a while; its rankings and percentiles are that day's, so a few cases' numbers can differ from the owner's.
Runs are saved, with every answer and tool call, to evals/results/.

An answer fails when the judge fails it, and also when the raider would have met a failure whatever the judge made
of the text: a tool crashed, the answer hit the bot's cap of turns or spend, or it took longer than the bot waits. An
error is only what says nothing about the answer: a usage limit, a judge with no verdict, a service that was down,
data this machine may not read. Errors are in no count; a summary and a comparison start with how many there were,
and a run with one exits non-zero (settle). So does a run in which a tool crashed, which the summary names next: a
fault in the code, which the totals alone may not show as one.

A full run (no -k, the mode's own model and effort) is kept as its mode's last in evals/full-runs.json, tracked: its
commit, day, trials, passes, errors and verdict line. scripts/release.py prints how many answer-changing commits and
days a release is from it.

A run, and a baseline, keep what was tested (tested: the models' full names, the SDK and Claude Code versions, the
effort, the commit, when the frozen data was last added to), and a comparison warns when the two differ. The date in
a question's first line is the recorded data's, like the tools' clock (freeze_today).

A run, and a baseline, keep a hash of the judge (its prompts and model) and of each case. A run is compared with the
baseline only on the cases unchanged since, with a warning when the judge changed; a case is listed as moved only
when its pass rate moved beyond noise (beyond_noise), and the total with its expected spread from run to run.
With `--trials auto` a case the baseline judged the same on every trial is asked twice and every other case three
times; once the total is half a spread below the baseline's, or two above, every case gets its third answer, so the
total shows a drop or a rise only on three trials (ask_all). In any run a case that moved beyond noise is asked again,
up to six answers, before it is printed. A comparison ends in one verdict line (compared): no change shown, a drop
shown, a rise shown or cannot tell, with the smallest drop it could have shown. A run exits non-zero when an answer
errored, a tool crashed or a drop is shown: the total two spreads below the baseline's, or a case that stayed down on
six answers. One spread below says "look" and exits zero.
"""
import argparse
import asyncio
import functools
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from evals.checks import (
    EXPECT,
    FACTS,
    FAIL_VOTES,
    GROUNDED_MODEL,
    MODELS,
    RUBRIC,
    WRONG,
    built,
    built_summary,
    coded,
    coded_keys,
    expect_prompt,
    facts,
    grounded,
    judge_majority,
    rubric,
    wrong_prompt,
)
from wipebot.core import server, wcl
from wipebot.core.cache import cache
from wipebot.discord import config, prompt
from wipebot.discord.bot import CHAT_TIMEOUT_S, POST_TIMEOUT_S
from wipebot.discord.checks import warnings
from wipebot.discord.host import CAPPED, Context, ModelError, Session, ToolCall
from wipebot.discord.prompt import post_prefetch, post_question
from wipebot.mcp import remote
from wipebot.mcp.plugin import plugin_files

HERE = Path(__file__).resolve().parent
CACHE, LABELS = HERE / "cache.sqlite", HERE / "labels.jsonl"
# Each mode's last full run (every case, no -k), tracked: scripts/release.py says how far a release is from it.
FULL_RUNS = HERE / "full-runs.json"
# The recorded data's "now" (wipebot.core.clock): after the guild's 12 Oct night (vjt3KLbN8g62J1H9, the
# latest log a case relies on, ends 20:57 UTC), before the first Kith'ix log (14 Oct 00:49); the tool tests' clock.
RECORDED_AT = datetime(2026, 10, 13, 15, 16, tzinfo=UTC).timestamp()


def billed_to(api_key: bool) -> str:
    """Who pays for the run's answers and judging. A run is on the developer's Claude Code login unless asked
    otherwise: an ANTHROPIC_API_KEY in the environment (.env sets one for the bot) is dropped, so a run is never
    billed to the API by accident; with `api_key` it's used, and must be set."""
    if not api_key:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        return "your Claude Code login"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("--api-key: ANTHROPIC_API_KEY is not set")
    return "the API (ANTHROPIC_API_KEY)"


def baseline(mode: str) -> Path:
    """The run later runs of a mode are compared with: each mode (plugin, the default: Claude with the plugin;
    connector: a client with only the connector; bot: the Discord bot) has its own."""
    return HERE / {"plugin": "baseline.json", "connector": "baseline-connector-only.json",
                   "bot": "baseline-bot.json"}[mode]
# case keys that go to the question's Context
CONTEXT = ["asker", "discord_name", "pull", "boss", "where", "live"]
JOBS = 4  # answers at a time
KEEP_FOR_OTHERS = 1 / 2  # of each hour's Warcraft Logs points, left to the bot and the server (same account)
# With only the connector, as claude.ai has it today: no skill (that comes with a plugin, or MCP-served skills) and no
# server instructions (claude.ai drops them, anthropics/claude-ai-mcp#93), so only the tools guide the answer. The
# app's own system prompt isn't public; a plain one stands in.
CONNECTOR_ONLY = ("You are Claude, an AI assistant made by Anthropic, in a chat on claude.ai. The user has connected "
                  "the Wipebot connector, World of Warcraft raid analysis from Warcraft Logs; use its tools. Each "
                  "message starts with what the app knows: the report and who is asking.")
# A tool that crashed (a bad argument is the model's doing, and no crash): the raider would have met it too, so the
# answer fails whatever the judge makes of it.
CRASHED = ("failed with an unexpected error",)
# Why an answer failed whatever the judge made of it (Trial.broke starts with one; a tally counts its answers by them)
CRASH, CAP, SLOW = "a tool crashed", "hit the bot's cap", "took too long"
# Data this machine couldn't read, which says nothing about the answer: a report Warcraft Logs won't show, or keys it
# refused (a run checks them once at its start, keys_work).
NO_DATA = ("won't show report", "auth_failed")
# A service that didn't answer (warcraft_cli's network failure: Warcraft Logs down, a 503), for a call the frozen
# cache doesn't hold: it breaks a failed trial, while an answer that got by without the call still passes.
DOWN = "upstream_error"
# The named checks an answer passes by, each kept with its trial (Trial.checks): the judge's two and the case's
# required calls. A baseline keeps the list it was scored by (`parts`) and a later run is scored by that list when the
# two are compared (scored), so a check added since moves no comparison until the next baseline. A file from before
# 10 November names none: it is read as these three.
PARTS = ("expect", "wrong", "calls", "numbers", "form")  # the last two by code, for the cases that ask (checks.coded)
FIRST_PARTS = PARTS[:3]  # what a tally from before the checks were named was scored by


def settle(t: Trial, case: dict, mode: str) -> Trial:
    """A trial's verdict from what it recorded, by today's rule (a saved run is read with it too):
    - a failed answer whatever the judge said (`broke`), as the raider would have met it: it hit the bot's cap of
      turns or spend, a tool crashed, or, in the bot, it took longer than the bot waits (the wait for the next hour's
      Warcraft Logs points left out, which is the eval's own: only where that was timed);
    - an error, in neither passes nor trials (`error`): the session failed another way (a usage limit), this machine
      couldn't read the data, a service was down for a call a failed answer needed, or the judge gave no verdict;
    - else passed when it passed every named check (PARTS): the judge's two, and no required call missing."""
    # the session raised, so nothing was judged: its error, or in a trial settled before (a saved run's), the cap
    session = "" if t.expect else t.error or t.broke
    if t.expect:  # judged; a check a later step added to the trial stays
        t.coded = coded(case, t.answer, mode)
        t.checks = {**t.checks, "expect": t.expect.get("ok") is True, "wrong": t.wrong.get("ok") is True,
                    "calls": not t.uncalled, **{part: not why for part, why in t.coded.items()}}
    judged_ok = bool(t.checks) and all(t.checks.get(part, True) for part in PARTS)
    failed = [c for c in t.calls if c.get("failed")]

    def said(words: tuple[str, ...]) -> str:
        return next((f"{c.get('name')}: {str(c.get('result'))[:200]}" for c in failed
                     if any(w in str(c.get("result")) for w in words)), "")
    t.error = t.broke = ""
    if cap := next((c for c in CAPPED if c in session), ""):
        t.broke = f"{CAP} ({cap}): the raider is told it needed more digging than one answer gets"
    elif session:
        t.error = session
    elif crashed := said(CRASHED):
        t.broke = f"{CRASH}: {crashed}"
    elif mode == "bot" and t.waited_s is not None and t.seconds - t.waited_s > (limit := time_limit(case)):
        t.broke = f"{SLOW}: {t.seconds - t.waited_s:.0f} s, and the bot gives up after {limit} s"
    elif unread := said(NO_DATA) or ("" if judged_ok else said((DOWN,))):
        t.error = unread
    else:
        t.error = next((f"the judge gave no verdict on {name}: {v.get('reason', '')[:200]}"
                        for name, v in (("expect", t.expect), ("wrong", t.wrong)) if v.get("ok") is None), "")
    t.errored = bool(t.error)
    t.passed = judged_ok and not t.errored and not t.broke
    return t


def time_limit(case: dict) -> int:
    """How long the bot waits for a case's answers before it gives up: a post's or a chat answer's, for each of its
    questions (a trial's seconds are the whole conversation's)."""
    return (POST_TIMEOUT_S if case.get("kind") == "posts" else CHAT_TIMEOUT_S) * len(questions(case))


# When this process waited for the next hour's Warcraft Logs points (time.monotonic(), from and to): the eval's own
# rule (KEEP_FOR_OTHERS), so no part of an answer's time. None until time_waits: not timed.
WAITS: list[tuple[float, float]] | None = None


def time_waits() -> None:
    """Time each wait for the next hour's points from now on (WAITS), around the wait wcl.py makes before a query.
    Where that wait isn't there to wrap, answers' seconds are kept but none fails on them."""
    global WAITS
    wait = getattr(wcl.WCL, "_wait_for_points", None)
    if WAITS is not None or wait is None:
        return
    WAITS = []

    def timed(self) -> None:
        started = time.monotonic()
        wait(self)
        if WAITS is not None and time.monotonic() - started >= 1:
            WAITS.append((started, time.monotonic()))
    wcl.WCL._wait_for_points = timed


def waited(calls: Iterable[ToolCall]) -> float | None:
    """How long an answer's tool calls waited for the next hour's points: of each wait, what the call it held
    longest spent in it. None when waits aren't timed."""
    if WAITS is None:
        return None
    spans = [(c.started, c.started + c.duration_s) for c in calls]
    return round(sum(max((max(0.0, min(end, to) - max(start, since)) for start, end in spans), default=0.0)
                     for since, to in WAITS), 1)


@dataclass
class Trial:
    answer: str = ""
    model: str = ""  # the model that answered, by the full name the API gave
    error: str = ""  # why it's an error
    seconds: float = 0.0
    waited_s: float | None = None  # of them, waiting for the next hour's Warcraft Logs points; None: not timed
    cost_usd: float = 0.0  # the answer's and the judge's
    passed: bool = False
    errored: bool = False  # says nothing about the answer (settle): in neither passes nor trials
    broke: str = ""  # why it's a failed answer whatever the judge said (settle)
    checks: dict = field(default_factory=dict)  # the named checks it passed or failed (PARTS), once judged
    coded: dict = field(default_factory=dict)  # the checks by code its case asks for: why it fails each, "" if not
    expect: dict = field(default_factory=dict)  # Verdict
    wrong: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)  # Facts: how many supporting facts it states (never fails it)
    warnings: list[str] = field(default_factory=list)
    form: dict = field(default_factory=dict)  # how the answer is built (wipebot.discord.form.Form)
    slips: dict[str, str] | None = None  # as the bot: where it leaves the form the bot's prompt asks for, by kind
    calls: list[dict] = field(default_factory=list)  # {name, args, result, failed}
    uncalled: list[str] = field(default_factory=list)  # the case's `calls` the answer didn't make: it fails
    loaded: list[str] = field(default_factory=list)  # with the plugin: the skills loaded and files read (Answer.loaded)


# One file per kind of question; bot.toml's cases are the bot's own rules, run only with --bot, and connector.toml's
# the connector's own (an officer saving a note), run only without. posts.toml's are the bot's posts after a wipe
# (follow_pulls), asked as the bot asks for one: --bot only.
CASES = HERE / "cases"


def load_cases(folder: Path = CASES) -> list[dict]:
    """Every case in the folder's files (each with its `[defaults]`); a case in bot.toml is `bot_only`, one in
    connector.toml `client_only`. A post's case (posts.toml) is `bot_only` too, and its question is the one the bot
    asks itself after the wipe: the asker's own feedback, or what they asked for (`request`)."""
    cases = []
    for path in sorted(folder.glob("*.toml")):
        spec = tomllib.loads(path.read_text(encoding="utf-8"))
        for case in spec["case"]:
            if unknown := set(case) - {"id", "question", "expect", "facts", "wrong", "derived",
                                       "drifts", "report", "tools", "calls", "asks_first", "cause", "request",
                                       "numbers", "form", *CONTEXT}:
                raise SystemExit(f"case {case.get('id')} ({path.name}): unknown keys {', '.join(sorted(unknown))}")
            if wrong := coded_keys(case):
                raise SystemExit(f"case {case.get('id')} ({path.name}): {wrong}")
            only = {"bot": {"bot_only": True}, "posts": {"bot_only": True},
                    "connector": {"client_only": True}}.get(path.stem, {})
            if path.stem == "posts":
                name = case.get("discord_name") or case.get("asker") or "a raider"
                case = case | {"question": post_question(case["boss"], case["pull"], name, case.get("asker"),
                                                         case.get("request", ""))}
            cases.append({"report": spec["defaults"]["report"], **case} | only | {"kind": path.stem})
    if len({c["id"] for c in cases}) != len(cases):
        raise SystemExit("two cases share an id")
    return cases


def regular(mode: str) -> list[dict]:
    """The cases a full run of a mode asks: all but the held-out ones and the other app's own."""
    return [c for c in load_cases() if c["kind"] != "heldout"
            and not c.get("client_only" if mode == "bot" else "bot_only")]


def picked(case: dict, filter: str) -> bool:
    """Whether `-k` picks the case: its id contains the filter, or one of its comma-separated parts."""
    return any(f.strip() in case["id"] for f in filter.split(","))


def questions(case: dict) -> list[str]:
    return case["question"] if isinstance(case["question"], list) else [case["question"]]


IN_CHANNEL = "in #raid (a server channel)"
# With the plugin, as Claude has Wipebot when a member adds it (the README's way): the connector, and the skill the
# plugin brings, which Claude loads itself when a question calls for it (plugin_folder).
PLUGIN = CONNECTOR_ONLY.replace("has connected the Wipebot connector", "has added the Wipebot plugin: its connector "
                                "and its skill")


@functools.cache
def plugin_folder() -> str:
    """The plugin as members get it (wipebot.mcp.plugin), written to a folder for the session to load: its skill and
    manifest, without the manifest's connector, which the session's in-process tools stand in for."""
    folder = Path(tempfile.mkdtemp(prefix="wipebot-plugin-"))
    for path, data in plugin_files("Wipebot", "the guild", "https://wipebot.invalid/mcp").items():
        if path == ".claude-plugin/plugin.json":
            data = json.dumps({k: v for k, v in json.loads(data).items() if k != "mcpServers"}).encode()
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
    return str(folder)
IN_CLAUDE = "in their own chat on claude.ai"
# The connector knows who's asking: an officer saves a pasted note or plan for their guild (save_note_or_plan). Here
# every asker is an officer, of a guild no case's report is from, so what one case saves no other case reads.
OFFICER = ("an officer", 0)


def as_connector() -> None:
    """The in-process tools as the connector has them for a signed-in officer: with save_note_or_plan (OFFICER), and
    report_answer, which keeps nothing here."""
    server.allow_saving(server.mcp, server.DEFAULT, lambda: OFFICER)
    remote.allow_reports(server.mcp, server.DEFAULT)


def where(case: dict, client: bool = False) -> str:
    """Where the question is asked: the case's place in Discord, or in a client (plugin, connector), the asker's chat
    (the bot's channels mean nothing there)."""
    return IN_CLAUDE if client else case.get("where", IN_CHANNEL)


def asked(case: dict, client: bool = False) -> str:
    """The question as the judge and the labeller see it: who asked it and where (the cases' rules on naming players
    turn on both), whether the raid was going on, and a follow-up after the ones before it."""
    *before, last = questions(case)
    who = case.get("asker") or (f"{case['discord_name']} (their Discord name)" if case.get("discord_name") else "")
    live = ", while the raid is going on" if case.get("live") and not client else ""
    head = "Asked" + (f" by {who}," if who else "") + f" {where(case, client)}{live}: "
    return head + "".join(f"{q}\n(then, in the same conversation) " for q in before) + last


async def trial(case: dict, library: server.Library, model: str, judge_model: str, jobs: asyncio.Semaphore,
                mode: str = "plugin") -> Trial:
    """One answer, judged."""
    model = model or config.model()  # what the bot would use
    t = Trial()
    calls, prompts, ran = [], [], []  # here, so that what fails before the session starts is an error like the rest
    async with jobs:
        started = time.time()  # not counting the wait for a free slot
        try:
            given = {k: case[k] for k in CONTEXT if k in case and k != "where"}
            client = mode != "bot"
            # the bot's own place for a channel ("in #raid"), or in a client, the asker's chat
            ctx = Context(where=IN_CLAUDE if client else case.get("where", "in #raid"), **given)
            # asked from the report's own guild (its server in Discord, a member in a client): the tools read what
            # that guild saved, which here is nothing
            ctx.guild_id = (await library.report(case["report"])).guild_id or 0
            if case["kind"] == "posts":  # as the bot asks for a post: with the tool it starts from already run
                ctx.prefetch = await post_prefetch(await library.report(case["report"]), case["boss"], case["pull"],
                                                   case.get("asker"), case.get("request", ""))
            # None: the bot's own, with the skill
            prompt = {"plugin": PLUGIN, "connector": CONNECTOR_ONLY, "bot": None}[mode]

            def keep(answer, turn: int) -> None:
                """What an answer cost and called; images as a count, a follow-up's calls marked with the question
                they answered (0 the first)."""
                t.cost_usd += answer.cost_usd or 0.0
                t.model = answer.model or t.model
                calls.extend({**c, "turn": turn} if len(questions(case)) > 1 else c for c in answer.records())
                ran.extend(answer.calls)
            async with Session(library, case["report"], model, system_prompt=prompt,
                               plugin=plugin_folder() if mode == "plugin" else None) as session:
                for turn, question in enumerate(questions(case)):  # follow-ups in one conversation; the last
                    try:                                           # answer is judged
                        answer = await session.ask(question, ctx)
                    except ModelError as e:  # a cap: what it got to is kept, for the record
                        if e.answer:
                            keep(e.answer, turn)
                        raise
                    keep(answer, turn)
                    prompts.append(answer.prompt)
                    t.loaded += answer.loaded
        except Exception as e:
            t.error, t.seconds = f"{type(e).__name__}: {e}", round(time.time() - started, 1)
            t.calls, t.waited_s = calls, waited(ran)
            return settle(t, case, mode)
    t.seconds, t.waited_s = round(time.time() - started, 1), waited(ran)
    # The answer with the follow-ups it offers (the bot's buttons); every call of the conversation, as a follow-up's
    # answer builds on them
    t.answer, t.calls = answer.full, calls
    expect, wrong, stated = await asyncio.gather(
        judge_majority(expect_prompt(asked(case, client), case["expect"], t.answer), judge_model),
        judge_majority(wrong_prompt(asked(case, client), case["wrong"], t.answer), judge_model),
        facts(asked(case, client), case["facts"], t.answer, judge_model) if case.get("facts")
        else asyncio.sleep(0))
    t.uncalled = uncalled(case, calls, mode)
    t.expect, t.wrong = asdict(expect), asdict(wrong)
    t.cost_usd += expect.cost_usd + wrong.cost_usd + (stated.cost_usd if stated else 0.0)
    if stated:
        t.facts = asdict(stated)
    t.warnings = warnings(t.answer, [c["result"] for c in t.calls] + prompts)
    t.form, t.slips = built(case, t.answer, mode)
    return settle(t, case, mode)


def uncalled(case: dict, calls: list[dict], mode: str = "plugin") -> list[str]:
    """The tools the case says a right answer calls (`calls`: a step of the method no answer's text shows, such as
    reading the spec's guide before advising) that the answer's conversation didn't call. Only where the method is
    read: a client with only the connector has no skill to give it the step. And in every mode, the tools it calls
    only once the user agreed (`asks_first`: sending a report), which only the last question does."""
    missing = []
    for name in case.get("asks_first") or []:
        last = len(questions(case)) - 1
        turns = {c.get("turn", last) for c in calls if c.get("name") == name}
        if last not in turns:
            missing.append(name)
        if any(t < last for t in turns):
            missing.append(f"{name} only after the last question (called it before the user agreed)")
    if mode == "connector":
        return missing
    made = {c.get("name") for c in calls}
    return [name for name in case.get("calls") or [] if name not in made] + missing


def answers(cases: list[dict], results: dict[str, list[Trial]]) -> dict:
    """What the answers did besides passing, per case over its trials that didn't error: the supporting facts they
    stated (`facts`: [stated, of]), their tool calls (`calls`) and how many began with one of the case's `tools`, the
    tools its right answers call (`first_tagged`); and how they are built: their words to read (`words`) and, as the
    bot, how many leave the form its prompt asks for (`slips`). They move when an answer gets fuller, finds its
    tools faster or grows longer, which a pass rate near the top can't show."""
    tagged = {c["id"]: set(c.get("tools") or []) for c in cases}
    out: dict[str, dict] = {"facts": {}, "calls": {}, "first_tagged": {}, "words": {}, "slips": {}}
    for c, ts in results.items():
        ok = [t for t in ts if not t.errored]
        if counted := [t.facts for t in ok if t.facts]:
            out["facts"][c] = [sum(f["stated"] for f in counted), sum(f["of"] for f in counted)]
        out["calls"][c] = sum(len(t.calls) for t in ok)
        if tagged[c]:
            out["first_tagged"][c] = sum(bool(t.calls) and t.calls[0]["name"] in tagged[c] for t in ok)
        if any(t.form for t in ok):
            out["words"][c] = sum(t.form.get("words", 0) for t in ok)
        if any(t.slips is not None for t in ok):
            out["slips"][c] = sum(bool(t.slips) for t in ok)
    return out


def how_answered(tally: dict, cases: Iterable[str]) -> str:
    """The answers' supporting facts stated, tool calls per answer, how often the first call was one of the case's
    tools, their words and, as the bot, how many leave its prompt's form, over `cases` (those of the run, or the
    ones compared with a baseline; one from before a count was kept leaves it out)."""
    cases = list(cases)
    answered = sum(judged(tally, c) for c in cases)
    facts = [tally.get("facts", {}).get(c) for c in cases]
    stated, of = sum(f[0] for f in facts if f), sum(f[1] for f in facts if f)
    tagged = [c for c in cases if c in tally.get("first_tagged", {})]
    first, of_first = sum(tally["first_tagged"][c] for c in tagged), sum(judged(tally, c) for c in tagged)
    worded = [c for c in cases if c in tally.get("words", {})]
    held = [c for c in cases if c in tally.get("slips", {})]
    of_worded, of_held = sum(judged(tally, c) for c in worded), sum(judged(tally, c) for c in held)
    parts = [f"supporting facts stated {stated}/{of} ({stated / of:.0%})" if of else "",
             f"{sum(tally.get('calls', {}).get(c, 0) for c in cases) / answered:.1f} tool calls an answer"
             if answered and tally.get("calls") else "",
             f"first call one of the case's tools in {first}/{of_first} ({first / of_first:.0%})" if of_first else "",
             f"{sum(tally['words'][c] for c in worded) / of_worded:.0f} words an answer" if of_worded else "",
             f"{sum(tally['slips'][c] for c in held)}/{of_held} leave the bot's form" if of_held else ""]
    return "; ".join(p for p in parts if p)


def skill_use(results: dict[str, list[Trial]]) -> str:
    """With the plugin: in how many answers Claude loaded the skill, which of its files it read how often, and the
    cases it answered without the skill on some trial."""
    answered = [(c, t) for c, ts in results.items() for t in ts if not t.errored]
    loaded = [(c, t) for c, t in answered if any(x.startswith("skill:") for x in t.loaded)]
    reads: dict[str, int] = {}
    for _, t in answered:
        for x in dict.fromkeys(t.loaded):  # a file read twice in one answer counts once
            if x.startswith("read:"):
                reads[x.removeprefix("read:")] = reads.get(x.removeprefix("read:"), 0) + 1
    without = sorted({c for c, t in answered if not any(x.startswith("skill:") for x in t.loaded)})
    return (f"The skill was loaded in {len(loaded)} of {len(answered)} answers; files read: "
            + (", ".join(f"{f} {n}" for f, n in sorted(reads.items(), key=lambda x: -x[1])) or "none")
            + (f"\n  answered without the skill on some trial: {', '.join(without)}" if without else ""))


def show(case_id: str, t: Trial) -> None:
    verdict = "ERROR" if t.errored else "pass" if t.passed else "FAIL"
    lines = [f"{case_id:30} {verdict:5} ({t.seconds:.0f} s" + (f", {t.waited_s:.0f} of them waiting for Warcraft "
                                                               "Logs points" if t.waited_s else "")
             + f", ${t.cost_usd:.2f})"]
    if t.error or t.broke:
        lines.append(f"error: {t.error}" if t.error else f"failed: {t.broke}")
    for name, v in (("expect", t.expect), ("wrong", t.wrong)):
        if v and v["ok"] is not True:
            lines.append(f"{name}: {v['reason'][:300]}")
    if t.uncalled:
        lines.append("didn't call " + ", ".join(t.uncalled))
    lines += [f"{part}: {why}" for part, why in t.coded.items() if why]
    if t.facts and t.facts["missing"]:
        lines.append(f"facts {t.facts['stated']}/{t.facts['of']}, missing: " + "; ".join(t.facts["missing"])[:300])
    lines += [f"warning: {w}" for w in t.warnings]
    lines += [f"form: {slip}" for slip in (t.slips or {}).values()]
    print("\n    ".join(lines), flush=True)


async def keys_work(library: server.Library) -> None:
    """Stop before the first answer when Warcraft Logs refuses this machine's keys: a run asks it live for what the
    frozen data lacks (a query the model writes itself, a tool whose query changed), and an answer without its data
    says nothing about the code."""
    try:
        await asyncio.to_thread(library.wcl._run, wcl.RATE, {})
    except wcl.WCLError as e:
        if "auth_failed" in str(e):
            raise SystemExit(f"Warcraft Logs refused this machine's keys ({e}): set working ones (.env.example) "
                             "before a run, which asks it for what the frozen data lacks") from e
        print(f"note: Warcraft Logs didn't answer ({e}): an answer that needs it for what the frozen data lacks "
              "will be an error", flush=True)


async def run(args) -> int:
    """Ask the cases, save and print what came of it; 1 when an answer errored, a tool crashed or the comparison
    with the baseline shows a drop (red), else 0."""
    print(f"Answers and judging on {args.billed_to}.", flush=True)
    frozen()
    freeze_today()
    model = args.model  # empty: the bot's own, for chat and for live answers
    if args.effort:
        os.environ["WIPEBOT_EFFORT"] = args.effort  # read by the answers' sessions only
    shown = (model or config.model()) + (f", effort {e}" if (e := config.effort()) else "")
    cases = [c for c in load_cases() if picked(c, args.filter)
             and not (args.mode != "bot" and c.get("bot_only"))  # the bot's channel rules mean nothing in claude.ai
             and not (args.mode == "bot" and c.get("client_only"))  # nor the connector's saving in Discord
             and (c["kind"] == "heldout") == args.held_out]  # the held-out cases only when asked, and then alone
    if args.mode != "bot":
        as_connector()
    library, jobs = server.configure(), asyncio.Semaphore(JOBS)
    await keys_work(library)
    base = json.loads(baseline(args.mode).read_text()) if baseline(args.mode).exists() else None
    if base and (warning := judge_warning(base, {"judge": judged_by(args.judge_model)})):
        print(warning + "\n", flush=True)  # before the run too: stop it if a comparison is what you're after
    results: dict[str, list[Trial]] = {c["id"]: [] for c in cases}

    async def one(case: dict) -> None:
        t = await trial(case, library, model, args.judge_model, jobs, args.mode)
        results[case["id"]].append(t)
        show(case["id"], t)

    def so_far() -> dict:
        return tally_of(cases, results, model=shown, commit=commit(), judge=judged_by(args.judge_model))

    full = not (args.filter or args.held_out or args.model or args.effort)  # every case, as the mode ships
    try:
        await ask_all(cases, base, args.trials, args.judge_model, one, so_far, confirm=not args.no_confirm)
    finally:  # the answers are kept before anything is made of them: no fault past here loses a paid run
        tally = so_far() | {"tested": tested(results)}
        if any(results.values()):
            out = keep(HERE / "results", results, tally, mode=args.mode, connector_only=args.mode == "connector",
                       judge_model=args.judge_model, filter=args.filter, full=full, billed_to=args.billed_to)
            print(f"\nsaved {out.relative_to(HERE.parent)}", flush=True)
    cost = sum(t.cost_usd for ts in results.values() for t in ts)
    lines, found = report(tally, results, base, args.mode,
                          f"({shown}, {args.mode}, judge {args.judge_model}, ${cost:.2f})")
    print("\n" + "\n".join(lines))
    if full:
        record_full_run(tally, args.mode, found.lines[-1] if found else "no baseline to compare with",
                        f"{datetime.now(UTC):%Y-%m-%d}")
    if args.save_baseline is not None:
        save_baseline(tally, args.mode, args.accept, args.filter, out.name if any(results.values()) else "")
    return exit_code(tally, found)


def keep(folder: Path, results: dict[str, list[Trial]], tally: dict, **about) -> Path:
    """A run's file: every answer with its tool calls, and what read_run needs to tally it again (`about`: its mode,
    judge, filter, whether it was a full run, who was billed)."""
    folder.mkdir(exist_ok=True)
    out = folder / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({**about, "date": f"{datetime.now(UTC):%Y-%m-%d}",
                               **{k: tally[k] for k in ("model", "trials", "commit", "judge", "tested", "case_hashes")},
                               "cases": {c: [asdict(t) for t in ts] for c, ts in results.items()}},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def exit_code(tally: dict, found: Comparison | None) -> int:
    """1, with why on a last line, when a run is red (red), else 0."""
    if why := red(tally, found):
        print(f"Exits non-zero: {'; '.join(why)}.")
    return 1 if why else 0


def read_run(path: Path) -> tuple[dict, dict[str, list[Trial]], dict]:
    """A saved run: the file, its trials per case as today's rule reads them (settle), and its tally, with the cases'
    hashes as the run asked them."""
    run = json.loads(path.read_text(encoding="utf-8"))
    run["mode"] = run.get("mode") or ("connector" if run.get("connector_only") else "plugin")
    known = {c["id"]: c for c in load_cases()}
    kept = {f.name for f in fields(Trial)}
    results = {c: [settle(Trial(**{k: v for k, v in t.items() if k in kept}),
                          known.get(c, {"id": c, "question": ""}), run["mode"]) for t in ts]
               for c, ts in run["cases"].items()}
    tally = tally_of([known.get(c, {"id": c}) for c in results], results, model=run.get("model", "?"),
                     commit=run.get("commit", "?"), judge=run.get("judge") or judged_by(run.get("judge_model", "")))
    kept = {k: run[k] for k in ("case_hashes", "tested") if run.get(k)}  # as the run asked them, and what it tested
    return run, results, tally | kept


def report(tally: dict, results: dict[str, list[Trial]], base: dict | None, mode: str,
           about: str) -> tuple[list[str], Comparison | None]:
    """What a run prints once it's asked, and a saved one with --summary: its summary, how its answers were made
    and built, and the comparison with its mode's baseline."""
    lines = [*summary(tally, base), about, *filter(None, [tested_line(tally)]), how_answered(tally, tally["passed"]),
             *built_summary((t.form, t.slips) for ts in results.values() for t in ts if not t.errored)]
    if mode == "plugin":
        lines.append(skill_use(results))
    found = compared(base, tally) if base else None
    return lines + (["", *found.lines] if found else []), found


def show_run(path: Path) -> int:
    """A saved run's summary and comparison again, by today's rules (no model); 1 as a run exits 1."""
    run, results, tally = read_run(path)
    base = json.loads(baseline(run["mode"]).read_text()) if baseline(run["mode"]).exists() else None
    lines, found = report(tally, results, base, run["mode"],
                          f"({path.name}: {tally['model']}, {run['mode']}, commit {tally['commit']})")
    print("\n".join(lines))
    return exit_code(tally, found)


def record_full_run(tally: dict, mode: str, verdict: str, day: str) -> dict | None:
    """Keep a full run as its mode's last in FULL_RUNS: its commit and day, its trials, passes of answers, errors
    and the verdict line of its comparison. The file is tracked: commit it with the change the run covered. A run
    that judged fewer than COVERED of the answers it asked is not kept: a release would count from a run that
    evaluated little."""
    of, errors = sum(judged(tally, c) for c in tally["passed"]), sum(tally.get("errors", {}).values())
    if of < COVERED * (of + errors):
        print(f"not recorded as the last full {mode} run: {errors} of its {of + errors} answers errored, so the "
              f"mode's last full run stays the one in evals/{FULL_RUNS.name}")
        return None
    runs = json.loads(FULL_RUNS.read_text(encoding="utf-8")) if FULL_RUNS.exists() else {}
    runs[mode] = {"commit": tally["commit"], "date": day, "trials": trials_of(tally),
                  "passed": sum(tally["passed"].values()), "of": of, "errors": errors, "verdict": verdict}
    FULL_RUNS.write_text(json.dumps(runs, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"recorded as the last full {mode} run in evals/{FULL_RUNS.name}: commit it (scripts/release.py reads it)")
    return runs[mode]


def record_saved(path: Path) -> None:
    """A saved full run as its mode's last (record_full_run), for one made where the file couldn't be committed (the
    Evals workflow): no model. Its day is the file's own, else the date in its name, else when it was written."""
    run, _, tally = read_run(path)
    if run.get("filter"):
        raise SystemExit(f"not a full run: it asked only the cases matching -k {run['filter']}")
    if run.get("full") is False:
        raise SystemExit("not a full run of what the mode ships: it was held out, or asked with --model or --effort")
    mine = regular(run["mode"])
    if (asked := sum(c["id"] in tally["passed"] for c in mine)) < COVERED * len(mine):  # a file older than `filter`
        raise SystemExit(f"not a full run: it asked {asked} of the {len(mine)} cases its mode has today")
    base = json.loads(baseline(run["mode"]).read_text()) if baseline(run["mode"]).exists() else None
    named = re.search(r"(20\d\d)(\d\d)(\d\d)", path.stem)
    day = run.get("date") or ("-".join(named.groups()) if named else
                              f"{datetime.fromtimestamp(path.stat().st_mtime, UTC):%Y-%m-%d}")
    record_full_run(tally, run["mode"], compared(base, tally).lines[-1] if base else "no baseline to compare with",
                    day)


def save_baseline(tally: dict, mode: str, accept: str = "", filtered: str = "", run: str = "") -> None:
    """Keep a run as its mode's baseline, after printing the comparison with the one it replaces. A run with a case
    asked fewer than UNSETTLED_TRIALS times is never one. A run that errored, in which a tool crashed, that doesn't
    cover every case of its mode as it is today, or is below the baseline it replaces by LOOK spreads or more is
    refused, unless `accept` says why: the reason is kept in the file, with what it replaced and the like-for-like
    total on the unchanged cases.

    A run of some cases (`filtered`, its -k) is merged into the baseline there is: its cases replace their own
    entries, every other case's stays, and the file says which cases came from which commit and run (`run`, its
    file). The same is refused for the cases it merges, and a run by another model or judge than the baseline's is
    never merged. So a corrected case costs three trials of itself, not of the suite."""
    path = baseline(mode)
    old = json.loads(path.read_text()) if path.exists() else None
    if thin := [c for c in tally["passed"] if judged(tally, c) < UNSETTLED_TRIALS]:
        raise SystemExit(f"not a baseline: {len(thin)} cases have fewer than {UNSETTLED_TRIALS} judged answers "
                         f"({', '.join(thin[:5])}{', ...' if len(thin) > 5 else ''}): a baseline asks every case as "
                         "often, to judge the next run (--trials 3)")
    if filtered and old:
        return merge_baseline(old, tally, mode, accept, filtered, run)
    stale = [c["id"] for c in regular(mode)
             if c["id"] not in tally["passed"] or tally["case_hashes"].get(c["id"]) != case_hash(c)]
    doubts = red(tally, None)  # answers that errored, a tool that crashed: the next runs would be measured by them
    if filtered or stale:
        doubts.append(f"it doesn't cover every case as it is today ({filtered and f'-k {filtered}: '}{len(stale)} "
                      f"missing or changed since{': ' + ', '.join(stale[:5]) if stale else ''})")
    replaced = {}
    if old:
        found = compared(old, tally)
        print("Against the baseline it replaces:\n" + "\n".join(found.lines[1:]) + "\n")
        if found.verdict == "a drop shown" or found.low >= LOOK:
            doubts.append(f"it is {found.low:.1f} spreads below the baseline it replaces on the cases unchanged "
                          "since")
        like = next((line.strip() for line in found.lines if line.startswith("  total ")), "")
        replaced = {"commit": old.get("commit", "?"), "trials": trials_of(old),
                    "passed": [sum(old["passed"].values()), sum(judged(old, c) for c in old["passed"])],
                    "like_for_like": like, "verdict": found.lines[-1]}
    if doubts and not accept:
        raise SystemExit(f"not saved as the baseline: {'; '.join(doubts)}. To keep it all the same, say why: "
                         '--accept "<reason>" (kept in the file).')
    kept = tally | ({"replaced": replaced} if replaced else {}) | (
        {"accepted": {"reason": accept, "despite": doubts}} if doubts else {})
    path.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved evals/{path.name}" + (f" (accepted despite: {'; '.join(doubts)})" if doubts else ""))


# What a tally keeps per case, by case id: a merge replaces these entries for the cases of the run
PER_CASE = ("case_hashes", "passed", "failed", "errors", "live", "facts", "calls", "first_tagged", "words", "slips")


def merge_baseline(old: dict, tally: dict, mode: str, accept: str = "", filtered: str = "", run: str = "") -> None:
    """The cases of a filtered run put in place of their entries in the baseline (save_baseline says when)."""
    path, cases = baseline(mode), sorted(tally["passed"])
    for what, mine, its in (("answered by", tally.get("model"), old.get("model")),
                            ("judged by", tally.get("judge"), old.get("judge"))):
        if mine != its:
            raise SystemExit(f"not merged into the baseline: this run was {what} {mine}, the baseline {its}. Its "
                             "other cases wouldn't compare with these: save a baseline from a run of every case.")
    today = {c["id"]: case_hash(c) for c in regular(mode)}
    doubts = red(tally, None)
    if stale := [c for c in cases if tally["case_hashes"].get(c) != today.get(c)]:
        doubts.append(f"{len(stale)} of its cases changed since the run or are not this mode's "
                      f"({', '.join(stale[:5])})")
    found = compared(old, tally)  # on the cases it ran that are unchanged since the baseline
    print("Against the baseline's own entries for these cases:\n" + "\n".join(found.lines[1:]) + "\n")
    if found.verdict == "a drop shown" or found.low >= LOOK:
        doubts.append(f"it is {found.low:.1f} spreads below the baseline's entries for the cases unchanged since")
    if doubts and not accept:
        raise SystemExit(f"not merged into the baseline: {'; '.join(doubts)}. To keep it all the same, say why: "
                         '--accept "<reason>" (kept in the file).')
    new = scored(tally, old.get("parts", FIRST_PARTS))  # a baseline counts every case by one list of checks: its own
    def times(t: dict, c: str) -> int:
        return t["trials"] if isinstance(t["trials"], int) else t["trials"].get(c, 0)
    asked = {c: times(old, c) for c in old["passed"] if c not in new["passed"]} | {
        c: times(new, c) for c in new["passed"]}
    kept: dict = dict(old) | {"trials": next(iter(asked.values())) if len(set(asked.values())) == 1 else asked}
    for key in PER_CASE:
        if key in old or key in ("case_hashes", "passed"):
            kept[key] = {c: v for c, v in old.get(key, {}).items() if c not in new["passed"]} | {
                c: v for c, v in new.get(key, {}).items() if c in new["passed"]}
    if "broke" in old:
        whys = {why: {c: n for c, n in old["broke"].get(why, {}).items() if c not in new["passed"]}
                | new.get("broke", {}).get(why, {}) for why in {*old["broke"], *new.get("broke", {})}}
        kept["broke"] = {why: found for why, found in whys.items() if found}
    if "hard" in old:
        kept["hard"] = [c for c in old["hard"] if c not in new["passed"]] + new.get("hard", [])
    kept["merged"] = old.get("merged", []) + [{
        "cases": cases, "commit": tally.get("commit", "?"), "run": run, "filter": filtered,
        "trials": trials_of(tally), "tested": tally.get("tested", {}),
        "passed": [sum(new["passed"].values()), sum(judged(new, c) for c in cases)],
        "before": {c: [old["passed"][c], judged(old, c)] for c in cases if c in old["passed"]},
        "verdict": found.lines[-1]} | ({"accepted": {"reason": accept, "despite": doubts}} if doubts else {})]
    path.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"merged {len(cases)} cases into evals/{path.name} ({', '.join(cases[:5])}{', ...' if len(cases) > 5 else ''}"
          f"): {len(asked) - len(cases)} other cases keep their entries"
          + (f" (accepted despite: {'; '.join(doubts)})" if doubts else ""))


def tested(results: dict[str, list[Trial]]) -> dict:
    """What a run tested besides its commit, kept with the run and with a baseline so that a comparison can say when
    the two differ: the models that answered and judged by the full names the API gave (an alias such as "opus"
    moves to another model without a word), the Agent SDK and the Claude Code it drives, the answers' effort,
    whether the code had uncommitted changes, and when the frozen data was last added to."""
    try:
        from claude_agent_sdk._cli_version import __cli_version__ as cli
    except ImportError:  # the SDK moved it
        cli = "?"
    changed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True,
                             cwd=HERE).stdout.strip()
    return {"answers": sorted({t.model for ts in results.values() for t in ts if t.model}),
            "judge": sorted(MODELS), "sdk": version("claude-agent-sdk"), "cli": cli,
            "effort": config.effort() or "", "uncommitted": bool(changed), "data": data_stored()}


def data_stored(path: Path | None = None) -> str:
    """When the frozen data was last added to (UTC), or "" without a file: a run that fetched what the file lacked
    moves it, and the next run reads data the baseline's didn't."""
    path = path or CACHE
    if not path.exists():
        return ""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        latest = db.execute("SELECT MAX(stored_at) FROM cache").fetchone()[0]
    return f"{datetime.fromtimestamp(latest, UTC):%Y-%m-%d %H:%M}" if latest else ""


def tested_line(tally: dict) -> str:
    """A run's `tested` as one line, or '' for a run from before it was kept."""
    t = tally.get("tested")
    if not t:
        return ""
    return (f"Tested: answers by {', '.join(t['answers']) or '?'}"
            + (f" at effort {t['effort']}" if t["effort"] else "")
            + f"; judged by {', '.join(t['judge']) or '?'}; Agent SDK {t['sdk']}, Claude Code {t['cli']}; commit "
            + tally.get("commit", "?") + (" with uncommitted changes" if t["uncommitted"] else "")
            + f"; frozen data last added to {t['data'] or '?'}")


def tested_alike(base: dict, now: dict) -> str:
    """A comparison's warning when the run and the baseline weren't tested alike (tested), or ''."""
    was, t = base.get("tested"), now.get("tested")
    if not t:
        return ""
    if not was:
        return ("  note: the baseline is older than the record of what a run tested: its models, versions and data "
                "may differ from this run's")
    names = {"answers": "the answers' model", "judge": "the judge's model", "sdk": "Agent SDK", "cli": "Claude Code",
             "effort": "effort", "data": "frozen data last added to"}

    def shown(v) -> str:
        return (", ".join(v) if isinstance(v, list) else str(v)) or "none"
    differs = [f"{name} {shown(was.get(k))} -> {shown(t.get(k))}" for k, name in names.items()
               if was.get(k) != t.get(k)]
    return f"  WARNING: not tested alike: {'; '.join(differs)}" if differs else ""


def freeze_today() -> None:
    """The date in each question's first line as of the recorded data's "now", like the tools' clock: the bot's
    prompt takes the real date, so a run asked about the same report as "yesterday's" one day and as weeks old the
    next. Wraps prompt.message, which the session calls for every question."""
    real = prompt.message
    if real.__module__ == __name__:  # frozen already
        return

    def message(*args, **kwargs) -> str:
        kwargs.setdefault("now", datetime.fromtimestamp(RECORDED_AT).astimezone())
        return real(*args, **kwargs)
    setattr(prompt, "message", message)  # noqa: B010  the module's own function, replaced for this process


def tally_of(cases: list[dict], results: dict[str, list[Trial]], **about) -> dict:
    """What a run found, as it's compared and as a baseline keeps it (`about`: its model, commit and judge): each
    case's trials, passes and errors, the checks it was scored by and which of them each failed answer failed, the
    answers that failed whatever the judge said by why (`broke`), the answers that asked Warcraft Logs live, and
    what the answers did besides passing (answers)."""
    asked = {c: len(ts) for c, ts in results.items()}
    return {**about, "trials": next(iter(asked.values())) if len(set(asked.values())) == 1 else asked,
            "case_hashes": {c["id"]: case_hash(c) for c in cases},
            "parts": [p for p in PARTS if p in FIRST_PARTS or any(p in c for c in cases)],  # a case's key asks
            "passed": {c: sum(t.passed for t in ts) for c, ts in results.items()},
            "failed": {c: f for c, ts in results.items()
                       if (f := [failed_parts(t) for t in ts if not t.passed and not t.errored])},
            "errors": {c: n for c, ts in results.items() if (n := sum(t.errored for t in ts))},
            "broke": {why: found for why in (CRASH, CAP, SLOW) if (found := {
                c: n for c, ts in results.items() if (n := sum(t.broke.startswith(why) for t in ts))})},
            "live": {c: n for c, ts in results.items() if (n := sum(asked_live(t) for t in ts))},
            "hard": [c["id"] for c in cases if c.get("hard")]} | answers(cases, results)


def failed_parts(t: Trial) -> list[str]:
    """Why an answer failed: the named checks it failed, and "broke" for what fails it whatever the checks say."""
    return (["broke"] if t.broke else []) + [part for part, ok in t.checks.items() if not ok]


def scored(tally: dict, parts: Iterable[str]) -> dict:
    """The tally with its passes counted by another list of checks, a baseline's: an answer that failed only a check
    the baseline never made passes. A tally from before the checks were named is kept as it is."""
    parts = set(parts)
    if "failed" not in tally or parts == set(tally.get("parts", FIRST_PARTS)):
        return tally
    passed = {c: judged(tally, c) - sum(bool((parts | {"broke"}) & set(f)) for f in tally["failed"].get(c, []))
              for c in tally["passed"]}
    return {**tally, "passed": passed, "parts": sorted(parts)}


def asked_live(t: Trial) -> bool:
    """Whether an answer's tool calls asked Warcraft Logs at run time: for data the frozen file didn't hold."""
    return any(c.get("wcl_points") for c in t.calls)


# ---- comparing runs ----
# A run's tally (tally_of) and a baseline have the same keys: model, commit, trials (each case's, or {case: trials}
# when they differ: --trials auto), judge ({prompts: hash, model}), case_hashes ({case: hash}), passed and errors
# ({case: trials}). An errored trial counts in neither passes nor trials.
# With --trials auto, the answers a case gets at first: a case the baseline judged the same on every trial, and every
# other case. Every answer counts; the total weighs each case as the baseline did, however many answers it got.
STEADY_TRIALS, UNSETTLED_TRIALS = 2, 3
ESCALATE = 0.5  # spreads the auto total is below the baseline's when the steady cases get their third answer too
CONFIRM_TRIALS = 6  # a case that moved beyond noise is asked up to this many answers before it is printed
LOOK, SHOWN = 1.0, 2.0  # spreads the total is from the baseline's: worth a look, and shown (the exit code)
STAYED = 0.02  # a moved case has stayed there when chance splits its answers this unevenly no more often than this
COVERED = 0.9  # of a run's cases, the share a comparison needs to speak for the run
TOO_WIDE = 10  # points of the pass rate: a comparison that couldn't have shown a smaller drop is on too few answers


def settled(base: dict | None, cases: list[dict], judge_model: str) -> dict[str, bool]:
    """The cases --trials auto asks STEADY_TRIALS times, with the verdict the baseline gave on every one of its
    trials (two or more): unchanged since it, judged by the same judge. A case that passed or failed only sometimes,
    a new or changed one, or any case after the judge changed is asked UNSETTLED_TRIALS times."""
    if not base or base.get("judge") != judged_by(judge_model):
        return {}
    sure = {}
    for c in cases:
        n, of = base["passed"].get(c["id"]), judged(base, c["id"])
        if n is not None and of >= 2 and n in (0, of) and base.get("case_hashes", {}).get(c["id"]) == case_hash(c):
            sure[c["id"]] = n == of
    return sure


def planned(base: dict | None, cases: list[dict], judge_model: str, trials: int | str) -> dict[str, int]:
    """The answers each case gets at first: `trials`, or with auto, by what the baseline made of it (settled)."""
    if trials != "auto":
        return {c["id"]: int(trials) for c in cases}
    sure = settled(base, cases, judge_model)
    return {c["id"]: STEADY_TRIALS if c["id"] in sure else UNSETTLED_TRIALS for c in cases}


async def ask_all(cases: list[dict], base: dict | None, trials: int | str, judge_model: str,
                  one: Callable[[dict], Awaitable[None]], so_far: Callable[[], dict], confirm: bool = True) -> None:
    """Ask every case its planned answers (`one` asks a case once and keeps the answer; `so_far` is the run's tally
    until now), then what the comparison with the baseline calls for:
    - with auto, the third answer for the steady cases too once the total is ESCALATE spreads below the baseline's
      or SHOWN above, so a run that looks low, or shows a rise, is a three-trial run and reads as one;
    - in any run (`confirm`), more answers, up to CONFIRM_TRIALS, for each case that moved beyond noise, so that no
      case is printed as moved on the luck of three answers. A second round covers answers that errored."""
    plan = planned(base, cases, judge_model, trials)
    if trials == "auto":
        steady = sum(n == STEADY_TRIALS for n in plan.values())
        print(f"{steady} of {len(cases)} cases asked {STEADY_TRIALS} times, the baseline's verdict on every trial; "
              f"the others {UNSETTLED_TRIALS} times.\n", flush=True)
    await asyncio.gather(*(one(c) for c in cases for _ in range(plan[c["id"]])))
    if not base or judge_warning(base, so_far()):
        return
    short = [c for c in cases if plan[c["id"]] < UNSETTLED_TRIALS]
    if short and ((low := compared(base, so_far()).low) >= ESCALATE or low <= -SHOWN):
        print(f"\nThe total is {abs(low):.1f} spreads {'below' if low > 0 else 'above'} the baseline's: asking the "
              f"{len(short)} steady cases a third time, as a three-trial run would.\n", flush=True)
        await asyncio.gather(*(one(c) for c in short for _ in range(UNSETTLED_TRIALS - plan[c["id"]])))
    for _ in range(2 if confirm else 0):
        now = so_far()
        more = {c["id"]: c for c in cases if c["id"] in compared(base, now).again}
        if not more:
            break
        print(f"\nAsking again, up to {CONFIRM_TRIALS} answers each, the cases that moved beyond noise: "
              f"{', '.join(more)}\n", flush=True)
        await asyncio.gather(*(one(c) for c in more.values() for _ in range(CONFIRM_TRIALS - judged(now, c["id"]))))


def trials_of(tally: dict) -> str:
    t = tally["trials"]
    return f"{t} trials" if isinstance(t, int) else f"{min(t.values(), default=0)}-{max(t.values(), default=0)} trials"


def short_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def judged_by(model: str) -> dict:
    """The judge, its prompts' hash and its model: a run judged by another isn't comparable answer for answer."""
    return {"prompts": short_hash([EXPECT, WRONG, FACTS, f"fails judged {FAIL_VOTES} times"]), "model": model}


def case_hash(case: dict) -> str:
    """What an answer is asked with and what the judge is asked (question, expect, facts, wrong, calls, its report
    and who asks), spaces aside, so a reflowed line keeps the hash and a changed word doesn't. Left out is what only
    code reads: its id, the hard set, the lint's `derived` and `drifts`, the coverage's `tools`, the engine's
    `cause`, its file's `kind`, and the code checks' `numbers` and `form`. So a case that gains a code check still
    compares with a baseline saved before it."""
    def plain(v):
        return " ".join(v.split()) if isinstance(v, str) else [plain(x) for x in v] if isinstance(v, list) else v
    left_out = ("id", "hard", "derived", "drifts", "tools", "cause", "kind", "numbers", "form")
    return short_hash({k: plain(v) for k, v in case.items() if k not in left_out})


def judged(tally: dict, case_id: str) -> int:
    t = tally["trials"]
    return (t if isinstance(t, int) else t.get(case_id, 0)) - tally.get("errors", {}).get(case_id, 0)


def beyond_noise(passed: int, of: int, base_passed: int, base_of: int) -> bool:
    """Whether a case moved further than chance takes it: its pass rate changed by at least 1/√n, n the fewer
    trials of the two (2 of 3, 3 of 5, 4 of 10; about 1.4 standard deviations of the difference at a 50% rate)."""
    return abs(passed / of - base_passed / base_of) >= 1 / math.sqrt(min(of, base_of)) - 1e-9


def by_chance(passed: int, of: int, base_passed: int, base_of: int) -> float:
    """How often chance alone splits a case's answers between the run and the baseline at least this unevenly, were
    its pass rate the same in both (Fisher's exact test, one side): 0 of 6 after 3 of 3 is 1 in 84, 1 of 6 is 1 in
    21, 0 of 3 is 1 in 20."""
    passes = passed + base_passed
    fewer = range(max(0, passes - base_of), passed + 1) if passed * base_of < base_passed * of \
        else range(passed, min(of, passes) + 1)
    return sum(math.comb(of, k) * math.comb(base_of, passes - k) for k in fewer) / math.comb(of + base_of, passes)


def spread(rates: dict[str, float], trials: dict[str, int], base_trials: dict[str, int] | None = None) -> float:
    """One standard deviation of a total from run to run, each case's trials passing at its rate: the difference
    from a baseline (its noise scaled to these trials), or with none, between two runs like this one."""
    base_trials = base_trials or trials
    return math.sqrt(sum(p * (1 - p) * (trials[c] + trials[c] ** 2 / base_trials[c]) for c, p in rates.items()))


def errors_line(tally: dict) -> str:
    """How many answers errored, and of which cases: the first line of a summary and of a comparison, since an
    errored answer is in no count below it."""
    errors = tally.get("errors", {})
    if not errors:
        return "No answer errored."
    n = sum(errors.values())
    return (f"ERRORS: {n} answer{'s' if n > 1 else ''} errored and {'are' if n > 1 else 'is'} in no count below: "
            + ", ".join(f"{c} ({n})" for c, n in errors.items()))


def broke_lines(tally: dict) -> list[str]:
    """The answers that failed whatever the judge made of their text, by why, a line each: they are failed answers
    in every count below, and a crashed tool is a fault in the code that no count would show as one."""
    return [f"{(n := sum(cases.values()))} answer{'s' * (n > 1)} failed whatever the judge said ({why}): "
            + ", ".join(f"{c} ({n})" for c, n in cases.items()) for why, cases in tally.get("broke", {}).items()]


def red(tally: dict, found: Comparison | None) -> list[str]:
    """Why a run exits non-zero, or none: an answer errored, a tool crashed, or the comparison shows a drop."""
    errors, crashed = sum(tally.get("errors", {}).values()), sum(tally.get("broke", {}).get(CRASH, {}).values())
    return [why for why in (f"{errors} answer{'s' * (errors > 1)} errored" if errors else "",
                            f"a tool crashed in {crashed} answer{'s' * (crashed > 1)}" if crashed else "",
                            "the comparison shows a drop" if found and found.verdict == "a drop shown" else "") if why]


def known_failure(passed: int, of: int) -> bool:
    """Whether a baseline fails a case more than once in three: a failure of it now is no news."""
    return of - passed > of / 3


def summary(tally: dict, base: dict | None = None) -> list[str]:
    """What a run found, first what could flatter it: the answers that errored, those that failed whatever the
    judge said (a crashed tool, the bot's cap, its wait), those that asked Warcraft Logs live, and with a baseline
    how many cases the comparison skips and why. Then each case's passes over its judged trials (flaky when it both
    passed and failed, with the baseline's result where that wasn't a pass on every trial), the total and its
    spread, how many cases were right on every trial, and from the baseline: the totals where it always, sometimes
    and never passed, and its known failures."""
    errors = tally.get("errors", {})
    lines = [errors_line(tally), *broke_lines(tally)]
    if "live" in tally:  # a run's; a baseline from before 10 November has no count
        asked = sum(judged(tally, c) + errors.get(c, 0) for c in tally["passed"])
        lines.append(f"{sum(tally['live'].values())} of {asked} answers asked Warcraft Logs live, for data the "
                     "frozen file didn't hold.")
    same, skipped = unchanged(base, tally) if base else ([], {})
    if base:
        why = "; ".join(f"{len(cs)} {reason}" for reason, cs in skipped.items())
        lines.append(f"{len(same)} of {len(tally['passed'])} cases are compared with the baseline"
                     + (f"; skipped: {why}" if why else ""))
    was = {c: (base["passed"][c], judged(base, c)) for c in same} if base else {}
    rates, trials = {}, {}
    for c, n in tally["passed"].items():
        of, (b, b_of) = judged(tally, c), was.get(c, (1, 1))
        lines.append(f"  {c:34} {n}/{of}" + ("  flaky" if 0 < n < of else "")
                     + ("" if b == b_of else f"  ({'known failure: ' if known_failure(b, b_of) else ''}"
                                             f"baseline {b}/{b_of})")
                     + (f"  ({errors[c]} errored)" if errors.get(c) else ""))
        if of:
            rates[c], trials[c] = n / of, of

    def got(cases: Iterable[str]) -> str:
        cases = [c for c in cases if c in trials]
        return f"{sum(tally['passed'][c] for c in cases)}/{sum(trials[c] for c in cases)}"
    flaky, errored = sum(0 < tally["passed"][c] < trials[c] for c in trials), sum(errors.values())
    lines.append(f"The main point got across in {got(trials)} answers, ±{spread(rates, trials):.1f} from run to run; "
                 f"{flaky} flaky" + (f"; {errored} errored, not counted" if errored else ""))
    if any(t > 1 for t in trials.values()):  # what a raider meets: a case right only sometimes isn't right
        lines.append(f"{sum(tally['passed'][c] == trials[c] for c in trials)}/{len(trials)} cases right on every "
                     "trial")
    for name, cases in (("always passed", [c for c in same if was[c][0] == was[c][1]]),
                        ("passed sometimes", [c for c in same if 0 < was[c][0] < was[c][1]]),
                        ("never passed", [c for c in same if not was[c][0]])):
        if cases:
            lines.append(f"  where the baseline {name} ({len(cases)} case{'s' * (len(cases) > 1)}): {got(cases)}")
    if known := [f"{c} {tally['passed'][c]}/{judged(tally, c)} (baseline {b}/{b_of})"
                 for c, (b, b_of) in was.items() if known_failure(b, b_of)]:
        lines.append("Known failures, which the baseline fails more than once in three: " + ", ".join(known))
    return lines


def judge_warning(base: dict, now: dict) -> str:
    """Why differences from the baseline may be the judge's, or ''."""
    was, judge_now = base.get("judge"), now["judge"]
    if was is None or was == judge_now:
        return ""
    what = [f"{k} {was.get(k)} -> {judge_now[k]}" for k in ("prompts", "model") if was.get(k) != judge_now[k]]
    return f"WARNING: the judge changed since the baseline: differences may be the judge's ({', '.join(what)})"


@dataclass
class Comparison:
    lines: list[str]  # as printed: the last one is the verdict
    verdict: str  # "no change shown", "a drop shown", "a rise shown" or "cannot tell"
    low: float = 0.0  # spreads the total is below the baseline's (negative: above)
    again: list[str] = field(default_factory=list)  # moved beyond noise on fewer than CONFIRM_TRIALS answers


def compare(base: dict, now: dict) -> list[str]:
    return compared(base, now).lines


def unchanged(base: dict, now: dict) -> tuple[list[str], dict[str, list[str]]]:
    """The run's cases a comparison with the baseline covers, and the ones it skips, by why."""
    hashes = base.get("case_hashes")
    skipped = {"changed since the baseline": [], "not in it": [], "every trial errored": []}
    same = []
    for c in now["passed"]:
        if c not in base["passed"]:
            skipped["not in it"].append(c)
        elif hashes is not None and hashes.get(c) != now["case_hashes"].get(c):
            skipped["changed since the baseline"].append(c)
        elif not (judged(now, c) and judged(base, c)):
            skipped["every trial errored"].append(c)
        else:
            same.append(c)
    return same, {reason: cs for reason, cs in skipped.items() if cs}


def total(base: dict, now: dict, cases: list[str]) -> tuple[int, int, float, float]:
    """Over `cases`: their weight (the baseline's trials), the baseline's passes, the run's at that weight (each
    case's pass rate now times the baseline's trials), and the spread of the difference from run to run."""
    of, base_of = {c: judged(now, c) for c in cases}, {c: judged(base, c) for c in cases}
    rates = {c: (now["passed"][c] + base["passed"][c]) / (of[c] + base_of[c]) for c in cases}
    return (sum(base_of.values()), sum(base["passed"][c] for c in cases),
            sum(base_of[c] * now["passed"][c] / of[c] for c in cases), spread(rates, base_of, of))


def moves(base: dict, now: dict, same: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    """The cases whose pass rate differs from the baseline's: those beyond noise as printed, with how often chance
    does that and whether they stayed there on CONFIRM_TRIALS answers; those within noise; the ones beyond noise
    on fewer answers than that (to ask again); and the ones that stayed down."""
    moved, noise, again, down = [], [], [], []
    for c in same:
        n, of, b, base_of = now["passed"][c], judged(now, c), base["passed"][c], judged(base, c)
        if n * base_of == b * of:
            continue
        if not beyond_noise(n, of, b, base_of):
            noise.append(f"{c} {n}/{of} (baseline {b}/{base_of})")
            continue
        chance = by_chance(n, of, b, base_of)
        stayed = of >= CONFIRM_TRIALS and chance <= STAYED
        if stayed and n * base_of < b * of:
            down.append(c)
        if of < CONFIRM_TRIALS:
            again.append(c)
        moved.append(f"{c} {n}/{of} (baseline {b}/{base_of}), by chance 1 in {1 / chance:.0f}"
                     + (f": stayed {'down' if c in down else 'up'}" if stayed else ""))
    return moved, noise, again, down


def verdict(above: float, width: float, weight: int, down: list[str], cannot: list[str],
            void: bool) -> tuple[str, str]:
    """A comparison's verdict and its line. `above`: the total against the baseline's in spreads of `width` answers,
    over `weight` answers; `down`: the cases that stayed down; `cannot`: why the total can't tell; `void`: the judge
    changed, so nothing shows a drop."""
    smallest = SHOWN * width  # the drop of the total this run would have shown, in answers
    points = 100 * smallest / weight
    few = not width or points > TOO_WIDE
    reach = f"a drop of {smallest:.1f} answers ({points:.1f} points) or more would have shown"
    low = f"the total is {-above:.1f} spreads below the baseline's"
    if (above <= -SHOWN and not few or down) and not void:
        said = "; ".join(x for x in (low if above <= -SHOWN and not few else "",
                                     f"stayed down on {CONFIRM_TRIALS} answers: {', '.join(down)}" if down else "")
                         if x) + (f" (but {'; '.join(cannot)})" if cannot else "")
        return "a drop shown", f"VERDICT: a drop shown: {said}."
    if cannot:
        return "cannot tell", (f"VERDICT: cannot tell: {'; '.join(cannot)}."
                               + (f" Look: {low}." if above <= -LOOK else ""))
    if above >= SHOWN:
        return "a rise shown", f"VERDICT: a rise shown: the total is {above:.1f} spreads above the baseline's."
    if above <= -LOOK:
        return "no change shown", (f"VERDICT: no change shown, but look: {low} (a drop is shown at {SHOWN:.0f}); "
                                   f"{reach}.")
    return "no change shown", f"VERDICT: no change shown: {reach}."


def compared(base: dict, now: dict) -> Comparison:
    """The run against the baseline on the cases unchanged since it, ending in one verdict line.

    The total weighs each case as the baseline did (total), so it doesn't turn on which cases got more answers this
    time; the cases the baseline judged the same on every trial and the others are also totalled apart. The cases
    that moved beyond noise come one per line with how often chance does that, the others on one line. The verdict
    is a drop shown when the total is SHOWN spreads below the baseline's or a case stayed down on CONFIRM_TRIALS
    answers, a look at LOOK spreads, and cannot tell when the judge changed, the baseline counts a check the run
    never made, cases were skipped or the answers are too few to have shown a drop."""
    warning = judge_warning(base, now)
    lines = [warning] if warning else []
    lines.append(f"Against the baseline ({base['model']}, {base.get('commit', '?')}, {trials_of(base)}):")
    lines.append("  " + errors_line(now))
    parts, mine = base.get("parts", FIRST_PARTS), now.get("parts", FIRST_PARTS)
    if added := [part for part in mine if part not in parts]:
        lines.append(f"  scored by the baseline's checks ({', '.join(parts)}): this run's {', '.join(added)} "
                     "is left out here, until a baseline has it")
    if unmade := [part for part in parts if part not in mine]:  # a run from before the check: none of it failed it
        lines.append(f"  the baseline counts a check this run never made ({', '.join(unmade)}): every answer here "
                     "passes it unseen")
    now = scored(now, parts)
    if differs := tested_alike(base, now):
        lines.append(differs)
    if base.get("case_hashes") is None:
        lines.append("  note: the baseline is older than case and judge hashes: every case is compared, though a "
                     "case or the judge may have changed since")
    same, skipped = unchanged(base, now)
    why = [f"{len(cs)} {reason} ({', '.join(cs)})" for reason, cs in skipped.items()]
    lines.append(f"  compared {len(same)} of {len(now['passed'])} cases"
                 + (f"; skipped {'; '.join(why)}" if why else ""))
    if not same:
        lines.append("VERDICT: cannot tell: none of this run's cases was compared.")
        return Comparison(lines, "cannot tell")
    weight, was, at, width = total(base, now, same)
    above = (at - was) / width if width else 0.0  # in spreads
    lines.append(f"  total {at:.4g} of {weight}, each case weighed as in the baseline, whose total was {was}: "
                 f"{at - was:+.3g}, {abs(above):.1f} times the ±{width:.1f} expected from run to run")
    steady = [c for c in same if judged(base, c) >= 2 and base["passed"][c] in (0, judged(base, c))]
    if steady and len(steady) < len(same):
        for name, cases in ((f"the {len(steady)} the baseline judged the same on every trial", steady),
                            ("the others", [c for c in same if c not in steady])):
            w, b, n, sd = total(base, now, cases)
            lines.append(f"    {name}: {n:.4g} of {w}, the baseline's {b}: {n - b:+.3g} (±{sd:.1f})")
    if "facts" in base and "facts" in now:  # baselines from before 22 October have none
        lines.append(f"  answers: {how_answered(now, same)}\n  the baseline's: {how_answered(base, same)}")
    moved, noise, again, down = moves(base, now, same)
    fewest = min(min(judged(now, c), judged(base, c)) for c in same)
    lines.append(f"  moved beyond noise (pass rate by 1/√n or more, n the fewer trials: "
                 f"{math.ceil(math.sqrt(fewest) - 1e-9)} of {fewest}):" + "".join(f"\n    {m}" for m in moved)
                 + (" none" if not moved else ""))
    if noise:
        lines.append(f"  within noise: {', '.join(noise)}")
    answers = sum(judged(now, c) for c in same)
    # a case whose every trial errored is in no total, and its answers could have failed all the same
    errored = sum(now.get("errors", {}).get(c, 0) for c in [*same, *skipped.get("every trial errored", [])])
    cannot = ["the judge changed since the baseline"] if warning else []
    if unmade:
        cannot.append(f"the baseline counts a check this run never made ({', '.join(unmade)})")
    if len(same) < COVERED * len(now["passed"]):
        cannot.append(f"{len(now['passed']) - len(same)} of this run's {len(now['passed'])} cases not compared")
    if errored and width and above > -SHOWN >= (at - errored - was) / width:
        cannot.append(f"the {errored} errored answers could have made it a drop")
    if not width or 100 * SHOWN * width / weight > TOO_WIDE:
        cannot.append(f"too few answers ({answers} on {len(same)} cases)" + (
            f": a drop of the total under {100 * SHOWN * width / weight:.0f} points could not show" if width else ""))
    name, line = verdict(above, width, weight, down, cannot, bool(warning))
    lines.append(line)
    return Comparison(lines, name, -above, again)


async def recheck(path: Path, judge_model: str, only: str = "") -> None:
    """A saved run's answers checked again, without asking again: the misleading claims about the raid their tool
    outputs don't back (checks.grounded) and how each is built (checks.RUBRIC). Neither fails an answer; both are
    kept in results/rechecks/, under the run's name."""
    run = json.loads(path.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases()}
    client = run.get("mode", "plugin") != "bot"
    todo = [(c, i, t) for c, ts in run["cases"].items() if c in cases and picked(cases[c], only)
            for i, t in enumerate(ts) if t.get("answer") and not t.get("errored")]
    jobs = asyncio.Semaphore(2 * JOBS)
    out: dict[str, list[dict]] = {}

    async def one(c: str, i: int, t: dict) -> None:
        async with jobs:
            question = asked(cases[c], client)
            g, (scores, cost) = await asyncio.gather(grounded(question, t["answer"], t.get("calls") or []),
                                                     rubric(question, t["answer"], judge_model))
        out.setdefault(c, []).append({"trial": i, "passed": t.get("passed"), "grounded": asdict(g),
                                      "rubric": scores, "cost_usd": g.cost_usd + cost})
        flagged = [f"misleading: {x}" for x in g.misleading]
        print(f"{c:34} {'grounded' if g.ok else 'NOT GROUNDED' if g.ok is False else 'no verdict':12} "
              + " ".join(f"{k}={'-' if v is None else 'y' if v else 'N'}" for k, v in scores.items())
              + "".join(f"\n    {x[:240]}" for x in flagged), flush=True)

    await asyncio.gather(*(one(*x) for x in todo))
    print("\n" + "\n".join(recheck_summary(out)) + f"\n(grounded {GROUNDED_MODEL}, rubric {judge_model}, "
          f"${sum(x['cost_usd'] for xs in out.values() for x in xs):.2f})")
    saved = HERE / "results" / "rechecks" / path.name  # apart from the runs, which coverage and lint read
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"run": path.name, "judge_model": judge_model, "grounded_model": GROUNDED_MODEL,
                                "cases": out}, indent=1,
                                ensure_ascii=False), encoding="utf-8")
    print(f"saved {saved}")


def recheck_summary(out: dict[str, list[dict]]) -> list[str]:
    """How many answers state something misleading that their tool outputs don't back (and of those that passed), and
    each rubric rule's yes rate where it applied."""
    every = [x for xs in out.values() for x in xs]
    wrong = [x for x in every if x["grounded"]["misleading"]]
    lines = [f"answers stating something misleading their tool outputs don't back: {len(wrong)}/{len(every)} "
             f"({sum(bool(x['passed']) for x in wrong)} of them passed; "
             f"{sum(len(x['grounded']['misleading']) for x in every)} claims)"]
    for rule in RUBRIC:
        applied = [x["rubric"].get(rule) for x in every if x["rubric"].get(rule) is not None]
        lines.append(f"{rule}: {sum(applied)}/{len(applied)}" + (f" ({sum(applied) / len(applied):.0%})"
                                                                  if applied else ""))
    return lines


def form(path: Path, only: str = "") -> list[str]:
    """How a saved run's answers are built, measured again with today's code and no model (checks.built): each
    answer that leaves the form the bot's prompt asks for with its slips, then the run's summary. For a run from
    before the measure, or to compare the run before a change to the bot's prompt with the one after."""
    run = json.loads(path.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases()}
    lines, every = [], []
    for c, ts in run["cases"].items():
        for t in ts if c in cases and picked(cases[c], only) else []:
            if t.get("answer") and not t.get("errored"):
                every.append(built(cases[c], t["answer"], run.get("mode", "plugin")))
                if slips := every[-1][1]:
                    lines.append(f"{c:34} " + "; ".join(slips.values()))
    return lines + built_summary(every)


def commit() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          cwd=HERE).stdout.strip()


def label(path: Path) -> None:
    """Show a run's answers one at a time, without the judge's verdicts, and keep your pass or fail of each check
    in LABELS."""
    runs = json.loads(path.read_text(encoding="utf-8"))["cases"]
    _ask([(c, t["answer"], ("expect", "wrong")) for c, ts in runs.items() for t in ts if t["answer"]])


def label_failed(n: int) -> None:
    """Label `n` answers the judge failed in past runs (the check it failed), spread over the cases: the judge's
    agreement on good answers is well measured, on bad ones not (15 October: 8 of 80 labels)."""
    picked = failed_sample(n)
    print(f"{len(picked)} answers the judge failed, from {len({c for c, _, _ in picked})} cases")
    _ask(picked)


def failed_sample(n: int) -> list[tuple[str, str, tuple[str, ...]]]:
    """Up to `n` (case, answer, (the check the judge failed,)) from past runs not yet labelled, on cases unchanged
    since, one per case in turn, newest first."""
    cases = {c["id"]: c for c in load_cases()}
    done = {(d["case"], d["check"], d["answer"]) for d in read_labels()}
    by_case: dict[str, list[tuple[str, str]]] = {}
    for path in sorted((HERE / "results").glob("*.json"), reverse=True):  # newest first
        run = json.loads(path.read_text(encoding="utf-8"))
        hashes = run.get("case_hashes") or {}
        for c, trials in (run.get("cases") or {}).items():
            if c not in cases or hashes.get(c) != case_hash(cases[c]):  # judged against another version of it
                continue
            for t in trials:
                for check in ("expect", "wrong"):
                    item = (check, t.get("answer") or "")
                    if (t.get(check) or {}).get("ok") is False and item[1] and (c, *item) not in done \
                            and item not in by_case.get(c, []):
                        by_case.setdefault(c, []).append(item)
    picked: list[tuple[str, str, tuple[str, ...]]] = []
    while len(picked) < n and any(by_case.values()):  # one per case in turn, so no case fills the sample
        for c in sorted(by_case):
            if by_case[c] and len(picked) < n:
                check, answer = by_case[c].pop(0)
                picked.append((c, answer, (check,)))
    return picked


def _ask(items: list[tuple[str, str, tuple[str, ...]]]) -> None:
    """Ask your call on each (case, answer, checks), skipping what you've labelled, and keep it in LABELS."""
    cases = {c["id"]: c for c in load_cases()}
    done = {(d["case"], d["check"], d["answer"]) for d in read_labels()}
    print("y = right, n = wrong, s = skip, q = quit")
    for case_id, answer, checks in items:
        if case_id not in cases:
            continue
        case = cases[case_id]
        print(f"\n{'=' * 100}\n[{case_id}] {asked(case)}\n\n{answer}\n")
        for check in checks:
            if (case_id, check, answer) in done:
                continue
            ask = (f"Does it get this across? {' '.join(case['expect'].split())}" if check == "expect"
                   else "Does it state none of these?\n" + "\n".join(f"- {w}" for w in case["wrong"]))
            key = input(f"{ask}\n> ").strip().lower()
            if key == "q":
                return
            if key in ("y", "n"):
                with LABELS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"case": case_id, "check": check, "question": asked(case),
                                        "expect": case["expect"], "wrong": case["wrong"], "answer": answer,
                                        "right": key == "y", "by": "owner", "split": split(case_id, check, answer)},
                                       ensure_ascii=False) + "\n")


def label_record(case: dict, check: str, answer: str, right: bool, by: str, **more) -> dict:
    """A label as LABELS keeps it."""
    return {"case": case["id"], "check": check, "question": asked(case), "expect": case["expect"],
            "wrong": case["wrong"], "answer": answer, "right": right, "by": by,
            "split": split(case["id"], check, answer), **more}


def split(case_id: str, check: str, answer: str) -> str:
    """A new label's half, "held out" or "tuning", from a hash: look only at the tuning half's misses when changing
    the judge's prompts, and count the held-out half's after, so a prompt fitted to the labels doesn't look better
    than it is. Labels from before 16 October have none: they were all read when the prompts were tuned."""
    return "held out" if hashlib.sha256(f"{case_id}|{check}|{answer}".encode()).digest()[0] < 128 else "tuning"


def held_out(d: dict) -> bool:
    return d.get("split") == "held out"


def read_labels() -> list[dict]:
    lines = LABELS.read_text(encoding="utf-8").splitlines() if LABELS.exists() else []
    return [json.loads(line) for line in lines if line.strip()]


async def agreement(judge_model: str) -> None:
    """Judge every labelled answer again, with today's prompts, and count how often the judge agrees with you.

    A label is by the owner (`--label`) or by a council (`"by": "council"`) standing in where the owner gave no call:
    five blind labellers (three Claude, GPT and Gemini; their votes in `"council"`), the majority kept. On the owner's
    29 calls the council's majority agreed on 27 (15 October). The owner's count comes first and is the measure."""
    labels = read_labels()
    if not labels:
        raise SystemExit("no labels yet: label a run first (--label)")
    verdicts = await asyncio.gather(*(
        judge_majority(expect_prompt(d["question"], d["expect"], d["answer"]) if d["check"] == "expect"
              else wrong_prompt(d["question"], d["wrong"], d["answer"]), judge_model) for d in labels))
    for by in ("owner", "council", "claude"):
        mine = [(d, v) for d, v in zip(labels, verdicts, strict=True) if d.get("by", "owner") == by]
        if not mine:
            continue
        print(f"\n{by}'s labels: the judge agrees on {sum(v.ok is d['right'] for d, v in mine)} of {len(mine)}")
        for half, pairs in (("all", mine), ("held out", [(d, v) for d, v in mine if held_out(d)])):
            bad = [v for d, v in pairs if not d["right"]]
            good = [v for d, v in pairs if d["right"]]
            print(f"  {half}: of {len(bad)} wrong answers the judge failed {sum(v.ok is False for v in bad)}; "
                  f"of {len(good)} right ones it passed {sum(v.ok is True for v in good)}")
        print("  where it differs, from the half not held out (the held-out half's misses stay unread):")
        for d, v in mine:
            if v.ok is not d["right"] and not held_out(d):
                print(f"    {d['case']} {d['check']} ({by}: {'pass' if d['right'] else 'fail'}): {v.reason[:200]}")


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run wipebot's eval cases.")
    parser.add_argument("-k", dest="filter", default="",
                        help="only cases whose id contains this (several, comma-separated: any of them)")
    parser.add_argument("--trials", type=lambda v: v if v == "auto" else int(v), default=1,
                        help=f"answers per case (default 1), or auto: {STEADY_TRIALS} where the baseline judged a "
                             f"case the same on every trial, {UNSETTLED_TRIALS} elsewhere, and {UNSETTLED_TRIALS} "
                             "everywhere once the total looks low")
    parser.add_argument("--no-confirm", action="store_true",
                        help=f"don't ask the cases that moved beyond noise again (up to {CONFIRM_TRIALS} answers each)")
    parser.add_argument("--model", default="", help="model for the answers (default: the bot's)")
    parser.add_argument("--effort", default="", choices=["", "low", "medium", "high", "xhigh", "max"],
                        help="the answers' effort (default: the bot's, WIPEBOT_EFFORT); the judge's stays")
    parser.add_argument("--judge-model", default="sonnet", help="model for the judge (default sonnet)")
    modes = parser.add_mutually_exclusive_group()  # default: as Claude with the plugin (the connector and the skill)
    modes.add_argument("--connector-only", dest="mode", action="store_const", const="connector", default="plugin",
                       help="answer with only the connector: no skill, no instructions (ChatGPT, Claude without it)")
    modes.add_argument("--bot", dest="mode", action="store_const", const="bot",
                       help="answer as the Discord bot: its prompt with the skill")
    parser.add_argument("--save-baseline", nargs="?", const="", metavar="RESULTS",
                        help="keep this run as its mode's baseline, or a saved run (no model); refused when it "
                             "errored, was filtered or is below the baseline it replaces, unless --accept")
    parser.add_argument("--accept", default="", metavar="REASON",
                        help="with --save-baseline: why a run it would refuse is the baseline all the same")
    parser.add_argument("--record", metavar="RESULTS",
                        help="keep a saved full run as its mode's last in evals/full-runs.json (no model)")
    parser.add_argument("--summary", metavar="RESULTS",
                        help="a saved run's summary and comparison again, by today's rules (no model)")
    parser.add_argument("--held-out", action="store_true",
                        help="only the held-out cases (cases/heldout.toml), at a milestone: never while tuning")
    parser.add_argument("--label", metavar="RESULTS", help="mark a run's answers yourself")
    parser.add_argument("--label-failed", type=int, metavar="N", help="mark N answers the judge failed yourself")
    parser.add_argument("--agreement", action="store_true", help="compare the judge with your marks")
    parser.add_argument("--lint", action="store_true",
                        help="check the cases' numbers against the tools' output now (no model)")
    parser.add_argument("--endings", action="store_true",
                        help="the engine's reading of how each case's pull ended against the experts' (no model)")
    parser.add_argument("--coverage", action="store_true",
                        help="what the cases cover: tools, kinds of question, places, bosses (no model)")
    parser.add_argument("--recheck", metavar="RESULTS",
                        help="check a saved run's answers again: claims their tools don't back, and the rubric")
    parser.add_argument("--form", metavar="RESULTS",
                        help="how a saved run's answers are built, against the bot's prompt (no model)")
    parser.add_argument("--api-key", action="store_true",
                        help="bill the answers and the judge to ANTHROPIC_API_KEY (default: your Claude Code login)")
    return parser


def main() -> None:
    parser = arguments()
    args = parser.parse_args()
    if args.trials == "auto" and args.save_baseline == "":
        parser.error("--trials auto with --save-baseline: a baseline asks every case as often, to judge the next run")
    if args.held_out and args.save_baseline is not None:
        parser.error("--held-out with --save-baseline: a baseline is the regular cases'")
    if args.summary:
        sys.exit(show_run(Path(args.summary)))
    if args.record:
        return record_saved(Path(args.record))
    if args.save_baseline:  # a saved run's file: no model
        run, _, tally = read_run(Path(args.save_baseline))
        return save_baseline(tally, run["mode"], args.accept, run.get("filter", ""), Path(args.save_baseline).name)
    args.billed_to = billed_to(args.api_key)
    if args.label:
        label(Path(args.label))
    elif args.label_failed:
        label_failed(args.label_failed)
    elif args.agreement:
        asyncio.run(agreement(args.judge_model))
    elif args.lint:
        sys.exit(asyncio.run(_lint_then_drain(args.filter)))
    elif args.recheck:
        asyncio.run(recheck(Path(args.recheck), args.judge_model, args.filter))
    elif args.form:
        print("\n".join(form(Path(args.form), args.filter)))
    elif args.endings:
        sys.exit(asyncio.run(_endings_then_drain()))
    elif args.coverage:
        from evals.coverage import report
        print("\n".join(report(load_cases(), HERE / "results")))
    else:
        sys.exit(asyncio.run(_run_then_drain(args)))


def frozen() -> None:
    """CACHE as the tools' cache, frozen: what's missing is fetched once and kept, nothing is refreshed."""
    if not CACHE.exists():
        print(f"No data at {CACHE} yet: this run fetches what the cases read and keeps it there (Warcraft Logs keys "
              "needed). A first full run spends more than an hour's Warcraft Logs points: it waits for the next "
              "hour once half of one is spent (the rest is the bot's and the server's), and what was fetched is "
              "kept.", flush=True)
    os.environ["WIPEBOT_CACHE"], os.environ["WIPEBOT_CACHE_FROZEN"] = str(CACHE), "1"  # read at first use
    wcl.keep_for_others(KEEP_FOR_OTHERS)
    time_waits()
    # No saved raid notes or plans: the cases paste theirs, and this machine's own (the connector reads them) stay out.
    os.environ["WIPEBOT_NOTES_DB"] = str(Path(tempfile.mkdtemp(prefix="wipebot-evals-")) / "notes.sqlite")
    cache().recorded_at(first=RECORDED_AT)  # kept in the file: "now" for the tools


async def _lint_then_drain(only: str, results: Path | None = None) -> int:
    """The lint's findings printed; 0 only when it checked cases and found nothing: a stale number, a replayed call
    that fails, a case it couldn't check for lack of data and a run with nothing to replay are all 1."""
    from evals.lint import lint
    results = results or HERE / "results"
    if not any(results.glob("*.json")):  # before frozen(), which would make an empty cache
        print(f"checked nothing: no run in {results} to replay the calls of (`just evals` makes one)")
        return 1
    frozen()
    as_connector()  # the calls of the connector's own cases replay too
    try:
        cases = [c for c in load_cases() if picked(c, only)]
        found = await lint(cases, results)
    finally:
        await cache().drain()
    print(lint_line(found, len(cases)))
    return 1 if found.flagged or found.failed or found.no_data or not found.checked else 0


def lint_line(found, cases: int) -> str:
    """The lint's last line: what it checked, and each thing that keeps it from passing."""
    said = [f"{found.checked} of {cases} cases checked: " + (
        f"{found.flagged} numbers no tool prints now" if found.flagged else
        "every number is in its tools' output" if found.checked else "no run has calls of theirs to replay")]
    if found.failed:
        said.append(f"{found.failed} replayed calls fail now")
    if found.no_data:
        said.append(f"{found.no_data} cases NOT checked for lack of data: the frozen file doesn't hold what their "
                    "calls read today and Warcraft Logs didn't give it (its keys: .env.example), which says nothing "
                    "about their numbers")
    return "; ".join(said)


async def _endings_then_drain() -> int:
    """The endings printed; 1 below the agreed count or with a pull that couldn't be read."""
    from evals.endings import AGREED, endings
    frozen()
    try:
        cases = [c for c in load_cases() if c.get("cause")]
        agree, lines, no_data = await endings(cases)
    finally:
        await cache().drain()
    print("\n".join(lines) + f"\nThe engine leads with the expert's cause, and its \"Ended by\" line names it "
          f"first, on {agree} of {len(cases)} pulls (agreed: at least {AGREED})."
          + (f" {no_data} NOT read for lack of data: the frozen file doesn't hold what the engine reads today and "
             "Warcraft Logs didn't give it." if no_data else ""))
    return 1 if agree < AGREED or no_data else 0


async def _run_then_drain(args) -> int:
    try:
        return await run(args)
    finally:
        await cache().drain()  # the background guide refreshes: asyncio.run hangs on them at shutdown


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
