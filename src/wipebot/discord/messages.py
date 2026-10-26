"""How the bot's answers look in Discord, as Components V2 messages: the text, then each picture whole in a box with
a button to its pull on Warcraft Logs and the picture's own actions (Act, Pick), edged gold for a new guild best.

An action answers the one who clicked with the next picture, which only they see (an ephemeral reply), with its own
actions in turn: the pull's first death, a player's card, against top players, their night, a pull of the night, and
the clicker's own card on a pull or their own night ("How did I do?", "My night", by the character the store has).
Each button and menu carries what it acts on in its custom_id (report, boss, pull, player), so the ones on old
messages still work after a restart (discord.py's DynamicItem, registered in WipeBot.setup_hook)."""
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import discord

from wipebot.core import server
from wipebot.core.pulls import is_reset, wipe_pct
from wipebot.core.report import FactError

from . import host
from .host import ToolCall

log = logging.getLogger(__name__)

PULL_LINK = re.compile(r"https://www\.warcraftlogs\.com/reports/\w+#fight=\d+")
GUILD_BEST = "**new guild best!**"  # pull_summary's post marks a wipe that beat every earlier log so (pulls.best_marks)
SEP = "|"  # between the parts of a custom_id; no report code, boss or character name has one
ID_CHARS = 100  # Discord's limit for a custom_id
CHOICES = 25  # Discord's limit for a menu's options


@dataclass(frozen=True)
class Picture:
    """A picture sent with an answer: its attachment's name, the pull it shows on Warcraft Logs, whether that pull
    was a new guild best, and what can be done from it (buttons and menus, under it)."""
    name: str
    link: str | None = None
    best: bool = False
    actions: tuple[Act | Pick, ...] = field(default=(), compare=False)

    @classmethod
    def of(cls, name: str, call: ToolCall) -> Picture:
        link = PULL_LINK.search(call.result)
        return cls(name, link[0] if link else None, call.name == "pull_summary" and GUILD_BEST in call.result)


def answer_layout(text: str, pictures: Sequence[Picture] = (), actions: Sequence[Act] = ()) -> discord.ui.LayoutView:
    """A message as Components V2: its text (none for a picture alone) with `actions` under it (the message's own
    buttons, not a picture's), then each picture (an attachment by its name) whole in a gallery of its own, boxed with
    its buttons and menus (one already above left out), the box edged gold for a new guild best. Several attachments
    on a plain message are shown as a grid, each cropped to its tile."""
    view = discord.ui.LayoutView(timeout=None)
    used: set[str] = set()  # Discord refuses a message with two items of one custom_id: the second does the same
    if text:
        view.add_item(discord.ui.TextDisplay(text))
    if actions:
        view.add_item(discord.ui.ActionRow(*(cast(Any, a) for a in actions)))
        used.update(a.item.custom_id for a in actions if a.item.custom_id)
    for p in pictures:
        items: list[discord.ui.Item] = [discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{p.name}"))]
        buttons: list[discord.ui.Item] = [discord.ui.Button(label="Open in Warcraft Logs", url=p.link)] \
            if p.link else []
        acts = [a for a in p.actions if a.item.custom_id not in used]
        used.update(a.item.custom_id for a in acts if a.item.custom_id)
        buttons += [a for a in acts if isinstance(a, Act)]
        if buttons:
            items.append(discord.ui.ActionRow(*buttons))
        items += [discord.ui.ActionRow(cast(Any, a)) for a in acts if isinstance(a, Pick)]  # a menu: a row
        view.add_item(discord.ui.Container(*items, accent_colour=discord.Colour.gold() if p.best else None))
    return view


def _id(prefix: str, *parts: object) -> str | None:
    """A custom_id from its parts, or None when it can't hold them (too long, or a part with SEP in it)."""
    texts = ["" if p is None else str(p) for p in parts]
    out = SEP.join([prefix, *texts])
    return out if len(out) <= ID_CHARS and not any(SEP in t for t in texts) else None


_FIELDS = r"(?P<what>[a-z]+)\|(?P<report>[^|]*)\|(?P<boss>[^|]*)\|(?P<pull>[^|]*)\|(?P<player>[^|]*)"
LABELS = {"death": "First death", "top": "Against top", "night": "Their night", "ppull": "Their pull",
          "mynight": "My night", "mypull": "How did I do?"}
MINE = {"mynight", "mypull"}  # actions on the clicker's own character


class Act(discord.ui.DynamicItem[discord.ui.Button], template=r"wb\|" + _FIELDS):
    """A button: what it does (LABELS) and what to: the report, boss, pull and player."""

    def __init__(self, what: str, report: str, boss: str = "", pull: str | int = "", player: str = "") -> None:
        self.what, self.report, self.boss, self.pull, self.player = what, report, boss, str(pull), player
        super().__init__(discord.ui.Button(label=LABELS[what], style=discord.ButtonStyle.secondary,
                                           custom_id=_id("wb", what, report, boss, pull, player) or "wb|none"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str], /) -> Act:
        return cls(match["what"], match["report"], match["boss"], match["pull"], match["player"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await act(interaction, self.what, self.report, self.boss, self.pull, self.player)


class Pick(discord.ui.DynamicItem[discord.ui.Select], template=r"wbs\|" + _FIELDS):
    """A menu: what a choice opens ("player": that player's pull, "ppull": the player's card on that pull, "pull": that
    pull), the report, boss, pull and player it's about, and its choices as (label, value)."""

    PLACEHOLDERS = {"player": "A player's pull", "ppull": "A pull of theirs", "pull": "A pull"}

    def __init__(self, what: str, report: str, boss: str = "", pull: str | int = "", player: str = "",
                 choices: Sequence[tuple[str, str]] = ()) -> None:
        self.what, self.report, self.boss, self.pull, self.player = what, report, boss, str(pull), player
        super().__init__(discord.ui.Select(
            custom_id=_id("wbs", what, report, boss, pull, player) or "wbs|none",
            placeholder=self.PLACEHOLDERS[what],
            options=[discord.SelectOption(label=label[:100], value=value) for label, value in choices[:CHOICES]]
            or [discord.SelectOption(label="none", value="")]))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str], /) -> Pick:
        return cls(match["what"], match["report"], match["boss"], match["pull"], match["player"])

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = (interaction.data or {}).get("values") or [""]  # type: ignore[union-attr]
        if self.what == "player":
            await act(interaction, "ppull", self.report, self.boss, self.pull, chosen[0])
        elif self.what == "ppull":
            await act(interaction, "ppull", self.report, self.boss, chosen[0], self.player)
        else:
            await act(interaction, "pull", self.report, self.boss, chosen[0], "")


def tool_of(what: str, report: str, boss: str, pull: str, player: str) -> tuple[str, dict[str, Any]]:
    """The tool an action draws, and its arguments."""
    where: dict[str, Any] = {"report": report, "boss": boss or None}
    if pull:
        where["pull"] = int(pull)
    match what:
        case "death":
            return "death_recap", {**where, "player": player}
        case "top":
            return "compare_with_top", {**where, "player": player, "aspects": ["casts"]}
        case "night" | "mynight":
            return "player_night", {"report": report, "boss": boss or None, "player": player}
        case "ppull" | "mypull":
            return "player_pull", {**where, "player": player}
    return "pull_summary", where


async def act(interaction: discord.Interaction, what: str, report: str, boss: str, pull: str, player: str) -> None:
    """An action, answered to the one who clicked alone: the picture it opens, with that picture's own actions."""
    if what in MINE:
        store = getattr(interaction.client, "store", None)
        player = (store.raider(interaction.user.id).character if store else None) or ""
        if not player:
            await interaction.response.send_message(
                "I don't know which character is yours yet. Tell me in a message (\"my character is ...\") and I'll "
                "remember it.", ephemeral=True)
            return
        if what == "mypull" and (missing := await _not_in_pull(report, boss, pull, player)):
            await interaction.response.send_message(missing, ephemeral=True)
            return
    await interaction.response.defer(ephemeral=True, thinking=True)
    tool, args = tool_of(what, report, boss, pull, player)
    log.info("%s clicked %s: %s %s", interaction.user, what, tool, args)
    images = await host.picture(tool, args)
    if not images:
        await interaction.followup.send("I couldn't draw that one.", ephemeral=True)
        return
    pictures = await pictures_for([(i, tool, args) for i in images])
    files = [discord.File(io.BytesIO(i), filename=p.name) for i, p in zip(images, pictures, strict=True)]
    await interaction.followup.send(view=answer_layout("", pictures), files=files, ephemeral=True)


async def _not_in_pull(report: str, boss: str, pull: str, player: str) -> str | None:
    """Why `player` has no card on the pull, when they weren't in it (an alt, a night on the bench)."""
    try:
        rep = await server.lib().report(report)
        f = rep.fight(int(pull) if pull else None, boss or None)
    except (FactError, ValueError) as e:
        return str(e)
    if rep.find_player(player, rep.pull_players(f).values()) is None:
        return (f"{player}, the character I have for you, wasn't in {rep.label(f)}. If you play another one, tell me "
                "in a message (\"my character is ...\").")
    return None


async def pictures_for(drawn: Sequence[tuple[bytes, str, dict]], results: Sequence[str] = ()) -> list[Picture]:
    """The pictures for (image, tool, its arguments), named in order; the last of each tool's pictures (a view's
    last panel) gets its actions, and each its pull's link; `results` are the tools' texts when known (an answer's),
    for the link and a new guild best as the text states them."""
    out = []
    for n, (_, tool, args) in enumerate(drawn):
        name = f"wipebot-{n + 1}.webp"
        text = results[n] if n < len(results) else ""
        last = n == len(drawn) - 1 or drawn[n + 1][1:] != (tool, args)
        link, actions = None, ()
        try:
            link, actions = await _extras(tool, args, with_actions=last)
            actions = [a for a in actions if not (a.item.custom_id or "").endswith("|none")]  # what an id couldn't hold
        except (FactError, ValueError, KeyError) as e:  # a picture still goes, without them
            log.warning("no actions for %s %s: %s", tool, args, e)
        found = PULL_LINK.search(text)
        out.append(Picture(name, found[0] if found else link, tool == "pull_summary" and GUILD_BEST in text,
                           tuple(actions)))
    return out


async def _extras(tool: str, args: dict, with_actions: bool) -> tuple[str | None, list[Act | Pick]]:
    """A view's pull on Warcraft Logs, and (`with_actions`) what can be done from it."""
    key = await server.view_key(tool, args)
    if key is None:
        return None, []
    rep = await server.lib().report(key["report"])
    code, boss, view = rep.code, key.get("boss") or "", key["view"]
    f = rep.fight(key["pull"], boss) if key.get("pull") else None
    link = rep.url(f) if f else None
    if not with_actions:
        return link, []
    pulls = [(f"Pull {rep.pull_no(g)} · {_result(g)}", str(rep.pull_no(g))) for g in rep.by_boss.get(boss, [])]
    pulls = pulls[::-1][:CHOICES]  # the latest first
    match view:
        case "pull" if f:
            deaths = await rep.deaths(f)
            first = rep.names.get(deaths[0]["targetID"]) if deaths else None
            order = {"tank": 0, "healer": 1}
            here = sorted(rep.pull_players(f).values(), key=lambda p: (order.get(p.role, 2), p.name))
            return link, [Act("mypull", code, boss, key["pull"]),
                          *([Act("death", code, boss, key["pull"], first)] if first else []),
                          Pick("player", code, boss, key["pull"], choices=[(p.name, p.name) for p in here])]
        case "player":
            return link, [*([] if key.get("top") else [Act("top", code, boss, key["pull"], key["player"])]),
                          Act("night", code, boss, "", key["player"])]
        case "death":
            return link, [Act("ppull", code, boss, key["pull"], key["player"])]
        case "player_night":
            return link, [Pick("ppull", code, boss, "", key["player"], choices=pulls)]
        case "night":
            return link, [Act("mynight", code, boss), Pick("pull", code, boss, choices=pulls)]
    return link, []


def _result(f: dict) -> str:
    """'kill', 'reset' or the boss % left, for a pull's choice in a menu."""
    pct = wipe_pct(f)
    return "kill" if f.get("kill") else "reset" if is_reset(f) else f"{pct:.1f}%" if pct is not None else "?"
