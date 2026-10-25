"""Wipebot for many users at once (`wipebot serve`): the tools over Streamable HTTP at /mcp for claude.ai and ChatGPT,
each user signed in with Warcraft Logs (signin.py), on a server of its own built from server.py's tools.

Over HTTP the tools read Warcraft Logs with the client credentials only: a user login saved in warcraft_cli would show
its private reports to everyone, so the server won't start with one, nor read with one saved later. Added here:

- `my_characters`: the signed-in user's characters, so "how did I do?" needs no name.
- `save_note_or_plan` (server.allow_saving): a raid note or plan an officer pasted, saved for their guild. Officers
  are the characters at guild rank 0 to WIPEBOT_OFFICER_RANK (signin.Access; by default WoW's Guild Master and
  Officer ranks), as Warcraft Logs had them at sign-in or the last membership check (within the hour).
- The views, an MCP App (core/report/views.py, the page in core/app/): `pull_summary` draws the pull, asking the
  app-only `view_data` for what to draw; `pull_map` opens the replay, which asks the app-only `pull_replay` for the
  whole pull and loads the room's map and the icons from /img/ (Warcraft Logs' public assets). `pull_map` with `plan`
  on one pull opens it with the plan's spots at each moment it checked.
- The connector and the skill as a Claude plugin to download, at /plugin.zip (plugin.py).
- For whoever serves it: a website (`site`'s routes) at /, and each tool call to `record`.
"""
import functools
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable
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
from wipebot.core.report import FactError, maps, views

from . import plugin
from .signin import CALLBACK_PATH, CONSENT_PATH, Access, SignIn

# Claude caches an app's page by its address: a changed page gets a new one. Any older address still gets today's
# page (`_any_views_address`), so a host with an older tool list doesn't break.
VIEWS_APP = views.page_uri()
IMG_PATH = re.compile(r"maps/\d+-map\.png|icons/[A-Za-z]+-[A-Za-z]+\.jpg|bosses/\d+-icon\.jpg")
LATEST_RAID_S = 600  # the default report: looked up this often
log = logging.getLogger(__name__)
# A website's routes, given who may use the server and its MCP URL (for a deployment that embeds the server: `serve`).
Site = Callable[[Access, str], list[Route]]


def app(signin: SignIn, host: str = "127.0.0.1", record: server.Record | None = None, site: Site | None = None,
        limits: server.UserLimits | None = None) -> Starlette:
    """The HTTP app: /mcp behind sign-in, the sign-in endpoints, /img/ and `site`'s pages. Each tool call goes to
    `record`, when given, and counts against the user's `limits`."""
    public = signin.public_url
    def guilds() -> list[tuple[str, str, str]]:  # as looked up: a guild given by id has its name once found
        if signin.access.anyone:  # open to anyone: the signed-in user's own guilds
            return _own_guilds(signin)
        return [(g.display_name(), g.realm, g.region) for g in signin.access.guilds if g.name]
    library = server.Library()
    library.latest = _latest_raid(library, signin)
    setup = server.Setup(library, server.Remote(
        guilds=guilds, user_name=lambda subject: (signin.user(int(subject)) or (f"account {subject}", []))[0],
        record=record,
        guild_ids=lambda subject: _guild_ids(signin, subject),
        wcl_token=lambda subject: signin.wcl_token(int(subject)), limits=limits))
    mcp = server.new_server(
        website_url=public,
        auth=AuthSettings(issuer_url=public, resource_server_url=signin.resource,  # type: ignore[arg-type]
                          validate_token_resource=True,
                          client_registration_options=ClientRegistrationOptions(enabled=True),
                          revocation_options=RevocationOptions(enabled=True)),
        auth_server_provider=signin, extensions=[_views_app(public, setup)])
    shown = {"ui": {"resourceUri": VIEWS_APP}, "openai/outputTemplate": VIEWS_APP}
    # A host shows a tool's view on every call: only tools about one pull have one (raid_plan mostly has no pull).
    server.register(mcp, setup, meta={"pull_map": shown, "pull_summary": shown},
                    notes={"pull_map": "In Claude, the user gets a replay of the pull to play instead of the picture "
                                       "(not with all_pulls); with plan, the plan's spots on it and a button per "
                                       "moment.",
                           "pull_summary": "In Claude and ChatGPT, the user also sees the pull drawn: boss health "
                                           "and raid damage taken over time, phases, cooldowns and deaths, with its "
                                           "replay a click away.",
                           "raid_plan": "In Claude, the user sees no map from raid_plan: for the pull against the "
                                        "plan, call pull_map with plan, which shows its replay."})
    mcp.add_tool(server.guarded(_my_characters(signin), setup), name="my_characters",
                 title=server.title("my_characters"), annotations=server.READ_ONLY, structured_output=False,
                 meta={"openai/profile": True})  # who the signed-in user is: ChatGPT tells connected accounts apart
    server.allow_saving(mcp, setup, _officer(signin))
    _any_views_address(mcp, public)
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
    try:
        limits = user_limits()
    except ValueError as e:
        problems.append(str(e))
    if problems:
        raise SystemExit("Not set or not right (.env.example says what each is):\n" + "\n".join(
            f"  {p}" for p in problems))
    assert credentials and public_url
    for name in ("wipebot.mcp.signin", "wipebot.calls", __name__):  # who signed in or was turned away, called what
        logging.getLogger(name).setLevel(logging.INFO)
    signin = SignIn(public_url, access, *credentials)
    log.info("serving on %s:%d as %s, for %s", host, port, public_url, signin.access)
    # Only the reverse proxy in front (WIPEBOT_TRUSTED_PROXIES, comma-separated IPs or networks) may say which client
    # and scheme a request came from.
    uvicorn.run(app(signin, host, record, site, limits), host=host, port=port, log_level="warning", proxy_headers=True,
                forwarded_allow_ips=os.environ.get("WIPEBOT_TRUSTED_PROXIES") or "127.0.0.1")


def user_limits() -> server.UserLimits | None:
    """WIPEBOT_POINTS_PER_USER and WIPEBOT_CALLS_PER_USER: what one user may ask in an hour, for a server many guilds
    share (unset: no limit); on the new point system (WIPEBOT_WCL_USER_POINTS) only the points the account pays
    count. ValueError says what's wrong with them."""
    found: dict[str, float] = {}
    for name in ("WIPEBOT_POINTS_PER_USER", "WIPEBOT_CALLS_PER_USER"):
        if raw := os.environ.get(name, "").strip():
            try:
                found[name] = float(raw)
            except ValueError:
                found[name] = 0
            if found[name] <= 0:
                raise ValueError(f"{name}: how many one user may spend in an hour, e.g. 200, not {raw!r}")
    if not found:
        return None
    calls = found.get("WIPEBOT_CALLS_PER_USER")
    return server.UserLimits(found.get("WIPEBOT_POINTS_PER_USER"), int(calls) if calls else None,
                             wcl.Budget.from_env().user_points)


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


def _own_guilds(signin: SignIn) -> list[tuple[str, str, str]]:
    """The signed-in user's guilds (name, realm, region), as their characters were at sign-in or the last check."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    chars = (signin.user(int(token.subject)) or ("", []))[1] if token and token.subject else []
    return list(dict.fromkeys((c.guild, c.guild_realm, c.guild_region) for c in chars
                              if c.guild and c.guild_realm and c.guild_region))


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
        if not ids and not signin.access.anyone:  # a sign-in from before guild ids were kept
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
        where = "claimed on Warcraft Logs" if signin.access.anyone else "in the guild"
        if not found or not found[1]:
            raise FactError(f"the signed-in user has no character {where}: ask which character is theirs")
        chars = ", ".join(c.where() + (f" of {c.guild}" if signin.access.anyone and c.guild else "")
                          for c in found[1])
        return (f"The user's characters {where}: {chars}. With several, the one in the report is the one "
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


# ---------- the views (an MCP App: report/views.py, the page in core/app/) ----------

def _views_csp(public_url: str) -> ResourceCsp:
    return ResourceCsp(resource_domains=[public_url])  # the room's map and the icons, from /img/


def _views_app(public_url: str, setup: server.Setup) -> Apps:
    """The views' page and the tools only it calls."""
    apps = Apps()
    apps.add_html_resource(VIEWS_APP, views.page(), name=VIEWS_APP.removeprefix("ui://"), title="Wipebot",
                           csp=_views_csp(public_url), prefers_border=False)

    # All only read (the log note changes nothing a user sees): ChatGPT lists a tool not marked so as Write
    @apps.tool(resource_uri=VIEWS_APP, visibility=["app"], structured_output=False, name="view_data",
               annotations=server.READ_ONLY)
    @functools.partial(server.guarded, setup=setup)
    async def view_data(view: Annotated[str, Field(description="which view: pull")],
                        pull: server.Pull = None, boss: server.Boss = None,
                        report: server.Report_ = None) -> CallToolResult:
        """What a view draws, for the pull its tool was asked about."""
        return await server.view_data(view, pull, boss, report)

    @apps.tool(resource_uri=VIEWS_APP, visibility=["app"], structured_output=False, name="pull_replay",
               annotations=server.READ_ONLY)
    @functools.partial(server.guarded, setup=setup)
    async def pull_replay(report: str, pull: int, boss: str, at_ms: int = 0,
                          players: list[str] | None = None) -> CallToolResult:
        """The whole pull for the replay: every player's and the boss's track, deaths, and when the wipe was
        decided."""
        data = await server.replay_data(report, pull, boss, at_ms, players)
        data["img_base"] = public_url + "/img/"
        _handed_out.update([data["map"]["image"], *(x["icon"] for x in data["bosses"] + data["players"])])
        return CallToolResult(content=[TextContent(type="text", text=f"the replay of {data['title']}")],
                              structured_content=data)

    @apps.tool(resource_uri=VIEWS_APP, visibility=["app"], structured_output=False, name="viewer_log",
               annotations=server.READ_ONLY)
    @functools.partial(server.guarded, setup=setup)  # which logs the note with its user
    async def viewer_log(note: Annotated[str, Field(max_length=500)]) -> str:
        """A note from a view for the server's log: script errors, and where a replay opened."""
        return "ok"

    return apps


def _any_views_address(mcp: MCPServer, public_url: str) -> None:
    """Every older versioned address of the views' page, the replay viewer's before it included, gets today's page."""
    meta = {"ui": {"csp": _views_csp(public_url).model_dump(by_alias=True, exclude_none=True),
                   "prefersBorder": False}}

    @mcp.resource("ui://wipebot/views-{version}.html", name="views-any", mime_type=APP_MIME_TYPE, meta=meta)
    def any_version(version: str) -> str:
        return views.page()

    @mcp.resource("ui://wipebot/pull-map-{version}.html", name="pull-map-any", mime_type=APP_MIME_TYPE, meta=meta)
    def any_replay_version(version: str) -> str:
        return views.page()


def _plugin(signin: SignIn) -> Callable[[Request], Awaitable[Response]]:
    """/plugin.zip: this connector and the skill as a Claude plugin (plugin.py), built once."""
    zipped = plugin.plugin_zip("Wipebot", str(signin.access), signin.resource)

    async def download(_request: Request) -> Response:
        return Response(zipped, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="wipebot-plugin.zip"'})
    return download


async def _health(_request: Request) -> Response:
    return Response("ok", media_type="text/plain")


_handed_out: set[str] = set()  # the images a replay named (`pull_replay`): the only ones /img/ fetches
_failed: dict[str, float] = {}  # an image that couldn't be fetched -> when to try again
IMG_RETRY_S = 600


async def _img(request: Request) -> Response:
    """A Warcraft Logs asset a replay named, for the views: a room's map (as WebP), a spec or a boss icon."""
    path = request.path_params["path"]
    if not IMG_PATH.fullmatch(path) or path not in _handed_out or _failed.get(path, 0) > time.time():
        return Response(status_code=404)
    try:
        data, media_type = await maps.asset(path)
    except (knowledge.KnowledgeError, OSError):  # not an image (PIL's UnidentifiedImageError is an OSError)
        _failed[path] = time.time() + IMG_RETRY_S
        return Response(status_code=404)
    return Response(data, media_type=media_type,
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
