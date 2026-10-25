"""The remote server (remote.py), as claude.ai and ChatGPT reach it, with Warcraft Logs faked: the sign-in (signin.py:
registration or a client ID metadata document, /authorize, the Warcraft Logs callback, tokens, refresh with the
membership check again), who's asking, the pull-map viewer and its images."""
import base64
import hashlib
import io
import json
import urllib.parse
import zipfile
from typing import cast

import pytest
from starlette.testclient import TestClient

from wipebot.core import accounts
from wipebot.mcp import plugin, remote, signin
from wipebot.mcp.signin import Access, SignIn

PUBLIC = "https://wipebot.test"
BACK = "https://claude.ai/api/mcp/auth_callback"
GUILD = accounts.Guild("eu", "argent-dawn", "the-copper-lanterns", 5)
CLIENT, SECRET = "wcl-client", "wcl-secret"  # the Warcraft Logs client the server reads and signs in with
LANTERNS_ID = 14169
LANTERNS = {"id": LANTERNS_ID, "name": "The Copper Lanterns",
            "server": {"slug": "argent-dawn", "region": {"slug": "EU"}}}


def character(id: int, name: str, realm: str = "argent-dawn", rank: int = 0, guilds=()) -> dict:
    """A claimed character as Warcraft Logs gives it."""
    return {"id": id, "name": name, "guildRank": rank, "server": {"slug": realm, "region": {"slug": "EU"}},
            "guilds": list(guilds)}


ACCOUNTS = {  # what a Warcraft Logs login code stands for
    "raider": accounts.Account(1, "Raider", [character(11, "Torvek", "silvermoon", 4, [LANTERNS]),
                                             character(12, "Alt", rank=8, guilds=[LANTERNS])],
                               "raider's warcraft logs token", 4e9),
    "social": accounts.Account(2, "Social", [character(21, "Social", rank=8, guilds=[LANTERNS])]),
    "stranger": accounts.Account(3, "Stranger", [character(31, "Pug", "silvermoon")]),
    "unclaimed": accounts.Account(4, "Unclaimed", []),
    "officer": accounts.Account(5, "Lead", [character(51, "Halvard", rank=1, guilds=[LANTERNS])],
                                "lead's warcraft logs token", 4e9),
}
VERIFIER = "a-code-verifier-of-at-least-forty-three-characters-long-xyz"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def now() -> dict[int, dict]:
    """The characters as Warcraft Logs has them at a refresh; -1 set is an outage."""
    return {c["id"]: c for a in ACCOUNTS.values() for c in a.characters}


async def find_guild(name, realm, region):
    """Warcraft Logs' guild by name, realm and region."""
    return LANTERNS if (name, realm, region) == ("The Copper Lanterns", "argent-dawn", "eu") else None


@pytest.fixture
def users() -> SignIn:
    """The sign-in, its store in memory: Raider in the guild (Torvek on a connected realm, his alt below the
    allowed rank), Social below it, Stranger in no guild, Unclaimed with no character claimed."""
    return SignIn(PUBLIC, Access([GUILD], find_guild), CLIENT, SECRET, ":memory:")


@pytest.fixture
def client(monkeypatch, now, users):
    """The HTTP app as `wipebot serve` serves it, on a server of its own."""
    yield from _serving(monkeypatch, now, users)


@pytest.fixture
def open_client(monkeypatch, now):
    """The same, open to anyone with a Warcraft Logs account (WIPEBOT_ALLOWED_GUILDS=*), as a public server."""
    yield from _serving(monkeypatch, now, SignIn(PUBLIC, Access([], anyone=True, officer_rank=9), CLIENT, SECRET,
                                                 ":memory:"))


def _serving(monkeypatch, now, users):
    verifiers: dict[str, str] = {}

    async def login(client_id, secret, code, verifier, redirect_uri):
        assert (client_id, secret) == (CLIENT, SECRET) and redirect_uri == PUBLIC + "/wcl/callback"
        assert verifiers.get(code, verifier) == verifier  # the verifier of its own login
        if code == "outage":
            raise accounts.AccountError("Warcraft Logs token: ConnectTimeout()")
        return ACCOUNTS[code]

    async def characters(ids):
        if -1 in now:
            raise accounts.AccountError("Warcraft Logs characters: ConnectTimeout()")
        return [now[i] for i in ids if i in now]
    monkeypatch.setattr(accounts, "login", login)
    monkeypatch.setattr(accounts, "characters", characters)
    with TestClient(remote.app(users), base_url=PUBLIC) as c:
        yield c


def register(c: TestClient) -> str:
    r = c.post("/register", json={"redirect_uris": [BACK], "token_endpoint_auth_method": "none",
                                  "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def consent(c: TestClient, client_id: str, back: str = BACK) -> str:
    """/authorize, then the consent page: the URL of our consent page."""
    r = c.get("/authorize", params=dict(response_type="code", client_id=client_id, redirect_uri=back, state="s1",
                                        code_challenge=CHALLENGE, code_challenge_method="S256",
                                        resource=PUBLIC + "/mcp"), follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith(PUBLIC + signin.CONSENT_PATH + "?login=")
    return r.headers["location"]


def to_warcraft_logs(c: TestClient, page: str) -> str:
    """The consent page opened and clicked on: the sign-in's state at Warcraft Logs."""
    r = c.get(page)
    assert r.status_code == 200 and "Sign in with Warcraft Logs" in r.text, r.text
    login = urllib.parse.parse_qs(urllib.parse.urlparse(page).query)["login"][0]
    r = c.post(signin.CONSENT_PATH, data={"login": login}, follow_redirects=False)
    assert r.status_code == 303, r.text
    to = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(to.query)
    assert to.netloc == "www.warcraftlogs.com" and q["client_id"] == [CLIENT]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0] != CHALLENGE  # its own, not the client's
    return q["state"][0]


def sign_in(c: TestClient, client_id: str, who: str, back: str = BACK):
    """/authorize, the consent page, then Warcraft Logs sending the browser back: the response of our callback."""
    state = to_warcraft_logs(c, consent(c, client_id, back))
    return c.get("/wcl/callback", params={"code": who, "state": state}, follow_redirects=False)


def tokens(c: TestClient, client_id: str, who: str = "raider", back: str = BACK) -> dict:
    r = sign_in(c, client_id, who, back)
    assert r.status_code == 302, r.text
    to = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(to.query)
    assert f"{to.scheme}://{to.netloc}{to.path}" == back and q["state"] == ["s1"] and q["iss"] == [PUBLIC]
    r = c.post("/token", data=dict(grant_type="authorization_code", code=q["code"][0], redirect_uri=back,
                                   client_id=client_id, code_verifier=VERIFIER, resource=PUBLIC + "/mcp"))
    assert r.status_code == 200, r.text
    return r.json()


def initialize(c: TestClient, token: str | None):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})


def test_the_plugin_is_this_connector_and_the_skill(client):
    """Claude gets the method only with the skill: the connector serves itself and the skill as a plugin."""
    r = client.get(plugin.PATH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read(".claude-plugin/plugin.json"))
        readme = z.read("README.md").decode()
    assert {"README.md", "skills/raid-log-analysis/SKILL.md"} <= names
    assert manifest["name"] == "wipebot" and manifest["mcpServers"] == {"Wipebot": {"type": "http",
                                                                                     "url": PUBLIC + "/mcp"}}
    assert str(GUILD) in manifest["description"] and len(manifest["version"]) == 8 and "{" not in readme
    assert client.get(plugin.PATH).content == r.content  # the same zip on every download


def test_the_metadata_points_clients_at_our_sign_in(client):
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == PUBLIC + "/mcp" and resource["authorization_servers"] == [PUBLIC]
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["registration_endpoint"] == PUBLIC + "/register"


def test_without_a_token_the_tools_ask_for_sign_in(client):
    r = initialize(client, None)
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


def test_a_raider_signs_in_and_reaches_the_tools(client):
    t = tokens(client, register(client))
    assert t["token_type"] == "Bearer" and t["refresh_token"]
    assert initialize(client, t["access_token"]).status_code == 200


def test_only_guild_characters_are_kept(client, users):
    tokens(client, register(client))
    name, chars = users.user(1) or ("", [])
    assert name == "Raider" and [(c.where(), c.rank) for c in chars] == [("Torvek (Silvermoon-EU)", 4)]


@pytest.mark.parametrize("who", ["social", "stranger", "unclaimed"])
def test_open_to_anyone_whoever_has_a_warcraft_logs_account_signs_in(open_client, who):
    t = tokens(open_client, register(open_client), who)
    assert initialize(open_client, t["access_token"]).status_code == 200


def test_open_to_anyone_every_claimed_character_is_kept_with_its_guild(open_client):
    call, _ = session(open_client)
    text = call("tools/call", {"name": "my_characters", "arguments": {}})["content"][0]["text"]
    assert ("The user's characters claimed on Warcraft Logs: Torvek (Silvermoon-EU) of The Copper Lanterns, "
            "Alt (Argent Dawn-EU) of The Copper Lanterns.") in text


def test_open_to_anyone_the_consent_page_says_why_it_reads_the_characters(open_client):
    r = open_client.get(consent(open_client, register(open_client)))
    assert "so it knows which are yours" in r.text and "in the guild" not in r.text


def test_open_to_anyone_a_users_own_guilds_are_where_find_reports_looks(monkeypatch):
    from mcp.server.auth.middleware import auth_context
    users = SignIn(PUBLIC, Access([], anyone=True), CLIENT, SECRET, ":memory:")
    chars = accounts.members(ACCOUNTS["raider"].characters + ACCOUNTS["stranger"].characters, None)
    users._db.execute("INSERT INTO users (id, name, characters, signed_in) VALUES (1, 'Raider', ?, 0)",
                      (json.dumps([c.__dict__ for c in chars]),))
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1"})())
    assert remote._own_guilds(users) == [("The Copper Lanterns", "argent-dawn", "eu")]
    assert [(c.name, c.guild_id, c.rank) for c in chars] == [("Torvek", LANTERNS_ID, 4), ("Alt", LANTERNS_ID, 8),
                                                             ("Pug", None, None)]


def test_anyone_may_sign_in_with_a_star(monkeypatch):
    monkeypatch.setenv("WIPEBOT_ALLOWED_GUILDS", "*")
    monkeypatch.setenv("WIPEBOT_OFFICER_RANK", "9")
    access = Access.from_env()
    assert access.anyone and access.guilds == [] and access.officer_rank == 9 and str(access) == "any guild"


@pytest.mark.parametrize("who", ["social", "stranger", "unclaimed"])
def test_who_isnt_in_the_guild_at_an_allowed_rank_is_turned_away(client, who):
    r = sign_in(client, register(client), who)
    assert r.status_code == 403 and "Not a member" in r.text and "Claim" in r.text


def test_a_refresh_rotates_the_tokens(client):
    client_id = register(client)
    t = tokens(client, client_id)
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 200, r.text
    assert initialize(client, r.json()["access_token"]).status_code == 200
    assert initialize(client, t["access_token"]).status_code == 401  # the old access token is gone


def test_a_used_refresh_token_works_a_little_longer_then_no_more(client, monkeypatch):
    client_id = register(client)
    t = tokens(client, client_id)

    def refresh():
        return client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                               client_id=client_id))
    assert refresh().status_code == 200
    again = refresh()  # the client lost the answer, or refreshed twice at once
    assert again.status_code == 200 and initialize(client, again.json()["access_token"]).status_code == 200
    monkeypatch.setattr(signin, "REFRESH_GRACE_S", -1)
    assert refresh().status_code == 200  # still within the grace given at the first use
    later = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=again.json()["refresh_token"],
                                            client_id=client_id))
    assert later.status_code == 200
    reused = client.post("/token", data=dict(grant_type="refresh_token",
                                             refresh_token=again.json()["refresh_token"], client_id=client_id))
    assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"
    # a token used again after it was rotated has leaked: what the client got for it goes too
    assert initialize(client, later.json()["access_token"]).status_code == 401
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=later.json()["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 400


def test_someone_who_left_the_guild_cant_refresh(client, now):
    client_id = register(client)
    t = tokens(client, client_id)
    now[11] = character(11, "Torvek", "silvermoon", 0)  # no guild now
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_a_users_warcraft_logs_token_is_kept_encrypted_for_their_calls(client, users):
    """Their tool calls read Warcraft Logs with it, so their own points pay first (wcl.reading_as)."""
    tokens(client, register(client))
    assert users.wcl_token(1) == "raider's warcraft logs token"
    stored = users._db.execute("SELECT wcl_token FROM users WHERE id = 1").fetchone()[0]
    assert stored and "raider" not in stored  # a copy of the database alone doesn't give it
    moved = SignIn(PUBLIC, Access([GUILD], find_guild), CLIENT, "a new secret", ":memory:")
    moved._db.execute("INSERT INTO users (id, name, characters, signed_in, wcl_token) VALUES (1, 'Raider', '[]', 0, ?)",
                      (stored,))
    assert moved.wcl_token(1) is None  # the client secret changed: read as the server until they sign in again
    users._db.execute("UPDATE users SET wcl_expires = 1 WHERE id = 1")
    assert users.wcl_token(1) is None  # lapsed


def test_a_users_warcraft_logs_token_goes_when_they_leave_the_guild_or_have_no_grant_left(client, users, now):
    client_id = register(client)
    t = tokens(client, client_id)
    now[11] = character(11, "Torvek", "silvermoon", 0)  # no guild now
    client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                    client_id=client_id))
    assert users._db.execute("SELECT wcl_token FROM users WHERE id = 1").fetchone() == (None,)

    tokens(client, client_id, "officer")
    users._purge(now_=True)
    assert users.wcl_token(5)  # signed in, with a grant
    users._db.execute("DELETE FROM tokens")  # signed out, revoked or lapsed
    users._purge(now_=True)
    assert users.wcl_token(5)  # back from Warcraft Logs moments ago: still exchanging its code
    users._db.execute("UPDATE users SET signed_in = 0")
    users._purge(now_=True)
    assert users._db.execute("SELECT wcl_token FROM users WHERE id = 5").fetchone() == (None,)


def test_a_user_with_no_grant_left_is_forgotten_after_a_while(client, users):
    """On a server open to anyone, strangers who stopped using it aren't kept for good."""
    tokens(client, register(client), "officer")
    users._db.execute("UPDATE users SET signed_in = 0, checked = 0")
    users._purge(now_=True)
    assert users.user(5)  # still signed in somewhere: kept
    users._db.execute("DELETE FROM tokens")
    users._purge(now_=True)
    assert users.user(5) is None


def test_a_user_who_says_no_goes_back_to_the_app_with_access_denied(client):
    client_id = register(client)
    state = to_warcraft_logs(client, consent(client, client_id))
    r = client.get("/wcl/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and "error=access_denied" in r.headers["location"]
    again = client.get("/wcl/callback", params={"code": "raider", "state": state}, follow_redirects=False)
    assert again.status_code == 400  # a state works once


def test_guilds_are_written_as_region_realm_name_their_address_or_their_id_with_a_highest_rank():
    assert accounts.Guild.parse("EU/argent-dawn/the-copper-lanterns:5") == GUILD
    assert accounts.Guild.parse("us/area-52/some-guild").max_rank is None
    copied = accounts.Guild.parse("https://www.warcraftlogs.com/guild/eu/argent-dawn/The%20Copper%20Lanterns:5")
    assert (copied.region, copied.realm, copied.name, copied.max_rank) == ("eu", "argent-dawn", "The Copper Lanterns",
                                                                         5)
    assert str(copied) == "The Copper Lanterns (Argent Dawn-EU)"
    by_id = accounts.Guild.parse("https://www.warcraftlogs.com/guild/id/14169")
    assert (by_id.id, by_id.max_rank, str(by_id)) == (14169, None, "guild 14169 on Warcraft Logs")
    assert accounts.Guild.parse("14169:2").id == 14169 and accounts.Guild.parse("14169:2").max_rank == 2
    with pytest.raises(ValueError, match="isn't a guild"):
        accounts.Guild.parse("The Copper Lanterns")


async def test_a_guild_given_by_its_id_gets_its_name_from_warcraft_logs():
    async def by_id(guild_id):
        return LANTERNS if guild_id == LANTERNS["id"] else None
    access = Access([accounts.Guild.parse(f"{LANTERNS['id']}:5")], find_guild, by_id)
    await access.resolve()
    assert str(access.guilds[0]) == "The Copper Lanterns (Argent Dawn-EU)" and access.guilds[0].max_rank == 5


async def test_a_look_alike_guild_on_the_same_realm_is_turned_away_by_its_id():
    """Names alone let in "Thecopper Lanterns": only the guild's id is in."""
    look_alike = {**LANTERNS, "id": 666, "name": "Thecopper Lanterns"}
    access = Access([GUILD], find_guild)
    assert await access.check([character(41, "Twin", guilds=[look_alike])]) is None
    found = await access.check([character(11, "Torvek", "silvermoon", 4, [LANTERNS])]) or []
    assert [c.guild_id for c in found] == [LANTERNS["id"]]
    assert not GUILD.has(LANTERNS)  # a guild whose id isn't known yet matches none, even by its own name


async def test_while_a_guild_isnt_found_whoever_may_be_in_it_cant_be_checked(monkeypatch, caplog):
    """Not matched by name meanwhile (a look-alike would get in), and looked up again a minute later."""
    other = accounts.Guild("eu", "silvermoon", "brightleaf-company", id=777)
    brightleaf = {"id": 777, "name": "Brightleaf Company", "server": {"slug": "silvermoon", "region": {"slug": "EU"}}}
    look_alike = {**LANTERNS, "id": 666, "name": "Thecopper Lanterns"}
    asked = []

    async def down(name, realm, region):
        asked.append(name)
        raise signin.WCLError("timeout")
    access = Access([other, GUILD], down)
    for guilds in ([LANTERNS], [look_alike]):
        with pytest.raises(accounts.AccountError, match="The Copper Lanterns"):
            await access.check([character(11, "Torvek", "silvermoon", 4, guilds)])
    assert await access.check([character(51, "Leaf", "silvermoon", 2, [brightleaf])])  # the other guild's members
    assert len(asked) == 1  # not again at once
    monkeypatch.setattr(signin, "GUILD_RETRY_S", -1)

    async def nowhere(name, realm, region):
        return None
    access._find = nowhere
    with pytest.raises(accounts.AccountError):
        await access.check([])
    assert "check WIPEBOT_ALLOWED_GUILDS" in caplog.text  # never found: likely written wrong
    access._find = find_guild
    assert await access.check([character(41, "Twin", guilds=[look_alike])]) is None


def test_while_a_guild_isnt_found_sign_in_waits_and_signed_in_users_stay(client, users, now):
    client_id = register(client)
    t = tokens(client, client_id)
    users.access.guilds[0] = GUILD  # its id lost, and Warcraft Logs not finding it again

    async def down(name, realm, region):
        raise signin.WCLError("timeout")
    users.access._find, users.access._looked_up = down, 0.0
    page = sign_in(client, client_id, "raider")
    assert page.status_code == 503 and "Try again in a few minutes" in page.text
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 200  # as with Warcraft Logs down


# ---- clients named by a metadata document (CIMD): the real documents, fetched on 2026-10-13 ----

CLAUDE = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"
CHATGPT = "https://chatgpt.com/oauth/client.json"
DOCUMENTS = {
    CLAUDE: {"client_id": CLAUDE, "client_name": "Claude", "client_uri": "https://claude.ai",
             "redirect_uris": [BACK], "grant_types": ["authorization_code", "refresh_token",
                                                      "urn:ietf:params:oauth:grant-type:jwt-bearer"],
             "response_types": ["code"], "token_endpoint_auth_method": "none"},
    CLAUDE_CODE: {"client_id": CLAUDE_CODE, "client_name": "Claude Code", "client_uri": "https://claude.ai",
                  "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
                  "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                  "token_endpoint_auth_method": "none"},
    CHATGPT: {"client_id": CHATGPT, "client_uri": "https://chatgpt.com/",
              "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
              "token_endpoint_auth_method": "private_key_jwt", "token_endpoint_auth_methods_supported": [
                  "none", "private_key_jwt"], "grant_types": ["authorization_code", "refresh_token"],
              "response_types": ["code"], "client_name": "ChatGPT"},
}


@pytest.fixture
def documents(monkeypatch) -> list[str]:
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return DOCUMENTS[url]
    monkeypatch.setattr(signin, "fetch_client_document", fetch)
    return fetched


def test_the_metadata_offers_client_documents_with_public_clients(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["client_id_metadata_document_supported"] is True
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert meta["authorization_response_iss_parameter_supported"] is True


@pytest.mark.parametrize("client_id, back", [
    (CLAUDE, BACK),
    (CHATGPT, "https://chatgpt.com/connector_platform_oauth_redirect"),
    (CLAUDE_CODE, "http://localhost:53122/callback"),  # Claude Code's port changes per session
    (CLAUDE_CODE, "http://127.0.0.1:4711/callback"),
])
def test_claude_and_chatgpt_sign_in_by_their_documents_without_registering(client, documents, client_id, back):
    t = tokens(client, client_id, back=back)
    assert initialize(client, t["access_token"]).status_code == 200
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 200, r.text
    assert documents.count(client_id) == 1  # fetched once, then remembered


@pytest.mark.parametrize("back", ["http://evil.example/callback", "http://localhost:53122/other",
                                  "https://claude.ai/somewhere-else"])
def test_a_document_client_goes_back_only_where_its_document_says(client, documents, back):
    r = client.get("/authorize", params=dict(response_type="code", client_id=CLAUDE_CODE, redirect_uri=back,
                                             state="s1", code_challenge=CHALLENGE, code_challenge_method="S256"),
                   follow_redirects=False)
    assert r.status_code == 400


def test_documents_are_fetched_only_from_claude_and_chatgpt(client, documents):
    r = client.get("/authorize", params=dict(response_type="code", client_id="https://evil.example/client.json",
                                             redirect_uri="https://evil.example/cb", state="s1",
                                             code_challenge=CHALLENGE, code_challenge_method="S256"),
                   follow_redirects=False)
    assert r.status_code == 400 and documents == []


def test_a_document_must_name_itself_and_keep_redirects_on_its_own_host():
    doc = DOCUMENTS[CLAUDE]
    assert signin.document_client(CLAUDE, doc) is not None
    assert signin.document_client(CLAUDE, {**doc, "client_id": CLAUDE_CODE}) is None
    assert signin.document_client(CLAUDE, {**doc, "redirect_uris": ["https://evil.example/cb"]}) is None
    assert signin.document_client(CLAUDE, {**doc, "redirect_uris": []}) is None


def session(c: TestClient, who: str = "raider"):
    """A signed-in MCP session that supports MCP Apps: (send a request and get its result, the initialize result)."""
    import json
    token = tokens(c, register(c), who)["access_token"]
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "Authorization": f"Bearer {token}"}
    apps = {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}
    r = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {"extensions": apps},
        "clientInfo": {"name": "t", "version": "1"}}})
    headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(method: str, params: dict) -> dict:
        r = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
        return json.loads(r.text.split("data: ", 1)[1].splitlines()[0])["result"]
    return call, json.loads(r.text.split("data: ", 1)[1].splitlines()[0])["result"]  # and initialize's


def test_my_characters_names_the_signed_in_users_guild_characters(client):
    call, _ = session(client)
    text = call("tools/call", {"name": "my_characters", "arguments": {}})["content"][0]["text"]
    assert "Torvek (Silvermoon-EU)" in text and "Alt" not in text
    tools = call("tools/list", {})["tools"]
    mine = {t["name"]: t for t in tools}["my_characters"]
    assert mine["title"] == "My characters" and mine["_meta"] == {"openai/profile": True}
    assert all(t.get("title") for t in tools if "app" not in t.get("_meta", {}).get("ui", {}).get("visibility", []))


def test_an_officer_saves_a_pasted_note_for_the_guild_and_no_one_else_does(client, tmp_path, monkeypatch):
    """Officers are the characters at the Guild Master or Officer rank (0 or 1); the save is its own tool, the only
    one that isn't read-only, so a client asks before it runs and never before raid_note."""
    from tests.support import NOTE, PLAN
    from wipebot.core.notebook import Notebook
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))
    call, _ = session(client, "raider")  # Torvek, rank 4
    tools = {t["name"]: t for t in call("tools/list", {})["tools"]}
    save = tools["save_note_or_plan"]
    assert save["title"] == "Save raid note or plan"
    assert list(save["inputSchema"]["properties"]) == ["pasted", "from_pull", "report"]
    assert save["annotations"] == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                                   "openWorldHint": False}
    # the viewer's own tools too: ChatGPT lists every tool not marked read-only as Write
    assert [n for n, t in tools.items() if not t.get("annotations", {}).get("readOnlyHint")] == ["save_note_or_plan"]
    assert tools["raid_note"]["description"].endswith("keeps a pasted note for the guild with save_note_or_plan.")
    assert tools["raid_plan"]["description"].endswith("keeps a pasted plan for the guild with save_note_or_plan.")
    note = NOTE.read_text(encoding="utf-8")
    refused = call("tools/call", {"name": "save_note_or_plan", "arguments": {"pasted": note}})
    assert refused["isError"] and "only the guild's officers" in refused["content"][0]["text"]
    assert Notebook(tmp_path / "notes.sqlite").latest_notes(LANTERNS_ID) == []
    call, _ = session(client, "officer")
    saved = call("tools/call", {"name": "save_note_or_plan", "arguments": {"pasted": note}})
    assert not saved.get("isError") and saved["content"][0]["text"].startswith("Saved the raid note")
    plan = call("tools/call", {"name": "save_note_or_plan", "arguments": {"pasted": PLAN.read_text(encoding="utf-8")}})
    assert plan["content"][0]["text"].startswith("Saved the raid plan")
    junk = call("tools/call", {"name": "save_note_or_plan", "arguments": {"pasted": "assignments: soak left"}})
    assert junk["isError"] and "EncounterID" in junk["content"][0]["text"]
    book = Notebook(tmp_path / "notes.sqlite")
    assert [(n.boss, n.saved_by) for n in book.latest_notes(LANTERNS_ID)] == [("Sszorak", "Lead")]  # their WCL name
    assert [p.saved_by for p in book.plans(LANTERNS_ID)] == ["Lead"]


def test_an_officer_in_several_guilds_saves_nowhere(monkeypatch):
    """Which guild a paste is for can't be told then. A character below the officers' ranks doesn't count."""
    from types import SimpleNamespace

    from mcp.server.auth.middleware import auth_context
    from mcp.server.auth.provider import AccessToken

    from wipebot.core.report import FactError
    other = accounts.Guild("eu", "argent-dawn", "the-brass-owls", id=555)
    chars = [accounts.Character(51, "Halvard", "argent-dawn", "eu", 1, LANTERNS_ID),
             accounts.Character(52, "Mirelle", "argent-dawn", "eu", 0, 555)]
    found = SimpleNamespace(user=lambda i: ("Lead", chars),
                            access=SimpleNamespace(guilds=[accounts.Guild("eu", "argent-dawn", "the-copper-lanterns",
                                                                          id=LANTERNS_ID), other], officer_rank=1))
    monkeypatch.setattr(auth_context, "get_access_token",
                        lambda: AccessToken(token="t", client_id="c", scopes=[], subject="5"))
    officer = remote._officer(cast(signin.SignIn, found))
    with pytest.raises(FactError, match=r"several guilds here \(The Brass Owls .*, The Copper Lanterns"):
        officer()
    chars[1] = accounts.Character(52, "Mirelle", "argent-dawn", "eu", 2, 555)  # a rank below Officer
    assert officer() == ("Lead", LANTERNS_ID)
    found.access.officer_rank = 2  # WIPEBOT_OFFICER_RANK=2: an Officer Alt rank counts too
    with pytest.raises(FactError, match="several guilds"):
        officer()
    found.access.officer_rank = 0  # the Guild Master alone
    with pytest.raises(FactError, match=r"guild ranks 0 to 0"):
        officer()


# ---- the views, an MCP App ----

def test_pull_tools_open_the_views_which_read_the_app_only_tools(client):
    call, _ = session(client)
    tools = {t["name"]: t for t in call("tools/list", {})["tools"]}
    for shown in ("pull_map", "pull_summary"):
        assert tools[shown]["_meta"]["ui"]["resourceUri"] == remote.VIEWS_APP
        assert tools[shown]["_meta"]["openai/outputTemplate"] == remote.VIEWS_APP
    assert tools["pull_map"]["description"].endswith("with plan, the plan's spots on it and a button per moment.")
    assert "the user also sees the pull drawn" in tools["pull_summary"]["description"]
    # raid_plan has no view: a host shows it on every call, and most of its answers have no pull to play
    assert "ui" not in (tools["raid_plan"].get("_meta") or {})
    assert "call pull_map with plan, which shows its replay." in tools["raid_plan"]["description"]
    for app_only in ("view_data", "pull_replay", "viewer_log"):
        assert tools[app_only]["_meta"]["ui"] == {"resourceUri": remote.VIEWS_APP, "visibility": ["app"]}
    page = call("resources/read", {"uri": remote.VIEWS_APP})["contents"][0]
    assert page["mimeType"] == "text/html;profile=mcp-app" and "pull_replay" in page["text"]
    assert "view_data" in page["text"] and "/*SCRIPT*/" not in page["text"]
    assert page["_meta"]["ui"]["csp"] == {"resourceDomains": [PUBLIC]}
    # a stale tool list: the replay viewer's addresses from before the views, and an older views page
    for older in ("ui://wipebot/pull-map-0123456789.html", "ui://wipebot/views-0123456789.html"):
        again = call("resources/read", {"uri": older})["contents"][0]
        assert again["text"] == page["text"] and again["mimeType"] == page["mimeType"]
        assert again["_meta"]["ui"]["csp"] == {"resourceDomains": [PUBLIC]}


@pytest.fixture
def images(monkeypatch) -> list[str]:
    """Warcraft Logs' assets, faked: an icon, and nothing else. Returns what was fetched."""
    fetched = []

    async def wcl_image(path):
        fetched.append(path)
        if path != "icons/Mage-Frost.jpg":
            raise remote.knowledge.KnowledgeError("404")
        return b"jpeg"
    monkeypatch.setattr(remote.knowledge, "wcl_image", wcl_image)
    monkeypatch.setattr(remote, "_handed_out", set())
    monkeypatch.setattr(remote, "_failed", {})
    return fetched


@pytest.mark.parametrize("path", ["maps/../../etc/passwd", "icons/x.png", "reports/abc", "maps/12-map.png.exe"])
def test_img_serves_only_warcraft_logs_maps_and_icons(client, images, path):
    remote._handed_out.add(path)
    assert client.get(f"/img/{path}").status_code == 404 and images == []


def test_img_fetches_only_what_a_replay_named_and_remembers_a_failure(client, images):
    assert client.get("/img/icons/Mage-Frost.jpg").status_code == 404 and images == []  # no replay named it
    remote._handed_out.update(["icons/Mage-Frost.jpg", "maps/123-map.png"])
    r = client.get("/img/icons/Mage-Frost.jpg")
    assert r.status_code == 200 and r.content == b"jpeg" and r.headers["content-type"] == "image/jpeg"
    for _ in range(2):
        assert client.get("/img/maps/123-map.png").status_code == 404
    assert images == ["icons/Mage-Frost.jpg", "maps/123-map.png?v=2"]  # the failure once, then remembered


def test_health_answers_without_signing_in(client):
    got = client.get("/health")
    assert (got.status_code, got.text) == (200, "ok")


# ---- what a client can't do, and Warcraft Logs being down ----

def exchange(c: TestClient, client_id: str, who: str = "raider", verifier: str = VERIFIER):
    r = sign_in(c, client_id, who)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    form = dict(grant_type="authorization_code", code=code, redirect_uri=BACK, client_id=client_id,
                code_verifier=verifier)
    return c.post("/token", data=form), form


def test_a_registered_client_may_send_users_back_only_to_its_machine_or_claude_or_chatgpt(client):
    for back in ("http://127.0.0.1:33418/callback", "https://chatgpt.com/connector/oauth/abc123", BACK):
        r = client.post("/register", json={"redirect_uris": [back], "token_endpoint_auth_method": "none"})
        assert r.status_code == 201, (back, r.text)
    for back in ("https://evil.example/cb", "https://claude.ai.evil.example/api/mcp/auth_callback"):
        r = client.post("/register", json={"redirect_uris": [back], "token_endpoint_auth_method": "none"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_a_code_needs_its_verifier_and_works_once(client):
    client_id = register(client)
    wrong, _ = exchange(client, client_id, verifier="x" * 50)
    assert wrong.status_code == 400
    r, form = exchange(client, client_id)
    assert r.status_code == 200
    assert client.post("/token", data=form).status_code == 400  # the same code again


def test_a_refresh_token_works_only_for_its_client_and_revoking_ends_the_grant(client):
    mine, other = register(client), register(client)
    t = tokens(client, mine)
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=other))
    assert r.status_code == 400
    # the SDK asks every client for a secret here, an empty one for a public client
    r = client.post("/revoke", data=dict(token=t["access_token"], client_id=mine, client_secret=""))
    assert r.status_code == 200, r.text
    assert initialize(client, t["access_token"]).status_code == 401
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=mine))
    assert r.status_code == 400  # revoking the access token ends its refresh token too


def test_warcraft_logs_down_keeps_signed_in_users_in_and_says_so_at_sign_in(client, now):
    client_id = register(client)
    t = tokens(client, client_id)
    now[-1] = {}  # Warcraft Logs stops answering
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 200 and initialize(client, r.json()["access_token"]).status_code == 200
    page = sign_in(client, client_id, "outage")
    assert page.status_code == 502 and "Warcraft Logs didn't answer" in page.text


def test_warcraft_logs_down_for_long_signs_a_user_out(client, now, monkeypatch):
    """Kept in only so long after the last membership check that worked: else someone who left would stay in."""
    client_id = register(client)
    t = tokens(client, client_id)
    now[-1] = {}
    monkeypatch.setattr(signin, "MEMBERSHIP_GRACE_S", -1)
    r = client.post("/token", data=dict(grant_type="refresh_token", refresh_token=t["refresh_token"],
                                        client_id=client_id))
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


async def test_warcraft_logs_answering_a_membership_check_in_part_is_an_outage(monkeypatch):
    """A character it failed to read comes back empty, next to an error: that's no reason to sign its player out."""
    import httpx
    import warcraftlogs_cli.client as cli

    def post(self, url, *, token, body):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "data": {"characterData": {"c0": None}},
            "errors": [{"message": "timeout", "path": ["characterData", "c0"]}]})
    monkeypatch.setattr(cli.WarcraftLogsClient, "_post_graphql", post)
    monkeypatch.setattr(cli.WarcraftLogsClient, "_token", lambda self: "client")
    with pytest.raises(accounts.AccountError, match="only part"):
        await accounts.characters([11])


def test_the_metadata_answers_a_browser_preflight(client):
    r = client.options("/.well-known/oauth-authorization-server",
                       headers={"Origin": "https://inspector.example", "Access-Control-Request-Method": "GET",
                                "Access-Control-Request-Headers": "mcp-protocol-version"})
    assert r.status_code == 200 and "mcp-protocol-version" in r.headers["access-control-allow-headers"].lower()


def test_a_client_document_that_isnt_an_object_is_refused():
    assert signin.document_client(CLAUDE, ["not", "an", "object"]) is None
    assert signin.document_client(CLAUDE, {**DOCUMENTS[CLAUDE], "redirect_uris": [42]}) is None


# ---- what anyone may send before signing in: kept small, and capped ----

def test_a_registration_keeps_only_what_sign_in_needs(client, users):
    r = client.post("/register", json={"redirect_uris": [BACK], "token_endpoint_auth_method": "none",
                                       "client_name": "x" * 5000, "software_id": "y" * 100_000})
    assert r.status_code == 201, r.text
    info = users._db.execute("SELECT info FROM clients WHERE client_id = ?", (r.json()["client_id"],)).fetchone()[0]
    assert len(info) < 1000 and "yyy" not in info and json.loads(info)["client_name"] == "x" * signin.MAX_NAME


@pytest.mark.parametrize("extra", [{"jwks": {"keys": [{"k": "z" * 1000}]}}, {"jwks_uri": "https://evil.example/k"},
                                   {"redirect_uris": [f"http://localhost:{p}/cb" for p in range(1000, 1011)]},
                                   {"scope": "s " * 1000}])
def test_a_registration_with_keys_or_too_much_is_refused(client, extra):
    r = client.post("/register", json={"redirect_uris": [BACK], "token_endpoint_auth_method": "none", **extra})
    assert r.status_code == 400, r.text


def test_registrations_never_used_are_capped_and_a_flood_pushes_out_only_the_oldest(client, users, monkeypatch):
    monkeypatch.setattr(signin, "MAX_NEW_CLIENTS", 2)
    used = register(client)
    tokens(client, used)  # one that got a token doesn't count
    flood = [register(client) for _ in range(5)]
    assert {r[0] for r in users._db.execute("SELECT client_id FROM clients")} == {used, *flood[-2:]}
    tokens(client, register(client))  # someone else still registers and signs in


def authorize(c: TestClient, client_id: str, **extra):
    return c.get("/authorize", params={**dict(response_type="code", client_id=client_id, redirect_uri=BACK,
                                              state="s1", code_challenge=CHALLENGE, code_challenge_method="S256"),
                                       **extra}, follow_redirects=False)


def test_a_long_state_is_refused(client):
    r = authorize(client, register(client), state="s" * 2000)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]


def test_a_flood_of_sign_ins_never_opened_doesnt_turn_anyone_away(client, users, monkeypatch):
    """Sign-ins under way are capped: past it, those whose page was never opened go first, then the oldest."""
    client_id = register(client)
    monkeypatch.setattr(signin, "MAX_LOGINS", 3)
    mine = to_warcraft_logs(client, consent(client, client_id))  # at Warcraft Logs' login
    for _ in range(10):
        assert "error" not in authorize(client, client_id).headers["location"]
    assert users._db.execute("SELECT COUNT(*) FROM logins").fetchone()[0] == 3
    r = client.get("/wcl/callback", params={"code": "raider", "state": mine}, follow_redirects=False)
    assert r.status_code == 302 and "code=" in r.headers["location"]
    states = [to_warcraft_logs(client, consent(client, client_id)) for _ in range(4)]  # every page opened
    old, new = (client.get("/wcl/callback", params={"code": "raider", "state": s}, follow_redirects=False)
                for s in (states[0], states[-1]))
    assert old.status_code == 400 and "took too long" in old.text and new.status_code == 302


def test_a_sign_in_database_from_before_is_opened_and_kept(tmp_path):
    import sqlite3
    path = tmp_path / "signin.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE clients (client_id TEXT PRIMARY KEY, info TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, characters TEXT NOT NULL, signed_in REAL);
        PRAGMA user_version = 1;
    """)
    db.execute("INSERT INTO clients VALUES ('c1', ?, 1e12)",
               (json.dumps({"client_id": "c1", "redirect_uris": [BACK]}),))
    db.execute("INSERT INTO users VALUES (1, 'Raider', '[]', 1e12)")
    db.commit()
    db.close()
    for _ in range(2):  # and again, as it is now
        users = SignIn(PUBLIC, Access([GUILD], find_guild), CLIENT, SECRET, path)
        assert users.user(1) == ("Raider", [])
        assert users._db.execute("SELECT client_id, used FROM clients").fetchall() == [("c1", None)]
        users._db.close()


def test_a_sign_in_database_half_brought_up_to_date_is_finished(tmp_path):
    """As one a migration without a transaction could leave: a column added, the version not yet set."""
    import sqlite3
    path = tmp_path / "signin.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE clients (client_id TEXT PRIMARY KEY, info TEXT NOT NULL, created REAL NOT NULL, used REAL);
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, characters TEXT NOT NULL, signed_in REAL);
        PRAGMA user_version = 1;
    """)
    db.execute("INSERT INTO users VALUES (1, 'Raider', '[]', 1e12)")
    db.commit()
    db.close()
    users = SignIn(PUBLIC, Access([GUILD], find_guild), CLIENT, SECRET, path)
    assert users.user(1) == ("Raider", []) and users._db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert {"checked", "wcl_token", "wcl_expires"} <= {r[1] for r in users._db.execute("PRAGMA table_info(users)")}
    assert users.wcl_token(1) is None  # signed in before tokens were kept


# ---- the consent page, and the browser a sign-in is bound to ----

def test_the_consent_page_names_the_app_and_where_it_sends_the_user_back(client):
    r = client.post("/register", json={"redirect_uris": [BACK], "token_endpoint_auth_method": "none",
                                       "client_name": "<script>alert(1)</script>"})
    page = client.get(consent(client, r.json()["client_id"]))
    assert page.status_code == 200 and "&lt;script&gt;" in page.text and "<script>" not in page.text
    assert "<b>claude.ai</b>" in page.text
    assert '<a href="https://github.com/Enf0/wipebot">its source</a>' in page.text  # the AGPL's §13
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "script-src" not in csp
    assert page.headers["referrer-policy"] == "no-referrer" and page.headers["x-content-type-options"] == "nosniff"
    cookie = page.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_a_sign_in_finishes_only_in_the_browser_that_started_it(client):
    client_id = register(client)
    page = consent(client, client_id)
    other = TestClient(client.app, base_url=PUBLIC)  # someone sent the link
    state = to_warcraft_logs(client, page)
    assert other.get(page).status_code == 400 and "another browser" in other.get(page).text
    login = urllib.parse.parse_qs(urllib.parse.urlparse(page).query)["login"][0]
    assert other.post(signin.CONSENT_PATH, data={"login": login}, follow_redirects=False).status_code == 400
    r = other.get("/wcl/callback", params={"code": "raider", "state": state}, follow_redirects=False)
    assert r.status_code == 400 and "another browser" in r.text
    r = client.get("/wcl/callback", params={"code": "raider", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and "code=" in r.headers["location"]  # still works where it was started


def test_the_consent_click_needs_the_page_opened_first(client):
    """A form on another site posting the click (its cookie isn't sent) goes nowhere."""
    page = consent(client, register(client))
    login = urllib.parse.parse_qs(urllib.parse.urlparse(page).query)["login"][0]
    assert client.post(signin.CONSENT_PATH, data={"login": login}, follow_redirects=False).status_code == 400


def test_one_browser_signs_in_to_two_apps_at_once(client):
    first, second = consent(client, register(client)), consent(client, register(client))
    states = to_warcraft_logs(client, first), to_warcraft_logs(client, second)
    for state in states:
        r = client.get("/wcl/callback", params={"code": "raider", "state": state}, follow_redirects=False)
        assert r.status_code == 302 and "code=" in r.headers["location"]


# ---- fetching a client's metadata document ----

@pytest.fixture
def served(monkeypatch) -> list:
    """claude.ai, faked: the responses it gives, in turn; what's asked is counted."""
    answers: list = []
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return answers.pop(0)
    real = signin.httpx.AsyncClient
    monkeypatch.setattr(signin.httpx, "AsyncClient",
                        lambda **kw: real(transport=signin.httpx.MockTransport(handler), **kw))
    return [answers, asked]


@pytest.mark.parametrize("answer", [
    lambda: signin.httpx.Response(302, headers={"Location": "https://evil.example/doc"}),  # never followed
    lambda: signin.httpx.Response(200, content=b" " * (signin.CLIENT_DOC_MAX_BYTES + 1)),
    lambda: signin.httpx.Response(500)])
async def test_a_client_document_too_big_redirected_or_failing_isnt_read(served, answer):
    answers, asked = served
    answers.append(answer())
    with pytest.raises(ValueError):
        await signin.fetch_client_document(CLAUDE)
    assert asked == [CLAUDE]


async def test_a_client_document_that_failed_is_tried_again_only_after_a_while(served, users, monkeypatch):
    answers, asked = served
    answers += [signin.httpx.Response(500), signin.httpx.Response(200, json=DOCUMENTS[CLAUDE])]
    assert await users.get_client(CLAUDE) is None and await users.get_client(CLAUDE) is None
    assert len(asked) == 1
    later = signin.time.time() + signin.CLIENT_DOC_RETRY_S + 1
    monkeypatch.setattr(signin.time, "time", lambda: later)
    found = await users.get_client(CLAUDE)
    assert found is not None and found.client_name == "Claude" and len(asked) == 2


async def test_only_so_many_client_documents_are_remembered_claudes_last(served, users, monkeypatch):
    """Anyone can have us look up made-up addresses on claude.ai: those go first, not Claude's own document."""
    answers, asked = served
    monkeypatch.setattr(signin, "MAX_DOCUMENTS", 3)
    answers.append(signin.httpx.Response(200, json=DOCUMENTS[CLAUDE]))
    await users.get_client(CLAUDE)
    for i in range(5):
        answers.append(signin.httpx.Response(404))
        await users.get_client(f"https://claude.ai/doc-{i}")
    assert list(users._documents) == [CLAUDE, *(f"https://claude.ai/doc-{i}" for i in (3, 4))]
    assert await users.get_client(CLAUDE) and len(asked) == 6  # not fetched again


# ---- `wipebot serve` ----

@pytest.fixture
def serving(monkeypatch, tmp_path) -> dict:
    """`wipebot serve`'s settings, uvicorn faked: returns what it was started with."""
    import uvicorn

    for name, value in (("WCL_CLIENT_ID", CLIENT), ("WCL_CLIENT_SECRET", SECRET), ("WIPEBOT_ALLOWED_GUILDS",
                        "eu/argent-dawn/the-copper-lanterns:5"), ("WIPEBOT_SIGNIN_DB", str(tmp_path / "s.sqlite"))):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("WIPEBOT_TRUSTED_PROXIES", raising=False)
    monkeypatch.setattr(remote.wcl, "saved_login", lambda: None)
    monkeypatch.setattr(remote.wcl, "_client_only", False)
    started: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))
    return started


def test_the_server_reads_with_the_client_only_and_trusts_only_its_proxy(serving, monkeypatch):
    remote.serve("127.0.0.1", 8000, PUBLIC)
    assert remote.wcl._client_only  # a user login saved while it runs isn't read
    assert serving["proxy_headers"] and serving["forwarded_allow_ips"] == "127.0.0.1"
    monkeypatch.setenv("WIPEBOT_TRUSTED_PROXIES", "172.18.0.1")
    remote.serve("127.0.0.1", 8000, PUBLIC)
    assert serving["forwarded_allow_ips"] == "172.18.0.1"


def test_the_server_needs_its_public_url_unless_it_listens_on_this_machine_alone(serving, caplog):
    """Else it would name localhost as its own, and sign-in would send users there."""
    for host, url in (("0.0.0.0", None), ("0.0.0.0", ""), ("127.0.0.1", "wipebot.example.com"),
                      ("0.0.0.0", "http://wipebot.example.com")):
        with pytest.raises(SystemExit, match="WIPEBOT_PUBLIC_URL"):
            remote.serve(host, 8000, url)
    assert serving == {}
    caplog.set_level("INFO")
    remote.serve("127.0.0.1", 8123, None)  # trying it out
    assert "as http://localhost:8123, for The Copper Lanterns (Argent Dawn-EU)" in caplog.text
    remote.serve("0.0.0.0", 8000, PUBLIC + "/")
    assert serving["host"] == "0.0.0.0" and f"as {PUBLIC}," in caplog.text


def test_the_server_names_every_setting_it_lacks_at_once(serving, monkeypatch):
    for name in ("WCL_CLIENT_ID", "WCL_CLIENT_SECRET", "WARCRAFTLOGS_CLIENT_ID", "WARCRAFTLOGS_CLIENT_SECRET",
                 "WIPEBOT_ALLOWED_GUILDS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as e:
        remote.serve("0.0.0.0", 8000, None)
    told = str(e.value)
    assert all(name in told for name in ("WCL_CLIENT_ID", "WIPEBOT_PUBLIC_URL", "WIPEBOT_ALLOWED_GUILDS"))
    monkeypatch.setenv("WIPEBOT_ALLOWED_GUILDS", "The Copper Lanterns")
    monkeypatch.setenv("WIPEBOT_OFFICER_RANK", "Officer")
    with pytest.raises(SystemExit, match="isn't a guild") as e:
        remote.serve("0.0.0.0", 8000, PUBLIC)
    assert "WIPEBOT_OFFICER_RANK" in str(e.value)  # said with the guilds, not after they're fixed


def test_the_server_wont_start_with_a_saved_user_login(serving, monkeypatch):
    monkeypatch.setattr(remote.wcl, "saved_login", lambda: {"access_token": "a user's"})
    with pytest.raises(SystemExit, match="user login is saved"):
        remote.serve("127.0.0.1", 8000, PUBLIC)
    assert serving == {}


async def test_the_default_report_is_the_signed_in_users_own_guilds_latest_raid(monkeypatch):
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    other = accounts.Guild("eu", "silvermoon", "brightleaf-company", id=777)
    users = SignIn(PUBLIC, Access([other, GUILD], find_guild), CLIENT, SECRET, ":memory:")
    users._db.execute("INSERT INTO users (id, name, characters, signed_in) VALUES (1, 'Raider', ?, 0)", (
        json.dumps([accounts.Character(11, "Torvek", "silvermoon", "eu", 4, LANTERNS_ID).__dict__]),))
    asked = []

    class Logs:
        async def guild_reports(self, guild_id, since, pulls=False):
            asked.append(guild_id)
            return [{"code": f"R{guild_id}", "startTime": 1, "pulls": 10}]

    async def latest_raid(library, reports):
        return reports[0]["code"]
    monkeypatch.setattr("wipebot.core.library.latest_raid", latest_raid)
    library = remote.server.Library(wcl=Logs())  # ty: ignore[invalid-argument-type]
    latest = remote._latest_raid(library, users)
    auth_context_var.set(AuthenticatedUser(AccessToken(token="t", client_id="c", scopes=[], subject="1")))
    assert await latest() == f"R{LANTERNS_ID}" and asked == [LANTERNS_ID]  # not the first allowed guild's
    auth_context_var.set(None)
    assert await latest() == "R777"  # no user (or one from before guild ids were kept): every allowed guild's


def test_the_limits_per_user_are_read_from_the_settings(monkeypatch):
    for name in ("WIPEBOT_POINTS_PER_USER", "WIPEBOT_CALLS_PER_USER", "WIPEBOT_WCL_USER_POINTS"):
        monkeypatch.delenv(name, raising=False)
    assert remote.user_limits() is None  # unset: no limit
    monkeypatch.setenv("WIPEBOT_POINTS_PER_USER", "200")
    monkeypatch.setenv("WIPEBOT_CALLS_PER_USER", "60")
    monkeypatch.setenv("WIPEBOT_WCL_USER_POINTS", "1")
    limits = remote.user_limits()
    assert limits and (limits.points, limits.calls, limits.own_points) == (200, 60, True)
    monkeypatch.setenv("WIPEBOT_CALLS_PER_USER", "lots")
    with pytest.raises(ValueError, match="WIPEBOT_CALLS_PER_USER: how many one user may spend in an hour"):
        remote.user_limits()
