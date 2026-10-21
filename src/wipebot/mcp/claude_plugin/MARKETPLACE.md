# {name} for Claude

The {name} connector at {mcp_url} and its raid-log-analysis skill, as a Claude plugin for {guild}. With the skill,
Claude reads raid logs the way the method says; a connector alone brings only the tools.

## Add it

In Claude (claude.ai or Claude Desktop): **Customize → Plugins → Add → Add marketplace**, enter `{repo}`, then add
the {name} plugin and sign in with Warcraft Logs. With **Sync automatically** on, Claude keeps it up to date; the
marketplace's **Refresh** updates it at once.

In Claude Code: `/plugin marketplace add {repo}`, then `/plugin install {slug}@{slug}`.

Made with [Wipebot](https://github.com/Enf0/wipebot): `wipebot plugin DIR` writes this repository.
