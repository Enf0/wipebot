"""What the person running wipebot sets, from the environment. Everything about a Discord server (its WCL
guild, channels, role) is set in Discord with /wipebot setup and kept in the store."""
import os
from typing import Literal

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def check_settings() -> None:
    """Exit, naming every one of them, when settings the bot can't start without aren't set: the Anthropic API key
    (it answers other people: on the API, never on a Claude subscription), the Discord token and the Warcraft Logs
    client."""
    from wipebot.core import wcl
    missing = [name for name in ("ANTHROPIC_API_KEY", "DISCORD_TOKEN") if not os.environ.get(name)]
    if not wcl.client_credentials():
        missing.append("WCL_CLIENT_ID and WCL_CLIENT_SECRET: a client from https://www.warcraftlogs.com/api/clients")
    if missing:
        raise SystemExit("Not set (.env.example says what each is):\n" + "\n".join(f"  {m}" for m in missing))


def model() -> str:
    """The Claude model for answers (WIPEBOT_MODEL). Opus by default: it answers best in the evals, at a higher
    cost (docs/models.md; `just evals --model sonnet` compares again)."""
    return os.environ.get("WIPEBOT_MODEL") or "opus"  # .env.example leaves it empty


def effort() -> Effort | None:
    """How hard the model thinks before it answers (WIPEBOT_EFFORT: one of EFFORTS); unset, the model's own default
    (medium for Opus). Lower is faster and cheaper (`just evals --bot --effort low` compares)."""
    value = os.environ.get("WIPEBOT_EFFORT") or None
    if value is not None and value not in EFFORTS:
        raise ValueError(f"WIPEBOT_EFFORT is {value!r}: it takes {', '.join(EFFORTS)}")
    return value



def channel_context() -> bool:
    """Whether answers see the channel's conversation (WIPEBOT_CHANNEL_CONTEXT=1): the Message Content intent,
    which must also be switched on in the developer portal. Off by default, as it sends raiders' messages to the
    model that weren't for the bot."""
    return os.environ.get("WIPEBOT_CHANNEL_CONTEXT", "").lower() in ("1", "true", "yes")


def record_answers() -> bool:
    """Whether each question, its prompt, answer and tool calls are kept (WIPEBOT_RECORD_ANSWERS=1), 90 days, for
    whoever reviews the bot's answers. Off by default: the bot never reads them back, and they hold what raiders
    asked and, with channel context, what was said around it. Off, the log doesn't print questions or answers
    either."""
    return os.environ.get("WIPEBOT_RECORD_ANSWERS", "").lower() in ("1", "true", "yes")
