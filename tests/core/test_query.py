"""Queries the model writes (report/query.py): read-only, within the hour's points, in parts."""
from typing import cast

import pytest

from wipebot.core.cache import Cache, cache_key
from wipebot.core.report import FactError
from wipebot.core.report import query as query_module
from wipebot.core.report.query import ANSWER_CHARS, RESERVE_POINTS, _checked, query_text
from wipebot.core.wcl import WCL


class Points:
    """A Warcraft Logs that spends `cost` points on each query and answers `answer`."""
    def __init__(self, spent: float, answer: dict, cost: float = 5):
        self.spent, self.answer, self.cost, self.asked = spent, answer, cost, []
        self.rate_checks = 0

    async def query(self, query: str, cache_for: float | None = None, **variables) -> dict:
        if "rateLimitData" in query:
            self.rate_checks += 1
            return {"rateLimitData": {"limitPerHour": 3600, "pointsSpentThisHour": self.spent, "pointsResetIn": 1800}}
        self.asked.append(query)
        self.spent += self.cost
        return self.answer


@pytest.mark.parametrize("query", [
    "mutation { deleteReport(code: \"x\") }",
    "{ userData { currentUser { name } } }",
    "{ __schema { types { name } } }",
    "{ nothing }",
])
def test_only_reading_queries_of_the_public_roots_run(query):
    with pytest.raises(FactError):
        _checked(query, "Abc")


def test_code_stands_for_the_report():
    assert _checked("{ reportData { report(code: $code) { title } } }", "Abc") == \
        '{ reportData { report(code: "Abc") { title } } }'
    assert _checked('{ reportData { report(code: "$code") { title } } }', "Abc") == \
        '{ reportData { report(code: "Abc") { title } } }'  # as the model wrote it once
    declared = "query($code: String!) { reportData { report(code: $code) { title } } }"
    assert _checked(declared, "Abc") == declared  # a variable the query declares itself is left alone
    with pytest.raises(FactError, match="report"):
        _checked("{ reportData { report(code: $code) { title } } }", None)


async def test_an_answer_says_what_it_cost_and_comes_in_parts():
    wcl = Points(100, {"worldData": {"rows": ["x" * 100] * 200}})
    first = await query_text(cast(WCL, wcl), "{ worldData { rows } }", None)
    assert first.startswith("Warcraft Logs' answer (5 points; 105 of 3600 spent this hour")
    assert "part 1 of 2" in first and first.endswith("(Part 1 of 2: the next is part 2.)")
    second = await query_text(cast(WCL, wcl), "{ worldData { rows } }", None, part=2)
    assert "part 2 of 2" in second and len(second) < ANSWER_CHARS


async def test_the_hours_last_points_are_kept_for_the_other_tools():
    wcl = Points(3600 - RESERVE_POINTS, {})
    with pytest.raises(FactError, match="come back in 30 min"):
        await query_text(cast(WCL, wcl), "{ worldData { zone(id: 1) { name } } }", None)
    assert not wcl.asked


async def test_an_answer_asked_moments_ago_costs_no_checks_of_the_points(monkeypatch):
    kept = Cache(":memory:")
    monkeypatch.setattr(query_module, "cache", lambda: kept)
    q = "{ worldData { zone(id: 1) { name } } }"
    kept.put(cache_key("wcl", q, {}), {"worldData": {"zone": {"name": "x"}}})
    wcl = Points(100, {})
    text = await query_text(cast(WCL, wcl), q, None)
    assert text.startswith("Warcraft Logs' answer (kept from 0 min ago: no points)") and '"name":"x"' in text
    assert not wcl.asked and not wcl.rate_checks


async def test_an_answer_too_long_to_read_is_refused_with_where_its_length_is():
    """Each part is another turn of the model's: 15 parts of raw events took minutes to read (#682)."""
    answer = {"reportData": {"report": {"bursts": {"data": ["x" * 100] * 400}, "deaths": {"data": ["y"] * 10}}}}
    with pytest.raises(FactError, match=r"\(4 parts\), too long to read: bursts 41,\d+, deaths \d+\. Ask for less"):
        await query_text(cast(WCL, Points(100, answer)), "{ reportData { report { bursts deaths } } }", None)


async def test_an_empty_answer_to_a_filter_says_an_unreadable_filter_reads_as_empty_too():
    """Warcraft Logs answers a filterExpression with a field it doesn't have (`fight = 4`) with nothing, no error."""
    q = '{ reportData { report { events(filterExpression: "fight = 4") { data } } } }'
    empty = await query_text(cast(WCL, Points(100, {"reportData": {"report": {"events": {"data": []}}}})), q, None)
    assert "Empty with a filterExpression" in empty and "`timestamp` counts ms from each fight's start" in empty
    found = await query_text(cast(WCL, Points(100, {"reportData": {"report": {"events": {"data": [1]}}}})), q, None)
    assert "Empty with" not in found
    unfiltered = "{ reportData { report { events { data } } } }"
    assert "Empty with" not in await query_text(
        cast(WCL, Points(100, {"reportData": {"report": {"events": {"data": []}}}})), unfiltered, None)
