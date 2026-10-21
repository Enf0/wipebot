"""The connector as a Claude plugin: the remote server's connector plus the core's raid-log-analysis skill, so Claude
gets the method too (a connector alone brings only the tools). The server serves it at /plugin.zip, to upload in
Claude (Customize > Plugins), and `wipebot plugin DIR` writes it as a marketplace for a repository, which Claude
keeps up to date; it names this server, so each host's plugin is its own."""
import hashlib
import io
import json
import re
import zipfile
from importlib import resources

PATH = "/plugin.zip"


def plugin_files(name: str, guild: str, mcp_url: str) -> dict[str, bytes]:
    """`name`'s plugin for `guild` at `mcp_url`, by path: claude_plugin/'s manifest and README filled in, and the
    skill as this server has it (no icon: claude.ai drops a manifest's `icon` as an unknown key). Its version is a
    hash of what's in it, so a changed skill shows as a new version (which is how Claude's marketplaces tell an
    update)."""
    skill = resources.files("wipebot.core").joinpath("skills/raid-log-analysis")
    files = {f"skills/raid-log-analysis/{f.name}": f.read_bytes() for f in sorted(skill.iterdir(), key=lambda f: f.name)
             if f.is_file() and f.name.endswith(".md")}
    manifest = _filled("plugin.json", name, guild, mcp_url)
    files["README.md"] = _filled("README.md", name, guild, mcp_url).encode()
    version = hashlib.sha256(b"".join(files[k] for k in sorted(files)) + manifest.encode()).hexdigest()[:8]
    files[".claude-plugin/plugin.json"] = manifest.replace("{version}", version).encode()
    return files


def plugin_zip(name: str, guild: str, mcp_url: str) -> bytes:
    """The plugin as a zip to upload in Claude, the same bytes on every start."""
    files = plugin_files(name, guild, mcp_url)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for file in sorted(files):
            z.writestr(zipfile.ZipInfo(file, (2026, 1, 1, 0, 0, 0)), files[file], compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def marketplace(name: str, guild: str, mcp_url: str, repo: str) -> dict[str, bytes]:
    """A marketplace holding the plugin, by path: a repository's whole contents. Pushed to `repo` (owner/name on
    GitHub, public), members add it in Claude (Customize > Plugins > Add marketplace), and Claude updates the plugin
    when its version changes (marketplaces added from claude.ai auto-update)."""
    slug = _slug(name)
    files = {f"plugins/{slug}/{path}": data for path, data in plugin_files(name, guild, mcp_url).items()}
    files[".claude-plugin/marketplace.json"] = (json.dumps({
        "name": slug, "owner": {"name": name, "url": mcp_url.removesuffix("/mcp")},
        "metadata": {"description": f"{name}, World of Warcraft raid analysis for {guild}, as a Claude plugin."},
        "plugins": [{"name": slug, "source": f"./plugins/{slug}",
                     "description": f"World of Warcraft raid analysis for {guild}, from Warcraft Logs."}],
    }, indent=2) + "\n").encode()
    files["README.md"] = _filled("MARKETPLACE.md", name, guild, mcp_url).replace("{repo}", repo).encode()
    return files


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _filled(file: str, name: str, guild: str, mcp_url: str) -> str:
    text = resources.files("wipebot.mcp").joinpath("claude_plugin", file).read_text(encoding="utf-8")
    fill = {"{name}": name, "{slug}": _slug(name), "{guild}": guild, "{mcp_url}": mcp_url,
            "{site}": mcp_url.removesuffix("/mcp")}
    for k, v in fill.items():
        text = text.replace(k, v)
    return text
