import io
from types import SimpleNamespace
from typing import cast

import discord
import pytest
from PIL import Image

from wipebot.core.knowledge import KnowledgeError
from wipebot.discord import emojis
from wipebot.discord.emojis import MARKERS, SPINNER, marker_png, spinner_gif, sync


def test_the_spinner_is_an_animated_gif_an_emoji_may_be():
    data = spinner_gif()
    image = Image.open(io.BytesIO(data))
    assert (image.format, image.size, getattr(image, "n_frames", 1)) == ("GIF", (128, 128), 8)
    assert len(data) < 256_000  # Discord's limit for an emoji


@pytest.mark.parametrize("marker", MARKERS)
def test_each_raid_marker_is_drawn_in_its_colour(marker):
    image = Image.open(io.BytesIO(marker_png(marker))).convert("RGBA")
    assert image.size == (128, 128) and cast(tuple, image.getpixel((0, 0)))[3] == 0  # clear around it
    assert MARKERS[marker] in {cast(tuple, px)[:3] for px in image.get_flattened_data()}


def http_error():
    return discord.HTTPException(SimpleNamespace(status=500, reason="down"), "down")


class FakeClient:
    def __init__(self, have=(), fail=()):
        self.have, self.fail, self.created = dict(have), set(fail), []

    async def fetch_application_emojis(self):
        if "fetch" in self.fail:
            raise http_error()
        return [Named(n, i) for n, i in self.have.items()]

    async def create_application_emoji(self, *, name, image):
        if name in self.fail:
            raise http_error()
        self.created.append((name, image))
        return Named(name, 100 + len(self.created))


class Named:
    def __init__(self, name, id):
        self.name, self.id = name, id

    def __str__(self):
        return f"<:{self.name}:{self.id}>"


@pytest.fixture
def specs(monkeypatch):
    async def trees():
        return [{"className": "Death Knight", "specName": "Blood"}, {"className": "Paladin", "specName": "Holy"}]

    async def image(path):
        if "Paladin-Holy" in path:
            raise KnowledgeError("not there")
        return f"image {path}".encode()
    monkeypatch.setattr(emojis.knowledge, "talent_trees", trees)
    monkeypatch.setattr(emojis.knowledge, "wcl_image", image)


async def test_the_emojis_missing_are_uploaded_once(specs):
    client, found = FakeClient({SPINNER: 1, "marker_star": 2}, fail={"marker_skull"}), {}
    await sync(cast(discord.Client, client), found)
    created = dict(client.created)
    assert created["DeathKnight_Blood"] == b"image icons/DeathKnight-Blood.jpg"
    assert created["Paladin"] == b"image icons/Paladin.jpg" and "Paladin_Holy" not in created  # no image
    assert SPINNER not in created and "marker_star" not in created and "marker_moon" in created
    assert found[SPINNER] == "<:wipebot_spinner:1>" and "marker_skull" not in found  # Discord said no
    assert found["DeathKnight"].startswith("<:DeathKnight:")


async def test_without_discord_there_are_no_emojis(specs):
    found = {}
    await sync(cast(discord.Client, FakeClient(fail={"fetch"})), found)
    assert found == {}
