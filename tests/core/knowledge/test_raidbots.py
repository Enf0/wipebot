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
