from wipebot.discord.checks import unsourced, warnings

SUMMARY = """**Sszorak** (Mythic) · pull 13 · wipe at **62.59%** · 2:42
1. 0:48 Brightleaf (Balance Druid) - Ula'tek's Presence [1285965] (19 alive)
The boss gained Unbound Ferocity at 2:32; 16 alive. Damage 160,312 per second, 72nd percentile.
  4:23 Plague Wave from Vashnik: 594.56k -> 37% hp; raid damage taken 879.77k/s at 4:25; Virulence x24"""


def test_numbers_from_the_tools_are_grounded():
    answer = ("**[Pull 13](https://www.warcraftlogs.com/reports/X#fight=21), 62.6%:** enrage at 2:31 with 16 alive; "
              "Brightleaf died at 0:48 ([Presence](https://www.wowhead.com/spell=1285965)). About 160k DPS, "
              "72nd percentile, 63%. 3 bullets. A 594k hit; about 0.8M/s; Virulence (24); hits of 590–600k.")
    assert unsourced(answer, [SUMMARY]) == []


def test_numbers_no_tool_gave_are_listed():
    assert unsourced("The enrage came at 2:45 at 55.5%, after a 612k hit and 1.4M/s; 17 alive.",
                     [SUMMARY]) == ["2:45", "55.5%", "612k", "1.4M"]


def test_norwegian_decimals_and_dates():
    # 187,6k is 187.6k, not 187 and 6k; 40 % is 40%; 21.09 is a date, not 21.09
    answer = "Du gjorde 187,6k dps, 1,63M i alt (40 % overheal) på Nymrissa 21.09, mot 1,500 hits."
    assert unsourced(answer, ["187.57k/s, 1.63M, overheal 40%, 1500 hits"]) == []
    assert unsourced("Killen 07.10. Boss på 68% ved P2, mot 72 % hos oss.", ["P2: health 72/68"]) == []
    assert unsourced("Du gjorde 191,2k dps.", ["187.57k/s"]) == ["191,2k"]


def test_round_times_and_bare_spell_ids():
    # a time on a round ten seconds is a rounded one; a spell id the tool gave in brackets may come bare
    assert unsourced("It slipped after 4:30, on a 2:30 pull (spell id 1292348).",
                     ["went through at 4:34; decided at 2:32; Eternal Venom [1292348]"]) == []
    assert unsourced("They lost it around 2:42.", ["  2:42: on 18: Sonyosh, Cptola"]) == []
    assert unsourced("It slipped after 4:20, at 4:31.", ["went through at 4:34"]) == ["4:20", "4:31"]


def test_warnings():
    assert warnings("Both guides say to lust on the pull.", []) == ['credits a source: "guides say"']
