"""Which defensives and raid cooldowns the death recap's replay can't read
(`WIPEBOT_CACHE=off uv run python -m tests.support.defensives`).

Every spell Lorrgs tags defensive or raid_cd, for every spec it tracks, read from its Wowhead tooltip as the replay
reads it (knowledge.defensive_effect): the ones whose wording isn't a known kind make the recap say "can't tell".
Run it after a patch, or when a raid's answers say "can't tell" often, and teach spells.parse_effect the new
wordings that state an amount. Live (Lorrgs and Wowhead), so it stays out of the tests: their tooltip text can't be
published there. About a minute cold.
"""
import asyncio

from wipebot.core.knowledge import fetch, spells

TAGS = {"defensive", "raid_cd"}


async def main() -> None:
    specs = [s["full_name_slug"] for s in (await fetch.cli("lorrgs", "specs")).get("specs") or []
             if s.get("full_name_slug")]
    found: dict[int, tuple[str, set[str]]] = {}  # spell id -> (name, specs)
    for slug in specs:
        try:
            data = await fetch.cli("lorrgs", "spec-spells", slug)
        except fetch.NotFound:
            continue
        for s in data.values():
            if isinstance(s, dict) and TAGS & set(s.get("tags") or []) and s.get("spell_id"):
                found.setdefault(s["spell_id"], (s.get("name") or "?", set()))[1].add(slug)
    effects = await asyncio.gather(*(spells.defensive_effect(i) for i in found))
    unread = sorted((found[i][0], i, sorted(found[i][1])) for i, e in zip(found, effects, strict=True) if e is None)
    print(f"{len(found)} defensives and raid cooldowns over {len(specs)} specs; "
          f"{len(found) - len(unread)} read, {len(unread)} not:")
    for name, i, slugs in unread:
        print(f"  {name} [{i}] ({', '.join(slugs)}): {(await spells.tooltip(i)).text[:160]}")


if __name__ == "__main__":
    asyncio.run(main())
