"""Wipebot for many users at once (`wipebot serve`): the tools over Streamable HTTP at /mcp for claude.ai and ChatGPT,
each user signed in with Warcraft Logs (signin.py), on a server of its own built from server.py's tools.

Over HTTP the tools read Warcraft Logs with the client credentials only: a user login saved in warcraft_cli would show
its private reports to everyone, so the server won't start with one, nor read with one saved later. Added here:

- `my_characters`: the signed-in user's characters, so "how did I do?" needs no name.
- `save_note_or_plan` (server.allow_saving): a raid note or plan an officer pasted, saved for their guild. Officers
  are the characters at guild rank 0 to WIPEBOT_OFFICER_RANK (signin.Access; by default WoW's Guild Master and
  Officer ranks), as Warcraft Logs had them at sign-in or the last membership check (within the hour).
- The pull-map replay, an MCP App: `pull_map` points at the viewer (pull_map_app.html), which asks the app-only
  `pull_replay` for the whole pull and loads the room's map and the icons from /img/ (Warcraft Logs' public assets).
  `raid_plan` on one pull opens it too, with the plan's spots at each moment it checked.
- The connector and the skill as a Claude plugin to download, at /plugin.zip (plugin.py).
- For whoever serves it: a website (`site`'s routes) at /, and each tool call to `record`.
"""
import asyncio
import functools
import hashlib
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Annotated

from mcp.server.apps import APP_MIME_TYPE, Apps, ResourceCsp
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from wipebot.core import knowledge, server, wcl
from wipebot.core.library import LatestRaids
from wipebot.core.report import FactError, maps

from . import plugin
from .signin import CALLBACK_PATH, CONSENT_PATH, Access, SignIn

PULL_MAP_HTML = resources.files("wipebot.mcp").joinpath("pull_map_app.html").read_text(encoding="utf-8")
# Claude caches an app's page by its address: a changed page gets a new one. Any older address still gets today's
# page (`_pull_map_app`), so a host with an older tool list doesn't break.
PULL_MAP_APP = f"ui://wipebot/pull-map-{hashlib.sha256(PULL_MAP_HTML.encode()).hexdigest()[:10]}.html"
IMG_PATH = re.compile(r"maps/\d+-map\.png|icons/[A-Za-z]+-[A-Za-z]+\.jpg|bosses/\d+-icon\.jpg")
LATEST_RAID_S = 600  # the default report: looked up this often
log = logging.getLogger(__name__)
# A website's routes, given who may use the server and its MCP URL (for a deployment that embeds the server: `serve`).
Site = Callable[[Access, str], list[Route]]


def app(signin: SignIn, host: str = "127.0.0.1", record: server.Record | None = None, site: Site | None = None
        ) -> Starlette:
    """The HTTP app: /mcp behind sign-in, the sign-in endpoints, /img/ and `site`'s pages. Each tool call goes to
    `record`, when given."""
    public = signin.public_url
    def guilds() -> list[tuple[str, str, str]]:  # as looked up: a guild given by id has its name once found
        return [(g.display_name(), g.realm, g.region) for g in signin.access.guilds if g.name]
    library = server.Library()
    library.latest = _latest_raid(library, signin)
    setup = server.Setup(library, server.Remote(
        guilds=guilds, user_name=lambda subject: (signin.user(int(subject)) or (f"account {subject}", []))[0],
        record=record,
        guild_ids=lambda subject: _guild_ids(signin, subject),
        wcl_token=lambda subject: signin.wcl_token(int(subject))))
    mcp = server.new_server(
        website_url=public,
        auth=AuthSettings(issuer_url=public, resource_server_url=signin.resource,  # type: ignore[arg-type]
                          validate_token_resource=True,
                          client_registration_options=ClientRegistrationOptions(enabled=True),
                          revocation_options=RevocationOptions(enabled=True)),
        auth_server_provider=signin, extensions=[_pull_map_app(public, setup)])
    viewer = {"ui": {"resourceUri": PULL_MAP_APP}, "openai/outputTemplate": PULL_MAP_APP}
    server.register(mcp, setup, meta={"pull_map": viewer, "raid_plan": viewer},
                    notes={"pull_map": "In Claude, the user gets a replay of the pull to play instead of the picture "
                                       "(not with all_pulls).",
                           "raid_plan": "In Claude, for one pull the user gets the replay of the pull with the plan's "
                                        "spots on it and a button per moment, instead of the picture."})
    mcp.add_tool(server.guarded(_my_characters(signin), setup), name="my_characters",
                 title=server.title("my_characters"), annotations=server.READ_ONLY, structured_output=False)
    server.allow_saving(mcp, setup, _officer(signin))
    _any_pull_map_address(mcp, public)
    mcp.custom_route(CALLBACK_PATH, ["GET"])(signin.callback)
    mcp.custom_route(CONSENT_PATH, ["GET", "POST"])(signin.consent)
    mcp.custom_route("/img/{path:path}", ["GET"])(_img)
    mcp.custom_route("/health", ["GET"])(_health)  # compose.yml's healthcheck
    mcp.custom_route(plugin.PATH, ["GET"])(_plugin(signin))
    for route in site(signin.access, signin.resource) if site else []:
        mcp.custom_route(route.path, ["GET"])(route.endpoint)
    starlette = mcp.streamable_http_app(host=host, transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", public.split("://", 1)[1]],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", public]))
    _advertise_client_documents(starlette, mcp)
    return starlette


def serve(host: str, port: int, public_url: str | None = None, site: Site | None = None,
          record: server.Record | None = None) -> None:
    """Run `app` (`wipebot serve`). A saved Warcraft Logs browser login would show its private reports to everyone,
    so the server refuses to start with one. `site` and `record` are for a deployment that embeds the server in its
    own program, with a website and a record of the calls; `wipebot serve` passes neither."""
    import uvicorn

    if wcl.saved_login():
        raise SystemExit("a Warcraft Logs user login is saved in warcraft_cli's store; over HTTP every user would "
                         "read its private reports. Serve from an account without one.")
    wcl.client_credentials_only()  # and one saved while it runs isn't read either
    problems = []  # every missing or wrong setting at once, as the bot says them
    if not (credentials := wcl.client_credentials()):
        problems.append("WCL_CLIENT_ID and WCL_CLIENT_SECRET: a client from https://www.warcraftlogs.com/api/clients "
                        "(redirect URL <public URL>/wcl/callback): Wipebot reads with it, and users sign in with it")
    try:
        public_url = _public_url(public_url, host, port)
    except ValueError as e:
        problems.append(str(e))
    try:
        access = Access.from_env()
    except ValueError as e:
        problems.append(str(e))
    if problems:
        raise SystemExit("Not set or not right (.env.example says what each is):\n" + "\n".join(
            f"  {p}" for p in problems))
    assert credentials and public_url
    for name in ("wipebot.mcp.signin", "wipebot.calls", __name__):  # who signed in or was turned away, called what
        logging.getLogger(name).setLevel(logging.INFO)
    signin = SignIn(public_url, access, *credentials)
    log.info("serving on %s:%d as %s, for %d allowed guilds", host, port, public_url, len(signin.access.guilds))
    # Only the reverse proxy in front (WIPEBOT_TRUSTED_PROXIES, comma-separated IPs or networks) may say which client
    # and scheme a request came from.
    uvicorn.run(app(signin, host, record, site), host=host, port=port, log_level="warning", proxy_headers=True,
                forwarded_allow_ips=os.environ.get("WIPEBOT_TRUSTED_PROXIES") or "127.0.0.1")


def _public_url(url: str | None, host: str, port: int) -> str:
    """Where users reach the server, which sign-in names as its own (the issuer, the Warcraft Logs callback). Only
    a server listening on this machine alone may go without one, for trying it out: http://localhost:<port>."""
    here = ("127.0.0.1", "localhost", "::1")
    if not url:
        if host not in here:
            raise ValueError("WIPEBOT_PUBLIC_URL (or --public-url), e.g. https://wipebot.example.com: the URL "
                             "users reach the server at, which sign-in sends them back to")
        return f"http://localhost:{port}"
    u = urllib.parse.urlsplit(url)
    if not u.hostname or u.query or u.fragment or not (
            u.scheme == "https" or u.scheme == "http" and u.hostname in here):  # as the MCP SDK's issuer
        raise ValueError(f"WIPEBOT_PUBLIC_URL must be an HTTPS URL as https://wipebot.example.com (sign-in needs "
                         f"HTTPS), not {url!r}")
    return url.rstrip("/")


def _guild_ids(signin: SignIn, subject: str) -> set[int]:
    """The Warcraft Logs ids of a signed-in user's allowed guilds."""
    return {c.guild_id for c in (signin.user(int(subject)) or ("", []))[1] if c.guild_id}


def _latest_raid(library: server.Library, signin: SignIn):
    """The report the tools use when given none: the latest raid night (as the bot's) of the signed-in user's own
    guild, else the allowed guilds', looked up again every LATEST_RAID_S."""
    raids = LatestRaids(library, LATEST_RAID_S)

    async def latest() -> str | None:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        ids = _guild_ids(signin, token.subject) if token and token.subject else set()
        if not ids:  # a sign-in from before guild ids were kept
            await signin.access.resolve()
            ids = {g.id for g in signin.access.guilds if g.id is not None}
        return await raids.of(ids) if ids else None
    return latest


# ---------- who's asking ----------

def _my_characters(signin: SignIn):
    async def my_characters() -> str:
        """Who the user is: the names, realms and region of their own WoW characters in the guild, the ones they
        claimed on Warcraft Logs. Call it whenever the user says I, me, my or mine, or asks about their character,
        before asking them for a name."""
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        found = signin.user(int(token.subject)) if token and token.subject else None
        if not found or not found[1]:
            raise FactError("the signed-in user has no character in the guild: ask which character is theirs")
        chars = ", ".join(c.where() for c in found[1])
        return (f"The user's characters in the guild: {chars}. With several, the one in the report is the one "
                "meant; if more than one or none of them is in it, ask which.")
    return my_characters


def _officer(signin: SignIn) -> Callable[[], tuple[str, int]]:
    """Setup.officer: the signed-in user's Warcraft Logs name and the guild where a character of theirs is an
    officer, which a note or plan they paste is saved for."""
    def officer() -> tuple[str, int]:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        name, chars = (signin.user(int(token.subject)) if token and token.subject else None) or ("", [])
        highest = signin.access.officer_rank
        ranked = [c for c in chars if c.guild_id and c.rank is not None and c.rank <= highest]
        guilds = sorted({c.guild_id for c in ranked if c.guild_id})
        if not guilds:
            raise FactError(f"not saved: only the guild's officers (guild ranks 0 to {highest}) save a raid note or "
                            "plan for the guild, and none of the user's characters is one. raid_note and raid_plan "
                            "still check a pasted one as pasted")
        if len(guilds) > 1:
            names = {g.id: str(g) for g in signin.access.guilds}
            raise FactError("not saved: the user is an officer in several guilds here ("
                            + ", ".join(names.get(g, str(g)) for g in guilds) + "), and which one it's for can't "
                            "be told. raid_note and raid_plan still check a pasted one as pasted")
        return name or ranked[0].name, guilds[0]
    return officer


# ---------- the pull-map replay (an MCP App) ----------

def _viewer_csp(public_url: str) -> ResourceCsp:
    return ResourceCsp(resource_domains=[public_url])  # the room's map and the icons, from /img/


def _pull_map_app(public_url: str, setup: server.Setup) -> Apps:
    """The viewer's page and the tools only it calls."""
    apps = Apps()
    apps.add_html_resource(PULL_MAP_APP, PULL_MAP_HTML, name=PULL_MAP_APP.removeprefix("ui://"), title="Pull map",
                           csp=_viewer_csp(public_url), prefers_border=False)

    @apps.tool(resource_uri=PULL_MAP_APP, visibility=["app"], structured_output=False, name="pull_replay")
    @functools.partial(server.guarded, setup=setup)
    async def pull_replay(report: str, pull: int, boss: str, at_ms: int = 0,
                          players: list[str] | None = None) -> CallToolResult:
        """The whole pull for the pull-map viewer: every player's and the boss's track, deaths, and when the wipe
        was decided."""
        rep = await server.lib().report(report)
        f = rep.fight(pull, boss)
        focus = {rep.in_pull(f, rep.player(n)).id for n in players or []}
        data = await maps.replay(rep, f, at_ms, focus)
        data["img_base"] = public_url + "/img/"
        _handed_out.update([data["map"]["image"], *(x["icon"] for x in data["bosses"] + data["players"])])
        return CallToolResult(content=[TextContent(type="text", text=f"the replay of {data['title']}")],
                              structured_content=data)

    @apps.tool(resource_uri=PULL_MAP_APP, visibility=["app"], structured_output=False, name="viewer_log")
    @functools.partial(server.guarded, setup=setup)  # which logs the note with its user
    async def viewer_log(note: Annotated[str, Field(max_length=500)]) -> str:
        """A note from the viewer for the server's log: script errors, and where a replay opened."""
        return "ok"

    return apps


def _any_pull_map_address(mcp: MCPServer, public_url: str) -> None:
    """Every older versioned address of the viewer gets today's page."""
    meta = {"ui": {"csp": _viewer_csp(public_url).model_dump(by_alias=True, exclude_none=True),
                   "prefersBorder": False}}

    @mcp.resource("ui://wipebot/pull-map-{version}.html", name="pull-map-any", mime_type=APP_MIME_TYPE, meta=meta)
    def any_version(version: str) -> str:
        return PULL_MAP_HTML


def _plugin(signin: SignIn) -> Callable[[Request], Awaitable[Response]]:
    """/plugin.zip: this connector and the skill as a Claude plugin (plugin.py), built once."""
    zipped = plugin.plugin_zip("Wipebot", " and ".join(str(g) for g in signin.access.guilds), signin.resource)

    async def download(_request: Request) -> Response:
        return Response(zipped, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="wipebot-plugin.zip"'})
    return download


async def _health(_request: Request) -> Response:
    return Response("ok", media_type="text/plain")


_webp_maps: dict[str, bytes] = {}  # the room maps, converted once
_handed_out: set[str] = set()  # the images a replay named (`pull_replay`): the only ones /img/ fetches
_failed: dict[str, float] = {}  # an image that couldn't be fetched -> when to try again
IMG_RETRY_S = 600


async def _img(request: Request) -> Response:
    """A Warcraft Logs asset a replay named, for the viewer: a room's map (as WebP), a spec or a boss icon."""
    path = request.path_params["path"]
    if not IMG_PATH.fullmatch(path) or path not in _handed_out or _failed.get(path, 0) > time.time():
        return Response(status_code=404)
    try:
        data = await knowledge.wcl_image(path + ("?v=2" if path.startswith("maps/") else ""))
        if path.startswith("maps/") and path not in _webp_maps:  # a second of CPU: off the event loop
            _webp_maps[path] = await asyncio.to_thread(maps.webp, data)
    except (knowledge.KnowledgeError, OSError):  # not an image (PIL's UnidentifiedImageError is an OSError)
        _failed[path] = time.time() + IMG_RETRY_S
        return Response(status_code=404)
    if path.startswith("maps/"):
        data = _webp_maps[path]
    return Response(data, media_type="image/webp" if path.startswith("maps/") else "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})


# ---------- sign-in metadata ----------

def _advertise_client_documents(starlette: Starlette, mcp: MCPServer) -> None:
    """The SDK's authorization server metadata, plus what it doesn't know we do: client ID metadata documents
    (CIMD) with public clients (`none`), which Claude and ChatGPT prefer to registering, and the `iss` on our
    redirects (RFC 9207), which gives ChatGPT its stable redirect URI. The SDK has no option for these, so its
    route is swapped for ours."""
    settings = mcp.settings.auth
    assert settings is not None
    meta = build_metadata(settings.issuer_url, settings.service_documentation_url,
                          settings.client_registration_options or ClientRegistrationOptions(),
                          settings.revocation_options or RevocationOptions()).model_dump(mode="json", exclude_none=True)
    meta["token_endpoint_auth_methods_supported"] = ["none", *meta["token_endpoint_auth_methods_supported"]]
    meta["client_id_metadata_document_supported"] = True
    meta["authorization_response_iss_parameter_supported"] = True

    async def metadata(_request: Request) -> Response:
        return JSONResponse(meta, headers={"Cache-Control": "public, max-age=3600"})
    path = "/.well-known/oauth-authorization-server"
    routes = starlette.router.routes
    i = next(i for i, r in enumerate(routes) if isinstance(r, Route) and r.path == path)
    routes[i] = Route(path, cors_middleware(metadata, ["GET", "OPTIONS"]), methods=["GET", "OPTIONS"])  # as the SDK
