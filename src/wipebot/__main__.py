"""Usage:
    wipebot mcp [--report CODE]          serve the tools over MCP (stdio), for Claude Code and other MCP apps
    wipebot serve [--port 8000]          serve the tools over MCP (Streamable HTTP at /mcp), for many users
    wipebot status                       show how Wipebot reaches Warcraft Logs
    wipebot plugin DIR --repo OWNER/NAME write the connector and skill as a Claude plugin marketplace, for a repo
    wipebot call TOOL [JSON]             run one tool and print its result, e.g. wipebot call pull_summary
                                         '{"report": "KBYT4xXn7VNjDgh8", "pull": 13}'
    wipebot bot                          run the Discord bot in every server set up with /wipebot setup
    wipebot replay CODE [--server]       replay an old report into one set-up server as if live, a pull every 20 s
    wipebot try "QUESTION" --channel ID  ask once in Discord, post the answer in that channel, exit
    wipebot ask CODE                     developer chat: ask about a report as a raider would in Discord
    wipebot note FILE --at TIME          save a raid note or plan as if an officer pasted it then (FILE - for stdin)
    wipebot raidstrats-login --guild ID  fetch a guild's raid plans from raidstrats.gg with your login there
    wipebot viserio --guild ID           read a guild's raid nights to come from Viserio Cooldowns with its key
"""
import argparse
import asyncio
import io
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wipebot.discord.store import Store


# ---------- the tools, without an app ----------

async def call(tool: str, args: dict) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from wipebot.core import server
    from wipebot.core.cache import cache
    server.configure()
    try:
        text, _failed = await server.call(tool, args)
        print(text, flush=True)
    except ToolError as e:
        raise SystemExit(str(e)) from None
    finally:
        await cache().drain()


async def view(args: argparse.Namespace) -> None:
    """`wipebot view`: the tool's view in a window (viewhost.watch), or screenshots with what went wrong in them."""
    from wipebot.core import server, viewhost
    from wipebot.core.cache import cache
    server.configure()
    themes = tuple(args.theme.split(","))
    widths = tuple(int(w) for w in args.width.split(","))
    try:
        if not args.shot:
            await viewhost.watch(args.tool, json.loads(args.args), themes[0], widths[0], args.mode.split(",")[0])
            return
        shots = await viewhost.shoot(args.tool, json.loads(args.args), Path(args.shot), themes, widths,
                                     tuple(args.mode.split(",")), tuple(args.click), args.tokens)
        failed = False
        for s in shots:
            print(f"{s.path}  height {s.height}" + "".join(f"\n  ! {e}" for e in s.errors), flush=True)
            failed |= bool(s.errors) or not s.height
        (Path(args.shot) / "messages.json").write_text(json.dumps([{"shot": s.path.name, "messages": s.messages}
                                                                   for s in shots], indent=1), encoding="utf-8")
        if failed:
            raise SystemExit("some views had errors or reported no height")
    finally:
        await cache().drain()


def status() -> None:
    from wipebot.core import wcl
    if wcl.saved_login():
        print("A Warcraft Logs user login is saved in warcraft_cli's store: Wipebot reads with it (private reports).")
    elif wcl.client_credentials():
        print("Client credentials are set: Wipebot reads public reports with them.")
    else:
        print("Not set up: set WCL_CLIENT_ID and WCL_CLIENT_SECRET (a client from warcraftlogs.com/api/clients).")


# ---------- the Discord bot ----------

def bot_wcl_access() -> None:
    """The bot reads Warcraft Logs with the client credentials only: with a saved user login (which warcraft_cli
    reads with instead), every member could read that user's private reports, as over HTTP (`serve`)."""
    from wipebot.core import wcl
    if wcl.saved_login():
        raise SystemExit("a Warcraft Logs user login is saved in warcraft_cli's store; in Discord every member would "
                         "read its private reports. Run the bot from an account without one.")
    wcl.client_credentials_only()  # and one saved while it runs isn't read either


def run(test_report: str | None = None, test_server: int | None = None, try_question: str | None = None,
        try_channel: int | None = None) -> None:
    import discord

    from wipebot.core import server
    from wipebot.discord.bot import WipeBot
    from wipebot.discord.config import channel_context, check_settings, model, record_answers
    from wipebot.discord.store import Store

    bot_wcl_access()
    check_settings()
    store = Store()
    if (test_report or try_question) and test_server is None:
        servers = store.all_settings()
        if not servers:
            raise SystemExit("no server is set up yet: run the bot and use /wipebot setup in Discord first")
        test_server = servers[0].discord_guild_id
    library = server.configure()  # the tools the agent uses; one WCL client shared with the followers
    # One handler for everything (the MCP library and discord.py would each add their own), and no line per
    # WCL request.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = WipeBot(store, library, model(), test_report, test_server, try_question, try_channel, channel_context(),
                  record_answers())
    try:
        bot.run(os.environ["DISCORD_TOKEN"], log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord turned down DISCORD_TOKEN: copy the bot's token again from the Discord developer "
                         "portal (the application's Bot page, Reset Token).") from None


def save_note(path: str, at: str | None, by: str, discord_server: int | None, store: Store | None = None) -> None:
    """A raid note or plan saved from the command line: one from before the bot kept them, for the pulls it was
    for."""
    from wipebot.core.report import FactError
    from wipebot.discord.commands import save_pasted
    from wipebot.discord.store import Store

    store = store or Store()
    servers = [s.discord_guild_id for s in store.all_settings()]
    if discord_server is None and len(servers) != 1:
        raise SystemExit(f"give --server: set-up servers are {servers or 'none'}")
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    when = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() if at else None
    try:
        print(save_pasted(store, discord_server or servers[0], text, by, when))
    except FactError as e:
        raise SystemExit(f"not a raid note or plan: {e}") from None


def raidstrats_login(guild: int, by: str, disconnect: bool) -> None:
    """A guild's raidstrats.gg login, saved in the notebook both apps read. The browser comes back to localhost:
    here, when run on this computer; otherwise the address it couldn't open is pasted."""
    import queue
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from wipebot.core import raidstrats
    from wipebot.core.notebook import Notebook

    book = Notebook()
    if disconnect:
        book.drop_login(guild)
        print("Disconnected: the plans are no longer fetched (the saved versions stay).")
        return
    try:
        url, state = raidstrats.login_url()
    except raidstrats.RaidstratsError as e:
        raise SystemExit(str(e)) from None
    pasted: queue.Queue[str] = queue.Queue()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done: you can close this tab and go back to the terminal.")
            pasted.put("http://localhost" + self.path)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 (the base class names it)
            pass

    try:
        port = int(raidstrats.REDIRECT_URL.split(":")[2].split("/")[0])
        listener = HTTPServer(("127.0.0.1", port), Callback)
        threading.Thread(target=listener.handle_request, daemon=True).start()
    except OSError:  # the port is taken: pasting still works
        pass
    print(f"Log in on raidstrats.gg and approve Wipebot (with \"In-game import\"):\n\n  {url}\n\n"
          "If the browser then shows a page that doesn't load, paste its address here.", flush=True)
    threading.Thread(target=lambda: pasted.put(input("> ")), daemon=True).start()
    try:
        username, token = asyncio.run(raidstrats.connect(pasted.get(), state))
    except raidstrats.RaidstratsError as e:
        raise SystemExit(f"Not connected: {e}.") from None
    book.save_login(guild, username, token, by)
    asyncio.run(raidstrats.sync(book, guild))
    login = book.login(guild)
    if login and login.problem:
        raise SystemExit(f"Connected as {username}, but fetching the plans failed: {login.problem}.")
    print(f"Connected as {username}. Plans it can open:")
    for p in book.latest_plans(guild):
        print(f"- {p.name} ({p.boss})")


def viserio_key(guild: int, by: str, disconnect: bool) -> None:
    """A guild's Viserio Cooldowns key (WOWUTILS_API_KEY, else typed), saved in the notebook both apps read once
    Viserio says its group is that guild's."""
    import getpass

    from wipebot.core import server, viserio
    from wipebot.core.notebook import Notebook

    book = Notebook()
    if disconnect:
        book.drop_viserio_key(guild)
        print("Disconnected: the raid nights to come are no longer read from Viserio.")
        return
    key = os.environ.get("WOWUTILS_API_KEY") or getpass.getpass("The group's API key: ")

    async def connect() -> str:
        found = await server.configure().wcl.guild_by_id(guild)
        if not found:
            raise SystemExit(f"Warcraft Logs has no guild {guild}")
        realm = found["server"]
        g = await viserio.connect(book, guild, key, (found["name"], realm["slug"], realm["region"]["slug"]), by)
        return g.name
    try:
        print(f"Connected to {asyncio.run(connect())}.")
    except viserio.ViserioError as e:
        raise SystemExit(f"Not connected: {e}.") from None


def write_plugin(out: Path, repo: str, name: str) -> None:
    """`wipebot plugin`: the marketplace for this server (WIPEBOT_PUBLIC_URL, WIPEBOT_ALLOWED_GUILDS) in `out`,
    replacing what was there but .git, for its owner to push."""
    from wipebot.mcp import plugin
    from wipebot.mcp.signin import Access
    url = os.environ.get("WIPEBOT_PUBLIC_URL")
    if not url:
        raise SystemExit("set WIPEBOT_PUBLIC_URL: the plugin names the connector at it")
    try:
        access = Access.from_env()
    except ValueError as e:
        raise SystemExit(f"set {e}") from None
    asyncio.run(access.resolve())  # the name of a guild given by its id
    files = plugin.marketplace(name, str(access), url.rstrip("/") + "/mcp", repo)
    for old in out.iterdir() if out.exists() else []:
        if old.name != ".git":
            shutil.rmtree(old) if old.is_dir() else old.unlink()
    for path, data in files.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(data)
    print(f"wrote {len(files)} files to {out}: push them to {repo}")


# ---------- the command line ----------

def _tool_commands(sub) -> None:
    sub.add_parser("mcp", help="serve the tools over MCP (stdio)").add_argument("--report", help="default report")
    s = sub.add_parser("serve", help="serve the tools over MCP (Streamable HTTP at /mcp)")
    s.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 in a container)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--public-url", help="the URL users reach it at, e.g. https://wipebot.example.com (default: "
                                        "WIPEBOT_PUBLIC_URL; needed unless it listens on localhost only)")
    sub.add_parser("status", help="show how Wipebot reaches Warcraft Logs")
    pl = sub.add_parser("plugin", help="write the connector and the skill as a Claude plugin marketplace, for a repo")
    pl.add_argument("dir", help="the repository's checkout: its files are replaced")
    pl.add_argument("--repo", required=True, help="where it's pushed, owner/name on GitHub (public)")
    pl.add_argument("--name", default="Wipebot", help="the plugin's and connector's name as members see it")
    rs = sub.add_parser("raidstrats-login", help="fetch a guild's raid plans from raidstrats.gg with your login")
    rs.add_argument("--guild", type=int, required=True, help="the guild's id on Warcraft Logs (its page's link: "
                                                             "warcraftlogs.com/guild/id/<id>)")
    rs.add_argument("--by", default="the command line", help="who connected it, kept with it")
    rs.add_argument("--disconnect", action="store_true", help="stop fetching and forget the login")
    vs = sub.add_parser("viserio", help="read a guild's raid nights to come from Viserio Cooldowns with its group's "
                                        "key (WOWUTILS_API_KEY, else asked)")
    vs.add_argument("--guild", type=int, required=True, help="the guild's id on Warcraft Logs")
    vs.add_argument("--by", default="the command line", help="who connected it, kept with it")
    vs.add_argument("--disconnect", action="store_true", help="stop reading them and forget the key")
    c = sub.add_parser("call", help="run one tool and print its result")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}", help="tool arguments as JSON")
    v = sub.add_parser("view", help="a tool's view as Claude and ChatGPT draw it, in a window or as screenshots")
    v.add_argument("tool")
    v.add_argument("args", nargs="?", default="{}", help="tool arguments as JSON")
    v.add_argument("--shot", metavar="DIR", help="screenshots into DIR instead of a window, with the console's errors "
                                                 "and the bridge's messages")
    v.add_argument("--theme", default="dark,light", help="themes, comma-separated (a window opens in the first)")
    v.add_argument("--width", default="430,760", help="inline widths in px, comma-separated")
    v.add_argument("--mode", default="inline", help="inline, fullscreen or both (inline,fullscreen)")
    v.add_argument("--click", action="append", default=[], help="a button to click after the first shot, by its "
                                                                 "text, or css:<selector>; repeat for more, each "
                                                                 "shot in turn")
    v.add_argument("--tokens", default="none", choices=["none", "claude"], help="the host's style variables")


def _bot_commands(sub) -> None:
    sub.add_parser("bot", help="run the Discord bot")
    replay = sub.add_parser("replay", help="replay an old report into a set-up server, one pull per poll, as if live")
    replay.add_argument("code")
    replay.add_argument("--server", type=int, help="Discord server id; default the first set-up server")
    try_ = sub.add_parser("try", help="ask one question, post the answer in a channel, exit")
    try_.add_argument("question")
    try_.add_argument("--channel", type=int, required=True, help="Discord channel id to post the answer in")
    try_.add_argument("--report", help="report code; default the guild's live or latest report")
    try_.add_argument("--server", type=int, help="Discord server id; default the first set-up server")
    ask = sub.add_parser("ask", help="developer chat: ask about a report, in place of Discord")
    ask.add_argument("code")
    ask.add_argument("--as", dest="asker", help="the character asking")
    ask.add_argument("--pull", type=int, help="ask about this pull (pull number on the boss)")
    ask.add_argument("--boss", help="boss for --pull, default the last boss in the report")
    ask.add_argument("--note", help="a raid note file, as if an officer pasted it to the bot")
    ask.add_argument("--plan", help="a raid plan file (raidstrats.gg's export), as if an officer pasted it")
    ask.add_argument("-q", "--question", action="append",
                     help="ask this and exit; repeat for follow-ups in the same conversation")
    note = sub.add_parser("note", help="save a raid note or plan in the bot's database, as an officer's paste does")
    note.add_argument("file", help="the note as exported from Viserio, or the plan's export from raidstrats.gg; - "
                                   "reads it from stdin")
    note.add_argument("--at", help='when it counts as saved, "YYYY-MM-DD HH:MM" UTC (before the raid for a '
                                   "past night's note); default now")
    note.add_argument("--by", default="an officer", help="who saved it, as answers name them")
    note.add_argument("--server", type=int, help="Discord server id; default the only set-up server")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wipebot", description="AI raid coaching for World of Warcraft.",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    _tool_commands(sub)
    _bot_commands(sub)
    return p


def main() -> None:  # noqa: C901
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # names like Mírëlle, on Windows too
    args = parser().parse_args()
    if args.cmd not in ("bot", "replay", "try"):  # the bot sets up its own logging
        logging.basicConfig(level=logging.WARNING)  # stderr; for `mcp`, stdout carries the protocol

    if args.cmd == "mcp":
        from wipebot.core.server import report_code
        from wipebot.mcp import stdio
        stdio.serve(report_code(args.report) if args.report else None)
    elif args.cmd == "serve":
        from wipebot.mcp import remote
        remote.serve(args.host, args.port, args.public_url or os.environ.get("WIPEBOT_PUBLIC_URL") or None)
    elif args.cmd == "status":
        status()
    elif args.cmd == "plugin":
        write_plugin(Path(args.dir), args.repo, args.name)
    elif args.cmd == "call":
        asyncio.run(call(args.tool, json.loads(args.args)))
    elif args.cmd == "view":
        asyncio.run(view(args))
    elif args.cmd == "bot":
        run()
    elif args.cmd == "replay":
        run(args.code, args.server)
    elif args.cmd == "try":
        run(args.report, args.server, args.question, args.channel)
    elif args.cmd == "ask":
        from wipebot.discord.devcli import main as ask_main
        ask_main(args)
    elif args.cmd == "note":
        save_note(args.file, args.at, args.by, args.server)
    elif args.cmd == "raidstrats-login":
        raidstrats_login(args.guild, args.by, args.disconnect)
    elif args.cmd == "viserio":
        viserio_key(args.guild, args.by, args.disconnect)


if __name__ == "__main__":
    main()
