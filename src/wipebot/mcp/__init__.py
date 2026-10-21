"""Wipebot in Claude and ChatGPT: the tools over MCP, over stdio (stdio.py) and for many signed-in users at once
(remote.py, signin.py). The MCP server itself (its tools, instructions and skill) is wipebot.core.server, which the
Discord bot runs in-process too; this layer serves it and signs users in."""
