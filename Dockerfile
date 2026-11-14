FROM node:24-bookworm-slim AS frontend
WORKDIR /build
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-alpine3.23 AS api-dependencies
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy
RUN uv sync --locked --only-group api --no-install-project --no-build \
    && find /app/.venv -type d -name __pycache__ -prune -exec rm -rf '{}' +

FROM python:3.13-alpine3.23 AS api
RUN apk add --no-cache ca-certificates libgcc libstdc++ \
    && addgroup -S -g 10001 teassistant \
    && adduser -S -D -H -u 10001 -G teassistant teassistant

WORKDIR /app
COPY --from=api-dependencies /app/.venv /app/.venv
COPY --chown=0:0 backend/app ./backend/app
COPY --chown=0:0 scripts ./scripts
COPY --chown=0:0 data ./data
COPY --chown=0:0 templates ./templates
COPY --from=frontend --chown=0:0 /build/dist ./frontend/dist

RUN mkdir -p /app/work/home /app/work/cache /bank \
    && chown -R 10001:10001 /app/work \
    && chmod 0555 /bank

ENV HOME=/app/work/home \
    PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TEASSISTANT_ALLOW_CODEX=0 \
    TEASSISTANT_DATA_DIR=/app/work/web/private \
    TEASSISTANT_FRONTEND_DIST=/app/frontend/dist \
    TEASSISTANT_MODEL_PROVIDER=deepseek \
    XDG_CACHE_HOME=/app/work/cache

VOLUME ["/app/work"]
EXPOSE 8000
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "/app/scripts/container_entrypoint.py"]
CMD ["/app/.venv/bin/uvicorn", "app.web_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/app/.venv/bin/python", "/app/scripts/container_healthcheck.py", "--role", "api"]

ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/contr4l/Teassistant" \
    org.opencontainers.image.revision="${VCS_REF}"

FROM python:3.13-slim-bookworm AS python-dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy
# Scope Git's HTTP/1.1 workaround to dependency installation behind HTTP/2 proxies.
RUN GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1 \
    uv sync --locked --no-dev --group export --no-install-project \
    && find /app/.venv -type d -name __pycache__ -prune -exec rm -rf '{}' +

# Main business worker. Browser, Word and PDF conversion run in tools/worker.py.
# Keep this target on the full Python dependency set, but without the large OS
# toolchain below so ordinary application releases remain small and quick.
FROM python:3.13-slim-bookworm AS worker
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=python-dependencies /app/.venv /app/.venv
COPY --chown=0:0 backend/app ./backend/app
COPY --chown=0:0 scripts ./scripts
COPY --chown=0:0 data ./data
COPY --chown=0:0 templates ./templates
COPY --from=frontend --chown=0:0 /build/dist ./frontend/dist
RUN groupadd --gid 10001 teassistant \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin teassistant \
    && mkdir -p /app/work/home /app/work/cache /app/work/tmp /bank \
    && chown -R 10001:10001 /app/work \
    && chmod 0555 /bank
ENV HOME=/app/work/home \
    PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TEASSISTANT_ALLOW_CODEX=0 \
    TEASSISTANT_DATA_DIR=/app/work/web/private \
    TEASSISTANT_FRONTEND_DIST=/app/frontend/dist \
    TEASSISTANT_MODEL_PROVIDER=deepseek \
    XDG_CACHE_HOME=/app/work/cache \
    TMPDIR=/app/work/tmp
VOLUME ["/app/work"]
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "/app/scripts/container_entrypoint.py"]
CMD ["/app/.venv/bin/python", "/app/scripts/run_web_worker.py"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/app/.venv/bin/python", "/app/scripts/container_healthcheck.py", "--role", "worker"]
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/contr4l/Teassistant" \
    org.opencontainers.image.revision="${VCS_REF}"

# Legacy full runtime target retained for existing local/deployment configs.

FROM python:3.13-slim-bookworm AS runtime
# Keep Debian copyright notices and licenses; discard offline reading material.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fontconfig \
        fonts-noto-cjk \
        libatomic1 \
        libreoffice-writer \
        texlive-fonts-recommended \
        texlive-lang-chinese \
        texlive-latex-extra \
        texlive-xetex \
        tzdata \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
        /usr/share/man /usr/share/info \
    && find /usr/share/doc -type f \
        \( -iname '*.pdf' -o -iname '*.html' -o -iname '*.html.gz' \
           -o -iname '*changelog*' -o -iname 'NEWS*' -o -iname 'README*' \) \
        ! -iname '*copyright*' ! -iname '*license*' ! -iname '*copying*' -delete

WORKDIR /app
COPY --from=python-dependencies /app/.venv /app/.venv
RUN /app/.venv/bin/python -c 'import importlib.util, pathlib, subprocess; driver = pathlib.Path(importlib.util.find_spec("playwright").origin).parent / "driver" / "node"; subprocess.run([str(driver), "--version"], check=True)' \
    && chromium --version
COPY --chown=0:0 backend/app ./backend/app
COPY --chown=0:0 scripts ./scripts
COPY --chown=0:0 data ./data
COPY --chown=0:0 templates ./templates
COPY --from=frontend --chown=0:0 /build/dist ./frontend/dist

RUN groupadd --gid 10001 teassistant \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin teassistant \
    && mkdir -p /app/work/home /app/work/cache /app/work/texmf-var /app/work/texmf-config /bank \
    && chown -R 10001:10001 /app/work \
    && chmod 0555 /bank

ENV HOME=/app/work/home \
    PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TEASSISTANT_ALLOW_CODEX=0 \
    TEASSISTANT_DATA_DIR=/app/work/web/private \
    TEASSISTANT_FRONTEND_DIST=/app/frontend/dist \
    TEASSISTANT_MODEL_PROVIDER=deepseek \
    TEASSISTANT_SOFFICE=/usr/bin/libreoffice \
    TEASSISTANT_XELATEX=/usr/bin/xelatex \
    TEASSISTANT_CHROMIUM=/usr/bin/chromium \
    TEXMFCONFIG=/app/work/texmf-config \
    TEXMFVAR=/app/work/texmf-var \
    XDG_CACHE_HOME=/app/work/cache

VOLUME ["/app/work"]
EXPOSE 8000
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "/app/scripts/container_entrypoint.py"]
CMD ["/app/.venv/bin/uvicorn", "app.web_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/app/.venv/bin/python", "/app/scripts/container_healthcheck.py", "--role", "api"]

ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/contr4l/Teassistant" \
    org.opencontainers.image.revision="${VCS_REF}"
