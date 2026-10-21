"""`breakdown` (report/tables.py): what it asks Warcraft Logs for. What it makes of the answers is pinned by the tool
cases on real pulls (test_tools.py: damage taken, healing, buffs, by pull, interrupts, dispels)."""
import pytest

from wipebot.core.report import FactError, tables


@pytest.mark.parametrize("args, error", [
    ({"data_type": "Threat"}, "data_type must be one of"),
    ({"data_type": "Healing", "group_by": "role"}, "group_by must be player, ability or pull"),
])
async def test_breakdown_says_what_it_takes(rep, args, error):
    with pytest.raises(FactError, match=error):
        await tables.breakdown(rep, **args)


async def test_a_players_part_is_asked_by_who_had_the_aura_cast_or_took_the_damage(rep, patch_wcl):
    """Warcraft Logs filters Buffs and Debuffs on who has the aura, every other table on its source, which for
    DamageTaken is who took it; debuffs on enemies are a player's by the name of who applied them. The tables
    summed per pull stop where the wipe was decided, but with whole_pull; auras are always the whole pull."""
    asked = []

    def table(code, fight_ids, data_type, **filters):
        asked.append((data_type, filters))
        return {"entries": []}
    patch_wcl(rep, table=table)
    p = rep.player("Enfo")
    for data_type in ("Buffs", "DamageTaken", "Healing", "EnemyDebuffs"):
        text = await tables.breakdown(rep, data_type, pulls=[13], player=p.name)
        assert text.endswith("nothing recorded")
    end = {"endTime": (await rep.decided_cuts([rep.fight(13)]))[rep.fight(13)["id"]]}
    assert asked == [("Buffs", {"targetID": p.id}),
                     ("DamageTaken", {"sourceID": p.id, "hostilityType": "Friendlies", **end}),
                     ("Healing", {"sourceID": p.id, **end}),
                     ("Debuffs", {"hostilityType": "Enemies", "filterExpression": 'source.name = "Enfo"'})]
    asked.clear()
    text = await tables.breakdown(rep, "Healing", pulls=[13], player=p.name, whole_pull=True)
    assert asked == [("Healing", {"sourceID": p.id})] and "decided" not in text



async def test_healing_on_one_kill_gives_each_players_spec_and_parse(rep, patch_wcl):
    """By player on one kill, healing and damage rows carry the spec and the parse (the test night's players are all
    Restoration Shamans: healing), so players of different specs are compared by their parses; several pulls carry
    the spec only."""
    f = next(f for f in rep.fights if f["kill"])
    boss, pull = rep.boss_key(f), rep.pull_no(f)
    ranked, unranked = list(rep.pull_players(f).values())[:2]
    chars = [{"name": ranked.name, "rankPercent": 77}, {"name": unranked.name, "rankPercent": "-"}]
    entries = [{"name": p.name, "id": p.id, "type": "Shaman", "total": 1000 - k}
               for k, p in enumerate((ranked, unranked))]
    patch_wcl(rep, rankings=lambda code, fight_id, metric: [{"roles": {"healers": {"characters": chars}}}],
              table=lambda code, fight_id, data_type, **filters: {"entries": entries, "totalTime": 416000})
    text = await tables.breakdown(rep, "Healing", pulls=[pull], boss=boss)
    assert f"{ranked.label}: " in text and "parse 77th percentile of the spec" in text
    assert "compare players by these, not by the totals across specs" in text
    assert text.count(", parse ") == 1  # one not ranked: no parse
    every = await tables.breakdown(rep, "Healing", boss=boss)
    assert ", parse " not in every and f"{ranked.label}: " in every


async def test_an_ability_given_by_id_matches_interrupts_by_its_name(rep, patch_wcl):
    """Interrupts and dispels tables are matched by the ability's name, so an id ([id] in pull_summary) is read as
    the name the report gives it."""
    rep.abilities[990001] = "Visceral Burst"
    cast = {"name": "Visceral Burst", "spellsBegun": 3, "spellsInterrupted": 2, "spellsCompleted": 1,
            "details": [{"name": "Enfo", "total": 2}]}
    patch_wcl(rep, table=lambda *_, **__: {"entries": [{"entries": [cast, {**cast, "name": "Other"}]}]})
    text = await tables.breakdown(rep, "Interrupts", pulls=[13], ability="990001")
    assert "Visceral Burst only" in text and "Visceral Burst: 3 began, 2 kicked, 1 went through" in text
    assert "Other" not in text
