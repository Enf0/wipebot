from wipebot.core.knowledge.guides import sections, subpages, wowhead_text


def test_wowhead_markup_becomes_text():
    assert wowhead_text("[b]x[/b] [spell=5]\n\n\n[ul][li]a[/li][li]b[/li][/ul]", {5: "Dig In"}) == \
        "x Dig In\n\n- a\n- b"
    assert wowhead_text("[html]<li>A <a href=\"/spell=1\">[B]</a> &amp; <!--x-->C</li>[/html]") == "- A B & C"


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
