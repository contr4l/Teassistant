FROM node:24-bookworm-slim AS frontend
WORKDIR /build
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim-bookworm AS python-dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN uv sync --locked --no-dev --group export --no-install-project

FROM python:3.13-slim-bookworm AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/contr4l/Teassistant" \
    org.opencontainers.image.revision="${VCS_REF}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fontconfig \
        fonts-noto-cjk \
        libreoffice-writer \
        texlive-fonts-recommended \
        texlive-lang-chinese \
        texlive-latex-extra \
        texlive-xetex \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=python-dependencies /app/.venv /app/.venv
COPY --chown=0:0 backend ./backend
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
