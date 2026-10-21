# Setting up Wipebot

The details behind the README's setup: what the connector and the bot need, every setting, the Warcraft Logs
quota, raid notes and plans, officers, upgrading, and what to do when something fails.

## What you need

- **Every way:** a Warcraft Logs account and an API client. The guild's reports must be public: the client reads
  public reports only.
- **The connector:** a domain with HTTPS in front of the server (a reverse proxy such as Caddy or nginx, or a
  Cloudflare Tunnel), and members whose assistant plan allows custom connectors. Each member claims their characters
  on warcraftlogs.com before signing in.
- **The bot:** a Discord application and an [Anthropic API](https://console.anthropic.com) key with credit.
- **To host either:** Linux with Docker Engine and Compose v2, and git.

## The Warcraft Logs client and quota

Create a client at [warcraftlogs.com/api/clients](https://www.warcraftlogs.com/api/clients), leaving **Public Client**
unticked so it gets a secret. For the connector, give it the redirect URL `https://<your host>/wcl/callback`
(several redirect URLs are comma-separated).

Warcraft Logs allows 3,600 points an hour per developer account, shared by every client of that account, so the
connector and the bot of one copy share it; keys made since 29 September 2026 get less for now. A first question
about a new pull costs tens of points and a follow-up a point or two: roughly 40 new questions an hour, or hundreds
of follow-ups. Following a live raid costs the bot about 300 points an hour. The few questions that read many nights
at once are refused when the hour is nearly used up, with when to ask again. Details:
[reference/wcl-api.md](reference/wcl-api.md).

## Settings

In `.env` (copy [.env.example](../.env.example)):

| Setting | Used by | |
|---|---|---|
| `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET` | both | required |
| `WIPEBOT_PUBLIC_URL` | connector | required: the address members reach, e.g. `https://wipebot.example.com` |
| `WIPEBOT_ALLOWED_GUILDS` | connector | required: whose members may sign in (below) |
| `WIPEBOT_OFFICER_RANK` | connector | the highest guild rank of officers, who save raid notes and plans (default 1, [below](#officers-and-admins)) |
| `WIPEBOT_TRUSTED_PROXIES` | connector | the reverse proxy's address, if you want real client IPs in the log |
| `DISCORD_TOKEN`, `ANTHROPIC_API_KEY` | bot | required |
| `WIPEBOT_MODEL` | bot | `opus` (default), `sonnet` or `haiku` |
| `WIPEBOT_CHANNEL_CONTEXT` | bot | `1`: answers also read the channel's last 15 messages (needs the Message Content intent) |
| `WIPEBOT_RECORD_ANSWERS` | bot | `1`: keep questions and answers for 90 days, for reviewing them |

Each app says at start which of its required settings are missing. Rarely needed: `WIPEBOT_MCP_MEMORY` and
`WIPEBOT_BOT_MEMORY`, Docker's memory limit per container (default `2g` and `4g`), and `WIPEBOT_CACHE_MAX_MB`,
past which the oldest cached entries go (default 2000).

## The connector

`WIPEBOT_ALLOWED_GUILDS` takes the guild's warcraftlogs.com address as copied from the browser, its id there, or
`region/realm/guild` (e.g. `eu/argent-dawn/the-copper-lanterns`), comma-separated for several. Add `:<highest rank>`
to let in only ranks up to it (0 is the guild master): `eu/argent-dawn/the-copper-lanterns:5`.

The server listens on `127.0.0.1:8000`; point the reverse proxy at it. Elsewhere than Claude, such as ChatGPT,
members add `https://<your host>/mcp` as a connector (the address must end in `/mcp`).

In Claude, members add Wipebot as a plugin: the connector with the skill, so Claude gets the method too. Either way,
they then press **Connect** on the plugin's connector and sign in with Warcraft Logs.

- **The zip** your server serves at `https://<your host>/plugin.zip`: members download it and add it under
  Customize → Plugins. Nothing to set up, but when the skill changes they download it again.
- **A marketplace**, so updates reach members: `python -m wipebot plugin <dir> --repo <owner>/<name>` writes the plugin
  for a GitHub repository; push it, and push again after an upgrade that changes the skill. Members add it under
  Customize → Plugins → Add → Add marketplace, and press Check for updates in Manage marketplaces. Claude needs its
  GitHub app to have access to the repository, public or not, so each member needs a GitHub account connected in
  Claude.

## The Discord bot

1. In the [developer portal](https://discord.com/developers/applications), set **Installation → Install Link** to
   None, then under **Bot** turn off **Public Bot** (so only you can add it) and copy the token. For
   `WIPEBOT_CHANNEL_CONTEXT=1`, also turn on **Message Content Intent** there.
2. The invite link is in the bot's log (`docker compose logs wipebot-discord | grep "Invite link"`). It asks to see
   channels, send messages (in threads too), read history and attach files (the maps).
3. In the server, someone with **Manage Server** runs:
   - `/wipebot setup link:<the guild's warcraftlogs.com page>`, optionally with a role needed to chat with it
     (`role:`) and the officers' role (`officers:`; default those with Manage Server; what officers can do:
     [below](#officers-and-admins));
   - `/wipebot names on:True` in any channel where it may name players, such as an officers' channel (in other
     channels it never blames anyone by name; in DMs it may);
   - `/wipebot settings` to see the setup and the saved notes and plans.

Raiders @mention it or reply to it in a channel, or DM it.

## Raid notes and plans

Members can paste a note or plan into a question, in either app. Saving them for the guild, so every answer checks
pulls against them, takes an officer ([below](#officers-and-admins)) pasting one: to the bot, or in Claude or ChatGPT
while asking to save it. Both apps read and write the same notes (`./data/wipebot-notes`). More:
[features.md](features.md).

**Fetching plans from raidstrats.gg** instead of pasting them, optional: create an app on
[raidstrats.gg/developers](https://raidstrats.gg/developers) with the data Plans and In-game import and the redirect
URL `http://localhost:8765/callback`, and set `RAIDSTRATS_CLIENT_ID` and `RAIDSTRATS_CLIENT_SECRET` in `.env`. The
bot then has `/wipebot raidstrats`, with which an officer logs in once (or `wipebot raidstrats-login --guild <id>` on
the command line), and the guild's plans are fetched whenever an answer needs one. For the connector to fetch them
too, pass both settings to it in a `compose.override.yml`:

```yaml
services:
  wipebot-mcp:
    environment:
      RAIDSTRATS_CLIENT_ID: ${RAIDSTRATS_CLIENT_ID}
      RAIDSTRATS_CLIENT_SECRET: ${RAIDSTRATS_CLIENT_SECRET}
```

## Officers and admins

Every member can ask questions. These can also change what Wipebot does for the whole guild:

| Who | Can also | Who they are |
|---|---|---|
| Officers, in Claude or ChatGPT | save a pasted raid note or plan for the guild | members with a character at guild rank 0 to `WIPEBOT_OFFICER_RANK` (default 1: WoW's default Guild Master and Officer ranks), as Warcraft Logs had it at sign-in or within the last hour |
| Officers, in Discord | save a pasted raid note or plan for the guild; change or stop the posts after wipes someone else asked for in a channel | members with the role `/wipebot setup officers:` sets; with none set, those with Manage Server |
| Admins, in Discord | the `/wipebot` commands: setup, names on or off per channel, settings | members with Manage Server (a server owner can change that under Server Settings → Integrations) |

With the officer role set, Manage Server alone doesn't make someone an officer: an admin gives themselves the role.

## What's kept

The connector keeps who signed in (Warcraft Logs id, name, guild characters); the bot keeps each raider's character
and the preferences they ask it to remember. With `WIPEBOT_RECORD_ANSWERS=1`, the bot also keeps each question, its
prompt (with the channel's messages it read), answer and tool calls for 90 days, for reviewing answers. All of it
stays in `./data`; only what an answer needs goes to the model. Tell your guild.

The bot's answers cost about $0.25 each with Claude Opus, the default (median $0.23, 90th percentile $0.37 in the
evals of October 2026), and at most $2; Haiku (`WIPEBOT_MODEL=haiku`) costs under a cent and answers less well
([models.md](models.md)).

## Upgrading and backups

`git pull && docker compose up -d --build`. Stopping the bot lets the answers in progress finish first (up to about
seven minutes). Everything Wipebot keeps is in `./data`: back that up. Changes raiders notice are in
[CHANGELOG.md](../CHANGELOG.md).

Outside Docker, the database, the raid notes and the sign-ins are in `~/.local/share/wipebot/` (`WIPEBOT_DB`,
`WIPEBOT_NOTES_DB` and `WIPEBOT_SIGNIN_DB` set other paths) and the cache in `~/.cache/wipebot/` (`WIPEBOT_CACHE`;
`off` for none).

## Third-party services

Wipebot uses the Warcraft Logs API with your clients, under
[its terms](https://www.archon.gg/wow/articles/help/rpg-logs-api-terms-of-service). Guide lookups go through
[warcraft_cli](https://github.com/aurokin/warcraft_cli), which reads Wowhead, Method, Icy Veins and Lorrgs, and
Wipebot downloads Raidbots' game data files. Some of these sites' may terms limit automated access; hosts are responsible for their use of them. Wipebot caches what it reads, links to the source and
doesn't republish pages.

## If something's wrong

- **"Not set (.env.example says what each is)"** with the settings under it, or **"Warcraft Logs isn't set up: set
  WCL_CLIENT_ID and WCL_CLIENT_SECRET"**, at start: they're missing from `.env`, or the container was started before
  they were added (`docker compose up -d` again).
- **"set WIPEBOT_PUBLIC_URL"** or **"WIPEBOT_PUBLIC_URL must be an HTTPS URL"**: the connector needs the HTTPS address
  members reach it at.
- **"Discord turned down DISCORD_TOKEN"**: copy the bot's token again (Bot → Reset Token in the developer portal).
- **The bot or server refuses to start because a Warcraft Logs user login is saved:** warcraft_cli on that machine
  has a personal login, which would let everyone read its private reports. Run it from an account without one.
- **"Warcraft Logs isn't answering"** in answers: the API is down or slow, or the hour's points are used up.
- **Members' assistant says it found no MCP server:** the address must end in `/mcp`.
- **New tools don't show in Claude or ChatGPT after an upgrade:** refresh the connector's tools there, or remove it
  and add it again.
