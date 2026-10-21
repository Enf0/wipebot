# Common commands; `just` lists them. Settings and secrets come from .env (see .env.example).
# Recipes run `python -m` rather than uv's generated launchers, which Windows application control can block.

set dotenv-load
set positional-arguments

py := "uv run python"

[private]
default:
    @just --list --unsorted

# Tests, lint (ruff), types (ty) and the layers (import-linter): run before every commit
check:
    uv run pytest -q
    uv run ruff check .
    uv run ty check
    uv run lint-imports

# Run the tests (pytest arguments pass through, e.g. just test -k follower)
test *args:
    uv run pytest "$@"

# Rewrite the snapshots (the pull summaries and tool outputs) after an intended change to them, then review `git diff`
snapshot $UPDATE_SNAPSHOTS="1":
    uv run pytest tests/core/test_pulls.py tests/core/test_tools.py -q

# Record what the tool tests ask Warcraft Logs and the guides, from the evals' frozen data (fetching what's missing)
record-tools source="evals/cache.sqlite":
    {{py}} -m tests.support.recorded "$1"

# The local test cases' recordings and snapshots (tests/README.md): fetch them, or push them after record-tools
local-fixtures action="fetch":
    {{py}} -m tests.support.local "$1"

# The evals' frozen data (evals/README.md): fetch it (--replace an older copy), or push this machine's (maintainers)
eval-data action="fetch" *args:
    {{py}} -m evals.data "$@"

# Check answers against the cases in evals/cases/ (e.g. just evals, just evals --model opus --trials 3)
evals *args:
    {{py}} -m evals.run "$@"

# Run one tool without an AI, e.g. just call pull_summary '{"report": "KBYT4xXn7VNjDgh8", "pull": 13}'
call tool json="{}":
    {{py}} -m wipebot call "$1" "$2"

# Chat about a report in the terminal, e.g. just ask KBYT4xXn7VNjDgh8 --as <character> -q "Why did we wipe?"
ask code *args:
    {{py}} -m wipebot ask "$@"

# Serve the tools over MCP for many users (Streamable HTTP at http://localhost:8000/mcp)
serve *args:
    {{py}} -m wipebot serve "$@"

# Run the Discord bot
bot:
    {{py}} -m wipebot bot
