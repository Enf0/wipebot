"""The bot's AI budget, which raiders see with /usage: what its OpenRouter key has spent and has left.

The bot answers through OpenRouter when the host sets OPENROUTER_API_KEY (config.model_env). The key's credit limit
is the budget: the host raises it by what raiders chip in, and OpenRouter refuses the key's requests past it. An
Anthropic key behind it (OpenRouter's BYOK) still pays Anthropic directly; its spend counts toward the limit when the
key has "include BYOK in limit" on. `GET /api/v1/key` with the key itself reports both (OpenRouter's limits guide).
"""
from dataclasses import dataclass

import httpx

from .config import OPENROUTER_URL


@dataclass
class KeyBudget:
    limit: float | None  # USD; None when the key has no limit
    remaining: float | None
    month: float  # spent this calendar month (UTC), on OpenRouter credits and through BYOK together
    total: float  # spent since the key was made


async def fetch(key: str) -> KeyBudget:
    """The key's limit and spend, from OpenRouter; httpx.HTTPError or KeyError when it can't be read."""
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{OPENROUTER_URL}/v1/key", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        data = r.json()["data"]
    return KeyBudget(limit=data.get("limit"), remaining=data.get("limit_remaining"),
                     month=(data.get("usage_monthly") or 0) + (data.get("byok_usage_monthly") or 0),
                     total=(data.get("usage") or 0) + (data.get("byok_usage") or 0))


def bar(limit: float | None, remaining: float | None, width: int = 20) -> str:
    """How much of a limit is spent, as a bar of text (for /wipebot usage post); empty without a limit."""
    if not limit or remaining is None:
        return ""
    used = min(max(1 - remaining / limit, 0), 1)
    full = round(used * width)
    return f"`{'█' * full}{'░' * (width - full)}` {used:.0%} spent"


def describe(b: KeyBudget, answers: int, answers_cost: float, note: str | None, with_bar: bool = False) -> str:
    """What /usage says: this month's spend, what's left and about how many answers that is (from the month's
    answers, `answers` of them costing `answers_cost` in all), and how
    to chip in; `with_bar` adds a bar of what's spent of the limit."""
    lines = [f"**AI budget:** ${b.month:.2f} spent this month, ${b.total:.2f} in all."]
    if with_bar and (spent := bar(b.limit, b.remaining)):
        lines.append(spent)
    if b.remaining is None:
        lines.append("It has no limit set.")
    else:
        left = f"**${b.remaining:.2f}** left of ${b.limit:.2f}"
        if answers and answers_cost > 0:
            per = answers_cost / answers
            left += f": about {int(b.remaining // per)} more answers at this month's ${per:.2f} an answer"
        lines.append(left + ".")
        if b.remaining <= 0:
            lines.append("It's used up, so wipebot can't answer until it's topped up.")
    if note:
        lines.append(f"To chip in: {note}")
    return "\n".join(lines)
