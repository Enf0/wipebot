# {name} for Claude, ChatGPT and Codex

The {name} connector at {mcp_url} and its raid-log-analysis skill, as a plugin for {guild}. With the skill,
the model reads raid logs the way the method says; a connector alone brings only the tools.

## Add it

In Claude (claude.ai or Claude Desktop): **Customize → Plugins → Add → Add marketplace**, enter `{repo}`, then add
the {name} plugin and sign in with Warcraft Logs. With **Sync automatically** on, Claude keeps it up to date; the
marketplace's **Refresh** updates it at once.

In Claude Code: `/plugin marketplace add {repo}`, then `/plugin install {slug}@{slug}`.

In the ChatGPT desktop app or Codex: Plugins → **Add** → **Add plugin marketplace**, enter `{repo}` (or
`codex plugin marketplace add {repo}`), then install {name} from the Plugins directory and sign in with Warcraft Logs. A ChatGPT workspace admin can import it for the workspace instead:
Admin Console → Plugins → Add → Import marketplace, with `https://github.com/{repo}` as the source; it syncs daily.
ChatGPT marks a plugin with an MCP server "Desktop only", so in ChatGPT on the web or a phone add {mcp_url} as a
custom connector instead (the tools without the skill).

Made with [Wipebot](https://github.com/Enf0/wipebot): `wipebot plugin DIR` writes this repository.
