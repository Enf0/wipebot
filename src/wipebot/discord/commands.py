"""Slash commands. For everyone: /help (what to ask, the commands, how the server is set up; also "help" said to
the bot, bot.py) and /usage (what's left of the AI budget, on an OpenRouter key, and of the hour's Warcraft Logs
points). For server admins: /wipebot setup (also the cost footer), /wipebot settings (with the raid notes and plans
saved), /wipebot key. Officers save a note or plan by pasting it to the bot (bot.py). docs/features.md lists them
all (a test checks). Viserio Cooldowns and raidstrats.gg, experimental, are connected by the host on the command line
(__main__.py).

Only members with Manage Server see /wipebot (Discord enforces this), and server owners can change that in
Server Settings > Integrations.
"""
import datetime
import os
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import discord
import httpx
from discord import app_commands

from wipebot.core import server
from wipebot.core.accounts import parse_guild_link
from wipebot.core.report import FactError
from wipebot.core.report import note as notes
from wipebot.core.report import plan_export as plans
from wipebot.core.wcl import WCLError

from . import allowance, budget, config, keycmd, logincmd
from .store import ServerSettings, Store

if TYPE_CHECKING:
    from .bot import WipeBot

MAX_NOTE_BYTES = 200_000  # a whole raid's note is some tens of kB; a plan's export a few
# A paste the officer says counts from a pull ("from pull 7", Norwegian "fra pull 7"), and the report it's in.
FROM_PULL = re.compile(r"\b(?:from|fra)\s+pull\s*#?(\d+)\b", re.I)
REPORT_LINK = re.compile(r"warcraftlogs\.com/reports/([A-Za-z0-9]+)")


def looks_like_note(text: str) -> bool:
    """A raid note (NSRT, from Viserio) or a raid plan (raidstrats.gg's export)."""
    return bool(notes.HEADER.search(text) or plans.EXPORT.search(text))


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


def describe_notes(store: Store, discord_guild_id: int) -> str:
    s = store.settings(discord_guild_id)
    if s is None:
        return "wipebot isn't set up here yet: run `/wipebot setup`, then save the raid note or plan."
    saved, planned = store.notebook.latest_notes(s.wcl_guild_id), store.notebook.latest_plans(s.wcl_guild_id)
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
                "paste into a file).")
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
    what's set, and the rest stays."""
    if found:
        wcl = ServerSettings(discord_guild_id, found["id"], found["name"], found["server"]["slug"],
                             found["server"]["region"]["slug"].lower())
    elif existing:
        wcl = existing
    else:
        raise ValueError("Give the guild's warcraftlogs.com link: open the guild on warcraftlogs.com and copy the "
                         "link from the address bar.")
    return replace(wcl, required_role_id=role_id or (existing and existing.required_role_id),
                   officer_role_id=officer_role_id or (existing and existing.officer_role_id))


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


def help_text(s: ServerSettings | None, admin: bool, in_dm: bool = False) -> str:
    """/help's text: what to ask, the commands this member can use, and how the server is set up (`s`, None or
    unset when it isn't; `admin`: the member may use /wipebot)."""
    parts = ["**What to ask:** " + ("DM me" if in_dm else "@mention me, reply to my answers, or DM me")
             + ". For example:\n" + "\n".join(f"- {q}" for q in EXAMPLES)
             + "\nTell me your character once (\"I'm Jekå\") and \"I\" means them from then on. A warcraftlogs.com "
               "report link asks about that report instead of the guild's latest."]
    budget = "the AI budget, how to chip in, and " if config.openrouter_key() else ""
    commands = ["`/help`: this", f"`/usage`: what's left of {budget}this hour's Warcraft Logs points"]
    if logincmd.callback_url():
        commands.append("`/login`: sign in with Warcraft Logs, so your own points pay for your questions")
    if admin:
        commands += ["`/wipebot setup`: connect the server to the guild on Warcraft Logs; who can chat; the officers",
                     "`/wipebot settings`: the setup, and the raid notes and plans saved",
                     "`/wipebot key`: answer on the server's own OpenRouter or Anthropic key",
                     "`/wipebot setup footer:`: end answers with what they cost, or not"]
    commands.append("Officers save a raid note or plan by pasting it to me.")
    parts.append("**Commands:**\n" + "\n".join(f"- {c}" for c in commands))
    if s is None or not s.set_up:
        parts.append("**Here:** not set up, so put a warcraftlogs.com report link in your question. An admin can run "
                     "`/wipebot setup` with the guild's warcraftlogs.com page: then I look at its latest raid by "
                     "myself, post after wipes and keep raid notes.")
    else:
        who = f"<@&{s.required_role_id}>" if s.required_role_id else "anyone in the server"
        parts.append(f"**Here:** the guild **{s.wcl_guild}** ({s.wcl_server}-{s.wcl_region.upper()}); who can chat: "
                     f"{who}. In DMs I name players; in channels I name nobody.")
    return "\n\n".join(parts)


def usage_text(rate: dict | None) -> str:
    """/usage's text from this hour's Warcraft Logs points (WCL.my_points: the bot's share; None when Warcraft Logs
    didn't say)."""
    if not rate:
        return "Warcraft Logs didn't say how many points are left this hour; try again in a minute."
    left = max(rate["limitPerHour"] - rate["pointsSpentThisHour"], 0)
    text = (f"**Warcraft Logs points:** {left:,.0f} of {rate['limitPerHour']:,} left this hour, shared by "
            f"everyone using this bot; the hour starts again in {rate['pointsResetIn'] // 60 + 1} min.")
    if db := rate.get("databaseLimitPerHour"):
        db_left = max(db - rate.get("databasePointsSpentThisHour", 0), 0)
        text += f"\n**Warcraft Logs database points** (rankings, other guilds): {db_left:,.0f} of {db:,} left."
    return text


async def server_usage(bot: WipeBot, discord_guild_id: int) -> str | None:
    """/usage's line about this server: what it spent this month, and its share of a shared copy's free answers or
    its own key; None on a bot that's neither."""
    n, spent, _ = bot.store.spent(allowance.month_start().timestamp(), discord_guild_id=discord_guild_id)
    mine = f"**This server:** {n} answers this month, ${spent:.2f}"
    if info := bot.store.server_key_info(discord_guild_id):
        return f"{mine}, on its own {keycmd.NAMES.get(info[0], info[0])} key (`/wipebot key`)."
    if bot.allowance and (left := await bot.allowance.left(discord_guild_id)):
        share, remaining = left
        return (f"{mine}: ${remaining:.2f} of its ${share:.2f} of free answers left, back in full on "
                f"<t:{int(allowance.next_month().timestamp())}:D>. An admin can add the server's own key with "
                "`/wipebot key`.")
    return None


def build_everyone(bot: WipeBot) -> list[app_commands.Command]:
    """/help, /usage and /login, for everyone."""
    @app_commands.command(name="help", description="What to ask wipebot, its commands, and how it's set up here")
    @app_commands.guild_only()
    async def show_help(interaction: discord.Interaction) -> None:
        gid = in_guild(interaction).id
        admin = bool(getattr(interaction.permissions, "manage_guild", False))
        await interaction.response.send_message(
            help_text(bot.store.settings(gid), admin), ephemeral=True)

    @app_commands.command(name="usage", description="What's left of the AI budget and the Warcraft Logs points "
                                                    "wipebot's answers share")
    @app_commands.guild_only()
    async def show_usage(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        points = usage_text(await bot.library.wcl.my_points())  # its share of the account's hour
        # the bot's OpenRouter key's credit limit is the AI budget raiders see
        text = f"{await budget_text(bot.store, key)}\n\n{points}" if (key := config.openrouter_key()) else points
        if mine := await logincmd.points_line(bot.store, bot.library.wcl, interaction.user.id):
            text = f"{text}\n{mine}"
        if here := await server_usage(bot, in_guild(interaction).id):
            text = f"{here}\n\n{text}"
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="login", description="Sign in with Warcraft Logs, so your own points pay for your "
                                                    "questions")
    @app_commands.describe(remove="Sign out and forget your Warcraft Logs token")
    @app_commands.guild_only()
    async def login(interaction: discord.Interaction, remove: bool = False) -> None:
        await logincmd.login_command(bot.store, interaction, remove)

    return [show_help, show_usage, login]


def build(bot: WipeBot) -> app_commands.Group:
    group = app_commands.Group(name="wipebot", description="Set up wipebot for this server",
                               guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    @group.command(name="setup", description="Connect this server to a guild on Warcraft Logs, or change a setting")
    @app_commands.describe(link="The guild's page on warcraftlogs.com (needed the first time)",
                           role="Role needed to chat with wipebot (default anyone)",
                           officers="Officers' role: save raid notes and plans, stop others' posts after wipes "
                                    "(default Manage Server)",
                           footer="End answers with a small line: their cost, Warcraft Logs points and free answers "
                                  "left")
    async def setup(interaction: discord.Interaction, link: str | None = None,
                    role: discord.Role | None = None, officers: discord.Role | None = None,
                    footer: bool | None = None) -> None:
        """Options left out keep what's set. The footer is the server's even before it's connected to a guild."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        gid = in_guild(interaction).id
        if footer is not None:
            bot.store.set_footer(gid, footer)
            if not (link or role or officers):
                await interaction.followup.send("Answers here now end with what they cost." if footer
                                                else "Answers here no longer end with what they cost.", ephemeral=True)
                return
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
        await interaction.followup.send("wipebot is set up:\n" + describe(settings),
                                        ephemeral=True)

    @group.command(name="key", description="Answer this server's questions on its own OpenRouter or Anthropic key")
    @app_commands.describe(remove="Go back to the shared free answers and forget the key")
    async def own_key(interaction: discord.Interaction, remove: bool = False) -> None:
        await keycmd.key_command(bot.store, interaction, remove, shared=bot.allowance is not None)

    @group.command(name="settings", description="Show how wipebot is set up in this server")
    async def show(interaction: discord.Interaction) -> None:
        gid = in_guild(interaction).id
        s = bot.store.settings(gid)
        text = (describe(s) + "\n\n" + describe_notes(bot.store, gid) if s
                else "wipebot isn't set up here yet: run `/wipebot setup`.")
        text += f"\n\nwipebot is free software (AGPL-3.0): its source is at <{server.source_url()}>."
        await interaction.response.send_message(text, ephemeral=True)

    return group


async def budget_text(store: Store, key: str) -> str:
    try:
        b = await budget.fetch(key)
        b.total += float(os.environ.get("WIPEBOT_SPENT_BEFORE") or 0)  # what the bot spent before this key
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return f"Couldn't read the budget from OpenRouter: {e}"
    month_start = datetime.datetime.now(datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return budget.describe(b, *store.answer_costs(month_start.timestamp()), config.budget_note())
