"""Finding reports (`find_reports`): a guild's logs, and every log a character is in."""
from .. import clock
from ..pulls import DIFFICULTIES
from ..text import date, name_key, plural
from ..wcl import GUILD_REPORTS_S, WCL
from .base import GUILD_RUN_SHARE, FactError


async def guild_reports(wcl: WCL, days: int, since_ms: float, guild: str | None = None, realm: str | None = None,
                        region: str | None = None, player: str | None = None,
                        guilds: list[tuple[str, str, str]] | None = None,
                        current: tuple[int, str] | None = None) -> str:
    """The reports of a guild (by name, realm and region), else of each of `guilds` (name, realm, region), else of
    the logged-in user's guilds, since `since_ms`, newest first; with `player`, only those they're in. A report
    most of whose players aren't in the guild is marked: a raider's pug or another group logged to its page.
    `current` is the current report's guild (id, name): a guild named like it needs no realm or region."""
    if guild and not (realm and region) and current and name_key(guild) == name_key(current[1]):
        targets = [current]
        head = current[1]
    elif guild:
        if not (realm and region):
            mine = f"; the current report's guild, {current[1]}, needs neither" if current else ""
            raise FactError(f"give the guild's server (realm) and region too{mine}")
        found = await wcl.find_guild(guild, realm, region.lower())
        if not found:
            raise FactError(f"Warcraft Logs has no guild {guild!r} on {realm}-{region}")
        targets = [(found["id"], found["name"])]
        head = f"{found['name']} ({found['server']['name']}-{region.upper()})"
    elif guilds is not None:
        targets, heads = [], []
        for name, server, reg in guilds:
            if found := await wcl.find_guild(name, server, reg):
                targets.append((found["id"], found["name"]))
                heads.append(f"{found['name']} ({found['server']['name']}-{reg.upper()})")
        if not targets:
            raise FactError("give a guild, server and region")
        head = ", ".join(heads)
    else:
        name, mine = await wcl.my_guilds()
        targets = [(g["id"], g["name"]) for g in mine]
        if not targets:
            return f"{name} is in no guild on Warcraft Logs."
        head = f"guilds of {name}: " + ", ".join(t[1] for t in targets)
    wanted = name_key(player) if player else ""
    lines = [f"Reports in the last {plural(days, 'day')}" + (f" with {player}" if player else "") + f", {head}:"]
    for guild_id, guild_name in targets:
        members = await wcl.guild_members(guild_id)
        reports = await wcl.guild_reports(guild_id, since_ms, players=True, cache_for=GUILD_REPORTS_S)
        for rep in sorted(reports, key=lambda x: -x["startTime"]):
            names = rep["players"]
            if (wanted and not any(name_key(n) == wanted for n in names)) or rep["startTime"] > clock.now() * 1000:
                continue
            pulls = ", ".join(f"{DIFFICULTIES.get(d, d)} {n}" for d, n in rep["pulls_by_difficulty"].items()
                              if d in DIFFICULTIES)
            bosses = ", ".join(f"{b} {n}" + (f" ({'killed' if k == 1 else f'{k} kills'})" if k else "")
                               for b, (n, k) in (rep.get("bosses") or {}).items())
            line = (f"- {rep['code']}: {rep['title']} ({guild_name}), {date(rep['startTime'], with_time=True)}: "
                    + (f"boss pulls {pulls}: {bosses}" if pulls else "no boss pulls"))
            if members and names and (inside := sum(n in members for n in names)) < GUILD_RUN_SHARE * len(names):
                line += (f"; only {inside} of its {plural(len(names), 'player')} are in the guild: a pug, not a "
                         "guild raid night")
            lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else lines[0] + " none"


async def character_reports(wcl: WCL, player: str, realm: str, region: str, days: int, since_ms: float) -> str:
    """Every Warcraft Logs report a character is in since `since_ms`, newest first, whoever logged it."""
    char = await wcl.find_character(player, realm, region.lower())
    if not char:
        return f"Warcraft Logs has no character {player} on {realm}-{region.upper()}."
    server = char.get("server") or {}
    where = f"{char['name']}, {server.get('name') or realm}-{region.upper()}"
    if char.get("hidden"):
        return f"{where} has hidden their logs on Warcraft Logs."
    found = [r for r in await wcl.character_reports(char["id"], since_ms) if r["startTime"] <= clock.now() * 1000]
    lines = [f"Reports with {where} in the last {plural(days, 'day')}, newest first, whoever logged them. "
             f"Character page: https://www.warcraftlogs.com/character/{region.lower()}/{server.get('slug', '')}/"
             f"{char['name'].lower()}"]
    for rep in found:
        guild = (rep.get("guild") or {}).get("name")
        about = [f"logged to {guild}" if guild else "a personal log, no guild"]
        if rep.get("visibility") and rep["visibility"] != "public":
            about.append(rep["visibility"])
        bosses: dict[tuple[int, str], list[bool]] = {}
        other: list[str] = []
        for f in rep.get("fights") or []:
            if f.get("difficulty") in DIFFICULTIES:
                bosses.setdefault((f["difficulty"], f["name"]), []).append(bool(f.get("kill")))
            elif f["name"] not in other:
                other.append(f["name"])
        pulls = [f"{DIFFICULTIES[d]} {name} {len(k)} pull{'s' * (len(k) > 1)}, {'killed' if any(k) else 'no kill'}"
                 for (d, name), k in bosses.items()]
        if other:
            pulls.append("other content: " + ", ".join(other))
        lines.append(f"- {rep['code']}: {rep['title']} ({', '.join(about)}), {date(rep['startTime'], with_time=True)}: "
                     + ("; ".join(pulls) or "no boss pulls"))
    return "\n".join(lines) if len(lines) > 1 else lines[0] + "\nNone."
