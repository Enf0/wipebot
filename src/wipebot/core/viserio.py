"""Viserio Cooldowns (wowutils.com): a guild's raid nights as its officers plan them, read with the group's API key.

A group's admin makes one key for the group (its settings, "API sharing"); the host gives it to Wipebot once
(`wipebot viserio`, on the command line) and it's kept per guild in the notebook, used only for the guild the group
names. What's read: the calendar's nights coming up with their sign-ups, and this week's setups, which say per boss
who's in and who's out. The API's terms (wowutils.com/api-terms) allow a bot serving the key's own group, and ask
for no copy kept past 30 days, conditional requests and Retry-After respected, and no battletags shown: answers are
kept in memory only, for FRESH_S, then asked again with their ETag (a 304 costs nothing of the group's hourly
points). Facts verified live are in docs/reference/sources.md.
"""
import asyncio
import datetime
import time
from dataclasses import dataclass

import httpx

from .notebook import Notebook
from .report import FactError

API = "https://api.wowutils.com/v1"
FRESH_S = 120  # an answer asked this soon after the last reuses it (a group has 60 points an hour)
ROSTER_FRESH_S = 3600  # the roster, which rarely changes, is reused this long (it's read for every answer)
TIMEOUT_S = 10
DAYS_AHEAD = 8  # the nights coming up that `upcoming` gives
ROLES = ("tank", "healer", "melee", "ranged")
MYTHIC_SIZE = 20  # a Mythic raid's players


class ViserioError(Exception):
    """Viserio didn't give what was asked; the message says why, for an officer."""


@dataclass
class Group:
    id: str
    name: str
    guild: str  # the guild it's for, as Viserio has it: "the dragonfell tribe"
    realm: str  # "kazzak"
    region: str  # "eu"


# (key, path) -> (its ETag, the answer, when fetched): in memory only, as the API's terms ask
_answers: dict[tuple[str, str], tuple[str, dict, float]] = {}


async def _get(key: str, path: str, fresh_s: float | None = None) -> dict:
    """One of the API's answers, reused for `fresh_s` (FRESH_S) and then asked again with its ETag."""
    etag, body, at = _answers.get((key, path), ("", {}, 0.0))
    if body and time.time() - at < (FRESH_S if fresh_s is None else fresh_s):
        return body
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "Wipebot"} | ({"If-None-Match": etag} if etag else {})
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.get(API + path, headers=headers)
    except httpx.HTTPError as e:
        raise ViserioError(f"Viserio can't be reached ({type(e).__name__})") from e
    if r.status_code == 304 and body:
        _answers[(key, path)] = (etag, body, time.time())
        return body
    if r.status_code in (401, 403):
        raise ViserioError("Viserio refused the key: a group admin turned API sharing off or made a new key, so the "
                           "bot's host gives Wipebot the new one (`wipebot viserio`)")
    if r.status_code in (429, 503):
        raise ViserioError(f"Viserio is busy; try again in {r.headers.get('Retry-After', 'a few')} seconds")
    if r.status_code != 200:
        raise ViserioError(f"Viserio answered {r.status_code}")
    body = r.json()
    _answers[(key, path)] = (r.headers.get("ETag", ""), body, time.time())
    return body


async def group(key: str) -> Group:
    """The group the key is for."""
    found = ((await _get(key, "/groups", ROSTER_FRESH_S)).get("data") or [None])[0]
    if not found:
        raise ViserioError("the key opens no group")
    guild = found.get("guild") or {}
    return Group(found["groupId"], found.get("name") or "", (guild.get("name") or "").lower(),
                 (guild.get("realm") or "").lower(), (guild.get("region") or "").lower())


def is_guild(g: Group, name: str, realm: str, region: str) -> bool:
    """Whether the group is this guild's (Warcraft Logs' name, realm and region)."""
    def norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())
    return (norm(g.guild), norm(g.realm), norm(g.region)) == (norm(name), norm(realm), norm(region))


@dataclass
class Night:
    """A raid night on the group's calendar, with the setups planned for it."""
    event: dict  # the calendar event, with its signups
    setups: list[dict]  # its setups in full, with bossAssignments


async def upcoming(key: str, days: int = DAYS_AHEAD) -> tuple[Group, list[Night]]:
    """The group's raid nights in the next `days`, each with the week's setups planned for it."""
    g = await group(key)
    base = f"/groups/{g.id}"
    events, reset = await asyncio.gather(_get(key, f"{base}/calendar-events?upcoming=true&limit=20"),
                                         _get(key, f"{base}/latest-reset"))
    setups = await asyncio.gather(*(_get(key, f"{base}/latest-reset/setups/{s['setupId']}/full")
                                    for s in reset.get("setups") or []))
    last = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    nights = []
    for e in events.get("data") or []:
        if e.get("status") == "cancelled" or (e.get("date") or "") > last:
            continue
        nights.append(Night(e, [s for s in setups if (s.get("eventRef") or {}).get("eventId") == e.get("eventId")]))
    return g, nights


async def roster_specs(key: str) -> dict[str, tuple[str, str]]:
    """Every character on the group's roster, alts and inactive ones too, and each player's roster name for their
    main: name -> (class, spec) in Warcraft Logs' form ("DeathKnight", "BeastMastery"). A character's own name wins
    over a roster name that's the same."""
    g = await group(key)
    found: dict[str, tuple[str, str]] = {}
    mains: dict[str, tuple[str, str]] = {}
    for m in (await _get(key, f"/groups/{g.id}/roster", ROSTER_FRESH_S)).get("members") or []:
        for c in m.get("characters") or []:
            if c.get("name") and c.get("class"):
                spec = ((c.get("class") or "").replace(" ", ""), (c.get("spec") or "").replace(" ", ""))
                found[c["name"]] = spec
                if c.get("playerId") == m.get("mainCharacter") and m.get("displayName"):
                    mains[m["displayName"]] = spec
    return mains | found


def _who(entry: dict) -> str:
    """A player as the logs name them, with their spec and the name the roster knows them by when it differs:
    "Wutroll (Brewmaster Monk, Edible)"."""
    name, shown = entry.get("characterName") or entry.get("displayName") or "?", entry.get("displayName") or ""
    about = [" ".join(s for s in (entry.get("playerSpec"), entry.get("playerClass")) if s),
             shown if shown.lower() != name.lower() else ""]
    return name + (f" ({', '.join(a for a in about if a)})" if any(about) else "")


def _by_role(entries: list[dict]) -> str:
    groups: dict[str, list[str]] = {}
    for e in entries:
        groups.setdefault(str(e.get("role")) if e.get("role") in ROLES else "other", []).append(_who(e))
    return "; ".join(f"{role} {len(names)}: " + ", ".join(names)
                     for role, names in sorted(groups.items(), key=lambda kv: (ROLES + ("other",)).index(kv[0])))


def _signups(event: dict) -> str:
    counts: dict[str, list[str]] = {}
    unanswered = 0
    for s in event.get("signups") or []:
        counts.setdefault(s.get("status") or "?", []).append((s.get("mainCharacter") or {}).get("name")
                                                              or s.get("displayName") or "?")
        unanswered += not s.get("responded")
    parts = [f"{len(counts.pop('present', []))} present"]
    parts += [f"{status} {len(names)}: {', '.join(names)}" for status, names in sorted(counts.items())]
    return "; ".join(parts) + (f" ({unanswered} of them haven't answered: the status is their default)"
                               if unanswered else "")


def nights_text(g: Group, nights: list[Night], boss: str | None = None) -> str:
    """The nights coming up as `roster` gives them: when, sign-ups, and per boss planned who's in and out."""
    if not nights:
        return f"Viserio has no raid night planned for {g.name} in the next {DAYS_AHEAD} days."
    lines = [f"The raid nights coming up for {g.name}, as its officers plan them in Viserio Cooldowns (sign-ups as "
             "of now; a setup can still change). Names are the characters' as in the logs, with the player's name "
             "on the roster after the spec when it differs."]
    for n in nights:
        e = n.event
        d = datetime.date.fromisoformat(e["date"])
        when = f"{d:%a} {d.day} {d:%b}"
        lines.append(f"\n{when} {e.get('startTime', '')}-{e.get('endTime', '')} (as Viserio's calendar has it), "
                     f"\"{e.get('name')}\", {e.get('difficulty') or ''}{' (optional)' if e.get('optional') else ''}")
        lines.append(f"  Sign-ups: {_signups(e)}")
        if not n.setups:
            lines.append("  No setup planned for it yet.")
        for s in n.setups:
            bosses = [b for b in s.get("bossAssignments") or [] if b.get("enabled")]
            if boss:
                bosses = [b for b in bosses if boss.lower() in (b.get("bossName") or "").lower()]
            planned = ", ".join(b["bossName"] for b in bosses) or (f"not {boss}" if boss else "none")
            lines.append(f"  Setup \"{s.get('name')}\" ({len(s.get('inSetupEntries') or [])} players), bosses "
                         f"planned: {planned}")
            if bench := s.get("benchEntries"):
                lines.append(f"    Bench: {', '.join(_who(b) for b in bench)}")
            for b in bosses:
                ins = b.get("inEntries") or []
                short = (f" ({MYTHIC_SIZE - len(ins)} short of Mythic's {MYTHIC_SIZE}: not planned in full yet)"
                         if s.get("difficulty") == "Mythic" and len(ins) < MYTHIC_SIZE else "")
                lines.append(f"    {b['bossName']}: in {len(ins)}{short}: {_by_role(ins)}")
                if out := b.get("outEntries"):
                    lines.append(f"      Out: {', '.join(_who(o) for o in out)}")
    return "\n".join(lines)


def no_key(guild_name: str) -> FactError:
    return FactError(f"{guild_name}'s raid nights aren't read from Viserio Cooldowns: an officer connects the "
                     "group's API key to Wipebot first.")


async def connect(book: Notebook, guild_id: int, key: str, guild: tuple[str, str, str], by: str) -> Group:
    """The group's key saved for the guild (Warcraft Logs' id, and its name, realm and region), once Viserio says
    the key's group is that guild's; ViserioError when it isn't."""
    g = await group(key.strip())
    if not is_guild(g, *guild):
        raise ViserioError(f"the key is for {g.name} ({g.guild}, {g.realm}-{g.region.upper()}), not "
                           f"{guild[0]} ({guild[1]}-{guild[2].upper()})")
    book.save_viserio_key(guild_id, key.strip(), g.name, by)
    return g
