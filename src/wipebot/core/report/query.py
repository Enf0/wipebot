"""Any Warcraft Logs query the model writes (`wcl_query`), for what no other tool shows, and the API's schema to
write it from. Read-only, within the hour's points less a share kept for the other tools; an answer is kept for a
while, so reading its later parts or asking it again costs nothing.

The answer comes in the tools' own terms, so nothing in it is converted by hand: a pull stands in the query by its
number and a player by name (`$fight(13)`, `$at(13, 2:15)`, `$player(Name)`: `_pulls`); event lists come back as
rows, each with its time into its pull and its actors and abilities named, counted and added up (rows.py); a
report's other times read m:ss into their pull, and the fights named get a legend (`_Answer`). One too long to read
is cut to a part, each list saying how much of it was left out, where it used to be refused after its points were
spent."""
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..cache import DAY, cache, cache_key
from ..text import clock, exact, parse_clock
from ..wcl import WCL, WCLError, counting_points
from . import rows
from .base import FactError, Report
from .fight import boss_left

RESERVE_POINTS = 600  # of the hour's points, kept for the other tools: a query is refused past the rest
ANSWER_CHARS = 12_000  # an answer is shown in parts this long
MAX_PARTS = 3  # a longer answer is cut to one part: each part is another turn of the model's (about 20 s)
EMPTY_FILTERED = ("\n(Empty with a filterExpression: Warcraft Logs answers one it can't read, such as a field it "
                  "doesn't have like `fight` or a typo, with nothing too, as if nothing matched. In it `timestamp` "
                  "counts ms from each fight's start; pulls go in fightIDs. Ask once without it to tell them apart.)")
KEEP_S = 10 * 60  # an answer is kept this long, so its parts and a repeat cost no points
ROOTS = ("reportData", "worldData", "characterData", "guildData", "progressRaceData", "rateLimitData")
TYPE_REF = "name kind ofType { name kind ofType { name kind ofType { name } } }"
SCHEMA = (f"query($name: String!) {{ __type(name: $name) {{ name kind description "
          f"fields {{ name description args {{ name description type {{ {TYPE_REF} }} }} type {{ {TYPE_REF} }} }} "
          f"inputFields {{ name description type {{ {TYPE_REF} }} }} enumValues {{ name description }} }} }}")
# What stands for a pull or a player of the tool's report in a query: $fight(13), $start(13), $end(13),
# $at(13, 2:15), $fights, $player(Name); with the quotes around one written as a string of its own, "$start(13)"
PULL = re.compile(r'("?)\$(fights|fight|start|end|at|player)\b(?:\(([^()]*)\))?\1')
NAMED_REPORTS = 6  # of the reports a query names, this many are read for their names (a load each, when not loaded)
LEGEND_FIGHTS = 30  # fights a legend names
LEGEND_DETAILED = 8  # up to this many, each with its length and how it ended
# A report's ms outside its events: a table's bands, a death's row, where a graph's series starts
TIME_KEYS = ("timestamp", "startTime", "endTime", "pointStart")
# In an answer too long to read, the lists inside a list's items (a table row's gear and abilities) are cut first, to
# this many items and then fewer until it fits; after the last, every list is.
INNER_KEPT = (20, 10, 5, 3)
TOKEN = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\.\.\.|[{}()\[\]:!=@$]|[^\s{}()\[\]:!=@$",]+')

Reports = Callable[[str], Awaitable[Report]]  # how a report a query names is read, by its code


def uses_report(query: str) -> bool:
    """Whether a query stands for something of the tool's report: its code (`$code`), or one of its pulls or
    players (PULL)."""
    return "$code" in query or PULL.search(query) is not None


def _declared(query: str, name: str) -> bool:
    """Whether the query declares `$name` as a variable of its own."""
    return re.search(rf"\${name}\s*:", query) is not None


def _pulls(query: str, rep: Report | None, boss: str | None) -> str:
    """The query with its pulls filled in: `$fight(13)` is pull 13's fight id, `$start(13)` and `$end(13)` its
    start and end and `$at(13, 2:15)` the moment 2:15 into it, in the report's ms (what startTime and endTime
    take); `$fights` the fight ids of every pull on the boss. Inside a string (a filterExpression, whose
    `timestamp` counts from the fight's start: docs/reference/wcl-api.md) the three times count from the pull's
    start too, where the report's ms would match nothing without an error. A pull is a number on `boss` or a name
    (kill, best wipe, ...: Report.fight); without one, the link's or the latest. `$player(Name)` is that player's
    actor id (what sourceID and targetID take): rows name the actors, so an answer has no ids to copy. A name the
    query declares as its own variable is left alone."""
    def filled(m: re.Match) -> str:
        name, given = m[2], m[3]
        in_filter = not m[1] and len(re.findall(r'(?<!\\)"', query[:m.start()])) % 2 == 1
        if given is None and _declared(query, name):
            return m[0]
        if rep is None:
            raise FactError(f"${name} needs a report: give `report`")
        if name == "fights":
            return json.dumps([f["id"] for f in rep.by_boss[rep.boss(boss)]])
        args = [a.strip().strip("\"'") for a in (given or "").split(",") if a.strip()]
        if name == "player":
            if len(args) != 1:
                raise FactError(f"{m[0]}: $player takes a player's name, $player(Name)")
            return str(rep.player(args[0]).id)
        after = 0.0
        if name == "at":
            at = parse_clock(args.pop()) if 1 <= len(args) <= 2 else None
            if at is None:
                raise FactError(f"{m[0]}: $at takes a pull and a time into it, $at(13, 2:15), or only the time for "
                                "the latest pull, $at(2:15)")
            after = at
        if len(args) > 1:
            raise FactError(f"{m[0]}: ${name} takes one pull, ${name}(13)")
        f = rep.fight(args[0] if args else None, boss)
        if name == "fight":
            return str(f["id"])
        return f"{(f['endTime'] if name == 'end' else f['startTime'] + after) - in_filter * f['startTime']:.0f}"
    return PULL.sub(filled, query)


def _checked(query: str, code: str | None) -> str:
    """The query as it will run: read-only roots only, `$code` filled in."""
    q = query.strip()
    if re.search(r"\b(mutation|subscription)\b", q) or "userData" in q or "__schema" in q:
        raise FactError("only reading queries of " + ", ".join(ROOTS) + " (the schema: schema_type)")
    roots = set(re.findall(r"\b(" + "|".join(ROOTS) + r")\b", q))
    if not roots:
        raise FactError("a query starts from one of " + ", ".join(ROOTS))
    if "$code" in q and not _declared(q, "code"):
        if not code:
            raise FactError("$code needs a report: give `report`")
        q = q.replace('"$code"', "$code").replace("$code", json.dumps(code))  # written quoted or not
    return q


def _type(t: dict | None) -> str:
    """A GraphQL type reference as written: [ReportFight]!, Int."""
    if not t:
        return "?"
    if t["kind"] == "NON_NULL":
        return _type(t.get("ofType")) + "!"
    if t["kind"] == "LIST":
        return f"[{_type(t.get('ofType'))}]"
    return t.get("name") or "?"


async def schema_text(wcl: WCL, name: str) -> str:
    """A schema type's fields with their arguments, input fields or values, and descriptions."""
    found = (await wcl.query(SCHEMA, DAY, name=name)).get("__type")
    if not found:
        raise FactError(f"Warcraft Logs' API has no type {name!r} (types are capitalised: Report, ReportFight, "
                        "Encounter, Character, Guild, Query; a field's type names the next one)")
    about = f": {found['description']}" if found.get("description") else ""
    lines = [f"{found['name']} ({found['kind'].lower()}){about}"]
    for f in found.get("fields") or []:
        args = ", ".join(f"{a['name']}: {_type(a['type'])}" for a in f.get("args") or [])
        lines.append(f"- {f['name']}{f'({args})' if args else ''}: {_type(f['type'])}"
                     + (f" — {' '.join(f['description'].split())}" if f.get("description") else ""))
    for f in found.get("inputFields") or []:
        lines.append(f"- {f['name']}: {_type(f['type'])}"
                     + (f" — {' '.join(f['description'].split())}" if f.get("description") else ""))
    for v in found.get("enumValues") or []:
        lines.append(f"- {v['name']}" + (f" — {' '.join(v['description'].split())}" if v.get("description") else ""))
    return "\n".join(lines)


async def query_text(wcl: WCL, query: str, rep: Report | None = None, part: int = 1, boss: str | None = None,
                     reports: Reports | None = None) -> str:
    """The query's answer in the tools' own terms (`_Answer`), in parts, with what it cost and the hour's points
    left. `rep` is the tool's report, which `$code` and the pulls in the query stand for (`_pulls`, on `boss`);
    `reports` reads another report the query names, for its names."""
    q = _checked(_pulls(query, rep, boss), rep.code if rep else None)
    kept = None if cache().frozen else cache().get(cache_key("wcl", q, {}))
    if kept and kept[1] <= KEEP_S:  # asked moments ago (its next part, or again): no points, so no checks of them
        return await _parts(kept[0], f"Warcraft Logs' answer (kept from {kept[1] / 60:.0f} min ago: no points)",
                            part, q, rep, reports)
    # The hour's points come with every query's answer (WCL.points asks only when none came in the last minute), and
    # the query's cost with its own: asked for apart, before and after, they were two more requests, and the cost
    # the difference of the account's points, other calls' spending in it.
    before = await wcl.points()
    if before and before["limitPerHour"] - RESERVE_POINTS - before["pointsSpentThisHour"] <= 0:
        raise FactError(f"this hour's Warcraft Logs points for such queries are spent "
                        f"({before['pointsSpentThisHour']:.0f} of {before['limitPerHour']}, {RESERVE_POINTS} kept for "
                        f"the other tools); they come back in {before['pointsResetIn'] // 60} min")
    try:
        with counting_points() as asked:
            data = await wcl.query(q, KEEP_S)
    except WCLError as e:
        raise FactError(f"Warcraft Logs refused the query: {e}") from None
    cost, after = asked.record().get("total", 0), await wcl.points()
    hour = (f"; {after['pointsSpentThisHour']:.0f} of {after['limitPerHour']} spent this hour, {RESERVE_POINTS} of "
            "them kept for the other tools") if after else ""
    return await _parts(data, f"Warcraft Logs' answer ({cost:.0f} points{hour})", part, q, rep, reports)


async def _parts(data: dict, head: str, part: int, query: str, rep: Report | None, reports: Reports | None) -> str:
    """An answer in the tools' own terms, part `part` of it, under `head`. One longer than MAX_PARTS parts is cut to
    one, and says what was left out."""
    answer = await _Answer.read(data, query, rep, reports)
    text = answer.text()
    if len(text) > MAX_PARTS * ANSWER_CHARS:
        cut = (f"(Cut short: whole, the answer is {len(text):,} characters; by field, as Warcraft Logs gave it: "
               f"{_sizes(data)}. Each list above says how much of it was left out. For the rest ask for less: the "
               "pulls needed in fightIDs, a filterExpression, a time window, fewer fields, a lower limit, or one "
               "query per question.)")
        text = f"{answer.text(ANSWER_CHARS - len(cut) - 1)}\n{cut}"
    pages = _pages(text)
    part = min(max(part, 1), len(pages))
    if len(pages) > 1:
        head += (f", part {part} of {len(pages)} ({len(text):,} characters: a narrower query, fewer fields or a "
                 "shorter time range, reads better than every part)")
    tail = f"\n(Part {part} of {len(pages)}: the next is part {part + 1}.)" if part < len(pages) else ""
    empty = EMPTY_FILTERED if "filterExpression" in query and _has_empty(data) else ""
    return f"{head}:\n{pages[part - 1]}{tail}{empty}"


def _pages(text: str) -> list[str]:
    """A text in parts of at most ANSWER_CHARS, each ending with a line where the lines are short enough."""
    pages, page = [], ""
    for line in text.split("\n"):
        while len(line) > ANSWER_CHARS:  # JSON is one line: cut where a part is full
            room = ANSWER_CHARS - len(page) - bool(page)
            if room <= 0:
                pages.append(page)
                page = ""
                continue
            page, line = (f"{page}\n" if page else "") + line[:room], line[room:]
        if page and len(page) + 1 + len(line) > ANSWER_CHARS:
            pages.append(page)
            page = line
        else:
            page = f"{page}\n{line}" if page else line
    return [*pages, page] if page or not pages else pages


def _sizes(data: dict) -> str:
    """Where an answer's characters are: each field under the single-field wrappers (reportData, report), biggest
    first."""
    while isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))
    sizes = sorted(((k, len(json.dumps(v, ensure_ascii=False, separators=(",", ":")))) for k, v in data.items()),
                   key=lambda kv: -kv[1])
    return ", ".join(f"{k} {n:,}" for k, n in sizes)


def _has_empty(data) -> bool:
    """Whether an answer has an empty list of events or table rows (`data`, `auras`, `entries`)."""
    if isinstance(data, dict):
        return any((k in ("data", "auras", "entries") and v == []) or _has_empty(v) for k, v in data.items())
    if isinstance(data, list):
        return any(_has_empty(v) for v in data)
    return False


@dataclass
class _Asked:
    """What a query's text says of the reports it reads: where in the answer each `report(code: "...")` is (the
    keys down to it), with its code; the fights each names in a `fightIDs`; and where its `fights` fields are."""
    reports: dict[tuple[str, ...], str] = field(default_factory=dict)
    fights: dict[tuple[str, ...], set[int]] = field(default_factory=dict)  # by the report's place
    fight_lists: set[tuple[str, ...]] = field(default_factory=set)  # where a `fights` field's answer is


def _closing(tokens: list[str], i: int) -> int:
    """Where the bracket opened at tokens[i] closes (a string is one token, so brackets inside one don't count)."""
    depth = 0
    for j in range(i, len(tokens)):
        depth += (tokens[j] == "(") - (tokens[j] == ")")
        if not depth:
            return j
    return len(tokens) - 1


def _asked(query: str) -> _Asked | None:
    """A query's reports and where they are in its answer, read off its text; None for a query whose text doesn't
    say (it spreads a fragment, or its brackets don't match): its answer is then shown as Warcraft Logs gave it,
    unless it names one report alone."""
    tokens: list[str] = TOKEN.findall(query)
    out = _Asked()
    open_sets: list[str | None] = []  # the key of each selection set that is open; None for the operation's own
    key: str | None = None  # the field read last, whose selection set a "{" opens
    report: tuple[str, ...] | None = None  # the report the fields being read are under
    i = 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if t == "...":
            return None
        if t == "{":
            open_sets.append(key)
            key = None
        elif t == "}":
            if not open_sets:
                return None
            open_sets.pop()
            key = None
            here = tuple(k for k in open_sets if k is not None)
            if report is not None and here[:len(report)] != report:
                report = None
        elif t == "(":  # the operation's own variables
            i = _closing(tokens, i - 1) + 1
        elif t == "@":  # a directive: its name, and its arguments with the "(" above
            i += 1
        elif open_sets and re.fullmatch(r"[A-Za-z_]\w*", t):
            name = t
            if i + 1 < len(tokens) and tokens[i] == ":":  # an alias
                name, i = tokens[i + 1], i + 2
            args: list[str] = []
            if i < len(tokens) and tokens[i] == "(":
                end = _closing(tokens, i)
                args, i = tokens[i + 1:end], end + 1
            key = t
            here = (*(k for k in open_sets if k is not None), t)
            if name == "report" and (code := _argument(args, "code")):
                out.reports[here], report = code, here
                out.fights[here] = set()
            elif report is not None and here[:len(report)] == report:
                if name == "fights" and len(here) == len(report) + 1:
                    out.fight_lists.add(here)
                out.fights[report] |= _fight_ids(args)
    return None if open_sets else out


def _argument(args: list[str], name: str) -> str | None:
    """A field's string argument, from its tokens; None when it has none or gives a variable."""
    for j in range(len(args) - 2):
        if args[j] == name and args[j + 1] == ":" and args[j + 2].startswith('"'):
            try:
                return json.loads(args[j + 2])
            except ValueError:
                return None
    return None


def _fight_ids(args: list[str]) -> set[int]:
    """The fights a field's `fightIDs` argument names, from its tokens."""
    for j in range(len(args) - 2):
        if args[j] == "fightIDs" and args[j + 1] == ":":
            ids = set()
            for t in args[j + 2:]:
                if t.isdigit():
                    ids.add(int(t))
                if t == "]" or (t.isdigit() and args[j + 2] != "["):
                    break
            return ids
    return set()


def _is_events(node: object) -> bool:
    """Whether a list is one of logged events: each has its time and kind, and most a source or a target (a
    Deaths table's rows have a time and a `type` too, the player's class, but no source)."""
    if not isinstance(node, list) or not node:
        return False
    if not all(isinstance(e, dict) and isinstance(e.get("timestamp"), (int, float)) and isinstance(e.get("type"), str)
               for e in node):
        return False
    return 2 * sum(isinstance(e.get("sourceID", e.get("targetID")), int) for e in node) >= len(node)


def _is_ms(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _has_times(node: object) -> bool:
    """Whether an answer's part holds anything a report's names or pulls would translate: events, or a time."""
    if isinstance(node, dict):
        return any((k in TIME_KEYS and _is_ms(v)) or _has_times(v) for k, v in node.items())
    return isinstance(node, list) and any(_has_times(v) for v in node)


def _phases(rep: Report, f: dict, transitions: object) -> object:
    """A fight's phase transitions as shown: each start m:ss.mmm into the pull, and the phase named as the other
    tools name it ('P3 Stage Three', where Warcraft Logs' id for it is 4 behind an intermission: Report.phase)."""
    if not isinstance(transitions, list):
        return transitions
    shown = []
    for t in transitions:
        if isinstance(t, dict):
            t = dict(t)
            if _is_ms(t.get("startTime")):
                t["startTime"] = exact(t["startTime"] - f["startTime"])
            if isinstance(t.get("id"), int):
                name = rep.phase_names.get((f["encounterID"], t["id"]))
                t["phase"] = rep.phase(f, t["id"]) + (f" {name}" if name else "")
        shown.append(t)
    return shown


def _fight_of(rep: Report, fight: dict) -> dict | None:
    """The pull an item of a `fights` field is: by its id, else by its start, else by its first phase's start
    (`fights(fightIDs: [21]) { phaseTransitions { id startTime } }` has neither); None when nothing places it."""
    if isinstance(fight.get("id"), int):
        return rows.pull_at(rep, 0, fight["id"])
    phases = fight.get("phaseTransitions")
    starts = [fight.get("startTime"), *(t.get("startTime") for t in phases or [] if isinstance(t, dict))]
    return next((f for at in starts if isinstance(at, (int, float)) and (f := rows.pull_at(rep, at))), None)


@dataclass
class _Events:
    """One list of events in an answer: the keys down to it, the report it is of, and the events."""
    path: tuple[str, ...]
    rep: Report
    events: list[dict]


def _fight_line(rep: Report, fight_id: int, detailed: bool = True) -> str:
    """'21 = Sszorak pull 13 (2:42 long, ended with the boss at 62.59% health)', a legend's entry for a fight;
    '21 = Sszorak pull 13' alone when not `detailed`."""
    f = rows.pull_at(rep, 0, fight_id)
    if f is None:
        return f"{fight_id} = not a boss pull (trash, or a fight the report doesn't have)"
    if not detailed:
        return f"{fight_id} = {rep.label(f)}"
    if f["id"] in rep.live:
        return f"{fight_id} = {rep.label(f)} (still going)"
    long = f"{clock(f['endTime'] - f['startTime'])} long"
    return f"{fight_id} = {rep.label(f)} ({'a kill, ' + long if f['kill'] else f'{long}, ended with {boss_left(f)}'})"


@dataclass
class _Shown:
    """An answer as it is shown, for the reports that were read (`reps`, by code): each list of events swapped for
    a pointer to its rows (`events`), a report's other times read into their pull, and the fights each report's
    part of the query names or holds collected for the legend (`fights`, by code)."""
    asked: _Asked
    reps: dict[str, Report]
    events: list[_Events] = field(default_factory=list)
    fights: dict[str, set[int]] = field(default_factory=dict)

    def of(self, node: object, path: tuple[str, ...] = (), code: str | None = None, of_pull: bool = False) -> object:
        """`node`, at `path` in the answer, as shown. `code` is its report when it is one's and that report was
        read; `of_pull` is whether its times name their pull (its report's part of the query isn't about one)."""
        if path in self.asked.reports:
            named = self.asked.fights.get(path) or set()
            code = self.asked.reports[path] if self.asked.reports[path] in self.reps else None
            of_pull = len(named) != 1
            if code is not None:
                self.fights.setdefault(code, set()).update(named)
        if code is not None:
            return self._of_report(node, path, code, of_pull)
        if not any(p[:len(path)] == path for p in self.asked.reports):  # no report under it: as Warcraft Logs gave it
            return node
        if isinstance(node, list):
            return [self.of(v, path) for v in node]
        return {k: self.of(v, (*path, k)) for k, v in node.items()} if isinstance(node, dict) else node

    def _of_report(self, node: object, path: tuple[str, ...], code: str, of_pull: bool) -> object:
        rep, fights = self.reps[code], self.fights.setdefault(code, set())
        if isinstance(node, list):
            if not _is_events(node):
                return [self.of(v, path, code, of_pull) for v in node]
            self.events.append(_Events(path, rep, node))
            fights.update(e["fight"] for e in node if isinstance(e.get("fight"), int))
            return f"events {len(self.events)} below"
        if not isinstance(node, dict):
            return node
        if path in self.asked.fight_lists:  # a fight of a `fights` field: its own start and end, in the report's ms
            f = _fight_of(rep, node)
            if isinstance(node.get("id"), int):
                fights.add(node["id"])
            elif f:
                fights.add(f["id"])
            return {k: v if k in TIME_KEYS else _phases(rep, f, v) if k == "phaseTransitions" and f
                    else self.of(v, (*path, k), code, of_pull) for k, v in node.items()}
        shown = {}
        for k, v in node.items():
            if k in TIME_KEYS and _is_ms(v) and (
                    f := rows.pull_at(rep, v, node.get("fight") if isinstance(node.get("fight"), int) else None)):
                fights.add(f["id"])
                shown[k] = exact(v - f["startTime"]) + (f" of {rep.label(f)}" if of_pull else "")
            else:
                shown[k] = self.of(v, (*path, k), code, of_pull)
        return shown


async def _read(asked: _Asked, data: dict, own: Report | None, reports: Reports | None
                ) -> tuple[dict[str, Report], list[str]]:
    """The reports a query names, by code, each read once, and a note for each that wasn't: the tool's own is in
    hand; another is read only when its part of the answer has events or times (a table's rows carry their names,
    so there is nothing to name), NAMED_REPORTS of them at most, all at once."""
    reps: dict[str, Report] = {}
    wanted: list[str] = []
    for path, code in asked.reports.items():
        part = _at(data, path)
        if part is None or code in reps or code in wanted:
            continue
        if own is not None and code == own.code:
            reps[code] = own
        elif _has_times(part):
            wanted.append(code)
    reading = wanted[:NAMED_REPORTS] if reports is not None else []
    notes = []
    read = await asyncio.gather(*(reports(code) for code in reading), return_exceptions=True) if reports else []
    for code, rep in zip(reading, read, strict=True):
        if isinstance(rep, Report):
            reps[code] = rep
        elif isinstance(rep, (FactError, WCLError)):
            notes.append(f"Report {code} couldn't be read for its names ({str(rep).rstrip('.')}): its ids and times "
                         "are as logged.")
        else:
            raise rep
    notes += [f"Report {code} wasn't read for its names: its ids and times are as logged."
              for code in wanted[len(reading):]]
    return reps, notes


@dataclass
class _Answer:
    """A Warcraft Logs answer in the tools' own terms. `shown` is the answer with each list of events swapped for
    a pointer to its rows (`events`), and a report's other times (TIME_KEYS) as m:ss into their pull, of which pull
    where the report's part of the query isn't about one alone; a `fights` field keeps each fight's own start and
    end in the report's ms, which the legend's length says in the pull's, and its phase transitions read into the
    pull, each phase named (`_phases`). `legend` names the fights each report's part names or holds (up to
    LEGEND_DETAILED of them with their length and ending); `notes` say which reports weren't read for their names."""
    shown: object
    events: list[_Events] = field(default_factory=list)
    legend: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    async def read(cls, data: dict, query: str, own: Report | None, reports: Reports | None) -> _Answer:
        asked = _asked(query)
        codes = set(re.findall(r'\breport\s*\(\s*code\s*:\s*"(\w+)"', query))
        if asked is None or not asked.reports:  # its text doesn't say where its reports are: one alone is all of it
            asked = _Asked({(): next(iter(codes))}, {(): set()}) if len(codes) == 1 else _Asked()
        if not asked.reports:
            return cls(data)
        reps, notes = await _read(asked, data, own, reports)
        shown = _Shown(asked, reps)
        out = cls(shown.of(data), shown.events, notes=notes)
        for code, ids in shown.fights.items():
            if listed := sorted(ids):
                more = f"; and {len(listed) - LEGEND_FIGHTS} more" if len(listed) > LEGEND_FIGHTS else ""
                detailed = len(listed) <= LEGEND_DETAILED
                out.legend.append(("Fights" if len(codes) <= 1 else f"Fights of report {code}") + ": " + "; ".join(
                    _fight_line(reps[code], i, detailed) for i in listed[:LEGEND_FIGHTS]) + more + ".")
        return out

    def text(self, room: int | None = None) -> str:
        """The whole answer: the legend and the notes, which say how the rest reads and so come first (in a long
        answer, in its first part), then the JSON and each events list's rows. With `room`, cut to that many
        characters: the JSON's lists to their first items (`_cut`), and each events list to the rows that fit its
        share of what's left, both saying how much was left out."""
        first = [*self.legend, *self.notes]
        if room is None:
            return "\n".join([*first, _json(self.shown), *(line for n, ev in enumerate(self.events, 1)
                                                           for line in _rows(n, ev))])
        room -= sum(len(line) + 1 for line in first)
        shown = _cut(self.shown, room if not self.events else room // 3)
        each = (room - len(shown)) // max(len(self.events), 1)
        return "\n".join([*first, shown, *(line for n, ev in enumerate(self.events, 1)
                                           for line in _rows(n, ev, each))])


def _at(data: object, path: tuple[str, ...]) -> object:
    """What an answer holds at a place (the keys down to it), through lists; None when it holds nothing there."""
    for k in path:
        while isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or data.get(k) is None:
            return None
        data = data[k]
    return data


def _json(node: object) -> str:
    return json.dumps(node, ensure_ascii=False, separators=(",", ":"))


def _rows(n: int, ev: _Events, room: int | None = None) -> list[str]:
    """An events list as lines: what it is and how its times read, then its rows (`rows.listed`: counted when
    long or cut, under their pulls, with the ids of the abilities named). With `room`, only the first rows that
    fit it."""
    listed = rows.listed(ev.rep, ev.events, room=room)
    return [f"Events {n} ({'.'.join(ev.path)}): {len(ev.events)}{listed.where}.", *listed.lines]


def _cut(node: object, room: int) -> str:
    """An answer as JSON within `room` characters: whole when it fits, else its lists cut to their first items,
    each ending with how many were left out. The lists inside a list's items go first (a table row's gear and
    abilities, to INNER_KEPT items at the least), then every list to as many items as fit; one still too long ends
    where the room does, and says so."""
    text = _json(node)
    for keep in INNER_KEPT:
        if len(text) <= room:
            return text
        node = _shortened(node, 2, keep)
        text = _json(node)
    if len(text) <= room:
        return text
    longest = _longest(node)
    lo, hi = 1, longest  # the most items every list can keep: `lo` fits or is 1, `hi` doesn't fit
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if len(_json(_shortened(node, 1, mid))) <= room else (lo, mid)
    text = _json(_shortened(node, 1, lo))
    return text if len(text) <= room else text[:max(room - 40, 0)] + "… (cut here: too long)"


def _longest(node: object) -> int:
    """The length of an answer's longest list."""
    if isinstance(node, dict):
        return max((_longest(v) for v in node.values()), default=0)
    if isinstance(node, list):
        return max(len(node), max((_longest(v) for v in node), default=0))
    return 0


def _shortened(node: object, depth: int, keep: int, inside: int = 0) -> object:
    """An answer with each list that is inside `depth` lists or more cut to its first `keep` items, with a last
    item that says how many were left out (counting what an earlier cut left out)."""
    if isinstance(node, dict):
        return {k: _shortened(v, depth, keep, inside) for k, v in node.items()}
    if not isinstance(node, list):
        return node
    left = 0
    if node and isinstance(node[-1], str) and (m := re.fullmatch(r"… ([\d,]+) more left out", node[-1])):
        node, left = node[:-1], int(m[1].replace(",", ""))
    if inside + 1 >= depth and len(node) > keep:
        node, left = node[:keep], left + len(node) - keep
    return [*(_shortened(v, depth, keep, inside + 1) for v in node), *([f"… {left:,} more left out"] if left else [])]
