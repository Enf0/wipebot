"""/wipebot key: a server brings its own model key, so its answers no longer come from the copy's shared allowance.

Two ways in, from one private reply:
- Sign in with OpenRouter (its OAuth PKCE flow): the admin approves wipebot on openrouter.ai, which sends the
  browser to WIPEBOT_OPENROUTER_CALLBACK (default a localhost address that doesn't load) with a code; they paste
  that address back, and the bot swaps the code for a key on *their* OpenRouter account, with their own limit,
  which they can revoke there.
- Paste a key: an OpenRouter key, or an Anthropic one. Other providers come with #32.

The key is checked with one cheap call, kept encrypted (keys.py), never shown again, and used for that server's
answers (Context.env).
"""
import base64
import datetime
import hashlib
import os
import re
import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse

import discord
import httpx

from .config import OPENROUTER_URL
from .keys import ServerKey

if TYPE_CHECKING:
    from .store import Store

AUTH_URL = "https://openrouter.ai/auth"
# OpenRouter allows a localhost callback, for apps without a page of their own
CALLBACK = "http://localhost:3000/wipebot"
CODE = re.compile(r"^[\w-]{8,}$")
NAMES = {"openrouter": "OpenRouter", "anthropic": "Anthropic"}


def callback_url() -> str:
    """Where OpenRouter sends the admin's browser after they approve: WIPEBOT_OPENROUTER_CALLBACK, a page that shows
    the code to paste, or else a localhost address whose address bar holds it."""
    return os.environ.get("WIPEBOT_OPENROUTER_CALLBACK") or CALLBACK


def login_url(verifier: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return f"{AUTH_URL}?" + urlencode({"callback_url": callback_url(), "code_challenge": challenge,
                                       "code_challenge_method": "S256"})


def code_from(pasted: str) -> str | None:
    """The code in a pasted address (`...?code=...`), or a code pasted alone."""
    pasted = pasted.strip()
    if found := parse_qs(urlparse(pasted).query).get("code"):
        return found[0]
    return pasted if CODE.match(pasted) else None


async def exchange(code: str, verifier: str) -> str:
    """The OpenRouter key a sign-in's code stands for; httpx.HTTPError when OpenRouter refuses it."""
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(f"{OPENROUTER_URL}/v1/auth/keys",
                            json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"})
        r.raise_for_status()
        return r.json()["key"]


def provider_of(key: str) -> str | None:
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    return None


async def check(key: ServerKey) -> str | None:
    """Why the key doesn't work, from one call that costs nothing; None when it does."""
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            if key.provider == "openrouter":
                r = await http.get(f"{OPENROUTER_URL}/v1/key", headers={"Authorization": f"Bearer {key.key}"})
            else:
                r = await http.get("https://api.anthropic.com/v1/models",
                                   headers={"x-api-key": key.key, "anthropic-version": "2023-06-01"})
        except httpx.HTTPError as e:
            return f"couldn't reach {key.provider}: {e}"
    if r.status_code in (401, 403):
        return "it was refused: check it's whole and not revoked"
    if r.is_error:
        return f"{key.provider} answered {r.status_code}"
    return None


async def save(store: Store, interaction: discord.Interaction, key: ServerKey) -> str:
    if why := await check(key):
        return f"That key doesn't work: {why}. Nothing was saved."
    assert interaction.guild_id is not None
    store.save_server_key(interaction.guild_id, key, interaction.user.id)
    return (f"Saved. This server's questions now run on its own {NAMES[key.provider]} key, with no limit from "
            "wipebot; set a spend limit on the key itself. `/wipebot key remove:True` goes back to the shared free "
            "answers.")



class PasteSignIn(discord.ui.Modal, title="Sign in with OpenRouter"):
    address: discord.ui.TextInput = discord.ui.TextInput(
        label="The address your browser shows now", placeholder=f"{CALLBACK}?code=...",
        style=discord.TextStyle.short, max_length=1000)

    def __init__(self, store: Store, verifier: str):
        super().__init__()
        self.store, self.verifier = store, verifier

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not (code := code_from(str(self.address.value))):
            await interaction.followup.send("That address has no code in it: paste the whole address after you "
                                            "approved wipebot on OpenRouter.", ephemeral=True)
            return
        try:
            key = await exchange(code, self.verifier)
        except (httpx.HTTPError, KeyError) as e:
            await interaction.followup.send(f"OpenRouter didn't give a key for that code ({e}). Sign in again from "
                                            "`/wipebot key`.", ephemeral=True)
            return
        await interaction.followup.send(await save(self.store, interaction, ServerKey("openrouter", key)),
                                        ephemeral=True)


class PasteKey(discord.ui.Modal, title="Your own model key"):
    key: discord.ui.TextInput = discord.ui.TextInput(
        label="An OpenRouter or Anthropic API key", placeholder="sk-or-... or sk-ant-...",
        style=discord.TextStyle.short, max_length=300)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        value = str(self.key.value).strip()
        if (provider := provider_of(value)) is None:
            await interaction.followup.send("That isn't an OpenRouter key (sk-or-...) or an Anthropic key "
                                            "(sk-ant-...). Nothing was saved.", ephemeral=True)
            return
        await interaction.followup.send(await save(self.store, interaction, ServerKey(provider, value)),
                                        ephemeral=True)


class KeyChoices(discord.ui.View):
    def __init__(self, store: Store):
        super().__init__(timeout=900)
        self.store, self.verifier = store, secrets.token_urlsafe(48)
        self.add_item(discord.ui.Button(label="1. Sign in with OpenRouter", url=login_url(self.verifier), row=0))

    @discord.ui.button(label="2. Paste the address", style=discord.ButtonStyle.primary, row=0)
    async def signed_in(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PasteSignIn(self.store, self.verifier))

    @discord.ui.button(label="Or paste a key", style=discord.ButtonStyle.secondary, row=1)
    async def paste(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PasteKey(self.store))


def status(store: Store, discord_guild_id: int) -> str:
    row = store.server_key_info(discord_guild_id)
    if row is None:
        return "This server answers from wipebot's shared free answers."
    provider, added_by, added_at = row
    when = datetime.datetime.fromtimestamp(added_at, datetime.UTC)
    return (f"This server answers on its own {NAMES.get(provider, provider)} key, added by <@{added_by}> "
            f"<t:{int(when.timestamp())}:R>.")


async def key_command(store: Store, interaction: discord.Interaction, remove: bool) -> None:
    """/wipebot key: the server's key status with the ways to add one, or with `remove`, the key forgotten."""
    assert interaction.guild_id is not None
    if remove:
        dropped = store.drop_server_key(interaction.guild_id)
        await interaction.response.send_message(
            "Removed: this server's questions go back to the shared free answers. To revoke the key too, delete it "
            "on openrouter.ai or in Anthropic's Console." if dropped else "This server has no key of its own.",
            ephemeral=True)
        return
    await interaction.response.send_message(
        status(store, interaction.guild_id) + "\n\nTo answer on your own key, with no limit from wipebot: sign in "
        "with OpenRouter and paste the address it ends on (it may not load, that's fine), or paste an OpenRouter or "
        "Anthropic key. It's kept encrypted and never shown again.", view=KeyChoices(store), ephemeral=True)
