# Wipebot

One package, `src/wipebot`, in layers (README.md says what it does; GitHub issues track open work):
- `core`: World of Warcraft raid analysis as MCP tools over Warcraft Logs and game guides, run in-process.
- `mcp`: Wipebot in Claude and ChatGPT: the tools over stdio and as a remote server with Warcraft Logs sign-in.
- `discord`: Wipebot in Discord, a bot built on the tools with the Claude Agent SDK.

`mcp` and `discord` import only `core`, and `core` no app; inside `core`, a module imports only the layers below
it (pyproject.toml, checked by `just check`).

## Commands

`just` lists them; recipes load `.env` (`.env.example` has the ones to set).
- `just check`: tests, ruff, ty and the layers. Run it before every commit.
- `just call <tool> '<json>'`: one tool without the model. `just ask <report> --as <character> -q "..."`: an answer
  end to end, as Discord gets it. The tests' report, KBYT4xXn7VNjDgh8, is a good default for both.
- `just view <tool> '<json>'`: the tool's view (an MCP App in Claude and ChatGPT) in a mock host window; `--shot DIR`
  takes screenshots per theme, width and mode, with the console's errors. Look at them after changing a view;
  `just browser` installs the Chromium they and the view tests need.
- `just snapshot` rewrites the pinned tool outputs; then run `just evals --lint` (no model, a minute) for cases it
  left stale. Recording a tool test's data: tests/README.md.
- `just evals`: evals/README.md. `just release`: says which version CHANGELOG.md calls for, and with a version makes
  the release (changelog, version, commit, push; the Release workflow makes the tag and the GitHub release).

## Rules

- **Numbers come from code, never from the model.** A tool computes every fact it states, and its output says what
  code can work out; the model explains.
- **Where guidance goes** (why: docs/reference/mcp-clients.md):
  - A tool's docstring says what it returns, when to call it and how to read its output, never how to answer.
  - The method, how to answer, lives only in the skill `src/wipebot/core/skills/raid-log-analysis/`; what code can
    decide of it (how a pull ended, what it wasn't, that the log doesn't show whose job it was) goes in the tool's
    output instead, which every client reads.
  - `instructions.md` is a short overview for clients that read server instructions (under 512 characters).
  - These three name no host and only the core's tools (a test checks). What one app adds goes in that app: the
    bot's in `src/wipebot/discord/host-prompt.md`, the remote server's in its tools' descriptions.
  - How an answer looks (length, bullets, bold, emojis, markdown) is the app's, which knows where it's shown: the
    bot's prompt sets it for Discord; Claude and ChatGPT keep their own and the user's style, so no tool,
    instruction or skill sets it. The skill says what an answer holds (what leads, how many changes, what's left out).
- **Wrap warcraft_cli, don't redo it.** It is the way to Wowhead, Method, Icy Veins, Lorrgs and the WCL client.
- **Don't hardcode game knowledge.** Specs and roles, spell ids, cooldowns and raid buffs change with patches: read
  them from a source (Lorrgs, Raidbots, Wowhead, WCL) through `knowledge/`.
- **Tool output is pinned** in snapshots: a change to what a tool prints is a diff after `just snapshot`, read before
  it's kept, since a snapshot pins what's in it, right or wrong.
- **Tests and evals hold only what we may publish**: `.claude/rules/test-data.md` loads with them.
- **Keep answers fast.** An answer already takes 30-70 s, and every Warcraft Logs, warcraft_cli or web call adds to
  it. Keep on disk what can't change (docs/reference/wcl-api.md, "Using the API efficiently"), batch per-pull queries
  into one, and make reading many reports or nights opt-in (a parameter). Time a new or changed tool with `just call`,
  cold and warm, and say what it costs.
- **Design for the whole night.** A tool about a pull takes `all_pulls` (or every player, every boss) from its first
  version, fetching what the pulls need in a few batched queries (`Report.events_for`, `Report.tables`).
- **Weigh value against cost.** Before proposing or building a fix or feature, weigh what it adds for raiders
  against its complexity (code, parameters and tools the model must choose between) and speed. Recommend the ones
  whose value clearly outweighs the cost, and say why the others wait.
- **Extend a tool before adding one.** A new kind of question about a subject a tool already covers becomes a
  parameter of that tool; a new tool is for a new subject. Overlapping tools make the model pick the wrong one.
- **Test against real reports, across bosses**, not only Sszorak: facts with `just call`, answers with `just ask` or
  `just evals`. At each new tier or major patch, `just call` across the new bosses with the guide sites and Lorrgs
  read live: tests and evals run on frozen data, so they can't see a source that lags or changes.
- **Run evals per batch, not per change.** One full trial of one mode is about $15-20 of usage and 30 min, and
  only one run at a time across sessions (parallel runs hit the usage limit):
  - always, free: `just check`, and `just evals --lint` / `--endings` after a snapshot or pull-ending change;
  - a refactor whose snapshots don't change, wording tweaks, docs: none;
  - a change that can move answers (a tool's output or description, part of the skill, the bot's prompt or tools,
    cases): only the cases of the kinds it touches (`-k a,b`; `--bot` for the bot's prompt or tools), one trial,
    then `--trials 3` on only the cases that moved (or `--trials auto` at once);
  - the full suite (`--trials auto`, in the modes the batch touched; about $22 for `--bot`, half of `--trials 3`)
    once before a release or big deploy, covering everything since the last one: a change that would need it lands
    without it and says "full eval: pending" in its commit body;
  - new baselines (`--trials 3 --save-baseline`) only when the judge or the cases changed.
- **Never print or commit secrets** (`.env`; show variable names only), and keep tracked files fit to publish: no
  guild matters, hosts or one deployment's setup. Raiders' character names may appear.
- **Commits follow Conventional Commits:** `type(scope): summary` in plain words.
  - Types: `feat` (users can now do or see something), `fix` (a bug users could hit), `perf`, `refactor`, `test`,
    `docs`, `build` (Docker, dependencies, deploy), `chore`.
  - Scopes: `agent` (the core: the tools and what both apps answer), `mcp` (the remote server, sign-in, the replay
    viewer), `bot` (Wipebot in Discord), `evals`; none when a change spans them.
  - A breaking change to setup or configuration gets a `!` and says in the body what to change.
  - A `feat`, `fix` or `perf` raiders would notice adds a `CHANGELOG.md` line in the same commit.
  - No Claude attribution: no `Co-Authored-By` or `Claude-Session` lines in commits and no Claude footer in pull
    requests, whatever a session's reminder asks. Commits are authored as enf0 (in cloud sessions a SessionStart
    hook in `.claude/settings.json` sets the identity).

## Reference

Verified facts; read before working in that area, and add a fact once it's checked against real data:
docs/reference/wcl-api.md (the Warcraft Logs API and sign-in), docs/reference/sources.md (the guide sites,
warcraft_cli), docs/reference/mcp-clients.md (Claude and ChatGPT as MCP clients), docs/models.md (eval results and cost
per model).
