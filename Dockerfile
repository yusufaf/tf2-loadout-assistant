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

# --- Stage 2: python runtime --------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
# Plain pip, not `uv sync`: uv would honor [tool.uv.sources]'s editable path to
# the sibling ../tf2-wiki-mcp repo, which doesn't exist in this build context.
# pip ignores that uv-only table and resolves tf2-wiki-mcp from PyPI instead.
# -e (editable) matters too: api.py's CACHE_DIR/FRONTEND_DIST resolve via
# __file__.parents[2], which only lands on /app when the package is installed
# from the source tree here rather than copied into site-packages.
RUN pip install --no-cache-dir -e .

# Cache is gitignored (see .gitignore) and there's no production refresh script
# (CLAUDE.md: rebuild it with `uv run pytest --live`) -- boots from whatever
# snapshot is baked in at build time. Re-run the live tests and rebuild the
# image to refresh schema/pricing data.
COPY .cache/ .cache/

COPY --from=frontend-build /app/frontend/dist/ frontend/dist/

EXPOSE 8000
CMD ["tf2-loadout-api"]
