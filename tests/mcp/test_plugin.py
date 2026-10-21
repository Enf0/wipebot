"""The connector and the skill as a Claude plugin (plugin.py): a zip to upload, or a marketplace for a repository."""
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
