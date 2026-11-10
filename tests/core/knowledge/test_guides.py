from wipebot.core.knowledge.guides import sections, subpages, wowhead_text


def test_wowhead_markup_becomes_text():
    assert wowhead_text("[b]x[/b] [spell=5]\n\n\n[ul][li]a[/li][li]b[/li][/ul]", {5: "Dig In"}) == \
        "x Dig In\n\n- a\n- b"
    assert wowhead_text("[html]<li>A <a href=\"/spell=1\">[B]</a> &amp; <!--x-->C</li>[/html]") == "- A B & C"


# The dungeon journal of a Wowhead boss guide, as its markup is: the words are made up, the shape is the page's.
JOURNAL = ("[html]<ul>\n<li><div><span><b> <a target=\"_blank\" href=\"https://www.wowhead.com/spell=11\">Gloom Aura"
           "</a></b></span>: The boss glows, inflicting <!--pts1:0:0:12-->5<!----> Shadow damage every 2 sec to all "
           "players within <!--pts2:0:0:12-->40<!----> yards and reducing healing received by "
           "<!--pts3:0:0:12-->50<!---->%.</div></li>\n"
           "<li><div><span><b> Dread Walk</b></span>: <a href=\"/spell=13/dread-walk\" target=\"_blank\">The boss "
           "possesses 3 players, causing them to absorb <!--pts1:0:0:13-->284196<!----> damage.<br /><br />Upon "
           "removal, [multiple][<!--pts3:0:0:13-->2<!---->] <span style=\"color:#FFFFFF\">Shades</span> emerge, "
           "inflicting <!--pts1:0:0:14-->100<!----> Nature and 617939 Physical damage, then 125011 Fire or Frost "
           "damage, and absorbing the next (<!--pts1:0:0:15-->5<!----> * 8) healing.</a> (Mythic)</div></li>\n"
           "</ul>[/html]")


def test_the_journals_amounts_are_left_out_and_everything_else_stays():
    """Wowhead's page gives most journal amounts unscaled (5 Shadow damage for 192,517), marked no differently from
    the right ones: none is printed. Seconds, yards, counts and percentages are no amounts."""
    assert wowhead_text(JOURNAL, amounts=False).splitlines() == [
        "- Gloom Aura: The boss glows, inflicting Shadow damage every 2 sec to all players within 40 yards and "
        "reducing healing received by 50%.",
        "",
        "- Dread Walk: The boss possesses 3 players, causing them to absorb damage.",
        "",
        "Upon removal, multiple or 2 Shades emerge, inflicting Nature and Physical damage, then Fire or Frost damage, "
        "and absorbing the next healing. (Mythic)"]
    assert "inflicting 5 Shadow damage" in wowhead_text(JOURNAL)  # another page's island keeps its numbers
    plain = "[ul][li]Deals 500000 Nature damage to the tank.[/li][/ul]"  # a writer's own number, not the journal's
    assert wowhead_text(plain, amounts=False) == "- Deals 500000 Nature damage to the tank."


def test_method_sections_cover_every_page():
    data = {"pages": [
        {"guide": {"section_title": "Introduction"},
         "article": {"sections": [{"title": "Introduction", "text": "Welcome"}, {"title": "Overview", "text": "x"}]}},
        {"guide": {"section_title": "Playstyle & Rotation"},
         "article": {"sections": [{"title": "Totemic Playstyle", "text": "Use Surging Totem"},
                                  {"title": "Empty", "text": ""}]}},
    ]}
    assert [s["title"] for s in sections(data)] == [
        "Introduction", "Introduction: Overview", "Playstyle & Rotation: Totemic Playstyle"]


def test_method_subpages_skip_the_current_page():
    data = {"navigation": {"items": [{"title": "Introduction", "url": "u1", "active": True},
                                     {"title": "Talents", "url": "u2", "active": False}]}}
    assert subpages("method", data) == [("Talents", "u2")]


def test_a_guide_updated_today_or_later_in_another_time_zone_reads_today(monkeypatch):
    from wipebot.core import clock
    from wipebot.core.knowledge.guides import updated
    monkeypatch.setattr(clock, "now", lambda: 1_791_936_000.0)  # 2026-10-14 00:00 UTC
    assert updated("2026-10-15T09:00:00-05:00") == "last updated 2026-10-15 (today)"
    assert updated("2026-10-04T00:00:00Z") == "last updated 2026-10-04 (10 days ago)"
