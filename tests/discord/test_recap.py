"""The night's recap (recap.py, WipeBot._recap, /wipebot recap): which report, its lines on a real night, what
Discover offers, and the post with its buttons."""
import json
from collections import Counter
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from tests.support.recorded import Replay, serve_from
from wipebot.core import server
from wipebot.discord import commands, recap
from wipebot.discord.messages import Act, FollowUp

MIN = 60_000
NOW = 1_000_000 * MIN


def code_of(found: dict | None) -> str | None:
    return found["code"] if found else None


def tool_of(found: recap.Discover | None) -> str | None:
    return found.tool if found else None


def report(code: str, start_min: float, end_min: float) -> dict:
    """A guild report that started and ended this many minutes before NOW."""
    return {"code": code, "startTime": NOW - start_min * MIN, "endTime": NOW - end_min * MIN}


def test_the_recap_waits_for_the_log_to_go_quiet_and_comes_once_a_night():
    quiet = recap.REPORT_QUIET_S // 60
    assert recap.due([report("LIVE", 200, 2)], NOW, None) is None  # still being logged
    assert code_of(recap.due([report("ENDED", 200, quiet + 1)], NOW, None)) == "ENDED"
    # the newest that ended, past one still being logged (a raider's own log after the raid)
    assert code_of(recap.due([report("SOLO", 30, 1), report("RAID", 220, quiet + 5)], NOW, None)) == "RAID"
    assert recap.due([report("OLD", 900, 800)], NOW, None) is None  # turned on the day after
    done = {"code": "RAID", "end": NOW - (quiet + 5) * MIN}
    assert recap.due([report("RAID", 220, quiet + 5)], NOW, done) is None
    # a second raider's log of the same night
    assert recap.due([report("SAME", 210, quiet + 2), report("RAID", 220, quiet + 5)], NOW, done) is None
    assert code_of(recap.due([report("NEXT", 100, quiet + 1)], NOW, {"code": "RAID", "end": NOW - 300 * MIN})) == "NEXT"


def test_discover_offers_what_the_raiders_asked_least_and_not_twice_in_a_row():
    every = {"killer", "two_wipes", "note"}
    asked = Counter({d.tool: 50 for d in recap.DISCOVER} | {"pull_map": 2, "healing": 2})
    assert tool_of(recap.discover(asked, every, None)) == "healing"  # the first of the least used, in DISCOVER's order
    assert tool_of(recap.discover(asked, every, "healing")) == "pull_map"
    assert tool_of(recap.discover(Counter(), set(), None)) == "healing"  # needs nothing the night lacks
    assert tool_of(recap.discover(Counter(), {"killer"}, None)) == "mechanic_hits"


def test_the_servers_language_picks_the_recaps():
    assert [recap.language(n) for n in ("Norwegian", "norsk", None, "English", "German")] == \
        ["no", "no", "en", "en", "en"]


@pytest.fixture
def recorded(monkeypatch):
    serve_from(monkeypatch, Replay())


async def test_the_lines_say_what_the_nights_picture_draws(recorded):
    rep = await server.lib().report("KBYT4xXn7VNjDgh8")
    made = await recap.recap(rep, "en", Counter(), False, None)
    assert made is not None and made.boss == "Sszorak" and made.fight == rep.by_boss["Sszorak"][-1]["id"]
    lines = made.text.split("\n")
    assert lines[0].startswith("**That's the raid.**") and lines[0].endswith(" pulls on Sszorak:")
    assert any(line.startswith("- ") and "Best wipe" in line or "Killed on pull" in line for line in lines[1:])
    assert made.questions[0] in ("What do we fix on Sszorak next time?", "What went right on the Sszorak kill?")
    assert len(made.questions) == 2 and made.pick and all(len(q) <= 80 for q in made.questions)
    norsk = await recap.recap(rep, "no", Counter(), False, made.pick)
    assert norsk is not None and norsk.text.startswith("**Det var kvelden.**") and norsk.pick != made.pick


async def test_a_quick_look_at_a_boss_is_no_raid_night(recorded):
    rep = await server.lib().report("KBYT4xXn7VNjDgh8")
    rep.by_boss = {"Sszorak": rep.by_boss["Sszorak"][:2]}
    assert await recap.recap(rep, "en", Counter(), False, None) is None


class Channel:
    def __init__(self):
        self.views: list[Any] = []

    async def send(self, *, view, files, allowed_mentions):
        self.views.append(view)
        return SimpleNamespace(id=77)


async def test_the_recap_is_posted_once_with_its_buttons_and_a_reply_is_about_the_boss(bot, monkeypatch, settings):
    quiet = recap.REPORT_QUIET_S // 60

    async def guild_reports(guild_id, since_ms):
        assert guild_id == settings.wcl_guild_id
        return [report("NIGHT1", 200, quiet + 3)]
    bot.library.wcl = SimpleNamespace(guild_reports=guild_reports)

    async def loaded(code, max_age_s=None):
        async def no_doubt():
            return None
        return SimpleNamespace(code=code, guild_run_doubt=no_doubt)
    monkeypatch.setattr(bot.library, "report", loaded)
    asked = []

    async def made(rep, lang, uses, has_note, last_pick):
        asked.append((rep.code, lang, has_note, last_pick))
        return recap.Recap("**Det var kvelden.** 31 pulls på Sszorak:", "Sszorak", 31,
                           ["Hva fikser vi på Sszorak til neste gang?", "Hvor sto alle på pull 25?"], "pull_map")
    monkeypatch.setattr(recap, "recap", made)
    channel = Channel()
    monkeypatch.setattr(bot, "get_channel", {50: channel}.get)
    bot.store.set_language(1001, "Norwegian")

    assert await bot._recap(settings, 50, NOW / 1000)
    assert not await bot._recap(settings, 50, NOW / 1000)  # once
    assert asked == [("NIGHT1", "no", False, None)]
    [view] = channel.views
    texts = [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]
    assert texts == ["**Det var kvelden.** 31 pulls på Sszorak:"]
    buttons = [c for c in view.walk_children() if isinstance(c, Act | FollowUp)]
    assert [cast(Any, b).item.label for b in buttons] == [
        "🏆 Kveldens priser", "Hva fikser vi på Sszorak til neste gang?", "Hvor sto alle på pull 25?"]
    assert all(b.asker == 0 for b in buttons if isinstance(b, FollowUp))  # anyone's to ask
    assert bot.store.pull_for_message(77) == ("NIGHT1", 31)
    assert json.loads(bot.store.state(recap.last_key(1001)))["pick"] == "pull_map"


async def test_a_night_that_isnt_the_guilds_is_noted_without_a_post(bot, monkeypatch, settings):
    quiet = recap.REPORT_QUIET_S // 60

    async def guild_reports(guild_id, since_ms):
        return [report("PUG", 200, quiet + 3)]
    bot.library.wcl = SimpleNamespace(guild_reports=guild_reports)

    async def loaded(code, max_age_s=None):
        async def doubt():
            return "most of its players aren't in the guild"
        return SimpleNamespace(code=code, guild_run_doubt=doubt)
    monkeypatch.setattr(bot.library, "report", loaded)
    assert not await bot._recap(settings, 50, NOW / 1000)
    assert json.loads(bot.store.state(recap.last_key(1001)))["code"] == "PUG"


async def test_admins_choose_the_recaps_channel(bot, settings):
    said: list[str | None] = []

    async def say(text=None, *, ephemeral=False):
        assert ephemeral
        said.append(text)

    class Here(discord.TextChannel):
        def __init__(self):
            self.id = 40

        @property
        def mention(self):
            return "<#40>"

        def permissions_for(self, obj):
            return SimpleNamespace(view_channel=True, send_messages=True)
    interaction = cast(Any, SimpleNamespace(guild=SimpleNamespace(id=1001, me=None), channel=Here(),
                                            response=SimpleNamespace(send_message=say)))
    callback = cast(Any, commands.build(bot).get_command("recap")).callback
    await callback(interaction)
    assert bot.store.recap_channel(1001) == 40 and "posted in <#40>" in str(said[0])
    await callback(interaction, stop=True)
    assert bot.store.recap_channel(1001) is None and "no longer posted" in str(said[1])
    interaction.guild.id = 1002  # not set up
    await callback(interaction)
    assert "/wipebot setup" in str(said[2]) and bot.store.recap_channel(1002) is None
