"""A character's experience from Raider.IO."""
from . import fetch


async def character_experience(name: str, realm: str, region: str) -> dict:
    """Raider.IO's profile of a character: `character`, `guild`, `raiding.progression` (per raid),
    `mythic_plus.current_score`. The realm as Warcraft Logs spells it ('TarrenMill'): Raider.IO's client tries its
    slug spellings."""
    return await fetch.cli("raiderio", "character", "--", region.lower(), realm, name)


async def character_experience_text(name: str, realm: str, region: str) -> str:
    """player_history's experience part: raid progression per raid and the Mythic+ score this season."""
    data = await character_experience(name, realm, region)
    c = data.get("character") or {}
    guild = (data.get("guild") or {}).get("name") or "none"
    lines = [f"{c.get('name', name)} ({c.get('active_spec_name', '?')} {c.get('class_name', '?')}, "
             f"{c.get('realm_name', realm)}-{region.upper()}), guild {guild}. "
             f"Source: Raider.IO {c.get('profile_url', 'https://raider.io')}"]
    progression = (data.get("raiding") or {}).get("progression") or []
    if progression:
        lines.append("Raid progression (most recent raids last): " + "; ".join(
            f"{p['raid_slug']} {p.get('summary') or 'none'}" for p in progression))
    mplus = data.get("mythic_plus") or {}
    if mplus.get("current_score") is not None:
        lines.append(f"Mythic+ score {mplus['current_score']:.0f} ({mplus.get('season', 'this season')}).")
    return "\n".join(lines)
