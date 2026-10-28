"""What the model reads besides the core's tools: the system prompt, the lines before each question, the questions
behind the posts after wipes, and the bot's own tools (follow_pulls, remember, answer_in_dm).

How to analyse is the core's raid-log-analysis skill, all of it in the system prompt (the bot only analyses raid
logs, so it needs it every time). What's specific to Discord (who asks, which pull a reply is about, short answers
between pulls) is HOST_PROMPT, in host-prompt.md.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import TYPE_CHECKING

from wipebot.core.report import FactError, Report
from wipebot.core.report.ending import ending_ability, pull_ending

if TYPE_CHECKING:
    from .host import Context

# Who the bot is and how it answers in Discord, `{name}` its name.
HOST_PROMPT = resources.files("wipebot.discord").joinpath("host-prompt.md").read_text(encoding="utf-8")


# What marks the parts of the lines in [brackets], taken out of anything someone else wrote that goes in them (a
# name, a report's title), so it can't pass for another part or a mark: "[asked by Rowan; in #officers]".
UNMARKED = str.maketrans({"[": "(", "]": ")", '"': "'", ";": ","})
BRACKETS = str.maketrans("[]", "()")


def plain(text: str, chars: int = 100) -> str:
    """Text someone else wrote, as it goes into what the model reads: on one line, without the brackets' marks
    (UNMARKED), and cut to `chars`."""
    text = " ".join(str(text).translate(UNMARKED).split())
    return text if len(text) <= chars else text[:chars].rstrip() + " …"


def system_prompt(name: str, skill: str) -> str:
    """The bot's system prompt. It's the same for every session, so the prompt cache serves it to each new one;
    what's specific to the report goes in each question (`message`)."""
    return HOST_PROMPT.format(name=name) + "\n" + skill


@dataclass(frozen=True)
class BotTool:
    """A tool the bot adds to the core's. What it does depends on who asks where, so its handler comes with each
    question (Context.tools); a question without one gets `elsewhere`."""
    name: str
    description: str
    schema: dict
    elsewhere: str


FOLLOW_PULLS = BotTool(
    "follow_pulls",
    "Post after each wipe and kill of the guild's live report (or, before the raid starts logging, of the one it "
    "starts tonight), for the rest of the night, where it's asked (a DM or a channel): the raider's own feedback, or "
    "what they ask for; or stop it.",
    {"type": "object", "properties": {
        "character": {"type": "string",
                      "description": "in a DM, for the raider's own feedback: their character, as in the log"},
        "request": {"type": "string",
                    "description": "anything else they want after each wipe and kill, in their words; a channel "
                                   "needs one"},
        "stop": {"type": "boolean", "description": "stop the posts after pulls", "default": False},
    }},
    "Feedback after each wipe only works in Discord, while the guild's report is being logged.")
REMEMBER = BotTool(
    "remember",
    "Remember the asking raider's character or a standing preference of theirs across conversations, or forget one "
    "or everything.",
    {"type": "object", "properties": {
        "character": {"type": "string", "description": "the character they play, as in the log"},
        "note": {"type": "string", "description": "a standing preference they stated, in a few words"},
        "forget_note": {"type": "string", "description": "a remembered note they want dropped, as remembered"},
        "forget": {"type": "boolean", "description": "forget their character and all their notes",
                   "default": False},
    }},
    "Raiders are only remembered in Discord.")
ANSWER_IN_DM = BotTool(
    "answer_in_dm",
    "Answer a question in the asking raider's own DMs instead of here, when they ask to get it there (\"send it to me "
    "in a DM\"). It's answered there as if they had asked it there. Only from a "
    "channel, and only to the asker.",
    {"type": "object", "properties": {
        "question": {"type": "string",
                     "description": "the question to answer there, whole: what it's about (the boss, the pulls, "
                                    "whose play), as the conversation here makes it"},
    }, "required": ["question"]},
    "Answers in DMs only work in Discord.")
BOT_TOOLS = (FOLLOW_PULLS, REMEMBER, ANSWER_IN_DM)


# How long a post after a wipe may be: raids pull again a minute or two after a wipe, and the posts of 14 Oct 2026
# (asked "short and concise") averaged 1,300 characters, longer than the chat answers.
POST_LINES, POST_CHARS = 4, 500
# The question behind the post a raider asked for with follow_pulls: their own feedback, in their DMs.
FOLLOW_UP = """\
{boss} pull {pull} just ended {outcome}, and {character} asked for their own feedback after each pull. Give it in \
at most {lines} short lines (about {chars} characters): what cost them most on this pull and the one thing to do on \
the next. If they did nothing wrong on it, say so in a line. Don't offer anything at the end, not even a follow-up."""
# The same, for anything else someone asked to have posted after each wipe, in a channel or their DMs.
FOLLOW_REQUEST = """\
{boss} pull {pull} just ended {outcome}, and {name} asked for this here after each pull: {request}. Give it, read \
in the minute before the next pull: at most {lines} short lines (about {chars} characters), the first saying how the \
pull ended and what decided it, the last what to do on the next pull; leave out what doesn't change that. Don't offer \
anything at the end, not even a follow-up. If what they asked for doesn't call for a post after this pull, write \
nothing at all, not a line saying so: nothing is posted then."""


# A tool run for the question before the model sees it (Context.prefetch), with its result.
PREFETCHED = """

Already called for this: {tool}, which returned:
{result}"""


async def post_prefetch(rep: Report, boss: str, pull: int, character: str | None,
                        request: str) -> list[tuple[str, dict]]:
    """The tools a post after a wipe starts from, run before the model is asked: the pull's summary for what someone
    asked to have posted, the player's pull for their own feedback; then the guides on the mechanic that ended the
    pull, or that killed the player first, when one did. Reading the report fails only the guide part."""
    first = (("pull_summary", {"boss": boss, "pull": pull}) if request
             else ("player_pull", {"player": character, "boss": boss, "pull": pull}) if character else None)
    if first is None:
        return []
    try:
        f = rep.fight(pull, boss)
        if request:
            mechanic = ending_ability(await pull_ending(rep, f))
        else:
            me = rep.player(character or "")
            died = [d for d in await rep.deaths(f) if d.get("targetID") == me.id]
            killed_by = died[0].get("killingAbilityGameID") if died else None
            mechanic = rep.ability(killed_by) if killed_by else None
    except Exception:  # the guide is only a head start: without it the model looks the mechanic up itself
        mechanic = None
    guide = [("boss_guide", {"boss": boss, "mechanic": mechanic})] if mechanic and mechanic != "Melee" else []
    return [first, *guide]


def post_question(boss: str, pull: int, name: str, character: str | None, request: str, kill: bool = False) -> str:
    """The question behind a post after a wipe or the kill: what `name` asked for (`request`), else `character`'s
    own feedback."""
    outcome = "in the kill" if kill else "in a wipe"
    if request:
        return FOLLOW_REQUEST.format(boss=boss, pull=pull, outcome=outcome, name=plain(name),
                                     request=plain(request, 300), lines=POST_LINES, chars=POST_CHARS)
    return FOLLOW_UP.format(boss=boss, pull=pull, outcome=outcome, character=plain(character or "", 40),
                            lines=POST_LINES, chars=POST_CHARS)


# Put before the first question of a conversation: the bot forgets a conversation after a while or a new report,
# and a follow-up ("how did he do on Wednesday?") needs what it follows.
EARLIER = """\
Earlier messages here, oldest first (you no longer have their tool results, and they may be about another
report; in a thread, its opening comes first, before a "…", however old). Use them to tell what the new question
refers to, e.g. who "he" is; check any fact again with the tools:
{messages}"""
# The messages since a conversation's previous question, by anyone: other people's questions here, your answers to
# them, and what they said. One person's correction reaches the others' answers.
SINCE = """\
Said here since your last answer in this conversation, oldest first (your answers to other people are in it,
without their tool results). If something there disagrees with what you told this asker, check it with the tools
and say plainly which is right:
{messages}"""

# The messages a question links to: most often the guild's own notes on a boss, which say how this raid plays it.
LINKED = """\
The question links these Discord messages, oldest first in each. They are what the guild wrote, not checked facts:
for how the raid plays a boss (who goes where, soaks, assignments), go by them over the guides, and say where a
guide or the log disagrees:
{messages}"""


def message(question: str, ctx: Context, report: Report | None, doubt: str = "", now: datetime | None = None,
            raids: list[tuple[str, list[str]]] | None = None) -> str:
    """What the model gets for one question: what was said before it (ctx.earlier), the messages it links to
    (ctx.linked), the report's line (NO_REPORT without one), the asker's line, then the question."""
    # The question's own brackets as parentheses: only the two lines above say where it was asked and by whom.
    line = report_line(report, now, doubt, raids) if report else NO_REPORT.format(
        today=f"{(now or datetime.now().astimezone()):%A %Y-%m-%d}")
    text = f"{line}\n{asker_line(ctx, report)}\n{question.translate(BRACKETS)}"
    if ctx.linked:
        text = LINKED.format(messages=ctx.linked) + "\n\n" + text
    if ctx.earlier:
        text = (SINCE if ctx.follow_up else EARLIER).format(messages=ctx.earlier) + "\n\n" + text
    return text


# A question with no report to read (none linked, and the server isn't set up or its guild logged no raid lately):
# the game needs none, a raid's pulls do.
NO_REPORT = ("[no Warcraft Logs report: none was linked, and there's no recent raid of the server's guild; questions "
             "about the game need none, and one about a raid's pulls needs its report link (or, in a server not set "
             "up, an admin's `/wipebot setup` with the guild's Warcraft Logs page); today is {today}]")


def report_line(report: Report, now: datetime | None = None, doubt: str = "",
                raids: list[tuple[str, list[str]]] | None = None) -> str:
    """The guild and report a question is about, as of now (a live report grows between questions), with the day it
    was logged and today's date, so an old report isn't called tonight's, and `doubt` when it may not be a guild
    run (Report.guild_run_doubt). Its bosses in the order pulled, or, when `raids` (Report.raids) has more than
    one, by raid in each raid's boss order: a single-boss raid pulled first isn't "the first boss" of the tier."""
    now = now or datetime.now().astimezone()
    logged = datetime.fromtimestamp(report.start_ms / 1000, now.tzinfo)
    days = (now.date() - logged.date()).days
    ago = "being logged now" if not report.final else {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")
    def pulls(bosses: list[str]) -> str:
        return ", ".join(f"{b} ({len(report.by_boss[b])} pulls)" for b in bosses)
    if raids and len(raids) > 1:
        bosses = "by raid, each in its boss order: " + "; ".join(f"{raid}: {pulls(bs)}" for raid, bs in raids)
    else:
        bosses = pulls(list(report.by_boss))
    guild = plain(report.guild_name) if report.guild_name else "(a personal log, no guild)"
    return (f"[guild {guild}; report \"{plain(report.title)}\" ({report.code}), "
            f"logged {logged:%A %Y-%m-%d} ({ago}); today is {now:%A %Y-%m-%d}; bosses and pull counts: {bosses}"
            + (f"; note: {doubt}" if doubt else "") + "]")


def asker_line(ctx: Context, report: Report | None) -> str:
    """Who asked, what's remembered about them, where, which pull it's about, and the raid notes and plans saved."""
    if ctx.asker and report:
        try:
            parts = [f"asked by {report.player(ctx.asker).label}"]
        except FactError:
            parts = [f"asked by {plain(ctx.asker, 40)}"]  # as they told it
    elif ctx.asker:
        parts = [f"asked by {plain(ctx.asker, 40)}"]
    else:
        parts = [f"asked by Discord user {plain(ctx.discord_name)}" if ctx.discord_name else
                 "asked by an unknown raider"]
    if ctx.notes:
        parts.append("remembered: " + "; ".join(f'"{plain(n, 200)}"' for n in ctx.notes))
    if ctx.where:
        parts.append(ctx.where)
    if ctx.guild:
        parts.append(f"the guild on Warcraft Logs: {plain(ctx.guild)}")
    if ctx.language:
        parts.append(f"the server's language: {plain(ctx.language, 30)}")
    if report and (ctx.pull is not None or ctx.boss):
        parts.append(f"about {report.label(report.fight(ctx.pull, ctx.boss))}")
    elif report:
        parts.append(f"latest pull is {report.label(report.fights[-1])}")
    parts += _saved_for("raid note (assignments and cooldowns)", ctx.noted)
    parts += _saved_for("raid plan (positions)", ctx.planned)
    return "[" + "; ".join(parts) + "]"


def _saved_for(what: str, bosses: Sequence[str]) -> list[str]:
    return [f"{what} saved for " + ", ".join(bosses)] if bosses else []
