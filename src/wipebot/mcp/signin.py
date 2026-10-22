"""Sign-in for the remote server (`wipebot serve`): Wipebot is the OAuth authorization server Claude and ChatGPT
talk to (the MCP SDK serves the metadata, /authorize, /token, /register and /revoke from this provider), and
Warcraft Logs upstream for who the user is (core/accounts.py).

    client ──> /authorize ──> /wcl/consent ──> Warcraft Logs login ──> /wcl/callback ── in the guild? ── no ──> page
                                                                                       │ yes
                                                                  client <── our code ─┘ ──> /token ──> our token

Who may use it: WIPEBOT_ALLOWED_GUILDS (`eu/argent-dawn/the-copper-lanterns:5`, comma-separated; `:5` lets in
ranks 0 to 5), each guild matched by its Warcraft Logs id; its officers, ranks 0 to WIPEBOT_OFFICER_RANK (default
1). The Warcraft Logs login uses the client the server reads with, WCL_CLIENT_ID and WCL_CLIENT_SECRET (the
authorization code flow with PKCE; its redirect URLs include <public URL>/wcl/callback). Membership is checked again
on every token refresh (an access token lasts an hour), so someone who leaves the guild loses access within about an
hour of Warcraft Logs seeing it.

Clients: Claude and ChatGPT name themselves by a client ID metadata document (CIMD), an HTTPS URL on their own
domain that we fetch (only from CLIENT_HOSTS); other apps register themselves (DCR, /register), for redirects to
their own machine (loopback) or Claude's and ChatGPT's callbacks only, so a client can't send users back anywhere
else. Before Warcraft Logs, the consent page names the client and where it sends the user back, and gives the browser
a cookie the callback asks for: a sign-in finishes only in the browser it was started in.

Tokens are stored as their SHA-256 in SQLite (WIPEBOT_SIGNIN_DB, default ~/.local/share/wipebot/signin.sqlite), with
the registered clients and each user's Warcraft Logs user id, name and guild characters, and their Warcraft Logs
token, encrypted with a key made from WCL_CLIENT_SECRET, nothing else. Their tool calls read Warcraft Logs with that
token (`wcl_token`), so their own Warcraft Logs points pay first; it goes when they lose access or have no grant left.
"""
import base64
import dataclasses
import hashlib
import html
import json
import logging
import os
import secrets
import sqlite3
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from wipebot.core import accounts, server
from wipebot.core.wcl import WCL, WCLError

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".local" / "share" / "wipebot" / "signin.sqlite"


def default_path() -> Path:
    """Where the sign-ins are: WIPEBOT_SIGNIN_DB, else DEFAULT_PATH."""
    return Path(os.environ.get("WIPEBOT_SIGNIN_DB") or DEFAULT_PATH)
LOGIN_TTL_S = 600  # from /authorize to the Warcraft Logs callback
CODE_TTL_S = 300
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 30 * 86400
REFRESH_GRACE_S = 30  # a used refresh token still works this long; used after that, its grant is revoked
MEMBERSHIP_GRACE_S = 86400  # with Warcraft Logs down, a user stays in this long after their last membership check
OFFICER_RANK = 1  # WIPEBOT_OFFICER_RANK's default: WoW's default Guild Master (0) and Officer (1) ranks
CALLBACK_PATH = "/wcl/callback"
CONSENT_PATH = "/wcl/consent"
COOKIE = "wipebot_signin"  # the browser's sign-in nonce, which each of its sign-ins is bound to
CLIENT_HOSTS = ("claude.ai", "chatgpt.com")  # whose client ID metadata documents we fetch
CLIENT_DOC_TTL_S = 86400
CLIENT_DOC_RETRY_S = 600  # a document that couldn't be fetched is tried again after this long
MAX_DOCUMENTS = 100  # documents remembered (Claude's and ChatGPT's are a handful, and go last)
REGISTERED_REDIRECTS = ("https://claude.ai/api/mcp/auth_callback", "https://chatgpt.com/connector_platform_oauth_redirect",
                        "https://chatgpt.com/connector/oauth/")  # the last one with a callback id after it
PURGE_EVERY_S = 600
UNUSED_CLIENT_S = 30 * 86400  # a registered client that hasn't got a token in this long is dropped,
NEVER_USED_CLIENT_S = 86400  # one that never got a token in this long
# Anyone may register a client or start a sign-in, so what's kept of each is small and their number capped: past
# the cap the oldest go (`_make_room`), so a flood can't turn everyone else away.
MAX_NEW_CLIENTS = MAX_LOGINS = 1000  # registered clients that never got a token; sign-ins under way
MAX_REDIRECTS, MAX_URL, MAX_NAME, MAX_SCOPE = 10, 2048, 60, 1024
MAX_STATE = 1024  # a client's `state`, echoed back to it
CLIENT_DOC_MAX_BYTES = 64 * 1024
GUILD_RETRY_S = 60  # an allowed guild Warcraft Logs didn't find is looked up again this often


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _loopback(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")


def _without_port(url: str) -> tuple[str, str | None, str]:
    u = urllib.parse.urlsplit(url)
    return u.scheme, u.hostname, u.path


class DocumentClient(OAuthClientInformationFull):
    """A client known by its metadata document. A loopback redirect (Claude Code's) matches on any port, as
    RFC 8252 7.3 asks for native apps: the document lists `http://localhost/callback`, the app adds its port."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and _loopback(str(redirect_uri)):
            if any(_loopback(str(r)) and _without_port(str(r)) == _without_port(str(redirect_uri))
                   for r in self.redirect_uris or []):
                return redirect_uri
            raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' not registered for client")
        return super().validate_redirect_uri(redirect_uri)


async def fetch_client_document(url: str) -> object:
    """A client ID metadata document, small and without redirects: read no further than CLIENT_DOC_MAX_BYTES."""
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as http:
        async with http.stream("GET", url, headers={"Accept": "application/json"}) as r:
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > CLIENT_DOC_MAX_BYTES:
                    raise ValueError(f"over {CLIENT_DOC_MAX_BYTES} bytes")
    return json.loads(body)


def document_client(url: str, doc: object) -> DocumentClient | None:
    """The client a metadata document describes, if it's one we accept: from CLIENT_HOSTS, naming itself by its
    own URL, sending users back only to its own host or to loopback. It authenticates as a public client (PKCE,
    no secret): ChatGPT's document offers private_key_jwt too and picks what we support."""
    u = urllib.parse.urlsplit(url)
    if (not isinstance(doc, dict) or u.scheme != "https" or u.hostname not in CLIENT_HOSTS
            or doc.get("client_id") != url):
        return None
    redirects = doc.get("redirect_uris")
    if (not isinstance(redirects, list) or not redirects or not all(
            isinstance(r, str) and (_loopback(r) or urllib.parse.urlsplit(r)[:2] == ("https", u.netloc))
            for r in redirects)):
        return None
    return DocumentClient(client_id=url, client_name=doc.get("client_name"), redirect_uris=redirects,
                          grant_types=doc.get("grant_types") or ["authorization_code", "refresh_token"],
                          response_types=["code"], token_endpoint_auth_method="none")


class Access:
    """Who may sign in: guilds, each with a highest rank. Each guild is matched by its Warcraft Logs id alone, looked
    up on first use (`find`: name, realm, region -> the guild or None), and again every GUILD_RETRY_S until it's
    found; meanwhile who isn't in another allowed guild can't be checked. Officers, who save raid notes and plans for
    their guild, are the ranks 0 to `officer_rank`."""

    def __init__(self, guilds: list[accounts.Guild],
                 find: Callable[[str, str, str], Awaitable[dict | None]] | None = None,
                 find_id: Callable[[int], Awaitable[dict | None]] | None = None, officer_rank: int = OFFICER_RANK):
        self.guilds, self.officer_rank = guilds, officer_rank
        self._find, self._find_id = find, find_id
        self._looked_up = 0.0

    @classmethod
    def from_env(cls) -> Access:
        """WIPEBOT_ALLOWED_GUILDS' guilds and WIPEBOT_OFFICER_RANK; ValueError says what's wrong with them."""
        allowed = os.environ.get("WIPEBOT_ALLOWED_GUILDS", "")
        problems, guilds = [], []
        try:
            guilds = [accounts.Guild.parse(g) for g in allowed.split(",") if g.strip()]
            if not guilds:
                problems.append("WIPEBOT_ALLOWED_GUILDS: whose members may sign in, e.g. eu/argent-dawn/"
                                "the-copper-lanterns:5 or the guild's warcraftlogs.com address")
        except ValueError as e:
            problems.append(f"WIPEBOT_ALLOWED_GUILDS: {e}")
        officer_rank = (os.environ.get("WIPEBOT_OFFICER_RANK") or str(OFFICER_RANK)).strip()
        if not officer_rank.isdigit():
            problems.append(f"WIPEBOT_OFFICER_RANK: the highest guild rank that saves raid notes and plans, e.g. "
                            f"{OFFICER_RANK} (0 is the guild master), not {officer_rank!r}")
        if problems:
            raise ValueError("\n  ".join(problems))
        return cls(guilds, officer_rank=int(officer_rank))

    async def resolve(self) -> None:
        """Each guild's Warcraft Logs id, and the name of one given by its id, for the guilds that lack them."""
        if all(g.id is not None and g.name for g in self.guilds) or time.time() - self._looked_up < GUILD_RETRY_S:
            return
        self._looked_up = time.time()
        find, find_id = self._find or WCL().find_guild, self._find_id or WCL().guild_by_id
        for i, g in enumerate(self.guilds):
            if g.id is not None and g.name:
                continue
            try:
                found = await (find_id(g.id) if g.id is not None else find(g.display_name(), g.realm, g.region))
            except WCLError as e:
                log.warning("guild %s: %s (looked up again in %d s)", g, e, GUILD_RETRY_S)
                continue
            if found and isinstance(found.get("id"), int):
                server = found.get("server") or {}
                self.guilds[i] = dataclasses.replace(
                    g, id=found["id"], name=g.name or str(found.get("name", "")),
                    realm=g.realm or str(server.get("slug", "")),
                    region=g.region or str((server.get("region") or {}).get("slug", "")).lower())
            else:
                log.error("guild %s isn't on Warcraft Logs: check WIPEBOT_ALLOWED_GUILDS (until it's found, its "
                          "members can't sign in)", g)

    async def check(self, characters: list[dict]) -> list[accounts.Character] | None:
        """The characters (as Warcraft Logs gives them) in an allowed guild, or None when there are none. While an
        allowed guild isn't found, someone in none of the others may be in it: that raises AccountError, as
        Warcraft Logs down does."""
        await self.resolve()
        if found := accounts.members(characters, self.guilds):
            return found
        if missing := [str(g) for g in self.guilds if g.id is None]:
            raise accounts.AccountError(f"{' and '.join(missing)} not found on Warcraft Logs yet")
        return None


class SignIn(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """The OAuth authorization server the MCP SDK serves (its identity-assertion grant stays refused)."""

    def __init__(self, public_url: str, access: Access, client_id: str, client_secret: str,
                 path: Path | str | None = None):
        """`client_id`, `client_secret`: the server's Warcraft Logs client, which users sign in with."""
        self.public_url = public_url.rstrip("/")
        self.access, self.client_id, self.client_secret = access, client_id, client_secret
        # Users' Warcraft Logs tokens are kept encrypted with a key only the server's settings give, not the database
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"wipebot sign-in: " + client_secret.encode())
                                                       .digest()))
        path = path or default_path()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("BEGIN IMMEDIATE")  # all of the migration or none, one server at a time
        try:
            self._migrate(self._db.execute("PRAGMA user_version").fetchone()[0])
            self._db.execute("PRAGMA user_version = 3")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, info TEXT NOT NULL, created REAL NOT NULL,
                                                used REAL);
            CREATE TABLE IF NOT EXISTS logins (state TEXT PRIMARY KEY, client_id TEXT NOT NULL, params TEXT NOT NULL,
                                               verifier TEXT NOT NULL, expires REAL NOT NULL, browser TEXT);
            CREATE TABLE IF NOT EXISTS codes (hash TEXT PRIMARY KEY, code TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
                                               user INTEGER NOT NULL, scopes TEXT NOT NULL, resource TEXT,
                                               expires REAL NOT NULL, grant_id TEXT NOT NULL, grace REAL);
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                                              characters TEXT NOT NULL, signed_in REAL NOT NULL, checked REAL,
                                              wcl_token TEXT, wcl_expires REAL);
        """)
        self._documents: dict[str, tuple[DocumentClient | None, float]] = {}  # url -> (client, fresh until)
        self._purged = 0.0
        self._crowded: dict[str, float] = {}  # table -> when it was last said to be full
        self._purge()

    def _migrate(self, version: int) -> None:
        """A store from before brought up to version 3 (the tables are then created as they are now)."""
        if version < 1:
            # The users were Battle.net accounts before: their ids mean nothing now, so everyone signs in again.
            for table in ("logins", "codes", "tokens", "users"):
                self._db.execute(f"DROP TABLE IF EXISTS {table}")
        if version < 2:
            # When a client last got a token; a rotated refresh token; a membership check.
            for table, column in (("clients", "used"), ("tokens", "grace"), ("users", "checked")):
                columns = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
                if columns and column not in columns:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL")
            self._db.execute("DROP TABLE IF EXISTS logins")  # under way, not bound to a browser: started again
        if version < 3:
            # The user's Warcraft Logs token (the ones signed in before have none until they sign in again).
            columns = {r[1] for r in self._db.execute("PRAGMA table_info(users)")}
            for column, kind in (("wcl_token", "TEXT"), ("wcl_expires", "REAL")):
                if columns and column not in columns:
                    self._db.execute(f"ALTER TABLE users ADD COLUMN {column} {kind}")

    def _purge(self, now_: bool = False) -> None:
        """Drop what has expired, and registered clients without a token unused for UNUSED_CLIENT_S (never used for
        NEVER_USED_CLIENT_S); every PURGE_EVERY_S, or `now_`."""
        now = time.time()
        if now - self._purged < PURGE_EVERY_S and not now_:
            return
        self._purged = now
        for table in ("logins", "codes", "tokens"):
            self._db.execute(f"DELETE FROM {table} WHERE expires < ?", (now,))
        self._db.execute("DELETE FROM clients WHERE client_id NOT IN (SELECT client_id FROM tokens) AND "
                         "(created < ? OR (used IS NULL AND created < ?))",
                         (now - UNUSED_CLIENT_S, now - NEVER_USED_CLIENT_S))
        # A user with no grant left (signed out, revoked, lapsed) has no use for their Warcraft Logs token; one just
        # back from Warcraft Logs is still exchanging their code for one
        self._db.execute("UPDATE users SET wcl_token = NULL, wcl_expires = NULL WHERE wcl_token IS NOT NULL AND "
                         "id NOT IN (SELECT user FROM tokens) AND signed_in < ?", (now - CODE_TTL_S,))

    def _make_room(self, table: str, rows: str, first: str, most: int) -> None:
        """Room for one more of `table`'s `rows` (a condition), kept to `most`: when full after dropping what has
        expired, the first by `first` go. Someone flooding it then pushes out mostly their own, never refuses
        everyone else's."""
        count = f"SELECT COUNT(*) FROM {table} WHERE {rows}"
        if self._db.execute(count).fetchone()[0] < most:
            return
        self._purge(now_=True)
        if (over := self._db.execute(count).fetchone()[0] - most + 1) <= 0:
            return
        self._db.execute(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {rows} ORDER BY "
                         f"{first} LIMIT ?)", (over,))
        if time.time() - self._crowded.get(table, 0) > PURGE_EVERY_S:  # once in a while, as a flood goes on
            self._crowded[table] = time.time()
            log.warning("%s full (%d): the oldest dropped", table, most)

    @property
    def callback_url(self) -> str:
        return self.public_url + CALLBACK_PATH

    # ---- clients: by their metadata document (CIMD), or registered (DCR) ----

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id.startswith("https://"):
            return await self._document_client(client_id)
        row = self._db.execute("SELECT info FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def _document_client(self, url: str) -> DocumentClient | None:
        """A CIMD client, its document fetched once a day (only from CLIENT_HOSTS); one that failed, again after
        CLIENT_DOC_RETRY_S, the last good one kept meanwhile."""
        found = self._documents.get(url)
        if found and time.time() < found[1]:
            return found[0]
        if urllib.parse.urlsplit(url).hostname not in CLIENT_HOSTS:
            return None
        try:
            client, ttl = document_client(url, await fetch_client_document(url)), CLIENT_DOC_TTL_S
            if client is None:
                log.warning("client document %s refused", url)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("client document %s: %s", url, e)
            client, ttl = (found[0] if found else None), CLIENT_DOC_RETRY_S  # while its host is down
        self._documents.pop(url, None)
        while len(self._documents) >= MAX_DOCUMENTS:  # the oldest that isn't a client, else the oldest
            self._documents.pop(next((u for u, (c, _) in self._documents.items() if c is None),
                                     next(iter(self._documents))))
        self._documents[url] = (client, time.time() + (ttl if client else CLIENT_DOC_RETRY_S))
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """A client registering itself (DCR): only what sign-in needs is kept."""
        redirects = client_info.redirect_uris or []
        for uri in redirects:
            u = str(uri)
            if not (_loopback(u) or u in REGISTERED_REDIRECTS[:2] or u.startswith(REGISTERED_REDIRECTS[2])):
                raise RegistrationError("invalid_redirect_uri", "a registered client may only send users back to "
                                        "its own machine (loopback), or to Claude's or ChatGPT's callback")
        if len(redirects) > MAX_REDIRECTS or any(len(str(u)) > MAX_URL for u in redirects):
            raise RegistrationError("invalid_redirect_uri", f"at most {MAX_REDIRECTS} redirect URIs, each under "
                                    f"{MAX_URL} characters")
        if client_info.jwks is not None or client_info.jwks_uri is not None:
            raise RegistrationError("invalid_client_metadata", "keys (jwks, jwks_uri) aren't supported")
        if len(client_info.scope or "") > MAX_SCOPE:
            raise RegistrationError("invalid_client_metadata", "scope is too long")
        self._make_room("clients", "used IS NULL", "created", MAX_NEW_CLIENTS)
        kept = OAuthClientInformationFull(
            client_id=client_info.client_id, client_secret=client_info.client_secret,
            client_id_issued_at=client_info.client_id_issued_at,
            client_secret_expires_at=client_info.client_secret_expires_at, redirect_uris=redirects,
            grant_types=client_info.grant_types[:5], response_types=client_info.response_types[:5],
            token_endpoint_auth_method=client_info.token_endpoint_auth_method, scope=client_info.scope,
            client_name=(client_info.client_name or "")[:MAX_NAME] or None)
        self._db.execute("INSERT OR REPLACE INTO clients VALUES (?, ?, ?, NULL)",
                         (client_info.client_id, kept.model_dump_json(exclude_none=True), time.time()))

    # ---- authorization: Warcraft Logs in between ----

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (self.resource, self.public_url):
            raise AuthorizeError("invalid_target", f"this server is {self.resource}")
        if len(params.state or "") > MAX_STATE:
            raise AuthorizeError("invalid_request", f"state is longer than {MAX_STATE} characters")
        info = params.model_dump_json()
        if len(info) > 4 * MAX_URL:
            raise AuthorizeError("invalid_request", "the request is too long")
        self._purge()
        # A sign-in's page is opened at once: one never opened goes first, then the oldest.
        self._make_room("logins", "1", "browser IS NOT NULL, expires", MAX_LOGINS)
        state = secrets.token_urlsafe(32)
        verifier, _ = accounts.pkce()
        self._db.execute("INSERT INTO logins VALUES (?, ?, ?, ?, ?, NULL)",
                         (state, client.client_id, info, verifier, time.time() + LOGIN_TTL_S))
        return f"{self.public_url}{CONSENT_PATH}?" + urllib.parse.urlencode({"login": state})

    @property
    def resource(self) -> str:
        return self.public_url + "/mcp"

    def _login(self, state: str) -> tuple | None:
        return self._db.execute("SELECT client_id, params, verifier, browser FROM logins WHERE state = ? AND "
                                "expires > ?", (state, time.time())).fetchone()

    async def consent(self, request: Request) -> Response:
        """/authorize sends the browser here (GET): which app asks and where it sends the user back, and one click
        on to Warcraft Logs (POST). The first browser to open it gets the cookie the rest of the sign-in asks for,
        so a sign-in link someone else started can't sign in anyone else."""
        form = await request.form() if request.method == "POST" else request.query_params
        state = str(form.get("login", ""))
        row = self._login(state)
        if not row:
            return _expired()
        nonce = request.cookies.get(COOKIE)
        mine = row[3] is not None and nonce is not None and _hash(nonce) == row[3]
        if request.method == "POST":
            if not mine:
                return _elsewhere()
            return RedirectResponse(accounts.authorize_url(self.client_id, self.callback_url, state,
                                                           accounts.challenge(row[2])), 303)
        if row[3] is None:
            nonce = nonce or secrets.token_urlsafe(32)
            self._db.execute("UPDATE logins SET browser = ? WHERE state = ?", (_hash(nonce), state))
        elif not mine:
            return _elsewhere()
        assert nonce
        client = await self.get_client(row[0])
        back = str(AuthorizationParams.model_validate_json(row[1]).redirect_uri)
        where = "this computer" if _loopback(back) else urllib.parse.urlsplit(back).hostname or "?"
        name = (client.client_name if client else None) or "An app"
        page = _page("Sign in to Wipebot", f"<b>{html.escape(name[:MAX_NAME])}</b> asks to use Wipebot as you, and "
                     f"sends you back to <b>{html.escape(where)}</b> once you're signed in. Go on only if you started "
                     f'this from that app.</p><form method="post" action="{CONSENT_PATH}"><input type="hidden" '
                     f'name="login" value="{html.escape(state)}"><button>Sign in with Warcraft Logs</button></form>'
                     "<p>Wipebot reads which characters you claimed on Warcraft Logs, to check they're in the guild.",
                     200)
        page.set_cookie(COOKIE, nonce, max_age=LOGIN_TTL_S, path="/wcl", secure=self.public_url.startswith("https:"),
                        httponly=True, samesite="lax")
        return page

    async def callback(self, request: Request) -> Response:
        """Warcraft Logs sends the browser back here: let the user in (a code for the client) or say why not."""
        state = request.query_params.get("state", "")
        row = self._login(state)
        if not row:
            return _expired()
        nonce = request.cookies.get(COOKIE)
        if row[3] is None or nonce is None or _hash(nonce) != row[3]:
            return _elsewhere()
        self._db.execute("DELETE FROM logins WHERE state = ?", (state,))
        client_id, params = row[0], AuthorizationParams.model_validate_json(row[1])
        back = str(params.redirect_uri)
        if "code" not in request.query_params:  # the user said no, or Warcraft Logs failed
            return RedirectResponse(construct_redirect_uri(back, error="access_denied", state=params.state,
                                                           iss=self.public_url), 302)
        try:
            account = await accounts.login(self.client_id, self.client_secret, request.query_params["code"], row[2],
                                           self.callback_url)
        except accounts.AccountError as e:
            log.warning("sign-in failed: %s", e)
            return _page("Warcraft Logs didn't answer", "Signing in with Warcraft Logs failed. Try again in a minute.",
                         502)
        try:
            allowed = await self.access.check(account.characters)
        except accounts.AccountError as e:
            log.warning("sign-in not checked: %s: %s", account.name, e)
            return _page("Can't check the guild right now", "Wipebot can't check guild membership right now. Try "
                         "again in a few minutes.", 503)
        if allowed is None:
            log.info("sign-in refused: %s (%d characters)", account.name, len(account.characters))
            return _page("Not a member", f"None of the characters {html.escape(account.name)} claimed on Warcraft Logs "
                         f"is in {html.escape(' or '.join(str(g) for g in self.access.guilds))} at a "
                         "rank that may use Wipebot. Claim your character on warcraftlogs.com (your character's "
                         "page, <b>Claim</b>), then sign in again. If you joined the guild recently, Warcraft Logs "
                         "can take a day or two to see it.", 403)
        self._db.execute("INSERT OR REPLACE INTO users (id, name, characters, signed_in, checked, wcl_token, "
                         "wcl_expires) VALUES (?, ?, ?, ?, ?, ?, ?)", (
            account.id, account.name, json.dumps([c.__dict__ for c in allowed]), time.time(), time.time(),
            self._fernet.encrypt(account.token.encode()).decode() if account.token else None,
            account.expires or None))
        log.info("signed in: %s as %s", account.name, ", ".join(c.name for c in allowed))
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + CODE_TTL_S, client_id=client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource, subject=str(account.id))
        self._db.execute("INSERT INTO codes VALUES (?, ?, ?)", (_hash(code), auth_code.model_dump_json(),
                                                                auth_code.expires_at))
        return RedirectResponse(construct_redirect_uri(back, code=code, state=params.state, iss=self.public_url), 302)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        row = self._db.execute("SELECT code FROM codes WHERE hash = ? AND expires > ?",
                               (_hash(authorization_code), time.time())).fetchone()
        code = AuthorizationCode.model_validate_json(row[0]) if row else None
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        self._db.execute("DELETE FROM codes WHERE hash = ?", (_hash(authorization_code.code),))
        assert client.client_id and authorization_code.subject
        return self._issue(client.client_id, int(authorization_code.subject), authorization_code.scopes,
                           authorization_code.resource, secrets.token_hex(8))

    # ---- tokens ----

    def _issue(self, client_id: str, user: int, scopes: list[str], resource: str | None, grant_id: str) -> OAuthToken:
        access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        self._db.execute("UPDATE clients SET used = ? WHERE client_id = ?", (now, client_id))
        for token, kind, ttl in ((access, "access", ACCESS_TTL_S), (refresh, "refresh", REFRESH_TTL_S)):
            self._db.execute("INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)", (
                _hash(token), kind, client_id, user, " ".join(scopes), resource, now + ttl, grant_id))
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL_S, refresh_token=refresh,
                          scope=" ".join(scopes) or None)

    def _load(self, token: str, kind: str) -> tuple | None:
        """A token's client, user, scopes, resource, expiry and grant. A refresh token used again past its grace
        has leaked (the client got a new one): the whole grant goes."""
        row = self._db.execute("SELECT client_id, user, scopes, resource, expires, grant_id, grace FROM tokens "
                               "WHERE hash = ? AND kind = ? AND expires > ?", (_hash(token), kind, time.time())
                               ).fetchone()
        if row and row[6] is not None and row[6] < time.time():
            log.warning("a refresh token was used again after it was rotated: the grant is revoked")
            self._db.execute("DELETE FROM tokens WHERE grant_id = ?", (row[5],))
            return None
        return row[:6] if row else None

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._load(token, "access")
        if not row:
            return None
        client_id, user, scopes, resource, expires, _ = row
        return AccessToken(token=token, client_id=client_id, scopes=scopes.split(), expires_at=int(expires),
                           resource=resource, subject=str(user), claims={"iss": self.public_url})

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self._load(refresh_token, "refresh")
        if not row or row[0] != client.client_id:
            return None
        client_id, user, scopes, resource, expires, _ = row
        return RefreshToken(token=refresh_token, client_id=client_id, scopes=scopes.split(), expires_at=int(expires),
                            resource=resource, subject=str(user))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        """A new pair for the old (rotated), if the user is still in the guild."""
        row = self._load(refresh_token.token, "refresh")
        if not row:
            raise TokenError("invalid_grant", "refresh token expired or revoked")
        assert refresh_token.subject and client.client_id
        grant_id, user = row[5], int(refresh_token.subject)
        if not await self.still_allowed(user):
            self._db.execute("DELETE FROM tokens WHERE grant_id = ?", (grant_id,))
            raise TokenError("invalid_grant", "no longer a member of an allowed guild")
        # The old access token goes; the old refresh token works for REFRESH_GRACE_S more, so a client that lost the
        # answer, or refreshed twice at once, isn't signed out. After that it's kept, to tell a reuse (`_load`).
        self._db.execute("DELETE FROM tokens WHERE grant_id = ? AND kind = 'access'", (grant_id,))
        self._db.execute("UPDATE tokens SET grace = COALESCE(grace, ?) WHERE hash = ?",
                         (time.time() + REFRESH_GRACE_S, _hash(refresh_token.token)))
        self._purge()
        return self._issue(client.client_id, user, scopes or refresh_token.scopes, refresh_token.resource, grant_id)

    async def still_allowed(self, user: int) -> bool:
        """The user's guild characters, as Warcraft Logs has them now, still in an allowed guild (and kept as they
        are now: a rank or guild changed). With Warcraft Logs down, the user stays in for MEMBERSHIP_GRACE_S after
        the last check that worked."""
        row = self._db.execute("SELECT characters, COALESCE(checked, signed_in) FROM users WHERE id = ?",
                               (user,)).fetchone()
        ids = [c["id"] for c in json.loads(row[0])] if row else []
        try:
            allowed = await self.access.check(await accounts.characters(ids))
        except accounts.AccountError as e:  # rather than sign everyone out at once
            kept = bool(row) and time.time() - row[1] < MEMBERSHIP_GRACE_S
            log.warning("membership check failed, %s: %s", "kept" if kept else "signed out", e)
            return kept
        if allowed:
            self._db.execute("UPDATE users SET characters = ?, checked = ? WHERE id = ?",
                             (json.dumps([c.__dict__ for c in allowed]), time.time(), user))
        elif allowed is None:
            self._db.execute("UPDATE users SET wcl_token = NULL, wcl_expires = NULL WHERE id = ?", (user,))
        return allowed is not None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        row = self._db.execute("SELECT grant_id FROM tokens WHERE hash = ?", (_hash(token.token),)).fetchone()
        if row:
            self._db.execute("DELETE FROM tokens WHERE grant_id = ?", (row[0],))

    def user(self, user: int) -> tuple[str, list[accounts.Character]] | None:
        """A signed-in user's Warcraft Logs name and guild characters."""
        row = self._db.execute("SELECT name, characters FROM users WHERE id = ?", (user,)).fetchone()
        return (row[0], [accounts.Character(**c) for c in json.loads(row[1])]) if row else None

    def wcl_token(self, user: int) -> str | None:
        """A signed-in user's Warcraft Logs token, which their tool calls read with; None when they have none (signed
        in before tokens were kept), it lapsed, or the client secret changed since."""
        row = self._db.execute("SELECT wcl_token, wcl_expires FROM users WHERE id = ?", (user,)).fetchone()
        if not row or not row[0] or (row[1] is not None and row[1] < time.time()):
            return None
        try:
            return self._fernet.decrypt(row[0].encode()).decode()
        except InvalidToken:
            return None


STYLE = ("body{font:16px/1.5 system-ui,sans-serif;background:#0b101e;color:#ecf0f8;margin:0;padding:15vh 16px}"
         "main{max-width:34rem;margin:auto}h1{font-size:1.4rem}button{font:inherit;padding:.5em 1.2em;"
         "border:0;border-radius:6px;background:#3b6fd8;color:#fff;cursor:pointer}"
         "footer{margin-top:3em;font-size:.85rem;color:#9aa3b8}footer a{color:inherit}")
# The page's own style only, a form posting here and on to Warcraft Logs, never in a frame, no referrer.
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'sha256-"
    + base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode() + "'; form-action 'self' https://"
    + urllib.parse.urlsplit(accounts.AUTHORIZE_URL).netloc + "; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store"}


def _page(title: str, text: str, status: int) -> HTMLResponse:
    """A sign-in page; `text` is HTML, anything from outside in it escaped."""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Wipebot: {title}</title>
<style>{STYLE}</style></head>
<body><main><h1>{title}</h1><p>{text}</p>
<footer>Wipebot is free software (AGPL-3.0): <a href="{html.escape(server.source_url())}">its source</a>.</footer>
</main></body></html>""", status, headers=PAGE_HEADERS)


def _expired() -> HTMLResponse:
    return _page("Sign-in expired", "This sign-in took too long or was already used. Start it again from your app.",
                 400)


def _elsewhere() -> HTMLResponse:
    return _page("Sign-in started elsewhere", "This sign-in was started in another browser, or this browser blocks "
                 "its cookie. Start it again from your app, in this browser.", 400)
