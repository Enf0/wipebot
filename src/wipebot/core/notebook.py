"""The raid notes and plans a guild saved, every version, keyed by its Warcraft Logs guild id.

An officer saves a note or plan by pasting it (server.save_pasted): to the Discord bot, or in Claude or ChatGPT
(the remote MCP server's save_note_or_plan). Both apps read them, so `raid_note` and `raid_plan` work without a
paste for the report's guild. The remote MCP server reads the file read-only, and writes it only to save a paste.
A notebook from when a guild's plans were fetched with an officer's raidstrats.gg login (until November 2026) had
those logins in it: they are deleted when it is opened for writing. One SQLite file, WIPEBOT_NOTES_DB
(default ~/.local/share/wipebot/notes.sqlite); without it there's nothing saved.
"""
import datetime
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local" / "share" / "wipebot" / "notes.sqlite"
MAX_VERSIONS = 500  # of a guild's notes, and of its plans, the newest kept: on a server open to anyone, any member
# of any guild saves, and every version would otherwise be kept for good


def default_path() -> Path:
    return Path(os.environ.get("WIPEBOT_NOTES_DB") or DEFAULT_PATH)


@dataclass
class SavedNote:
    """One boss's raid note as saved."""
    boss: str
    note: str  # the boss's part of the note, as pasted
    saved_by: str  # who saved it (a Discord name)
    saved_at: float  # epoch seconds

    def source(self, pull_start: float) -> str:
        """Where it's from, for the model: who saved it when, and whether that was after the pull began."""
        when = datetime.datetime.fromtimestamp(self.saved_at, datetime.UTC)
        text = f"saved by {self.saved_by} on {when:%a %d %b %H:%M} UTC"
        if self.saved_at > pull_start:
            text += ", after this pull began: the plan for this pull may have been different"
        return text


@dataclass
class SavedPlan:
    """One version of a raid plan from raidstrats.gg as saved."""
    boss: str  # as the plan names it
    plan_id: str
    name: str
    plan: str  # the export, raidstrats-addon-...
    saved_by: str
    saved_at: float  # epoch seconds


SCHEMA = """
    CREATE TABLE IF NOT EXISTS raid_notes (
        id INTEGER PRIMARY KEY,
        wcl_guild_id INTEGER NOT NULL,
        encounter_id INTEGER NOT NULL,  -- the boss, from the note's EncounterID line
        boss TEXT NOT NULL,
        note TEXT NOT NULL,      -- the boss's part of the note, as pasted; every version is kept (MAX_VERSIONS)
        saved_by TEXT NOT NULL,
        saved_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS raid_plans (
        id INTEGER PRIMARY KEY,
        wcl_guild_id INTEGER NOT NULL,
        boss TEXT NOT NULL,      -- as the plan names it
        plan_id TEXT NOT NULL,   -- raidstrats.gg's: a plan pasted again is a new version of it
        name TEXT NOT NULL,
        plan TEXT NOT NULL,      -- the export (raidstrats-addon-...); every version is kept
        saved_by TEXT NOT NULL,
        saved_at REAL NOT NULL
    );
    DROP TABLE IF EXISTS raidstrats_logins;  -- officers' raidstrats.gg logins, used until November 2026
    CREATE TABLE IF NOT EXISTS viserio_keys (
        wcl_guild_id INTEGER PRIMARY KEY,
        api_key TEXT NOT NULL,   -- the group's key for Viserio Cooldowns' API
        group_name TEXT NOT NULL,
        connected_by TEXT NOT NULL,
        connected_at REAL NOT NULL
    );
"""


class Unreadable(Exception):
    """The file is there and can't be read now (locked by another process, damaged, or of another version's
    making); the message is shown to the model. Not "nothing saved": only a missing file or table is that."""


class Notebook:
    """The saved notes and plans. `readonly` opens an existing file for reading only (the remote server); a
    missing one then reads as empty."""

    def __init__(self, path: Path | str | None = None, readonly: bool = False):
        path = path or default_path()
        self.path = path
        self._db: sqlite3.Connection | None = None
        if readonly:
            if path == ":memory:" or Path(path).exists():
                self._db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            return
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")  # the remote server reads while the bot writes
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _rows(self, query: str, args: tuple) -> list[tuple]:
        if self._db is None:
            return []
        try:
            return self._db.execute(query, args).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):  # read-only, before the bot made the tables
                return []
            raise Unreadable(f"the saved raid notes and plans can't be read right now ({e}); that doesn't mean "
                             "none is saved: ask again in a moment") from e

    def _keep_newest(self, table: str, guild_id: int) -> None:
        """Only a guild's MAX_VERSIONS newest rows of `table`."""
        assert self._db is not None
        self._db.execute(f"DELETE FROM {table} WHERE wcl_guild_id = ? AND id NOT IN (SELECT id FROM {table} WHERE "
                         "wcl_guild_id = ? ORDER BY saved_at DESC, id DESC LIMIT ?)",
                         (guild_id, guild_id, MAX_VERSIONS))

    # ---------- notes ----------

    def save_note(self, guild_id: int, encounter_id: int, boss: str, note: str, saved_by: str,
                  at: float | None = None) -> None:
        assert self._db is not None
        self._db.execute("INSERT INTO raid_notes (wcl_guild_id, encounter_id, boss, note, saved_by, saved_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (guild_id, encounter_id, boss, note, saved_by, time.time() if at is None else at))
        self._keep_newest("raid_notes", guild_id)
        self._db.commit()

    def note_for(self, guild_id: int, encounter_id: int, at: float, night: tuple[float, float] = (0, math.inf)
                 ) -> SavedNote | None:
        """The boss's note as it was at `at` (epoch seconds): the last one saved by then, else the first one saved
        after (a note saved during the night, checked against an earlier pull); only those saved in `night`
        (from, until), as notes change from night to night."""
        query = ("SELECT boss, note, saved_by, saved_at FROM raid_notes WHERE wcl_guild_id = ? AND "
                 "encounter_id = ? AND saved_at >= ? AND saved_at < ? AND saved_at {} ? ORDER BY saved_at {} LIMIT 1")
        since, until = night
        rows = (self._rows(query.format("<=", "DESC"), (guild_id, encounter_id, since, until, at))
                or self._rows(query.format(">", "ASC"), (guild_id, encounter_id, since, until, at)))
        return SavedNote(*rows[0]) if rows else None

    def notes(self, guild_id: int, encounter_id: int) -> list[SavedNote]:
        """Every version of a boss's note, oldest first."""
        return [SavedNote(*r) for r in self._rows(
            "SELECT boss, note, saved_by, saved_at FROM raid_notes WHERE wcl_guild_id = ? AND encounter_id = ? "
            "ORDER BY saved_at", (guild_id, encounter_id))]

    def latest_notes(self, guild_id: int) -> list[SavedNote]:
        """Each boss's newest note, newest first."""
        return [SavedNote(*r) for r in self._rows(
            "SELECT boss, note, saved_by, MAX(saved_at) FROM raid_notes WHERE wcl_guild_id = ? "
            "GROUP BY encounter_id ORDER BY MAX(saved_at) DESC", (guild_id,))]

    # ---------- plans ----------

    def save_plan(self, guild_id: int, boss: str, plan_id: str, name: str, plan: str, saved_by: str,
                  at: float | None = None) -> None:
        assert self._db is not None
        self._db.execute("INSERT INTO raid_plans (wcl_guild_id, boss, plan_id, name, plan, saved_by, saved_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (guild_id, boss, plan_id, name, plan, saved_by, time.time() if at is None else at))
        self._keep_newest("raid_plans", guild_id)
        self._db.commit()

    def plans(self, guild_id: int) -> list[SavedPlan]:
        """Every version of every plan, oldest first."""
        return [SavedPlan(*r) for r in self._rows(
            "SELECT boss, plan_id, name, plan, saved_by, saved_at FROM raid_plans WHERE wcl_guild_id = ? "
            "ORDER BY saved_at", (guild_id,))]

    def latest_plans(self, guild_id: int) -> list[SavedPlan]:
        """Each plan's newest version, newest first."""
        newest: dict[str, SavedPlan] = {p.plan_id: p for p in self.plans(guild_id)}
        return sorted(newest.values(), key=lambda p: -p.saved_at)

    # ---------- the Viserio Cooldowns key ----------

    def save_viserio_key(self, guild_id: int, key: str, group_name: str, connected_by: str) -> None:
        """The group's key for the guild, replacing its old one."""
        assert self._db is not None
        self._db.execute("INSERT OR REPLACE INTO viserio_keys (wcl_guild_id, api_key, group_name, connected_by, "
                         "connected_at) VALUES (?, ?, ?, ?, ?)", (guild_id, key, group_name, connected_by, time.time()))
        self._db.commit()

    def viserio_key(self, guild_id: int) -> tuple[str, str, str, float] | None:
        """The guild's key, its group's name, who connected it and when; None without one."""
        rows = self._rows("SELECT api_key, group_name, connected_by, connected_at FROM viserio_keys "
                          "WHERE wcl_guild_id = ?", (guild_id,))
        return rows[0] if rows else None

    def drop_viserio_key(self, guild_id: int) -> None:
        assert self._db is not None
        self._db.execute("DELETE FROM viserio_keys WHERE wcl_guild_id = ?", (guild_id,))
        self._db.commit()
