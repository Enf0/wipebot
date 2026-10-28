"""Private reports (wcl.mark_private): read only with the asker's private-reports token, kept per token, never cached
on disk or shared with another user, and left out of the call records."""
import hashlib

import pytest
from mcp.server.auth.middleware import auth_context
from mcp.server.mcpserver.exceptions import ToolError

from tests.support import CODE, FakeWCL
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

    async def private_report(self, code):
        return wcl.private_token() in self.readers

    async def report_fights(self, code, cache_for=None):
        if wcl.private_token() not in self.readers or not wcl.is_private(code):
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
