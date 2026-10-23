"""The connector and the skill as a plugin for Claude, ChatGPT and Codex (plugin.py): a zip to upload, or a marketplace
for a repository."""
import json

from wipebot.mcp import plugin

URL = "https://wipebot.test/mcp"


def test_a_marketplace_lists_its_plugin_and_says_how_to_add_it():
    files = plugin.marketplace("Wipebot", "The Copper Lanterns (Argent Dawn-EU)", URL, "someone/wipebot-plugin")
    listing = json.loads(files[".claude-plugin/marketplace.json"])
    [entry] = listing["plugins"]
    manifest = json.loads(files[entry["source"].removeprefix("./") + "/.claude-plugin/plugin.json"])
    assert entry["name"] == manifest["name"] == listing["name"] == "wipebot"
    assert manifest["mcpServers"] == {"Wipebot": {"type": "http", "url": URL}}
    assert "plugins/wipebot/skills/raid-log-analysis/SKILL.md" in files
    readme = files["README.md"].decode()
    assert "`someone/wipebot-plugin`" in readme and "{" not in readme


def test_the_version_changes_only_with_what_is_in_the_plugin():
    version = lambda guild: json.loads(plugin.plugin_files("Wipebot", guild, URL)[".claude-plugin/plugin.json"])[  # noqa: E731
        "version"]
    assert version("A") == version("A") != version("B")


def test_the_same_plugin_carries_openais_portable_format_for_chatgpt_and_codex():
    """Codex and ChatGPT desktop read the root plugin.json and mcp.json (the Agent Plugins schema) and the marketplace
    in .agents/plugins; they find the server's sign-in themselves."""
    files = plugin.marketplace("Wipebot", "any guild", URL, "someone/wipebot-plugin")
    catalogue = json.loads(files[".agents/plugins/marketplace.json"])
    [entry] = catalogue["plugins"]
    assert entry["source"] == {"source": "local", "path": "./plugins/wipebot"} and entry["policy"]["installation"]
    root = entry["source"]["path"].removeprefix("./")
    manifest = json.loads(files[f"{root}/plugin.json"])
    claude = json.loads(files[f"{root}/.claude-plugin/plugin.json"])
    assert manifest["name"] == "wipebot" and manifest["version"].endswith("+" + claude["version"])
    interface = manifest["extensions"]["com.openai"]["interface"]
    assert interface["privacyPolicyURL"] == "https://wipebot.test/privacy" and interface["defaultPrompt"]
    servers = json.loads(files[f"{root}/mcp.json"])
    assert servers["$schema"].endswith("/mcp.schema.json")
    assert servers["mcpServers"] == {"wipebot": {"type": "streamable-http", "url": URL}}  # Codex rejects other keys
    assert f"{root}/skills/raid-log-analysis/SKILL.md" in files
    readme = files["README.md"].decode()
    assert "codex plugin marketplace add someone/wipebot-plugin" in readme and "{" not in readme
    assert "anyone with an account is let in" in files[f"{root}/README.md"].decode()
