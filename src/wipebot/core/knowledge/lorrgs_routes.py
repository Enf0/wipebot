"""A stopgap for warcraft_cli 0.6.0's Lorrgs client (lorrgs_cli): on 2 November 2026 Lorrgs moved its game data
under the game version (`/api/specs` became `/api/retail/specs`), and every Lorrgs command answers "Invalid
Route". `patch()` sends the client's requests to the new routes; `fetch` applies it in the Lorrgs command's process.
Remove this module once a warcraft_cli release has the new routes."""
import re

# The routes Lorrgs now serves under a game version (its /api/openapi.json, 3 November 2026)
VERSIONED = re.compile(r"^/api/(roles|classes|specs|zones|seasons|bosses|spells|trinkets|spec_ranking|comp_ranking"
                       r"|builds|talent_trees)(/|$)")
GAME_VERSION = "retail"


def route(path: str) -> str:
    """A path as Lorrgs serves it now: '/api/specs/x/spells' -> '/api/retail/specs/x/spells'."""
    return f"/api/{GAME_VERSION}{path[4:]}" if VERSIONED.match(path) else path


def patch() -> None:
    from lorrgs_cli.client import LorrgsClient

    get = LorrgsClient._get

    def _get(self, path: str, **kwargs):
        return get(self, route(path), **kwargs)
    LorrgsClient._get = _get  # type: ignore[method-assign]
