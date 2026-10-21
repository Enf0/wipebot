# {name}

World of Warcraft raid analysis for {guild}, from the guild's Warcraft Logs reports. Ask why a pull wiped, how a raid
night went, what keeps wiping the raid, or how a player did and why their damage or healing is low, and get an answer
the way an experienced raid leader reads the log.

The plugin has two parts:

- **The {name} connector** at {mcp_url}. Its tools read the guild's reports from Warcraft Logs and the game's guides,
  compute the facts in code and return them. You sign in with your Warcraft Logs account; only members of the guild
  are let in. What it sends to the server is what you ask the tools: report codes, bosses, pulls and character names.
- **The raid-log-analysis skill**: how to turn those facts into an answer, such as reading a boss's mechanics before
  judging them and never blaming the deaths after a wipe was decided.
