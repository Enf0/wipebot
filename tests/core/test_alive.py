"""A player brought back by a battle res is alive again: in their time alive (base.alive_ms), what's measured per
minute of it and who had a kick ready."""
from wipebot.core.report import adds, casting, coaching, mechanics
from wipebot.core.report.base import alive_ms


def test_time_alive_stops_at_each_death_and_starts_again_at_a_battle_res():
    deaths = [{"targetID": 7, "timestamp": 10_000}, {"targetID": 7, "timestamp": 70_000},
              {"targetID": 8, "timestamp": 5_000}]
    assert alive_ms(deaths, [40_000], 7, 0, 100_000) == 10_000 + 30_000
    assert alive_ms(deaths, [], 7, 0, 100_000) == 10_000  # never brought back
    assert alive_ms(deaths, [40_000], 7, 0, 30_000) == 10_000  # the window ends while they're dead
    assert alive_ms(deaths, [40_000], 7, 50_000, 100_000) == 20_000  # and starts after they were back
    assert alive_ms(deaths, [], 9, 0, 100_000) == 100_000


async def test_a_mechanics_damage_per_minute_alive_counts_the_time_after_a_battle_res(rep, patch_wcl):
    """Four players take the same hit; one died early and was brought back at once: they took it at the same rate
    per minute alive, not several times the others'."""
    f = rep.fight(13)
    t0, boss = f["startTime"], next(iter(rep.bosses))
    ids = f["friendlyPlayers"][:4]
    gid = next(iter(rep.abilities))
    events = rep.wcl.events

    async def answer(code, fight_id, data_type, **filters):
        if data_type == "Deaths" and not filters:
            return [{"timestamp": t0 + 20_000, "targetID": ids[0], "killingAbilityGameID": gid, "fight": f["id"]}]
        if data_type == "All":  # the battle res
            return [{"timestamp": t0 + 21_000, "type": "resurrect", "targetID": ids[0], "fight": f["id"]}]
        if data_type == "DamageTaken":
            return [{"timestamp": t0 + 10_000, "type": "damage", "sourceID": boss, "targetID": i,
                     "abilityGameID": gid, "amount": 1_000_000, "fight": f["id"]} for i in ids]
        return await events(code, fight_id, data_type, **filters)
    patch_wcl(rep, events=answer)
    text = await mechanics.mechanic_outliers(rep, 13)
    line = next(line for line in text.splitlines() if line.startswith(f"  {rep.ability(gid)}"))
    assert all(f"{rep.names[i]} 1.0x" in line for i in ids[:3]), line


async def test_a_player_brought_back_by_a_battle_res_has_their_kick_ready_again(rep, patch_wcl, fake_knowledge):
    """Who had a kick ready as an add's cast went through: not a player dead then, but one dead earlier and brought
    back, with their interrupt off cooldown."""
    f = rep.fight(13)
    t0, (a, b, c) = f["startTime"], f["friendlyPlayers"][:3]
    fake_knowledge(spell_cooldown=15)
    events = rep.wcl.events

    async def answer(code, fight_id, data_type, **filters):
        if data_type == "Interrupts":  # each of them kicked on the boss tonight
            return [{"timestamp": t0, "type": "interrupt", "sourceID": i, "abilityGameID": 1766} for i in (a, b, c)]
        if data_type == "Deaths" and not filters:  # a and b died; a was brought back
            return [{"timestamp": t0 + 10_000, "targetID": a}, {"timestamp": t0 + 20_000, "targetID": b}]
        if data_type == "All":
            return [{"timestamp": t0 + 30_000, "type": "resurrect", "targetID": a, "fight": f["id"]}]
        if data_type == "Casts":
            return []
        return await events(code, fight_id, data_type, **filters)
    patch_wcl(rep, events=answer)
    ready, _ = await adds.kicks_ready(rep, f, [t0 + 25_000, t0 + 40_000])
    assert ready == {t0 + 25_000: [rep.names[c]], t0 + 40_000: sorted([rep.names[a], rep.names[c]])}


async def test_a_fake_cast_after_a_death_doesnt_bring_a_player_back(rep):
    """Warcraft Logs derives a fake cast from a dead Demon Hunter about a second after the death: no sign of life,
    unlike the next real cast after a battle res or a Reincarnation."""
    f = rep.fight(13)
    t0, dur, p = f["startTime"], f["endTime"] - f["startTime"], next(iter(rep.players.values()))
    deaths = [{"targetID": p.id, "timestamp": t0 + 10_000}]
    fake = {"type": "cast", "timestamp": t0 + 11_000, "fake": True}
    real = {"type": "cast", "timestamp": t0 + 15_000}
    assert casting.alive(rep, f, p, deaths, [fake]) == (10.0, t0 + 10_000)
    assert casting.alive(rep, f, p, deaths, [fake, real]) == ((dur - 5_000) / 1000, f["endTime"])
    assert coaching._dead_s(f, deaths, [fake], f["endTime"]) == (dur - 10_000) / 1000
    assert coaching._dead_s(f, deaths, [fake, real], f["endTime"]) == 5.0


async def test_a_player_who_kicks_with_two_spells_has_a_kick_ready_while_either_is_off_cooldown(
        rep, patch_wcl, fake_knowledge):
    """Some players kick with more than one spell (a tank with a shield throw too): one on cooldown leaves them the
    other, so they had a kick ready. Counting only the one they used most read such a tank as not ready."""
    f = rep.fight(13)
    t0, (a, b) = f["startTime"], f["friendlyPlayers"][:2]
    fake_knowledge(spell_cooldown=15)
    events = rep.wcl.events

    async def answer(code, fight_id, data_type, **filters):
        if data_type == "Interrupts":  # a kicked with 1 twice and 2 once on the boss tonight, b with 1 only
            return [{"timestamp": t0, "type": "interrupt", "sourceID": who, "abilityGameID": spell}
                    for who, spell in ((a, 1), (a, 1), (a, 2), (b, 1))]
        if data_type == "Deaths" and not filters or data_type == "All":
            return []
        if data_type == "Casts":  # both pressed 1 at 20 s
            return [{"timestamp": t0 + 20_000, "type": "cast", "sourceID": who, "abilityGameID": 1} for who in (a, b)]
        return await events(code, fight_id, data_type, **filters)
    patch_wcl(rep, events=answer)
    ready, _ = await adds.kicks_ready(rep, f, [t0 + 25_000, t0 + 40_000])
    assert ready == {t0 + 25_000: [rep.names[a]], t0 + 40_000: sorted([rep.names[a], rep.names[b]])}
