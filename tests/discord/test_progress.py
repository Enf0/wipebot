import io
from types import SimpleNamespace

import discord
import pytest
from PIL import Image

from wipebot.discord import progress
from wipebot.discord.host import sdk_tools
from wipebot.discord.progress import DOING, doing, spinner, spinner_gif
from wipebot.discord.prompt import BOT_TOOLS


async def test_every_tool_says_what_it_does():
    assert {t.name for t in await sdk_tools()} | {t.name for t in BOT_TOOLS} <= set(DOING)


def test_a_call_names_its_boss_pull_and_spec_never_a_player():
    assert doing("pull_summary", {"boss": "Coiled Altar", "pull": 7}) == "Summing up the pull (Coiled Altar, pull 7)"
    assert doing("player_pull", {"player": "Torvek", "pull": "best wipe"}) == (
        "Reading a player's pull (the best wipe)")
    assert doing("mechanic_hits", {"boss": "Sszorak", "all_pulls": True}) == (
        "Counting mechanic hits (Sszorak, every pull)")
    assert doing("spec_guide", {"spec": "Holy Paladin"}) == "Reading the spec guide (Holy Paladin)"
    assert doing("raid_note", {"boss": "**<@1> Sszorak**"}) == "Reading the raid note (1 Sszorak)"  # no markdown
    assert doing("save_note_or_plan", {}) == "Save raid note or plan"  # not in DOING: its title


def test_the_spinner_is_an_animated_gif_an_emoji_may_be():
    data = spinner_gif()
    image = Image.open(io.BytesIO(data))
    assert (image.format, image.size, getattr(image, "n_frames", 1)) == ("GIF", (128, 128), 8)
    assert len(data) < 256_000  # Discord's limit for an emoji


class FakeClient:
    def __init__(self, emojis=(), fail=False):
        self.emojis, self.fail, self.created = list(emojis), fail, []

    async def fetch_application_emojis(self):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="down"), "down")
        return self.emojis

    async def create_application_emoji(self, *, name, image):
        self.created.append(name)
        return "<a:wipebot_spinner:2>"


class Emoji:
    name = progress.SPINNER

    def __str__(self):
        return "<a:wipebot_spinner:1>"


@pytest.mark.parametrize(("client", "shown", "created"), [
    (FakeClient([Emoji()]), "<a:wipebot_spinner:1>", []),  # uploaded before
    (FakeClient(), "<a:wipebot_spinner:2>", [progress.SPINNER]),  # the first time
    (FakeClient(fail=True), progress.FALLBACK_SPINNER, []),
])
async def test_the_spinner_is_the_bots_own_emoji(client, shown, created):
    assert await spinner(client) == shown and client.created == created
