"""What the bot keeps in SQLite: settings, raiders, the messages holding a post after a pull, answers
and reactions."""
import json
import sqlite3
import time
from dataclasses import replace

import pytest

from wipebot.discord.host import ToolCall
from wipebot.discord.store import MAX_NOTES, Raider, ServerSettings, Store


@pytest.fixture(autouse=True)
def _notes(tmp_path, monkeypatch):
    """A Store on a file opens the notebook the core's tools read: here one in the test's directory."""
    monkeypatch.setenv("WIPEBOT_NOTES_DB", str(tmp_path / "notes.sqlite"))


def test_store_keeps_settings_per_server(store, settings):
    assert store.settings(1001) is None
    store.save_settings(settings)
    other = ServerSettings(2002, 555, "Other Guild", "tarren-mill", "eu", required_role_id=22)
    store.save_settings(other)
    assert store.settings(1001) == settings and store.settings(2002) == other
    assert sorted(s.discord_guild_id for s in store.all_settings()) == [1001, 2002]
    store.save_settings(replace(settings, required_role_id=11))  # setup again
    assert (s := store.settings(1001)) and s.required_role_id == 11


def test_store_reads_and_updates_a_database_from_older_versions(tmp_path, settings):
    """Their columns: a chat channel, then the posts channel of the answer after each wipe (NOT NULL, so dropped:
    new settings have none) and whether to post it."""
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("""CREATE TABLE server_settings (discord_guild_id INTEGER PRIMARY KEY, wcl_guild_id INTEGER NOT NULL,
                   wcl_guild TEXT NOT NULL, wcl_server TEXT NOT NULL, wcl_region TEXT NOT NULL,
                   posts_channel_id INTEGER NOT NULL, chat_channel_id INTEGER, required_role_id INTEGER,
                   answer_after_wipes INTEGER NOT NULL DEFAULT 1)""")
    old.execute("INSERT INTO server_settings VALUES "
                "(1001, 14169, 'The Copper Lanterns', 'argent-dawn', 'eu', 10, 99, NULL, 1)")
    old.commit()
    old.close()
    store = Store(db)
    assert store.settings(1001) == settings
    store.save_settings(replace(settings, required_role_id=5))
    assert (s := store.settings(1001)) and s.required_role_id == 5
    store.save_settings(ServerSettings(2002, 555, "Other Guild", "tarren-mill", "eu"))
    assert len(Store(db).all_settings()) == 2  # opened again: nothing left to drop


def test_a_database_from_before_the_officer_role_gets_it_unset(tmp_path, settings):
    """Its server settings have no officer role: Manage Server decides until /wipebot setup sets one."""
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE server_settings (discord_guild_id INTEGER PRIMARY KEY, wcl_guild_id INTEGER NOT NULL, "
                "wcl_guild TEXT NOT NULL, wcl_server TEXT NOT NULL, wcl_region TEXT NOT NULL, "
                "required_role_id INTEGER)")
    old.execute("INSERT INTO server_settings VALUES (1001, 14169, 'The Copper Lanterns', 'argent-dawn', 'eu', 111)")
    old.commit()
    old.close()
    s = Store(db).settings(1001)
    assert s and s == replace(settings, required_role_id=111) and s.officer_role_id is None
    Store(db).save_settings(replace(s, officer_role_id=222))
    assert (s := Store(db).settings(1001)) and s.officer_role_id == 222  # opened again: nothing left to add


def _with_settings(db, settings, officer_channel_id: int | None = None) -> None:
    """The server's settings in a database, written as an older version would (with its one officer channel),
    without opening it as a Store."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE server_settings (discord_guild_id INTEGER PRIMARY KEY, wcl_guild_id INTEGER NOT NULL, "
                "wcl_guild TEXT NOT NULL, wcl_server TEXT NOT NULL, wcl_region TEXT NOT NULL, "
                "required_role_id INTEGER, officer_channel_id INTEGER)")
    con.execute("CREATE TABLE naming_channels (discord_guild_id INTEGER, channel_id INTEGER)")
    con.execute("INSERT INTO server_settings VALUES (?, ?, ?, ?, ?, ?, ?)", (
        settings.discord_guild_id, settings.wcl_guild_id, settings.wcl_guild, settings.wcl_server,
        settings.wcl_region, None, officer_channel_id))
    con.commit()
    con.close()


def test_store_remembers_which_pull_a_post_is_about(store):
    store.remember_message(999, "KBYT4xXn7VNjDgh8", 21)
    assert store.pull_for_message(999) == ("KBYT4xXn7VNjDgh8", 21)
    assert store.pull_for_message(1) is None


def test_answers_are_recorded_with_their_reactions(store, monkeypatch):
    call = ToolCall("pull_summary", {"report": "KBYT4xXn7VNjDgh8", "pull": 13}, "Sszorak pull 13 ...", False)
    answer_id = store.record_interaction(
        kind="chat", report="KBYT4xXn7VNjDgh8", question="why did we wipe?", answer="**Enraged.**",
        asker="Rowan", usage={"output_tokens": 12}, thinking=["Check the timeline."], tool_calls=[vars(call)],
        wcl_points={"total": 6, "database": 2, "queries": 2}, messages=[501, 502])
    store.record_interaction(kind="follow", report="KBYT4xXn7VNjDgh8", question="Why?", error="timeout after 120 s")
    row = store._db.execute("SELECT kind, asker, answer, usage, thinking, tool_calls FROM interactions "
                            "WHERE id = ?", (answer_id,)).fetchone()
    assert row[:3] == ("chat", "Rowan", "**Enraged.**")
    assert json.loads(row[3]) == {"output_tokens": 12} and json.loads(row[4]) == ["Check the timeline."]
    assert json.loads(row[5])[0]["args"]["pull"] == 13
    points = store._db.execute("SELECT wcl_points FROM interactions WHERE id = ?", (answer_id,)).fetchone()[0]
    assert json.loads(points) == {"total": 6, "database": 2, "queries": 2}

    assert store.add_reaction(502, 7, "👎") == answer_id
    assert store.add_reaction(999, 7, "👍") is None  # not an answer: not kept
    store.remove_reaction(502, 7, "👎")
    store.add_reaction(501, 8, "👍")
    assert store._db.execute("SELECT message_id, user_id, emoji FROM reactions").fetchall() == [(501, 8, "👍")]

    monkeypatch.setattr("time.time", lambda: 1e12)  # long after: everything is old
    assert store.prune_interactions() == 2
    assert store._db.execute("SELECT count(*) FROM reactions").fetchone() == (0,)
    assert store._db.execute("SELECT count(*) FROM answer_messages").fetchone() == (0,)


def test_store_remembers_raiders_until_they_are_forgotten(store):
    assert store.raider(7) == Raider() and not store.raider(7)
    store.save_raider(7, Raider("Torvek", ["  answer in English  ", ""]))
    assert store.raider(7) == Raider("Torvek", ["answer in English"])
    store.save_raider(7, Raider("Torvek", [f"note {i}" for i in range(MAX_NOTES + 2)]))
    assert store.raider(7).notes == [f"note {i}" for i in range(2, MAX_NOTES + 2)]  # the newest kept
    store.save_raider(7, Raider())
    assert not store.raider(7)


def test_the_remember_tool_changes_what_is_remembered():
    raider = Raider("Torvek", ["answer in English", "don't name me in the channel"])
    assert raider.apply({"forget_note": "NAME ME"}) is None and raider.notes == ["answer in English"]
    assert "No remembered note matches 'shorter'" in (raider.apply({"forget_note": "shorter"}) or "")
    assert raider.apply({"character": "Mirelle", "note": "short answers"}) is None
    assert raider == Raider("Mirelle", ["answer in English", "short answers"])
    assert raider.apply({"forget": True}) is None and not raider


def test_raid_notes_and_plans_kept_here_before_move_to_the_notebook_by_guild(tmp_path, settings):
    """Before the notebook, notes and plans were kept in this database by Discord server."""
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript("""
        CREATE TABLE raid_notes (id INTEGER PRIMARY KEY, discord_guild_id INTEGER NOT NULL,
            encounter_id INTEGER NOT NULL, boss TEXT NOT NULL, note TEXT NOT NULL, saved_by TEXT NOT NULL,
            saved_at REAL NOT NULL);
        CREATE TABLE raid_plans (id INTEGER PRIMARY KEY, discord_guild_id INTEGER NOT NULL, boss TEXT NOT NULL,
            plan_id TEXT NOT NULL, name TEXT NOT NULL, plan TEXT NOT NULL, saved_by TEXT NOT NULL,
            saved_at REAL NOT NULL);
        INSERT INTO raid_notes VALUES (1, 1001, 3420, 'Sszorak', 'first', 'Rowan', 1000.0),
                                      (2, 1001, 3420, 'Sszorak', 'second', 'Rowan', 2000.0),
                                      (3, 3003, 3420, 'Sszorak', 'a server no longer set up', 'X', 1500.0);
        INSERT INTO raid_plans VALUES (1, 1001, 'Sszorak', 'p1', 'Spread', 'raidstrats-addon-x', 'Halvard', 1200.0);
    """)
    old.commit()
    old.close()
    _with_settings(db, settings)
    store = Store(db)  # opened again, with the settings there
    assert [n.note for n in store.notebook.notes(14169, 3420)] == ["first", "second"]
    assert [(p.name, p.saved_by, p.saved_at) for p in store.notebook.plans(14169)] == [("Spread", "Halvard", 1200.0)]
    assert (tmp_path / "notes.sqlite").exists()  # the core's (WIPEBOT_NOTES_DB), which its tools read
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"raid_notes", "raid_plans"}
    assert len(Store(db).notebook.notes(14169, 3420)) == 2  # moved once


def test_by_default_the_bot_saves_raid_notes_where_the_core_reads_them(tmp_path, monkeypatch):
    """Outside Docker too (no WIPEBOT_NOTES_DB), wherever the bot's own database is."""
    from wipebot.core import notebook
    monkeypatch.delenv("WIPEBOT_NOTES_DB")
    monkeypatch.setattr(notebook, "DEFAULT_PATH", tmp_path / "share" / "notes.sqlite")
    opened = Store(tmp_path / "elsewhere" / "wipebot.db")
    assert opened.notebook.path == tmp_path / "share" / "notes.sqlite"


def test_an_older_versions_officer_channel_and_names_on_are_dropped(tmp_path, settings):
    db = tmp_path / "wipebot.db"
    _with_settings(db, settings, officer_channel_id=20)
    store = Store(db)
    assert store.settings(1001) == settings
    tables = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "naming_channels" not in tables and Store(db).settings(1001) == settings


def test_spend_is_kept_per_server_and_asker_recorded_or_not(store):
    import time
    store.add_spend(1001, 7, "chat", 0.25, 90.0)
    store.add_spend(1001, None, "follow", 0.5, 10.0)  # a channel's post after a wipe
    store.add_spend(2002, 8, "chat", 0.125, 4.0)
    assert store.spent(0) == (3, 0.875, 104.0)
    assert store.spent(0, discord_guild_id=1001) == (2, 0.75, 100.0)
    assert store.spent(0, user_id=8) == (1, 0.125, 4.0)
    assert store.spent(time.time() + 60) == (0, 0.0, 0.0)
    assert store.answer_costs(0) == (3, 0.875)


def test_a_server_the_bot_left_is_forgotten_but_its_spend_stays(store):
    store.add_spend(1001, 7, "chat", 0.25, 90.0)
    store.forget_server(1001)
    assert store.settings(1001) is None
    assert store.spent(0, discord_guild_id=1001)[0] == 1


def test_a_servers_own_key_is_kept_encrypted_and_forgotten_with_the_server(store, monkeypatch):
    from wipebot.discord.keys import ServerKey
    monkeypatch.setenv("WIPEBOT_KEY_SECRET", "s3cret")
    store.save_server_key(1001, ServerKey("openrouter", "sk-or-abc"), added_by=7)
    assert "sk-or-abc" not in str(store._db.execute("SELECT * FROM server_keys").fetchall())
    assert store.server_key(1001) == ServerKey("openrouter", "sk-or-abc")
    assert store.server_key(1001).env()["ANTHROPIC_AUTH_TOKEN"] == "sk-or-abc"
    monkeypatch.setenv("WIPEBOT_KEY_SECRET", "another")  # the host changed the secret: add it again
    assert store.server_key(1001) is None
    monkeypatch.setenv("WIPEBOT_KEY_SECRET", "s3cret")
    store.forget_server(1001)
    assert store.server_key(1001) is None and not store.drop_server_key(1001)


def test_a_sign_in_is_kept_until_it_lapses_or_the_raider_signs_out(store, monkeypatch):
    store.save_login(7, 101, "Kaelnor", "token-abc", expires=time.time() + 3600)
    assert store.login(7) == ("Kaelnor", "token-abc")
    assert "token-abc" not in str(store._db.execute("SELECT * FROM logins").fetchall())  # kept encrypted
    store.save_login(8, 102, "Other", "token-old", expires=time.time() - 1)
    assert store.login(8) is None and store.login(9) is None
    assert store.drop_login(7) and not store.drop_login(7) and store.login(7) is None
