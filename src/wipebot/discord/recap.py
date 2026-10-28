"""The night's recap: one post in the channel an admin chose (/wipebot recap), once the guild's raid report has
stopped being logged. A few lines worked out from the night's pulls (the kills, the best wipe and whether it beat
the guild's best, the trend, what ended most pulls), the night on the boss pulled most as its one picture, and
buttons for the next thing to look at: each raider's own night, the night's awards, what to fix, and one question
the server's raiders have rarely asked (Discover), so a feature they don't know comes up when it fits the night.

The lines and the picture come from the tools' code without the model, so the post costs no answer; what the
questions' buttons ask is an answer like any other, posted where anyone sees it. This module picks the report and
writes the post's parts; the bot sends it (WipeBot.recaps)."""
import json
from collections import Counter
from dataclasses import dataclass, field

from wipebot.core.pulls import NO_KILLING_BLOW, is_reset, wipe_text
from wipebot.core.report import REPORT_QUIET_S, Report
from wipebot.core.report.views import night_view

RECAP_CHECK_S = 10 * 60  # how often to look for a report that ended, in each server with a recap channel
RECAP_WITHIN_S = 6 * 3600  # a report that ended longer ago gets none (turned on the day after, a long restart)
LOOKBACK_S = 18 * 3600  # the reports looked at: started this recently
MIN_PULLS = 3  # boss pulls past a reset a night needs for a recap: a quick look at a boss isn't a raid night
USES_DAYS = 30  # Discover offers the question whose tool the server's answers used least over this many days
NOT_WIPES = ("a kill", "a reset", "still going", "called off")


def due(reports: list[dict], now_ms: float, last: dict | None) -> dict | None:
    """The report to recap now, from the guild's reports (guild_reports, newest first): the newest one that stopped
    being logged at least REPORT_QUIET_S ago, at most RECAP_WITHIN_S ago, and started after the one recapped last
    (`last`: its code and end), so a night two raiders logged gets one recap; None when no report is due."""
    for r in reports:
        quiet_s = (now_ms - r["endTime"]) / 1000
        if quiet_s < REPORT_QUIET_S:
            continue  # still being logged, or only just stopped: the night may go on
        if quiet_s > RECAP_WITHIN_S or (last and (r["code"] == last["code"] or r["startTime"] < last["end"])):
            return None
        return r
    return None


def language(name: str | None) -> str:
    """'no' for a server whose language is Norwegian, else 'en': the recap's lines and buttons."""
    return "no" if (name or "").lower().startswith(("norw", "nors", "bokm", "nynorsk")) else "en"


@dataclass
class Discover:
    """A question Discover may offer: the tool that answers it, and the question in each language, which can name
    the night's boss, best pull, last wipe and killer."""
    tool: str
    en: str
    no: str
    needs: tuple[str, ...] = ()  # what the night must have: "killer", "two_wipes", "note"

    def asked(self, lang: str, **night: object) -> str:
        return (self.no if lang == "no" else self.en).format(**night)


DISCOVER = [
    Discover("mechanic_hits", "Who got hit most by {killer} tonight?", "Hvem ble truffet mest av {killer} i kveld?",
             ("killer",)),
    Discover("raid_note", "Were the assignments followed tonight?", "Ble assignments fulgt i kveld?", ("note",)),
    Discover("compare_pulls", "What did pull {best} do differently from pull {last}?",
             "Hva gjorde pull {best} annerledes enn pull {last}?", ("two_wipes",)),
    Discover("healing", "Were the healing cooldowns placed well tonight?",
             "Ble healer-cooldownene brukt på riktige steder i kveld?"),
    Discover("pull_map", "Where did everyone stand on pull {best}?", "Hvor sto alle på pull {best}?", ("two_wipes",)),
    Discover("defensives", "Who pressed a defensive when it counted tonight?",
             "Hvem trykket defensiv når det gjaldt i kveld?"),
    Discover("player_history", "Am I getting better from week to week?", "Blir jeg bedre fra uke til uke?"),
]


def discover(uses: Counter[str], has: set[str], last_pick: str | None) -> Discover | None:
    """The question to offer: of those the night has what they need for (`has`), the one whose tool the server's
    answers used least (`uses`), not the one offered last time (`last_pick`, a tool) when another fits."""
    fits = [d for d in DISCOVER if set(d.needs) <= has]
    fresh = [d for d in fits if d.tool != last_pick] or fits
    least = min((uses[d.tool] for d in fresh), default=None)
    return next((d for d in fresh if uses[d.tool] == least), None)  # the first in DISCOVER of the least used


@dataclass
class Recap:
    """The post's parts: its text, the boss whose night it draws (its last fight, which a reply is about), the
    questions its buttons ask, and the tool Discover offered."""
    text: str
    boss: str
    fight: int
    questions: list[str] = field(default_factory=list)
    pick: str | None = None


def main_boss(rep: Report) -> str | None:
    """The boss the night was about: the one pulled most past resets (the later one of a tie); None for a night
    with fewer than MIN_PULLS such pulls on any boss."""
    counts = {b: sum(1 for f in fs if not f.get("inProgress") and not is_reset(f)) for b, fs in rep.by_boss.items()}
    boss = max(reversed(list(counts)), key=lambda b: counts[b], default=None)
    return boss if boss and counts[boss] >= MIN_PULLS else None


def _pct(x: float, lang: str) -> str:
    return f"{x:.1f}".replace(".", ",") + " %" if lang == "no" else f"{x:.1f}%"


def _whole(x: float, lang: str) -> str:
    return f"{x:.0f} %" if lang == "no" else f"{x:.0f}%"


def _said(text: str, lang: str) -> str:
    """wipe_text's 'P2 64.0%' as the language writes it."""
    return text.replace(".", ",").replace("%", " %") if lang == "no" else text


def _and(names: list[str], lang: str) -> str:
    word = " og " if lang == "no" else " and "
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + word + names[-1]


def lines(rep: Report, view: dict, lang: str) -> str:
    """The post's text from the night view on the boss (views.night_view, what its picture draws)."""
    no = lang == "no"
    boss, rows = view["boss"], view["pulls"]
    kills = [b["boss"] for b in view["bosses"] if b["kill"] and b["boss"] != boss]
    head = "**Det var kvelden.** " if no else "**That's the raid.** "
    if kills:
        head += (f"Kill på {_and(kills, lang)}, så " if no else f"Kills on {_and(kills, lang)}, then ")
    head += f"{len(rows)} pulls på {boss}:" if no else f"{len(rows)} pulls on {boss}:"
    out = []
    if kill := next((r for r in rows if r["kill"]), None):
        out.append(f"**Drept på pull {kill['pull']}!**" if no else f"**Killed on pull {kill['pull']}!**")
    elif best := next((r for r in reversed(rows) if r["best"]), None):
        f = rep.fight(best["pull"], boss)
        said, pct, before = _said(wipe_text(f, wcl=False), lang), f.get("fightPercentage"), view["guild_best"]
        line = f"Beste wipe: {said} på pull {best['pull']}" if no else f"Best wipe: {said} on pull {best['pull']}"
        if before and pct is not None and pct < before["pct"]:
            line = f"**{line}, ny guild-best**" if no else f"**{line}, a new guild best**"
            if said != _said(f"{pct:.1f}%", lang):  # by phase: the guild's best is Warcraft Logs' measure
                line += (f" (WCL {_pct(pct, lang)}, {_pct(before['pct'], lang)} før)" if no
                         else f" (WCL {_pct(pct, lang)}, {_pct(before['pct'], lang)} before)")
        elif before:
            line += (f" (guildens beste: WCL {_pct(before['pct'], lang)})" if no
                     else f" (the guild's best: WCL {_pct(before['pct'], lang)} on {before['day']})")
        out.append(line + ".")
    if (t := view.get("thirds")) and not kill:
        steps = " → ".join(_whole(x, lang) for x in t)
        if t[2] < t[0] - 1:
            out.append(f"Wipene ble bedre utover kvelden: {steps} i snitt." if no
                       else f"Wipes got better over the night: {steps} on average.")
        elif t[2] > t[0] + 1:
            out.append(f"Wipene ble dårligere utover kvelden: {steps} i snitt." if no
                       else f"Wipes got worse over the night: {steps} on average.")
        else:
            out.append(f"Wipene lå rundt {_whole(t[1], lang)} hele kvelden." if no
                       else f"Wipes stayed around {_whole(t[1], lang)} all night.")
    wipes = [(what, n) for what, n in view["ends"] if what not in NOT_WIPES]
    if wipes and (total := sum(n for _, n in wipes)) >= MIN_PULLS:
        what, n = wipes[0]
        if what.endswith(" killing many"):
            ability = what.removesuffix(" killing many")
            out.append(f"{ability} avsluttet {n} av {total} wipes." if no else f"{ability} ended {n} of {total} wipes.")
        elif what.startswith("the boss gaining ") and what.endswith(" and keeping it to the end"):
            buff = what.removeprefix("the boss gaining ").removesuffix(" and keeping it to the end")
            out.append(f"Bossen fikk {buff} og beholdt den til slutten på {n} av {total} wipes." if no
                       else f"The boss gained {buff} and kept it to the end on {n} of {total} wipes.")
        elif what == "deaths one after another":
            out.append(f"Dødsfall etter hverandre avsluttet {n} av {total} wipes." if no
                       else f"Deaths one after another ended {n} of {total} wipes.")
        else:
            out.append(f"Flest wipes endte med {what} ({n} av {total})." if no
                       else f"Most wipes ended with {what} ({n} of {total}).")
    return head + "".join(f"\n- {line}" for line in out)


async def recap(rep: Report, lang: str, uses: Counter[str], has_note: bool, last_pick: str | None) -> Recap | None:
    """The night's recap of a report that ended, in `lang`; None for a night too short for one (main_boss)."""
    if (boss := main_boss(rep)) is None:
        return None
    view = await night_view(rep, boss)
    rows, no = view["pulls"], lang == "no"
    killed = any(r["kill"] for r in rows)
    questions = [(f"Hva gikk bra på killen av {boss}?" if no else f"What went right on the {boss} kill?") if killed
                 else (f"Hva fikser vi på {boss} til neste gang?" if no else f"What do we fix on {boss} next time?")]
    wipes = [r for r in rows if not r["kill"] and not r["reset"] and not r["live"]]
    best = next((r["pull"] for r in reversed(rows) if r["best"]), None)
    night = {"boss": boss, "best": best, "last": wipes[-1]["pull"] if wipes else None,
             "killer": next((k for k, _ in view["firsts"] if k and k != NO_KILLING_BLOW), None)}
    has = {"note"} if has_note else set()
    has |= {"killer"} if night["killer"] else set()
    has |= {"two_wipes"} if best and night["last"] and best != night["last"] else set()
    pick = discover(uses, has, last_pick)
    if pick:
        questions.append(pick.asked(lang, **night))
    fights = rep.by_boss[boss]
    return Recap(lines(rep, view, lang), boss, fights[-1]["id"], [q[:80] for q in questions],
                 pick.tool if pick else None)


def last_key(discord_guild_id: int) -> str:
    """Where the server's last recap is kept (Store.state): its report's code and end, and the tool Discover
    offered."""
    return f"recap:{discord_guild_id}"


def last(saved: str | None) -> dict | None:
    try:
        return json.loads(saved) if saved else None
    except ValueError:
        return None
