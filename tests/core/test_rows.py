"""Logged events as rows (report/rows.py): made-up events over the captured night's players and abilities."""
from wipebot.core.report import rows

T0 = 6_330_494  # Sszorak pull 13's start, in the report's ms
BOSS, NALAXD, POMAA, TEMPEST, VIRULENCE = 21, 1, 4, 1287083, (1300089, 1312189)


def _events() -> list[dict]:
    hit = {"type": "damage", "sourceID": BOSS, "abilityGameID": TEMPEST, "fight": 21}
    return [
        {"timestamp": T0 + 15_250, "type": "cast", "sourceID": BOSS, "targetID": -1, "abilityGameID": TEMPEST,
         "fight": 21},
        {**hit, "timestamp": T0 + 16_498, "targetID": NALAXD, "amount": 104_700, "absorbed": 1_060, "overkill": 12_720,
         "hitType": 1, "unmitigatedAmount": 180_000},
        {**hit, "timestamp": T0 + 17_498, "targetID": POMAA, "amount": 20_300, "tick": True},
        {"timestamp": T0 + 18_000, "type": "applydebuffstack", "sourceID": BOSS, "targetID": POMAA,
         "abilityGameID": VIRULENCE[0], "stack": 3, "fight": 21},
        {"timestamp": T0 + 18_001, "type": "applydebuff", "sourceID": BOSS, "sourceInstance": 2, "targetID": POMAA,
         "abilityGameID": VIRULENCE[1], "fight": 21},
        {"timestamp": T0 + 19_000, "type": "death", "sourceID": -1, "targetID": NALAXD, "abilityGameID": 0, "fight": 21,
         "killerID": BOSS, "killingAbilityGameID": TEMPEST},
    ]


def test_a_row_names_the_moment_the_kind_and_who_did_what_to_whom(rep):
    cast, hit, tick, stack, applied, death = (rows.row(rep, e, T0) for e in _events())
    assert cast == "0:15.250 cast: Boss → Tempest"  # no target: the fight itself (-1) isn't one
    assert hit == ("0:16.498 damage: Boss → Tempest → Nalaxd; amount 104700, absorbed 1060, overkill 12720, hitType 1, "
                   "unmitigatedAmount 180000")  # all else the event holds, as logged
    assert tick == "0:17.498 damage: Boss → Tempest → Pomaa; amount 20300, tick yes"
    assert stack == "0:18.000 applydebuffstack: Boss → Virulence → Pomaa; stack 3"
    assert applied == "0:18.001 applydebuff: Boss #2 → Virulence → Pomaa"  # one of several of an enemy
    # a death names who died; what killed them is the event's own, by name
    assert death == "0:19.000 death: Nalaxd; killer Boss, killingAbility Tempest"


def test_a_row_in_short_holds_what_a_raider_reads_off_an_event(rep):
    cast, hit, tick, stack, _, death = (rows.row(rep, e, T0, rows.in_short) for e in _events())
    assert cast == "0:15.250 cast: Boss → Tempest"
    assert hit == "0:16.498 damage: Boss → Tempest → Nalaxd; 104.70k (1.06k absorbed, overkill 12.72k)"
    assert tick == "0:17.498 damage: Boss → Tempest → Pomaa; 20.30k (a tick)"
    assert stack == "0:18.000 applydebuffstack: Boss → Virulence → Pomaa; stack 3"
    assert death == "0:19.000 death: Nalaxd; killing blow Tempest"


def test_an_ability_logged_under_several_ids_is_told_apart_by_its_id(rep):
    """One cast logged under two ids of one name read as the same event twice."""
    events = _events()
    assert rows.several_ids(rep, events) == {"Virulence"}
    listed = rows.listed(rep, events)
    assert "0:18.000 applydebuffstack: Boss → Virulence [1300089] → Pomaa; stack 3" in listed.lines
    assert "0:18.001 applydebuff: Boss #2 → Virulence [1312189] → Pomaa" in listed.lines
    assert listed.lines[-1] == "Ability ids: Tempest [1287083], Virulence [1300089, 1312189]"
    assert listed.where == " in Sszorak pull 13, times from the pull's start" and not listed.left_out


def test_events_are_counted_by_code_and_a_list_cut_to_its_first_rows_says_what_was_left_out(rep):
    events = _events()
    assert rows.counted(rep, events) == ("Counted: 2 damage, 1 cast, 1 applydebuffstack, 1 applydebuff, 1 death; "
                                         "by ability: Tempest 3, Virulence 2; from: Boss 5; on: Pomaa 3, Nalaxd 2.")
    assert not rows.listed(rep, events).lines[0].startswith("Counted")  # few enough to count by eye
    # of each count the most common are named, the rest summed
    on_nine = [{**events[1], "targetID": player} for player in list(rep.names)[:9]]
    assert rows.counted(rep, on_nine).endswith(" and 1 more (1 event).")
    cut = rows.listed(rep, events, most=4)
    assert cut.left_out == 2 and cut.lines[0].startswith("Counted: 2 damage, ") and len(cut.lines) == 7
    assert cut.lines[-2] == "… 2 more events left out, the last at 0:19.000: the counts above are of all 6."
    many = [{**events[1], "timestamp": T0 + n} for n in range(40)]
    by_room = rows.listed(rep, many, room=1_000)
    assert 3 <= 40 - by_room.left_out < 10 and sum(len(line) + 1 for line in by_room.lines) <= 1_000
    # rows that fit the room alone, but not with the counts and the ids under them, are cut too
    size = sum(len(line) + 1 for line in rows.listed(rep, many).lines)
    tight = rows.listed(rep, many, room=size - 50)
    assert tight.left_out and sum(len(line) + 1 for line in tight.lines) <= size - 50
    assert rows.listed(rep, many).lines[0] == ("Counted: 40 damage; by ability: Tempest 40; from: Boss 40; on: "
                                               "Nalaxd 40.")


def test_a_moment_between_pulls_is_in_none(rep):
    assert (rows.pull_at(rep, T0 + 1) or {}).get("id") == 21 and (rows.pull_at(rep, 0, 21) or {}).get("id") == 21
    assert rows.pull_at(rep, T0 - 1) is None and rows.pull_at(rep, 0, 9_999) is None
    outside = rows.listed(rep, [{"timestamp": T0 - 1_000, "type": "cast", "sourceID": NALAXD,
                                 "abilityGameID": TEMPEST}])
    assert outside.where == ", times from the report's start"
