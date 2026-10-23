# Wipebot

An AI raid analyst for World of Warcraft®. It reads [Warcraft Logs](https://www.warcraftlogs.com) reports and answers
questions about them in Claude, ChatGPT or another assistant that takes MCP connectors. Self-hosted: each guild runs
its own copy.

It can also run as a Discord bot, answering in the guild's channels and DMs and posting after each wipe.

## Try it

A public copy runs at [wipebot.enfo.dev](https://wipebot.enfo.dev), free and best effort, for any guild. In Claude,
add the plugin marketplace `Enf0/wipebot-plugin` (the connector with the raid-log-analysis skill); elsewhere, add
`https://wipebot.enfo.dev/mcp` as a custom connector. Then sign in with Warcraft Logs. It's shared
by everyone on one Warcraft Logs API allowance, so questions that need new data can wait for the next hour; for your
guild alone, run your own copy ([Setup](#setup)).

## What you can ask

- **Why did we wipe?** What ended the pull and what led up to it, on one pull or across the night.
- **How did I do?** Deaths, mechanics, casts, cooldowns, talents and gear, against the top players of the spec on
  the same boss.
- **Where was everyone standing?** A map of the room at any moment of a pull.
- **Did we follow the plan?** Raid notes and cooldown assignments against what players did.
- **What about the game?** Boss strategies, class guides, spells, loot and recent hotfixes.

More, with examples: [docs/features.md](docs/features.md).

## How it works

The analysis is code: tools compute every number in an answer from the log, and the model picks the tools and
explains their output. The tools are an [MCP](https://modelcontextprotocol.io) server, used two ways:

- **Connector**: members add your server to Claude, ChatGPT or similar and sign in with Warcraft Logs; answers run
  on their own subscription.
- **Discord bot**: the guild asks in channels and DMs, and it can post after each wipe; answers run on your
  Anthropic API key.

## Setup

You need a Warcraft Logs API client from [warcraftlogs.com/api/clients](https://www.warcraftlogs.com/api/clients)
(not a public client) and the guild's reports public. The connector and the bot run with Docker Compose.

```sh
git clone https://github.com/Enf0/wipebot.git && cd wipebot
cp .env.example .env
mkdir -p data/wipebot-discord data/wipebot-mcp data/wipebot-notes && sudo chown -R 1000:1000 data
```

### Connector

1. In `.env`, set `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET`, `WIPEBOT_PUBLIC_URL` (e.g. `https://wipebot.example.com`)
   and `WIPEBOT_ALLOWED_GUILDS` (the guild's warcraftlogs.com address). Give the client the redirect URL
   `https://<your host>/wcl/callback`.
2. `docker compose up -d --build wipebot-mcp`, with a reverse proxy serving HTTPS in front of `127.0.0.1:8000`.
3. Members sign in with Warcraft Logs. In Claude, they add the plugin (Customize → Plugins): the zip your server
   serves at `https://<your host>/plugin.zip`, or a marketplace if you publish one
   ([docs/setup.md](docs/setup.md)). Elsewhere, they add `https://<your host>/mcp` as a connector.

### Discord bot

1. Create an application in the [developer portal](https://discord.com/developers/applications), turn off **Public
   Bot**, and set `DISCORD_TOKEN`, `ANTHROPIC_API_KEY` and the Warcraft Logs client in `.env`.
2. `docker compose up -d --build wipebot-discord`, and invite it with the link in its log.
3. In the server, run `/wipebot setup` with the guild's Warcraft Logs page.

Every setting, the Warcraft Logs quota, raid notes and plans, upgrading and troubleshooting:
[docs/setup.md](docs/setup.md).

## Costs and data

- The connector costs only hosting: members' answers run on their own plan. The bot's answers cost about $0.25 each
  with Claude Opus ([docs/models.md](docs/models.md)).
- Everything Wipebot keeps stays in `./data`: who signed in to the connector, and the characters and preferences
  raiders give the bot. Only what an answer needs goes to the model.

## Limitations

- Built and tested for retail raids, for now. It may answer about a Mythic+ log's boss fights, but it knows nothing
  of keys yet (the timer, trash, affixes) and its guides and comparisons are for raids. Classic logs are on Warcraft
  Logs' separate Classic sites, which it doesn't read yet.
- The method, how to read a raid log, is a [skill](src/wipebot/core/skills/raid-log-analysis): the bot and Claude
  (with the plugin) use it; other assistants get the tools, whose output carries what code can decide of it (how a
  pull ended, and what it wasn't), for now. MCP has a
  [standard](https://modelcontextprotocol.io/extensions/skills/overview) for a server to serve its skills itself, but
  the major assistants [don't load them yet](https://modelcontextprotocol.io/extensions/client-matrix)
  ([docs/reference/mcp-clients.md](docs/reference/mcp-clients.md)).
- The Discord bot runs on Claude only, for now (an Anthropic API key); the connector works with
  any assistant that takes MCP connectors.
- It sees only what Warcraft Logs recorded, and answers can be wrong.

## Development

Needs [uv](https://docs.astral.sh/uv/) and [just](https://just.systems): `uv sync`, then `just` lists the commands
and `just check` runs the tests, lint, types and layers. Tests run offline ([tests/README.md](tests/README.md));
evals need Claude and Warcraft Logs keys and cost money ([evals/README.md](evals/README.md)). Maintained on a
best-effort basis; for a feature, open an issue before a pull request ([CONTRIBUTING.md](CONTRIBUTING.md)).

## Contact

Open an issue, or reach the maintainer: Enfo (`enf0`) on [Discord](https://discord.com/users/111820993068081152), or
enfo@enfo.dev. Security problems go privately through **Security → Report a vulnerability**
([CONTRIBUTING.md](CONTRIBUTING.md#security)).

## License

Wipebot is free software under the [GNU Affero General Public License v3.0](LICENSE) or later. You may use, change
and host it; if you run a changed copy for others, as a connector or a bot, you must offer them its source under the
same license.

Wipebot isn't affiliated with or endorsed by Blizzard Entertainment or Warcraft Logs. World of Warcraft and Warcraft
are trademarks or registered trademarks of Blizzard Entertainment, Inc.
