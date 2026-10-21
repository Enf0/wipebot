# Wipebot

One package, `src/wipebot`, in layers (README.md says what it does; GitHub issues track open work):
- `core`: World of Warcraft raid analysis as MCP tools over Warcraft Logs and game guides, run in-process.
- `mcp`: Wipebot in Claude and ChatGPT: the tools over stdio and as a remote server with Warcraft Logs sign-in.
- `discord`: Wipebot in Discord, a bot built on the tools with the Claude Agent SDK.

`mcp` and `discord` import only `core`, and `core` no app; inside `core`, a module imports only the layers below
it (pyproject.toml). import-linter checks both in `just check`.

## Commands

`just` lists them; recipes run `uv run python -m ...` and load `.env` (`.env.example` has the ones to set).
- `just check`: tests, ruff, ty and the layers. Run it before every commit.
- `just call <tool> '<json>'`: one tool without the model. `just ask <report> --as <character> -q "..."`: an answer
  end to end, as Discord gets it. The tests' report, KBYT4xXn7VNjDgh8, is a good default for both.
- `just snapshot` rewrites the pinned tool outputs; `just record-tools` records what a tool test newly asks;
  `just local-fixtures` fetches the cases that can't be published, and `just local-fixtures push` stores them again
  whenever `just record-tools` or `just snapshot` changes anything in `tests/fixtures/real/tools/local/` (CI's local
  cases test the stored copy, so they fail until then);
  `just evals` checks answers (how: evals/README.md); `just evals --lint` checks the cases' numbers against the
  tools' output now (no model, a minute): run it after a snapshot changes.

## Rules

- **Numbers come from code, never from the model.** A tool computes every fact it states, and its output says what
  code can work out; the model explains.
- **Where guidance goes** (why: docs/reference/mcp-clients.md):
  - A tool's docstring says what it returns, when to call it and how to read its output, never how to answer.
  - The method, how to answer, lives only in the skill `src/wipebot/core/skills/raid-log-analysis/`.
  - `instructions.md` is a short overview for clients that read server instructions (under 512 characters).
  - These three are for every host, so they name no host and only the core's tools (a test checks). What one app
    adds (the remote server's `my_characters`, the bot's channels and own tools) and which method rule it replaces
    goes in that app: the bot's in `src/wipebot/discord/host-prompt.md`, the tool's own description for the rest.
- **Wrap warcraft_cli, don't redo it.** It is the way to Wowhead, Method, Icy Veins, Lorrgs and the WCL client.
- **Don't hardcode game knowledge.** Specs and roles, spell ids, cooldowns and raid buffs change with patches: read
  them from a source (Lorrgs, Raidbots, Wowhead, WCL) through `knowledge/`.
- **Tool output is pinned** in snapshots (how the tests work: tests/README.md). A change to what
  a tool prints is a diff after `just snapshot`, read before it's kept: a snapshot pins what's in it, right or
  wrong. A new tool gets a case there.
- **Tests and evals hold only what we may publish** (why: the RPGLogs API ToS §5d and the guide sites' terms;
  tests/README.md), so nothing slips in with a new case:
  - real Warcraft Logs data in a tracked file comes only from reports of the maintainers' guild (The Dragonfell
    Tribe, EU-Kazzak), whose uploader agreed and whose raw responses Warcraft Logs' developers allowed;
  - never tracked as data (recordings, snapshots, test fixtures): Warcraft Logs' rankings, percentiles, character
    pages, other guilds' reports and Raidbots' data files (its terms forbid reproducing them). A case reading them is
    `local = true` (recorded.py refuses one that isn't), and a test needing such data makes it up (Warcraft Logs asks
    for synthetic data in public tests). Eval cases and docs may cite what a tool computes from them about our
    raiders (a percentile, a gap to the top), never another guild's players or reports;
  - the guide sites' writing (pages, tooltip text, comments) and Warcraft Logs' images get a stand-in
    (`tests/support/standins.py`); game facts (spell ids, cooldowns, talent trees) may stay;
  - evals: cases on that guild's reports, in English. Their data is fetched once and kept in the untracked
    `evals/cache.sqlite`, never in the repo.
- **Keep answers fast.** An answer already takes 30-70 s, and every Warcraft Logs, warcraft_cli or web call adds to
  it. Keep on disk what can't change (docs/reference/wcl-api.md, "Using the API efficiently"), batch per-pull queries
  into one, and make reading many reports or nights opt-in (a parameter). Time a new or changed tool with `just call`,
  cold and warm, and say what it costs.
- **Design for the whole night.** Raiders ask about the night as often as about one pull, and a per-pull tool gets
  called once per pull. A tool about a pull takes `all_pulls` (or every player, every boss) from its first version,
  fetching what the pulls need in a few batched queries (`Report.events_for`, `Report.tables`).
- **Weigh value against cost.** Before proposing or building a fix or feature, weigh what it adds for raiders
  against what it costs: complexity (code, parameters and tools the model must choose between) and speed (queries,
  seconds per answer). Recommend the ones whose value clearly outweighs the cost, and say why the others wait.
- **Extend a tool before adding one.** A new kind of question about a subject a tool already covers becomes a
  parameter of that tool (a scope, a topic, an aspect); a new tool is for a new subject, and comes with a snapshot case
  and an eval case. Overlapping tools make the model pick the wrong one (why: docs/reference/mcp-clients.md).
- **Test against real reports, across bosses**, not only Sszorak: facts with `just call`, answers with `just ask` or
  `just evals` (`evals/cases/`; its rules load with it).
- **Run as few evals as the change needs.** A full run is about $14 and 30 min per mode with one trial (three: about $40), so:
  - a refactor whose snapshots don't change: no evals (the snapshots prove the outputs are the same);
  - a change to a tool's output or to cases: only the cases that call that tool or were changed (`-k`), 3 trials;
  - a change to the skill or tool descriptions: the full suite (plugin mode, the default); to the bot's prompt or
    its own tools: the full suite with `--bot`;
  - new baselines (all three modes: plugin, `--connector-only`, `--bot`): only when the judge or the cases changed,
    or before a big deploy.

  One eval run at a time across sessions: parallel runs hit the usage limit.
- **Never print or commit secrets** (`.env`; show variable names only), and keep tracked files fit to publish: no
  guild matters, hosts or one deployment's setup. Raiders' character names may appear (public in the game and on
  Warcraft Logs).
- **Commits follow Conventional Commits:** `type(scope): summary` in plain words.
  - Types: `feat` (users can now do or see something), `fix` (a bug users could hit), `perf`, `refactor`, `test`,
    `docs`, `build` (Docker, dependencies, deploy), `chore`.
  - Scopes: `agent` (the core: the tools and what both apps answer), `mcp` (the remote server, sign-in, the replay
    viewer), `bot` (Wipebot in Discord), `evals`; none when a change spans them.
  - A breaking change to setup or configuration gets a `!` and says in the body what to change.
  - A `feat`, `fix` or `perf` raiders would notice adds a `CHANGELOG.md` line in the same commit (its format loads
    with the file).

## Reference

Verified facts; read before working in that area, and add a fact once it's checked against real data:
docs/reference/wcl-api.md (the Warcraft Logs API and sign-in), docs/reference/sources.md (the guide sites,
warcraft_cli), docs/reference/mcp-clients.md (Claude and ChatGPT as MCP clients), docs/models.md (eval results and cost
per model).
