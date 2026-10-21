import httpx
import pytest

from wipebot.core.knowledge import raidbots
from wipebot.core.knowledge.fetch import KnowledgeError, Memo


async def test_raidbots_down_is_unavailable_not_empty(monkeypatch):
    async def get(self, url, **kwargs):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(raidbots, "_talents", Memo())
    for accessor in (raidbots.talent_names, raidbots.talent_trees, raidbots.consumable_items, raidbots.enchant_names):
        with pytest.raises(KnowledgeError, match="Raidbots"):
            await accessor()


async def test_a_choice_nodes_two_versions_of_one_spell_are_told_apart(monkeypatch):
    """Elemental's Earthquake node offers two Earthquakes: each gets its spell id, and each names the other. A
    talent's entries per rank share a name too, but aren't versions."""
    async def trees():
        return [{"specNodes": [
            {"type": "choice", "entries": [{"id": 1, "name": "Earthquake", "spellId": 462620},
                                           {"id": 2, "name": "Earthquake", "spellId": 61882}]},
            {"type": "choice", "entries": [{"id": 3, "name": "Ascending Air", "spellId": 5},
                                           {"id": 4, "name": "Jet Stream", "spellId": 6}]},
            {"type": "tiered", "entries": [{"id": 5, "name": "Everbloom", "spellId": 7},
                                           {"id": 6, "name": "Everbloom", "spellId": 8}]}]}]
    monkeypatch.setattr(raidbots, "talent_trees", trees)
    monkeypatch.setattr(raidbots, "_talents", Memo())
    assert await raidbots.talent_names() == {1: "Earthquake [462620]", 2: "Earthquake [61882]", 3: "Ascending Air",
                                             4: "Jet Stream", 5: "Everbloom", 6: "Everbloom"}
    assert await raidbots.choice_versions() == {1: 2, 2: 1}
