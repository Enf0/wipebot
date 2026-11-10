"""The engine's reading of how a pull ended against the experts' (`just evals --endings`, no model): a case's
`cause` is what an experienced raid leader names first for its pull, in report/ending.py's words (`lead`). The engine
agrees when it leads with the same and the "Ended by" line the model and the raider read (`ending_text`) names it
first. Where it doesn't, the model has to find the cause itself from the rest of the output: a list of what the tools
should work out next. Fewer agreeing pulls than AGREED fails, and so does a pull this machine has no data for."""
import re

from wipebot.core import server, wcl
from wipebot.core.report.ending import ending_text, lead, pull_ending
from wipebot.core.text import parse_clock

SAME_DEATHS_MS = 2000  # "deaths from" two times this close apart name the same first death (one burst)
# Of the cases with a `cause`, how many the engine led with the expert's on 10 November (12 of 15): a change to how
# pulls end that takes it below fails `--endings`. Raise it when the engine gets one more right.
AGREED = 12
# How the "Ended by" line says each kind of cause (ending_text, moment_text): the words before or around the name
SAID = {"reset": "a reset", "alive": "Ended by: no death", "live": "not yet, the pull is still going",
        "half the raid dead": "half the raid was dead", "moment: no killing blow": "no killing blow logged"}
DEATHS_FROM = re.compile(r"deaths (?:one after another )?from (\d+:\d\d)")


def agrees(engine: str, expert: str) -> bool:
    """Whether the engine leads with the expert's cause: the same words, or deaths from times within SAME_DEATHS_MS."""
    if engine == expert:
        return True
    prefix = "deaths from "
    if engine.startswith(prefix) and expert.startswith(prefix):
        a, b = parse_clock(engine.removeprefix(prefix)), parse_clock(expert.removeprefix(prefix))
        return a is not None and b is not None and abs(a - b) <= SAME_DEATHS_MS
    return False


def names_first(text: str | None, cause: str) -> bool:
    """Whether the printed "Ended by" line names the cause before anything else: in what comes before its first
    "; then" and before what it says came "Before it"."""
    if text is None:
        return cause == "kill"
    first = text.split("; then ")[0].split(" Before it, ")[0]
    if cause.startswith("deaths from "):
        at, said = parse_clock(cause.removeprefix("deaths from ")), DEATHS_FROM.search(first)
        when = parse_clock(said[1]) if said else None
        return at is not None and when is not None and abs(at - when) <= SAME_DEATHS_MS
    if cause in SAID:
        return SAID[cause] in first
    kind, _, name = cause.partition(": ")  # "enrage: <buff>", "ability: <ability>", "cast: <the boss's cast>"
    return bool(name) and {"enrage": f"gained {name}", "cast": f"cast {name}"}.get(kind, name) in first


async def endings(cases: list[dict]) -> tuple[int, list[str], int]:
    """(how many of the cases with a `cause` the engine leads with and prints first, a line per case, how many
    pulls couldn't be read for lack of data)."""
    library, agree, lines, no_data = server.configure(), 0, [], 0
    for c in [c for c in cases if c.get("cause")]:
        try:
            rep = await library.report(c["report"], max_age_s=float("inf"))
            f = rep.fight(c.get("pull"), c.get("boss"))
            e = await pull_ending(rep, f)
        except wcl.WCLError as error:  # the frozen file lacks what today's code reads, and Warcraft Logs didn't give it
            no_data += 1
            lines.append(f"  NO DATA  {c['id']}: {str(error)[:120]}")
            continue
        found, text = lead(e), ending_text(e)
        same, said = agrees(found, c["cause"]), names_first(text, c["cause"])
        agree += same and said
        lines.append(f"  {'agrees' if same and said else 'UNSAID' if same else 'DIFFERS':8} {c['id']} "
                     f"({rep.label(f)}): expert \"{c['cause']}\""
                     + ("" if found == c["cause"] else f", engine \"{found}\"")
                     + (f"; the engine leads with it, but its line doesn't name it first: {(text or 'a kill')[:140]}"
                        if same and not said else ""))
    return agree, lines, no_data
