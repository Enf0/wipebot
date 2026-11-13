"""/wipebot setup's model: a server that answers on its own key picks the model its answers run on, from the ones
that key reaches.

The list is the provider's own, asked with the server's key when an admin opens the option and kept a few minutes:
OpenRouter's models that can call tools (answers are built on tool calls), or Anthropic's. Nothing here says how well
a model answers: docs/models.md holds what was measured, and only for the Claude models.
"""
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx

from .config import OPENROUTER_URL
from .keys import ServerKey

log = logging.getLogger(__name__)

DEFAULT = "default"  # the option's value for the bot's own model
KEPT_S = 600
PRICES_KEPT_S = 3600
TOKENS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
SHOWN = 25  # the most choices Discord shows for an option
LABEL = 100  # and the longest a choice's name or value may be


@dataclass(frozen=True)
class Model:
    id: str  # what the provider calls it, and what answers are asked of
    name: str
    price: str = ""  # per million tokens in and out, where the provider's list says; else empty
    created: float = 0.0

    @property
    def claude(self) -> bool:
        return self.id.startswith(("anthropic/", "claude-"))

    def label(self) -> str:
        return f"{self.name} ({self.price})"[:LABEL] if self.price else self.name[:LABEL]


def _usd(per_token: str) -> str:
    per_million = round(float(per_token) * 1_000_000, 4)
    if per_million == int(per_million):
        return f"${per_million:.0f}"
    return f"${per_million:.2f}" if round(per_million, 2) == per_million else f"${per_million:g}"


def _price(pricing: dict) -> str:
    try:
        return f"{_usd(pricing['prompt'])} in, {_usd(pricing['completion'])} out per million tokens"
    except (KeyError, TypeError, ValueError):
        return ""


def from_openrouter(data: list[dict]) -> list[Model]:
    """OpenRouter's models an answer can run on: those that call tools and write text, without the batch variants
    (asked and answered hours apart)."""
    found = []
    for m in data:
        if "tools" not in (m.get("supported_parameters") or []) or ":batch" in m["id"] or len(m["id"]) > LABEL:
            continue
        if "text" not in ((m.get("architecture") or {}).get("output_modalities") or ["text"]):
            continue
        found.append(Model(m["id"], m.get("name") or m["id"], _price(m.get("pricing") or {}), m.get("created") or 0))
    return found


def from_anthropic(data: list[dict]) -> list[Model]:
    return [Model(m["id"], m.get("display_name") or m["id"]) for m in data if len(m["id"]) <= LABEL]


_kept: dict[str, tuple[float, list[Model]]] = {}


async def available(key: ServerKey) -> list[Model]:
    """The models the key reaches, Claude's first (the ones the bot is tested on), then the rest by name; the
    newest first among Claude's. Raises httpx.HTTPError when the provider can't be asked."""
    digest = hashlib.sha256(f"{key.provider}:{key.key}".encode()).hexdigest()
    if (kept := _kept.get(digest)) and time.time() - kept[0] < KEPT_S:
        return kept[1]
    async with httpx.AsyncClient(timeout=10) as http:
        if key.provider == "openrouter":  # the models the account's own provider settings allow
            r = await http.get(f"{OPENROUTER_URL}/v1/models/user", headers={"Authorization": f"Bearer {key.key}"})
            r.raise_for_status()
            found = from_openrouter(r.json()["data"])
        else:
            r = await http.get("https://api.anthropic.com/v1/models", params={"limit": 1000},
                               headers={"x-api-key": key.key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            found = from_anthropic(r.json()["data"])  # newest first, as Anthropic lists them
    if key.provider == "openrouter":
        found.sort(key=lambda m: (not m.claude, -m.created if m.claude else 0, m.name.lower()))
    _kept[digest] = (time.time(), found)
    return found


def matching(models: list[Model], typed: str, limit: int = SHOWN) -> list[Model]:
    """The first `limit` models whose name or id holds every word typed, in the list's order."""
    words = typed.lower().split()
    return [m for m in models if all(w in f"{m.name} {m.id}".lower() for w in words)][:limit]


def find(models: list[Model], chosen: str) -> Model | None:
    return next((m for m in models if m.id == chosen), None)


@dataclass(frozen=True)
class Rates:
    """What a model costs per token at OpenRouter, in dollars; `above` are the rates of a request whose prompt is at
    least `above_tokens` long, where the model has such a tier."""
    read: float
    written: float
    cache_read: float
    cache_written: float
    above_tokens: int = 0
    above: Rates | None = None

    def cost(self, used: dict, context_tokens: int = 0) -> float:
        """What the tokens of `used` (Claude Code's count for a model: ModelUsage's keys) cost, at the long prompt's
        rates when the conversation's context has reached them."""
        if self.above and context_tokens >= self.above_tokens:
            return self.above.cost(used)
        return (used.get("inputTokens", 0) * self.read + used.get("outputTokens", 0) * self.written
                + used.get("cacheReadInputTokens", 0) * self.cache_read
                + used.get("cacheCreationInputTokens", 0) * self.cache_written)


def _rates(pricing: dict) -> Rates | None:
    try:
        read, written = float(pricing["prompt"]), float(pricing["completion"])
        if read < 0 or written < 0:  # a router that picks the model: no price of its own
            return None
        rates = Rates(read, written, float(pricing.get("input_cache_read") or read),
                      float(pricing.get("input_cache_write") or read))
        tier = next((o for o in pricing.get("overrides") or [] if o.get("min_prompt_tokens")), None)
        if tier and (above := _rates({**pricing, **tier, "overrides": []})):
            return Rates(rates.read, rates.written, rates.cache_read, rates.cache_written,
                         int(tier["min_prompt_tokens"]), above)
        return rates
    except (KeyError, TypeError, ValueError):
        return None


def rates_from_openrouter(data: list[dict]) -> dict[str, Rates]:
    return {m["id"]: rates for m in data if (rates := _rates(m.get("pricing") or {}))}


_prices: tuple[float, dict[str, Rates]] = (0.0, {})


async def openrouter_rates() -> dict[str, Rates]:
    """OpenRouter's price of each model, by the name it is asked by, from its public list, kept an hour: Claude Code
    knows no price for a model under OpenRouter's name and counts an answer on one at a rate of its own. Empty
    (and asked again next time) when the list can't be read."""
    global _prices
    if _prices[1] and time.time() - _prices[0] < PRICES_KEPT_S:
        return _prices[1]
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"{OPENROUTER_URL}/v1/models")
            r.raise_for_status()
            _prices = (time.time(), rates_from_openrouter(r.json()["data"]))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("OpenRouter's prices: %r", e)
        return _prices[1]  # an older list beats Claude Code's guess
    return _prices[1]


def cost(used: dict[str, dict], before: dict[str, dict], rates: dict[str, Rates], context_tokens: int = 0) -> float:
    """What an answer cost: the tokens each model used since `before` (Claude Code's counts add up over a
    conversation), at OpenRouter's price for the models it lists, else at what Claude Code counted."""
    total = 0.0
    for model, now in used.items():
        was = before.get(model) or {}
        if any((now.get(k) or 0) < (was.get(k) or 0) for k in TOKENS):  # counted from zero again
            was = {}
        if model in rates:
            spent = {k: (now.get(k) or 0) - (was.get(k) or 0) for k in TOKENS}
            total += rates[model].cost(spent, context_tokens)
        else:
            total += (now.get("costUSD") or 0) - (was.get("costUSD") or 0)
    return total
