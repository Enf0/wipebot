"""Who asked for posts after each wipe, where, and which posts a poll's pulls make due."""
import time
from typing import cast

import pytest

from wipebot.core import server
from wipebot.discord.follower import Pull
from wipebot.discord.posts import FOLLOW_WAIT_S, Following, Posts

SERVER = 1001
ENDED = (time.time() + 3600) * 1000  # the pulls end after the tests' requests
WIPE, KILL, RESET = (Pull("LIVE1", 30, "Sszorak", 12, ENDED), Pull("LIVE1", 31, "Sszorak", 13, ENDED, kill=True),
                     Pull("LIVE1", 32, "Sszorak", 14, ENDED, reset=True))


@pytest.fixture
def posts(store, library) -> Posts:
    return Posts(store, cast(server.Library, library))


@pytest.fixture
def rowan(member):
    return member(7, "Rowan")


def places(posts: Posts) -> dict:
    """Each place's request: (character, request, report)."""
    return {k: (f.character, f.request, f.code) for k, f in posts.by_server.get(SERVER, {}).items()}


async def test_a_raiders_own_feedback_goes_to_their_dms(posts, store, rowan):
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, "LIVE1", "LIVE1")
    assert told.startswith("Done: after each wipe of the guild's live report, Torvek's feedback goes to Rowan in a DM")
    assert places(posts) == {7: ("Torvek", "", "LIVE1")}  # as the log spells it, keyed by their DMs
    assert store.raider(7).character == "Torvek"  # remembered for later conversations too
    assert [f.key for f in store.all_following()] == [7]  # outlives a restart
    assert await posts.due(SERVER, "LIVE1", [WIPE, KILL, RESET]) == [(posts.by_server[SERVER][7], WIPE)]


async def test_after_a_gap_only_the_latest_wipe_gets_a_post(posts, rowan):
    """A restart or Warcraft Logs being down leaves several wipes for one poll: a post about each would come pulls
    late, one after the other."""
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, "LIVE1", "LIVE1")
    later = Pull("LIVE1", 33, "Sszorak", 15, ENDED)
    assert await posts.due(SERVER, "LIVE1", [WIPE, KILL, later, RESET]) == [(posts.by_server[SERVER][7], later)]


async def test_wipes_from_before_the_request_get_no_post(posts, rowan):
    """Nothing polls the live report while nobody asked for posts, so the first poll after a request may find wipes
    from before it."""
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, "LIVE1", "LIVE1")
    before = Pull("LIVE1", 29, "Sszorak", 11, (time.time() - 600) * 1000)
    assert await posts.due(SERVER, "LIVE1", [before]) == []
    assert await posts.due(SERVER, "LIVE1", [before, WIPE]) == [(posts.by_server[SERVER][7], WIPE)]


async def test_a_character_not_in_the_live_report_is_refused(posts, rowan):
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "Nobody"}, "LIVE1", "LIVE1")
    assert "no single player matching 'Nobody'" in told and not places(posts)


async def test_a_request_before_the_raid_logs_waits_for_the_live_report(posts, store, rowan):
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, None, "LASTWEEK")
    assert told.startswith("Done") and "once the raid starts logging" in told
    assert places(posts) == {7: ("Torvek", "", None)}
    assert await posts.due(SERVER, None, []) == [] and places(posts) == {7: ("Torvek", "", None)}  # still waiting
    assert await posts.due(SERVER, "LIVE1", [WIPE]) == [(posts.by_server[SERVER][7], WIPE)]  # its first wipe
    assert places(posts) == {7: ("Torvek", "", "LIVE1")} and store.all_following()[0].code == "LIVE1"


async def test_a_request_that_waited_too_long_for_the_raid_ends(posts, store, rowan):
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, None, "LASTWEEK")
    posts.by_server[SERVER][7].asked -= FOLLOW_WAIT_S + 1
    assert await posts.due(SERVER, None, []) == []
    assert not places(posts) and store.all_following() == []


async def test_posts_end_with_the_report_they_follow(posts, store, rowan):
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, "LIVE1", "LIVE1")
    assert await posts.due(SERVER, "NEXTNIGHT", [Pull("NEXTNIGHT", 1, "Sszorak", 1, ENDED)]) == []
    assert not places(posts) and store.all_following() == []


async def test_anyone_can_ask_for_posts_in_a_channel_and_only_they_or_an_officer_change_them(posts, member, channel,
                                                                                            rowan):
    halvard, officer = member(8, "Halvard"), member(9, "Brann")
    assert "as `request`" in await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "Torvek"}, "LIVE1",
                                                      "LIVE1", channel)  # a channel's post is what they ask for
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "who died first and why"}, "LIVE1", "LIVE1",
                                    channel)
    assert "a post here with: who died first and why" in told and "Nothing was set up here before" in told
    assert places(posts) == {20: (None, "who died first and why", "LIVE1")}
    for args in ({"stop": True}, {"request": "the deaths"}):
        assert await posts.follow_pulls(SERVER, halvard, "Halvard", args, "LIVE1", "LIVE1", channel) == (
            "Rowan asked for the posts after wipes here (who died first and why): only they, or an officer "
            "(/wipebot settings says who), can change or stop them.")
    assert places(posts) == {20: (None, "who died first and why", "LIVE1")}
    assert await posts.follow_pulls(SERVER, officer, "Brann", {"stop": True}, "LIVE1", "LIVE1", channel,
                                    officer=True) == \
        "Stopped: no more posts after wipes here."
    assert not places(posts)
    assert await posts.follow_pulls(SERVER, halvard, "Halvard", {"stop": True}, "LIVE1", "LIVE1", channel) == \
        "There were no posts after wipes to stop."


async def test_a_person_has_posts_in_one_channel_and_a_server_a_few(posts, member, channel, rowan, monkeypatch):
    thread = type(channel)(21, "pull-talk")
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "who died first"}, "LIVE1", "LIVE1", channel)
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "who died first"}, "LIVE1", "LIVE1", thread)
    assert told.endswith("Their posts in #raid stop: one channel per person.")
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "Torvek"}, "LIVE1", "LIVE1")  # and their DMs
    assert places(posts) == {21: (None, "who died first", "LIVE1"), 7: ("Torvek", "", "LIVE1")}
    assert (await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "x" * 201}, "LIVE1", "LIVE1", thread)
            ).startswith("That request is too long")
    monkeypatch.setattr("wipebot.discord.posts.MAX_PER_SERVER", 2)
    told = await posts.follow_pulls(SERVER, member(8, "Halvard"), "Halvard", {"character": "Mirelle"}, "LIVE1",
                                    "LIVE1")
    assert told.startswith("This server already has 2 requests for posts after wipes") and len(places(posts)) == 2
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "the\nsoaks"}, "LIVE1", "LIVE1", channel)  # moved
    assert places(posts) == {20: (None, "the soaks", "LIVE1"), 7: ("Torvek", "", "LIVE1")}


async def test_a_new_request_in_a_place_replaces_the_one_before(posts, channel, rowan):
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"character": "torvek"}, "LIVE1", "LIVE1")
    told = await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "the raid's deaths"}, "LIVE1", "LIVE1")
    assert "a post to Rowan in a DM with: the raid's deaths" in told
    assert "It replaces the request made here before: their own feedback." in told
    await posts.follow_pulls(SERVER, rowan, "Rowan", {"request": "who died first"}, "LIVE1", "LIVE1", channel)
    assert places(posts) == {7: (None, "the raid's deaths", "LIVE1"), 20: (None, "who died first", "LIVE1")}
    due = await posts.due(SERVER, "LIVE1", [WIPE])
    assert sorted(f.key for f, _ in due) == [7, 20]  # one post in each place


def test_a_new_setup_stops_every_request_in_the_server(posts, store, rowan):
    posts.add(SERVER, Following(rowan, "Rowan", "Torvek", "LIVE1"))
    posts.add(2002, Following(rowan, "Rowan", "Torvek", "OTHER"))
    posts.clear(SERVER)
    assert not places(posts) and [f.discord_guild_id for f in store.all_following()] == [2002]
