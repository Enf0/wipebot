"""Game knowledge on demand: Wowhead (spells, guides, loot, hotfixes, news), Method and Icy Veins (boss and spec
guides with their talent builds), Lorrgs (spec cooldowns and roles, top parses' cooldowns), Raider.IO (a
character's experience, the raids and their bosses) through warcraft_cli (`fetch`); Raidbots' talent trees and item
data, the game's map table and Warcraft Logs' images over HTTP. Every answer is kept in the cache on disk.

A module per subject. Each holds data accessors (what report/ computes with) and, for the knowledge tools, a
`*_text` function that is the tool's whole answer. The names below are those report/, server.py and remote.py use.
"""
from .bosses import boss_guide_text, boss_loot_text, boss_mechanic_text, boss_spells, boss_tooltips_text, raids
from .fetch import KnowledgeError
from .maps import map_bounds, map_image, wcl_image
from .names import matches_boss, names_boss, spaced
from .news import hotfixes_text
from .pages import read_text, search_text
from .players import character_experience_text
from .raidbots import consumable_items, enchant_names, talent_names
from .specs import heals, spec_cooldowns_text, spec_guide_text, spec_spells
from .spells import (
                     defensive_effect,
                     items_granting,
                     lust_debuffs,
                     lust_duration_s,
                     spell_cooldown,
                     spell_name,
                     spell_text,
)
from .talents import build_hero, class_and_spec, guide_builds, hero_tree, tree_for, untaken_spells
from .top_parses import cooldown_comparison_text, top_cooldowns_text

__all__ = [
    "KnowledgeError", "boss_guide_text", "boss_loot_text", "boss_mechanic_text", "boss_spells", "boss_tooltips_text",
    "build_hero",
    "character_experience_text", "class_and_spec", "consumable_items", "cooldown_comparison_text", "top_cooldowns_text",
    "defensive_effect", "enchant_names", "guide_builds", "heals", "hero_tree", "hotfixes_text", "items_granting",
    "lust_debuffs", "lust_duration_s", "map_bounds", "map_image", "matches_boss",
    "names_boss", "raids", "read_text", "search_text",
    "spaced",
    "spec_cooldowns_text", "spec_guide_text", "spec_spells", "spell_cooldown", "spell_name",
    "spell_text", "talent_names", "tree_for", "untaken_spells", "wcl_image",
]
