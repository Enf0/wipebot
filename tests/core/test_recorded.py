"""The published recordings hold only what we may publish (tests/README.md): the guard recording applies
(support/recorded.py), run over every row of the tracked recorded.sqlite, and the stand-ins (support/standins.py)."""
import base64
import io
import json
import sqlite3
import zlib

import pytest
from PIL import Image

from tests.support import standins
from tests.support.recorded import RECORDED, _guilds, _not_ours, published_leaks

OURS = 14169  # The Dragonfell Tribe, EU-Kazzak


@pytest.fixture(scope="module")
def rows() -> list[tuple[str, float, bytes]]:
    db = sqlite3.connect(f"file:{RECORDED}?mode=ro&immutable=1", uri=True)
    return db.execute("SELECT key, stored_at, value FROM cache").fetchall()


@pytest.fixture(scope="module")
def answers(rows) -> list[tuple[str, object]]:
    return [(k, json.loads(zlib.decompress(v))) for k, _, v in rows]


def test_the_published_recordings_hold_no_rankings_and_no_other_guilds_report(rows, answers):
    assert published_leaks(rows) == []
    guilds = _guilds([(k, json.dumps(v)) for k, v in answers])  # every report loaded
    assert len(guilds) > 5 and set(guilds.values()) == {OURS}


def test_the_guilds_listings_name_no_player_outside_it(answers):
    members = standins.members(answers)
    listed = [p["name"] for k, v in answers if k.startswith('["wcl"') and any(q in k for q in standins._LISTINGS)
              for p in standins.listed_players(v)]
    assert members and listed
    assert [n for n in listed if n not in members and not standins.is_outsider(n)] == []


def same(key: str, a: object, b: object) -> bool:
    """Whether two answers are the same; an image by its pixels, as each platform's PNG encoder writes other bytes."""
    if json.loads(key)[0] != "wcl-image" or not (isinstance(a, str) and isinstance(b, str)):
        return a == b
    first, second = (Image.open(io.BytesIO(base64.b64decode(x))) for x in (a, b))
    return first.size == second.size and first.convert("RGB").tobytes() == second.convert("RGB").tobytes()


def test_the_guide_sites_answers_and_the_images_are_stand_ins(answers):
    """A stand-in made again from itself is itself; the site's own page or image isn't (its writing gets made up)."""
    seen = standins.Seen(answers)
    replaced = [(k, v) for k, v in answers if standins.replaced(k)]
    assert replaced and [k for k, v in replaced if not same(k, seen.standin(k, v), v)] == []
    for k, v in replaced:
        if json.loads(k)[0] == "wcl-image" and not json.loads(k)[1].startswith("maps/"):
            assert len(Image.open(io.BytesIO(base64.b64decode(v))).convert("RGB").getcolors() or []) == 1, k


# ---- the guard ----

def _ours(key: str, value: object = None, guilds: dict | None = None, listed: frozenset = frozenset()) -> bool:
    return not _not_ours(key, json.dumps(value), guilds or {"KBYT4xXn7VNjDgh8": OURS}, OURS, set(listed))


def test_our_reports_are_ours_and_others_are_not():
    assert _ours(json.dumps(["report", "KBYT4xXn7VNjDgh8", "deaths", 21]))
    assert not _ours(json.dumps(["report", "TheirReport00001", "deaths", 21]))
    assert not _ours(json.dumps(["report", "TheirReport00001", "deaths", 21]), guilds={"TheirReport00001": 99})


def test_a_command_reading_top_parses_is_not_ours_whatever_it_was_asked():
    asked = "https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21"
    assert not _ours(json.dumps(["cli", "warcraft", "cooldown-packet", asked, "--actor-name", "Enfo"]), {})
    assert not _ours(json.dumps(["cli", "lorrgs", "spec-ranking", "shaman-restoration", "sszorak"]), {})
    assert _ours(json.dumps(["cli", "lorrgs", "spec-spells", "shaman-restoration"]), {})


def test_an_answer_naming_another_guilds_report_or_a_ranking_is_not_ours():
    key = json.dumps(["cli", "warcraft", "made-up-command", "x"])
    assert _ours(key, {"report_url": "https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21"})
    assert not _ours(key, {"samples": [{"report_id": "TheirReport00001"}]})
    assert not _ours(key, {"link": "https://www.warcraftlogs.com/reports/TheirReport00001#fight=3"})
    assert not _ours(key, {"data": [{"name": "Enfo", "rankPercent": 97.5}]})
    assert not _ours(key, {"zoneRanking": {"progress": {"worldRank": {"number": 2464}}}})
    assert _ours(key, {"talentTree": [{"id": 1, "rank": 1}]})  # a talent's rank is no ranking


def test_a_report_our_guilds_listing_names_is_ours():
    key = json.dumps(["cli", "warcraft", "made-up-command", "x"])
    assert _ours(key, {"code": "JKMYjNLkFcgHG9wd"}, listed=frozenset({"JKMYjNLkFcgHG9wd"}))
    assert not _ours(key, {"code": "JKMYjNLkFcgHG9wd"})


def test_raidbots_files_and_character_pages_are_not_ours():
    assert not _ours(json.dumps(["raidbots", "talents.json"]))
    assert not _ours(json.dumps(["wcl-character", "enfo", "kazzak", "eu"]))
    assert not _ours(json.dumps(["cli", "raiderio", "character", "eu", "kazzak", "Enfo"]))


# ---- the stand-ins ----

def test_made_up_text_keeps_markup_links_and_dates_and_makes_up_the_words():
    text = "Use [spell=98008] before <b>Tempest</b> lands, see https://example.org/x on October 5, 2026 for more."
    made = standins.made_up_text(text, "key")
    assert made == standins.made_up_text(text, "key") != standins.made_up_text(text, "other key")
    for kept in ("[spell=98008]", "<b>", "</b>", "https://example.org/x", "October 5, 2026"):
        assert kept in made
    assert not {"Use", "before", "Tempest", "lands", "more"} & set(made.replace(".", " ").split())
    assert standins.made_up_text(made, "key") == made  # made again from itself: the same


def test_a_guides_writing_writers_and_comments_go():
    key = json.dumps(["cli", "method", "guide-full", "https://www.method.gg/guides/x"])
    page = {"title": "A long title that stays as it is, since pages are found by it",
            "body": "This boss is about standing in the right place at the right time, every time.",
            "author": {"name": "Someone", "bio": "Writes guides."}, "comments": {"count": 3, "items": ["Nice"]}}
    out = standins.guide(key, page, {}, lambda code: None)
    assert out["title"] == page["title"] and out["body"] != page["body"]
    assert out["author"] == {"name": "", "bio": ""} and out["comments"] == {"count": 0, "items": []}


def test_a_tooltip_keeps_only_its_facts():
    key = json.dumps(["cli", "wowhead", "entity", "spell", "98008"])
    text = "Summons a totem for 6 sec that reduces damage taken by 10% and lore nobody needs. 3 min cooldown"
    out = standins.guide(key, {"entity": {"name": "Spirit Link Totem"}, "tooltip": {"html": text, "text": text},
                               "comments": {"count": 9, "top": ["a comment"]}}, {}, lambda code: None)
    tip = out["tooltip"]["text"]
    assert tip.startswith("Spirit Link Totem. 3 min cooldown") and "lore" not in tip
    assert out["comments"]["top"] == []


def test_an_image_is_a_flat_colour_of_the_same_size():
    real = Image.new("RGB", (40, 30))
    real.putpixel((3, 4), (255, 0, 0))
    buf = io.BytesIO()
    real.save(buf, format="PNG")
    made = standins.image(json.dumps(["wcl-image", "icons/abilities/x.png"]), base64.b64encode(buf.getvalue()).decode())
    img = Image.open(io.BytesIO(base64.b64decode(made)))
    assert img.size == (40, 30) and len(img.convert("RGB").getcolors() or []) == 1


def test_players_outside_the_guild_get_a_made_up_name_and_the_guilds_rank_goes():
    members = json.dumps(["wcl", "query { guildData { guild(id: $guild) { members(limit: 100) } } }", {"guild": OURS}])
    attendance = json.dumps(["wcl", "query { guildData { guild(id: $guild) { attendance(zoneID: $zone) } } }",
                             {"guild": OURS}])
    log = {"guildData": {"guild": {"attendance": {"data": [{"code": "x", "players": [
        {"name": "Enfo"}, {"name": "Strangerone"}, {"name": "Strangertwo"}]}]}}}}
    seen = standins.Seen([(members, {"guildData": {"guild": {"members": {"data": [{"name": "Enfo"}]}}}})])
    names = [p["name"] for p in standins.listed_players(seen.standin(attendance, log))]
    assert names[0] == "Enfo" and all(standins.is_outsider(n) for n in names[1:]) and len(set(names)) == 3
    assert seen.standin(attendance, seen.standin(attendance, log)) == seen.standin(attendance, log)
    guild = {"id": OURS, "name": "The Dragonfell Tribe", "zoneRanking": {"progress": {}}}
    assert seen.standin(json.dumps(["wcl-guild", "the dragonfell tribe", "kazzak", "eu"]), guild) == {
        "id": OURS, "name": "The Dragonfell Tribe"}


def test_stand_ins_are_made_afresh_only_for_the_sites_and_images():
    assert standins.replaced(json.dumps(["cli", "wowhead", "entity", "spell", "1"]))
    assert standins.replaced(json.dumps(["wcl-image", "maps/1.png"]))
    assert not standins.replaced(json.dumps(["cli", "lorrgs", "spec-spells", "shaman-restoration"]))
    assert not standins.replaced(json.dumps(["report", "KBYT4xXn7VNjDgh8", "deaths", 21]))
