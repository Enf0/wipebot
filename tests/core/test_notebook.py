"""The raid notes and plans a guild saved (notebook.py): every version, by guild, readable by the remote server;
and raid_note and raid_plan reading them when given no paste, for the report's guild's members."""
from typing import cast

import pytest

from tests.support import NOTE, PLAN
from wipebot.core import notebook as notebook_module
from wipebot.core import server
from wipebot.core.notebook import Notebook
from wipebot.core.report import FactError, Report

SSZORAK = {"name": "Sszorak", "encounterID": 3420, "startTime": 1_500_000, "difficulty": 5}
DAY = 86400


class Logs:
    """Warcraft Logs as the lookup asks it for the guild's other raid nights."""
    def __init__(self, *nights: tuple[float, str]):
        self.nights = nights  # (start in epoch seconds, boss pulled)
        self.asked: list[float] = []

    async def guild_reports(self, guild_id, since_ms, pulls=False, cache_for=None):
        self.asked.append(since_ms)
        return [{"code": f"n{start:.0f}", "startTime": start * 1000, "endTime": (start + 3 * 3600) * 1000,
                 "bosses": {boss: (20, 0)}, "pulls_by_difficulty": {5: 20}} for start, boss in self.nights
                if start * 1000 >= since_ms]  # as Warcraft Logs: the reports begun since


class Night:
    """A report as the lookup reads it: the guild's, one Sszorak pull at 1,500 s epoch."""
    guild_id: int | None = 14169
    guild_name, start_ms, end_ms, code = "The Copper Lanterns", 0, 3 * 3600_000, "tonight"
    wcl = Logs()

    def fight(self, pull, boss):
        return SSZORAK

    def boss_key(self, f):
        return "Sszorak"

    def boss(self, name):
        if name != "Sszorak":
            raise FactError(f"no {name}")
        return "Sszorak"


def night(guild_id: int | None = 14169, start_s: int = 0, logs: Logs | None = None) -> Report:
    n = Night()
    n.guild_id, n.start_ms, n.end_ms, n.wcl = guild_id, start_s * 1000, (start_s + 3 * 3600) * 1000, logs or Logs()
    return cast(Report, n)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))
    book = Notebook()
    book.save_note(14169, 3420, "Sszorak", "the note", "Rowan", at=1000.0)
    book.save_plan(14169, "Sszorak", "p1", "Spread", "raidstrats-addon-x", "Halvard", at=1200.0)
    book.save_plan(14169, "Vashnik", "p2", "Totems", "raidstrats-addon-y", "Halvard", at=1300.0)
    return book


def signed_in(monkeypatch, guilds: set[int]) -> None:
    """A call on the remote server by a user whose characters are in `guilds`."""
    from mcp.server.auth.middleware import auth_context
    from mcp.server.auth.provider import AccessToken

    remote = server.Remote(guilds=list, user_name=str, guild_ids=lambda subject: guilds if subject == "1" else set())
    monkeypatch.setattr(server, "setup", lambda: server.Setup(remote=remote))
    token = AccessToken(token="t", client_id="c", scopes=[], subject="1")
    monkeypatch.setattr(auth_context, "get_access_token", lambda: token)


async def test_without_a_paste_a_tool_reads_what_the_reports_guild_saved(book):
    note, source = await server._saved_note(night(), None, None, all_pulls=False)
    assert note == "the note" and source.startswith("saved by Rowan")
    every, _ = await server._saved_note(night(), None, None, all_pulls=True)
    assert every == "[saved 1000 by Rowan]\nthe note"
    plans = await server._saved_plans(night(), None, None)
    assert plans == ("[saved 1200 by Halvard] raidstrats-addon-x", "")  # Sszorak's only; pasted, no login



async def test_with_none_saved_and_a_broken_login_the_tool_says_the_login_stopped_working(book, monkeypatch):
    book.save_login(14169, "Enfo-2581", {"access_token": "a", "refresh_token": "r", "expires_in": 3600}, "Rowan")
    book.fetched(14169, "raidstrats.gg no longer accepts the login: an officer connects again")
    monkeypatch.setattr(server.raidstrats, "sync", lambda *a: _done())
    assert book._db is not None
    book._db.execute("DELETE FROM raid_plans")  # the boss's plan was made after the login broke
    book._db.commit()
    with pytest.raises(FactError, match="its plans are fetched from raidstrats.gg as Enfo-2581.*an officer connects "
                                        "again"):
        await server._saved_plans(night(), None, None)


async def _done() -> None:
    pass

async def test_a_member_of_the_reports_guild_reads_its_notes_on_the_remote_server_and_no_one_else(book, monkeypatch):
    signed_in(monkeypatch, {14169})
    assert (await server._saved_note(night(), None, None, all_pulls=False))[0] == "the note"
    signed_in(monkeypatch, {555})
    with pytest.raises(FactError, match="members only"):
        await server._saved_note(night(), None, None, all_pulls=False)
    with pytest.raises(FactError, match="members only"):
        await server._saved_plans(night(), None, None)


async def test_with_nothing_saved_or_a_personal_log_it_asks_for_a_paste(book):
    with pytest.raises(FactError, match="personal log"):
        await server._saved_note(night(None), None, None, all_pulls=False)
    with pytest.raises(FactError, match="none saved for Sszorak that night: ask the user"):
        await server._saved_note(night(555), None, None, all_pulls=False)


async def test_a_note_counts_only_for_the_night_it_was_saved_for(book):
    """Officers change the note each night with who comes, so a pull isn't checked against another night's: one
    saved before the guild's last raid night on the boss, or for the next night (half a day after), isn't used."""
    monday, wednesday, friday = 10 * DAY, 12 * DAY, 14 * DAY
    logs = Logs((monday, "Sszorak"), (wednesday - 4 * 3600, "Vashnik"), (friday, "Sszorak"))
    book.save_note(14169, 3420, "Sszorak", "monday's", "Rowan", at=monday - 3600)
    book.save_note(14169, 3420, "Sszorak", "friday's", "Rowan", at=friday - 3600)
    with pytest.raises(FactError, match="none saved for Sszorak that night \\(only for other nights"):
        await server._saved_note(night(start_s=wednesday, logs=logs), None, None, all_pulls=True)
    assert logs.asked == [(monday - 3600 - 12 * 3600) * 1000]  # from half a day before the version saved last
    book.save_note(14169, 3420, "Sszorak", "monday's, changed in the raid", "Rowan", at=monday + 3600)
    with pytest.raises(FactError, match="only for other nights"):  # monday's log began before it
        await server._saved_note(night(start_s=wednesday, logs=logs), None, None, all_pulls=True)
    book.save_note(14169, 3420, "Sszorak", "for wednesday", "Rowan", at=monday + 6 * 3600)  # after monday's raid
    every, _ = await server._saved_note(night(start_s=wednesday, logs=logs), None, None, all_pulls=True)
    assert "for wednesday" in every and "monday's" not in every and "friday's" not in every
    every, _ = await server._saved_note(night(start_s=monday, logs=logs), None, None, all_pulls=True)
    assert "monday's" in every and "changed in the raid" in every
    assert "for wednesday" in every  # saved 3 h after monday's log ended: a late paste
    assert "the note" not in every and "friday's" not in every
    logs.asked.clear()  # pasted the same day: no need to ask
    assert (await server._saved_note(night(start_s=950, logs=logs), None, None, all_pulls=False))[0] == "the note"
    assert logs.asked == []


def test_notes_are_kept_per_guild_and_boss_with_each_version():
    book = Notebook(":memory:")
    book.save_note(14169, 3420, "Sszorak", "first", "Rowan", at=1000.0)
    book.save_note(14169, 3420, "Sszorak", "second", "Rowan", at=2000.0)
    book.save_note(14169, 3418, "Vashnik", "vashnik", "Halvard", at=1500.0)
    book.save_note(555, 3420, "Sszorak", "another guild's", "X", at=1800.0)
    assert (n := book.note_for(14169, 3420, 1999.0)) and n.note == "first"  # as it was when that pull began
    assert (n := book.note_for(14169, 3420, 2500.0)) and n.note == "second"
    assert (n := book.note_for(14169, 3420, 500.0)) and n.note == "first"  # saved after: the first one saved
    assert "after this pull began" in n.source(500.0) and "after" not in n.source(1500.0)
    assert book.note_for(14169, 9999, 2500.0) is None
    assert [n.note for n in book.notes(14169, 3420)] == ["first", "second"] and book.notes(14169, 9999) == []
    assert [(n.boss, n.note, n.saved_at) for n in book.latest_notes(14169)] == [
        ("Sszorak", "second", 2000.0), ("Vashnik", "vashnik", 1500.0)]


def test_the_remote_server_reads_what_the_bot_saves_and_reads_nothing_when_theres_no_file(tmp_path):
    path = tmp_path / "notes.sqlite"
    reader = Notebook(path, readonly=True)
    assert reader.notes(14169, 3420) == [] and not path.exists()  # nothing saved yet, and nothing made
    Notebook(path).save_plan(14169, "Sszorak", "p1", "Spread", "raidstrats-addon-x", "Halvard", at=1200.0)
    reader = Notebook(path, readonly=True)
    assert [(p.plan, p.saved_by, p.saved_at) for p in reader.plans(14169)] == [
        ("raidstrats-addon-x", "Halvard", 1200.0)]
    Notebook(path).save_plan(14169, "Sszorak", "p1", "Spread", "raidstrats-addon-y", "Rowan", at=2200.0)
    assert [p.saved_by for p in reader.latest_plans(14169)] == ["Rowan"]  # a newer version, seen while open


def test_a_pasted_note_or_plan_is_saved_for_the_guild_and_says_what_was_saved():
    """What an officer's paste saved, whichever app it came through: each boss's note, or each plan (a new version
    when saved before)."""
    book = Notebook(":memory:")
    assert server.save_pasted(book, 14169, NOTE.read_text(encoding="utf-8"), "Rowan", at=1000.0) == (
        "Saved the raid note, for checking pulls against from now on (that night's only, as a note changes with who "
        "comes: the previous one is kept for earlier pulls; "
        'pasted after the pulls it was for? Paste it again with the pull it counts from, such as "from pull 1"):\n'
        "- **Sszorak** (Mythic): 59 timed cooldowns, 28 callouts; Mutilate Soaks (Left, Right); Intermission Orb "
        "Popper (Orb Popper); Virulence Spreads (Spread, Bait Serpent's Fury / Bite, Don't Stack); Virulence trigger "
        "(Trigger)")
    assert [(n.boss, n.saved_by, n.saved_at) for n in book.latest_notes(14169)] == [("Sszorak", "Rowan", 1000.0)]
    plan = PLAN.read_text(encoding="utf-8")
    first = server.save_pasted(book, 14169, plan, "Rowan", at=1000.0)
    assert first.startswith("Saved the raid plan") and "(Sszorak): 16 spots" in first and "a new version" not in first
    assert "(a new version)" in server.save_pasted(book, 14169, plan, "Halvard", at=2000.0)
    assert [p.saved_by for p in book.plans(14169)] == ["Rowan", "Halvard"] and book.plans(555) == []
    with pytest.raises(FactError, match="EncounterID"):
        server.save_pasted(book, 14169, "just some text", "Rowan")


async def test_a_note_pasted_after_the_pulls_it_was_for_counts_from_the_pull_named(rep):
    """An officer who forgot to paste the note before the raid pastes it after, from the pull it was used from: that
    pull and the later ones are checked against it, the earlier ones against the version saved before the night,
    and the next night's against it too."""
    rep.guild_id = 14169
    book, text = Notebook(":memory:"), NOTE.read_text(encoding="utf-8")
    pulls = [(rep.start_ms + f["startTime"]) / 1000 for f in rep.by_boss["Sszorak"]]
    book.save_note(14169, 3420, "Sszorak", "last week's", "Halvard", at=pulls[0] - 86400 * 7)
    said = server.save_pasted(book, 14169, text, "Rowan", from_pull=(rep, 7))
    assert said.startswith("Saved the raid note, for checking pulls against from Sszorak pull 7 of ")
    found = [book.note_for(14169, 3420, at) for at in (pulls[5], pulls[6], pulls[-1], pulls[-1] + 86400 * 7)]
    assert [n and n.saved_by for n in found] == ["Halvard", "Rowan", "Rowan", "Rowan"]
    assert '"from pull 1"' in server.save_pasted(Notebook(":memory:"), 14169, text, "Rowan")  # how, when it's late
    with pytest.raises(FactError, match="Sszorak has pulls 1-33"):
        server.save_pasted(book, 14169, text, "Rowan", from_pull=(rep, 34))
    with pytest.raises(FactError, match="no pull on Vashnik"):  # every boss is checked before anything is saved
        server.save_pasted(book, 14169, text + "\nEncounterID:3418;Difficulty:Mythic;Name:Vashnik\n", "Rowan",
                           from_pull=(rep, 1))
    with pytest.raises(FactError, match="isn't the guild's"):
        server.save_pasted(book, 555, text, "Rowan", from_pull=(rep, 1))
    assert len(book.notes(14169, 3420)) == 2


def test_only_a_guilds_newest_versions_are_kept(monkeypatch):
    """On a server open to anyone, any member saves: a guild keeps its MAX_VERSIONS newest notes, and of plans."""
    monkeypatch.setattr(notebook_module, "MAX_VERSIONS", 3)
    book = Notebook(":memory:")
    for i in range(5):
        book.save_note(14169, 3420, "Sszorak", f"v{i}", "Rowan", at=1000.0 + i)
        book.save_plan(14169, "Sszorak", "p1", "Plan", f"raidstrats-addon-v{i}", "Rowan", at=1000.0 + i)
    book.save_note(555, 3420, "Sszorak", "another guild's", "X", at=900.0)
    assert [n.note for n in book.notes(14169, 3420)] == ["v2", "v3", "v4"]
    assert [p.plan for p in book.plans(14169)] == ["raidstrats-addon-v2", "raidstrats-addon-v3", "raidstrats-addon-v4"]
    assert [n.note for n in book.notes(555, 3420)] == ["another guild's"]


def test_a_note_for_a_boss_not_pulled_yet_is_given_as_it_is(book):
    assert not server._pulled(night(), "Vashnik") and server._pulled(night(), "Sszorak")
    text = server._unpulled_note(night(), "Sszorak")
    assert text.startswith("Sszorak has no pull in this report, so here is the newest raid note")
    assert "saved by Rowan" in text and text.endswith("\nthe note")
    with pytest.raises(FactError, match="Vashnik has no pull in this report, and The Copper Lanterns has no raid note"):
        server._unpulled_note(night(), "Vashnik")
