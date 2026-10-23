"""The model's answer made ready for Discord (`polish`).

Two passes do what the prompt leaves to the bot: abilities and items linked to Wowhead, player names in bold (Discord
can't colour text). The others repair what the model gets wrong now and then: a Wowhead link with a name where the
spell id goes, a pull link with a made-up fight, a blank line above a list; and a masked link to a site the tools
don't link to keeps only its text."""
import re
from collections.abc import Iterable, Sequence
from urllib.parse import urlsplit

from wipebot.core.report import Report

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
BLANK_BEFORE_LIST = re.compile(r"^(?![ \t]*(?:[-*]|\d+\.) )(\S.*)\n[ \t]*\n(?=[ \t]*(?:[-*]|\d+\.) )", re.M)


def polish(text: str, results: Sequence[str], reports: Sequence[Report]) -> str:
    """The answer as posted, from what the tools returned (`results`) and the reports they read, the session's
    first. A broken link is undone first, so the ability in it gets a proper one."""
    abilities = {spell: name for r in reports[::-1] for spell, name in r.abilities.items()}  # the first report wins
    text = link_items(link_abilities(unlink_broken(unlink_elsewhere(text)), results, abilities), results)
    text = bold_players(text, {p.name for r in reports for p in r.players.values()})
    for report in reports:
        text = fix_pull_links(text, report)
    return tight_lists(text)


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


def fix_pull_links(text: str, report: Report) -> str:
    """Links to pulls of the report with the right fight: the model makes "[pull 42](...#fight=42)" up from the
    pull number when a tool printed no link (pull 42 of Sszorak was fight 46). The boss is the one the link's text
    names, else the report's only boss; a link it can't place is left alone."""
    def fixed(m: re.Match) -> str:
        label, pull, code = m.group(1), int(m.group(2)), m.group(3)
        if code != report.code:
            return m.group(0)
        bosses = [b for b in report.by_boss if b.lower() in label.lower()] or (
            list(report.by_boss) if len(report.by_boss) == 1 else [])
        if len(bosses) != 1 or not 1 <= pull <= len(report.by_boss[bosses[0]]):
            return m.group(0)
        return f"[{label}]({report.url(report.by_boss[bosses[0]][pull - 1])})"
    return PULL_LINK.sub(fixed, text)


def tight_lists(text: str) -> str:
    """No blank line between a line and the list under it: Discord already puts space above a list."""
    return BLANK_BEFORE_LIST.sub(r"\1\n", text)
