"""Wipebot's tools: every tool defined once, from its signature and docstring, and the in-process MCP server `mcp`.

The Discord bot and `wipebot call` run `mcp` in-process; `wipebot mcp` serves it over stdio to any MCP app (Claude
Code, Claude Desktop, ChatGPT desktop, ...; wipebot.mcp.stdio). `wipebot serve` builds its own server from the same
tools for many users at once (wipebot.mcp.remote). A tool's docstring says what it returns, when to call it and
how to read its output; how to answer is the raid-log-analysis skill (skills/, `skill`); instructions.md is a short
overview.

Where each tool's work is done, under report/ unless another folder is named:
- pull_summary, pull_timeline: fight.py (a pull's post: ../pulls.py); compare_pulls: compare.py
- death_recap, defensives: deaths.py (what the healers did: healing.py); healing: healing.py
- mechanic_hits: mechanics.py; enemies: adds.py; tank_swaps: tanks.py; breakdown: tables.py
- pull_map: maps.py (positions.py, drawn by draw.py)
- raid_note: note.py; raid_plan: plan.py (fetched by ../raidstrats.py; both saved in ../notebook.py)
- save_note_or_plan, on a server that knows who's asking (`allow_saving`): here (`save_pasted`)
- progress, roster: night.py; find_reports: guild.py; raid_prep: build.py
- player_pull, player_night, buff_at_casts: coaching.py (casting.py, cooldowns.py, the parses: ranks.py)
- player_history: history.py (experience: knowledge/players.py)
- compare_with_top: build.py (build), top.py (casts, and without a player), knowledge/top_parses.py (cooldowns)
- wcl_query: query.py
- spell_info: knowledge/spells.py; boss_guide: knowledge/bosses.py; spec_guide: knowledge/specs.py;
  hotfixes: knowledge/news.py; web_lookup: knowledge/pages.py
"""
import asyncio
import base64
import functools
import inspect
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import resources
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import clock, knowledge, notebook, raidstrats, wcl
from .knowledge import KnowledgeError
from .library import Library, ensure_wcl_access, latest_raid, report_code  # noqa: F401 (imported from here too)
from .report import (
    FactError,
    PullNotLogged,
    PullRef,
    Report,
    adds,
    build,
    coaching,
    compare,
    cooldowns,
    deaths,
    fight,
    history,
    maps,
    mechanics,
    night,
    saved,
    tables,
    tanks,
    top,
)
from .report import guild as guild_logs
from .report import healing as heals
from .report import note as notes
from .report import plan as plans
from .report import query as queries
from .text import day, name_key, plural
from .wcl import WCLError

INSTRUCTIONS = resources.files("wipebot.core").joinpath("instructions.md").read_text(encoding="utf-8")
SKILL = resources.files("wipebot.core").joinpath("skills/raid-log-analysis")  # how to answer: one source, see skill()
# Titles where the tool's name doesn't read as one ("Wcl query"); the rest are its name in words.
TITLES = {"compare_with_top": "Compare with top players",
          "save_note_or_plan": "Save raid note or plan",
          "wcl_query": "Warcraft Logs query"}


# The AGPL (section 13): whoever uses a copy over the network can get its source. A host who changed the code points
# WIPEBOT_SOURCE_URL at theirs.
SOURCE = "https://github.com/Enf0/wipebot"


def source_url() -> str:
    return os.environ.get("WIPEBOT_SOURCE_URL") or SOURCE


def title(name: str) -> str:
    """A tool's title for people (clients show it, and Claude's directory requires one)."""
    return TITLES.get(name) or name.replace("_", " ").capitalize()


def skill() -> str:
    """The raid-log-analysis skill as one text: SKILL.md's body, then each file it points to. For a host that
    always analyses raid logs (the Discord bot), which needs all of it in its prompt rather than loading it when a
    question calls for it as a skill-aware client does."""
    body = SKILL.joinpath("SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].strip()
    parts = [body] + [SKILL.joinpath(name).read_text(encoding="utf-8").strip()
                      for name in re.findall(r"\]\(([\w-]+\.md)\)", body)]
    return "\n\n".join(parts)


_log = logging.getLogger(__name__)


# Takes a tool call: (user, client, tool, args, started, duration_s, failed, result, its Warcraft Logs points as
# wcl.Points.record gives them)
Record = Callable[[str | None, str | None, str, dict, float, float, bool, str, dict], None]


@dataclass
class Remote:
    """Serving many signed-in users (remote.py): what the tools do differently then."""
    guilds: Callable[[], list[tuple[str, str, str]]]  # (name, realm, region): who may use it, find_reports' default
    user_name: Callable[[str], str]  # a signed-in user (the token's subject) -> how the log names them
    record: Record | None = None  # given each call, when whoever serves it asks
    # A signed-in user -> the Warcraft Logs ids of their guilds: whose saved raid notes and plans they may read
    guild_ids: Callable[[str], set[int]] = lambda subject: set()
    # A signed-in user -> their Warcraft Logs token, which their calls read with (wcl.reading_as), or None
    wcl_token: Callable[[str], str | None] = lambda subject: None


@dataclass
class Setup:
    """How one server's tools run: the reports they read, and whom they serve. `register` gives a server its tools
    with one; the stdio and in-process server (`mcp`) has DEFAULT, remote.py's server one of its own."""
    library: Library | None = None  # made on first use (`configure` sets DEFAULT's)
    remote: Remote | None = None  # serving many signed-in users
    # Who saves a pasted raid note or plan, for which guild: (how the saved version names them, the guild's Warcraft
    # Logs id), or a FactError saying why they can't. Set by `allow_saving`, on a server that knows who's asking.
    officer: Callable[[], tuple[str, int]] | None = None


DEFAULT = Setup()
_current: ContextVar[Setup] = ContextVar("setup", default=DEFAULT)  # the Setup of the tool call being run
log = logging.getLogger("wipebot.calls")


def setup() -> Setup:
    """The Setup of the tool call being run; DEFAULT outside one."""
    return _current.get()


def _log_call(remote: Remote, tool: str, args: dict) -> tuple[str | None, str | None]:
    """Who called which tool, while serving many users; returns the token's (subject, client)."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    who = remote.user_name(token.subject) if token and token.subject else "someone"
    log.info("%s: %s %s", who, tool, args)
    return (token.subject, token.client_id) if token else (None, None)


def _text(result: object) -> str:
    """What a tool returned, as text: images are left out."""
    if isinstance(result, CallToolResult):
        return "".join(c.text for c in result.content if isinstance(c, TextContent))
    return result if isinstance(result, str) else repr(result)


def configure(default_report: str | None = None) -> Library:
    """Set up the reports the in-process tools read (DEFAULT's): a host calls this once, with its default report."""
    DEFAULT.library = Library(default_report)
    return DEFAULT.library


def lib() -> Library:
    """The reports of the tool call being run."""
    s = setup()
    if s.library is None:
        s.library = Library()
    return s.library


PULL_WAIT_S = 20  # a live log uploads a pull some seconds after it ends: wait this long, then read it again,
PULL_WAITS = 2    # this many times, before telling the model it isn't there yet


_TOOLS: list[tuple[str, Callable[..., Any]]] = []  # (name, function), in the order they're defined
_PROMPTS: list[Callable[..., str]] = []


def _tool(fn):
    """A tool every server gets (`register`)."""
    _TOOLS.append((fn.__name__, fn))
    return fn


def _prompt(fn):
    _PROMPTS.append(fn)
    return fn


def guarded(fn, setup: Setup = DEFAULT):
    """`fn` as a tool of a server with `setup`. Bad arguments and source failures reach the model as readable tool
    errors. A question about the pull that just ended, before the live log has it, waits for it a little. Serving
    many users, each call is logged with its user, kept (Remote.record) and read with their Warcraft Logs token
    (Remote.wcl_token)."""
    async def run(*args, **kwargs):
        for waited in range(PULL_WAITS + 1):
            try:
                return await fn(*args, **kwargs)
            except PullNotLogged as e:
                if waited == PULL_WAITS:
                    raise ToolError(f"{e}; ask again in a minute") from e
                await asyncio.sleep(PULL_WAIT_S)
                await e.report.refresh()
            except (FactError, KnowledgeError, WCLError) as e:
                raise ToolError(str(e)) from e
            except Exception as e:
                # The MCP server would hide an unexpected error as "Error executing tool X": the model and the
                # records need to know what it was. Serving many users, they get only an id the log has it under.
                if setup.remote:
                    error_id = secrets.token_hex(4)
                    _log.exception("%s failed (error id %s)", fn.__name__, error_id)
                    raise ToolError(f"{fn.__name__} failed with an unexpected error (error id {error_id})") from e
                _log.exception("%s failed", fn.__name__)
                raise ToolError(f"{fn.__name__} failed with an unexpected error: {type(e).__name__}: {e}") from e
        raise AssertionError("unreachable")

    async def kept(remote: Remote, *args, **kwargs):
        shown = {k: v for k, v in kwargs.items() if v is not None and k != "ctx"}
        user, client = _log_call(remote, fn.__name__, shown)
        started, began, failed, out = time.time(), time.monotonic(), True, ""
        points = wcl.Points()
        try:
            # the user's own points pay first; what the call cost is kept with it
            with wcl.reading_as(remote.wcl_token(user) if user else None), wcl.counting_points() as points:
                result = await run(*args, **kwargs)
            failed, out = False, _text(result)
            return result
        except ToolError as e:
            cause = e.__cause__
            unexpected = cause and not isinstance(cause, (PullNotLogged, FactError, KnowledgeError, WCLError))
            out = f"{e}: {type(cause).__name__}: {cause}" if unexpected else str(e)  # the records keep what it was
            raise
        except Exception as e:
            out = repr(e)
            raise
        finally:
            if remote.record:
                remote.record(user, client, fn.__name__, shown, started, time.monotonic() - began, failed, out,
                              points.record())

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        token = _current.set(setup)
        try:
            return await (kept(setup.remote, *args, **kwargs) if setup.remote else run(*args, **kwargs))
        finally:
            _current.reset(token)
    return wrapper


Report_ = Annotated[str | None, Field(description="Warcraft Logs report code or URL; omit for the current report")]
Pull = Annotated[int | str | None, Field(description="pull number on the boss, or kill, best wipe, last wipe, first "
                                                    "or last; omit for the latest")]
Boss = Annotated[str | None, Field(description="boss name, or part of it; omit for the boss of the latest pull")]
Player = Annotated[str, Field(description="character name as in the log (accents optional)")]
WholePull = Annotated[bool, Field(description="include what happened after the wipe was decided (the boss's "
                                              "enrage or half the raid dead)")]


# ---------- Warcraft Logs access and reports ----------

@_tool
async def find_reports(guild: Annotated[str | None, Field(description=(
                           "guild name; omit for the user's guilds when they're signed in, else the current "
                           "report's guild. The current report's guild needs no server or region"))] = None,
                       server: Annotated[str | None, Field(description=(
                           "realm, e.g. Argent Dawn: the guild's, or with `player` alone the character's (omit for a "
                           "player in the report)"))] = None,
                       region: Annotated[str | None, Field(description="eu, us, kr, tw or cn")] = None,
                       days: Annotated[int, Field(description="how far back to look; a guild's logs go back "
                                                              "months, so look further for older kills")] = 14,
                       player: Annotated[str | None, Field(description=(
                           "a character: with a guild, that guild's reports they're in; alone, every report they're "
                           "in, whoever logged it"))] = None,
                       report: Report_ = None) -> str:
    """Find Warcraft Logs reports: a guild's raid nights, a player's pugs and alts, last week's log. Each report's
    code, title, date, pulls by difficulty, and each boss with its pulls and kills, over the last `days` days,
    newest first; pass a code as `report` to the other tools.
    For a guild: a report most of whose players aren't in the guild is marked (a raider's pug or another group
    logged to the guild's page). With `player` alone: every report the character is in, whoever logged it (the
    guild's nights, pugs, other guilds' runs), with the guild it was logged to."""
    since = (clock.now() - days * 86400) * 1000
    if player and not guild:  # every report the character is in
        if not server:
            try:
                p = (await lib().report(report)).player(player)
            except FactError:
                raise FactError(f"{player} isn't in the report: give their realm and region (a guildmate's is most "
                                 f"likely the guild's)") from None
            player, server, region = p.name, p.server, p.region
        if not (server and region):
            raise FactError(f"give {player}'s region (eu, us, kr, tw or cn) with the realm")
        return await guild_logs.character_reports(lib().wcl, player, server, region, days, since)
    remote, current = setup().remote, None
    if guild and not (server and region):
        current = await _report_guild(report)
    elif not (guild or remote or wcl.saved_login()):  # no sign-in to know the user's guilds: the report's
        if not (current := await _report_guild(report)):
            raise FactError("give a guild, with its server and region")
        guild = current[1]
    return await guild_logs.guild_reports(lib().wcl, days, since, guild, server, region, player,
                                          remote.guilds() if remote else None, current)


async def _report_guild(report: str | None) -> tuple[int, str] | None:
    """The guild (id, name) of `report`, else of the current report; None without one, or for a personal log."""
    try:
        rep = await lib().report(report)
    except FactError:
        return None
    return (rep.guild_id, rep.guild_name) if rep.guild_id and rep.guild_name else None


@_tool
async def roster(weeks: Annotated[int | None, Field(description=(
                     "attendance over this many weeks back instead (1-26)"), ge=1, le=26)] = None,
                 report: Report_ = None) -> str:
    """Who was in the raid: every player in the report with class, spec and role (to work out which character a
    person is). With `weeks`, attendance: which raiders came on which of the guild's raid nights in this raid over
    the last weeks ("who's been raiding?", "how often does X come?"; Warcraft Logs' attendance, two logs of one
    night count once), with each night's reports."""
    rep = await lib().report(report)
    if weeks:
        await lib().wcl.ensure_points(ATTENDANCE_WEEK_POINTS * weeks, "attendance over weeks")
    return await (night.attendance(rep, weeks) if weeks else night.roster(rep))


# ---------- one pull ----------

@_tool
async def pull_summary(pull: Pull = None, boss: Boss = None, report: Report_ = None) -> str:
    """For "why did we wipe?", "who wiped us?", "what do we fix?": start here.
    One pull: its post, how it ended, phases, raid size, every death in order with spec, killing ability [id] and
    players still alive, when half the raid was dead, and any buff the boss gained and kept to the end, such as
    an enrage.
    Reading it: "Ended by" is computed from the log alone (one ability killing many within 2 s or in pulses within
    6 s, a buff the boss gained and kept, half the raid dead, or deaths spread out, from the first that stayed dead),
    with the deaths one after another that led into a moment killing many; what that ability or buff is, boss_guide
    says.
    Deaths after the wipe was decided are marked as fallout of it. "P1 (2nd time)" is a phase the fight went back
    to after an intermission. The post compares the wipe with this log's earlier pulls and with the guild's best
    in its earlier logs: only "new guild best" is a best ever. "No killing blow recorded" means the log names no
    killing hit (death_recap shows the damage before it and where the player was last)."""
    return await fight.pull_summary(await lib().report(report), pull, boss)


@_tool
async def pull_timeline(pull: Pull = None, boss: Boss = None,
                        start: Annotated[str | None, Field(description="window start, m:ss; omit for the pull "
                                                                       "start")] = None,
                        end: Annotated[str | None, Field(description="window end, m:ss; omit for the pull end")] = None,
                        report: Report_ = None) -> str:
    """For "what happened at 2:31?", "when did we lust?", "where did the raid cooldowns go?" (start/end for a
    window).
    One pull in time order: phases, the boss's casts (◆ marks the ones Lorrgs tracks: the casts to line pulls up
    by) and buffs (enrage), the adds' casts and whether each was kicked (by whom) or went through, its debuffs on
    players and how many got each (per hit when one cast lands on several groups), a stacking debuff reaching its
    highest stacks, deaths with players alive, Bloodlust, battle res and self-res, raid cooldowns; then the boss's
    health and power bar (energy, rage) and raid damage taken over time. Shows what the boss did and what state it
    was in, which deaths alone don't."""
    return await fight.pull_timeline(await lib().report(report), pull, boss, start, end)


@_tool
async def death_recap(player: Player, pull: Pull = None, boss: Boss = None,
                      death: Annotated[int, Field(description="which death of this player in the pull")] = 1,
                      report: Report_ = None) -> str:
    """For "what killed X?", "could they have lived?": the first deaths of a wipe.
    One death in detail: every damage taken event in the 10 s before it with health after each hit, the
    killing blow, the healing they received and what each healer cast in the last 5 s (on them or others, no
    cast, dead), the player's defensives and whether each was ready, healthstone and potion, and other deaths
    close by.
    Reading it: "ready" is off cooldown, not that it was theirs to press then: the raid note (raid_note) plans
    defensives and external cooldowns; whether the hit could be avoided, boss_guide says.
    Not for everyone's defensives and healthstones over a boss's pulls (defensives).
    Example: player="Scopidruid", pull=42."""
    return await deaths.death_recap(await lib().report(report), player, pull, boss, death)


@_tool
async def mechanic_hits(ability: Annotated[str | None, Field(description=(
                            "ability name or id ([id] in pull_summary), e.g. 'Tempest'; omit for who took far more "
                            "than the rest of every ability"))] = None,
                        pull: Pull = None, boss: Boss = None,
                        all_pulls: Annotated[bool, Field(description="every pull on the boss")] = False,
                        whole_pull: WholePull = False,
                        start: Annotated[str | None, Field(description="with `ability`: only from this m:ss of each "
                                                                        "pull (\"the first orb set\")")] = None,
                        end: Annotated[str | None, Field(description="with `ability`: only until this m:ss of each "
                                                                      "pull")] = None,
                        report: Report_ = None) -> str:
    """For "who stood in it?", "who missed the soak?", "who keeps getting hit by X?".
    Who got hit by a boss ability or its debuff: for one pull or every pull on the boss, each until its wipe was
    decided.
    With `ability`: per player its hits, damage, debuff applications, deaths soon after a hit, and deaths it dealt
    the killing blow; then each time the debuff went on a player (pull, time, how long they kept it), when few
    did, or for a debuff one player has most of the time ("who took the bait?"), each time anyone else got it and
    whether that player was alive. Without: every enemy ability that hurt the raid, how many took it, the median
    damage per minute alive, and who took twice the median or more (the mechanics people keep failing, and who).
    Not for the raid's damage taken in totals by ability (breakdown), nor whose job a soak was (raid_note).
    Example: ability="Tempest", all_pulls=true."""
    rep = await lib().report(report)
    if ability:
        return await mechanics.mechanic_hits(rep, ability, pull, boss, all_pulls, whole_pull, start, end)
    return await mechanics.mechanic_outliers(rep, pull, boss, all_pulls)


@_tool
async def pull_map(pull: Pull = None, boss: Boss = None,
                   at: Annotated[str | None, Field(description=(
                       "m:ss from the pull start, or an ability (name or id) for every time it happened; omit for "
                       "the first death of `players`, else the pull's first death"))] = None,
                   players: Annotated[list[str] | None, Field(description=(
                       "players to make stand out, e.g. the one asked about (everyone is named anyway); with "
                       "all_pulls, the first one's position at every moment"))] = None,
                   event: Annotated[Literal["ends", "applied", "cast", "hit"] | None, Field(description=(
                       "with an ability: which of its moments (a debuff ending or being applied, the boss's cast, "
                       "its hits); omit for a debuff ending, else the cast, else the hits"))] = None,
                   all_pulls: Annotated[bool, Field(description="with an ability: every pull on the boss")] = False,
                   report: Report_ = None, *, ctx: Context) -> CallToolResult:
    """For "where did we stand when X?", "who exploded on whom?", "were we spread for X?", "who stood in front of
    the boss?", "where was everyone when X died?".
    Where everyone stood at a moment of a pull, drawn on the boss room's map (a picture the user sees), with each
    player's distance to the boss and degrees off its facing. The moment is
    a time, a death, or each time an ability happened: who it was on or hit, and for a debuff, who was hit by it
    without having it and from whose (the nearest player with it), and the two with it who stood closest. With
    all_pulls, those counted per player and per pair over every pull on the boss, and for a player in `players`,
    each of their moments and a map of where they stood every time. With a raid plan, raid_plan checks spots.
    Example: at="Virulence", all_pulls=true, players=["Martin"]."""
    rep = await lib().report(report)
    # Serving many users, the host may show the replay viewer (remote.py) instead of the picture: it reads which pull
    # and moment from structuredContent. Whether it does is the host's call, and an older protocol version may hide
    # the host's capabilities from us, so the picture is left out only when the host surely shows the viewer.
    serving_many = setup().remote is not None
    viewer = serving_many and _supports_apps(ctx) and not all_pulls
    ans = await maps.pull_map(rep, pull, boss, at, players, event, all_pulls, draw_it=not viewer)
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=ans.text)]
    if ans.image:
        content.append(ImageContent(type="image", data=base64.b64encode(ans.image).decode(), mime_type="image/webp"))
    if not serving_many:
        return CallToolResult(content=content)
    f = rep.fight(pull, boss)
    return CallToolResult(content=content, structured_content={
        "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f), "at_ms": round(ans.at_ms),
        "players": players or []})


def _supports_apps(ctx: Context) -> bool:
    from mcp.server.apps import client_supports_apps
    try:
        return client_supports_apps(ctx)
    except ValueError:  # an in-process call (the Discord bot, `wipebot call`) has no client
        return False


AGGRO_WEEKS = 4  # tank_swaps: the guild's raid nights it looks through for the spells each tank took a boss with
# What the opt-in reads of many nights or pulls cost in Warcraft Logs points (measured on a Mythic night of 24 pulls)
TAUNTS_POINTS, ATTENDANCE_WEEK_POINTS, HEALING_PULL_POINTS = 500, 40, 8
DEATHS_NIGHT_POINTS = 35  # player_history's deaths: a night read cold (7 nights took 240)


@_tool
async def tank_swaps(pull: Pull = None, boss: Boss = None,
                     taunts: Annotated[bool, Field(description=(
                         "also the spells each tank has taken a boss's aggro with on the guild's recent raid nights: "
                         "only for whether a spell taunts or what a tank could have taunted with; slow (reads "
                         "every night, about 500 Warcraft Logs points)"))] = False,
                     report: Report_ = None) -> str:
    """For "did the tanks swap?", "why did a tank die?".
    Tank swaps on one pull: who had the boss's aggro when and what took it (usually a taunt), each tank's
    stacks of the boss's debuffs over time (one stint per application), and tank deaths. With `taunts`, also the
    spells each tank has taken a boss's aggro with on the guild's recent raid nights: what their kit can taunt
    with, as the log shows it.
    Reading it: it shows when each tank taunted and their stacks then, not the swap plan (the stacks to swap at,
    whose call it is); the raid note (raid_note) holds that."""
    rep = await lib().report(report)
    text = await tanks.tank_swaps(rep, pull, boss)
    names = [p.name for p in tanks.pull_tanks(rep, rep.fight(pull, boss))]
    if not (taunts and names):
        return text
    await lib().wcl.ensure_points(TAUNTS_POINTS, "taunts")
    try:
        nights, _, _ = await night.raid_nights(rep, AGGRO_WEEKS)
        nights = [n for n in nights if n["start"] <= rep.end_ms]  # up to this report
    except FactError:  # a personal log, no guild: this report alone
        nights = []
    loaded = await asyncio.gather(*(lib().night(n, max_age_s=float("inf")) for n in nights))
    reports = [rep, *(r for night_ in loaded for r in night_ if r.code != rep.code)]
    return text + "\n" + await tanks.aggro_history(reports, names, AGGRO_WEEKS)


@_tool
async def compare_pulls(pull: Pull = None, boss: Boss = None,
                        other: Annotated[int | str | None, Field(description="another pull of ours on the boss "
                                                                             "to set side by side (a number, or "
                                                                             "kill, best wipe, ...); omit to compare "
                                                                             "with a kill")] = None,
                        kill: Annotated[str | None, Field(description="the kill to compare with, as a Warcraft Logs "
                                                                      "link with #fight=N; omit for the one with "
                                                                      "the closest item level among the 50 fastest "
                                                                      "kills of the same difficulty")] = None,
                        report: Report_ = None) -> str:
    """Compare a pull with a kill or with another of our pulls: do we need more damage, was a mechanic early or
    late, what changed between two pulls, what went right on the kill.
    Against a kill of the same boss and difficulty (by default, of the 50 fastest, the one with the closest item
    level to ours): phase changes, when the boss's power bar was reset, how far behind the kill's pace (boss
    health at the same moment) the pull was and since when, with deaths on each side, and boss health and power
    at the same moments. With `other`: both pulls' posts, every killing ability before and after the wipe was
    decided, and the most damage taken."""
    rep = await lib().report(report)
    if other is not None:
        return await compare.compare_pulls(rep, pull, other, boss)
    return await compare.compare_with_kill(rep, pull, boss, kill)


@_tool
async def enemies(pull: Pull = None, boss: Boss = None,
                  all_pulls: Annotated[bool, Field(description="every pull on the boss")] = False,
                  report: Report_ = None) -> str:
    """For "were the adds killed in time?", "were their casts kicked?", "did anyone have a kick ready?".
    The adds in a pull: how many of each came, when they died, what became of the rest (alive at the end, or
    gone without a death: despawned, transformed or removed), the damage they took and who did it, and their
    casts with a cast bar: begun, kicked and by whom, gone through, and for each that went through who was alive
    with their interrupt ready. With all_pulls, over the night: per kind how many came and died and the damage
    they took, their casts and who kicked them, then each pull in a line.
    Reading it: adds with no death recorded can't show when or in what order they were killed.
    Not for the raid's kicks and dispels in totals per player (breakdown)."""
    rep = await lib().report(report)
    if all_pulls:
        return await adds.enemies_night(rep, boss)
    return await adds.enemies(rep, pull, boss)


# Said with every "no note or plan": what the model is tempted to put in its place.
NO_JOBS = (" Without one, who was assigned to what isn't in the log: who did it earlier in the pull or night isn't an "
           "assignment (raids swap spots and keep reserves).")


def _saved_guild(rep: Report, what: str) -> int:
    """The report's guild, whose saved `what` the user may read; else a FactError that says to paste it."""
    if not rep.guild_id:
        raise FactError(f"no {what} was given, and this report is a personal log, not a guild's: ask the user to "
                        f"paste the {what}." + NO_JOBS)
    remote = setup().remote
    if remote:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if not (token and token.subject and rep.guild_id in remote.guild_ids(token.subject)):
            raise FactError(f"no {what} was given, and the ones {rep.guild_name} saved are for its members only: "
                            f"ask the user to paste the {what}." + NO_JOBS)
    return rep.guild_id


NIGHT_APART_S = 12 * 3600  # a log begun this long before another is another raid night; a note saved this long after
# the night's log ended is for the next one


async def note_night(rep: Report, f: dict, saved_at: list[float]) -> tuple[float, float]:
    """The times (epoch seconds, from and until) a raid note for pull `f`'s night was saved in, as officers change
    the note each night with who comes: from the end of the guild's last raid night on the boss (a log with pulls on
    it at the pull's difficulty), or the version saved last before the report if that's later, until NIGHT_APART_S
    after the report ends. Asks Warcraft Logs only when that version is from a day before."""
    start, until = rep.start_ms / 1000, rep.end_ms / 1000 + NIGHT_APART_S
    since = max((t for t in saved_at if t <= start), default=0.0)
    if not since or since > start - NIGHT_APART_S:
        return since, until
    try:  # from a night before: a version saved during a night's raid, after its log began
        logs = await rep.wcl.guild_reports(rep.guild_id or 0, (since - NIGHT_APART_S) * 1000, pulls=True,
                                           cache_for=wcl.GUILD_REPORTS_S)
    except WCLError:
        return since, until
    ended = [r["endTime"] / 1000 for r in logs if f["name"] in (r.get("bosses") or {})
             and (r.get("pulls_by_difficulty") or {}).get(f.get("difficulty"))
             and r["startTime"] / 1000 <= start - NIGHT_APART_S]
    return max([since, *ended]), until


async def saved_note(book: notebook.Notebook, guild: int, rep: Report, f: dict,
                     all_pulls: bool = False) -> tuple[str, str] | None:
    """The raid note `guild` saved for pull `f`'s boss as it was when the pull began, and where it's from; with
    `all_pulls`, every version saved for the night after its mark (saved.py), each pull checked against the one
    saved before it began. Only versions saved for the pull's night (note_night). None when there's none."""
    start = (rep.start_ms + f["startTime"]) / 1000
    every = book.notes(guild, f["encounterID"])
    since, until = await note_night(rep, f, [n.saved_at for n in every])
    if all_pulls and (every := [n for n in every if since <= n.saved_at < until]):
        return "\n".join(f"{saved.mark(n.saved_at, n.saved_by)}\n{n.note}" for n in every), ""
    found = book.note_for(guild, f["encounterID"], start, (since, until))
    return (found.note, found.source(start)) if found else None


def saved_plans(book: notebook.Notebook, guild: int, rep: Report, f: dict) -> list[str]:
    """Every version of the raid plans `guild` saved for pull `f`'s boss, each after its mark (saved.py)."""
    key = rep.boss_key(f)

    def same(name: str) -> bool:
        try:
            return rep.boss(name) == key
        except FactError:
            return False
    return [f"{saved.mark(p.saved_at, p.saved_by)} {p.plan}" for p in book.plans(guild) if same(p.boss)]


FromPull = tuple[Report, int]  # a report of the guild, and the pull on the boss a paste counts from


def save_pasted(book: notebook.Notebook, guild_id: int, text: str, saved_by: str, at: float | None = None,
                from_pull: FromPull | None = None) -> str:
    """A pasted raid note (each boss's part) or raid plan (each plan in it) saved for the guild (as saved `at`,
    default now, or with `from_pull` as saved just before that pull on its boss began: one pasted after the pulls it
    was for); what was saved, for the officer. A paste that's neither raises FactError."""
    if plans.EXPORT.search(text) and not notes.HEADER.search(text):
        return _save_plans(book, guild_id, text, saved_by, at, from_pull)
    return _save_notes(book, guild_id, text, saved_by, at, from_pull)


def _pull_start(guild_id: int, from_pull: FromPull | None, at: float | None, encounter_id: int | None = None,
                boss: str = "") -> tuple[float | None, str]:
    """When a paste counts as saved: `at`, or, counting from a pull, a second before that pull on its boss began (a
    version saved as a pull begins is that pull's); and the pull, for the officer ("" without one). The boss is the
    note's encounter or the plan's boss name."""
    if from_pull is None:
        return at, ""
    rep, pull = from_pull
    if rep.guild_id != guild_id:
        raise FactError(f"report {rep.code} isn't the guild's: name a pull in one of the guild's reports")
    if encounter_id is not None:
        keys = [k for k, fs in rep.by_boss.items() if fs[0]["encounterID"] == encounter_id]
        if not keys:
            raise FactError(f"report {rep.code} has no pull on {boss}")
        key = max(keys, key=lambda k: rep.by_boss[k][-1]["startTime"])
    elif boss:
        key = rep.boss(boss)
    else:
        raise FactError("the plan names no boss, so it can't count from a pull: save it without one")
    pulls = rep.by_boss[key]
    if not 1 <= pull <= len(pulls):
        raise FactError(f"{key} has pulls 1-{len(pulls)} in report {rep.code}")
    return (rep.start_ms + pulls[pull - 1]["startTime"]) / 1000 - 1, f"{key} pull {pull} of {day(rep.start_ms)}"


def _saved_for(what: str, kept: str, starts: list[str]) -> str:
    """The first line of what a paste saved: for pulls from now on, or from the pull it counts from."""
    if not any(starts):
        return (f"Saved the {what}, for checking pulls against from now on ({kept}; pasted after the pulls it was "
                'for? Paste it again with the pull it counts from, such as "from pull 1"):')
    return f"Saved the {what}, for checking pulls against from {', '.join(dict.fromkeys(starts))} on ({kept}):"


def _save_plans(book: notebook.Notebook, guild_id: int, text: str, saved_by: str, at: float | None,
                from_pull: FromPull | None) -> str:
    """A plan saved before gets a new version."""
    parsed = plans.parse(text)
    known = {p.plan_id for p in book.plans(guild_id)}
    when = [_pull_start(guild_id, from_pull, at, boss=p.boss) for p in parsed]  # all checked before any is saved
    lines = []
    for p, (saved_at, _) in zip(parsed, when, strict=True):
        book.save_plan(guild_id, p.boss, p.plan_id, p.name, p.export, saved_by, saved_at)
        spots = [s for sc in p.scenes for s in sc.spots]
        named = [s.label for s in spots if s.label]
        aside = [n for sc in p.scenes for n in sc.aside]
        lines.append(f"**{p.name}**" + (f" ({p.boss})" if p.boss else "")
                     + (" (a new version)" if p.plan_id in known else "")
                     + f": {plural(len(named), 'spot')} ({', '.join(named)})"
                     + (f", {len(spots) - len(named)} open" if len(spots) > len(named) else "")
                     + (f"; side panel: {', '.join(aside)}" if aside else ""))
    return (_saved_for("raid plan", "an earlier version is kept for earlier pulls", [s for _, s in when]) + "\n"
            + "\n".join(f"- {line}" for line in lines))


def _save_notes(book: notebook.Notebook, guild_id: int, text: str, saved_by: str, at: float | None,
                from_pull: FromPull | None) -> str:
    parsed = [(n, n.boss or f"encounter {n.encounter_id}") for n in notes.parse(text)]
    when = [_pull_start(guild_id, from_pull, at, n.encounter_id, boss) for n, boss in parsed]
    lines = []
    for (n, boss), (saved_at, _) in zip(parsed, when, strict=True):
        book.save_note(guild_id, n.encounter_id, boss, n.text, saved_by, saved_at)
        groups = "; ".join(f"{heading} ({', '.join(labels)})" for heading, labels in n.groups.items())
        lines.append(f"**{boss}**" + (f" ({n.difficulty})" if n.difficulty else "")
                     + f": {plural(len(n.cooldowns), 'timed cooldown')}, {plural(len(n.callouts), 'callout')}"
                     + (f"; {groups}" if groups else ""))
    kept = "that night's only, as a note changes with who comes: the previous one is kept for earlier pulls"
    return (_saved_for("raid note", kept, [s for _, s in when]) + "\n"
            + "\n".join(f"- {line}" for line in lines))


OTHER_NIGHTS = (" (only for other nights: a note changes with who comes, so another night's isn't checked against "
                "this one)")


async def _saved_note(rep: Report, pull: PullRef, boss: str | None, all_pulls: bool) -> tuple[str, str]:
    """The note the report's guild saved for the pull's boss, and where it's from (`saved_note`)."""
    guild = _saved_guild(rep, "raid note")
    f = rep.fight(pull, boss)
    book = notebook.Notebook(readonly=True)
    if (found := await saved_note(book, guild, rep, f, all_pulls)) is None:
        other = OTHER_NIGHTS if book.notes(guild, f["encounterID"]) else ""
        raise FactError(f"no raid note was given, and {rep.guild_name} has none saved for {f['name']} that "
                        f"night{other}: ask the user to paste the boss's note (the NSRT note from Viserio)." + NO_JOBS)
    return found


async def _saved_plans(rep: Report, pull: PullRef, boss: str | None) -> tuple[str, str]:
    """Every version of the plans the report's guild saved for the pull's boss, fetched from raidstrats.gg first
    when the guild has a login there, and where they're from ("" without a login: each version's text says)."""
    guild = _saved_guild(rep, "raid plan")
    f = rep.fight(pull, boss)
    if notebook.Notebook(readonly=True).login(guild):
        await raidstrats.sync(notebook.Notebook(), guild)
    book = notebook.Notebook(readonly=True)
    login = book.login(guild)
    if not (found := saved_plans(book, guild, rep, f)):
        raise FactError(f"no raid plan was given, and {rep.guild_name} has none saved for {rep.boss_key(f)}: ask the "
                        "user to paste the plan's export from raidstrats.gg." + NO_JOBS)
    return "\n".join(found), raidstrats.source(login) if login else ""


@_tool
async def raid_note(note: Annotated[str | None, Field(description=(
                        "the raid note as the user pasted it (the NSRT note from Viserio, EncounterID line "
                        "included); omit for the one the report's guild saved"))] = None,
                    pull: Pull = None, boss: Boss = None,
                    all_pulls: Annotated[bool, Field(description="every pull on the boss, counted per player")] = False,
                    report: Report_ = None) -> str:
    """For "did people do their jobs?", "who missed their soak?", "were the cooldowns on time?", "who didn't kick
    as assigned?", and over the night "who missed their soak most?", "who pressed personals when the note said?".
    The raid's assignment and cooldown note checked against a pull: the assignments (soak groups, spreads,
    single-player jobs), each soak's hits against its group (who of the group wasn't hit, alive or dead, who
    else was), each planned cooldown against when the player cast it (on time, early, late, not cast), each kick
    assignment (a callout to one player as an add begins a cast) against who kicked that cast, and each
    other callout ("Big Personals" to everyone, a job for one player) with what the players it names pressed
    around it and what they had ready.
    Reading it: a callout's tracked spells carry Lorrgs' tags (defensive, raid_cd, tank, move, dps, untagged);
    which answer the callout is in its words ("personals": their defensives, a tank's tank cooldowns, untagged
    ones such as Anti-Magic Shell when they are one). Spreads are listed, not checked: raid_plan checks positions.
    With all_pulls, one call checks every pull on the boss (each against the note saved before it began) and counts
    per player: soaks of their group missed while alive, each planned cooldown on time, early, late or not cast,
    kicks made and missed, and per callout how often they pressed something and what was ready when they didn't;
    a night's question needs no call per pull."""
    rep, source = await lib().report(report), ""
    if not note:
        note, source = await _saved_note(rep, pull, boss, all_pulls)
    return await notes.note_check(rep, note, pull, boss, source=source, all_pulls=all_pulls)


@_tool
async def raid_plan(plan: Annotated[str | None, Field(description=(
                        "the raid plan as the user pasted it: raidstrats.gg's export, the text starting "
                        "raidstrats-addon-; omit for the ones the report's guild saved"))] = None,
                    pull: Pull = None, boss: Boss = None,
                    all_pulls: Annotated[bool, Field(description="every pull on the boss")] = False,
                    at: Annotated[str | None, Field(description=(
                        "the ability to check at, when the plan draws several or none; or, on one pull, the moment "
                        "to draw (m:ss); omit for the plan's ability and the moment with most players off their "
                        "spot"))] = None,
                    player: Annotated[str | None, Field(description="a player to draw: with all_pulls, where they "
                                                                    "stood at every moment against their spot")] = None,
                    latest: Annotated[bool, Field(description=(
                        "check against the newest version of the plan, not the one each pull was planned "
                        "with"))] = False,
                    report: Report_ = None, *, ctx: Context) -> CallToolResult:
    """For "was everyone on their spot?", "who stood in the wrong place for the spread?", "show me where people
    stood against the plan".
    The raid's positioning plan from raidstrats.gg against where each player stood at each moment of its
    mechanic (the moments pull_map finds: a debuff running out, a boss cast): per moment, who was off their spot by
    how far and whose spot they stood at, who was dead, and who was hit by the debuff from whose; with all_pulls,
    per player how often they were off and where. Draws a map the user sees: for one pull, the plan's spots laid
    over where everyone stood at one moment, with lines to those off their spot; with all_pulls and a player, that
    player at every moment against their spot.
    Reading it: the plan is laid over where the raid stood each time, so a player is off relative to the rest of
    the raid, and the whole raid standing elsewhere in the room isn't. A plan's side panel (players who stay
    out, notes) isn't in the room. A pull is checked against the version of the plan saved last before it began
    (with `latest`, the newest); the output names a newer version when one is saved."""
    rep, source = await lib().report(report), ""
    if not plan:
        plan, source = await _saved_plans(rep, pull, boss)
    # As pull_map: on one pull, a host that shows the replay viewer gets the plan's spots on it, not the picture.
    serving_many = setup().remote is not None
    viewer = serving_many and _supports_apps(ctx) and not all_pulls
    ans = await plans.plan_check(rep, plan, pull, boss, all_pulls, at, player, draw=not viewer, latest=latest)
    text = f"(The plans were {source}.)\n{ans.text}" if source else ans.text
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=text)] + [
        ImageContent(type="image", data=base64.b64encode(i).decode(), mime_type="image/webp") for i in ans.images]
    if not serving_many or ans.fight is None or ans.overlay is None:
        return CallToolResult(content=content)
    f = ans.fight
    return CallToolResult(content=content, structured_content={
        "report": rep.code, "pull": rep.pull_no(f), "boss": rep.boss_key(f), "at_ms": ans.overlay["at_ms"],
        "players": [player] if player else [], "plan": ans.overlay["moments"]})


@_tool
async def raid_prep(pull: Pull = None, boss: Boss = None, report: Report_ = None) -> str:
    """For "is everyone prepared?".
    Every player's preparation on a pull: who had no flask, food, augment rune, vantus rune or weapon oil, and
    whose talents, enchants or gems stray from the top parses of their spec on the boss."""
    return await build.raid_prep(await lib().report(report), pull, boss)


@_tool
async def progress(boss: Annotated[str | None, Field(description=(
                       "boss name, or part of it; omit for the whole night: every boss at a glance, then the latest "
                       "boss's pulls"))] = None,
                   each_pull: Annotated[bool, Field(description="also each pull's post (first deaths, every "
                                                                "killer, the trend)")] = False,
                   first_hits: Annotated[bool, Field(description="also who hit the boss first on each pull, for "
                                                                 "\"who pulls early?\"")] = False,
                   weeks: Annotated[int | None, Field(description=(
                       "also the boss on each of the guild's raid nights over this many weeks back (1-12), for "
                       "\"are we doing better than last week?\": reads every night, slow"), ge=1, le=12)] = None,
                   report: Report_ = None) -> str:
    """How did tonight go, are we improving, what keeps wiping us, who keeps dying first, which pulls were there:
    start here for a night.
    Without a boss, the night at a glance first: every boss with pulls, kills (with speed and execution
    percentiles), best wipe, time spent, the trend, and the guild's progress rank. Then every pull on the boss:
    boss %, phase, duration and first death of each, the guild's best wipe before the night, averages over the
    night, the buffs the boss kept to the end, what killed the first 3 deaths of each pull, and the night's
    killer. With `first_hits`: who hit the boss first on each pull, with what and how far ahead of the next
    player, and a tally ("who pulled?").
    Reading it: deaths after a buff the boss kept to the end are left out of the killers. The night's killer is
    the ability with the most deaths (every death on a kill, a wipe's until it was decided), with the players it
    killed and the players it hit most; a tank tops a "hit most" list by standing at the boss. With `weeks`: each
    night's pulls on the boss, the kill, the best wipe before it and how the pulls ended, then the best wipe night
    by night."""
    rep = await lib().report(report)
    # The overview asks Warcraft Logs other things than the pulls do: both at once. The posts read what the
    # pulls' lines just fetched, so they come after.
    texts = list(await asyncio.gather(*([] if boss else [night.overview(rep)]), night.progress(rep, boss)))
    if each_pull or first_hits:
        texts.append(await night.each_pull(rep, boss, each_pull, first_hits))
    if weeks:
        texts.append(await _boss_over_weeks(rep, boss, weeks))
    return "\n\n".join(texts)


async def _boss_over_weeks(rep: Report, boss: str | None, weeks: int) -> str:
    """The boss of `progress` (on its difficulty in this report) on each of the guild's raid nights over the weeks."""
    f = rep.by_boss[rep.boss(boss)][-1]
    nights, _, _ = await night.raid_nights(rep, weeks)
    found = []
    for n in [n for n in nights if n["start"] <= rep.end_ms]:  # the weeks up to this report's night, not after it
        if (one := await night.boss_night(await lib().night(n), n["start"], f["encounterID"], f.get("difficulty"))):
            found.append(one)
    return night.weeks_text(rep.boss_key(f), weeks, found)


# ---------- players ----------

@_tool
async def player_pull(player: Player, pull: Pull = None, boss: Boss = None, report: Report_ = None) -> str:
    """For "how did I do?", "why is my damage or healing low?": start here for one pull.
    One player on one pull: hero talents, the WCL parse on a kill or, on a wipe, where their throughput per
    second alive falls among the spec's ranked kills; rank in their role, active time, damage by target, deaths
    (before or after the wipe was decided), damage taken, casts per ability, major cooldowns against possible uses
    and the defensives they used, Bloodlust and phases, uptime of their DoTs and own buffs, mana for a healer,
    the resource they spend (a healer's Holy Power or Essence too: time at its cap, and how much each source
    gained and lost there), healthstones and potions, and consumables at the pull.
    Reading it: "Time lost" is seconds until the wipe was decided spent dead (no output at all), in casting gaps
    (no cast begun) or with the resource at its cap (only its gain lost); how much of that the fight forces shows
    against top players of the spec, who meet the same fight (compare_with_top casts). Cooldown uses / possible
    assume pressing on cooldown from the pull until the player died or the wipe was decided. A wipe's percentile
    against kills runs high on a short pull (Bloodlust and every cooldown come at the start); healing on a wipe
    is capped by the damage taken."""
    return await coaching.player_pull(await lib().report(report), player, pull, boss)


async def _parts(parts: dict[str, Awaitable[str]]) -> str:
    """Several answers of one tool run at once, under a heading each when there's more than one; a part that
    fails says why, and the others still come back."""
    async def one(part: Awaitable[str]) -> str:
        try:
            return await part
        except PullNotLogged:
            raise
        except (FactError, KnowledgeError, WCLError) as e:
            return f"Not available: {e}"
    texts = await asyncio.gather(*(one(p) for p in parts.values()))
    if len(texts) == 1:
        return texts[0]
    return "\n\n".join(f"## {name}\n{text}" for name, text in zip(parts, texts, strict=True))


Aspect = Literal["build", "casts", "cooldowns"]
Difficulty = Annotated[Literal["Mythic", "Heroic", "Normal"] | None, Field(
    description="omit for the hardest with ranked kills")]


@_tool
async def compare_with_top(
    player: Annotated[str | None, Field(description=(
        "character name as in the log; omit, with `spec` and `boss`, for what the top players of a spec play, or "
        "with only `boss` for what the fastest kills brought"))] = None,
    aspects: Annotated[list[Aspect] | None, Field(description=(
        "build (talents, gear, stats, consumables), casts (per minute, per spell), cooldowns (when, in one phase); "
        "omit for all three"))] = None,
    pull: Pull = None, boss: Boss = None,
    phase: Annotated[int, Field(description="for cooldowns: the boss phase, 1 for the first")] = 1,
    spec: Annotated[str | None, Field(description="without a player: spec and class, e.g. 'balance druid'")] = None,
    difficulty: Difficulty = None,
    report: Report_ = None,
) -> str:
    """Compare a player with the top players of their spec: is my build right, why is my damage low (after
    player_pull), were my cooldowns on time, how do my stats compare; without a player, what the top players of a
    spec play on a boss ("what are the top balance druids doing on X?", a boss not pulled yet); with only a boss,
    what the fastest kills brought ("is it a 4-heal fight?").
    One player on one pull against top Warcraft Logs parses of their spec on the same boss and difficulty:
    - build: their talents, item level, trinkets, enchants, gems, stats and consumables against the top 50 parses
      (talents against those of their hero tree; stats against the top 10's own logs) and the guides' builds;
    - casts: casts per minute of every spell against the 5 top parses of their spec and hero tree closest in item
      level: spells they cast far less or more often, or never; casting gaps and time at the resource cap;
    - cooldowns: their cooldown casts in one phase against Lorrgs' top parses of the spec on the boss, with when.
    Without a player: the top 50 parses of `spec` on `boss`: hero trees, the talents where they differ, the guides'
    builds against them, trinkets, item level, kill time, links to the top logs and a typical build's import
    string; and their major cooldowns: how many pressed each, and when the top 5 did beside the boss's casts
    ("where do I want my cooldowns?"). With only `boss`: the 50 fastest kills' tanks, healers, melee and ranged,
    how many kills brought each count, kill time and item level by healers brought, and links to the fastest.
    Example: player="Draiman", aspects=["casts", "cooldowns"]; or spec="balance druid", boss="Kith'ix"."""
    if not player:
        if not boss:
            raise FactError("give a player, or a boss (and a spec for what its top players play)")
        if not spec:
            return await top.top_kills(lib().wcl, boss, difficulty)
        name = await top.boss_name(lib().wcl, boss)
        run_top = {"build": lambda: top.top_builds(lib().wcl, spec, boss, difficulty),
                   "cooldowns": lambda: knowledge.top_cooldowns_text(spec, name, difficulty)}
        return await _parts({a.capitalize(): run_top[a]() for a in dict.fromkeys(aspects or ("build", "cooldowns"))
                             if a in run_top})
    rep = await lib().report(report)
    rep.require_in(rep.fight(pull, boss), rep.player(player))  # a wrong pull or name fails the call, not each part
    wanted = list(dict.fromkeys(aspects or ("build", "casts", "cooldowns")))

    async def cooldowns_part() -> str:
        f, p = rep.fight(pull, boss), rep.player(player)
        early = (await cooldowns.prepull(rep, [f], p))[f["id"]]
        return (await knowledge.cooldown_comparison_text(rep.code, f["id"], p.name, phase)
                + f"\n{p.name}'s own buffs already up at the pull (cast or drunk before it, so not in their casts): "
                + (", ".join(early) or "none"))
    run = {"build": lambda: build.compare_build(rep, player, pull, boss),
           "casts": lambda: top.compare_casts(rep, player, pull, boss), "cooldowns": cooldowns_part}
    return await _parts({a.capitalize(): run[a]() for a in wanted})


@_tool
async def player_night(player: Player, boss: Boss = None, report: Report_ = None) -> str:
    """For "how did I do tonight?": start here for a night on one boss.
    One player across every pull on a boss: rank among their role, their best pull, when and to what they died
    (before or after each wipe was decided), time dead before each wipe was decided, and major cooldown use
    against possible uses, per pull and summed over the night; the resource they spend lost at the cap over the
    night, by source; and the trend ("am I getting better?"): deaths and output per second alive over the first,
    middle and last third of the pulls.
    Reading it: a death after a wipe was decided is marked as fallout and left out of the counts. Cooldown uses /
    possible assume pressing on cooldown from the pull until the player died or the wipe was decided."""
    return await coaching.player_night(await lib().report(report), player, boss)


History = Literal["kills", "deaths", "experience"]


@_tool
async def player_history(
    player: Annotated[str | None, Field(description=(
        "character name as in the log; omit for the guild rankings of the report's raiders"))] = None,
    show: Annotated[list[History] | None, Field(description="with a player: kills, deaths and/or experience; omit "
                                                             "for kills")] = None,
    boss: Annotated[str | None, Field(description="one boss of the raid; omit for every boss")] = None,
    weeks: Annotated[int, Field(description="for deaths: how many weeks back", ge=1, le=12)] = 4,
    difficulty: Annotated[Literal["Mythic", "Heroic", "Normal"], Field(
        description="for the guild rankings")] = "Mythic",
    role: Annotated[Literal["dps", "healer", "tank"], Field(
        description="for the guild rankings: dps and tank ranked by damage per second, healer by healing")] = "dps",
    report: Report_ = None,
) -> str:
    """Parses and guild rankings: a player's ranked kills and best parses this season, what they keep dying to
    night after night, and their experience; without a player, the guild rankings of the raiders' best parses.
    With `player`, `show` picks:
    - kills (the default): every ranked kill of their character this season in this report's raid, from any
      report (other nights, pugs, other groups), per boss and difficulty: percentile, date, item level, kill time
      and log link. For earlier kills, progress over the tier, or how they do when they survive;
    - deaths: their deaths before each wipe was decided on each of the guild's raid nights in this raid over the
      last `weeks` (its hardest difficulty): pulls, how often they died early and first, to what, and the
      abilities that come back night after night ("what do I keep dying to?", "what should I work on?");
    - experience: Raider.IO's raid progression in this and earlier raids and their Mythic+ score: tells a new or
      undergeared player from one who plays below their experience.
    Without `player`: the report's raiders of `role` ranked as on the guild's rankings page on Warcraft Logs: each
    one's best kill per boss this season (any report) as a percentile against their spec, the average of those,
    and All Stars points ("who's our best healer?", "who parses best?", "what's X's average parse?")."""
    rep = await lib().report(report)
    if not player:
        return await history.guild_rankings(rep, difficulty, role)
    rep.player(player)  # a wrong name fails the call, not each part

    async def deaths_part() -> str:
        nights, hardest, _ = await night.raid_nights(rep, weeks)
        wanted = name_key(player)
        there = [n for n in nights if any(name_key(name) == wanted and presence == 1
                                          for name, presence in n["players"].items())]
        await lib().wcl.ensure_points(DEATHS_NIGHT_POINTS * len(there), "deaths over weeks")
        loaded = [(n["start"], await lib().night(n)) for n in there]
        return await history.deaths_by_night(loaded, player, hardest, boss)

    async def experience() -> str:
        p = rep.player(player)
        if not p.server:
            raise FactError(f"the log doesn't give {p.name}'s realm")
        return await knowledge.character_experience_text(p.name, p.server, p.region or "us")
    run = {"kills": lambda: history.ranked_kills(rep, player, boss), "deaths": deaths_part,
           "experience": experience}
    return await _parts({s.capitalize(): run[s]() for s in dict.fromkeys(show or ("kills",))})


@_tool
async def defensives(boss: Boss = None, whole_pull: WholePull = False,
                     player: Annotated[str | None, Field(description="one player: each of their deaths, replayed "
                                                                     "with what was ready")] = None,
                     report: Report_ = None) -> str:
    """For "who used healthstones and defensives?", "would a personal have saved me?".
    Everyone's healthstones and healing potions and personal defensives over a boss's pulls, in one table:
    uses per player, deaths, and at how many deaths each defensive was ready but unused. With `player`: each of
    their deaths, replayed with a ready defensive or a healthstone.
    Not for one death in detail, the hits and the healing before it (death_recap)."""
    return await deaths.defensives(await lib().report(report), boss, whole_pull, player)


@_tool
async def healing(pull: Pull = None, boss: Boss = None,
                  all_pulls: Annotated[bool, Field(description="every pull on the boss")] = False,
                  report: Report_ = None) -> str:
    """For "did the healers keep up?", "when do we take the most damage?", "are our healing cooldowns on the
    peaks?", "who was idle when it hit?".
    The raid's damage spikes on a pull, until the wipe was decided: each spike's time, length, damage taken per
    second and the abilities behind it, the raid's healing as a share of it, each healer's healing per second
    and overhealing in it, time with no cast begun or dead, the healing cooldowns used in it or ready and unused,
    and deaths in it; then each healer in the spikes against the rest of the time. With all_pulls, each pull's
    biggest spikes in a line and the spikes by the ability behind them over the night.
    Reading it: a spike is the raid's damage at 1.5 times the pull's median or more, so a pull's quiet stretches set
    the bar. Ready and unused counts a healer's cooldown only when they press it somewhere in the report;
    cooldowns are often planned for one spike and held through another (the raid note says). Overhealing is
    healing past full health: healing is capped by the damage taken, so a high share where the raid took little
    (heals over time and smart heals landing on players at full health) isn't a fault on its own."""
    rep = await lib().report(report)
    if all_pulls:
        await lib().wcl.ensure_points(HEALING_PULL_POINTS * len(rep.by_boss[rep.boss(boss)]), "all_pulls")
    return await heals.healing(rep, pull, boss, all_pulls)


@_tool
async def buff_at_casts(player: Player,
                        buff: Annotated[str, Field(description="the buff as the log names it, e.g. Starlord")],
                        spell: Annotated[str, Field(description="the spell cast, e.g. Convoke the Spirits")],
                        pull: Pull = None, boss: Boss = None,
                        all_pulls: Annotated[bool, Field(description="every pull on the boss they were in")] = False,
                        report: Report_ = None) -> str:
    """Whether a buff was up on a player, and how many stacks, at each of their casts of a spell on a pull: "did I
    have 2-3 stacks of Starlord when I cast Convoke?". With all_pulls, every pull on the boss in one call, with
    when they first cast the spell on each (median, earliest, latest): "when does my first Arcane Surge go out?".
    The buff may be the spell's own (Arcane Surge and Arcane Surge) when only the cast times matter; Bloodlust
    (or Heroism, lust) is whichever of its kin the raid had (Time Warp, Primal Rage, ...)."""
    return await coaching.buff_at_casts(await lib().report(report), player, buff, spell, pull, boss, all_pulls)


@_tool
async def breakdown(
    data_type: Annotated[tables.BreakdownType, Field(description="the Warcraft Logs table")],
    group_by: tables.GroupBy = "player",
    pulls: Annotated[list[int] | None, Field(description="pull numbers on the boss; omit for all")] = None,
    boss: Boss = None,
    player: Annotated[str | None, Field(description="one player only (for Buffs/Debuffs: auras on them; for "
                                                    "EnemyDebuffs: the ones they applied)")] = None,
    ability: Annotated[str | None, Field(description="one ability or aura only: its name or id ([id] in "
                                                     "pull_summary)")] = None,
    cutoff: Annotated[int | None, Field(description="ignore everything after this many deaths in each pull, "
                                                    "e.g. 3 for the start of the wipe")] = None,
    whole_pull: Annotated[bool, Field(description=(
        "include what happened after each wipe was decided (the boss's enrage or half the raid dead); "
        + ", ".join(tables.UNTIL_DECIDED_TYPES) + " leave it out by default (but with a cutoff), the other "
        "tables are always whole pulls"))] = False,
    report: Report_ = None,
) -> str:
    """For "who did the most damage?", "what hurt us most?", "who kicked or dispelled?", "who played well on the
    kill?".
    Damage, healing, damage taken, casts, buffs, debuffs, interrupts and dispels: any Warcraft Logs table over
    any set of pulls, with totals and per second, grouped by player, ability or pull.
    Buff or debuff uptime is on players; EnemyDebuffs is on enemies (DoTs, a player's with `player`). Interrupts
    and dispels: began, kicked or dispelled and by whom, went through or ran out; per player who kicked what, per
    pull the counts; who was dispelled, by whom (who had a kick ready for a cast that went through: enemies).
    By ability, or for one `ability`, each row has its hits (and periodic ticks), average hit and crit rate ("why
    does their Starsurge hit harder?"); damage taken by ability also names who took most of each. By player, each
    row has the player's spec, and damage or healing on one kill each player's parse against their spec: the way
    to compare players of different specs ("who played well on the kill?").
    Reading it: healing is capped by the damage taken, so its overhealing (healing past full health) is high where
    the raid took little and isn't a fault on its own.
    Not for who stood in one mechanic (mechanic_hits), the damage spikes and the healing cooldowns in them
    (healing), or who had a kick ready for a cast that went through (enemies).
    Example: data_type="DamageTaken", group_by="ability", pulls=[13]."""
    return await tables.breakdown(await lib().report(report), data_type, group_by, pulls, boss, player, ability, cutoff,
                                  whole_pull)


@_tool
async def wcl_query(query: Annotated[str | None, Field(description=(
                        "a read-only GraphQL query of Warcraft Logs' API; $code stands for the report's code"))] = None,
                    schema_type: Annotated[str | None, Field(description=(
                        "instead of a query, a type of the API to describe, e.g. Query, Report, ReportFight, "
                        "Encounter"))] = None,
                    part: Annotated[int, Field(description="for a long answer, which part, from 1", ge=1)] = 1,
                    report: Report_ = None) -> str:
    """Ask Warcraft Logs anything its API answers that no other tool shows: a ranking by item level bracket or past
    its first page, the players in one fight of another log, events of one kind through a filter expression, a
    character's or guild's data. The other tools are faster and know how to read a wipe; this is for the rest, and
    spends the hour's Warcraft Logs points that every tool shares.
    With `query`: the answer as compact JSON, in parts when long, with what it cost and the hour's points left;
    kept for 10 minutes, so its parts and a repeat cost nothing. One over 3 parts is refused, with which fields
    made it long. With `schema_type`: that type's fields, their arguments and types, to write a query from. The
    roots: reportData { report(code) { fights, playerDetails, events, table, rankings, masterData } }, worldData {
    encounter(id) { fightRankings, characterRankings }, zone(id) }, characterData { character(name, serverSlug,
    serverRegion) }, guildData { guild(...) }.
    Reading it: times in a report are milliseconds from its start; events come a page at a time (nextPageTimestamp
    is where the next starts); table and rankings are JSON inside the answer. Rankings are kept per partition (a
    patch's), and a new one is empty for a while: an empty ranking may need zone(id) { partitions } and an older
    `partition`; item level brackets are zone(id) { brackets }, counted from its min in steps of its bucket."""
    if schema_type:
        return await queries.schema_text(lib().wcl, schema_type)
    if not query:
        raise FactError("give a query, or a schema_type to describe")
    code = None
    if "$code" in query:
        code = (await lib().report(report)).code
    return await queries.query_text(lib().wcl, query, code, part)


# ---------- game knowledge ----------

@_tool
async def spell_info(spell: Annotated[str, Field(description="spell id (best, from a log tool) or name")]) -> str:
    """For "what does X do?": a spell or boss ability from Wowhead's tooltip (its text, cooldown, what kind of
    defensive), by the [id] a tool printed or by name.
    Not for how to handle a boss's mechanic (boss_guide with `mechanic`) or a spec's cooldowns (spec_guide).
    Example: spell=1287083."""
    return await knowledge.spell_text(spell)


@_tool
async def boss_guide(boss: Annotated[str, Field(description="raid boss name, e.g. Sszorak")],
                     topic: Annotated[Literal["strategy", "tooltips", "loot"], Field(description=(
                         "strategy (the default): how every mechanic works and how to handle it; tooltips: only "
                         "each ability's tooltip numbers, no strategy; loot"))] = "strategy",
                     mechanic: Annotated[str | None, Field(description=(
                         "for strategy: one ability's name or id ([id] in pull_summary), for only what the guides "
                         "say about it and its tooltip; omit for the whole guides"))] = None,
                     difficulty: Annotated[Literal["Mythic", "Heroic", "Normal"] | None, Field(description=(
                         "omit for the report's; Normal reads the Heroic guides"))] = None,
                     raid: Annotated[str | None, Field(description="raid name; omit to use the report's")] = None,
                     spec: Annotated[str | None, Field(description=(
                         "for loot: spec and class, e.g. 'restoration shaman', to see which drops its gear guides "
                         "name"))] = None,
                     report: Report_ = None) -> str:
    """How to beat a raid boss and how each of its mechanics works (who it targets, whether it can be avoided, what
    happens when it fails); also its abilities' exact tooltip numbers and its loot.
    strategy (the default): three guides side by side, each with its last update: Method's (raid setup, strategy,
    the usual wipe causes), Wowhead's cheat sheet (assignments per phase, the dungeon journal) and Icy Veins'
    (every mechanic, a quick guide, the Mythic changes). With `mechanic`, for judging one mechanic: its tooltip
    and only the guides' sections and paragraphs that name it, a tenth of the whole. tooltips: only Wowhead's
    tooltip of every ability (exact damage and effects), without the strategy. loot: each drop's slot, stats and
    effect, the item level per difficulty, the other rewards; with a spec, which drops its best-in-slot lists and
    trinket advice (Wowhead, Method, Icy Veins) name."""
    boss = await top.boss_name(lib().wcl, boss)
    try:
        return await _boss_guide(boss, topic, difficulty, raid, spec, report, mechanic)
    except KnowledgeError as e:  # a name no guide has: the model picks the boss from the raid's list and asks again
        try:
            rep = await lib().report(report)
            if rep.zone_id is None:
                raise e from None
            raid_name, bosses = await rep.wcl.zone_encounters(rep.zone_id)
        except (FactError, WCLError):
            raise e from None
        raise KnowledgeError(f"{e}. The bosses of {raid_name}, as Warcraft Logs names them: "
                             + ", ".join(b["name"] for b in bosses)) from None


async def _boss_guide(boss: str, topic: str, difficulty: str | None, raid: str | None, spec: str | None,
                      report: str | None, mechanic: str | None = None) -> str:
    if topic == "tooltips":
        return await knowledge.boss_tooltips_text(boss)
    if topic == "loot":
        return await knowledge.boss_loot_text(boss, spec)
    if not raid:
        try:
            raid = (await lib().report(report)).zone
        except FactError:
            raise FactError("give the raid name (no report to take it from)") from None
    if not difficulty:
        try:
            f = (await lib().report(report)).fight(None, boss)
            boss, difficulty = f["name"], "mythic" if f.get("difficulty") == 5 else "heroic"
        except FactError:
            difficulty = "mythic"
    difficulty = "mythic" if difficulty.lower() == "mythic" else "heroic"
    if mechanic:
        return await knowledge.boss_mechanic_text(boss, raid, difficulty, await _ability_name(mechanic, report))
    return await knowledge.boss_guide_text(boss, raid, difficulty)


async def _ability_name(ability: str, report: str | None) -> str:
    """An ability given by name or id, as its name: an id's from the report, else Wowhead's."""
    if not ability.strip().isdigit():
        return ability.strip()
    try:
        if (name := (await lib().report(report)).abilities.get(int(ability))):
            return name
    except FactError:
        pass
    return await knowledge.spell_name(int(ability)) or ability


@_tool
async def web_lookup(query: Annotated[str | None, Field(description=(
                         "words to search for, e.g. 'twin fangs mythic nerf'"))] = None,
                     url: Annotated[str | None, Field(description=(
                         "a page to read: a Wowhead news post, blue post or guide, a Method, Icy Veins or Warcraft "
                         "Wiki page"))] = None,
                     part: Annotated[int, Field(description="for a long page, which part, from 1", ge=1)] = 1) -> str:
    """Search the game's guide and news sites, or read any page of them in full: for what the other tools don't
    show or cut short (the rest of a news post or guide, a page a tool linked, a guide, article or name no tool
    covers or recognised).
    With `query`: the matching pages on Wowhead, Method, Icy Veins and Warcraft Wiki, best first, with their
    addresses. With `url`: the page's text in parts of about 6,000 characters; the
    end of a part says which comes next."""
    if url:
        return await knowledge.read_text(url, part)
    if not query:
        raise FactError("give a query to search for, or a page's url to read")
    return await knowledge.search_text(query)


@_tool
async def spec_guide(spec: Annotated[str, Field(description="spec and class, e.g. 'restoration shaman'")],
                     topic: Annotated[str | None, Field(description=(
                         "e.g. rotation, talents, stat priority, best in slot gear, trinkets, raid tips, or "
                         "cooldowns (Lorrgs' list); omit for an overview"))] = None,
                     source: Literal["method", "wowhead", "icy-veins"] = "method",
                     page: Annotated[str | None, Field(description="a page URL listed by an earlier call")] = None
                     ) -> str:
    """A class guide from Method, Wowhead or Icy Veins: the sections on a topic (rotation, talents, stats, gear),
    or an overview, plus its other pages and its last update. Call it before advising a player how to play their
    spec (what to cast or spend and when, procs, targets, cooldowns), on the topic of the advice. Topic
    "cooldowns": the spec's cooldowns with spell ids, cooldown lengths and tags (defensive, raid_cd, dps, tank,
    move), from Lorrgs."""
    if topic and topic.strip().lower() in ("cooldowns", "cooldown"):
        return await knowledge.spec_cooldowns_text(spec)
    return await knowledge.spec_guide_text(spec, topic, source, page)


@_tool
async def hotfixes(about: Annotated[str, Field(description="a boss, raid, class or spec, e.g. 'Sszorak' or "
                                                           "'Brewmaster'")],
                   days: Annotated[int, Field(description="how far back")] = 60) -> str:
    """Blizzard's recent hotfixes that mention a boss, raid, class or spec: tuning that may explain a change between
    nights or make a guide out of date. When Blizzard's post has none, Wowhead's news posts about it, which are
    news, not Blizzard's list."""
    return await knowledge.hotfixes_text(await top.boss_name(lib().wcl, about), days)


# ---------- prompts ----------

@_prompt
def why_did_we_wipe(report: str, pull: str = "", boss: str = "") -> str:
    """Why one pull wiped."""
    which = f"pull {pull}" if pull else "the latest pull"
    return f"Why did we wipe on {which}{' on ' + boss if boss else ''} in Warcraft Logs report {report}?"


@_prompt
def how_did_i_do(report: str, player: str) -> str:
    """How one player did over a night."""
    return f"How did {player} do in Warcraft Logs report {report}?"


# Every tool only reads, from Warcraft Logs and the guides on the web: ChatGPT asks before a call
# to a tool not marked read-only.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=True)


# The tools answers start from: Claude Code loads these up front rather than through its tool search.
ALWAYS_LOAD = ("pull_summary", "progress", "player_pull", "player_night")


def compact(schema: dict) -> dict:
    """A tool's input schema without what pydantic adds and no model needs: titles, `anyOf [X, null]` for an
    optional argument (omitting it is how it's left out) and `default: null`. A third of the tool list was this;
    the arguments are still checked against the function's own types."""
    out = {k: v for k, v in schema.items() if k != "title" and not (k == "default" and v is None)}
    if (alts := out.pop("anyOf", None)) is not None:
        kept = [a for a in alts if a != {"type": "null"}]
        if len(kept) == 1:
            out = {**compact(kept[0]), **out}
        else:
            out["anyOf"] = [compact(a) for a in kept]
    if "properties" in out:
        out["properties"] = {name: compact(p) for name, p in out["properties"].items()}
    if isinstance(out.get("items"), dict):
        out["items"] = compact(out["items"])
    return out


# Saving changes nothing already saved (every version is kept), and only this server's own notebook.
SAVES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
# What raid_note and raid_plan add on a server with save_note_or_plan.
SAVING = {"raid_note": "An officer of the guild keeps a pasted note for the guild with save_note_or_plan.",
          "raid_plan": "An officer of the guild keeps a pasted plan for the guild with save_note_or_plan."}


async def save_note_or_plan(pasted: Annotated[str, Field(description=(
        "the raid note or plan as the user pasted it: the NSRT note from Viserio (EncounterID lines included) or "
        "raidstrats.gg's export (the text starting raidstrats-addon-)"))],
        from_pull: Annotated[int | None, Field(description=(
            "the pull on its boss in the report it counts from, when pasted after the pulls it was for (\"we used "
            "it all night\": 1); omit for from now on"))] = None,
        report: Report_ = None) -> str:
    """For "save this note", "keep this plan for the guild", and "save this, we used it last night from pull 7": a
    raid note or plan the user pasted and wants kept for the guild.
    Saves it for the user's guild, credited to them, when they are one of its officers: raid_note and raid_plan
    then check pulls against it with no paste, each pull against the version saved last before it began (earlier
    versions are kept), so one pasted after the pulls it was for takes from_pull. Returns what was saved: each
    boss's note with its timed cooldowns, callouts and assignment groups, or each plan with its spots, and the pull
    it counts from; or why nothing was (not an officer, not a note or plan export, no such pull)."""
    officer = setup().officer
    if officer is None:
        raise FactError("nothing is saved here: raid_note and raid_plan check a pasted note or plan as pasted")
    by, guild = officer()
    since = (await lib().report(report), from_pull) if from_pull else None
    return save_pasted(notebook.Notebook(), guild, pasted, by, from_pull=since)


def allow_saving(server: MCPServer, setup: Setup, officer: Callable[[], tuple[str, int]]) -> None:
    """Give a server with the tools (`register`), whose users are known, save_note_or_plan, run as `officer` says
    (Setup.officer); raid_note's and raid_plan's descriptions then say so (SAVING)."""
    setup.officer = officer
    server.add_tool(guarded(save_note_or_plan, setup), name="save_note_or_plan", title=title("save_note_or_plan"),
                    description=inspect.cleandoc(save_note_or_plan.__doc__ or ""), annotations=SAVES,
                    structured_output=False)
    for name in ("save_note_or_plan", *SAVING):
        if tool := server._tool_manager.get_tool(name):  # the SDK has no hook for these
            tool.parameters = compact(tool.parameters)
            tool.description = "\n".join(filter(None, [tool.description, SAVING.get(name)]))


def register(server: MCPServer, setup: Setup = DEFAULT, meta: dict[str, dict] | None = None,
             notes: dict[str, str] | None = None) -> None:
    """Give `server` the tools, run with `setup` (`meta` adds a tool's _meta, `notes` a paragraph to its
    description: what that server does differently), and the prompts."""
    for name, fn in _TOOLS:
        extra = {**((meta or {}).get(name) or {}), **({"anthropic/alwaysLoad": True} if name in ALWAYS_LOAD else {})}
        server.add_tool(guarded(fn, setup), name=name, title=title(name),
                        description="\n".join(filter(None, [inspect.cleandoc(fn.__doc__ or ""),
                                                             (notes or {}).get(name)])), meta=extra or None,
                        annotations=READ_ONLY, structured_output=False)
        if tool := server._tool_manager.get_tool(name):  # the SDK has no hook for the schema it builds
            tool.parameters = compact(tool.parameters)
    for prompt in _PROMPTS:
        server.prompt()(prompt)


def new_server(**kwargs) -> MCPServer:
    """An MCP server with Wipebot's name and instructions (`kwargs` for MCPServer: auth, extensions, website_url)."""
    return MCPServer("wipebot", title="Wipebot", instructions=INSTRUCTIONS, log_level="WARNING",
                     **kwargs)  # INFO logs every request


mcp = new_server()  # in-process (the Discord bot, `wipebot call`) and over stdio (`wipebot mcp`)
register(mcp)


async def call(name: str, args: dict) -> tuple[str, bool]:
    """Run one tool in-process: (its text, whether it failed). Raises ToolError for bad arguments."""
    text, failed, _ = await call_with_images(name, args)
    return text, failed


async def call_with_images(name: str, args: dict) -> tuple[str, bool, list[bytes]]:
    """`call`, with the images the tool drew (pull_map's WebP map)."""
    result = await mcp.call_tool(name, args)
    if not isinstance(result, CallToolResult):  # a tool asking the user for input; none of ours do
        return "the tool asked for input, which isn't supported here", True, []
    text = "".join(c.text for c in result.content if isinstance(c, TextContent))
    images = [base64.b64decode(c.data) for c in result.content if isinstance(c, ImageContent)]
    return text, bool(result.is_error), images

