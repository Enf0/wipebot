"""Slash commands. For everyone: /help (what to ask, the commands, how the server is set up; also "help" said to
the bot, bot.py) and /usage (what's left of the AI budget, on an OpenRouter key, and of the hour's Warcraft Logs
points; admins also get a button there to post the AI budget). For server admins, one command: /wipebot setup, which
alone shows how the server is set up and what's left to do, with buttons for its AI key, and with options changes the
guild, roles, footers, language and the channel for what's new. Officers save a note or plan by
pasting it to the bot (bot.py). docs/features.md lists them all (a test checks). Viserio Cooldowns and raidstrats.gg,
experimental, are connected by the host on the command line (__main__.py).

Only members with Manage Server see /wipebot (Discord enforces this), and server owners can change that in
Server Settings > Integrations.
"""
import logging
import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import discord
import httpx
from discord import app_commands

from wipebot.core import accounts, raidstrats, server
from wipebot.core.accounts import parse_guild_link
from wipebot.core.notebook import Unreadable as NotesUnreadable
from wipebot.core.report import FactError
from wipebot.core.report import note as notes
from wipebot.core.report import plan_export as plans
from wipebot.core.wcl import WCLError

from . import budget, config, keycmd, logincmd, models, news, texts
from .store import ServerSettings, Store

if TYPE_CHECKING:
    from .bot import WipeBot

log = logging.getLogger(__name__)
MAX_NOTE_BYTES = 200_000  # a whole raid's note is some tens of kB; a plan's export a few
# A paste the officer says counts from a pull ("from pull 7", Norwegian "fra pull 7"), and the report it's in.
FROM_PULL = re.compile(r"\b(?:from|fra)\s+pull\s*#?(\d+)\b", re.I)
REPORT_LINK = re.compile(r"warcraftlogs\.com/reports/([A-Za-z0-9]+)")
REPORT_LINK_WHOLE = re.compile(r"\S*warcraftlogs\.com/reports/\S*")
MENTION = re.compile(r"<@[!&]?\d+>")
# Where a plan is saved by its link (the host has raidstrats.gg's key): the gesture has no button in Viserio.
BY_LINK = (" A plan is also saved by its link: in Viserio's assignment note, right-click the raidstrats.gg badge at "
           "the bottom right of a section's raid plan, Copy Link, and paste the link to me; the night's raiders "
           "come with it.")
# ...with the pull it opens: "?fight=4&type=healing" as the address bar has it, "#fight=21" as the tools link a pull.
# Not several at once ("fight=4,5", in an address "fight=4%2C5"): that is no one pull.
PULL_LINK = re.compile(r"warcraftlogs\.com/reports/([A-Za-z0-9]+)[^\s<>()\[\]]*?[?#&]fight=(\d+|last)(?![\w,]|%2[Cc])")


def linked_pull(text: str) -> tuple[str, int | None] | None:
    """The one pull a text links on Warcraft Logs: its report's code and the fight's id there (None for "last", the
    report's latest). None when the text links no pull, or different ones. A fight's id isn't its pull number: ids
    count over the report, pulls per boss."""
    found = {(code, None if fight == "last" else int(fight)) for code, fight in PULL_LINK.findall(text)}
    return found.pop() if len(found) == 1 else None


def looks_like_note(text: str) -> bool:
    """A raid note (NSRT, from Viserio) or a raid plan: raidstrats.gg's export, or a plan's link there with the
    night's raiders (as Viserio's assignment note embeds it), or sent with nothing else but a mention and the pull
    it counts from. Another message with a plan link is a question."""
    if notes.HEADER.search(text) or plans.EXPORT.search(text):
        return True
    if not (links := plans.links(text)):
        return False
    rest = FROM_PULL.sub("", MENTION.sub("", plans.LINK.sub("", REPORT_LINK_WHOLE.sub("", text))))
    return any(link.named for link in links) or not rest.strip()


def _guild(store: Store, discord_guild_id: int) -> int:
    """The server's guild on Warcraft Logs, whose notes and plans are the server's."""
    s = store.settings(discord_guild_id)
    if s is None:
        raise FactError("this server isn't set up yet: run `/wipebot setup` first, so it's kept for your guild")
    return s.wcl_guild_id


def from_pull(message: str) -> tuple[int, str | None] | None:
    """The pull a paste counts from, and the report linked for it (None: the guild's current one), when the
    message says ("from pull 7"); None when it doesn't."""
    if not (m := FROM_PULL.search(message)):
        return None
    return int(m[1]), (link[1] if (link := REPORT_LINK.search(message)) else None)


def save_pasted(store: Store, discord_guild_id: int, text: str, saved_by: str, at: float | None = None,
                from_pull: server.FromPull | None = None) -> str:
    """A pasted raid plan or raid note saved for the server's guild (server.save_pasted); what was saved, for the
    officer. A paste that's neither, or a server not set up, raises FactError."""
    return server.save_pasted(store.notebook, _guild(store, discord_guild_id), text, saved_by, at, from_pull)


# On a bot many servers share (config.own_keys), a server reads and saves what its guild keeps (raid notes and plans)
# only once an admin has shown the guild is the server's own (`verify`). Until then, what it's told:
NOTES_OFF = ("Raid notes and plans are off here until an admin with a character in **{guild}** (claimed on Warcraft "
             "Logs) runs `/login`, then `/wipebot setup`. Everything else works already.")
NO_SIGN_IN = ("Raid notes and plans are off here: showing that **{guild}** is this server's guild takes a Warcraft "
              "Logs sign-in, which whoever runs this bot hasn't set up. Everything else works already.")
NOTES_ON = "Raid notes and plans are on here: one of the characters you claimed on Warcraft Logs is in **{guild}**."


def notes_off(s: ServerSettings) -> str:
    """Why a server on a shared bot keeps no raid notes or plans yet, and what turns them on."""
    return (NOTES_OFF if logincmd.callback_url() else NO_SIGN_IN).format(guild=s.wcl_guild)


async def verify(bot: WipeBot, s: ServerSettings, user_id: int) -> tuple[ServerSettings, str]:
    """/wipebot setup on a bot many servers share: whether the admin who ran it shows the guild is the server's own.
    They do when they're signed in with Warcraft Logs (/login, or its sign-in for private reports, which says who
    they are as well) and one of the characters they claimed there is in the guild, asked of Warcraft Logs now with
    their own token. The settings, verified and saved when so, and what to tell
    the admin about raid notes and plans ("" where nothing changed: a host's own bot, or a server verified before)."""
    if not bot.own_keys or not s.set_up or s.verified_at is not None:
        return s, ""
    if not logincmd.callback_url():
        return s, notes_off(s)
    if not (signed := bot.store.login(user_id) or bot.store.private_login(user_id)):
        return s, notes_off(s) + " You aren't signed in yet."
    try:
        member = s.wcl_guild_id in await accounts.claimed_guilds(signed[1])
    except accounts.AccountError as e:
        log.warning("Warcraft Logs didn't say which guilds an admin's characters are in: %s", e)
        return s, notes_off(s) + (" You're signed in, but Warcraft Logs didn't say which guilds your characters are "
                                  "in: run `/wipebot setup` again in a few minutes.")
    if not member:
        return s, notes_off(s) + (f" You're signed in as **{signed[0]}**, but none of the characters claimed on that "
                                  "Warcraft Logs account is in it.")
    s = replace(s, verified_by=user_id, verified_at=time.time())
    bot.store.save_settings(s)
    log.info("server %s verified as its guild's own (Warcraft Logs guild %s)", s.discord_guild_id, s.wcl_guild_id)
    return s, NOTES_ON.format(guild=s.wcl_guild)


def describe_notes(store: Store, discord_guild_id: int) -> str:
    s = store.settings(discord_guild_id)
    if s is None:
        return "wipebot isn't set up here yet: run `/wipebot setup`, then save the raid note or plan."
    try:
        saved, planned = store.notebook.latest_notes(s.wcl_guild_id), store.notebook.latest_plans(s.wcl_guild_id)
    except NotesUnreadable:  # locked or damaged: the overview still shows the setup, and never "none saved"
        return ("The saved raid notes and plans can't be read right now, which doesn't mean none is saved: run "
                "`/wipebot setup` again in a moment.")
    parts = []
    if saved:
        parts.append("Raid notes saved here (the newest per boss):\n" + "\n".join(
            f"- **{n.boss}**, by {n.saved_by} <t:{int(n.saved_at)}:R>" for n in saved))
    if planned:
        parts.append("Raid plans saved here (the newest version of each):\n" + "\n".join(
            f"- **{p.name}** ({p.boss}), by {p.saved_by} <t:{int(p.saved_at)}:R>" for p in planned))
    if not parts:
        return ("No raid note or plan saved yet: an officer exports the boss's note from Viserio, or the plan from "
                "raidstrats.gg (the planner's Export button), and pastes it in a message to me (Discord turns a long "
                "paste into a file)." + BY_LINK * bool(raidstrats.key()))
    return "\n".join(parts)


def describe(s: ServerSettings) -> str:
    """The server's setup."""
    role = f"<@&{s.required_role_id}>" if s.required_role_id else "anyone in the server"
    officers = f"<@&{s.officer_role_id}>" if s.officer_role_id else "members who can manage the server"
    return (f"**Warcraft Logs guild:** {s.wcl_guild} ({s.wcl_server}-{s.wcl_region.upper()})\n"
            f"**Who can chat** (by @mention or DM): {role}\n"
            f"**Officers** (save the raid note or plan by pasting it to wipebot, and change or stop anyone's posts "
            f"after wipes): {officers}")


async def find_wcl_guild(bot: WipeBot, link: str) -> tuple[dict | None, str]:
    """The guild on Warcraft Logs from its link; and how it was asked for."""
    wcl = bot.library.wcl
    parsed = parse_guild_link(link)
    if isinstance(parsed, int):
        return await wcl.guild_by_id(parsed), f"with id {parsed}"
    region, realm, guild = parsed
    return await wcl.find_guild(guild, realm, region), f"**{guild}** on {realm}-{region.upper()}"


def updated_settings(existing: ServerSettings | None, discord_guild_id: int, found: dict | None,
                     role_id: int | None, officer_role_id: int | None = None) -> ServerSettings:
    """A server's settings after /wipebot setup: what was given (`found`: the guild on Warcraft Logs) replaces
    what's set, and the rest stays. A link to another guild is no longer verified as the server's own."""
    if found:
        wcl = ServerSettings(discord_guild_id, found["id"], found["name"], found["server"]["slug"],
                             found["server"]["region"]["slug"].lower())
        if existing and existing.wcl_guild_id == wcl.wcl_guild_id:  # the same guild, linked again
            wcl = replace(wcl, verified_by=existing.verified_by, verified_at=existing.verified_at)
    elif existing:
        wcl = existing
    else:
        raise ValueError("Give the guild's warcraftlogs.com link: open the guild on warcraftlogs.com and copy the "
                         "link from the address bar.")
    return replace(wcl, required_role_id=role_id or (existing and existing.required_role_id),
                   officer_role_id=officer_role_id or (existing and existing.officer_role_id))


NO_LANGUAGE = "Give the language's name, like Norwegian or English."


def server_options(store: Store, discord_guild_id: int, footer: bool | None, language: str | None,
                   steps: bool | None = None) -> list[str]:
    """/wipebot setup's footer, steps and language, kept for the server set up or not; what changed, for the admin
    (NO_LANGUAGE alone, and nothing kept, for a language that can't be one)."""
    if language is not None and not (name := server_language(language)):
        return [NO_LANGUAGE]
    told = []
    if footer is not None:
        store.set_footer(discord_guild_id, footer)
        told.append("Answers here now end with what they cost." if footer
                    else "Answers here no longer end with what they cost.")
    if steps is not None:
        store.set_steps(discord_guild_id, steps)
        told.append("Answers here now end with the steps they went through, like ✓ Roster · ✓ Pull summary." if steps
                    else "Answers here no longer end with the steps they went through.")
    if language is not None:
        store.set_language(discord_guild_id, name)
        told.append(f"The server's language is now {name}: posts after wipes and what's new are in it, and a "
                    "question is still answered in its own.")
    return told


NO_KEY_FOR_MODEL = ("The model can be chosen once this server answers on its own AI key: add one with the button "
                    "under `/wipebot setup`.")


async def choose_model(store: Store, discord_guild_id: int, chosen: str, default: str) -> str:
    """/wipebot setup model: the server's answers run on `chosen`, one of the models its own key reaches, or with
    models.DEFAULT on the bot's own (`default`) again; what was done, for the admin."""
    if (key := store.server_key(discord_guild_id)) is None:
        return NO_KEY_FOR_MODEL
    if chosen.strip().lower() == models.DEFAULT:
        store.set_model(discord_guild_id, None)
        return f"Answers here run on my default model again ({default})."
    try:
        model = models.find(await models.available(key), chosen.strip())
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return f"Couldn't ask {keycmd.NAMES[key.provider]} which models the key reaches ({e}). Nothing was changed."
    if model is None:
        return (f"This server's key reaches no model `{chosen.strip()[:80]}`: pick one from the list the option "
                "shows as you type. Nothing was changed.")
    store.set_model(discord_guild_id, model.id)
    untested = "" if model.claude else (" I'm only tested on the Claude models: another may answer worse or not at "
                                        "all, and the cost line under answers can be off for it (the key's own page "
                                        "has the real cost).")
    return (f"Answers here now run on **{model.name}** (`{model.id}`), from the next question on.{untested} "
            f"`model: {models.DEFAULT}` goes back to my default ({default}).")


async def model_choices(store: Store, discord_guild_id: int | None, typed: str,
                        default: str) -> list[app_commands.Choice[str]]:
    """What the model option shows as an admin types: the bot's default, then the models the server's key reaches
    that match, at most what Discord shows. Without a key of its own, or when the provider can't be asked, only a
    line saying so (choosing it changes nothing)."""
    key = store.server_key(discord_guild_id) if discord_guild_id else None
    if key is None:
        return [app_commands.Choice(name="Add the server's own AI key first (the button under /wipebot setup)",
                                    value=models.DEFAULT)]
    try:
        found = await models.available(key)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("couldn't list the models of a server's %s key: %s", key.provider, e)
        return [app_commands.Choice(name=f"Couldn't ask {keycmd.NAMES[key.provider]} for the key's models: try again",
                                    value=models.DEFAULT)]
    first = [app_commands.Choice(name=f"My default ({default})"[:models.LABEL], value=models.DEFAULT)] \
        if all(w in f"default {default}".lower() for w in typed.lower().split()) else []
    return first + [app_commands.Choice(name=m.label(), value=m.id)
                    for m in models.matching(found, typed, models.SHOWN - len(first))]


def server_language(text: str) -> str | None:
    """A language's name as an admin gave it ("norwegian" is Norwegian); None for what can't be one."""
    name = " ".join(text.split())
    return name[:1].upper() + name[1:] if re.fullmatch(r"[^\W\d_]+(?:[ -][^\W\d_]+)*", name) and len(name) <= 30 \
        else None


def in_guild(interaction: discord.Interaction) -> discord.Guild:
    """The server a (guild-only) command was used in."""
    if interaction.guild is None:
        raise RuntimeError("a guild-only command was used outside a server")
    return interaction.guild


# What a raider can ask, for /help: each a question the bot's tools answer.
EXAMPLES = (
    "why did we wipe?", "why did we wipe on pull 12?", "how did I do tonight?", "what killed me the most?",
    "how did the night go?", "are we doing better than last week?", "post after each wipe in here",
)
HELP_WORDS = {"help", "hjelp", "/help", "commands"}


def asks_for_help(question: str) -> bool:
    """Whether a message to the bot is only asking for help, answered with /help's text and no model."""
    return question.strip().strip("?!. ").lower() in HELP_WORDS


def needs_key(lang: str = "en") -> str:
    """What a server without an AI key is told, on a bot that answers only on servers' own: that an admin adds one,
    and, when the host runs the connector too, that it answers the same questions without one."""
    url = config.connector_url()
    return texts.NEEDS_KEY.to(lang) + (texts.WITHOUT_KEY.to(lang, url=url) if url else "")


def help_text(s: ServerSettings | None, admin: bool, in_dm: bool = False, keyless: bool = False) -> str:
    """/help's text: what to ask, the commands this member can use, and how the server is set up (`s`, None or
    unset when it isn't; `admin`: the member may use /wipebot). `keyless`: the server has no AI key on a bot that
    answers only on servers' own, which comes first, as no question below it gets an answer until it has one."""
    parts = ["**I can't answer here yet.** " + needs_key()] if keyless else []
    parts += ["**What to ask:** " + ("DM me" if in_dm else "@mention me, reply to my answers, or DM me")
             + ". For example:\n" + "\n".join(f"- {q}" for q in EXAMPLES)
             + "\nTell me your character once (\"I'm Jekå\") and \"I\" means them from then on. A warcraftlogs.com "
               "report link asks about that report instead of the guild's latest."]
    budget = "the AI budget and " if config.openrouter_key() or config.own_keys() else ""
    commands = ["`/help`: this", f"`/usage`: what's left of {budget}this hour's Warcraft Logs points"]
    if logincmd.callback_url():
        commands.append("`/login`: sign in with Warcraft Logs, so your own points pay for your questions")
    if admin:
        commands += ["`/wipebot setup`: how I'm set up here and what's left to do, with buttons for the server's AI "
                     "key",
                     "`/wipebot setup` with options: `link:` the guild on Warcraft Logs, `role:` who can chat, "
                     "`officers:`, `footer:` and `steps:` (the small lines under answers), `language:` of posts, "
                     "`model:` the model answers run on (with the server's own AI key), `news:` the channel for "
                     "what's new in each release",
                     "`/usage` shows you a button to post the AI budget in the channel"]
    commands.append("Officers save a raid note or plan by pasting it to me.")
    parts.append("**Commands:**\n" + "\n".join(f"- {c}" for c in commands))
    if s is None or not s.set_up:
        parts.append("**Here:** not set up, so put a warcraftlogs.com report link in your question. An admin can run "
                     "`/wipebot setup` with the guild's warcraftlogs.com page: then I look at its latest raid by "
                     "myself, post after wipes and keep raid notes." + (
                         " Until then, a raider signed in with `/login` asks about their own guild's latest raid "
                         "without a link." if logincmd.callback_url() else ""))
    else:
        who = f"<@&{s.required_role_id}>" if s.required_role_id else "anyone in the server"
        parts.append(f"**Here:** the guild **{s.wcl_guild}** ({s.wcl_server}-{s.wcl_region.upper()}); who can chat: "
                     f"{who}.")
    return "\n\n".join(parts)


def usage_text(rate: dict | None) -> str:
    """/usage's text from this hour's Warcraft Logs points (WCL.my_points: the bot's share; None when Warcraft Logs
    didn't say)."""
    if not rate:
        return "Warcraft Logs didn't say how many points are left this hour; try again in a minute."
    left = max(rate["limitPerHour"] - rate["pointsSpentThisHour"], 0)
    text = (f"**Warcraft Logs points:** {left:,.0f} left this hour, shared by "
            f"everyone using this bot; the hour starts again in {rate['pointsResetIn'] // 60 + 1} min.")
    if db := rate.get("databaseLimitPerHour"):
        db_left = max(db - rate.get("databasePointsSpentThisHour", 0), 0)
        text += f"\n**Warcraft Logs database points** (rankings, other guilds): {db_left:,.0f} left."
    return text


async def server_usage(bot: WipeBot, discord_guild_id: int, admin: bool = False) -> str | None:
    """/usage's line about this server: what it spent this month on its own key, for an `admin` with who it went to
    most (questions in DMs spend the key too, where nobody else sees them), or on a bot that answers only on servers'
    own keys, that it has none yet; None on a server that answers on the bot's key."""
    since = budget.month_start().timestamp()
    n, spent, _ = bot.store.spent(since, discord_guild_id=discord_guild_id)
    if info := bot.store.server_key_info(discord_guild_id):
        most = bot.store.asked_most(since, discord_guild_id) if admin else []
        return (f"**This server:** {n} answers this month, ${spent:.2f}, on its own "
                f"{keycmd.NAMES.get(info[0], info[0])} key (`/wipebot setup`)." + (
                    " Most went to " + ", ".join(f"<@{user}> ({answers}, ${cost:.2f})"
                                                 for user, answers, cost in most) + ", DMs included."
                    if most else ""))
    if bot.own_keys:
        return "**This server** has no AI key for answers yet: an admin adds one with `/wipebot setup`."
    return None


def budget_key(bot: WipeBot, discord_guild_id: int) -> str | None:
    """The OpenRouter key whose budget this server sees: its own (/wipebot setup), else the bot's, unless the bot
    answers only on servers' own keys; None when that's no OpenRouter key."""
    if own := bot.store.server_key(discord_guild_id):
        return own.key if own.provider == "openrouter" else None
    return None if bot.own_keys else config.openrouter_key()


def build_everyone(bot: WipeBot) -> list[app_commands.Command]:
    """/help, /usage and /login, for everyone."""
    @app_commands.command(name="help", description="What to ask wipebot, its commands, and how it's set up here")
    @app_commands.guild_only()
    async def show_help(interaction: discord.Interaction) -> None:
        gid = in_guild(interaction).id
        admin = bool(getattr(interaction.permissions, "manage_guild", False))
        await interaction.response.send_message(
            help_text(bot.store.settings(gid), admin, keyless=bot.keyless(gid)), ephemeral=True)

    @app_commands.command(name="usage", description="What's left of the AI budget and the Warcraft Logs points "
                                                    "wipebot's answers share")
    @app_commands.guild_only()
    async def show_usage(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        points = usage_text(await bot.library.wcl.my_points())  # its share of the account's hour
        # the OpenRouter key's credit limit is the AI budget raiders see: the server's own key's, else the bot's
        gid = in_guild(interaction).id
        text = f"{await budget_text(bot.store, key, gid)}\n\n{points}" if (key := budget_key(bot, gid)) else points
        if mine := await logincmd.points_line(bot.store, bot.library.wcl, interaction.user.id):
            text = f"{text}\n{mine}"
        admin = bool(getattr(interaction.permissions, "manage_guild", False))
        if here := await server_usage(bot, gid, admin):
            text = f"{here}\n\n{text}"
        if key and admin:
            await interaction.followup.send(text, view=PostUsage(bot), ephemeral=True)
        else:
            await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="login", description="Sign in with Warcraft Logs, so your own points pay for your "
                                                    "questions")
    @app_commands.describe(remove="Sign out and forget your Warcraft Logs token",
                           private="Let Wipebot read your private reports too (with remove: stop it)")
    @app_commands.guild_only()
    async def login(interaction: discord.Interaction, remove: bool = False, private: bool = False) -> None:
        async def signed_in() -> str:
            """Signed in by an admin of a server whose guild isn't shown to be its own yet: shown now, without
            another `/wipebot setup`."""
            s = bot.store.settings(interaction.guild_id) if interaction.guild_id else None
            if s is None or not interaction.permissions.manage_guild:
                return ""
            return (await verify(bot, s, interaction.user.id))[1]
        await logincmd.login_command(bot.store, interaction, remove, private, signed_in)

    return [show_help, show_usage, login]


def build(bot: WipeBot) -> app_commands.Group:
    group = app_commands.Group(name="wipebot", description="Set up wipebot for this server",
                               guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    @group.command(name="setup", description="How wipebot is set up here, with its AI key; with options, change "
                                             "a setting")
    @app_commands.describe(link="The guild's page on warcraftlogs.com (needed the first time)",
                           role="Role needed to chat with wipebot (default anyone)",
                           officers="Officers' role: save raid notes and plans, stop others' posts after wipes "
                                    "(default Manage Server)",
                           footer="End answers with a small line: their cost and Warcraft Logs points",
                           steps="End answers with a small line of the steps they went through (what they looked at)",
                           language="The server's language, e.g. Norwegian: for posts and what's new (questions get "
                                    "answers in theirs)",
                           model="The model answers run on, from the ones the server's own AI key reaches (type to "
                                 "search)",
                           news="Post what's new in each release of wipebot to this channel",
                           news_stop="Stop posting what's new")
    async def setup(interaction: discord.Interaction, link: str | None = None,
                    role: discord.Role | None = None, officers: discord.Role | None = None,
                    footer: bool | None = None, language: str | None = None, steps: bool | None = None,
                    model: str | None = None, news: discord.TextChannel | None = None,
                    news_stop: bool = False) -> None:
        """Alone: the server's setup and what's left to do, with the AI key's buttons. Options left out keep what's
        set; the footer, steps, language and news are the server's even before it's connected to a guild."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = in_guild(interaction)
        gid = guild.id
        if not (link or role or officers or news or news_stop or model) and footer is None and steps is None \
                and language is None:
            notes = ""
            if (s := bot.store.settings(gid)) is not None:  # run again by a signed-in member: the guild is its own
                _, notes = await verify(bot, s, interaction.user.id)
            await interaction.followup.send(overview(bot, gid, notes),
                                            view=keycmd.KeyButtons(bot.store, gid, bot.own_keys), ephemeral=True)
            return
        if language is not None and not server_language(language):
            await interaction.followup.send(NO_LANGUAGE, ephemeral=True)
            return
        told = []
        if link or role or officers:
            found = None
            try:
                if link:
                    found, asked = await find_wcl_guild(bot, link)
                    if not found:
                        await interaction.followup.send(f"Warcraft Logs has no guild {asked}. Check the link.",
                                                        ephemeral=True)
                        return
                settings = updated_settings(bot.store.settings(gid), gid, found, role.id if role else None,
                                            officers.id if officers else None)
            except WCLError as e:
                await interaction.followup.send(f"Couldn't reach Warcraft Logs: {e}", ephemeral=True)
                return
            except ValueError as e:
                await interaction.followup.send(str(e), ephemeral=True)
                return
            bot.store.save_settings(settings)
            bot.settings_changed(gid)
            settings, notes = await verify(bot, settings, interaction.user.id)
            told.append("wipebot is set up:\n" + describe(settings) + (f"\n{notes}" if notes else ""))
        told += server_options(bot.store, gid, footer, language, steps)
        if model:
            told.append(await choose_model(bot.store, gid, model, bot.model))
        preview = None
        if news or news_stop:
            said, preview = news_channel(bot, guild, news, news_stop)
            told.append(said)
        await interaction.followup.send("\n".join(told), ephemeral=True)
        if preview:
            await interaction.followup.send(view=preview, ephemeral=True)

    @setup.autocomplete("model")
    async def setup_models(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await model_choices(bot.store, interaction.guild_id, current, bot.model)

    return group


def news_channel(bot: WipeBot, guild: discord.Guild, channel: discord.TextChannel | None,
                 stop: bool) -> tuple[str, discord.ui.LayoutView | None]:
    """/wipebot setup news: post what's new in each release to `channel`, or `stop`; what was done, for the admin,
    with the latest release's post as it looks (None when there's none to show)."""
    if stop:
        bot.store.set_news_channel(guild.id, None)
        return "What's new in each release is no longer posted here.", None
    if channel is None:
        return "Pick a text channel to post what's new in.", None
    can = channel.permissions_for(guild.me)
    if not (can.view_channel and can.send_messages):
        return (f"I can't post what's new in {channel.mention}: let me see it and send messages there first.",
                None)
    bot.store.set_news_channel(guild.id, channel.id)
    saved = bot.store.state(news_key(bot.store.language(guild.id))) or bot.store.state(news_key(None))
    return (f"From the next release on, what's new is posted in {channel.mention}" + (
        ". The latest release's post looks like this:" if saved else "."),
        news.Post.from_json(saved).layout(bot.name) if saved else None)


def overview(bot: WipeBot, discord_guild_id: int, notes: str = "") -> str:
    """/wipebot setup alone: what's left to do first, then how the server is set up. `notes`: what `verify` just
    said about the server's raid notes and plans, when it said something. A server whose guild isn't verified as its
    own is shown none of what the guild saved."""
    store, gid = bot.store, discord_guild_id
    s = store.settings(gid)
    connected = s is not None and s.set_up
    keeps = s is not None and s.keeps_notes(bot.own_keys)
    todo = []
    if bot.own_keys and store.server_key_info(gid) is None:
        todo.append("Add an AI key with the button below: I answer only on a server's own key.")
    if not connected:
        todo.append("Connect the guild: `/wipebot setup link:` with its page on warcraftlogs.com (open the guild "
                    "there and copy the address). Then I look at its latest raid by myself, post after wipes and "
                    "keep raid notes.")
    elif s is not None and not keeps:
        todo.append(notes or notes_off(s))
    parts = ["**To do:**\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(todo, 1))] if todo else []
    if s is not None and connected:
        parts.append(describe(s) + ("\n\n" + (f"{notes}\n" if notes else "") + describe_notes(store, gid)
                                    if keeps else ""))
    name = bot.user.name if bot.user else "the bot"
    parts.append("**AI key:** " + keycmd.status(store, gid, name, bot.own_keys)
                 + ("\n**Model:** " + (f"`{chosen}`" if (chosen := store.model(gid)) else f"my default ({bot.model})")
                    + ", changed with `model:`" if store.server_key_info(gid) else ""))
    channel, language = store.news_channel(gid), store.language(gid)
    extra = [x for on, x in ((store.footer(gid), "what they cost"), (store.steps(gid), "the steps they went "
                                                                                       "through")) if on]
    parts.append("**What's new** in each release: " + (f"posted in <#{channel}>" if channel else "not posted")
                 + "\n**Language** of posts: " + (language or "none set, so English")
                 + "\n**Answers end with:** " + (" and ".join(extra) or "nothing extra"))
    parts.append("Change any of it with this command's options: `link:`, `role:`, `officers:`, `footer:`, "
                 "`steps:`, `language:`, `model:`, `news:`.")
    parts.append(f"wipebot is free software (AGPL-3.0): its source is at <{server.source_url()}>.")
    return "\n\n".join(parts)


class PostUsage(discord.ui.View):
    """/usage's button, for admins: post the AI budget in the channel, for everyone to see."""

    def __init__(self, bot: WipeBot):
        super().__init__(timeout=900)
        self.bot = bot

    @discord.ui.button(label="Post in this channel", style=discord.ButtonStyle.secondary)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        gid = in_guild(interaction).id
        if not getattr(interaction.permissions, "manage_guild", False):
            await interaction.response.send_message("Only admins post the AI budget.", ephemeral=True)
            return
        if not (key := budget_key(self.bot, gid)):
            await interaction.response.send_message("This server's answers have no AI budget to post: it shows "
                                                    "for an OpenRouter key.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(await budget_text(self.bot.store, key, gid, with_bar=True))


def news_key(language: str | None) -> str:
    """Where the latest release's post in `language` is kept (Store.state)."""
    return f"news_post:{(language or news.ENGLISH).lower()}"


async def budget_text(store: Store, key: str, discord_guild_id: int | None = None, with_bar: bool = False) -> str:
    """What an OpenRouter key has spent and has left, at this month's cost an answer: the server's own key's answers
    with its `discord_guild_id`, else every answer the bot gave; the host's note on chipping in only on its own key."""
    own = discord_guild_id is not None and store.server_key_info(discord_guild_id) is not None
    try:
        b = await budget.fetch(key)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return f"Couldn't read the budget from OpenRouter: {e}"
    since = budget.month_start().timestamp()
    note = None if with_bar or own else config.budget_note()  # the post leaves out how to chip in
    costs = store.spent(since, discord_guild_id=discord_guild_id)[:2] if own else store.answer_costs(since)
    return budget.describe(b, *costs, note, with_bar)
