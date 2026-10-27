"""The admins' slash commands: /wipebot setup and the raid note or plan they save."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import NOTE, PLAN
from wipebot.core.wcl import WCLError
from wipebot.discord import commands
from wipebot.discord.commands import parse_guild_link, save_pasted, updated_settings

GUILD = {"id": 14169, "name": "The Copper Lanterns", "server": {"slug": "argent-dawn", "region": {"slug": "EU"}}}


@pytest.mark.parametrize("link, expected", [
    ("https://www.warcraftlogs.com/guild/id/14169", 14169),
    ("https://www.warcraftlogs.com/guild/id/14169#zone=46", 14169),
    ("https://www.warcraftlogs.com/guild/eu/argent-dawn/the%20copper%20lanterns",
     ("eu", "argent-dawn", "the copper lanterns")),
    ("https://www.warcraftlogs.com/guild/EU/tarren-mill/Some+Guild?zone=46", ("eu", "tarren-mill", "Some Guild")),
    ("https://www.warcraftlogs.com/guild/eu/%D0%B3%D0%BE%D1%80%D0%B4%D1%83%D0%BD%D0%BD%D0%B8/X",
     ("eu", "гордунни", "X")),
])
def test_parse_guild_link(link, expected):
    assert parse_guild_link(link) == expected


def test_parse_guild_link_rejects_other_pages():
    with pytest.raises(ValueError, match="guild page"):
        parse_guild_link("https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8")


def test_setup_changes_only_the_options_given(settings):
    with pytest.raises(ValueError, match="link"):
        updated_settings(None, 1001, None, 111)  # the first time needs the guild
    first = updated_settings(None, 1001, GUILD, 111)
    assert first == replace(settings, required_role_id=111)
    moved = {**GUILD, "id": 555, "name": "Other Guild"}
    assert updated_settings(first, 1001, moved, None) == replace(first, wcl_guild_id=555, wcl_guild="Other Guild")
    officers = updated_settings(first, 1001, None, None, 222)
    assert officers == replace(first, officer_role_id=222)
    assert updated_settings(officers, 1001, moved, 333).officer_role_id == 222  # kept


class FakeInteraction:
    """A slash command's use in server 1001, whose answers (all ephemeral) are kept in `said`."""

    def __init__(self):
        self.said: list[str] = []
        raiders = SimpleNamespace(id=111, mention="@Raider")
        self.guild = SimpleNamespace(id=1001, get_role=lambda i: raiders if i == 111 else None)
        self.user = SimpleNamespace(display_name="Rowan")
        self.response = SimpleNamespace(defer=self._nothing, send_message=self._say)
        self.followup = SimpleNamespace(send=self._say)

    async def _nothing(self, **kwargs):
        pass

    async def _say(self, text, ephemeral=False):
        assert ephemeral  # only the admin sees it
        self.said.append(text)


@pytest.fixture
def setup(bot) -> Any:
    """/wipebot setup, as `await setup(link=..., role=...)`; Warcraft Logs finds only GUILD."""
    async def guild_by_id(guild_id):
        if guild_id == 666:
            raise WCLError("Warcraft Logs is down")
        return GUILD if guild_id == GUILD["id"] else None
    bot.library.wcl = SimpleNamespace(guild_by_id=guild_by_id)
    callback = cast(Any, commands.build(bot).get_command("setup")).callback

    async def run(**options) -> str:
        interaction = FakeInteraction()
        await callback(interaction, **options)
        return interaction.said[-1]
    return run


async def test_setup_connects_the_server_to_its_guild(bot, setup):
    bot.store._db.execute("DELETE FROM server_settings")
    assert "Give the guild's warcraftlogs.com link" in await setup()
    assert "no guild with id 123" in await setup(link="https://www.warcraftlogs.com/guild/id/123")
    assert "Couldn't reach Warcraft Logs" in await setup(link="https://www.warcraftlogs.com/guild/id/666")
    assert bot.store.settings(1001) is None
    told = await setup(link="https://www.warcraftlogs.com/guild/id/14169", role=SimpleNamespace(id=111))
    assert told.startswith("wipebot is set up:") and "**Warcraft Logs guild:** The Copper Lanterns" in told
    assert "posts after wipes): members who can manage" in told
    assert bot.store.settings(1001).required_role_id == 111
    told = await setup(officers=SimpleNamespace(id=222))
    assert ("**Officers** (save the raid note or plan by pasting it to wipebot, and change or stop anyone's posts "
            "after wipes): <@&222>") in told
    assert (s := bot.store.settings(1001)) and (s.required_role_id, s.officer_role_id) == (111, 222)


def test_raidstrats_is_a_command_only_where_the_host_set_up_an_app(bot, monkeypatch):
    for name in ("RAIDSTRATS_CLIENT_ID", "RAIDSTRATS_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert commands.build(bot).get_command("raidstrats") is None
    monkeypatch.setenv("RAIDSTRATS_CLIENT_ID", "app")
    monkeypatch.setenv("RAIDSTRATS_CLIENT_SECRET", "secret")
    assert commands.build(bot).get_command("raidstrats") is not None


async def test_settings_show_the_setup_and_whats_saved_and_notes_are_saved_by_paste_only(bot, settings):
    group = commands.build(bot)
    assert group.get_command("note") is None  # pasting to the bot replaces /wipebot note
    show = cast(Any, group.get_command("settings")).callback
    interaction = FakeInteraction()
    await show(interaction)
    assert "**Officers**" in interaction.said[-1] and "No raid note or plan saved yet" in interaction.said[-1]
    save_pasted(bot.store, 1001, NOTE.read_text(encoding="utf-8"), "Rowan", at=1000.0)
    await show(interaction)
    assert "Raid notes saved here (the newest per boss):\n- **Sszorak**, by Rowan" in interaction.said[-1]
    assert interaction.said[-1].endswith("its source is at <https://github.com/Enf0/wipebot>.")  # the AGPL's §13


def test_names_are_turned_on_and_off_in_a_channel_and_its_threads(bot, settings):
    assert "run `/wipebot setup` first" in commands.names_command(bot.store, 2002, SimpleNamespace(id=20), True)
    told = commands.names_command(bot.store, 1001, SimpleNamespace(id=21, parent_id=20), True)  # in a thread
    assert told.startswith("Names are on in <#20>") and bot.store.naming_channels(1001) == {20}
    assert "<#20>" in commands.describe(settings, bot.store.naming_channels(1001))
    commands.names_command(bot.store, 1001, SimpleNamespace(id=20, parent_id=None), False)
    assert bot.store.naming_channels(1001) == set()


async def test_a_new_setup_stops_the_servers_posts_after_wipes(bot, setup, member):
    from wipebot.discord.posts import Following
    bot.posts.add(1001, Following(member(7, "Rowan"), "Rowan", "Torvek", "LIVE1"))
    await setup(link="https://www.warcraftlogs.com/guild/id/14169")
    assert not bot.posts.by_server.get(1001) and 1001 not in bot.followers  # the guild may have changed


def test_a_raid_plan_pasted_again_is_a_new_version_and_every_version_is_kept(store, settings):
    store.save_settings(settings)
    export = PLAN.read_text(encoding="utf-8")
    first = save_pasted(store, 1001, export, "Rowan", at=1000.0)
    assert "(Sszorak): 16 spots" in first and "side panel:" in first and "a new version" not in first
    assert "(a new version)" in save_pasted(store, 1001, f"tonight's: {export}", "Halvard", at=2000.0)
    saved = store.notebook.plans(14169)  # kept for the server's guild
    assert [(p.boss, p.saved_by, p.saved_at) for p in saved] == [("Sszorak", "Rowan", 1000.0),
                                                                 ("Sszorak", "Halvard", 2000.0)]
    assert saved[0].plan == export.strip()
    assert [p.saved_by for p in store.notebook.latest_plans(14169)] == ["Halvard"]
    assert store.notebook.plans(2002) == []
    assert "Raid plans saved here" in commands.describe_notes(store, 1001)


def test_a_past_nights_note_saved_from_the_command_line_counts_for_that_nights_pulls(store, settings, capsys):
    from wipebot import __main__ as cli
    store.save_settings(settings)
    cli.save_note(str(NOTE), "2026-10-12 18:00", "Rowan", None, store)
    assert "**Sszorak** (Mythic): 59 timed cooldowns" in capsys.readouterr().out
    pull_42 = datetime(2026, 10, 12, 21, 30, tzinfo=UTC).timestamp()
    n = store.notebook.note_for(14169, 3420, pull_42)
    assert n and n.saved_by == "Rowan" and n.note.startswith("EncounterID")
    assert "Raid notes saved here" in commands.describe_notes(store, 1001)


def test_a_paste_counts_from_the_pull_the_message_names():
    assert commands.from_pull("forgot this last night, from pull 7\nEncounterID:3421;...") == (7, None)
    assert commands.from_pull("gjaldt fra pull #3 https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8") == (
        3, "KBYT4xXn7VNjDgh8")
    assert commands.from_pull("EncounterID:3421;Difficulty:Mythic;Name:Twin Fangs") is None


def test_text_that_isnt_a_note_or_plan_is_refused(store, settings):
    from wipebot.core.report import FactError
    store.save_settings(settings)
    assert not commands.looks_like_note("just some text")
    with pytest.raises(FactError):
        save_pasted(store, 1001, "just some text", "Rowan")
    assert "No raid note or plan saved yet" in commands.describe_notes(store, 1001)


def test_a_note_needs_the_server_set_up_for_its_guild(store):
    from wipebot.core.report import FactError
    with pytest.raises(FactError, match="set up"):
        save_pasted(store, 1001, NOTE.read_text(encoding="utf-8"), "Rowan")
    assert "isn't set up here yet" in commands.describe_notes(store, 1001)


async def test_raidstrats_login_status_and_connecting_from_discord(store, settings, monkeypatch):
    from wipebot.core import raidstrats
    store.save_settings(settings)
    assert "aren't fetched from raidstrats.gg" in commands.describe_login(store, 1001)
    synced = []

    async def connect(pasted, state):
        if state != "s1":
            raise raidstrats.RaidstratsError("that isn't the address from this login")
        return "Enfo-2581", {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    async def sync(book, guild):
        synced.append(guild)
        book.save_plan(guild, "Sszorak", "p1", "Spread", "raidstrats-addon-x", "raidstrats.gg")
        book.fetched(guild)
    monkeypatch.setattr(raidstrats, "connect", connect)
    monkeypatch.setattr(raidstrats, "sync", sync)
    assert "Not connected: that isn't the address" in await commands.connect_raidstrats(
        store, 1001, "http://localhost:8765/callback?code=c&state=s2", "s2", "Rowan")
    reply = await commands.connect_raidstrats(store, 1001, "http://localhost:8765/callback?code=c&state=s1", "s1",
                                              "Rowan")
    assert "Connected as **Enfo-2581**" in reply and "**Spread** (Sszorak)" in reply and synced == [14169]
    status = commands.describe_login(store, 1001)
    assert "as **Enfo-2581**, connected by Rowan" in status and "last fetched" in status


async def test_viserio_key_status_and_connecting_from_discord(store, settings, monkeypatch):
    from wipebot.core import viserio
    store.save_settings(settings)
    assert "aren't read from Viserio Cooldowns" in commands.describe_viserio(store, 1001)

    async def connect(book, guild, key, named, by):
        assert named == ("The Copper Lanterns", "argent-dawn", "eu")
        if key != "key-1":
            raise viserio.ViserioError("the key is for another group")
        book.save_viserio_key(guild, key, "Lanterns", by)
        return viserio.Group("g1", "Lanterns", "the copper lanterns", "argent-dawn", "eu")

    async def upcoming(key):
        return None, [viserio.Night({"date": "2026-10-26", "name": "Raid"}, [])]
    monkeypatch.setattr(viserio, "connect", connect)
    monkeypatch.setattr(viserio, "upcoming", upcoming)
    assert "Not connected: the key is for another group" in await commands.connect_viserio(store, 1001, "x", "Rowan")
    reply = await commands.connect_viserio(store, 1001, "key-1", "Rowan")
    assert "Connected to **Lanterns**" in reply and "2026-10-26 Raid" in reply
    assert "group **Lanterns**, connected by Rowan" in commands.describe_viserio(store, 1001)


def test_budget_says_what_the_key_spent_and_has_left():
    from wipebot.discord.budget import KeyBudget, describe
    b = KeyBudget(limit=50.0, remaining=12.5, month=20.25, total=37.5)
    assert describe(b, 0, 0.0, None) == ("wipebot's AI budget: **$20.25** spent this month, $37.50 in all.\n"
                                         "**$12.50** left of $50.00.")
    assert describe(b, 81, 20.25, "Vipps to Oscar, 12345678").splitlines()[1:] == [
        "**$12.50** left of $50.00: about 50 more answers at this month's $0.25 an answer.",
        "To chip in: Vipps to Oscar, 12345678"]
    empty = describe(KeyBudget(limit=50.0, remaining=0.0, month=50.0, total=50.0), 0, 0.0, None)
    assert empty.endswith("It's used up, so wipebot can't answer until it's topped up.")
    assert describe(KeyBudget(None, None, 3.0, 3.0), 0, 0.0, None).endswith("It has no limit set.")


async def test_budget_reads_the_key_from_openrouter(monkeypatch):
    import httpx

    from wipebot.discord import budget

    def reply(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openrouter.ai/api/v1/key"
        assert request.headers["Authorization"] == "Bearer sk-or-k"
        # fields as OpenRouter's limits guide gives them; BYOK spend counts with credit spend
        return httpx.Response(200, json={"data": {"limit": 50, "limit_remaining": 12.5, "usage": 30.0,
                                                  "usage_monthly": 1.0, "byok_usage": 7.5,
                                                  "byok_usage_monthly": 19.25}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw))
    assert await budget.fetch("sk-or-k") == budget.KeyBudget(limit=50, remaining=12.5, month=20.25, total=37.5)
