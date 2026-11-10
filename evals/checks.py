"""The checks on one answer.

An answer passes when it gets the case's main point across (`expect`) and states nothing on its `wrong` list (a
model judges both, one question each). How many of the case's supporting `facts` it states is counted apart and
never fails it. Code also gives warnings that don't fail it (wipebot.discord.checks): numbers no tool gave, and a site
credited for a fact; and it measures how the answer is built (`built`, wipebot.discord.form), which as the bot is held
to the bot's prompt: its slips never fail it either.
"""
import asyncio
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from statistics import median

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from wipebot.discord import form, texts
from wipebot.discord.prompt import budget

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
    """The judge's verdict; asked again when it fails or answers without one (a usage limit hit mid-run), and at
    once when it says UNKNOWN: an answer it leaves undecided to the end is an error, in no count."""
    cost = 0.0
    for tries in range(1, JUDGE_TRIES + 1):
        try:
            text, spent = await _ask_judge(prompt, model)
            cost += spent
        except Exception as e:  # the SDK's errors for a limit or an overload
            text = f"the judge failed: {type(e).__name__}: {e}"
        verdicts = VERDICT.findall(text)  # the last one: the reasoning may quote the words before it
        if (verdicts and verdicts[-1] != "UNKNOWN") or tries == JUDGE_TRIES:
            break
        if not verdicts:
            await asyncio.sleep(JUDGE_WAIT_S * tries)
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


MODELS: set[str] = set()  # the models the judge's replies came from, by full name: for a run's record


async def _ask_judge(prompt: str, model: str) -> tuple[str, float]:
    options = ClaudeAgentOptions(tools=[], setting_sources=[], strict_mcp_config=True, max_turns=1, model=model)
    text, cost = "", 0.0
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            MODELS.add(msg.model)
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        elif isinstance(msg, ResultMessage):
            cost = msg.total_cost_usd or 0.0
    return text, cost


def expect_prompt(question: str, expect: str, answer: str) -> str:
    return EXPECT.format(question=question, expect=" ".join(expect.split()), answer=answer)


def wrong_prompt(question: str, wrong: list[str], answer: str) -> str:
    return WRONG.format(question=question, wrong="\n".join(f"- {w}" for w in wrong), answer=answer)


# ---- how an answer is built: measured by code on every answer, no model, never a pass or fail ----

def languages(case: dict, answer: str, mode: str = "plugin") -> tuple[str, str] | None:
    """(the language the case asks in, the language the answer is in), each 'no' or 'en', told the way the bot tells
    a question's (texts.language: which language's common words a text has more of, with the game's English words
    in neither count). The question's own first; where that doesn't show (a short one, a bare link), the questions
    before it in the conversation, then, as the bot, the server's language the case sets (`language`), as the bot's
    prompt says. None where the check says nothing: a post, whose question is the bot's own English text, a case
    whose language shows nowhere, or an answer with as many of one language's words as of the other's."""
    if case.get("kind") == "posts":
        return None
    *before, last = case["question"] if isinstance(case["question"], list) else [case["question"]]
    asked = (texts.language(last, "") or texts.language(" ".join(before), "")
             or (texts.named(case["language"]) if mode == "bot" and case.get("language") else ""))
    answered = texts.language(answer, "")
    return (asked, answered) if asked and answered else None


def in_language(case: dict, answer: str, mode: str = "plugin") -> bool | None:
    """Whether the answer is in the language its case asks in (`languages`); None where that can't be told. An
    answer in another language than its question fails (the "language" part of run.PARTS), whatever the judge made
    of it: the raider asked in theirs."""
    found = languages(case, answer, mode)
    return None if found is None else found[0] == found[1]


def built(case: dict, answer: str, mode: str) -> tuple[dict, dict[str, str] | None]:
    """How the answer is built (form.Form as a dict: its words, its verdict's and each bullet's, where the change to
    make stands, its follow-ups) and, as the bot, where it leaves the form the bot's prompt asks for (Form.slips, by
    kind), held to what its case is: a post after a wipe, an answer while the raid is going on, or one in chat. In a
    client the form is the client's own, so there the slips are None."""
    measured = form.measure(answer)
    as_bot = budget(post=case.get("kind") == "posts", live=bool(case.get("live")))
    return asdict(measured), measured.slips(as_bot) if mode == "bot" else None


def built_summary(trials: Iterable[tuple[dict, dict[str, str] | None]]) -> list[str]:
    """Over a run's answers (each `built`): the middle answer's words, bullets, a bullet's words and the verdict's;
    then, as the bot, how many leave its prompt's form and each kind of slip with its count, most frequent first."""
    trials = [(f, slips) for f, slips in trials if f]
    if not trials:
        return []
    forms = [f for f, _ in trials]
    bullets = [n for f in forms for n in f["bullet_words"]]
    verdicts = [f["verdict_words"] for f in forms if f["verdict_words"]]
    lines = [f"how the answers are built (the middle one of {len(forms)}): {median(f['words'] for f in forms):.0f} "
             f"words, {median(len(f['bullet_words']) for f in forms):.0f} bullets"
             + (f" of {median(bullets):.0f} words" if bullets else "")
             + (f", a verdict of {median(verdicts):.0f} words" if verdicts else "")
             + f", {median(f['follow_ups'] for f in forms):.0f} follow-ups"]
    held = [slips for _, slips in trials if slips is not None]
    if held:
        kinds = Counter(kind for slips in held for kind in slips)
        lines.append(f"{sum(bool(slips) for slips in held)} of {len(held)} leave the form the bot's prompt asks for"
                     + (": " + ", ".join(f"{kind} {n}" for kind, n in kinds.most_common()) if kinds else ""))
    return lines


# ---- checks by code that fail an answer: what a case's `numbers` and `form` ask for, no model ----

FORM_KEYS = ("verdict_words", "bullets", "bullet_words", "bullet_sentences", "lines", "chars")  # form.Budget's
LEADING_ZERO = re.compile(r"(?<![\d:])0(?=\d:\d\d)")  # 02:32 is 2:32


def states(wanted: str, answer: str) -> bool:
    """Whether the answer states every number of `wanted` the way the lint reads a number: a time exactly (02:32 for
    2:32 too), an amount or a percentage as rounded, a count as a number of its own (16, not the 16 of 2:16 or of
    16.4k). Read in what a raider reads: no link's address, no follow-up line."""
    from evals.lint import AMOUNT, PERCENT, TIME, in_output, numbers
    read = "\n".join(line for line in answer.split("\n") if not form.FOLLOW_UP.match(line))
    text = LEADING_ZERO.sub("", form.visible(read))
    return all(in_output(n, text) if TIME.fullmatch(n) or AMOUNT.fullmatch(n) or PERCENT.fullmatch(n)
               else re.search(rf"(?<![\d.:]){re.escape(n)}(?!\d|[.:]\d)", text) is not None
               for n in numbers(LEADING_ZERO.sub("", wanted)))


def coded(case: dict, answer: str, mode: str) -> dict[str, str]:
    """The checks code makes of an answer, for the cases that ask for one: each named part with why the answer fails
    it, "" when it passes. A part the case doesn't ask for is left out.
    - `numbers`: every entry is stated (states); "7:58 or 7:59" takes either. For the numbers a case's `expect`
      names as its point, which a judge told that rounded numbers and missing detail are fine lets through.
    - `form`: as the bot, the answer keeps to the bullets, sentences, words, lines and characters the case asks for
      (form.Budget's keys, measured by wipebot.discord.form; `chars` is "about" so many, as a post's). In a client
      the form is the client's own: not checked there."""
    found = {}
    if wanted := case.get("numbers"):
        missing = [w for w in wanted if not any(states(alt, answer) for alt in w.split(" or "))]
        found["numbers"] = f"doesn't state {', '.join(missing)}" if missing else ""
    if (asked := case.get("form")) and mode == "bot":
        limits = {key: asked.get(key) for key in FORM_KEYS} | (
            {"chars": round(asked["chars"] * form.POST_SLACK)} if "chars" in asked else {})
        found["form"] = "; ".join(form.measure(answer).slips(form.Budget(**limits)).values())
    return found


def coded_keys(case: dict) -> str:
    """What is wrong with a case's `numbers` and `form` keys, or "": a number its `expect` doesn't state (the check
    is for the point the case already makes, not a second one), or a limit nothing measures."""
    from evals.lint import numbers
    for wanted in case.get("numbers", []):
        ways = wanted.split(" or ")  # the same moment as tools print it: a cast at 7:58, its deaths at 7:59
        if not all(numbers(way) for way in ways):
            return f"numbers: \"{wanted}\" holds no number"
        if not any(states(way, " ".join(case["expect"].split())) for way in ways):
            return f"numbers: its expect doesn't state {wanted}"
    asked = case.get("form", {})
    if unknown := set(asked) - set(FORM_KEYS):
        return f"form: unknown limits {', '.join(sorted(unknown))} (it takes {', '.join(FORM_KEYS)})"
    if "form" in case and (not asked or not all(isinstance(v, int) and v > 0 for v in asked.values())):
        return "form: each limit is a whole number above 0, and there is at least one"
    return ""


# ---- checks that never fail an answer: run on saved answers (`just evals --recheck`) ----

GROUNDED = """You check an answer from a World of Warcraft raid-analysis bot against what its tools returned. The tools
compute every fact from the raid's log and fetch the guides; the bot should state only what they show, and say so when
it can't tell.

Question: {question}

<tool_outputs>
{outputs}
</tool_outputs>

<answer>
{answer}
</answer>

Find the claims in the answer that would mislead a raider about what happened in this raid or what to change:
- a cause, a fault or a job stated as fact that the outputs don't show (who caused it, whose fault it wasn't, who was
  meant to soak or stand where, what lost the pull);
- something about players the outputs don't show: where they stood or moved, what they could have pressed, how long a
  cooldown or buff lasted or when it would be ready;
- a comparison or ranking no output makes ("the biggest problem", "the only one that");
- a number or time the answer uses to back its point that the outputs contradict.

Leave out: a slip in a side detail that doesn't change the answer's point (a count off by one, a rounded time), a fair
reading of the outputs or of the guide text in them, a possibility the answer hedges ("may", "likely", "can't tell"),
and general advice that names nothing in this raid.

Reply with one line per claim, "MISLEADING: ...", quoting the answer briefly and saying in a few words what the
outputs show; then a last line with only GROUNDED (none) or NOT GROUNDED."""

# How the answer is built, the skill's rules for every answer: each judged alone, binary, with "n/a" where the rule
# doesn't apply. A score to watch, never a pass or fail.
RUBRIC = {
    "verdict_first": "Does the answer open with its main finding (what ended the pull, what cost the most, the "
                     "direct answer to the question), rather than background, a list of events or a hedge?",
    "concrete_fix": "When the answer recommends a change, does the change say who does what and when (a cast, a "
                    "phase, a time, a mechanic's moment), rather than a general wish ('handle the mechanic better', "
                    "'use more cooldowns')? Reply N/A when it recommends no change.",
    "few_changes": "Does the answer keep its recommended changes few and ordered: at most one change for one pull, "
                   "at most three, most important first, for a night or a player? Reply N/A when it recommends no "
                   "change.",
}

RUBRIC_PROMPT = """You check one thing about an answer from a World of Warcraft raid-analysis bot that raiders ask in
Discord.

Question: {question}

{rule}

<answer>
{answer}
</answer>

Reply with one sentence of reasoning, then a last line with only YES, NO or N/A."""

GROUNDED_LINE = re.compile(r"^\s*MISLEADING:\s*(.+)$", re.M)
# The grounded check's judge. Against a Claude council's labels of 26 answers (22 October 2026: evals/
# grounding-labels.jsonl) Opus flagged all 10 answers the council found a misleading claim in, and 9 of the 16 it
# didn't (Sonnet: 15 of 16): a list to read, not a verdict.
GROUNDED_MODEL = "opus"
RUBRIC_VERDICT = re.compile(r"\b(YES|NO|N/A)\b")


@dataclass
class Grounding:
    ok: bool | None  # None: the judge gave no verdict
    misleading: list[str]  # the claims the tool outputs don't back, in the judge's words
    cost_usd: float = 0.0


def tool_outputs(calls: list[dict]) -> str:
    """The answer's tool calls and what each returned, as the judge reads them."""
    return "\n\n".join(f"[{c.get('name')} {json.dumps(c.get('args') or {}, ensure_ascii=False)}]\n{c.get('result')}"
                       for c in calls)


async def grounded(question: str, answer: str, calls: list[dict], model: str = GROUNDED_MODEL) -> Grounding:
    """The answer's claims about the raid that its tool outputs don't back and that would mislead a raider."""
    text, cost = await _ask_patiently(GROUNDED.format(question=question, outputs=tool_outputs(calls),
                                                      answer=answer), model)
    last = text.strip().splitlines()[-1].strip(" *`.").upper() if text.strip() else ""
    ok = True if last == "GROUNDED" else False if last == "NOT GROUNDED" else None
    return Grounding(ok, GROUNDED_LINE.findall(text), cost)


async def rubric(question: str, answer: str, model: str) -> tuple[dict[str, bool | None], float]:
    """Each RUBRIC rule's verdict on the answer: True, False, or None where it doesn't apply or the judge gave
    none."""
    async def one(rule: str) -> tuple[bool | None, float]:
        text, cost = await _ask_patiently(RUBRIC_PROMPT.format(question=question, rule=rule, answer=answer), model)
        verdicts = RUBRIC_VERDICT.findall(text)
        return {"YES": True, "NO": False}.get(verdicts[-1] if verdicts else "", None), cost
    got = await asyncio.gather(*(one(rule) for rule in RUBRIC.values()))
    return dict(zip(RUBRIC, (v for v, _ in got), strict=True)), sum(c for _, c in got)


async def _ask_patiently(prompt: str, model: str) -> tuple[str, float]:
    """The judge's reply, asked again after a wait when the SDK fails (a usage limit or an overload); "" at last."""
    for tries in range(1, JUDGE_TRIES + 1):
        try:
            return await _ask_judge(prompt, model)
        except Exception:  # noqa: BLE001  the SDK's errors for a limit or an overload
            if tries < JUDGE_TRIES:
                await asyncio.sleep(JUDGE_WAIT_S * tries)
    return "", 0.0
