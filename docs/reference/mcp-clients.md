# MCP clients: verified facts

What Claude and ChatGPT do as clients of Wipebot's remote server, and where the MCP, Anthropic and OpenAI
guidance says each kind of guidance for the model goes. This is why tool descriptions say what and when, the
raid-log-analysis skill holds the method (what an answer holds, never how it looks), what code can decide of the
method is in the tools' output (the one channel every client reads), and `instructions.md` stays under 512
characters.

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
a git URL. A public repository needs no GitHub account; a private one needs the member's GitHub connected and the
Claude GitHub App given the repository, which the dialog asks for (https://claude.com/docs/plugins/overview, read
2026-10-24). Its "Sync automatically" toggle keeps the plugins up to date "when the repository changes on GitHub", and
needs the Claude GitHub App to have access to the repository, public or not. The plugin page shows its version,
skills and connectors; the marketplace's page, the synced commit and any warnings. Its menu's Refresh syncs at once:
the plugin moved to the pushed version, with a notice that "executable files or settings may differ from the version
you were using before this sync". On its own it never synced a push, with Sync automatically on and the Claude GitHub
App granted the repository (checked from minutes to two days after a push, 2026-10-19 to 21), so members press Check
for updates in Manage marketplaces.

Adding a plugin from it (its page's **Add**) doesn't connect its connector: the plugin's Connectors tab lists it as
"Not added" with a **Connect** button, which opens "Add custom connector" with the name and URL filled in and detects
the server's sign-in ("Sign in now") and its support for Claude's published client identity (CIMD). After signing in
it shows "Connected · Custom". The plugin page shows a revision ("v2"), not the manifest's version.

## ChatGPT and Codex as plugin hosts

From OpenAI's docs, read 2026-10-23 (https://developers.openai.com/plugins/build/plugins and
https://help.openai.com/en/articles/20001504):
- A plugin's portable manifest is the root `plugin.json`, its MCP servers in root `mcp.json` with a transport `type`;
  `.codex-plugin/plugin.json` is only a fallback for OpenAI's settings. The docs put sign-in settings in each
  server's `extensions["com.openai"].auth`, but Codex (which the ChatGPT desktop app's plugins run on) drops a server
  with any key beyond `type`, `url` and `headers` (its source, 23 October 2026), so leave them out: it finds the
  sign-in from the server's OAuth metadata. With a root `plugin.json` it reads only `mcp.json`, not `.mcp.json`.
- A marketplace is `.agents/plugins/marketplace.json` (or a Claude-compatible `.claude-plugin/marketplace.json`);
  each entry needs `name`, `source` (a path from the repository root, starting `./`), `policy` and `category`.
- A workspace admin imports a GitHub marketplace in Admin Console → Plugins → Add → Import marketplace (the
  repository URL, a path only when the manifest isn't at the root); it syncs daily or on **Sync now**. Import ignores
  the entries' `policy`: the admin sets installation per plugin, and importing connects no one's account.
- ChatGPT labels a plugin that declares MCP servers "Desktop only", even a remote HTTPS one, so it doesn't run in
  ChatGPT on the web; there the connector alone is the way (Plugins → Add → Add custom MCP server). An `.app.json`
  pointing at an existing app doesn't lift the label.

Seen in the ChatGPT desktop app (Windows), 2026-10-23: Settings → Plugins → Add → **Add plugin marketplace** takes a
GitHub `owner/repo`, a git ref and sparse paths, no terminal needed. The plugin then lists under Personal with its
example prompts, skill, version, website and privacy links, and installs. Its server shows under Settings → Plugins →
MCPs, "From plugins", only when Codex could read it (above); signing in there works, and its tools then answer in
the app's Codex part only, not in its ChatGPT chats, which use connectors registered in ChatGPT (an `.app.json` can
point a plugin at one only by its app id, which a directory listing gets). ChatGPT on the web doesn't list the marketplace's plugin at all.

The connector in ChatGPT (developer mode, Windows desktop app), 2026-10-23:
- Added at the bare domain it signs in and then says "Authentication succeeded, action discovery failed", with no
  tools: as in Claude, the URL must end in `/mcp`.
- Its tool list groups the tools by annotation and puts every tool not marked read-only under Write, the viewer's
  app-only tools too.
- It notes "These private tools can't render their widgets: pull_replay, viewer_log": the viewer's own tools carry
  its `ui://` address (the SDK's `Apps.tool` sets it), which ChatGPT expects only on a tool the model calls. The
  viewer calls them, so nothing is lost.
- `openai/profile` on `my_characters` changed nothing on the connector's page: the account is still named from the
  sign-in ("<name>'s Wipebot account").

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
  - How an answer looks (length, prose style, formatting) has no place in any of these (concluded 2026-10-25): the
    review criteria above reject descriptions that tell Claude how to behave, OpenAI keeps personality out of server
    instructions, the user sets their own style in Claude and ChatGPT, and a server can't know where the answer is
    shown (a chat on a phone, voice, a document, another agent). What a server controls is its tools' output,
    which shapes the answer's content (what decided a pull comes first), and a skill's judgement of what an answer
    holds. The one documented exception is an app that draws its own view telling the model not to repeat it
    (https://docs.workato.com/en/mcp/mcp-app-development). The Discord bot is the app itself, so its prompt sets
    the answer's look there.
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
  connector-only evals went from 118/150 to 129/150.
- Overlap that merging leaves: OpenAI's guidance names what a tool is not for ("Do not use for...",
  https://developers.openai.com/apps-sdk/guides/optimize-metadata), and MCP has no field for example arguments
  (Anthropic's API has `input_examples`). Ours: the tools that answer a similar question say "Not for ... (the other
  tool)", and those with arguments that aren't obvious end on "Example: ..." (22 October).
- Guidance in a tool's output reaches every client, with or without a skill. Ours: the connector-only evals' worst
  cases rose when what code can decide moved into the output (how a pull ended, that it wasn't an enrage, that whose
  job it was isn't in the log): `ask-assignment` from 0/3 to 2/3, `night-not-a-dps-problem` from 0/3 to 3/3, while the
  same caveat in five tool descriptions hadn't moved them (22 October).
