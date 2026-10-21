"""SQLite state: each Discord server's settings (from /wipebot setup), which pulls of a live report have been
handled, who asked for posts after each wipe and where, which Discord message holds the feedback after which pull,
what the bot remembers about each raider (their character and preferences), and a record of every answer (question,
answer, tool calls, thinking, cost) with the reactions to it, for improving the bot from real use. Raid notes and
plans are in the notebook (core/notebook.py), which the remote MCP server reads too."""
import json
import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import astuple, dataclass, field, fields
from pathlib import Path

from wipebot.core import notebook
from wipebot.core.notebook import Notebook

# WIPEBOT_DB, else next to the raid notes' default: ~/.local/share/wipebot/
DB_PATH = Path(os.environ.get("WIPEBOT_DB") or notebook.DEFAULT_PATH.with_name("wipebot.db"))
INTERACTION_DAYS = 90  # answers and their reactions are kept this long
MAX_NOTES = 5  # preferences kept per raider: a new one past this replaces the oldest
NOTE_CHARS = 120
CHARACTER_CHARS = 40  # a character, "Name-Realm" at most; one not found in a log is kept as told


@dataclass
class ServerSettings:
    """One Discord server's setup: which WCL guild to follow and who may chat. The channels where wipebot names
    players are the store's `naming_channels`."""
    discord_guild_id: int
    wcl_guild_id: int  # the guild on Warcraft Logs; reports are found by this id
    wcl_guild: str  # the guild's name, realm and region, as found at setup (for display)
    wcl_server: str
    wcl_region: str
    required_role_id: int | None = None  # role needed to chat with wipebot; None for anyone
    officer_role_id: int | None = None  # the officers' role (bot.is_officer); None: who can manage the server


@dataclass
class Raider:
    """What the bot remembers about one Discord user across conversations, which they can see and clear."""
    character: str | None = None  # the character they play, as in the logs
    notes: list[str] = field(default_factory=list)  # preferences they stated, in a few words each

    def __bool__(self) -> bool:
        return bool(self.character or self.notes)

    def apply(self, args: dict) -> str | None:
        """The remember tool's arguments applied (character, note, forget_note, forget); why not, if they can't
        be."""
        if args.get("forget"):
            self.character, self.notes = None, []
            return None
        if drop := str(args.get("forget_note") or "").strip().lower():
            kept = [n for n in self.notes if drop not in n.lower()]
            if len(kept) == len(self.notes):
                return f"No remembered note matches {drop!r}; their notes: {self.notes or 'none'}."
            self.notes = kept
        if character := str(args.get("character") or "").strip():
            self.character = character
        if note := str(args.get("note") or "").strip():
            self.notes.append(note)
        return None


@dataclass
class SavedFollowing:
    """A post after each wipe someone asked for (follow_pulls), kept so a restart doesn't end it: the bot's
    Following without its Discord objects, which are looked up again by id."""
    discord_guild_id: int
    key: int  # where it goes: the user's id for their DMs, else the channel's
    user_id: int  # who asked
    name: str
    character: str | None
    code: str | None
    channel_id: int | None
    request: str
    asked: float


# Columns of older versions that new rows can't leave empty: the posts channel of the answer after each wipe.
DROPPED_COLUMNS = {"server_settings": ("posts_channel_id",)}
# Columns added since a table was made: (table, column, type).
ADDED_COLUMNS = [("interactions", "fight", "INTEGER"), ("server_settings", "officer_role_id", "INTEGER")]


class Store:
    def __init__(self, path: Path | str = DB_PATH, notebook: Notebook | None = None):
        """`notebook`: where raid notes and plans are saved, shared with the remote MCP server and the core's tools
        (default: theirs, WIPEBOT_NOTES_DB or ~/.local/share/wipebot/notes.sqlite; in memory with this database)."""
        memory = str(path) == ":memory:"
        if not memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        if notebook is None:
            notebook = Notebook(":memory:" if memory else None)
        self.notebook = notebook
        for table, columns in DROPPED_COLUMNS.items():
            have = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column in have:
                    self._db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        self._db.executescript(
            """CREATE TABLE IF NOT EXISTS posted (
                report TEXT NOT NULL,
                fight INTEGER NOT NULL,
                posted_at REAL NOT NULL,
                latency_s REAL,          -- seconds from pull end to post; NULL if skipped as backlog
                PRIMARY KEY (report, fight)
            );
            CREATE TABLE IF NOT EXISTS post_messages (
                message_id INTEGER PRIMARY KEY,  -- a Discord message holding the feedback after a pull
                report TEXT NOT NULL,
                fight INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS server_settings (
                discord_guild_id INTEGER PRIMARY KEY,
                wcl_guild_id INTEGER NOT NULL,
                wcl_guild TEXT NOT NULL,
                wcl_server TEXT NOT NULL,
                wcl_region TEXT NOT NULL,
                required_role_id INTEGER,
                officer_role_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS naming_channels (  -- where an admin turned names on (/wipebot names)
                discord_guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (discord_guild_id, channel_id)
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY,
                at REAL NOT NULL,
                kind TEXT NOT NULL,      -- chat, follow (feedback after a wipe, in a DM or a channel),
                                         -- try or test (developer runs);
                                         -- live in older rows (the answer after each wipe, since removed)
                discord_guild_id INTEGER,  -- NULL in a DM
                channel_id INTEGER,
                message_id INTEGER,      -- the question's Discord message; NULL when the bot spoke first
                asker TEXT,              -- Discord name
                report TEXT NOT NULL,
                question TEXT NOT NULL,
                prompt TEXT,             -- what the model got: earlier messages, context line, question
                answer TEXT,             -- NULL when none was posted
                error TEXT,              -- why not: a timeout or the exception
                model TEXT,
                session_id TEXT,         -- the Agent SDK session (its transcript, while Claude Code keeps it)
                duration_s REAL,
                cost_usd REAL,           -- this answer's own (in rows of Oct 12 2026 and before: its conversation's
                                         -- total so far, as the SDK gives it)
                turns INTEGER,
                usage TEXT,              -- JSON: tokens
                thinking TEXT,           -- JSON: list of thinking blocks
                tool_calls TEXT,         -- JSON: list of {name, args, result, failed, images, started_s, duration_s}
                                         -- (the last two from 13 Oct 2026: seconds from the question)
                fight INTEGER            -- feedback after a wipe: the pull's fight id (from 13 Oct 2026)
            );
            CREATE TABLE IF NOT EXISTS answer_messages (
                message_id INTEGER PRIMARY KEY,  -- a Discord message holding (part of) an answer
                interaction INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raiders (
                discord_user_id INTEGER PRIMARY KEY,
                character TEXT,
                notes TEXT NOT NULL DEFAULT '[]',  -- JSON: list of short preferences, oldest first
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS following (
                discord_guild_id INTEGER NOT NULL,
                key INTEGER NOT NULL,    -- where the posts go: a user's id for their DMs, else the channel's
                user_id INTEGER NOT NULL,  -- who asked
                name TEXT NOT NULL,
                character TEXT,          -- whose feedback goes to their DMs; NULL with a request
                code TEXT,               -- the report followed; NULL until the raid starts logging
                channel_id INTEGER,      -- NULL for their DMs
                request TEXT NOT NULL,
                asked REAL NOT NULL,
                PRIMARY KEY (discord_guild_id, key)
            );
            CREATE TABLE IF NOT EXISTS reactions (
                message_id INTEGER NOT NULL,  -- an answer's message
                user_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                at REAL NOT NULL,
                PRIMARY KEY (message_id, user_id, emoji)
            );"""
        )
        for table, column, kind in ADDED_COLUMNS:
            if column not in {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        if "officer_channel_id" in {row[1] for row in self._db.execute("PRAGMA table_info(server_settings)")}:
            # An older version's one officer channel: names stay on there.
            self._db.execute("INSERT OR IGNORE INTO naming_channels SELECT discord_guild_id, officer_channel_id "
                             "FROM server_settings WHERE officer_channel_id IS NOT NULL")
            self._db.execute("ALTER TABLE server_settings DROP COLUMN officer_channel_id")
        self._db.commit()
        self._move_notes()

    def _move_notes(self) -> None:
        """Raid notes and plans kept here before (by Discord server) go to the notebook (by the server's guild on
        Warcraft Logs), once; a server no longer set up loses its."""
        tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        guild = {s.discord_guild_id: s.wcl_guild_id for s in self.all_settings()}
        if "raid_notes" in tables:
            for server, encounter, boss, note, by, at in self._db.execute(
                    "SELECT discord_guild_id, encounter_id, boss, note, saved_by, saved_at FROM raid_notes "
                    "ORDER BY saved_at").fetchall():
                if server in guild:
                    self.notebook.save_note(guild[server], encounter, boss, note, by, at)
            self._db.execute("DROP TABLE raid_notes")
        if "raid_plans" in tables:
            for server, boss, plan_id, name, plan, by, at in self._db.execute(
                    "SELECT discord_guild_id, boss, plan_id, name, plan, saved_by, saved_at FROM raid_plans "
                    "ORDER BY saved_at").fetchall():
                if server in guild:
                    self.notebook.save_plan(guild[server], boss, plan_id, name, plan, by, at)
            self._db.execute("DROP TABLE raid_plans")
        self._db.commit()

    # ---------- server settings ----------

    def save_settings(self, s: ServerSettings) -> None:
        marks = ", ".join("?" for _ in fields(ServerSettings))
        self._db.execute(f"INSERT OR REPLACE INTO server_settings ({self._COLUMNS}) VALUES ({marks})", astuple(s))
        self._db.commit()

    # Columns by name, so a database with columns from an older version (chat_channel_id, taunt_watch,
    # answer_after_wipes) still reads.
    _COLUMNS = ", ".join(f.name for f in fields(ServerSettings))

    def settings(self, discord_guild_id: int) -> ServerSettings | None:
        row = self._db.execute(f"SELECT {self._COLUMNS} FROM server_settings WHERE discord_guild_id = ?",
                               (discord_guild_id,)).fetchone()
        return ServerSettings(*row) if row else None

    def all_settings(self) -> list[ServerSettings]:
        return [ServerSettings(*r) for r in self._db.execute(f"SELECT {self._COLUMNS} FROM server_settings")]

    def naming_channels(self, discord_guild_id: int) -> set[int]:
        """The channels where wipebot names players (and their threads), as the server's admins set them."""
        return {r[0] for r in self._db.execute("SELECT channel_id FROM naming_channels WHERE discord_guild_id = ?",
                                               (discord_guild_id,))}

    def set_naming(self, discord_guild_id: int, channel_id: int, on: bool) -> None:
        self._db.execute("INSERT OR IGNORE INTO naming_channels VALUES (?, ?)" if on else
                         "DELETE FROM naming_channels WHERE discord_guild_id = ? AND channel_id = ?",
                         (discord_guild_id, channel_id))
        self._db.commit()

    # ---------- posts ----------

    def is_posted(self, report: str, fight: int) -> bool:
        row = self._db.execute("SELECT 1 FROM posted WHERE report = ? AND fight = ?", (report, fight)).fetchone()
        return row is not None

    def mark_posted(self, report: str, fight: int, latency_s: float | None) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO posted (report, fight, posted_at, latency_s) VALUES (?, ?, ?, ?)",
            (report, fight, time.time(), latency_s),
        )
        self._db.commit()

    def remember_message(self, message_id: int, report: str, fight: int) -> None:
        """Note which pull a Discord message (feedback after a wipe) is about, so replies to it are too."""
        self._db.execute("INSERT OR REPLACE INTO post_messages VALUES (?, ?, ?)", (message_id, report, fight))
        self._db.commit()

    def pull_for_message(self, message_id: int) -> tuple[str, int] | None:
        """(report, fight id) of the pull a Discord message is the feedback after, if it is one."""
        row = self._db.execute("SELECT report, fight FROM post_messages WHERE message_id = ?", (message_id,)).fetchone()
        return (row[0], row[1]) if row else None

    # ---------- raiders ----------

    def raider(self, user_id: int) -> Raider:
        """What's remembered about a Discord user; an empty Raider when nothing is."""
        row = self._db.execute("SELECT character, notes FROM raiders WHERE discord_user_id = ?",
                               (user_id,)).fetchone()
        return Raider(row[0], json.loads(row[1])) if row else Raider()

    def save_raider(self, user_id: int, raider: Raider) -> None:
        """Keep what's remembered about a Discord user: the last MAX_NOTES notes, each cut to NOTE_CHARS, and the
        character cut to CHARACTER_CHARS. An empty Raider forgets them."""
        if not raider:
            self._db.execute("DELETE FROM raiders WHERE discord_user_id = ?", (user_id,))
        else:
            notes = [n.strip()[:NOTE_CHARS] for n in raider.notes if n.strip()][-MAX_NOTES:]
            self._db.execute("INSERT OR REPLACE INTO raiders VALUES (?, ?, ?, ?)",
                             (user_id, raider.character and raider.character[:CHARACTER_CHARS],
                              json.dumps(notes, ensure_ascii=False), time.time()))
        self._db.commit()

    # ---------- posts after wipes ----------

    def save_following(self, f: SavedFollowing) -> None:
        self._db.execute("INSERT OR REPLACE INTO following VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", astuple(f))
        self._db.commit()

    def drop_following(self, discord_guild_id: int, key: int | None = None) -> None:
        """Forget one place's posts after wipes, or (no `key`) every one in a server."""
        if key is None:
            self._db.execute("DELETE FROM following WHERE discord_guild_id = ?", (discord_guild_id,))
        else:
            self._db.execute("DELETE FROM following WHERE discord_guild_id = ? AND key = ?", (discord_guild_id, key))
        self._db.commit()

    def all_following(self) -> list[SavedFollowing]:
        return [SavedFollowing(*row) for row in self._db.execute(
            "SELECT discord_guild_id, key, user_id, name, character, code, channel_id, request, asked FROM following")]

    # ---------- answers and reactions ----------

    def record_interaction(self, *, messages: Sequence[int] = (), **row) -> int:
        """Keep one answer (or a question that got none) and the Discord `messages` it was posted in; returns its
        id. `row` holds the interactions columns; usage, thinking and tool_calls are stored as JSON."""
        for column in ("usage", "thinking", "tool_calls"):
            if row.get(column) is not None:
                row[column] = json.dumps(row[column], ensure_ascii=False, default=str)
        row["at"] = time.time()
        marks = ", ".join("?" for _ in row)
        interaction = self._db.execute(f"INSERT INTO interactions ({', '.join(row)}) VALUES ({marks})",
                                       tuple(row.values())).lastrowid
        assert interaction is not None
        self._db.executemany("INSERT OR REPLACE INTO answer_messages VALUES (?, ?)",
                             [(m, interaction) for m in messages])
        self._db.commit()
        return interaction

    def add_reaction(self, message_id: int, user_id: int, emoji: str) -> int | None:
        """Keep a reaction to an answer's message; returns the answer's interaction id, or None (and keeps
        nothing) when the message isn't an answer."""
        row = self._db.execute("SELECT interaction FROM answer_messages WHERE message_id = ?",
                               (message_id,)).fetchone()
        if row is None:
            return None
        self._db.execute("INSERT OR REPLACE INTO reactions VALUES (?, ?, ?, ?)",
                         (message_id, user_id, emoji, time.time()))
        self._db.commit()
        return row[0]

    def remove_reaction(self, message_id: int, user_id: int, emoji: str) -> None:
        self._db.execute("DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                         (message_id, user_id, emoji))
        self._db.commit()

    def prune_interactions(self, days: float = INTERACTION_DAYS) -> int:
        """Forget answers older than `days`, with their messages and reactions; returns how many."""
        cutoff = time.time() - days * 86400
        old = "SELECT id FROM interactions WHERE at < ?"
        messages = f"SELECT message_id FROM answer_messages WHERE interaction IN ({old})"
        self._db.execute(f"DELETE FROM reactions WHERE message_id IN ({messages})", (cutoff,))
        self._db.execute(f"DELETE FROM answer_messages WHERE interaction IN ({old})", (cutoff,))
        pruned = self._db.execute("DELETE FROM interactions WHERE at < ?", (cutoff,)).rowcount
        self._db.commit()
        return pruned
