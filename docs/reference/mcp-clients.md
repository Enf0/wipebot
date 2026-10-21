# MCP clients: verified facts

What Claude and ChatGPT do as clients of Wipebot's remote server, and where the MCP, Anthropic and OpenAI
guidance says each kind of guidance for the model goes. This is why tool descriptions say what and when, the
raid-log-analysis skill holds the method, and `instructions.md` stays under 512 characters.

## Claude as a client of the remote server

Seen live on 2026-10-13 with claude.ai and the deployed remote server:

- The hosted apps name themselves by CIMD, `https://claude.ai/oauth/mcp-oauth-client-metadata` (redirect
  `https://claude.ai/api/mcp/auth_callback`); Claude Code by `https://claude.ai/oauth/claude-code-client-metadata`
  (`http://localhost/callback` and `http://127.0.0.1/callback`, any port). ChatGPT's is
  `https://chatgpt.com/oauth/client.json` (`token_endpoint_auth_method` `private_key_jwt`, `none` also offered).
- A connector added at the bare domain signs in fine (discovery works from the root) and then fails with "no MCP
  server was found at the provided URL": the URL must end in `/mcp`.
- claude.ai keeps a connector's tool list from when it connected: a tool added later shows up only after
  **Refresh tools** in the connector's settings (restarting the app doesn't do it). Until then its tool search
  picks other tools.
- Claude caches an MCP App's page by its `ui://` address: a changed page under the same address kept showing
  the old one after Refresh tools. The address carries a hash of the page (`remote.PULL_MAP_APP`).
- Claude's tool search picks tools by name and description; the server's instructions weigh less, so a tool's
  description says when to call it.

A plugin with the remote connector and the `raid-log-analysis` skill, uploaded on claude.ai on 2026-10-18
(Customize → Plugins, a zip of the plugin folder):

- It works in chat: the skill loads (asked as `/raid-log-analysis`), its reference files are read, and the
  connector signs in as before.
- The connector's name is the `mcpServers` key in `plugin.json`, but a connector already added at the same URL is
  reused with its own name: remove it, then add the plugin again.
- No icon shows for the plugin or the connector: none showed while the server sent icons in its handshake and
  served `/icon.png` and `/favicon.ico` (until 21 October 2026; it sends none now). A marketplace's sync says why for
  the plugin: "unrecognized key in plugin.json: 'icon' (stripped)", so the plugin has none.

A marketplace added on claude.ai (Customize → Plugins → Add → Add marketplace, 2026-10-19): a GitHub `owner/repo` or
a git URL. Its "Sync automatically" toggle keeps the plugins up to date "when the repository changes on GitHub", and
needs the Claude GitHub App to have access to the repository, public or not. The plugin page shows its version,
skills and connectors; the marketplace's page, the synced commit and any warnings. Its menu's Refresh syncs at once:
the plugin moved to the pushed version, with a notice that "executable files or settings may differ from the version
you were using before this sync". On its own it hadn't synced a push 8 minutes later, with Sync automatically on
and the Claude GitHub App granted the repository (a push changing only the repository's README, 08:46; still the
earlier commit at 08:54). It hadn't synced that push two days later either, nor a change to the plugin itself (pushed
2026-10-21 14:59 UTC, still the old commit an hour later). So members press Check for updates in Manage marketplaces.

Adding a plugin from it (its page's **Add**) doesn't connect its connector: the plugin's Connectors tab lists it as
"Not added" with a **Connect** button, which opens "Add custom connector" with the name and URL filled in and detects
the server's sign-in ("Sign in now") and its support for Claude's published client identity (CIMD). After signing in
it shows "Connected · Custom". The plugin page shows a revision ("v2"), not the manifest's version.

## Server instructions, tool metadata and skills

- What each client does with the server's instructions (2026-10-14): claude.ai and Claude Desktop drop them, as
  canary tests in anthropics/claude-ai-mcp#93 show (open); a tool's description arrives whole (1,770 characters
  tested). Claude Code puts them in its first user turn, cut at 4,096 characters, and cuts a tool's description
  at 4,096 (16,384 for a tool it loads through tool search; https://code.claude.com/docs/en/mcp). OpenAI says
  ChatGPT and Codex weigh the instructions' first 512 characters. Hence `instructions.md` is a short overview,
  under 512.
- Where each kind of guidance is meant to go (checked 2026-10-14):
  - Tool metadata says what a tool does and when to call it. Anthropic's connector review criteria
    (https://claude.com/docs/connectors/building/review-criteria): every tool needs a `title` and `readOnlyHint` or
    `destructiveHint` (read-only tools run without asking), and "describe what the tool does, and don't tell
    Claude how to behave". OpenAI (https://developers.openai.com/plugins/build/mcp-server): per-tool "when to use
    it", a title, annotations; server instructions only for cross-tool rules, the essentials in 512 characters.
  - How to do the task well is an Agent Skill (SKILL.md: a "what + when" description up to 1,024 characters, the body
    under 500 lines, files one level deep;
    https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices). Claude's plugins bundle skills
    with connectors and run the skills in claude.ai chat on paid plans
    (https://support.claude.com/en/articles/13837440); ChatGPT's plugins bundle skills and apps too.
  - MCP's Skills extension (`io.modelcontextprotocol/skills`, SEP-2640, Final) lets a server serve its skills
    itself (Final 13 Sep 2026). The Python SDK merged it on 11 Oct (python-sdk#3485), after 2.3.0; the TypeScript
    SDK, which Claude Code's MCP client is built on, hasn't (typescript-sdk#2818, #2972). Per
    https://modelcontextprotocol.io/extensions/client-matrix claude.ai and Claude Desktop don't load server skills,
    and ChatGPT imports them only at plugin submission, as reviewed snapshots. Anthropic hasn't said whether Claude
    will; it ships skills with connectors as plugins. The working group's open problems: prompt injection, unsigned
    digests, approval bound to content; its early tests saw models skip skills and call tools directly
    (https://aaif.io/blog/skills-over-mcp).
  - Why claude.ai drops server instructions isn't stated anywhere; the issue is open as a backlog enhancement.
- ChatGPT treats a tool without `readOnlyHint` as a write action and asks the user before each call
  (https://developers.openai.com/api/docs/guides/developer-mode); every tool carries it but the remote server's
  `save_note_or_plan`, the one write, which is a tool of its own so that only saving asks (`raid_note` stays
  read-only).

## How many tools (checked 15 October 2026)

- No client we serve caps the count. Claude Code and the Agent SDK defer tools behind tool search by default (Claude
  Code: no per-server cap; a tool marked `_meta["anthropic/alwaysLoad"]` loads up front; tool search matches only the
  first 4,096 characters of a description; https://code.claude.com/docs/en/mcp). claude.ai's tool access setting
  suggests Auto for 30+ tools (https://support.claude.com/en/articles/13730515). OpenAI suggests fewer than 20
  functions visible at the start of a turn, as a soft limit, and offers tool search for the rest
  (https://developers.openai.com/api/docs/guides/function-calling). VS Code Copilot caps a request at 128.
- What hurts is overlap: Anthropic names picking the wrong one of similar tools as the failure, and advises fewer,
  more capable tools, related operations behind one parameter
  (https://www.anthropic.com/engineering/writing-tools-for-agents,
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools). The GitHub MCP server merged its issue
  tools into one with a `method` for the same reason (github/github-mcp-server#1211).
- One catch-all tool is the other extreme: Sentry's single agent tool steered worse and took about twice as long
  (https://cra.mr/a-bigger-toolbox-for-mcp).
- Ours: 44 tools became 26 on 15 October by merging tools that answer one kind of question at different scopes; the
  connector-only evals went from 118/150 to 129/150. There are 28 now.
