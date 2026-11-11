"""Answers written to be wrong, one known way each: does the judge fail them? (`just evals --planted`)

A case's `wrong` list is its defence against the tempting misreading of the log, and its `expect` against an answer
that says nothing. In the saved runs the `wrong` check hardly ever fails an answer (26 of 1,114 trials on 10
November): either the bot rarely makes those mistakes, or the judge, told to read each item "as narrowly as it's
written", lets them through. The judge's agreement with the owner's marks is measured on good answers, hardly on bad
ones, so nothing said which. A plant does: a short answer written by hand for one case, kept in evals/planted.toml,
that the judge must fail.

- `wrong`: a fair short answer to the case's question, but for one thing: it states one item of the case's `wrong`
  list plainly as true (`item`, the item's text as the case has it; the first of the list unless that one can't be
  stated). The judge's `wrong` check must fail it.
- `vague`: an answer on the case's subject with every specific taken out: no player, time, number or ability, only
  what anyone could say without reading the log. It need not be wrong, only empty. The judge's `expect` check must
  fail it.

The judge is asked as a run asks it: the case's question as run.asked builds it, the judge's own prompts
(checks.wrong_prompt, checks.expect_prompt) and the majority rule (checks.judge_majority), so the count of plants
caught is the real pipeline's. A plant the judge lets through names a wrong item to rewrite, a judge prompt to fix,
or a plant that is too soft: read it. No model writes a plant, and a held-out case has none.

A plant names what it was written for. One whose case is gone, or whose item its case no longer lists, is stale:
the lint says so and fails (`just evals --lint`, no model), and `--planted` asks nothing until it is rewritten.
"""
import asyncio
import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals import checks
from evals.lint import numbers

HERE = Path(__file__).resolve().parent
PLANTS = HERE / "planted.toml"
KINDS = ("wrong", "vague")
KEYS = {"wrong": {"case", "kind", "item", "answer"}, "vague": {"case", "kind", "answer"}}
CHECK = {"wrong": "wrong", "vague": "expect"}  # the judge's check a plant of each kind must fail
JOBS = 8  # plants judged at a time
# What one judge call costs, to say what a run costs before it is spent: Sonnet's mean over the 5,172 checks of the
# saved runs of 27 October to 10 November that it passed at the first vote was 0.85 cents, on whole answers; a plant
# is shorter. (docs/models.md's 6 cents an answer, of 21 October, was three checks and the votes after a fail.)
CALL_USD = 0.01
CAUGHT, LET_THROUGH, NOT_JUDGED = "caught", "let through", "not judged"


@dataclass
class Plant:
    case: str  # the case it was written for, by id
    kind: str  # KINDS
    answer: str
    item: str = ""  # a `wrong` plant: the item of the case's wrong list it states, as the case has it


def plain(text: str) -> str:
    return " ".join(text.split())


def some(n: int) -> str:
    return f"{n} plant{'' if n == 1 else 's'}"


def load_plants(path: Path = PLANTS) -> list[Plant]:
    """The file's plants, in its order. A malformed file is refused: a key it doesn't take or lacks, an unknown
    kind, an empty answer or item, a vague plant with a number in it (a specific), or the same plant twice (a case's
    vague one, or its wrong one for the same item)."""
    spec = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(spec) != {"plant"} or not isinstance(spec["plant"], list):
        raise SystemExit(f"{path.name}: it holds [[plant]] tables and nothing else")
    plants, seen = [], set()
    for n, entry in enumerate(spec["plant"], 1):
        where = f"{path.name}: plant {n}" + (f" ({entry['case']})" if isinstance(entry.get("case"), str) else "")
        kind = entry.get("kind")
        if kind not in KINDS:
            raise SystemExit(f"{where}: kind is {' or '.join(KINDS)}")
        if set(entry) != KEYS[kind]:
            raise SystemExit(f"{where}: a {kind} plant has the keys {', '.join(sorted(KEYS[kind]))}")
        if not all(isinstance(v, str) and v.strip() for v in entry.values()):
            raise SystemExit(f"{where}: every key is a text that says something")
        plant = Plant(entry["case"], kind, entry["answer"].strip(), plain(entry.get("item", "")))
        if kind == "vague" and (stated := numbers(plant.answer)):
            raise SystemExit(f"{where}: a vague plant states no number ({', '.join(stated)})")
        if (key := (plant.case, kind, plant.item)) in seen:
            raise SystemExit(f"{where}: the case has this plant twice")
        seen.add(key)
        plants.append(plant)
    return plants


def stale(plants: list[Plant], cases: list[dict]) -> list[str]:
    """A line for each plant that no longer fits a case, with what to do about it: its case is gone or held out, or
    its case no longer lists the item it states (a changed word makes it another item: the plant was written to state
    the old one)."""
    known = {c["id"]: c for c in cases}
    lines = []
    for p in plants:
        case = known.get(p.case)
        if case is None:
            lines.append(f"{p.case}: its {p.kind} plant is stale, the case is gone: drop the plant, or write it for "
                         "the case that took its place")
        elif case["kind"] == "heldout":
            lines.append(f"{p.case}: a held-out case gets no plant (what it expects stands in heldout.toml alone): "
                         "drop the plant")
        elif p.kind == "wrong" and p.item not in {plain(w) for w in case["wrong"]}:
            lines.append(f"{p.case}: its wrong plant is stale, the case no longer lists \"{p.item}\": rewrite the "
                         "plant to state an item of the case's wrong list as it is now, and name that item")
    return lines


def unplanted(plants: list[Plant], cases: list[dict]) -> list[str]:
    """The regular cases (all but the held-out ones) with no `wrong` plant, by id."""
    have = {p.case for p in plants if p.kind == "wrong"}
    return [c["id"] for c in cases if c["kind"] != "heldout" and c["id"] not in have]


def lint_line(plants: list[Plant], old: list[str]) -> str:
    """The plants' part of the lint's last line."""
    return f"{some(len(plants))}, " + (f"{len(old)} stale: rewrite them (evals/planted.toml)" if old else "none stale")


def judge_prompt(plant: Plant, case: dict) -> tuple[str, str]:
    """(the question as the judge sees it, the judge's prompt for the plant): as a run asks about an answer, with
    the case's own question text and the prompt of the check the plant must fail. A case only a client asks is asked
    as in a client, every other as the bot is asked it, with its place and whether the raid was on."""
    from evals.run import asked
    question = asked(case, bool(case.get("client_only")))
    return question, (checks.wrong_prompt(question, case["wrong"], plant.answer) if plant.kind == "wrong"
                      else checks.expect_prompt(question, case["expect"], plant.answer))


def estimate(plants: list[Plant], judge_model: str) -> str:
    """What a run will ask and about what it costs, said before it asks: the judge is asked once per plant, again
    when it fails it and a third time when those two votes differ (checks.judge_majority)."""
    n, kinds = len(plants), Counter(p.kind for p in plants)
    return (f"{some(n)} ({', '.join(f'{kinds[k]} {k}' for k in KINDS if kinds[k])}), judged by {judge_model}: "
            f"{n} to {3 * n} judge calls, {2 * n} if it fails every plant twice running. About "
            f"${2 * n * CALL_USD:.2f} at {CALL_USD * 100:.0f} cent a call, at most ${3 * n * CALL_USD:.2f}.")


def counted(found: list[dict]) -> dict[str, dict[str, int]]:
    """Per kind of plant, how many the judge caught, let through and couldn't judge."""
    return {kind: {outcome.replace(" ", "_"): sum(f["verdict"] == outcome for f in found if f["kind"] == kind)
                   for outcome in (CAUGHT, LET_THROUGH, NOT_JUDGED)}
            for kind in KINDS if any(f["kind"] == kind for f in found)}


def report(found: list[dict]) -> list[str]:
    """What a run prints once it has asked: per kind, the plants the judge failed of those it judged; then each
    plant it let through with its reason, and each it couldn't judge. A plant not judged is in no count."""
    lines = []
    for kind, n in counted(found).items():
        lines.append(f"{kind}: the judge failed {n['caught']} of {some(n['caught'] + n['let_through'])}"
                     + (f" ({n['not_judged']} more could not be judged)" if n["not_judged"] else ""))
    for outcome, head in ((LET_THROUGH, "let through: the judge passed an answer written to fail. Read each one: the "
                           "case's wrong item (or its expect) doesn't hold an answer to it as the judge reads it, or "
                           "the plant is too soft"),
                          (NOT_JUDGED, "could not be judged (in no count)")):
        mine = [f for f in found if f["verdict"] == outcome]
        if mine:
            lines.append(f"\n{len(mine)} {head}:")
        for f in mine:
            lines += [f"  {f['case']}, {f['kind']}" + (f": \"{f['item']}\"" if f["item"] else ""),
                      f"    the plant: {plain(f['plant'])}", f"    the judge: {f['reason']}"]
    return lines


def keep(folder: Path, found: list[dict], **about) -> Path:
    """A run's file, planted-<stamp>.json beside the runs' (the Evals workflow sends every file of the folder to the
    eval-results release): each plant with its case, kind and item, the question the judge saw, its verdict, votes,
    reason and cost. It has no `mode` and no `cases`, so the lint and the coverage report read past it."""
    folder.mkdir(exist_ok=True)
    out = folder / f"planted-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"planted": counted(found), **about, "date": f"{datetime.now(UTC):%Y-%m-%d}",
                               "votes": sum(len(f["votes"]) for f in found),
                               "cost_usd": round(sum(f["cost_usd"] for f in found), 4), "plants": found},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    return out


async def planted(judge_model: str, only: str = "", path: Path = PLANTS, results: Path | None = None,
                  billed_to: str = "") -> int:
    """Ask the judge about the plants `only` picks (by their case, as -k picks cases), print how many it failed and
    each it let through, and save the lot. 1 when a plant could not be judged (the judge gave no verdict, or failed),
    when a picked plant is stale (then nothing is asked) or when none is picked; a plant let through is a finding to
    read, not a failure of the run, so it leaves the exit at 0."""
    from evals.run import case_hash, commit, judged_by, load_cases, picked
    cases = {c["id"]: c for c in load_cases()}
    plants = [p for p in load_plants(path) if picked({"id": p.case}, only)]
    if old := stale(plants, list(cases.values())):
        print("\n".join(old))
        print(f"Exits non-zero: stale plants ({len(old)} of the {some(len(plants))} picked), so none was asked. "
              f"Rewrite them first ({path.name}).")
        return 1
    if not plants:
        print(f"Exits non-zero: no plant is picked by -k {only} ({path.name}).")
        return 1
    print(estimate(plants, judge_model) + (f" Billed to {billed_to}." if billed_to else ""), flush=True)
    jobs = asyncio.Semaphore(JOBS)
    found: list[dict | None] = [None] * len(plants)  # in the file's order, whatever order the judge answers in

    async def one(i: int, plant: Plant) -> None:
        case = cases[plant.case]
        question, prompt = judge_prompt(plant, case)
        async with jobs:
            try:
                v = await checks.judge_majority(prompt, judge_model)
            except Exception as e:  # noqa: BLE001  a judge that fails leaves its plant unjudged, and the rest go on
                v = checks.Verdict(None, f"the judge failed: {type(e).__name__}: {e}")
        verdict = CAUGHT if v.ok is False else LET_THROUGH if v.ok is True else NOT_JUDGED
        found[i] = {"case": plant.case, "kind": plant.kind, "item": plant.item, "plant": plant.answer,
                    "check": CHECK[plant.kind], "question": question, "case_hash": case_hash(case),
                    "verdict": verdict, "votes": v.votes, "reason": v.reason, "cost_usd": v.cost_usd}
        print(f"{plant.case:38} {plant.kind:6} {verdict}", flush=True)

    try:
        await asyncio.gather(*(one(i, p) for i, p in enumerate(plants)))
    finally:  # what was judged is kept before anything is made of it, as a run's answers are
        judged = [f for f in found if f]
        if judged:
            out = keep(results or HERE / "results", judged, judge=judged_by(judge_model),
                       judge_models=sorted(checks.MODELS), commit=commit(), filter=only, billed_to=billed_to)
            print(f"\nsaved {out}", flush=True)
    print("\n".join(report(judged)) + f"\n(judge {judge_model}, {sum(len(f['votes']) for f in judged)} votes, "
          f"${sum(f['cost_usd'] for f in judged):.2f})")
    if unjudged := sum(f["verdict"] == NOT_JUDGED for f in judged):
        print(f"Exits non-zero: {some(unjudged)} could not be judged.")
    return 1 if unjudged else 0
