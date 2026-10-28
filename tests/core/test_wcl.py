"""Warcraft Logs access (wcl.py): a request that hangs, answers in part or about a report gone private; several
fields in one query, and every page of a long answer."""
import re
import time

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
               GRAPHQL_WARNINGS_KEY: [{"message": "You do not have permission to view this report."}]},
              {"reportData": {"report": None}, GRAPHQL_WARNINGS_KEY: [{"message": "This report does not exist."}]})
    for _ in range(3):
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


async def test_a_nights_events_are_asked_a_fight_an_alias_and_come_back_in_time_order(monkeypatch):
    """Over several fights in one alias, Warcraft Logs pages by time (a night's casts of one player in forty
    queries one after another); a fight an alias, they come in one."""
    w = WCL()
    asked = []

    async def query(q, cache_for=None, **variables):
        asked.append(q)
        found = re.findall(r"(f\d+): events\(fightIDs: \[(\d+)\]", q)
        return {"reportData": {"report": {alias: {"data": [{"fight": int(f), "timestamp": 1000 - int(f)}],
                                                  "nextPageTimestamp": None} for alias, f in found}}}
    monkeypatch.setattr(w, "query", query)
    got = await w.events_many("ABC", [([1, 2, 3], {}), (4, {}), ([], {})], "Casts", sourceID=7)
    assert len(asked) == 1 and len(re.findall(r"f\d+: events", asked[0])) == 4
    assert [e["fight"] for e in got[0]] == [3, 2, 1] and got[1] == [{"fight": 4, "timestamp": 996}] and got[2] == []


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


async def test_the_latest_ranked_kills_are_found_by_halving_to_the_last_page(monkeypatch):
    w = WCL()
    last = 13  # pages 1 to 13 have kills, 13 only two
    asked = []

    async def query(_q, cache_for=None, **variables):
        n = variables["page"]
        asked.append(n)
        kills = [] if n > last else [{"startTime": n * 100 + i} for i in range(2 if n == last else 50)]
        return {"worldData": {"encounter": {"fightRankings": {"rankings": kills, "hasMorePages": n < last}}}}
    monkeypatch.setattr(w, "query", query)
    kills, cut = await w.ranked_kills(1, 5, "progress", 20, latest=True)
    assert [k["startTime"] for k in kills] == [*range(1232, 1250), 1300, 1301]
    assert not cut
    assert len(asked) < 10  # page 1, five halvings, the last page and the one before it


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
    which leaves the answer it came with; outside the count, it isn't added up."""
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
    assert asked[0] == ("query($code: String!) { wipebotCost: rateLimitData { limitPerHour pointsSpentThisHour "
                        "pointsResetIn databaseLimitPerHour databasePointsSpentThisHour currentRequest }  reportData { "
                        "report(code: $code) { title } } }")
    assert points.record() == {"application": 2, "database": 4, "user": 6, "total": 6, "queries": 2}
    await w.query(q, code="C")
    assert wcl_module.COST_ALIAS in asked[-1] and points.record()["queries"] == 2
    # A raw query with fragments goes as written: where its first selection is isn't plain
    assert wcl_module._with_cost("fragment F on Report { title } query { a }") == (
        "fragment F on Report { title } query { a }")


def _hourly(w, monkeypatch, spent: float, reset_in: int, cost: float = 1, limit: int = 3600, db_spent: float = 9,
            **request) -> list[str]:
    """Answer w's queries as if `spent` of the hour's `limit` points (3600: the old point system) and `db_spent` of
    its 250 database points were gone, `reset_in` s before the next hour; each query costs `cost` in all, and
    `request` adds its other kinds (application, database, user)."""
    asked = []
    monkeypatch.setattr(wcl_module, "_limit", None)

    class Client:
        def raw_graphql(self, operation_name, query, variables):
            asked.append(query)
            return {"a": 1, wcl_module.COST_ALIAS: {"limitPerHour": limit, "pointsSpentThisHour": spent,
                                                    "pointsResetIn": reset_in, "databaseLimitPerHour": 250,
                                                    "databasePointsSpentThisHour": db_spent,
                                                    "currentRequest": {"total": cost, "database": 0,
                                                                       **request}}}, "public"
    monkeypatch.setattr(w, "_client", lambda: Client())
    return asked


async def test_when_an_hour_ends_its_points_are_logged_this_processs_and_the_accounts(monkeypatch, caplog):
    """So a host sees how much of the account's points went elsewhere: the other app, the evals, a developer."""
    w = WCL()
    _hourly(w, monkeypatch, 2500, 1, cost=3)
    await w.query("query { a }")
    await w.query("query { a }")
    # no query of its own: the last answer's
    assert await w.points() == {"limitPerHour": 3600, "pointsSpentThisHour": 2500, "pointsResetIn": 1,
                                "databaseLimitPerHour": 250, "databasePointsSpentThisHour": 9}
    now = time.time()
    monkeypatch.setattr(wcl_module.time, "time", lambda: now + 5)  # the next hour
    with caplog.at_level("INFO", "wipebot.core.wcl"):
        await w.query("query { a }")
    assert "the hour to" in caplog.text and ": 6 by this process (0 database); the account had spent at least " \
        "2,500 of 3,600 (9 database)" in caplog.text


async def test_kept_for_others_a_process_waits_for_the_next_hour_once_its_share_is_spent(monkeypatch):
    """The evals share the account's points with the bot and the server: past half the hour they wait."""
    w = WCL()
    asked = _hourly(w, monkeypatch, 2000, 600)
    slept = []
    monkeypatch.setattr(wcl_module.time, "sleep", slept.append)
    monkeypatch.setattr(wcl_module, "_keep_share", 1 / 2)
    await w.query("query { a }")  # nothing known yet: it goes
    await w.query("query { a }")  # 1600 left, 1800 kept: waits first
    assert slept == [605] and len(asked) == 2


def _budgeted(monkeypatch, **env: str) -> WCL:
    for name in ("WIPEBOT_WCL_SHARE", "WIPEBOT_WCL_LEAVE", "WIPEBOT_WCL_USER_POINTS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(f"WIPEBOT_WCL_{name.upper()}", value)
    return WCL()


async def test_a_server_with_a_share_of_the_points_is_refused_past_it_until_the_hour_ends(monkeypatch):
    """A public instance on a guild's account: a tenth of the hour is 360 points; past it, nothing is read, and
    the refusal says when to ask again. Before the account is opted in, every point it spends is the account's."""
    w = _budgeted(monkeypatch, share="0.1")
    asked = _hourly(w, monkeypatch, 1000, 600, cost=200, application=20, user=180)
    await w.query("query { a }")
    await w.query("query { a }")  # 200 of 360 spent: it goes
    with pytest.raises(WCLError, match=r"spent its share of this hour's points \(400 of 360\); ask again in 10 min"):
        await w.query("query { a }")
    assert len(asked) == 2
    now = time.time()
    monkeypatch.setattr(wcl_module.time, "time", lambda: now + 601)  # the next hour
    await w.query("query { a }")
    assert len(asked) == 3


async def test_on_the_new_point_system_a_share_counts_only_what_the_account_pays(monkeypatch):
    """Opted in, a signed-in user's own points pay first: only the application points (database points among
    them) are the account's."""
    w = _budgeted(monkeypatch, share="0.1", user_points="1")
    asked = _hourly(w, monkeypatch, 1000, 600, cost=200, application=20, user=180)
    for _ in range(5):
        await w.query("query { a }")  # 1000 in all, 100 of them the account's
    assert len(asked) == 5


async def test_the_new_point_system_is_told_from_the_accounts_limit_unless_set(monkeypatch):
    """So the switch needs nothing done: 3,600 an hour is the old system's, every account's alike; the new one's
    are 1,000, 3,000 or 9,000. WIPEBOT_WCL_USER_POINTS says it instead."""
    w = _budgeted(monkeypatch, share="0.1")
    _hourly(w, monkeypatch, 0, 600, limit=1000, cost=200, application=20, user=180)
    assert not wcl_module.on_new_point_system()  # nothing read yet
    for _ in range(5):
        await w.query("query { a }")  # 1000 in all, 100 of them the account's
    assert wcl_module.on_new_point_system() and not wcl_module.on_new_point_system(False)
    w = _budgeted(monkeypatch, share="0.1", user_points="0")
    _hourly(w, monkeypatch, 0, 600, limit=1000, cost=200, application=20, user=180)
    await w.query("query { a }")
    with pytest.raises(WCLError, match=r"spent its share of this hour's points \(200 of 100\)"):
        await w.query("query { a }")
    with pytest.raises(ValueError, match="WIPEBOT_WCL_USER_POINTS: 1 or 0"):
        _budgeted(monkeypatch, user_points="maybe")


async def test_once_the_accounts_database_points_are_spent_only_queries_spending_them_are_refused(monkeypatch):
    """On the new point system, rankings and report lists fail once the account's 250 an hour are gone, whoever
    spent them: they aren't sent, the refusal says when they're back, and report data still goes."""
    w = _budgeted(monkeypatch)
    asked = _hourly(w, monkeypatch, 500, 600, limit=1000, db_spent=250)
    await w.query("query { a }")
    rankings = "query { reportData { report(code: \"C\") { rankings(playerMetric: dps) } } }"
    with pytest.raises(WCLError, match="spent this hour's database points, which rankings, character pages and guild "
                                       "report lists cost; they're back in 10 min, and everything else still answers"):
        await w.query(rankings)
    await w.query("query { reportData { report(code: \"C\") { fights { id } } } }")
    assert len(asked) == 2
    w = _budgeted(monkeypatch)
    asked = _hourly(w, monkeypatch, 500, 600, db_spent=286)  # the old system counts them, but doesn't hold to it
    await w.query("query { a }")
    await w.query(rankings)
    assert len(asked) == 2


def test_the_queries_that_spend_database_points_are_told_by_their_fields():
    assert wcl_module.spends_database(wcl_module.GUILD_REPORTS)
    assert wcl_module.spends_database("query { characterData { c1: character(id: 1) { zoneRankings(zoneID: 1) } } }")
    assert not wcl_module.spends_database(wcl_module.GUILD_PROGRESS)  # a guild's zoneRanking is user-eligible
    assert not wcl_module.spends_database("query { reportData { report(code: \"C\") { fights { id } } } }")


async def test_warcraft_logs_saying_the_database_points_are_spent_stops_asking_for_them(monkeypatch):
    """Its wording is a guess (the account isn't on the new system yet): any error naming database points."""
    w = _budgeted(monkeypatch)
    _hourly(w, monkeypatch, 500, 600, limit=1000)
    await w.query("query { a }")
    sent = []

    class Spent:
        def raw_graphql(self, operation_name, query, variables):
            sent.append(query)
            raise WarcraftLogsClientError("graphql_error", "You have used all of your database points for this hour")
    monkeypatch.setattr(w, "_client", lambda: Spent())
    rankings = "query { worldData { encounter(id: 1) { characterRankings(className: \"Mage\") } } }"
    for _ in range(2):  # the second isn't sent
        with pytest.raises(WCLError, match="spent this hour's database points.*back in 10 min"):
            await w.query(rankings)
    assert len(sent) == 1


async def test_a_share_of_the_database_points_is_refused_past_it_too(monkeypatch):
    w = _budgeted(monkeypatch, share="0.1", user_points="1")
    asked = _hourly(w, monkeypatch, 1000, 600, cost=13, application=13, database=13)
    await w.query("query { a }")
    await w.query("query { a }")  # 13 of 25 database points: it goes
    with pytest.raises(WCLError, match=r"share of this hour's database points.*\(26 of 25\)"):
        await w.query("query { a }")
    assert len(asked) == 2


async def test_a_server_leaving_part_of_the_accounts_hour_to_others_stops_short_of_it(monkeypatch):
    """So a public instance gives way to the guild's raid night: with 30% left to others, it reads nothing once
    the account has less than 1080 of its 3600 points left, however little it spent itself."""
    w = _budgeted(monkeypatch, leave="0.3")
    asked = _hourly(w, monkeypatch, 2600, 600)
    await w.query("query { a }")  # nothing known yet: it goes
    with pytest.raises(WCLError, match="2,600 of the account's 3,600 points this hour are spent, and the rest is "
                                       "kept for others"):
        await w.query("query { a }")
    assert len(asked) == 1


def test_a_share_that_isnt_one_is_refused_at_start(monkeypatch):
    with pytest.raises(ValueError, match="WIPEBOT_WCL_SHARE: a share .* from 0 to 1, e.g. 0.1, not '10%'"):
        _budgeted(monkeypatch, share="10%")
    assert _budgeted(monkeypatch)._budget.refusal({"limitPerHour": 3600, "pointsSpentThisHour": 3599}, True,
                                          wcl_module.Counter(total=3599), 5) is None  # unset: no limit


def test_a_process_with_a_share_sees_its_own_share_of_the_hour():
    """The footer and /usage of a public copy show what it may spend, not the account's whole hour."""
    from collections import Counter
    rate = {"limitPerHour": 3600, "pointsSpentThisHour": 165, "pointsResetIn": 600}
    assert wcl_module.Budget().view(rate, Counter()) is rate  # no share: the account's hour
    mine = wcl_module.Budget(share=0.15, leave=0.3, user_points=False).view(rate, Counter(total=20))
    assert (mine["limitPerHour"], mine["pointsSpentThisHour"]) == (540, 20)
    # the others spent the account down to its last 30%: nothing left for this process
    busy = wcl_module.Budget(share=0.15, leave=0.3, user_points=False).view(
        {**rate, "pointsSpentThisHour": 2600}, Counter(total=20))
    assert busy["limitPerHour"] - busy["pointsSpentThisHour"] == 0
