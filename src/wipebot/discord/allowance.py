"""What a server's answers cost: the calendar month they're counted in (UTC), and the small line under an answer.

A copy many servers share (WIPEBOT_OWN_KEYS, a public copy) answers each server only on its own key (/wipebot setup),
so it pays for none of them; config.own_keys.
"""
import datetime


def month_start(now: datetime.datetime | None = None) -> datetime.datetime:
    now = now or datetime.datetime.now(datetime.UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def footer(cost: float | None, points: dict, rate: dict | None = None, user_points: bool = False) -> str:
    """The small line under an answer: what it cost and its Warcraft Logs points (`points`, by kind, as
    Answer.wcl_points) with the bot's left this hour (`rate`, WCL.my_points: its share). With `user_points` (the
    account on Warcraft Logs' new point system), points counted as the asker's are theirs."""
    total = points.get("total", 0)
    yours = min(points.get("user", 0), total) if user_points else 0
    spent = f"{yours:,.0f} yours, {total - yours:,.0f} the bot's" if yours else f"{total:,.0f}"
    if rate and isinstance(limit := rate.get("limitPerHour"), int | float):
        spent += f" ({max(limit - rate.get('pointsSpentThisHour', 0), 0):,.0f} left this hour)"
    parts = [f"${cost or 0:.2f}", f"Warcraft Logs points: {spent}"]
    return "-# " + " · ".join(parts)
