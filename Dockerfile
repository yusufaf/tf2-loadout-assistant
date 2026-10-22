# One Fly app serves both the API and the built frontend from the same origin,
# so the deployed site needs no CORS config. See tf2-loadout-assistant CLAUDE.md
# for the dev-mode split (separate vite dev server on :5173).

# --- Stage 1: frontend build -------------------------------------------------
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
# Empty base -> src/api.ts's fetches resolve against this app's own origin
# instead of http://127.0.0.1:8000 (the dev default).
ENV VITE_API_BASE=""
RUN pnpm build

# --- Stage 2: python dependencies --------------------------------------------
FROM python:3.12-slim AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /bin/uv
# Bytecode is compiled at build time because the runtime user can't write
# __pycache__ into the venv, and Fly's auto-start cold boots shouldn't pay for it.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_CACHE=1
WORKDIR /app

# --frozen installs exactly what uv.lock pins, so prod runs the versions the
# tests ran against. Not --locked: that re-resolves [tool.uv.sources], whose
# editable ../tf2-wiki-mcp doesn't exist in this build context. CI's
# `uv sync --locked` is what keeps uv.lock in step with pyproject.toml.
# --no-install-package skips only tf2-wiki-mcp itself; its dependencies
# (fastmcp, mwparserfromhell, ...) still come from the lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package tf2-wiki-mcp

# The project installs editable on purpose: api.py's CACHE_DIR/FRONTEND_DIST
# resolve via __file__.parents[2], which only lands on /app when the package
# runs from /app/src rather than from site-packages.
COPY README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-install-package tf2-wiki-mcp

# tf2-wiki-mcp comes from PyPI, pinned to the version uv.lock recorded from the
# sibling checkout. If that version isn't released yet the build fails here, so
# release tf2-wiki-mcp first. Unreleased sibling edits at the same version don't
# reach prod. This must stay after the last `uv sync`, which is exact and would
# uninstall it.
RUN TF2_WIKI_MCP_VERSION="$(python -c 'import tomllib; print(next(p["version"] for p in tomllib.load(open("uv.lock", "rb"))["package"] if p["name"] == "tf2-wiki-mcp"))')"     && uv pip install --no-deps "tf2-wiki-mcp==${TF2_WIKI_MCP_VERSION}"     && rm -f .venv/.lock

# --- Stage 3: app files --------------------------------------------------------
# Files from a Windows build context arrive world-writable, which would let the
# unprivileged runtime user rewrite its own code, catalog and static assets
# (Vite copies frontend/public into dist with those modes). Modes are fixed
# here so the runtime stage's COPY --from carries them without a second 15 MB
# layer for schema_items.json.
FROM python:3.12-slim AS app-files
WORKDIR /app
COPY src/ src/
# Cache is gitignored (see .gitignore) and there's no production refresh script
# (CLAUDE.md: rebuild it with `uv run pytest --live`) -- boots from whatever
# snapshot is baked in at build time. Re-run the live tests and rebuild the
# image to refresh schema/pricing data.
COPY .cache/ .cache/
COPY --from=frontend-build /app/frontend/dist/ frontend/dist/
RUN mkdir -p .cache/lore && chmod -R u=rwX,go=rX src .cache frontend

# --- Stage 4: runtime ------------------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app
RUN useradd --system --create-home app

COPY --from=python-build /app/.venv/ .venv/
COPY --from=app-files /app/ ./
# LoreService caches wiki lookups under .cache/lore at runtime (best-effort, lost
# on restart). Everything else stays root-owned and read-only to the app user.
RUN chown -R app:app .cache/lore

ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["tf2-loadout-api"]
