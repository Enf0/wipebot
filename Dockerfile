# One image for Wipebot's remote MCP server and its Discord bot; compose.yml runs each.
# Pinned by digest, so a new build gets the same base until Dependabot bumps it (it reads the tag beside it).
FROM ghcr.io/astral-sh/uv:0.13.4-python3.14-trixie-slim@sha256:db955a4c5fd85c9e08ae2c02f3fcedf76765aebc7c9bc10c55a6bbcc1b0510be

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --all-extras --no-install-project

COPY src src
COPY CHANGELOG.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --all-extras

# uid 1000, the first user on most Linux hosts, so files in the mounted data directories keep their owner.
RUN useradd --uid 1000 --create-home app
USER app
ENV PATH=/app/.venv/bin:$PATH HOME=/home/app
