"""Where a boss room's map sits in the world (the game's UiMapAssignment table) and Warcraft Logs' images."""
import base64
import csv
import io

import httpx

from ..cache import DAY, cache, cache_key
from .fetch import KnowledgeError

# The game's UiMapAssignment table: wago.tools publishes every build's; threechest (a Mythic+ route planner) keeps a
# copy on GitHub, updated by hand, for when wago.tools is down.
UIMAP_SOURCES = (
    "https://wago.tools/db2/UiMapAssignment/csv",
    "https://raw.githubusercontent.com/acornellier/threechest/main/src/data/coordinates/uimapassignment.json",
)
WCL_ASSETS = "https://assets.rpglogs.com/img/warcraft/"


async def _uimap_rows() -> list[dict]:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": "Wipebot"}) as client:
        for url in UIMAP_SOURCES:
            try:
                r = await client.get(url)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            if url.endswith(".json"):
                return r.json()
            return list(csv.DictReader(io.StringIO(r.text)))
    raise KnowledgeError("the game's map table (UiMapAssignment) can't be reached: wago.tools and GitHub failed")


async def map_bounds(ui_map_id: int) -> tuple[float, float, float, float]:
    """Where map `ui_map_id`'s image sits, in Warcraft Logs' positions (hundredths of a yard): minX, maxX, minY,
    maxY. The game's table has it in yards with the axes the other way round (docs/reference/wcl-api.md)."""
    async def get() -> list[float]:
        for row in await _uimap_rows():
            if int(row["UiMapID"]) == ui_map_id and int(row.get("OrderIndex") or 0) == 0:
                r = [float(row[f"Region_{i}"]) for i in (0, 1, 3, 4)]
                return [-100 * r[3], -100 * r[1], 100 * r[0], 100 * r[2]]
        raise KnowledgeError(f"map {ui_map_id} isn't in the game's map table yet")
    return tuple(await cache().fetch(cache_key("uimap", ui_map_id), 7 * DAY, get))


async def wcl_image(path: str) -> bytes:
    """An image from Warcraft Logs' assets (`maps/2609-map.png?v=2`, `icons/Shaman-Restoration.jpg`), cached."""
    async def get() -> str:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "Wipebot"}) as client:
            r = await client.get(WCL_ASSETS + path)
            r.raise_for_status()
            return base64.b64encode(r.content).decode()
    try:
        return base64.b64decode(await cache().fetch(cache_key("wcl-image", path), 30 * DAY, get))
    except httpx.HTTPError as e:
        raise KnowledgeError(f"Warcraft Logs' image {path} can't be fetched: {e}") from e


async def map_image(ui_map_id: int) -> bytes:
    """A boss room's map image from Warcraft Logs."""
    return await wcl_image(f"maps/{ui_map_id}-map.png?v=2")
