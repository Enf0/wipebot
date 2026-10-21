# docs

For people using and running Wipebot:

- [features.md](features.md): what Wipebot can answer, for raiders and officers.
- [setup.md](setup.md): how to run it: every setting, the quota, upgrading and troubleshooting.
- [models.md](models.md): how the models compare on the evals, with cost and speed.

For working on it, [reference/](reference): what Wipebot depends on that no schema or manual says. Each fact was
checked against the real thing (a live report, a site, a client) and carries the date it was checked. Read the file
for an area before changing code there, and add a fact once you've verified it.

- [reference/wcl-api.md](reference/wcl-api.md): the Warcraft Logs API as it behaves: sign-in and quota, caching and
  batching, and what events, tables and rankings really return.
- [reference/sources.md](reference/sources.md): the guide and data sites behind the game knowledge (Wowhead, Method,
  Icy Veins, Lorrgs, Raidbots, Raider.IO, raid planners) and warcraft_cli, which reads most of them.
- [reference/mcp-clients.md](reference/mcp-clients.md): Claude and ChatGPT as clients of the MCP server, and why tool
  descriptions, the skill and the server instructions each hold what they do.
