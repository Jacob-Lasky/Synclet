# syntax=docker/dockerfile:1.6
#
# Production image. ONE container: built Vue SPA + Litestar backend + uv venv.
# Built and published by .github/workflows/publish.yml on push to dev or main.
# DO NOT use for dev: Dockerfile.dev runs Vite + uvicorn --reload with bind
# mounts. This image bakes a static dist/ and an immutable venv.

# ─ Stage 1: build the frontend ────────────────────────────────────────────
FROM node:24-slim AS frontend-builder

WORKDIR /app

# corepack pins pnpm to the version in packageManager (or 11.1.3 fallback to
# match Dockerfile.dev). Without --activate, pnpm shells through corepack
# every invocation and adds noise.
RUN corepack enable && corepack prepare pnpm@11.1.3 --activate

# Cache the dep layer separately from source so source-only changes don't
# bust pnpm install.
COPY frontend/pnpm-lock.yaml frontend/package.json frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build
# Litestar's static handler with html_mode=True serves 404.html on missing
# files. Vite doesn't emit one, so any unknown path (typo'd bookmark, future
# vue-router HTML5 route) would return a bare 404. Cloning index.html to
# 404.html makes the response body be the SPA shell — Vue mounts and renders
# the default view. Note: the HTTP status is still 404, not 200; Litestar's
# html_mode preserves the missing-file status. Functionally fine for Synclet
# today (no client-side routing, no CDN in front). If/when vue-router lands
# with HTML5 history mode, swap to a custom catch-all handler returning 200.
RUN cp dist/index.html dist/404.html
# After this stage: /app/dist contains the static SPA bundle + 404 fallback.


# ─ Stage 2: backend deps via uv ───────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.11.15-python3.14-trixie-slim AS backend-builder

WORKDIR /app

# Venv outside /app so the runtime stage can copy /opt/venv independently of
# the source tree. Matches the dev image layout.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Lockfile first for layer caching; --frozen pins the resolved versions.
# DO NOT use uv.lock* (wildcard) here — that pattern silently tolerates a
# missing lockfile, after which `--frozen` would fail with a confusing
# message instead of failing here at copy time with a clear "uv.lock missing".
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-cache --link-mode copy --no-dev


# ─ Stage 3: runtime ───────────────────────────────────────────────────────
# Same uv base — gives us a fully-functional python 3.14 without pulling a
# second large layer. The runtime doesn't need uv itself, but the base is
# small and matches the build env exactly.
FROM ghcr.io/astral-sh/uv:0.11.15-python3.14-trixie-slim AS runtime

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SYNCLET_STATIC_DIR=/app/static

# Backend source + frontend bundle. Order matters for cache: source changes
# more often than venv contents.
COPY --from=backend-builder /opt/venv /opt/venv
COPY --from=frontend-builder /app/dist /app/static
COPY backend/ /app/

# Single port: Litestar serves API + SPA + assets from 1314.
EXPOSE 1314

# Image label so GHCR links back to the repo; ghcr.io picks this up.
LABEL org.opencontainers.image.source="https://github.com/Jacob-Lasky/Synclet"

# uvicorn directly (no --reload). uv run here would re-resolve deps at start;
# the venv is already in place from Stage 2, so call the binary directly.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1314"]
