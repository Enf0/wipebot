"""How the bot's answers with pictures look in Discord, as Components V2 messages (one without is plain text, which
a notification shows): the text, then each picture whole in a box with a button to its pull on Warcraft Logs and the
picture's own actions (Act, Pick), edged gold for a new guild best.

An action answers the one who clicked with the next picture, which only they see (an ephemeral reply), with its own
actions in turn: the pull's first death, a player's card, against top players, their night, a pull of the night, and
the clicker's own card on a pull or their own night ("How did I do?", "My night", by the character the store has).
Each button and menu carries what it acts on in its custom_id (report, boss, pull, player), so the ones on old
messages still work after a restart (discord.py's DynamicItem, registered in WipeBot.setup_hook).

The follow-ups an answer offers are buttons too (FollowUp), then the cost line under a thin line: a click asks it in
the asker's conversation, as if they had typed it, and only the asker's click does.

The buttons and menus speak the answer's language (texts.language; a click's reply, the clicker's Discord's): their
words are in texts.py."""
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import discord

from wipebot.core import server
from wipebot.core.pulls import is_reset, wipe_pct, wipe_text
from wipebot.core.report import FactError
from wipebot.core.wcl import WCLError

from . import host, logincmd, texts
from .host import Context, ToolCall
from .texts import language, locale_language  # noqa: F401 (language: the bot's and the tests')

log = logging.getLogger(__name__)

PULL_LINK = re.compile(r"https://www\.warcraftlogs\.com/reports/\w+#fight=\d+")
GUILD_BEST = "**new guild best!**"  # pull_summary's post marks a wipe that beat every earlier log so (pulls.best_marks)
SEP = "|"  # between the parts of a custom_id; no report code, boss or character name has one
ID_CHARS = 100  # Discord's limit for a custom_id
CHOICES = 25  # Discord's limit for a menu's options


@dataclass(frozen=True)
class Picture:
    """A picture sent with an answer: its attachment's name, the pull it shows on Warcraft Logs, whether that pull
    was a new guild best, what can be done from it (buttons and menus, under it), and where in the text it goes (an
    offset; None under the whole text)."""
    name: str
    link: str | None = None
    best: bool = False
    actions: tuple[Act | Pick, ...] = field(default=(), compare=False)
    at: int | None = field(default=None, compare=False)
    lang: str = field(default="en", compare=False)  # its buttons' language

    @classmethod
    def of(cls, name: str, call: ToolCall) -> Picture:
        link = PULL_LINK.search(call.result)
        return cls(name, link[0] if link else None, call.name == "pull_summary" and GUILD_BEST in call.result)


def answer_layout(text: str, pictures: Sequence[Picture] = (), actions: Sequence[Act] = (),
                  follow: Sequence[FollowUp] = (), footer: str = "") -> discord.ui.LayoutView:
    """A message as Components V2: its text (none for a picture alone) with `actions` under it (the message's own
    buttons, not a picture's), then each picture (an attachment by its name) whole in a gallery of its own, boxed with
    its buttons and menus (one already above left out), the box edged gold for a new guild best, then the follow-ups
    it offers, and last the small `footer` line under a thin line. A picture placed in the text (Picture.at) goes
    there instead, next to the part it backs. Several attachments on a plain message are shown as a grid, each
    cropped to its tile."""
    view = discord.ui.LayoutView(timeout=None)
    used: set[str] = set()  # Discord refuses a message with two items of one custom_id: the second does the same
    if actions:
        used.update(a.item.custom_id for a in actions if a.item.custom_id)
    cuts = sorted({p.at for p in pictures if p.at is not None and 0 < p.at < len(text.rstrip())})
    for start, end in zip([0, *cuts], [*cuts, len(text)], strict=True):
        if part := text[start:end].strip("\n"):
            view.add_item(discord.ui.TextDisplay(part))
        if end < len(text):
            for p in pictures:
                if p.at == end:
                    view.add_item(_boxed(p, used))
    if actions:
        view.add_item(discord.ui.ActionRow(*(cast(Any, a) for a in actions)))
    for p in pictures:
        if p.at not in cuts:
            view.add_item(_boxed(p, used))
    if follow:
        view.add_item(discord.ui.ActionRow(*(cast(Any, f) for f in follow)))
    if footer:
        view.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        view.add_item(discord.ui.TextDisplay(footer))
    return view


def _boxed(p: Picture, used: set[str]) -> discord.ui.Container:
    """A picture whole in a gallery of its own, boxed with its buttons and menus (those in `used` left out, and the
    rest added to it), edged gold for a new guild best."""
    items: list[discord.ui.Item] = [discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{p.name}"))]
    buttons: list[discord.ui.Item] = [discord.ui.Button(label=texts.OPEN.to(p.lang), url=p.link)] \
        if p.link else []
    acts = [a for a in p.actions if a.item.custom_id not in used]
    used.update(a.item.custom_id for a in acts if a.item.custom_id)
    buttons += [a for a in acts if isinstance(a, Act)]
    if buttons:
        items.append(discord.ui.ActionRow(*buttons))
    items += [discord.ui.ActionRow(cast(Any, a)) for a in acts if isinstance(a, Pick)]  # a menu: a row
    return discord.ui.Container(*items, accent_colour=discord.Colour.gold() if p.best else None)


def label(text: texts.Text, lang: str, boss: str = "", pull: str | int = "", player: str = "", limit: int = 80) -> str:
    """A button's or menu's label in `lang`, cut to Discord's `limit` (80 for a button, 150 for a menu's
    placeholder): a long boss name goes first."""
    said = text.to(lang, boss=boss, pull=pull, player=player)
    return said if len(said) <= limit else said[:limit - 1] + "…"


def _id(prefix: str, *parts: object) -> str | None:
    """A custom_id from its parts, or None when it can't hold them (too long, or a part with SEP in it)."""
    texts = ["" if p is None else str(p) for p in parts]
    out = SEP.join([prefix, *texts])
    return out if len(out) <= ID_CHARS and not any(SEP in t for t in texts) else None


_FIELDS = r"(?P<what>[a-z]+)\|(?P<report>[^|]*)\|(?P<boss>[^|]*)\|(?P<pull>[^|]*)\|(?P<player>[^|]*)"
MINE = {"mynight", "mypull"}  # actions on the clicker's own character


class Act(discord.ui.DynamicItem[discord.ui.Button], template=r"wb\|" + _FIELDS):
    """A button: what it does (texts.BUTTONS, in `lang`) and what to: the report, boss, pull and player."""

    def __init__(self, what: str, report: str, boss: str = "", pull: str | int = "", player: str = "",
                 lang: str = "en") -> None:
        self.what, self.report, self.boss, self.pull, self.player = what, report, boss, str(pull), player
        super().__init__(discord.ui.Button(label=label(texts.BUTTONS[what], lang, boss, pull, player),
                                           style=discord.ButtonStyle.secondary,
                                           custom_id=_id("wb", what, report, boss, pull, player) or "wb|none"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str], /) -> Act:
        return cls(match["what"], match["report"], match["boss"], match["pull"], match["player"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await act(interaction, self.what, self.report, self.boss, self.pull, self.player)


class Pick(discord.ui.DynamicItem[discord.ui.Select], template=r"wbs\|" + _FIELDS):
    """A menu: what a choice opens ("player": that player's pull, "ppull": the player's card on that pull, "pull": that
    pull; texts.MENUS), the report, boss, pull and player it's about, and its choices as (label, value)."""

    def __init__(self, what: str, report: str, boss: str = "", pull: str | int = "", player: str = "",
                 choices: Sequence[tuple[str, str]] = (), lang: str = "en") -> None:
        self.what, self.report, self.boss, self.pull, self.player = what, report, boss, str(pull), player
        super().__init__(discord.ui.Select(
            custom_id=_id("wbs", what, report, boss, pull, player) or "wbs|none",
            placeholder=label(texts.MENUS[what], lang, boss, pull, player, 150),
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


class FollowUp(discord.ui.DynamicItem[discord.ui.Button], template=r"wbf\|(?P<asker>\d+)\|(?P<n>\d+)"):
    """A follow-up the answer offered: its label is the question, which a click by the asker asks in their
    conversation (WipeBot.follow_up); the custom_id keeps who asked (Discord's 100 characters can't hold the question,
    and the label comes back with the click)."""

    def __init__(self, asker: int, n: int, question: str) -> None:
        self.asker, self.question = asker, question
        super().__init__(discord.ui.Button(label=question[:80], style=discord.ButtonStyle.secondary,
                                           custom_id=f"wbf|{asker}|{n}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str], /) -> FollowUp:
        return cls(int(match["asker"]), int(match["n"]), getattr(item, "label", None) or "")

    async def callback(self, interaction: discord.Interaction) -> None:
        lang = locale_language(interaction.locale)
        if interaction.user.id != self.asker:
            await interaction.response.send_message(
                texts.NOT_YOURS.to(lang, asker=self.asker), ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none())
            return
        follow_up = getattr(interaction.client, "follow_up", None)
        if follow_up is None or not self.question:
            await interaction.response.send_message(texts.CANT_ASK.to(lang), ephemeral=True)
            return
        await follow_up(interaction, self.question)


def without_follow_ups(message: discord.Message, asked: str) -> discord.ui.View | discord.ui.LayoutView | None:
    """The message's components without the follow-up button whose custom_id is `asked`, once it was asked (so it
    isn't asked twice; the others stay); None when it isn't there. An answer with pictures is a Components V2 message,
    one without a plain message with a view."""
    def clicked(item: object) -> bool:
        return getattr(item, "custom_id", None) == asked
    if not message.flags.components_v2:
        plain = discord.ui.View.from_message(message, timeout=None)
        buttons = [b for b in plain.children if clicked(b)]
        for b in buttons:
            plain.remove_item(b)
        return plain if buttons else None
    view = discord.ui.LayoutView.from_message(message, timeout=None)
    found = False
    for row in [c for c in view.children if isinstance(c, discord.ui.ActionRow)]:
        for b in [b for b in row.children if clicked(b)]:
            row.remove_item(b)
            found = True
        if not row.children:
            view.remove_item(row)
    return view if found else None


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
        case "hits":  # `player` holds the ability
            return "mechanic_hits", {"report": report, "boss": boss or None, "ability": player, "all_pulls": True}
    return "pull_summary", where


def asker_of(store: Any, user_id: int) -> Context:
    """A Discord user as the asker of what the bot draws for them without a question (a button they clicked, the
    night's post they asked for): it's read with their own Warcraft Logs sign-in (`store`: the bot's, None for none),
    and reads no guild's notes."""
    signed = store.login(user_id) if store else None
    private = store.private_login(user_id) if store else None
    return Context(user_id=user_id, wcl_token=signed[1] if signed else None,
                   wcl_private=private[1] if private else None,
                   private_hint=logincmd.PRIVATE_HINT if logincmd.callback_url() else None)


async def act(interaction: discord.Interaction, what: str, report: str, boss: str, pull: str, player: str) -> None:
    """An action, answered to the one who clicked alone: the picture it opens, with that picture's own actions, read
    as the one who clicked."""
    with asker_of(getattr(interaction.client, "store", None), interaction.user.id).reading():
        await _act(interaction, what, report, boss, pull, player)


async def _act(interaction: discord.Interaction, what: str, report: str, boss: str, pull: str, player: str) -> None:
    store = getattr(interaction.client, "store", None)
    mine = (store.raider(interaction.user.id).character if store else None) or ""
    lang = locale_language(interaction.locale)
    if what in MINE:
        player = mine
        if not player:
            await interaction.response.send_message(texts.WHICH_CHARACTER.to(lang), ephemeral=True)
            return
        if what == "mypull" and (missing := await _not_in_pull(report, boss, pull, player, lang)):
            await interaction.response.send_message(missing, ephemeral=True)
            return
    await interaction.response.defer(ephemeral=True, thinking=True)
    tool, args = tool_of(what, report, boss, pull, player)
    log.info("%s clicked %s: %s %s", interaction.user, what, tool, args)
    images = await host.picture(tool, args)
    if what == "hits":  # the meter's hits pull by pull, not the meter again
        images = [i for i in images if getattr(i, "name", "") == host.BY_PULL]
    if not images:
        await interaction.followup.send(texts.CANT_DRAW.to(lang), ephemeral=True)
        return
    pictures = await pictures_for([(i, tool, args) for i in images], lang=lang, mine=mine)
    files = [discord.File(io.BytesIO(i), filename=p.name) for i, p in zip(images, pictures, strict=True)]
    await interaction.followup.send(view=answer_layout("", pictures), files=files, ephemeral=True)


async def _not_in_pull(report: str, boss: str, pull: str, player: str, lang: str = "en") -> str | None:
    """Why `player` has no card on the pull, when they weren't in it (an alt, a night on the bench), in `lang`."""
    try:
        rep = await server.lib().report(report)
        f = rep.fight(int(pull) if pull else None, boss or None)
    except (FactError, ValueError) as e:
        return str(e)
    except WCLError as e:
        log.warning("couldn't read report %s for a click: %s", report, e)
        return texts.CANT_READ.to(lang)
    if rep.find_player(player, rep.pull_players(f).values()) is None:
        return texts.NOT_IN_PULL.to(lang, player=player, pull=rep.label(f))
    return None


async def pictures_for(drawn: Sequence[tuple[bytes, str, dict]], results: Sequence[str] = (), lang: str = "en",
                       mine: str = "") -> list[Picture]:
    """The pictures for (image, tool, its arguments), named in order; the last of each tool's pictures (a view's
    last panel) gets its actions, and each its pull's link; `results` are the tools' texts when known (an answer's),
    for the link and a new guild best as the text states them. Their buttons speak `lang`, and a player's card of
    `mine` (the asker's character) offers their own night."""
    out = []
    for n, (_, tool, args) in enumerate(drawn):
        name = f"wipebot-{n + 1}.webp"
        text = results[n] if n < len(results) else ""
        last = n == len(drawn) - 1 or drawn[n + 1][1:] != (tool, args)
        link, actions = None, ()
        try:
            link, actions = await _extras(tool, args, with_actions=last, lang=lang, mine=mine)
            actions = [a for a in actions if not (a.item.custom_id or "").endswith("|none")]  # what an id couldn't hold
        except (FactError, ValueError, KeyError, WCLError) as e:  # a picture still goes, without them
            log.warning("no actions for %s %s: %s", tool, args, e)
        found = PULL_LINK.search(text)
        out.append(Picture(name, found[0] if found else link, tool == "pull_summary" and GUILD_BEST in text,
                           tuple(actions), lang=lang))
    return out


async def _extras(tool: str, args: dict, with_actions: bool, lang: str = "en",
                  mine: str = "") -> tuple[str | None, list[Act | Pick]]:
    """A view's pull on Warcraft Logs, and (`with_actions`) what can be done from it, in `lang`; on a card of `mine`
    (the asker's character), their own night."""
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
            return link, [Act("mypull", code, boss, key["pull"], lang=lang),
                          *([Act("death", code, boss, key["pull"], first, lang=lang)] if first else []),
                          Pick("player", code, boss, key["pull"], choices=[(p.name, p.name) for p in here],
                               lang=lang)]
        case "player":
            own = bool(mine) and key["player"].lower() == mine.lower()
            return link, [*([] if key.get("top") else [Act("top", code, boss, key["pull"], key["player"], lang=lang)]),
                          Act("mynight", code, boss, lang=lang) if own
                          else Act("night", code, boss, "", key["player"], lang=lang)]
        case "death":
            return link, [Act("ppull", code, boss, key["pull"], key["player"], lang=lang)]
        case "player_night":
            return link, [Pick("ppull", code, boss, "", key["player"], choices=pulls, lang=lang)]
        case "night":
            return link, [Act("mynight", code, boss, lang=lang), Pick("pull", code, boss, choices=pulls, lang=lang)]
        case "meter" if key["tool"] == "mechanic_hits" and key["args"].get("all_pulls") and key["args"].get("ability"):
            return link, [Act("hits", code, boss, "", key["args"]["ability"], lang=lang)]
    return link, []


def _result(f: dict) -> str:
    """'kill', 'reset' or how far the wipe got ('P2 68.3%' on a boss with phases), for a pull's choice in a menu."""
    if f.get("kill") or is_reset(f):
        return "kill" if f.get("kill") else "reset"
    return wipe_text(f, wcl=False) if wipe_pct(f) is not None else "?"
