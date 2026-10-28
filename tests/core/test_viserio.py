"""Viserio Cooldowns (viserio.py): a guild's raid nights to come read with its group's key, kept in memory and asked
again with their ETag. Viserio is faked, with made-up players."""
import datetime

import httpx
import pytest

from wipebot.core import viserio
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError

GROUP = {"groupId": "g1", "name": "Lanterns", "guild": {"region": "eu", "realm": "argent-dawn",
                                                        "name": "the copper lanterns"}}
TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
LATER = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()


def player(name: str, role: str, spec: str, cls: str, shown: str = "") -> dict:
    return {"characterName": name, "displayName": shown or name, "role": role, "playerSpec": spec,
            "playerClass": cls}


ROWAN, BRIGHT = player("Rowan", "tank", "Blood", "Death Knight"), player("Brightleaf", "healer", "Holy", "Paladin")
TORVEK = player("Torvek", "ranged", "Beast Mastery", "Hunter", shown="Tor")
SETUP = {"name": "Setup 1", "difficulty": "Mythic", "eventRef": {"eventId": "e1"}, "inSetupEntries": [ROWAN, BRIGHT],
         "benchEntries": [TORVEK],
         "bossAssignments": [
             {"bossName": "The Coiled Altar", "enabled": True, "inEntries": [BRIGHT, ROWAN], "outEntries": [TORVEK]},
             {"bossName": "Sszorak", "enabled": False, "inEntries": [], "outEntries": []}]}
EVENTS = {"data": [
    {"eventId": "e1", "name": "Raid", "date": TOMORROW, "startTime": "20:00", "endTime": "23:00",
     "difficulty": "Mythic", "signups": [
         {"displayName": "Rowan", "status": "present", "responded": True, "mainCharacter": {"name": "Rowan"}},
         {"displayName": "Tor", "status": "absent", "responded": True, "mainCharacter": {"name": "Torvek"}},
         {"displayName": "Brightleaf", "status": "present", "responded": False}]},
    {"eventId": "e2", "name": "Far off", "date": LATER, "startTime": "20:00", "endTime": "23:00", "signups": []}]}
NOTES = {"data": [  # metadata only: the API gives no note's text
    {"noteId": "n1", "bossName": "Sszorak", "difficulty": "Mythic", "updatedAt": "2026-10-20T19:00:00.000Z"},
    {"noteId": "n2", "bossName": "Sszorak", "difficulty": "Heroic", "updatedAt": "2026-10-22T19:00:00.000Z"},
    {"noteId": "n3", "bossName": "The Coiled Altar", "difficulty": "Mythic", "updatedAt": "2026-10-25T19:00:00.000Z"}],
    "page": {}}


ROSTER = {"members": [
    {"displayName": "Tor", "mainCharacter": "torvek-kazzak", "characters": [
        {"playerId": "torvek-kazzak", "name": "Torvek", "class": "Hunter", "spec": "Beast Mastery"},
        {"playerId": "torvekdk-kazzak", "name": "Torvekdk", "class": "Death Knight", "spec": "Blood"},
        {"playerId": "tor-kazzak", "name": "Tor", "class": "Warrior", "spec": "Arms"}]},
    {"displayName": "Bright", "mainCharacter": "brightleaf-kazzak", "characters": [
        {"playerId": "brightleaf-kazzak", "name": "Brightleaf", "class": "Paladin", "spec": "Holy"}]}]}


@pytest.fixture
def api(monkeypatch) -> list[tuple[str, str]]:
    """Viserio answering the group, calendar, reset and setup; the (path, If-None-Match) of each request."""
    asked: list[tuple[str, str]] = []
    real = httpx.AsyncClient
    answers = {"/v1/groups": {"data": [GROUP]}, "/v1/groups/g1/calendar-events": EVENTS,
               "/v1/groups/g1/latest-reset": {"setups": [{"setupId": 1}]},
               "/v1/groups/g1/latest-reset/setups/1/full": SETUP, "/v1/groups/g1/roster": ROSTER,
               "/v1/groups/g1/notes": NOTES}

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer key-1"
        asked.append((request.url.path, request.headers.get("If-None-Match", "")))
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json=answers[request.url.path], headers={"ETag": '"v1"'})
    monkeypatch.setattr(viserio.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    viserio._answers.clear()
    return asked


async def test_the_nights_to_come_with_sign_ups_and_who_is_in_per_boss(api):
    text = viserio.nights_text(*await viserio.upcoming("key-1"))
    assert "Far off" not in text  # past the days ahead
    assert "Sign-ups: 2 present; absent 1: Torvek (1 of them haven't answered" in text
    assert ("The Coiled Altar: in 2 (18 short of Mythic's 20: not planned in full yet): tank 1: Rowan (Blood Death "
            "Knight); healer 1: Brightleaf (Holy Paladin)") in text
    assert "Out: Torvek (Beast Mastery Hunter, Tor)" in text and "Bench: Torvek" in text
    assert "Sszorak" not in text  # not planned
    assert "bosses planned: not Sszorak" in viserio.nights_text(*await viserio.upcoming("key-1"), boss="Sszorak")


async def test_answers_are_reused_then_asked_again_with_their_etag(api, monkeypatch):
    await viserio.upcoming("key-1")
    await viserio.upcoming("key-1")
    assert len(api) == 4 and all(not etag for _, etag in api)  # the second time from memory
    monkeypatch.setattr(viserio, "FRESH_S", 0)
    assert "Rowan" in viserio.nights_text(*await viserio.upcoming("key-1"))
    assert [etag for _, etag in api[4:]] == ['"v1"'] * 3  # 304s: the answers kept (the group's for the hour)


async def test_a_key_is_saved_only_for_its_own_guild(api, tmp_path):
    book = Notebook(tmp_path / "notes.sqlite")
    with pytest.raises(viserio.ViserioError, match="the key is for Lanterns"):
        await viserio.connect(book, 1, "key-1", ("Other Guild", "kazzak", "eu"), "Rowan")
    assert book.viserio_key(1) is None
    g = await viserio.connect(book, 1, " key-1 ", ("The Copper Lanterns", "Argent Dawn", "EU"), "Rowan")
    assert g.name == "Lanterns" and (book.viserio_key(1) or ())[:3] == ("key-1", "Lanterns", "Rowan")
    book.drop_viserio_key(1)
    assert book.viserio_key(1) is None


@pytest.mark.parametrize("status, message", [(401, "refused the key"), (429, "try again in 30 seconds")])
async def test_viserio_saying_no_says_why(monkeypatch, status, message):
    real = httpx.AsyncClient
    monkeypatch.setattr(viserio.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, headers={"Retry-After": "30"})), **kw))
    viserio._answers.clear()
    with pytest.raises(viserio.ViserioError, match=message):
        await viserio.group("key-2")


async def test_the_roster_gives_every_character_and_each_players_main(api):
    assert await viserio.roster_specs("key-1") == {
        "Torvek": ("Hunter", "BeastMastery"), "Torvekdk": ("DeathKnight", "Blood"),
        "Tor": ("Warrior", "Arms"),  # a character of that name wins over the player's roster name
        "Brightleaf": ("Paladin", "Holy"), "Bright": ("Paladin", "Holy")}
    await viserio.roster_specs("key-1")
    assert [path for path, _ in api] == ["/v1/groups", "/v1/groups/g1/roster"]  # kept for the hour


async def test_a_notes_last_change_is_its_bosss_newest_on_the_difficulty_asked(api):
    def at(iso: str) -> float:
        return datetime.datetime.fromisoformat(iso).timestamp()
    assert await viserio.note_changed("key-1", "Sszorak", "Mythic") == at("2026-10-20T19:00:00Z")
    assert await viserio.note_changed("key-1", "Sszorak") == at("2026-10-22T19:00:00Z")  # any difficulty
    assert await viserio.note_changed("key-1", "Sszorak", "Normal") == at("2026-10-22T19:00:00Z")  # none on it
    assert await viserio.note_changed("key-1", "Coiled Altar", "Mythic") == at("2026-10-25T19:00:00Z")
    assert await viserio.note_changed("key-1", "Vashnik") is None


def test_without_a_key_the_answer_says_who_connects_one():
    assert "an officer connects the group's API key" in str(viserio.no_key("The Copper Lanterns"))
    assert isinstance(viserio.no_key("x"), FactError)
