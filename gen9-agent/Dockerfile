# syntax=docker/dockerfile:1
# gen9-agent API image. Follows uv's Docker guide (https://docs.astral.sh/uv/guides/integration/docker/):
# locked, cached dependency layer first, then the project; the runtime stage has no uv and runs as non-root.

# Pinned as tag@digest. To upgrade: docker buildx imagetools inspect <image>:<tag>
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv

# Compile bytecode for faster starts; copy (not hardlink) from the cache mount; use the image's Python
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies only: cached until uv.lock or pyproject.toml change
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE}

# git fetches plugin sources (plugin_sources.py), hardened there
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# pip, and the copy ensurepip keeps, came with the base and serve nothing here: uv installs at
# build, and nothing runs pip (`make scan` found six advisories in the base's pip 25.0.1)
RUN python -m pip uninstall --yes --quiet pip && rm -rf /usr/local/lib/python3*/ensurepip
RUN groupadd --system --gid 1001 app && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000

USER app
EXPOSE 8000

CMD ["gen9-agent"]
