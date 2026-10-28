"""The admins' slash commands: /wipebot setup and the raid note or plan they save."""
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from discord import app_commands

from tests.support import NOTE, PLAN
from wipebot.core.wcl import WCLError
from wipebot.discord import commands
from wipebot.discord.commands import parse_guild_link, save_pasted, updated_settings
from wipebot.discord.store import ServerSettings

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


def test_the_experimental_sources_are_connected_on_the_command_line_only(bot, monkeypatch):
    monkeypatch.setenv("RAIDSTRATS_CLIENT_ID", "app")
    monkeypatch.setenv("RAIDSTRATS_CLIENT_SECRET", "secret")
    group = commands.build(bot)
    assert group.get_command("raidstrats") is None and group.get_command("viserio") is None


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


def test_budget_says_what_the_key_spent_and_has_left():
    from wipebot.discord.budget import KeyBudget, describe
    b = KeyBudget(limit=50.0, remaining=12.5, month=20.25, total=37.5)
    assert describe(b, 0, 0.0, None) == ("**AI budget:** $20.25 spent this month, $37.50 in all.\n"
                                         "**$12.50** left of $50.00.")
    assert describe(b, 81, 20.25, "Vipps to Oscar, 12345678").splitlines()[1:] == [
        "**$12.50** left of $50.00: about 50 more answers at this month's $0.25 an answer.",
        "To chip in: Vipps to Oscar, 12345678"]
    empty = describe(KeyBudget(limit=50.0, remaining=0.0, month=50.0, total=50.0), 0, 0.0, None)
    assert empty.endswith("It's used up, so wipebot can't answer until it's topped up.")
    assert describe(KeyBudget(None, None, 3.0, 3.0), 0, 0.0, None).endswith("It has no limit set.")
    assert describe(b, 0, 0.0, None, with_bar=True).splitlines()[1] == "`███████████████░░░░░` 75% spent"
    assert describe(KeyBudget(None, None, 3.0, 3.0), 0, 0.0, None, with_bar=True).count("\n") == 1


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


def registered(bot, monkeypatch) -> set[str]:
    """Every slash command the bot registers, as typed ("/wipebot setup")."""
    return {f"/{c.qualified_name}" for c in commands.build(bot).walk_commands()
            if not isinstance(c, app_commands.Group)} | {f"/{c.name}" for c in commands.build_everyone(bot)}


def test_the_docs_list_every_command(bot, monkeypatch):
    docs = (Path(__file__).parents[2] / "docs" / "features.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `(/[a-z ]+?)`", docs, re.M))
    assert listed == registered(bot, monkeypatch)


def test_every_command_fits_discords_limits(bot):
    """Discord refuses the whole command list over one description past 100 characters (seen on a deploy)."""
    def texts(payload):
        yield payload["name"], 32
        yield payload["description"], 100
        for option in payload.get("options", []):
            yield from texts(option)
            for choice in option.get("choices", []):
                yield choice["name"], 100

    for command in [commands.build(bot), *commands.build_everyone(bot)]:
        for text, limit in texts(command.to_dict(bot.tree)):
            assert 1 <= len(text) <= limit, text


def test_help_lists_what_the_member_may_use(settings, monkeypatch):
    raider = commands.help_text(settings, admin=False)
    assert "/help" in raider and "/usage" in raider and "/wipebot" not in raider
    admin = commands.help_text(replace(settings, required_role_id=111), admin=True)
    assert "/wipebot setup" in admin
    assert "<@&111>" in admin and "in channels I name nobody." in admin
    assert "not set up" in commands.help_text(None, admin=True)
    assert "not set up" in commands.help_text(ServerSettings.unset(1001), admin=False)
    assert len(admin) < 2000  # one Discord message


def test_usage_says_whats_left_of_the_hours_points():
    rate = {"limitPerHour": 18000, "pointsSpentThisHour": 4500.5, "pointsResetIn": 1500,
            "databaseLimitPerHour": 600, "databasePointsSpentThisHour": 700}
    text = commands.usage_text(rate)
    assert "13,500 of 18,000 left" in text and "in 26 min" in text and "0 of 600 left" in text
    assert "didn't say" in commands.usage_text(None)


async def test_setup_turns_the_footer_on_or_off_even_before_the_server_is_connected(setup, bot):
    assert await setup(footer=False) == "Answers here no longer end with what they cost."
    assert bot.store.footer(1001) is False
    told = await setup(link="https://www.warcraftlogs.com/guild/id/14169", footer=True)
    assert told.startswith("wipebot is set up") and bot.store.footer(1001) is True


async def test_usage_post_on_a_shared_copy_shows_the_servers_share(bot):
    async def left(server_id):
        return 5.0, 1.25

    bot.allowance = SimpleNamespace(left=left)
    text = await commands.share_text(bot, 1001)
    assert text.startswith("**This server's free answers:** $1.25 of its $5.00 left this month")
    assert text.endswith("`███████████████░░░░░` 75% spent")
