"""raidstrats.gg (raidstrats.py): a guild's login kept in the notebook and refreshed by whichever app needs it,
and the plans it can open fetched and saved as a new version only when they changed. raidstrats.gg is faked."""
import json
import time

import httpx
import pytest

from tests.support import PLAN
from wipebot.core import raidstrats
from wipebot.core.notebook import Notebook

TOKEN = {"access_token": "rsoat_1", "refresh_token": "rsort_1", "expires_in": 3600, "scope": "addon plans profile"}


@pytest.fixture
def book(tmp_path, monkeypatch) -> Notebook:
    monkeypatch.setenv("RAIDSTRATS_CLIENT_ID", "rs_app")
    monkeypatch.setenv("RAIDSTRATS_CLIENT_SECRET", "rss_secret")
    book = Notebook(tmp_path / "notes.sqlite")
    book.save_login(14169, "Enfo-2581", TOKEN, "Rowan")
    return book


def expire(book: Notebook, refreshed_days_ago: float = 0) -> None:
    assert book._db is not None
    book._db.execute("UPDATE raidstrats_logins SET access_expires = 0, refreshed_at = ?",
                     (time.time() - refreshed_days_ago * 86400,))
    book._db.commit()


def test_a_login_is_refreshed_once_when_expired_and_the_new_refresh_token_kept(book):
    traded = []

    def refresh(token):
        traded.append(token)
        return {"access_token": f"rsoat_{len(traded) + 1}", "refresh_token": f"rsort_{len(traded) + 1}",
                "expires_in": 3600}
    assert book.access_token(14169, refresh) == "rsoat_1" and traded == []  # still fresh
    expire(book)
    assert book.access_token(14169, refresh) == "rsoat_2" and traded == ["rsort_1"]
    # the other app, opening the same file, reads the new tokens instead of trading the old refresh token again
    other = Notebook(book.path)
    assert other.access_token(14169, refresh) == "rsoat_2" and traded == ["rsort_1"]
    assert book.access_token(9999, refresh) is None  # a guild without a login


def test_a_week_old_login_is_refreshed_to_keep_it_alive_and_a_refused_one_stops_working(book):
    expire(book, refreshed_days_ago=8)
    assert book._db is not None
    book._db.execute("UPDATE raidstrats_logins SET access_expires = ?", (time.time() + 3000,))
    book._db.commit()
    assert book.access_token(14169, lambda t: TOKEN | {"access_token": "new"}, raidstrats.KEEP_ALIVE_S) == "new"
    expire(book)
    assert book.access_token(14169, lambda t: None) is None
    login = book.login(14169)
    assert login and not login.working and "connects again" in login.problem and book.logins() == []


def fake_raidstrats(monkeypatch, exports: dict[str, str]) -> list[str]:
    """raidstrats.gg answering the plan list and the import texts of `exports` (plan id -> text); returns the
    paths asked."""
    asked = []
    real = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer rsoat_1"
        if request.url.path == "/api/oauth/v1/plans":
            return httpx.Response(200, json={"plans": [{"id": i} for i in exports],
                                             "guildPlans": [{"id": i} for i in exports]})
        ids = json.loads(request.content)["planIds"]
        assert len(ids) == len(set(ids))  # a plan both own and guild's is asked once
        return httpx.Response(200, json={"results": [
            {"ok": True, "id": i, "addonExport": exports[i]} if exports.get(i)
            else {"ok": False, "id": i, "error": "addon_export_missing"} for i in ids]})
    monkeypatch.setattr(raidstrats.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    return asked


async def test_the_plans_are_fetched_and_a_version_saved_only_when_one_changed(book, monkeypatch):
    export = "!" + PLAN.read_text(encoding="utf-8").strip()
    plan_id = raidstrats.plans.parse(export)[0].plan_id
    asked = fake_raidstrats(monkeypatch, {plan_id: export, "never-saved": ""})
    await raidstrats.sync(book, 14169)
    saved = book.plans(14169)
    assert [(p.boss, p.saved_by) for p in saved] == [("Sszorak", "raidstrats.gg")]
    assert saved[0].plan == export.lstrip("!")
    assert asked == ["/api/oauth/v1/plans", "/api/oauth/v1/plans/addon-export"]
    login = book.login(14169)
    assert login and login.fetched_at and not login.problem
    await raidstrats.sync(book, 14169)
    assert len(asked) == 2  # fetched a moment ago: not again
    expire_fetch = time.time() - raidstrats.FRESH_S - 1
    assert book._db is not None
    book._db.execute("UPDATE raidstrats_logins SET fetched_at = ?", (expire_fetch,))
    book._db.commit()
    await raidstrats.sync(book, 14169)
    assert len(asked) == 4 and len(book.plans(14169)) == 1  # fetched again, unchanged: no new version
    book.save_plan(14169, "Sszorak", plan_id, "Spread", "raidstrats-addon-pasted", "Rowan")
    book._db.execute("UPDATE raidstrats_logins SET fetched_at = ?", (expire_fetch,))
    book._db.commit()
    await raidstrats.sync(book, 14169)
    assert [p.saved_by for p in book.plans(14169)] == ["raidstrats.gg", "Rowan"]  # unchanged there: the paste stays


async def test_a_plan_deleted_on_raidstrats_is_dropped_and_a_paste_of_it_kept(book, monkeypatch):
    export = "!" + PLAN.read_text(encoding="utf-8").strip()
    plan_id = raidstrats.plans.parse(export)[0].plan_id
    book.save_plan(14169, "Coiled Altar", "deleted", "Old plan", "raidstrats-addon-old", raidstrats.SAVED_BY)
    book.save_plan(14169, "Coiled Altar", "deleted", "Old plan", "raidstrats-addon-pasted", "Rowan")
    book.save_plan(14169, "Coiled Altar", "default", "Wowutils plan", "raidstrats-addon-default", "Rowan")
    fake_raidstrats(monkeypatch, {plan_id: export})
    await raidstrats.sync(book, 14169)
    assert [(p.plan_id, p.saved_by) for p in book.plans(14169)] == [
        ("deleted", "Rowan"), ("default", "Rowan"), (plan_id, raidstrats.SAVED_BY)]


async def test_the_model_is_told_which_login_the_plans_are_fetched_with_and_when(book, monkeypatch):
    login = book.login(14169)
    assert login and "as Enfo-2581" in raidstrats.source(login) and "not fetched yet" in raidstrats.source(login)
    assert "RSGG Addon" in raidstrats.source(login)  # Viserio's default plans aren't fetched: an officer pastes one
    fake_raidstrats(monkeypatch, {})
    await raidstrats.sync(book, 14169)
    login = book.login(14169)
    assert login and ", last at " in raidstrats.source(login) and "failed" not in raidstrats.source(login)


async def test_when_raidstrats_fails_the_saved_plans_are_used_and_the_problem_kept(book, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(raidstrats.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw))
    await raidstrats.sync(book, 14169)  # doesn't raise
    login = book.login(14169)
    assert login and login.working and "didn't answer" in login.problem and login.fetched_at is None
    assert "the last fetch failed: raidstrats.gg didn't answer" in raidstrats.source(login)



async def test_a_refused_app_keeps_the_login_for_when_its_secret_is_fixed_and_a_refused_login_stops(book,
                                                                                                  monkeypatch):
    expire(book)
    refusal = httpx.Response(401, json={"error": "invalid_client",
                                        "error_description": "The client_id or secret is wrong."})
    monkeypatch.setattr(raidstrats.httpx, "post", lambda *a, **kw: refusal)
    await raidstrats.sync(book, 14169)
    login = book.login(14169)
    assert login and login.working and "refuses this Wipebot's app" in login.problem and book.logins() == [14169]
    refusal = httpx.Response(400, json={"error": "invalid_grant",
                                        "error_description": "The refresh token is not valid for this app."})
    await raidstrats.sync(book, 14169)
    login = book.login(14169)
    assert login and not login.working and "an officer connects again" in login.problem

async def test_the_login_address_must_carry_this_logins_state_and_code():
    with pytest.raises(raidstrats.RaidstratsError, match="cancelled"):
        await raidstrats.connect("http://localhost:8765/callback?error=access_denied&state=s1", "s1")
    with pytest.raises(raidstrats.RaidstratsError, match="isn't the address from this login"):
        await raidstrats.connect("http://localhost:8765/callback?code=c&state=other", "s1")
