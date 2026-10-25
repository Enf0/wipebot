"""raidstrats.gg: a guild's raid plans fetched with an officer's login, so nobody pastes them.

raidstrats.gg lets an app read only as a person who logged in and approved it (OAuth, the authorization code flow;
no app-only login). The app's client is RAIDSTRATS_CLIENT_ID and RAIDSTRATS_CLIENT_SECRET, with the scopes
`profile plans addon` and the redirect URL REDIRECT_URL. An officer logs in once (/wipebot raidstrats) and the
login is kept per guild in the notebook. Access tokens last an hour; the refresh token 30 days, replaced on each
refresh, so a login nobody uses is refreshed weekly (`keep_alive`).

`sync` fetches every plan the login can open (its own and the guild's, `/plans`) and their in-game import text in
one request (`POST /plans/addon-export`, up to 50), and saves a version of each plan whose text changed since it was
last fetched. A version counts from when it was fetched: raidstrats.gg gives no time a plan was last saved. Facts
verified live are in docs/reference/sources.md.
"""
import asyncio
import logging
import os
import secrets
import time
import urllib.parse

import httpx

from .notebook import Notebook, RaidstratsLogin
from .report import FactError
from .report import plan_export as plans
from .text import day

log = logging.getLogger(__name__)

BASE = "https://raidstrats.gg"
SCOPES = "profile plans addon"
# raidstrats.gg sends the browser back here after the login. Nothing listens: the officer pastes the address the
# browser couldn't open back to the bot, and the code in it is useless without the app's secret.
REDIRECT_URL = "http://localhost:8765/callback"
SAVED_BY = "raidstrats.gg"
FRESH_S = 60  # plans fetched this recently aren't fetched again (raid_plan called several times in one answer)
KEEP_ALIVE_S = 7 * 86400  # a login's refresh token is replaced at least this often
TIMEOUT_S = 10
UA = {"User-Agent": "Wipebot"}


class RaidstratsError(Exception):
    """raidstrats.gg didn't give what was asked; the message says why, for an officer."""


def client() -> tuple[str, str] | None:
    """The app's client id and secret, or None where it isn't set up."""
    cid, secret = os.environ.get("RAIDSTRATS_CLIENT_ID"), os.environ.get("RAIDSTRATS_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def login_url() -> tuple[str, str]:
    """Where to send an officer to log in, and the state to expect back."""
    found = client()
    if not found:
        raise RaidstratsError("this Wipebot has no raidstrats.gg app: set RAIDSTRATS_CLIENT_ID and "
                              "RAIDSTRATS_CLIENT_SECRET")
    state = secrets.token_urlsafe(16)
    return BASE + "/oauth/authorize?" + urllib.parse.urlencode(dict(
        response_type="code", client_id=found[0], redirect_uri=REDIRECT_URL, scope=SCOPES, state=state)), state


async def connect(pasted: str, state: str) -> tuple[str, dict]:
    """The raidstrats.gg username and token reply for the address the browser was sent back to."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(pasted.strip()).query)
    if query.get("error"):
        raise RaidstratsError("the login was cancelled on raidstrats.gg")
    if query.get("state", [""])[0] != state or not query.get("code"):
        raise RaidstratsError("that isn't the address from this login: run /wipebot raidstrats again and paste the "
                              "address your browser shows after you approve")
    cid, secret = client() or ("", "")
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=UA) as http:
        r = await http.post(BASE + "/oauth/token", data=dict(
            grant_type="authorization_code", code=query["code"][0], redirect_uri=REDIRECT_URL, client_id=cid,
            client_secret=secret))
        if r.status_code != 200:
            raise RaidstratsError(f"raidstrats.gg refused the login ({_why(r)}); the code works once and for 5 "
                                  "minutes: run /wipebot raidstrats again")
        token = r.json()
        if "addon" not in str(token.get("scope", "")).split():
            raise RaidstratsError("the login doesn't allow reading the plans' in-game import text: approve "
                                  "\"In-game import\" when logging in")
        me = await http.get(BASE + "/api/oauth/v1/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    return (me.json().get("username") if me.status_code == 200 else None) or "?", token


def _why(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error_description") or r.json().get("error") or r.status_code)
    except ValueError:
        return str(r.status_code)


def _error(r: httpx.Response) -> str:
    """The OAuth error code of a refusal ("invalid_client", "invalid_grant"), or ""."""
    try:
        return str(r.json().get("error") or "")
    except ValueError:
        return ""


def _refresh(refresh_token: str) -> dict | None:
    """raidstrats.gg's token reply for a refresh token; None when it refuses it (revoked, lapsed). Raises
    RaidstratsError when it refuses the app instead, so the login is kept for when the app's secret is fixed."""
    cid, secret = client() or ("", "")
    r = httpx.post(BASE + "/oauth/token", headers=UA, timeout=TIMEOUT_S, data=dict(
        grant_type="refresh_token", refresh_token=refresh_token, client_id=cid, client_secret=secret))
    if r.status_code == 401 and _error(r) == "invalid_client":
        raise RaidstratsError("raidstrats.gg refuses this Wipebot's app: whoever runs it sets the app's current "
                              "RAIDSTRATS_CLIENT_SECRET")
    if r.status_code in (400, 401):
        log.warning("raidstrats.gg refused a login's refresh token: %s", _why(r))
        return None
    r.raise_for_status()
    return r.json()


async def _token(book: Notebook, guild_id: int, older_than: float = 0) -> str | None:
    return await asyncio.to_thread(book.access_token, guild_id, _refresh, older_than)


async def sync(book: Notebook, guild_id: int) -> None:
    """Fetch the plans of a guild with a login and save each one whose text changed. Never fails: a problem is
    logged and kept with the login (/wipebot raidstrats shows it), and the saved plans are used as they are."""
    login = book.login(guild_id)
    if not (login and login.working and client()):
        return
    if login.fetched_at and time.time() - login.fetched_at < FRESH_S and not login.problem:
        return
    try:
        token = await _token(book, guild_id)
        if token is None:
            return
        async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT_S,
                                     headers={**UA, "Authorization": f"Bearer {token}"}) as http:
            r = await http.get("/api/oauth/v1/plans", params={"limit": 100})
            r.raise_for_status()
            listed = r.json()
            ids = list(dict.fromkeys(p["id"] for p in listed.get("plans", []) + listed.get("guildPlans", [])))
            results = []
            for i in range(0, len(ids), 50):
                r = await http.post("/api/oauth/v1/plans/addon-export", json={"planIds": ids[i:i + 50]})
                r.raise_for_status()
                results += r.json().get("results") or []
        # Against the last version fetched, not the last saved: a paste newer than the plan as saved on raidstrats.gg
        # (a roster given in the planner's link isn't in it) stays the newest until the plan changes there.
        saved = {p.plan_id: p.plan for p in book.plans(guild_id) if p.saved_by == SAVED_BY}
        for result in results:
            if not (result.get("ok") and result.get("addonExport")):
                continue  # one not saved on raidstrats.gg since its import text was added, or gone
            try:
                p = plans.parse(result["addonExport"])[0]
            except FactError:
                log.warning("raidstrats.gg plan %s doesn't decode", result.get("id"))
                continue
            if saved.get(p.plan_id) != p.export:
                book.save_plan(guild_id, p.boss, p.plan_id, p.name, p.export, SAVED_BY)
                log.info("raidstrats.gg plan %r (%s) saved for guild %s", p.name, p.boss, guild_id)
        book.fetched(guild_id)
    except RaidstratsError as e:
        log.warning("fetching guild %s's plans from raidstrats.gg failed: %s", guild_id, e)
        book.fetched(guild_id, str(e))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("fetching guild %s's plans from raidstrats.gg failed: %r", guild_id, e)
        book.fetched(guild_id, f"raidstrats.gg didn't answer ({type(e).__name__})")


def source(login: RaidstratsLogin) -> str:
    """Where a guild's saved plans come from, for the model: the login they're fetched with, when, and why the last
    fetch failed."""
    text = f"fetched from raidstrats.gg as {login.username}, every plan that account can open"
    text += f", last at {day(login.fetched_at * 1000, with_time=True)}" if login.fetched_at else ", not fetched yet"
    if login.problem:
        text += f" (the last fetch failed: {login.problem})"
    return text + "; a version is saved each time a plan's text changes"


async def keep_alive(book: Notebook) -> None:
    """Refresh each login whose refresh token is a week old, so a login nobody used for 30 days doesn't lapse."""
    if not client():
        return
    for guild_id in book.logins():
        try:
            await _token(book, guild_id, KEEP_ALIVE_S)
        except Exception as e:  # one guild's failure doesn't keep the others' from being refreshed
            log.warning("refreshing guild %s's raidstrats.gg login failed: %r", guild_id, e)
