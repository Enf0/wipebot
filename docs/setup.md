# Setting up Wipebot

The details behind the README's setup: what the connector and the bot need, every setting, the Warcraft Logs
quota, raid notes and plans, officers, upgrading, and what to do when something fails.

## What you need

- **Every way:** a Warcraft Logs account and an API client. The guild's reports must be public: the client reads
  public reports only.
- **The connector:** a domain with HTTPS in front of the server (a reverse proxy such as Caddy or nginx, or a
  Cloudflare Tunnel), and members whose assistant plan allows custom connectors. Each member claims their characters
  on warcraftlogs.com before signing in.
- **The bot:** a Discord application and an [Anthropic API](https://console.anthropic.com) key with credit, or an
  [OpenRouter](https://openrouter.ai) key ([below](#a-budget-the-guild-chips-in-to)).
- **To host either:** Linux with Docker Engine and Compose v2, and git.

## The Warcraft Logs client and quota

Create a client at [warcraftlogs.com/api/clients](https://www.warcraftlogs.com/api/clients), leaving **Public Client**
unticked so it gets a secret. For the connector, give it the redirect URL `https://<your host>/wcl/callback`
(several redirect URLs are comma-separated).

Warcraft Logs allows 3,600 points an hour per developer account, shared by every client of that account, so the
connector and the bot of one copy share it. An account on Warcraft Logs' new point system (in beta, opted into for
the whole account on that page) gets 1,000 unsubscribed. A first question about a night nobody has asked about yet
costs about 100-170 points, one about a pull tens, and a follow-up on what was already read a few or none: five
questions on a fresh night came to about 450, and later askers about the same night pay little. Posting after wipes
costs the bot about 300 points an hour while a raider has asked for it; otherwise the bot reads nothing between
questions. The few questions that read many nights at once are refused when the hour is nearly used up, with when to
ask again. Details:
[reference/wcl-api.md](reference/wcl-api.md).

## Settings

In `.env` (copy [.env.example](../.env.example)):

| Setting | Used by | |
|---|---|---|
| `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET` | both | required |
| `WIPEBOT_PUBLIC_URL` | connector | required: the address members reach, e.g. `https://wipebot.example.com` |
| `WIPEBOT_ALLOWED_GUILDS` | connector | required: whose members may sign in (below), or `*` for anyone |
| `WIPEBOT_OFFICER_RANK` | connector | the highest guild rank of officers, who save raid notes and plans (default 1, [below](#officers-and-admins)) |
| `WIPEBOT_TRUSTED_PROXIES` | connector | the reverse proxy's address, if you want real client IPs in the log |
| `DISCORD_TOKEN`, `ANTHROPIC_API_KEY` | bot | required; or `OPENROUTER_API_KEY` in place of the Anthropic key (below) |
| `OPENROUTER_API_KEY` | bot | answer through [OpenRouter](https://openrouter.ai) with this key: its credit limit is the budget anyone in the server sees with `/usage` ([below](#a-budget-the-guild-chips-in-to)) |
| `WIPEBOT_BUDGET_NOTE` | bot | how raiders chip in, shown by `/usage`, e.g. `Vipps to Oscar, 12345678` |
| `WIPEBOT_SERVER_SHARE` | bot | for a copy many servers share: the part of the OpenRouter key's limit (set to reset monthly) one server may spend a month, e.g. `0.1`; past it, that server's raiders are told when the free answers are back |
| `WIPEBOT_KEY_SECRET` | bot | what the AI keys admins add for their servers (`/wipebot key`) are encrypted with; default the Warcraft Logs client secret. Changing it makes saved keys unreadable, and servers add theirs again |
| `WIPEBOT_OPENROUTER_CALLBACK` | bot | where OpenRouter sends an admin after "Sign in with OpenRouter" in `/wipebot key`: a page that shows the address to paste back; default a localhost address that doesn't load, whose address bar holds it |
| `WIPEBOT_WCL_CALLBACK` | bot | turns on `/login`: where Warcraft Logs sends a raider after they sign in, a page that shows its address to paste back into Discord (any page does: the address bar holds the code). Add it to your Warcraft Logs client's redirect URLs (comma-separated) |
| `WIPEBOT_MODEL` | bot | `opus` (default), `sonnet` or `haiku`; through OpenRouter, another model's OpenRouter id (untested) |
| `WIPEBOT_EFFORT` | bot | how hard the model thinks: `low`, `medium`, `high`, `xhigh` or `max`; unset, the model's default |
| `WIPEBOT_CHANNEL_CONTEXT` | bot | `1`: answers also read the channel's last 15 messages and the Discord messages a question links to (needs the Message Content intent) |
| `WIPEBOT_RECORD_ANSWERS` | bot | `1`: keep questions and answers for 90 days, for reviewing them |
| `WIPEBOT_SOURCE_URL` | both | where users get your copy's source, shown on the sign-in page and in `/wipebot settings` (default this repository): set it if you changed the code, as the AGPL asks |

Each app says at start which of its required settings are missing. Rarely needed: `WIPEBOT_MCP_MEMORY` and
`WIPEBOT_BOT_MEMORY`, Docker's memory limit per container (default `2g` and `4g`), and `WIPEBOT_CACHE_MAX_MB`,
past which the oldest cached entries go (default 2000).

For a copy that shares its Warcraft Logs account with another (a public connector beside a guild's own), each
process can be held to part of the account's hour: `WIPEBOT_WCL_SHARE` (e.g. `0.1`: at most a tenth of the hour's
points, and of its database points), `WIPEBOT_WCL_LEAVE` (e.g. `0.3`: it reads nothing more once the account has
less than 30% of the hour left, whoever spent it). On Warcraft Logs' new point system, signed-in users' own points
don't count against the share; Wipebot tells the new system from the account's hourly limit (3,600 is the old
one's), and `WIPEBOT_WCL_USER_POINTS=1` or `0` says it instead. Past either, a tool says to ask again when the hour
ends, and what was read before still answers. Once the account's own database points for the hour are spent (new
system only), rankings, character pages and guild report lists say so until the hour ends, and the rest answers.

## The connector

`WIPEBOT_ALLOWED_GUILDS` takes the guild's warcraftlogs.com address as copied from the browser, its id there, or
`region/realm/guild` (e.g. `eu/argent-dawn/the-copper-lanterns`), comma-separated for several. Add `:<highest rank>`
to let in only ranks up to it (0 is the guild master): `eu/argent-dawn/the-copper-lanterns:5`.

`*` lets in anyone with a Warcraft Logs account, for a connector many guilds share. Each user's own guilds are then
where `find_reports` looks and whose saved notes they read; `WIPEBOT_OFFICER_RANK=9` lets any member save their
guild's raid note (each saved version names who saved it). Hold each user to part of the server's points with
`WIPEBOT_POINTS_PER_USER` and `WIPEBOT_CALLS_PER_USER` (an hour each; on the new point system only the points the
account pays count), and the server to part of the account's with the `WIPEBOT_WCL_*` settings above.

The server listens on `127.0.0.1:8000`; point the reverse proxy at it. Elsewhere than Claude, such as ChatGPT,
members add `https://<your host>/mcp` as a connector (the address must end in `/mcp`).

In Claude, the quickest way is a link that opens Claude's connector dialog filled in:
`https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Wipebot&connectorUrl=` followed by
the address, percent-encoded (`https%3A%2F%2F<your host>%2Fmcp`); members choose **Continue** and sign in.

Or members add Wipebot as a plugin: the connector with the skill, so Claude gets the method too. Either way,
they then press **Connect** on the plugin's connector and sign in with Warcraft Logs.

- **The zip** your server serves at `https://<your host>/plugin.zip`: members download it and add it under
  Customize → Plugins. Nothing to set up, but when the skill changes they download it again.
- **A marketplace**, so updates reach members: `python -m wipebot plugin <dir> --repo <owner>/<name>` writes the plugin
  for a GitHub repository; push it, and push again after an upgrade that changes the skill. Members add it under
  Customize → Plugins → Add → Add marketplace, and press Check for updates in Manage marketplaces. A public
  repository needs no GitHub account; a private one needs each member's GitHub connected in Claude, with the Claude
  GitHub App given the repository.

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
   - `/wipebot settings` to see the setup and the saved notes and plans.

   `footer:` on setup ends answers with what they cost; `/wipebot key` lets an admin add an AI key they have
   (OpenRouter or Anthropic) for the server's answers to run on.

Raiders @mention it or reply to it in a channel, or DM it; `/help` tells them what to ask, and `/usage` what's left of
the hour's Warcraft Logs points (and of the budget, [below](#a-budget-the-guild-chips-in-to)). With
`WIPEBOT_WCL_CALLBACK` set, `/login` signs a raider in with Warcraft Logs, so their own points pay first, and
`/login private:True` lets the bot read their private reports for their own questions. Every command: [features.md](features.md#discord-commands).

### A budget the guild chips in to

With `OPENROUTER_API_KEY` set, the bot answers through [OpenRouter](https://openrouter.ai) (Claude Code takes
OpenRouter's address and key: [their guide](https://openrouter.ai/docs/guides/community/anthropic-agent-sdk)), and
anyone in the server runs `/usage` to see what answering has cost this month, what's left and how to chip in
(`WIPEBOT_BUDGET_NOTE`). Raiders send you money as a contribution to running costs, and you raise the key's limit.
Credit itself can't be passed on: Anthropic's [credit terms](https://www.anthropic.com/legal/credit-terms) forbid
transferring or selling it.

1. In OpenRouter, under **BYOK**, add an Anthropic API key and choose **Never use OpenRouter's Anthropic capacity
   for any model**: Anthropic bills your key as before, with no OpenRouter fee up to their monthly allowance, and
   Max or Team plans' monthly API credit still pays. Without BYOK, the bot spends OpenRouter credits (bought with a
   fee).
2. Create a key for the bot with no expiry, a credit limit (what the guild has given) and **Include BYOK in limit**.
   At the limit OpenRouter refuses it, so the bot can't spend more than the budget. A guardrail allowing only the
   Anthropic provider keeps other hosts of the same models from serving it on OpenRouter credits.
3. Set `OPENROUTER_API_KEY` (in place of `ANTHROPIC_API_KEY`) and `WIPEBOT_BUDGET_NOTE`, and restart the bot.

When someone chips in, raise the key's limit by the amount (OpenRouter → **Keys**). `/usage` estimates how many
answers are left only when answers are recorded (`WIPEBOT_RECORD_ANSWERS=1`).

## Raid notes and plans

Members can paste a note or plan into a question, in either app. Saving them for the guild, so every answer checks
pulls against them, takes an officer ([below](#officers-and-admins)) pasting one: to the bot, or in Claude or ChatGPT
while asking to save it. Both apps read and write the same notes (`./data/wipebot-notes`). More:
[features.md](features.md).

Viserio Cooldowns and raidstrats.gg are experimental, and connected by the host on the command line only (in Docker,
`docker compose exec wipebot-discord wipebot ...`); Discord has no command for them.

**The raid nights to come from Viserio Cooldowns**, optional: a group admin turns on API sharing and makes the group's
key in its settings on wowutils.com, and you give it to Wipebot with `wipebot viserio --guild <id>` (the guild's
Warcraft Logs id, with the key in `WOWUTILS_API_KEY` or typed). Both apps then answer "am I in on
the next boss?" and "who's coming Monday?" from the calendar's sign-ups and the week's setups. The key is used only
for the guild its group names, and what it reads is kept in memory for two minutes, never on disk.

**Fetching plans from raidstrats.gg** instead of pasting them, optional: create an app on
[raidstrats.gg/developers](https://raidstrats.gg/developers) with the data Plans and In-game import and the redirect
URL `http://localhost:8765/callback`, and set `RAIDSTRATS_CLIENT_ID` and `RAIDSTRATS_CLIENT_SECRET` in `.env`. Then
`wipebot raidstrats-login --guild <id>` logs in once, with a raidstrats.gg account that can open the guild's plans,
and the guild's plans are fetched whenever an answer needs one. For the connector to fetch them
too, pass both settings to it in a `compose.override.yml`:

```yaml
services:
  wipebot-mcp:
    environment:
      RAIDSTRATS_CLIENT_ID: ${RAIDSTRATS_CLIENT_ID}
      RAIDSTRATS_CLIENT_SECRET: ${RAIDSTRATS_CLIENT_SECRET}
```

The fetch reads the plans the officer's account can open: their own and the guild's. A plan Viserio's assignment
note links by default belongs to its author: duplicate it on raidstrats.gg and save the copy once (raidstrats.gg makes
its export on save), or paste it. Pasting Viserio's export of a section (its RSGG Addon button) also brings the
night's raiders on their numbers. If the app's secret is changed on raidstrats.gg, set the new one in `.env`: the
officer's login is kept and works again.

## Officers and admins

Every member can ask questions. These can also change what Wipebot does for the whole guild:

| Who | Can also | Who they are |
|---|---|---|
| Officers, in Claude or ChatGPT | save a pasted raid note or plan for the guild | members with a character at guild rank 0 to `WIPEBOT_OFFICER_RANK` (default 1: WoW's default Guild Master and Officer ranks), as Warcraft Logs had it at sign-in or within the last hour |
| Officers, in Discord | save a pasted raid note or plan for the guild; change or stop the posts after wipes someone else asked for in a channel | members with the role `/wipebot setup officers:` sets; with none set, those with Manage Server |
| Admins, in Discord | the `/wipebot` commands ([features.md](features.md#discord-commands)): setup (with the cost footer), settings, an AI key of theirs for the server's answers | members with Manage Server (a server owner can change that under Server Settings → Integrations) |

With the officer role set, Manage Server alone doesn't make someone an officer: an admin gives themselves the role.

## What's kept

The connector keeps who signed in (Warcraft Logs id, name, guild characters); the bot keeps each raider's character
and the preferences they ask it to remember, and for a raider who used `/login`, their Warcraft Logs id, name and
token (encrypted) until they sign out. It also keeps what each answer cost, with the server and the asker, for 90
days (`/usage` and the free answers' share read it), each server's settings and the AI key its admin added (encrypted) until it's
removed, and the conversations Claude Code keeps on disk so follow-ups and restarts go on from them. With
`WIPEBOT_RECORD_ANSWERS=1`, the bot also keeps each question, its prompt (with the channel's messages it read),
answer and tool calls for 90 days, for reviewing answers. All of it stays in `./data`; only what an answer needs goes
to the model. Tell your guild.

The bot's answers cost about $0.15-0.25 each with Claude Opus, the default, and at most $2; Haiku
(`WIPEBOT_MODEL=haiku`) costs about a cent and answers less well ([models.md](models.md)).

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
Wipebot downloads Raidbots' game data files. Some of these sites' terms may limit automated access; hosts are
responsible for their use of them. Wipebot caches what it reads, links to the source and doesn't republish pages.

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
