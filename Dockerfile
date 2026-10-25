# One image for Wipebot's remote MCP server and its Discord bot; compose.yml runs each.
# Pinned by digest, so a new build gets the same base until Dependabot bumps it (it reads the tag beside it).
FROM ghcr.io/astral-sh/uv:0.13.4-python3.14-trixie-slim@sha256:db955a4c5fd85c9e08ae2c02f3fcedf76765aebc7c9bc10c55a6bbcc1b0510be

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --all-extras --no-install-project

# Chromium's headless shell, for the bot's pictures of the views (viewhost.Painter), where every user finds it; with the
# dependencies, so only a new Playwright installs it again. Its system libraries are the ones its binary links to
# (ldd), not Playwright's --with-deps, which are full Chromium's and fonts for every script (twice the size); the
# pictures bring their own font (Inter), DejaVu covers the rest.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatomic1 libatspi2.0-0t64 libdbus-1-3 libdrm2 libexpat1 \
        libgbm1 libglib2.0-0t64 libnspr4 libnss3 libx11-6 libxau6 libxcb1 libxcomposite1 libxdamage1 libxdmcp6 \
        libxext6 libxfixes3 libxi6 libxkbcommon0 libxrandr2 libxrender1 fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && /app/.venv/bin/python -m playwright install --only-shell chromium

COPY src src
COPY CHANGELOG.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --all-extras

# uid 1000, the first user on most Linux hosts, so files in the mounted data directories keep their owner.
RUN useradd --uid 1000 --create-home app
USER app
ENV PATH=/app/.venv/bin:$PATH HOME=/home/app
