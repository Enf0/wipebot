"""What the person running wipebot sets, from the environment. Everything about a Discord server (its WCL
guild, channels, role) is set in Discord with /wipebot setup and kept in the store."""
import os
from typing import Literal

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS = ("low", "medium", "high", "xhigh", "max")


OPENROUTER_URL = "https://openrouter.ai/api"


def check_settings() -> None:
    """Exit, naming every one of them, when settings the bot can't start without aren't set: an Anthropic or
    OpenRouter API key (it answers other people: on the API, never on a Claude subscription), the Discord token and
    the Warcraft Logs client."""
    from wipebot.core import wcl
    missing = [name for name in ("DISCORD_TOKEN",) if not os.environ.get(name)]
    if not os.environ.get("ANTHROPIC_API_KEY") and not openrouter_key():
        missing.insert(0, "ANTHROPIC_API_KEY or OPENROUTER_API_KEY")
    if not wcl.client_credentials():
        missing.append("WCL_CLIENT_ID and WCL_CLIENT_SECRET: a client from https://www.warcraftlogs.com/api/clients")
    if missing:
        raise SystemExit("Not set (.env.example says what each is):\n" + "\n".join(f"  {m}" for m in missing))


def openrouter_key() -> str | None:
    """The OpenRouter API key the bot answers with (OPENROUTER_API_KEY), in place of ANTHROPIC_API_KEY: its credit
    limit is the budget raiders see with /usage, and an Anthropic key behind it (OpenRouter's BYOK) still pays."""
    return os.environ.get("OPENROUTER_API_KEY") or None


def model_env() -> dict[str, str]:
    """What Claude Code needs in its environment to answer through OpenRouter, when OPENROUTER_API_KEY is set
    (OpenRouter's guide to the Agent SDK); else nothing, and it answers on ANTHROPIC_API_KEY."""
    key = openrouter_key()
    return {"ANTHROPIC_BASE_URL": OPENROUTER_URL, "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_API_KEY": ""} if key else {}


def budget_note() -> str | None:
    """How raiders chip in to the bot's budget (WIPEBOT_BUDGET_NOTE, e.g. "Vipps to Oscar, 12345678"), shown by
    /usage."""
    return os.environ.get("WIPEBOT_BUDGET_NOTE") or None


def spent_before() -> float:
    """What the bot spent before its OpenRouter key (WIPEBOT_SPENT_BEFORE, in USD, e.g. an Anthropic key's cost in
    the Console), which /usage counts in the key's spend in all; 0 when unset."""
    try:
        return float(os.environ.get("WIPEBOT_SPENT_BEFORE") or 0)
    except ValueError:
        return 0.0


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
