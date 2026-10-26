"""The answers' layout and the pictures' actions: buttons and menus that keep what they act on in their custom_id
and answer the one who clicked with the next picture."""
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from tests.support.recorded import CASES, Replay, serve_from
from wipebot.discord import messages
from wipebot.discord.messages import Act, Pick, answer_layout, pictures_for, tool_of

PULL = CASES["pull-summary"].args  # Sszorak pull 13 of KBYT4xXn7VNjDgh8


@pytest.fixture
def recorded(monkeypatch):
    serve_from(monkeypatch, Replay())


def test_a_button_keeps_what_it_acts_on_in_its_id_and_comes_back_from_it():
    b = Act("death", "KBYT4xXn7VNjDgh8", "Sszorak", 13, "Scopidruid")
    assert b.item.custom_id == "wb|death|KBYT4xXn7VNjDgh8|Sszorak|13|Scopidruid" and b.item.label == "First death"
    match = Act.__discord_ui_compiled_template__.fullmatch(b.item.custom_id)  # as discord.py dispatches a click
    assert match and (match["what"], match["pull"], match["player"]) == ("death", "13", "Scopidruid")
    assert tool_of("death", "KBYT4xXn7VNjDgh8", "Sszorak", "13", "Scopidruid") == (
        "death_recap", {"report": "KBYT4xXn7VNjDgh8", "boss": "Sszorak", "pull": 13, "player": "Scopidruid"})
    assert tool_of("top", "R", "B", "2", "P")[1]["aspects"] == ["casts"]  # compare_with_top's view: the casts
    assert messages._id("wb", "x" * 120) is None and messages._id("wb", "a|b") is None  # what an id can't hold


async def test_each_view_gets_its_actions_on_its_last_picture(recorded):
    pull = await pictures_for([(b"1", "pull_summary", PULL)])
    assert pull[0].link == "https://www.warcraftlogs.com/reports/KBYT4xXn7VNjDgh8#fight=21"
    mine, death, players = pull[0].actions
    assert isinstance(mine, Act) and (mine.what, mine.pull, mine.player) == ("mypull", "13", "")  # the clicker's own
    assert isinstance(death, Act) and death.player == "Scopidruid"  # the pull's first death
    assert isinstance(players, Pick) and 0 < len(players.item.options) <= messages.CHOICES
    player = {**PULL, "player": "Scopidruid"}
    night = await pictures_for([(b"1", "player_night", player), (b"2", "player_night", player)])
    assert night[0].actions == () and [type(a) for a in night[1].actions] == [Pick]  # under the last panel
    menu = night[1].actions[0]
    assert isinstance(menu, Pick) and menu.item.options[0].label.startswith("Pull 33 · ")  # the latest pull first
    card = await pictures_for([(b"1", "player_pull", player)])
    assert [a.what for a in card[0].actions] == ["top", "night"]
    against = await pictures_for([(b"1", "compare_with_top", {**player, "aspects": ["casts"]})])
    assert [a.what for a in against[0].actions] == ["night"]


async def test_four_pictures_with_their_actions_fit_in_one_message(recorded):
    drawn = [(b"1", "pull_summary", PULL), (b"2", "player_pull", {**PULL, "player": "Scopidruid"}),
             (b"3", "death_recap", {**PULL, "player": "Scopidruid"}), (b"4", "progress", {"report": PULL["report"],
                                                                                         "boss": "Sszorak"})]
    view = answer_layout("**Spread out.**", await pictures_for(drawn))
    assert view.content_length() <= 4000 and len(list(view.walk_children())) <= 40  # Discord's limits


async def test_a_button_two_pictures_share_goes_once(recorded):
    """Two pictures of one player each have "Their night": Discord refuses a message with an id twice."""
    player = {**PULL, "player": "Scopidruid"}
    pictures = await pictures_for([(b"1", "player_pull", player), (b"2", "compare_with_top", {**player,
                                                                                              "aspects": ["casts"]})])
    view = answer_layout("", pictures)
    ids = [i for c in view.walk_children() if (i := getattr(c, "custom_id", None))]
    assert len(ids) == len(set(ids)) and sum(i.startswith("wb|night|") for i in ids) == 1


class Clicked:
    """A click on an action: what the bot answered, to whom."""

    def __init__(self, user_id: int = 7, store: Any = None):
        self.user = SimpleNamespace(id=user_id)
        self.client = SimpleNamespace(store=store)
        self.said: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, send_message=self._send)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, **kwargs):
        self.said.append({"deferred": kwargs})

    async def _send(self, content=None, **kwargs):
        self.said.append({"content": content, **kwargs})


async def test_a_click_answers_the_clicker_alone_with_the_next_picture_and_its_actions(recorded, monkeypatch):
    drawn: list[tuple[str, dict]] = []

    async def picture(tool, args):
        drawn.append((tool, args))
        return [b"WEBP"]
    monkeypatch.setattr(messages.host, "picture", picture)
    click = Clicked()
    await Act("death", PULL["report"], "Sszorak", 13, "Scopidruid").callback(cast(Any, click))
    assert drawn == [("death_recap", {"report": PULL["report"], "boss": "Sszorak", "pull": 13, "player": "Scopidruid"})]
    deferred, sent = click.said
    assert deferred == {"deferred": {"ephemeral": True, "thinking": True}} and sent["ephemeral"] is True
    assert [f.filename for f in sent["files"]] == ["wipebot-1.webp"]
    labels = [(c.item if isinstance(c, Act) else c).label for c in sent["view"].walk_children()
              if isinstance(c, discord.ui.Button | Act)]
    assert labels == ["Open in Warcraft Logs", "Their pull"]  # the death's own action, to go on from there


async def test_my_night_needs_the_clickers_character(recorded, monkeypatch):
    async def picture(tool, args):
        return [b"WEBP"]
    monkeypatch.setattr(messages.host, "picture", picture)
    unknown = Clicked(store=SimpleNamespace(raider=lambda uid: SimpleNamespace(character=None)))
    await Act("mynight", PULL["report"], "Sszorak").callback(cast(Any, unknown))
    assert unknown.said[0]["ephemeral"] and "which character is yours" in unknown.said[0]["content"]
    known = Clicked(store=SimpleNamespace(raider=lambda uid: SimpleNamespace(character="Scopidruid")))
    await Act("mynight", PULL["report"], "Sszorak").callback(cast(Any, known))
    assert [f.filename for f in known.said[-1]["files"]] == ["wipebot-1.webp"]


async def test_how_did_i_do_draws_the_clickers_own_card_on_the_pull(recorded, monkeypatch):
    drawn: list[tuple[str, dict]] = []

    async def picture(tool, args):
        drawn.append((tool, args))
        return [b"WEBP"]
    monkeypatch.setattr(messages.host, "picture", picture)
    known = Clicked(store=SimpleNamespace(raider=lambda uid: SimpleNamespace(character="Scopidruid")))
    await Act("mypull", PULL["report"], "Sszorak", 13).callback(cast(Any, known))
    assert drawn == [("player_pull", {"report": PULL["report"], "boss": "Sszorak", "pull": 13, "player": "Scopidruid"})]
    assert known.said[-1]["ephemeral"] and [f.filename for f in known.said[-1]["files"]] == ["wipebot-1.webp"]
    # a character not in the pull (an alt, a night on the bench) is told so, not handed an error
    benched = Clicked(store=SimpleNamespace(raider=lambda uid: SimpleNamespace(character="Nobodyhere")))
    await Act("mypull", PULL["report"], "Sszorak", 13).callback(cast(Any, benched))
    assert len(drawn) == 1 and "wasn't in Sszorak pull 13" in benched.said[0]["content"]


def test_a_placed_picture_sits_after_the_part_it_backs_and_the_rest_go_under_it_all():
    text = "**Strong night.**\n1. Coiled Altar: top 3.\n2. Twin Fangs: never died."
    at = text.index("2.")
    view = answer_layout(text, [messages.Picture("wipebot-1.webp", at=at), messages.Picture("wipebot-2.webp"),
                                messages.Picture("wipebot-3.webp", at=at)])
    shown = [c.content if isinstance(c, discord.ui.TextDisplay) else
             [i.items[0].media.url for i in cast(discord.ui.Container, c).children
              if isinstance(i, discord.ui.MediaGallery)]
             for c in view.children]
    assert shown == ["**Strong night.**\n1. Coiled Altar: top 3.",
                     ["attachment://wipebot-1.webp"], ["attachment://wipebot-3.webp"],  # a view's panels, together
                     "2. Twin Fangs: never died.", ["attachment://wipebot-2.webp"]]
    placed_at_end = answer_layout(text, [messages.Picture("wipebot-1.webp", at=len(text))])
    assert [type(c).__name__ for c in placed_at_end.children] == ["TextDisplay", "Container"]
