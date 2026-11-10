"""How an answer is built, measured by code: its verdict line and how long it is, its bullets with the words and
sentences of each, where the change to make stands, how much there is to read and how many follow-ups it offers.

The bot's prompt (host-prompt.md) sets these, and a rule nothing measures slips unseen: the two-sentence bullet of
28 October 2026 was broken in more bullets after it than before. `measure` reads an answer as it's recorded
(Answer.full: the posted text with its picture marks and follow-up lines) and `Form.slips` holds it to a `Budget`,
the prompt's numbers, which a test keeps the same as the prompt's (prompt.budget picks the one for a post, an answer
while the raid is going on or one in chat; prompt.recorded_budget for a stored answer). The evals print both for
every answer as the bot, with no model (evals/checks.py); whatever shows recorded answers can do the same per answer
and by week. Nothing here reads a report or needs the bot running.
"""
import re
from dataclasses import dataclass, field

from .checks import TIME, amounts

FOLLOW_UP = re.compile(r"[ \t]*(?:[-*][ \t]*)?Follow-up:[ \t]*\S", re.I)  # a line host.FOLLOW_UP makes a button of
PICTURE = re.compile(r"[ \t]*\[\s*picture\s*:[^\]\n]*\][ \t]*", re.I)  # a line that only places a picture
MASKED_LINK = re.compile(r"\[([^\]\n]*)\]\(https?://[^)\s]+\)")  # [words](address): a raider reads the words
ICON = re.compile(r"<a?:\w+:\d+>")  # the bot's own emojis: a spec, an ability, a raid marker
MARKUP = re.compile(r"\*\*|__|`|(?<![\w*])\*(?=[^\s*])|(?<=[^\s*])\*(?![\w*])")  # bold, underline, code, italics
BULLET = re.compile(r"(?:[-*•]|\d+[.)])[ \t]+(.*)")  # a bullet at the top level, and what it says
NESTED = re.compile(r"[ \t]+(?:[-*•]|\d+[.)])[ \t]+")  # a line of a list under a bullet
SMALL = "-# "  # Discord's small text: the one line for a limit of what was checked
LINE_MARK = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)]|-#|>|#{1,3})[ \t]+", re.M)  # what opens a line and is no word
FIX = "⚠"  # the marker of the one change to make, with or without the selector that makes it an emoji
MARKER = re.compile(r"^(?:[⚠✅💀]\ufe0f?[ \t]*)+")  # the emojis a bullet may open with
LEAD = re.compile(r"^\*\*(.+?)\*\*[:.]?[ \t]*")  # a bullet's point, its first few words in bold
VERDICT = re.compile(r"\*\*(.+?)\*\*")  # the bold sentence an answer opens with
# The end of a sentence: a stop, then a capital ("f.eks. 0:07", "ca. 6:01" and "nr. 2" go on)
SENTENCE_END = re.compile(r"(?<=[.!?])[\"”)]?\s+(?=[\"“(]?[A-ZÆØÅÄÖÜ])")


@dataclass(frozen=True)
class Budget:
    """What an answer may run to, as the bot's prompt sets it; None: not held to it."""
    verdict_words: int | None = 20  # the bold sentence it opens with
    bullets: int | None = 5  # at the top level: the most any kind of question gets (a night, a player, a plan)
    bullet_words: int | None = 30  # each bullet, its bold point included, not the list under it
    bullet_sentences: int | None = 2  # after its bold point: one that backs it, a second for what to do
    lines: int | None = None  # a post after a wipe: its lines in all,
    chars: int | None = None  # and the characters a raider reads


CHAT = Budget()
LIVE = Budget(bullets=3, bullet_sentences=1)  # while the raid is going on: read between two pulls
POST_SLACK = 1.25  # a post is asked for "about" so many characters: this much over is still about


def post(lines: int, chars: int) -> Budget:
    """A post after a wipe, asked for in so many short lines of about so many characters (prompt.POST_LINES and
    POST_CHARS): held to those alone."""
    return Budget(verdict_words=None, bullets=None, bullet_words=None, bullet_sentences=None, lines=lines,
                  chars=round(chars * POST_SLACK))


@dataclass(frozen=True)
class Form:
    """One answer's form. Words and characters are what a raider reads: a link's words without its address, no
    icons, no markup or list marks, and neither the follow-up lines (buttons) nor the lines that place a picture."""
    words: int = 0
    chars: int = 0
    posted: int = 0  # characters as Discord counts them, addresses and icons included: past 2,000 it's two messages
    lines: int = 0  # lines with text
    verdict_words: int = 0  # the bold sentence it opens with; 0: it opens with none
    preamble: bool = False  # a line comes before the bold verdict
    bullet_words: list[int] = field(default_factory=list)  # each top-level bullet's own words, in order
    bullet_sentences: list[int] = field(default_factory=list)  # ...and its sentences after its bold point
    nested: int = 0  # lines of the lists under bullets
    fix: int | None = None  # which bullet opens with the marker of the change to make, from 1; None: none does
    markers: int = 0  # how many of that marker the answer has, wherever they stand
    closing: bool = False  # a paragraph follows the last bullet (the small line is none)
    small: int = 0  # small lines (a limit of what was checked)
    follow_ups: int = 0
    pictures: int = 0  # pictures the answer placed
    numbers: int = 0  # times and amounts

    @property
    def bullets(self) -> int:
        return len(self.bullet_words)

    def slips(self, budget: Budget = CHAT) -> dict[str, str]:
        """Where the answer runs past `budget` or leaves the form the prompt asks for: the kind of slip (the same
        few words for every answer, to count by) and this answer's own measure of it. Empty when it keeps to the
        form. An answer without bullets (a greeting, a line saying it can't) has no verdict to open with and is held
        only to its length."""
        out: dict[str, str] = {}
        b = budget
        if b.verdict_words is not None and self.bullets:
            if self.preamble:
                out["a line before the verdict"] = "a line comes before the bold verdict"
            elif not self.verdict_words:
                out["no bold verdict"] = "opens with no bold verdict"
            if self.verdict_words > b.verdict_words:
                out["a long verdict"] = f"the verdict is {self.verdict_words} words (at most {b.verdict_words})"
        if b.bullets is not None and self.bullets > b.bullets:
            out["many bullets"] = f"{self.bullets} bullets (at most {b.bullets})"
        if b.bullet_words is not None and (long := [n for n in self.bullet_words if n > b.bullet_words]):
            out["a long bullet"] = (f"{len(long)} of {self.bullets} bullets over {b.bullet_words} words "
                                    f"({', '.join(map(str, long))})")
        if b.bullet_sentences is not None and (
                many := [n for n in self.bullet_sentences if n > b.bullet_sentences]):
            out["a bullet of many sentences"] = (
                f"{len(many)} of {self.bullets} bullets over {b.bullet_sentences} "
                f"sentence{'s' if b.bullet_sentences > 1 else ''} after the bold point")
        if b.verdict_words is not None:  # an answer in chat: one change, marked, first, and nothing after the bullets
            if self.markers > 1:
                out["several changes marked"] = f"{self.markers} {FIX} markers (one change to make)"
            if self.fix and self.fix > 1:
                out["the change to make isn't first"] = f"the change to make is bullet {self.fix} (the first)"
            elif self.markers and not self.fix:
                out["the marker outside the bullets"] = f"{FIX} opens no bullet"
            if self.closing:
                out["a closing paragraph"] = "a paragraph after the last bullet"
        if b.lines is not None and self.lines > b.lines:
            out["many lines"] = f"{self.lines} lines (at most {b.lines})"
        if b.chars is not None and self.chars > b.chars:
            out["a long post"] = f"{self.chars} characters to read (about {round(b.chars / POST_SLACK)})"
        return out


def visible(text: str) -> str:
    """What a raider reads of posted text: a link's words without its address, no icons and no markup."""
    return MARKUP.sub("", ICON.sub("", MASKED_LINK.sub(r"\1", text)))


def _sentences(text: str) -> int:
    return len(SENTENCE_END.split(text.strip())) if text.strip() else 0


def measure(answer: str) -> Form:
    """The form of an answer as recorded (the posted text, its picture marks and its "Follow-up:" lines)."""
    lines = answer.split("\n")
    follow_ups = sum(bool(FOLLOW_UP.match(line)) for line in lines)
    pictures = sum(bool(PICTURE.fullmatch(line)) and "none" not in line.lower() for line in lines)
    body = [line for line in lines if not FOLLOW_UP.match(line) and not PICTURE.fullmatch(line)]
    posted = "\n".join(body).strip()
    text = LINE_MARK.sub("", visible(posted))
    written = [line for line in posted.split("\n") if line.strip()]
    bullet_words, bullet_sentences, after = [], [], []
    nested = small = 0
    fix, in_code = None, False
    for line in written:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif in_code:
            continue  # a text chart: its lines are no bullets
        elif NESTED.match(line):
            nested, after = nested + 1, []
        elif m := BULLET.match(line):
            said = ICON.sub("", m[1]).lstrip()
            if fix is None and FIX in said[:8]:  # before the bold point, or inside it where it couldn't be moved out
                fix = len(bullet_words) + 1
            said = MARKER.sub("", said)
            bullet_words.append(len(visible(said).split()))
            bullet_sentences.append(_sentences(visible(LEAD.sub("", said))))
            after = []
        elif line.startswith(SMALL):
            small += 1
        else:
            after.append(line)
    first = written[0] if written else ""
    verdict = VERDICT.match(first)
    return Form(
        words=len(text.split()), chars=len(text), posted=len(posted), lines=len(written),
        verdict_words=len(visible(verdict[1]).split()) if verdict else 0,
        preamble=not verdict and any(VERDICT.match(line) for line in written[1:3]),
        bullet_words=bullet_words, bullet_sentences=bullet_sentences, nested=nested, fix=fix,
        markers=posted.count(FIX), closing=bool(bullet_words and after), small=small, follow_ups=follow_ups,
        pictures=pictures, numbers=len(TIME.findall(text)) + len(amounts(text, every=True)))
