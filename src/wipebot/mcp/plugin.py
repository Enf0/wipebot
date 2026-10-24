"""The connector as a plugin for Claude, and for ChatGPT and Codex: the remote server's connector plus the core's
raid-log-analysis skill, so the model gets the method too (a connector alone brings only the tools). One folder
carries both formats: Claude's manifest in `.claude-plugin/`, and OpenAI's: the portable `plugin.json` at its root
(the Agent Plugins schema) and its server in `mcp.json`. The server serves it at /plugin.zip, to upload in Claude
(Customize > Plugins) or by a ChatGPT workspace admin, and `wipebot plugin DIR` writes it as a marketplace for a
repository, with a catalogue for each (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`), which
Claude, Codex and ChatGPT desktop keep up to date; it names this server, so each host's plugin is its own."""
import hashlib
import io
import json
import re
import zipfile
from importlib import metadata, resources

PATH = "/plugin.zip"
AGENT_PLUGINS = "https://agent-plugins.org/schemas/1.0.0"  # OpenAI's portable plugin format
CATEGORY = "Productivity"
PROMPTS = ["Why did we wipe on the last pull?", "How did I do on this boss tonight?", "How did tonight's raid go?"]


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
    # The server for ChatGPT desktop and Codex, which read it from mcp.json when the root plugin.json is there, and
    # reject a server with any key beyond `type`, `url` and `headers` (codex-rs, 23 October 2026; the docs'
    # `extensions` sign-in settings drop it): they find the sign-in from the server's own OAuth metadata.
    files["mcp.json"] = _json({"$schema": f"{AGENT_PLUGINS}/mcp.schema.json",
                               "mcpServers": {_slug(name): {"type": "streamable-http", "url": mcp_url}}})
    version = hashlib.sha256(b"".join(files[k] for k in sorted(files)) + manifest.encode()).hexdigest()[:8]
    files[".claude-plugin/plugin.json"] = manifest.replace("{version}", version).encode()
    files["plugin.json"] = _portable(name, guild, mcp_url, version)
    return files


def _portable(name: str, guild: str, mcp_url: str, version: str) -> bytes:
    """OpenAI's portable manifest, for ChatGPT and Codex: semver from Wipebot's version, the content hash after it."""
    try:
        release = metadata.version("wipebot")
    except metadata.PackageNotFoundError:
        release = "0.0.0"
    site = mcp_url.removesuffix("/mcp")
    what = f"World of Warcraft raid analysis for {guild}, from Warcraft Logs"
    return _json({
        "$schema": f"{AGENT_PLUGINS}/plugin.schema.json", "name": _slug(name), "version": f"{release}+{version}",
        "description": f"{what}: why a pull wiped, how a night went, how a player did, with the method for reading "
                       "them.",
        "author": {"name": name, "url": site}, "homepage": site, "keywords": ["world-of-warcraft", "warcraft-logs",
                                                                           "raiding", "raid-analysis"],
        "extensions": {"com.openai": {"interface": {
            "displayName": name, "shortDescription": f"{what}.",
            "longDescription": f"Ask why a pull wiped, how a raid night went, what keeps wiping the raid, or how a "
                               f"player did and why their damage or healing is low. The {name} connector reads the "
                               "reports from Warcraft Logs and computes every number in code; the raid-log-analysis "
                               "skill says how to read them. Sign in with your Warcraft Logs account.",
            "developerName": name, "category": CATEGORY, "capabilities": ["Read"], "websiteURL": site,
            "privacyPolicyURL": f"{site}/privacy", "defaultPrompt": PROMPTS}}}})


def _json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()


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
    GitHub, public, so members need no GitHub account), members add it in Claude (Customize > Plugins > Add
    marketplace) and get a changed version with Check for updates; in Codex and ChatGPT desktop, from
    `codex plugin marketplace add`, the same plugin through OpenAI's catalogue."""
    slug = _slug(name)
    files = {f"plugins/{slug}/{path}": data for path, data in plugin_files(name, guild, mcp_url).items()}
    files[".claude-plugin/marketplace.json"] = (json.dumps({
        "name": slug, "owner": {"name": name, "url": mcp_url.removesuffix("/mcp")},
        "metadata": {"description": f"{name}, World of Warcraft raid analysis for {guild}, as a Claude plugin."},
        "plugins": [{"name": slug, "source": f"./plugins/{slug}",
                     "description": f"World of Warcraft raid analysis for {guild}, from Warcraft Logs."}],
    }, indent=2) + "\n").encode()
    files[".agents/plugins/marketplace.json"] = _json({
        "name": slug, "interface": {"displayName": name},
        "plugins": [{"name": slug, "source": {"source": "local", "path": f"./plugins/{slug}"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": CATEGORY}]})
    files["README.md"] = _filled("MARKETPLACE.md", name, guild, mcp_url).replace("{repo}", repo).encode()
    return files


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _filled(file: str, name: str, guild: str, mcp_url: str) -> str:
    text = resources.files("wipebot.mcp").joinpath("claude_plugin", file).read_text(encoding="utf-8")
    fill = {"{name}": name, "{slug}": _slug(name), "{guild}": guild, "{mcp_url}": mcp_url,
            "{site}": mcp_url.removesuffix("/mcp"),
            "{who}": ("anyone with an account is let in" if guild == "any guild"
                      else "only members of the guild are let in")}
    for k, v in fill.items():
        text = text.replace(k, v)
    return text
