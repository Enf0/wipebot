"""The admins' slash commands: /wipebot setup and the raid note or plan they save."""
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from discord import app_commands

from tests.support import NOTE, PLAN
from wipebot.core.wcl import WCLError
from wipebot.discord import budget, commands, keys
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
    verified = replace(first, verified_by=7, verified_at=1000.0)  # the guild is the server's own
    assert updated_settings(verified, 1001, GUILD, 333) == replace(verified, required_role_id=333)  # linked again
    assert updated_settings(verified, 1001, None, None, 222).verified_at == 1000.0
    relinked = updated_settings(verified, 1001, moved, None)  # another guild: no longer verified
    assert (relinked.wcl_guild_id, relinked.verified_by, relinked.verified_at) == (555, None, None)


class FakeInteraction:
    """A slash command's use in server 1001, whose answers (all ephemeral) are kept in `said`."""

    def __init__(self):
        self.said: list[str] = []
        self.views: list[Any] = []
        raiders = SimpleNamespace(id=111, mention="@Raider")
        self.guild = SimpleNamespace(id=1001, get_role=lambda i: raiders if i == 111 else None)
        self.user = SimpleNamespace(id=7, display_name="Rowan")
        self.response = SimpleNamespace(defer=self._nothing, send_message=self._say)
        self.followup = SimpleNamespace(send=self._say)

    async def _nothing(self, **kwargs):
        pass

    async def _say(self, text=None, ephemeral=False, view=None):
        assert ephemeral  # only the admin sees it
        self.said.append(text or "")
        self.views.append(view)


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
    assert "Give the guild's warcraftlogs.com link" in await setup(role=SimpleNamespace(id=111))
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


async def test_on_a_shared_bot_a_guild_is_a_servers_own_once_an_admin_with_a_character_in_it_signed_in(
        bot, setup, monkeypatch):
    """A link alone says nothing about whose guild it is: the server's raid notes and plans turn on when an admin who
    ran /wipebot setup is signed in with Warcraft Logs and has a character in the guild, asked of Warcraft Logs then."""
    monkeypatch.setenv("WIPEBOT_WCL_CALLBACK", "https://example.org/signed-in")
    bot.own_keys = True
    bot.store._db.execute("DELETE FROM server_settings")
    asked_with: list[str] = []
    guilds: set[int] | Exception = {555}

    async def claimed_guilds(token):
        asked_with.append(token)
        if isinstance(guilds, Exception):
            raise guilds
        return guilds
    monkeypatch.setattr(commands.accounts, "claimed_guilds", claimed_guilds)
    link = "https://www.warcraftlogs.com/guild/id/14169"
    told = await setup(link=link)  # not signed in: the link is saved, the notes stay off
    assert told.startswith("wipebot is set up:") and told.endswith("You aren't signed in yet.")
    assert ("Raid notes and plans are off here until an admin with a character in **The Copper Lanterns** (claimed on "
            "Warcraft Logs) runs `/login`, then `/wipebot setup`. Everything else works already.") in told
    assert (s := bot.store.settings(1001)) and s.set_up and s.verified_at is None and not asked_with
    bot.store.save_login(7, 42, "Rowan#1", "rowan's token", time.time() + 3600)
    told = await setup()  # signed in, with characters in another guild only
    assert "You're signed in as **Rowan#1**, but none of the characters claimed on that Warcraft Logs account" in told
    assert asked_with == ["rowan's token"] and bot.store.settings(1001).verified_at is None
    guilds = commands.accounts.AccountError("Warcraft Logs currentUser: 502")
    assert "Warcraft Logs didn't say which guilds your characters are in" in await setup()
    assert bot.store.settings(1001).verified_at is None
    guilds = {555, 14169}
    told = await setup()  # run again by a member: verified
    assert ("Raid notes and plans are on here: one of the characters you claimed on Warcraft Logs is in **The Copper "
            "Lanterns**.\nNo raid note or plan saved yet") in told and "are off here" not in told
    assert (s := bot.store.settings(1001)) and s.verified_by == 7 and s.verified_at
    asked_with.clear()
    told = await setup(link=link, role=SimpleNamespace(id=111))  # the same guild again: still its own, not asked
    assert "Raid notes" not in told and bot.store.settings(1001) == replace(s, required_role_id=111)
    assert "are off here" not in await setup() and not asked_with
    bot.store._db.execute("DELETE FROM server_settings")
    told = await setup(link=link)  # signed in and a member: verified at once
    assert told.endswith("Raid notes and plans are on here: one of the characters you claimed on Warcraft Logs is in "
                         "**The Copper Lanterns**.")
    assert (s := bot.store.settings(1001)) and s.verified_by == 7
    bot.store._db.execute("DELETE FROM server_settings")
    bot.store.drop_login(7)
    bot.store.save_login(7, 42, "Rowan#1", "rowan's private token", time.time() + 3600, private=True)
    asked_with.clear()
    told = await setup(link=link)  # signed in for private reports only: that sign-in says who they are as well
    assert told.endswith("in **The Copper Lanterns**.") and asked_with == ["rowan's private token"]
    assert bot.store.settings(1001).verified_by == 7


async def test_a_shared_bot_without_sign_in_says_so_and_a_hosts_own_bot_asks_for_no_proof(bot, setup, monkeypatch):
    async def claimed_guilds(token):
        raise AssertionError("not asked")
    monkeypatch.setattr(commands.accounts, "claimed_guilds", claimed_guilds)
    monkeypatch.delenv("WIPEBOT_WCL_CALLBACK", raising=False)
    link = "https://www.warcraftlogs.com/guild/id/14169"
    told = await setup(link=link)  # the host's own bot: its servers' guilds are theirs
    assert told.startswith("wipebot is set up:") and "Raid notes" not in told
    assert "No raid note or plan saved yet" in await setup()
    bot.own_keys = True
    told = await setup(link=link)
    assert told.endswith("Raid notes and plans are off here: showing that **The Copper Lanterns** is this server's "
                         "guild takes a Warcraft Logs sign-in, which whoever runs this bot hasn't set up. Everything "
                         "else works already.")
    assert "No raid note or plan saved yet" not in await setup()


def test_the_experimental_sources_are_connected_on_the_command_line_only(bot, monkeypatch):
    monkeypatch.setenv("RSGG_REST_API_KEY", "key")
    group = commands.build(bot)
    assert group.get_command("raidstrats") is None and group.get_command("viserio") is None


async def test_setup_alone_shows_the_setup_and_whats_saved_and_notes_are_saved_by_paste_only(bot, setup, settings):
    group = commands.build(bot)
    assert {c.name for c in group.walk_commands()} == {"setup"}  # settings, key, news and usage post are in it
    told = await setup()
    assert "**To do:**" not in told and "**Officers**" in told and "No raid note or plan saved yet" in told
    assert "**AI key:** This server's questions are answered on wipebot's own key" in told
    save_pasted(bot.store, 1001, NOTE.read_text(encoding="utf-8"), "Rowan", at=1000.0)
    told = await setup()
    assert "Raid notes saved here (the newest per boss):\n- **Sszorak**, by Rowan" in told
    assert "**Answers end with:** the steps they went through" in told
    assert told.endswith("its source is at <https://github.com/Enf0/wipebot>.")  # the AGPL's §13


async def test_setup_alone_on_a_new_server_says_what_to_do_first_with_the_key_buttons(bot, setup):
    bot.store._db.execute("DELETE FROM server_settings")
    bot.own_keys = True
    told = await setup()
    assert told.startswith("**To do:**\n1. Add an AI key with the button below") and "\n2. Connect the guild: " \
        "`/wipebot setup link:`" in told
    def labels():
        return [cast(Any, b).label for b in commands.keycmd.KeyButtons(bot.store, 1001, True).children]
    assert labels() == ["Add AI key"]
    bot.store.save_server_key(1001, keys.ServerKey("openrouter", "sk-or-guild"), added_by=7)
    assert "Add an AI key" not in await setup()
    assert labels() == ["Change AI key", "Remove AI key"]


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
    link = "https://raidstrats.gg/planner?view=abc"
    assert not commands.looks_like_note(f"where do I stand in {link}?")  # a question about a plan
    assert commands.looks_like_note(f"<@42> {link} from pull 3")  # the link alone is one to save
    assert commands.looks_like_note(f"P1 orbs: {link}&players=Rowan%7Cwr%2C10%3AEsme%7Csh")  # with the raiders too
    with pytest.raises(FactError):
        save_pasted(store, 1001, "just some text", "Rowan")
    assert "No raid note or plan saved yet" in commands.describe_notes(store, 1001)


def test_notes_that_cant_be_read_are_said_so_in_the_setup_overview_not_as_none_saved(store, settings, monkeypatch):
    """A notes file locked by another process or damaged: the overview says it can't be read now, and doesn't fail
    or read as nothing saved."""
    from wipebot.core.notebook import Unreadable

    def locked(guild_id):
        raise Unreadable("the saved raid notes and plans can't be read right now (database is locked)")
    store.save_settings(settings)
    monkeypatch.setattr(store.notebook, "latest_notes", locked)
    said = commands.describe_notes(store, 1001)
    assert "can't be read right now" in said and "No raid note or plan saved yet" not in said


def test_a_note_needs_the_server_set_up_for_its_guild(store):
    from wipebot.core.report import FactError
    with pytest.raises(FactError, match="set up"):
        save_pasted(store, 1001, NOTE.read_text(encoding="utf-8"), "Rowan")
    assert "isn't set up here yet" in commands.describe_notes(store, 1001)


def test_budget_says_what_the_key_spent_and_has_left():
    from wipebot.discord.budget import KeyBudget, describe
    b = KeyBudget(limit=50.0, remaining=12.5, month=20.25)
    assert describe(b, 0, 0.0, None) == ("**AI budget:** $20.25 spent this month.\n"
                                         "**$12.50** left.")
    assert describe(b, 81, 20.25, "Vipps to Oscar, 12345678").splitlines()[1:] == [
        "**$12.50** left: about 50 more answers at this month's $0.25 an answer.",
        "To chip in: Vipps to Oscar, 12345678"]
    empty = describe(KeyBudget(limit=50.0, remaining=0.0, month=50.0), 0, 0.0, None)
    assert empty.endswith("It's used up, so wipebot can't answer until it's topped up.")
    assert describe(KeyBudget(None, None, 3.0), 0, 0.0, None).endswith("It has no limit set.")
    assert describe(b, 0, 0.0, None, with_bar=True).splitlines()[1] == "`███████████████░░░░░` 75% spent"
    assert describe(KeyBudget(None, None, 3.0), 0, 0.0, None, with_bar=True).count("\n") == 1


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
    assert await budget.fetch("sk-or-k") == budget.KeyBudget(limit=50, remaining=12.5, month=20.25)


async def test_a_limit_set_as_a_budget_is_read_from_the_effective_budget(monkeypatch):
    import httpx

    from wipebot.discord import budget

    def reply(request: httpx.Request) -> httpx.Response:  # as OpenRouter answered for a key given a budget, Nov 2026
        return httpx.Response(200, json={"data": {
            "limit": 0, "limit_remaining": 0, "limit_reset": "monthly", "usage_monthly": 0.0016,
            "effective_budget": {"limit_usd": 5, "spend_usd": 0.005, "remaining_usd": 4.995}}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw))
    assert await budget.fetch("sk-or-k") == budget.KeyBudget(limit=5, remaining=4.995, month=0.0016)


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
    assert "<@&111>" in admin and "name nobody" not in admin  # channels name players as DMs do
    assert "`news:`" in admin and "/wipebot key" not in admin
    assert "not set up" in commands.help_text(None, admin=True)
    assert "not set up" in commands.help_text(ServerSettings.unset(1001), admin=False)
    assert len(admin) < 2000  # one Discord message


def test_help_on_a_server_without_a_key_says_so_first_and_where_to_ask_meanwhile(settings, monkeypatch):
    """On a bot that answers only on servers' own keys, /help listed what to ask and every question was refused."""
    monkeypatch.delenv("WIPEBOT_PUBLIC_URL", raising=False)
    assert "AI key" not in commands.help_text(settings, admin=False)
    raider = commands.help_text(settings, admin=False, keyless=True)
    assert raider.startswith("**I can't answer here yet.** This server hasn't added an AI key for my answers yet: an "
                             "admin adds one with `/wipebot setup` (OpenRouter or Anthropic).\n\n**What to ask:**")
    assert "Claude" not in raider  # the host runs no connector, or the bot wasn't told where
    monkeypatch.setenv("WIPEBOT_PUBLIC_URL", "https://wipebot.example.com/")
    raider = commands.help_text(settings, admin=False, keyless=True)
    assert ("(OpenRouter or Anthropic). Until then you can ask me the same in Claude or ChatGPT, which needs no key "
            "here: add `https://wipebot.example.com/mcp` there as a connector") in raider
    assert "som ikke trenger nøkkel her: legg til `https://wipebot.example.com/mcp` der" in commands.needs_key("no")
    assert len(commands.help_text(replace(settings, required_role_id=111), admin=True, keyless=True)) < 2000


def test_usage_says_whats_left_of_the_hours_points():
    rate = {"limitPerHour": 18000, "pointsSpentThisHour": 4500.5, "pointsResetIn": 1500,
            "databaseLimitPerHour": 600, "databasePointsSpentThisHour": 700}
    text = commands.usage_text(rate)
    assert "13,500 left this hour" in text and "in 26 min" in text and ": 0 left." in text
    assert "didn't say" in commands.usage_text(None)


async def test_setup_turns_the_footer_on_or_off_even_before_the_server_is_connected(setup, bot):
    assert await setup(footer=False) == "Answers here no longer end with what they cost."
    assert bot.store.footer(1001) is False
    told = await setup(link="https://www.warcraftlogs.com/guild/id/14169", footer=True)
    assert told.startswith("wipebot is set up") and bot.store.footer(1001) is True


async def test_setup_keeps_the_steps_under_answers_or_not(setup, bot):
    assert bot.store.steps(1001) is True
    assert await setup(steps=False) == "Answers here no longer end with the steps they went through."
    assert bot.store.steps(1001) is False
    assert "now end with the steps they went through" in await setup(steps=True)
    assert bot.store.steps(1001) is True


async def test_usage_reads_the_servers_own_key_on_a_copy_that_needs_one(bot, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-host")
    bot.own_keys = True
    assert commands.budget_key(bot, 1001) is None  # never the host's key
    assert "no AI key for answers yet" in (await commands.server_usage(bot, 1001) or "")
    bot.store.save_server_key(1001, keys.ServerKey("openrouter", "sk-or-guild"), added_by=7)
    assert commands.budget_key(bot, 1001) == "sk-or-guild"
    bot.store.add_spend(1001, 7, "chat", 0.5, 0)
    bot.store.add_spend(2002, 8, "chat", 9.0, 0)  # another server's answers aren't this key's

    async def fetch(key):
        assert key == "sk-or-guild"
        return budget.KeyBudget(limit=20.0, remaining=5.0, month=15.0)
    monkeypatch.setattr(budget, "fetch", fetch)
    text = await commands.budget_text(bot.store, "sk-or-guild", 1001, with_bar=True)
    assert "**$5.00** left: about 10 more answers at this month's $0.50 an answer" in text
    assert "`███████████████░░░░░` 75% spent" in text
    bot.store.add_spend(1001, 9, "chat", 0.25, 0)  # asked in a DM, on this server's key
    line = "**This server:** 2 answers this month, $0.75, on its own OpenRouter key (`/wipebot setup`)."
    assert await commands.server_usage(bot, 1001) == line  # a raider sees what it cost, not who asked
    assert await commands.server_usage(bot, 1001, admin=True) == (
        line + " Most went to <@7> (1, $0.50), <@9> (1, $0.25), DMs included.")
    bot.store.save_server_key(1001, keys.ServerKey("anthropic", "sk-ant-x"), added_by=7)
    assert commands.budget_key(bot, 1001) is None  # Anthropic's keys say nothing of a budget


async def test_setup_sets_the_servers_language_even_before_the_server_is_connected(setup, bot):
    assert "Give the language's name" in await setup(language="1337")
    told = await setup(language="norwegian", footer=True)
    assert "now end with what they cost" in told and "The server's language is now Norwegian" in told
    assert bot.store.language(1001) == "Norwegian"
