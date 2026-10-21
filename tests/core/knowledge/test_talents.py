import pytest

from wipebot.core.knowledge.talents import LOADOUT_ALPHABET, build_hero, decode_loadout, hero_tree, untaken_spells

TREE = {"specId": 264, "className": "Shaman", "specName": "Restoration", "fullNodeOrder": [1, 2, 3, 4, 5],
        "classNodes": [{"id": 1, "entries": [{"id": 11, "name": "A", "maxRanks": 1}]},
                       {"id": 2, "entries": [{"id": 21, "name": "B", "maxRanks": 2}]},
                       {"id": 3, "entries": [{"id": 31, "name": "C1"}, {"id": 32, "name": "C2"}]}],
        "specNodes": [], "heroNodes": [{"id": 5, "subTreeId": 7, "entries": [{"id": 51, "name": "Hero"}]}],
        "subTreeNodes": [{"id": 4, "entries": [{"id": 41, "name": "Farseer", "traitSubTreeId": 8},
                                               {"id": 42, "name": "Totemic", "traitSubTreeId": 7}]}]}


def loadout(bits: list[int]) -> str:
    """Bits, least significant first, as a talent string."""
    bits = bits + [0] * (-len(bits) % 6)
    return "".join(LOADOUT_ALPHABET[sum(b << i for i, b in enumerate(bits[k:k + 6]))] for k in range(0, len(bits), 6))


def test_talent_strings_decode_against_the_tree():
    def num(v, n):
        return [(v >> i) & 1 for i in range(n)]
    bits = num(2, 8) + num(264, 16) + [0] * 128
    bits += [1, 1, 0, 0]  # node 1: purchased, full rank, not a choice
    bits += [1, 1, 1] + num(1, 6) + [0]  # node 2: 1 of 2 ranks
    bits += [1, 1, 0, 1] + num(1, 2)  # node 3: the second choice
    bits += [1, 1, 0, 1] + num(1, 2)  # node 4: hero tree choice, Totemic
    bits += [1, 0]  # node 5: granted for free
    tree, talents = decode_loadout(loadout(bits), [TREE])
    assert tree is TREE and talents == {11: 1, 21: 1, 32: 1, 42: 1}
    assert build_hero(TREE, talents) == "Totemic"
    assert hero_tree(TREE, {5}) == "Totemic"
    with pytest.raises(ValueError):
        decode_loadout("not a build!", [TREE])


# Two hero trees, made up as the game's are: each its own hero nodes, and a sub-tree node choosing between them.
HERO_TREES = {"specId": 268, "fullNodeOrder": [1, 10, 11, 12, 20, 21, 30],
              "classNodes": [{"id": 1, "entries": [{"id": 101, "name": "A", "maxRanks": 1}]}],
              "heroNodes": [{"id": n, "subTreeId": sub, "entries": [{"id": n * 10, "name": f"H{n}"}]}
                            for n, sub in ((10, 70), (11, 70), (12, 70), (20, 71), (21, 71))],
              "subTreeNodes": [{"id": 30, "entries": [{"id": 301, "name": "Ironvane", "traitSubTreeId": 70},
                                                      {"id": 302, "name": "Emberwake", "traitSubTreeId": 71}]}]}


def _hero_string(choice: int | None) -> str:
    """HERO_TREES' string with every hero node picked, both trees' (the game keeps the other tree's picks when a
    player switches), and the sub-tree node's `choice`, or that node not picked."""
    bits = [0] * 8 + [(268 >> i) & 1 for i in range(16)] + [0] * 128
    bits += [1, 1, 0, 0] * 6  # node 1 and the five hero nodes: purchased, full rank, not a choice
    bits += [0] if choice is None else [1, 1, 0, 1, choice & 1, choice >> 1]
    return loadout(bits)


def test_a_string_with_both_hero_trees_keeps_the_chosen_one():
    tree, talents = decode_loadout(_hero_string(1), [HERO_TREES])
    assert tree is HERO_TREES and talents == {101: 1, 200: 1, 210: 1, 302: 1}  # Emberwake's 2, not the other's 3
    assert build_hero(HERO_TREES, talents) == "Emberwake"


def test_a_string_without_the_sub_tree_choice_keeps_the_tree_with_most_picks():
    _, talents = decode_loadout(_hero_string(None), [HERO_TREES])
    assert talents == {101: 1, 100: 1, 110: 1, 120: 1}
    assert build_hero(HERO_TREES, talents) == "Ironvane"


def test_untaken_spells_are_the_other_side_of_a_choice_node_or_a_talent_skipped():
    tree = {"specNodes": [
        {"id": 81032, "type": "choice", "entries": [{"id": 101912, "name": "Ascendance", "spellId": 114052},
                                                     {"id": 135486, "name": "Healing Tide Totem", "spellId": 108280}]},
        {"id": 81041, "type": "single", "entries": [{"id": 101924, "name": "Spirit Link Totem", "spellId": 98008}]},
    ]}
    assert untaken_spells(tree, {101912, 101924}) == {108280}
    assert untaken_spells(tree, set()) == {114052, 108280, 98008}
