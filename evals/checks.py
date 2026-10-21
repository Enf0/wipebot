"""The checks on one answer.

An answer passes when it gets the case's main point across (`expect`) and states nothing on its `wrong` list (a
model judges both, one question each). How many of the case's supporting `facts` it states is counted apart and
never fails it. Code also gives warnings that don't fail it (wipebot.discord.checks): numbers no tool gave, and a site
credited for a fact.
"""
import asyncio
import re
from dataclasses import dataclass

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

VERDICT = re.compile(r"\b(PASS|FAIL|UNKNOWN)\b")


FACT_LINE = re.compile(r"^\s*(\d+)\W+(YES|NO)\b", re.M | re.I)

FACTS = """You check an answer from a World of Warcraft raid-analysis bot that raiders ask in Discord.

Question: {question}

Which of these facts does the answer state? Other words and rounded numbers are fine; a fact it leaves out or gets
wrong is NO.
{facts}

<answer>
{answer}
</answer>

Reply with one line per fact, its number then YES or NO (e.g. "1 YES"), and nothing else."""


@dataclass
class Facts:
    stated: int  # of the case's facts, how many the answer states
    of: int
    missing: list[str]
    cost_usd: float = 0.0


async def facts(question: str, listed: list[str], answer: str, model: str) -> Facts | None:
    """How many of a case's supporting facts an answer states (None when the judge's reply can't be read)."""
    prompt = FACTS.format(question=question, answer=answer,
                          facts="\n".join(f"{i}. {' '.join(f.split())}" for i, f in enumerate(listed, 1)))
    cost = 0.0
    for tries in range(1, JUDGE_TRIES + 1):
        try:
            text, spent = await _ask_judge(prompt, model)
            cost += spent
        except Exception as e:
            text = f"the judge failed: {type(e).__name__}: {e}"
        said = {int(n): yes.upper() == "YES" for n, yes in FACT_LINE.findall(text)}
        if set(said) >= set(range(1, len(listed) + 1)):
            missing = [f for i, f in enumerate(listed, 1) if not said[i]]
            return Facts(len(listed) - len(missing), len(listed), missing, cost)
        if tries < JUDGE_TRIES:
            await asyncio.sleep(JUDGE_WAIT_S * tries)
    return None


@dataclass
class Verdict:
    ok: bool | None  # None: the judge couldn't tell
    reason: str = ""
    cost_usd: float = 0.0


EXPECT = """You check one thing about an answer from a World of Warcraft raid-analysis bot that raiders ask in
Discord.

Question: {question}

Does the answer get this main point across? {expect}

Judge the answer as a whole, the way a raider reading it would take it: does it leave them with this point? Other
words, rounded numbers, extra detail and missing detail are fine, and so is a slip on a side matter. It fails if
the point is missing, if the answer's conclusion is a different one, or if anything in it contradicts the point.

<answer>
{answer}
</answer>

Reply with one sentence of reasoning, then a last line with only PASS, FAIL or UNKNOWN."""

WRONG = """You check one thing about an answer from a World of Warcraft raid-analysis bot that raiders ask in
Discord.

Question: {question}

Does the answer state any of these as true? They are wrong.
{wrong}

Count only a claim the answer itself makes, with the same meaning. Read each item as narrowly as it's written: a
remark near it, a broader or related claim, or something you could infer from the answer is not stating it. A claim
that something caused or lost the pull counts only if the answer names it as the cause, not when it mentions that it
happened. Ruling one out, or raising it as a possibility it doesn't vouch for, doesn't count either.

<answer>
{answer}
</answer>

Reply with one sentence of reasoning (which one it states, or none), then a last line with only PASS (states
none), FAIL (states one) or UNKNOWN."""


JUDGE_TRIES = 3
JUDGE_WAIT_S = 30  # before another try, times the tries so far: a usage limit or an overload passes


async def judge(prompt: str, model: str) -> Verdict:
    """The judge's verdict; asked again when it fails or answers without one (a usage limit hit mid-run)."""
    cost = 0.0
    for tries in range(1, JUDGE_TRIES + 1):
        try:
            text, spent = await _ask_judge(prompt, model)
            cost += spent
        except Exception as e:  # the SDK's errors for a limit or an overload
            text = f"the judge failed: {type(e).__name__}: {e}"
        if VERDICT.search(text) or tries == JUDGE_TRIES:
            break
        await asyncio.sleep(JUDGE_WAIT_S * tries)
    verdicts = VERDICT.findall(text)  # the last one: the reasoning may quote the words before it
    if not verdicts:
        return Verdict(None, f"no verdict: {text[:200]}", cost)
    reason = VERDICT.sub("", text).strip(" \n*.`")
    return Verdict({"PASS": True, "FAIL": False}.get(verdicts[-1]), " ".join(reason.split()), cost)


FAIL_VOTES = 3  # a failed check is judged this many times in all, the majority kept


async def judge_majority(prompt: str, model: str) -> Verdict:
    """The judge's verdict, and on a fail two more, the majority kept: the judge's error is mostly failing a right
    answer on a detail (evals/labels.jsonl), and a fail asked again often comes back a pass. A pass is kept as is."""
    first = await judge(prompt, model)
    if first.ok is not False:
        return first
    more = await asyncio.gather(*(judge(prompt, model) for _ in range(FAIL_VOTES - 1)))
    votes = [first, *more]
    cost = sum(v.cost_usd for v in votes)
    fails, passes = sum(v.ok is False for v in votes), sum(v.ok is True for v in votes)
    kept = first if fails >= passes else next(v for v in more if v.ok is True)
    return Verdict(fails < passes, f"{kept.reason} ({fails} of {len(votes)} judges failed it)", cost)


async def _ask_judge(prompt: str, model: str) -> tuple[str, float]:
    options = ClaudeAgentOptions(tools=[], setting_sources=[], strict_mcp_config=True, max_turns=1, model=model)
    text, cost = "", 0.0
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        elif isinstance(msg, ResultMessage):
            cost = msg.total_cost_usd or 0.0
    return text, cost


def expect_prompt(question: str, expect: str, answer: str) -> str:
    return EXPECT.format(question=question, expect=" ".join(expect.split()), answer=answer)


def wrong_prompt(question: str, wrong: list[str], answer: str) -> str:
    return WRONG.format(question=question, wrong="\n".join(f"- {w}" for w in wrong), answer=answer)
