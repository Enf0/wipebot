"""A copy's free answers per server, for a bot many servers share (WIPEBOT_SERVER_SHARE, a public copy).

The pool is the bot's OpenRouter key's limit, set to reset monthly (`limit_reset`), which the host raises or lowers
as they like ($100 one month, $10 or nothing the next). Each server may spend up to its share of that limit in a
calendar month (UTC), first come first served, counted from what its answers cost (store.spend); OpenRouter refuses
the key once the whole pool is spent. Without a share or an OpenRouter key, every server answers without limit.
"""
import datetime
import logging
import os
import time

import httpx

from . import budget
from .store import Store

log = logging.getLogger(__name__)

POOL_CACHE_S = 60  # the key's limit and spend are read again after this long


def server_share() -> float | None:
    """WIPEBOT_SERVER_SHARE: the share of the pool (0 to 1) one server may spend a month; unset, no limit."""
    value = os.environ.get("WIPEBOT_SERVER_SHARE") or None
    if value is None:
        return None
    share = float(value)
    if not 0 < share <= 1:
        raise ValueError(f"WIPEBOT_SERVER_SHARE is {value}: it takes a share above 0, up to 1")
    return share


def month_start(now: datetime.datetime | None = None) -> datetime.datetime:
    now = now or datetime.datetime.now(datetime.UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month(now: datetime.datetime | None = None) -> datetime.datetime:
    start = month_start(now)
    return (start + datetime.timedelta(days=32)).replace(day=1)


class Allowance:
    """Whether a server may have another free answer this month."""

    def __init__(self, store: Store, key: str, share: float):
        self.store, self.key, self.share = store, key, share
        self._pool: tuple[float, budget.KeyBudget] | None = None

    async def pool(self) -> budget.KeyBudget | None:
        """The key's limit and spend, read at most every POOL_CACHE_S; None when OpenRouter can't be read."""
        if self._pool and time.monotonic() - self._pool[0] < POOL_CACHE_S:
            return self._pool[1]
        try:
            b = await budget.fetch(self.key)
        except (httpx.HTTPError, KeyError) as e:
            log.warning("couldn't read the pool from OpenRouter: %s", e)
            return self._pool[1] if self._pool else None
        self._pool = (time.monotonic(), b)
        return b

    async def refusal(self, server_id: int) -> str | None:
        """What to tell a raider when their server has spent its share of this month's pool, or the pool is spent;
        None when it may answer (also when the pool can't be read: OpenRouter still refuses past the limit)."""
        b = await self.pool()
        if b is None or b.limit is None:
            return None
        back = f"<t:{int(next_month().timestamp())}:D>"  # Discord shows it in the reader's own date format
        if b.limit <= 0 or (b.remaining is not None and b.remaining <= 0):
            return f"The free answers every server shares are used up for this month. They're back on {back}."
        _, spent, _ = self.store.spent(month_start().timestamp(), discord_guild_id=server_id)
        if spent >= self.share * b.limit:
            return (f"This server has used its free answers for this month (${spent:.2f} of its "
                    f"${self.share * b.limit:.2f}). They're back on {back}.")
        return None
