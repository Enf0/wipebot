"""What's new, posted to the channel a server's admins chose (/wipebot news) when the bot starts on a release it
hasn't posted yet, in the server's language (/wipebot setup language:). From CHANGELOG.md's items a raider in
Discord notices (tagged `Agent` or `Discord`), the model says each in a short line for raiders, most important
first, leaving out what only matters elsewhere; the post shows each item's pictures under its line and counts what it
left to the changelog. What's new decides whether a post is worth it and leads it; a few fixes may follow. Without
the model, in English, each item's bold lead and first sentence, Added first, then Changed and Fixed."""
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import discord

from wipebot.core import server

# Wipebot's, at its root above its installed source: the checkout, /app in the image
CHANGELOG = next((d / "CHANGELOG.md" for d in Path(__file__).resolve().parents
                  if (d / "CHANGELOG.md").exists()), Path("CHANGELOG.md"))
SHOWN = 10  # lines in a post at most; the rest are counted
FIXES = 3  # lines on fixes in a post at most, after the new things that make it worth posting
SECTIONS = ("Added", "Changed", "Removed", "Fixed")  # in the order a post takes them
FOR_DISCORD = {"Agent", "Discord"}  # an item tagged only `MCP` is Claude's and ChatGPT's
VERSION = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)
PICTURE = re.compile(r"!\[([^\]]*)\]\((https://[^)\s]+)\)")  # on a line of its own under the item
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(`])")
MAX_PICTURES = 10  # Discord's gallery holds 10
MAX_LINE = 250
ENGLISH = "English"
# The post's own words, which the model gives in the post's language
LABELS = {"heading": "What's new in", "more": "more in the changelog", "all": "All of it in the changelog"}

REWRITE = """These changes are in a new release of {name}, a Discord bot that answers raiders' questions about their \
World of Warcraft raid logs from Warcraft Logs. A raider reads this in the guild's channel. Be critical about what \
makes a post worth it: something new to ask or see, or an answer that is clearly better (Added and Changed). If none \
is, answer with no lines: then nothing is posted, and these changes wait for the next post. When one is, the post \
leads with those, then may add up to {fixes} fixes or improvements a raider would notice (Fixed), most noticed \
first; leave out small tweaks, wording, fixes few would notice and how the bot works inside. Say each one you keep \
in one short, plain line for raiders, in {language} (at most 25 words), starting with its name in bold, like \
"**Kicks by pull.** You can now ask who missed their kick on each pull, and see it drawn." Leave out what only \
matters in Claude or ChatGPT, to whoever runs the bot, or to the server's admins (setup, settings and admin \
commands: raiders never use them), and keep command names as they are written. Raiders who speak another language \
still say most game terms in English, so keep them in English, in that language's grammar, never translated: \
"holy paladin" (not "hellig paladin"), "spec" (not "spesialisering"), "healere" (not "helere"), "guilden" (not \
"gildet"), "armor" (not "rustning"), "pulls" (not "trekk"), "parse", "kill", "wipe", "cooldown", "DPS" and \
abilities' names. Among the new ones, put those with a picture \
first (the post shows it under the line), then the most important, at most {shown} lines in all. Answer with JSON only:
{{"lines": [{{"item": <its number>, "line": "<the line>"}}], "heading": "<'What's new in' in {language}, \
without the name: it follows>", \
"more": "<'more in the changelog' in {language}>", "all": "<'All of it in the changelog' in {language}>"}}

{items}"""


@dataclass
class Item:
    section: str
    text: str  # as the changelog has it, without its tags and pictures
    pictures: list[tuple[str, str]] = field(default_factory=list)  # (alt, url)

    @property
    def short(self) -> str:
        """Its bold lead and first sentence."""
        if lead := re.match(r"(\*\*.+?\*\*)\s*(.*)", self.text):
            return f"{lead[1]} {SENTENCE_END.split(lead[2], maxsplit=1)[0]}".strip()
        return SENTENCE_END.split(self.text, maxsplit=1)[0]


@dataclass
class Post:
    version: str  # the newest it covers
    lines: list[str]
    pictures: list[list[tuple[str, str]]]  # each line's item's, (alt, url)
    more: int  # items left to the changelog
    labels: dict[str, str] = field(default_factory=lambda: dict(LABELS))  # in its language

    def parts(self, name: str) -> list[str | list[tuple[str, str]]]:
        """The post in order: its texts, and after a line with pictures, those pictures."""
        changelog = f"{server.source_url()}/blob/main/CHANGELOG.md"
        more = f"{self.more} {self.labels['more']}" if self.more else self.labels["all"]
        out: list[str | list[tuple[str, str]]] = [f"### {self.labels['heading']} {name} {self.version}"]
        left = MAX_PICTURES  # in all: a message holds so many
        for line, pictures in zip(self.lines, self.pictures, strict=True):
            if isinstance(out[-1], str):
                out[-1] += f"\n- {line}"
            else:
                out.append(f"- {line}")
            if pictures[:left]:
                out.append(pictures[:left])
                left -= len(out[-1])
        tail = f"-# {more}: <{changelog}>"
        if isinstance(out[-1], str):
            out[-1] += "\n" + tail
        else:
            out.append(tail)
        return out

    def text(self, name: str) -> str:
        return "\n".join(p for p in self.parts(name) if isinstance(p, str))

    def layout(self, name: str) -> discord.ui.LayoutView:
        """The post as Components V2: its lines, each item's pictures right under its own."""
        view = discord.ui.LayoutView(timeout=None)
        for part in self.parts(name):
            view.add_item(discord.ui.TextDisplay(part) if isinstance(part, str) else discord.ui.MediaGallery(
                *(discord.MediaGalleryItem(url, description=alt[:256] or None) for alt, url in part)))
        return view

    def to_json(self) -> str:
        return json.dumps({"version": self.version, "lines": self.lines, "pictures": self.pictures,
                           "more": self.more, "labels": self.labels})

    @classmethod
    def from_json(cls, text: str) -> Post:
        d = json.loads(text)
        return cls(d["version"], d["lines"], [[(alt, url) for alt, url in p] for p in d["pictures"]], d["more"],
                   d["labels"])


def read() -> str | None:
    return CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else None


def latest(changelog: str) -> str | None:
    """The newest released version (Unreleased isn't one)."""
    return m[1] if (m := VERSION.search(changelog)) else None


def previous(changelog: str) -> str | None:
    """The version before the newest, whose post the newest's is."""
    versions = VERSION.findall(changelog)
    return versions[1] if len(versions) > 1 else None


def since(changelog: str, version: str) -> list[str]:
    """The sections of the versions released after `version`, newest first; none when the changelog doesn't have
    it (the bot went back to an older release)."""
    found = list(VERSION.finditer(changelog))
    names = [m[1] for m in found]
    if version not in names:
        return []
    ends = [m.start() for m in found[1:]] + [len(changelog)]
    return [changelog[m.end():end] for m, end in zip(found, ends, strict=True)][:names.index(version)]


def items(changelog: str, after: str) -> list[Item]:
    """The items for Discord released after version `after`: Added first, then Changed, Removed and Fixed, the
    newest version first in each."""
    found: list[tuple[str, str]] = []  # (section, markdown)
    for text in since(changelog, after):
        section = ""
        for line in text.splitlines():
            if line.startswith("### "):
                section = line[4:].strip()
            elif line.startswith("- "):
                found.append((section, line[2:].strip()))
            elif line.startswith("  ") and line.strip() and found:  # an item wrapped onto more lines
                found[-1] = (found[-1][0], found[-1][1] + "\n" + line.strip())
    out = []
    for section, text in found:
        tags = re.match(r"((?:`[^`]+`\s*)*)(.*)", text, re.S)
        assert tags
        if (named := set(re.findall(r"`([^`]+)`", tags[1]))) and not named & FOR_DISCORD:
            continue
        out.append(Item(section, " ".join(PICTURE.sub("", tags[2]).split()), PICTURE.findall(tags[2])))
    rank = {name: n for n, name in enumerate(SECTIONS)}
    return sorted(out, key=lambda i: rank.get(i.section, len(SECTIONS)))  # stable: newest first in a section


def picked(answer: str, count: int) -> tuple[list[tuple[int, str]], dict[str, str]] | None:
    """The model's lines, (item's index, its line), none when it kept none, and the post's own words; None when the
    answer isn't what was asked."""
    try:
        found = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
        picks = [(int(p["item"]) - 1, " ".join(str(p["line"]).split())) for p in found["lines"]]
        labels = {k: " ".join(str(found[k]).split()) for k in LABELS}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not all(0 < len(v) <= 60 for v in labels.values()):
        return None
    seen: set[int] = set()
    out = []
    for n, line in picks:
        if 0 <= n < count and n not in seen and line and len(line) <= MAX_LINE:
            seen.add(n)
            out.append((n, line))
    return out[:SHOWN], labels


def shaped(picks: list[tuple[int, str]], found: list[Item]) -> list[tuple[int, str]]:
    """A post's lines as it goes out: what's new first, then at most FIXES fixes; none when only fixes are left, as
    fixes alone aren't worth a post (they wait for the next)."""
    new = [(n, line) for n, line in picks if found[n].section != "Fixed"]
    return new + [(n, line) for n, line in picks if found[n].section == "Fixed"][:FIXES] if new else []


async def write(changelog: str, after: str, name: str, ask: Callable[[str], Awaitable[str]] | None = None,
                language: str | None = None) -> Post | None:
    """What's new since version `after`, for Discord, its lines by the model (`ask`: a prompt to its answer) in
    `language` (English for None) or, without one, in English, each item's bold lead and first sentence. None when
    nothing is new for Discord, the model found nothing worth a raider's read, or it failed."""
    found = items(changelog, after)
    version = latest(changelog)
    if not found or not version:
        return None
    picks: list[tuple[int, str]] = [(n, i.short) for n, i in enumerate(found[:SHOWN])]
    labels = dict(LABELS)
    if ask:
        numbered = "\n".join(f"{n}. ({i.section}{', with a picture' if i.pictures else ''}) {i.text}"
                             for n, i in enumerate(found, 1))
        prompt = REWRITE.format(name=name, shown=SHOWN, fixes=FIXES, items=numbered, language=language or ENGLISH)
        try:
            answer = picked(await ask(prompt), len(found))
        except Exception:  # nothing posted: its changes wait for the next post
            answer = None
        if answer is None or not answer[0]:
            return None
        picks, labels = answer
    if not (picks := shaped(picks, found)):
        return None
    return Post(version, [line for _, line in picks], [found[n].pictures for n, _ in picks],
                len(found) - len(picks), labels)
