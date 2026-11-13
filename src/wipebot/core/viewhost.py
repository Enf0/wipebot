"""The views without Claude or ChatGPT: a mock MCP Apps host (app/host.html) in Chromium, which runs a tool here,
hands its result to the views' page (report/views.py) and answers the page's calls (view_data, pull_replay,
viewer_log) with the tools in-process. Everything is served from memory: the browser reaches no network.

`watch` opens it in a window to work on a view (`wipebot view`), reloading the page when app/ changes; `shoot` takes
screenshots per theme, width and mode, with the console's errors and every bridge message (`wipebot view --shot`, the
tests). Both draw what the server sends: a card for the tools whose result carries a view's key in Claude and
ChatGPT (SHOWN, and pull_map's replay), nothing for a call of one of those without a view (the page takes no room),
and for a tool whose drawing only the Discord bot posts, that picture as the Painter draws it, its shots named
`discord-`. Needs Playwright and its Chromium (`uv run python -m playwright install chromium-headless-shell`).
"""
import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from . import server
from .report import maps, views

ORIGIN = "https://wipebot.local"  # never fetched: every request to it is answered here
# As a host without the page's CSP would, plus the page's resourceDomains (its images, here our own origin)
CSP = (f"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src {ORIGIN} data:; "
       f"font-src {ORIGIN}; media-src 'none'; connect-src 'none'; object-src 'none'")
# The pictures' font, as a host gives its own (hostContext.styles.css.fonts): Inter, which the maps are drawn in
# too (report/draw.py), so a picture looks the same wherever it's drawn
PICTURE_FONTS = "".join(f"@font-face {{ font-family: Inter; font-weight: {weight}; "
                        f"src: url({ORIGIN}/fonts/{name}) format('truetype'); }}"
                        for weight, name in (("100 550", "Inter-Medium.ttf"), ("551 900", "Inter-Bold.ttf")))
FULL_WIDTH, FULL_HEIGHT = 1100, 760  # fullscreen, as a desktop host gives it
PICTURE_WIDTH = 680  # a view's picture, in CSS px: about what Discord shows an image at, its text readable there
PICTURE_SCALE = 2  # drawn at twice that, for a sharp picture on any screen
# The tools whose result carries their view's key for a client that shows MCP Apps (server._shown; pull_map gives its
# replay's key itself). A test holds this to the server's tools and to the list remote.py gives the views' page to.
# Every other tool with a drawing (server.VIEW_TOOLS) is drawn only as a picture, for the Discord bot.
SHOWN = frozenset({"pull_summary", "player_pull", "player_night", "death_recap", "progress", "breakdown",
                   "mechanic_hits"})
log = logging.getLogger(__name__)


@dataclass
class Shot:
    """One screenshot of a view, and what the page said and did on the way."""
    path: Path  # no file where the page drew nothing and took no room (a call without a view: height 0)
    theme: str
    width: int
    mode: str
    height: int  # the height the page last reported (ui/notifications/size-changed); 0 if none
    text: str = ""  # what the view shows as text
    errors: list[str] = field(default_factory=list)  # console errors, failed script loads, CSP refusals
    messages: list[dict] = field(default_factory=list)  # every bridge message, both ways


async def tool_result(tool: str, args: dict) -> dict:
    """The tool's result as a host hands it to the page: run serving many users, as remote.py runs it, with the
    view's key a client that shows apps gets, for the tools that give one there (SHOWN) and no other. Only for this
    call: the in-process tools are left as they were. A card that always comes folded (server.ALWAYS_FOLDED) comes
    open here, where it is what one looks at."""
    was = server.DEFAULT.remote
    server.DEFAULT.remote = was or server.Remote(guilds=list, user_name=str)
    try:
        result = await server.mcp.call_tool(tool, args)
    finally:
        server.DEFAULT.remote = was
    if not isinstance(result, CallToolResult):
        raise ToolError(f"{tool} asked for input")
    if result.structured_content is None and tool in SHOWN:  # in-process no client says it shows apps: as served
        result.structured_content = await server.view_key(tool, args)
    key = result.structured_content
    if key and key.get("view") in server.ALWAYS_FOLDED:  # the card itself: the line that opens it is the page's test
        result.structured_content = {k: v for k, v in key.items() if k not in ("folded", "label")}
    return result.model_dump(by_alias=True, exclude_none=True, mode="json")


async def picture_key(tool: str, args: dict) -> dict | None:
    """The key of a drawing only the Discord bot posts (the adds' meter, a note's board, two raid mates, the
    healers' mana, a player against the top): None for a tool whose view Claude and ChatGPT show, or with nothing to
    draw."""
    return None if tool in SHOWN or tool == "pull_map" else await server.view_key(tool, args)


def _picture_setup(tool: str, args: dict, key: dict) -> dict:
    """What the mock host is told for a view as the Discord bot's picture: inline, dark, PICTURE_WIDTH wide, in the
    pictures' font, to a page that knows it is a picture (no buttons)."""
    return {"tool": tool, "args": args, "result": {"content": [], "structuredContent": key}, "theme": "dark",
            "width": PICTURE_WIDTH, "mode": "inline", "tokens": "none", "bare": True, "picture": True,
            "fonts": PICTURE_FONTS, "fontFamily": "Inter, system-ui, sans-serif",
            "fullWidth": FULL_WIDTH, "fullHeight": FULL_HEIGHT}


async def answer(name: str, args: dict) -> dict:
    """The page's call to one of the views' tools, as remote.py's app-only tools answer it."""
    try:
        if name == "view_data":
            result = await server.guarded(server.view_data)(**args)
            if result.structured_content is not None:
                result.structured_content["img_base"] = ORIGIN + "/img/"  # its spells' icons (views.icon)
        elif name == "pull_replay":
            data = await server.guarded(server.replay_data)(**args)
            data["img_base"] = ORIGIN + "/img/"
            result = CallToolResult(content=[], structured_content=data)
        elif name == "viewer_log":
            log.info("view: %s", args.get("note"))
            return {"content": [{"type": "text", "text": "ok"}]}
        else:
            raise ToolError(f"no tool {name}")
    except (ToolError, TypeError) as e:
        return {"isError": True, "content": [{"type": "text", "text": str(e)}]}
    return result.model_dump(by_alias=True, exclude_none=True, mode="json")


async def _serve(route) -> None:
    """The host page, the views' page (as fresh as app/ is: the one Claude and ChatGPT load, or for a picture the
    one with the drawings only the Discord bot posts) and the replay's images; nothing else."""
    url: str = route.request.url
    path = url.removeprefix(ORIGIN).split("?")[0]
    if path == "/host.html":
        body = resources.files("wipebot.core").joinpath("app/host.html").read_text(encoding="utf-8")
        await route.fulfill(status=200, content_type="text/html", body=body)
    elif path in ("/view.html", "/picture.html"):
        views.page.cache_clear()
        await route.fulfill(status=200, content_type="text/html", body=views.page(pictures=path == "/picture.html"),
                            headers={"Content-Security-Policy": CSP})
    elif path in ("/fonts/Inter-Medium.ttf", "/fonts/Inter-Bold.ttf"):
        body = resources.files("wipebot.core").joinpath(path.removeprefix("/")).read_bytes()
        await route.fulfill(status=200, content_type="font/ttf", body=body)
    elif path.startswith("/img/"):
        try:
            data, media_type = await maps.asset(path.removeprefix("/img/"))
        except Exception as e:  # noqa: BLE001 (an image that can't be had is left out, as the server's /img/ does)
            log.info("no image %s: %s", path, e)
            await route.fulfill(status=404)
            return
        await route.fulfill(status=200, content_type=media_type, body=data)
    else:
        await route.abort()


async def _open(browser: Any, setup: dict, errors: list[str]):
    """A page with the mock host, set up as `setup` says (host.html's window.__setup); a picture at its scale."""
    page = await browser.new_page(viewport={"width": max(setup["width"], FULL_WIDTH) + 40, "height": 1000},
                                  device_scale_factor=PICTURE_SCALE if setup.get("picture") else 1)
    await _wire(page, setup, errors)
    return page


async def _wire(page: Any, setup: dict, errors: list[str]) -> None:
    """Load the mock host into `page`: everything it asks served from memory, the view's calls answered here, and
    what goes wrong in `errors`."""
    await page.route("**/*", _serve)
    await page.expose_function("wipebotCall", answer)
    await page.add_init_script(f"window.__setup = {json.dumps(setup)};")
    # An image that couldn't be had (an icon Wowhead hasn't) is the browser's "Failed to load resource": not the view's
    page.on("console", lambda m: errors.append(m.text) if m.type == "error"
            and not m.text.startswith("Failed to load resource") else None)
    page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    loading: set = set()  # requests in flight, for _settled (Playwright's networkidle fires once per page)
    page.on("request", lambda r: loading.add(r))
    page.on("requestfinished", lambda r: loading.discard(r))
    page.on("requestfailed", lambda r: loading.discard(r))
    page.loading = loading
    await page.goto(ORIGIN + "/host.html")


QUIET_MS = 300  # no request in flight for this long: the view's images are in


async def _settled(page: Any, timeout_ms: float = 60_000) -> None:
    """Until the view has drawn (its loading placeholder gone, or showing why it failed) and its images are in."""
    view = page.frame_locator("iframe")
    await view.locator("#status:not(.failed)").wait_for(state="detached", timeout=timeout_ms)
    quiet, waited = 0, 0
    while quiet < QUIET_MS and waited < timeout_ms:
        await page.wait_for_timeout(50)
        waited += 50
        quiet = 0 if page.loading else quiet + 50
    await page.wait_for_timeout(100)  # a last resize and redraw


async def shoot(tool: str, args: dict, out: Path, themes: tuple[str, ...] = ("dark", "light"),
                widths: tuple[int, ...] = (430, 760), modes: tuple[str, ...] = ("inline",),
                clicks: tuple[str, ...] = (), tokens: str = "none", as_picture: bool = False,
                mark: str | None = None) -> list[Shot]:
    """Screenshots of the tool's view in each theme, width and mode, after clicking each of `clicks` (a button's
    text, or "css:" and a selector) in turn: <out>/<theme>-<width>-<mode>[-<n>].png. A drawing only the Discord bot
    posts is shot once, as its picture: <out>/discord-dark-<PICTURE_WIDTH>-inline.png; with `as_picture`, so is a
    view Claude and ChatGPT show too (a meter, a trend), `mark`'s row ringed (the player asking)."""
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    result = await tool_result(tool, args)
    picture = (await server.view_key(tool, args) if as_picture
               else None if result.get("structuredContent") else await picture_key(tool, args))
    if picture and mark:
        picture["mark"] = mark
    if picture:
        themes, widths, modes, clicks = ("dark",), (PICTURE_WIDTH,), ("inline",), ()
    shots = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for mode in modes:
                for theme in themes:
                    for width in widths if mode == "inline" else (FULL_WIDTH,):
                        errors: list[str] = []
                        setup = _picture_setup(tool, args, picture) if picture else {
                            "tool": tool, "args": args, "result": result, "theme": theme, "width": width,
                            "mode": mode, "tokens": tokens, "bare": True, "fullWidth": FULL_WIDTH,
                            "fullHeight": FULL_HEIGHT}
                        page = await _open(browser, setup, errors)
                        for n in range(len(clicks) + 1):
                            if n:
                                await _click(page, clicks[n - 1])
                                await page.wait_for_timeout(300)
                            try:
                                await _settled(page)
                                errors += await _markup_errors(page)
                                if picture:
                                    await page.frames[-1].evaluate("document.fonts.ready.then(() => true)")
                            except Exception as e:  # noqa: BLE001 (what didn't draw is in the shot and the errors)
                                errors.append(f"not drawn: {e}")
                            name = ("discord-" if picture else "") + f"{theme}-{width}-{mode}" + (f"-{n}" if n else "")
                            messages = await page.evaluate("window.__log")
                            heights = [m["msg"]["params"]["height"] for m in messages
                                       if m["msg"].get("method") == "ui/notifications/size-changed"]
                            if not heights or heights[-1] or mode != "inline":  # a page with nothing to draw takes
                                await page.locator("iframe").screenshot(path=out / f"{name}.png")  # no room: no shot
                            text = await page.frame_locator("iframe").locator("body").inner_text()
                            shots.append(Shot(out / f"{name}.png", theme, width, mode, round(heights[-1]) if heights
                                              else 0, text, list(errors), messages))
                        await page.close()
        finally:
            await browser.close()
    return shots


# Draws the view again with every chart's markup read as XML, which refuses what the browser's HTML parser lets
# through in silence: an attribute given twice (it keeps the first, so a second font-size or opacity does nothing).
STRICT = """() => {
  const bad = [], svg = kit.svg;
  kit.svg = (...a) => {
    const s = svg(...a);
    const xml = s.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
    const doc = new DOMParser().parseFromString(xml, "image/svg+xml");
    const e = doc.querySelector("parsererror");
    if (e) bad.push("markup: " + e.textContent.replace(/\\s+/g, " ").slice(0, 300));
    return s;
  };
  try { for (const v of Object.values(views)) if (v.relayout) v.relayout(); } finally { kit.svg = svg; }
  return bad;
}"""


async def _markup_errors(page: Any) -> list[str]:
    """What's wrong in the markup of the charts the view draws (STRICT); none for a view that hasn't drawn."""
    return await page.frames[-1].evaluate(STRICT)


async def _click(page: Any, what: str) -> None:
    """Click in the view: a button by its text, or with "css:" anything by a CSS selector (a pull's row)."""
    view = page.frame_locator("iframe")
    target = (view.locator(what.removeprefix("css:")) if what.startswith("css:")
              else view.get_by_role("button", name=what))
    await target.first.click()


async def watch(tool: str, args: dict, theme: str = "dark", width: int = 430, mode: str = "inline") -> None:
    """The tool's view in a window, to work on: the toolbar switches theme, width, mode and tokens; the view reloads
    when a file in app/ changes. A drawing only the Discord bot posts opens as its picture. Returns when the window
    is closed."""
    from playwright.async_api import async_playwright

    result = await tool_result(tool, args)
    picture = None if result.get("structuredContent") else await picture_key(tool, args)
    app = Path(str(resources.files("wipebot.core").joinpath("app")))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        errors: list[str] = []
        setup = _picture_setup(tool, args, picture) | {"bare": False} if picture else {
            "tool": tool, "args": args, "result": result, "theme": theme, "width": width, "mode": mode,
            "tokens": "none", "bare": False, "fullWidth": FULL_WIDTH, "fullHeight": FULL_HEIGHT}
        page = await _open(browser, setup, errors)
        page.on("console", lambda m: print(f"[{m.type}] {m.text}") if m.type != "log" else None)
        stamp = _stamp(app)
        closed = asyncio.Event()
        page.on("close", lambda _: closed.set())
        while not closed.is_set():
            try:
                await asyncio.wait_for(closed.wait(), 0.5)
            except TimeoutError:
                if (now := _stamp(app)) != stamp:
                    stamp = now
                    print("app/ changed: reloading the view", flush=True)
                    await page.evaluate("load()")
        await browser.close()


def _stamp(folder: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in folder.iterdir()))


# Leaves one of a view's panels on the page, the others out of the layout: its picture is then the page with its
# margin, as wide as a whole view's (a panel shot by itself was 36 px narrower and had no margin)
ALONE = """(i) => document.querySelectorAll("[data-panel]").forEach((p, j) => {
  p.style.display = j === i ? "" : "none";
  p.style.marginTop = "0";
})"""


class Painter:
    """Still pictures of views, for an app without MCP Apps (the Discord bot): the card as Claude and ChatGPT show
    it inline, dark, without its buttons, as WebP. One headless browser is kept open between pictures (a launch
    takes a second or more) and opened again if it went away."""

    def __init__(self):
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def _ready(self) -> Any:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright
                if self._pw is None:
                    self._pw = await async_playwright().start()
                # /dev/shm is small in a container; Playwright already runs Chromium without its sandbox
                self._browser = await self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
            return self._browser

    async def pictures(self, tool: str, args: dict, rows: int | None = None,
                       mark: str | None = None) -> list[bytes]:
        """The view of the tool's answer to `args` (as the tool was called: report, pull, boss): one picture, or one
        per panel for a view drawn in panels (its [data-panel] parts, the player's night), each a Panel named as its
        part is (data-panel="callouts"); none for a tool without a view. A meter draws `rows` players' bars, when
        given, and rings `mark`'s (the player asking). Raises what went wrong: a view that didn't draw, or drew with
        errors."""
        key = await server.view_key(tool, args)
        if key is None:
            return []
        if rows:
            key["rows"] = rows
        if mark:
            key["mark"] = mark
        browser = await self._ready()
        errors: list[str] = []
        setup = _picture_setup(tool, args, key)
        context = await browser.new_context(device_scale_factor=PICTURE_SCALE,
                                            viewport={"width": PICTURE_WIDTH + 40, "height": 1000})
        try:
            page = await context.new_page()
            await _wire(page, setup, errors)
            await _settled(page)
            # a view that couldn't load says why in place of its card (main.js's fail): no picture of that
            failed = page.frames[-1].locator("#status.failed #status-msg")
            if await failed.count():
                raise RuntimeError(f"not drawn: {await failed.first.inner_text()}")
            await page.frames[-1].evaluate("document.fonts.ready.then(() => true)")
            if errors:
                raise RuntimeError("; ".join(errors))
            view = page.frames[-1]
            panels = view.locator("[data-panel]")
            shots = []
            for i in range(await panels.count()):  # each alone on the page, inside the margin a whole view has
                await view.evaluate(ALONE, i)
                shots.append((await panels.nth(i).get_attribute("data-panel") or "",
                              await view.locator("#app").screenshot(type="png")))
            shots = shots or [("", await page.locator("iframe").screenshot(type="png"))]
        finally:
            await context.close()
        return [Panel(await asyncio.to_thread(_webp, png), name) for name, png in shots]

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


class Panel(bytes):
    """A view's picture, with the name of the panel it is (its data-panel), "" for a view drawn whole."""
    name: str

    def __new__(cls, data: bytes, name: str = "") -> Panel:
        self = super().__new__(cls, data)
        self.name = name
        return self


def _webp(png: bytes) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "WEBP", quality=88)
    return out.getvalue()
