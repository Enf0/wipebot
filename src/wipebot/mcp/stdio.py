"""The tools over stdio (`wipebot mcp`), for Claude Code, Claude Desktop and any other MCP app on this machine: the
in-process server every app runs (wipebot.core.server.mcp), reading Warcraft Logs with the client credentials
(WCL_CLIENT_ID, WCL_CLIENT_SECRET), or with a login saved in warcraft_cli, which shows its user's private reports."""
from wipebot.core import server


def serve(default_report: str | None = None) -> None:
    """Run the server over stdio."""
    server.configure(default_report)
    server.mcp.run("stdio")
