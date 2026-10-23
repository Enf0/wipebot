"""Warcraft Logs accounts: who a user is (a login with the `view-user-profile` scope) and which guilds the
characters they claimed are in.

The remote MCP server (`wipebot serve`) lets a user in when a character they claimed on Warcraft Logs is in an
allowed guild at an allowed rank, or anyone, when it's open to every guild. The login is the authorization code
flow (with PKCE) of the confidential client the server reads with (WCL_CLIENT_ID, WCL_CLIENT_SECRET), whose redirect
URLs include the server's callback. The user's token is kept (its scope reads only what the client credentials
read), so their own Warcraft Logs points pay first for what they ask (`wcl.reading_as`). Membership is checked again
with the client credentials, which read any character's guild and rank. Facts verified live are in
docs/reference/wcl-api.md, "Sign-in" and "Point types".
"""
import base64
import hashlib
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx

from .wcl import WCL, WCLError

AUTHORIZE_URL = "https://www.warcraftlogs.com/oauth/authorize"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
USER_API = "https://www.warcraftlogs.com/api/v2/user"
SCOPES = "view-user-profile"
# A claimed character with its guilds; a guild may be on a connected realm, so its server is the guild's own.
CHARACTER = "id name guildRank server { slug region { slug } } guilds { id name server { slug region { slug } } }"
ME = f"{{ userData {{ currentUser {{ id name characters {{ {CHARACTER} }} }} }} }}"


class AccountError(RuntimeError):
    pass


def place(slug: str) -> str:
    """A realm or guild slug as people write it: `the-copper-lanterns` -> The Copper Lanterns."""
    return slug.replace("-", " ").title()


def parse_guild_link(link: str) -> int | tuple[str, str, str]:
    """A warcraftlogs.com guild page -> its guild id, or (region, realm, name).

    Both forms exist: https://www.warcraftlogs.com/guild/id/14169 and
    https://www.warcraftlogs.com/guild/eu/<realm>/<guild%20name> (with anything after it)."""
    if m := re.search(r"warcraftlogs\.com/guild/id/(\d+)", link):
        return int(m.group(1))
    if m := re.search(r"warcraftlogs\.com/guild/(eu|us|kr|tw|cn)/([^/?#]+)/([^/?#]+)", link, re.I):
        return (m.group(1).lower(), urllib.parse.unquote(m.group(2)).lower(),
                urllib.parse.unquote(m.group(3)).replace("+", " "))
    raise ValueError("That doesn't look like a Warcraft Logs guild page: open the guild on warcraftlogs.com "
                     "and copy the link from the address bar.")


@dataclass(frozen=True)
class Guild:
    """An allowed guild: `eu/argent-dawn/the-copper-lanterns:5` lets in ranks 0 to 5. Matched by its Warcraft Logs
    id alone, once that's known (signin.Access looks it up, and the name of one given by id)."""
    region: str
    realm: str  # slug
    name: str  # a slug, or the name as Warcraft Logs writes it
    max_rank: int | None = None
    id: int | None = field(default=None, compare=False)

    @classmethod
    def parse(cls, text: str) -> Guild:
        """One of WIPEBOT_ALLOWED_GUILDS: `region/realm/guild`, the guild's warcraftlogs.com address (either form)
        or its id there, each with an optional `:highest rank`."""
        text, rank = text.strip(), None
        if m := re.fullmatch(r"(.+?):(\d+)", text):
            text, rank = m[1], int(m[2])
        if text.isdigit():
            return cls("", "", "", rank, int(text))
        if "warcraftlogs.com" in text:
            try:
                found = parse_guild_link(text)
            except ValueError:
                found = None
            if isinstance(found, int):
                return cls("", "", "", rank, found)
            if found:
                return cls(*found, rank)
        if m := re.fullmatch(r"(\w+)/([\w-]+)/([\w-]+)", text):
            return cls(m[1].lower(), m[2].lower(), m[3].lower(), rank)
        raise ValueError(f"{text!r} isn't a guild: give region/realm/guild (eu/argent-dawn/the-copper-lanterns), "
                         "the guild's warcraftlogs.com address or its id there, each with an optional :highest rank")

    def display_name(self) -> str:
        """The guild's name as people write it (a slug's words capitalised)."""
        return place(self.name) if re.fullmatch(r"[a-z0-9-]+", self.name) else self.name

    def __str__(self) -> str:
        if not self.name:
            return f"guild {self.id} on Warcraft Logs"
        return f"{self.display_name()} ({place(self.realm)}-{self.region.upper()})"

    def has(self, guild: dict) -> bool:
        """Whether a Warcraft Logs guild ({id, ...}) is this one: by id, never by name, as names alone let in a
        look-alike ("Thecopper Lanterns") on the same realm. While its id isn't known, none is."""
        return self.id is not None and guild.get("id") == self.id


@dataclass(frozen=True)
class Character:
    id: int
    name: str
    realm: str  # slug
    region: str
    rank: int | None = None  # in an allowed guild
    guild_id: int | None = None  # that guild's id on Warcraft Logs (none in sign-ins before 17 October 2026)
    guild: str = ""  # that guild's name, realm (slug) and region (none in sign-ins before 23 October 2026)
    guild_realm: str = ""
    guild_region: str = ""

    def where(self) -> str:
        return f"{self.name} ({place(self.realm)}-{self.region.upper()})"


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    characters: list[dict]  # as Warcraft Logs gives them (CHARACTER)
    token: str = field(default="", repr=False)  # the user's Warcraft Logs token
    expires: float = 0.0  # when it lapses (Warcraft Logs gives about 360 days)


def members(characters: list[dict], guilds: list[Guild] | None) -> list[Character]:
    """The characters in an allowed guild at an allowed rank, each with its rank and guild; with `guilds` None
    (anyone may sign in), every character, with its guild if it has one."""
    out = []
    for c in characters:
        rank = c.get("guildRank")
        if guilds is None:
            found = next(iter(c.get("guilds") or []), None)
        else:
            found = next((found for g in guilds for found in c.get("guilds") or [] if isinstance(rank, int)
                          and g.has(found) and (g.max_rank is None or rank <= g.max_rank)), None)
            if found is None:
                continue
        server, home = c.get("server") or {}, (found or {}).get("server") or {}
        out.append(Character(int(c["id"]), str(c.get("name", "?")), str(server.get("slug", "")),
                             str((server.get("region") or {}).get("slug", "")).lower(),
                             rank if found and isinstance(rank, int) else None,
                             int(found["id"]) if found and isinstance(found.get("id"), int) else None,
                             str((found or {}).get("name") or ""), str(home.get("slug") or ""),
                             str((home.get("region") or {}).get("slug") or "").lower()))
    return out


def pkce() -> tuple[str, str]:
    """A PKCE verifier and its challenge."""
    verifier = secrets.token_urlsafe(64)
    return verifier, challenge(verifier)


def challenge(verifier: str) -> str:
    """A PKCE verifier's challenge (S256)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """Where to send the user's browser to log in."""
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(dict(
        response_type="code", client_id=client_id, redirect_uri=redirect_uri, scope=SCOPES, state=state,
        code_challenge=challenge, code_challenge_method="S256"))


async def login(client_id: str, secret: str, code: str, verifier: str, redirect_uri: str) -> Account:
    """The account behind a login's code, with the characters it claimed and its token: the authorization code flow,
    the client's secret by basic auth plus the PKCE verifier (Warcraft Logs takes both, verified 19 October 2026)."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(TOKEN_URL, auth=(client_id, secret), data=dict(
                grant_type="authorization_code", code=code, code_verifier=verifier, redirect_uri=redirect_uri))
            if r.status_code != 200:
                raise AccountError(f"Warcraft Logs token: {r.status_code}")
            granted = r.json()
            token, expires = str(granted["access_token"]), time.time() + float(granted.get("expires_in") or 0)
            r = await http.post(USER_API, json={"query": ME}, headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                raise AccountError(f"Warcraft Logs currentUser: {r.status_code}")
            user = r.json()["data"]["userData"]["currentUser"]
        return Account(int(user["id"]), str(user.get("name") or ""), list(user.get("characters") or []), token,
                       expires)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise AccountError(f"Warcraft Logs sign-in: {e!r}") from e


async def characters(ids: list[int]) -> list[dict]:
    """Characters by id, with their guilds and rank as Warcraft Logs has them now (client credentials, one query).
    An answer only in part (a character it failed to read comes back empty) raises, as an outage would: a sign-in
    isn't ended over a character Warcraft Logs didn't read."""
    if not ids:
        return []
    fields = " ".join(f"c{i}: character(id: {int(id)}) {{ {CHARACTER} }}" for i, id in enumerate(ids))
    try:
        data = await WCL().query(f"{{ characterData {{ {fields} }} }}", whole=True)
    except WCLError as e:
        raise AccountError(f"Warcraft Logs characters: {e}") from e
    return [c for c in (data.get("characterData") or {}).values() if isinstance(c, dict)]
