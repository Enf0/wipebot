# One image for Wipebot's remote MCP server and its Discord bot; compose.yml runs each.
# Pinned by digest, so a new build gets the same base until Dependabot bumps it (it reads the tag beside it).
FROM ghcr.io/astral-sh/uv:0.12.21-python3.14-trixie-slim@sha256:8f4ab6a0f2cc074290ba6a136eb016350b2b6b93a2108e07b72724901ad1914f

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
