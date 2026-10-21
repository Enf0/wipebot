"""Any Warcraft Logs query the model writes (`wcl_query`), for what no other tool shows, and the API's schema to
write it from. Read-only, within the hour's points less a share kept for the other tools; an answer is kept for a
while, so reading its later parts or asking it again costs nothing."""
import json
import re

from ..cache import DAY, cache, cache_key
from ..wcl import WCL, WCLError
from .base import FactError

RESERVE_POINTS = 600  # of the hour's points, kept for the other tools: a query is refused past the rest
ANSWER_CHARS = 12_000  # an answer is shown in parts this long
KEEP_S = 10 * 60  # an answer is kept this long, so its parts and a repeat cost no points
ROOTS = ("reportData", "worldData", "characterData", "guildData", "progressRaceData", "rateLimitData")
RATE = "rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }"
TYPE_REF = "name kind ofType { name kind ofType { name kind ofType { name } } }"
SCHEMA = (f"query($name: String!) {{ __type(name: $name) {{ name kind description "
          f"fields {{ name description args {{ name description type {{ {TYPE_REF} }} }} type {{ {TYPE_REF} }} }} "
          f"inputFields {{ name description type {{ {TYPE_REF} }} }} enumValues {{ name description }} }} }}")


def _checked(query: str, code: str | None) -> str:
    """The query as it will run: read-only roots only, `$code` filled in."""
    q = query.strip()
    if re.search(r"\b(mutation|subscription)\b", q) or "userData" in q or "__schema" in q:
        raise FactError("only reading queries of " + ", ".join(ROOTS) + " (the schema: schema_type)")
    roots = set(re.findall(r"\b(" + "|".join(ROOTS) + r")\b", q))
    if not roots:
        raise FactError("a query starts from one of " + ", ".join(ROOTS))
    if "$code" in q and not re.search(r"\$code\s*:", q):
        if not code:
            raise FactError("$code needs a report: give `report`")
        q = q.replace('"$code"', "$code").replace("$code", json.dumps(code))  # written quoted or not
    return q


async def _rate(wcl: WCL) -> dict:
    return (await wcl.query(f"query {{ {RATE} }}"))["rateLimitData"]


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


async def query_text(wcl: WCL, query: str, code: str | None, part: int = 1) -> str:
    """The query's answer as compact JSON, in parts, with what it cost and the hour's points left."""
    q = _checked(query, code)
    kept = None if cache().frozen else cache().get(cache_key("wcl", q, {}))
    if kept and kept[1] <= KEEP_S:  # asked moments ago (its next part, or again): no points, so no checks of them
        return _parts(kept[0], f"Warcraft Logs' answer (kept from {kept[1] / 60:.0f} min ago: no points)", part)
    before = await _rate(wcl)
    left = before["limitPerHour"] - RESERVE_POINTS - before["pointsSpentThisHour"]
    if left <= 0:
        raise FactError(f"this hour's Warcraft Logs points for such queries are spent "
                        f"({before['pointsSpentThisHour']:.0f} of {before['limitPerHour']}, {RESERVE_POINTS} kept for "
                        f"the other tools); they come back in {before['pointsResetIn'] // 60} min")
    try:
        data = await wcl.query(q, KEEP_S)
    except WCLError as e:
        raise FactError(f"Warcraft Logs refused the query: {e}") from None
    after = await _rate(wcl)
    cost = max(0.0, after["pointsSpentThisHour"] - before["pointsSpentThisHour"])
    return _parts(data, f"Warcraft Logs' answer ({cost:.0f} points; {after['pointsSpentThisHour']:.0f} of "
                        f"{after['limitPerHour']} spent this hour, {RESERVE_POINTS} of them kept for the other tools)",
                  part)


def _parts(data: dict, head: str, part: int) -> str:
    """An answer as compact JSON, part `part` of it, under `head`."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    n = max(1, -(-len(text) // ANSWER_CHARS))
    part = min(max(part, 1), n)
    if n > 1:
        head += (f", part {part} of {n} ({len(text):,} characters: a narrower query, fewer fields or a shorter time "
                 "range, reads better than every part)")
    tail = f"\n(Part {part} of {n}: the next is part {part + 1}.)" if part < n else ""
    return f"{head}:\n{text[(part - 1) * ANSWER_CHARS:part * ANSWER_CHARS]}{tail}"
