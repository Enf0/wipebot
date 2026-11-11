# The tests

One folder per layer of `src/wipebot`: `core/`, `mcp/` and `discord/` (`core/conftest.py` and
`discord/conftest.py` have their fixtures); `fixtures/` holds the data they share. `just test` runs them all; `just
check` adds ruff, ty and the layers' import contracts. The suite takes about four minutes with Playwright's Chromium,
whose pages are its slowest tests (the tool cases alone, `just test tests/core/test_tools.py`, about twenty seconds),
and never touches the network: the root `conftest.py`'s `offline` fixture, on for every test here, refuses any
connection but to this machine and any warcraft_cli command (a subprocess), so a test that would ask Warcraft Logs or
a guide site fails at once instead of passing only with someone's login.

## What each kind proves

- **Unit tests** (most files): a rule holds, as someone wrote it down: a kill not ranked yet is asked again, a death
  after the wipe was decided is fallout. They run on small made-up inputs or the captured night, so they cover the
  cases someone thought of, edge cases included. A failure names the broken rule.
- **Snapshot tests** (`core/test_tools.py`, and the pull summaries in `core/test_pulls.py`): each tool's whole
  output, on real recorded data, is still what it was when someone last read and kept it. They catch what nobody
  wrote a test for, such as a change in one place moving a number in another tool's output, but not whether that
  output is right: a snapshot pins what's in it, right or wrong. A failure is a diff to read.
- **View tests** (`core/test_views.py`): each view's data on a real pull, pinned like a snapshot
  (`fixtures/real/views/`) and checked against its tool's text, and the page drawn in a mock host in Chromium
  (`viewhost.py`): no console error, a height reported, the numbers shown, the MCP Apps messages in order. Without
  Playwright's Chromium (`uv run python -m playwright install chromium-headless-shell`) those are skipped; how a view
  looks is for a person or a model to judge, from `just view --shot`.
- **Evals** (`evals/`, not run by `just check`): a model with the tools answers raiders well. A tool can print every
  number right and the answer still blame the wrong player; evals/README.md.

None of them proves the tools still work against Warcraft Logs and the guide sites today: the answers are frozen, so
an API or page that changed shows only live (`just call`).

## Where the data comes from

- **Recorded answers**, for the tools on real pulls. `fixtures/real/tools/cases.toml` lists the cases (a name, a
  tool, its arguments); `core/test_tools.py` runs each against `recorded.sqlite`, the answers Warcraft Logs, warcraft_cli
  and Raidbots gave, and compares its output with `<name>.txt` next to it. A case that asks something not recorded
  fails with "not recorded". `support/recorded.py` is the whole mechanism.
- **The captured night**, `fixtures/real/KBYT4xXn7VNjDgh8.json`: one report's fights, players, deaths and boss buffs,
  served by `FakeWCL` (`support/night.py`) with every player a Restoration Shaman and empty tables. It's for what
  recorded answers can't do: a report still being logged, growing pull by pull, and counting the queries a report
  asks. `rep` (core/conftest.py) is that night as a loaded `Report`; `patch_wcl` and `fake_knowledge` give a test the
  Warcraft Logs or guide answers it needs. Facts that depend on specs, tables or casts belong in a tool case, not here.
- **Guide pages** in `fixtures/knowledge/`, for `knowledge/`'s parsers: short excerpts, cut to the markup and the
  clauses a parser reads.
- Anything else a test builds itself, with made-up names (Torvek, Mirelle, The Copper Lanterns).

Everything under `fixtures/real/` is copied from the reports, raid notes and plans of The Dragonfell Tribe
(EU-Kazzak), the maintainers' guild, with players under their real names (character names are public in the game
and on Warcraft Logs). The RPGLogs API terms (§5d) let content from the API be published only with its owner's
permission, and the reports' uploader gave it. Other guilds' reports don't go here.

Warcraft Logs' rankings and other guilds' reports (the top parses' casts, talents and gear, the fastest kills,
percentiles, character pages) may not be published: Warcraft Logs asks for synthetic data in public tests, and their
developers confirmed our own reports' raw responses are fine. Raidbots' data files (`talents.json` and the like) can't
be either: its terms forbid reproducing them. So a case that reads any of these is marked `local = true` in
cases.toml: its answers (`fixtures/real/tools/local/recorded.sqlite`) and its snapshot (`local/<name>.txt`) are
untracked, and without them it's skipped. Recording stops if a case that isn't local asks for or is answered with
rankings, another guild's report or a Raidbots file, and `core/test_recorded.py` checks every published answer the
same way.

To run the local cases, put your own Warcraft Logs keys in `.env` (`.env.example`), then `just record-tools`, which
without `evals/cache.sqlite` starts from the published answers and fetches only what the local cases need (about a
minute and some 450 Warcraft Logs points, measured on a fresh clone), and `just snapshot`, which writes the local
snapshots and fails once for each new one; `just test` then passes. Rankings and other guilds' kills move, so these
snapshots are your own baseline: make it before a change and compare after. The repository's CI runs the local cases
against a copy the maintainers keep privately, and prints only which failed (`.github/workflows/checks.yml`; skipped
in a fork).

A whole guide page never goes in. What the guide sites and Warcraft Logs' images give gets a stand-in when recording
(`support/standins.py`):
- The guide sites: their writing becomes made-up words (markup, links, titles and dates stay), their talent builds
  that guild's raiders' talent strings, a tooltip its facts (cooldown, what a defensive does, an item's stats);
  comments and writers' names go.
- Warcraft Logs' images are flat colours of the same size.
- The guild's listings (its attendance, its reports with their players) name everyone in every log tagged to the
  guild, pugs too: a player outside the guild gets a made-up name (`Pug…`), so who's in the guild still counts the
  same.

Game facts stay: spell ids, names, cooldowns, map bounds and Lorrgs' spec spells (lorrgs.io, MIT). Talent trees come
from Raidbots' files, so a test that needs one makes up a small tree of the same shape.

## Snapshots

Every pull's summary (`fixtures/real/<code>.summaries.md`) and each tool case (`fixtures/real/tools/<name>.txt`).
After an intended change to an output, `just snapshot` rewrites them: read the diff before keeping it. Don't repeat a
snapshot's wording in a test: test a fact it can't hold (a made-up situation, a rule) as a structured assert.

## Size budgets

`fixtures/real/tools/sizes.toml` holds each tool case's budget: the size of its pinned output, in characters (the
local cases' in `local/sizes.toml`, untracked like their snapshots). The model reads a tool's whole output on every
call, so an output that grows costs every answer that reads it, and a snapshot's diff shows changed text, not that it
is a fifth longer. `test_no_tool_output_outgrew_its_size_budget` (`core/test_tools.py`) fails for an output past its
budget by more than a tenth, or by more than 200 characters where that is more.

`just snapshot` lowers a line when its output shrank and adds a new case's. It never raises one: where the growth is
meant, raise the line by hand and give the reason in the commit; where it isn't, cut what repeats.

## Adding a tool's case

1. Add a line to `fixtures/real/tools/cases.toml`, ideally on a boss other than Sszorak. A case reading rankings,
   another guild's report or a Raidbots file: `local = true`.
2. `just record-tools` records again: every case, not only the new one, with each case's view data and the replay
   viewer's. It runs them on the evals' frozen data (`evals/cache.sqlite`, if you have it) plus what was recorded
   before (which wins), fetches only what neither has, and keeps exactly the answers the cases asked for. With
   nothing to fetch that takes about half a minute and needs no keys; what a new case asks Warcraft Logs is fetched
   with your keys in `.env`, for its points. `recorded.sqlite` is one binary file of some 25 MB: a recording that
   changes an answer changes the file, a diff git can't show, and an answer no case asks any more leaves it.
3. `just snapshot` writes the new snapshot, failing once for it, and adds the case's line to `sizes.toml`: read the
   snapshot, then `just test`. (`just test -k <name>` writes the snapshot too, and leaves the size line out.)

A change that moves what a case asks Warcraft Logs (another cut, another set of pulls) leaves its recording
without the answer. Mark the case `stale = "<what changed>"` in cases.toml: its test is skipped until
`just record-tools` and `just snapshot`, and fails after them until the mark is taken off.

`core/test_server.py` checks that every tool has a case, or says in `TESTED_ELSEWHERE` where it's tested.
