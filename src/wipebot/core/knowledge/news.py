"""Blizzard's hotfixes (the running post on Wowhead's blue tracker) and Wowhead's news posts."""
import asyncio
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

from .. import clock
from ..cache import DAY
from ..text import plural
from . import fetch
from .fetch import KnowledgeError
from .guides import cut
from .names import fold_quotes

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
NEWS_PAGE_DAYS = 1.5  # Wowhead posts about 20 news items (one listing page) every day and a half
NEWS_PAGES_MAX = 40
# A news title about tuning: the post read in full is the newest such, else the newest of all
TUNING = re.compile(r"\b(nerf|buff|tuning|hotfix|change)", re.I)
NEWS_POST_CHARS = 1500  # of the post read, the rest through web_lookup
NEWS_AFTER_S = 7 * DAY  # hotfix matches older than this: Wowhead's news is read too, for newer tuning


class HotfixParser(HTMLParser):
    """Blizzard's hotfix post -> (date, 'Heading › Subheading', change) per change. Dates are paragraphs
    ("October 6, 2026"), headings the bold text that opens a list item (class, spec, raid, boss)."""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, str, str]] = []
        self.date = ""
        self.path: list[str | None] = []  # per open <li>: its bold heading, if any
        self.text: list[str] = []
        self.bold: list[str] | None = None
        self.in_p = False

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._flush()
            self.path.append(None)
        elif tag in ("strong", "b"):
            self.bold = []
        elif tag == "p":
            self.in_p, self.text = True, []

    def handle_endtag(self, tag):
        if tag in ("strong", "b") and self.bold is not None:
            bold = " ".join("".join(self.bold).split())
            self.bold = None
            if self.in_p and re.fullmatch(rf"(?:{MONTHS}) \d+, \d{{4}}", bold):
                self.date = bold
            elif self.path and self.path[-1] is None and not "".join(self.text).strip():
                self.path[-1] = bold
                return
            self.text.append(bold)
        elif tag == "li":
            self._flush()
            self.path.pop()
        elif tag == "ul":
            self._flush()
        elif tag == "p":
            self.in_p, self.text = False, []

    def handle_data(self, data):
        if self.bold is not None:
            self.bold.append(data)
        else:
            self.text.append(data)

    def _flush(self):
        text = " ".join("".join(self.text).split())
        self.text = []
        if text and self.path and self.date:
            self.rows.append((self.date, " › ".join(h for h in self.path if h), text))


@dataclass
class Hotfix:
    date: str  # as the post says it: "October 6, 2026"
    path: str  # the headings it's under: "The Venomous Abyss › Sszorak", '' for none
    text: str
    when: float  # the date as a timestamp


async def hotfixes(about: str, days: int = 60) -> tuple[list[str], list[Hotfix]]:
    """(the posts' URLs, their changes from the last `days` days that mention `about`, newest first): Blizzard's
    running hotfix posts on Wowhead's blue tracker (its first pages carry them). Blizzard starts a new running post
    now and then (with a patch), so every one listed is read: the newest alone missed a month of changes. A change
    posted again is listed once."""
    listing = await fetch.cli("wowhead", "blue-tracker", "hotfixes", "--pages", "2", "--limit", "40")
    topics = [r["url"] for r in listing.get("results") or []
              if "/blue-tracker/topic/us/" in r.get("url", "") and "hotfix" in r.get("title", "").lower()]
    if not topics:
        raise KnowledgeError("no hotfix post found on Wowhead's blue tracker")
    parser = HotfixParser()
    for data in await asyncio.gather(*(fetch.cli("wowhead", "blue-topic", url) for url in topics)):
        for p in (data.get("posts") or {}).get("items") or []:
            if p.get("blue"):
                parser.feed(p.get("body_html") or "")
    since = clock.now() - days * 86400
    hits: dict[tuple[str, str, str], float] = {}
    for date, path, text in parser.rows:
        try:
            when = datetime.strptime(date, "%B %d, %Y").replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
        if when >= since and mentions(f"{path} {text}", about):
            hits.setdefault((date, path, text), when)
    return topics, [Hotfix(*k, when) for k, when in sorted(hits.items(), key=lambda kv: -kv[1])]


def mentions(text: str, about: str) -> bool:
    """Whether a hotfix mentions `about`: each of its words (but "the", "of") whole in it, a plural too. "Twin
    Fangs" isn't "Twinfang Harrower" in "Altar of Fangs"."""
    words = [w for w in re.findall(r"\w+", fold_quotes(about.lower())) if len(w) > 1 and w not in ("the", "of")]
    text = fold_quotes(text.lower())
    return all(re.search(rf"\b{re.escape(w)}(?:s|es)?\b", text) for w in words)


async def news_posts(about: str, since: float) -> list[dict]:
    """Wowhead's news posts about `about` since a timestamp, newest first: posted_at, title, url."""
    day = datetime.fromtimestamp(since, UTC).date()
    # Asked from the Monday before, so the question (and its cache entry) stays the same all week: a listing takes
    # about 10 s to read, and a date in it that moved every day was never found in the cache.
    monday = day - timedelta(days=day.weekday())
    from_s = datetime.combine(monday, datetime.min.time(), UTC).timestamp()
    pages = str(min(NEWS_PAGES_MAX, math.ceil((clock.now() - from_s) / 86400 / NEWS_PAGE_DAYS)))
    news = await fetch.cli("wowhead", "news", "--date-from", monday.isoformat(), "--pages", pages, "--limit", "20",
                           "--", about)
    return [p for p in news.get("results") or [] if (p.get("posted_at") or "9")[:10] >= day.isoformat()]


async def news_post(url: str) -> str:
    """A Wowhead news post's text."""
    post = await fetch.cli("wowhead", "news-post", "--", url)
    return ((post.get("content") or {}).get("text") or "").strip()


async def hotfixes_text(about: str, days: int = 60) -> str:
    """The hotfixes tool's answer. When no hotfix in the last NEWS_AFTER_S matches, Wowhead's news posts about it
    too: tuning is often posted there (a weekly reset's nerfs) before the running post has it."""
    urls, found = await hotfixes(about, days)
    lines = [f"Hotfixes mentioning {about!r} in the last {plural(days, 'day')}. Source: Blizzard's hotfix posts on "
             f"Wowhead's blue tracker {', '.join(urls)}"]
    lines += [f"- {h.date} · {h.path or 'General'}: {h.text}" for h in found[:40]]
    newest = max((h.when for h in found), default=0)
    if clock.now() - newest > NEWS_AFTER_S:  # nothing, or nothing recent: Wowhead's news may have newer tuning
        lines += ([] if found else ["none"]) + ["", await _news_text(about, days, newest)]
    return "\n".join(lines)


async def _news_text(about: str, days: int, after: float = 0) -> str:
    """Wowhead's news posts of the last `days` days about `about` (those after `after`, a timestamp), one line
    each, and the text of the newest about tuning (or of the newest)."""
    since = max(after, clock.now() - days * 86400)
    posts = await news_posts(about, since)
    span = (f"since {datetime.fromtimestamp(since, UTC).date().isoformat()}" if after
            else f"in the last {plural(days, 'day')}")
    if not posts:
        return f"No Wowhead news post about {about!r} {span} either."
    lines = [f"Wowhead news posts about {about!r} {span} (news articles, not Blizzard's hotfix list; what they "
             "report may be in a later hotfix post):"]
    lines += [f"- {(p.get('posted_at') or '')[:10]} {p.get('title')} {p.get('url')}" for p in posts]
    read = next((p for p in posts if TUNING.search(p.get("title") or "")), posts[0])
    try:
        text = await news_post(read["url"])
    except (KnowledgeError, KeyError):
        text = ""
    if text:
        lines.append(f"The post \"{read.get('title')}\" says: {cut(text, NEWS_POST_CHARS)}"
                     + (" (cut here: web_lookup with its url reads it all)" if len(text) > NEWS_POST_CHARS else ""))
    return "\n".join(lines)
