"""Developer chat: ask wipebot questions in the terminal, the way raiders would in Discord.

    wipebot ask CODE [--as NAME] [--pull N] [--boss NAME] [--note FILE] [--plan FILE] [-q QUESTION ...]

With -q the questions are asked in order in one conversation (so later ones are follow-ups) and the
program exits. Otherwise it is interactive; type /help for the commands.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError
from wipebot.core.report import note as raid_notes
from wipebot.core.report import plan_export as raid_plans

from .config import model
from .host import Context, Session
from .prompt import asker_line
from .store import Raider

HELP = """\
Commands:
  /as NAME        ask as this character ("I", "my" mean them)
  /pull N [BOSS]  ask about that pull, as a reply to its answer would (starts a new conversation)
  /channel        back to the raid channel: questions default to the latest pull (new conversation)
  /new            start a new conversation in the same place
  /tools          show or hide the tool calls the AI makes
  /quit
Anything else is a question for wipebot."""


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if sys.stdout.isatty() else text


def _print_tool(name: str, args: dict) -> None:
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
    print("  " + _dim(f"· {name}({shown})"), flush=True)


async def chat(code: str, asker: str | None, pull: int | None, boss: str | None, questions: list[str],  # noqa: C901, PLR0915
               note: str | None = None, plan: str | None = None) -> None:
    library = server.configure(server.report_code(code))
    show_tools = True
    report = await library.report()
    ctx = Context(asker=asker, pull=pull, boss=boss)

    async def remember(args: dict) -> str:  # for this run only: the bot keeps it in its database
        if args.get("character"):
            try:
                args = {**args, "character": report.player(args["character"]).name}
            except FactError:
                pass
        raider = Raider(ctx.asker, list(ctx.notes))
        if problem := raider.apply(args):
            return problem
        ctx.asker, ctx.notes = raider.character, raider.notes
        print("  " + _dim(f"[remembered: {raider}]"))
        return f"Remembered: character {raider.character or 'unknown'}; notes: {raider.notes or 'none'}."
    ctx.tools["remember"] = remember
    book, at = Notebook(":memory:"), report.start_ms / 1000  # a file, as if saved in the server before the night
    if note:
        parsed = raid_notes.parse(Path(note).read_text(encoding="utf-8"))
        for n in parsed:
            book.save_note(0, n.encounter_id, n.boss, n.text, note, at)
        ctx.noted = [n.boss for n in parsed]
    if plan:
        given = raid_plans.parse(Path(plan).read_text(encoding="utf-8"))
        for p in given:
            book.save_plan(0, p.boss, p.plan_id, p.name, p.export, plan, at)
        ctx.planned = list(dict.fromkeys(p.boss for p in given))
    if note or plan:
        ctx.notebook = book
    print(f'{report.title} ({code}): ' + ", ".join(f"{b} x{len(f)}" for b, f in report.by_boss.items()))
    try:
        print(asker_line(ctx, report))
    except FactError as e:  # a --pull or --boss the report doesn't have
        raise SystemExit(f"error: {e}") from None

    def on_tool(name: str, args: dict) -> None:
        if show_tools:
            _print_tool(name, args)

    while True:  # one iteration per conversation
        async with Session(library, report.code, model(), on_tool) as session:
            if questions:
                for q in questions:
                    print(f"\n> {q}")
                    await _answer(session, q, ctx)
                return
            restart = False
            while not restart:
                try:
                    line = (await asyncio.to_thread(input, "\n> ")).strip()
                except EOFError:
                    return
                if not line:
                    continue
                if not line.startswith("/"):
                    await _answer(session, line, ctx)
                    continue
                cmd, *rest = line[1:].split()
                try:
                    if cmd in ("quit", "exit", "q"):
                        return
                    elif cmd == "help":
                        print(HELP)
                    elif cmd == "as" and rest:
                        ctx.asker = report.player(" ".join(rest)).name
                        print(asker_line(ctx, report))
                    elif cmd == "pull" and rest:
                        pull, boss = int(rest[0]), " ".join(rest[1:]) or None
                        report.fight(pull, boss)  # raises for a pull the report doesn't have
                        ctx.pull, ctx.boss = pull, boss
                        print(asker_line(ctx, report))
                        restart = True
                    elif cmd == "channel":
                        ctx.pull = ctx.boss = None
                        print(asker_line(ctx, report))
                        restart = True
                    elif cmd == "new":
                        restart = True
                    elif cmd == "tools":
                        show_tools = not show_tools
                        print("tool calls " + ("shown" if show_tools else "hidden"))
                    else:
                        print(HELP)
                except (FactError, ValueError) as e:
                    print(f"error: {e}")


async def _answer(session: Session, question: str, ctx: Context) -> None:
    try:
        a = await session.ask(question, ctx)
    except FactError as e:
        print(f"error: {e}")
        return
    print(f"\nwipebot: {a.text}")
    for image in a.images:  # what Discord would attach
        with tempfile.NamedTemporaryFile("wb", prefix="wipebot-picture-", suffix=".webp", delete=False) as fh:
            fh.write(image)
        print("  " + _dim(f"[image: {fh.name}]"))
    if a.cost_usd is not None:
        print("  " + _dim(f"({len(a.calls)} tool calls, ${a.cost_usd:.3f})"))


async def _chat_then_drain(*args) -> None:
    from wipebot.core.cache import cache
    try:
        await chat(*args)
    finally:
        await cache().drain()  # the background guide refreshes: asyncio.run hangs on them at shutdown


def main(args) -> None:
    asyncio.run(_chat_then_drain(args.code, args.asker, args.pull, args.boss, args.question or [], args.note,
                                  args.plan))
