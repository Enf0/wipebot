"""Reading the guide sites' pages (Method, Wowhead, Icy Veins) as warcraft_cli returns them: their sections, their
other pages, Wowhead's markup as text, when a page was last updated, and where a spec's guide is."""
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .. import clock
from ..text import plural
from . import fetch
from .fetch import KnowledgeError

GUIDE_SOURCES = ("method", "wowhead", "icy-veins")
SOURCE_NAMES = {"wowhead": "Wowhead", "method": "Method", "icy-veins": "Icy Veins"}
# Sections about other content than raiding, left out unless asked for.
OFF_TOPIC = re.compile(r"leveling|war mode|mythic\+|\bm\+|easy mode|delve|pvp|changelog", re.I)


@dataclass
class Guide:
    """A guide page as read, with the sections an answer wants."""
    source: str  # a key of SOURCE_NAMES
    title: str
    url: str
    last_updated: str | None
    sections: list[tuple[str, str]]  # (title, text)
    pages: list[tuple[str, str]] = field(default_factory=list)  # the guide's other pages: (title, url)

    @property
    def site(self) -> str:
        return SOURCE_NAMES[self.source]


def sections(data: dict) -> list[dict]:
    """A guide's text sections. Method's and Icy Veins' guide-full hold every page of the guide (a section's title
    is then prefixed with its page's); Wowhead's one page."""
    if isinstance(data.get("pages"), list):
        rows = []
        for page in data["pages"]:
            page_title = (page.get("guide") or {}).get("section_title") or ""
            for s in (page.get("article") or {}).get("sections") or []:
                title = s.get("title") or ""
                rows.append({"title": title if page_title.lower() in title.lower() else f"{page_title}: {title}",
                             "text": s.get("text") or ""})
        return [r for r in rows if r["text"].strip()]
    body = data.get("body") or {}
    for key in ("section_chunks", "sections"):
        rows = body.get(key) or data.get(key) or []
        rows = [r for r in rows if (r.get("content_text") or r.get("text") or "").strip()]
        if rows:
            return rows
    return []


def subpages(source: str, data: dict) -> list[tuple[str, str]]:
    """(title, url) of a guide's other pages, from its navigation."""
    nav = data.get("navigation") or {}
    if isinstance(nav.get("items"), list):  # Method
        return [(i["title"], i["url"]) for i in nav["items"] if not i.get("active")]
    raw = nav.get("raw_markup") or ""  # Wowhead
    return [(t, "https://www.wowhead.com/" + u) for u, t in re.findall(r"\[url=(guide/[^\]]+)\]([^\[]+)\[/url\]", raw)]


def wowhead_text(raw: str, names: dict[int, str] | None = None) -> str:
    """Wowhead guide markup (bbcode with [html] islands) as plain text: list items as lines, tabs as headings,
    [spell=id] and the journal's linked [Names] as names."""
    names = names or {}
    raw = re.sub(r"\[spell=(\d+)[^\]]*\]", lambda m: names.get(int(m[1]), f"spell {m[1]}"), raw)

    def html_block(m: re.Match) -> str:
        h = re.sub(r"<!--.*?-->", "", m[1], flags=re.S)
        h = re.sub(r"<(?:p|h\d|ul|ol|tr)[^>]*>", "\n", h)
        h = re.sub(r"<li[^>]*>", "\n- ", h)
        h = re.sub(r"<br\s*/?>", "\n", h)
        h = re.sub(r"<[^>]+>", "", h)
        return re.sub(r"\[([^\[\]\n]+)\]", r"\1", html.unescape(h))

    text = re.sub(r"\[html\](.*?)\[/html\]", html_block, raw, flags=re.S)
    text = re.sub(r"\[tab name=\"?([^\]\"]+)\"?\]", r"\n### \1\n", text)
    text = re.sub(r"\[li\]", "\n- ", text).replace("[/li]", "")
    text = re.sub(r"\[/?(?:ul|ol|tabs|tab|grid|div|table|tr|td|h\d|pad|br)\b[^\]]*\]", "\n", text)
    text = re.sub(r"\[/?[a-z]+(?:[= ][^\]]*)?\]", "", text)  # [b], [color=q5], [icon ...], ...
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def cut(text: str, chars: int) -> str:
    """The text cut to `chars` at a line break, with ' ...' when cut."""
    if len(text) <= chars:
        return text
    head = text[:chars]
    return head[:head.rfind("\n") if "\n" in head[chars // 2:] else chars].rstrip() + " ..."


def updated_on(when: str | None) -> datetime | None:
    """A guide's last update as the site gives it ('2026-10-03', '2026-08-26T15:49:22-05:00'), None when unreadable."""
    try:
        day = datetime.fromisoformat((when or "").replace("Z", "+00:00")[:25])
    except ValueError:
        return None
    return day if day.tzinfo else day.replace(tzinfo=UTC)


def updated(when: str | None) -> str:
    """'last updated 2026-08-13 (58 days ago)', so a stale guide is plain to see."""
    if not when:
        return "last update unknown"
    day = updated_on(when)
    if day is None:
        return f"last updated {when}"
    days = (datetime.fromtimestamp(clock.now(), UTC) - day).days  # below 0 for a later day in another time zone
    return f"last updated {day:%Y-%m-%d} ({plural(days, 'day') + ' ago' if days > 0 else 'today'})"


async def spec_guide_ref(spec: str, source: str) -> str:
    """The guide-full reference of a spec's main guide on one site."""
    if source == "icy-veins":
        # Icy Veins' resolve can pick a guide from an old expansion; its search ranks the current spec guide.
        found = await fetch.cli(source, "search", "--", f"{spec} guide")
        ref = next((r["id"] for r in found.get("results") or []
                    if (r.get("metadata") or {}).get("content_family") == "spec_guide"), None)
        if not ref:
            raise KnowledgeError(f"no Icy Veins guide found for {spec!r}")
        return ref
    found = await fetch.cli(source, "resolve", "--", f"{spec} guide" if source == "wowhead" else spec)
    cmd = found.get("next_command") or ""
    if not found.get("resolved") or not cmd:
        raise KnowledgeError(f"no {source} guide found for {spec!r}")
    return cmd.split()[-1]


async def wowhead_guide_updated(spec: str, url: str, guide_id: int | None) -> str | None:
    """When a Wowhead guide page was last updated: its page has no date, the class guide listing has one per
    guide, matched by URL or id."""
    try:
        listing = await fetch.cli("wowhead", "guides", "classes", "--sort", "updated", "--limit", "100", "--", spec)
    except KnowledgeError:
        return None
    path = url.rstrip("/").split("wowhead.com/")[-1]
    for r in listing.get("results") or []:
        if (r.get("url") or "").rstrip("/").endswith(path) or (guide_id and r.get("id") == guide_id):
            return r.get("last_updated")
    return None
