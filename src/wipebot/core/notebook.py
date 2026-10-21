"""The raid notes and plans a guild saved, every version, keyed by its Warcraft Logs guild id, and the raidstrats.gg
login its plans are fetched with.

An officer saves a note or plan by pasting it (server.save_pasted): to the Discord bot, or in Claude or ChatGPT
(the remote MCP server's save_note_or_plan). The bot saves the login (/wipebot raidstrats). Both apps read them, so
`raid_note` and `raid_plan` work without a paste for the report's guild, and both fetch the plans of a guild with a
login (raidstrats.py), saving a new version and the login's new tokens here. The remote MCP server reads the file
read-only, and writes it only to save a paste or fetched plans. One SQLite file, WIPEBOT_NOTES_DB
(default ~/.local/share/wipebot/notes.sqlite); without it there's nothing saved.
"""
import datetime
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local" / "share" / "wipebot" / "notes.sqlite"


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


@dataclass
class RaidstratsLogin:
    """The raidstrats.gg login a guild's plans are fetched with: an officer's, who approved Wipebot reading them."""
    username: str  # on raidstrats.gg
    connected_by: str  # who connected it (a Discord name)
    connected_at: float
    refreshed_at: float  # when its refresh token was last replaced; it lapses 30 days after
    fetched_at: float | None  # the last fetch of the plans that worked
    problem: str  # why the last fetch failed, or ""
    working: bool  # False once raidstrats.gg refused it: someone connects again


SCHEMA = """
    CREATE TABLE IF NOT EXISTS raid_notes (
        id INTEGER PRIMARY KEY,
        wcl_guild_id INTEGER NOT NULL,
        encounter_id INTEGER NOT NULL,  -- the boss, from the note's EncounterID line
        boss TEXT NOT NULL,
        note TEXT NOT NULL,      -- the boss's part of the note, as pasted; every version is kept
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
    CREATE TABLE IF NOT EXISTS raidstrats_logins (
        wcl_guild_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        access_token TEXT NOT NULL,
        access_expires REAL NOT NULL,
        refresh_token TEXT NOT NULL,  -- "" once raidstrats.gg refused it
        refreshed_at REAL NOT NULL,
        connected_by TEXT NOT NULL,
        connected_at REAL NOT NULL,
        fetched_at REAL,
        problem TEXT NOT NULL DEFAULT ''
    );
"""


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
        except sqlite3.OperationalError:  # read-only, before the bot made the tables
            return []

    # ---------- notes ----------

    def save_note(self, guild_id: int, encounter_id: int, boss: str, note: str, saved_by: str,
                  at: float | None = None) -> None:
        assert self._db is not None
        self._db.execute("INSERT INTO raid_notes (wcl_guild_id, encounter_id, boss, note, saved_by, saved_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (guild_id, encounter_id, boss, note, saved_by, time.time() if at is None else at))
        self._db.commit()

    def note_for(self, guild_id: int, encounter_id: int, at: float) -> SavedNote | None:
        """The boss's note as it was at `at` (epoch seconds): the last one saved by then, else the first one saved
        after (a note saved during the night, checked against an earlier pull)."""
        query = ("SELECT boss, note, saved_by, saved_at FROM raid_notes WHERE wcl_guild_id = ? AND "
                 "encounter_id = ? AND saved_at {} ? ORDER BY saved_at {} LIMIT 1")
        rows = (self._rows(query.format("<=", "DESC"), (guild_id, encounter_id, at))
                or self._rows(query.format(">", "ASC"), (guild_id, encounter_id, at)))
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

    # ---------- the raidstrats.gg login ----------

    def save_login(self, guild_id: int, username: str, token: dict, connected_by: str) -> None:
        """A new login (raidstrats.gg's token reply), replacing the guild's old one."""
        assert self._db is not None
        now = time.time()
        self._db.execute("INSERT OR REPLACE INTO raidstrats_logins (wcl_guild_id, username, access_token, "
                         "access_expires, refresh_token, refreshed_at, connected_by, connected_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (guild_id, username, token["access_token"], _expires(token, now), token["refresh_token"],
                          now, connected_by, now))
        self._db.commit()

    def login(self, guild_id: int) -> RaidstratsLogin | None:
        rows = self._rows("SELECT username, connected_by, connected_at, refreshed_at, fetched_at, problem, "
                          "refresh_token != '' FROM raidstrats_logins WHERE wcl_guild_id = ?", (guild_id,))
        return RaidstratsLogin(*rows[0][:6], working=bool(rows[0][6])) if rows else None

    def logins(self) -> list[int]:
        """The guilds with a working login."""
        return [r[0] for r in self._rows("SELECT wcl_guild_id FROM raidstrats_logins WHERE refresh_token != ''",
                                         ())]

    def drop_login(self, guild_id: int) -> None:
        assert self._db is not None
        self._db.execute("DELETE FROM raidstrats_logins WHERE wcl_guild_id = ?", (guild_id,))
        self._db.commit()

    def fetched(self, guild_id: int, problem: str = "") -> None:
        """A fetch of the guild's plans worked, or failed for `problem`."""
        assert self._db is not None
        if problem:
            self._db.execute("UPDATE raidstrats_logins SET problem = ? WHERE wcl_guild_id = ?", (problem, guild_id))
        else:
            self._db.execute("UPDATE raidstrats_logins SET problem = '', fetched_at = ? WHERE wcl_guild_id = ?",
                             (time.time(), guild_id))
        self._db.commit()

    def access_token(self, guild_id: int, refresh: Callable[[str], dict | None], older_than: float = 0) -> str | None:
        """The login's access token, after a refresh when it has expired or its refresh token is older than
        `older_than` seconds. `refresh` trades a refresh token for raidstrats.gg's token reply, or None when it
        refused (the login is then marked not working). Each refresh replaces the refresh token, so it runs in a
        write transaction on its own connection: the other app, refreshing at the same moment, waits and then
        reads the new tokens instead of trading the old one again. Blocks: run it in a thread. None when there's
        no working login."""
        shared = str(self.path) == ":memory:"  # tests: there's no other connection to the same database
        db = self._db if shared else sqlite3.connect(self.path, timeout=30, isolation_level=None)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT access_token, access_expires, refresh_token, refreshed_at FROM raidstrats_logins "
                             "WHERE wcl_guild_id = ?", (guild_id,)).fetchone()
            if not row or not row[2]:
                db.execute("ROLLBACK")
                return None
            access, expires, refresh_token, refreshed_at = row
            now = time.time()
            if now < expires and not (older_than and now - refreshed_at >= older_than):
                db.execute("ROLLBACK")
                return access
            token = refresh(refresh_token)
            if token is None:
                db.execute("UPDATE raidstrats_logins SET refresh_token = '', problem = ? WHERE wcl_guild_id = ?",
                           ("raidstrats.gg no longer accepts the login: connect again with /wipebot raidstrats",
                            guild_id))
                db.execute("COMMIT")
                return None
            db.execute("UPDATE raidstrats_logins SET access_token = ?, access_expires = ?, refresh_token = ?, "
                       "refreshed_at = ? WHERE wcl_guild_id = ?",
                       (token["access_token"], _expires(token, now), token.get("refresh_token") or refresh_token,
                        now, guild_id))
            db.execute("COMMIT")
            return token["access_token"]
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            if not shared:
                db.close()


def _expires(token: dict, now: float) -> float:
    """When an access token stops working, a minute early."""
    return now + float(token.get("expires_in") or 3600) - 60
