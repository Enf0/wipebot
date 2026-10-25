"""The views without Claude or ChatGPT: a mock MCP Apps host (app/host.html) in Chromium, which runs a tool here,
hands its result to the views' page (report/views.py) and answers the page's calls (view_data, pull_replay,
viewer_log) with the tools in-process. Everything is served from memory: the browser reaches no network.

`watch` opens it in a window to work on a view (`wipebot view`), reloading the page when app/ changes; `shoot` takes
screenshots per theme, width and mode, with the console's errors and every bridge message (`wipebot view --shot`, the
tests). Needs Playwright and its Chromium (`uv run python -m playwright install chromium-headless-shell`).
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

from . import knowledge, server
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
log = logging.getLogger(__name__)


@dataclass
class Shot:
    """One screenshot of a view, and what the page said and did on the way."""
    path: Path
    theme: str
    width: int
    mode: str
    height: int  # the height the page last reported (ui/notifications/size-changed); 0 if none
    text: str = ""  # what the view shows as text
    errors: list[str] = field(default_factory=list)  # console errors, failed script loads, CSP refusals
    messages: list[dict] = field(default_factory=list)  # every bridge message, both ways


async def tool_result(tool: str, args: dict) -> dict:
    """The tool's result as a host hands it to the page: run serving many users, as remote.py runs it, so the
    result carries the view's key."""
    if server.DEFAULT.remote is None:
        server.DEFAULT.remote = server.Remote(guilds=list, user_name=str)
    result = await server.mcp.call_tool(tool, args)
    if not isinstance(result, CallToolResult):
        raise ToolError(f"{tool} asked for input")
    return result.model_dump(by_alias=True, exclude_none=True, mode="json")


async def answer(name: str, args: dict) -> dict:
    """The page's call to one of the views' tools, as remote.py's app-only tools answer it."""
    try:
        if name == "view_data":
            result = await server.guarded(server.view_data)(**args)
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
    """The host page, the views' page (as fresh as app/ is) and the replay's images; nothing else."""
    url: str = route.request.url
    path = url.removeprefix(ORIGIN).split("?")[0]
    if path == "/host.html":
        body = resources.files("wipebot.core").joinpath("app/host.html").read_text(encoding="utf-8")
        await route.fulfill(status=200, content_type="text/html", body=body)
    elif path == "/view.html":
        views.page.cache_clear()
        await route.fulfill(status=200, content_type="text/html", body=views.page(),
                            headers={"Content-Security-Policy": CSP})
    elif path in ("/fonts/Inter-Medium.ttf", "/fonts/Inter-Bold.ttf"):
        body = resources.files("wipebot.core").joinpath(path.removeprefix("/")).read_bytes()
        await route.fulfill(status=200, content_type="font/ttf", body=body)
    elif path.startswith("/img/"):
        try:
            data, media_type = await maps.asset(path.removeprefix("/img/"))
            await route.fulfill(status=200, content_type=media_type, body=data)
        except (knowledge.KnowledgeError, OSError):
            await route.fulfill(status=404)
    else:
        await route.abort()


async def _open(browser: Any, setup: dict, errors: list[str]):
    """A page with the mock host, set up as `setup` says (host.html's window.__setup)."""
    page = await browser.new_page(viewport={"width": max(setup["width"], FULL_WIDTH) + 40, "height": 1000})
    await _wire(page, setup, errors)
    return page


async def _wire(page: Any, setup: dict, errors: list[str]) -> None:
    """Load the mock host into `page`: everything it asks served from memory, the view's calls answered here, and
    what goes wrong in `errors`."""
    await page.route("**/*", _serve)
    await page.expose_function("wipebotCall", answer)
    await page.add_init_script(f"window.__setup = {json.dumps(setup)};")
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
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
                clicks: tuple[str, ...] = (), tokens: str = "none") -> list[Shot]:
    """Screenshots of the tool's view in each theme, width and mode, after clicking each of `clicks` (a button's
    text) in turn: <out>/<theme>-<width>-<mode>[-<n>].png."""
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    result = await tool_result(tool, args)
    shots = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for mode in modes:
                for theme in themes:
                    for width in widths if mode == "inline" else (FULL_WIDTH,):
                        errors: list[str] = []
                        setup = {"tool": tool, "args": args, "result": result, "theme": theme, "width": width,
                                 "mode": mode, "tokens": tokens, "bare": True, "fullWidth": FULL_WIDTH,
                                 "fullHeight": FULL_HEIGHT}
                        page = await _open(browser, setup, errors)
                        for n in range(len(clicks) + 1):
                            if n:
                                await page.frame_locator("iframe").get_by_role("button", name=clicks[n - 1]).click()
                                await page.wait_for_timeout(300)
                            try:
                                await _settled(page)
                            except Exception as e:  # noqa: BLE001 (what didn't draw is in the shot and the errors)
                                errors.append(f"not drawn: {e}")
                            name = f"{theme}-{width}-{mode}" + (f"-{n}" if n else "")
                            frame = page.locator("iframe")
                            await frame.screenshot(path=out / f"{name}.png")
                            messages = await page.evaluate("window.__log")
                            heights = [m["msg"]["params"]["height"] for m in messages
                                       if m["msg"].get("method") == "ui/notifications/size-changed"]
                            text = await page.frame_locator("iframe").locator("body").inner_text()
                            shots.append(Shot(out / f"{name}.png", theme, width, mode, round(heights[-1]) if heights
                                              else 0, text, list(errors), messages))
                        await page.close()
        finally:
            await browser.close()
    return shots


async def watch(tool: str, args: dict, theme: str = "dark", width: int = 430, mode: str = "inline") -> None:
    """The tool's view in a window, to work on: the toolbar switches theme, width, mode and tokens; the view reloads
    when a file in app/ changes. Returns when the window is closed."""
    from playwright.async_api import async_playwright

    result = await tool_result(tool, args)
    app = Path(str(resources.files("wipebot.core").joinpath("app")))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        errors: list[str] = []
        setup = {"tool": tool, "args": args, "result": result, "theme": theme, "width": width, "mode": mode,
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


PICTURE_WIDTH = 680  # a view's picture, in CSS px: about what Discord shows an image at, its text readable there
PICTURE_SCALE = 2  # drawn at twice that, for a sharp picture on any screen


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

    async def picture(self, tool: str, args: dict) -> bytes | None:
        """The view of the tool's answer to `args` (as the tool was called: report, pull, boss), or None for a tool
        without one. Raises what went wrong: a view that didn't draw, or drew with errors."""
        key = await server.view_key(tool, args)
        if key is None:
            return None
        browser = await self._ready()
        errors: list[str] = []
        setup = {"tool": tool, "args": args, "result": {"content": [], "structuredContent": key}, "theme": "dark",
                 "width": PICTURE_WIDTH, "mode": "inline", "tokens": "none", "bare": True, "picture": True,
                 "fonts": PICTURE_FONTS, "fontFamily": "Inter, system-ui, sans-serif",
                 "fullWidth": FULL_WIDTH, "fullHeight": FULL_HEIGHT}
        context = await browser.new_context(device_scale_factor=PICTURE_SCALE,
                                            viewport={"width": PICTURE_WIDTH + 40, "height": 1000})
        try:
            page = await context.new_page()
            await _wire(page, setup, errors)
            await _settled(page)
            await page.frames[-1].evaluate("document.fonts.ready.then(() => true)")
            if errors:
                raise RuntimeError("; ".join(errors))
            png = await page.locator("iframe").screenshot(type="png")
        finally:
            await context.close()
        return await asyncio.to_thread(_webp, png)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


def _webp(png: bytes) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "WEBP", quality=88)
    return out.getvalue()
