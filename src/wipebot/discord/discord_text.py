"""The model's answer made ready for Discord (`polish`).

Two passes do what the prompt leaves to the bot: abilities and items linked to Wowhead, player names in bold (Discord
can't colour text). The others repair what the model gets wrong now and then: a Wowhead link with a name where the
spell id goes, a pull link with a made-up fight, a blank line above a list; and a masked link to a site the tools
don't link to keeps only its text."""
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

from wipebot.core.knowledge import spaced
from wipebot.core.report import Report

from .emojis import MARKERS, SPINNER, ability_key, marker_emoji, spec_emoji, spell_emoji

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)|<?https?://\S+")
BOLD = re.compile(r"\*\*.+?\*\*")  # on one line
CODE = re.compile(r"^```.*?^```|`[^`\n]*`", re.M | re.S)  # a code block, or code inline: markdown shows as typed
CODE_OR_HEADING = re.compile(rf"{CODE.pattern}|^#{{1,3}} [^\n]*$", re.M | re.S)
ABILITY_ID = re.compile(r"\[(\d{2,9})\]")  # "Tempest [1287083]" in a tool result
# boss_guide's loot: "- Twisted Horror's Tendril (item 281215), ..."
ITEM_ID = re.compile(r"(?m)^\s*- (.+?) \(item (\d+)\)")
NAME_WORD = r"(?:[A-Z][\w'’-]*|of|the)"  # a word of a spell or talent name: "Lycara's", "Light of the Sun"
# A Wowhead link the model wrote with a name where the spell id goes: "[Dig In](https://www.wowhead.com/spell=Dig In)".
BROKEN_SPELL_LINK = re.compile(r"\[([^\]]+)\]\(https?://www\.wowhead\.com/spell=(?!\d+\))[^)]*\)")
PULL_LINK = re.compile(r"\[([^\]]*?\bpull (\d+)[^\]]*)\]\(https?://www\.warcraftlogs\.com/reports/(\w+)(?:#fight=\d+)?\)")
# The sites the tools link to. A masked link hides its address, so one to any other site could carry what the answer
# read (a raider's name, a question) to someone's server in its address: only its text is kept.
LINKED_SITES = ("warcraftlogs.com", "wowhead.com", "method.gg", "icy-veins.com", "warcraft.wiki.gg", "raidstrats.gg",
                "raider.io", "lorrgs.io", "raidbots.com")
# A raid marker as raid notes write it: {skull}, {rt8}
MARKER_TOKEN = re.compile(r"\{(rt[1-8]|x|" + "|".join(MARKERS) + r")\}", re.I)
SKIPPED = (MARKDOWN_LINK, CODE)  # where add_icons adds none
SPELL_LINK = re.compile(r"\[([^\]]+)\]\(https://www\.wowhead\.com/spell=(\d+)\)")  # [Tempest](...spell=1287083)
# A raid plan's scene with its link: "Scene 2 (https://raidstrats.gg/planner?view=<plan>/2):"
SCENE_LINK = re.compile(r"^\s*Scene (\d+) \((https://raidstrats\.gg/\S+?)\):", re.M)
SCENE = re.compile(r"(?<![\w\[])scene (\d+)(?![\w\]])", re.I)
# The clause a scene is named after in brackets: "**Grønne orbs (scene 2):**", "pil inn til orbene i P1 (scene 3),"
NAMED_SCENE = re.compile(r"(?:(?<=\*\*)|(?<=^- )|(?<=^)|(?<=[,;:.!?] ))([^\s*\[(\-][^*\n()\[\],;:.!?]{1,80}?) "
                         r"\((?i:scene) (\d+)\)", re.M)
# A spot on a raid plan with its raider's class: "#9 Martin (hunter):", "#7 Zebino (Zebinomage) (mage):"
PLAN_SPOT = re.compile(r"#\d+ ([^():#\n]+?)(?: \(([^()\n]+)\))? \(([a-z]+)\):")
MASKED_LINK = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)")  # [text](url)
LIST_LINE = r"[ \t]*(?:[-*]|\d+\.) "
BLANK_IN_LIST = re.compile(rf"^({LIST_LINE}.*)\n[ \t]*\n(?={LIST_LINE})", re.M)  # Discord ends a nested list there
BLANK_BEFORE_LIST = re.compile(r"^(?![ \t]*(?:[-*]|\d+\.) )(\S.*)\n[ \t]*\n(?=[ \t]*(?:[-*]|\d+\.) )", re.M)


def polish(text: str, results: Sequence[str], reports: Sequence[Report], icons: Mapping[str, str] = {},
           roster: Mapping[str, tuple[str, str]] = {}) -> str:
    """The answer as posted, from what the tools returned (`results`) and the reports they read, the session's
    first, with the bot's emojis (`icons`, by name) where it has them. A player's spec for their icon is what a tool
    result says of them (the pulls asked about), else the report's, else the guild's `roster` (name -> WCL's class
    and spec). A broken link is undone first, so the ability in it gets a proper one."""
    abilities = {spell: name for r in reports[::-1] for spell, name in r.abilities.items()}  # the first report wins
    text = unlink_wrong(unlink_broken(unlink_elsewhere(text)), abilities)
    text = link_items(link_abilities(text, results, abilities), results)
    text = link_scenes(text, results)
    if not icons:  # with icons, each name is marked by its icon: bold on top of them is noise
        text = bold_players(text, {p.name for r in reports for p in r.players.values()})
    for report in reports:
        text = fix_pull_links(text, report, results)
    if icons:
        classes = {name.lower(): name for name in icons if "_" not in name and ":" not in name}
        specs = dict(roster) | planned_classes(results, classes)
        specs |= {p.name: (p.class_name, p.spec) for r in reports[::-1] for p in r.players.values()}
        specs |= specs_in(results, icons)  # the pulls asked about, over the spec played most in the report
        text = add_icons(text, specs, icons)
    return tight_lists(verdict_bold(text))


# The end of a sentence in the opening verdict: a stop and a capital after it ("f.eks. 0:07" and "nr. 2" go on)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÆØÅÄÖÜ])")


def verdict_bold(text: str) -> str:
    """The answer with only its verdict's first sentence bold, when the model bolded more of its opening line: the
    verdict is one short sentence, and a bold block of three is a wall before the picture."""
    first, nl, rest = text.partition("\n")
    if not (first.startswith("**") and first.endswith("**") and len(first) > 4 and "**" not in first[2:-2]):
        return text
    inner = first[2:-2]
    links = [m.span() for m in MARKDOWN_LINK.finditer(inner)]
    for m in SENTENCE_END.finditer(inner):
        if not any(a <= m.start() < b for a, b in links):
            return f"**{inner[:m.start()]}** {inner[m.end():]}{nl}{rest}"
    return text


def specs_in(results: Sequence[str], icons: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The players tool results name with their spec or class in brackets after them ("Grusarn (Fury Warrior)",
    "Torvek (Hunter, ...)"): name -> WCL's class and spec ("" for a class alone), for the specs and classes the bot
    has an icon for."""
    pairs = _spec_pairs(icons)
    known = {f"{spaced(s)} {spaced(c)}": (c, s) for c, s in pairs} | {spaced(c): (c, "") for c, _ in pairs}
    if not known:
        return {}
    pattern = re.compile(rf"(?<![\w'’-])([^\W\d_][\w'’-]{{2,}}) \(({_either(known)})(?=[,)])")
    return {name: known[said] for result in results for name, said in pattern.findall(result)}


def _link_first(text: str, name: str, url: str, longer: Iterable[str] = ()) -> str:
    """`text` with the first mention of `name` linked to `url`; not one inside a link, code or a `longer` name."""
    taken = [m.span() for pattern in (MARKDOWN_LINK, CODE) for m in pattern.finditer(text)]
    inside = [m.span() for n in longer for m in re.finditer(re.escape(n), text)]
    for m in re.finditer(rf"(?<![\w\[]){re.escape(name)}(?![\w\]])", text):
        if not any(a <= m.start() < b for a, b in taken) and \
                not any(a <= m.start() and m.end() <= b for a, b in inside):
            return f"{text[:m.start()]}[{name}]({url}){text[m.end():]}"
    return text


def longer_names(name: str, results: str) -> set[str]:
    """Names in the tool results that hold `name` with more words around it ("Lycara's Inspiration", "Improved
    Stampeding Roar"): talents and abilities the report's log doesn't have, so only the results tell them."""
    out = set()
    for m in re.finditer(rf"((?:{NAME_WORD} )*){re.escape(name)}((?: {NAME_WORD})*)", results):
        if re.search(r"[A-Z]", m.group(1) + m.group(2)):
            out.add(m.group(0).strip())
    return out


def link_abilities(text: str, results: Sequence[str], abilities: dict[int, str]) -> str:
    """The first mention of each ability linked to Wowhead: the abilities the tool results gave with an [id], so the
    link is the spell this report means (a boss's Tempest, not the shaman talent), and the abilities the results
    name that have one id in the report (a player's spells). An ability the answer already links is left alone, and
    so is one inside a longer name ("Exhaustion" in "Soul Exhaustion", "Inspiration" in "Lycara's Inspiration")."""
    named: dict[str, int] = {}
    for result in results:
        for found in ABILITY_ID.finditer(result):
            spell = int(found.group(1))
            name = abilities.get(spell)
            if name and len(name) >= 4 and spell > 10:  # not Melee (1) and the like
                named.setdefault(name, spell)
    ids_of: dict[str, set[int]] = {}
    for spell, name in abilities.items():
        ids_of.setdefault(name, set()).add(spell)
    seen = "\n".join(results)
    for name, ids in ids_of.items():
        spell = next(iter(ids))
        if len(ids) == 1 and spell > 10 and len(name) >= 5 and name[0].isupper() and name in seen:
            named.setdefault(name, spell)
    names = set(abilities.values()) | set(named)
    for name, spell in sorted(named.items(), key=lambda kv: -len(kv[0])):
        if f"spell={spell}" in text or f"[{name}](" in text:
            continue
        longer = {n for n in names if len(n) > len(name) and name in n} | longer_names(name, seen)
        text = _link_first(text, name, f"https://www.wowhead.com/spell={spell}", longer)
    return text


def link_items(text: str, results: Sequence[str]) -> str:
    """The first mention of each item a tool result gave with its id, linked to Wowhead; an item the answer already
    links is left alone."""
    items = {name: int(item) for result in results for name, item in ITEM_ID.findall(result)}
    for name, item in sorted(items.items(), key=lambda kv: -len(kv[0])):
        if f"item={item}" not in text and f"[{name}](" not in text:
            text = _link_first(text, name, f"https://www.wowhead.com/item={item}")
    return text


def link_scenes(text: str, results: Sequence[str]) -> str:
    """Each scene of a raid plan linked once to it on raidstrats.gg, as the plan's tool result links it: the clause
    a scene is named after in brackets ("**Grønne orbs (scene 2):**", "pil inn til orbene (scene 3),") becomes the
    link and the brackets go, else the first "scene 2" is linked. Not when the results hold several plans with that
    scene, or the answer links it."""
    urls: dict[int, set[str]] = {}
    for result in results:
        for n, url in SCENE_LINK.findall(result):
            urls.setdefault(int(n), set()).add(url)
    linked = {n for n, found in urls.items() if len(found) != 1 or next(iter(found)) in text}

    def linker(label: Callable[[re.Match], str], n: Callable[[re.Match], int]) -> Callable[[re.Match], str]:
        def link(m: re.Match) -> str:
            taken = [s.span() for pattern in (MARKDOWN_LINK, CODE) for s in pattern.finditer(m.string)]
            if n(m) not in urls or n(m) in linked or any(a <= m.start() < b for a, b in taken):
                return m.group(0)
            linked.add(n(m))
            return f"[{label(m)}]({next(iter(urls[n(m)]))})"
        return link
    text = NAMED_SCENE.sub(linker(lambda m: m.group(1), lambda m: int(m.group(2))), text)
    return SCENE.sub(linker(lambda m: m.group(0), lambda m: int(m.group(1))), text)


def planned_classes(results: Sequence[str], classes: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    """The raiders a raid plan names with their class, typed name and character alike: name -> (WCL's class, no
    spec), for those not in a report. `classes`: the lowercase class -> WCL's ("deathknight" -> "DeathKnight")."""
    found = {}
    for result in results:
        for name, character, cls in PLAN_SPOT.findall(result):
            if cls in classes:
                found |= {n: (classes[cls], "") for n in (name, character) if n}
    return found


def unlink_elsewhere(text: str) -> str:
    """Masked links to sites other than LINKED_SITES turned back into their text. Any "](" starts one, whatever
    brackets its text holds, and its address runs to the bracket that closes it, so no way of writing a link that
    Discord shows ("[a [b] c](...)", "[x]( url 'title' )") gets past."""
    at = 0
    while (close := text.find("](", at)) >= 0:
        end, depth = close + 2, 1
        while end < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        start = _opening(text, close)
        if depth or start < 0 or _linked(text[close + 2:end - 1]):
            at = close + 2
            continue
        label = text[start + 1:close]
        text, at = text[:start] + label + text[end:], start + len(label)
    return text


def _opening(text: str, close: int) -> int:
    """Where the text of the link whose "]" is at `close` opens: its matching "[", else the last "[" before it."""
    depth = 1
    for i in range(close - 1, -1, -1):
        depth += {"]": 1, "[": -1}.get(text[i], 0)
        if not depth:
            return i
    return text.rfind("[", 0, close)


def _linked(destination: str) -> bool:
    """Whether a masked link's destination ("url" or "<url>", with a title or not) is a page of LINKED_SITES: an
    http(s) address whose real host is one, with no login in it and no backslash (which browsers read as "/")."""
    destination = destination.strip()
    url = destination[1:].partition(">")[0] if destination.startswith("<") else (destination.split() or [""])[0]
    if "\\" in url or not url.isprintable():
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and "@" not in parts.netloc and bool(re.fullmatch(r"[a-z0-9.-]+", host))
            and any(host == s or host.endswith("." + s) for s in LINKED_SITES))


def unlink_broken(text: str) -> str:
    """Wowhead spell links without a spell id turned back into plain names."""
    return BROKEN_SPELL_LINK.sub(r"\1", text)


def unlink_wrong(text: str, abilities: Mapping[int, str]) -> str:
    """Wowhead spell links whose id the report names as another spell ("[Visceral Burst](...spell=1290662)", which is
    Ravenous Feast) turned back into plain names, so the ability gets its own link; a short name for the spell
    ("[Wind Rush](...)" for Wind Rush Totem) stays."""
    def checked(m: re.Match) -> str:
        said, named = m.group(1).lower(), abilities.get(int(m.group(2)), "").lower()
        return m.group(0) if not named or said in named or named in said else m.group(1)
    return SPELL_LINK.sub(checked, text)


def bold_players(text: str, names: Iterable[str]) -> str:
    """The first mention of each player in bold, so names stand out without bolding a whole answer. A name already
    in bold text (a bold first line, a bullet's bold lead), a link, code or a heading is left alone and counts as
    its first mention; a name the model bolded on its own is unbolded first, so it gets bold only the first time."""
    names = sorted({n for n in names if len(n) >= 3}, key=len, reverse=True)
    if not names:
        return text
    text = BOLD.sub(lambda m: m.group(0)[2:-2] if m.group(0)[2:-2] in names else m.group(0), text)
    skip = [m.span() for pattern in (BOLD, MARKDOWN_LINK, CODE_OR_HEADING) for m in pattern.finditer(text)]
    seen: set[str] = set()

    def bold(m: re.Match) -> str:
        first = m.group(0) not in seen
        seen.add(m.group(0))
        return m.group(0) if not first or any(a <= m.start() < b for a, b in skip) else f"**{m.group(0)}**"
    return re.sub(rf"(?<![\w\[])(?:{'|'.join(map(re.escape, names))})(?!\w)", bold, text)


def fix_pull_links(text: str, report: Report, results: Sequence[str] = ()) -> str:
    """Links to pulls of the report with the right fight: the model makes "[pull 42](...#fight=42)" up from the
    pull number when a tool printed no link (pull 42 of Sszorak was fight 46). The boss is the one the link's text
    names, else the boss of the report the tools' `results` name most (a night's overview names every boss once),
    else the latest pull's, as the tools take it; a link it can't place keeps only its text, as it would open
    another pull."""
    times = {b: sum(r.count(b) for r in results) for b in report.by_boss} if PULL_LINK.search(text) else {}
    most = max(times.values(), default=0)
    named = [b for b, n in times.items() if n == most] if most else []

    def fixed(m: re.Match) -> str:
        label, pull, code = m.group(1), int(m.group(2)), m.group(3)
        if code != report.code:
            return m.group(0)
        bosses = [b for b in report.by_boss if b.lower() in label.lower()] or named or [report.boss(None)]
        if len(bosses) != 1 or not 1 <= pull <= len(report.by_boss[bosses[0]]):
            return label
        return f"[{label}]({report.url(report.by_boss[bosses[0]][pull - 1])})"
    return PULL_LINK.sub(fixed, text)


def tight_lists(text: str) -> str:
    """No blank line between a line and the list under it (Discord already puts space above a list), nor between
    two items of a list (Discord ends a nested list at one, so the next item shows as a new list)."""
    return BLANK_IN_LIST.sub(r"\1\n", BLANK_BEFORE_LIST.sub(r"\1\n", text))


def _marker(token: str) -> str:
    """{rt8}, {x}, {skull} -> 'skull', 'cross', 'skull'."""
    token = token.lower()
    return list(MARKERS)[int(token[2]) - 1] if token.startswith("rt") else "cross" if token == "x" else token


def _either(names: Iterable[str]) -> str:
    """A regex for any of `names`, the longest first so "Death Knight" wins over a shorter name inside it."""
    return "|".join(map(re.escape, sorted(names, key=len, reverse=True)))


def add_icons(text: str, specs: Mapping[str, tuple[str, str]], icons: Mapping[str, str]) -> str:
    """The bot's emojis in an answer: a raid marker's for every {skull} or {rt8} and before every "skull marker", a
    spec's before a player's first mention (`specs`: name -> WCL's class and spec), and a boss ability's before its
    first mention, its Wowhead link or its name capitalised: once is enough to know them by, and more is noise in a
    long answer. Not before a spec or class named as such ("the shaman cooldowns", a plan's spot labelled "Priest").
    A marker in a link's text goes after the link, as Discord shows no emoji there. Code is left alone, and links but
    for that; without an emoji, the text stays as it is."""
    if not icons:
        return text

    def skipped(at: int, spans: list[tuple[int, int]]) -> bool:
        return any(a <= at < b for a, b in spans)

    def out_of_link(m: re.Match) -> str:
        found = [icons[e] for t in MARKER_TOKEN.findall(m.group(1)) if (e := marker_emoji(_marker(t))) in icons]
        label = " ".join(MARKER_TOKEN.sub("", m.group(1)).split()) if found else ""
        return f"[{label}]({m.group(2)}) {' '.join(found)}" if label else m.group(0)
    text = MASKED_LINK.sub(out_of_link, text)
    spans = [m.span() for pattern in SKIPPED for m in pattern.finditer(text)]

    def token(m: re.Match) -> str:
        icon = icons.get(marker_emoji(_marker(m.group(1))))
        return icon if icon and not skipped(m.start(), spans) else m.group(0)
    text = MARKER_TOKEN.sub(token, text)
    spans = [m.span() for pattern in SKIPPED for m in pattern.finditer(text)]

    classes = {w.lower() for c, _ in _spec_pairs(icons) for w in (c, spaced(c))}
    players = {name: cs for name, cs in specs.items() if len(name) >= 3 and name.lower() not in classes}
    parts = [f"(?P<player>{_either(players)})"] if players else []
    parts.append(f"(?i:(?P<marker>{'|'.join(MARKERS)})(?= markers?\\b))")

    seen: set[str] = set()  # the players and boss abilities with their icon already

    def icon(m: re.Match) -> str:
        said = m.group(0)
        if skipped(m.start(), spans):
            return said
        if m.lastgroup == "marker":
            found = icons.get(marker_emoji(said.lower()))
        elif said in seen:
            return said
        else:
            c, s = players[said]
            found = icons.get(spec_emoji(c, s)) or icons.get(spec_emoji(c))
            seen.add(said)
        return f"{found} {said}" if found else said
    text = re.sub(rf"(?<![\w\[])(?:{'|'.join(parts)})(?![\w\]])", icon, text)
    # a boss ability's icon before its first mention: its Wowhead link or its name, by the icon (a cast and its
    # debuff have different ids, and one icon)
    def once(said: str, found: str | None) -> str:
        if not found or found in seen:
            return said
        seen.add(found)
        return f"{found} {said}"
    found_at = [(m.start(), m.end(), icons.get(spell_emoji(m.group(2))) or icons.get(ability_key(m.group(1))))
                for m in SPELL_LINK.finditer(text)]
    abilities = [key.split(":", 1)[1] for key in icons if key.startswith("ability:")]
    named = re.compile(rf"(?<![\w\[])(?i:{_either(abilities)})(?![\w\]])") if abilities else None
    skip = [m.span() for pattern in SKIPPED for m in pattern.finditer(text)]
    if named:  # capitalised: a name, not the word ("sever")
        found_at += [(m.start(), m.end(), icons[ability_key(m.group(0))]) for m in named.finditer(text)
                     if m.group(0)[0].isupper() and not skipped(m.start(), skip)]
    out, at = [], 0
    for a, b, found in sorted(found_at, key=lambda t: t[:2]):
        if a < at:
            continue
        out += [text[at:a], once(text[a:b], found)]
        at = b
    return "".join(out) + text[at:]


def _spec_pairs(icons: Mapping[str, str]) -> list[tuple[str, str]]:
    """The (class, spec) of each spec's emoji the bot has."""
    return [cast(tuple[str, str], tuple(name.split("_", 1))) for name in icons
            if "_" in name and ":" not in name and not name.startswith(("marker_", "raid_", "spell_"))
            and name != SPINNER]
