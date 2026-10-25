"""Stand-ins for what isn't ours in the recordings: the guide sites' writing, Warcraft Logs' images and the names of
players outside the guild in its listings.

A whole guide page can't go in a public repo (the sites' terms); Warcraft Logs' rankings and other guilds' reports
don't either, and stay local instead (recorded.py), and a guild's rank among guilds is left out. Recording makes
the stand-ins; numbers and words come from a hash, the same each time, and a stand-in made again from itself is
itself (tests/core/test_recorded.py checks the recordings by that)."""
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any


def number(*seed: object) -> float:
    """A number in [0, 1) from a hash of `seed`: made up, and the same every time."""
    return int.from_bytes(hashlib.sha256(json.dumps(seed, default=str).encode()).digest()[:8]) / 2**64



# Guide sites (Method, Icy Veins, Wowhead): their writing isn't ours. Long text gets made-up words (markup, links and
# dates stay, so the pages still parse); a build becomes one of our own raiders' talent strings; a tooltip keeps only
# its facts (cooldown, what a defensive does, an item's stats) in plain words the parsers read; comments go.
_SYLLABLES = ["ka", "ro", "mi", "tel", "dor", "an", "ve", "lis", "gar", "um", "sha", "nor", "bri", "eth", "qu", "zo"]
_LONG = 40  # characters: a string longer than this (not a link) is writing; shorter is a title, a name or a label
_TITLES = {"title", "name", "headline", "page_title", "section_title", "heading", "command", "next_command",
           "fallback_search_command", "fetch_more_command"}  # kept whatever their length: pages are found by them
_PEOPLE = {"author", "authors", "username", "fullName", "bio", "updated_by"}  # the guides' writers
_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                   r" \d{1,2},? \d{4}\b|\b\d{4}-\d{2}-\d{2}\b")
_MARKUP = re.compile(r"(<[^>]*>|\[[^\]]*\]|https?://\S+)")


def _word(*seed: object) -> str:
    n = number("word", *seed)
    return "".join(_SYLLABLES[int(number("syllable", *seed, i) * len(_SYLLABLES))] for i in range(1 + int(n * 3)))


def made_up_text(text: str, *seed: object) -> str:
    """`text` with its words made up: markup (HTML tags, [spell=123]-style links), links and dates stay."""
    out = []
    for i, part in enumerate(_MARKUP.split(text)):
        if _MARKUP.fullmatch(part):
            out.append(part)
            continue
        kept = _DATE.split(part)
        dates = _DATE.findall(part)
        piece = []
        for j, chunk in enumerate(kept):
            words = len(chunk.split())
            filler = " ".join(_word(*seed, i, j, w) for w in range(words))
            lead, trail = chunk[:len(chunk) - len(chunk.lstrip())], chunk[len(chunk.rstrip()):]
            # beside a date, a space: made-up words glued to it ("2026: The" as "2026Telro") hide it the next time
            if filler:
                lead, trail = lead or " " * (j > 0), trail or " " * (j < len(dates))
            piece.append(lead + (filler[:1].upper() + filler[1:] if filler else "") + (trail if filler else ""))
            if j < len(dates):
                piece.append(dates[j])
        out.append("".join(piece))
    return "".join(out)


def _tooltip(name: str, text: str, item: bool) -> str:
    """A tooltip standing in for Wowhead's: the spell's or item's facts, in words the parsers read."""
    from wipebot.core.knowledge.spells import _COOLDOWN, parse_effect
    facts = [name]
    if item:
        facts += re.findall(r"\b(?:Cloth|Leather|Mail|Plate|Trinket|Finger|Neck|Back|Head|Shoulder|Chest|Wrist|Hands|"
                            r"Waist|Legs|Feet|One-Hand|Two-Hand|Main Hand|Off Hand|Held In Off-hand|Shield|Item Level"
                            r" \d+)\b", text)
        facts += re.findall(r"\+\d+ (?:\[[^\]]+\]|Agility|Strength|Intellect|Stamina|Critical Strike|Haste|Mastery|"
                            r"Versatility)", text)
    elif m := _COOLDOWN.search(text):
        facts.append(f"{m[1]} {m[2]} cooldown")
    effect = None if item else parse_effect(text)
    if effect:
        what = {"immunity": "Immune to all damage" if not effect.only else "Magic immunity",
                "heal": f"Heals you for {effect.pct:g}% of your maximum health",
                "reduction": f"Reduces {'magic ' if effect.only == 'magic' else ''}damage taken"
                             f"{' from area-of-effect attacks' if effect.only == 'aoe' else ''} by {effect.pct:g}%"}
        facts.append(what[effect.kind] + (f" for {effect.duration_s:g} sec" if effect.duration_s else "")
                     + (f" to allies within {effect.radius_yd:g} yards" if effect.radius_yd else ""))
    return ". ".join(facts) + "."


def guide(key: str, value: Any, builds: dict[int, list[str]], spec_of: Callable[[str], int | None]) -> Any:
    """A guide site's answer with its writing made up (see above). `builds`: the test guild's raiders' talent
    strings by spec id; `spec_of`: a talent string's spec id."""
    parts = json.loads(key)
    if parts[1] == "wowhead" and parts[2] == "entity":
        entity, tip = value.get("entity") or {}, value.get("tooltip") or {}
        from wipebot.core.knowledge.spells import tooltip_text
        text = _tooltip(entity.get("name") or "", tooltip_text(tip.get("html") or "") or tip.get("text") or "",
                        parts[3] == "item")
        return {**value, "tooltip": {"icon": tip.get("icon"), "quality": tip.get("quality"), "html": text,
                                     "text": text, "summary": text},
                "comments": {"count": 0, "all_comments_included": True, "top": []}, "citations": {}}
    def walk(o: Any, path: str) -> Any:
        if isinstance(o, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(o)]
        if isinstance(o, dict):
            if o.get("reference_type") == "wow_talent_export":
                return _build(o, path, builds, spec_of)
            if path.endswith("comments") or path.endswith(".author"):
                return {k: [] if isinstance(v, list) else 0 if isinstance(v, int) else "" for k, v in o.items()}
            return {k: walk(v, f"{path}.{k}") for k, v in o.items()}
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if isinstance(o, str) and leaf in _PEOPLE:
            return "A guide writer"
        if isinstance(o, str) and len(o) > _LONG and not re.fullmatch(r"\S+", o) and leaf not in _TITLES:
            return made_up_text(o, key, path)
        return o

    out = walk(value, "")
    items = ((out.get("build_references") or {}).get("items")) if isinstance(out, dict) else None
    if isinstance(items, list):
        out["build_references"]["items"] = [b for b in items if b is not None]
    return out


def _build(b: dict, path: str, builds: dict[int, list[str]], spec_of: Callable[[str], int | None]) -> dict | None:
    """A guide's talent build standing in: one of the test guild's raiders' talent strings for the same spec,
    labelled for raids or not as the guide's was (knowledge.talents leaves out builds for Mythic+ and the like); None
    when we have none for that spec."""
    ours = builds.get(spec_of(b.get("build_code") or "") or 0, [])
    if not ours:
        return None
    raid = not re.search(r"mythic\+|dungeon|m\+|level|pvp|arena|delve|beginner|easy|legacy|movement",
                         b.get("label") or "", re.I)
    return {"reference_type": "wow_talent_export",
            "label": f"{'Raid' if raid else 'Mythic+'} build {1 + int(number('label', path) * 9)}",
            "build_code": ours[int(number("build", path) * len(ours))]}


def image(key: str, value: str) -> str:
    """A Warcraft Logs image (base64) standing in: the same size and format, one flat colour (a class icon in its
    class colour) and, for a map, a grid."""
    import base64
    import io

    from PIL import Image, ImageDraw

    from wipebot.core.classes import class_rgb
    path = json.loads(key)[1]
    real = Image.open(io.BytesIO(base64.b64decode(value)))
    cls = path.split("/")[-1].split("-")[0] if path.startswith("icons/") else ""
    colour = class_rgb(cls) if cls else (60, 60, 70) if path.startswith("maps/") else (120, 40, 40)
    made = Image.new("RGB", real.size, colour)
    if path.startswith("maps/"):
        draw = ImageDraw.Draw(made)
        for x in range(0, real.size[0], 100):
            draw.line([(x, 0), (x, real.size[1])], fill=(90, 90, 100))
        for y in range(0, real.size[1], 100):
            draw.line([(0, y), (real.size[0], y)], fill=(90, 90, 100))
    out = io.BytesIO()
    made.save(out, format=real.format or "PNG")
    return base64.b64encode(out.getvalue()).decode()


# The guild's listings (its attendance, its reports with their players) name everyone in every log tagged to it, pugs
# too: a player outside the guild gets a made-up name, the same each time, so counts of who's in the guild still hold.
_LISTINGS = ("attendance(zoneID", "reports(guildID")
_MADE_UP_NAME = re.compile("^Pug(?:" + "|".join(_SYLLABLES) + ")+$")


def outsider(name: str) -> str:
    """A made-up name for a player outside the guild."""
    if _MADE_UP_NAME.match(name):
        return name
    digest = hashlib.sha256(f"outsider {name}".encode()).digest()
    return "Pug" + "".join(_SYLLABLES[b % len(_SYLLABLES)] for b in digest[:7])  # 16^7: two never meet


def is_outsider(name: str) -> bool:
    """Whether a name is one `outsider` made up."""
    return bool(_MADE_UP_NAME.match(name))


def listed_players(value: Any) -> list[dict]:
    """The players a listing of the guild's names: attendance's `players`, a report's `actors`."""
    out = []
    def walk(o: Any, parent: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            out.extend(p for p in o if parent in ("players", "actors") and isinstance(p, dict) and "name" in p)
            for v in o:
                walk(v, parent)
    walk(value, "")
    return out


def members(rows: list[tuple[str, Any]]) -> set[str]:
    """The guild's members, from the recorded pages of its member list."""
    return {m["name"] for k, v in rows if "members(limit" in k
            for m in ((((v or {}).get("guildData") or {}).get("guild") or {}).get("members") or {}).get("data") or []}


class Seen:
    """What the stand-ins go by: Raidbots' talent trees, the test guild's raiders' talent strings by spec and its
    members."""

    def __init__(self, rows: list[tuple[str, Any]]):
        self.trees = next((v for k, v in rows if k == '["raidbots", "talents.json"]'), [])
        self.members = members(rows)
        self.builds: dict[int, list[str]] = {}
        for k, v in rows:  # the test guild's raiders' talent strings (Report.talent_code)
            if k.startswith('["report"') and json.loads(k)[2] == "talent code" and isinstance(v, str) and v:
                self.builds.setdefault(self.spec_of(v) or 0, []).append(v)

    def spec_of(self, code: str) -> int | None:
        """A talent string's spec id, or None."""
        from wipebot.core.knowledge.talents import decode_loadout
        try:
            return decode_loadout(code, self.trees)[0].get("specId")
        except (ValueError, KeyError, IndexError):
            return None

    def standin(self, key: str, value: Any) -> Any:
        """An answer with what isn't ours made up, or left out; any other as it is."""
        if key.startswith('["wcl-guild"') and isinstance(value, dict):  # its rank among guilds is a ranking
            return {k: v for k, v in value.items() if k != "zoneRanking"}
        if key.startswith('["wcl"') and any(q in key for q in _LISTINGS) and self.members:
            value = json.loads(json.dumps(value))
            for p in listed_players(value):
                if p["name"] not in self.members:
                    p["name"] = outsider(p["name"])
            return value
        if replaced(key):
            return image(key, value) if json.loads(key)[0] == "wcl-image" else guide(key, value, self.builds,
                                                                                       self.spec_of)
        return value


def replaced(key: str) -> bool:
    """Whether an answer gets a stand-in (so a recording never reads one back as the site's own)."""
    parts = json.loads(key)
    return parts[0] == "wcl-image" or (parts[0] == "cli" and (parts[1] in ("wowhead", "method", "icy-veins")
                                                              or parts[1:3] == ["warcraft", "search"]))
