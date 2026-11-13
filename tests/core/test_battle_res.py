"""A pull's battle res (report/battle_res.py): the raid's charges by the game's rule, counted from the res taken in
the log, and who could cast one."""
from wipebot.core import knowledge
from wipebot.core.knowledge import spells
from wipebot.core.report import battle_res as br
from wipebot.core.report import fight
from wipebot.core.report.battle_res import BattleRes, Res


def _res(at_s: float, taken: bool = True) -> Res:
    return Res(at_s * 1000, "Rebirth", "Mirelle", "Torvek", taken)


def test_a_raid_of_twenty_has_one_charge_at_the_pull_and_one_more_every_four_and_a_half_minutes():
    none = BattleRes(20, [])
    assert none.every_ms == 270_000
    assert [none.charges(s * 1000) for s in (0, 269, 270, 540)] == [1, 1, 2, 3]
    assert BattleRes(30, []).every_ms == 180_000  # 90 minutes over the raid's players


def test_a_res_spends_a_charge_when_taken_and_none_when_not():
    res = BattleRes(20, [_res(60, taken=False), _res(101)])
    assert res.charges(100_000) == 1  # the one not taken spent nothing
    assert res.charges(101_000) == 1 and res.charges(101_000, after=True) == 0
    assert res.charges(271_000) == 1 and res.next_at(101_000) == 270_000  # the next comes on the pull's clock
    assert res.fits


def test_more_res_taken_than_the_rule_gives_does_not_fit():
    assert not BattleRes(20, [_res(30), _res(60)]).fits
    assert BattleRes(20, [_res(30), _res(271)]).fits


def _pull(rep, patch_wcl, res: list[tuple[float, int, int]], deaths: list[tuple[float, int]], cast_by: int | None):
    """Pull 13 with these deaths (s, who) and resurrect events (s, by, on); `cast_by` casts right after each res
    (the sign of life that shows it was taken)."""
    f = rep.fight(13)
    t0, gid = f["startTime"], next(iter(rep.abilities))
    events = rep.wcl.events

    async def answer(code, fight_id, data_type, **filters):
        if data_type == "Deaths" and not filters:
            return [{"timestamp": t0 + s * 1000, "targetID": who, "killingAbilityGameID": gid, "fight": f["id"]}
                    for s, who in deaths]
        if data_type == "All":
            return [{"timestamp": t0 + s * 1000, "type": "resurrect", "sourceID": by, "targetID": on,
                     "abilityGameID": gid, "fight": f["id"]} for s, by, on in res]
        return await events(code, fight_id, data_type, **filters)

    async def events_many(code, items, data_type, **filters):
        return [[{"type": "cast", "sourceID": cast_by, "timestamp": t0 + s * 1000 + 2000, "abilityGameID": 1}
                 for s, _, on in res if on == cast_by] for _ in items]
    patch_wcl(rep, events=answer, events_many=events_many)
    return f


async def test_a_res_cast_again_and_again_on_one_player_is_one_try(rep, patch_wcl):
    f = rep.fight(13)
    a, b = f["friendlyPlayers"][:2]
    f = _pull(rep, patch_wcl, [(30, a, b), (32, a, b), (34, a, b)], [(20, b)], cast_by=None)
    found = await br.battle_res(rep, f)
    assert [(r.at_ms, r.taken, r.casts) for r in found.cast] == [(30_000, False, 3)]
    assert found.charges(40_000) == 1  # nothing spent


async def test_the_summary_says_what_was_ready_at_each_death_and_who_could_cast(rep, patch_wcl, fake_knowledge):
    f = rep.fight(13)
    a, b, c = f["friendlyPlayers"][:3]
    fake_knowledge(battle_res_classes={"Shaman": "Ancestral Call"})  # the fake night: everyone a shaman
    f = _pull(rep, patch_wcl, [(30, a, b)], [(20, b), (50, c)], cast_by=b)
    lines = await br.battle_res_lines(rep, f, await rep.deaths(f))
    text = "\n".join(lines)
    raid = await rep.raid_size(f)
    assert "1 charge at the pull and 1 more every 4:30 (the raid's 90 min over its 20 players)" in lines[0]
    assert f"Taken: 0:30 {rep.ability(next(iter(rep.abilities)))} by {rep.names[a]} on {rep.names[b]} (0 left" in text
    assert "A charge was ready at 1 of the 2 deaths above" in text
    assert f"0:20 {rep.names[b]} ({raid - 1}), back at 0:3" in text  # everyone else could cast
    assert rep.names[c] not in lines[-1]  # none ready at 0:50
    timeline = await fight.pull_timeline(rep, 13)
    assert "(taken; the raid's charges after it: 0 left" in timeline


async def test_no_lines_for_a_pull_nobody_died_in_and_the_classes_unavailable_is_said(rep, patch_wcl, fake_knowledge):
    f = _pull(rep, patch_wcl, [], [], cast_by=None)
    assert await br.battle_res_lines(rep, f, []) == []
    who = f["friendlyPlayers"][0]
    f = _pull(rep, patch_wcl, [], [(20, who)], cast_by=None)
    rep._deaths.clear()
    rep._kept.clear()

    async def down():
        raise knowledge.KnowledgeError("Wowhead is down")
    fake_knowledge(battle_res_classes=down)
    lines = await br.battle_res_lines(rep, f, await rep.deaths(f))
    assert lines[1] == "None was taken." and lines[2] == "Who could cast one: unavailable (Wowhead is down)."


async def test_the_classes_with_a_battle_res_are_the_ones_wowhead_requires_for_its_spells(monkeypatch):
    tips = {20484: "Rebirth 2 sec cast 10 min cooldown Requires Druid Requires level 19 Returns the spirit",
            61999: "Raise Ally Instant 10 min cooldown Requires Death Knight Requires level 19 Pours dark",
            20707: "Soulstone was removed"}

    async def tooltip(spell_id: int) -> spells.Spell:
        if spell_id not in tips:
            raise spells.NotFound("no such spell")
        return spells.Spell(spell_id, tips[spell_id].split(" was ")[0].split(" 2 sec")[0].split(" Instant")[0],
                            tips[spell_id], "")
    monkeypatch.setattr(spells, "tooltip", tooltip)
    monkeypatch.setattr(spells, "_battle_res", spells.Memo())
    assert await spells.battle_res_classes() == {"Druid": "Rebirth", "DeathKnight": "Raise Ally"}
