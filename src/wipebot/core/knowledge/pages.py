"""Any page of the sites warcraft_cli reads, whole and in parts, and a search across them: for what the other tools
don't show (the rest of a news post, a guide's other pages, a wiki article, a site no tool covers)."""
import html
import re
from urllib.parse import unquote, urlparse

from . import fetch
from .fetch import KnowledgeError
from .guides import sections, wowhead_text

PART_CHARS = 6000  # a page is read in parts this long, cut at a line break
SEARCH_LIMIT = 8
SITES = "Wowhead (news, blue posts, guides), Method, Icy Veins and Warcraft Wiki"


def _command(url: str) -> tuple[str, ...]:
    """The warcraft_cli command that reads a page, by its address."""
    u = urlparse(url if "://" in url else "https://" + url)
    host, path = u.netloc.lower().removeprefix("www."), u.path
    def on(site: str) -> bool:
        return host == site or host.endswith("." + site)
    if on("wowhead.com"):
        if "/news/" in path:
            return ("wowhead", "news-post", "--", url)
        if "/blue-tracker/topic/" in path:
            return ("wowhead", "blue-topic", "--", url)
        if "/guide" in path:
            return ("wowhead", "guide-full", "--", url)
    elif on("method.gg") and path.startswith("/guides/"):
        return ("method", "guide-full", "--", url)
    elif on("icy-veins.com"):
        return ("icy-veins", "guide", "--", url)  # guide-full is every boss of the raid
    elif on("warcraft.wiki.gg") and path.startswith("/wiki/"):
        return ("warcraft-wiki", "article-full", "--", unquote(path.removeprefix("/wiki/")).replace("_", " "))
    raise KnowledgeError(f"can't read {url}: pages of {SITES} only (a spell's tooltip: spell_info)")


def _html_text(h: str) -> str:
    h = re.sub(r"<br\s*/?>|</p>|</li>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h)).strip()


def _strings(data: object, keys: tuple[str, ...] = ("text", "content_text")) -> list[str]:
    """Every text field in a JSON answer, in order: for a page whose shape the readers below don't know."""
    if isinstance(data, dict):
        out = [v for k, v in data.items() if k in keys and isinstance(v, str) and v.strip()]
        return out + [s for k, v in data.items() if k not in keys for s in _strings(v, keys)]
    if isinstance(data, list):
        return [s for v in data for s in _strings(v, keys)]
    return []


def _page(cmd: tuple[str, ...], data: dict) -> tuple[str, str]:
    """(title, text) of a page as its reader returned it."""
    title = next((t for k in ("page", "guide", "article", "topic") if isinstance(data.get(k), dict)
                  and (t := data[k].get("title"))), "")
    if cmd[:2] == ("wowhead", "news-post"):
        return title, ((data.get("content") or {}).get("text") or "").strip()
    if cmd[:2] == ("wowhead", "blue-topic"):
        posts = (data.get("posts") or {}).get("items") or []
        return title, "\n\n".join(f"{p.get('author') or 'Someone'}{' (Blizzard)' if p.get('blue') else ''}, "
                                  f"{p.get('posted') or p.get('date') or ''}:\n{_html_text(p.get('body_html') or '')}"
                                  for p in posts)
    if cmd[:2] == ("wowhead", "guide-full"):
        chunks = (data.get("body") or {}).get("section_chunks") or []
        names = {e["id"]: e["name"] for e in (data.get("linked_entities") or {}).get("items") or []
                 if e.get("entity_type") == "spell" and e.get("name")}
        return title, "\n\n".join(f"## {c.get('title') or ''}\n{wowhead_text(c.get('content_raw') or '', names)}"
                                  for c in chunks if c.get("content_raw"))
    rows = sections(data)
    if rows:
        return title, "\n\n".join(f"## {r.get('title') or ''}\n{(r.get('text') or r.get('content_text') or '').strip()}"
                                  for r in rows)
    return title, "\n\n".join(dict.fromkeys(s.strip() for s in _strings(data)))


def _parts(text: str) -> list[str]:
    """The text in parts of about PART_CHARS, each ending at a line break, else at a sentence's end."""
    parts = []
    while len(text) > PART_CHARS:
        cut = text.rfind("\n", PART_CHARS // 2, PART_CHARS)
        if cut < 0:  # one long paragraph (Icy Veins): at a sentence's end
            cut = text.rfind(". ", PART_CHARS // 2, PART_CHARS) + 1 or PART_CHARS
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts + [text] if text else parts


async def read_text(url: str, part: int = 1) -> str:
    """A page's text, part `part` of it, with how many parts there are."""
    cmd = _command(url)
    title, text = _page(cmd, await fetch.cli(*cmd))
    parts = _parts(re.sub(r"\n\s*\n(\s*\n)+", "\n\n", text).strip())
    if not parts:
        raise KnowledgeError(f"{url} has no text warcraft_cli could read")
    n = len(parts)
    part = min(max(part, 1), n)
    head = f"{title or url} ({url}), part {part} of {n}" if n > 1 else f"{title or url} ({url})"
    tail = f"\n\n(Part {part} of {n}: the next is part {part + 1}.)" if part < n else ""
    return f"{head}\n\n{parts[part - 1]}{tail}"


async def search_text(query: str) -> str:
    """The pages on `query` that read_text can read, best first, from warcraft_cli's search across the sites."""
    data = await fetch.cli("warcraft", "search", "--brief", "--limit", str(SEARCH_LIMIT * 2), "--", query)
    rows = [r for r in data.get("results") or [] if r.get("name") and readable(r.get("url") or "")][:SEARCH_LIMIT]
    if not rows:
        return f"No page on {SITES} found for {query!r}."
    lines = [f"Pages for {query!r}, best first (site, kind, title, address to read):"]
    lines += [f"- {r.get('provider')}, {r.get('kind')}: {r['name']} {r['url']}" for r in rows]
    return "\n".join(lines)


def readable(url: str) -> bool:
    """Whether read_text can read the page."""
    try:
        return bool(url) and bool(_command(url))
    except KnowledgeError:
        return False
