"""Claude, through the Claude Agent SDK, answering raiders with Wipebot's tools (`Session`).

The tools are the core's MCP server, run in-process (no subprocess, one set of loaded reports), plus the bot's own
(prompt.BOT_TOOLS), whose handlers come with each question. What the model reads is in prompt.py; what's made of its
answer for Discord in discord_text.py. Only this module knows about the SDK.
"""
import asyncio
import functools
import json
import logging
import os
import re
import tempfile
import time
import warnings
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkPluginConfig,
    TextBlock,
    ThinkingBlock,
    ToolAnnotations,
    ToolUseBlock,
    create_sdk_mcp_server,
    get_session_info,
    query,
    tool,
)
from mcp.server.mcpserver.exceptions import ToolError

from wipebot.core import raidstrats, server, viewhost, viserio, wcl
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError, Report

from . import config, prompt
from .discord_text import polish

log = logging.getLogger(__name__)
# With a plugin, can_use_tool gates Read alone: the tools allowed outright are meant to skip it.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

# Claude Code attaches any file a prompt names as @path, tools or not, and a prompt carries what raiders, Discord and
# Warcraft Logs wrote (a report's title, a thread's name): denying Read stops that, for any path. So does a prompt
# whose every @ before a word is a full-width ＠ (`no_paths`), a guard that doesn't hang on how a new Claude Code
# reads its settings.
NO_FILES = json.dumps({"permissions": {"deny": ["Read"]}})


def no_paths(text: str) -> str:
    """The prompt with each @ that could start a path made a ＠, which Claude Code doesn't read as one."""
    return re.sub(r"@(?=\S)", "＠", text)


@functools.cache
def _empty_dir() -> str:
    """The sessions' working directory: an empty one, not the checkout with its .env. Always the same one, as
    Claude Code keeps a session's transcript by its working directory, where a resume looks for it."""
    path = os.path.join(tempfile.gettempdir(), "wipebot-sessions")
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class Context:
    """What a Discord message tells us besides its text: who asked, where, about which pull, and what the bot's own
    tools do for this asker (`tools`, by name: the tool's arguments -> what to tell the model)."""
    asker: str | None = None  # the asker's character, when known (`wipebot ask --as`)
    discord_name: str | None = None  # the asker's Discord name, when their character isn't known
    pull: int | None = None  # the pull it's about (a reply to the answer after it); None for the latest
    boss: str | None = None
    where: str = ""  # e.g. "in a DM"
    guild: str | None = None  # the server's guild on Warcraft Logs: "name, realm, region", to find its other reports
    earlier: str = ""  # the messages before this question that the conversation hasn't seen, from Discord
    follow_up: bool = False  # `earlier` is what was said since the conversation's previous question (SINCE)
    linked: str = ""  # the Discord messages the question links to (LINKED), from bot.linked_messages
    notes: list[str] = field(default_factory=list)  # the asker's standing preferences, remembered across conversations
    tools: dict[str, Callable[[dict], Awaitable[str]]] = field(default_factory=dict)  # follow_pulls, remember
    # each tool the model calls for this question, (name, arguments), as it calls it, and as the call returns
    on_tool: Callable[[str, dict], None] | None = None
    on_done: Callable[[str, dict], None] | None = None
    icons: Mapping[str, str] = field(default_factory=dict)  # the bot's emojis by name, for polish
    # tools run for the question before the model sees it, (name, arguments): their results come with it (a post's
    # pull_summary, which it would call first anyway), saving the model a turn
    prefetch: list[tuple[str, dict]] = field(default_factory=list)
    # raid_note and raid_plan: where the server's raid notes and plans are saved, and its guild's Warcraft Logs id
    # they're saved under; None where none are kept (they take no note or plan from the model)
    notebook: Notebook | None = None
    guild_id: int = 0
    server_id: int = 0  # the Discord server whose settings it answers under, and who asked: what an answer cost
    user_id: int = 0  # is kept under them (store.spend); 0 for none
    env: dict | None = None  # what the session runs on when the server brought its own key (keys.ServerKey.env)
    wcl_token: str | None = field(default=None, repr=False)  # the asker's Warcraft Logs token, signed in (/login)
    wcl_private: str | None = field(default=None, repr=False)  # ...and the one reading their private reports
    language: str | None = None  # the server's, set by an admin: for when the question's own doesn't say
    private_hint: str | None = None  # how they'd let Wipebot read their private reports, told with one it can't
    noted: Sequence[str] = ()  # the bosses with a raid note saved in the server
    planned: Sequence[str] = ()  # the bosses with a raid plan saved in the server

    def tokens(self) -> tuple[str | None, str | None, str | None]:
        """What the asker's tool calls read with (wcl.reading_as) and the hint for a report they can't read."""
        return self.wcl_token, self.wcl_private, None if self.wcl_private else self.private_hint


SERVER = "wipebot"  # the tools' MCP server: the model calls mcp__wipebot__<tool>
MAX_IMAGES = 4  # pictures sent with one answer (maps, views)
# A conversation's context (every tool result so far) is read again on every turn of every later answer, so a
# long one makes each answer slower and dearer. Past this many tokens, the session is worn out: the bot closes it
# after the answer, and the next question starts a new one with the place's recent messages (EARLIER) instead.
CONTEXT_LIMIT = 100_000
MAX_TURNS = 25  # model turns per answer: the evals' longest answers make about 20 tool calls
# What one answer may spend. Claude Code's cap (max_budget_usd) counts the whole session, not one answer, so a session
# gets twice this and is worn out once it has spent it: every answer may spend at least this and at most twice it.
ANSWER_BUDGET_USD = 1.0
# An answer that hit a cap ends with this result (ResultMessage.subtype); ModelError's kind is the cap.
CAPPED = ("error_max_turns", "error_max_budget_usd")
# A follow-up the answer offers, on a line of its own ("Follow-up: Replay those deaths?"): a button under it in Discord
FOLLOW_UP = re.compile(r"^[ \t]*(?:[-*][ \t]*)?Follow-up:[ \t]*(.+?)[ \t]*$", re.M | re.I)
MAX_FOLLOW_UPS = 3
FOLLOW_UP_CHARS = 80  # Discord's limit for a button's label, which is the question


# Where a picture goes in the answer ("[picture: player_night]"): the bot shows that tool's picture there, next to
# the part it backs, instead of under the whole answer. Asked for on a line of its own; one written as a bullet, in
# backticks or at the end of a line counts too, and its picture goes after that line. A view drawn in panels may be
# narrowed to one ("[picture: raid_note callouts]", its data-panel): only that one is posted.
PICTURE_MARK = re.compile(r"\[\s*picture\s*:\s*`?([a-z_]+(?:[ \t]+[a-z_]+)?)`?\s*\]", re.I)
_BARE = re.compile(r"[\s>*_`-]*(?:\d+[.)])?[\s*_`-]*")  # what's left of a line that held only marks: its bullet
NO_ANSWER = "(no answer)"  # an answer with no text at all: a post after a pull that nothing called for
NO_PICTURE = "none"  # "[picture: none]": the answer goes without the pictures its tools drew
_PLACED = "\ue000"  # a picture's place while the text is polished: a private-use character no answer has


def picture_marks(text: str) -> str:
    """The answer with each picture mark as a token on a line of its own after the mark's line, which polish leaves
    alone (`placed` reads them after); a line that held only marks goes."""
    lines = []
    for line in text.split("\n"):
        tools = PICTURE_MARK.findall(line)
        if not tools:
            lines.append(line)
            continue
        rest = PICTURE_MARK.sub("", line).rstrip()
        if not _BARE.fullmatch(rest):
            lines.append(rest)
        lines += [f"{_PLACED}{':'.join(t.lower().split())}{_PLACED}" for t in tools]
    return "\n".join(lines)


def placed(text: str) -> tuple[str, list[tuple[int, str]]]:
    """The polished answer without its picture tokens, and where each was: (offset in the text, tool or
    "tool:panel")."""
    marks, out, at = [], [], 0
    for m in re.finditer(rf"{_PLACED}([a-z_:]+){_PLACED}\n?", text):
        out.append(text[at:m.start()])
        marks.append((sum(map(len, out)), m[1]))
        at = m.end()
    out.append(text[at:])
    return "".join(out).rstrip(), marks


# A view's panels that are each their own subject (raid_note's), by the words an answer's headline names it with:
# an answer that places the view without naming a panel posts the ones its headline is about, else every one.
PANEL_WORDS = {"kicks": re.compile(r"\b(?:kick|interrupt)", re.I),
               "cooldowns": re.compile(r"\b(?:cooldowns?|cds?)\b", re.I),
               "callouts": re.compile(r"\b(?:personals?|defensives?|defs?)\b", re.I)}


def _tool(mark: str) -> str:
    """The tool a picture mark names ("raid_note:callouts" -> raid_note)."""
    return mark.partition(":")[0]


SHORT_ANSWER = 3  # bullets an answer may have and still keep its picture under it all
_BULLET = re.compile(r"^(?:[-*•]|\d+[.)])\s", re.M)  # a bullet at the top level


def placed_by_default(text: str, marks: list[tuple[int, str]], view: str | None) -> list[tuple[int, str]]:
    """Where the answer's pictures go: where it placed them, or else, in an answer of more than SHORT_ANSWER bullets,
    its view right after the headline (its first line), since the model often leaves the mark out or puts it last."""
    bullets = bool(_BULLET.search(text))  # a mark after the last bullet places nothing: the picture backs no bullet
    marks = [(at, tool) for at, tool in marks if text[at:].strip() and (not bullets or _BULLET.search(text, at))]
    if marks or view is None or len(_BULLET.findall(text)) <= SHORT_ANSWER or (end := text.find("\n")) < 0:
        return marks
    return [(end, view)]


def follow_ups(text: str) -> tuple[str, list[str]]:
    """An answer's text without its follow-up lines, and the follow-ups (at most MAX_FOLLOW_UPS that fit a button)."""
    found = [q.strip("*_\"' ") for q in FOLLOW_UP.findall(text)]
    kept = [q for q in dict.fromkeys(found) if 0 < len(q) <= FOLLOW_UP_CHARS][:MAX_FOLLOW_UPS]
    if len(kept) < len(found):
        log.info("follow-ups left out (too long, too many or repeated): %s", [q for q in found if q not in kept])
    return re.sub(r"\n{3,}", "\n\n", FOLLOW_UP.sub("", text)).strip(), kept


def context_size(usage: dict | None) -> int:
    """The conversation's context after an answer, in tokens, from the SDK's usage: its last API call read the
    whole context (cached or not) and added its output."""
    last = ((usage or {}).get("iterations") or [usage or {}])[-1]
    return sum(int(last.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))


_painter: viewhost.Painter | None = None  # draws the views' pictures; opened on the first one
_no_pictures = ""  # why views can't be drawn here (no Playwright or no Chromium): said once, then not tried


PICTURE_WAIT_S = 20  # an answer waits this long at most for its pictures still being drawn, then goes without


async def tool_call(name: str, args: dict) -> tuple[str, bool, list[bytes], asyncio.Task | None]:
    """One of the core's tools, run in-process: its text, whether it failed, the pictures for the answer (what the
    tool drew: pull_map's map) and, for a tool with a view, the task drawing the view as Claude and ChatGPT show it
    (`picture`) into those pictures. The model goes on meanwhile; the answer waits for it (`pictures_drawn`)."""
    text, failed, images = await server.call_with_images(name, args)
    drawing = None
    if not failed and name in server.VIEW_TOOLS and not _no_pictures:
        async def draw() -> None:
            images.extend(await picture(name, args))
        drawing = asyncio.create_task(draw())
    return text, failed, images, drawing


async def pictures_drawn(calls: Sequence[ToolCall]) -> None:
    """Until the calls' pictures still being drawn are done, for PICTURE_WAIT_S at most."""
    if pending := [c.drawing for c in calls if c.drawing and not c.drawing.done()]:
        await asyncio.wait(pending, timeout=PICTURE_WAIT_S)


async def picture(name: str, args: dict, rows: int | None = None) -> list[bytes]:
    """The tool's view as pictures (viewhost.Painter: one, or one per panel; a meter with `rows` bars), or none when
    it can't be drawn: the answer goes without."""
    global _painter, _no_pictures
    if _no_pictures:
        return []
    started = time.monotonic()
    try:
        _painter = _painter or viewhost.Painter()
        drawn = await _painter.pictures(name, args, rows)
    except ImportError as e:
        _no_pictures = f"Playwright isn't installed ({e})"
    except Exception as e:  # noqa: BLE001 (a picture never fails an answer)
        if "Executable doesn't exist" in str(e):
            _no_pictures = "Playwright's Chromium isn't installed (python -m playwright install --only-shell chromium)"
        else:
            log.warning("couldn't draw %s's view: %s", name, e)
            return []
    if _no_pictures:
        log.warning("answers go without the views' pictures: %s", _no_pictures)
        return []
    log.info("drew %s's view in %.1f s", name, time.monotonic() - started)
    return drawn


SHARED_ROWS = 5  # a meter's bars in an answer with several: two top fives take the room of one top ten


def meters(calls: Sequence[ToolCall]) -> list[ToolCall]:
    """The calls whose picture is a meter (a ranking of players), each asked once."""
    seen: set[str] = set()
    out = []
    for c in calls:
        if not c.failed and c.images and server.VIEW_TOOLS.get(c.name) == "meter" \
                and (key := f"{c.name} {json.dumps(c.args, sort_keys=True)}") not in seen:
            seen.add(key)
            out.append(c)
    return out


async def meters_shared(calls: Sequence[ToolCall]) -> None:
    """Several meters an answer posts (damage and healing: Answer.pictures), each drawn again with SHARED_ROWS bars,
    the ranking's rest a line under them as before."""
    if len(found := meters(calls)) < 2:
        return
    for c, images in zip(found, await asyncio.gather(*(picture(c.name, c.args, SHARED_ROWS) for c in found)),
                         strict=True):
        if images:
            c.images[:] = images


@dataclass
class ToolCall:
    """A tool call as the tool ran it (with the session's report filled in) and what it returned."""
    name: str
    args: dict
    result: str
    failed: bool
    images: list[bytes] = field(default_factory=list)  # pictures for the answer: a map, a view (tool_call)
    started: float = 0.0  # time.monotonic() when it started
    duration_s: float = 0.0
    wcl_points: dict = field(default_factory=dict)  # what its Warcraft Logs queries cost (wcl.Points.record)
    drawing: asyncio.Task | None = None  # its view being drawn into `images` (tool_call)

    def record(self, asked: float) -> dict:
        """For the interaction record, with when it started counted from `asked` (the question's time.monotonic()):
        the images stay out."""
        return {"name": self.name, "args": self.args,
                "result": server.PRIVATE_KEPT if self.wcl_points.get("private") else self.result, "failed": self.failed,
                "images": len(self.images), "started_s": round(self.started - asked, 2),
                "duration_s": round(self.duration_s, 2), "wcl_points": self.wcl_points}


async def sdk_tools(code: str | None = None, on_result: Callable[[ToolCall], None] | None = None,
                    guild: Callable[[], str | None] | None = None,
                    note: Callable[[dict], Awaitable[tuple[str, str] | str]] | None = None,
                    plan: Callable[[dict], Awaitable[tuple[str, str] | str]] | None = None,
                    wcl_token: Callable[[], tuple[str | None, str | None, str | None]] | None = None) -> list:
    """The core's MCP tools, wrapped for the Agent SDK's in-process server. Tools that take a report get `code`
    when the model names none, so sessions about different servers' reports don't mix. `on_result` gets every
    call, failed ones too. `guild` gives the server's guild as "name, realm, region": find_reports asked with no
    guild, or for that guild by name alone, gets the guild's name, realm and region. With `note`, raid_note
    takes no note from the model: `note` finds the saved one for the call's pull, (note, where it's from), or says
    why there's none. `plan` does the same for raid_plan (where it's from may be "": the plan's text says).
    `wcl_token` gives the asker's Warcraft Logs token, which a call reads with, so their own points pay first, their
    token that reads their private reports, and how they'd let Wipebot read them (server.telling_unreadable)."""
    saved_args = {"raid_note": ("note", note), "raid_plan": ("plan", plan)}

    def wrap(name: str, description: str, schema: dict):
        takes_report = "report" in (schema.get("properties") or {})
        arg, find = saved_args.get(name, ("", None))
        if find:
            schema = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != arg},
                      "required": [r for r in schema.get("required", []) if r != arg]}
            description = (description.replace("checked against", "saved in this server checked against", 1)
                           .replace("plan from raidstrats.gg", "plan from raidstrats.gg saved in this server", 1))

        async def handler(args: dict) -> dict:
            if takes_report and code and not args.get("report"):
                args = {**args, "report": code}
            if name == "find_reports" and guild and (own := guild()):
                args = with_own_guild(args, own)
            started = time.monotonic()
            drawing = None
            token, private, hint = wcl_token() if wcl_token else (None, None, None)
            with wcl.reading_as(token, private), server.telling_unreadable(hint), wcl.counting_points() as points:
                try:
                    found = await find(args) if find else None
                    if isinstance(found, str):
                        text, failed, images = found, False, []
                    else:
                        text, failed, images, drawing = await tool_call(name, {**args, arg: found[0]} if found
                                                                        else args)
                        if found and found[1] and not failed:
                            text = f"(The {arg} was {found[1]}.)\n{text}"
                except (ToolError, FactError) as e:
                    log.warning("tool %s failed: %s", name, e)  # the model only paraphrases it
                    text, failed, images = str(e), True, []
            if on_result:  # without the note or plan, which is the same in every call
                on_result(ToolCall(name, args, text, failed, images, started, time.monotonic() - started,
                                   points.record(), drawing))
            # The model gets the text; an image goes to the user with the answer (Answer.images).
            return {"content": [{"type": "text", "text": text}], "is_error": failed}
        # Read-only tools may run side by side: the model often asks for several at once.
        return tool(name, description, schema, annotations=ToolAnnotations(readOnlyHint=True))(handler)

    return [wrap(t.name, t.description or "", t.input_schema) for t in await server.mcp.list_tools()]


def with_own_guild(args: dict, own: str) -> dict:
    """find_reports' arguments for the server's own guild ("name, realm, region"): asked with no guild at all
    (there's no Warcraft Logs login in Discord to list "your own guilds"), or with its name alone, it gets the
    guild's name, realm and region; another guild is left as asked."""
    name, _, rest = own.partition(", ")
    realm, _, region = rest.partition(", ")
    asked = (args.get("guild") or "").strip().lower()
    if (asked and asked != name.strip().lower()) or not (name and realm and region):
        return args
    return {**args, "guild": args.get("guild") or name, "server": args.get("server") or realm,
            "region": args.get("region") or region}


WRITER_MODEL = "haiku"  # a short text of the bot's own, not an answer: the cheapest does
WRITER_BUDGET_USD = 0.05


async def write(prompt: str, env: Mapping[str, str] | None = None) -> tuple[str, float]:
    """A short text the bot writes itself (news.py), from a prompt alone: no tools, files or session; and what it
    cost. Raises ModelError when the API refuses it."""
    options = ClaudeAgentOptions(system_prompt="", tools=[], setting_sources=[], strict_mcp_config=True,
                                 settings=NO_FILES, cwd=_empty_dir(), max_turns=1, max_budget_usd=WRITER_BUDGET_USD,
                                 model=WRITER_MODEL, env=dict(env or {}))
    texts: list[str] = []
    cost = 0.0
    async for msg in query(prompt=no_paths(prompt), options=options):
        if isinstance(msg, AssistantMessage):
            if msg.error:
                raise ModelError(msg.error)
            texts += [b.text for b in msg.content if isinstance(b, TextBlock)]
        elif isinstance(msg, ResultMessage):
            cost = msg.total_cost_usd or 0.0
    return "".join(texts), cost


class ModelError(Exception):
    """Claude's API refused the question: `kind` is the SDK's reason, e.g. billing_error when the API key's
    credit has run out, or rate_limit; or the answer hit its cap of turns or spend (CAPPED). `answer` is what it
    got to, without its text: the cost, the tool calls and the prompt, for the record."""

    def __init__(self, kind: str, answer: Answer | None = None):
        super().__init__(kind)
        self.kind, self.answer = kind, answer


@dataclass
class Answer:
    text: str
    cost_usd: float | None = None  # this answer's own cost, not the conversation's so far
    calls: list[ToolCall] = field(default_factory=list)  # every tool call, as it ran, with its result
    # For the record of each answer (Store.record_interaction):
    prompt: str = ""  # what the model got: earlier messages, the context line, the question
    thinking: list[str] = field(default_factory=list)
    model: str = ""
    usage: dict | None = None  # tokens, from the SDK
    turns: int | None = None
    session_id: str = ""  # the Agent SDK's session, whose transcript has the same and more
    asked: float = 0.0  # time.monotonic() when the question went to the model
    # With a plugin (Session's `plugin`): each skill the model loaded ("skill:wipebot:raid-log-analysis") and file it
    # read ("read:examples.md"), in order
    loaded: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)  # what the answer offers next, as buttons in Discord
    marks: list[tuple[int, str]] = field(default_factory=list)  # where a tool's picture goes: (offset, tool[:panel])
    unpictured: bool = False  # the answer said "[picture: none]": what was drawn isn't what it's about
    footer: str = ""  # the small cost line under it, in Discord (WipeBot._with_footer)

    @property
    def full(self) -> str:
        """The text with its pictures' places and its follow-ups as the model wrote them, for the record and the
        evals' judge."""
        text = self.text
        for at, name in sorted(self.marks, reverse=True):
            mark = name.replace(":", " ")
            text = f"{text[:at].rstrip(chr(10))}\n[picture: {mark}]\n{text[at:].lstrip(chr(10))}".strip("\n")
        if self.unpictured:
            text += f"\n[picture: {NO_PICTURE}]"
        return text + "".join(f"\nFollow-up: {q}" for q in self.follow_ups)

    def records(self) -> list[dict]:
        """The tool calls for the interaction record."""
        return [c.record(self.asked) for c in self.calls]

    def wcl_points(self) -> dict:
        """The Warcraft Logs points the answer's tool calls cost, by kind (wcl.Points.record summed)."""
        spent: Counter = Counter()
        for c in self.calls:
            spent.update(c.wcl_points)
        return {k: round(v, 2) for k, v in spent.items()}

    @property
    def pictures(self) -> list[tuple[bytes, ToolCall]]:
        """The pictures of the answer's tool calls, to send with it, each with its call; the last few: every map, and
        one view (one panel or several), the first the answer placed (`marks`) or else the first drawn, the others a
        click away from its buttons and menus, so a few views don't fill the screen under a short answer. A meter
        comes with the answer's other meters (damage and healing), drawn shorter (meters_shared)."""
        if self.unpictured:
            return []
        drawn = [c for c in self.calls if not c.failed and c.images]
        marked = {_tool(m) for _, m in self.marks}  # a view the answer placed comes before the first drawn
        view = next((c for c in drawn if c.name in server.VIEW_TOOLS and c.name in marked), None) \
            or next((c for c in drawn if c.name in server.VIEW_TOOLS), None)
        shown = meters(drawn) if view and server.VIEW_TOOLS[view.name] == "meter" else [view]
        out = [(i, c) for c in drawn if any(c is v for v in shown) or c.name not in server.VIEW_TOOLS
               for i in c.images]
        named = {m.partition(":")[2] for _, m in self.marks if view and _tool(m) == view.name} - {""}
        if view and not named:  # the panels the headline is about, the answer not naming one
            headline = self.text.split("\n", 1)[0]
            named = {panel for panel, words in PANEL_WORDS.items() if words.search(headline)}
        if any(getattr(i, "name", "") in named for i, c in out if c is view):  # the panel the answer is about
            out = [(i, c) for i, c in out if c is not view or getattr(i, "name", "") in named]
        return out[-MAX_IMAGES:]

    def place(self, call: ToolCall) -> int | None:
        """Where in the text the call's picture goes (an offset), when the answer placed it; None: under it all."""
        return next((at for at, m in self.marks if _tool(m) == call.name), None)

    @property
    def images(self) -> list[bytes]:
        return [i for i, _ in self.pictures]


NO_REPORT = "No report to read: ask for a Warcraft Logs report link."


class Session:
    """One conversation about one report (`code`, "" for none): follow-up questions keep the context, and a report still
    being logged is refreshed before every question."""

    def __init__(self, library: server.Library, code: str, model: str = "",
                 on_tool: Callable[[str, dict], None] | None = None, name: str = "wipebot",
                 system_prompt: str | None = None, plugin: str | None = None, resume: str | None = None,
                 env: dict[str, str] | None = None):
        self.library, self.code, self.model, self.on_tool = library, code, model, on_tool
        self.env = env or {}  # Claude Code's own: config.model_env() answers through OpenRouter
        self.name = name  # what the bot calls itself: its name in Discord
        # Another client's prompt in place of the bot's, without the bot's own tools (follow_pulls, remember): the
        # evals answer as a client with only the connector would (claude.ai today: no instructions, no skill).
        self.system_prompt = system_prompt
        # A plugin's folder whose skills the model loads when a question calls for one, as Claude does with a plugin
        # (the evals' plugin mode): it may load them and read the files in that folder, nothing else.
        self.plugin = plugin
        # A conversation saved before a restart: its session goes on from its transcript, when Claude Code still has it
        self.resume = resume
        self.resumed = False
        self.session_id = resume or ""  # the transcript's, once known
        self._client: ClaudeSDKClient | None = None
        self._calls: list[ToolCall] = []  # tool calls of the current question
        self._ctx: Context | None = None  # the current question's
        self._spent = 0.0  # the SDK's cost so far, which adds up over the conversation
        self.context_tokens = 0  # the conversation's context after its last answer

    @property
    def worn_out(self) -> bool:
        """Whether the conversation has grown past CONTEXT_LIMIT, or spent ANSWER_BUDGET_USD, and should be closed
        after this answer."""
        return self.context_tokens >= CONTEXT_LIMIT or self._spent >= ANSWER_BUDGET_USD

    def _bot_tool(self, spec: prompt.BotTool):
        """One of the bot's own tools, run by the current question's handler (Context.tools)."""
        async def handler(args: dict) -> dict:
            run = self._ctx.tools.get(spec.name) if self._ctx else None
            started = time.monotonic()
            text = await run(args) if run else spec.elsewhere
            self._returned(ToolCall(spec.name, args, text, False, started=started,
                                    duration_s=time.monotonic() - started))
            return {"content": [{"type": "text", "text": text}]}
        return tool(spec.name, spec.description, spec.schema)(handler)

    async def _saved_note(self, args: dict) -> tuple[str, str] | str:
        """raid_note: the server's note (Context.notebook) for the boss of the pull the call is about."""
        if not (self._ctx and self._ctx.notebook):
            return "Raid notes are only kept in Discord: there's none here."
        if not (code := args.get("report") or self.code):
            return NO_REPORT
        report = await self.library.report(code)
        book, guild = self._ctx.notebook, self._ctx.guild_id
        boss, upcoming = args.get("boss"), bool(args.get("upcoming"))
        if upcoming or (boss and not server.pulled(report, boss)):  # the next raid's, or not pulled: as it is
            return await server.unpulled_note(book, guild, report, boss or report.boss_key(report.fight(None, None)),
                                              upcoming)
        f = report.fight(args.get("pull"), boss)
        if found := await server.saved_note(book, guild, report, f, bool(args.get("all_pulls"))):
            return found
        other = server.OTHER_NIGHTS if book.notes(guild, f["encounterID"]) else ""
        return (f"No raid note is saved for {f['name']} that night in this server{other}. Officers save one by "
                "pasting the note exported from Viserio to the bot." + server.NO_JOBS)

    async def _saved_plan(self, args: dict) -> tuple[str, str] | str:
        """raid_plan: every version of the server's plans (Context.notebook) for the boss of the call's pull, after
        fetching them from raidstrats.gg when the guild has a login there; the tool checks each pull against the
        version it was planned with."""
        if not (self._ctx and self._ctx.notebook):
            return "Raid plans are only kept in Discord: there's none here."
        book, guild = self._ctx.notebook, self._ctx.guild_id
        if not (code := args.get("report") or self.code):
            return NO_REPORT
        report = await self.library.report(code)
        boss = args.get("boss")
        # The plan as it is (`show`, or a boss not pulled yet) needs no pull: every plan, raid_plan picks the boss's
        whole = bool(args.get("show")) or bool(boss and not server.pulled(report, boss))
        f = None if whole else report.fight(args.get("pull"), boss)
        await raidstrats.sync(book, guild)
        login = book.login(guild)
        source = raidstrats.source(login) if login else "saved from pastes"
        found = server.every_plan(book, guild) if f is None else server.saved_plans(book, guild, report, f)
        if not found:
            named = boss if f is None else report.boss_key(f)
            return (f"No raid plan is saved for {named} in this server. Officers save one by pasting the "
                    "plan's export from raidstrats.gg (the planner's Export button) to the bot." + server.NO_JOBS)
        return "\n".join(found), source

    async def __aenter__(self) -> Session:
        # without a report (code ""), it answers about the game, or asks for a report link
        self.report = await self.library.report(self.code) if self.code else None
        bot = self.system_prompt is None  # else a client with only the connector: the core's raid_note and raid_plan
        tools = await sdk_tools(self.code, self._returned,lambda: self._ctx.guild if self._ctx else None,
                                self._saved_note if bot else None, self._saved_plan if bot else None,
                                lambda: self._ctx.tokens() if self._ctx else (None, None, None))
        if bot:
            tools += [self._bot_tool(spec) for spec in prompt.BOT_TOOLS]
        options = ClaudeAgentOptions(
            system_prompt=(prompt.system_prompt(self.name, server.skill(), config.names_in_channels())
                           if self.system_prompt is None else self.system_prompt),
            mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=tools)},
            tools=[],  # no built-in tools: no files, shell or web
            allowed_tools=[f"mcp__{SERVER}__{t.name}" for t in tools],
            setting_sources=[],  # don't pick up the developer's CLAUDE.md, settings or plugins
            strict_mcp_config=True,  # nor their other MCP servers and claude.ai connectors
            settings=NO_FILES,
            cwd=(cwd := _empty_dir()),
            max_turns=MAX_TURNS,
            max_budget_usd=2 * ANSWER_BUDGET_USD,
            model=self.model or None,
            effort=config.effort(),
            # Opus leaves its thinking out of what it returns unless asked: summarised, the reviews can read it
            thinking={"type": "adaptive", "display": "summarized"},
            env=self.env,
        )
        if self.resume and get_session_info(self.resume, directory=cwd):
            options.resume, self.resumed = self.resume, True
        else:
            self.session_id = ""
        if self.plugin:  # its skills, and Read for their files only: the folder is all can_use_tool lets it read
            options.tools, options.settings, options.skills = ["Skill", "Read"], None, "all"
            options.plugins = [SdkPluginConfig(type="local", path=self.plugin)]
            options.can_use_tool = self._in_plugin
        self._client = ClaudeSDKClient(options)
        await self._client.connect()
        return self

    async def _in_plugin(self, name: str, args: dict, context) -> PermissionResultAllow | PermissionResultDeny:
        """With a plugin: Read only inside its folder; Claude Code asks this for any other built-in tool use."""
        path, folder = os.path.realpath(str(args.get("file_path") or "")), os.path.realpath(self.plugin or "")
        if name == "Read" and self.plugin and path.startswith(folder + os.sep):
            return PermissionResultAllow()
        return PermissionResultDeny(message="Only the plugin's own files can be read here.")

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.disconnect()

    def _returned(self, call: ToolCall) -> None:
        """A tool call of the current question returned: keep it, and tell who's watching."""
        self._calls.append(call)
        if self._ctx and self._ctx.on_done:
            self._ctx.on_done(call.name, call.args)

    def _called(self, name: str, args: dict, ctx: Context, answer: Answer) -> None:
        """The model called tool `name`: tell who's watching, and note a skill or file it loaded."""
        if self.on_tool:
            self.on_tool(name, args)
        if ctx.on_tool:
            ctx.on_tool(name, args)
        if name == "Skill":
            answer.loaded.append(f"skill:{args.get('skill', '')}")
        elif name == "Read":
            answer.loaded.append(f"read:{os.path.basename(str(args.get('file_path', '')))}")

    async def ask(self, question: str, ctx: Context) -> Answer:
        assert self._client, "use 'async with Session(...)'"
        asked = time.monotonic()
        if self.code:
            # read again for every question, so a pull that just ended is in it: the tools then reuse this read
            self.report = await self.library.report(self.code, max_age_s=0)
        ran = [await self._prefetched(name, args, ctx.tokens()) for name, args in ctx.prefetch]
        question += "".join(prompt.PREFETCHED.format(tool=c.name, result=c.result) for c in ran if not c.failed)
        report = self.report
        text = no_paths(prompt.message(question, ctx, report, await report.guild_run_doubt() if report else "",
                                       raids=await report.raids() if report else None))
        self._ctx = ctx
        await self._client.query(text)
        self._calls[:] = ran
        answer = Answer("", prompt=text, asked=asked)
        texts: list[str] = []
        error = None
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                answer.model = msg.model
                error = error or msg.error  # its text is the API's error message, not an answer
                step = []
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        step.append(block.text)
                    elif isinstance(block, ThinkingBlock) and block.thinking:
                        answer.thinking.append(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        self._called(block.name.removeprefix(f"mcp__{SERVER}__"), block.input, ctx, answer)
                if step:
                    texts.append("".join(step))
            elif isinstance(msg, ResultMessage):
                if msg.total_cost_usd is not None:
                    answer.cost_usd = msg.total_cost_usd - self._spent
                    self._spent = msg.total_cost_usd
                answer.usage, answer.turns, answer.session_id = msg.usage, msg.num_turns, msg.session_id
                self.session_id = msg.session_id
                self.context_tokens = context_size(msg.usage)
                if msg.subtype in CAPPED:  # what it wrote so far is half an answer
                    error = error or msg.subtype
        answer.calls = list(self._calls)
        if error:
            raise ModelError(error, answer)
        await pictures_drawn(answer.calls)
        text = texts[-1].strip() if texts else NO_ANSWER  # the last text is the answer
        text, answer.follow_ups = follow_ups(text)
        with wcl.reading_as(*ctx.tokens()[:2]):  # a private report the asker read too
            read = await self._reports_read(answer.calls)
        answer.text, answer.marks = placed(polish(
            picture_marks(text), [c.result for c in answer.calls if not c.failed], read, ctx.icons,
            await self._roster(ctx)))
        answer.unpictured = any(tool == NO_PICTURE for _, tool in answer.marks)
        if answer.unpictured:
            answer.marks = []
        await meters_shared([c for _, c in answer.pictures])
        view = next((c.name for _, c in answer.pictures if c.name in server.VIEW_TOOLS), None)
        answer.marks = placed_by_default(answer.text, answer.marks, view)
        return answer

    @staticmethod
    async def _roster(ctx: Context) -> dict[str, tuple[str, str]]:
        """The server's guild's roster from Viserio, name -> class and spec, for the icons of players the answer's
        reports and tool results don't give; none without icons or a key, or when Viserio doesn't answer."""
        found = ctx.notebook.viserio_key(ctx.guild_id) if ctx.icons and ctx.notebook and ctx.guild_id else None
        if not found:
            return {}
        try:
            return await viserio.roster_specs(found[0])
        except viserio.ViserioError as e:
            log.info("no roster from Viserio for the icons: %s", e)
            return {}

    async def _prefetched(self, name: str, args: dict,
                          tokens: tuple[str | None, str | None, str | None] = (None, None, None)) -> ToolCall:
        """One of a question's `prefetch` tools, run as the model's own call would be, on the session's report."""
        args = {"report": self.code, **args} if self.code else args
        started, drawing = time.monotonic(), None
        with wcl.reading_as(tokens[0], tokens[1]), server.telling_unreadable(tokens[2]), \
                wcl.counting_points() as points:
            try:
                text, failed, images, drawing = await tool_call(name, args)
            except (ToolError, FactError) as e:
                log.warning("tool %s failed: %s", name, e)
                text, failed, images = str(e), True, []
        return ToolCall(name, args, text, failed, images, started, time.monotonic() - started, points.record(),
                        drawing)

    async def _reports_read(self, calls: Sequence[ToolCall]) -> list[Report]:
        """The session's report, then the others the tool calls read (a question about another night), for
        linking the answer's abilities and pulls."""
        out = [self.report] if self.report else []
        for code in dict.fromkeys(str(c.args["report"]) for c in calls if c.args.get("report")):
            try:
                report = await self.library.report(code, max_age_s=float("inf"))  # loaded by the call itself
            except Exception:  # not a report the tools could read: nothing to link from it
                continue
            if report.code not in {r.code for r in out}:
                out.append(report)
        return out
