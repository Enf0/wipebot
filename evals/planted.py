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
import tomllib
from dataclasses import dataclass
from pathlib import Path

from evals.lint import numbers

HERE = Path(__file__).resolve().parent
PLANTS = HERE / "planted.toml"
KINDS = ("wrong", "vague")
KEYS = {"wrong": {"case", "kind", "item", "answer"}, "vague": {"case", "kind", "answer"}}


@dataclass
class Plant:
    case: str  # the case it was written for, by id
    kind: str  # KINDS
    answer: str
    item: str = ""  # a `wrong` plant: the item of the case's wrong list it states, as the case has it


def plain(text: str) -> str:
    return " ".join(text.split())


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
    return f"{len(plants)} plants, " + (f"{len(old)} stale: rewrite them (evals/planted.toml)" if old else "none stale")
