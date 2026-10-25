"""The bot's own (application) emojis: the progress line's spinner, the raid markers (the game's), an icon per
class and spec, and one per boss ability of the current expansion's raids (Wowhead's). Discord keeps them for the
app, usable in every server it's in; the bot uploads the ones missing when it starts, so an image is drawn or
downloaded once, ever, and a new tier's boss abilities come with the first start after it. None is deleted: an emoji
gone shows as its name in every message that used it. A drawing changed needs a new name to replace the old."""
import io
import logging
import math
from collections.abc import Awaitable, Callable

import discord
from PIL import Image, ImageDraw, ImageFilter

from wipebot.core import knowledge
from wipebot.core.knowledge import KnowledgeError

log = logging.getLogger(__name__)

SPINNER = "wipebot_spinner"
SIZE = 128  # Discord's size for an emoji
LIMIT, WARN_AT = 2000, 1900  # an app's emojis, and when the log warns: old ones are only dropped by hand
# The raid markers in the game's order ({rt1} is the star), with their colours
MARKERS = {"star": (255, 214, 0), "circle": (255, 132, 20), "diamond": (214, 76, 255), "triangle": (46, 204, 64),
           "moon": (196, 220, 240), "square": (30, 144, 255), "cross": (232, 32, 42), "skull": (245, 245, 245)}
OUTLINE = (24, 24, 24)


# The game's own marker textures (Interface/TargetingFrame/UI-RaidTargetingIcon_1..8.blp), by file id, in MARKERS'
# order. The drawn ones (marker_png) stay on Discord as "marker_<name>" for the messages that used them.
MARKER_FILES = range(137001, 137009)


def marker_emoji(marker: str) -> str:
    return f"raid_{marker}"


def ability_key(name: str) -> str:
    """A boss ability's emoji by its name, for a link to another id of it (a cast and its debuff): never an emoji's
    own name, which can't hold a colon."""
    return f"ability:{name.lower()}"


def spell_emoji(spell_id: int | str) -> str:
    """A boss ability's emoji: its icon, by spell id."""
    return f"spell_{spell_id}"


def spec_emoji(class_name: str, spec: str = "") -> str:
    """WCL's forms ("DeathKnight", "BeastMastery") -> the emoji's name; a class's own without a spec."""
    return f"{class_name}_{spec}" if spec else class_name


def spinner_gif() -> bytes:
    """The spinner: eight gold dots in a ring, shrinking towards the tail, turning one dot a frame. Drawn at four
    times the size and scaled down; a GIF's pixel is either clear or not, so the edges are cut at half."""
    big, dots = SIZE * 4, 8
    frames = []
    for frame in range(dots):
        image = Image.new("L", (big, big), 0)
        draw = ImageDraw.Draw(image)
        for i in range(dots):
            angle = 2 * math.pi * (frame - i) / dots - math.pi / 2
            r = 52 * (1 - i / dots) + 12
            x, y = big / 2 + 180 * math.cos(angle), big / 2 + 180 * math.sin(angle)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
        mask = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).point(lambda v: 1 if v >= 128 else 0)
        out = mask.convert("P")
        out.putpalette([0, 0, 0, 240, 178, 50] + [0, 0, 0] * 254)
        frames.append(out)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=90, loop=0, transparency=0,
                   disposal=2)
    return buf.getvalue()


def _star(c: float, r: float, points: int, inner: float) -> list[tuple[float, float]]:
    return [(c + (r if i % 2 == 0 else r * inner) * math.cos(math.pi * i / points - math.pi / 2),
             c + (r if i % 2 == 0 else r * inner) * math.sin(math.pi * i / points - math.pi / 2))
            for i in range(2 * points)]


def marker_png(marker: str) -> bytes:
    """A raid marker drawn as the game shows it, in its colour with a dark outline that shows on light and dark
    themes alike: drawn at four times the size and scaled down."""
    big = SIZE * 4
    c, w = big / 2, 24  # the centre, the outline's width
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = MARKERS[marker]

    def poly(points: list[tuple[float, float]]) -> None:
        draw.polygon(points, fill=fill, outline=OUTLINE, width=w)
    if marker == "star":
        poly(_star(c, 240, 4, 0.42))
    elif marker == "circle":
        draw.ellipse((40, 40, big - 40, big - 40), fill=fill, outline=OUTLINE, width=w)
    elif marker == "diamond":
        poly([(c, 24), (big - 90, c), (c, big - 24), (90, c)])
    elif marker == "triangle":
        poly([(c, 40), (big - 24, big - 70), (24, big - 70)])
    elif marker == "moon":  # a disc less one moved up and right, outlined by widening it
        moon = Image.new("L", (big, big), 0)
        cut = ImageDraw.Draw(moon)
        cut.ellipse((50, 50, big - 50, big - 50), fill=255)
        cut.ellipse((170, 0, big + 40, big - 150), fill=0)
        image.paste(OUTLINE, mask=moon.filter(ImageFilter.MaxFilter(2 * w + 1)))
        image.paste(fill, mask=moon)
    elif marker == "square":
        draw.rectangle((60, 60, big - 60, big - 60), fill=fill, outline=OUTLINE, width=w)
    elif marker == "cross":
        for a, b in (((70, 70), (big - 70, big - 70)), ((big - 70, 70), (70, big - 70))):
            draw.line((a, b), fill=OUTLINE, width=150)
        for a, b in (((80, 80), (big - 80, big - 80)), ((big - 80, 80), (80, big - 80))):
            draw.line((a, b), fill=fill, width=104)
    elif marker == "skull":
        draw.rectangle((150, 300, big - 150, big - 40), fill=fill, outline=OUTLINE, width=w)  # the jaw
        draw.ellipse((60, 30, big - 60, 380), fill=fill, outline=OUTLINE, width=w)
        draw.rectangle((150 + w, 300, big - 150 - w, 340), fill=fill)  # the head over the jaw's top edge
        for x in (170, big - 170):
            draw.ellipse((x - 62, 170, x + 62, 290), fill=OUTLINE)  # the eyes
        draw.polygon([(c, 300), (c + 34, 360), (c - 34, 360)], fill=OUTLINE)  # the nose
        for x in (c - 60, c, c + 60):
            draw.line(((x, big - 120), (x, big - 40)), fill=OUTLINE, width=16)  # the teeth
    buf = io.BytesIO()
    image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


async def _wanted(spells: dict[int, str]) -> list[tuple[str, Callable[[], Awaitable[bytes]]]]:
    """Every emoji the bot uses, (name, its image), the spinner first: the raid markers from the game's textures,
    specs and classes from Raidbots' list of specs with Warcraft Logs' icons (as the maps of a pull show them), and
    the boss abilities `spells` with Wowhead's."""
    async def drawn(image: bytes) -> bytes:
        return image

    wanted: list[tuple[str, Callable[[], Awaitable[bytes]]]] = [(SPINNER, lambda: drawn(spinner_gif()))]
    wanted += [(marker_emoji(m), lambda m=m, f=f: _game_marker(m, f))
               for m, f in zip(MARKERS, MARKER_FILES, strict=True)]
    try:
        specs = [(t["className"].replace(" ", ""), t["specName"].replace(" ", ""))
                 for t in await knowledge.talent_trees()]
    except KnowledgeError as e:
        log.warning("no class or spec emojis: %s", e)
        specs = []
    for class_name in dict.fromkeys(c for c, _ in specs):
        wanted.append((spec_emoji(class_name), lambda c=class_name: knowledge.wcl_image(f"icons/{c}.jpg")))
    for class_name, spec in specs:
        wanted.append((spec_emoji(class_name, spec),
                       lambda c=class_name, s=spec: knowledge.wcl_image(f"icons/{c}-{s}.jpg")))
    wanted += [(spell_emoji(s), lambda s=s: knowledge.spell_icon(s)) for s in spells]
    return wanted


async def _boss_spells() -> dict[int, str]:
    try:
        return await knowledge.raid_boss_spells()
    except KnowledgeError as e:
        log.warning("no boss ability emojis: %s", e)
        return {}


async def _game_marker(marker: str, file_id: int) -> bytes:
    """A raid marker as the game shows it, from its texture; the drawn one when the texture can't be had."""
    try:
        buf = io.BytesIO()
        Image.open(io.BytesIO(await knowledge.game_file(file_id))).convert("RGBA").save(buf, "PNG")
        return buf.getvalue()
    except (KnowledgeError, OSError) as e:
        log.warning("the game's %s marker can't be had, so it's drawn: %s", marker, e)
        return marker_png(marker)


async def sync(client: discord.Client, emojis: dict[str, str]) -> None:
    """Fill `emojis` (name -> how a message writes it) with the bot's emojis, uploading those it doesn't have yet.
    One that can't be made is left out: the text shows without it."""
    try:
        emojis.update({e.name: str(e) for e in await client.fetch_application_emojis()})
        uploaded = 0
        spells = await _boss_spells()
        for name, image in await _wanted(spells):
            if name in emojis:
                continue
            try:
                emojis[name] = str(await client.create_application_emoji(name=name, image=await image()))
                uploaded += 1
            except (KnowledgeError, discord.HTTPException) as e:
                log.warning("no emoji %s: %s", name, e)
        if uploaded:
            log.info("uploaded %d emojis", uploaded)
        emojis.update({ability_key(name): emojis[spell_emoji(s)] for s, name in spells.items()
                       if name and spell_emoji(s) in emojis})
        if len(emojis) >= WARN_AT:
            log.warning("the bot has %d of Discord's %d emojis: drop the oldest boss abilities' (spell_...) before "
                        "it runs out", len(emojis), LIMIT)
    except discord.HTTPException as e:
        log.warning("the bot's emojis can't be read: %s", e)
