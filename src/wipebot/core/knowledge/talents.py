"""Talents against Raidbots' trees: which spec a text names, hero trees, talent import strings, and the raid
builds the spec guides give."""
import difflib
import re
from collections import Counter, defaultdict
from collections.abc import Collection

from . import fetch
from .fetch import KnowledgeError
from .guides import SOURCE_NAMES, spec_guide_ref
from .names import spaced
from .raidbots import talent_trees
from .spells import tooltip

VERSIONS_WORDS = 20  # tooltips differing in more words than this aren't quoted

LOADOUT_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


async def tree_for(class_name: str, spec: str) -> dict | None:
    """A spec's Raidbots talent tree, from WCL's class and spec names ('DeathKnight', 'BeastMastery'); None when
    Raidbots has none for it."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower())
    return next((t for t in await talent_trees()
                 if norm(t.get("className", "")) == norm(class_name) and norm(t.get("specName", "")) == norm(spec)),
                None)


async def class_and_spec(text: str) -> tuple[str, str]:
    """'balance druid', 'Beast Mastery Hunter', 'unholy' -> WCL's class and spec names ('Druid', 'Balance';
    'Hunter', 'BeastMastery'), from Raidbots' trees. A spec name alone does when only one class has it."""
    words = text.lower()

    def says(name: str) -> bool:  # 'Beast Mastery' in 'beast mastery' or 'beastmastery', not 'Holy' in 'unholy'
        return bool(re.search(r"\b" + r"\s*".join(map(re.escape, name.lower().split())) + r"\b", words))
    found = [(t["className"], t["specName"]) for t in await talent_trees()
             if t.get("className") and t.get("specName") and says(t["specName"])]
    with_class = [(c, s) for c, s in found if says(c)]
    found = with_class or found
    if len(found) != 1:
        raise KnowledgeError(f"which spec is '{text}'? Give spec and class, e.g. 'balance druid'"
                             + (f" (could be {', '.join(f'{s} {c}' for c, s in found)})" if found else ""))
    class_name, spec = found[0]
    return class_name.replace(" ", ""), spec.replace(" ", "")


def hero_tree(tree: dict, node_ids: Collection[int]) -> str | None:
    """The hero talent tree a set of picked talent nodes belongs to ('Totemic', 'Farseer'), or None."""
    subtrees = {e["traitSubTreeId"]: e["name"] for n in tree.get("subTreeNodes") or [] for e in n.get("entries") or []
                if e.get("traitSubTreeId")}
    picked = Counter(n["subTreeId"] for n in tree.get("heroNodes") or [] if n["id"] in node_ids and n.get("subTreeId"))
    return subtrees.get(picked.most_common(1)[0][0]) if picked else None


def untaken_spells(tree: dict, talents: Collection[int]) -> set[int]:
    """Spell ids the tree grants only as talents and the picked talent entries don't include: a choice node's
    other side (Ascendance or Healing Tide Totem) or a talent skipped, by the spell it grants and the one it shows
    (Avenging Crusader grants 394088 and shows 216331, the id it's cast and listed by). Baseline spells aren't in the
    tree."""
    grants: dict[int, set[int]] = defaultdict(set)
    for key in ("classNodes", "specNodes", "heroNodes"):
        for n in tree.get(key) or []:
            for e in n.get("entries") or []:
                for spell in {e.get("spellId"), e.get("visibleSpellId")} - {None}:
                    grants[spell].add(e["id"])
    return {spell for spell, entries in grants.items() if not entries & set(talents)}


async def versions_difference(entry: int, other: int) -> str:
    """How two versions of one spell on a choice node (`choice_versions`) differ, from their Wowhead tooltips:
    'their tooltips differ only in "a selected location" / "your target"'; '' when a tooltip can't be read."""
    spells = {e["id"]: e["spellId"] for spec in await talent_trees() for key in ("classNodes", "specNodes")
              for n in spec.get(key) or [] for e in n.get("entries") or [] if e.get("id") in (entry, other)
              and e.get("spellId")}
    try:
        a, b = [(await tooltip(spells[i])).text.split() for i in (entry, other)]
    except (KeyError, KnowledgeError):
        return ""
    if not (a and b):
        return ""
    opcodes = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
    changes = [(" ".join(a[i1:i2]), " ".join(b[j1:j2])) for op, i1, i2, j1, j2 in opcodes if op != "equal"]
    if not changes:
        return "their tooltips are the same"
    if sum(len(x.split()) + len(y.split()) for x, y in changes) > VERSIONS_WORDS:
        return "their tooltips differ more widely (spell_info reads each)"
    return "their tooltips differ only in " + ", ".join(
        " / ".join(f'"{words}"' if words else "(nothing)" for words in change) for change in changes)


def build_hero(tree: dict, talents: Collection[int]) -> str | None:
    """The hero tree of decoded talents: the sub-tree choice the build makes, else its hero nodes."""
    for n in tree.get("subTreeNodes") or []:
        for e in n.get("entries") or []:
            if e["id"] in talents:
                return e.get("name")
    nodes = {n["id"] for n in tree.get("heroNodes") or [] if any(e["id"] in talents for e in n.get("entries") or [])}
    return hero_tree(tree, nodes)


def decode_loadout(code: str, trees: list[dict]) -> tuple[dict, dict[int, int]]:
    """A WoW talent import string -> (the spec's Raidbots tree, {talent entry id: rank}), with the talents the
    tree grants for free left out. The string is base64 of a bit stream, least significant bit first: version (8
    bits), spec id (16), tree hash (128), then per node in the tree's fullNodeOrder: selected (1); if selected,
    purchased (1); if purchased, partially ranked (1) with its ranks (6), and choice node (1) with the choice (2)."""
    bits = []
    for ch in code.strip():
        if ch not in LOADOUT_ALPHABET:
            raise ValueError(f"not a talent string: {code[:20]}...")
        v = LOADOUT_ALPHABET.index(ch)
        bits += [(v >> i) & 1 for i in range(6)]
    pos = 0

    def read(n: int) -> int:
        nonlocal pos
        if pos + n > len(bits):
            raise ValueError("talent string ends early")
        v = sum(bits[pos + i] << i for i in range(n))
        pos += n
        return v

    read(8)  # version
    spec_id = read(16)
    read(128)  # tree hash
    tree = next((t for t in trees if t.get("specId") == spec_id), None)
    if not tree:
        raise ValueError(f"no talent tree for spec id {spec_id}")
    nodes = {n["id"]: n for key in ("classNodes", "specNodes", "heroNodes", "subTreeNodes")
             for n in tree.get(key) or []}
    picked: dict[int, int] = {}
    hero: dict[int, int] = {}  # picked entry -> its hero tree
    for node_id in tree.get("fullNodeOrder") or []:
        if not read(1) or not read(1):  # not selected, or granted for free
            continue
        ranks = read(6) if read(1) else None
        choice = read(2) if read(1) else 0
        node = nodes.get(node_id)
        if node and node.get("entries") and choice < len(node["entries"]):
            entry = node["entries"][choice]
            picked[entry["id"]] = ranks or entry.get("maxRanks") or node.get("maxRanks") or 1
            if node.get("subTreeId"):
                hero[entry["id"]] = node["subTreeId"]
    # The string carries both hero trees' picks (the game keeps the other tree's when a player switches); only the
    # chosen sub-tree's count. The choice is the subTreeNodes entry picked, else the tree with the most picks.
    chosen = next((e.get("traitSubTreeId") for n in tree.get("subTreeNodes") or [] for e in n.get("entries") or []
                   if e["id"] in picked), None)
    if chosen is None and hero:
        chosen = Counter(hero.values()).most_common(1)[0][0]
    for entry_id, sub_tree in hero.items():
        if sub_tree != chosen:
            del picked[entry_id]
    return tree, picked


async def guide_builds(class_name: str, spec: str) -> list[dict]:
    """The raid talent builds the spec's Icy Veins and Method guides give, decoded: source, label, url, hero tree
    (from the string, not the label: docs/reference/sources.md), {talent entry id: rank}. Builds for Mythic+,
    leveling, PvP, movement-heavy fights or a legacy patch are left out. Raises when neither guide can be read."""
    trees = await talent_trees()
    tree = await tree_for(class_name, spec)
    if not tree:
        return []
    name = f"{spaced(spec)} {spaced(class_name)}".lower()
    out, failed = [], []
    for source in ("icy-veins", "method"):
        try:
            data = await fetch.cli(source, "guide-full", await spec_guide_ref(name, source))
        except KnowledgeError as e:
            failed.append(str(e))
            continue
        url = (data.get("guide") or {}).get("page_url") or ""
        for b in (data.get("build_references") or {}).get("items") or []:
            label = b.get("label") or ""
            if b.get("reference_type") != "wow_talent_export" or re.search(
                    r"mythic\+|dungeon|m\+|level|pvp|arena|delve|beginner|easy|legacy|movement", label, re.I):
                continue
            try:
                build_tree, talents = decode_loadout(b.get("build_code") or "", trees)
            except ValueError:
                continue
            if build_tree.get("specId") != tree.get("specId"):
                continue
            out.append({"source": SOURCE_NAMES[source], "label": label, "url": url,
                        "hero": build_hero(build_tree, talents), "talents": talents})
    if len(failed) == 2:
        raise KnowledgeError("; ".join(failed))
    return out
