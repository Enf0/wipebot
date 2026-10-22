"""Ask the eval cases through wipebot's agent session and check the answers.

    just evals                        # every case once, as Claude with the plugin: the connector and the skill
    just evals -k wipe --trials 3     # some cases, three times each
    just evals --model opus           # another model; the cases that differ from the baseline are listed
    just evals --save-baseline        # keep this run as its mode's baseline, to compare later runs with
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

Warcraft Logs data and the guides come from evals/cache.sqlite (untracked), frozen: what a run asks that isn't there
is fetched once and kept, never refreshed, so every run on a machine reads the same data, as of the time it was
first fetched (wipebot.core.clock). A fresh clone fetches it on its first run, which needs Warcraft Logs keys and
takes a while; its rankings and percentiles are that day's, so a few cases' numbers can differ from the owner's.
Runs are saved, with every answer and tool call, to evals/results/.

A run, and a baseline, keep a hash of the judge (its prompts and model) and of each case. A run is compared with the
baseline only on the cases unchanged since, with a warning when the judge changed; a case is listed as moved only
when its pass rate moved beyond noise (beyond_noise), and the total with its expected spread from run to run.
"""
import argparse
import asyncio
import functools
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.checks import (
    EXPECT,
    FACTS,
    FAIL_VOTES,
    GROUNDED_MODEL,
    RUBRIC,
    WRONG,
    expect_prompt,
    facts,
    grounded,
    judge_majority,
    rubric,
    wrong_prompt,
)
from wipebot.core import server
from wipebot.core.cache import cache
from wipebot.discord import config
from wipebot.discord.checks import warnings
from wipebot.discord.host import Context, Session
from wipebot.mcp.plugin import plugin_files

HERE = Path(__file__).resolve().parent
CACHE, LABELS = HERE / "cache.sqlite", HERE / "labels.jsonl"
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
CONTEXT = ["asker", "discord_name", "pull", "boss", "where"]  # case keys that go to the question's Context
JOBS = 4  # answers at a time
# With only the connector, as claude.ai has it today: no skill (that comes with a plugin, or MCP-served skills) and no
# server instructions (claude.ai drops them, anthropics/claude-ai-mcp#93), so only the tools guide the answer. The
# app's own system prompt isn't public; a plain one stands in.
CONNECTOR_ONLY = ("You are Claude, an AI assistant made by Anthropic, in a chat on claude.ai. The user has connected "
                  "the Wipebot connector, World of Warcraft raid analysis from Warcraft Logs; use its tools. Each "
                  "message starts with what the app knows: the report and who is asking.")
# A tool failure that isn't the model's doing (a bad argument is): a crash, or a report Warcraft Logs won't show.
BROKEN_TOOL = ("failed with an unexpected error", "won't show report")
# A service that didn't answer (warcraft_cli's network failure: Warcraft Logs down, a 503), for a call the frozen
# cache doesn't hold: it breaks a failed trial, while an answer that got by without the call still passes.
DOWN = "upstream_error"


def broken(t: Trial) -> str:
    """Why a trial says nothing about the answer, or ''."""
    if t.error:
        return t.error
    if failed := next((c for c in t.calls if c.get("failed") and any(
            b in str(c.get("result")) for b in BROKEN_TOOL + (() if t.passed else (DOWN,)))), None):
        return f"{failed.get('name')}: {str(failed.get('result'))[:200]}"
    for name, v in (("expect", t.expect), ("wrong", t.wrong)):
        if v and v.get("ok") is None:
            return f"the judge gave no verdict on {name}: {v.get('reason', '')[:200]}"
    return ""


@dataclass
class Trial:
    answer: str = ""
    error: str = ""
    seconds: float = 0.0
    cost_usd: float = 0.0  # the answer's and the judge's
    passed: bool = False
    errored: bool = False  # not the answer's fault: the session or a tool broke, or the judge gave no verdict
    expect: dict = field(default_factory=dict)  # Verdict
    wrong: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)  # Facts: how many supporting facts it states (never fails it)
    warnings: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # {name, args, result, failed}
    loaded: list[str] = field(default_factory=list)  # with the plugin: the skills loaded and files read (Answer.loaded)


# One file per kind of question; bot.toml's cases are the bot's own rules, run only with --bot, and connector.toml's
# the connector's own (an officer saving a note), run only without.
CASES = HERE / "cases"


def load_cases(folder: Path = CASES) -> list[dict]:
    """Every case in the folder's files (each with its `[defaults]`); a case in bot.toml is `bot_only`, one in
    connector.toml `client_only`."""
    cases = []
    for path in sorted(folder.glob("*.toml")):
        spec = tomllib.loads(path.read_text(encoding="utf-8"))
        for case in spec["case"]:
            if unknown := set(case) - {"id", "question", "expect", "facts", "wrong", "wrong_in_channel", "derived",
                                       "drifts", "hard", "report", "tools", "cause", *CONTEXT}:
                raise SystemExit(f"case {case.get('id')} ({path.name}): unknown keys {', '.join(sorted(unknown))}")
            only = {"bot": {"bot_only": True}, "connector": {"client_only": True}}.get(path.stem, {})
            cases.append({"report": spec["defaults"]["report"], **case} | only | {"kind": path.stem})
    if len({c["id"] for c in cases}) != len(cases):
        raise SystemExit("two cases share an id")
    return cases


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


def as_officer() -> None:
    """The in-process tools as the connector has them for an officer: with save_note_or_plan (OFFICER)."""
    server.allow_saving(server.mcp, server.DEFAULT, lambda: OFFICER)


def where(case: dict, client: bool = False) -> str:
    """Where the question is asked: the case's place in Discord, or in a client (plugin, connector), the asker's chat
    (the bot's channels mean nothing there)."""
    return IN_CLAUDE if client else case.get("where", IN_CHANNEL)


def wrongs(case: dict, client: bool = False) -> list[str]:
    """What would make an answer wrong where it's asked: `wrong_in_channel` (naming who died or had a kick ready)
    only in the server channel, a rule of the bot's."""
    return case["wrong"] + ([] if client else case.get("wrong_in_channel", []))


def asked(case: dict, client: bool = False) -> str:
    """The question as the judge and the labeller see it: who asked it and where (the cases' rules on naming players
    turn on both), and a follow-up after the ones before it."""
    *before, last = questions(case)
    who = case.get("asker") or (f"{case['discord_name']} (their Discord name)" if case.get("discord_name") else "")
    head = "Asked" + (f" by {who}," if who else "") + f" {where(case, client)}: "
    return head + "".join(f"{q}\n(then, in the same conversation) " for q in before) + last


async def trial(case: dict, library: server.Library, model: str, judge_model: str, jobs: asyncio.Semaphore,
                mode: str = "plugin") -> Trial:
    """One answer, judged."""
    model = model or config.model()  # what the bot would use
    t = Trial()
    async with jobs:
        started = time.time()  # not counting the wait for a free slot
        try:
            given = {k: case[k] for k in CONTEXT if k in case and k != "where"}
            client = mode != "bot"
            # the bot's own place for a channel ("in #raid"), or in a client, the asker's chat
            ctx = Context(where=IN_CLAUDE if client else case.get("where", "in #raid"), **given)
            # None: the bot's own, with the skill
            prompt = {"plugin": PLUGIN, "connector": CONNECTOR_ONLY, "bot": None}[mode]
            calls, prompts = [], []
            async with Session(library, case["report"], model, system_prompt=prompt,
                               plugin=plugin_folder() if mode == "plugin" else None) as session:
                for turn, question in enumerate(questions(case)):  # follow-ups in one conversation; the last
                    answer = await session.ask(question, ctx)      # answer is judged
                    t.cost_usd += answer.cost_usd or 0.0
                    # images as a count; a follow-up's calls marked with the question they answered (0 the first)
                    calls += [{**c, "turn": turn} if len(questions(case)) > 1 else c for c in answer.records()]
                    prompts.append(answer.prompt)
                    t.loaded += answer.loaded
        except Exception as e:
            t.error, t.seconds, t.errored = f"{type(e).__name__}: {e}", time.time() - started, True
            return t
    t.seconds = round(time.time() - started, 1)
    t.answer, t.calls = answer.text, calls  # every call of the conversation: a follow-up's answer builds on them
    expect, wrong, stated = await asyncio.gather(
        judge_majority(expect_prompt(asked(case, client), case["expect"], t.answer), judge_model),
        judge_majority(wrong_prompt(asked(case, client), wrongs(case, client), t.answer), judge_model),
        facts(asked(case, client), case["facts"], t.answer, judge_model) if case.get("facts")
        else asyncio.sleep(0))
    t.passed = expect.ok is True and wrong.ok is True
    t.expect, t.wrong = asdict(expect), asdict(wrong)
    t.cost_usd += expect.cost_usd + wrong.cost_usd + (stated.cost_usd if stated else 0.0)
    if stated:
        t.facts = asdict(stated)
    t.warnings = warnings(t.answer, [c["result"] for c in t.calls] + prompts)
    if why := broken(t):
        t.errored, t.passed, t.error = True, False, why
    return t


def answers(cases: list[dict], results: dict[str, list[Trial]]) -> dict:
    """What the answers did besides passing, per case over its trials that didn't error: the supporting facts they
    stated (`facts`: [stated, of]), their tool calls (`calls`) and how many began with one of the case's `tools`, the
    tools its right answers call (`first_tagged`). They move when an answer gets fuller or finds its tools faster,
    which a pass rate near the top can't show."""
    tagged = {c["id"]: set(c.get("tools") or []) for c in cases}
    out: dict[str, dict] = {"facts": {}, "calls": {}, "first_tagged": {}}
    for c, ts in results.items():
        ok = [t for t in ts if not t.errored]
        if counted := [t.facts for t in ok if t.facts]:
            out["facts"][c] = [sum(f["stated"] for f in counted), sum(f["of"] for f in counted)]
        out["calls"][c] = sum(len(t.calls) for t in ok)
        if tagged[c]:
            out["first_tagged"][c] = sum(bool(t.calls) and t.calls[0]["name"] in tagged[c] for t in ok)
    return out


def how_answered(tally: dict, cases: Iterable[str]) -> str:
    """The answers' supporting facts stated, tool calls per answer, and how often the first call was one of the
    case's tools, over `cases` (those of the run with facts, or the ones compared with a baseline)."""
    cases = list(cases)
    answered = sum(judged(tally, c) for c in cases)
    facts = [tally.get("facts", {}).get(c) for c in cases]
    stated, of = sum(f[0] for f in facts if f), sum(f[1] for f in facts if f)
    tagged = [c for c in cases if c in tally.get("first_tagged", {})]
    first, of_first = sum(tally["first_tagged"][c] for c in tagged), sum(judged(tally, c) for c in tagged)
    parts = [f"supporting facts stated {stated}/{of} ({stated / of:.0%})" if of else "",
             f"{sum(tally.get('calls', {}).get(c, 0) for c in cases) / answered:.1f} tool calls an answer"
             if answered and tally.get("calls") else "",
             f"first call one of the case's tools in {first}/{of_first} ({first / of_first:.0%})" if of_first else ""]
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
    lines = [f"{case_id:30} {verdict:5} ({t.seconds:.0f} s, ${t.cost_usd:.2f})"]
    if t.error:
        lines.append(f"error: {t.error}")
    for name, v in (("expect", t.expect), ("wrong", t.wrong)):
        if v and v["ok"] is not True:
            lines.append(f"{name}: {v['reason'][:300]}")
    if t.facts and t.facts["missing"]:
        lines.append(f"facts {t.facts['stated']}/{t.facts['of']}, missing: " + "; ".join(t.facts["missing"])[:300])
    lines += [f"warning: {w}" for w in t.warnings]
    print("\n    ".join(lines), flush=True)


async def run(args) -> None:
    print(f"Answers and judging on {args.billed_to}.", flush=True)
    frozen()
    model = args.model  # empty: the bot's own, for chat and for live answers
    shown = model or config.model()
    cases = [c for c in load_cases() if picked(c, args.filter)
             and not (args.mode != "bot" and c.get("bot_only"))  # the bot's channel rules mean nothing in claude.ai
             and not (args.mode == "bot" and c.get("client_only"))  # nor the connector's saving in Discord
             and (c["kind"] == "heldout") == args.held_out]  # the held-out cases only when asked, and then alone
    if args.mode != "bot":
        as_officer()
    library, jobs = server.configure(), asyncio.Semaphore(JOBS)
    base = json.loads(baseline(args.mode).read_text()) if baseline(args.mode).exists() else None
    if base and (warning := judge_warning(base, {"judge": judged_by(args.judge_model)})):
        print(warning + "\n", flush=True)  # before the run too: stop it if a comparison is what you're after
    results: dict[str, list[Trial]] = {c["id"]: [] for c in cases}

    async def one(case: dict) -> None:
        t = await trial(case, library, model, args.judge_model, jobs, args.mode)
        results[case["id"]].append(t)
        show(case["id"], t)

    await asyncio.gather(*(one(c) for c in cases for _ in range(args.trials)))

    passed = {case_id: sum(t.passed for t in ts) for case_id, ts in results.items()}
    errors = {case_id: n for case_id, ts in results.items() if (n := sum(t.errored for t in ts))}
    tally = {"model": shown, "commit": commit(), "trials": args.trials, "judge": judged_by(args.judge_model),
             "case_hashes": {c["id"]: case_hash(c) for c in cases}, "passed": passed, "errors": errors,
             "hard": [c["id"] for c in cases if c.get("hard")]} | answers(cases, results)
    cost = sum(t.cost_usd for ts in results.values() for t in ts)
    mode = f", {args.mode}"
    print("\n" + "\n".join(summary(tally)) + f"\n({shown}{mode}, judge {args.judge_model}, ${cost:.2f})")
    print(how_answered(tally, tally["passed"]))
    if args.mode == "plugin":
        print(skill_use(results))
    if base:
        print("\n" + "\n".join(compare(base, tally)))


    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    out = HERE / "results" / f"{stamp}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": shown, "mode": args.mode, "connector_only": args.mode == "connector",
                               "judge_model": args.judge_model,
                               "billed_to": args.billed_to, "trials": args.trials, "commit": tally["commit"],
                               "judge": tally["judge"], "case_hashes": tally["case_hashes"],
                               "cases": {c: [asdict(t) for t in ts] for c, ts in results.items()}},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved {out.relative_to(HERE.parent)}")
    if args.save_baseline:
        baseline(args.mode).write_text(json.dumps(tally, indent=1) + "\n", encoding="utf-8")
        print(f"saved {baseline(args.mode).relative_to(HERE.parent)}")


# ---- comparing runs ----
# A run's tally and a baseline have the same keys: model, commit, trials, judge ({prompts: hash, model}),
# case_hashes ({case: hash}), passed and errors ({case: trials}). An errored trial counts in neither passes nor trials.


def short_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def judged_by(model: str) -> dict:
    """The judge, its prompts' hash and its model: a run judged by another isn't comparable answer for answer."""
    return {"prompts": short_hash([EXPECT, WRONG, FACTS, f"fails judged {FAIL_VOTES} times"]), "model": model}


def case_hash(case: dict) -> str:
    """What a case asks and checks: all but its id, the hard set, the lint's `drifts`, the coverage's `tools`, the
    engine's `cause` and its file's `kind` (question, expect, facts, wrong, its report and who asks), spaces aside, so
    a reflowed line keeps the hash and a changed word doesn't."""
    def plain(v):
        return " ".join(v.split()) if isinstance(v, str) else [plain(x) for x in v] if isinstance(v, list) else v
    left_out = ("id", "hard", "drifts", "tools", "cause", "kind")
    return short_hash({k: plain(v) for k, v in case.items() if k not in left_out})


def judged(tally: dict, case_id: str) -> int:
    return tally["trials"] - tally.get("errors", {}).get(case_id, 0)


def beyond_noise(passed: int, of: int, base_passed: int, base_of: int) -> bool:
    """Whether a case moved further than chance takes it: its pass rate changed by at least 1/√n, n the fewer
    trials of the two (2 of 3, 3 of 5, 4 of 10; about 1.4 standard deviations of the difference at a 50% rate)."""
    return abs(passed / of - base_passed / base_of) >= 1 / math.sqrt(min(of, base_of)) - 1e-9


def spread(rates: dict[str, float], trials: dict[str, int], base_trials: dict[str, int] | None = None) -> float:
    """One standard deviation of a total from run to run, each case's trials passing at its rate: the difference
    from a baseline (its noise scaled to these trials), or with none, between two runs like this one."""
    base_trials = base_trials or trials
    return math.sqrt(sum(p * (1 - p) * (trials[c] + trials[c] ** 2 / base_trials[c]) for c, p in rates.items()))


def summary(tally: dict) -> list[str]:
    """Each case's passes over its judged trials, flaky when it both passed and failed, then the total and its
    spread, and with several trials how many cases were right on every one: the regular cases, then the hard set
    (cases the bot still gets wrong, apart so the total shows a regression and the hard set an improvement)."""
    errors, hard = tally.get("errors", {}), set(tally.get("hard", []))
    lines, rates, trials = [], {}, {}
    for c, n in tally["passed"].items():
        of = judged(tally, c)
        lines.append(f"  {c:34} {n}/{of}" + ("  flaky" if 0 < n < of else "") + ("  (hard)" if c in hard else "")
                     + (f"  ({errors[c]} errored)" if errors.get(c) else ""))
        if of:
            rates[c], trials[c] = n / of, of
    for name, part in (("", [c for c in trials if c not in hard]), ("hard set: ", [c for c in trials if c in hard])):
        if not part:
            continue
        flaky = sum(0 < tally["passed"][c] < trials[c] for c in part)
        errored = sum(errors.get(c, 0) for c in tally["passed"] if (c in hard) == bool(name))
        lines.append(f"{name}{sum(tally['passed'][c] for c in part)}/{sum(trials[c] for c in part)} answers passed, "
                     f"±{spread({c: rates[c] for c in part}, {c: trials[c] for c in part}):.1f} from run to run; "
                     f"{flaky} flaky" + (f"; {errored} errored, not counted" if errored else ""))
        if any(trials[c] > 1 for c in part):  # what a raider meets: a case right only sometimes isn't right
            every = [c for c in part if tally["passed"][c] == trials[c]]
            lines.append(f"{name}{len(every)}/{len(part)} cases right on every trial")
            if name and every:
                lines.append("  right on every trial now, to move to the regular cases: " + ", ".join(every))
    return lines


def judge_warning(base: dict, now: dict) -> str:
    """Why differences from the baseline may be the judge's, or ''."""
    was, judge_now = base.get("judge"), now["judge"]
    if was is None or was == judge_now:
        return ""
    what = [f"{k} {was.get(k)} -> {judge_now[k]}" for k in ("prompts", "model") if was.get(k) != judge_now[k]]
    return f"WARNING: the judge changed since the baseline: differences may be the judge's ({', '.join(what)})"


def compare(base: dict, now: dict) -> list[str]:
    """The run against the baseline on the cases unchanged since it: the totals and their spread, the cases that
    moved beyond noise one per line, the others on one line."""
    lines = [judge_warning(base, now)] if judge_warning(base, now) else []
    lines.append(f"Against the baseline ({base['model']}, {base.get('commit', '?')}, {base['trials']} trials):")
    hashes = base.get("case_hashes")
    if hashes is None:
        lines.append("  note: the baseline is older than case and judge hashes: every case is compared, though a "
                     "case or the judge may have changed since")
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
    why = [f"{len(cs)} {reason} ({', '.join(cs)})" for reason, cs in skipped.items() if cs]
    lines.append(f"  compared {len(same)} of {len(now['passed'])} cases"
                 + (f"; skipped {'; '.join(why)}" if why else ""))
    if not same:
        return lines
    trials, base_trials = {c: judged(now, c) for c in same}, {c: judged(base, c) for c in same}
    total = sum(now["passed"][c] for c in same)
    expected = sum(base["passed"][c] / base_trials[c] * trials[c] for c in same)
    rates = {c: (now["passed"][c] + base["passed"][c]) / (trials[c] + base_trials[c]) for c in same}
    width = spread(rates, trials, base_trials)
    lines.append(f"  total {total}/{sum(trials.values())}, the baseline's {expected:.4g} at these trials: "
                 f"{total - expected:+.3g}, {'within' if abs(total - expected) <= width else 'beyond'} the "
                 f"±{width:.1f} expected from run to run")
    if "facts" in base and "facts" in now:  # baselines from before 22 October have none
        lines.append(f"  answers: {how_answered(now, same)}\n  the baseline's: {how_answered(base, same)}")
    moved, noise = [], []
    for c in same:
        n, of, b, b_of = now["passed"][c], trials[c], base["passed"][c], base_trials[c]
        if n * b_of != b * of:
            (moved if beyond_noise(n, of, b, b_of) else noise).append(f"{c} {n}/{of} (baseline {b}/{b_of})")
    fewest = min(min(trials.values()), min(base_trials.values()))
    lines.append(f"  moved beyond noise (pass rate by 1/√n or more, n the fewer trials: "
                 f"{math.ceil(math.sqrt(fewest) - 1e-9)} of {fewest}):" + "".join(f"\n    {m}" for m in moved)
                 + (" none" if not moved else ""))
    if noise:
        lines.append(f"  within noise: {', '.join(noise)}")
    return lines


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
                   else "Does it state none of these?\n" + "\n".join(f"- {w}" for w in wrongs(case)))
            key = input(f"{ask}\n> ").strip().lower()
            if key == "q":
                return
            if key in ("y", "n"):
                with LABELS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"case": case_id, "check": check, "question": asked(case),
                                        "expect": case["expect"], "wrong": wrongs(case), "answer": answer,
                                        "right": key == "y", "by": "owner", "split": split(case_id, check, answer)},
                                       ensure_ascii=False) + "\n")


def label_record(case: dict, check: str, answer: str, right: bool, by: str, **more) -> dict:
    """A label as LABELS keeps it."""
    return {"case": case["id"], "check": check, "question": asked(case), "expect": case["expect"],
            "wrong": wrongs(case), "answer": answer, "right": right, "by": by,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run wipebot's eval cases.")
    parser.add_argument("-k", dest="filter", default="",
                        help="only cases whose id contains this (several, comma-separated: any of them)")
    parser.add_argument("--trials", type=int, default=1, help="answers per case (default 1)")
    parser.add_argument("--model", default="", help="model for the answers (default: the bot's)")
    parser.add_argument("--judge-model", default="sonnet", help="model for the judge (default sonnet)")
    modes = parser.add_mutually_exclusive_group()  # default: as Claude with the plugin (the connector and the skill)
    modes.add_argument("--connector-only", dest="mode", action="store_const", const="connector", default="plugin",
                       help="answer with only the connector: no skill, no instructions (ChatGPT, Claude without it)")
    modes.add_argument("--bot", dest="mode", action="store_const", const="bot",
                       help="answer as the Discord bot: its prompt with the skill")
    parser.add_argument("--save-baseline", action="store_true", help="keep this run as its mode's baseline")
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
    parser.add_argument("--api-key", action="store_true",
                        help="bill the answers and the judge to ANTHROPIC_API_KEY (default: your Claude Code login)")
    args = parser.parse_args()
    if args.held_out and args.save_baseline:
        parser.error("--held-out with --save-baseline: a baseline is the regular cases'")
    args.billed_to = billed_to(args.api_key)
    if args.label:
        label(Path(args.label))
    elif args.label_failed:
        label_failed(args.label_failed)
    elif args.agreement:
        asyncio.run(agreement(args.judge_model))
    elif args.lint:
        asyncio.run(_lint_then_drain(args.filter))
    elif args.recheck:
        asyncio.run(recheck(Path(args.recheck), args.judge_model, args.filter))
    elif args.endings:
        asyncio.run(_endings_then_drain())
    elif args.coverage:
        from evals.coverage import report
        print("\n".join(report(load_cases(), HERE / "results")))
    else:
        asyncio.run(_run_then_drain(args))


def frozen() -> None:
    """CACHE as the tools' cache, frozen: what's missing is fetched once and kept, nothing is refreshed."""
    if not CACHE.exists():
        print(f"No data at {CACHE} yet: this run fetches what the cases read and keeps it there (Warcraft Logs keys "
              "needed). A first full run can pass Warcraft Logs' hourly limit (3600 points): answers then fail as "
              "errors; what was fetched is kept, so run again after the hour, or a few cases at a time (-k).",
              flush=True)
    os.environ["WIPEBOT_CACHE"], os.environ["WIPEBOT_CACHE_FROZEN"] = str(CACHE), "1"  # read at first use
    # No saved raid notes or plans: the cases paste theirs, and this machine's own (the connector reads them) stay out.
    os.environ["WIPEBOT_NOTES_DB"] = str(Path(tempfile.mkdtemp(prefix="wipebot-evals-")) / "notes.sqlite")
    cache().recorded_at(first=RECORDED_AT)  # kept in the file: "now" for the tools


async def _lint_then_drain(only: str) -> None:
    from evals.lint import lint
    if not any((HERE / "results").glob("*.json")):  # before frozen(), which would make an empty cache
        raise SystemExit(f"nothing to check: no run in {HERE / 'results'} yet (`just evals` makes one)")
    frozen()
    as_officer()  # the calls of the connector's own cases replay too
    try:
        cases = [c for c in load_cases() if picked(c, only)]
        flagged, checked = await lint(cases, HERE / "results")
    finally:
        await cache().drain()
    if not checked:
        raise SystemExit(f"none of the {len(cases)} cases checked: no run in {HERE / 'results'} has calls to replay "
                         "yet (`just evals` makes one)")
    print(f"{checked} of {len(cases)} cases checked: " + (
        f"{flagged} numbers no tool prints now" if flagged else "every number is in its tools' output"))
    if flagged:
        raise SystemExit(1)


async def _endings_then_drain() -> None:
    from evals.endings import endings
    frozen()
    try:
        cases = [c for c in load_cases() if c.get("cause")]
        agree, lines = await endings(cases)
    finally:
        await cache().drain()
    print("\n".join(lines) + f"\nThe engine leads with the expert's cause on {agree} of {len(cases)} pulls.")


async def _run_then_drain(args) -> None:
    try:
        await run(args)
    finally:
        await cache().drain()  # the background guide refreshes: asyncio.run hangs on them at shutdown


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
