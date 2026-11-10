"""Private reports (wcl.mark_private): read only with the asker's private-reports token, kept per token, never cached
on disk or shared with another user, and left out of the call records."""
import asyncio
import hashlib
import json
import re
import sqlite3
import threading
import zlib

import pytest
from mcp.server.auth.middleware import auth_context
from mcp.server.mcpserver.exceptions import ToolError
from warcraftlogs_cli.client import GRAPHQL_WARNINGS_KEY

from tests.support import CODE, FakeWCL
from tests.support.recorded import RECORDED
from wipebot.core import cache as cache_module
from wipebot.core import library, server, wcl
from wipebot.core.cache import Cache, cache_key


@pytest.fixture(autouse=True)
def _no_private_reports(monkeypatch):
    """No report is known private but what a test makes so: the sets live for the process."""
    monkeypatch.setattr(wcl, "_private_codes", set())
    monkeypatch.setattr(cache_module, "_unkept", set())


async def test_nothing_naming_a_private_report_is_kept_or_shared():
    kept = Cache(":memory:")
    fetched = []

    async def get():
        fetched.append(1)
        return {"x": len(fetched)}
    public, private = cache_key("wcl", "query", {"code": "PUBLIC0000000000"}), cache_key("report", "PRIVATE000000000")
    assert await kept.fetch(public, 60, get) == {"x": 1} == await kept.fetch(public, 60, get)
    kept.put(private, {"secret": 1})  # stored before it was known to be private
    wcl.mark_private("PRIVATE000000000")
    assert kept.get(private) is None
    assert await kept.fetch(private, 60, get) == {"x": 2} and await kept.fetch(private, 60, get) == {"x": 3}
    kept.put(private, {"secret": 2})
    assert kept._db.execute("SELECT COUNT(*) FROM cache WHERE key = ?", (private,)).fetchone()[0] == 1  # the old one
    assert kept.get(private) is None and kept.get(public) == ({"x": 1}, pytest.approx(0, abs=5))


def answering(w: wcl.WCL, monkeypatch, read_as: list) -> None:
    """warcraft_cli's client answers every query with a report, noting which token read it."""
    class Client:
        def raw_graphql(self, operation_name, query, variables):
            read_as.append(wcl._user.get())
            return {"reportData": {"report": {"visibility": "private"}}}, "user"
    monkeypatch.setattr(w, "_client", lambda: Client())


async def test_a_private_report_is_read_only_with_the_askers_private_token(monkeypatch):
    w, read_as = wcl.WCL(), []
    answering(w, monkeypatch, read_as)
    wcl.mark_private("PRIVATE000000000")
    with pytest.raises(wcl.Unreadable, match="won't show report PRIVATE000000000"):  # no private token: nobody's
        await w.query("query($code: String!) { x }", code="PRIVATE000000000")
    with wcl.reading_as("profile", "private"), wcl.counting_points() as points:
        await w.query("query($code: String!) { x }", code="PRIVATE000000000")
        await w.query('query { reportData { report(code: "PRIVATE000000000") { x } } }')  # named in the query
        await w.query("query($code: String!) { x }", code="PUBLIC0000000000")  # anything else: the first token
    assert read_as == ["private", "private", "profile"] and points.private
    assert points.record()["private"] is True


async def test_a_report_is_marked_private_only_once_the_private_token_reads_it(monkeypatch):
    """So a code that isn't a report the user may read (a typo, a word) never changes how anything is read."""
    w, read_as = wcl.WCL(), []
    answering(w, monkeypatch, read_as)
    assert not await w.private_report("PRIVATE000000000")  # no private token
    with wcl.reading_as(None, "private"):
        assert await w.private_report("PRIVATE000000000")
    assert read_as == ["private"] and not wcl.is_private("PRIVATE000000000")


class PrivateWCL(FakeWCL):
    """The night as a private report: only the tokens in `readers` read it."""

    def __init__(self, night, readers: set[str]):
        super().__init__(night)
        self.readers = readers
        self.hidden = False  # made private after it was read as a public one
        self.published = False  # made public after it was read as a private one
        self.asked_visibility = 0

    async def private_report(self, code):
        return wcl.private_token() in self.readers

    async def visibility(self, code):
        self.asked_visibility += 1
        if self.published:
            return "public"
        if self.hidden and wcl.private_token() not in self.readers:
            raise wcl.Unreadable(code)
        return "private" if self.hidden or wcl.is_private(code) else "public"

    async def report_fights(self, code, cache_for=None):
        if not self.published and (wcl.private_token() not in self.readers or not wcl.is_private(code)):
            raise wcl.Unreadable(code)
        return await super().report_fights(code, cache_for)


async def test_each_user_gets_a_private_report_with_their_own_token_and_nobody_else_does(night):
    lib = library.Library(wcl=PrivateWCL(night, {"a's", "b's"}).client())
    with wcl.reading_as(None, "a's"):
        a = await lib.report(CODE)
        assert await lib.report(CODE) is a
    assert wcl.is_private(CODE) and CODE not in lib._reports
    assert (hashlib.sha256(b"a's").hexdigest(), CODE) in lib._reports
    with wcl.reading_as(None, "b's"):
        assert await lib.report(CODE) is not a  # read with b's own token: Warcraft Logs decides
    for token in (None, "c's"):
        with wcl.reading_as("profile", token), pytest.raises(wcl.Unreadable):
            await lib.report(CODE)


async def test_a_report_nobody_may_read_isnt_marked_private(night):
    lib = library.Library(wcl=PrivateWCL(night, set()).client())
    with wcl.reading_as(None, "a's"), pytest.raises(wcl.Unreadable):
        await lib.report(CODE)
    assert not wcl.is_private(CODE)


async def test_a_users_call_on_a_private_report_isnt_kept_and_one_they_cant_read_says_how_to_allow_it(monkeypatch):
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1", "client_id": "c"})())
    kept, private = [], {}
    setup = server.Setup(remote=server.Remote(
        guilds=list, user_name=str, record=lambda *a: kept.append(a[7:]), wcl_private_token=private.get,
        private_reports_url="https://wipebot.example/wcl/private"))

    async def read() -> str:
        if not wcl.private_token():
            raise wcl.Unreadable("PRIVATE000000000")
        if points := wcl._points.get():  # as a query of it notes (wcl._run)
            points.private = True
        return "Sszorak pull 3: the raid's private secrets"

    with pytest.raises(ToolError, match=r"won't show report PRIVATE000000000.*let Wipebot read their private reports "
                                        r"at https://wipebot.example/wcl/private"):
        await server.guarded(read, setup)()
    private["1"] = "a's"
    assert await server.guarded(read, setup)() == "Sszorak pull 3: the raid's private secrets"
    assert kept[1] == (server.PRIVATE_KEPT, {"queries": 0, "private": True})


COST = re.compile(r" " + wcl.COST_ALIAS + r": rateLimitData \{ [^}]* \} ")


class ShownTo:
    """Stands in for warcraft_cli's client: the tests' recorded report as Warcraft Logs answers about a private one,
    shown only to a query read with `token`. `sent` lists the tokens the queries were read with."""

    def __init__(self, token: str):
        self.token, self.sent = token, []
        self._db = sqlite3.connect(f"file:{RECORDED}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self._one = threading.Lock()  # queries run in threads side by side

    def raw_graphql(self, operation_name, query, variables):
        plain = COST.sub("", query, count=1)
        self.sent.append(wcl._user.get())
        if wcl._user.get() != self.token:
            return {"reportData": {"report": None},
                    GRAPHQL_WARNINGS_KEY: [{"message": "You do not have permission to view this report."}]}, "client"
        if plain == wcl.VISIBILITY:
            return {"reportData": {"report": {"visibility": "private"}}}, "user"
        with self._one:
            row = self._db.execute("SELECT value FROM cache WHERE key = ?",
                                   (cache_key("wcl", plain, variables),)).fetchone()
        assert row, f"not recorded: {plain[:120]}"
        return json.loads(zlib.decompress(row[0])), "user"


@pytest.fixture
def private_night(monkeypatch) -> tuple[library.Library, ShownTo]:
    """The tests' report as a private one that only the token "a's" reads, through the real Warcraft Logs client code
    (wcl.WCL), with nothing loaded yet."""
    w, client = wcl.WCL(), ShownTo("a's")
    monkeypatch.setattr(w, "_client", lambda: client)
    return library.Library(wcl=w), client


async def test_no_call_on_a_private_report_is_kept_whether_it_asked_warcraft_logs_or_not(monkeypatch, private_night):
    """A call answered from what the call before it loaded sends no query, and two calls at once share one load:
    neither's record may hold the output."""
    monkeypatch.setattr(auth_context, "get_access_token", lambda: type("T", (), {"subject": "1", "client_id": "c"})())
    lib, client = private_night
    kept = []
    setup = server.Setup(lib, server.Remote(guilds=list, user_name=str, wcl_private_token=lambda subject: "a's",
                                            record=lambda *a: kept.append(a[7])))
    roster = server.guarded(server.roster, setup)
    assert "24 players" in await roster(report=CODE)
    asked = len(client.sent)
    assert "24 players" in await roster(report=CODE) and len(client.sent) == asked  # from memory: no query
    assert kept == [server.PRIVATE_KEPT] * 2
    lib._reports.clear()  # two calls at once, nothing loaded: a client's parallel tool calls
    assert all("24 players" in text for text in await asyncio.gather(roster(report=CODE), roster(report=CODE)))
    assert kept == [server.PRIVATE_KEPT] * 4


async def test_a_call_in_process_on_a_private_report_says_so_with_its_points_each_time(monkeypatch, private_night):
    """As the Discord bot runs a tool: its record of the call leaves the output out when the points say private."""
    lib, client = private_night
    monkeypatch.setattr(server.DEFAULT, "library", lib)
    for _ in range(2):
        with wcl.reading_as(None, "a's"), wcl.counting_points() as points:
            text, failed = await server.call("roster", {"report": CODE})
        assert not failed and "24 players" in text and points.record()["private"] is True


class Shown:
    """Stands in for warcraft_cli's client: a public report that Warcraft Logs shows until `hidden` is set (made
    private, or deleted), and then to nobody. `sent` counts the queries."""

    def __init__(self):
        self.hidden, self.sent = "", 0

    def raw_graphql(self, operation_name, query, variables):
        self.sent += 1
        if self.hidden:
            return {"reportData": {"report": None}, GRAPHQL_WARNINGS_KEY: [{"message": self.hidden}]}, "client"
        if "visibility" in query:
            return {"reportData": {"report": {"visibility": "public"}}}, "client"
        return {"reportData": {"report": {"code": variables["code"], "title": "Mythic night", "fights": []}}}, "client"


@pytest.mark.parametrize("hidden", ["You do not have permission to view this report.", "This report does not exist."])
async def test_what_was_kept_of_a_report_made_private_or_deleted_isnt_served_any_more(monkeypatch, hidden):
    """Read while it was public and kept on disk for good; then its owner hides or deletes it."""
    kept = Cache(":memory:")
    monkeypatch.setattr(wcl, "cache", lambda: kept)
    w, client = wcl.WCL(), Shown()
    monkeypatch.setattr(w, "_client", lambda: client)
    assert (await w.report_fights("PUBLIC0000000000", cache_for=60))["title"] == "Mythic night"
    kept.put(cache_key("report", "PUBLIC0000000000", "deaths"), ["who died"])
    kept._db.execute("UPDATE cache SET stored_at = stored_at - 3600")  # as a later question finds it
    client.hidden = hidden
    with pytest.raises(wcl.Unreadable):
        await w.report_fights("PUBLIC0000000000", cache_for=60)
    assert kept._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0  # nothing naming it is left on disk


async def test_a_loaded_report_is_asked_for_again_each_day_and_let_go_once_warcraft_logs_hides_it(night, monkeypatch):
    """A settled report stays loaded for as long as the process runs: Warcraft Logs is asked once a day whether it
    still shows it, and when it doesn't, nobody is answered from the copy in memory or on disk."""
    kept = Cache(":memory:")
    monkeypatch.setattr(library, "cache", lambda: kept)
    fake = PrivateWCL(night, set())
    monkeypatch.setattr(fake, "report_fights", FakeWCL(night).report_fights)  # public: anyone reads it
    lib = library.Library(wcl=fake.client())
    rep = await lib.report(CODE)
    kept.put(cache_key("report", CODE, "deaths"), ["who died"])
    assert await lib.report(CODE) is rep and fake.asked_visibility == 1  # asked with the load, not on every call
    lib._shown_at[CODE] -= library.VISIBLE_FOR_S + 1
    assert await lib.report(CODE) is rep and fake.asked_visibility == 2  # a day later: still shown
    lib._shown_at[CODE] -= library.VISIBLE_FOR_S + 1
    fake.hidden = True  # its owner made it private
    for _ in range(2):  # and asking again doesn't bring it back
        with pytest.raises(wcl.Unreadable):
            await lib.report(CODE)
    assert CODE not in lib._reports and kept.get(cache_key("report", CODE, "deaths")) is None


async def test_a_private_report_its_owner_published_is_read_and_kept_as_any_other_again(night):
    fake = PrivateWCL(night, {"a's"})
    lib = library.Library(wcl=fake.client())
    with wcl.reading_as(None, "a's"):
        await lib.report(CODE)
        assert wcl.is_private(CODE) and not cache_module.keeps(cache_key("report", CODE))
        fake.published = True
        lib._shown_at[hashlib.sha256(b"a's").hexdigest(), CODE] -= library.VISIBLE_FOR_S + 1
        await lib.report(CODE)  # a day later, Warcraft Logs says it's public
    assert not wcl.is_private(CODE) and cache_module.keeps(cache_key("report", CODE))
    with wcl.counting_points() as points:
        await lib.report(CODE)  # anyone reads it now, and their call is kept as any other
    assert list(lib._reports) == [CODE] and not points.private
