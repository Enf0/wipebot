"""Slash commands for server admins: /wipebot setup, /wipebot settings (with the raid notes and plans saved),
/wipebot names, /wipebot raidstrats (the login the guild's raid plans are fetched with, where the host set up a
raidstrats.gg app). Officers save a note or plan
by pasting it to the bot (bot.py).

Only members with Manage Server see them (Discord enforces this), and server owners can change that in
Server Settings > Integrations.
"""
from dataclasses import replace
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from wipebot.core import raidstrats, server
from wipebot.core.accounts import parse_guild_link
from wipebot.core.report import FactError
from wipebot.core.report import note as notes
from wipebot.core.report import plan as plans
from wipebot.core.wcl import WCLError

from .store import ServerSettings, Store

if TYPE_CHECKING:
    from .bot import WipeBot

MAX_NOTE_BYTES = 200_000  # a whole raid's note is some tens of kB; a plan's export a few


def looks_like_note(text: str) -> bool:
    """A raid note (NSRT, from Viserio) or a raid plan (raidstrats.gg's export)."""
    return bool(notes.HEADER.search(text) or plans.EXPORT.search(text))


def _guild(store: Store, discord_guild_id: int) -> int:
    """The server's guild on Warcraft Logs, whose notes and plans are the server's."""
    s = store.settings(discord_guild_id)
    if s is None:
        raise FactError("this server isn't set up yet: run `/wipebot setup` first, so it's kept for your guild")
    return s.wcl_guild_id


def save_pasted(store: Store, discord_guild_id: int, text: str, saved_by: str, at: float | None = None) -> str:
    """A pasted raid plan or raid note saved for the server's guild (server.save_pasted); what was saved, for the
    officer. A paste that's neither, or a server not set up, raises FactError."""
    return server.save_pasted(store.notebook, _guild(store, discord_guild_id), text, saved_by, at)


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


def describe_login(store: Store, discord_guild_id: int) -> str:
    """The guild's raidstrats.gg login, for /wipebot raidstrats."""
    login = store.notebook.login(_guild(store, discord_guild_id))
    if login is None:
        return ("Raid plans aren't fetched from raidstrats.gg: they're used as pasted. Log in below with a "
                "raidstrats.gg account that can open the guild's plans (yours, as an officer), and wipebot fetches "
                "them whenever an answer needs one.")
    text = (f"Raid plans are fetched from raidstrats.gg as **{login.username}**, connected by {login.connected_by} "
            f"<t:{int(login.connected_at)}:R>")
    text += f", last fetched <t:{int(login.fetched_at)}:R>." if login.fetched_at else "."
    if login.problem:
        text += f"\n**Problem:** {login.problem}."
    return text + ("\nLog in below to connect another account, or use `disconnect: True` to stop fetching."
                   if login.working else "\nLog in below to connect again.")


async def connect_raidstrats(store: Store, discord_guild_id: int, pasted: str, state: str, by: str) -> str:
    """The officer's raidstrats.gg login saved for the server's guild from the address their browser was sent back
    to, and the guild's plans fetched with it; what happened, for the officer."""
    guild = _guild(store, discord_guild_id)
    try:
        username, token = await raidstrats.connect(pasted, state)
    except raidstrats.RaidstratsError as e:
        return f"Not connected: {e}."
    store.notebook.save_login(guild, username, token, by)
    await raidstrats.sync(store.notebook, guild)
    login = store.notebook.login(guild)
    if login and login.problem:
        return (f"Connected as **{username}**, but fetching the plans failed: {login.problem}. Wipebot tries again "
                "on the next question.")
    planned = store.notebook.latest_plans(guild)
    return (f"Connected as **{username}**. Plans it can open:\n"
            + ("\n".join(f"- **{p.name}** ({p.boss})" for p in planned) if planned else "none yet")
            + "\nWipebot fetches them again whenever an answer needs a plan, and keeps every version.")


class PasteAddress(discord.ui.Modal, title="raidstrats.gg login"):
    address: discord.ui.TextInput = discord.ui.TextInput(
        label="The address your browser shows now", placeholder="http://localhost:8765/callback?code=...",
        style=discord.TextStyle.short, max_length=1000)

    def __init__(self, store: Store, state: str):
        super().__init__()
        self.store, self.state = store, state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await connect_raidstrats(self.store, in_guild(interaction).id, str(self.address.value), self.state,
                                         interaction.user.display_name)
        await interaction.followup.send(reply, ephemeral=True)


class RaidstratsLogin(discord.ui.View):
    """The button after the login link, which opens the box to paste the address in."""

    def __init__(self, store: Store, url: str, state: str):
        super().__init__(timeout=600)
        self.store, self.state = store, state
        self.add_item(discord.ui.Button(label="1. Log in on raidstrats.gg", url=url))

    @discord.ui.button(label="2. Paste the address", style=discord.ButtonStyle.primary)
    async def paste(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PasteAddress(self.store, self.state))


async def raidstrats_command(store: Store, interaction: discord.Interaction, disconnect: bool) -> None:
    """/wipebot raidstrats: the login's status with a link to log in, or with `disconnect`, the login dropped."""
    gid = in_guild(interaction).id
    try:
        if disconnect:
            store.notebook.drop_login(_guild(store, gid))
            await interaction.response.send_message(
                "Disconnected: plans are no longer fetched, and the saved versions stay. To revoke it on "
                "raidstrats.gg too, remove Wipebot under \"Access you've given\" on its developers page.",
                ephemeral=True)
            return
        status = describe_login(store, gid)
        url, state = raidstrats.login_url()
    except (FactError, raidstrats.RaidstratsError) as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    await interaction.response.send_message(
        status + "\n\nTo log in: open the link and approve Wipebot, with \"In-game import\". Your browser then "
        "opens a page that doesn't load (localhost): that's expected. Copy that page's address, then press "
        "Paste the address.", view=RaidstratsLogin(store, url, state), ephemeral=True)


def names_command(store: Store, gid: int, channel: object, on: bool) -> str:
    """/wipebot names in a channel (or a thread: its channel): names on or off there; what to tell the admin."""
    if not store.settings(gid):
        return "wipebot isn't set up here yet: run `/wipebot setup` first."
    channel_id = getattr(channel, "parent_id", None) or getattr(channel, "id", 0)
    store.set_naming(gid, channel_id, on)
    if on:
        return (f"Names are on in <#{channel_id}> and its threads: wipebot names players there and says who made "
                "the costly mistakes, as in DMs. Everyone who can read the channel sees it.")
    return f"Names are off in <#{channel_id}>: wipebot names nobody there, as in every channel."


def describe(s: ServerSettings, named: set[int]) -> str:
    """The server's setup, with the channels where names are on (`named`)."""
    role = f"<@&{s.required_role_id}>" if s.required_role_id else "anyone in the server"
    officers = f"<@&{s.officer_role_id}>" if s.officer_role_id else "members who can manage the server"
    channels = ", ".join(f"<#{c}>" for c in sorted(named)) or "none"
    return (f"**Warcraft Logs guild:** {s.wcl_guild} ({s.wcl_server}-{s.wcl_region.upper()})\n"
            f"**Who can chat** (by @mention or DM): {role}\n"
            f"**Officers** (save the raid note or plan by pasting it to wipebot, and change or stop anyone's posts "
            f"after wipes): {officers}\n"
            f"**Names on** (wipebot names players there, as in DMs; `/wipebot names`): {channels}")


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


def build(bot: WipeBot) -> app_commands.Group:
    group = app_commands.Group(name="wipebot", description="Set up wipebot for this server",
                               guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    @group.command(name="setup", description="Connect this server to a guild on Warcraft Logs, or change a setting")
    @app_commands.describe(link="The guild's page on warcraftlogs.com (needed the first time)",
                           role="Role needed to chat with wipebot (default anyone)",
                           officers="Officers' role: save raid notes and plans, stop others' posts after wipes "
                                    "(default Manage Server)")
    async def setup(interaction: discord.Interaction, link: str | None = None,
                    role: discord.Role | None = None, officers: discord.Role | None = None) -> None:
        """Options left out keep what's set."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        gid = in_guild(interaction).id
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
        await interaction.followup.send("wipebot is set up:\n" + describe(settings, bot.store.naming_channels(gid)),
                                        ephemeral=True)

    if raidstrats.client():  # only where the host set up a raidstrats.gg app (docs/setup.md)
        @group.command(name="raidstrats", description="Fetch the guild's raid plans from raidstrats.gg with your "
                                                      "login there, instead of pasting them")
        @app_commands.describe(disconnect="Stop fetching the plans and forget the login")
        async def raidstrats_login(interaction: discord.Interaction, disconnect: bool = False) -> None:
            await raidstrats_command(bot.store, interaction, disconnect)

    @group.command(name="names", description="Whether wipebot names players in this channel, as in DMs")
    @app_commands.describe(on="On: in this channel and its threads, wipebot says who made the costly mistakes")
    async def names(interaction: discord.Interaction, on: bool) -> None:
        await interaction.response.send_message(names_command(bot.store, in_guild(interaction).id,
                                                              interaction.channel, on), ephemeral=True)

    @group.command(name="settings", description="Show how wipebot is set up in this server")
    async def show(interaction: discord.Interaction) -> None:
        gid = in_guild(interaction).id
        s = bot.store.settings(gid)
        text = (describe(s, bot.store.naming_channels(gid)) + "\n\n" + describe_notes(bot.store, gid) if s
                else "wipebot isn't set up here yet: run `/wipebot setup`.")
        text += f"\n\nwipebot is free software (AGPL-3.0): its source is at <{server.source_url()}>."
        await interaction.response.send_message(text, ephemeral=True)

    return group
