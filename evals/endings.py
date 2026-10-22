"""The engine's reading of how a pull ended against the experts' (`just evals --endings`, no model): a case's
`cause` is what an experienced raid leader names first for its pull, in report/ending.py's words (`lead`); the
engine agrees when its "Ended by" line leads with the same. Where it doesn't, the model has to find the cause itself
from the rest of the output: a list of what the tools should work out next."""
from wipebot.core import server
from wipebot.core.report.ending import ending, lead
from wipebot.core.text import parse_clock

SAME_DEATHS_MS = 2000  # "deaths from" two times this close apart name the same first death (one burst)


def agrees(engine: str, expert: str) -> bool:
    """Whether the engine leads with the expert's cause: the same words, or deaths from times within SAME_DEATHS_MS."""
    if engine == expert:
        return True
    prefix = "deaths from "
    if engine.startswith(prefix) and expert.startswith(prefix):
        a, b = parse_clock(engine.removeprefix(prefix)), parse_clock(expert.removeprefix(prefix))
        return a is not None and b is not None and abs(a - b) <= SAME_DEATHS_MS
    return False


async def endings(cases: list[dict]) -> tuple[int, list[str]]:
    """(how many of the cases with a `cause` the engine leads with, a line per case)."""
    library, agree, lines = server.configure(), 0, []
    for c in [c for c in cases if c.get("cause")]:
        rep = await library.report(c["report"], max_age_s=float("inf"))
        f = rep.fight(c.get("pull"), c.get("boss"))
        found = lead(ending(rep, f, await rep.deaths(f), (await rep.resurrects([f]))[f["id"]],
                            await rep.raid_size(f), await rep.enrages(f), await rep.decided_at(f)))
        same = agrees(found, c["cause"])
        agree += same
        lines.append(f"  {'agrees' if same else 'DIFFERS':8} {c['id']} ({rep.label(f)}): expert \"{c['cause']}\""
                     + ("" if found == c["cause"] else f", engine \"{found}\""))
    return agree, lines
