"""Warcraft Logs access (wcl.py): a request that hangs, answers in part or about a report gone private; several
fields in one query, and every page of a long answer."""
import re

import httpx
import pytest
import warcraft_api.http
from warcraftlogs_cli.client import GRAPHQL_WARNINGS_KEY, WarcraftLogsClient, WarcraftLogsClientError

from wipebot.core import wcl as wcl_module
from wipebot.core.cache import Cache
from wipebot.core.wcl import ALIASES_PER_QUERY, END_OF_REPORT, WCL, WCL_ATTEMPTS, WCLError


@pytest.fixture
def cache(monkeypatch) -> Cache:
    """Warcraft Logs' answers kept in memory."""
    kept = Cache(":memory:")
    monkeypatch.setattr(wcl_module, "cache", lambda: kept)
    return kept


def answering(w: WCL, monkeypatch, *answers: dict) -> None:
    """warcraft_cli's client gives these GraphQL answers, one per query."""
    left = list(answers)

    class Client:
        def raw_graphql(self, operation_name, query, variables):
            return left.pop(0), "public"
    monkeypatch.setattr(w, "_client", lambda: Client())


async def test_a_hung_request_is_given_up_after_two_tries(monkeypatch):
    """On 14 Oct 2026 Warcraft Logs hung, and three tries of 20 s made one answer take over two minutes."""
    tries = []

    def hang(request: httpx.Request) -> httpx.Response:
        tries.append(request.url.path)
        raise httpx.ReadTimeout("no answer", request=request)
    monkeypatch.setattr(WarcraftLogsClient, "_client", lambda self: httpx.Client(transport=httpx.MockTransport(hang)))
    monkeypatch.setattr(WarcraftLogsClient, "_has_user_token", lambda self: False)
    monkeypatch.setattr(WarcraftLogsClient, "_token", lambda self: "token")
    monkeypatch.setattr(warcraft_api.http, "backoff_seconds", lambda attempt: 0)
    monkeypatch.setenv("WARCRAFT_HTTP_MIN_INTERVAL_SECONDS", "0")
    with pytest.raises(WCLError, match="^timeout: "):
        await WCL().query("query { x }")
    assert len(tries) == WCL_ATTEMPTS == 2


@pytest.mark.parametrize("refused", [False, True])
async def test_serving_many_users_a_users_token_reads_at_the_user_endpoint_and_a_refused_one_falls_back(
        monkeypatch, refused):
    """Their own Warcraft Logs points pay first; a token Warcraft Logs no longer takes reads as the server."""
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        asked.append((request.url.path, bearer))
        if refused and bearer == "raider's token":
            return httpx.Response(401, json={"error": "Unauthenticated."})
        return httpx.Response(200, json={"data": {"x": bearer}})
    monkeypatch.setattr(WarcraftLogsClient, "_client", lambda self: httpx.Client(transport=httpx.MockTransport(answer)))
    monkeypatch.setattr(WarcraftLogsClient, "_token", lambda self: "server's token")
    monkeypatch.setattr(warcraft_api.http, "backoff_seconds", lambda attempt: 0)
    monkeypatch.setenv("WARCRAFT_HTTP_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(wcl_module, "_client_only", True)
    w = WCL()
    with wcl_module.reading_as("raider's token"):
        found = await w.query("query { x }")
    assert found == {"x": "server's token" if refused else "raider's token"}
    assert asked[0] == ("/api/v2/user", "raider's token")
    assert asked[1:] == ([("/api/v2/client", "server's token")] if refused else [])
    assert await w.query("query { x }") == {"x": "server's token"}  # no user: the client credentials


async def test_a_partial_answer_is_not_kept(cache, monkeypatch):
    """Warcraft Logs sometimes answers a rankings query with an error next to empty data; kept for a day, that
    would read as "no ranked kills"."""
    w = WCL()
    partial = {"worldData": {"encounter": {"characterRankings": {"rankings": []}}},
               GRAPHQL_WARNINGS_KEY: [{"message": "An error occurred."}]}
    answering(w, monkeypatch, dict(partial), dict(partial))
    with pytest.raises(WCLError, match="only part"):
        await w.query("query { x }", 60, encounter=1)
    assert cache.get('["wcl", "query { x }", {"encounter": 1}]') is None
    assert (await w.query("query { x }", encounter=1))["worldData"]  # uncached: served, with a warning logged


async def test_a_guild_not_found_is_asked_again_next_time(cache, monkeypatch):
    """A guild just made, or a name fixed in the settings, is found at the next look-up, not a day later."""
    asked, guilds = [], [WarcraftLogsClientError("not_found", "no guild"), {"id": 14169, "name": "Lanterns"}]

    class Client:
        def guild(self, region, realm, name):
            asked.append(name)
            found = guilds.pop(0)
            if isinstance(found, Exception):
                raise found
            return found
    w = WCL()
    monkeypatch.setattr(w, "_client", Client)
    assert await w.find_guild("Lanterns", "argent-dawn", "eu") is None
    assert (await w.find_guild("Lanterns", "argent-dawn", "eu") or {})["id"] == 14169
    assert (await w.find_guild("Lanterns", "argent-dawn", "eu") or {})["id"] == 14169 and len(asked) == 2  # kept now


async def test_a_batched_query_answered_in_part_says_ask_again(monkeypatch):
    """Forty pulls' tables in one query, one of them missing: a clear error, not a crash on the missing table."""
    w = WCL()
    answering(w, monkeypatch, {"reportData": {"report": {"a": {"data": {}}, "b": None}}},
              {"reportData": {"report": {"a": {"data": {}}, "b": {"data": {}}}},
               GRAPHQL_WARNINGS_KEY: [{"message": "An error occurred."}]})
    for _ in range(2):  # a missing table, then an error next to the answer
        with pytest.raises(WCLError, match="only part of the question .*ask again"):
            await w.tables("ABC", [("a", 1, {}), ("b", 2, {})], "Healing")


async def test_a_heavy_read_is_refused_when_the_hours_points_run_low(monkeypatch):
    w = WCL()
    rate = {"rateLimitData": {"limitPerHour": 3600, "pointsSpentThisHour": 2800, "pointsResetIn": 1200}}
    answering(w, monkeypatch, rate)  # read once: kept for a minute
    await w.ensure_points(100, "taunts")  # 800 left, 600 of them kept
    with pytest.raises(WCLError, match="taunts reads about 500 .*800 of this hour's 3600 are left.*again in 21 min"):
        await w.ensure_points(500, "taunts")


async def test_a_report_gone_private_says_so_instead_of_crashing(cache, monkeypatch):
    """A report made private answers with no report (kept so in a frozen cache) or a permission error: either way
    the tools say it can't be read, not crash on the missing data or tell the model to ask again."""
    w = WCL()
    answering(w, monkeypatch, {"reportData": {"report": None}},
              {"reportData": {"report": None},
               GRAPHQL_WARNINGS_KEY: [{"message": "You do not have permission to view this report."}]})
    for _ in range(2):
        with pytest.raises(WCLError, match="won't show report ABC: it's private now") as e:
            await w.query("query { x }", code="ABC")
        assert "ask again" not in str(e.value).replace("asking again won't help", "")


async def test_events_come_in_one_query_per_forty_items_then_each_later_page(monkeypatch):
    w = WCL()
    asked = []

    async def query(q, cache_for=None, **variables):
        asked.append(q)
        out = {}
        for alias, args in re.findall(r"(f\d+): events\(([^)]*)\)", q):
            fight = int(re.findall(r"fightIDs: \[(\d+)\]", args)[0])
            if "startTime" in args:  # fight 3's second page, from where the first ended
                assert f"startTime: 500, endTime: {END_OF_REPORT}" in args
                out[alias] = {"data": [{"fight": fight, "page": 2}], "nextPageTimestamp": None}
            else:
                out[alias] = {"data": [{"fight": fight}], "nextPageTimestamp": 500 if fight == 3 else None}
        return {"reportData": {"report": out}}
    monkeypatch.setattr(w, "query", query)
    items = 45
    got = await w.events_many("ABC", [(i, {}) for i in range(items)], "Deaths")
    assert len(asked) == -(-items // ALIASES_PER_QUERY) + 1  # the first pages in two queries, then fight 3's next
    assert got[3] == [{"fight": 3}, {"fight": 3, "page": 2}]
    assert [g[0]["fight"] for g in got] == list(range(items)) and all(len(g) == 1 for i, g in enumerate(got) if i != 3)


async def test_guild_reports_follow_every_page(monkeypatch):
    w = WCL()
    pages = {1: ([{"code": "A"}, {"code": "B"}], True), 2: ([{"code": "C"}], False)}
    asked = []

    async def query(_q, cache_for=None, **variables):
        asked.append(variables["page"])
        data, more = pages[variables["page"]]
        return {"reportData": {"reports": {"data": data, "has_more_pages": more}}}
    monkeypatch.setattr(w, "query", query)
    assert [r["code"] for r in await w.guild_reports(1, 0)] == ["A", "B", "C"]
    assert asked == [1, 2]


class _GuildClient:
    def __init__(self, error=None):
        self.error, self.asked = error, None

    def guild(self, *, region, realm, name):
        self.asked = (region, realm, name)
        if self.error:
            raise self.error
        return {"id": 78, "name": name,
                "server": {"name": "Azjol-Nerub", "slug": "azjolnerub", "region": {"slug": "US"}}}


async def test_find_guild_takes_the_realm_as_typed_and_none_when_missing(monkeypatch):
    w = WCL()
    client = _GuildClient()
    monkeypatch.setattr(w, "_client", lambda: client)  # the lookup runs in a worker thread
    found = await w.find_guild("Copper Lanterns", "Azjol-Nerub", "us")
    assert found and found["id"] == 78
    assert client.asked == ("us", "Azjol-Nerub", "Copper Lanterns")  # warcraft_cli tries the slug spellings
    client.error = WarcraftLogsClientError("not_found", "no such guild")
    assert await w.find_guild("Nope", "Argent Dawn", "eu") is None
    client.error = WarcraftLogsClientError("network_error", "down")
    with pytest.raises(WCLError, match="network_error"):
        await w.find_guild("Copper Lanterns", "Azjol-Nerub", "us")


def test_serving_many_users_never_reads_with_a_user_login_saved_later(monkeypatch):
    """warcraft_cli looks for a saved user login on every request: once client credentials only, it's never used."""
    import time

    import warcraftlogs_cli.client as cli
    monkeypatch.setattr(cli, "load_provider_auth_state", lambda provider: {
        "auth_mode": "pkce", "access_token": "user", "expires_at": time.time() + 3600})
    tokens = []

    def post(self, url, *, token, body):
        tokens.append(token)
        return httpx.Response(200, json={"data": {}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(cli.WarcraftLogsClient, "_post_graphql", post)
    monkeypatch.setattr(cli.WarcraftLogsClient, "_token", lambda self: "client")
    monkeypatch.setattr(wcl_module, "_client_only", False)
    WCL()._run("{ a }", {})
    assert tokens == ["user"]  # warcraft_cli's own choice
    wcl_module.client_credentials_only()
    w = WCL()
    w._run("{ b }", {})
    with pytest.raises(WarcraftLogsClientError):  # nothing found in the empty answers
        w._client().guild(region="eu", realm="argent-dawn", name="The Copper Lanterns")
    assert len(tokens) > 2 and set(tokens[1:]) == {"client"}


async def test_a_report_s_pulls_are_read_anew_but_the_last_copy_serves_when_warcraft_logs_is_down(monkeypatch):
    from wipebot.core.report.base import PULLS_FRESH_S
    kept = Cache(":memory:")
    monkeypatch.setattr(wcl_module, "cache", lambda: kept)
    answers = [{"reportData": {"report": {"fights": [{"id": 1}]}}}, {"reportData": {"report": {"fights": [{"id": 2}]}}}]

    def run(self, query, variables, whole=False):
        if not answers:
            raise WCLError("upstream_error: Warcraft Logs returned HTTP 503")
        return answers.pop(0)
    monkeypatch.setattr(WCL, "_run", run)
    w = WCL()
    assert (await w.report_fights("abc", PULLS_FRESH_S))["fights"] == [{"id": 1}]
    kept._db.execute("UPDATE cache SET stored_at = stored_at - 60")
    assert (await w.report_fights("abc", PULLS_FRESH_S))["fights"] == [{"id": 2}]  # read anew: a pull came
    kept._db.execute("UPDATE cache SET stored_at = stored_at - 60")
    assert (await w.report_fights("abc", PULLS_FRESH_S))["fights"] == [{"id": 2}]  # down: the last copy


async def test_while_points_are_counted_each_query_asks_its_cost_and_adds_it_up(monkeypatch):
    """A tool call's Warcraft Logs points, for the records: each query also asks rateLimitData's currentRequest,
    which leaves the answer it came with; outside the count, queries go as written."""
    w = WCL()
    asked = []

    class Client:
        def raw_graphql(self, operation_name, query, variables):
            asked.append(query)
            cost = {"currentRequest": {"application": 1, "database": 2, "user": 3, "total": 3}}
            return {"reportData": {"report": {"title": "x"}},
                    **({wcl_module.COST_ALIAS: cost} if wcl_module.COST_ALIAS in query else {})}, "public"
    monkeypatch.setattr(w, "_client", lambda: Client())
    q = "query($code: String!) { reportData { report(code: $code) { title } } }"
    with wcl_module.counting_points() as points:
        assert await w.query(q, code="A") == {"reportData": {"report": {"title": "x"}}}
        await w.query(q, code="B")
    assert asked[0] == ("query($code: String!) { wipebotCost: rateLimitData { currentRequest }  reportData { "
                        "report(code: $code) { title } } }")
    assert points.record() == {"application": 2, "database": 4, "user": 6, "total": 6, "queries": 2}
    await w.query(q, code="C")
    assert asked[-1] == q and points.record()["queries"] == 2
    # A raw query with fragments goes as written: where its first selection is isn't plain
    assert wcl_module._with_cost("fragment F on Report { title } query { a }") == (
        "fragment F on Report { title } query { a }")
