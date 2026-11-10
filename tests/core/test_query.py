"""Queries the model writes (report/query.py): read-only, within the hour's points, in parts."""
import re
from typing import cast

import pytest

from wipebot.core import wcl as wcl_module
from wipebot.core.cache import Cache, cache_key
from wipebot.core.report import FactError
from wipebot.core.report import query as query_module
from wipebot.core.report.query import (
    ANSWER_CHARS,
    RESERVE_POINTS,
    _asked,
    _checked,
    _pulls,
    query_text,
    uses_report,
)
from wipebot.core.text import exact
from wipebot.core.wcl import WCL


class Points:
    """A Warcraft Logs that spends `cost` points on each query and answers `answer`, while other calls spend
    `others` of the account's points."""
    def __init__(self, spent: float, answer: dict, cost: float = 5, others: float = 0):
        self.spent, self.answer, self.cost, self.others, self.asked = spent, answer, cost, others, []
        self.rate_checks = 0

    async def points(self) -> dict:  # as the latest answer gave them: no query of its own
        return {"limitPerHour": 3600, "pointsSpentThisHour": self.spent, "pointsResetIn": 1800}

    async def query(self, query: str, cache_for: float | None = None, **variables) -> dict:
        if "rateLimitData" in query:
            self.rate_checks += 1
            return {"rateLimitData": await self.points()}
        self.asked.append(query)
        self.spent += self.cost + self.others
        if (counted := wcl_module._points.get()) is not None:
            counted.add({"total": self.cost})
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


async def test_a_querys_cost_is_its_own_and_the_hours_points_are_not_asked_for_apart():
    """The cost was the account's points after less before, two more requests, so whatever other calls spent
    meanwhile was shown as this query's."""
    wcl = Points(100, {"worldData": {"zone": {"name": "x"}}}, others=500)
    with wcl_module.counting_points() as call:
        text = await query_text(cast(WCL, wcl), "{ worldData { zone(id: 1) { name } } }", None)
    assert text.startswith("Warcraft Logs' answer (5 points; 605 of 3600 spent this hour")
    assert wcl.rate_checks == 0 and len(wcl.asked) == 1
    assert call.record() == {"total": 5, "queries": 1}  # counted for the tool call's record too


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


async def test_an_answer_too_long_to_read_is_cut_to_one_part_and_says_what_was_left_out():
    """It was refused, after its points were spent, which was most of the calls that failed. Each part is another
    turn of the model's (15 parts of raw events took minutes to read, #682), so it is cut to one."""
    answer = {"worldData": {"rows": [{"name": f"n{i}", "gear": [{"id": g} for g in range(40)]} for i in range(400)],
                            "few": ["y"] * 10}}
    text = await query_text(cast(WCL, Points(100, answer)), "{ worldData { rows few } }", None)
    assert "part 1 of" not in text and len(text) < ANSWER_CHARS + 200
    # the lists inside a row go first, then the rows; each cut list ends with how many it left out
    assert '{"name":"n0","gear":[{"id":0},{"id":1},{"id":2},"… 37 more left out"]}' in text
    rows = re.search(r'\{"name":"n(\d+)","gear":\[[^\]]*\]\},"… (\d+) more left out"\],"few":\["y","y"', text)
    assert rows and int(rows[1]) + 1 + int(rows[2]) == 400 and int(rows[1]) > 100  # as many rows as fit
    assert re.search(r"\(Cut short: whole, the answer is 1\d\d,\d+ characters; by field, as Warcraft Logs gave it: "
                     r"rows 1\d\d,\d+, few \d+\. Each list above says how much of it was left out", text)
    assert await query_text(cast(WCL, Points(100, answer)), "{ worldData { rows few } }", None, part=3) == text


def test_parts_end_with_a_line_where_lines_are_short():
    text = "\n".join(f"row {i} " + "x" * 90 for i in range(300))
    pages = query_module._pages(text)
    assert len(pages) == 3 and "\n".join(pages) == text
    assert all(len(page) <= ANSWER_CHARS and page.startswith("row ") and page.endswith("x") for page in pages)
    assert query_module._pages("") == [""]


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


# ---- a pull by its number, and an answer in the tools' terms ----

def test_a_pull_stands_in_a_query_by_its_number(rep):
    """The model asked for the report's fights first, to turn "pull 13" into a fight id and a time into ms, a
    query spent on nothing else."""
    f, last = rep.fight(13, "Sszorak"), rep.fight(None, "Sszorak")
    t0 = f["startTime"]
    q = ("{ reportData { report(code: $code) { events(fightIDs: [$fight(13)], startTime: $at(13, 2:15), "
         "endTime: $end(13)) { data } } } }")
    assert _pulls(q, rep, "Sszorak") == (
        f"{{ reportData {{ report(code: $code) {{ events(fightIDs: [{f['id']}], startTime: {t0 + 135_000}, "
        f"endTime: {f['endTime']}) {{ data }} }} }} }}")
    assert f["id"] == 21  # as the recorded case asks it: tests/fixtures/real/tools/cases.toml
    assert _pulls("$start(13) $start $fight $at(0:10) $at(90)", rep, "Sszorak") == (
        f"{t0} {last['startTime']} {last['id']} {last['startTime'] + 10_000} {last['startTime'] + 90_000}")
    assert _pulls('$fight(best wipe) $fight("first")', rep, "sszorak") == (
        f"{rep.fight('best wipe', 'Sszorak')['id']} {rep.fight('first', 'Sszorak')['id']}")
    vashnik = [f["id"] for f in rep.by_boss[rep.boss("Vashnik")]]
    assert _pulls("fightIDs: $fights", rep, "Vashnik") == f"fightIDs: {vashnik}"
    declared = "query($fight: Int, $start: Float) { reportData { report(code: $code) { fights(fightIDs: [$fight]) } } }"
    assert _pulls(declared, rep, None) == declared  # variables the query declares itself are left alone


@pytest.mark.parametrize("query, error", [
    ("$fight(99)", "Sszorak has pulls 1-33"),
    ("$at()", "takes a pull and a time"),
    ("$at(13, noon)", "takes a pull and a time"),
    ("$start(1, 2)", "takes one pull"),
])
def test_a_pull_a_query_cant_stand_for_is_an_error_that_says_how(rep, query, error):
    with pytest.raises(FactError, match=error):
        _pulls(query, rep, "Sszorak")


def test_a_pull_needs_a_report():
    with pytest.raises(FactError, match="needs a report"):
        _pulls("{ reportData { report(code: \"Abc\") { events(fightIDs: [$fight(3)]) { data } } } }", None, None)
    assert uses_report("events(fightIDs: [$fight(3)])") and uses_report("report(code: $code)")
    assert not uses_report('{ worldData { zone(id: 1) { name } } }')


def test_a_querys_text_says_where_its_reports_are_in_the_answer():
    asked = _asked('query Q($x: Int) { a: reportData { report(code: "A1") { table(fightIDs: 3) } } '
                   'b: reportData { r: report(code: "B2") { f: fights(fightIDs: [5, 6]) { id } '
                   'events(fightIDs:[5,6], filterExpression: "source.name = \\"X (y) { z\\"") { data } } } '
                   'worldData { zone(id: 1) { name } } }')
    assert asked is not None
    assert asked.reports == {("a", "report"): "A1", ("b", "r"): "B2"}
    assert asked.fights == {("a", "report"): {3}, ("b", "r"): {5, 6}}
    assert asked.fight_lists == {("b", "r", "f")}
    assert _asked("{ reportData { report(code: \"A1\") { ...Fields } } }") is None  # a fragment: the text doesn't say
    assert _asked("{ reportData { report(code: \"A1\") { title } }") is None


async def test_events_come_as_rows_with_the_time_into_the_pull_and_names(rep):
    """A list of events was raw JSON: report ms, actor ids and ability ids the model then asked `fights` and
    `masterData` for and converted by hand."""
    f = rep.fight(13, "Sszorak")
    deaths = await rep.deaths(f)
    answer = {"reportData": {"report": {"events": {"data": deaths, "nextPageTimestamp": None}}}}
    q = ("{ reportData { report(code: $code) { events(fightIDs: [$fight(13)], dataType: Deaths) "
         "{ data nextPageTimestamp } } } }")
    text = await query_text(cast(WCL, Points(100, answer)), q, rep, boss="Sszorak")
    lines = text.splitlines()
    assert lines[1] == '{"reportData":{"report":{"events":{"data":"events 1 below","nextPageTimestamp":null}}}}'
    assert lines[2] == (f"Events 1 (reportData.report.events.data): {len(deaths)} in Sszorak pull 13, times from the "
                        "pull's start.")
    assert lines[3].startswith(f"Counted: {len(deaths)} death; on: ")
    first = deaths[0]
    blow = first["killingAbilityGameID"]
    assert lines[4] == (f"{exact(first['timestamp'] - f['startTime'])} death: {rep.names[first['targetID']]}; killer "
                        f"{rep.actor(first['killerID'])}, killingAbility {rep.ability(blow)}")
    assert re.fullmatch(r"0:48\.\d{3} death: Scopidruid; killer Environment, killingAbility Ula'tek's Presence",
                        lines[4])
    assert f"Ula'tek's Presence [{blow}]" in lines[-2] and lines[-2].startswith("Ability ids: ")
    assert lines[-1] == "Fights: 21 = Sszorak pull 13 (2:42 long, ended with the boss at 62.59% health)."
    assert str(first["timestamp"]) not in text  # no report ms left to convert


async def test_events_over_several_pulls_come_under_each_pull_and_long_lists_are_cut_and_counted(rep):
    fights = rep.by_boss["Sszorak"]
    q = "{ reportData { report(code: $code) { events(fightIDs: $fights, dataType: Deaths) { data } } } }"
    few = [d for f in fights[:5] for d in await rep.deaths(f)]
    text = await query_text(cast(WCL, Points(100, {"reportData": {"report": {"events": {"data": few}}}})), q, rep,
                            boss="Sszorak")
    assert (f"Events 1 (reportData.report.events.data): {len(few)} over 5 pulls, each time from its pull's start."
            in text)
    assert "\nSszorak pull 1:\n" in text and "\nSszorak pull 5:\n" in text and "\nSszorak pull 6:\n" not in text
    assert text.count(" death: ") == len(few) and "left out" not in text
    assert f"Counted: {len(few)} death; on: " in text
    # a night's fights are named in short: the pull each id is, without its length and ending
    ids = [f["id"] for f in fights]
    assert text.splitlines()[-1] == (
        "Fights: " + "; ".join(f"{i} = Sszorak pull {n}" for n, i in enumerate(ids[:30], 1)) + "; and 3 more.")
    # the whole night's: over three parts, so cut to one, and counted in full
    deaths = [d for f in fights for d in await rep.deaths(f)]
    cut = await query_text(cast(WCL, Points(100, {"reportData": {"report": {"events": {"data": deaths}}}})), q, rep,
                           boss="Sszorak")
    assert "part 1 of" not in cut and len(cut) < ANSWER_CHARS + 200
    shown = cut.count(" death: ")
    assert 50 < shown < len(deaths)
    left = re.search(r"… ([\d,]+) more events left out, the last at (\d+:\d\d\.\d{3}) of Sszorak pull 33: the counts "
                     r"above are of all ([\d,]+)\.", cut)
    assert left and int(left[1].replace(",", "")) == len(deaths) - shown
    assert int(left[3].replace(",", "")) == len(deaths) and f"Counted: {len(deaths)} death; on: " in cut
    assert "(Cut short: whole, the answer is" in cut


async def test_a_reports_other_times_read_into_their_pull_and_a_fights_field_keeps_its_own(rep):
    f, g = rep.fight(13, "Sszorak"), rep.fight(14, "Sszorak")
    table = {"data": {"auras": [{"name": "Sated", "guid": 57724, "bands": [
        {"startTime": f["startTime"] + 2_197, "endTime": f["endTime"]}]}],
        "entries": [{"name": "Scopidruid", "type": "Druid", "timestamp": f["startTime"] + 48_031, "fight": f["id"]}],
        "startTime": f["startTime"], "endTime": f["endTime"], "totalTime": 162_854}}
    answer = {"reportData": {"report": {"fights": [{"id": f["id"], "startTime": f["startTime"]}], "table": table}}}
    q = ("{ reportData { report(code: $code) { fights(fightIDs: [$fight(13)]) { id startTime } "
         "table(fightIDs: [$fight(13)], dataType: Debuffs) } } }")
    text = await query_text(cast(WCL, Points(100, answer)), q, rep, boss="Sszorak")
    assert '"bands":[{"startTime":"0:02.197","endTime":"2:42.854"}]' in text
    assert '{"name":"Scopidruid","type":"Druid","timestamp":"0:48.031","fight":21}' in text  # a row, not an event
    assert '"startTime":"0:00.000","endTime":"2:42.854","totalTime":162854' in text
    assert f'"fights":[{{"id":21,"startTime":{f["startTime"]}}}]' in text  # the report's ms: what startTime takes
    # over two pulls, each time says which pull it is in
    both = q.replace("table(fightIDs: [$fight(13)]", "table(fightIDs: [$fight(13), $fight(14)]")
    table["data"]["auras"][0]["bands"].append({"startTime": g["startTime"] + 1_000, "endTime": g["startTime"] + 5_500})
    text = await query_text(cast(WCL, Points(100, answer)), both, rep, boss="Sszorak")
    assert ('{"startTime":"0:02.197 of Sszorak pull 13","endTime":"2:42.854 of Sszorak pull 13"},'
            '{"startTime":"0:01.000 of Sszorak pull 14","endTime":"0:05.500 of Sszorak pull 14"}') in text
    assert text.splitlines()[-1].startswith("Fights: 21 = Sszorak pull 13 (2:42 long, ended with the boss at 62.59% "
                                            "health); 22 = Sszorak pull 14 (")


async def test_another_report_a_query_names_is_read_for_its_names_only_when_the_answer_has_times(rep):
    """A top parse's casts are in another report: its names and pulls come from reading it, once, and a table of
    it, whose rows carry their names, reads nothing more."""
    f = rep.fight(13, "Sszorak")
    read = []

    async def reports(code: str):
        read.append(code)
        if code == "Private123456789":
            raise FactError("Warcraft Logs won't show report Private123456789.")
        return rep

    events = {"data": (await rep.deaths(f))[:2]}
    q = ('{ reportData { a: report(code: "Other12345678901") { events(fightIDs: [21], dataType: Deaths) { data } } '
         'b: report(code: "Table12345678901") { table(fightIDs: [3], dataType: DamageDone) } '
         'c: report(code: "Private123456789") { events(fightIDs: [4], dataType: Deaths) { data } } } }')
    answer = {"reportData": {"a": {"events": events}, "c": {"events": events},
                             "b": {"table": {"data": {"entries": [{"name": "X", "total": 5}]}}}}}
    text = await query_text(cast(WCL, Points(100, answer)), q, None, reports=reports)
    assert read == ["Other12345678901", "Private123456789"]
    assert '"a":{"events":{"data":"events 1 below"}}' in text and " death: Scopidruid; " in text
    assert '"b":{"table":{"data":{"entries":[{"name":"X","total":5}]}}}' in text
    assert '"c":{"events":{"data":[{"timestamp":' in text  # as logged
    assert "Fights of report Other12345678901: 21 = Sszorak pull 13" in text
    assert text.endswith("Report Private123456789 couldn't be read for its names (Warcraft Logs won't show report "
                         "Private123456789): its ids and times are as logged.")


async def test_an_answer_about_no_report_is_shown_as_warcraft_logs_gave_it():
    answer = {"worldData": {"encounter": {"fightRankings": {"rankings": [{"startTime": 1760000000000, "fightID": 3,
                                                                         "report": {"code": "Abc", "fightID": 3}}]}}}}
    text = await query_text(cast(WCL, Points(100, answer)), "{ worldData { encounter(id: 1) { fightRankings } } }")
    assert text.splitlines()[1:] == [query_module._json(answer)]
