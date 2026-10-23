"""Posts after each wipe of the guild's live report, for whoever asks (the follow_pulls tool): a raider's own
feedback in their DMs, or what someone asked for, in their DMs or a channel. One per place, and per person one in
their DMs and one in a channel, up to MAX_PER_SERVER in a server: each is an answer after every wipe. They're kept in
the store, so a restart doesn't end them, and end with the report they follow.

This module keeps who asked for what; the bot finds the pulls (follower.py) and writes and sends the posts."""
import logging
import time
from dataclasses import dataclass

import discord

from wipebot.core.report import FactError
from wipebot.core.server import Library

from .follower import Pull
from .store import Raider, SavedFollowing, Store

log = logging.getLogger(__name__)

FOLLOW_WAIT_S = 12 * 3600  # a request made before the raid starts logging waits this long for the live report
MAX_PER_SERVER = 10  # requests in one server, DMs and channels together
REQUEST_CHARS = 200  # a request's length: it goes into the question after every wipe


@dataclass
class Following:
    """A post after each wipe that someone asked for."""
    user: discord.User | discord.Member  # who asked
    name: str  # their Discord name, for the context line
    character: str | None  # whose own feedback goes to their DMs; None with a request
    # The report followed: posts stop when the server follows another, or none. None until the guild's live
    # report starts (asked before the raid logged a pull).
    code: str | None
    channel: discord.abc.Messageable | None = None  # the channel where the posts go; None for their DMs
    request: str = ""  # what they asked for after each wipe, in their words
    asked: float = 0.0  # when (time.time()), for how long a request waits for the live report

    @property
    def key(self) -> int:
        """Where it goes, as a Discord id (a user's for their DM, else the channel's; ids are unique across both)."""
        return getattr(self.channel, "id", None) or self.user.id


class Posts:
    """Every server's requests for posts after wipes, by Discord server and then by place (Following.key)."""

    def __init__(self, store: Store, library: Library, record_answers: bool = False):
        self.store, self.library = store, library
        self.by_server: dict[int, dict[int, Following]] = {}
        self.record_answers = record_answers  # the log names who asked and what (as the bot's own log does)

    def _shown(self, name: str) -> str:
        return name if self.record_answers else "a raider"

    def add(self, guild_id: int, f: Following, save: bool = True) -> Following | None:
        """Start posts after wipes where `f` goes, replacing the request there before (returned)."""
        before = self.by_server.setdefault(guild_id, {}).get(f.key)
        self.by_server[guild_id][f.key] = f
        if save:
            self._save(guild_id, f)
        return before

    def end(self, guild_id: int, key: int) -> Following | None:
        """Stop the posts after wipes in one place; the request stopped, if there was one."""
        self.store.drop_following(guild_id, key)
        return self.by_server.get(guild_id, {}).pop(key, None)

    def clear(self, guild_id: int) -> None:
        """Stop every request in a server (its setup changed)."""
        self.by_server.pop(guild_id, None)
        self.store.drop_following(guild_id)

    def _save(self, guild_id: int, f: Following) -> None:
        self.store.save_following(SavedFollowing(guild_id, f.key, f.user.id, f.name, f.character, f.code,
                                                 getattr(f.channel, "id", None), f.request, f.asked))

    async def follow_pulls(self, guild_id: int, user: discord.User | discord.Member, name: str, args: dict,
                           live: str | None, code: str, channel: discord.abc.Messageable | None = None,
                           officer: bool = False) -> str:
        """The follow_pulls tool, asked by `user` in a DM or a server channel (`channel`): start or stop the posts
        after each wipe of the live report (`live`, None before the raid logs) there; what to tell the model. `code`
        is the report the question was about, where the character is looked up before the raid logs. Someone else's
        request in a channel is changed or stopped only by them or an `officer`."""
        place = "here" if channel else f"to {name} in a DM"
        following = self.by_server.get(guild_id, {})
        key = getattr(channel, "id", None) or user.id
        theirs = following.get(key)
        if channel is not None and theirs and theirs.user.id != user.id and not officer:
            return (f"{theirs.name} asked for the posts after wipes here ({theirs.request}): only they, or an officer "
                    "(/wipebot settings says who), can change or stop them.")
        if args.get("stop"):
            ended = self.end(guild_id, key)
            log.info("posts after wipes %s: stopped by %s", "in a channel" if channel else "in a DM", self._shown(name))
            return "Stopped: no more posts after wipes here." if ended else "There were no posts after wipes to stop."
        request = " ".join(str(args.get("request") or "").split())
        if len(request) > REQUEST_CHARS:
            return f"That request is too long: put it in at most {REQUEST_CHARS} characters."
        # One channel per person: asking in another moves their posts there.
        elsewhere = [k for k, f in following.items()
                     if channel is not None and f.channel is not None and f.user.id == user.id and k != key]
        if key not in following and len(following) - len(elsewhere) >= MAX_PER_SERVER:
            return (f"This server already has {MAX_PER_SERVER} requests for posts after wipes, the most there can be: "
                    "one has to stop first.")
        report = ("of the guild's live report" if live else
                  f"of the guild's report once the raid starts logging (within {FOLLOW_WAIT_S // 3600} hours; "
                  "nothing is being logged now)")
        if request:
            moved = [self.end(guild_id, k) for k in elsewhere]
            before = self.add(guild_id, Following(user, name, None, live, channel, request, time.time()))
            log.info("posts after wipes %s for %s on report %s%s", "in a channel" if channel else "in a DM",
                     self._shown(name), live or "(not started)", f": {request}" if self.record_answers else "")
            was = (f"It replaces the request made here before: {before.request or 'their own feedback'}."
                   if before else "Nothing was set up here before this request.")
            was += "".join(f" Their posts in #{getattr(m.channel, 'name', '')} stop: one channel per person."
                           for m in moved if m)
            return (f"Done: after each wipe {report}, a post {place} with: {request}. Until the report stops "
                    f"being logged or someone asks to stop. {was}")
        if channel is not None:
            return "Say what they want posted here after each wipe, in their words, as `request`."
        character = str(args.get("character") or "").strip()
        try:  # as the log spells it; before the raid logs, as the latest night has it, if they're in it
            character = (await self.library.report(live or code)).player(character).name
        except FactError as e:
            if live or not character:
                return str(e)
        before = self.add(guild_id, Following(user, name, character, live, asked=time.time()))
        self._remember_character(user.id, character)
        log.info("feedback after wipes for %s on report %s", self._shown(f"{name} as {character}"),
                 live or "(not started)")
        was = "It was already set up." if before else "Nothing was set up for them before this request."
        return (f"Done: after each wipe {report}, {character}'s feedback goes to {name} in a DM, until the "
                f"report stops being logged or they ask to stop. {was}")

    async def due(self, guild_id: int, live: str | None, pulls: list[Pull]) -> list[tuple[Following, Pull]]:
        """The posts due after `pulls` (one per request, on the latest wipe of its report), once the server's requests
        are brought up to the live report `live`: one waiting for the raid follows it from now on, and one for a
        report no longer live, or that waited too long, ends."""
        following = self.by_server.get(guild_id, {})
        for key, f in list(following.items()):
            if f.code is None and live:
                await self._attach(f, live)
                self._save(guild_id, f)
            elif f.code is None and time.time() - f.asked > FOLLOW_WAIT_S:
                self.end(guild_id, key)
                log.info("stopped waiting for the live report for %s: it didn't start", self._shown(f.name))
            elif f.code is not None and f.code != live:
                self.end(guild_id, key)
                log.info("stopped posts after wipes for %s: report %s ended", self._shown(f.name), f.code)
        # After a gap (a restart, Warcraft Logs down) a poll finds several wipes: only the latest is worth a post. The
        # first poll after a request may find wipes from before it (nothing polls while nobody asked): those aren't.
        latest = {id(f): (f, pull) for pull in pulls if pull.wipe for f in following.values()
                  if f.code == pull.report and pull.ended_ms >= f.asked * 1000}
        return list(latest.values())

    async def _attach(self, f: Following, code: str) -> None:
        """A request made before the raid started logging follows the live report from now on, with the character
        spelled as that report has it."""
        f.code = code
        if f.character:
            try:
                f.character = (await self.library.report(code)).player(f.character).name
                self._remember_character(f.user.id, f.character)
            except FactError:
                pass
        log.info("posts after wipes for %s now follow report %s", self._shown(f.name), code)

    def _remember_character(self, user_id: int, character: str) -> None:
        self.store.save_raider(user_id, Raider(character, self.store.raider(user_id).notes))
