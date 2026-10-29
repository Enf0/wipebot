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


async def test_a_window_asks_each_pull_from_its_start_to_its_end_or_where_the_wipe_was_decided(rep, patch_wcl):
    """start and end are m:ss of each pull: a pull's table is asked over that window, cut where its wipe was decided
    when that came first (and said so); the totals are per second of the window. A pull that never got to the window
    is left out, and a window no pull got to is an error saying why."""
    asked = []

    def table(code, fight_ids, data_type, **filters):
        asked.append(filters)
        return {"entries": [{"name": "Enfo", "total": 5000}, {"name": "Scopidruid", "total": 3000}]}
    patch_wcl(rep, table=table)
    f = rep.fight(13)
    decided = (await rep.decided_cuts([f]))[f["id"]] - f["startTime"]
    text = await tables.breakdown(rep, "DamageDone", pulls=[13], start="0:10", end="0:20")
    assert asked == [{"startTime": f["startTime"] + 10_000, "endTime": f["startTime"] + 20_000}]
    assert "0:10-0:20, by player (0:10 of combat; total 8.00k, 800/s)" in text and "decided" not in text
    asked.clear()
    lo = decided - 5_000
    text = await tables.breakdown(rep, "DamageDone", pulls=[13], start=str(lo / 1000), end=str(lo / 1000 + 60))
    assert asked == [{"startTime": f["startTime"] + lo, "endTime": f["startTime"] + decided}]
    assert "when the wipe was decided" in text and "(0:05 of combat" in text
    with pytest.raises(FactError, match="never got to .*: its wipe was decided at"):
        await tables.breakdown(rep, "DamageDone", pulls=[13], start=str(decided / 1000 + 1))
    with pytest.raises(FactError, match="start and end work for DamageDone"):
        await tables.breakdown(rep, "Buffs", pulls=[13], start="0:10")


def test_a_composite_damage_or_healing_row_is_split_by_name_and_its_parts_of_one_name_added_up():
    from wipebot.core.report.tables import _amount_rows

    holy_shock = {"name": "Holy Shock", "composite": True, "total": 9, "subentries": [
        {"name": "Holy Shock", "total": 4, "hitCount": 3}, {"name": "Holy Shock", "total": 2, "hitCount": 2},
        {"name": "Overflowing Light", "total": 3, "hitCount": 5}]}
    folio = {"name": "Omnium Folio", "composite": True, "total": 3, "subentries": [
        {"name": "Rune of Lingering", "total": 1}, {"name": "Rune of Unleashed Fire", "total": 2}]}
    trinket = {"name": "Soulcoiler Ritual Vessel", "composite": True, "total": 5,
               "subentries": [{"name": "Soulcoil Barrier", "total": 5}]}
    rows = {r["name"]: r for r in _amount_rows([holy_shock, folio, trinket])}
    assert rows["Holy Shock"]["total"] == 6 and rows["Holy Shock"]["hitCount"] == 5
    assert rows["Overflowing Light"]["total"] == 3
    assert rows["Rune of Lingering (Omnium Folio)"]["total"] == 1
    assert rows["Soulcoiler Ritual Vessel"]["total"] == 5
