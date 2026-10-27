"""/login: a raider signs in with Warcraft Logs, so their own Warcraft Logs points pay first for what they ask.

The sign-in is the authorization code flow of the client the bot reads with (core/accounts.py, as the remote
server's), with the code pasted back: Warcraft Logs sends the browser to WIPEBOT_WCL_CALLBACK, a page that shows its
address (one of the client's redirect URLs, which the host adds on warcraftlogs.com), and the raider pastes that
address into Discord. The token is kept encrypted (keys.py), read with only for that raider's questions
(Context.wcl_token, wcl.reading_as), and its scope reads only what the bot reads anyway. Without WIPEBOT_WCL_CALLBACK
the bot offers no sign-in.
"""
import os
import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import discord

from wipebot.core import accounts, wcl

from .keycmd import code_from

if TYPE_CHECKING:
    from .store import Store


def callback_url() -> str | None:
    """Where Warcraft Logs sends the raider's browser after they sign in: WIPEBOT_WCL_CALLBACK, a page that shows the
    address to paste; None when the host set none up."""
    return os.environ.get("WIPEBOT_WCL_CALLBACK") or None


class Choices(discord.ui.View):
    """The sign-in link and the button to paste the address it ends on, for one sign-in (its PKCE verifier)."""

    def __init__(self, store: Store, callback: str, client_id: str, secret: str):
        super().__init__(timeout=900)
        self.store, self.callback, self.client_id, self.secret = store, callback, client_id, secret
        self.verifier, self.challenge = accounts.pkce()
        self.state = secrets.token_urlsafe(16)
        steps = [self.signed_in]  # after the sign-in link, in the order they're used
        self.clear_items()
        self.add_item(discord.ui.Button(label="1. Sign in with Warcraft Logs", row=0, url=accounts.authorize_url(
            client_id, callback, self.state, self.challenge)))
        for item in steps:
            self.add_item(item)

    @discord.ui.button(label="2. Paste the address", style=discord.ButtonStyle.primary, row=0)
    async def signed_in(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PasteSignIn(self))


class PasteSignIn(discord.ui.Modal, title="Sign in with Warcraft Logs"):
    address: discord.ui.TextInput = discord.ui.TextInput(
        label="The address your browser shows now", placeholder="https://...?code=...",
        style=discord.TextStyle.short, max_length=2000)

    def __init__(self, choices: Choices):
        super().__init__()
        self.choices = choices

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await sign_in(self.choices, interaction.user.id, str(self.address.value)),
                                        ephemeral=True)


async def sign_in(choices: Choices, user_id: int, pasted: str) -> str:
    """The pasted address's code swapped for the raider's token, kept; what to tell them."""
    state = parse_qs(urlparse(pasted.strip()).query).get("state")
    if state and state[0] != choices.state:
        return "That address is from another sign-in: start again from `/login`."
    if not (code := code_from(pasted)):
        return "That address has no code in it: paste the whole address after you signed in on Warcraft Logs."
    try:
        account = await accounts.login(choices.client_id, choices.secret, code, choices.verifier, choices.callback)
    except accounts.AccountError as e:
        return f"Warcraft Logs didn't sign you in ({e}). Start again from `/login`."
    choices.store.save_login(user_id, account.id, account.name, account.token, account.expires)
    return (f"Signed in as **{account.name}** on Warcraft Logs. Your own Warcraft Logs points now pay first for what "
            "you ask; `/login remove:True` signs you out.")


def status(store: Store, user_id: int) -> str:
    if found := store.login(user_id):
        return f"You're signed in as **{found[0]}** on Warcraft Logs: your own points pay first for what you ask."
    return "You aren't signed in with Warcraft Logs: your questions use the bot's points, shared by everyone."


USER_RATE = "query { rateLimitData { userHasRemainingPoints userPointsResetIn } }"


async def points_line(store: Store, reader: wcl.WCL, user_id: int) -> str | None:
    """/usage's line about the asker's own Warcraft Logs points, asked of Warcraft Logs with their token (spent in
    Claude and ChatGPT too); None on a bot without sign-in. Warcraft Logs says only whether some are left and when
    they come back, and nothing before the account is on its new point system."""
    if not callback_url():
        return None
    if not (found := store.login(user_id)):
        return "**Your Warcraft Logs points:** not signed in. `/login` lets your own points pay for your questions."
    line = f"**Your Warcraft Logs points:** signed in as {found[0]}, so yours pay first for your questions"
    try:
        with wcl.reading_as(found[1]):
            rate = (await reader.query(USER_RATE))["rateLimitData"]
    except (wcl.WCLError, KeyError, TypeError):
        return line + "; Warcraft Logs didn't say how many you have left."
    left, reset = rate.get("userHasRemainingPoints"), rate.get("userPointsResetIn")
    if left is None:
        return line + (". Warcraft Logs counts them against the bot's until it moves the bot's account to its new "
                       "point system.")
    back = f", back in {int(reset) // 60 + 1} min" if reset else ""
    return line + ("; you have some left this hour." if left else f"; yours are used up{back}, so the bot's pay.")


async def login_command(store: Store, interaction: discord.Interaction, remove: bool) -> None:
    """/login: the raider's sign-in status with the way to sign in, or with `remove`, their sign-in forgotten."""
    if remove:
        dropped = store.drop_login(interaction.user.id)
        await interaction.response.send_message(
            "Signed out: your questions use the bot's points again. To revoke Wipebot's access too, do it on "
            "warcraftlogs.com under your profile's applications." if dropped else "You weren't signed in.",
            ephemeral=True)
        return
    callback, client = callback_url(), wcl.client_credentials()
    if not callback or not client:
        await interaction.response.send_message(
            "Signing in with Warcraft Logs isn't set up on this bot: your questions use its points.", ephemeral=True)
        return
    await interaction.response.send_message(
        status(store, interaction.user.id) + "\n\nTo sign in: open Warcraft Logs, approve, and paste the address it "
        "ends on here. The bot keeps your token encrypted and reads only public reports with it.",
        view=Choices(store, callback, *client), ephemeral=True)
