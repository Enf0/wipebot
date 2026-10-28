"""Slash commands. For everyone: /help (what to ask, the commands, how the server is set up; also "help" said to
the bot, bot.py) and /usage (what's left of the AI budget, on an OpenRouter key, and of the hour's Warcraft Logs
points). For server admins: /wipebot setup (also the cost footer), /wipebot settings (with the raid notes and plans
saved), /wipebot key, /wipebot news (the channel for what's new in each release). Officers save a note or plan by
pasting it to the bot (bot.py). docs/features.md lists them all (a test checks). Viserio Cooldowns and raidstrats.gg,
experimental, are connected by the host on the command line (__main__.py).

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

from . import allowance, budget, config, keycmd, logincmd, news
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
                     "`/wipebot news`: post what's new in each release of mine to a channel",
                     "`/wipebot setup footer:`: end answers with what they cost, or not",
                     "`/wipebot setup steps:`: end answers with the steps they went through, or not",
                     "`/wipebot setup language:`: the language of posts after wipes and of what's new"]
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
    @app_commands.describe(remove="Sign out and forget your Warcraft Logs token",
                           private="Let Wipebot read your private reports too (with remove: stop it)")
    @app_commands.guild_only()
    async def login(interaction: discord.Interaction, remove: bool = False, private: bool = False) -> None:
        await logincmd.login_command(bot.store, interaction, remove, private)

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
                                  "left",
                           steps="End answers with a small line of the steps they went through (what they looked at)",
                           language="The server's language, e.g. Norwegian: for posts and what's new (questions get "
                                    "answers in theirs)")
    async def setup(interaction: discord.Interaction, link: str | None = None,
                    role: discord.Role | None = None, officers: discord.Role | None = None,
                    footer: bool | None = None, language: str | None = None, steps: bool | None = None) -> None:
        """Options left out keep what's set. The footer, steps and language are the server's even before it's connected
        to a guild."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        gid = in_guild(interaction).id
        if (told := server_options(bot.store, gid, footer, language, steps)) and (told[0] == NO_LANGUAGE
                                                                          or not (link or role or officers)):
            await interaction.followup.send("\n".join(told), ephemeral=True)
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
        channel, language = bot.store.news_channel(gid), bot.store.language(gid)
        text += "\n\n**What's new** in each release: " + (f"posted in <#{channel}>" if channel
                                                            else "not posted (`/wipebot news` turns it on)")
        text += "\n**Language** of posts: " + (language or "none set (`/wipebot setup language:`), so English")
        text += f"\n\nwipebot is free software (AGPL-3.0): its source is at <{server.source_url()}>."
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="news", description="Post what's new in each release of wipebot to a channel")
    @app_commands.describe(channel="Where to post (default this channel)", stop="Stop posting what's new")
    async def news_command(interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                           stop: bool = False) -> None:
        await news_channel(bot, interaction, channel, stop)

    usage = app_commands.Group(name="usage", description="The bot's AI budget", parent=group)

    @usage.command(name="post", description="Post the AI budget to this channel, for everyone to see")
    async def post_usage(interaction: discord.Interaction) -> None:
        if not (key := config.openrouter_key()):
            await interaction.response.send_message("This bot has no AI budget to post.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        # a shared copy's key is many servers' budget: post this server's share of it
        text = (await share_text(bot, in_guild(interaction).id) if bot.allowance
                else await budget_text(bot.store, key, with_bar=True))
        await interaction.followup.send(text)

    return group


async def news_channel(bot: WipeBot, interaction: discord.Interaction, channel: discord.TextChannel | None,
                       stop: bool) -> None:
    """/wipebot news: post what's new in each release to `channel` (else this one), or `stop`; with the latest
    release's post, as it looks, seen only by the admin."""
    guild = in_guild(interaction)
    if stop:
        bot.store.set_news_channel(guild.id, None)
        await interaction.response.send_message("What's new in each release is no longer posted here.",
                                                ephemeral=True)
        return
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel | discord.Thread):
        await interaction.response.send_message("Pick a text channel to post in.", ephemeral=True)
        return
    can = target.permissions_for(guild.me)
    if not (can.view_channel and (can.send_messages_in_threads if isinstance(target, discord.Thread)
                                  else can.send_messages)):
        await interaction.response.send_message(f"I can't post in {target.mention}: let me see it and send "
                                                "messages there first.", ephemeral=True)
        return
    bot.store.set_news_channel(guild.id, target.id)
    await interaction.response.send_message(f"From the next release on, what's new is posted in {target.mention}"
                                            ". The latest release's post looks like this:", ephemeral=True)
    if saved := bot.store.state(news_key(bot.store.language(guild.id))) or bot.store.state(news_key(None)):
        await interaction.followup.send(view=news.Post.from_json(saved).layout(bot.name), ephemeral=True)


def news_key(language: str | None) -> str:
    """Where the latest release's post in `language` is kept (Store.state)."""
    return f"news_post:{(language or news.ENGLISH).lower()}"


async def share_text(bot: WipeBot, discord_guild_id: int) -> str:
    """/wipebot usage post on a shared copy: this server's share of this month's free answers, with a bar."""
    if bot.store.server_key_info(discord_guild_id):
        return "This server answers on its own key (`/wipebot key`), so it has no share of free answers to post."
    if not bot.allowance or not (left := await bot.allowance.left(discord_guild_id)):
        return "Couldn't read this server's share of free answers."
    share, remaining = left
    return (f"**This server's free answers:** ${remaining:.2f} of its ${share:.2f} left this month, back in full "
            f"on <t:{int(allowance.next_month().timestamp())}:D>.\n{budget.bar(share, remaining)}")


async def budget_text(store: Store, key: str, with_bar: bool = False) -> str:
    try:
        b = await budget.fetch(key)
        b.total += float(os.environ.get("WIPEBOT_SPENT_BEFORE") or 0)  # what the bot spent before this key
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return f"Couldn't read the budget from OpenRouter: {e}"
    month_start = datetime.datetime.now(datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    note = None if with_bar else config.budget_note()  # the post leaves out how to chip in
    return budget.describe(b, *store.answer_costs(month_start.timestamp()), note, with_bar)
