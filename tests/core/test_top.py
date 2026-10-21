"""A player against the top parses of their spec (report/top.py)."""
from wipebot.core.report import casting, top


def entry(name: str, guid: int, total: int) -> dict:
    """A row of a Casts table."""
    return {"name": name, "guid": guid, "total": total}


async def test_casts_vs_top_merges_a_spells_ids_and_flags_only_gaps_that_matter(rep, patch_wcl, fake_knowledge,
                                                                                 monkeypatch):
    f = rep.fight(13)
    p = rep.player("Enfo")
    spells = [{"spell_id": 383410, "name": "Celestial Alignment", "cooldown": 120.0, "tags": ["dps"],
               "variations": [102560], "spell_type": "druid-balance"},
              {"spell_id": 1236994, "name": "Potion of Recklessness", "cooldown": 300.0, "spell_type": "other-potions"}]
    mine = [entry("Mutilate", 1329, 30), entry("Mutilate", 27576, 30), entry("Incarnation: Chosen of Elune", 102560, 1),
            entry("Feint", 1966, 2), entry("Potion of Recklessness", 1236994, 1), entry("Fire Breath", 359073, 20)]
    refs = {1: [entry("Mutilate", 1329, 15), entry("Mutilate", 27576, 15), entry("Celestial Alignment", 383410, 3),
                entry("Fire Breath", 359073, 0)],
            2: [entry("Mutilate", 1329, 16), entry("Mutilate", 27576, 14), entry("Celestial Alignment", 383410, 3),
                entry("Fire Breath", 382411, 48), entry("Feint", 1966, 1)]}

    async def table(code, fight_ids, data_type, **filters):
        return {"entries": mine if code == rep.code else refs[filters["sourceID"]]}

    async def top_parses(r, fight, player):
        return [{"name": f"Ref{i}", "bracketData": 300, "duration": 360_000, "report": {"code": f"R{i}", "fightID": 1}}
                for i in (1, 2)]

    async def spec_spells(player):
        return spells
    patch_wcl(rep, table=table, player_ids={"Ref1": 1, "Ref2": 2})
    fake_knowledge(tree_for=None)
    monkeypatch.setattr(top, "top_parses", top_parses)
    monkeypatch.setattr(rep, "spells", spec_spells)
    alive_s, _ = casting.alive(rep, f, p, [d for d in await rep.deaths(f) if d["targetID"] == p.id], [])
    minutes = alive_s / 60
    lines = (await top.compare_casts(rep, "Enfo", 13)).splitlines()
    # Both Mutilate ids count as one spell, for the player and the top parses.
    assert f"  Mutilate [1329, 27576]: {60 / minutes:.1f} / 5.0 (5.0-5.0)  <- far more" in lines
    # A replacing talent's casts are the spell Lorrgs lists, and a cooldown of a minute or more is never "far more".
    assert (f"  Celestial Alignment [102560] (cast as Incarnation: Chosen of Elune): {1 / minutes:.1f} / 0.5 (0.5-0.5)"
            in lines)
    # A reference that lists a spell with no cast counted (an empowered spell under its other id) doesn't count.
    assert f"  Fire Breath [359073]: {20 / minutes:.1f} / 8.0 (8.0-8.0)" in lines
    # One extra cast on a short pull isn't far more, and neither is a potion.
    assert f"  Feint [1966]: {2 / minutes:.1f} / 0.1 (0.0-0.2)" in lines
    assert f"  Potion of Recklessness [1236994]: {1 / minutes:.1f} / 0.0 (0.0-0.0)" in lines
    assert "far more" not in "\n".join(line for line in lines if "Mutilate" not in line)
