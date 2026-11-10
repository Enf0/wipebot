"""raidstrats.gg (raidstrats.py): a plan an officer saves by its link, fetched with the key, the link's raiders
written on its spots. raidstrats.gg is faked."""
import json

import httpx
import pytest

from tests.support import PLAN
from wipebot.core import raidstrats
from wipebot.core.report import FactError

REAL_CLIENT = httpx.AsyncClient


def fake_plan_api(monkeypatch, exports: dict[str, str], status: int = 200) -> list[dict]:
    """raidstrats.gg's plan REST API answering the exports of `exports` (plan id -> text; "" a guild's own plan);
    returns the requests' headers."""
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.headers))
        assert request.url.path == "/api/plans/addon-export"
        if status != 200:
            return httpx.Response(status, json={"error": "Missing or invalid Authorization header."})
        ids = json.loads(request.content)["planIds"]
        assert len(ids) == len(set(ids))
        return httpx.Response(200, json={"ok": True, "results": [
            {"ok": True, "planId": i, "addonExport": "!" + exports[i]} if exports.get(i)
            else {"ok": False, "planId": i, "code": "guild_plan" if i in exports else "not_found"} for i in ids]})
    monkeypatch.setattr(raidstrats.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(answer), **kw))
    return asked


async def test_a_plan_saved_by_its_link_is_fetched_with_the_key_and_gets_the_links_raiders(monkeypatch):
    export = PLAN.read_text(encoding="utf-8").strip()
    plan_id = raidstrats.plans.parse(export)[0].plan_id
    link = f"https://raidstrats.gg/planner?view={plan_id}&players=Rowan%7Cwr%2CHalvard%7Cdk%2C9%3AEsme%7Csh"
    assert await raidstrats.from_links("just a question") == "just a question"
    assert await raidstrats.from_links(export) == export  # an export is saved as pasted
    monkeypatch.delenv("RSGG_REST_API_KEY", raising=False)
    with pytest.raises(FactError, match="RSGG Addon"):  # no key here: the officer pastes the export
        await raidstrats.from_links(link)
    monkeypatch.setenv("RSGG_REST_API_KEY", "key-1")
    asked = fake_plan_api(monkeypatch, {plan_id: export, "theirs": ""})
    text = await raidstrats.from_links(f"P1 {link} and again {link}")
    assert len(asked) == 1 and asked[0]["authorization"] == "Bearer key-1" and asked[0]["user-agent"] == "Wipebot"
    first, second = raidstrats.plans.parse(text)
    assert first.plan_id == second.plan_id == plan_id
    named = {s.slot: s.label for s in first.scenes[0].spots}
    assert (named[1], named[2], named[9]) == ("Rowan", "Halvard", "Esme")
    with pytest.raises(FactError, match="guild's own plan"):
        await raidstrats.from_links("https://raidstrats.gg/planner?view=theirs")
    with pytest.raises(FactError, match="no such plan"):
        await raidstrats.from_links("https://raidstrats.gg/planner?view=gone")
    fake_plan_api(monkeypatch, {}, status=401)
    with pytest.raises(FactError, match="refused this Wipebot's key"):
        await raidstrats.from_links(link)
