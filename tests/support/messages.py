"""What the bot's Discord messages read as, for the tests' stand-ins of channels and messages."""
from typing import Any

import discord


def shown(content: str | None, view: Any = None) -> str | None:
    """What a message sent with `content` or a Components V2 `view` reads as: its text."""
    if view is None or not view.has_components_v2():  # a plain message: its buttons, if any, aren't text
        return content
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))
