# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12-slim-bookworm

FROM python:${PYTHON_VERSION} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
# Корпоративный SSL-inspection прокси (Unitel/Beeline FortiGate) переподписывает
# весь внешний HTTPS своим корнем — без него httpx падает с CERTIFICATE_VERIFY_FAILED
# на любом внешнем API (Modal, LLM). Доверяем корень системно; SSL_CERT_FILE
# переключает httpx (и всё, что уважает эту переменную) с certifi на системный
# бандл, куда update-ca-certificates добавил корень.
COPY docker/unitel-root-ca.crt /usr/local/share/ca-certificates/unitel-root-ca.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv


FROM base AS builder
WORKDIR /app
# Install prod dependencies from the committed lockfile into /opt/venv
# (UV_PROJECT_ENVIRONMENT). --frozen uses uv.lock as-is (reproducible, hash-checked)
# and errors if it is missing. --no-install-project: only third-party deps live in
# the image; app source is copied separately and imported via PYTHONPATH, so the
# project package itself is never built/installed here.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM base AS dev
WORKDIR /app
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/src"
# Prod + dev groups from the same lockfile, so ruff/mypy/pytest are pinned exactly
# as in CI. --no-install-project: source is copied below, not installed.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
# Operator entrypoints run with `compose exec app python scripts/<name>.py`
# (tenant provisioning, directory seeding) — they must live in the image.
COPY scripts ./scripts
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


FROM base AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/src"
COPY --chown=app:app src ./src
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app scripts ./scripts
USER app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--forwarded-allow-ips", "*", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
