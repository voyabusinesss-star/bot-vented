# Discord interactions + webhook Whop (Railway)
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

# Railway injecte PORT ; le webhook / healthcheck écoute dessus
EXPOSE 8080

# Migrations lancées dans discord-interactions après bind PORT (healthcheck OK)
CMD ["uv", "run", "vinted-bot", "discord-interactions"]
